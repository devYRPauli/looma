import tempfile
import unittest
from pathlib import Path

from looma import pipeline
from looma.adapters.claude import ClaudeAdapter
from tests.helpers import assistant_edit_rec, make_store, user_rec, write_session


def _wi_titles(store, pid):
    return sorted(w["title"] for w in store.project_work_items(pid))


class IncrementalRebuildTest(unittest.TestCase):
    def _setup_two_projects(self, projects):
        cwd_a = "/work/alpha"
        cwd_b = "/work/beta"
        write_session(projects, "-work-alpha", "sa", [
            user_rec("u1", "sa", cwd_a, "feat", "implement billing export"),
            assistant_edit_rec("a1", "sa", cwd_a, "feat", f"{cwd_a}/billing/export.py"),
            assistant_edit_rec("a2", "sa", cwd_a, "feat", f"{cwd_a}/billing/report.py"),
        ])
        write_session(projects, "-work-beta", "sb", [
            user_rec("u2", "sb", cwd_b, "feat", "add search indexing"),
            assistant_edit_rec("b1", "sb", cwd_b, "feat", f"{cwd_b}/search/index.py"),
            assistant_edit_rec("b2", "sb", cwd_b, "feat", f"{cwd_b}/search/query.py"),
        ])

    def test_incremental_isolates_and_matches_full(self):
        with tempfile.TemporaryDirectory() as t:
            projects = Path(t) / "claude"
            self._setup_two_projects(projects)
            store = make_store()
            adapters = [ClaudeAdapter(projects)]
            pipeline.ingest_messages(store, adapters=adapters)
            pipeline.rebuild(store)  # full

            projs = {p["canonical_key"]: p["id"] for p in store.list_projects()}
            pid_a = projs["path:/work/alpha"]
            pid_b = projs["path:/work/beta"]
            full_a = _wi_titles(store, pid_a)
            full_b = _wi_titles(store, pid_b)
            b_ids = sorted(w["id"] for w in store.project_work_items(pid_b))

            # incremental rebuild of A only
            res = pipeline.rebuild(store, project_ids=[pid_a])
            self.assertTrue(res["incremental"])
            # A reproduces its full-rebuild titles
            self.assertEqual(_wi_titles(store, pid_a), full_a)
            # B is completely untouched: same titles AND same row ids
            self.assertEqual(_wi_titles(store, pid_b), full_b)
            self.assertEqual(sorted(w["id"] for w in store.project_work_items(pid_b)), b_ids)
            store.close()


if __name__ == "__main__":
    unittest.main()
