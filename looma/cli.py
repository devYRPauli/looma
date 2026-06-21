"""looma CLI - init, ingest, work, resume, ask, status, doctor, reset (+ reprocess)."""

import argparse
import os
import sys
import time
from pathlib import Path

from . import config, doctor, identity, pipeline
from .retrieval import ask as ask_mod
from .retrieval import resume as resume_mod
from .storage.sqlite_store import Store

PRIVACY = "Local-first: your transcript contents never leave this machine. No cloud, no API key."


def _db_path(args) -> Path:
    return Path(args.db or config.default_db_path())


def _open_store(args) -> Store:
    return Store.open(_db_path(args))


def _vstore(args):
    from .storage.vector_store import get_vector_store
    return get_vector_store(_db_path(args))


def _conf(conf) -> str:
    conf = conf or 0.0
    return f"conf {conf:.2f} ({config.band(conf)})"


def _files_preview(files_json: str, n: int = 3) -> tuple[int, str]:
    import json

    try:
        files = json.loads(files_json or "[]")
    except (ValueError, TypeError):
        files = []
    preview = ", ".join(files[:n]) + (" ..." if len(files) > n else "")
    return len(files), preview


def _resolve_current_project(store: Store):
    ident = identity.resolve(os.getcwd())
    if not ident:
        return None
    return store.find_project_by_key(ident["canonical_key"])


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_init(args) -> int:
    path = _db_path(args)
    existed = path.exists()
    store = Store.open(path)
    store.migrate()
    store.close()
    print(f"{'Re-initialized' if existed else 'Initialized'} looma at {path}")
    print(PRIVACY)
    if not config.claude_projects_dir().exists():
        print(f"\nNote: no Claude history found at {config.claude_projects_dir()} yet.")
    else:
        print("\nNext: `looma ingest --once`")
    return 0


def cmd_ingest(args) -> int:
    claude_dir = config.claude_projects_dir()
    if not claude_dir.exists():
        print(f"error: no Claude Code history found at {claude_dir}", file=sys.stderr)
        print("Claude Code stores sessions there once you have used it in a project.",
              file=sys.stderr)
        return 1

    project_filter = None
    if args.project:
        target = Path(os.path.expanduser(args.project))
        if not target.exists():
            print(f"error: --project path does not exist: {target}", file=sys.stderr)
            return 1
        ident = identity.resolve(str(target))
        if not ident:
            print(f"error: could not resolve a project for {target}", file=sys.stderr)
            return 1
        project_filter = ident["canonical_key"]

    store = _open_store(args)
    store.migrate()

    t0 = time.perf_counter()
    ing = pipeline.ingest_messages(
        store, limit=args.limit, project_filter=project_filter, verbose=args.verbose
    )
    t1 = time.perf_counter()
    built = pipeline.rebuild(store)
    t2 = time.perf_counter()
    counts = store.counts()
    store.close()

    print(f"DB: {_db_path(args)}")
    if ing["sessions"] == 0:
        if project_filter:
            print(f"No Claude sessions matched project '{project_filter}'.")
        else:
            print("No Claude sessions found to ingest.")
        return 0
    print(f"Ingested {ing['sessions']} sessions, {ing['new_messages']} new messages"
          + (f" (skipped {ing['skipped']} outside --project)" if ing.get('skipped') else ""))
    print(f"Indexed:  {counts['projects']} projects, {counts['sessions']} sessions, "
          f"{counts['messages']} messages")
    print(f"Built:    {built['work_items']} work items, {built['candidates']} candidate "
          f"memories, {built['promoted']} promoted to validated memory")
    print(f"Extraction: {built.get('extractor', 'heuristic')}"
          + ("  (local LLM detected)" if built.get('extractor') == 'llm'
             else "  (set up a local model server for higher-quality extraction; see `looma doctor`)"))
    if args.verbose:
        print(f"\n[timing] ingest {t1 - t0:.1f}s · build {t2 - t1:.1f}s · total {t2 - t0:.1f}s")
    return 0


def cmd_reprocess(args) -> int:
    store = _open_store(args)
    store.migrate()
    t0 = time.perf_counter()
    built = pipeline.rebuild(store)
    dt = time.perf_counter() - t0
    store.close()
    print(f"Rebuilt graph from raw events + ledger: {built['work_items']} work items, "
          f"{built['candidates']} candidates, {built['promoted']} promoted "
          f"(extraction: {built.get('extractor', 'heuristic')})")
    if args.verbose:
        print(f"[timing] {dt:.1f}s")
    return 0


