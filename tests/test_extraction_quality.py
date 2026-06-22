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


if __name__ == "__main__":
    unittest.main()
