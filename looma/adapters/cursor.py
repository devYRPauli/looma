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


def _uri_to_path(u: str):
    if isinstance(u, str) and u.startswith("file://"):
        p = unquote(urlparse(u).path)
        return p or None
    return None


def _paths_from_composer(cd: dict) -> list[str]:
    """Absolute paths of files the session edited or attached - used to recover the
    workspace when no bubble carries a workspaceUris. Cursor scatters these across
    several composer fields; we read the reliable file-edit ones. (V2.1)
    """
    paths = []
    ofs = cd.get("originalFileStates")  # dict keyed by file:// URI of each edited file
    if isinstance(ofs, dict):
        for uri in ofs:
            p = _uri_to_path(uri)
            if p:
                paths.append(p)
    for item in cd.get("newlyCreatedFiles") or []:  # {uri: {fsPath, external, path}}
        uri = item.get("uri") if isinstance(item, dict) else None
        if isinstance(uri, dict):
            p = uri.get("fsPath") or uri.get("path") or _uri_to_path(uri.get("external") or "")
            if isinstance(p, str) and p.startswith("/"):
                paths.append(p)
    for u in cd.get("allAttachedFileCodeChunksUris") or []:
        p = _uri_to_path(u)
        if p:
            paths.append(p)
    return paths


def _common_root(paths: list[str]):
    """Deepest directory containing all given file paths (the workspace root).

    Cursor often omits a session's workspace but lists the files it touched in
    `allAttachedFileCodeChunksUris`. Their common parent recovers the project so
    the session clusters with the rest of that repo instead of going unsorted.
    """
    dirs = [os.path.dirname(p) for p in paths if p and p.startswith("/")]
    if not dirs:
        return None
    common = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
    # guard against a too-shallow common ancestor (e.g. /Users) that is not a project
    return common if common.count("/") >= 3 else None


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
        # Each discovery cycle needs a fresh snapshot of the live database.
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            Path(self._copy).unlink()
            self._copy = None
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

    def fingerprint(self, handle: SessionHandle):
        con = self._connect()
        row = con.execute("SELECT value FROM cursorDiskKV WHERE key=?",
                          (f"composerData:{handle.native_id}",)).fetchone()
        if not row:
            return None
        digest = hashlib.sha256()
        digest.update(str(row["value"]).encode())
        prefix = f"bubbleId:{handle.native_id}:"
        for bubble in con.execute(
            "SELECT key,value FROM cursorDiskKV WHERE key>=? AND key<? ORDER BY key",
            (prefix, prefix + "\uffff"),
        ):
            digest.update(json.dumps([bubble["key"], str(bubble["value"])]).encode())
        return "cursor-v1:" + digest.hexdigest()

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
        # composer-level workspace fallback: the common parent of the files the
        # session edited/attached, used when no bubble carries a workspaceUris
        # (V2 Phase 2 identity hygiene; V2.1 widened beyond attached chunks).
        composer_root = _common_root(_paths_from_composer(cd))
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
        resolved_root = project_root or composer_root
        for e in events:
            if not e.project_root:
                e.project_root = resolved_root
        yield from events