def _pick_project(store, args):
    if getattr(args, "project", None):
        proj = store.find_project_by_key(args.project)
        if not proj:
            print(f"No project with key '{args.project}'. Try `looma status`.", file=sys.stderr)
        return proj
    proj = _resolve_current_project(store)
    if not proj:
        print(f"No Looma project for the current directory ({os.getcwd()}).", file=sys.stderr)
        projs = store.list_projects()
        if projs:
            print("\nKnown projects (cd into one, or pass --project KEY):", file=sys.stderr)
            for p in projs:
                print(f"  {p['canonical_key']}", file=sys.stderr)
        else:
            print("Nothing ingested yet. Run `looma ingest --once`.", file=sys.stderr)
    return proj


def cmd_work(args) -> int:
    store = _open_store(args)
    proj = _pick_project(store, args)
    if not proj:
        store.close()
        return 1
    wis = store.project_work_items(proj["id"])
    if args.status:
        wis = [w for w in wis if args.status in (w["status"], w["lifecycle"])]
    print(f"WorkItems for {proj['display_name']} ({proj['canonical_key']})   [{len(wis)} items]\n")
    if not wis:
        print("  (none yet - run `looma ingest --once`)")
    for w in wis:
        nfiles, preview = _files_preview(w.get("files"))
        last = (w.get("last_active") or "?")[:10]
        print(f"  #{w['id']:<4} {w['title']}")
        print(f"       {w['kind']} · {w['lifecycle']} · {_conf(w['confidence'])} · "
              f"last {last} · {nfiles} files")
        if preview:
            print(f"       files: {preview}")
    store.close()
    return 0


def _print_bundle(b, indent="  "):
    wi = b["work_item"]
    print(f"{indent}WORKITEM #{wi['id']}: {wi['title']}")
    print(f"{indent}  {wi['kind']} · {wi['lifecycle']} · {_conf(wi['confidence'])}")
    if b["decisions"]:
        print(f"\n{indent}CONSTRAINTS (decisions / architecture)")
        for e in b["decisions"][:5]:
            print(f"{indent}  - {e['title']}")
    if b["todos"]:
        print(f"\n{indent}UNFINISHED / BLOCKING")
        for e in b["todos"][:6]:
            print(f"{indent}  [ ] {e['title']}")
    if b["bugs"]:
        print(f"\n{indent}AFFECTING BUGS")
        for e in b["bugs"][:4]:
            print(f"{indent}  (!) {e['title']}")
    if b["sessions"]:
        print(f"\n{indent}RECENT SESSIONS")
        for s in b["sessions"][:4]:
            when = (s.get("ended_at") or "?")[:10]
            print(f"{indent}  - {when}  {s.get('source')}/{(s.get('agent_model') or '?')}")
    if b["commits"]:
        print(f"\n{indent}COMMITS")
        for c in b["commits"][:4]:
            print(f"{indent}  - {c['sha'][:9]} {(c.get('message') or '')[:60]}")
    if b["files"]:
        print(f"\n{indent}FILES FOR THIS WORK")
        print(f"{indent}  " + ", ".join(b["files"][:8]) + (" ..." if len(b["files"]) > 8 else ""))
    if b["next_step"]:
        print(f"\n{indent}NEXT LIKELY STEP: {b['next_step']}")


