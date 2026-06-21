import tempfile, unittest
from pathlib import Path
from looma import pipeline, timeline
from tests.helpers import assistant_edit_rec, make_store, user_rec, write_session


class TimelineTest(unittest.TestCase):
    def test_build_orders_events(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cwd = str(tmp / "p"); (tmp / "p").mkdir()
            projects = tmp / "claude"
            for i in range(2):  # two sessions -> active WI, decision promoted
                write_session(projects, f"-p{i}", f"s{i}", [
                    user_rec(f"u{i}", f"s{i}", cwd, "feat",
                             "we decided to use postgres instead of mysql for transactions",
                             ts=f"2026-06-1{i}T10:00:00Z"),
                    assistant_edit_rec(f"a{i}", f"s{i}", cwd, "feat", f"{cwd}/db.py",
                                       ts=f"2026-06-1{i}T10:05:00Z"),
                ])
            store = make_store()
            pipeline.ingest_messages(store, projects_dir=projects)
            pipeline.rebuild(store)
            pid = store.list_projects()[0]["id"]
            wi = store.project_work_items(pid)[0]
            events = timeline.build(store, pid, wi["id"])
            self.assertTrue(events)
            self.assertTrue(any(e["type"] == "session" for e in events))
            self.assertTrue(any(e["type"] == "decision" for e in events))
            # sorted ascending by ts
            ts = [e["ts"] or "9999" for e in events]
            self.assertEqual(ts, sorted(ts))
            self.assertIn("TIMELINE", timeline.format_timeline(wi, events))


if __name__ == "__main__":
    unittest.main()
