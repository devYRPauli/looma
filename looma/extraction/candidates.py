"""Heuristic candidate-memory extraction (ARCHITECTURE.md 4.2/F).

Phase 1 uses pattern heuristics rather than a local LLM. Each detected line
becomes a CandidateMemory of a typed kind. These are staging-tier facts; only
promotion (section 5) moves corroborated ones into the graph.
"""

import re
from typing import Optional

from ..sanitize import is_noise, looks_like_code, strip_injected

# Ordered so the strongest/most-specific kind wins when several match a line.
# Bug patterns require an explicit problem assertion - never a bare "fix"/"error",
# which on the real corpus were dominated by code, logs, git output, and assistant
# narration of *completed* work ("I've fixed both issues"), driving bug to 79% of
# all candidates. Decision/architecture recall is broadened to balance the mix.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("decision", re.compile(r"(?i)\bwe (?:decided|chose|went with|agreed|settled on|opted)\b")),
    ("decision", re.compile(r"(?i)\bdecision\b\s*[:\-]")),
    ("decision", re.compile(r"(?i)\b(?:decided|chose|opting|settled) (?:to|on|for)\b")),
    ("decision", re.compile(r"(?i)\buse\s+\S.+\s+instead of\s+\S")),
    ("decision", re.compile(r"(?i)\b(?:use|prefer|choose|chose|going with|switch(?:ed)? to)\s+\S.+\s+(?:over|instead of|rather than)\s+\S")),
    ("decision", re.compile(r"(?i)\blet'?s (?:use|go with|switch to|stick with)\b")),
    # architecture: a design RULE, not any mention of the word "architecture"
    ("architecture", re.compile(r"(?i)\barchitecturally\b")),
    ("architecture", re.compile(r"(?i)\b(?:design decision|design rule|design constraint|hard constraint|trade-?off|invariant)\b")),
    ("architecture", re.compile(r"(?i)\b(?:must|should|has to) (?:always |never |only )?(?:come from|live in|go through|be owned by|be the source of truth)\b")),
    ("architecture", re.compile(r"(?i)\b(?:should|must|has to|needs? to|will) (?:always |never |only )?be (?:idempotent|stateless|immutable|atomic|deterministic|thread-safe|backwards?-compatible|append-only|monotonic|side-effect-free)\b")),
    # bug: explicit label or a concrete symptom assertion (not bare fix/error)
    ("bug", re.compile(r"(?i)(?:^|\s)(?:there'?s|found) a bug\b|\bthe bug is\b|\bbug\s*[:\-]")),
    ("bug", re.compile(r"(?i)\b(?:regression|race condition|deadlock|memory leak|null pointer|segfault|infinite loop)\b")),
    ("bug", re.compile(r"(?i)\b(?:returns?|returned|gives?|throws?|raises?|shows?|displays?) (?:the )?(?:wrong|incorrect|stale|empty|duplicate|a 5\d\d)\b")),
    ("bug", re.compile(r"(?i)\boff by (?:a|an|one|\d)\b")),
    ("bug", re.compile(r"(?i)\b(?:does(?:n'?t| not)|do(?:n'?t| not)|is(?:n'?t| not)|are(?:n'?t| not)|won'?t|can'?t|never) (?:work|working|render|load|save|persist|update|fire|trigger|match|return|remove)\b")),
    ("bug", re.compile(r"(?i)\bis (?:broken|crashing|hanging|leaking|failing|incorrect|wrong|flaky)\b")),
    ("bug", re.compile(r"(?i)\b(?:crashes|crashed|hangs|deadlocks|silently (?:drops|fails|swallows))\b")),
    ("todo", re.compile(r"(?i)\bTODO\b")),
    ("todo", re.compile(r"^\s*[-*]\s*\[\s\]\s+")),
    ("todo", re.compile(r"(?i)\b(?:we (?:\w+ )?(?:need to|should|must|have to|still have to)|still need to|next step|follow-?up|needs? to be (?:done|added|written))\b")),
]

_MAX_LINE = 180
_MAX_PER_SESSION = 40
# lines that are mostly noise/log/caveat boilerplate, git plumbing, or table rows
_SKIP = re.compile(
    r"(?i)(local-command|system-reminder|caveat:|stdout|stderr|\bnpm (?:warn|err)\b|"
    r"traceback|most recent call last|@typescript-eslint|"
    r"remotes?/origin/|set up to track|^\s*\[?(?:branch|detached|HEAD)\b|"
    r"^\s*(?:error|fatal|warning):|^\s*at\s+\S+\(|^\s*file\s+\".+\",\s*line\b)"
)
# Not a bug report: completed-work narration ("I've fixed", "this fixes"),
# negated/absent problems ("no regression", "not a crash"), and the standing
# vocabulary of test names / coverage ("regression test", "regression.test").
_BUG_NOT = re.compile(
    r"(?i)\b(?:i'?ve|we'?ve|now|already|just|this|that|these|here'?s the|the)\b[^.]*\bfix(?:ed|es)\b"
    r"|\bfix(?:ed|es)\b[^.]*\b(?:issue|bug|it|this|both|all|now)\b"
    r"|\b(?:no|without|zero|any|eliminates?|avoids?|prevents?|not a) (?:\w+ )?(?:regression|crash|failure|error)"
    r"|\bregression[\s_.-]?test|\.test\.|are not (?:service )?crashes"
)
_ALPHA_WORD = re.compile(r"[A-Za-z]{2,}")
# file/log dumps and memory-log lines start with a bare line number or a
# "<n> <date>" stamp (Looma's own memory format) - never human prose. A real
# numbered list uses "5." (digit+period), which this does not match.
_LINE_DUMP = re.compile(r"^\s*\d{2,7}\s+\S")


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _classify(line: str) -> Optional[str]:
    for kind, pat in _PATTERNS:
        if pat.search(line):
            return kind
    return None


def extract_candidates(messages: list[dict]) -> list[dict]:
    """Return candidate dicts: {kind, title, body, ts, message_id, role}."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = strip_injected(m.get("text") or "")
        for raw_line in text.splitlines():
            line = _clean(raw_line)
            if not (12 <= len(line) <= _MAX_LINE):
                continue
            if _SKIP.search(line) or is_noise(line) or _LINE_DUMP.search(line):
                continue
            if looks_like_code(line) or line.count("*") >= 2 or line.count("|") >= 2:
                # code/diff fragments, markdown-bolded spec text, and table rows
                continue
            if len(_ALPHA_WORD.findall(line)) < 4:
                continue
            kind = _classify(line)
            if not kind:
                continue
            # completed-fix narration, negated problems, and test names are not bugs
            if kind == "bug" and _BUG_NOT.search(line):
                continue
            key = (kind, line.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "kind": kind,
                    "title": line,
                    "body": line,
                    "ts": m.get("ts"),
                    "message_id": m.get("id"),
                    "role": role,
                }
            )
            if len(out) >= _MAX_PER_SESSION:
                return out
    return out
