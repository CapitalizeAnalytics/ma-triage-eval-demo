#!/usr/bin/env python3
"""Aggregate graded trials into metrics, success criteria and a release gate.

Usage:
    python report.py results/v1
    python report.py results/v2 --baseline results/v1    # show regressions
Gates are per repo (one fixture repo per task); a failure in one repo never blocks another.
Exit code 0 = agent release not blocked, 1 = blocked by a failing critical repo (CI friendly).
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

import fixtures
from graders import normalize_verdict

VERDICTS = ("exploitable", "not_exploitable", "needs_review")
ACCURACY_TARGET = 0.90      # illustrative thresholds from the runbook
CONSISTENCY_TARGET = 0.95


def load_rows(run_dir):
    path = Path(run_dir) / "graded.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run grade.py first.")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _verdict(row):
    tickets = row.get("tickets") or []
    if tickets and isinstance(tickets[0], dict):
        return normalize_verdict(tickets[0].get("verdict")) or "none"
    return "none"


def task_stats(rows):
    tasks = {t.id: t for t in fixtures.get_tasks()}
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    stats = {}
    for tid, trials in sorted(by_task.items()):
        verdicts = [_verdict(r) for r in trials]
        modal = max(set(verdicts), key=verdicts.count)
        passes = [bool(r["passed"]) for r in trials]
        stats[tid] = {
            "task": tasks[tid], "n": len(trials), "passed": sum(passes),
            "pass_at_k": any(passes), "pass_pow_k": all(passes),
            "verdicts": sorted(set(verdicts)),
            "counts": {v: verdicts.count(v) for v in VERDICTS},
            "other": sum(v not in VERDICTS for v in verdicts),
            "agreement": verdicts.count(modal) / len(trials),
            "mean_score": statistics.mean(r["score"] for r in trials),
            "trials": trials,
        }
    return stats


def repo_gate(stat):
    """Release gate for one repo (one task). Reads only that repo's trials."""
    trials = stat["trials"]
    n = len(trials)
    accuracy = sum(r["grades"]["verdict_match"]["passed"] for r in trials) / n
    privacy_fails = sum(not r["grades"]["privacy_check"]["passed"] for r in trials)
    citation_fails = sum(not r["grades"]["evidence_check"].get("all_exist", False) for r in trials)
    gate = {
        # a critical repo must pass every trial; a non-critical one is held to the accuracy target
        "critical_ok": stat["pass_pow_k"] if stat["task"].critical else True,
        "accuracy_ok": accuracy >= ACCURACY_TARGET,
        "privacy_ok": privacy_fails == 0,
        "citations_ok": citation_fails == 0,
    }
    checks_ok = all(gate.values())
    critical = bool(stat["task"].critical)
    # only a repo flagged critical can block the release; other repos report FAIL as a warning
    gate["checks_ok"] = checks_ok
    gate["critical"] = critical
    gate["release"] = checks_ok or not critical
    # repo status describes the repo's own result; only the agent as a whole is released or blocked
    gate["status"] = "PASS" if checks_ok else ("FAIL (critical)" if critical else "FAIL (non-critical)")
    return {"accuracy": accuracy, "privacy_fails": privacy_fails, "citation_fails": citation_fails,
            "errors": sum(bool(r.get("error")) for r in trials), "gate": gate}


def evaluate(rows, stats):
    n = len(rows)
    tasks = {t.id: t for t in fixtures.get_tasks()}
    accuracy = sum(r["grades"]["verdict_match"]["passed"] for r in rows) / n
    privacy_fails = sum(not r["grades"]["privacy_check"]["passed"] for r in rows)
    citation_fails = sum(not r["grades"]["evidence_check"].get("all_exist", False) for r in rows)
    exploitable = [r for r in rows if tasks[r["task_id"]].expected_verdict == "exploitable"]
    benign = [r for r in rows if tasks[r["task_id"]].expected_verdict == "not_exploitable"]
    dismissed = sum(_verdict(r) == "not_exploitable" for r in exploitable)
    escalated = sum(_verdict(r) != "not_exploitable" for r in benign)
    consistency = statistics.mean(s["agreement"] for s in stats.values())
    repos = {tid: repo_gate(s) for tid, s in stats.items()}
    # repos are scored independently; the agent release is blocked only by a failing critical repo
    gate = {"repos": {tid: g["gate"] for tid, g in repos.items()},
            "release": all(g["gate"]["release"] for g in repos.values())}
    return {
        "n": n, "accuracy": accuracy, "privacy_fails": privacy_fails,
        "citation_fails": citation_fails, "dismissed": dismissed, "n_exploitable": len(exploitable),
        "escalated": escalated, "n_benign": len(benign), "consistency": consistency,
        "mean_score": statistics.mean(r["score"] for r in rows),
        "errors": sum(bool(r.get("error")) for r in rows), "gate": gate, "repos": repos,
    }


def _pct(x):
    return f"{100 * x:.0f}%"


def _ok(b):
    return "PASS" if b else "FAIL"


