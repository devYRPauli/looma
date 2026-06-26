# Changelog

All notable changes to Looma are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses pre-1.0 alpha
versions.

## [2.1.4] - 2026-06-26

### Fixed
- `looma weekly` / `looma today` no longer surface two classes of meta noise in
  DECISIONS:
  - Dangling preambles that announce a memory but carry no content ("Here's
    where we are and the decision:"). The meta guard's trailing-colon rule was a
    fixed word-list ("... is:", "... done:"); it is now the general structural
    case - any line ending in "<word>:" is a preamble, not a self-contained
    memory. Lines with a colon mid-sentence ("Decision: use gRPC over REST")
    are unaffected.
  - Injected agent-memory-rule boilerplate that bleeds into turns untagged
    ("... must come from a tool result this turn, never from memory ..."), which
    `strip_injected` misses and the architecture classifier read as a design
    rule. Added its signature to the noise-phrase filter.
- Both fixes route through the shared guards, so they apply to the heuristic
  extractor, the `looma weekly` / `today` display layer, and the optional
  local-LLM path alike. Run `looma reprocess` to apply them to an existing graph.

## [2.1.3] - 2026-06-25

### Changed
- The optional local-LLM extractor now inherits the same output guards as the
  heuristic. Model-produced memories run through a shared rejection predicate
  (transcript/agent meta, code, action/progress narration, low-value lines),
  are ASCII-folded, and de-duped - so the LLM cannot reintroduce the noise the
  heuristic filters. The extraction prompt was also sharpened (ignore action
  narration and finished work; a bug is a concrete observed wrong behavior).
- Local model detection is now honest: a reachable server with no model loaded
  (an idle `llama-server`, or a shim returning an empty model list) no longer
  counts as available, so `auto` mode does not select a dead endpoint and fall
  back on every call.



### Changed
- Sharper `weekly` and `today` retrospectives. The decision/blocker extraction
  now rejects transcript structure and agent meta that previously leaked through:
  role-prefixed turns ("[346] assistant: ..."), ascii arrow/graph diagrams,
  quoted-vocabulary listings (Looma's own pattern source, ingested from its dev
  sessions), dangling preambles that trail off after a colon, greetings, and the
  assistant's own action narration ("Let me check ...", "I'm adding ..."). A
  blocker that narrates finished work ("... done:", "checks passed") no longer
  reads as unresolved.
- Heuristic extraction now folds smart quotes to ASCII before matching, so a
  curly apostrophe no longer silently defeats the apostrophe-bearing patterns
  (I'm / can't / let's / won't).

## [2.1.1] - 2026-06-23

### Added
- A recorded demo (`docs/demo/demo.gif`) and static stills, embedded in the
  README. Recorded against a fully synthetic sample project - no real history is
  shown - and reproducible via `docs/demo/gen_demo.py` + `docs/demo/demo.tape`.

### Changed
- Project identity recovery now also reads the working directory of shell tool
  calls (for example Codex's `workdir`), so sessions started from an ephemeral
  directory but doing their work in a real checkout resolve to that project.

## [2.1.0] - 2026-06-23

Extraction and identity accuracy, driven by testing the v2.0.0 release as a fresh
install against a real 639-session, 79k-message, three-agent corpus.

### Fixed
- Tool results are no longer mistaken for the user. Claude Code delivers tool
  results as `role: user` turns (in one project, 201 of them versus 35 real human
  messages); they were polluting work-item titles, decision extraction, and `ask`.
  They are now retagged so extraction reads what the human actually said, while the
  text stays stored and searchable.
- `ask` now returns the relevant memory or work item for concrete queries that
  previously came back empty - it was being starved by the noise above, not
  fundamentally broken.

### Changed
- Project identity recovery for sessions run from an ephemeral working directory
  (for example a shell started in `/tmp`): the project is recovered from the files
  the session actually touched. Programmatic / automated sessions (memory
  summarizers, review bots, extraction calls) are bucketed as `Automated (<source>)`
  separately from genuinely unresolved work, so `Unsorted` reflects real work.
  Across the test corpus, unresolved real sessions dropped from 383 to 32.
- Cursor workspace recovery now reads the files a session edited
  (`originalFileStates`, newly created files) when no workspace is recorded,
  resolving far more Cursor sessions to their real project.
- Work-item titles capture more real intent: broadened the set of recognized
  action verbs, with guards so pasted shell commands and flags never become a
  title.
- Automated-session detection now also catches security-review bots, memory and
  subagent prompts, and usage reports.
- Confidence band labels recalibrated to the score distribution the model actually
  produces, so the label distinguishes thin single-session items from corroborated
  ones instead of marking nearly everything "low". Scores, promotion, and ranking
  are unchanged.
- Duplicate projects are reconciled: a remote-less local checkout folds into the
  canonical project sharing its repository name, when the match is unambiguous.
- Trimmed low-value extracted memories (bare acknowledgements and filler).

## [2.0.0] - 2026-06-22

The agent context layer. Make Looma the default, grounded context source for
coding agents. Driven by a seven-phase evaluation on a 615-session, three-agent
real corpus.

### Added
- `looma pack` (CLI + MCP): the minimal, token-budgeted, confidence-aware context
  package for another agent - active work, decisions, blockers, relevant files,
  recent changes. 2985x lighter than the raw transcript; bounded under ~900
  tokens for any repo.
- `looma inspect` (CLI + MCP): understand a repository without reading its
  transcripts - architecture summary, active systems, ownership clusters, risks,
  change hotspots.

### Changed
- Extraction quality: filter synthetic/programmatic sessions (48% of the corpus)
  from WorkItem/memory generation; tighten bug classification to require an
  explicit problem assertion; broaden decision/architecture recall; normalize
  WorkItem-naming verb stems and clause trimming; calibrate memory confidence to
  inherit WorkItem grounding. Real corpus: Untitled work 45%->13%, bug share of
  memories 79%->38%, named work items 34%->56%. Benchmark memory F1 0.69->0.90,
  architecture 0.00->1.00, work label-hit 0.60->1.00.
- Identity hygiene: reject ephemeral/degenerate roots (temp dirs, fs root, config
  homes, home dir); collapse unresolvable sessions into one per-agent unsorted
  bucket instead of one project per session; recover Cursor workspaces from
  attached-file URIs. Projects 72->24; unknown:<uuid> singletons 46->0; junk
  projects 5->0.
- MCP efficiency: `ask` retrieval fixed - stemmed/prefix FTS so inflected queries
  match (was returning 0 hits); `explain`/`timeline` output bounded; JSON/
  tool-schema fragments dropped from answers. Every MCP tool under ~900 tokens;
  per-turn tools average 40-350.

### Validated
- Cross-agent: the one cross-agent work item scores 4.6x the single-agent
  confidence average (0.44 vs 0.095) - merge mechanism confirmed; extraction
  robust across Claude/Codex/Cursor; 0 manual corrections needed.

## [1.6.0] - 2026-06-21

The daily loop. Driven by a usage analysis of the real corpus.

### Added
- **`looma today`** (and bare `looma`) - the daily driver: what you're working on,
  what changed recently (sessions + commits + working tree), what's blocked, what
  to do next, plus the other repos you touched recently with their next step.
  Benchmarked at 0.89/4 daily completeness vs resume 0.55 / brief 0.61.
- **`looma weekly`** - cross-project retrospective: worked on, shipped (commits),
  decisions, unresolved blockers. Understand your week in under 2 minutes.
- MCP `today` and `weekly` tools (9 tools total).

### Changed
- **Bare `looma` runs `today`** - the daily habit costs zero keystrokes beyond
  the binary name.
- **`init` is now optional** - every command auto-creates and migrates the store,
  so a brand-new machine works without it.
- `doctor` ends with a concrete next step; the empty daily view shows a quickstart;
  first ingest prints a progress hint with a `--limit` fast-path.
- MCP tool output is ASCII-folded centrally (no transcript emoji/smart-quotes in
  another agent's context).

### Fixed
- `dirty_files` truncated every modified path by one character ("looma/cli.py" ->
  "ooma/cli.py") because the porcelain status field's leading space was stripped.
  Fixed with a NUL-delimited parse. This corrupted "what changed" everywhere.
- `looks_like_code` now also drops diff hunks (@@) and comment/heading lines.

## [1.5.0] - 2026-06-21

Refinement cycle: make Looma feel indispensable. Driven by a real-corpus
evaluation.

### Added
- **`looma brief`** - 60-second project orientation (summary, active work, recent
  decisions, current risks, open blockers, recent commits, suggested next work).
- **`looma explain <work>`** - why a WorkItem exists, how it evolved, which
  decisions shaped it, what changed. MCP `brief` + `explain` tools too.

### Changed
- **Resume gates on match relevance**, not intrinsic confidence (solo-dev work no
  longer goes COLD on a perfect match). Matching now scores file paths + linked
  memories; real next-step inference; no-goal resume picks the most resumable
  item. goal-match MRR 0.67 -> 1.00; COLD-on-true-match 4/6 -> 0/6; next-step
  real-rate 0.54 -> 0.72.
- **WorkItem titles**: intent extraction rejects code/SQL/JSX; "Work on <salient
  segment>" replaces "Work in <dir>/". Garbage titles 15 -> 0; dir titles 80+ -> 0.
- **Lifecycle**: a single substantive session is now `active`. Active rate
  11% -> 46%.
- **Incremental rebuild**: daemon re-derives only changed repos (one-project
  rebuild 3.1s vs ~85s full, ~27x).
- **`ask`** ranks by coverage-weighted relevance blended with confidence.
- Code/diff-line memories filtered from resume/brief/explain; non-ASCII folded.

### Fixed
- Crash in `deterministic.session_artifacts` when `project_root` is None
  (rootless/unknown projects) - the real cause of the original ingest rc=1. Full
  corpus now rebuilds cleanly (277 -> 325 work items).

## [1.0.0] - 2026-06-21

First "complete v1" milestone for solo developers.

### Added (v1 milestone)
- **Multi-agent ingestion**: Codex + Cursor adapters alongside Claude Code; sessions
  merge across agents on the same repo. Per-source ingest reporting.
- **Hybrid retrieval**: optional sqlite-vec semantic vectors fused with FTS + graph
  (FTS stays the zero-dependency default). Retrieval recall@3 0.62 -> 1.00.
- **Resolution fix**: agglomerative file-overlap merge - unresolved RELATED 158 -> 3,
  work items 316 -> 159 on real data.
- `looma timeline` (WorkItem evolution), `looma mcp` (stdlib MCP server),
  `looma daemon` (auto-stay-current), `looma status --health` degradation warnings,
  `looma benchmark --retrieval`.
- Forward-compatible column migrations for pre-v1 databases.

### Earlier in this line: extraction quality, trust, and evaluation

### Added
- **Auto-detected local LLM extraction.** `LOOMA_EXTRACTOR=auto` (now the default)
  detects a reachable local model server and uses the LLM extractor, else falls back
  to the stdlib heuristic - so the LLM is the best-supported path when available while
  the zero-dependency default is preserved (local HTTP over stdlib urllib, no new dep).
  `looma doctor` reports the model server; `ingest`/`reprocess` report which extractor ran.
- **Evaluation system.** `looma benchmark` and `looma benchmark --compare` report
  precision/recall/F1 for extraction over a golden fixture set.
- **Extractor interface** with `HeuristicExtractor` (default) and a fully-local
  `LocalLLMExtractor` (llama.cpp/Ollama, no hosted API), opt-in via `LOOMA_EXTRACTOR=llm`,
  with per-session fallback to the heuristic.
- **Human Correction Layer:** `looma correct merge|split|rename|promote|reject|
  false-positive|undo|log`. Corrections are ledgered, replayable, override automated
  inference, and survive deterministic rebuilds (anchored to stable keys).
- **Graph health metrics:** `looma status --health` (conversion rate, merge rate,
  false-positive rate, avg work item size, orphan candidates, unresolved related items).

### Benchmark
- Local LLM extractor (Qwen2.5-7B Q3_K_M) beats the heuristic on the golden set:
  **F1 0.96 vs 0.69** (precision 1.00 vs 0.67), so it is kept as an opt-in upgrade.

### Tests
- 53 tests (was 44): benchmark scoring, corrections (merge/rename/reject/undo with
  replay), health metrics.

## [0.1.0-alpha.1] - 2026-06-21

First public alpha.

### What Looma does

Looma turns coding-agent history into **resumable project context**. Instead of
searching transcripts, it reconstructs the active work, decisions, blockers,
commits, files in flight, and next likely steps - organized around WorkItems and
anchored to git. Local-first: no cloud, no API keys, no telemetry.

### Added (works today)

- Claude Code history ingestion (`~/.claude/projects/*.jsonl`), idempotent on a
  content hash, defensive against malformed lines.
- Project identity resolution (git remote -> git root -> path) as a cross-agent key.
- Deterministic extraction of files, commits (git-validated), and commands.
- WorkItem generation and multi-signal resolution (feature / bugfix / refactor /
  migration / investigation).
- Heuristic candidate-memory extraction (decisions, todos, bugs, architecture).
- Confidence scoring (file overlap, commit linkage, multi-session, multi-agent,
  temporal persistence), surfaced in every command.
- Promotion of corroborated candidates into validated, graph-linked memory.
- WorkItem-first `resume` with explicit uncertainty handling (confident /
  ambiguous / low-confidence), never silently collapsing weak matches.
- CLI: `init`, `ingest` (`--once`, `--limit N`, `--project <path>`), `work`,
  `resume`, `ask`, `status`, `doctor`, `reset --confirm`, `reprocess`, `--verbose`.
- Local SQLite storage with FTS5 lexical retrieval; persistent git-SHA cache.
- 44 unit tests.

### Known limitations

- **Claude Code only.** Other agents are designed but not yet implemented.
- **Extraction is heuristic.** WorkItem titles and candidate memories use regex/
  cue-word heuristics; expect occasional generic titles or false-positive "bugs".
  Confidence + promotion down-rank them; a local-LLM extractor is the next milestone.
- **No semantic search yet.** Retrieval is FTS5 + graph; the `VectorStore` is stubbed.
- **No UI, no MCP server, single local store.**
- First full ingest of a large history is git-bound (cached; `--limit` for demos).

### Roadmap

Local-LLM extraction engine, benchmark framework, cross-agent adapters (Codex,
Cursor, Gemini, Windsurf, OpenCode), MCP integration, timeline views, human
correction workflows, graph health metrics, semantic retrieval, local UI.

[0.1.0-alpha.1]: https://github.com/devYRPauli/looma/releases/tag/v0.1.0-alpha.1
