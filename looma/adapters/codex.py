"""Codex CLI adapter (goal Phase 5).

Reads ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl. Records have a top-level `type`
and a `payload`. Message turns are `response_item` with payload.type=="message";
tool calls are payload.type=="function_call" (arguments is a JSON string). cwd comes
from `session_meta`/`turn_context`. No git branch and no per-record uuid are stored.
"""

import glob
import hashlib
import json
from pathlib import Path
from typing import Iterator

from ..models import NormalizedEvent, SessionHandle

SOURCE = "codex"


def _hash(session_id, seq):
    return hashlib.sha1(f"{SOURCE}:{session_id}:{seq}".encode()).hexdigest()


def _msg_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return " ".join(b["text"] for b in content
                    if isinstance(b, dict) and b.get("type") in ("input_text", "output_text")
                    and b.get("text"))


class CodexAdapter:
    id = SOURCE

    def __init__(self, root):
        self.root = Path(root)

    def discover(self) -> Iterator[SessionHandle]:
        sessions = self.root / "sessions"
        if not sessions.exists():
            return
        for p in sorted(glob.glob(str(sessions / "**" / "rollout-*.jsonl"), recursive=True)):
            yield SessionHandle(SOURCE, Path(p).stem, p)

    def read(self, handle: SessionHandle) -> Iterator[NormalizedEvent]:
        cwd = None
        seq = 0
        events: list[NormalizedEvent] = []
        try:
            fh = open(handle.path, "r", encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                t = rec.get("type")
                p = rec.get("payload") or {}
                ts = rec.get("timestamp")
                if t == "session_meta":
                    cwd = p.get("cwd") or cwd
                elif t == "turn_context":
                    cwd = p.get("cwd") or cwd
                elif t == "response_item":
                    pt = p.get("type")
                    if pt == "message":
                        role = p.get("role") or "user"
                        if role == "developer":
                            role = "system"
                        seq += 1
                        events.append(NormalizedEvent(
                            event_hash=_hash(handle.native_id, seq), source=SOURCE,
                            session_native_id=handle.native_id, project_root=cwd,
                            git_remote=None, git_branch=None, seq=seq, ts=ts, role=role,
                            agent_model=None, text=_msg_text(p.get("content")),
                            tool_calls=[], raw_json=line))
                    elif pt == "function_call":
                        try:
                            inp = json.loads(p.get("arguments") or "{}")
                        except (json.JSONDecodeError, ValueError):
                            inp = {}
                        call = {"name": p.get("name"), "input": inp}
                        if events and events[-1].role == "assistant":
                            events[-1].tool_calls.append(call)
                        else:
                            seq += 1
                            events.append(NormalizedEvent(
                                event_hash=_hash(handle.native_id, seq), source=SOURCE,
                                session_native_id=handle.native_id, project_root=cwd,
                                git_remote=None, git_branch=None, seq=seq, ts=ts,
                                role="assistant", agent_model=None, text="",
                                tool_calls=[call], raw_json=line))
        # backfill project_root for early events seen before the first cwd record
        for e in events:
            if not e.project_root:
                e.project_root = cwd
        yield from events
