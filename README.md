# Looma

**Looma turns coding-agent history into resumable project context.**

Instead of searching transcripts, Looma reconstructs:

- active work
- decisions
- blockers
- commits
- files in flight
- next likely steps

Local-first. No cloud. No API keys.

![Looma demo](docs/demo/demo.gif)

> Demo recorded against a synthetic sample project - see [docs/demo/](docs/demo/).

> Status: **v2.1.0** - the agent context layer. Works today on Claude Code, Codex,
> and Cursor history. Honest about what is real vs heuristic - see
> [Current Status](#current-status).

---

## Install

From source (standard-library Python 3.10+, zero third-party dependencies):

```bash
git clone https://github.com/devYRPauli/looma
cd looma
pip install -e .      # exposes the `looma` binary
looma doctor          # verify your environment
```

Prefer no install? Run `python3 -m looma <cmd>` from the repo.

## Demo

```bash
looma doctor              # check Python, FTS5, Claude history, git, data dir
looma ingest              # index your coding-agent history (auto-creates the DB)
looma                     # the daily driver: working on / changed / blocked / next
looma weekly              # the week across all repos: worked on / shipped / decisions
looma pack                # minimal token-budgeted context pack for another agent
looma inspect             # understand a repo: architecture / systems / ownership / risks
looma resume "auth"       # reconstruct context for a goal
looma explain "auth"      # why a WorkItem exists, how it evolved, what shaped it
looma ask "why postgres"  # search validated memory + work items
```

`looma` with no arguments runs the daily view (`today`) for the current repo,
then lists the other repos you touched recently, each with its next step - so a
context switch is one command.

`looma resume "auth"` returns the active auth work - the WorkItem, its constraints,
unfinished todos, affecting bugs, recent sessions, the linked commits and files,
and a next likely step - each with a confidence score. If Looma is not sure, it
says so and shows alternatives instead of guessing.

Looma has been exercised on hundreds of sessions across a dozen projects; see
[docs/demo/](docs/demo/) for the recorded demo.

## How it works

```
Claude Code history
      |
      v
Normalized Events     (vendor-agnostic turns; raw_json preserved)
      |
      v
WorkItems             (feature / bugfix / refactor / migration / investigation)
      |
      v
Candidate Memories    (decisions, todos, bugs, architecture notes - staged)
      |
      v
Confidence            (file overlap + commit linkage + multi-session/agent + time)
      |
      v
Resume Bundle         (WorkItem-first context, git-anchored, honest about certainty)
```

Everything runs on your machine over SQLite + FTS5. Commits and file paths come
from your repo (git is ground truth), never invented. Full design:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Extraction: zero-dependency default, optional local LLM

By default Looma extracts with a fast, deterministic, **standard-library** heuristic -
no model, no dependency, no setup. That is the default and the always-available
fallback.

If you run a local OpenAI-compatible model server (e.g. llama.cpp `llama-server` or
Ollama), Looma **auto-detects it and uses it** for much higher-quality extraction -
on the golden benchmark the local LLM scores **F1 0.96 vs the heuristic's 0.69**
(precision 1.00 vs 0.67). Nothing leaves your machine; it is a local HTTP call over
stdlib `urllib`, so the zero-dependency promise holds.

```bash
# optional: start any local model server, then just use looma normally
llama-server -m <qwen2.5-7b-instruct.gguf> --port 8080 -ngl 99
looma doctor          # shows "Local model server ... reachable - LLM extraction active"
looma ingest --once   # prints "Extraction: llm (local LLM detected)"
```

Control it explicitly with `LOOMA_EXTRACTOR=auto|heuristic|llm` (default `auto`) and
`LOOMA_LLM_URL` / `LOOMA_LLM_MODEL`. Compare them yourself: `looma benchmark --compare`.

## Current Status

**v2.0.0** - the agent context layer: `looma pack` (the smallest grounded preamble
for another agent, 2985x lighter than the raw transcript) and `looma inspect`
(understand a repo - architecture, systems, ownership, risks - without reading the
transcripts), on top of a sharper extractor (Untitled work 45%->13%, bug
overclassification 79%->38%, benchmark F1 0.69->0.90) and clean identities
(72->24 projects). See the [CHANGELOG](CHANGELOG.md) for the full history.

Built on **v1.6.0** - the daily loop: `looma today` (bare `looma`) and
`looma weekly` - and the v1.5 refinement.

### Works today

- **Multi-agent ingestion**: Claude Code, Codex, and Cursor (idempotent); sessions
  from different agents on the same repo merge into the same WorkItems
- WorkItem extraction and resolution (agglomerative file-overlap merging)
- Confidence scoring, surfaced everywhere
- Git-anchored context reconstruction (validated commits, branches, files)
- WorkItem-first resume bundles with explicit uncertainty handling
- **Hybrid retrieval**: graph + FTS5 + optional semantic vectors (sqlite-vec)
- Optional fully-local LLM extractor, **auto-detected** when a local model server is
  running (F1 0.96 vs 0.69 on the benchmark); the stdlib heuristic stays the
  zero-dependency default and fallback
- Evaluation: `looma benchmark [--compare|--retrieval]` (P/R/F1, retrieval recall)
- Human corrections: `looma correct merge|split|rename|promote|reject|false-positive|undo`
  (ledgered, replayable, override automated inference)
- **Daily driver**: `looma` / `looma today` (working on / changed / blocked / next,
  plus the other repos you touched recently) and `looma weekly` (the week across
  all repos: worked on, shipped, decisions, blockers)
- **Brief**: `looma brief` (60-second project orientation: active work, decisions,
  risks, blockers, recent commits, suggested next work)
- **Timeline**: `looma timeline` (feature evolution over time)
- **Explain**: `looma explain <work>` (why a WorkItem exists, how it evolved, which
  decisions shaped it, what changed)
- **Context pack**: `looma pack` (the minimal, token-budgeted, confidence-aware
  context package to prepend to a fresh agent session; 2985x lighter than the raw
  transcript and bounded under ~900 tokens for any repo)
- **Repository intelligence**: `looma inspect` (architecture, active systems,
  ownership clusters, risks, change hotspots - understand a repo without reading
  its transcripts)
- **MCP server**: `looma mcp` (any agent can consume Looma context, local stdio;
  tools: today, weekly, resume_work, brief, pack, inspect, ask, timeline, explain,
  list_work, recall). Hands an agent a grounded orientation in a rounding-error
  fraction of the raw-transcript tokens.
- **Watcher daemon**: `looma daemon` (stays current automatically)
- Graph health with degradation warnings: `looma status --health`
- CLI: `today` (bare `looma`), `weekly`, `ingest`, `brief`, `pack`, `inspect`,
  `resume`, `ask`, `explain`, `timeline`, `work`, `status`, `doctor`, `reset`,
  `benchmark`, `correct`, `reprocess`, `mcp`, `daemon`, `init`

### Planned (not yet built)

- More adapters: Gemini, Windsurf, OpenCode
- Route WorkItem titles through the LLM extractor (currently candidate memories only)
- Local UI

Extraction is heuristic by default and will let the occasional noisy item through;
confidence + promotion down-rank it, and the auto-detected local LLM extractor is the
precision upgrade. The full design is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Why not just search transcripts

Transcript search answers "which message mentioned Redis?". Looma answers "what is
the active work, what did we decide and why, which commits implement it, and what is
still unfinished?" - by organizing history around **WorkItems** and anchoring them
to git, not by ranking chat lines.

## Local-first / privacy

Looma runs entirely on your machine. Your transcript contents never leave the
device: no hosted API, no cloud, no API key, no telemetry. The store is **derived
data that can still contain snippets of your transcripts - do not commit it.** A
`.gitignore` is included (`*.db`, `.looma/`). `looma reset --confirm` deletes only
Looma's store, never your Claude transcripts.

## Project layout

```
looma/        package: adapters, storage, extraction, resolution, promotion, retrieval, cli
tests/        unit tests (run: python3 -m unittest discover -s tests -t .)
docs/         launch assets (screenshots, demo)
```

## Contributing

Feedback, bug reports, and small PRs welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). (c) 2026 devYRPauli.
