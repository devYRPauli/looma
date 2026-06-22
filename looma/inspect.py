"""`looma inspect` - understand a repository without reading its transcripts.

Synthesizes a repo-level view from already-derived data (WorkItems, their files,
decisions/architecture/bug memories, sessions/agents) plus live git state:
  - architecture summary  : what the repo is, its design rules, its size
  - active systems        : the subsystems work actually concentrates in
  - ownership clusters     : which agents drove which systems
  - risks                  : open bugs + high-churn / low-consolidation areas
  - change hotspots        : the files touched most, weighted by recency

No new extraction - this is a read-side aggregation over the graph.
"""

from collections import defaultdict

from . import gitutil
from .brief import _project_entities
from .util import to_ascii

def _module(path: str) -> str:
    """The subsystem a file belongs to: its directory, capped at two levels so
    packages split into real systems (looma/retrieval, looma/storage) rather than
    collapsing to the package name."""
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:-1][:2])


def _files(wi) -> list:
    import json
    try:
        return json.loads(wi.get("files") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def build(store, project: dict, vstore=None) -> dict:
    pid = project["id"]
    root = project.get("root_path")
    wis = store.project_work_items(pid)
    sessions = store.project_sessions(pid)

    # --- systems: module -> file set, work-item count, recency, confidence ---
    sys_files = defaultdict(set)
    sys_wis = defaultdict(int)
    sys_last = defaultdict(str)
    sys_conf = defaultdict(float)
    file_touch = defaultdict(int)
    file_recency = {}
    for w in wis:
        last = w.get("last_active") or ""
        conf = w.get("confidence") or 0.0
        mods = set()
        for f in _files(w):
            m = _module(f)
            sys_files[m].add(f)
            mods.add(m)
            file_touch[f] += 1
            if last > file_recency.get(f, ""):
                file_recency[f] = last
        for m in mods:
            sys_wis[m] += 1
            if last > sys_last[m]:
                sys_last[m] = last
            sys_conf[m] = max(sys_conf[m], conf)

    systems = sorted(
        ({"module": m, "files": len(sys_files[m]), "work_items": sys_wis[m],
          "last_active": sys_last[m], "confidence": sys_conf[m]}
         for m in sys_files),
        key=lambda s: (s["files"], s["work_items"]), reverse=True,
    )

    # --- ownership: which agents drove each top system ---
    owners = defaultdict(lambda: defaultdict(int))
    for w in wis:
        mods = {_module(f) for f in _files(w)}
        if not mods:
            continue
        for s in store.work_item_sessions(pid, w["id"]):
            agent = (s.get("agent_model") or s.get("source") or "unknown")
            for m in mods:
                owners[m][agent] += 1
    ownership = {m: sorted(a.items(), key=lambda kv: kv[1], reverse=True)
                 for m, a in owners.items()}

    # --- hotspots: files touched by the most efforts (recency breaks ties) ---
    hotspots = sorted(
        ({"file": f, "touches": file_touch[f], "last": file_recency.get(f, "")}
         for f in file_touch),
        key=lambda h: (h["touches"], h["last"]), reverse=True,
    )[:10]

    # --- architecture summary + risks (from memory) ---
    architecture = _project_entities(store, pid, ("architecture", "decision"), limit=6)
    open_bugs = _project_entities(store, pid, ("bug",), limit=6, drop_resolved=True)

    # churn risk: lots of files churned but the work never consolidated (low conf)
    churn_risks = [s for s in systems
                   if s["files"] >= 4 and s["confidence"] < 0.20][:4]

    git_state = {
        "branch": gitutil.current_branch(root) if root else None,
        "dirty": gitutil.dirty_files(root) if root else [],
    }
    ts = [s.get("ended_at") for s in sessions if s.get("ended_at")]
    return {
        "project": project,
        "git": git_state,
        "summary": {
            "work_items": len(wis),
            "sessions": len(sessions),
            "systems": len(systems),
            "span": (min(ts)[:10], max(ts)[:10]) if ts else (None, None),
        },
        "systems": systems[:8],
        "ownership": ownership,
        "hotspots": hotspots,
        "architecture": architecture,
        "risks": open_bugs,
        "churn_risks": churn_risks,
    }


def format_inspect(x: dict) -> str:
    proj = x["project"]
    s = x["summary"]
    git = x["git"]
    L = [f"REPOSITORY: {proj['display_name']}  ({proj['canonical_key']})"]
    meta = []
    if s["span"][0]:
        meta.append(f"{s['span'][0]} -> {s['span'][1]}")
    meta.append(f"{s['sessions']} sessions")
    meta.append(f"{s['work_items']} work items")
    meta.append(f"{s['systems']} systems")
    if git.get("branch"):
        b = f"branch {git['branch']}"
        if git.get("dirty"):
            b += f", {len(git['dirty'])} uncommitted"
        meta.append(b)
    L.append("  " + " | ".join(meta))

    L.append("\nARCHITECTURE")
    if x["architecture"]:
        for e in x["architecture"]:
            L.append(f"  - {e['title']}")
    else:
        L.append("  (no design rules captured)")

    L.append("\nACTIVE SYSTEMS  (where work concentrates)")
    if x["systems"]:
        for sy in x["systems"]:
            own = x["ownership"].get(sy["module"], [])
            who = (", ".join(f"{a} x{n}" for a, n in own[:2])) if own else ""
            last = (sy["last_active"] or "")[:10]
            L.append(f"  {sy['module']:28} {sy['files']:3} files  {sy['work_items']:2} efforts"
                     + (f"  [{last}]" if last else "") + (f"  ({who})" if who else ""))
    else:
        L.append("  (no file-grounded systems)")

    L.append("\nCHANGE HOTSPOTS  (touched by the most efforts)")
    if x["hotspots"]:
        for h in x["hotspots"]:
            L.append(f"  {h['touches']:2}x  {(h['last'] or '')[:10]}  {h['file']}")
    else:
        L.append("  (none)")

    L.append("\nRISKS")
    risks = [f"  (!) {b['title']}" for b in x["risks"]]
    for sy in x["churn_risks"]:
        risks.append(f"  (~) {sy['module']}: {sy['files']} files churned, "
                     f"never consolidated (confidence {sy['confidence']:.2f})")
    L.extend(risks or ["  (none surfaced)"])

    return to_ascii("\n".join(L))