def cmd_resume(args) -> int:
    store = _open_store(args)
    proj = _pick_project(store, args)
    if not proj:
        store.close()
        return 1
    goal = " ".join(args.goal or [])
    res = resume_mod.resume(store, proj, goal, vstore=_vstore(args))
    git = res.get("git", {})

    header = f"PROJECT: {proj['display_name']} ({proj['canonical_key']})"
    if git.get("branch") or git.get("head"):
        bits = []
        if git.get("branch"):
            bits.append(f"branch {git['branch']}")
        if git.get("head"):
            bits.append(f"head {git['head'][:9]}")
        if git.get("dirty"):
            bits.append(f"{len(git['dirty'])} dirty")
        header += "\n         " + ", ".join(bits)
    print(header)
    if goal:
        print(f"GOAL: {goal}")

    mode = res["mode"]
    if mode == resume_mod.EMPTY:
        print("\nNo work items for this project yet. Run `looma ingest --once`.")
        store.close()
        return 0
    if mode == resume_mod.COLD and res.get("reason") == "no_match":
        print(f"\nNo confident match for '{goal}'. Recent work to start from:")
        for w in res["alternatives"]:
            print(f"  #{w['id']} {w['title']}  [{_conf(w['confidence'])}]")
        store.close()
        return 0

    banner = {
        resume_mod.CONFIDENT: "[CONFIDENT MATCH]",
        resume_mod.AMBIGUOUS: "[AMBIGUOUS - kept separate, not collapsed]",
        resume_mod.COLD: "[LOW CONFIDENCE - a starting point, not a certainty]",
        resume_mod.NO_GOAL: "[MOST RECENTLY ACTIVE - no goal given]",
    }.get(mode, "")
    print(f"\n{banner}\n")
    _print_bundle(res["bundle"])

    if res.get("alternatives"):
        print("\n  OTHER CANDIDATE WORK ITEMS (not merged):")
        for w in res["alternatives"]:
            print(f"    #{w['id']} {w['title']}  [{_conf(w['confidence'])}]")
        print("\n  Narrow with:  looma resume --project <key> '<more specific goal>'")
    store.close()
    return 0


def cmd_timeline(args) -> int:
    from . import timeline as tl
    from .correction import resolve_workitem
    from .retrieval.match import match_work_items
    store = _open_store(args)
    proj = _pick_project(store, args)
    if not proj:
        store.close()
        return 1
    token = " ".join(args.work or [])
    wi = None
    if token:
        wi = resolve_workitem(store, proj["id"], token)  # '#5' / '5'
        if not wi:
            hits = match_work_items(store, proj["id"], token, vstore=_vstore(args))
            wi = hits[0] if hits else None
    else:
        wis = store.project_work_items(proj["id"])
        wi = wis[0] if wis else None
    if not wi:
        print(f"no work item matching '{token}' (see `looma work`)", file=sys.stderr)
        store.close()
        return 1
    print(tl.format_timeline(wi, tl.build(store, proj["id"], wi["id"])))
    store.close()
    return 0


def cmd_ask(args) -> int:
    store = _open_store(args)
    proj = _pick_project(store, args)
    if not proj:
        store.close()
        return 1
    query = " ".join(args.query or [])
    results = ask_mod.ask(store, proj["id"], query, vstore=_vstore(args))
    print(f"Q: {query}\n")
    if not results:
        print("  No matches in validated memory or work items.")
    for r in results:
        ctx = f"   (work: {r['work_item']})" if r.get("work_item") else ""
        print(f"  [{r['type']}/{r['kind']}] {r['title']}  [{_conf(r['confidence'])}]{ctx}")
    store.close()
    return 0


