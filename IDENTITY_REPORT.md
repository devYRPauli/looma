# Identity Hygiene - V2 Phase 2 Report

Goal: every session belongs somewhere meaningful. A session whose "project" is
`unknown:<uuid>`, `tmp`, or `/` pollutes `looma status`, fragments a repo's
history, and makes cross-session retrieval miss. Measured on the same real corpus
(617 sessions, 3 agents), full re-ingest.

---

## 1. Headline

| Metric                                   | Baseline | V2   | Delta |
|------------------------------------------|----------|------|-------|
| Total projects                           | 72       | 24   | -67%  |
| `unknown:<uuid>` singleton projects      | 46       | 0    | -46   |
| Junk/ephemeral projects (tmp, /, T, ...) | 5        | 0    | -5    |
| Real git-remote projects                 | 9        | 9    | =     |
| Real local-path projects                 | 16       | 12   | cleaned |
| Honest "Unsorted (<agent>)" buckets      | 0        | 3    | +3    |

Every one of the 617 sessions now resolves to either a real repository or a
single clearly-labeled per-agent "Unsorted" bucket. There are no more sessions
masquerading as their own project, and no projects named after a temp directory.

---

## 2. What was wrong

### 2.1 `unknown:<uuid>` - 46 singleton projects (64% of the corpus)

Every Cursor session that lacked a `workspaceUris` field minted its own project
keyed on the session id (`unknown:<native_id>`). 46 sessions became 46 separate
"projects", none of which clustered with anything.

### 2.2 Junk projects from ephemeral roots

Sessions whose cwd was a scratch path created real-looking projects:

| Fake project | Root                                              | Sessions |
|--------------|---------------------------------------------------|----------|
| `tmp`        | `/private/tmp`                                    | 298      |
| `/`          | `/`                                               | 7        |
| `T`          | `/private/var/folders/.../T` (macOS scratch)      | 5        |
| `<user>`     | `/Users/<home>` (home dir itself)                 | 12       |
| `projects`   | `/Users/<home>/.claude/projects`                  | 1        |

The 298-session `tmp` project is the tell: those are the programmatic
memory-log/compression API calls from Phase 1, all launched from `/private/tmp`.
They were the single largest "project" in the corpus and entirely synthetic.

### 2.3 Duplicate identity

`shb_database` existed twice - once canonically as
`github.com/donandrade/shb_database` (89 sessions) and once as
`path:/Users/<home>/Desktop/shb_database` (2 sessions), because the second
checkout's git remote was unreadable at ingest, so identity fell back to the path.

---

## 3. Changes

1. **Ephemeral / degenerate root rejection** (`identity.resolve`). The
   filesystem root, OS temp roots (`/tmp`, `/var/tmp`, the macOS
   `.../var/folders/<x>/<y>/T` scratch root), config/cache homes (`.claude`,
   `.codex`, `.cursor`, `.config`, `.cache`), and the home directory itself now
   resolve to `None`. The match is precise: a real project that merely lives
   under a temp dir (e.g. a pytest fixture at `.../T/tmpXXXX/myproject`) still
   resolves - only the scratch root itself is rejected.

2. **Single per-agent unsorted bucket** (`pipeline.ingest_messages`). An
   unresolvable session now joins `unsorted:<source>` ("Unsorted (cursor)")
   instead of minting `unknown:<session-id>`. 46 singleton Cursor projects
   collapse into one honest bucket; the synthetic `/tmp` sessions collapse into
   "Unsorted (claude)" instead of a fake `tmp` project.

3. **Cursor workspace recovery** (`adapters/cursor.py`). When no bubble carries a
   `workspaceUris`, the adapter derives the workspace from the common parent of
   the session's attached-file URIs (`allAttachedFileCodeChunksUris`), guarded
   against too-shallow ancestors. Where the signal exists, the session clusters
   with the rest of that repo (e.g. `Desktop/testing`) instead of going unsorted.

---

## 4. Result

| Project bucket          | Sessions |
|-------------------------|----------|
| Real git-remote repos   | looma 26, mddocs 34, shb_database 89, Lab-Agents 56, world-cup 3, fundrd 2, resume-builder 2, continue 1, portfolio 1 |
| Real local-path repos   | papers-rag 5, yash-portfolio 5, testing 2, content-copilot 2, football-hub 1, AI 1, Julia 1, TurboQuant 1/2 ... |
| Unsorted (claude)       | 323 (the synthetic /tmp jobs + home/scratch sessions) |
| Unsorted (cursor)       | 46 (chat-only, no file or workspace signal) |
| Unsorted (codex)        | 10 |

Real repositories are untouched and intact; only the noise moved. A consuming
agent asking "what projects exist" now sees 21 real repos and 3 honest unsorted
buckets, not 72 entries half of which are temp directories and session ids.

---

## 5. Known residual

- **Cross-checkout duplicate** (`shb_database` under both its git remote and a
  bare path) is not auto-merged. The path-only checkout had no readable remote at
  ingest, so the two are genuinely indistinguishable without it; fuzzy-merging by
  repo name risks colliding unrelated repositories. Left as-is by design; a future
  `looma correct merge-project` (the correction layer already exists) is the safe
  manual remedy.
- **Cursor chat-only sessions** (46) carry no file or workspace signal at all and
  remain in the unsorted bucket - correctly, since there is nothing to attribute
  them to.

Tests: `tests/test_project_identity.py` (ephemeral rejection + deep-temp-subdir
still resolves), `tests/test_adapters.py` (Cursor common-root recovery). Full
suite 93 passed, 1 skipped.
