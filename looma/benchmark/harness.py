"""Benchmark harness - precision / recall / F1 for extraction quality (goal Phase A).

Loads golden fixtures (sessions with hand-labeled gold entities), runs an Extractor,
matches predictions to gold by token overlap, and reports per-kind and overall
precision/recall/F1. Honest by construction: matching is generous (paraphrase-
tolerant) but a prediction with no gold counterpart is a false positive.
"""

import json
from pathlib import Path

from ..extraction.extractor import MEMORY_KINDS, HeuristicExtractor, LocalLLMExtractor
from ..util import tokens as _tok

_STOP = {"the", "a", "an", "to", "of", "for", "and", "so", "is", "it", "we",
         "in", "on", "that", "this", "be", "with", "use", "add", "fix"}
FIXTURES = Path(__file__).parent / "fixtures.json"
# extractor kinds are singular; fixture gold keys are plural
GOLD_KEY = {"decision": "decisions", "todo": "todos", "bug": "bugs", "architecture": "architecture"}


def _toks(s: str) -> set:
    return {t for t in _tok(s) if t not in _STOP and len(t) > 2}


def _matches(pred: str, gold: str) -> bool:
    a, b = _toks(pred), _toks(gold)
    if not a or not b:
        return False
    inter = len(a & b)
    jacc = inter / len(a | b)
    overlap = inter / min(len(a), len(b))
    return jacc >= 0.3 or overlap >= 0.55


def _score_kind(preds: list[str], gold: list[str]) -> tuple[int, int, int]:
    """Return (tp, fp, fn) matching each gold at most once."""
    used = set()
    tp = 0
    for p in preds:
        for i, g in enumerate(gold):
            if i in used:
                continue
            if _matches(p, g):
                used.add(i)
                tp += 1
                break
    fp = len(preds) - tp
    fn = len(gold) - len(used)
    return tp, fp, fn


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3),
            "recall": round(r, 3), "f1": round(f1, 3)}


def load_fixtures() -> list[dict]:
    return json.loads(FIXTURES.read_text())


def run(extractor, fixtures=None) -> dict:
    fixtures = fixtures or load_fixtures()
    per_kind = {k: [0, 0, 0] for k in MEMORY_KINDS}
    work_kind_ok = work_label_ok = work_total = 0

    for fx in fixtures:
        out = extractor.extract(fx["messages"])
        gold = fx["gold"]
        by_kind = {k: [] for k in MEMORY_KINDS}
        for m in out.get("memories", []):
            if m["kind"] in by_kind:
                by_kind[m["kind"]].append(m["title"])
        for k in MEMORY_KINDS:
            tp, fp, fn = _score_kind(by_kind[k], gold.get(GOLD_KEY[k], []))
            per_kind[k][0] += tp
            per_kind[k][1] += fp
            per_kind[k][2] += fn
        # work label
        gw = gold.get("work") or {}
        if gw:
            work_total += 1
            w = out.get("work") or {}
            if (w.get("kind") or "") == gw.get("kind"):
                work_kind_ok += 1
            label = (w.get("label") or "").lower()
            if label and any(kw in label for kw in gw.get("keywords", [])):
                work_label_ok += 1

    tot = [sum(per_kind[k][i] for k in MEMORY_KINDS) for i in range(3)]
    return {
        "extractor": extractor.name,
        "overall": _prf(*tot),
        "per_kind": {k: _prf(*per_kind[k]) for k in MEMORY_KINDS},
        "work_kind_accuracy": round(work_kind_ok / work_total, 3) if work_total else 0.0,
        "work_label_hit_rate": round(work_label_ok / work_total, 3) if work_total else 0.0,
    }


def format_one(m: dict) -> str:
    o = m["overall"]
    lines = [f"Extractor: {m['extractor']}",
             f"  Memory  P={o['precision']:.2f} R={o['recall']:.2f} F1={o['f1']:.2f}"
             f"  (tp={o['tp']} fp={o['fp']} fn={o['fn']})"]
    for k in MEMORY_KINDS:
        pk = m["per_kind"][k]
        lines.append(f"    {k:13} P={pk['precision']:.2f} R={pk['recall']:.2f} F1={pk['f1']:.2f}")
    lines.append(f"  Work    kind-acc={m['work_kind_accuracy']:.2f} "
                 f"label-hit={m['work_label_hit_rate']:.2f}")
    return "\n".join(lines)


def compare() -> str:
    fixtures = load_fixtures()
    h = run(HeuristicExtractor(), fixtures)
    out = [format_one(h)]
    try:
        llm_m = run(LocalLLMExtractor(), fixtures)
        out.append("")
        out.append(format_one(llm_m))
        hf, lf = h["overall"]["f1"], llm_m["overall"]["f1"]
        verdict = ("llm WINS" if lf > hf else "heuristic wins" if hf > lf else "tie")
        delta = lf - hf
        out.append("")
        out.append(f"VERDICT: {verdict}  (heuristic F1={hf:.2f} vs llm F1={lf:.2f}, "
                   f"delta={delta:+.2f})")
        out.append("Keep the LLM extractor only if it wins (LOOMA_EXTRACTOR=llm).")
    except Exception as e:
        out.append("")
        out.append(f"LLM extractor unavailable ({type(e).__name__}); "
                   "start a local model server (llama-server / ollama). Heuristic shown above.")
    return "\n".join(out)
