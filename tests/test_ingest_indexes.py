import unittest

from tests.helpers import make_store


class IngestIndexesTest(unittest.TestCase):
    def test_migration_adds_indexes_and_queries_use_them(self):
        store = make_store()
        self.addCleanup(store.close)
        # Exercise upgrading an existing store, then repeating the migration.
        store.conn.execute('DROP INDEX idx_messages_session_seq')
        store.conn.execute('DROP INDEX idx_sessions_project')
        store.migrate()
        store.migrate()
        cases = [
            ('SELECT * FROM messages WHERE session_id=? ORDER BY seq', 'idx_messages_session_seq'),
            ('SELECT * FROM sessions WHERE project_id=?', 'idx_sessions_project'),
        ]
        for query, index in cases:
            with self.subTest(index=index):
                plan = ' '.join(row[3] for row in store.conn.execute('EXPLAIN QUERY PLAN ' + query, (1,)))
                self.assertIn(index, plan)
                self.assertNotIn('USE TEMP B-TREE', plan)
        # UNIQUE(source, native_id) already provides the source lookup index.
        plan = ' '.join(row[3] for row in store.conn.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM sessions WHERE source=?', ('claude',)))
        self.assertIn('USING INDEX', plan)
