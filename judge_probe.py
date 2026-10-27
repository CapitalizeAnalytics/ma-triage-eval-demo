#!/usr/bin/env python3
"""Show exactly what the judge model returns for one graded trial.

Use this when many rubric answers come back unknown or invalid. It prints the
stop reason, the response block types, token usage, and the raw text, so you can
see whether replies are being cut off or arrive without a text block.

Usage:  python judge_probe.py results/v1 T1 3
"""
import json
import sys
from pathlib import Path

import fixtures
import graders
from grade import DEFAULT_JUDGE_MODEL

run_dir, task_id, trial = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
row = next(r for r in rows if r["task_id"] == task_id and r["trial"] == trial)
task = next(t for t in fixtures.get_tasks() if t.id == task_id)
ticket = row["tickets"][0]

from anthropic import Anthropic
client = Anthropic()
for max_tokens in (10, 300):
    for dim, question in graders.RUBRIC.items():
        resp = client.messages.create(
            model=DEFAULT_JUDGE_MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": graders.judge_prompt(task, ticket, dim, question)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        print(f"max_tokens={max_tokens:<4} {dim:<13} stop={resp.stop_reason} "
              f"blocks={[b.type for b in resp.content]} out_tokens={resp.usage.output_tokens}")
        print(f"    text={text!r}")
