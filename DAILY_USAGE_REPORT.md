# Looma Daily Usage Report (Phase 1)

Date: 2026-06-21
Question: when does Looma provide the most value, and the least? Grounded in the
real local corpus (72 project buckets, 597 sessions, 73k messages, 325 WorkItems),
not assumptions.

## 1. The shape of real usage

| Signal | Value | Implication |
|---|---|---|
| Projects with >= 5 sessions (daily-drivers) | 10 / 72 | the loop lives in a handful of repos |
| Single-session projects | 55 / 72 | most "projects" are one-off; low recall value |
| `unknown:<uuid>` buckets (no cwd) | 46 / 72 | dead weight in any listing |
| Projects with linked commits | 4 / 72 | "what shipped" is currently thin |
| Distinct projects active in last 1 day | 6 | a dev touches ~6 repos/day |
| Distinct projects active in last 7 days | 9 | ~9 repos/week |
| Distinct projects active in last 30 days | 12 | the working set is ~12 repos |

The daily reality is **cross-project context switching across ~6 repos/day**, with
a long tail of one-off projects and junk buckets. The sustained efforts where
memory matters - shb_database (89 sessions, Oct 2025 -> May 2026), lab-agents (55
sessions over 2 months), mddocs (34 sessions) - are exactly the ones a human
cannot hold in their head.

## 2. When Looma provides the MOST value

1. **Returning to a project after a gap.** shb_database spans 6 months; nobody
   remembers what they were mid-way through in a repo they last touched weeks ago.
   `resume` / `brief` / `explain` are strongest here. This is the core moment.
2. **Reloading context at the start of a work session.** "Where was I, what's
   next" - the single most repeated developer action, every morning, per repo.
3. **Context-switching between the ~6 repos touched in a day.** Each switch is a
   cold-start that Looma can warm.
4. **Recalling a decision made weeks ago** ("why did we go with X?") - `explain` /
   `ask` over the decision graph.
5. **End-of-week / standup recall** - "what did I actually get done?" across the
   working set. Nothing serves this today.

## 3. When Looma provides the LEAST value

1. **Single-session throwaway projects (55/72).** You just did it minutes ago; you
   remember. Looma adds nothing and, worse, clutters listings.
2. **`unknown:<uuid>` buckets (46/72).** No project identity -> no useful resume.
   Pure noise in `status` and pickers.
3. **Mid-task in a repo you are already deep in.** The context is already in your
   head; re-running resume is redundant.
4. **"What changed / what shipped" today** - commit linkage exists for only 4/72
   projects, so the shipped-work story is weak. This is a real gap for a daily
   command, not just thin data.

## 4. The daily-habit barrier

The value is real but the *trigger* is weak. Today a developer must:
- remember Looma exists at the start of work,
- choose between `resume`, `brief`, `work`, `ask`, `explain`,
- often pass `--project <key>` because the cwd may not resolve.

That is three decisions before any value. A daily habit needs **one obvious
command that just works**: sit down, run it, get "what was I doing, what changed,
what's blocked, what's next." That is the `looma today` thesis (Phase 2). The
cross-project reality (6 repos/day) means `today` should also answer "what else
did I touch recently" so a context switch is one command, not six.

## 5. What this means for the loop

- **Emphasize**: a single zero-argument daily entry point; recency; next-step.
- **De-emphasize for daily use**: per-subcommand choice, junk/unknown buckets,
  single-session noise.
- **Fix to unlock "what changed"**: commit linkage is the weakest input; until it
  improves, `today` should lean on sessions + work-item activity + git working
  state (uncommitted files) rather than promising a commit log.

The rest of this cycle builds and measures against this: `looma today` (Phase 2),
`looma weekly` (Phase 3), retention/overlap analysis (Phase 4), time-to-value
(Phase 5), agent value (Phase 6), and a V2 strategy (Phase 7).
