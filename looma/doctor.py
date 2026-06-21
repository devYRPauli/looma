"""Environment diagnostics for `looma doctor`."""

import os
import sqlite3
import sys
from pathlib import Path

from . import config, gitutil

OK, WARN, FAIL = "ok", "warn", "fail"


def _python() -> tuple[str, str, str]:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return ("Python version", OK, ver)
    return ("Python version", FAIL, f"{ver} (need >= 3.10)")


def _fts5() -> tuple[str, str, str]:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return ("SQLite FTS5", OK, "available")
    except Exception as e:  # pragma: no cover - environment dependent
        return ("SQLite FTS5", FAIL, f"unavailable: {e}")


def _claude_history() -> tuple[str, str, str]:
    p = config.claude_projects_dir()
    if not p.exists():
        return ("Claude history", WARN, f"not found at {p} (nothing to ingest yet)")
    n = len(list(p.glob("*/*.jsonl")))
    if n == 0:
        return ("Claude history", WARN, f"{p} exists but has no transcripts")
    return ("Claude history", OK, f"{n} transcript files under {p}")


def _data_dir(db_path: Path) -> tuple[str, str, str]:
    d = db_path.parent
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".looma_write_probe"
        probe.write_text("ok")
        probe.unlink()
        return ("Looma data dir", OK, f"writable: {d}")
    except Exception as e:
        return ("Looma data dir", FAIL, f"not writable ({d}): {e}")


def _database(db_path: Path) -> tuple[str, str, str]:
    if db_path.exists():
        size = db_path.stat().st_size
        return ("Database", OK, f"{db_path} ({size // 1024} KB)")
    return ("Database", WARN, f"not created yet at {db_path} (auto-created on first `looma ingest`)")


def _model_server() -> tuple[str, str, str]:
    from .extraction.extractor import detect_server, _local_url
    ok, model = detect_server()
    if ok:
        return ("Local model server", OK,
                f"{model} reachable - LLM extraction active (higher quality)")
    return ("Local model server", WARN,
            f"none at {_local_url()} - using stdlib heuristic extraction (fine). "
            "For best extraction run e.g. `llama-server -m <model.gguf> --port 8080`.")


def _git_repo() -> tuple[str, str, str]:
    root = gitutil.repo_root(os.getcwd())
    if root:
        remote = gitutil.normalize_remote(gitutil.remote_url(os.getcwd()))
        detail = root + (f"  remote={remote}" if remote else "  (no remote)")
        return ("Current git repo", OK, detail)
    return ("Current git repo", WARN, f"{os.getcwd()} is not a git repo (path-key fallback used)")


def run(db_path) -> list[tuple[str, str, str]]:
    db_path = Path(db_path)
    return [
        _python(),
        _fts5(),
        _data_dir(db_path),
        _database(db_path),
        _claude_history(),
        _git_repo(),
        _model_server(),
    ]
