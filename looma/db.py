"""SQLite connection + schema migrations (system of record).

Schema mirrors ARCHITECTURE.md section 6.1. Tables required by the Phase 1
checklist are fully used; correction_ledger, correction_constraints, and
graph_health_snapshots are created as schema-only (queried by the confidence
ledger-override hook, but not yet written by any CLI command).
"""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = [
    # --- identity ---
    """CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY,
        canonical_key TEXT UNIQUE,
        display_name TEXT,
        root_path TEXT,
        git_remote TEXT,
        created_at TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS project_aliases (
        project_id INTEGER REFERENCES projects(id),
        alias_kind TEXT,
        alias TEXT,
        UNIQUE(alias_kind, alias)
    )""",
    # --- sessions and turns ---
    """CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        source TEXT,
        native_id TEXT,
        branch TEXT,
        head_sha TEXT,
        agent_model TEXT,
        started_at TEXT,
        ended_at TEXT,
        ingest_cursor TEXT,
        schema_version INTEGER,
        UNIQUE(source, native_id)
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        session_id INTEGER REFERENCES sessions(id),
        seq INTEGER,
        ts TEXT,
        role TEXT,
        agent_model TEXT,
        text TEXT,
        tool_calls TEXT,
        raw_json TEXT,
        event_hash TEXT UNIQUE
    )""",
    # --- WorkItem spine ---
    """CREATE TABLE IF NOT EXISTS work_items (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        kind TEXT,
        title TEXT,
        summary TEXT,
        status TEXT,
        lifecycle TEXT,
        branch TEXT,
        aliases TEXT,
        files TEXT,
        confidence REAL DEFAULT 0,
        name_locked INTEGER DEFAULT 0,
        first_seen TEXT,
        last_active TEXT
    )""",
    # --- staging tier (CandidateMemory) ---
    """CREATE TABLE IF NOT EXISTS candidate_memories (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        kind TEXT,
        title TEXT,
        body TEXT,
        status TEXT,
        attrs TEXT,
        session_refs TEXT,
        agent_refs TEXT,
        first_seen TEXT,
        last_seen TEXT,
        confidence REAL DEFAULT 0,
        state TEXT DEFAULT 'candidate',
        promoted_entity_id INTEGER,
        work_item_id INTEGER REFERENCES work_items(id)
    )""",
    # --- ValidatedMemory ---
    """CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        kind TEXT,
        title TEXT,
        body TEXT,
        status TEXT,
        attrs TEXT,
        work_item_id INTEGER REFERENCES work_items(id),
        promoted_from_candidate_id INTEGER,
        confidence REAL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS entity_evidence (
        entity_id INTEGER REFERENCES entities(id),
        message_id INTEGER REFERENCES messages(id),
        char_start INTEGER,
        char_end INTEGER
    )""",
    # --- git ground truth ---
    """CREATE TABLE IF NOT EXISTS commits (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        sha TEXT,
        author TEXT,
        ts TEXT,
        message TEXT,
        UNIQUE(project_id, sha)
    )""",
    """CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        path TEXT,
        UNIQUE(project_id, path)
    )""",
    """CREATE TABLE IF NOT EXISTS commit_files (
        commit_id INTEGER REFERENCES commits(id),
        file_id INTEGER REFERENCES files(id),
        change TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        name TEXT,
        head_sha TEXT,
        UNIQUE(project_id, name)
    )""",
    """CREATE TABLE IF NOT EXISTS prs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        number INTEGER,
        title TEXT,
        state TEXT,
        branch TEXT,
        merged_sha TEXT,
        url TEXT
    )""",
    # --- graph ---
    """CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY,
        project_id INTEGER,
        node_type TEXT,
        ref_id INTEGER,
        UNIQUE(node_type, ref_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edges (
        src_node INTEGER REFERENCES nodes(id),
        dst_node INTEGER REFERENCES nodes(id),
        rel TEXT,
        weight REAL,
        attrs TEXT,
        created_at TEXT,
        UNIQUE(src_node, dst_node, rel)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_node, rel)",
    "CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_node, rel)",
    # --- correction subsystem (schema only for Phase 1) ---
    """CREATE TABLE IF NOT EXISTS correction_ledger (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        action_type TEXT,
        actor TEXT,
        ts TEXT,
        affected TEXT,
        payload TEXT,
        rationale TEXT,
        inverse_of INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS correction_constraints (
        id INTEGER PRIMARY KEY,
        project_id INTEGER REFERENCES projects(id),
        ctype TEXT,
        a_ref TEXT,
        b_ref TEXT,
        payload TEXT,
        source_ledger_id INTEGER,
        active INTEGER DEFAULT 1
    )""",
    # --- graph health (schema only for Phase 1) ---
    """CREATE TABLE IF NOT EXISTS graph_health_snapshots (
        id INTEGER PRIMARY KEY,
        project_id INTEGER,
        ts TEXT,
        conversion_rate REAL,
        merge_rate REAL,
        false_positive_rate REAL,
        avg_work_item_size REAL,
        orphan_candidate_count INTEGER,
        unresolved_related_count INTEGER,
        metrics TEXT
    )""",
    # --- FTS5 retrieval indexes (external-content, populated manually) ---
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages
        USING fts5(text, content='messages', content_rowid='id')""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_workitems
        USING fts5(title, summary, aliases)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_entities
        USING fts5(title, body, content='entities', content_rowid='id')""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    # persistent git-SHA validation cache (NOT a derived table - survives rebuild)
    """CREATE TABLE IF NOT EXISTS git_sha_cache (
        root TEXT, sha TEXT, present INTEGER, PRIMARY KEY(root, sha)
    )""",
]


def connect(db_path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first public schema; ALTERed in for old DBs (alpha -> v1).
_EXTRA_COLUMNS = {
    "work_items": [("branch", "TEXT"), ("name_locked", "INTEGER DEFAULT 0"),
                   ("confidence", "REAL DEFAULT 0")],
    "candidate_memories": [("confidence", "REAL DEFAULT 0")],
    "entities": [("confidence", "REAL DEFAULT 0"), ("work_item_id", "INTEGER"),
                 ("promoted_from_candidate_id", "INTEGER")],
    "summaries": [("work_item_id", "INTEGER")],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _EXTRA_COLUMNS.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not existing:
            continue
        for col, typ in cols:
            if col not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                except sqlite3.Error:
                    pass


def migrate(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA:
        conn.execute(stmt)
    _ensure_columns(conn)  # forward-compatible upgrades for pre-v1 databases
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
