import os, tempfile, unittest
from pathlib import Path
from looma import pipeline
from looma.adapters.claude import ClaudeAdapter
from looma.adapters.codex import CodexAdapter
from tests.helpers import (assistant_edit_rec, make_store, user_rec,
                           write_codex_session, write_session)


class CrossAgentTest(unittest.TestCase):
    def test_claude_and_codex_merge_into_one_workitem(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cwd = str(tmp / "proj")
            os.makedirs(os.path.join(cwd, "auth"))
            open(os.path.join(cwd, "auth", "oauth.py"), "w").close()  # real file for path validation

            claude_dir = tmp / "claude"
            write_session(claude_dir, "-proj", "csession", [
                user_rec("u1", "csession", cwd, "feat", "implement oauth login"),
                assistant_edit_rec("a1", "csession", cwd, "feat", f"{cwd}/auth/oauth.py"),
            ])
            codex_root = tmp / "codex"
            write_codex_session(codex_root, "xsession", cwd, [
                ("user", "continue the oauth work in auth/oauth.py"),
                ("assistant", "updating auth/oauth.py for token refresh"),
            ])

            store = make_store()
            res = pipeline.ingest_messages(store, adapters=[
                ClaudeAdapter(claude_dir), CodexAdapter(codex_root)])
            self.assertEqual(res["per_source"], {"claude": 1, "codex": 1})
            pipeline.rebuild(store)

            # same project (cross-agent identity)
            projs = store.list_projects()
            self.assertEqual(len(projs), 1)
            pid = projs[0]["id"]

            # both sessions contribute to ONE work item (file-overlap merge across agents)
            wis = store.project_work_items(pid)
            merged = [w for w in wis
                      if len({s["source"] for s in store.work_item_sessions(pid, w["id"])}) == 2]
            self.assertTrue(merged, "a Claude and a Codex session on the same file should merge")
            sources = {s["source"] for s in store.work_item_sessions(pid, merged[0]["id"])}
            self.assertEqual(sources, {"claude", "codex"})


if __name__ == "__main__":
    unittest.main()
