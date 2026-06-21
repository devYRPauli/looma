# Looma Retention Report (Phase 4)

Date: 2026-06-21
Question: which commands create value, which are rarely useful, which overlap -
and what should be emphasized, deprecated, or merged so Looma earns a daily habit.

## 1. The command surface (18 commands)

Grouped by how they fit the loop, with a value read grounded in the daily-usage
analysis (DAILY_USAGE_REPORT.md) and the daily-completeness benchmark
(TODAY_EVALUATION.md).

| Command | Role | Run frequency | Value | Verdict |
|---|---|---|---|---|
| `today` | daily driver (now bare `looma`) | every session | **Highest** (0.89/4, cross-project) | **Emphasize** |
| `ask` | targeted recall ("what did we decide about X") | as-needed, frequent | High, distinct | Emphasize |
| `weekly` | retrospective | weekly | High, distinct time-scale | Emphasize |
| `resume` | goal-driven context reload | frequent | High when you know the goal | Keep |
| `explain` | one effort's story (why/how/decisions/changes) | occasional | High, distinct depth | Keep |
| `brief` | onboarding snapshot of a project | occasional | Overlaps `today` heavily | De-emphasize |
| `work` | raw WorkItem list | occasional | Low (no synthesis; subsumed) | **Deprecate** |
| `timeline` | raw event chronology of one item | rare | Subsumed by `explain` | **Merge -> explain** |
| `status` | store + project + health | diagnostic | Medium (trust/diagnostic) | Keep |
| `correct` | human corrections to the graph | rare but high-trust | Medium (trust) | Keep |
| `doctor` | environment check | install-time | Medium (time-to-value) | Keep |
| `ingest` | index history | setup + via daemon | High (enabling) | Keep |
| `daemon` | stay current automatically | set-and-forget | High (enabling) | Keep |
| `mcp` | agent integration | per-agent | High (distinct audience) | Keep |
| `init` | create store | once | Necessary | Keep |
| `reprocess` | full rebuild after upgrades | rare | Maintenance | Keep |
| `reset` | delete store | rare | Maintenance | Keep |
| `benchmark` | dev/eval harness | dev-only | Internal | Keep (internal) |

## 2. Which command creates the most value

**`today`.** It answers the four daily questions most completely (0.89/4 vs
`resume` 0.55, `brief` 0.61), is the only one that reflects "what changed" for
every project (it reads sessions + working tree, not just the sparse commit
links), and is the only one built for the multi-repo reality (~6 repos/day). It
is now the bare-`looma` default, so the habit costs zero keystrokes beyond the
binary name. This is the retention anchor.

Runners-up by distinct value: `ask` (targeted recall), `weekly` (the retro),
`explain` (the deep-dive). Each answers a question no other command does.

## 3. Which commands are rarely useful

- **`work`** - a raw WorkItem list with no synthesis. Everything it shows,
  `today` and `brief` show with context. Its only edge is `--status` filtering,
  which is a power-user need, not a daily one.
- **`timeline`** - a flat event list for one item. `explain` already builds on
  `timeline.build` and wraps it in the why/how/decisions/changes narrative, so
  `timeline` is the same data with less meaning.
- **`brief`** - still useful for "explain this project to someone new", but for a
  solo developer on their own repos that moment is rare; `today` covers the daily
  need and reuses `brief`'s builder under the hood.

## 4. Where commands overlap

The "current state of my project" question is answered by four commands that
share `brief.build`/`resume`/`project_work_items`:

```
work     -> raw list of WorkItems
brief    -> list + decisions/risks/blockers/commits/next   (onboarding framing)
resume   -> one WorkItem's full bundle                      (goal framing)
today    -> brief + recency + working-tree + cross-project  (daily framing)
```

`today` is the superset for daily use. `resume` keeps a distinct goal-driven
entry; `work` and `brief` are the redundant middle.

The "history of one effort" question overlaps too: `timeline` (raw) ⊂ `explain`
(narrative). One command, two depths.

## 5. Recommendations

### Emphasize (the loop)
- **`today`** as the front door (done: bare `looma` runs it). All docs and
  onboarding should lead here.
- **`ask`**, **`weekly`**, **`explain`** as the three distinct supporting
  questions (recall, retro, deep-dive).

### De-emphasize
- **`brief`** - keep for onboarding, but stop presenting it as a primary daily
  command; `today` is the daily view.

### Deprecate (documentation, not removal yet)
- **`work`** - fold its only unique feature (status filter) into `today
  --status` in v2, then retire the standalone command. Until then, drop it from
  the headline command list.

### Merge
- **`timeline` -> `explain`** - they are the same data at two depths. Recommend
  `explain --timeline` for the raw view in v2 and retiring the standalone
  `timeline`. (Both already share `timeline.build`, so the merge is low-risk.)

### Net effect
Headline surface drops from "18 commands" to a clear loop:

```
daily:    looma            (today)
recall:   looma ask "..."
weekly:   looma weekly
deep:     looma explain <work>
setup:    looma doctor / ingest / daemon
```

Five things to remember instead of eighteen. Fewer decisions per session is the
single biggest retention lever after making the daily command frictionless.

## 6. What changed this phase

- Bare `looma` now runs `today` (zero-friction daily habit).
- This report; no commands removed yet (deprecations are documentation-first to
  avoid breaking anyone's muscle memory before v2).
