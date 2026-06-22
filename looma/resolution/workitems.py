"""Deterministic WorkItem generation + resolution (ARCHITECTURE.md 4.4, checklist E).

Phase 1 uses no LLM. A session's "work signal" is derived from its branch, touched
files, and user-message intent phrases. Signals are clustered into WorkItems using
multi-signal scoring with file overlap weighted highest (per 4.4). The embedding
cosine term is stubbed by lexical label similarity.
"""

import re
from typing import Optional

from .. import config
from ..sanitize import is_noise, looks_like_code, strip_injected
from ..util import jaccard, prettify_label, text_sim

_INTENT = re.compile(
    r"(?i)\b(implement(?:ing)?|build(?:ing)?|creat(?:e|ing)|add(?:ing)?|fix(?:ing)?|"
    r"refactor(?:ing)?|migrat(?:e|ing)|investigat(?:e|ing)|debug(?:ging)?|"
    r"continu(?:e|ing)|set(?:ting)? up|wir(?:e|ing) up|switch(?:ing)? to)\b\s+(.{3,60})"
)
# verb-stem -> canonical verb (so -ing/-ion forms map to the right kind/label)
_VERB_STEM = [
    ("migrat", "migrate"), ("investigat", "investigate"), ("refactor", "refactor"),
    ("debug", "debug"), ("implement", "implement"), ("continu", "continue"),
    ("switch", "switch to"), ("creat", "create"), ("build", "build"),
    ("set", "set up"), ("wir", "wire up"), ("add", "add"), ("fix", "fix"),
]
_CODEISH = re.compile(r"[{}<>=+|;`\\]")
# SQL/DDL and dotted-identifier / quoted-ternary patterns the verb regex would
# otherwise scrape out of pasted code, docs, and JSX (Phase 1: a big source of
# garbage titles like 'Create TABLE IF NOT EXISTS Projects').
_NOT_INTENT = re.compile(
    # contiguous SQL structure only - bare words like "table"/"from"/"where"
    # are ordinary English and must not reject prose ("migrate the users table")
    r"(?i)\b(?:create|alter|drop) table\b|\binsert into\b|\bselect\b.+\bfrom\b|"
    r"\bvalues\s*\(|\b(?:primary|foreign) key\b|\bnot null\b|\bvarchar\s*\(|"
    r"create table if not exists"
    r"|\w\.\w"                 # dotted identifier, e.g. store.set_defaults
    r'|"\s*[:?]'              # quoted ternary / JSX string literal
    r"|\)\.\w"                # method chain
)
_LEAD_FILLER = re.compile(r"(?i)^(?:the|a|an|to|now|then|please|out|up|in|on|this|that|it|all)\b\s*")
_ALPHA_TOK = re.compile(r"[A-Za-z]{3,}")
# path segments too generic to name an effort after
_GENERIC_SEG = {
    "src", "lib", "app", "apps", "packages", "package", "test", "tests", "dist",
    "build", "node_modules", "components", "component", "pages", "page", "api",
    "utils", "util", "scripts", "script", "docs", "doc", "public", "static",
    "styles", "assets", "config", "index", "main", "__pycache__", "backend",
    "frontend", "server", "client", "core",
}
_SEG_SPLIT = re.compile(r"[/\\]")
_KIND = {
    "fix": "bugfix", "debug": "bugfix",
    "refactor": "refactor",
    "migrate": "migration",
    "investigate": "investigation",
}
_GENERIC_BRANCH = {"", "head", "main", "master", "develop", "dev"}
_STOP_TAIL = re.compile(r"(?i)\b(so that|because|in order to|using|with the|and then|to make)\b.*$")


def _intent(messages: list[dict]) -> tuple[Optional[str], str]:
    """Return (label, kind) from the first real user intent phrase, else (None, 'feature')."""
    for m in messages:
        if m.get("role") != "user":
            continue
        text = strip_injected(m.get("text") or "")
        if not text or is_noise(text):
            continue
        for line in text.splitlines():
            line = line.strip()
            # whole-line rejects: code/diff/log lines and pasted SQL/JSX never
            # carry the user's intent, even when they contain an imperative verb.
            if looks_like_code(line) or _NOT_INTENT.search(line):
                continue
            match = _INTENT.search(line)
            if not match:
                continue
            raw_verb = match.group(1).lower()
            # normalize an inflected verb ("migrating") to its canonical form
            verb = next((canon for stem, canon in _VERB_STEM if raw_verb.startswith(stem)), raw_verb)
            # trim the tail at the first clause boundary so a trailing ";" or "."
            # (legitimate prose punctuation) does not poison the label
            tail = re.split(r"[;.]\s|,\s+(?:and|so|which|but)\b", match.group(2))[0]
            tail = _STOP_TAIL.sub("", tail).strip().strip("`\"'.,:;()[]")
            tail = _LEAD_FILLER.sub("", tail).strip()
            # reject code fragments and tails without at least two real words
            if (not tail or _CODEISH.search(tail) or _NOT_INTENT.search(tail)
                    or len(_ALPHA_TOK.findall(tail)) < 2):
                continue
            kind = _KIND.get(verb, "feature")
            return f"{verb} {tail}".strip()[:60], kind
    return None, "feature"


def _salient_segment(files: list[str]) -> Optional[str]:
    """Most frequent meaningful path segment (dir or filename stem), skipping
    generic scaffolding dirs - so a generic top dir does not name the effort."""
    counts: dict[str, int] = {}
    for f in files:
        parts = [p for p in _SEG_SPLIT.split(f) if p]
        for i, p in enumerate(parts):
            seg = p
            if i == len(parts) - 1:  # filename -> use stem
                seg = p.rsplit(".", 1)[0]
            low = seg.lower()
            if not seg or low in _GENERIC_SEG or low.startswith(".") or len(seg) < 3:
                continue
            counts[seg] = counts.get(seg, 0) + 1
    if not counts:
        return None
    # prefer frequency, then longer (more specific) names
    return sorted(counts.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]


