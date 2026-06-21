# Looma First-Run Report (Phase 5)

Date: 2026-06-21
Question: how long from install to first useful answer, and where is the friction?

## 1. The path

```
pip install looma        # zero runtime dependencies -> seconds
looma ingest             # index your coding-agent history  (auto-creates the DB)
looma                    # the daily view (today)           ~0.2s
```

`looma init` is no longer required - any command auto-creates and migrates the
store. The critical path is two commands: ingest, then `looma`.

## 2. Measured time-to-value

| Step | Time | Notes |
|---|---|---|
| install (`pip install -e .`) | seconds | no third-party runtime deps |
| `looma init` | -- | now optional (auto-migrate) |
| `looma ingest --limit 25` (quick start) | ~10s | instant taste: 25 sessions, value immediately |
| `looma resume` / `looma` (first answer) | ~0.2s | snappy |
| **install -> first useful answer (quick path)** | **~10-15s** | |
| `looma ingest` (full, large history) | minutes | 73k-message corpus; new users have far less |

The quick path (`--limit 25`) gets a developer to a real resume/today answer in
about ten seconds. The full ingest is the only slow step and scales with history
size; the command now sets expectations up front (see below).

## 3. Friction found and fixed this phase

1. **Fresh-DB crash (worst offender).** Any command run before `looma init`
   crashed with a raw `no such table` traceback - a terrible first impression.
   Fixed: `_open_store` now always migrates (idempotent, cheap), so `looma`,
   `looma ingest`, `looma resume` all just work on a brand-new machine. `init`
   becomes optional.
2. **Unknown next step.** `looma doctor` now ends with a concrete action -
   "`looma ingest` to index your history, then `looma`" when empty, or "`looma`
   for your daily view" once data exists. The empty `today` view prints a
   two-line quickstart instead of a terse hint.
3. **Blank screen on first ingest.** A multi-minute first ingest showed nothing
   until done. It now prints "Indexing N transcript files (first run can take a
   minute; try `--limit 25` for an instant taste)..." before starting.
4. **Stale guidance.** Messages that said "run `looma init`" updated to reflect
   auto-creation.

## 4. Remaining friction (scoped to v2)

- **Full ingest latency** on very large histories is still minutes. A
  first-ingest that processes most-recent-first and streams results (value in
  seconds, completeness in the background) would remove the last wait. Out of
  scope for this cycle (it touches the ingest pipeline), recommended for v2.
- **Discovery**: a user still has to know `looma ingest` exists. A post-install
  message or a `looma` first-run banner that offers to ingest would close this.
  Low-risk; candidate for v2 polish.

## 5. Verdict

Time-to-first-answer on the quick path is ~10-15 seconds, and the brand-new-user
crash is gone. The two real levers left (streaming first ingest, post-install
discovery) are v2 items because they touch the ingest pipeline and packaging, not
the daily loop this cycle optimizes.
