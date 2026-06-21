"""Human Correction Layer (ARCHITECTURE.md 13-14, goal Phase C).

User corrections are durable, ledgered evidence that overrides automated inference.
A correction writes a `correction_ledger` row plus `correction_constraints`, then a
deterministic rebuild applies the constraints - so corrections are replayable and
survive reprocessing.

Constraints anchor to STABLE keys, not regenerated row ids:
- WorkItem  -> the set of its member session ids (sessions persist across rebuild).
- Memory    -> (kind, normalized title) within the project.
"""

import json
import re
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:120]


# ----- resolving user-facing tokens to stable anchors -----

def workitem_sessions(store, project_id, wi_id) -> list[int]:
    return [s["id"] for s in store.work_item_sessions(project_id, wi_id)]


def resolve_workitem(store, project_id, token):
    """'#5' or '5' -> work_item row, or None."""
    token = str(token).lstrip("#")
    if not token.isdigit():
        return None
    wi = store.get_work_item(int(token))
    if wi and wi["project_id"] == project_id:
        return wi
    return None


def find_memory(store, project_id, query):
    """Best (kind, title) anchor for a text query, searching entities then candidates."""
    q = norm(query)
    rows = store.conn.execute(
        "SELECT kind, title FROM entities WHERE project_id=?", (project_id,)
    ).fetchall() + store.conn.execute(
        "SELECT kind, title FROM candidate_memories WHERE project_id=?", (project_id,)
    ).fetchall()
    best, best_score = None, 0.0
    qtok = set(q.split())
    for r in rows:
        t = norm(r["title"])
        ttok = set(t.split())
        if not ttok:
            continue
        score = len(qtok & ttok) / max(1, len(qtok))
        if q in t:
            score += 1.0
        if score > best_score:
            best, best_score = (r["kind"], r["title"]), score
    return best if best_score > 0 else None


# ----- writing corrections -----

def _ledger(store, project_id, action, actor, affected, payload, rationale, inverse_of=None):
    cur = store.conn.execute(
        """INSERT INTO correction_ledger(project_id, action_type, actor, ts, affected,
           payload, rationale, inverse_of) VALUES(?,?,?,?,?,?,?,?)""",
        (project_id, action, actor, _now(), json.dumps(affected),
         json.dumps(payload), rationale, inverse_of),
    )
    return cur.lastrowid


def _constraint(store, project_id, ctype, a_ref, b_ref, payload, ledger_id):
    store.conn.execute(
        """INSERT INTO correction_constraints(project_id, ctype, a_ref, b_ref, payload,
           source_ledger_id, active) VALUES(?,?,?,?,?,?,1)""",
        (project_id, ctype, json.dumps(a_ref), json.dumps(b_ref) if b_ref else None,
         json.dumps(payload) if payload else None, ledger_id),
    )


def correct(store, project_id, action, payload, rationale=""):
    """Apply a correction: write ledger + constraint(s). Caller then rebuilds.

    payload shapes by action:
      merge:          {"a": [sess...], "b": [sess...]}
      split:          {"a": [sess...], "b": [sess...]}
      rename:         {"sessions": [...], "title": "..."}
      promote/reject/false_positive: {"kind": ..., "title": ...}
    Returns the ledger id.
    """
    lid = _ledger(store, project_id, action, "user", payload, payload, rationale)
    if action == "merge":
        _constraint(store, project_id, "MUST_LINK", {"sessions": payload["a"]},
                    {"sessions": payload["b"]}, None, lid)
    elif action == "split":
        _constraint(store, project_id, "CANNOT_LINK", {"sessions": payload["a"]},
                    {"sessions": payload["b"]}, None, lid)
    elif action == "rename":
        _constraint(store, project_id, "PIN_NAME", {"sessions": payload["sessions"]},
                    None, {"title": payload["title"]}, lid)
    elif action == "promote":
        _constraint(store, project_id, "FORCE_PROMOTE",
                    {"kind": payload["kind"], "title": norm(payload["title"])}, None, None, lid)
    elif action in ("reject", "false_positive"):
        ctype = "FALSE_POSITIVE" if action == "false_positive" else "FORCE_REJECT"
        _constraint(store, project_id, ctype,
                    {"kind": payload["kind"], "title": norm(payload["title"])}, None, None, lid)
    store.commit()
    return lid


