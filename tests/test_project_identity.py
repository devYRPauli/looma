import os
import tempfile
import unittest

from looma import identity
from looma.gitutil import normalize_remote


class ProjectIdentityTest(unittest.TestCase):
    def test_normalize_remote_variants(self):
        cases = {
            "git@github.com:devYRPauli/looma.git": "github.com/devyrpauli/looma",
            "https://github.com/devYRPauli/looma.git": "github.com/devyrpauli/looma",
            "https://github.com/devYRPauli/looma": "github.com/devyrpauli/looma",
            "ssh://git@gitlab.com/group/sub/repo.git": "gitlab.com/group/sub/repo",
        }
        for url, expected in cases.items():
            self.assertEqual(normalize_remote(url), expected, url)

    def test_normalize_remote_none(self):
        self.assertIsNone(normalize_remote(None))
        self.assertIsNone(normalize_remote("not a url"))

    def test_resolve_falls_back_to_path_key_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = identity.resolve(tmp)
            self.assertIsNotNone(ident)
            # not a git repo -> path: key anchored on the abspath
            self.assertTrue(ident["canonical_key"].startswith("path:"))
            self.assertEqual(ident["display_name"], os.path.basename(ident["root_path"]))

    def test_resolve_none_for_missing_root(self):
        self.assertIsNone(identity.resolve(None))

    def test_ephemeral_and_degenerate_roots_rejected(self):
        # V2 Phase 2: temp/scratch/config/degenerate roots are not projects
        for junk in ["/", "/tmp", "/private/tmp", "/var/tmp",
                     "/private/var/folders/sm/abcd1234567890/T",
                     os.path.expanduser("~/.claude/projects"),
                     os.path.expanduser("~")]:
            self.assertIsNone(identity.resolve(junk), junk)

    def test_deep_temp_subdir_still_resolves(self):
        # a real project that happens to live under a temp dir (pytest fixtures)
        # must still resolve - only the scratch ROOT itself is ephemeral
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "myproject")
            os.makedirs(sub)
            self.assertIsNotNone(identity.resolve(sub))


class _Ev:
    """Minimal NormalizedEvent stub: resolve_from_events only reads tool_calls."""

    def __init__(self, *file_paths):
        self.tool_calls = [
            {"name": "Read", "input": {"file_path": p}} for p in file_paths
        ]


class _ShellEv:
    """A shell tool turn carrying a workdir (Codex-style)."""

    def __init__(self, *workdirs):
        self.tool_calls = [
            {"name": "shell", "input": {"cmd": ["ls"], "workdir": w}} for w in workdirs
        ]


class ResolveFromEventsTest(unittest.TestCase):
    def test_recovers_project_from_touched_files_under_temp(self):
        # cwd was /tmp, but the real work was in a cloned repo under it
        events = [
            _Ev("/tmp/hunt-mem0/mem0/client.py"),
            _Ev("/tmp/hunt-mem0/tests/test_client.py"),
        ]
        ident = identity.resolve_from_events(events)
        self.assertIsNotNone(ident)
        self.assertEqual(ident["display_name"], "hunt-mem0")

    def test_dominant_directory_wins(self):
        events = [
            _Ev("/tmp/hunt-mem0/a.py", "/tmp/hunt-mem0/b.py", "/tmp/hunt-mem0/c.py"),
            _Ev("/tmp/scratch-other/z.py"),
        ]
        ident = identity.resolve_from_events(events)
        self.assertEqual(ident["display_name"], "hunt-mem0")

    def test_harness_scratchpad_is_not_a_project(self):
        # files only under the harness scratchpad -> no recoverable project
        events = [_Ev("/private/tmp/claude-501/abc/session/scratchpad/note.md")]
        self.assertIsNone(identity.resolve_from_events(events))

    def test_no_touched_files_returns_none(self):
        self.assertIsNone(identity.resolve_from_events([_Ev()]))

    def test_recovers_project_from_shell_workdir_under_temp(self):
        # codex session run from home but executing commands in a /tmp checkout
        events = [_ShellEv("/tmp/hunt-litellm", "/tmp/hunt-litellm")]
        ident = identity.resolve_from_events(events)
        self.assertIsNotNone(ident)
        self.assertEqual(ident["display_name"], "hunt-litellm")

    def test_ephemeral_workdir_is_ignored(self):
        # workdir is the home dir / fs root -> no project
        self.assertIsNone(identity.resolve_from_events([_ShellEv("/", os.path.expanduser("~"))]))


if __name__ == "__main__":
    unittest.main()
