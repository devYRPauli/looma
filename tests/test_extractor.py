import os, unittest
from looma.extraction import extractor as ex


class ExtractorSelectionTest(unittest.TestCase):
    def test_forced_modes(self):
        self.assertEqual(ex.get_extractor("heuristic").name, "heuristic")
        self.assertEqual(ex.get_extractor("llm").name, "llm")

    def test_auto_uses_llm_only_when_server_detected(self):
        orig = ex.detect_server
        try:
            ex.detect_server = lambda *a, **k: (False, None)
            self.assertEqual(ex.get_extractor("auto").name, "heuristic")
            ex.detect_server = lambda *a, **k: (True, "qwen")
            self.assertEqual(ex.get_extractor("auto").name, "llm")
        finally:
            ex.detect_server = orig


class ExtractorBehaviourTest(unittest.TestCase):
    def test_extract_json_object_and_array(self):
        self.assertEqual(ex._extract_json('noise {"a":1} tail')["a"], 1)
        self.assertEqual(ex._extract_json('x [{"k":2}] y')[0]["k"], 2)
        self.assertIsNone(ex._extract_json("no json here"))

    def test_llm_falls_back_to_heuristic_when_unreachable(self):
        prev = os.environ.get("LOOMA_LLM_URL")
        os.environ["LOOMA_LLM_URL"] = "http://127.0.0.1:9/v1/chat/completions"  # dead port
        try:
            out = ex.LocalLLMExtractor().extract(
                [{"role": "user", "text": "we need to add integration tests for billing"}])
            self.assertIn("memories", out)
            self.assertIn("work", out)
            # heuristic fallback should still surface the todo
            self.assertTrue(any(m["kind"] == "todo" for m in out["memories"]))
        finally:
            if prev is None:
                os.environ.pop("LOOMA_LLM_URL", None)
            else:
                os.environ["LOOMA_LLM_URL"] = prev


if __name__ == "__main__":
    unittest.main()
