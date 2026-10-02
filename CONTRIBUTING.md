# Contributing to Looma

Looma is an early alpha. The most useful contributions right now are bug reports,
honest feedback on whether the resume bundles are actually useful on your own
history, and small, focused PRs. Thanks for taking a look.

## Ground rules

- **Local-first, always.** No hosted APIs, no cloud calls, no API keys, no
  telemetry. Anything that would send transcript content off the machine is out of
  scope.
- **Standard library only** for the core. The project currently has zero
  third-party runtime dependencies; keep it that way unless there is a strong,
  discussed reason.
- **Git is ground truth.** Commits, branches, and file paths come from the repo and
  are validated - never invented by a heuristic or a model.
- **Be honest in output and docs.** Surface confidence and uncertainty; do not
  present heuristic guesses as certainties. Match the existing positioning (see
  below).

## Positioning / language

Looma is **resumable project context** / **work reconstruction** /
**git-anchored project understanding** / **local-first project memory**.

Please avoid the framings we deliberately do not use: "AI memory", "second brain",
"RAG". They over-promise and misdescribe what Looma does.

## Dev setup

```bash
git clone https://github.com/devYRPauli/looma
cd looma
pip install -e .
looma doctor
python3 -m unittest discover -s tests -t .   # 44 tests, must stay green
```

No build step; it is pure Python. `looma reprocess` rebuilds the graph from stored
events if you change extraction/resolution/promotion logic.

## Development reports

Keep benchmark results, development reports, and release-readiness notes in the
local-only `reports/` directory or a temporary directory. These files can contain
project names, paths, and transcript-derived data; do not commit them.

The repository ignores `reports/`, `*_REPORT.md`, `*_READINESS.md`, and
`*_EVALUATION.md`. Keep the root's public documentation limited to README,
CHANGELOG, CONTRIBUTING, ARCHITECTURE, and LICENSE. Before a release, check the
tracked files for internal reports; ignore rules do not protect files already
tracked or added with `git add -f`.

## Project layout

```
looma/adapters/      source adapters (Claude Code today)
looma/storage/       SQLite system of record + VectorStore interface (stubbed)
looma/extraction/    deterministic + heuristic candidate extraction
looma/resolution/    WorkItem clustering
looma/promotion/     CandidateMemory -> ValidatedMemory rules
looma/retrieval/     resume, ask, matching
looma/confidence.py  the v3 confidence formula
```

The full design and the rationale behind every layer is in
[ARCHITECTURE.md](ARCHITECTURE.md). Read it before a non-trivial PR.

## Pull requests

- Keep PRs small and focused; one concern per PR.
- Add or update tests for behavior changes; run the full suite.
- Match the surrounding style (it is plain, explicit Python).
- Describe what you verified, with real output where relevant.

## Good first issues

- New deterministic candidate/intent heuristics that reduce false positives
  (with tests proving before/after on a sample).
- Adapter scaffolding for a second agent behind the existing `SourceAdapter`
  interface (discussion first - this is on the roadmap and needs format grounding).
- Doctor checks, error messages, and first-run UX polish.

## Reporting bugs

Open an issue with: what you ran, what you expected, what happened, and your
`looma doctor` output (it contains no transcript content). Redact anything sensitive.
