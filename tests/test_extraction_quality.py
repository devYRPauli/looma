"""V2 Phase 1 extraction-quality guards: automated-session filtering and the
tightened bug classifier. These lock in the real-corpus wins (Untitled 45->13%,
bug overclassification 79->38%) so they cannot silently regress."""

import unittest

from looma import sanitize
from looma.extraction import candidates as cand


def _kinds(text, role="user"):
    out = cand.extract_candidates([{"role": role, "text": text}])
    return [(c["kind"], c["title"]) for c in out]


class AutomatedSessionTest(unittest.TestCase):
    def test_synthetic_prompts_flagged(self):
        for opener in [
            "You are summarizing a Claude Code session for a daily memory log.",
            "Apply maximum non-destructive compression. Rules: keep all facts.",
            "Read the conversation extract below and write ONE memory entry in this exact format:",
            "Extract structured project memory from a coding session transcript.",
            "You are a helpful assistant. Respond with only the answer.",
        ]:
            self.assertTrue(
                sanitize.is_automated_session([{"role": "user", "text": opener}]),
                f"should flag synthetic: {opener!r}",
            )

    def test_real_coding_session_not_flagged(self):
        for opener in [
            "Let's implement OAuth login. We decided to use JWT over opaque tokens.",
            "Fix the checkout rounding bug - the total is off by a cent.",
            "Can you refactor the parser to stream instead of buffering?",
        ]:
            self.assertFalse(
                sanitize.is_automated_session([{"role": "user", "text": opener}]),
                f"should NOT flag real work: {opener!r}",
            )


class BugPrecisionTest(unittest.TestCase):
    def test_completed_fix_narration_is_not_a_bug(self):
        for line in [
            "Done. I've fixed both timeout issues and the suite passes now.",
            "Now fixed the race condition in the worker pool.",
            "No regression in the provider policy after the change.",
            "agent-edit-regression.test.ts: FAIL -> FAIL",
        ]:
            self.assertFalse(
                any(k == "bug" for k, _ in _kinds(line)),
                f"should not be a bug: {line!r}",
            )

    def test_real_symptom_assertions_are_bugs(self):
        for line in [
            "The export button does not work on Safari and never triggers a download.",
            "There's a bug: the callback drops the state param on redirect.",
            "The total is off by a cent because line items round individually here.",
            "The handler returns the wrong content type so the browser ignores it.",
        ]:
            self.assertTrue(
                any(k == "bug" for k, _ in _kinds(line)),
                f"should be a bug: {line!r}",
            )

    def test_architecture_requires_a_design_rule_not_a_mention(self):
        self.assertFalse(any(k == "architecture" for k, _ in _kinds(
            "Use ARCHITECTURE.md as the source of truth for the rebuild.")))
        self.assertTrue(any(k == "architecture" for k, _ in _kinds(
            "Architecturally, the leader election must be idempotent so a re-run is safe.")))


