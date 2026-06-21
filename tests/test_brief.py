import unittest

from looma import brief as brief_mod
from tests.helpers import make_store


def _wi(store, pid, title, lifecycle, files, conf=0.4, last="2026-06-18T11:00:00Z"):
    return store.insert_work_item(
        pid, kind="feature", title=title, summary=title, status="active",
        lifecycle=lifecycle, aliases=[title], files=files, confidence=conf,
        first_seen="2026-06-18T10:00:00Z", last_active=last,
    )


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.pid = self.store.upsert_project("path:/p", "myapp", None, None)
        self.project = self.store.find_project_by_key("path:/p")

    def test_brief_has_all_sections_and_filters_noise(self):
        wid = _wi(self.store, self.pid, "Add OAuth login", "active", ["auth/login.ts"])
        _wi(self.store, self.pid, "Untitled work", "candidate", [])
        # one real decision + one code-line decision (must be dropped)
        self.store.insert_entity(self.pid, kind="decision", title="Use Postgres instead of SQLite",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.insert_entity(self.pid, kind="architecture", title="+ const x = await db.query()",
                                 work_item_id=wid, status="open", confidence=0.5)
        # one open bug (risk) + one already-resolved bug (must be dropped from risks)
        self.store.insert_entity(self.pid, kind="bug", title="Login fails on expired token",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.insert_entity(self.pid, kind="bug", title="Fixed the logout redirect",
                                 work_item_id=wid, status="open", confidence=0.5)
        # a bare file path bug (noise) must be dropped too
        self.store.insert_entity(self.pid, kind="bug", title="src/tests/login.test.ts",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.insert_entity(self.pid, kind="todo", title="Wire up refresh tokens",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.upsert_commit(self.pid, {"sha": "abcdef1234", "author": "x",
                                            "ts": "2026-06-18T10:30:00Z", "message": "add login route"})
        self.store.commit()

        b = brief_mod.build(self.store, self.project)
        # active work excludes the empty candidate
        self.assertEqual([w["id"] for w in b["active_work"]], [wid])
        # decision present, code-line decision filtered
        dtitles = [d["title"] for d in b["decisions"]]
        self.assertIn("Use Postgres instead of SQLite", dtitles)
        self.assertNotIn("+ const x = await db.query()", dtitles)
        # risks: open bug only; resolved + path filtered
        rtitles = [r["title"] for r in b["risks"]]
        self.assertIn("Login fails on expired token", rtitles)
        self.assertNotIn("Fixed the logout redirect", rtitles)
        self.assertNotIn("src/tests/login.test.ts", rtitles)
        # blockers + commits + next step
        self.assertEqual([t["title"] for t in b["blockers"]], ["Wire up refresh tokens"])
        self.assertEqual(b["commits"][0]["sha"], "abcdef1234")
        self.assertTrue(b["next_step"])

        out = brief_mod.format_brief(b)
        for header in ("ACTIVE WORK", "RECENT DECISIONS", "CURRENT RISKS",
                       "OPEN BLOCKERS", "RECENT COMMITS", "SUGGESTED NEXT WORK"):
            self.assertIn(header, out)
        self.assertTrue(out.isascii(), "brief output must be ASCII")

    def test_brief_empty_project(self):
        b = brief_mod.build(self.store, self.project)
        out = brief_mod.format_brief(b)
        self.assertIn("PROJECT: myapp", out)
        self.assertIn("(none)", out)


if __name__ == "__main__":
    unittest.main()
