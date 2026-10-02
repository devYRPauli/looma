"""Orchestration: ingest messages, then rebuild the work graph deterministically.

Two phases keep the system idempotent (ARCHITECTURE.md 14):
  ingest_messages() - incremental, idempotent on event_hash.
  rebuild()         - drops derived tables and regenerates WorkItems, candidates,
                      confidence, promotion, and the graph from stored messages.
                      Equivalent to `looma reprocess`.
"""

import json
import os
import sys
import time
from pathlib import Path

from . import confidence, correction, gitutil, identity
from .sanitize import is_automated_session
from .adapters.claude import ClaudeAdapter
from .config import claude_projects_dir
from .extraction import candidates as cand_mod
from .extraction import deterministic
from .extraction import extractor as extractor_mod
from .promotion import promote
from .resolution import workitems as wi_mod
from .storage.sqlite_store import Store

def default_adapters():
    """All available source adapters (Claude + Codex + Cursor when present)."""
    from .adapters.codex import CodexAdapter
    from .adapters.cursor import CursorAdapter, default_global_db

    adapters = [ClaudeAdapter(claude_projects_dir())]
    codex_root = Path(os.path.expanduser("~")) / ".codex"
    if (codex_root / "sessions").exists():
        adapters.append(CodexAdapter(codex_root))
    if default_global_db().exists():
        adapters.append(CursorAdapter(default_global_db()))
    return adapters


