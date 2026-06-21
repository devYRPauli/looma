import tempfile, unittest
from pathlib import Path
from looma import pipeline, correction
from tests.helpers import assistant_edit_rec, make_store, user_rec, write_session


def _ingest(store, tmp, sessions):
    cwd = str(tmp / "p"); (tmp / "p").mkdir(exist_ok=True)
    projects = tmp / "claude"
    for i, (branch, fname, text) in enumerate(sessions):
        write_session(projects, f"-p{i}", f"s{i}", [
            user_rec(f"u{i}", f"s{i}", cwd, branch, text),
            assistant_edit_rec(f"a{i}", f"s{i}", cwd, branch, f"{cwd}/{fname}"),
        ])
    pipeline.ingest_messages(store, projects_dir=projects)
    pipeline.rebuild(store)
    return store.list_projects()[0]["id"]


class CorrectionMergeTest(unittest.TestCase):
    def test_merge_reduces_count_and_survives_rebuild(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            store = make_store()
            pid = _ingest(store, tmp, [
                ("feat-a", "a.py", "implement feature a"),
                ("feat-b", "b.py", "implement feature b"),
            ])
            wis = store.project_work_items(pid)
            self.assertGreaterEqual(len(wis), 2)
            sa = correction.workitem_sessions(store, pid, wis[0]["id"])
            sb = correction.workitem_sessions(store, pid, wis[1]["id"])
            correction.correct(store, pid, "merge", {"a": sa, "b": sb})
            pipeline.rebuild(store)
            self.assertEqual(len(store.project_work_items(pid)), len(wis) - 1)
            # survives a second rebuild (deterministic replay)
            pipeline.rebuild(store)
            self.assertEqual(len(store.project_work_items(pid)), len(wis) - 1)

    def test_rename_pins_title(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); store = make_store()
            pid = _ingest(store, tmp, [("feat-a", "a.py", "implement feature a")])
            wi = store.project_work_items(pid)[0]
            sess = correction.workitem_sessions(store, pid, wi["id"])
            correction.correct(store, pid, "rename", {"sessions": sess, "title": "Custom Title"})
            pipeline.rebuild(store)
            self.assertTrue(any(w["title"] == "Custom Title" for w in store.project_work_items(pid)))


class CorrectionMemoryTest(unittest.TestCase):
    def _two_session_wi(self, store, tmp):
        cwd = str(tmp / "p"); (tmp / "p").mkdir(exist_ok=True)
        projects = tmp / "claude"
        for i in range(2):
            write_session(projects, f"-p{i}", f"s{i}", [
                user_rec(f"u{i}", f"s{i}", cwd, "feat", "we need to add integration tests for billing"),
                assistant_edit_rec(f"a{i}", f"s{i}", cwd, "feat", f"{cwd}/x.py"),
            ])
        pipeline.ingest_messages(store, projects_dir=projects)
        pipeline.rebuild(store)
        return store.list_projects()[0]["id"]

    def test_reject_removes_promoted_memory(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); store = make_store()
            pid = self._two_session_wi(store, tmp)
            ents = store.conn.execute("SELECT kind,title FROM entities WHERE project_id=?", (pid,)).fetchall()
            self.assertTrue(ents, "a multi-session todo should have been promoted")
            target = next(e for e in ents if e["kind"] == "todo")
            correction.correct(store, pid, "reject", {"kind": target["kind"], "title": target["title"]})
            pipeline.rebuild(store)
            left = store.conn.execute(
                "SELECT COUNT(*) FROM entities WHERE project_id=? AND kind='todo' AND title=?",
                (pid, target["title"])).fetchone()[0]
            self.assertEqual(left, 0)

    def test_undo_restores(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); store = make_store()
            pid = self._two_session_wi(store, tmp)
            target = store.conn.execute(
                "SELECT kind,title FROM entities WHERE project_id=? AND kind='todo'", (pid,)).fetchone()
            lid = correction.correct(store, pid, "reject", {"kind": target["kind"], "title": target["title"]})
            pipeline.rebuild(store)
            correction.undo(store, lid)
            pipeline.rebuild(store)
            back = store.conn.execute(
                "SELECT COUNT(*) FROM entities WHERE project_id=? AND title=?",
                (pid, target["title"])).fetchone()[0]
            self.assertGreaterEqual(back, 1)


if __name__ == "__main__":
    unittest.main()