def cmd_correct(args) -> int:
    from . import correction
    store = _open_store(args)
    proj = _pick_project(store, args)
    if not proj:
        store.close()
        return 1
    pid = proj["id"]
    action = args.action
    rest = args.args or []

    def need_wi(tok):
        wi = correction.resolve_workitem(store, pid, tok)
        if not wi:
            print(f"no work item '{tok}' in this project (see `looma work`)", file=sys.stderr)
        return wi

    rebuilt = True
    if action == "merge":
        if len(rest) != 2:
            print("usage: looma correct merge <idA> <idB>", file=sys.stderr); store.close(); return 1
        a, b = need_wi(rest[0]), need_wi(rest[1])
        if not (a and b):
            store.close(); return 1
        sa = correction.workitem_sessions(store, pid, a["id"])
        sb = correction.workitem_sessions(store, pid, b["id"])
        lid = correction.correct(store, pid, "merge", {"a": sa, "b": sb})
        print(f"merged #{a['id']} + #{b['id']} (correction #{lid})")
    elif action == "split":
        if len(rest) != 1:
            print("usage: looma correct split <id> [--session <sid>]", file=sys.stderr); store.close(); return 1
        wi = need_wi(rest[0])
        if not wi:
            store.close(); return 1
        sess = store.work_item_sessions(pid, wi["id"])
        ids = [s["id"] for s in sess]
        if args.session:
            if args.session not in ids:
                print(f"session {args.session} is not in #{wi['id']}", file=sys.stderr); store.close(); return 1
            a, b = [args.session], [i for i in ids if i != args.session]
        else:  # split by branch
            from collections import defaultdict
            bybr = defaultdict(list)
            for s in sess:
                bybr[s.get("branch") or "?"].append(s["id"])
            if len(bybr) < 2:
                print("nothing to split (single branch); use --session <sid>", file=sys.stderr); store.close(); return 1
            grps = list(bybr.values())
            a, b = grps[0], [i for g in grps[1:] for i in g]
        lid = correction.correct(store, pid, "split", {"a": a, "b": b})
        print(f"split #{wi['id']} (correction #{lid})")
    elif action == "rename":
        if len(rest) < 2:
            print("usage: looma correct rename <id> <new title>", file=sys.stderr); store.close(); return 1
        wi = need_wi(rest[0])
        if not wi:
            store.close(); return 1
        title = " ".join(rest[1:])
        sa = correction.workitem_sessions(store, pid, wi["id"])
        lid = correction.correct(store, pid, "rename", {"sessions": sa, "title": title})
        print(f"renamed #{wi['id']} -> {title!r} (correction #{lid})")
    elif action in ("promote", "reject", "false-positive"):
        if not rest:
            print(f"usage: looma correct {action} <text of the memory>", file=sys.stderr); store.close(); return 1
        mem = correction.find_memory(store, pid, " ".join(rest))
        if not mem:
            print("no matching memory found", file=sys.stderr); store.close(); return 1
        act = "false_positive" if action == "false-positive" else action
        lid = correction.correct(store, pid, act, {"kind": mem[0], "title": mem[1]})
        print(f"{action}: [{mem[0]}] {mem[1][:60]} (correction #{lid})")
    elif action == "undo":
        if len(rest) != 1 or not rest[0].isdigit():
            print("usage: looma correct undo <correction_id>", file=sys.stderr); store.close(); return 1
        if correction.undo(store, int(rest[0])) is None:
            print(f"no correction #{rest[0]}", file=sys.stderr); store.close(); return 1
        print(f"undid correction #{rest[0]}")
    elif action == "log":
        rebuilt = False
        for e in correction.ledger_entries(store, pid):
            print(f"  #{e['id']} {e['action_type']:14} {e['ts'][:19]}  {e.get('rationale') or ''}")
    if rebuilt:
        pipeline.rebuild(store)
    store.close()
    return 0


def cmd_benchmark(args) -> int:
    if args.retrieval:
        from .benchmark import retrieval
        print(retrieval.compare())
        return 0
    from .benchmark import harness
    if args.compare:
        print(harness.compare())
    else:
        from .extraction.extractor import HeuristicExtractor
        print(harness.format_one(harness.run(HeuristicExtractor())))
    return 0


def cmd_status(args) -> int:
    store = _open_store(args)
    c = store.counts()
    print("looma status")
    print(f"  db:         {_db_path(args)}")
    print(f"  projects:   {c['projects']}")
    print(f"  sessions:   {c['sessions']}")
    print(f"  messages:   {c['messages']}")
    print(f"  work items: {c['work_items']}")
    print(f"  memories:   {c['candidates']} candidate "
          f"({c['promoted']} promoted -> {c['entities']} validated)")
    print(f"  commits:    {c['commits']}")
    if getattr(args, "health", False):
        from . import health
        print()
        print(health.format_health(health.compute(store)))
    if c["sessions"] == 0:
        print("\n  Nothing ingested yet. Run `looma ingest --once`.")
    cur = _resolve_current_project(store)
    if cur:
        print(f"\n  current dir -> {cur['display_name']} ({cur['canonical_key']})")
    else:
        print(f"\n  current dir ({os.getcwd()}) is not a known project.")
    store.close()
    return 0


def cmd_doctor(args) -> int:
    checks = doctor.run(_db_path(args))
    sym = {doctor.OK: "[ OK ]", doctor.WARN: "[WARN]", doctor.FAIL: "[FAIL]"}
    print("looma doctor\n")
    worst_ok = True
    for name, status, detail in checks:
        print(f"  {sym[status]}  {name:18} {detail}")
        if status == doctor.FAIL:
            worst_ok = False
    print(f"\n{PRIVACY}")
    if not worst_ok:
        print("\nOne or more checks FAILED - looma may not run correctly.", file=sys.stderr)
        return 1
    return 0