def _title_from(label: Optional[str], files: list[str], branch: Optional[str]) -> str:
    if label:
        return prettify_label(label)
    seg = _salient_segment(files) if files else None
    if seg:
        return f"Work on {prettify_label(seg.replace('-', ' ').replace('_', ' '))}"
    if branch and branch.lower() not in _GENERIC_BRANCH:
        return f"Work on {branch}"
    if files:
        top = files[0].split("/")[0]
        return f"Work in {top}/"
    return "Untitled work"


def build_session_signal(session: dict, messages: list[dict], files: list[str]) -> dict:
    label, kind = _intent(messages)
    return {
        "session_id": session["id"],
        "branch": session.get("branch"),
        "files": set(files),
        "label": label,
        "kind": kind,
        "agent_model": session.get("agent_model"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
    }


def _score(signal: dict, wi: dict) -> float:
    files = 0.45 * jaccard(signal["files"], wi["files"])
    same_branch = signal.get("branch") and wi.get("branch")
    branch = 0.25 if (same_branch and signal["branch"].lower() not in _GENERIC_BRANCH
                      and signal["branch"] == wi["branch"]) else 0.0
    label_sim = 0.0
    if signal.get("label"):
        label_sim = 0.20 * max(
            [text_sim(signal["label"], a) for a in wi["aliases"]] + [text_sim(signal["label"], wi["title"])]
        )
    alias = 0.10 if signal.get("label") and signal["label"] in wi["aliases"] else 0.0
    return files + branch + label_sim + alias


def resolve(signals: list[dict]) -> list[dict]:
    """Cluster session signals into WorkItems. Returns list of WorkItem builders.

    Each builder: {title, kind, aliases, files, branch, members:[session_id],
    agents:set, started, ended, related:[index]}.
    """
    workitems: list[dict] = []
    # process oldest-first so titles anchor on the originating session
    for sig in sorted(signals, key=lambda s: (s.get("started_at") or "", s["session_id"])):
        best_i, best_score = -1, 0.0
        for i, wi in enumerate(workitems):
            sc = _score(sig, wi)
            if sc > best_score:
                best_i, best_score = i, sc

        if best_i >= 0 and best_score >= config.RESOLVE_HIGH:
            _absorb(workitems[best_i], sig)
            continue
        workitems.append(_new_workitem(sig))

    # Phase 1 fix: agglomerative merge of WorkItems editing substantially the same
    # files (the dominant under-merging cause), then RELATED only for moderate overlap.
    workitems = _merge_pass(workitems)
    _compute_related(workitems)
    return workitems


def _file_jac(a: set, b: set) -> float:
    return jaccard(a, b)


def _merge_pass(builders: list[dict]) -> list[dict]:
    """Union-find merge any two builders with file overlap >= MERGE_FILE_JACCARD."""
    n = len(builders)
    if n < 2:
        return builders
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _file_jac(builders[i]["files"], builders[j]["files"]) >= config.MERGE_FILE_JACCARD:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [_coalesce([builders[k] for k in idxs]) for idxs in groups.values()]


def _coalesce(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]
    group = sorted(group, key=lambda b: (b.get("started_at") or "", b["title"]))
    base = dict(group[0])  # earliest builder anchors title/kind/branch
    base["members"] = sorted({m for b in group for m in b["members"]})
    base["files"] = set().union(*(b["files"] for b in group))
    base["aliases"] = set().union(*(b["aliases"] for b in group))
    base["agents"] = set().union(*(b["agents"] for b in group))
    starts = [b["started_at"] for b in group if b.get("started_at")]
    ends = [b["ended_at"] for b in group if b.get("ended_at")]
    base["started_at"] = min(starts) if starts else None
    base["ended_at"] = max(ends) if ends else None
    base["related"] = []
    return base


def _compute_related(builders: list[dict]) -> None:
    """RELATED links only for genuinely moderate (not merge-worthy) file overlap."""
    for b in builders:
        b["related"] = []
    for i in range(len(builders)):
        for j in range(i + 1, len(builders)):
            jac = _file_jac(builders[i]["files"], builders[j]["files"])
            if config.RELATED_MIN <= jac < config.MERGE_FILE_JACCARD:
                builders[i]["related"].append(j)


def _new_workitem(sig: dict) -> dict:
    title = _title_from(sig.get("label"), sorted(sig["files"]), sig.get("branch"))
    aliases = set()
    if sig.get("label"):
        aliases.add(sig["label"])
    return {
        "title": title,
        "kind": sig["kind"],
        "aliases": aliases,
        "files": set(sig["files"]),
        "branch": sig.get("branch"),
        "members": [sig["session_id"]],
        "agents": {sig["agent_model"]} if sig.get("agent_model") else set(),
        "started_at": sig.get("started_at"),
        "ended_at": sig.get("ended_at"),
        "related": [],
    }


def _absorb(wi: dict, sig: dict) -> None:
    wi["files"] |= sig["files"]
    if sig.get("label"):
        wi["aliases"].add(sig["label"])
    wi["members"].append(sig["session_id"])
    if sig.get("agent_model"):
        wi["agents"].add(sig["agent_model"])
    if sig.get("started_at") and (not wi["started_at"] or sig["started_at"] < wi["started_at"]):
        wi["started_at"] = sig["started_at"]
    if sig.get("ended_at") and (not wi["ended_at"] or sig["ended_at"] > wi["ended_at"]):
        wi["ended_at"] = sig["ended_at"]
    if not wi.get("branch") and sig.get("branch"):
        wi["branch"] = sig["branch"]
