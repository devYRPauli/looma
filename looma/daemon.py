"""Watcher daemon (goal Phase 6) - keeps Looma current automatically.

Polls the transcript locations; when anything changed, runs an incremental,
idempotent ingest and rebuilds only if new messages arrived. Pure stdlib. Low
resource: idle polls are a cheap mtime stat (no parsing). Crash-safe: every cycle
is independent and idempotent on content-hash, so a kill mid-cycle loses nothing.
"""

import glob
import os
import time
from pathlib import Path

from . import pipeline
from .adapters.cursor import default_global_db
from .config import claude_projects_dir
from .storage.sqlite_store import Store


def transcript_mtime() -> float:
    """Newest modification time across all watched transcript locations (0 if none)."""
    newest = 0.0
    pats = [
        str(claude_projects_dir() / "*" / "*.jsonl"),
        str(Path(os.path.expanduser("~")) / ".codex" / "sessions" / "**" / "rollout-*.jsonl"),
    ]
    for pat in pats:
        for p in glob.iglob(pat, recursive=True):
            try:
                newest = max(newest, os.path.getmtime(p))
            except OSError:
                pass
    db = default_global_db()
    if db.exists():
        try:
            newest = max(newest, os.path.getmtime(db))
        except OSError:
            pass
    return newest


def cycle(store: Store, adapters=None) -> dict:
    """One incremental ingest; rebuild only when new messages arrived."""
    ing = pipeline.ingest_messages(store, adapters=adapters)
    if ing["new_messages"] > 0:
        pipeline.rebuild(store)
    return ing


def run(db_path, interval: int = 60, once: bool = False, verbose: bool = False,
        adapters=None, log=print) -> None:
    store = Store.open(db_path)
    store.migrate()
    log(f"looma daemon watching transcripts; db={db_path}, interval={interval}s. Ctrl-C to stop.")
    last_mtime = -1.0
    try:
        while True:
            m = transcript_mtime()
            if m != last_mtime:
                ing = cycle(store, adapters)
                last_mtime = m
                if ing["new_messages"] or verbose:
                    src = ", ".join(f"{k}:{v}" for k, v in sorted((ing.get("per_source") or {}).items()))
                    log(f"[looma] +{ing['new_messages']} messages from {ing['sessions']} sessions "
                        f"({src or 'none'}); graph updated")
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        log("\nlooma daemon stopped.")
    finally:
        store.close()
