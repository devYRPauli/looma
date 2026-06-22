"""Strip harness-injected content from agent turns before extraction.

Claude transcripts fold system-reminders, slash-command scaffolding, skill text,
and local-command caveats into user-role turns. Left in, these fool the
deterministic intent/candidate heuristics (e.g. skill instructions read as a user
"investigate ..." request). We remove them so extraction sees real user prose.
"""

import re

_BLOCKS = [
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I),
    re.compile(r"<local-command-[^>]*>.*?</local-command-[^>]*>", re.S | re.I),
    re.compile(r"<command-[^>]*>.*?</command-[^>]*>", re.S | re.I),
]
_INLINE_TAG = re.compile(r"</?(?:system-reminder|local-command|command)[^>]*>", re.I)

_LINE_MARKERS = (
    "caveat:", "system-reminder", "do not respond to these messages",
    "local-command", "command-name", "command-message", "command-args",
    "command-stdout", "command-contents",
)

# phrases that mark a turn as injected agent-instruction boilerplate, not user intent
_NOISE_PHRASES = (
    "in your instructions", "per the method", "invoke the skill", "using the skill",
    "create a todo per", "you must use", "brainstorming", "you are claude",
    "following context", "this context may or may not be relevant",
    "checklist and start", "work through this carefully", "think step by step",
    "use this before any", "before any creative work",
    "invoke any other skill", "writing-plans is the next", "fix any issues inline",
)


def strip_injected(text: str) -> str:
    if not text:
        return ""
    t = text
    for pat in _BLOCKS:
        t = pat.sub(" ", t)
    t = _INLINE_TAG.sub(" ", t)
    keep = []
    for line in t.splitlines():
        low = line.strip().lower()
        if any(m in low for m in _LINE_MARKERS):
            continue
        keep.append(line)
    return "\n".join(keep)


def is_noise(text: str) -> bool:
    """True if the (already-stripped) text looks like injected boilerplate."""
    low = (text or "").lower()
    return any(p in low for p in _NOISE_PHRASES)


# Programmatic / synthetic sessions: API calls made BY tooling (memory-log
# summarizers, compression jobs, Looma's own extractor, one-shot "you are X"
# prompts) that land in the transcript dirs. They are not human coding work and
# must not generate WorkItems or memories - on the real corpus they were 48% of
# sessions and 84% of "Untitled work". The signature is an instruction-prompt
# opening in the first real user turn; these sessions are always short.
_SYNTHETIC_PROMPT = re.compile(
    r"(?i)you are summariz|non-destructive compression|daily memory log|"
    r"conversation extract below|memory entry in this exact format|"
    r"extract structured project memory|reply with only|respond with only|"
    r"output only|return only the|in this exact format|"
    r"you are an? (?:helpful|world-class|senior|expert|coding|summariz)|"
    r"your task is to (?:summariz|extract|classif|rewrite|compress|score|rank)"
)


def _first_user_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            t = strip_injected(m.get("text") or "").strip()
            if t:
                return t
    return ""


def is_automated_session(messages: list[dict]) -> bool:
    """True if a session is a programmatic API call, not human coding work.

    Detected from a synthetic instruction-prompt opening in the first user turn.
    Such sessions are excluded from WorkItem and candidate-memory generation so
    the graph reflects real work. Their raw messages are still stored.
    """
    return bool(_SYNTHETIC_PROMPT.search(_first_user_text(messages)[:600]))


# code / diff / log fragments that must never surface as a title, memory, or
# next step. Shared by retrieval (display-time filtering) and extraction.
_CODE_FRAGMENT = re.compile(
    r"(^\s*[+\-]\s)|(^\s*\+)|(^\s*#)|@@|//|[{}]|=>|;\s*$|`|</|/>|::|==|!=|\)\s*\{|<[A-Za-z][\w-]*[\s/>]|className=|"
    r"\b(?:const|let|await|function|return|def|import|class|console|throw new)\b|"
    r"Date\.now|\b\w+\([^)]*\)\s*[:{]|\b\w+\.\w+\s*\("  # dotted method call, e.g. store.set_defaults(
)
_ALPHA_RUN = re.compile(r"[A-Za-z]{3,}")
_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def looks_like_code(text: str) -> bool:
    """True if text reads like a code/diff/log line rather than human prose.

    Used to keep raw diff lines and source fragments out of resume bundles,
    next-step suggestions, and (in extraction) promoted memories.
    """
    t = (text or "").strip()
    if not t:
        return True
    if _CODE_FRAGMENT.search(t):
        return True
    # a bare file path / dotted identifier with no spaces is not prose
    # (e.g. "src/tests/foo-bar.test.ts", "looma/cli.py", "x.set_defaults")
    if " " not in t and ("/" in t or _FILE_EXT.search(t)):
        return True
    # too few real words to be a sentence (e.g. "cp037/gbk")
    return len(_ALPHA_RUN.findall(t)) < 2
