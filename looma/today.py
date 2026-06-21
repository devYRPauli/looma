"""`looma today` - the daily driver.

One zero-argument command for the start of a work session. Answers, for the
current project: what am I working on, what changed recently, what is blocked,
what should I do next - then points at the other repos touched recently so a
context switch is one command, not six. Combines resume + brief + recent activity
+ a light trust signal; adds nothing new to the graph.
"""

from datetime import datetime, timedelta

from . import brief as brief_mod
from .retrieval import resume as resume_mod
from .util import to_ascii


def _concise(items, n=4, maxlen=90):
    """Prefer the most scannable blockers/risks: crisp (shorter) ones first, then
    truncate. Heuristic extraction sometimes promotes whole conversational
    sentences; a daily view needs glanceable lines, not paragraphs."""
    ranked = sorted(items, key=lambda e: len(e.get("title") or ""))
    out = []
    for e in ranked[:n]:
        t = to_ascii(e.get("title") or "")
        if len(t) > maxlen:
            t = t[:maxlen - 3].rstrip() + "..."
        out.append({**e, "title": t})
    return out


def _recent_sessions(store, pid, since_iso, limit=6):
    rows = store.conn.execute(
        """SELECT ended_at, source, agent_model FROM sessions
           WHERE project_id=? AND ended_at IS NOT NULL AND ended_at>=?
           ORDER BY ended_at DESC LIMIT ?""",
        (pid, since_iso, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _next_step_for(store, project, vstore=None):
    res = resume_mod.resume(store, project, "", vstore=vstore)
    return (res.get("bundle") or {}).get("next_step")


def recent_projects(store, days=7, exclude_pid=None, limit=6, vstore=None):
    """Other projects touched in the window, each with its next step - the
    cross-project switch list (a dev touches ~6 repos/day, Phase 1)."""
    mx = store.conn.execute("SELECT MAX(ended_at) FROM sessions").fetchone()[0]
    if not mx:
        return []
    since = (datetime.fromisoformat(mx[:19]) - timedelta(days=days)).isoformat()
    rows = store.conn.execute(
        """SELECT p.id, p.canonical_key, p.display_name, MAX(se.ended_at) last_active,
                  COUNT(se.id) n
           FROM sessions se JOIN projects p ON p.id=se.project_id
           WHERE se.ended_at>=? AND p.canonical_key NOT LIKE 'unknown:%'
           GROUP BY p.id ORDER BY last_active DESC""",
        (since,),
    ).fetchall()
    out = []
    for r in rows:
        if exclude_pid is not None and r["id"] == exclude_pid:
            continue
        proj = store.find_project_by_key(r["canonical_key"])
        if not proj or not store.project_work_items(proj["id"]):
            continue
        out.append({
            "project": proj,
            "last_active": r["last_active"],
            "sessions": r["n"],
            "next_step": _next_step_for(store, proj, vstore=vstore),
        })
        if len(out) >= limit:
            break
    return out


def build(store, project, days=7, vstore=None) -> dict:
    """Daily view for one project + the cross-project switch list."""
    pid = project["id"]
    b = brief_mod.build(store, project, vstore=vstore)
    mx = store.conn.execute("SELECT MAX(ended_at) FROM sessions").fetchone()[0]
    since = ((datetime.fromisoformat(mx[:19]) - timedelta(days=days)).isoformat()
             if mx else "0000")
    recent = _recent_sessions(store, pid, since)
    # what you're working on: active items, but never show nothing - fall back to
    # the most recently touched work items so the daily view always orients you.
    working = b["active_work"][:3] or store.project_work_items(pid)[:3]
    top = working[0] if working else None
    return {
        "project": project,
        "git": b["git"],
        "summary": b["summary"],
        "working_on": working,
        "top_confidence": (top.get("confidence") if top else None),
        "recent_sessions": recent,
        "recent_commits": b["commits"][:5],
        "blockers": _concise(b["blockers"], n=4),
        "risks": _concise(b["risks"], n=3),
        "next_step": b["next_step"],
        "elsewhere": recent_projects(store, days=days, exclude_pid=pid, vstore=vstore),
        "days": days,
    }


def build_cross_project(store, days=7, vstore=None) -> dict:
    """When not inside a known project: the multi-repo standup view."""
    return {
        "project": None,
        "days": days,
        "elsewhere": recent_projects(store, days=days, exclude_pid=None, vstore=vstore),
    }


def _conf(c):
    return f"{(c or 0):.2f}"


def format_today(t: dict) -> str:
    L = []
    if t["project"] is None:
        L.append(f"LOOMA TODAY - repos touched in the last {t['days']} days")
        if not t["elsewhere"]:
            L.append("  (nothing recent - run `looma ingest --once`)")
        for e in t["elsewhere"]:
            p = e["project"]
            L.append(f"\n  {p['display_name']}  ({(e['last_active'] or '')[:10]}, {e['sessions']} sessions)")
            L.append(f"    next: {to_ascii(e['next_step'] or '(open `looma resume` here)')}")
        return "\n".join(L)

    proj = t["project"]
    s = t["summary"]
    git = t["git"]
    L.append(f"LOOMA TODAY - {proj['display_name']}")
    bits = [f"{s['active']} active of {s['work_items']} work items"]
    if git.get("branch"):
        gb = f"branch {git['branch']}"
        if git.get("dirty"):
            gb += f", {len(git['dirty'])} uncommitted"
        bits.append(gb)
    L.append("  " + " | ".join(bits))

    L.append("\nWHAT YOU'RE WORKING ON")
    if t["working_on"]:
        for w in t["working_on"]:
            L.append(f"  #{w['id']} {to_ascii(w['title'])}  [{_conf(w['confidence'])}]")
    else:
        L.append("  (nothing active - run `looma resume`)")

    L.append(f"\nWHAT CHANGED (last {t['days']} days)")
    any_change = False
    if git.get("dirty"):
        any_change = True
        head = ", ".join(git["dirty"][:4])
        more = f" (+{len(git['dirty'])-4} more)" if len(git["dirty"]) > 4 else ""
        L.append(f"  uncommitted: {head}{more}")
    for c in t["recent_commits"]:
        any_change = True
        L.append(f"  commit {(c['sha'] or '')[:9]} {to_ascii(c.get('message') or '')[:56]}")
    if t["recent_sessions"]:
        any_change = True
        days_seen = sorted({(r["ended_at"] or "")[:10] for r in t["recent_sessions"]}, reverse=True)
        L.append(f"  {len(t['recent_sessions'])} recent sessions on {', '.join(days_seen[:4])}")
    if not any_change:
        L.append("  (no activity in window)")

    L.append("\nWHAT'S BLOCKED")
    blocked = t["blockers"] + t["risks"]
    if blocked:
        for e in t["blockers"]:
            L.append(f"  [ ] {e['title']}")
        for e in t["risks"]:
            L.append(f"  (!) {e['title']}")
    else:
        L.append("  (nothing flagged)")

    L.append("\nWHAT TO DO NEXT")
    L.append("  " + to_ascii(t["next_step"] or "(open `looma resume` for the full bundle)"))

    if t["elsewhere"]:
        L.append(f"\nELSEWHERE (other repos touched in {t['days']}d)")
        for e in t["elsewhere"][:5]:
            L.append(f"  {e['project']['display_name']}: {to_ascii(e['next_step'] or 'resume to see')}"[:90])
    return "\n".join(L)
