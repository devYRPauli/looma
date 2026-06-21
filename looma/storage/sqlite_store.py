"""Store: the single access layer over the SQLite system of record.

Holds the connection and the graph helpers. Domain logic (extraction,
resolution, promotion, retrieval) lives in its own modules and calls Store.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .. import db
from ..gitutil import normalize_remote


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, conn, path=":memory:"):
        self.conn = conn
        self.path = str(path)

    @classmethod
    def open(cls, db_path) -> "Store":
        conn = db.connect(db_path)
        return cls(conn, path=db_path)

    def migrate(self) -> None:
        db.migrate(self.conn)

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ----- projects / identity -----

    def upsert_project(
        self,
        canonical_key: str,
        display_name: str,
        root_path: Optional[str],
        git_remote: Optional[str],
    ) -> int:
        cur = self.conn.execute(
            "SELECT id FROM projects WHERE canonical_key=?", (canonical_key,)
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO projects(canonical_key, display_name, root_path, git_remote,
               created_at, updated_at) VALUES(?,?,?,?,?,?)""",
            (canonical_key, display_name, root_path, git_remote, now, now),
        )
        pid = cur.lastrowid
        self.add_alias(pid, "root", root_path)
        self.add_alias(pid, "remote", git_remote)
        return pid

    def add_alias(self, project_id: int, kind: str, alias: Optional[str]) -> None:
        if not alias:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO project_aliases(project_id, alias_kind, alias) VALUES(?,?,?)",
            (project_id, kind, alias),
        )

    def find_project_by_key(self, canonical_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE canonical_key=?", (canonical_key,)
        ).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM projects ORDER BY id")]

    # ----- sessions / messages -----

    def upsert_session(
        self,
        project_id: int,
        source: str,
        native_id: str,
        branch: Optional[str],
        agent_model: Optional[str],
    ) -> int:
        row = self.conn.execute(
            "SELECT id FROM sessions WHERE source=? AND native_id=?", (source, native_id)
        ).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            """INSERT INTO sessions(project_id, source, native_id, branch, agent_model,
               schema_version) VALUES(?,?,?,?,?,?)""",
            (project_id, source, native_id, branch, agent_model, db.SCHEMA_VERSION),
        )
        return cur.lastrowid

    def update_session_meta(
        self, session_id: int, branch, head_sha, started_at, ended_at, agent_model
    ) -> None:
        self.conn.execute(
            """UPDATE sessions SET branch=COALESCE(?,branch), head_sha=COALESCE(?,head_sha),
               started_at=COALESCE(?,started_at), ended_at=?, agent_model=COALESCE(?,agent_model)
               WHERE id=?""",
            (branch, head_sha, started_at, ended_at, agent_model, session_id),
        )

    def insert_message(self, session_id: int, ev) -> Optional[int]:
        """Idempotent on event_hash. Returns message id, or None if duplicate."""
        try:
            cur = self.conn.execute(
                """INSERT INTO messages(session_id, seq, ts, role, agent_model, text,
                   tool_calls, raw_json, event_hash) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    ev.seq,
                    ev.ts,
                    ev.role,
                    ev.agent_model,
                    ev.text,
                    json.dumps(ev.tool_calls),
                    ev.raw_json,
                    ev.event_hash,
                ),
            )
        except Exception:
            return None
        mid = cur.lastrowid
        if ev.text:
            self.conn.execute(
                "INSERT INTO fts_messages(rowid, text) VALUES(?,?)", (mid, ev.text)
            )
        return mid

    def session_messages(self, session_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,)
            )
        ]

    def project_sessions(self, project_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sessions WHERE project_id=? ORDER BY id", (project_id,)
            )
        ]

    # ----- git mirror -----

    def upsert_file(self, project_id: int, path: str) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO files(project_id, path) VALUES(?,?)", (project_id, path)
        )
        return self.conn.execute(
            "SELECT id FROM files WHERE project_id=? AND path=?", (project_id, path)
        ).fetchone()["id"]

    def upsert_commit(self, project_id: int, info: dict) -> int:
        self.conn.execute(
            """INSERT OR IGNORE INTO commits(project_id, sha, author, ts, message)
               VALUES(?,?,?,?,?)""",
            (project_id, info["sha"], info.get("author"), info.get("ts"), info.get("message")),
        )
        return self.conn.execute(
            "SELECT id FROM commits WHERE project_id=? AND sha=?", (project_id, info["sha"])
        ).fetchone()["id"]

    def link_commit_file(self, commit_id: int, file_id: int, change: str = "M") -> None:
        self.conn.execute(
            "INSERT INTO commit_files(commit_id, file_id, change) VALUES(?,?,?)",
            (commit_id, file_id, change),
        )

    def upsert_branch(self, project_id: int, name: str, head_sha: Optional[str]) -> None:
        if not name:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO branches(project_id, name, head_sha) VALUES(?,?,?)",
            (project_id, name, head_sha),
        )

    # ----- work items -----

    def insert_work_item(self, project_id: int, **kw) -> int:
        cur = self.conn.execute(
            """INSERT INTO work_items(project_id, kind, title, summary, status, lifecycle,
               branch, aliases, files, confidence, first_seen, last_active)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                kw.get("kind"),
                kw.get("title"),
                kw.get("summary"),
                kw.get("status", "active"),
                kw.get("lifecycle", "candidate"),
                kw.get("branch"),
                json.dumps(sorted(kw.get("aliases", []))),
                json.dumps(sorted(kw.get("files", []))),
                kw.get("confidence", 0.0),
                kw.get("first_seen"),
                kw.get("last_active"),
            ),
        )
        return cur.lastrowid

    def update_work_item(self, wi_id: int, **kw) -> None:
        sets, vals = [], []
        for k, v in kw.items():
            if k in ("aliases", "files") and not isinstance(v, str):
                v = json.dumps(sorted(v))
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(wi_id)
        self.conn.execute(f"UPDATE work_items SET {', '.join(sets)} WHERE id=?", vals)

    def get_work_item(self, wi_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM work_items WHERE id=?", (wi_id,)).fetchone()
        return dict(row) if row else None

    def project_work_items(self, project_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM work_items WHERE project_id=? ORDER BY last_active DESC, id DESC",
                (project_id,),
            )
        ]

    def index_work_item_fts(self, wi: dict) -> None:
        self.conn.execute(
            "INSERT INTO fts_workitems(rowid, title, summary, aliases) VALUES(?,?,?,?)",
            (wi["id"], wi.get("title") or "", wi.get("summary") or "", wi.get("aliases") or ""),
        )

    # ----- candidate memories -----

    def insert_candidate(self, project_id: int, **kw) -> int:
        cur = self.conn.execute(
            """INSERT INTO candidate_memories(project_id, kind, title, body, status, attrs,
               session_refs, agent_refs, first_seen, last_seen, confidence, state, work_item_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                kw.get("kind"),
                kw.get("title"),
                kw.get("body"),
                kw.get("status"),
                json.dumps(kw.get("attrs", {})),
                json.dumps(sorted(kw.get("session_refs", []))),
                json.dumps(sorted(kw.get("agent_refs", []))),
                kw.get("first_seen"),
                kw.get("last_seen"),
                kw.get("confidence", 0.0),
                kw.get("state", "candidate"),
                kw.get("work_item_id"),
            ),
        )
        return cur.lastrowid

    def update_candidate(self, cand_id: int, **kw) -> None:
        sets, vals = [], []
        for k, v in kw.items():
            if k in ("session_refs", "agent_refs") and not isinstance(v, str):
                v = json.dumps(sorted(v))
            if k == "attrs" and not isinstance(v, str):
                v = json.dumps(v)
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(cand_id)
        self.conn.execute(f"UPDATE candidate_memories SET {', '.join(sets)} WHERE id=?", vals)

    def project_candidates(self, project_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM candidate_memories WHERE project_id=? ORDER BY id", (project_id,)
            )
        ]

    # ----- entities (validated memory) -----

    def insert_entity(self, project_id: int, **kw) -> int:
        now = _now()
        cur = self.conn.execute(
            """INSERT INTO entities(project_id, kind, title, body, status, attrs, work_item_id,
               promoted_from_candidate_id, confidence, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                kw.get("kind"),
                kw.get("title"),
                kw.get("body"),
                kw.get("status"),
                json.dumps(kw.get("attrs", {})),
                kw.get("work_item_id"),
                kw.get("promoted_from_candidate_id"),
                kw.get("confidence", 0.0),
                now,
                now,
            ),
        )
        eid = cur.lastrowid
        self.conn.execute(
            "INSERT INTO fts_entities(rowid, title, body) VALUES(?,?,?)",
            (eid, kw.get("title") or "", kw.get("body") or ""),
        )
        return eid

    def work_item_entities(self, wi_id: int, kind: Optional[str] = None) -> list[dict]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE work_item_id=? AND kind=? ORDER BY confidence DESC",
                (wi_id, kind),
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE work_item_id=? ORDER BY confidence DESC", (wi_id,)
            )
        return [dict(r) for r in rows]

    # ----- graph -----

    def node_id(self, project_id: int, node_type: str, ref_id: int) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO nodes(project_id, node_type, ref_id) VALUES(?,?,?)",
            (project_id, node_type, ref_id),
        )
        return self.conn.execute(
            "SELECT id FROM nodes WHERE node_type=? AND ref_id=?", (node_type, ref_id)
        ).fetchone()["id"]

    def add_edge(self, src_node: int, dst_node: int, rel: str, weight: float = 1.0) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO edges(src_node, dst_node, rel, weight, created_at)
               VALUES(?,?,?,?,?)""",
            (src_node, dst_node, rel, weight, _now()),
        )

    def in_neighbors(self, dst_node: int, rel: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT n.node_type, n.ref_id FROM edges e JOIN nodes n ON n.id = e.src_node
               WHERE e.dst_node=? AND e.rel=?""",
            (dst_node, rel),
        )
        return [dict(r) for r in rows]

    def work_item_node(self, project_id: int, wi_id: int) -> int:
        return self.node_id(project_id, "workitem", wi_id)

    def work_item_sessions(self, project_id: int, wi_id: int) -> list[dict]:
        node = self.work_item_node(project_id, wi_id)
        refs = [n["ref_id"] for n in self.in_neighbors(node, "CONTRIBUTES_TO")]
        if not refs:
            return []
        q = ",".join("?" * len(refs))
        rows = self.conn.execute(
            f"SELECT * FROM sessions WHERE id IN ({q}) ORDER BY ended_at DESC, id DESC", refs
        )
        return [dict(r) for r in rows]

    def work_item_commits(self, project_id: int, wi_id: int) -> list[dict]:
        node = self.work_item_node(project_id, wi_id)
        refs = [n["ref_id"] for n in self.in_neighbors(node, "IMPLEMENTS")]
        if not refs:
            return []
        q = ",".join("?" * len(refs))
        rows = self.conn.execute(
            f"SELECT * FROM commits WHERE id IN ({q}) ORDER BY ts DESC", refs
        )
        return [dict(r) for r in rows]

    # ----- correction constraints (ledger override hook, read-only in Phase 1) -----

    def constraint_for(self, ctype: str, node_type: str, ref_id: int) -> Optional[dict]:
        ref = json.dumps({"type": node_type, "id": ref_id})
        row = self.conn.execute(
            """SELECT * FROM correction_constraints
               WHERE active=1 AND ctype=? AND a_ref=?""",
            (ctype, ref),
        ).fetchone()
        return dict(row) if row else None

    # ----- git sha validation cache (persistent across runs) -----

    def sha_cached(self, root: str, sha: str):
        row = self.conn.execute(
            "SELECT present FROM git_sha_cache WHERE root=? AND sha=?", (root, sha)
        ).fetchone()
        return None if row is None else bool(row["present"])

    def cache_sha(self, root: str, sha: str, present: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO git_sha_cache(root, sha, present) VALUES(?,?,?)",
            (root, sha, 1 if present else 0),
        )

    # ----- misc / stats -----

    def counts(self) -> dict:
        def c(sql):
            return self.conn.execute(sql).fetchone()[0]

        return {
            "projects": c("SELECT COUNT(*) FROM projects"),
            "sessions": c("SELECT COUNT(*) FROM sessions"),
            "messages": c("SELECT COUNT(*) FROM messages"),
            "work_items": c("SELECT COUNT(*) FROM work_items"),
            "candidates": c("SELECT COUNT(*) FROM candidate_memories"),
            "promoted": c("SELECT COUNT(*) FROM candidate_memories WHERE state='promoted'"),
            "entities": c("SELECT COUNT(*) FROM entities"),
            "commits": c("SELECT COUNT(*) FROM commits"),
        }
