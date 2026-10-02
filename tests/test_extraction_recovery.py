import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from looma import daemon, pipeline
from looma.adapters.claude import ClaudeAdapter
from looma.extraction.extractor import LocalLLMExtractor
from looma.storage.sqlite_store import Store
from tests.helpers import make_store, user_rec, write_session


class ExtractionRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.history = self.root / 'history'
        self.adapters = [ClaudeAdapter(self.history)]
        self.store = make_store()
        self.addCleanup(self.store.close)
        self.valid = json.dumps({'memories': [
            {'kind': 'decision', 'title': 'Use Postgres for durable billing records'},
        ], 'work': {'label': 'Implement billing', 'kind': 'feature'}})

    def ingest(self, count=2, store=None):
        for index in range(count):
            session = f'session-{index}'
            write_session(self.history, '-project', session, [
                user_rec(f'message-{index}', session, str(self.project), 'main',
                         f'implement billing feature number {index}'),
            ])
        return pipeline.ingest_messages(store or self.store, adapters=self.adapters)

    def test_cache_survives_reopen_and_changes_invalidate_it(self):
        path = self.root / 'store.db'
        ext = LocalLLMExtractor()
        with patch.object(ext, '_call', return_value=self.valid) as call, patch.object(
            pipeline.extractor_mod, 'get_extractor', return_value=ext
        ):
            for iteration in range(2):
                store = Store.open(path)
                try:
                    store.migrate()
                    if iteration == 0:
                        self.ingest(store=store)
                    pipeline.rebuild(store)
                finally:
                    store.close()
            self.assertEqual(call.call_count, 2)
            with patch.dict(os.environ, {'LOOMA_LLM_MODEL': 'different-model'}):
                store = Store.open(path)
                try:
                    pipeline.rebuild(store)
                finally:
                    store.close()
            self.assertEqual(call.call_count, 4)
            with (self.history / '-project' / 'session-0.jsonl').open('a') as f:
                f.write(json.dumps(user_rec('appended', 'session-0', str(self.project), 'main',
                                           'add export tests')) + '\n')
            store = Store.open(path)
            try:
                pipeline.ingest_messages(store, adapters=self.adapters)
                pipeline.rebuild(store)
            finally:
                store.close()
            self.assertEqual(call.call_count, 5)

    def test_daemon_limits_attempts_across_all_projects(self):
        self.ingest(12)
        ext = LocalLLMExtractor()
        with patch.object(ext, '_call', return_value=self.valid) as call, patch.object(
            pipeline.extractor_mod, 'get_extractor', return_value=ext
        ), contextlib.redirect_stderr(io.StringIO()):
            daemon.cycle(self.store, adapters=self.adapters)
        self.assertEqual(call.call_count, 8)
        self.assertEqual(self.store.pending_rebuild_projects(), [])
        self.assertEqual(self.store.counts()['sessions'], 12)
        self.assertGreater(self.store.counts()['work_items'], 0)

    def test_invalid_output_opens_durable_cooldown_then_recovers(self):
        self.ingest(3)
        ext = LocalLLMExtractor()
        output = io.StringIO()
        with patch.object(ext, '_call', return_value='{"memories": 42, "work": []}') as call, patch.object(
            pipeline.extractor_mod, 'get_extractor', return_value=ext
        ), patch.object(pipeline.time, 'time', return_value=1000), contextlib.redirect_stderr(output):
            pipeline.rebuild(self.store)
            pipeline.rebuild(self.store)  # new rebuild, same persisted cooldown
        self.assertEqual(call.call_count, 1)
        self.assertIn('failed', output.getvalue())
        self.assertEqual(self.store.extraction_retry_after(ext.cache_namespace()), 1300)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM extraction_cache').fetchone()[0], 0)
        with patch.object(ext, '_call', return_value=self.valid) as call, patch.object(
            pipeline.extractor_mod, 'get_extractor', return_value=ext
        ), patch.object(pipeline.time, 'time', return_value=1301):
            pipeline.rebuild(self.store)
        self.assertEqual(call.call_count, 3)

    def test_slow_extraction_does_not_hold_a_write_transaction(self):
        path = self.root / 'store.db'
        store = Store.open(path)
        peer = Store.open(path)
        try:
            store.migrate()
            self.ingest(store=store)
            ext = LocalLLMExtractor()
            def call(prompt):
                self.assertFalse(store.conn.in_transaction)
                peer.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('concurrent-writer', 'ok'))
                peer.commit()
                return self.valid
            with patch.object(ext, '_call', side_effect=call), patch.object(
                pipeline.extractor_mod, 'get_extractor', return_value=ext
            ):
                pipeline.rebuild(store)
        finally:
            peer.close()
            store.close()

    def test_failed_rebuild_preserves_graph_and_retries_without_new_messages(self):
        self.ingest()
        pipeline.rebuild(self.store)
        old_items = [dict(r) for r in self.store.conn.execute('SELECT * FROM work_items')]
        pid = self.store.list_projects()[0]['id']
        self.store.mark_rebuild_pending(pid)
        self.store.commit()
        with patch.object(pipeline, '_rebuild_project', side_effect=RuntimeError('rebuild failed')):
            with self.assertRaisesRegex(RuntimeError, 'rebuild failed'):
                daemon.cycle(self.store, adapters=self.adapters)
        self.assertEqual([dict(r) for r in self.store.conn.execute('SELECT * FROM work_items')], old_items)
        self.assertEqual(self.store.pending_rebuild_projects(), [pid])
        result = daemon.cycle(self.store, adapters=self.adapters)
        self.assertEqual(result['new_messages'], 0)
        self.assertEqual(self.store.pending_rebuild_projects(), [])
        self.assertGreater(self.store.counts()['work_items'], 0)

    def test_messages_arriving_during_extraction_remain_queued(self):
        self.ingest()
        pid = self.store.list_projects()[0]['id']
        ext = LocalLLMExtractor()
        def call(prompt):
            self.store.mark_rebuild_pending(pid)
            self.store.commit()
            return self.valid
        with patch.object(ext, '_call', side_effect=call), patch.object(
            pipeline.extractor_mod, 'get_extractor', return_value=ext
        ):
            pipeline.rebuild(self.store)
        self.assertEqual(self.store.pending_rebuild_projects(), [pid])

    def test_late_project_failure_rolls_back_the_entire_project(self):
        self.ingest()
        pipeline.rebuild(self.store)
        original = pipeline._rebuild_project
        old_items = [dict(r) for r in self.store.conn.execute('SELECT * FROM work_items')]
        self.ingest(3)
        def rebuild(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('failed after graph writes')
        with patch.object(pipeline, '_rebuild_project', side_effect=rebuild):
            with self.assertRaisesRegex(RuntimeError, 'failed after graph writes'):
                pipeline.rebuild(self.store)
        self.assertEqual([dict(r) for r in self.store.conn.execute('SELECT * FROM work_items')], old_items)
        self.assertTrue(self.store.pending_rebuild_projects())

    def test_vector_failure_leaves_graph_rebuild_queued(self):
        self.ingest()
        with patch.object(pipeline, '_populate_vectors', side_effect=RuntimeError('vector failure')):
            with self.assertRaisesRegex(RuntimeError, 'vector failure'):
                daemon.cycle(self.store, adapters=self.adapters)
        self.assertTrue(self.store.pending_rebuild_projects())
        self.assertEqual(daemon.cycle(self.store, adapters=self.adapters)['new_messages'], 0)
        self.assertEqual(self.store.pending_rebuild_projects(), [])

    def test_daemon_recovers_after_cycle_failure(self):
        messages = []
        with patch.object(daemon, 'transcript_mtime', return_value=1), patch.object(
            daemon, 'cycle', side_effect=[RuntimeError('synthetic cycle failure'),
                                        {'new_messages': 0, 'sessions': 0, 'per_source': {}}]
        ) as cycle, patch.object(daemon.time, 'sleep', side_effect=[None, KeyboardInterrupt]):
            daemon.run(self.root / 'daemon.db', log=messages.append)
        self.assertEqual(cycle.call_count, 2)
        self.assertTrue(any('synthetic cycle failure' in message for message in messages))

    def test_daemon_once_surfaces_failure(self):
        with patch.object(daemon, 'cycle', side_effect=RuntimeError('synthetic cycle failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic cycle failure'):
                daemon.run(self.root / 'daemon.db', once=True, log=lambda message: None)

    def test_partial_ingest_commits_are_durable_and_pending(self):
        self.ingest()
        original = self.adapters[0].read
        for index in range(2):
            path = self.history / '-project' / f'session-{index}.jsonl'
            with path.open('a') as f:
                f.write(json.dumps(user_rec(f'new-{index}', f'session-{index}', str(self.project), 'main', 'add export tests')) + '\n')
        def read(handle):
            self.assertFalse(self.store.conn.in_transaction)
            if handle.native_id == 'session-1':
                raise OSError('synthetic read failure')
            return original(handle)
        with patch.object(self.adapters[0], 'read', side_effect=read):
            with self.assertRaisesRegex(RuntimeError, 'Could not read claude session session-1'):
                pipeline.ingest_messages(self.store, adapters=self.adapters)
        self.store.conn.rollback()
        self.assertEqual(self.store.counts()['messages'], 3)
        self.assertTrue(self.store.pending_rebuild_projects())
        self.assertEqual(daemon.cycle(self.store, adapters=self.adapters)['new_messages'], 1)