def cmd_reset(args) -> int:
    path = _db_path(args)
    targets = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    existing = [p for p in targets if p.exists()]
    if not args.confirm:
        print("WARNING: `looma reset` permanently deletes the local Looma database.")
        print("Your Claude transcripts are NOT touched - only Looma's derived store.")
        print(f"\nWould delete:")
        for p in existing or [path]:
            print(f"  {p}")
        print("\nRe-run with --confirm to proceed:  looma reset --confirm")
        return 1
    for p in existing:
        p.unlink()
    print(f"Deleted Looma database ({len(existing)} file(s)). Run `looma init` to start fresh.")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="path to the looma store (default ~/.looma/looma.db)")
    common.add_argument("--verbose", action="store_true", help="show timing / extra detail")

    p = argparse.ArgumentParser(
        prog="looma",
        description="Looma - resumable project context from your coding-agent history (local-first)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[common], help="create the store and run migrations").set_defaults(
        func=cmd_init)

    pi = sub.add_parser("ingest", parents=[common], help="ingest Claude history + build the graph")
    pi.add_argument("--once", action="store_true", help="single ingest pass (default)")
    pi.add_argument("--limit", type=int, help="ingest at most N sessions (quick demos)")
    pi.add_argument("--project", help="only ingest sessions under this path")
    pi.set_defaults(func=cmd_ingest)

    sub.add_parser("reprocess", parents=[common], help="rebuild graph from stored events").set_defaults(
        func=cmd_reprocess)

    pw = sub.add_parser("work", parents=[common], help="list work items for the current project")
    pw.add_argument("--project", help="project canonical key (default: current dir)")
    pw.add_argument("--status", help="filter by status/lifecycle")
    pw.set_defaults(func=cmd_work)

    pr = sub.add_parser("resume", parents=[common], help="WorkItem-first resume bundle")
    pr.add_argument("goal", nargs="*", help="optional goal, e.g. auth")
    pr.add_argument("--project", help="project canonical key (default: current dir)")
    pr.set_defaults(func=cmd_resume)

    ptl = sub.add_parser("timeline", parents=[common], help="show a work item's evolution over time")
    ptl.add_argument("work", nargs="*", help="work item id (#5) or goal text")
    ptl.add_argument("--project", help="project canonical key (default: current dir)")
    ptl.set_defaults(func=cmd_timeline)

    pa = sub.add_parser("ask", parents=[common], help="search validated memory + work items")
    pa.add_argument("query", nargs="*", help="question / keywords")
    pa.add_argument("--project", help="project canonical key (default: current dir)")
    pa.set_defaults(func=cmd_ask)

    pc = sub.add_parser("correct", parents=[common], help="human corrections (merge/split/rename/promote/reject/false-positive/undo/log)")
    pc.add_argument("action", choices=["merge", "split", "rename", "promote", "reject", "false-positive", "undo", "log"])
    pc.add_argument("args", nargs="*")
    pc.add_argument("--session", type=int, help="session id to peel off (split)")
    pc.add_argument("--project", help="project canonical key (default: current dir)")
    pc.set_defaults(func=cmd_correct)

    pb = sub.add_parser("benchmark", parents=[common], help="extraction precision/recall/F1 on golden fixtures")
    pb.add_argument("--compare", action="store_true", help="compare heuristic vs local-LLM extractor")
    pb.add_argument("--retrieval", action="store_true", help="benchmark retrieval: FTS-only vs FTS+vectors")
    pb.set_defaults(func=cmd_benchmark)

    pst = sub.add_parser("status", parents=[common], help="store + current-project overview")
    pst.add_argument("--health", action="store_true", help="show graph health metrics")
    pst.set_defaults(func=cmd_status)
    sub.add_parser("doctor", parents=[common], help="diagnose the local environment").set_defaults(
        func=cmd_doctor)

    prs = sub.add_parser("reset", parents=[common], help="delete the local Looma database")
    prs.add_argument("--confirm", action="store_true", help="actually delete (required)")
    prs.set_defaults(func=cmd_reset)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    start = time.perf_counter()
    rc = args.func(args)
    if getattr(args, "verbose", False) and args.cmd not in ("ingest", "reprocess"):
        print(f"\n[timing] {time.perf_counter() - start:.2f}s", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
