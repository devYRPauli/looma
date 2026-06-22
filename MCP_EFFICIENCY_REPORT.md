# MCP Efficiency - V2 Phase 4 Report

Goal: make Looma context cheap enough to call on every turn, and make what comes
back actually relevant. Evaluated all consuming tools - resume_work, ask, brief,
timeline, explain, pack - on token cost, retrieval quality, and answer quality,
against the real corpus (24 projects). MCP is pure stdlib JSON-RPC over stdio; no
network, no dependency.

---

## 1. Token cost per tool

Output tokens (est. 4 chars/token) across five representative projects, smallest
to largest, after the Phase 4 optimizations:

| Tool      | looma | mddocs | shb_database | Lab-Agents | world-cup | avg | max  |
|-----------|-------|--------|--------------|------------|-----------|-----|------|
| ask       | 0*    | 9      | 0*           | 173        | 30        | 42  | 173  |
| resume    | 88    | 129    | 23           | 273        | 369       | 176 | 369  |
| timeline  | 101   | 51     | 47           | 253        | 579       | 206 | 579  |
| explain   | 165   | 88     | 79           | 478        | 742       | 310 | 742  |
| pack      | 137   | 185    | 27           | 737        | 405       | 298 | 737  |
| today     | 240   | 212    | 186          | 517        | 415       | 314 | 517  |
| brief     | 149   | 154    | 86           | 896        | 430       | 343 | 896  |

\* 0 = the query term has no memory in *that* project (the matching memories live
in another project); not a retrieval failure - see section 2.

Every tool is well under 1,000 tokens even on the largest project, and the common
ones (pack, resume, today, ask) average 40-314. For comparison, the raw
transcript for these projects totals ~10.7M tokens (Phase 3). An agent can call a
Looma tool on every turn for a rounding-error cost.

---

## 2. Retrieval quality - the `ask` fix

`ask` was effectively broken on the real corpus: it returned **zero** results for
obviously-relevant queries.

| Query (project)        | Before | After |
|------------------------|--------|-------|
| extraction (cross-proj)| 0      | hits  |
| migration (shb)        | 0      | 4     |
| database (shb)         | 0      | 2     |
| genotype (Lab-Agents)  | 0      | hits  |
| picks (world-cup)      | 0      | 1     |

**Cause.** `fts_query` emitted exact-token phrase matches (`"extraction"`), so a
query noun never matched its verb/participle form in the stored memory
("extracting", "extracted"). On a corpus of natural-language memories that is most
queries.

**Fix.** `fts_query` now applies light suffix stripping and FTS5 prefix matching:
`extraction -> extrac*`, `migration -> migr*`, `confidence -> confid*`,
`picks -> pick*`. Tokens shorter than a 4-char stem stay exact, so precision is
preserved for short/code-like terms. Semantic (vector) retrieval still runs first
when a vector store is present; this fixes the lexical floor that everything falls
back to.

---

## 3. Answer-quality fixes

- **JSON / tool-definition noise.** Promoted memories included raw fragments like
  `"description": "Reconstruct ..."` ingested from source and tool schemas.
  `sanitize.looks_like_code` now recognizes JSON-key fragments, so they are
  dropped at extraction and filtered at display. After a rebuild, JSON-key noise
  entities fell to 2 of 711.

- **Bounded detail tools.** `explain` and `timeline` listed every event /
  decision / bug unbounded - 1,029 and 703 tokens on the busiest work item.
  They now cap (evolution 12, decisions 8, bugs 6, todos 8; timeline last 20
  events) with an "... +N earlier" marker, so cost stays bounded as a work item
  accumulates history. explain max 1029 -> 742, timeline 703 -> 579.

---

## 4. The tool ladder

The tools form a cost/scope ladder a consuming agent can climb only as needed:

| Need                                   | Tool       | Typical tokens |
|----------------------------------------|------------|----------------|
| "what is this project, minimally"      | pack       | ~140-740       |
| "search a fact"                        | ask        | ~40            |
| "resume a specific thread"             | resume     | ~90-370        |
| "today's orientation across repos"     | today      | ~200-520       |
| "full 60-second brief"                 | brief      | ~150-900       |
| "why does this work item exist"        | explain    | ~80-740        |
| "how did this evolve"                  | timeline   | ~50-580        |

`pack` is the floor: the cheapest grounded preamble, designed to be prepended to
every session. The rest are pulled deliberately.

---

## 5. Result

- Retrieval recall restored: `ask` went from 0 hits on inflected queries to
  matching across the corpus.
- Every tool bounded under ~900 tokens; the constantly-used ones under ~350.
- Noise (JSON/tool-schema fragments) removed from answers.

Looma context is now cheap enough and clean enough to be a default, per-turn part
of an agent's loop rather than a special-case lookup.

Tests: `tests/test_retrieval_stemming.py` (stemmed fts_query + inflected-query
recall). Full suite 101 passed, 1 skipped.
