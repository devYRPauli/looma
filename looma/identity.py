"""Project identity resolution (ARCHITECTURE.md section 3).

First hit wins: normalized git remote -> git root path -> raw path. The returned
canonical_key is the cross-agent join key.
"""

import os
import re
from typing import Optional

from . import gitutil

# Roots that are not a project: the filesystem root, OS temp/scratch dirs, and
# config/cache homes. A session whose only cwd is one of these does not "belong"
# to a repository - it is routed to the shared unsorted bucket instead of minting
# a junk project named "tmp" or "/". (V2 Phase 2 identity hygiene.)
_EPHEMERAL_ROOT = re.compile(
    r"^/$"                                            # filesystem root
    r"|^/(?:private/)?tmp/?$"                         # the temp root itself
    r"|^/var/tmp/?$"
    r"|^/(?:private/)?var/folders/[^/]+/[^/]+/[A-Z]/?$"  # macOS per-user scratch root (.../T, .../C)
    r"|/\.(?:claude|codex|cursor|config|cache)(?:/|$)"   # config/cache homes
)


def _is_ephemeral(path: str) -> bool:
    # match both the raw and symlink-resolved form (/tmp vs /private/tmp on macOS)
    if _EPHEMERAL_ROOT.search(path) or _EPHEMERAL_ROOT.search(os.path.realpath(path)):
        return True
    # the user's home directory itself (not a subdir of it) is too broad
    return os.path.abspath(path) == os.path.abspath(os.path.expanduser("~"))


# A session run from a bare temp root often still does real work in a subdir - an
# OSS repo cloned into /tmp/hunt-foo, a fixture under /var/folders. We recover the
# project from the files it touched. The harness scratchpad (claude-<uid>/...) and
# dotfiles are noise, not projects.
_TEMP_ROOT = re.compile(
    r"^(/(?:private/)?tmp"
    r"|/(?:private/)?var/folders/[^/]+/[^/]+/[A-Z])(?=/)"
)
_SCRATCH_SUBDIR = re.compile(r"^claude-\d+$|^\.")


def _touched_paths(events) -> list:
    """Absolute file paths the session read or edited, from its tool calls."""
    paths = []
    for ev in events:
        for call in getattr(ev, "tool_calls", None) or []:
            inp = call.get("input")
            if not isinstance(inp, dict):
                continue
            for key in ("file_path", "path", "notebook_path"):
                v = inp.get(key)
                if isinstance(v, str) and v.startswith("/"):
                    paths.append(v)
    return paths


def _touched_dirs(events) -> list:
    """Working directories the session ran commands in (e.g. a shell tool's
    `workdir` / `cwd`). Recovers the project when the session cwd is ephemeral."""
    dirs = []
    for ev in events:
        for call in getattr(ev, "tool_calls", None) or []:
            inp = call.get("input")
            if not isinstance(inp, dict):
                continue
            for key in ("workdir", "cwd"):
                v = inp.get(key)
                if isinstance(v, str) and v.startswith("/"):
                    dirs.append(v)
    return dirs


def _temp_subdir(real: str) -> Optional[str]:
    """temp-root + first subdir for a path under a scratch root, else None."""
    m = _TEMP_ROOT.match(real)
    if not m:
        return ""  # not under a temp root (sentinel distinct from "skip")
    rest = real[m.end():].lstrip("/")
    first = rest.split("/", 1)[0] if rest else ""
    if not first or _SCRATCH_SUBDIR.match(first):
        return None
    return m.group(1) + "/" + first


def _candidate_project_dir(path: str) -> Optional[str]:
    """The project directory a touched file implies, or None if it is scratch.

    Under a temp root: temp-root + first subdir (the cloned repo / fixture);
    the harness scratchpad is skipped. Elsewhere: the enclosing git repo root if
    one exists on disk, so the session merges with that project's identity.
    """
    real = os.path.realpath(path)
    sub = _temp_subdir(real)
    if sub != "":
        return sub  # under a temp root (a recovered subdir, or None for scratch)
    return gitutil.repo_root(os.path.dirname(real)) or None


def _candidate_from_dir(d: str) -> Optional[str]:
    """The project a working directory implies (e.g. a shell tool's `workdir`),
    or None if it is an ephemeral/scratch location. Unlike a file path, the dir
    is itself the candidate repo root."""
    real = os.path.realpath(d)
    if _is_ephemeral(real):
        return None
    sub = _temp_subdir(real)
    if sub != "":
        return sub
    return gitutil.repo_root(real) or real


def resolve_from_events(events) -> Optional[dict]:
    """Fallback identity for an ephemeral-cwd session: recover the project from
    the files it actually touched. Used only when resolve(cwd) gives nothing.

    Returns the identity of the dominant touched directory, or None when the
    session left no recoverable project footprint (pure scratch work).
    """
    counts: dict[str, int] = {}
    for p in _touched_paths(events):
        d = _candidate_project_dir(p)
        if d:
            counts[d] = counts.get(d, 0) + 1
    for wd in _touched_dirs(events):
        d = _candidate_from_dir(wd)
        if d:
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return None
    # dominant directory wins; deepest path breaks ties (more specific project)
    best = max(counts, key=lambda d: (counts[d], len(d)))
    ident = resolve(best)
    if ident:
        return ident
    return {
        "canonical_key": f"path:{best}",
        "display_name": os.path.basename(best) or best,
        "root_path": None,
        "git_remote": None,
    }


def resolve(root: Optional[str]) -> Optional[dict]:
    """Resolve a directory to a project identity dict, or None if no root.

    Returns None for missing, ephemeral, or degenerate roots (temp dirs, the
    filesystem root, config homes) so the caller buckets the session rather than
    creating a meaningless project.
    """
    if not root:
        return None
    root = os.path.abspath(os.path.expanduser(root))

    git_root = gitutil.repo_root(root) or root
    # an ephemeral *git root* is still ephemeral; a real repo under /tmp is rare
    # and not worth a permanent identity
    if _is_ephemeral(git_root):
        return None
    remote = gitutil.normalize_remote(gitutil.remote_url(root))

    if remote:
        canonical_key = remote
    elif git_root:
        canonical_key = f"path:{git_root}"
    else:
        canonical_key = f"path:{root}"

    display = os.path.basename(git_root.rstrip("/")) or git_root
    return {
        "canonical_key": canonical_key,
        "display_name": display,
        "root_path": git_root,
        "git_remote": remote,
    }
