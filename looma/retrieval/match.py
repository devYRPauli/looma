"""Shared lexical matching helpers over FTS5 + a lexical fallback."""

import json
import re

from ..util import tokens

_FTS_SAFE = re.compile(r"[^a-z0-9]+")
_PATH_SPLIT = re.compile(r"[/_\-.]+")
_FTS_FLOOR = 0.16  # an FTS-only (stemmed) match clears COLD but ranks below real coverage


def _file_tokens(files_field) -> str:
    """Path components as searchable words: 'a/sync-results/route.ts' -> 'a sync results route ts'."""
    try:
        files = json.loads(files_field or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    return " ".join(_PATH_SPLIT.sub(" ", f) for f in files)


def _memory_text_by_workitem(store, project_id: int) -> dict:
    """One pass: join each WorkItem's linked memory titles into one searchable blob."""
    out: dict[int, list[str]] = {}
    try:
        rows = store.conn.execute(
            "SELECT work_item_id, title FROM entities WHERE project_id=? AND work_item_id IS NOT NULL",
            (project_id,),
        ).fetchall()
    except Exception:
        return {}
    for r in rows:
        out.setdefault(r["work_item_id"], []).append(r["title"] or "")
    return {k: " ".join(v) for k, v in out.items()}


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


# light suffix stripping so a query token prefix-matches its morphological
# variants ("extraction" -> "extract*" catches extracting/extracted/extractor).
# Longest suffix first; only applied when the stem stays >= 4 chars.
_SUFFIXES = ("izations", "isations", "ization", "isation", "ations", "ation",
             "ings", "ing", "ments", "ment", "ence", "ance", "tion", "sion",
             "edly", "ers", "ors", "ity", "ive", "es", "ed", "ly", "s")


def _stem(t: str) -> str:
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: len(t) - len(suf)]
    return t


def fts_query(text: str) -> str:
    """Build a safe FTS5 OR query from free text, with prefix/stem tolerance.

    Each token becomes a prefix match on its stem (recall across inflections);
    short tokens stay exact (precision). FTS5 prefix syntax is `term*`."""
    toks = [t for t in _FTS_SAFE.sub(" ", (text or "").lower()).split() if len(t) > 1]
    if not toks:
        return ""
    terms = []
    for t in toks:
        stem = _stem(t)
        terms.append(f"{stem}*" if len(stem) >= 4 else f'"{t}"')
    return " OR ".join(terms)


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

    # FTS contributes RECALL, not score: it ensures stemmed/tokenized matches are
    # considered (a small floor), but the relevance number is coverage-based (see
    # below) so it is honest 0-1 and the resume thresholds mean something. Scoring
    # by bm25 position gave every top hit relevance 1.0 regardless of overlap.
    q = fts_query(goal)
    if q:
        try:
            rows = store.conn.execute(
                "SELECT rowid FROM fts_workitems WHERE fts_workitems MATCH ?", (q,),
            ).fetchall()
            for r in rows:
                if r["rowid"] in wis:
                    scores[r["rowid"]] = max(scores.get(r["rowid"], 0.0), _FTS_FLOOR)
        except Exception:
            pass

    # Coverage-based lexical score over title + aliases + summary + file-path
    # tokens + linked-memory text, so relevance survives a generic title
    # ("Work in src/"): a goal like "sync results cron" still matches an item that
    # touches sync-results/route.ts or has a "sync" todo. Without this, 48%
    # generic titles cap relevance ~0.28 and resume goes COLD (Phase 1).
    mem_text = _memory_text_by_workitem(store, project_id)
    for wid, w in wis.items():
        files_txt = _file_tokens(w.get("files"))
        lex = max(
            soft_sim(goal, w.get("title") or ""),
            soft_sim(goal, w.get("aliases") or ""),
            soft_sim(goal, w.get("summary") or ""),
            0.9 * soft_sim(goal, files_txt),          # files are strong but indirect
            0.8 * soft_sim(goal, mem_text.get(wid, "")),  # linked decisions/todos/bugs
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
