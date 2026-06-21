"""WorkItem-first Resume engine with uncertainty handling (ARCHITECTURE.md 10).

Resolves an optional goal to a WorkItem, decides confident vs ambiguous vs cold
per section 10.1 (never silently collapses low-confidence work), and assembles a
bundle from the WorkItem's graph star.
"""

import json

from .. import config, gitutil
from .match import match_work_items

# modes
CONFIDENT = "confident"
AMBIGUOUS = "ambiguous"
COLD = "cold"
NO_GOAL = "no_goal"
EMPTY = "empty"


def _bundle_for(store, project: dict, wi: dict) -> dict:
    pid = project["id"]
    wid = wi["id"]
    decisions = [
        e for e in store.work_item_entities(wid)
        if e["kind"] in ("decision", "architecture")
    ]
    todos = store.work_item_entities(wid, "todo")
    bugs = store.work_item_entities(wid, "bug")
    sessions = store.work_item_sessions(pid, wid)
    commits = store.work_item_commits(pid, wid)
    try:
        files = json.loads(wi.get("files") or "[]")
    except (json.JSONDecodeError, TypeError):
        files = []

    open_todos = [t for t in todos if (t.get("status") or "open") == "open"]
    if open_todos:
        nxt = f"address todo: {open_todos[0]['title']}"
    elif bugs:
        nxt = f"investigate bug: {bugs[0]['title']}"
    elif files:
        nxt = f"continue editing {files[0]}"
    else:
        nxt = None

    return {
        "work_item": wi,
        "decisions": decisions,
        "todos": open_todos,
        "bugs": bugs,
        "sessions": sessions,
        "files": files,
        "commits": commits,
        "next_step": nxt,
    }


def resume(store, project: dict, goal: str = "", vstore=None) -> dict:
    pid = project["id"]
    root = project.get("root_path")
    git_state = {
        "branch": gitutil.current_branch(root) if root else None,
        "head": gitutil.head_sha(root) if root else None,
        "dirty": gitutil.dirty_files(root) if root else [],
    }

    all_wis = store.project_work_items(pid)
    if not all_wis:
        return {"mode": EMPTY, "project": project, "git": git_state}

    if not goal.strip():
        chosen = all_wis[0]  # most-recently-active
        return {
            "mode": NO_GOAL, "project": project, "git": git_state,
            "bundle": _bundle_for(store, project, chosen), "alternatives": [],
        }

    matches = match_work_items(store, pid, goal, vstore=vstore)
    if not matches:
        return {
            "mode": COLD, "project": project, "git": git_state, "reason": "no_match",
            "alternatives": all_wis[:3],
        }

    c1 = matches[0].get("confidence") or 0.0
    c2 = matches[1].get("confidence") or 0.0 if len(matches) > 1 else 0.0

    if c1 < config.RESUME_LOW:
        # cold: a match exists but confidence is too low to assert
        return {
            "mode": COLD, "project": project, "git": git_state, "reason": "low_confidence",
            "bundle": _bundle_for(store, project, matches[0]),
            "alternatives": matches[1:3],
        }

    if c1 >= config.RESUME_HIGH and (c1 - c2) >= config.RESUME_MARGIN:
        return {
            "mode": CONFIDENT, "project": project, "git": git_state,
            "bundle": _bundle_for(store, project, matches[0]), "alternatives": [],
        }

    # ambiguous: never collapse - show the top pick's bundle PLUS the alternatives
    return {
        "mode": AMBIGUOUS, "project": project, "git": git_state,
        "bundle": _bundle_for(store, project, matches[0]),
        "alternatives": matches[1:3],
    }
