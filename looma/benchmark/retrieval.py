"""Retrieval benchmark: FTS-only vs FTS+vectors (goal Phase 2).

Synthetic WorkItem set + vocabulary-mismatched queries (the case where lexical
search fails and semantics help). Measures recall@1, recall@3, MRR through the real
`match_work_items` path, so it benchmarks the actual hybrid retrieval.
"""

import tempfile
from pathlib import Path

from ..retrieval.match import match_work_items
from ..storage.sqlite_store import Store

# (title, summary)
WORKITEMS = [
    ("Implement OAuth login flow", "sign in with Google provider, JWT tokens"),
    ("Add Redis session cache", "store sessions in redis for horizontal scaling"),
    ("Fix checkout total rounding bug", "line items rounded individually, total off"),
    ("Migrate users table to add tenant_id", "online migration with backfill job"),
    ("Investigate worker memory leak", "RSS grows unbounded, event listeners"),
    ("Refactor payment gateway integration", "clean up stripe and paypal handlers"),
    ("Add rate limiting to public API", "token bucket per client to prevent abuse"),
    ("Improve search relevance ranking", "tune bm25 and add semantic reranking"),
]

# (query with DIFFERENT vocabulary, expected WorkItem index)
QUERIES = [
    ("user authentication and sign-in security", 0),
    ("speed up by caching sessions in memory", 1),
    ("incorrect order amount at purchase", 2),
    ("multi-tenant database schema change", 3),
    ("process running out of RAM under load", 4),
    ("credit card processing cleanup", 5),
    ("throttle abusive clients hitting the endpoint", 6),
    ("better full-text result ordering", 7),
]


def _build_store(use_vectors: bool, embedder, tmp: Path):
    store = Store.open(str(tmp / "rb.db"))
    store.migrate()
    pid = store.upsert_project("path:/rb", "rb", None, None)
    ids = []
    for title, summary in WORKITEMS:
        wid = store.insert_work_item(pid, kind="feature", title=title, summary=summary,
                                     status="active", lifecycle="active", confidence=0.6)
        store.index_work_item_fts(store.get_work_item(wid))
        ids.append(wid)
    store.commit()
    vstore = None
    if use_vectors and embedder is not None:
        from ..storage.vector_store import SqliteVecStore
        probe = embedder(["dim probe"])
        vstore = SqliteVecStore(str(tmp / "rb.db"), len(probe[0]), embedder)
        vstore.add_many("workitem", [(wid, f"{t} {s}") for wid, (t, s) in zip(ids, WORKITEMS)])
    return store, pid, ids, vstore


def run(use_vectors: bool, embedder=None) -> dict:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        store, pid, ids, vstore = _build_store(use_vectors, embedder, tmp)
        hit1 = hit3 = 0
        mrr = 0.0
        for q, exp_idx in QUERIES:
            expected = ids[exp_idx]
            ranked = [w["id"] for w in match_work_items(store, pid, q, vstore=vstore)]
            rank = ranked.index(expected) + 1 if expected in ranked else 0
            if rank == 1:
                hit1 += 1
            if 1 <= rank <= 3:
                hit3 += 1
            if rank:
                mrr += 1.0 / rank
        store.close()
        n = len(QUERIES)
        return {"mode": "fts+vec" if use_vectors else "fts-only",
                "recall@1": round(hit1 / n, 3), "recall@3": round(hit3 / n, 3),
                "mrr": round(mrr / n, 3), "n": n}


def compare() -> str:
    from .. import embedding

    fts = run(False)
    lines = [f"Retrieval benchmark ({fts['n']} vocabulary-mismatched queries)",
             f"  fts-only   recall@1={fts['recall@1']:.2f} recall@3={fts['recall@3']:.2f} "
             f"mrr={fts['mrr']:.2f}"]
    ok, _ = embedding.detect()
    if not ok:
        lines.append("  fts+vec    (no local embedding server; start "
                     "`llama-server -m <embed.gguf> --embeddings --port 8081`)")
        return "\n".join(lines)
    try:
        vec = run(True, embedding.embed)
        lines.append(f"  fts+vec    recall@1={vec['recall@1']:.2f} recall@3={vec['recall@3']:.2f} "
                     f"mrr={vec['mrr']:.2f}")
        lines.append(f"  delta recall@3: {vec['recall@3'] - fts['recall@3']:+.2f}")
    except Exception as e:
        lines.append(f"  fts+vec    (sqlite-vec unavailable: {type(e).__name__}; this "
                     "Python's sqlite3 lacks loadable-extension support - FTS fallback in use)")
    return "\n".join(lines)
