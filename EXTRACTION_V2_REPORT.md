# Extraction Quality - V2 Phase 1 Report

Mission test for every change: "Does this make another agent substantially better
when Looma is present?" Extraction quality is the answer-crispness lever - if the
WorkItems are untitled and 79% of memories are mislabeled "bug", every downstream
resume/ask/brief/pack inherits that noise.

This phase was measured two ways, as required:
- Benchmark harness (`looma benchmark`) - labeled precision/recall/F1.
- Real corpus - 614 sessions across 72 projects, ingested from all three agents
  (Claude 396, Codex 170, Cursor 48), 75,202 messages. Rebuilt end to end.

---

## 1. Headline results

### Benchmark (golden fixtures, heuristic extractor)

| Metric                 | Baseline | V2    | Delta   |
|------------------------|----------|-------|---------|
| Memory F1 (overall)    | 0.69     | 0.90  | +0.21   |
| Memory precision       | 0.67     | 0.90  | +0.23   |
| Memory recall          | 0.71     | 0.90  | +0.19   |
| decision F1            | 1.00     | 1.00  | =       |
| todo F1                | 0.80     | 0.88  | +0.08   |
| bug F1                 | 0.67     | 0.80  | +0.13   |
| architecture F1        | 0.00     | 1.00  | +1.00   |
| work kind-accuracy     | 0.60     | 0.88  | +0.28   |
| work label-hit rate    | 0.60     | 1.00  | +0.40   |

The fixture set was also expanded from 5 to 8 with real-corpus-derived hard cases
(completed-fix narration, symptom-phrased bugs, design-property architecture), so
the V2 number is measured against a *harder* benchmark than the baseline.

### Real corpus (614 sessions, full rebuild)

| Metric                          | Baseline      | V2            | Delta        |
|---------------------------------|---------------|---------------|--------------|
| Work items (total)              | 420           | 243           | noise removed|
| - named (real title)            | 34.5%         | 56.4%         | +21.9 pts    |
| - "Work on <area>"              | 20.5%         | 30.5%         | +10.0 pts    |
| - "Untitled work"               | 45.0%         | 13.2%         | -31.8 pts    |
| Candidate memories (total)      | 2811          | 768           | -73%         |
| - bug share                     | 78.7%         | 37.8%         | -40.9 pts    |
| - architecture share            | 10.2%         | 7.0%          | rebalanced   |
| - todo share                    | 8.8%          | 48.7%         | rebalanced   |
| - decision share                | 2.3%          | 6.5%          | rebalanced   |
| Promoted-memory confidence      | median 0.00   | median 0.07   | grounded     |
|                                 | max 0.27      | max 0.33      |              |

Untitled work fell by 71% relative; bug overclassification fell from "four of
five memories are bugs" to a balanced distribution; promoted memories no longer
carry a self-contradictory 0.00 confidence.

---

## 2. Root causes found on the real corpus

### 2.1 Half the corpus was not human work (dominant cause of Untitled)

48.5% of sessions (298 of 614) were programmatic API calls that land in the same
transcript directories: daily-memory-log summarizers, "apply maximum
non-destructive compression" jobs, one-shot "you are a ..." prompts, and Looma's
own LLM-extractor prompt echoed back. They are short (median 2 messages), produce
no file artifacts, and carry no human intent.

They accounted for **84% of all "Untitled work"**. They were generating WorkItems
and memories indistinguishable, to a consuming agent, from real engineering work.

### 2.2 Bug classification keyed on bare tokens

The old classifier flagged a bug on any line containing `fix`, `error`,
`failing`, or `broken`. On real transcripts those tokens are dominated by:
- assistant narration of *completed* work ("I've fixed both issues"),
- code, diffs, and logs ("`+ "error": res.get("error")`", stack frames),
- git plumbing ("remotes/origin/fix/...", "branch 'fix/x' set up to track"),
- test names ("...-regression.test.ts"), and negations ("no regression").

The bare `fix` pattern alone produced 44% of all bug candidates. Genuine bug
reports were a minority of the "bug" pile.

### 2.3 WorkItem intent dropped legitimate sentences

`_intent` rejected any tail containing `;` (legitimate prose punctuation killed
"investigate the memory leak in the worker process; RSS grows ...") and any line
containing the bare word "table"/"from"/"where" (the SQL guard misfired on
"migrate the users table"). Inflected verbs ("migrating", "investigating") were
not recognized, so the work kind defaulted to "feature".

### 2.4 Promoted memories scored ~0 confidence

Candidate-memory confidence hardcoded `file_overlap = 0`, so a memory could be
promoted to "validated" yet score 0.00 - it ignored the grounding of the WorkItem
it documents.

---

## 3. Changes

All changes are stdlib-only, surgical, and covered by tests.

1. **Synthetic-session filter** (`sanitize.is_automated_session`, wired in
   `pipeline._rebuild_project`). Sessions whose first real user turn is an
   instruction-prompt signature are excluded from WorkItem and memory generation.
   Their raw messages are still stored - nothing is lost, they just stop
   masquerading as work.

2. **Bug classifier rewrite** (`extraction/candidates.py`). Bugs now require an
   explicit problem assertion: a "bug:"/"there's a bug" label, a named failure
   class (regression, race condition, memory leak, ...), or a symptom
   ("returns the wrong ...", "off by a cent", "does not work", "is broken").
   A negative guard drops completed-fix narration, negated problems
   ("no regression"), and test-name lines. Table rows, leading line-number /
   memory-log dumps, and the shared `looks_like_code` filter remove pasted
   content.

3. **Decision and architecture recall** (`extraction/candidates.py`). Decisions
   pick up "settled on", "switched to X over Y", "let's go with". Architecture
   now matches design *rules* - "architecturally", "design constraint",
   "trade-off", "invariant", and design properties ("must be idempotent /
   stateless / atomic ...") - instead of any line mentioning the word
   "architecture" (which used to match "ARCHITECTURE.md").

4. **WorkItem naming** (`resolution/workitems._intent`). Verb stems normalize
   ("migrating" -> migration kind), the label tail is trimmed at clause
   boundaries (so `;` no longer voids it), and the SQL guard now requires real
   SQL structure ("create table", "select ... from") rather than the bare word
   "table".

5. **Confidence calibration** (`pipeline.py`). A memory inherits 40% of its
   parent WorkItem's confidence, so a memory attached to committed, file-grounded
   work scores higher than one floating on a thin candidate - and no promoted
   memory reports 0.00.

---

## 4. Tests

- `tests/test_extraction_quality.py` (new) - guards the synthetic-session filter
  (5 synthetic openers flagged, 3 real ones not) and bug precision (fix-narration
  and test-names rejected, real symptom assertions accepted; architecture needs a
  rule, not a mention).
- `looma/benchmark/fixtures.json` - expanded 5 -> 8 with hard cases.
- Full suite: 90 passed, 1 skipped.

---

## 5. Impact on the mission

A consuming agent that calls `resume_work`, `ask`, `brief`, or `pack` now sees:
- WorkItems that are named after the work (56% vs 34%), not "Untitled".
- A memory stream where "bug" means a real defect, not assistant chatter - the
  bug share dropped from 79% to 38% and the three other kinds are no longer
  starved.
- Confidence values that mean something - grounded in the work, never 0.00 on a
  promoted fact.

Net: the same retrieval surface, carrying substantially less noise per token. The
token-efficiency consequences are quantified in Phase 3 (pack) and Phase 4 (MCP).
