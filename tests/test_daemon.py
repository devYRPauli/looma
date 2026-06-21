import tempfile, unittest
from pathlib import Path
from looma import daemon
from looma.adapters.claude import ClaudeAdapter
from tests.helpers import assistant_edit_rec, make_store, user_rec, write_session


class DaemonTest(unittest.TestCase):
    def test_cycle_ingests_then_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cwd = str(tmp / "p"); (tmp / "p").mkdir()
            projects = tmp / "claude"
            write_session(projects, "-p", "s0", [
                user_rec("u", "s0", cwd, "feat", "implement billing export"),
                assistant_edit_rec("a", "s0", cwd, "feat", f"{cwd}/billing.py"),
            ])
            store = make_store()
            adapters = [ClaudeAdapter(projects)]
            first = daemon.cycle(store, adapters=adapters)
            self.assertEqual(first["sessions"], 1)
            self.assertGreater(first["new_messages"], 0)
            self.assertGreaterEqual(store.counts()["work_items"], 1)
            # second cycle: nothing new (crash-safe / idempotent)
            second = daemon.cycle(store, adapters=adapters)
            self.assertEqual(second["new_messages"], 0)

    def test_transcript_mtime_returns_float(self):
        self.assertIsInstance(daemon.transcript_mtime(), float)


if __name__ == "__main__":
    unittest.main()
