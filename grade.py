#!/usr/bin/env python3
"""Apply graders to a run's saved results (results.jsonl -> graded.jsonl).

Re-run this any time you fix a grader; it never calls the agent.

Usage:
    python grade.py results/v1               # code graders + model-based rubric
    python grade.py results/v1 --no-judge    # code graders only (free, offline)
"""
import argparse
import json
from pathlib import Path

import fixtures
import graders

DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"  # deliberately not the agent's model


def grade_rows(rows, judge=None):
    tasks = {t.id: t for t in fixtures.get_tasks()}
    graded = []
    for row in rows:
        result = graders.grade_trial(tasks[row["task_id"]], row, judge=judge)
        graded.append({**row, **result})
    return graded


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    judge = None
    if not args.no_judge:
        from anthropic import Anthropic
        judge = (Anthropic(), args.judge_model)

    graded = grade_rows(rows, judge)
    with (run_dir / "graded.jsonl").open("w", encoding="utf-8") as fh:
        for g in graded:
            fh.write(json.dumps(g, default=str) + "\n")
    passed = sum(g["passed"] for g in graded)
    print(f"Graded {len(graded)} trials: {passed} passed. Wrote {run_dir / 'graded.jsonl'}")
    print(f"Next: python report.py {run_dir}")


if __name__ == "__main__":
    main()
