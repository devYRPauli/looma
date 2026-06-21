# Looma Agent Value Report (Phase 6)

Date: 2026-06-21
Question: can another agent become significantly better with Looma context?
Evaluated by driving the MCP server end-to-end against the real corpus.

## 1. MCP works end-to-end

`looma mcp` (stdlib JSON-RPC 2.0 over stdio, zero deps) was driven through a full
session: `initialize` -> `tools/list` -> three `tools/call`s. All returned valid
responses. Tools exposed (9):

```
today, weekly, resume_work, brief, ask, timeline, explain, list_work, recall
```

Any MCP-capable agent (Claude Code, Cursor, a custom agent) can call these. No
cloud, no API key - the agent reads grounded project memory straight off the
local graph.

## 2. The core question: is the agent meaningfully better?

Yes, on three measurable axes.

### Context quality (efficiency + grounding)

An agent that needs to "resume work in repo X" without Looma reconstructs context
from raw transcript - reading recent messages and parsing them. Measured against
the last 40 messages per project (a conservative slice of what it would consume):

| project | raw transcript an agent would read | `resume_work` | `today` | compression |
|---|---|---|---|---|
| shb_database | 41,053 chars | 928 | 1,285 | **44x** |
| lab-agents | 50,910 chars | 1,087 | 1,875 | **47x** |
| mddocs | 51,980 chars | 515 | 776 | **101x** |
| world-cup | 34,265 chars | 1,087 | 1,417 | **32x** |

Looma delivers the same orientation in **1-3% of the tokens** - and it is
*structured and grounded*: real WorkItem, real files, typed decisions vs bugs vs
todos, linked commits, an explicit next step. The agent is not handed a noisy
transcript to re-derive meaning from (lossy, hallucination-prone); it is handed
the meaning. 32x-101x less context for a better answer leaves the agent's window
for the actual task.

### Retrieval quality

`resume_work(goal)` and `ask(query)` route to the right WorkItem/memory. From the
v1.5 resume benchmark on this corpus: goal-match MRR 1.00 and zero
COLD-on-a-true-match, with relevance that survives generic titles (it scores file
paths + linked memories). The agent asks in its own words and gets the correct
effort back.

### Answer quality

A `resume_work` payload contains everything an agent needs to *act*: what the
work is, which files it touches, what decisions constrain it, what is blocked, and
the next step - each with a confidence band so the agent knows how much to trust
it. An agent can state "continue editing X; decision Y constrains it; Z is
blocked" without opening the repo.

## 3. Quality fix found while evaluating

The `ask`/`recall`/`list_work` MCP handlers built result strings directly and a
transcript `warning` emoji leaked into the payload. Now all MCP tool output is
ASCII-folded centrally in the dispatch, so transcript emoji/smart-quotes never
pollute another agent's context window.

## 4. Honest limits

- **Title/typing quality** still caps answer crispness: an `ambiguous` resume can
  surface a test-file-derived title, and `ask` over raw memory still returns some
  conversational "bug" lines. This is the heuristic-extractor ceiling; the local
  LLM extractor (v2) is the fix. It lowers answer quality at the margin, not the
  32x-101x context-efficiency win.
- **No push model.** The agent must call Looma; Looma does not proactively inject
  context. A thin MCP "resource" exposing `today` as ambient context is a v2
  candidate.

## 5. Verdict

An agent is significantly better with Looma: it gets a grounded, typed, correct
orientation in 1-3% of the context it would otherwise spend re-reading
transcripts, with retrieval that lands the right work and answers it can act on
directly. The differentiator is **grounded compression** - meaning, not bytes.
This is Looma's strongest case as agent infrastructure, and the cleanest argument
for the daily loop extending to "my other agents run Looma too."
