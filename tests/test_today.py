import unittest

from looma import today as today_mod
from tests.helpers import make_store


def _wi(store, pid, title, lifecycle, files, conf=0.4, last="2026-06-18T11:00:00Z"):
    return store.insert_work_item(
        pid, kind="feature", title=title, summary=title, status="active",
        lifecycle=lifecycle, aliases=[title], files=files, confidence=conf,
        first_seen="2026-06-18T10:00:00Z", last_active=last,
    )


class TodayTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.pid = self.store.upsert_project("path:/p", "myapp", None, None)
        self.project = self.store.find_project_by_key("path:/p")
        # a session so recency + cross-project queries have data
        self.sid = self.store.upsert_session(self.pid, "claude", "s1", "main", "claude-opus-4-8")
        self.store.update_session_meta(self.sid, "main", None,
                                       "2026-06-18T10:00:00Z", "2026-06-18T11:00:00Z", "claude-opus-4-8")

    def test_today_answers_four_questions(self):
        wid = _wi(self.store, self.pid, "Add OAuth login", "active", ["auth/login.ts"])
        self.store.insert_entity(self.pid, kind="todo", title="Wire refresh tokens",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.insert_entity(self.pid, kind="bug", title="Token expiry crashes login",
                                 work_item_id=wid, status="open", confidence=0.5)
        self.store.commit()
        t = today_mod.build(self.store, self.project, days=30)
        self.assertEqual([w["id"] for w in t["working_on"]], [wid])      # what working on
        self.assertEqual(len(t["recent_sessions"]), 1)                    # what changed
        self.assertTrue(t["blockers"] or t["risks"])                      # what's blocked
        self.assertTrue(t["next_step"])                                   # what next
        out = today_mod.format_today(t)
        for h in ("WHAT YOU'RE WORKING ON", "WHAT CHANGED", "WHAT'S BLOCKED", "WHAT TO DO NEXT"):
            self.assertIn(h, out)
        self.assertTrue(out.isascii())

    def test_concise_truncates_verbose_blockers(self):
        long = "We should " + "really " * 40 + "do this"
        out = today_mod._concise([{"title": long}], n=1, maxlen=90)
        self.assertLessEqual(len(out[0]["title"]), 90)
        self.assertTrue(out[0]["title"].endswith("..."))

    def test_cross_project_view(self):
        _wi(self.store, self.pid, "Add OAuth login", "active", ["auth/login.ts"])
        # a second project, also recently active
        pid2 = self.store.upsert_project("path:/q", "other", None, None)
        s2 = self.store.upsert_session(pid2, "claude", "s2", "main", None)
        self.store.update_session_meta(s2, "main", None, "2026-06-18T09:00:00Z",
                                       "2026-06-18T09:30:00Z", None)
        _wi(self.store, pid2, "Build search", "active", ["search/index.ts"])
        self.store.commit()
        t = today_mod.build_cross_project(self.store, days=3650)
        names = {e["project"]["display_name"] for e in t["elsewhere"]}
        self.assertIn("myapp", names)
        self.assertIn("other", names)
        self.assertTrue(today_mod.format_today(t).isascii())


if __name__ == "__main__":
    unittest.main()