def _ingest_fingerprint(adapter, handle):
    fingerprint = getattr(adapter, "fingerprint", None)
    if fingerprint is not None:
        return fingerprint(handle)
    try:
        path = Path(handle.path)
        stat = path.stat()
        if not path.is_file():
            return None
        return json.dumps(["file-v1", str(path.resolve()), stat.st_ino,
                           stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    except OSError:
        return None


def ingest_messages(store: Store, projects_dir=None, limit=None, project_filter=None,
                    verbose=False, adapters=None) -> dict:
    """Discover sessions from all adapters, insert messages idempotently. Returns counts.

    limit: stop after this many sessions (quick demos). project_filter: canonical_key.
    projects_dir: restrict to a Claude dir (used by tests). adapters: explicit override.
    """
    if adapters is None:
        adapters = [ClaudeAdapter(projects_dir)] if projects_dir else default_adapters()
    new_msgs = 0
    sessions_seen = 0
    skipped = 0
    unchanged = 0
    per_source: dict[str, int] = {}
    changed_projects: set[int] = set()
    for adapter in adapters:
      handles = list(adapter.discover()) if verbose else adapter.discover()
      for index, handle in enumerate(handles, 1):
        if limit is not None and sessions_seen >= limit:
            break
        fingerprint = _ingest_fingerprint(adapter, handle)
        cached = store.find_session(handle.source, handle.native_id)
        if fingerprint is not None and cached and cached["ingest_cursor"] == fingerprint:
            if project_filter and cached["canonical_key"] != project_filter:
                skipped += 1
                continue
            sessions_seen += 1
            unchanged += 1
            per_source[handle.source] = per_source.get(handle.source, 0) + 1
            if verbose:
                print(f"[ingest] Unchanged {handle.source} session {index}/{len(handles)}", flush=True)
            continue
        if verbose:
            print(f"[ingest] Reading {handle.source} session {index}/{len(handles)}", flush=True)
        try:
            events = list(adapter.read(handle))
        except Exception as exc:
            raise RuntimeError(f"Could not read {handle.source} session {handle.native_id}") from exc
        if not events:
            continue
        # project identity from the session's first event that carries a cwd
        root = next((e.project_root for e in events if e.project_root), None)
        ident = identity.resolve(root)
        if not ident:
            # cwd was an ephemeral root (e.g. /tmp): recover the project from the
            # files the session actually touched before giving up. (V2.1.)
            ident = identity.resolve_from_events(events)
        if not ident:
            # Unresolvable session: one shared per-source bucket, NOT one project
            # per session. Minting unknown:<session-id> created 46 singleton
            # "projects" (64% of the corpus); a single labeled bucket is honest
            # and keeps `looma status` meaningful. (V2 Phase 2.)
            #
            # Automated sessions (memory summarizers / extraction API calls run
            # from /tmp) are projectless noise - on this corpus they were ~all of
            # the "unsorted" claude sessions. Bucket them separately so "Unsorted"
            # reflects genuinely-unresolved *real* work, not tooling. (V2.1.)
            msgs = [{"role": e.role, "text": e.text} for e in events]
            kind = "automated" if is_automated_session(msgs) else "unsorted"
            label = "Automated" if kind == "automated" else "Unsorted"
            ident = {
                "canonical_key": f"{kind}:{handle.source}",
                "display_name": f"{label} ({handle.source})",
                "root_path": None,
                "git_remote": None,
            }
        if project_filter and ident["canonical_key"] != project_filter:
            skipped += 1
            continue
        sessions_seen += 1
        per_source[handle.source] = per_source.get(handle.source, 0) + 1
        pid = store.upsert_project(
            ident["canonical_key"], ident["display_name"],
            ident["root_path"], ident["git_remote"],
        )
        branch = next((e.git_branch for e in events if e.git_branch and e.git_branch != "HEAD"), None)
        model = next((e.agent_model for e in events if e.agent_model), None)
        sid = store.upsert_session(pid, handle.source, handle.native_id, branch, model)
        ts_list = [e.ts for e in events if e.ts]
        head = gitutil.head_sha(ident["root_path"]) if ident["root_path"] else None
        store.update_session_meta(
            sid, branch, head,
            min(ts_list) if ts_list else None,
            max(ts_list) if ts_list else None,
            model,
        )
        session_new = 0
        for ev in events:
            if store.insert_message(sid, ev) is not None:
                new_msgs += 1
                session_new += 1
                changed_projects.add(pid)
        if session_new:
            store.mark_rebuild_pending(pid)
        # Never mark a moving transcript complete: retry it on the next cycle.
        if fingerprint is not None and _ingest_fingerprint(adapter, handle) == fingerprint:
            store.update_ingest_cursor(sid, fingerprint)
        store.commit()
    store.commit()
    return {"sessions": sessions_seen, "new_messages": new_msgs, "skipped": skipped,
            "per_source": per_source, "changed_projects": sorted(changed_projects),
            "unchanged_sessions": unchanged}


def _repo_basename(s: str) -> str:
    return s.rstrip("/").rsplit("/", 1)[-1].lower() if s else ""


def reconcile_projects(store: Store) -> int:
    """Fold an orphan path-keyed project into the canonical remote-keyed project of
    the same repo. Handles the same repo cloned in two locations where one checkout
    had no readable git remote at ingest, so its sessions split off under a path:
    key. (V2.1)

    Conservative: only a remote-less `path:` project is ever absorbed, and only
    when exactly one remote project shares its repo basename - so genuinely distinct
    repos that happen to share a name are left alone. Returns the number merged.
    Run after ingest_messages and before rebuild (operates on sessions, not the
    derived graph).
    """
    projects = store.list_projects()
    remote_by_name: dict[str, list[dict]] = {}
    for p in projects:
        if p.get("git_remote"):
            remote_by_name.setdefault(_repo_basename(p["git_remote"]), []).append(p)
    merged = 0
    for p in projects:
        key = p["canonical_key"]
        if not key.startswith("path:") or p.get("git_remote"):
            continue
        name = _repo_basename(p.get("root_path") or key.split(":", 1)[1])
        cands = remote_by_name.get(name)
        if not cands or len(cands) != 1 or cands[0]["id"] == p["id"]:
            continue
        store.reassign_sessions(p["id"], cands[0]["id"])
        _wipe_project(store, p["id"])  # drop any stale derived rows before delete
        store.delete_project(p["id"])
        merged += 1
    if merged:
        store.commit()
    return merged


def _wipe_project(store: Store, pid: int) -> None:
    """Delete one project's derived rows in FK-safe order (for incremental rebuild)."""
    c = store.conn
    # FTS rows first (keyed by the work_item / entity rowid)
    c.execute("DELETE FROM fts_workitems WHERE rowid IN (SELECT id FROM work_items WHERE project_id=?)", (pid,))
    c.execute("DELETE FROM fts_entities WHERE rowid IN (SELECT id FROM entities WHERE project_id=?)", (pid,))
    # join-table children
    c.execute("DELETE FROM edges WHERE src_node IN (SELECT id FROM nodes WHERE project_id=?) "
              "OR dst_node IN (SELECT id FROM nodes WHERE project_id=?)", (pid, pid))
    c.execute("DELETE FROM entity_evidence WHERE entity_id IN (SELECT id FROM entities WHERE project_id=?)", (pid,))
    c.execute("DELETE FROM commit_files WHERE commit_id IN (SELECT id FROM commits WHERE project_id=?)", (pid,))
    # project-scoped tables (child -> parent)
    for t in ("nodes", "entities", "candidate_memories", "commits", "files", "branches", "work_items"):
        c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))


