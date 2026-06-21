import io, json, unittest
from looma import mcp
from tests.helpers import make_store  # ensures LOOMA_EXTRACTOR/VECTORS pinned


def drive(lines):
    out = io.StringIO()
    mcp.serve(stdin=io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), stdout=out)
    return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]


class McpTest(unittest.TestCase):
    def test_initialize_and_tools_list(self):
        resp = drive([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no response
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(resp[0]["id"], 1)
        self.assertEqual(resp[0]["result"]["serverInfo"]["name"], "looma")
        names = {t["name"] for t in resp[1]["result"]["tools"]}
        self.assertEqual(names, {"today", "resume_work", "brief", "ask", "timeline",
                                 "explain", "list_work", "recall"})

    def test_tools_call_unknown_project_is_graceful(self):
        resp = drive([{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "list_work", "arguments": {"project": "path:/nope"}}}])
        r = resp[0]["result"]
        self.assertIn("content", r)
        self.assertEqual(r["content"][0]["type"], "text")

    def test_unknown_tool_errors(self):
        resp = drive([{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "bogus", "arguments": {}}}])
        self.assertIn("error", resp[0])


if __name__ == "__main__":
    unittest.main()
