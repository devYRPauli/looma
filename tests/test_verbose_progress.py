import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from looma import pipeline
from looma.adapters.claude import ClaudeAdapter
from tests.helpers import make_store, user_rec, write_session


class VerboseProgressTest(unittest.TestCase):
    def test_ingest_reports_progress_before_reading_each_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            write_session(root / "history", "-project", "session", [
                user_rec("one", "session", str(project), "main", "implement billing"),
            ])
            adapter = ClaudeAdapter(root / "history")
            output = io.StringIO()
            original_read = adapter.read

            def read(handle):
                self.assertIn("[ingest] Reading claude session 1/1", output.getvalue())
                return original_read(handle)

            store = make_store()
            self.addCleanup(store.close)
            with patch.object(adapter, "read", side_effect=read), contextlib.redirect_stdout(output):
                result = pipeline.ingest_messages(store, adapters=[adapter], verbose=True)
            self.assertEqual(result["new_messages"], 1)

    def test_rebuild_reports_progress_before_extraction_and_default_is_quiet(self):
        store = make_store()
        self.addCleanup(store.close)
        pid = store.upsert_project("unsorted:claude", "Unsorted", None, None)
        store.upsert_session(pid, "claude", "session", None, None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pipeline.rebuild(store)
        self.assertEqual(output.getvalue(), "")
        with contextlib.redirect_stdout(output), patch.object(pipeline, "_rebuild_project") as rebuild:
            def run(store, project, extractor, verbose=False, extracted=None):
                self.assertIn("[rebuild] Project 1/1", output.getvalue())
                self.assertTrue(verbose)
                return {"work_items": 0, "candidates": 0, "promoted": 0}
            rebuild.side_effect = run
            pipeline.rebuild(store, verbose=True)

    def test_cli_forwards_verbose_to_rebuild(self):
        from looma import cli
        from argparse import Namespace
        store = make_store()
        args = Namespace(verbose=True)
        with patch.object(cli, "_open_store", return_value=store), patch.object(
            pipeline, "rebuild", return_value={"work_items": 0, "candidates": 0, "promoted": 0}
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_reprocess(args)
        rebuild.assert_called_once_with(store, verbose=True)
