import tempfile, unittest
from pathlib import Path
from looma import pipeline, health
from tests.helpers import assistant_edit_rec, make_store, user_rec, write_session


class HealthTest(unittest.TestCase):
    def test_metrics_present_and_ranged(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cwd = str(tmp / "p"); (tmp / "p").mkdir()
            projects = tmp / "claude"
            write_session(projects, "-p", "s0", [
                user_rec("u", "s0", cwd, "feat", "we decided to use postgres instead of mysql"),
                assistant_edit_rec("a", "s0", cwd, "feat", f"{cwd}/db.py"),
            ])
            store = make_store()
            pipeline.ingest_messages(store, projects_dir=projects)
            pipeline.rebuild(store)
            h = health.compute(store)
            for k in ("conversion_rate", "merge_rate", "false_positive_rate",
                      "avg_work_item_size", "orphan_candidates", "unresolved_related_items"):
                self.assertIn(k, h)
            self.assertTrue(0.0 <= h["conversion_rate"] <= 1.0)
            self.assertIsInstance(h["orphan_candidates"], int)
            self.assertIn("graph health", health.format_health(h))


if __name__ == "__main__":
    unittest.main()
