"""VectorStore: FTS-fallback by default, real sqlite-vec when available (Phase 2).

The zero-dependency default is `NullVectorStore` (semantic search disabled; FTS5
carries retrieval). When the optional `sqlite-vec` package AND a local embedding
server are both present, `get_vector_store` returns a real `SqliteVecStore` that
stores embeddings in a sidecar DB (`<db>.vec`) and serves KNN - so the main schema
and the zero-dependency mode are untouched.
"""

import os
from pathlib import Path
from typing import Optional, Protocol

KINDS = ("workitem", "entity")


class VectorStore(Protocol):
    available: bool

    def reset(self) -> None: ...
    def add_many(self, kind: str, items: list[tuple[int, str]]) -> None: ...
    def search(self, kind: str, query: str, limit: int = 10) -> list[tuple[int, float]]: ...


class NullVectorStore:
    """No-op. Semantic search disabled; lexical (FTS5) handles retrieval."""

    available = False

    def reset(self) -> None:
        return None

    def add_many(self, kind: str, items: list[tuple[int, str]]) -> None:
        return None

    def search(self, kind: str, query: str, limit: int = 10) -> list[tuple[int, float]]:
        return []


class SqliteVecStore:
    """sqlite-vec backed KNN over a sidecar DB. rowid == the item's ref_id."""

    available = True

    def __init__(self, db_path, dim: int, embedder):
        import sqlite3
        import sqlite_vec

        self.embed = embedder
        self.dim = dim
        self.conn = sqlite3.connect(str(db_path) + ".vec")
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._serialize = sqlite_vec.serialize_float32
        for k in KINDS:
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_{k} USING vec0(embedding float[{dim}])"
            )

    def reset(self) -> None:
        for k in KINDS:
            self.conn.execute(f"DELETE FROM vec_{k}")
        self.conn.commit()

    def add_many(self, kind: str, items: list[tuple[int, str]]) -> None:
        if kind not in KINDS or not items:
            return
        vecs = self.embed([t for _, t in items])
        if not vecs:
            return
        for (ref_id, _), v in zip(items, vecs):
            self.conn.execute(
                f"INSERT OR REPLACE INTO vec_{kind}(rowid, embedding) VALUES(?, ?)",
                (ref_id, self._serialize(v)),
            )
        self.conn.commit()

    def search(self, kind: str, query: str, limit: int = 10) -> list[tuple[int, float]]:
        if kind not in KINDS:
            return []
        v = self.embed([query])
        if not v:
            return []
        rows = self.conn.execute(
            f"SELECT rowid, distance FROM vec_{kind} WHERE embedding MATCH ? AND k = ? "
            f"ORDER BY distance",
            (self._serialize(v[0]), limit),
        ).fetchall()
        # convert L2 distance to a (0,1] similarity-ish score
        return [(rid, 1.0 / (1.0 + dist)) for rid, dist in rows]


def get_vector_store(db_path) -> VectorStore:
    """Real SqliteVecStore only when sqlite-vec + a local embed server are present."""
    if os.environ.get("LOOMA_VECTORS", "auto").lower() == "off":
        return NullVectorStore()
    try:
        import sqlite_vec  # noqa: F401
    except Exception:
        return NullVectorStore()
    from .. import embedding

    ok, dim = embedding.detect()
    if not ok:
        return NullVectorStore()
    try:
        return SqliteVecStore(db_path, dim, embedding.embed)
    except Exception:
        return NullVectorStore()
