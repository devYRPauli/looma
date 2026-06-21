"""Shared lexical matching helpers over FTS5 + a lexical fallback."""

import re

from ..util import tokens

_FTS_SAFE = re.compile(r"[^a-z0-9]+")


def soft_sim(goal: str, text: str) -> float:
    """Token overlap with substring credit, so 'auth' matches 'oauth'/'authentication'."""
    gt, tt = tokens(goal), tokens(text)
    if not gt:
        return 0.0
    hits = 0.0
    for g in gt:
        if g in tt:
            hits += 1.0
        elif len(g) >= 3 and any(g in t or t in g for t in tt):
            hits += 0.7
    return hits / len(gt)


def fts_query(text: str) -> str:
    """Build a safe FTS5 OR query from free text."""
    toks = [t for t in _FTS_SAFE.sub(" ", (text or "").lower()).split() if len(t) > 1]
    if not toks:
        return ""
    return " OR ".join(f'"{t}"' for t in toks)


def match_work_items(store, project_id: int, goal: str, vstore=None) -> list[dict]:
    """Return work items relevant to `goal`, best-first, with a 'relevance' field.

    Hybrid: FTS5 + lexical Jaccard fallback + (when available) semantic vectors, so a
    result still surfaces on vocabulary mismatch (e.g. 'auth' -> 'OAuth login').
    """
    wis = {w["id"]: dict(w) for w in store.project_work_items(project_id)}
    if not wis:
        return []

    scores: dict[int, float] = {}
    if vstore is not None and getattr(vstore, "available", False):
        for ref_id, vscore in vstore.search("workitem", goal, limit=10):
            if ref_id in wis:
                scores[ref_id] = max(scores.get(ref_id, 0.0), vscore)
    q = fts_query(goal)
    if q:
        try:
            rows = store.conn.execute(
                """SELECT rowid, bm25(fts_workitems) AS rank FROM fts_workitems
                   WHERE fts_workitems MATCH ? ORDER BY rank""",
                (q,),
            ).fetchall()
            for i, r in enumerate(rows):
                if r["rowid"] in wis:
                    # higher for better bm25 (lower rank); normalize by position
                    scores[r["rowid"]] = max(scores.get(r["rowid"], 0.0), 1.0 / (1 + i))
        except Exception:
            pass

    # lexical fallback / booster over title + aliases (substring-aware)
    for wid, w in wis.items():
        lex = max(
            soft_sim(goal, w.get("title") or ""),
            soft_sim(goal, w.get("aliases") or ""),
            soft_sim(goal, w.get("summary") or ""),
        )
        if lex > 0:
            scores[wid] = max(scores.get(wid, 0.0), lex)

    out = []
    for wid, rel in scores.items():
        w = wis[wid]
        w["relevance"] = round(rel, 4)
        out.append(w)
    out.sort(key=lambda w: (w["relevance"], w.get("confidence") or 0.0, w.get("last_active") or ""), reverse=True)
    return out
