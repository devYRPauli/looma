# Next steps

Status after the "genuinely useful" work cycle. Every change here improved
extraction quality, resume quality, user trust, or reliability - not feature count.

## Completed work

### Phase A - Evaluation system (the gate)
- Golden dataset: 5 hand-labeled fixtures (`looma/benchmark/fixtures.json`), synthetic
  but modeled on real transcript noise (filler, code, stack traces, lint output).
- Benchmark harness (`looma/benchmark/harness.py`): paraphrase-tolerant matching ->
  per-kind and overall **precision / recall / F1**, plus work kind-accuracy and
  label-hit. False positives are counted honestly.
- Commands: `looma benchmark` and `looma benchmark --compare`.

### Phase B - Extraction quality
- `Extractor` interface with two implementations (`looma/extraction/extractor.py`),
  identical JSON schema, interchangeable and benchmarkable:
  - `HeuristicExtractor` - wraps the existing deterministic logic (no duplication).
  - `LocalLLMExtractor` - fully local (llama.cpp `llama-server` / Ollama, no hosted
    API), strict JSON, robust parsing (object or bare array), per-session fallback to
    heuristic on any error.
- Wired into the pipeline as **opt-in** (`LOOMA_EXTRACTOR=llm`); heuristic stays the
  default and the reliable fallback when no model server is running.

### Phase C - Human Correction Layer
- `looma correct merge | split | rename | promote | reject | false-positive | undo | log`.
- Corrections write a `correction_ledger` row + `correction_constraints`, then a
  deterministic rebuild applies them - **replayable and override automated inference**.
- Constraints anchor to STABLE keys (member session-id sets for WorkItems; (kind,
  normalized-title) for memories), so corrections survive reprocessing and id churn.

### Phase D - Graph Health Metrics
- `looma status --health`: conversion rate, merge rate, false-positive rate, average
  work item size, orphan candidates, unresolved related items (`looma/health.py`).

### Tests
- 53 tests pass (was 44): added benchmark scoring, correction merge/rename/reject/undo
  (with deterministic-replay assertions), and health metrics.

## Benchmark results

Golden set, memory extraction (decision / todo / bug / architecture):

| Extractor                 | Precision | Recall | F1   |
|---------------------------|-----------|--------|------|
| HeuristicExtractor        | 0.67      | 0.71   | 0.69 |
| LocalLLMExtractor (Qwen2.5-7B-Instruct Q3_K_M, local) | **1.00** | **0.93** | **0.96** |

VERDICT: **the local LLM extractor wins, +0.27 F1.** It eliminates the heuristic's
false positives (cue-words in filler/code/logs -> precision 0.67 to 1.00) while
matching recall. Per the stopping condition, it is kept (opt-in).

Note: a 1B-class model (OLMoE-1b-7b) did NOT beat the heuristic (F1 0.55) - the win
needs an ~7-8B model, as the goal anticipated. The harness makes this measurable
rather than assumed.

## Architectural changes

- Extraction is now behind an interface; the pipeline selects an extractor and always
  has a deterministic local fallback. No new runtime dependency (LLM is over local
  HTTP; absence degrades to heuristic).
- The rebuild is now correction-aware: `correction.apply_to_builders` reshapes
  WorkItems (merge/split/rename) and `corr.mem` overrides promotion - both keyed on
  stable anchors. The old id-keyed constraint stubs were removed.
- Graph health is a read-only view over existing tables; no schema change.

## Remaining opportunities (ranked by resume-quality impact)

1. **Default to LLM when a local server is detected**, with a one-time `looma doctor`
   hint; keep heuristic fallback. (Biggest quality lever, now proven by benchmark.)
2. **Route WorkItem titles through the LLM** too (work kind-acc 0.60 -> 0.80 in the
   benchmark). Currently only candidate memories use the extractor.
3. **Address the 158 unresolved RELATED items** surfaced by health on real data -
   resolution is under-merging; tune `RESOLVE_HIGH` or add an LLM merge judge.
4. Grow the golden set (more fixtures, more domains) to harden the benchmark.
5. Phase E: second adapter (Codex first) behind the existing `SourceAdapter` interface.
6. Evidence spans for LLM-extracted memories (currently only heuristic memories carry
   message-level evidence).

## How to reproduce the benchmark

```bash
# start any local OpenAI-compatible server, e.g. llama.cpp:
llama-server -m <qwen2.5-7b-instruct-q3_k_m.gguf> --port 8080 -ngl 99
LOOMA_LLM_URL=http://127.0.0.1:8080/v1/chat/completions looma benchmark --compare
```