def _extract_sessions(store, projects, extractor, budget, verbose):
    results = {}
    if extractor.name == "heuristic":
        return results
    namespace = extractor.cache_namespace()
    retry_after = store.extraction_retry_after(namespace)
    attempts = 0
    warned_budget = False
    for project in projects:
        sessions = store.project_sessions(project["id"])
        for index, session in enumerate(sessions, 1):
            messages = store.session_messages(session["id"])
            if is_automated_session(messages):
                continue
            if verbose:
                print(f"[rebuild] Extracting {session['source']} session {index}/{len(sessions)}", flush=True)
            key = extractor.cache_key(messages)
            result = store.cached_extraction(key)
            if result is None:
                if time.time() < retry_after or (budget is not None and attempts >= budget):
                    if time.time() >= retry_after and not warned_budget:
                        print(f"[looma] Extraction budget exhausted ({attempts} attempts); using heuristic extraction.", file=sys.stderr)
                        warned_budget = True
                    result = extractor.fallback.extract(messages)
                else:
                    attempts += 1
                    result = extractor.extract(messages)
                    if extractor.last_failed:
                        retry_after = time.time() + 300
                        store.defer_extraction(namespace, retry_after)
                        print("[looma] Local LLM extraction failed; using heuristic extraction for 300s.", file=sys.stderr)
                    else:
                        store.cache_extraction(key, result)
                    store.commit()
            results[session["id"]] = result
    return results


def rebuild(store: Store, project_ids=None, verbose=False, extraction_budget=None) -> dict:
    """Regenerate derived data from stored messages. Idempotent.

    project_ids=None rebuilds everything (full wipe). A subset rebuilds only those
    projects (incremental) - the daemon uses this so an edit to one repo does not
    re-derive all of them."""
    if project_ids is None:
        projects = store.list_projects()
        incremental = False
    else:
        ids = set(project_ids)
        projects = [p for p in store.list_projects() if p["id"] in ids]
        incremental = True
    store.commit()

    extractor = extractor_mod.get_extractor()  # chosen once per rebuild (auto-detects)
    generations = {p["id"]: store.rebuild_generation(p["id"]) for p in projects}
    extracted = _extract_sessions(store, projects, extractor, extraction_budget, verbose)
    totals = {"work_items": 0, "candidates": 0, "promoted": 0}
    for index, project in enumerate(projects, 1):
        if verbose:
            print(f"[rebuild] Project {index}/{len(projects)}", flush=True)
        with store.conn:
            _wipe_project(store, project["id"])
            totals_p = _rebuild_project(store, project, extractor, verbose=verbose, extracted=extracted)
        for k in totals:
            totals[k] += totals_p[k]
    totals["extractor"] = extractor.name
    totals["incremental"] = incremental
    store.commit()
    _populate_vectors(store)
    with store.conn:
        for project in projects:
            store.clear_rebuild_pending(project["id"], generations[project["id"]])
    return totals


def _populate_vectors(store: Store) -> None:
    """Rebuild the semantic index when a vector store is active (else a no-op)."""
    from .storage.vector_store import get_vector_store

    vstore = get_vector_store(store.path)
    if not getattr(vstore, "available", False):
        return
    vstore.reset()
    wis = [(w["id"], " ".join(filter(None, [w.get("title"), w.get("summary"), w.get("aliases")])))
           for w in store.conn.execute("SELECT id,title,summary,aliases FROM work_items")]
    ents = [(e["id"], " ".join(filter(None, [e.get("title"), e.get("body")])))
            for e in store.conn.execute("SELECT id,title,body FROM entities")]
    vstore.add_many("workitem", wis)
    vstore.add_many("entity", ents)


def _make_sha_validator(store: Store, root):
    """Persistent SHA validator: DB cache first, then git, caching the result."""
    if not root:
        return lambda sha: False

    def validate(sha):
        cached = store.sha_cached(root, sha)
        if cached is not None:
            return cached
        present = gitutil.commit_exists(sha, root)
        store.cache_sha(root, sha, present)
        return present

    return validate


