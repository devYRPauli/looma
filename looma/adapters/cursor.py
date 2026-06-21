"""Cursor adapter (goal Phase 5).

Conversation data lives in the GLOBAL VS Code store
(~/Library/Application Support/Cursor/User/globalStorage/state.vscdb), table
`cursorDiskKV`: `composerData:<id>` holds an ordered header list, and
`bubbleId:<composerId>:<bubbleId>` holds each message ({type 1=user/2=assistant,
text, createdAt, workspaceUris}). The DB is open by the live app, so we read a copy.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

from ..models import NormalizedEvent, SessionHandle

SOURCE = "cursor"


def default_global_db() -> Path:
    return (Path(os.path.expanduser("~")) / "Library" / "Application Support" / "Cursor"
            / "User" / "globalStorage" / "state.vscdb")


def _hash(cid, bid):
    return hashlib.sha1(f"{SOURCE}:{cid}:{bid}".encode()).hexdigest()


class CursorAdapter:
    id = SOURCE

    def __init__(self, global_db):
        self.global_db = Path(global_db)
        self._copy = None
        self._conn = None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        self._copy = tempfile.mktemp(suffix=".vscdb")  # avoid lock on the live DB
        shutil.copy2(self.global_db, self._copy)
        self._conn = sqlite3.connect(self._copy)
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def discover(self) -> Iterator[SessionHandle]:
        if not self.global_db.exists():
            return
        try:
            con = self._connect()
            rows = con.execute(
                "SELECT key FROM cursorDiskKV WHERE key LIKE 'composerData:%'").fetchall()
        except Exception:
            return
        for r in rows:
            yield SessionHandle(SOURCE, r["key"].split(":", 1)[1], str(self.global_db))

    def read(self, handle: SessionHandle) -> Iterator[NormalizedEvent]:
        try:
            con = self._connect()
        except Exception:
            return
        cid = handle.native_id
        row = con.execute("SELECT value FROM cursorDiskKV WHERE key=?",
                          (f"composerData:{cid}",)).fetchone()
        if not row:
            return
        try:
            cd = json.loads(row["value"])
        except (json.JSONDecodeError, ValueError, TypeError):
            return
        headers = cd.get("fullConversationHeadersOnly") or []
        project_root = None
        seq = 0
        events: list[NormalizedEvent] = []
        for h in headers:
            bid = h.get("bubbleId")
            if not bid:
                continue
            brow = con.execute("SELECT value FROM cursorDiskKV WHERE key=?",
                               (f"bubbleId:{cid}:{bid}",)).fetchone()
            if not brow:
                continue
            try:
                b = json.loads(brow["value"])
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            text = (b.get("text") or "").strip()
            if not text:  # skip empty context-carrier bubbles
                continue
            btype = b.get("type")
            role = "user" if btype == 1 else "assistant" if btype == 2 else "system"
            uris = b.get("workspaceUris") or []
            if uris and not project_root:
                project_root = unquote(urlparse(uris[0]).path) or None
            seq += 1
            events.append(NormalizedEvent(
                event_hash=_hash(cid, bid), source=SOURCE, session_native_id=cid,
                project_root=project_root, git_remote=None, git_branch=None, seq=seq,
                ts=b.get("createdAt"), role=role,
                agent_model=(b.get("modelInfo") or {}).get("modelName"),
                text=text, tool_calls=[], raw_json=""))
        for e in events:
            if not e.project_root:
                e.project_root = project_root
        yield from events
