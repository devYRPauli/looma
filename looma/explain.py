"""`looma explain <workitem>` - the story of one effort.

Answers four questions about a WorkItem, grounded in its graph + timeline:
  why it exists      - the originating intent and when it started
  how it evolved     - sessions, commits, and bugs over time
  which decisions    - the decisions/architecture that shaped it
  what changed       - commits, file growth, and lifecycle
Built from already-derived data (timeline + graph); no new extraction.
"""

import json

from . import timeline as timeline_mod
from .sanitize import looks_like_code
from .util import to_ascii


def _files(wi):
    try:
        return json.loads(wi.get("files") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _clean(rows):
    out, seen = [], set()
    for r in rows:
        t = to_ascii((r["title"] or "").strip())
        k = t.lower()
        if not t or k in seen or looks_like_code(t):
            continue
        seen.add(k)
        d = dict(r)
        d["title"] = t
        out.append(d)
    return out


def build(store, project_id: int, wi: dict) -> dict:
    wid = wi["id"]
    events = timeline_mod.build(store, project_id, wid)
    decisions = _clean([e for e in store.work_item_entities(wid)
                        if e["kind"] in ("decision", "architecture")])
    bugs = _clean(store.work_item_entities(wid, "bug"))
    todos = _clean([t for t in store.work_item_entities(wid, "todo")
                    if (t.get("status") or "open") == "open"])
    sessions = store.work_item_sessions(project_id, wid)
    commits = store.work_item_commits(project_id, wid)
    try:
        raw_aliases = json.loads(wi.get("aliases") or "[]")
    except (json.JSONDecodeError, TypeError):
        raw_aliases = []
    aliases = [a for a in raw_aliases if a and not looks_like_code(a)]

    # originating intent: first session by time, plus the (cleaned) title/alias
    first_ts = events[0]["ts"] if events else wi.get("first_seen")
    agents = sorted({s.get("agent_model") for s in sessions if s.get("agent_model")})

    return {
        "work_item": wi,
        "why": {
            "title": to_ascii(wi.get("title") or ""),
            "aliases": [to_ascii(a) for a in aliases][:4],
            "started": (first_ts or "")[:10],
            "kind": wi.get("kind"),
        },
        "evolution": events,
        "decisions": decisions[:6],
        "bugs": bugs[:5],
        "open_todos": todos[:5],
        "sessions": sessions,
        "commits": commits,
        "agents": agents,
        "files": _files(wi),
    }


def format_explain(x: dict) -> str:
    wi = x["work_item"]
    why = x["why"]
    L = []
    L.append(f"EXPLAIN #{wi['id']}: {why['title']}")
    L.append(f"  {why['kind']} | {wi.get('lifecycle')} | confidence {(wi.get('confidence') or 0):.2f}")

    L.append("\nWHY IT EXISTS")
    origin = f"  Started {why['started'] or '?'} as {why['kind']} work."
    if why["aliases"]:
        origin += " Asked for as: " + "; ".join(why["aliases"]) + "."
    L.append(origin)
    span = ""
    if x["sessions"]:
        L.append(f"  Worked across {len(x['sessions'])} session(s)"
                 + (f" by {', '.join(x['agents'])}" if x["agents"] else "")
                 + f", touching {len(x['files'])} file(s).")

    # bounded lists keep explain cheap on large work items (MCP efficiency)
    L.append("\nHOW IT EVOLVED")
    if x["evolution"]:
        for e in x["evolution"][:12]:
            L.append(f"  {(e['ts'] or '?')[:10]}  {e['type']:11} {to_ascii(e['text'])}")
        if len(x["evolution"]) > 12:
            L.append(f"  ... +{len(x['evolution']) - 12} earlier events")
    else:
        L.append("  (no dated events)")

    L.append("\nDECISIONS THAT SHAPED IT")
    if x["decisions"]:
        for d in x["decisions"][:8]:
            L.append(f"  - {d['title']}")
    else:
        L.append("  (none captured)")

    if x["bugs"]:
        L.append("\nBUGS ALONG THE WAY")
        for b in x["bugs"][:6]:
            L.append(f"  (!) {b['title']}")

    if x["open_todos"]:
        L.append("\nSTILL OPEN")
        for t in x["open_todos"][:8]:
            L.append(f"  [ ] {t['title']}")

    L.append("\nWHAT CHANGED")
    if x["commits"]:
        for c in x["commits"][:8]:
            L.append(f"  {(c['sha'] or '')[:9]} {to_ascii(c.get('message') or '')[:64]}")
    else:
        L.append("  (no commits linked to this work)")
    return "\n".join(L)
