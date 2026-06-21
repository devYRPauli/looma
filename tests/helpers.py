"""Shared test helpers: synthetic Claude transcripts + stores."""

import json
import os
from pathlib import Path

# Pin extraction to the deterministic stdlib heuristic for tests, regardless of
# whether a local model server happens to be running (default mode is now 'auto').
os.environ["LOOMA_EXTRACTOR"] = "heuristic"
# Disable the optional vector store in tests (deterministic, no network probe).
os.environ["LOOMA_VECTORS"] = "off"

from looma.storage.sqlite_store import Store


def make_store():
    store = Store.open(":memory:")
    store.migrate()
    return store


def write_session(projects_dir: Path, encoded_cwd: str, session_id: str, records: list[dict]):
    d = projects_dir / encoded_cwd
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_id}.jsonl"
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def user_rec(uuid, session_id, cwd, branch, text, ts="2026-06-18T10:00:00Z"):
    return {
        "type": "user", "uuid": uuid, "sessionId": session_id, "cwd": cwd,
        "gitBranch": branch, "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def assistant_edit_rec(uuid, session_id, cwd, branch, file_path, ts="2026-06-18T10:01:00Z",
                       model="claude-opus-4-8"):
    return {
        "type": "assistant", "uuid": uuid, "sessionId": session_id, "cwd": cwd,
        "gitBranch": branch, "timestamp": ts,
        "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": file_path}}],
        },
    }


def write_codex_session(codex_root, session_id, cwd, texts, ts="2026-06-18T10:00:00Z"):
    """texts: list of (role, text). Writes a Codex rollout-*.jsonl under codex_root/sessions."""
    d = codex_root / "sessions" / "2026" / "06" / "18"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-2026-06-18T10-00-00-{session_id}.jsonl"
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "session_meta", "timestamp": ts,
                             "payload": {"id": session_id, "cwd": cwd}}) + "\n")
        for role, text in texts:
            block = "input_text" if role == "user" else "output_text"
            fh.write(json.dumps({"type": "response_item", "timestamp": ts,
                                 "payload": {"type": "message", "role": role,
                                             "content": [{"type": block, "text": text}]}}) + "\n")
    return path