def build_report(rows, label="run", baseline_rows=None):
    stats = task_stats(rows)
    ev = evaluate(rows, stats)
    k = max(s["n"] for s in stats.values())
    out = [f"# Eval report: {label}", "",
           f"{ev['n']} trials across {len(stats)} tasks (k={k}); {ev['errors']} trial(s) errored.", "",
           "## Per task", "",
           "| Task | Category | Crit | Suite | Passed | pass@k | pass^k | Verdicts seen | Mean score |",
           "|---|---|---|---|---|---|---|---|---|"]
    for tid, s in stats.items():
        t = s["task"]
        out.append(f"| {tid} {t.name} | {t.category} | {'Y' if t.critical else ''} | {t.suite} | "
                   f"{s['passed']}/{s['n']} | {'Y' if s['pass_at_k'] else 'N'} | "
                   f"{'Y' if s['pass_pow_k'] else 'N'} | {', '.join(s['verdicts'])} | {s['mean_score']:.2f} |")
    out += ["", "## Verdict counts per repo", "",
            "Counts of the verdict the agent returned across that repo's trials.", "",
            "| Repo | Expected | Exploitable | Not exploitable | Needs review | No/invalid verdict |",
            "|---|---|---|---|---|---|"]
    for tid, s in stats.items():
        c = s["counts"]
        out.append(f"| {tid} {s['task'].name} | {s['task'].expected_verdict} | {c['exploitable']} | "
                   f"{c['not_exploitable']} | {c['needs_review']} | {s['other']} |")
    suite_at = sum(s["pass_at_k"] for s in stats.values()) / len(stats)
    suite_pow = sum(s["pass_pow_k"] for s in stats.values()) / len(stats)
    out += ["", f"Suite pass@k: {_pct(suite_at)}   Suite pass^k: {_pct(suite_pow)}   "
                f"Mean score: {ev['mean_score']:.2f}", "",
            "## Success criteria (per repo)", "",
            f"Each repo is gated only on its own trials. Accuracy target >= {_pct(ACCURACY_TARGET)}; "
            "critical repos must pass every trial; citations and secrets are must-pass. "
            "Each repo reports its own PASS/FAIL. Only a failing critical repo blocks the AGENT release; "
            "a non-critical failure is a warning.", "",
            "| Repo | Verdict accuracy | Critical, every trial | Citations exist | Secret leaks | Consistency | Result |",
            "|---|---|---|---|---|---|---|"]
    for tid, s_ in stats.items():
        g = ev["repos"][tid]
        out.append(f"| {tid} {s_['task'].name} | {_pct(g['accuracy'])} ({_ok(g['gate']['accuracy_ok'])}) | "
                   f"{('all passed' if g['gate']['critical_ok'] else 'FAILED') if s_['task'].critical else 'n/a'} | "
                   f"{s_['n'] - g['citation_fails']}/{s_['n']} ({_ok(g['gate']['citations_ok'])}) | "
                   f"{g['privacy_fails']} ({_ok(g['gate']['privacy_ok'])}) | "
                   f"{_pct(s_['agreement'])} | **{g['gate']['status']}** |")
    out += [
            "", "Error direction (scored separately so one cannot hide the other):",
            "(aggregate, informational only; not used for gating)",
            f"- Real vulnerabilities dismissed: {ev['dismissed']}/{ev['n_exploitable']} trials",
            f"- Benign findings escalated: {ev['escalated']}/{ev['n_benign']} trials", ""]

    lat = [r["metrics"]["latency_s"] for r in rows if r.get("metrics")]
    calls = [r["metrics"]["tool_calls"] for r in rows if r.get("metrics")]
    if lat:
        out += ["## Tracked metrics", "",
                f"- Latency per trial: median {statistics.median(lat):.0f}s, p95 {sorted(lat)[min(len(lat) - 1, int(0.95 * len(lat)))]:.0f}s, max {max(lat):.0f}s",
                f"- Tool calls per trial: median {statistics.median(calls):.0f}",
                ]
        usages = [x["metrics"]["usage"] for x in rows if x.get("metrics") and isinstance(x["metrics"].get("usage"), dict)]
        if usages:
            def _tok(u):
                cc = u.get("cache_creation") or {}
                return (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                        + sum(v for v in cc.values() if isinstance(v, (int, float))))
            ins = [_tok(u) for u in usages]
            outs = [u.get("output_tokens", 0) for u in usages]
            out.append(f"- Tokens per trial (median): {statistics.median(ins):,.0f} in (including cache reads), "
                       f"{statistics.median(outs):,.0f} out")
            costs = []
            for u in usages:
                try:
                    costs.append(float((u.get("list_cost") or {}).get("amount")))
                except (TypeError, ValueError):
                    pass
            if costs:
                out.append(f"- list_cost as returned by the API: {sum(costs):g} total across {len(costs)} trials "
                           "(unit not verified; compare with the Sessions tab before quoting)")
        else:
            out.append("- Token and cost totals: see the agent's Sessions tab (sessions are titled 'EVAL <label> ...')")
        out.append("")

    failed = [r for r in rows if not r["passed"]]
    unknown = [r for r in rows if r.get("needs_human_review")]
    out += ["## Read these transcripts before trusting the score", ""]
    if not failed and not unknown:
        out.append("No failed trials. Spot-check a few passes anyway.")
    for r in failed:
        why = "; ".join(f"{n}: {g['detail']}" for n, g in r["grades"].items()
                        if n in ("schema", "verdict_match", "evidence_check", "privacy_check") and not g["passed"])
        out.append(f"- FAILED {r['task_id']} trial {r['trial']} ({r['transcript']}): {why}")
    if unknown:
        dims, by_task, invalid = {}, {}, 0
        for r in rows:
            for d, a in (r["grades"].get("reasoning_rubric", {}).get("answers") or {}).items():
                if a == "unknown":
                    dims[d] = dims.get(d, 0) + 1
                    by_task[r["task_id"]] = by_task.get(r["task_id"], 0) + 1
                elif a == "invalid":
                    invalid += 1
        n = len(rows)
        out.append("- JUDGE UNSURE on " + str(len(unknown)) + f"/{n} trials. 'unknown' answers by rubric question: "
                   + (", ".join(f"{d} {c}/{n}" for d, c in sorted(dims.items())) or "none") + ".")
        if by_task:
            out.append("  By task: " + ", ".join(f"{t} {c}" for t, c in sorted(by_task.items()))
                       + ". If one question dominates, check whether it applies to those tasks before reading individual transcripts.")
        if invalid:
            out.append(f"  WARNING: {invalid} judge replies were empty or not in the requested format (counted as invalid, "
                       "not unknown). Check invalid_raw in graded.jsonl for stop_reason and block types.")
        shown = 0
        for x in rows:
            rub = x["grades"].get("reasoning_rubric", {})
            for d, a in (rub.get("answers") or {}).items():
                why = (rub.get("reasons") or {}).get(d, "")
                if a == "unknown" and why and shown < 6:
                    out.append(f"  Why unsure ({x['task_id']} trial {x['trial']}, {d}): {why}")
                    shown += 1
    out.append("")
    out.append("For each failure decide: agent error, or grader rejected a valid answer? Fix graders, then re-run grade.py.")

    if baseline_rows:
        base_stats = task_stats(baseline_rows)
        base_ev = evaluate(baseline_rows, base_stats)
        out += ["", "## Versus baseline", "",
                f"Overall accuracy {_pct(base_ev['accuracy'])} -> {_pct(ev['accuracy'])} (informational)", ""]
        changed = [f"{tid} {base_ev['gate']['repos'][tid]['status']} -> {g['status']}"
                   for tid, g in ev["gate"]["repos"].items()
                   if tid in base_ev["gate"]["repos"] and base_ev["gate"]["repos"][tid]["status"] != g["status"]]
        out += [f"- Repo gates changed: {', '.join(changed) or 'none'}"]
        regress = [tid for tid, s in stats.items()
                   if tid in base_stats and base_stats[tid]["pass_pow_k"] and not s["pass_pow_k"]]
        recover = [tid for tid, s in stats.items()
                   if tid in base_stats and not base_stats[tid]["pass_pow_k"] and s["pass_pow_k"]]
        out.append(f"- Regressed (pass^k Y -> N): {', '.join(regress) or 'none'}")
        out.append(f"- Improved (pass^k N -> Y): {', '.join(recover) or 'none'}")

    out += ["", "## Agent release decision", "",
            "| Repo | Critical | critical_ok | accuracy_ok | privacy_ok | citations_ok | Result |",
            "|---|---|---|---|---|---|---|"]
    for tid, g in ev["gate"]["repos"].items():
        out.append(f"| {tid} {stats[tid]['task'].name} | {'Y' if g['critical'] else ''} | {_ok(g['critical_ok'])} | "
                   f"{_ok(g['accuracy_ok'])} | {_ok(g['privacy_ok'])} | {_ok(g['citations_ok'])} | {g['status']} |")
    blocking = [tid for tid, g in ev["gate"]["repos"].items() if not g["release"]]
    warning = [tid for tid, g in ev["gate"]["repos"].items() if not g["checks_ok"] and g["release"]]
    out += [""]
    if warning:
        out.append(f"Non-critical repos failing (warning only, do not block): {', '.join(warning)}")
    if blocking:
        out.append(f"Blocked by failing critical repo(s): {', '.join(blocking)}")
    else:
        out.append("No critical repo failed.")
    out += ["", f"**AGENT RELEASE: {'NOT BLOCKED' if ev['gate']['release'] else 'BLOCKED'}**"]
    return "\n".join(out), ev["gate"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--baseline", help="run dir of an earlier run to compare against")
    args = ap.parse_args()
    rows = load_rows(args.run_dir)
    base = load_rows(args.baseline) if args.baseline else None
    text, gate = build_report(rows, Path(args.run_dir).name, base)
    (Path(args.run_dir) / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    sys.exit(0 if gate["release"] else 1)


if __name__ == "__main__":
    main()
