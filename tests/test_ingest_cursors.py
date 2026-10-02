import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from looma import pipeline
from looma.adapters.claude import ClaudeAdapter
from looma.adapters.cursor import CursorAdapter
from looma.storage.sqlite_store import Store
from tests.helpers import make_store, user_rec, write_session


class IngestCursorsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.history = self.root / 'history'
        self.path = write_session(self.history, '-project', 'session', [
            user_rec('one', 'session', str(self.project), 'main', 'implement billing'),
        ])
        self.adapter = ClaudeAdapter(self.history)
        self.store = make_store()
        self.addCleanup(self.store.close)

    def ingest(self, **kwargs):
        return pipeline.ingest_messages(self.store, adapters=[self.adapter], **kwargs)

    def test_unchanged_transcript_is_not_read(self):
        self.ingest()
        with patch.object(self.adapter, 'read', wraps=self.adapter.read) as read:
            result = self.ingest()
        read.assert_not_called()
        self.assertEqual(result['sessions'], 1)
        self.assertEqual(result['unchanged_sessions'], 1)
        self.assertEqual(result['changed_projects'], [])

    def test_cursor_survives_reopening_and_appends_are_ingested(self):
        path = self.root / 'store.db'
        for iteration in range(2):
            store = Store.open(path)
            try:
                store.migrate()
                with patch.object(self.adapter, 'read', wraps=self.adapter.read) as read:
                    result = pipeline.ingest_messages(store, adapters=[self.adapter])
                self.assertEqual(read.call_count, 1 if iteration == 0 else 0)
            finally:
                store.close()
        with self.path.open('a') as f:
            f.write(json.dumps(user_rec('two', 'session', str(self.project), 'main', 'add tests')) + '\n')
        store = Store.open(path)
        try:
            result = pipeline.ingest_messages(store, adapters=[self.adapter])
            self.assertEqual(result['new_messages'], 1)
            self.assertEqual(store.counts()['messages'], 2)
        finally:
            store.close()

    def test_changed_file_is_read_but_unchanged_peer_is_skipped(self):
        write_session(self.history, '-project', 'peer', [
            user_rec('peer', 'peer', str(self.project), 'main', 'implement exports'),
        ])
        self.ingest()
        with self.path.open('a') as f:
            f.write(json.dumps(user_rec('two', 'session', str(self.project), 'main', 'add tests')) + '\n')
        with patch.object(self.adapter, 'read', wraps=self.adapter.read) as read:
            result = self.ingest()
        self.assertEqual([call.args[0].native_id for call in read.call_args_list], ['session'])
        self.assertEqual(result['new_messages'], 1)
        self.assertEqual(result['unchanged_sessions'], 1)

    def test_cursor_is_not_saved_if_file_changes_during_read(self):
        read = self.adapter.read
        def moving(handle):
            events = list(read(handle))
            with self.path.open('a') as f:
                f.write(json.dumps(user_rec('two', 'session', str(self.project), 'main', 'add tests')) + '\n')
            return iter(events)
        with patch.object(self.adapter, 'read', side_effect=moving):
            self.ingest()
        self.assertIsNone(self.store.find_session('claude', 'session')['ingest_cursor'])
        self.assertEqual(self.ingest()['new_messages'], 1)

    def test_project_filter_and_limit_apply_to_cached_sessions(self):
        first = self.ingest()
        self.assertEqual(self.ingest(project_filter='other')['skipped'], 1)
        self.assertEqual(self.ingest(limit=0)['sessions'], 0)
        self.assertEqual(self.ingest(limit=1)['sessions'], first['sessions'])

    def test_failed_insert_does_not_advance_cursor(self):
        with patch.object(self.store, 'insert_message', side_effect=RuntimeError('insert failed')):
            with self.assertRaisesRegex(RuntimeError, 'insert failed'):
                self.ingest()
        self.store.conn.rollback()
        self.assertIsNone(self.store.find_session('claude', 'session'))
        self.assertEqual(self.ingest()['new_messages'], 1)

    def test_cursor_database_churn_does_not_reparse_unchanged_sessions(self):
        path = self.root / 'cursor.db'
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)')
        def put(key, value):
            conn.execute('INSERT OR REPLACE INTO cursorDiskKV VALUES (?,?)', (key, json.dumps(value)))
            conn.commit()
        put('composerData:c1', {'fullConversationHeadersOnly': [{'bubbleId': 'b1'}]})
        put('bubbleId:c1:b1', {'type': 1, 'text': 'implement billing', 'workspaceUris': [self.project.as_uri()]})
        adapter = CursorAdapter(path)
        pipeline.ingest_messages(self.store, adapters=[adapter])
        put('unrelatedUISetting', {'theme': 'dark'})
        with patch.object(adapter, 'read', wraps=adapter.read) as read:
            result = pipeline.ingest_messages(self.store, adapters=[adapter])
        read.assert_not_called()
        self.assertEqual(result['unchanged_sessions'], 1)
        put('composerData:c1', {'fullConversationHeadersOnly': [{'bubbleId': 'b1'}, {'bubbleId': 'b2'}]})
        put('bubbleId:c1:b2', {'type': 2, 'text': 'add export tests'})
        self.assertEqual(pipeline.ingest_messages(self.store, adapters=[adapter])['new_messages'], 1)
        adapter._conn.close()
        Path(adapter._copy).unlink()
