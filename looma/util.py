"""Small shared helpers: tokenization, similarity, timestamps, label prettifying."""

import re
from datetime import datetime
from typing import Iterable, Optional

_WORD = re.compile(r"[a-z0-9]+")

# Map the common non-ASCII characters that leak in from transcript prose to ASCII
# so CLI output stays plain-text and copy-pasteable in any terminal.
_ASCII_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "→": "->", "•": "-", "·": "-",
}
_ASCII_RE = re.compile("|".join(re.escape(k) for k in _ASCII_MAP))


def to_ascii(text: str) -> str:
    """Best-effort fold of smart quotes / dashes / arrows to ASCII; drop the rest."""
    if not text:
        return text
    t = _ASCII_RE.sub(lambda m: _ASCII_MAP[m.group(0)], text)
    return t.encode("ascii", "ignore").decode("ascii")
_ACRONYMS = {
    "oauth": "OAuth", "api": "API", "jwt": "JWT", "ui": "UI", "cli": "CLI",
    "http": "HTTP", "https": "HTTPS", "sql": "SQL", "db": "DB", "id": "ID",
    "url": "URL", "sdk": "SDK", "mcp": "MCP", "fts": "FTS",
}


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def jaccard(a: Iterable, b: Iterable) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def text_sim(a: str, b: str) -> float:
    return jaccard(tokens(a), tokens(b))


def prettify_label(label: str) -> str:
    label = (label or "").strip().strip("`\"'.,:;")
    if not label:
        return ""
    out = []
    for word in label.split():
        low = word.lower()
        out.append(_ACRONYMS.get(low, word.capitalize() if word.islower() else word))
    return " ".join(out)


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def span_days(first: Optional[str], last: Optional[str]) -> float:
    a, b = parse_ts(first), parse_ts(last)
    if not a or not b:
        return 0.0
    return max(0.0, (b - a).total_seconds() / 86400.0)
