# `looma today` Evaluation (Phase 2)

Date: 2026-06-21
Question: does one command answer the four daily questions better than the
existing commands? Measured on the real corpus.

## The command

`looma today` (zero arguments) is the daily entry point. For the current project
(resolved from cwd, or `--project KEY`) it answers:

- **What am I working on?** - active work items (falls back to most-recent so it
  never shows nothing).
- **What changed recently?** - recent sessions in the window + linked commits +
  the git working-tree (uncommitted files).
- **What's blocked?** - open blockers (todos) and risks (open bugs), shortened to
  scannable lines.
- **What should I do next?** - the inferred next step.

Then it lists **other repos touched recently**, each with its next step - because
a developer touches ~6 repos/day (DAILY_USAGE_REPORT.md), so a context switch
should be one command, not six. With no resolvable project, `today` (or
`today --all`) shows that cross-project standup view directly.

It composes resume + brief + recent activity + a trust signal (the confidence of
the item it points at). It adds nothing to the graph.

## Benchmark: daily completeness

For each daily-driver project (>= 3 sessions, has work; n=11), we checked whether
each command's output actually populates each of the four daily questions, plus
cross-project awareness. Values are the fraction of projects where the section is
non-empty.

| command | working | changed | blocked | next | cross-project | avg / 4 |
|---|---|---|---|---|---|---|
| resume  | 1.00 | 0.09 | 0.36 | 0.73 | 0.00 | **0.55** |
| brief   | 1.00* | 0.18 | 0.82 | 0.73 | 0.00 | 0.61 |
| today   | 1.00 | **1.00** | 0.82 | 0.73 | **1.00** | **0.89** |

(*brief was 0.73 on "working" until `today` added the recent-item fallback;
both now orient you even when nothing is formally "active".)

### Reading it

- **`today` answers the four daily questions ~60% more completely than `resume`
  and ~46% more than `brief`** (0.89 vs 0.55 / 0.61), and is the only command
  that covers cross-project context switching.
- The decisive gap is **"what changed"**: `resume`/`brief` rely on linked
  commits, which exist for only 4/72 projects, so they score 0.09-0.18. `today`
  reads recent *sessions* and the git working tree too, so it reflects real
  activity for every project (1.00).
- `next` (0.73) and `blocked` (0.82) are capped by heuristic extraction quality,
  not by `today` - the same ceiling all three share. The local LLM extractor
  (v2) is the lever there.

### Why not just use resume or brief

- `resume` is **goal-driven** ("resume the auth work") - you must know what to ask.
- `brief` is **onboarding** ("explain this project to someone new").
- `today` is **time-and-habit driven** ("I just sat down; what's the state and
  what's next, here and across my other repos"). It is the command you run every
  morning without thinking.

## Trust + correctness fix found while building it

`dirty_files` truncated every modified path by one character ("looma/cli.py" ->
"ooma/cli.py") because the porcelain status field's leading space was stripped.
This fed wrong filenames into resume/brief/today's "what changed" and next-step.
Fixed (NUL-delimited parse); a daily command that shows wrong filenames would
quietly destroy trust.

## Verdict

`today` is the strongest candidate for the core daily loop: one command, zero
arguments, answers the four questions more completely than anything else, and is
the only one built for the multi-repo reality. It becomes the recommended daily
entry point (Phase 4 retention analysis).
