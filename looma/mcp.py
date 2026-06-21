"""Minimal MCP server (goal Phase 4) - lets any MCP agent consume Looma context.

Pure stdlib: JSON-RPC 2.0 over newline-delimited stdio. No dependency, no network,
no hosted service - fully local. Tools: resume_work, ask, timeline, list_work, recall.
Run via `looma mcp` (typically launched by the agent inside the project directory).
"""

import json
import os
import sys

from . import config, identity, timeline
from .retrieval import ask as ask_mod
from .retrieval import resume as resume_mod
from .retrieval.match import match_work_items
from .storage.sqlite_store import Store
from .storage.vector_store import get_vector_store

PROTOCOL = "2024-11-05"

_OPT_PROJECT = {"project": {"type": "string", "description": "project canonical key (optional)"},
                "cwd": {"type": "string", "description": "working dir to resolve a project (optional)"}}

TOOLS = [
    {"name": "resume_work",
     "description": "Reconstruct the active work for a goal: WorkItem, decisions, blockers, bugs, commits, files, next step.",
     "inputSchema": {"type": "object", "properties": {**_OPT_PROJECT,
                     "goal": {"type": "string", "description": "what to resume, e.g. 'auth'"}}}},
    {"name": "ask",
     "description": "Search validated project memory and work items.",
     "inputSchema": {"type": "object", "properties": {**_OPT_PROJECT,
                     "query": {"type": "string"}}, "required": ["query"]}},
    {"name": "timeline",
     "description": "Show a work item's evolution (decisions, commits, bugs, sessions) over time.",
     "inputSchema": {"type": "object", "properties": {**_OPT_PROJECT,
                     "work": {"type": "string", "description": "work item id (#5) or goal text"}}}},
    {"name": "list_work",
     "description": "List work items for the project with confidence and status.",
     "inputSchema": {"type": "object", "properties": {**_OPT_PROJECT,
                     "status": {"type": "string", "description": "filter by status/lifecycle"}}}},
    {"name": "recall",
     "description": "Recall what was decided/known about a topic (decisions, architecture, bugs).",
     "inputSchema": {"type": "object", "properties": {**_OPT_PROJECT,
                     "query": {"type": "string"}}, "required": ["query"]}},
]


class _Server:
    def __init__(self):
        path = config.default_db_path()
        self.store = Store.open(path)
        self.store.migrate()
        self.vstore = get_vector_store(path)

    def _project(self, args):
        key = args.get("project")
        if key:
            return self.store.find_project_by_key(key)
        ident = identity.resolve(args.get("cwd") or os.getcwd())
        return self.store.find_project_by_key(ident["canonical_key"]) if ident else None

    # ---- tools ----
    def resume_work(self, a):
        proj = self._project(a)
        if not proj:
            return self._no_project(a)
        res = resume_mod.resume(self.store, proj, a.get("goal", ""), vstore=self.vstore)
        return _fmt_resume(res, proj)

    def ask(self, a):
        proj = self._project(a)
        if not proj:
            return self._no_project(a)
        rows = ask_mod.ask(self.store, proj["id"], a.get("query", ""), vstore=self.vstore)
        if not rows:
            return "No matches."
        return "\n".join(f"[{r['type']}/{r['kind']}] {r['title']}  "
                         f"(conf {r['confidence']:.2f} {r['band']})" for r in rows)

    def recall(self, a):
        proj = self._project(a)
        if not proj:
            return self._no_project(a)
        rows = [r for r in ask_mod.ask(self.store, proj["id"], a.get("query", ""), vstore=self.vstore)
                if r["type"] == "memory"]
        if not rows:
            return "Nothing recalled."
        return "\n".join(f"[{r['kind']}] {r['title']}  (conf {r['confidence']:.2f}; "
                         f"work: {r.get('work_item')})" for r in rows)

    def timeline(self, a):
        from .correction import resolve_workitem
        proj = self._project(a)
        if not proj:
            return self._no_project(a)
        token = a.get("work", "")
        wi = resolve_workitem(self.store, proj["id"], token) if token else None
        if not wi and token:
            hits = match_work_items(self.store, proj["id"], token, vstore=self.vstore)
            wi = hits[0] if hits else None
        if not wi:
            wis = self.store.project_work_items(proj["id"])
            wi = wis[0] if wis else None
        if not wi:
            return "No work items."
        return timeline.format_timeline(wi, timeline.build(self.store, proj["id"], wi["id"]))

    def list_work(self, a):
        proj = self._project(a)
        if not proj:
            return self._no_project(a)
        wis = self.store.project_work_items(proj["id"])
        if a.get("status"):
            wis = [w for w in wis if a["status"] in (w["status"], w["lifecycle"])]
        if not wis:
            return "No work items."
        return "\n".join(f"#{w['id']} {w['title']}  [{w['kind']}/{w['lifecycle']}] "
                         f"conf {(w['confidence'] or 0):.2f}" for w in wis)

    def _no_project(self, a):
        return (f"No Looma project for {a.get('cwd') or os.getcwd()}. "
                "Pass project=<canonical key> (see `looma status`).")

    # ---- dispatch ----
    def handle(self, msg):
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            return _ok(mid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                             "serverInfo": {"name": "looma", "version": "1.0"}})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            fn = getattr(self, name, None) if name in {t["name"] for t in TOOLS} else None
            if not fn:
                return _err(mid, -32601, f"unknown tool {name}")
            try:
                text = fn(args)
            except Exception as e:
                return _ok(mid, {"content": [{"type": "text", "text": f"error: {e}"}],
                                 "isError": True})
            return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
        return _err(mid, -32601, f"unknown method {method}")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _fmt_resume(res, proj) -> str:
    mode = res["mode"]
    if mode in ("empty", "cold") and not res.get("bundle"):
        alts = res.get("alternatives", [])
        head = f"No confident match in {proj['display_name']}."
        if alts:
            head += " Recent work:\n" + "\n".join(f"  #{w['id']} {w['title']}" for w in alts)
        return head
    b = res["bundle"]
    wi = b["work_item"]
    out = [f"[{mode}] PROJECT {proj['display_name']}",
           f"WORKITEM #{wi['id']}: {wi['title']}  [{wi['kind']}/{wi['lifecycle']}] "
           f"conf {(wi['confidence'] or 0):.2f}"]
    if b["decisions"]:
        out.append("DECISIONS: " + " | ".join(e["title"] for e in b["decisions"][:5]))
    if b["todos"]:
        out.append("UNFINISHED: " + " | ".join(e["title"] for e in b["todos"][:6]))
    if b["bugs"]:
        out.append("BUGS: " + " | ".join(e["title"] for e in b["bugs"][:4]))
    if b["commits"]:
        out.append("COMMITS: " + " | ".join(f"{c['sha'][:9]} {(c.get('message') or '')[:40]}"
                                             for c in b["commits"][:4]))
    if b["files"]:
        out.append("FILES: " + ", ".join(b["files"][:8]))
    if res.get("alternatives"):
        out.append("ALTERNATIVES: " + " | ".join(f"#{w['id']} {w['title']}"
                                                  for w in res["alternatives"]))
    if b["next_step"]:
        out.append("NEXT: " + b["next_step"])
    return "\n".join(out)


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = _Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
