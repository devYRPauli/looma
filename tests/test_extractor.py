import json, os, unittest
from looma.extraction import extractor as ex
from looma.extraction import candidates as cand


class ServerDetectionTest(unittest.TestCase):
    def test_empty_or_null_model_list_is_not_available(self):
        # a reachable server with no model loaded (llama-server idle, the :8080
        # case) must not count as up, or auto-mode silently fails every call.
        self.assertIsNone(ex._server_model({"object": "list", "data": None}))
        self.assertIsNone(ex._server_model({"data": []}))
        self.assertIsNone(ex._server_model({}))
        self.assertIsNone(ex._server_model({"data": [{}]}))

    def test_real_model_id_is_available(self):
        self.assertEqual(ex._server_model({"data": [{"id": "qwen2.5-7b"}]}), "qwen2.5-7b")
        self.assertEqual(ex._server_model({"data": [{"id": "org/Qwen2.5-7B"}]}), "Qwen2.5-7B")


class LLMOutputGuardTest(unittest.TestCase):
    """The LLM path must inherit the same sanitation guards as the heuristic, so
    a model cannot re-emit the transcript/agent-meta junk we filter elsewhere."""

    def _extract_with(self, memories):
        ext = ex.LocalLLMExtractor()
        ext._call = lambda prompt: json.dumps(
            {"memories": memories, "work": {"label": "Wire up billing", "kind": "feature"}})
        return ext.extract([{"role": "user", "text": "real work about the database choice here"}])

    def test_meta_and_narration_and_code_are_dropped(self):
        out = self._extract_with([
            {"kind": "todo", "title": "Let me check the DB state and re-run the build"},
            {"kind": "decision", "title": "Decision --CONSTRAINS--> [ WorkItem ] <--BLOCKS-- Todo"},
            {"kind": "bug", "title": "const x = 1; return x;"},
            {"kind": "decision", "title": "Use Postgres over SQLite for the write-heavy path"},
        ])
        titles = [m["title"] for m in out["memories"]]
        self.assertEqual(titles, ["Use Postgres over SQLite for the write-heavy path"])

    def test_titles_are_ascii_folded_and_deduped(self):
        out = self._extract_with([
            {"kind": "decision", "title": "We chose gRPC over REST for lower latency"},
            {"kind": "decision", "title": "We chose gRPC over REST for lower latency"},
        ])
        self.assertEqual(len(out["memories"]), 1)
        self.assertTrue(out["memories"][0]["title"].isascii())

    def test_rejects_memory_predicate_shared(self):
        self.assertTrue(cand.rejects_memory("todo", "Let me inspect the parser output"))
        self.assertTrue(cand.rejects_memory("decision", "A --> B <-- C"))
        self.assertFalse(cand.rejects_memory("decision", "Use Redis over Memcached for sessions"))


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