def undo(store, ledger_id):
    """Deactivate constraints from a ledger row and record an inverse entry."""
    row = store.conn.execute(
        "SELECT * FROM correction_ledger WHERE id=?", (ledger_id,)
    ).fetchone()
    if not row:
        return None
    store.conn.execute(
        "UPDATE correction_constraints SET active=0 WHERE source_ledger_id=?", (ledger_id,)
    )
    _ledger(store, row["project_id"], "undo", "user", {"undid": ledger_id}, {},
            f"undo of #{ledger_id}", inverse_of=ledger_id)
    store.commit()
    return row["project_id"]


def ledger_entries(store, project_id):
    return [dict(r) for r in store.conn.execute(
        "SELECT * FROM correction_ledger WHERE project_id=? ORDER BY id", (project_id,))]


# ----- consumed by the rebuild (pipeline) -----

class Corrections:
    def __init__(self):
        self.mem = {}        # (kind, norm_title) -> 'promote' | 'reject'
        self.renames = []    # (frozenset(sessions), title)
        self.merges = []     # (frozenset, frozenset)
        self.splits = []     # (frozenset, frozenset)

    @property
    def empty(self):
        return not (self.mem or self.renames or self.merges or self.splits)


def load(store, project_id) -> Corrections:
    c = Corrections()
    for r in store.conn.execute(
        "SELECT * FROM correction_constraints WHERE project_id=? AND active=1", (project_id,)
    ):
        a = json.loads(r["a_ref"]) if r["a_ref"] else {}
        b = json.loads(r["b_ref"]) if r["b_ref"] else {}
        p = json.loads(r["payload"]) if r["payload"] else {}
        ct = r["ctype"]
        if ct == "FORCE_PROMOTE":
            c.mem[(a["kind"], a["title"])] = "promote"
        elif ct in ("FORCE_REJECT", "FALSE_POSITIVE"):
            c.mem[(a["kind"], a["title"])] = "reject"
        elif ct == "PIN_NAME":
            c.renames.append((frozenset(a["sessions"]), p["title"]))
        elif ct == "MUST_LINK":
            c.merges.append((frozenset(a["sessions"]), frozenset(b["sessions"])))
        elif ct == "CANNOT_LINK":
            c.splits.append((frozenset(a["sessions"]), frozenset(b["sessions"])))
    return c


def apply_to_builders(builders, corr, sess_files):
    """Rewrite WorkItem builders per merge/split/rename constraints. Deterministic."""
    if corr.empty:
        return builders

    # --- merges: union builders touching either side of a MUST_LINK pair ---
    parent = list(range(len(builders)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for ga, gb in corr.merges:
        touch = [i for i, b in enumerate(builders) if set(b["members"]) & (ga | gb)]
        for i in touch[1:]:
            union(touch[0], i)

    groups = {}
    for i in range(len(builders)):
        groups.setdefault(find(i), []).append(i)
    merged = [_coalesce([builders[i] for i in idxs]) for idxs in groups.values()]

    # --- splits: separate members that a CANNOT_LINK pair forbids together ---
    result = []
    for b in merged:
        out = [b]
        for ga, gb in corr.splits:
            new_out = []
            for bb in out:
                mem = set(bb["members"])
                if mem & ga and mem & gb:
                    side_a = sorted(mem & ga) + sorted(mem - ga - gb)  # remainder stays with A
                    side_b = sorted(mem & gb)
                    new_out.append(_subset(bb, side_a, sess_files))
                    new_out.append(_subset(bb, side_b, sess_files))
                else:
                    new_out.append(bb)
            out = new_out
        result.extend(out)

    # --- renames: pin titles ---
    for b in result:
        mem = set(b["members"])
        for anchor, title in corr.renames:
            if mem & anchor:
                b["title"] = title
                b["name_locked"] = True
    return result


def _coalesce(group):
    if len(group) == 1:
        return group[0]
    base = dict(group[0])
    base["members"] = sorted({m for b in group for m in b["members"]})
    base["files"] = set().union(*(b["files"] for b in group))
    base["aliases"] = set().union(*(b["aliases"] for b in group))
    base["agents"] = set().union(*(b["agents"] for b in group))
    starts = [b["started_at"] for b in group if b.get("started_at")]
    ends = [b["ended_at"] for b in group if b.get("ended_at")]
    base["started_at"] = min(starts) if starts else None
    base["ended_at"] = max(ends) if ends else None
    base["related"] = []
    return base


def _subset(b, members, sess_files):
    nb = dict(b)
    nb["members"] = list(members)
    nb["files"] = set().union(*[sess_files.get(s, set()) for s in members]) if members else set()
    nb["related"] = []
    return nb
