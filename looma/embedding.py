"""Local embedding client (goal Phase 2).

Talks to a local OpenAI-compatible /v1/embeddings endpoint (e.g. llama.cpp
`llama-server --embeddings` with a small embedding model, or Ollama). Pure stdlib
urllib - no dependency. Absence of a server means no vectors and a clean FTS fallback.
"""

import functools
import json
import os
import urllib.request


def _url() -> str:
    return os.environ.get("LOOMA_EMBED_URL", "http://localhost:8081/v1/embeddings")


def _model() -> str:
    return os.environ.get("LOOMA_EMBED_MODEL", "local")


def embed(texts: list[str], timeout: float = 60.0):
    """Return a list of float vectors, or None on any failure."""
    if not texts:
        return []
    body = json.dumps({"model": _model(), "input": texts}).encode()
    req = urllib.request.Request(_url(), data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        out = [d["embedding"] for d in data["data"]]
        return out if out and out[0] else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def detect():
    """Return (ok, dim). Cached per process."""
    v = embed(["probe"], timeout=2.0)
    if v and v[0]:
        return (True, len(v[0]))
    return (False, 0)
