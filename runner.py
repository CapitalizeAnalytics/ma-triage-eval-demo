#!/usr/bin/env python3
"""Run eval trials against the managed agent and save raw results.

The runner only COLLECTS: it starts a fresh session per trial, captures the
transcript and the tickets (the outcome), and writes results.jsonl. Grading is
a separate step (grade.py) so graders can be fixed and re-run without paying
for new agent runs.

Usage:
    set ANTHROPIC_API_KEY, TRIAGE_AGENT_ID, TRIAGE_ENV_ID
    python runner.py --label v1 --k 5
    python runner.py --label smoke --k 1 --tasks T1,T3
    python runner.py --dry-run
"""
import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import fixtures

BASE = Path(__file__).parent
DEFAULT_K = 5
MAX_SECONDS = 900  # wall-clock cap per trial, checked between events


def dump(event):
    try:
        return event.model_dump(mode="json")
    except Exception:
        return {"type": getattr(event, "type", "unknown"), "repr": repr(event)}


def run_trial(client, task, idx, resources, agent_id, env_id, label, transcripts_dir):
    t0 = time.time()
    tickets, texts, transcript = [], [], []
    tool_calls, error, session_id, usage = 0, None, None, None
    try:
        session = client.beta.sessions.create(
            agent=agent_id,
            environment_id=env_id,
            resources=resources,
            title=f"EVAL {label} {task.id} trial {idx}",
        )
        session_id = session.id
        with client.beta.sessions.events.stream(session_id) as stream:
            client.beta.sessions.events.send(session_id, events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": fixtures.task_statement(task)}],
            }])
            for event in stream:
                transcript.append(dump(event))
                if time.time() - t0 > MAX_SECONDS:
                    error = "timeout"
                    break
                et = event.type
                if et == "agent.message":
                    texts.extend(b.text for b in event.content if getattr(b, "text", None))
                elif et == "agent.tool_use":
                    tool_calls += 1
                elif et == "agent.custom_tool_use":
                    tool_calls += 1
                    # Outcome store: the ticket the agent "filed". Graders read this,
                    # not the agent's description of what it did.
                    tickets.append(event.input)
                    client.beta.sessions.events.send(session_id, events=[{
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": "Draft created."}],
                    }])
                elif et == "session.status_idle":
                    reason = getattr(getattr(event, "stop_reason", None), "type", None)
                    if reason != "requires_action":
                        break
                elif et.startswith("session.") and ("error" in et or "terminated" in et):
                    error = et
                    break
        try:  # token usage, if the API exposes it on the session
            s = client.beta.sessions.retrieve(session_id)
            u = getattr(s, "usage", None)
            usage = u.model_dump(mode="json") if hasattr(u, "model_dump") else u
        except Exception:
            usage = None
    except Exception as exc:  # noqa: BLE001 - record and keep the run going
        error = repr(exc)

    latency = round(time.time() - t0, 1)
    tpath = transcripts_dir / f"{task.id}_trial{idx}.jsonl"
    tpath.write_text("\n".join(json.dumps(e, default=str) for e in transcript), encoding="utf-8")
    return {
        "run_label": label, "task_id": task.id, "trial": idx, "session_id": session_id,
        "error": error, "tickets": tickets, "agent_text": "\n".join(texts),
        "metrics": {"latency_s": latency, "tool_calls": tool_calls, "usage": usage},
        "transcript": str(tpath.relative_to(tpath.parents[1])),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--k", type=int, default=DEFAULT_K, help="trials per task")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    ap.add_argument("--workers", type=int, default=4, help="parallel sessions (use 1 if rate limited)")
    ap.add_argument("--agent-id", default=os.environ.get("TRIAGE_AGENT_ID"))
    ap.add_argument("--env-id", default=os.environ.get("TRIAGE_ENV_ID"))
    ap.add_argument("--dry-run", action="store_true", help="print the plan; make no API calls")
    args = ap.parse_args()

    tasks = fixtures.get_tasks([t for t in args.tasks.split(",") if t])
    n_sessions = len(tasks) * args.k
    print(f"Plan: {len(tasks)} tasks x {args.k} trials = {n_sessions} sessions")
    for t in tasks:
        print(f"  {t.id} {t.name:<28} {t.category:<12} critical={t.critical!s:<5} "
              f"expect={t.expected_verdict:<16} hash={t.repo_hash}")
    if args.dry_run:
        return
    if not (args.agent_id and args.env_id):
        raise SystemExit("Set TRIAGE_AGENT_ID and TRIAGE_ENV_ID (or pass --agent-id / --env-id).")

    from anthropic import Anthropic
    client = Anthropic()

    out = BASE / "results" / args.label
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    fixtures_dir = BASE / "fixtures"

    # Upload each task's files once. Every session mounts its own copy, so each
    # trial starts from a clean, identical environment.
    resources_by_task, uploaded = {}, []
    for t in tasks:
        res = []
        for local, mount in fixtures.materialize(t, fixtures_dir):
            f = client.beta.files.upload(file=local)
            uploaded.append(f.id)
            res.append({"type": "file", "file_id": f.id, "mount_path": mount})
        resources_by_task[t.id] = res

    (out / "run.json").write_text(json.dumps({
        "label": args.label, "k": args.k, "agent_id": args.agent_id, "env_id": args.env_id,
        "started": datetime.now(timezone.utc).isoformat(),
        "tasks": {t.id: t.repo_hash for t in tasks},
    }, indent=2), encoding="utf-8")

    lock = threading.Lock()
    results_path = out / "results.jsonl"
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(run_trial, client, t, i, resources_by_task[t.id],
                            args.agent_id, args.env_id, args.label, out / "transcripts")
                for t in tasks for i in range(1, args.k + 1)
            ]
            for fut in as_completed(futures):
                row = fut.result()
                with lock:
                    with results_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, default=str) + "\n")
                    done += 1
                v = (row["tickets"][0].get("verdict") if row["tickets"] and isinstance(row["tickets"][0], dict) else None)
                print(f"[{done}/{n_sessions}] {row['task_id']} trial {row['trial']}: "
                      f"verdict={v} tickets={len(row['tickets'])} "
                      f"{row['metrics']['latency_s']}s" + (f" ERROR={row['error']}" if row["error"] else ""))
    finally:
        for fid in uploaded:
            try:
                client.beta.files.delete(fid)
            except Exception:
                pass
    print(f"\nSaved raw results to {out}\nNext: python grade.py {out}")


if __name__ == "__main__":
    main()