class MetaNoiseTest(unittest.TestCase):
    """Transcript structure and agent-directed meta must never become memories.
    Lines below are real false positives observed in `looma weekly` decisions /
    blockers (role-prefixed turns, ascii arrow diagrams, Looma's own pattern
    vocabulary ingested from dev sessions, dangling preambles, agent imperatives,
    greetings). (V2.1.2)"""

    META = [
        "[346] assistant: I'm adding tests first around the worker routing",
        "assistant: let me check the worktree now",
        "Decision --CONSTRAINS--> [ WorkItem ] <--BLOCKS-- Todo",
        '* "we decided", "decision", "use X instead of Y"',
        "For your lab, the most important design decision is:",
        "Next step is done:",
        "Here's where we are and the decision:",
        "Here is the approach we will take:",
        "Now, move on to the next step then",
        "Continue to the next step now",
        "Hey! Good Morning. Where were we and what were we doing?",
    ]
    REAL = [
        "We decided to use JWT over opaque tokens for the session layer.",
        "Architecturally, the leader election must be idempotent so a re-run is safe.",
        "The export button does not work on Safari and never triggers a download.",
        "Continue supporting the legacy v1 API until the Q3 migration lands.",
        "Decision: use gRPC over REST for lower latency.",
    ]

    def test_meta_lines_flagged(self):
        for line in self.META:
            self.assertTrue(sanitize.looks_like_meta(line),
                            f"should flag meta: {line!r}")

    def test_real_memories_not_flagged(self):
        for line in self.REAL:
            self.assertFalse(sanitize.looks_like_meta(line),
                             f"should NOT flag real memory: {line!r}")

    def test_meta_lines_never_become_candidates(self):
        for line in self.META:
            self.assertEqual(_kinds(line), [], f"should yield no candidate: {line!r}")

    def test_real_memories_still_extracted(self):
        for line in self.REAL[:2]:  # decision + architecture lines carry a kind
            self.assertTrue(_kinds(line), f"should still extract: {line!r}")

    def test_injected_memory_rule_boilerplate_is_noise(self):
        # Agent-memory-rule instruction prose ("... must come from a tool result
        # this turn, never from memory ...") bleeds into turns untagged, so
        # strip_injected misses it and "must come from" reads as an architecture
        # rule. It is injected boilerplate, not a memory. (V2.1.4)
        line = "must come from a tool result this turn, never from memory. Definitions,"
        self.assertTrue(sanitize.is_noise(line), f"should be noise: {line!r}")
        self.assertEqual(_kinds(line), [], f"should yield no candidate: {line!r}")


class NarrationTest(unittest.TestCase):
    """First-person progress narration ("I'm checking ...", "Let me ...", "Both
    pass.") is activity in flight, not a durable decision or open task - the
    analogue of the completed-fix guard for the bug kind. (V2.1.2)"""

    def test_progress_narration_is_not_a_decision_or_todo(self):
        for line in [
            "I'm adding tests first around the worker routing for DOCX and PPTX.",
            "I am preparing the Ubuntu GitHub SSH connectivity now and will verify.",
            "Let me start with the repair fail-closed bug since it is highest value.",
            "Both pass. Let me confirm the end-to-end test is a genuine regression.",
            "I'll switch the parser to streaming next so memory stays flat.",
        ]:
            kinds = [k for k, _ in _kinds(line)]
            self.assertNotIn("decision", kinds, f"narration is not a decision: {line!r}")
            self.assertNotIn("todo", kinds, f"narration is not a todo: {line!r}")
            self.assertNotIn("architecture", kinds, f"narration is not architecture: {line!r}")

    def test_action_narration_is_never_a_bug(self):
        # "Let me ..." is the assistant's next move, not a symptom - even when the
        # line name-drops a bug/crash word it must not surface as a blocker.
        for line in [
            "Let me check the DB state since the rebuild likely crashed earlier.",
            "Let me add a focused test file for the extraction-quality changes.",
            "Now I will inspect the empty-memory case before touching the parser.",
        ]:
            self.assertEqual(_kinds(line), [], f"action narration leaked: {line!r}")

    def test_let_us_decision_survives_action_guard(self):
        # "Let's use X over Y" is a real decision and must not be caught as action
        self.assertTrue(any(k == "decision" for k, _ in _kinds(
            "Let's use Postgres over SQLite for the write-heavy path.")))

    def test_smart_quote_narration_is_still_caught(self):
        # transcripts carry curly apostrophes; folding to ASCII keeps the
        # apostrophe-bearing patterns (I'm/let's/won't) from silently missing.
        for line in [
            "I’m adding tests first around the worker routing for DOCX kinds.",
            "Let’s not do that; I’ll switch the parser to streaming next.",
        ]:
            kinds = [k for k, _ in _kinds(line)]
            self.assertNotIn("decision", kinds, f"smart-quote narration leaked: {line!r}")
            self.assertNotIn("todo", kinds, f"smart-quote narration leaked: {line!r}")

    def test_real_decisions_and_todos_survive_narration_guard(self):
        self.assertTrue(any(k == "decision" for k, _ in _kinds(
            "We decided to use JWT over opaque tokens for the session layer.")))
        self.assertTrue(any(k == "todo" for k, _ in _kinds(
            "We need to add a timeout to outbound calls before the release.")))


if __name__ == "__main__":
    unittest.main()
