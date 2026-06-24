"""Build a fully synthetic Looma demo store - no real user data.

Creates two fake 'acme' git repos and realistic Claude Code transcripts in a
temp directory, then ingests them into a demo database. The README demo GIF is
recorded against this store (see demo.tape), so nothing private is ever shown.

Usage:
    python docs/demo/gen_demo.py /tmp/looma-demo/demo.db
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def sh(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_repo(repos, name, files):
    d = repos / name
    d.mkdir(parents=True, exist_ok=True)
    sh(["git", "init", "-q"], d)
    sh(["git", "config", "user.email", "dev@acme.test"], d)
    sh(["git", "config", "user.name", "Acme Dev"], d)
    sh(["git", "remote", "add", "origin", f"git@github.com:acme/{name}.git"], d)
    for fp, content in files.items():
        p = d / fp
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    sh(["git", "add", "-A"], d)
    sh(["git", "commit", "-q", "-m", f"Initial {name}"], d)
    return str(d)


def urec(uid, sid, cwd, branch, text, ts):
    return {"type": "user", "uuid": uid, "sessionId": sid, "cwd": cwd,
            "gitBranch": branch, "timestamp": ts,
            "message": {"role": "user", "content": text}}


def arec(uid, sid, cwd, branch, files, ts, text=""):
    content = [{"type": "text", "text": text}] if text else []
    for f in files:
        content.append({"type": "tool_use", "name": "Edit", "input": {"file_path": f}})
    return {"type": "assistant", "uuid": uid, "sessionId": sid, "cwd": cwd,
            "gitBranch": branch, "timestamp": ts,
            "message": {"role": "assistant", "model": "claude-opus-4-8", "content": content}}


def write_session(projects, cwd, sid, records):
    d = projects / cwd.replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{sid}.jsonl", "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def build(db_path):
    # repos/transcripts live next to the db so demo.tape can cd into a real repo
    base = Path(db_path).resolve().parent
    repos, projects = base / "repos", base / "projects"
    checkout = make_repo(repos, "checkout", {
        "src/webhooks/stripe.ts": "// stripe webhook handler\n",
        "src/webhooks/retry.ts": "// retry policy\n",
        "src/payments/charge.ts": "// charge\n",
        "src/lib/logger.ts": "// logger\n",
    })
    dashboard = make_repo(repos, "dashboard", {
        "src/ui/theme.ts": "// theme\n",
        "src/ui/toggle.tsx": "// toggle\n",
        "src/charts/Chart.tsx": "// chart\n",
    })

    write_session(projects, checkout, "c1", [
        urec("u1", "c1", checkout, "feature/stripe-retries",
             "Implement retry with exponential backoff for failed Stripe webhook deliveries.",
             "2026-06-20T09:00:00Z"),
        arec("a1", "c1", checkout, "feature/stripe-retries",
             ["src/webhooks/stripe.ts", "src/webhooks/retry.ts"], "2026-06-20T09:05:00Z",
             "We decided to use idempotency keys instead of a dedup table for redelivered events."),
    ])
    write_session(projects, checkout, "c1b", [
        urec("u1b", "c1b", checkout, "feature/stripe-retries",
             "Continue the webhook retry work: cap retries at 5 attempts and add jitter.",
             "2026-06-21T08:30:00Z"),
        arec("a1b", "c1b", checkout, "feature/stripe-retries",
             ["src/webhooks/retry.ts", "src/webhooks/stripe.ts"], "2026-06-21T08:45:00Z",
             "Capped backoff at 5 attempts with full jitter."),
    ])
    write_session(projects, checkout, "c2", [
        urec("u2", "c2", checkout, "fix/double-charge",
             "Fix the double-charge bug when a webhook is redelivered.", "2026-06-21T11:00:00Z"),
        arec("a2", "c2", checkout, "fix/double-charge",
             ["src/webhooks/stripe.ts", "src/payments/charge.ts"], "2026-06-21T11:10:00Z",
             "The charge endpoint returns a duplicate charge when Stripe redelivers an event; "
             "gating on the idempotency key fixes it."),
    ])
    write_session(projects, checkout, "c3", [
        urec("u3", "c3", checkout, "main",
             "Add structured logging to the payment pipeline.", "2026-06-22T14:00:00Z"),
        arec("a3", "c3", checkout, "main",
             ["src/payments/charge.ts", "src/lib/logger.ts"], "2026-06-22T14:08:00Z",
             "TODO: add alerting on repeated webhook delivery failures."),
    ])
    write_session(projects, dashboard, "d1", [
        urec("u4", "d1", dashboard, "feature/dark-mode",
             "Add a dark mode toggle across the dashboard.", "2026-06-21T10:00:00Z"),
        arec("a4", "d1", dashboard, "feature/dark-mode",
             ["src/ui/theme.ts", "src/ui/toggle.tsx"], "2026-06-21T10:12:00Z",
             "Use CSS variables instead of a styled-components theme provider for dark mode."),
    ])
    write_session(projects, dashboard, "d2", [
        urec("u5", "d2", dashboard, "fix/chart-flicker",
             "Fix the chart flicker on data refresh.", "2026-06-22T16:00:00Z"),
        arec("a5", "d2", dashboard, "fix/chart-flicker",
             ["src/charts/Chart.tsx"], "2026-06-22T16:06:00Z",
             "The chart flickers and loses state on every refresh due to a race condition in the effect."),
    ])

    os.environ["LOOMA_VECTORS"] = "off"
    from looma.storage.sqlite_store import Store
    from looma.adapters.claude import ClaudeAdapter
    from looma import pipeline
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    store = Store.open(str(db))
    store.migrate()
    pipeline.ingest_messages(store, adapters=[ClaudeAdapter(projects)])
    pipeline.reconcile_projects(store)
    pipeline.rebuild(store)
    store.close()
    print("demo store built at", db)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/looma-demo/demo.db")
