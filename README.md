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

> Status: **alpha** (v0.1.0a1). Works today on Claude Code history. Honest about
> what is real vs heuristic - see [Current Status](#current-status) and
> [RELEASE_ALPHA.md](RELEASE_ALPHA.md).

---

## Install

Alpha, from source (standard-library Python 3.10+, zero third-party dependencies):

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
looma ingest --once       # index your Claude Code history (or: --once --limit 25)
looma work                # list WorkItems for the current repo, with confidence
looma resume "auth"       # reconstruct context for a goal
```

`looma resume "auth"` returns the active auth work - the WorkItem, its constraints,
unfinished todos, affecting bugs, recent sessions, the linked commits and files,
and a next likely step - each with a confidence score. If Looma is not sure, it
says so and shows alternatives instead of guessing.

See [SAMPLE_OUTPUT.md](SAMPLE_OUTPUT.md) for representative output (Looma has been
exercised on hundreds of sessions across a dozen projects), and
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

**Alpha.**

### Works today

- Claude Code history ingestion (idempotent)
- WorkItem extraction and resolution
- Candidate-memory extraction (heuristic)
- Confidence scoring, surfaced everywhere
- Git-anchored context reconstruction (validated commits, branches, files)
- WorkItem-first resume bundles with explicit uncertainty handling
- Local SQLite storage + FTS5 lexical retrieval
- Evaluation: `looma benchmark [--compare]` (precision/recall/F1 on a golden set)
- Optional fully-local LLM extractor, **auto-detected** when a local model server is
  running (F1 0.96 vs 0.69 on the benchmark); the stdlib heuristic stays the
  zero-dependency default and fallback
- Human corrections: `looma correct merge|split|rename|promote|reject|false-positive|undo`
  (ledgered, replayable, override automated inference)
- Graph health: `looma status --health`
- CLI: `init`, `ingest`, `work`, `resume`, `ask`, `status`, `doctor`, `reset`,
  `benchmark`, `correct`, `reprocess`

### Planned (not yet built)

- Cross-agent support: Codex, Cursor, Gemini, Windsurf, OpenCode
- MCP integration (inject resume bundles into any agent)
- Timeline views (feature evolution over time)
- Semantic retrieval (sqlite-vec behind the existing VectorStore interface)
- Local UI

The line between these is deliberate: today's extraction is heuristic and will let
the occasional noisy item through. Confidence and promotion down-rank it, and the
planned local-LLM engine is the precision upgrade. Details in
[IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md).

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

Early alpha - feedback, bug reports, and small PRs welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). (c) 2026 devYRPauli.
