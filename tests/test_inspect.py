"""`looma inspect` - repository intelligence from the derived graph."""

import unittest

from looma import inspect as inspect_mod
from tests.helpers import make_store


class InspectModuleTest(unittest.TestCase):
    def test_module_splits_packages_into_subsystems(self):
        self.assertEqual(inspect_mod._module("looma/retrieval/resume.py"), "looma/retrieval")
        self.assertEqual(inspect_mod._module("looma/cli.py"), "looma")
        self.assertEqual(inspect_mod._module("README.md"), "(root)")
        self.assertEqual(inspect_mod._module("backend/api/routes/users.py"), "backend/api")


class InspectBuildTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.pid = self.store.upsert_project("path:/p", "app", None, None)
        self.project = self.store.find_project_by_key("path:/p")

    def tearDown(self):
        self.store.close()

    def _wi(self, title, files, conf=0.4):
        return self.store.insert_work_item(
            self.pid, kind="feature", title=title, summary=title, status="active",
            lifecycle="active", aliases=[title], files=files, confidence=conf,
            first_seen="2026-06-18T10:00:00Z", last_active="2026-06-18T11:00:00Z")

    def test_systems_and_hotspots(self):
        self._wi("auth", ["backend/auth/login.py", "backend/auth/jwt.py"])
        self._wi("auth2", ["backend/auth/login.py"])  # login.py touched twice
        self._wi("ui", ["frontend/src/App.tsx"])
        self.store.insert_entity(self.pid, kind="architecture",
                                 title="Auth must be stateless", status="open", confidence=0.5)
        self.store.insert_entity(self.pid, kind="bug",
                                 title="Login throws on expired token", status="open", confidence=0.4)
        self.store.commit()

        x = inspect_mod.build(self.store, self.project)
        mods = [s["module"] for s in x["systems"]]
        self.assertIn("backend/auth", mods)
        self.assertIn("frontend/src", mods)
        # most-touched file ranks first in hotspots
        self.assertEqual(x["hotspots"][0]["file"], "backend/auth/login.py")
        self.assertEqual(x["hotspots"][0]["touches"], 2)
        # architecture + risk surfaced from memory
        self.assertTrue(any("stateless" in e["title"] for e in x["architecture"]))
        self.assertTrue(any("expired token" in b["title"] for b in x["risks"]))
        text = inspect_mod.format_inspect(x)
        self.assertIn("ACTIVE SYSTEMS", text)
        self.assertTrue(text.isascii())


if __name__ == "__main__":
    unittest.main()