def _rebuild_project(store: Store, project: dict, extractor=None, verbose=False, extracted=None) -> dict:
    pid = project["id"]
    root = project["root_path"]
    sessions = store.project_sessions(pid)
    validate = _make_sha_validator(store, root)

    # ---- per-session deterministic artifacts + work signals ----
    # Synthetic/programmatic sessions (memory-log summarizers, compression jobs,
    # one-shot prompts) are not human coding work; they generate no WorkItems or
    # memories (sanitize.is_automated_session). On the real corpus they were 48%
    # of sessions and 84% of "Untitled work".
    from .sanitize import is_automated_session
    signals = []
    session_artifacts = {}
    session_msgs = {}
    work_sessions = []
    for s in sessions:
        msgs = store.session_messages(s["id"])
        session_msgs[s["id"]] = msgs
        if is_automated_session(msgs):
            continue
        work_sessions.append(s)
        arts = deterministic.session_artifacts(msgs, root, validate=validate)
        session_artifacts[s["id"]] = arts
        signals.append(wi_mod.build_session_signal(s, msgs, arts["files"]))
    sessions = work_sessions

    builders = wi_mod.resolve(signals)

    # ---- apply human corrections (override automated inference; ARCHITECTURE.md 13) ----
    corr = correction.load(store, pid)
    sess_files = {sid: set(a["files"]) for sid, a in session_artifacts.items()}
    builders = correction.apply_to_builders(builders, corr, sess_files)

    # ---- persist WorkItems + membership graph ----
    session_to_wi = {}
    wi_ids = []
    project_node = store.node_id(pid, "project", pid)
    for b in builders:
        has_commit = any(session_artifacts[sid]["shas"] for sid in b["members"])
        # A single session that actually edited multiple files is real work, not a
        # tentative candidate. Without this, 89% of solo-dev work stayed 'candidate'
        # (Phase 1) - the candidate tier should mean thin/uncertain, not "solo".
        substantive = len(b["files"]) >= 2
        lifecycle = "active" if (has_commit or len(b["members"]) >= 2 or substantive) else "candidate"
        file_sets = [set(session_artifacts[sid]["files"]) for sid in b["members"]]
        conf = confidence.score(
            file_overlap=confidence.cohesion(file_sets),
            has_commit=has_commit,
            n_sessions=len(b["members"]),
            n_agents=len(b["agents"]),
            span_days=_span(b["started_at"], b["ended_at"]),
        )
        wi_id = store.insert_work_item(
            pid, kind=b["kind"], title=b["title"],
            summary=" / ".join(sorted(b["aliases"])) or b["title"],
            status="active", lifecycle=lifecycle, branch=b.get("branch"),
            aliases=list(b["aliases"]), files=sorted(b["files"]),
            confidence=conf, first_seen=b["started_at"], last_active=b["ended_at"],
        )
        if b.get("name_locked"):
            store.update_work_item(wi_id, name_locked=1)
        store.index_work_item_fts(store.get_work_item(wi_id))
        wi_ids.append(wi_id)
        wi_node = store.node_id(pid, "workitem", wi_id)
        store.add_edge(wi_node, project_node, "PART_OF")
        for sid in b["members"]:
            session_to_wi[sid] = wi_id
            store.add_edge(store.node_id(pid, "session", sid), wi_node, "CONTRIBUTES_TO")

    # RELATED edges (sub-HIGH resolution near-matches)
    for idx, b in enumerate(builders):
        for rel_idx in b["related"]:
            if rel_idx < len(wi_ids) and idx < len(wi_ids):
                a = store.node_id(pid, "workitem", wi_ids[idx])
                c = store.node_id(pid, "workitem", wi_ids[rel_idx])
                store.add_edge(a, c, "RELATED", 0.5)

    # ---- commits + files graph (IMPLEMENTS / MODIFIED_FOR) ----
    for s in sessions:
        wi_id = session_to_wi.get(s["id"])
        if not wi_id:
            continue
        wi_node = store.node_id(pid, "workitem", wi_id)
        arts = session_artifacts[s["id"]]
        for rel in arts["files"]:
            fid = store.upsert_file(pid, rel)
            store.add_edge(store.node_id(pid, "file", fid), wi_node, "MODIFIED_FOR")
        for sha in arts["shas"]:
            info = gitutil.commit_info(sha, root) or {"sha": sha}
            cid = store.upsert_commit(pid, info)
            store.add_edge(store.node_id(pid, "commit", cid), wi_node, "IMPLEMENTS")
            for cf in gitutil.commit_files(sha, root):
                store.link_commit_file(cid, store.upsert_file(pid, cf))
    if project.get("git_remote") or root:
        for s in sessions:
            store.upsert_branch(pid, s.get("branch"), s.get("head_sha"))

    # ---- candidate memories + cross-session merge ----
    # default heuristic; opt into the local-LLM extractor with LOOMA_EXTRACTOR=llm
    # (it falls back to heuristic per-session if no local model server is reachable).
    _extractor = extractor or extractor_mod.get_extractor()
    _use_extractor = _extractor.name != "heuristic"
    merged: dict[tuple, dict] = {}
    for index, s in enumerate(sessions, 1):
        wi_id = session_to_wi.get(s["id"])
        model = s.get("agent_model")
        if verbose:
            print(f"[rebuild] Extracting {s['source']} session {index}/{len(sessions)}", flush=True)
        if _use_extractor:
            cands = [{"kind": mm["kind"], "title": mm["title"], "body": mm["title"],
                      "ts": None, "message_id": None}
                     for mm in extracted[s["id"]].get("memories", [])]
        else:
            cands = cand_mod.extract_candidates(session_msgs[s["id"]])
        for c in cands:
            key = (c["kind"], c["title"].lower())
            if key in merged:
                m = merged[key]
                m["session_refs"].add(s["id"])
                if model:
                    m["agent_refs"].add(model)
                m["last_seen"] = c.get("ts") or m["last_seen"]
                if c.get("message_id"):
                    m["evidence"].append(c["message_id"])
            else:
                merged[key] = {
                    "kind": c["kind"], "title": c["title"], "body": c["body"],
                    "work_item_id": wi_id, "session_refs": {s["id"]},
                    "agent_refs": {model} if model else set(),
                    "first_seen": c.get("ts"), "last_seen": c.get("ts"),
                    "evidence": [c["message_id"]] if c.get("message_id") else [],
                }

    n_candidates = n_promoted = 0
    for m in merged.values():
        wi = store.get_work_item(m["work_item_id"]) if m["work_item_id"] else None
        wi_commits = bool(store.work_item_commits(pid, wi["id"])) if wi else False
        own = confidence.score(
            file_overlap=0.0,
            has_commit=wi_commits,
            n_sessions=len(m["session_refs"]),
            n_agents=len(m["agent_refs"]),
            span_days=_span(m["first_seen"], m["last_seen"]),
        )
        # Calibration (V2): a memory documents its WorkItem, so it inherits that
        # work's grounding. Without this a memory's file_overlap is always 0, so
        # promoted memories scored ~0.00 confidence - "validated" yet near-zero.
        # Blend own corroboration with the parent WorkItem's confidence.
        wi_conf = wi["confidence"] if wi else 0.0
        cand_conf = round(min(1.0, 0.6 * own + 0.4 * wi_conf), 4)
        # human-correction override on this memory (ARCHITECTURE.md 13.2)
        override = corr.mem.get((m["kind"], correction.norm(m["title"])))
        force_p = override == "promote"
        force_r = override == "reject"
        if force_p:
            cand_conf = 1.0
        elif force_r:
            cand_conf = 0.0
        cid = store.insert_candidate(
            pid, kind=m["kind"], title=m["title"], body=m["body"],
            status="open", session_refs=list(m["session_refs"]),
            agent_refs=list(m["agent_refs"]), first_seen=m["first_seen"],
            last_seen=m["last_seen"], confidence=cand_conf,
            work_item_id=m["work_item_id"],
        )
        n_candidates += 1

        if promote.should_promote(
            commit_linked=wi_commits,
            work_item_active=bool(wi and wi["lifecycle"] == "active"),
            n_sessions=len(m["session_refs"]),
            force_promote=force_p,
            force_reject=force_r,
        ):
            eid = store.insert_entity(
                pid, kind=m["kind"], title=m["title"], body=m["body"],
                status="open", work_item_id=m["work_item_id"],
                promoted_from_candidate_id=cid, confidence=cand_conf,
            )
            store.update_candidate(cid, state="promoted", promoted_entity_id=eid)
            for mid in m["evidence"]:
                store.conn.execute(
                    "INSERT INTO entity_evidence(entity_id, message_id) VALUES(?,?)",
                    (eid, mid),
                )
            rel = promote.KIND_REL.get(m["kind"], "CONSTRAINS")
            if m["work_item_id"]:
                store.add_edge(
                    store.node_id(pid, "entity", eid),
                    store.node_id(pid, "workitem", m["work_item_id"]),
                    rel,
                )
            n_promoted += 1

    return {"work_items": len(wi_ids), "candidates": n_candidates, "promoted": n_promoted}


def _span(first, last) -> float:
    from .util import span_days

    return span_days(first, last)
