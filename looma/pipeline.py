"""Orchestration: ingest messages, then rebuild the work graph deterministically.

Two phases keep the system idempotent (ARCHITECTURE.md 14):
  ingest_messages() - incremental, idempotent on event_hash.
  rebuild()         - drops derived tables and regenerates WorkItems, candidates,
                      confidence, promotion, and the graph from stored messages.
                      Equivalent to `looma reprocess`.
"""

import json

from . import confidence, correction, gitutil, identity
from .adapters.claude import ClaudeAdapter
from .config import claude_projects_dir
from .extraction import candidates as cand_mod
from .extraction import deterministic
from .extraction import extractor as extractor_mod
from .promotion import promote
from .resolution import workitems as wi_mod
from .storage.sqlite_store import Store

# child -> parent order so FK constraints hold during a full-rebuild wipe
_DERIVED_TABLES = [
    "edges", "nodes", "entity_evidence", "entities", "candidate_memories",
    "commit_files", "commits", "files", "branches", "work_items",
]


def ingest_messages(store: Store, projects_dir=None, limit=None, project_filter=None,
                    verbose=False) -> dict:
    """Discover Claude sessions, insert new messages idempotently. Returns counts.

    limit: stop after this many sessions are ingested (quick demos).
    project_filter: canonical_key; only ingest sessions resolving to that project.
    """
    base = projects_dir or claude_projects_dir()
    adapter = ClaudeAdapter(base)
    new_msgs = 0
    sessions_seen = 0
    skipped = 0
    for handle in adapter.discover():
        if limit is not None and sessions_seen >= limit:
            break
        events = list(adapter.read(handle))
        if not events:
            continue
        # project identity from the session's first event that carries a cwd
        root = next((e.project_root for e in events if e.project_root), None)
        ident = identity.resolve(root) or {
            "canonical_key": f"unknown:{handle.native_id}",
            "display_name": "unknown",
            "root_path": None,
            "git_remote": None,
        }
        if project_filter and ident["canonical_key"] != project_filter:
            skipped += 1
            continue
        sessions_seen += 1
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
        for ev in events:
            if store.insert_message(sid, ev) is not None:
                new_msgs += 1
    store.commit()
    return {"sessions": sessions_seen, "new_messages": new_msgs, "skipped": skipped}


def rebuild(store: Store) -> dict:
    """Regenerate all derived data from stored messages. Idempotent."""
    for t in _DERIVED_TABLES:
        store.conn.execute(f"DELETE FROM {t}")
    store.conn.execute("DELETE FROM fts_workitems")
    store.conn.execute("DELETE FROM fts_entities")
    store.commit()

    totals = {"work_items": 0, "candidates": 0, "promoted": 0}
    for project in store.list_projects():
        totals_p = _rebuild_project(store, project)
        for k in totals:
            totals[k] += totals_p[k]
    store.commit()
    return totals


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


def _rebuild_project(store: Store, project: dict) -> dict:
    pid = project["id"]
    root = project["root_path"]
    sessions = store.project_sessions(pid)
    validate = _make_sha_validator(store, root)

    # ---- per-session deterministic artifacts + work signals ----
    signals = []
    session_artifacts = {}
    session_msgs = {}
    for s in sessions:
        msgs = store.session_messages(s["id"])
        session_msgs[s["id"]] = msgs
        arts = deterministic.session_artifacts(msgs, root, validate=validate)
        session_artifacts[s["id"]] = arts
        signals.append(wi_mod.build_session_signal(s, msgs, arts["files"]))

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
        lifecycle = "active" if (has_commit or len(b["members"]) >= 2) else "candidate"
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
    _extractor = extractor_mod.get_extractor()
    _use_extractor = _extractor.name != "heuristic"
    merged: dict[tuple, dict] = {}
    for s in sessions:
        wi_id = session_to_wi.get(s["id"])
        model = s.get("agent_model")
        if _use_extractor:
            cands = [{"kind": mm["kind"], "title": mm["title"], "body": mm["title"],
                      "ts": None, "message_id": None}
                     for mm in _extractor.extract(session_msgs[s["id"]]).get("memories", [])]
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
        cand_conf = confidence.score(
            file_overlap=0.0,
            has_commit=wi_commits,
            n_sessions=len(m["session_refs"]),
            n_agents=len(m["agent_refs"]),
            span_days=_span(m["first_seen"], m["last_seen"]),
        )
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

    store.commit()
    return {"work_items": len(wi_ids), "candidates": n_candidates, "promoted": n_promoted}


def _span(first, last) -> float:
    from .util import span_days

    return span_days(first, last)
