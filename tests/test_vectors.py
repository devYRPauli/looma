import os, sqlite3, unittest
from looma.storage.vector_store import NullVectorStore, get_vector_store
from looma.retrieval.match import match_work_items
from tests.helpers import make_store


def _ext_ok():
    try:
        return hasattr(sqlite3.connect(":memory:"), "enable_load_extension") and __import__("sqlite_vec")
    except Exception:
        return False


class StubVec:
    available = True
    def __init__(self, target): self.target = target
    def reset(self): pass
    def add_many(self, kind, items): pass
    def search(self, kind, query, limit=10):
        return [(self.target, 0.9)] if kind == "workitem" else []


class VectorStoreTest(unittest.TestCase):
    def test_null_store(self):
        n = NullVectorStore()
        self.assertFalse(n.available)
        self.assertEqual(n.search("workitem", "x"), [])

    def test_get_vector_store_off_is_null(self):
        prev = os.environ.get("LOOMA_VECTORS")
        os.environ["LOOMA_VECTORS"] = "off"
        try:
            self.assertIsInstance(get_vector_store(":memory:"), NullVectorStore)
        finally:
            if prev is None: os.environ.pop("LOOMA_VECTORS", None)
            else: os.environ["LOOMA_VECTORS"] = prev

    def test_fusion_surfaces_semantic_hit_fts_misses(self):
        store = make_store()
        pid = store.upsert_project("path:/v", "v", None, None)
        wid = store.insert_work_item(pid, kind="feature", title="Implement OAuth login flow",
                                     summary="google jwt", status="active", lifecycle="active", confidence=0.6)
        store.index_work_item_fts(store.get_work_item(wid))
        store.commit()
        q = "user authentication sign-in"  # no lexical overlap with the title
        self.assertEqual(match_work_items(store, pid, q), [])  # FTS/lexical miss
        hits = match_work_items(store, pid, q, vstore=StubVec(wid))
        self.assertEqual([h["id"] for h in hits], [wid])      # vector surfaces it

    @unittest.skipUnless(_ext_ok(), "sqlite3 without loadable-extension support")
    def test_sqlitevec_roundtrip(self):
        from looma.storage.vector_store import SqliteVecStore
        import tempfile, os as _os
        emb = {"apple": [1.0, 0, 0, 0], "banana": [0, 1.0, 0, 0]}
        def fake(texts): return [emb.get(t.split()[0], [0, 0, 1.0, 0]) for t in texts]
        with tempfile.TemporaryDirectory() as t:
            vs = SqliteVecStore(_os.path.join(t, "x.db"), 4, fake)
            vs.add_many("workitem", [(1, "apple pie"), (2, "banana split")])
            top = vs.search("workitem", "apple", limit=1)
            self.assertEqual(top[0][0], 1)


if __name__ == "__main__":
    unittest.main()
