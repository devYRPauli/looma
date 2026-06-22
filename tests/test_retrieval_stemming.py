"""V2 Phase 4: ask retrieval recall - stemmed/prefix FTS so inflected queries
match. Previously `ask("extraction")` returned 0 against memories that say
"extracting"/"extracted"."""

import unittest

from looma.retrieval.match import fts_query
from looma.retrieval import ask as ask_mod
from tests.helpers import make_store


class FtsQueryStemTest(unittest.TestCase):
    def test_inflections_become_prefix_stems(self):
        self.assertEqual(fts_query("extraction"), "extrac*")
        self.assertEqual(fts_query("migration"), "migr*")
        self.assertEqual(fts_query("confidence"), "confid*")
        self.assertEqual(fts_query("picks"), "pick*")

    def test_short_tokens_stay_exact(self):
        self.assertEqual(fts_query("api"), '"api"')

    def test_empty(self):
        self.assertEqual(fts_query(""), "")


class AskRecallTest(unittest.TestCase):
    def test_ask_matches_inflected_memory(self):
        store = make_store()
        pid = store.upsert_project("path:/p", "app", None, None)
        wid = store.insert_work_item(pid, kind="feature", title="Extractor work",
                                     summary="x", status="active", lifecycle="active",
                                     aliases=[], files=[], confidence=0.3,
                                     first_seen=None, last_active=None)
        store.insert_entity(pid, kind="decision",
                            title="We are extracting decisions from the transcript",
                            work_item_id=wid, status="open", confidence=0.4)
        store.commit()
        # query uses the noun "extraction"; memory says "extracting"
        hits = ask_mod.ask(store, pid, "extraction")
        self.assertTrue(any("extracting" in h["title"] for h in hits))
        store.close()


if __name__ == "__main__":
    unittest.main()
