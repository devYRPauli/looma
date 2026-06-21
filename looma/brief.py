"""`looma brief` - a 60-second project orientation.

Assembles a one-screen brief: what the project is, what is actively in flight,
the decisions that constrain it, current risks and blockers, recent commits, and
the single best next thing to do. Built entirely from already-derived WorkItems,
memories, and commits - no new extraction.
"""

import json

import re

from . import gitutil
from .retrieval import resume as resume_mod
from .sanitize import looks_like_code
from .util import to_ascii

# A "bug" memory phrased as already-handled is not a current risk.
_RESOLVED = re.compile(r"(?i)(?:^\s*(?:i\s+)?(?:fixed|resolved|implemented|done|fix:))|"
                       r"(?:\b(?:now fixed|already fixed|has been fixed|is fixed)\b)")


def _project_entities(store, pid, kinds, limit=6, drop_resolved=False):
    """Recent, de-duplicated, prose memories of the given kinds for a project.

    Ordered by the parent effort's recency; code/diff-line memories dropped. When
    drop_resolved is set, memories phrased as already-handled are excluded (so
    risks list live concerns, not past fixes)."""
    marks = ",".join("?" * len(kinds))
    rows = store.conn.execute(
        f"""SELECT e.kind, e.title, e.confidence, e.work_item_id,
                   w.last_active AS wi_last, w.title AS wi_title
            FROM entities e LEFT JOIN work_items w ON w.id = e.work_item_id
            WHERE e.project_id=? AND e.kind IN ({marks})
            ORDER BY w.last_active DESC, e.id DESC""",
        (pid, *kinds),
    ).fetchall()
    out, seen = [], set()
    for r in rows:
        title = to_ascii((r["title"] or "").strip())
        key = title.lower()
        if not title or key in seen or looks_like_code(title):
            continue
        if drop_resolved and _RESOLVED.search(title):
            continue
        seen.add(key)
        row = dict(r)
        row["title"] = title
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _files(wi):
    try:
        return json.loads(wi.get("files") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _themes(wis, limit=4):
    """Dominant top-level directories across the project's work - a quick 'what'."""
    tops = {}
    for w in wis:
        for f in _files(w):
            top = f.split("/")[0]
            if top and not top.startswith("."):
                tops[top] = tops.get(top, 0) + 1
    return [t for t, _ in sorted(tops.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def build(store, project: dict, vstore=None) -> dict:
    pid = project["id"]
    root = project.get("root_path")
    wis = store.project_work_items(pid)
    active = [w for w in wis if w.get("lifecycle") == "active"]
    sessions = store.project_sessions(pid)

    ts = [s.get("ended_at") for s in sessions if s.get("ended_at")]
    span = (min(ts)[:10], max(ts)[:10]) if ts else (None, None)

    decisions = _project_entities(store, pid, ("decision", "architecture"))
    risks = _project_entities(store, pid, ("bug",), drop_resolved=True)
    blockers = _project_entities(store, pid, ("todo",))
    commits = [dict(r) for r in store.conn.execute(
        "SELECT sha, message, ts, author FROM commits WHERE project_id=? ORDER BY ts DESC LIMIT 6",
        (pid,),
    ).fetchall()]

    # single best next thing: reuse resume's no-goal inference
    res = resume_mod.resume(store, project, "", vstore=vstore)
    next_step = (res.get("bundle") or {}).get("next_step")

    git_state = {
        "branch": gitutil.current_branch(root) if root else None,
        "head": gitutil.head_sha(root) if root else None,
        "dirty": gitutil.dirty_files(root) if root else [],
    }

    return {
        "project": project,
        "git": git_state,
        "summary": {
            "work_items": len(wis),
            "active": len(active),
            "sessions": len(sessions),
            "span": span,
            "themes": _themes(wis),
        },
        "active_work": active[:5],
        "decisions": decisions,
        "risks": risks,
        "blockers": blockers,
        "commits": commits,
        "next_step": next_step,
    }


def _conf(c):
    c = c or 0.0
    return f"{c:.2f}"


def format_brief(b: dict) -> str:
    proj = b["project"]
    s = b["summary"]
    git = b["git"]
    lines = []
    lines.append(f"PROJECT: {proj['display_name']}  ({proj['canonical_key']})")
    meta = []
    if s["span"][0]:
        meta.append(f"{s['span'][0]} -> {s['span'][1]}")
    meta.append(f"{s['sessions']} sessions")
    meta.append(f"{s['work_items']} work items ({s['active']} active)")
    if git.get("branch"):
        b_ = f"branch {git['branch']}"
        if git.get("dirty"):
            b_ += f", {len(git['dirty'])} uncommitted"
        meta.append(b_)
    lines.append("  " + " | ".join(meta))
    if s["themes"]:
        lines.append("  areas: " + ", ".join(s["themes"]))

    def section(title, items, render):
        lines.append(f"\n{title}")
        if not items:
            lines.append("  (none)")
            return
        for it in items:
            lines.append("  " + render(it))

    section("ACTIVE WORK", b["active_work"],
            lambda w: f"#{w['id']} {to_ascii(w['title'])}  [{_conf(w['confidence'])}]")
    section("RECENT DECISIONS", b["decisions"],
            lambda e: f"- {e['title']}")
    section("CURRENT RISKS", b["risks"],
            lambda e: f"(!) {e['title']}")
    section("OPEN BLOCKERS", b["blockers"],
            lambda e: f"[ ] {e['title']}")
    section("RECENT COMMITS", b["commits"],
            lambda c: f"{(c['sha'] or '')[:9]} {to_ascii(c.get('message') or '')[:64]}")

    lines.append("\nSUGGESTED NEXT WORK")
    lines.append("  " + to_ascii(b["next_step"] or "(nothing obvious - run `looma resume` for the full bundle)"))
    return "\n".join(lines)
