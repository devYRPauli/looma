import unittest
from looma.benchmark import harness
from looma.extraction.extractor import HeuristicExtractor


class BenchmarkTest(unittest.TestCase):
    def test_scoring_matches_paraphrase(self):
        tp, fp, fn = harness._score_kind(
            ["use JWT over opaque tokens for stateless auth"],
            ["use JWT instead of opaque tokens for stateless verification"])
        self.assertEqual((tp, fp, fn), (1, 0, 0))

    def test_prf_perfect_and_empty(self):
        self.assertEqual(harness._prf(3, 0, 0)["f1"], 1.0)
        self.assertEqual(harness._prf(0, 5, 0)["precision"], 0.0)
        self.assertEqual(harness._prf(0, 0, 0)["f1"], 0.0)

    def test_false_positive_counted(self):
        tp, fp, fn = harness._score_kind(["totally unrelated nonsense text here"],
                                         ["use redis for sessions"])
        self.assertEqual((tp, fp, fn), (0, 1, 1))

    def test_run_returns_metrics(self):
        m = harness.run(HeuristicExtractor())
        self.assertEqual(m["extractor"], "heuristic")
        for key in ("precision", "recall", "f1"):
            self.assertIn(key, m["overall"])
            self.assertTrue(0.0 <= m["overall"][key] <= 1.0)
        # heuristic should get a non-trivial F1 on the golden set
        self.assertGreater(m["overall"]["f1"], 0.3)


if __name__ == "__main__":
    unittest.main()
