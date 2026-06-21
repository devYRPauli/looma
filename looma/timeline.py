"""WorkItem timeline - feature evolution in the terminal (goal Phase 3).

Orders decisions, commits, bugs, todos, and sessions by their real timestamps
(evidence-message time / commit author date / session end), then shows the current
status. No UI; useful as plain text.
"""


def _entity_ts(store, entity_id, fallback):
    row = store.conn.execute(
        """SELECT MIN(m.ts) FROM entity_evidence ev JOIN messages m ON m.id=ev.message_id
           WHERE ev.entity_id=?""", (entity_id,)).fetchone()
    return (row[0] if row and row[0] else fallback)


def build(store, project_id, wi_id) -> list[dict]:
    events = []
    for e in store.work_item_entities(wi_id):
        ts = _entity_ts(store, e["id"], e.get("created_at"))
        kind = e["kind"]
        label = {"decision": "decision", "architecture": "architecture",
                 "bug": "bug", "todo": "todo"}.get(kind, kind)
        events.append({"ts": ts, "type": label, "text": e["title"]})
    for c in store.work_item_commits(project_id, wi_id):
        events.append({"ts": c.get("ts"), "type": "commit",
                       "text": f"{c['sha'][:9]} {(c.get('message') or '')[:64]}"})
    for s in store.work_item_sessions(project_id, wi_id):
        events.append({"ts": s.get("ended_at") or s.get("started_at"), "type": "session",
                       "text": f"{s.get('source')}/{s.get('agent_model') or '?'}"})
    events.sort(key=lambda e: (e["ts"] or "9999"))
    return events


def format_timeline(wi: dict, events: list[dict]) -> str:
    conf = wi.get("confidence") or 0.0
    head = [f"TIMELINE: {wi['title']}   [{wi['kind']}, {wi['lifecycle']}] conf {conf:.2f}"]
    if events:
        span = f"{(events[0]['ts'] or '?')[:10]} -> {(events[-1]['ts'] or '?')[:10]}"
        head.append(f"{len(events)} events  ({span})\n")
    else:
        head.append("(no timeline events yet)\n")
    for e in events:
        head.append(f"  {(e['ts'] or '?')[:10]}  {e['type']:13} {e['text']}")
    head.append(f"\ncurrent status: {wi.get('status')}/{wi['lifecycle']}, "
                f"last active {(wi.get('last_active') or '?')[:10]}")
    return "\n".join(head)
