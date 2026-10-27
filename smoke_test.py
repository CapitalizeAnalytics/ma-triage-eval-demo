#!/usr/bin/env python3
"""Basic functionality test: run the AppSec Triage Agent once on a tiny sample.

Not part of the eval. Use it to confirm your API key, agent ID, environment ID,
and file mounting work before running the full harness (runner.py).

Set these first (PowerShell):
    $env:ANTHROPIC_API_KEY="sk-ant-..."
    $env:TRIAGE_AGENT_ID="agent_..."
    $env:TRIAGE_ENV_ID="env_..."
Then:  python smoke_test.py
Output files are written to the smoke_test_files folder next to this script.
"""
import json
import os
from pathlib import Path

from anthropic import Anthropic

AGENT_ID = os.environ.get("TRIAGE_AGENT_ID", "agent_...")
ENVIRONMENT_ID = os.environ.get("TRIAGE_ENV_ID", "env_...")

OUT = Path(__file__).parent / "smoke_test_files"
OUT.mkdir(exist_ok=True)

SAMPLE_FILES = {
    "app.py": '''import sqlite3
from flask import request

def get_user():
    name = request.args.get("name")
    conn = sqlite3.connect("db.sqlite")
    return conn.execute("SELECT * FROM users WHERE name = '" + name + "'").fetchall()

def get_config():
    key = "version"
    conn = sqlite3.connect("db.sqlite")
    return conn.execute("SELECT * FROM config WHERE k = '" + key + "'").fetchall()
''',
    "scan.json": '''[
  {"id": "F-001", "rule": "SQL Injection", "file": "app.py", "line": 7},
  {"id": "F-002", "rule": "SQL Injection", "file": "app.py", "line": 12}
]
''',
}

if "..." in AGENT_ID or "..." in ENVIRONMENT_ID:
    raise SystemExit("Set TRIAGE_AGENT_ID and TRIAGE_ENV_ID first.")

client = Anthropic()  # reads ANTHROPIC_API_KEY; the SDK sets the beta header

for name, content in SAMPLE_FILES.items():
    # Always rewrite so edits to SAMPLE_FILES take effect; LF endings on every platform.
    (OUT / name).write_text(content, encoding="utf-8", newline="\n")

resources = []
for name in SAMPLE_FILES:
    f = client.beta.files.upload(file=OUT / name)
    resources.append({
        "type": "file",
        "file_id": f.id,
        "mount_path": f"/mnt/session/uploads/{name}",
    })

session = client.beta.sessions.create(
    agent=AGENT_ID,
    environment_id=ENVIRONMENT_ID,
    resources=resources,
    title="Triage smoke test",
)
print(f"Session: {session.id}\n")

tickets = []

# Open the stream first, then send the kickoff message
with client.beta.sessions.events.stream(session.id) as stream:
    client.beta.sessions.events.send(
        session.id,
        events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": "Triage the findings in scan.json."}],
        }],
    )

    for event in stream:
        match event.type:
            case "agent.message":
                for block in event.content:
                    print(block.text, end="")
            case "agent.tool_use":
                print(f"\n[{event.name}] {event.input}")
            case "agent.custom_tool_use":
                # Stub handler: save the ticket draft, tell the agent it worked
                tickets.append(event.input)
                client.beta.sessions.events.send(
                    session.id,
                    events=[{
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": "Draft created."}],
                    }],
                )
            case "session.status_idle":
                reason = getattr(getattr(event, "stop_reason", None), "type", None)
                if reason != "requires_action":
                    break

(OUT / "tickets.json").write_text(json.dumps(tickets, indent=2), encoding="utf-8")
print(f"\n\nSaved {len(tickets)} ticket draft(s) to {OUT / 'tickets.json'}")
