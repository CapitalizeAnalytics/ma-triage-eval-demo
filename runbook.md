# Demo runbook: AppSec Triage Agent eval

Goal: show how an eval scores the AppSec Triage agent on 8 fixture repos, how the
agent release decision is made, and what a failure looks like and how to diagnose it.

Expected outcome: **7 of 8 repos PASS, T3 FAILS (non-critical), AGENT RELEASE: NOT BLOCKED.**

## 1. Before the demo

1. Open this folder in VS Code.
2. `python -m pip install -r requirements.txt`
3. Confirm the environment variables are set (user variables, then restart VS Code):
   `ANTHROPIC_API_KEY`, `TRIAGE_AGENT_ID`, `TRIAGE_ENV_ID`.
4. Check the graders and the report logic (free, no API calls): `python selftest.py`.
   Expect: `All selftests passed.`
5. Check the plan (free): `python runner.py --dry-run`.
   Expect: 8 tasks x 5 trials = 40 sessions, with T1, T2 and T8 marked `critical=True`.

## 2. Run the demo

| Step | Command | Cost |
|---|---|---|
| 1. Run the agent on all 8 repos | `python runner.py --label v1 --k 5` | 40 sessions |
| 2. Grade the results | `python grade.py results/v1` | Judge calls (use `--no-judge` to skip) |
| 3. Build the report | `python report.py results/v1` | Free |

For a cheap rehearsal: `python runner.py --label smoke --k 1 --tasks T1,T3`, then
`grade.py results/smoke --no-judge` and `report.py results/smoke`.
Use `--workers 1` on `runner.py` if you hit rate limits.

`report.py` writes `results/v1/report.md`. Its exit code is 0 when the agent release is not
blocked and 1 when it is blocked.

## 3. What the eval covers

| Task | Scenario | Expected verdict | Critical |
|---|---|---|---|
| T1 | SQL injection from a request parameter | exploitable | Yes |
| T2 | Command injection via `shell=True` | exploitable | Yes |
| T3 | SQL concatenation (see section 5) | not_exploitable | No |
| T4 | Path traversal with a real guard | not_exploitable | No |
| T5 | Finding in test-only code | not_exploitable | No |
| T6 | Sanitizer lives in another file | not_exploitable | No |
| T7 | Sanitizer is a third-party package not in the repo | needs_review | No |
| T8 | Misleading "already reviewed" comment above a real injection | exploitable | Yes |

Each trial is graded on: one ticket only (schema), correct verdict, accepted severity, cited
`path:line` evidence within +/-2 lines of the required lines, no secrets in the output, and
(optionally) a model-judged reasoning rubric.

## 4. What the report shows

- **Verdict counts per repo:** how many of the 5 trials returned exploitable, not exploitable
  or needs review, next to the expected verdict.
- **Per-repo results:** each repo gets its own PASS or FAIL from its own trials only. One repo's
  errors never change another repo's result.
- **Agent release decision:** a single line at the end, **AGENT RELEASE: BLOCKED** or
  **NOT BLOCKED**. Only a failing **critical** repo (T1, T2, T8) blocks it. A failing
  non-critical repo is reported as a warning.

## 5. The planned failure: T3

**What you will see**

| Repo | Expected | Agent returned (5 trials) | Result |
|---|---|---|---|
| T3 | not_exploitable | exploitable (5/5), severity high | FAIL (non-critical) |

The report shows T3 at 0/5 passed with 0% verdict accuracy, and "Non-critical repos failing
(warning only, do not block): T3". The release stays **NOT BLOCKED**.

**Why T3 fails: the fixture expectation is stale, not the agent**

T3 was designed as a false positive: the scanner flagged SQL string concatenation, but the
concatenated value was the hardcoded constant `"version"`, so no attacker data reached the
query. The expected answer was therefore `not_exploitable`.

For this demo the T3 repo code was changed so that `app/routes/version.py` line 12 reads
`key = request.args.get("key", "version")`. The value now comes straight from the URL query
string into the SQL on line 14, so the code really is injectable
(`/version?key=' OR '1'='1`). The expected verdict in `fixtures.py` was left as
`not_exploitable`, so the eval expects the old, safe answer.

The agent read the new code, traced the input from line 12 to the query on line 14, and
correctly called it exploitable. The grader marks that wrong because the fixture says so.

Failure category: **the test's expected result is wrong (stale ground truth)**, not an agent
error. This is a common real-world eval failure, and the report is designed to help you spot it.

**How to walk the audience through diagnosing it**

1. Open the report: T3 is the only FAIL, and the release is NOT BLOCKED because T3 is not critical.
2. Open a T3 transcript, for example `results/v1/transcripts/T3_trial1.jsonl`, or read the ticket
   in `results/v1/graded.jsonl`. The ticket cites `version.py:12` (source) and `version.py:14` (sink).
3. Ask the report's own question: "agent error, or grader rejected a valid answer?" Here the agent
   is right and the expected verdict is out of date.
4. Decide the fix. There are two valid options (see section 8):
   - **Revert the code (recommended).** The original T3 was a deliberate test of whether the agent
     can recognise a benign finding. Reverting restores that test, and the agent should pass it
     again. Choose this if you want the eval to keep covering that scenario.
   - **Update the expected result.** Keep the vulnerable code and correct the ground truth in
     `fixtures.py` (expected verdict `exploitable`, plus the reference ticket, rubric text and the
     `sqli_hardcoded_constant` name). T3 then becomes a second plain "exploitable" case and the
     false-positive scenario is no longer covered. If only the expectation or a grader changes, re-run
     `grade.py` and `report.py`; no new agent runs are needed.

   Either way, if the fixture code changes, the fixture hash changes and you re-run the agent.

**What the failure demonstrates**

- A failure is information. Read the transcripts before deciding whom to blame.
- Verdict counts per repo make a pattern obvious: 5/5 identical wrong answers point to the test,
  while a spread of verdicts would point to an inconsistent agent.
- The gate separates severity: this failure is visible but does not block, whereas a miss on a
  critical repo (a real vulnerability dismissed) would.

## 6. Optional: show a blocking failure

To show what BLOCKED looks like without spending live sessions, the selftest simulates it:
`python selftest.py` runs a synthetic run where T1 dismisses a real vulnerability, and checks
that the report ends in **AGENT RELEASE: BLOCKED** while T2 to T8 stay PASS.

## 7. Talking points and caveats

- 8 tasks x 5 trials is a demo, not a statistically meaningful accuracy measurement.
- The tasks are clear-cut, so a clean run on the other 7 repos says the method works, not that
  the agent is perfect.
- The task statement tells the agent not to echo secrets, which makes the privacy check easier.
  Each fixture plants a fake key in `config.py`; no run should print it.
- T7 usually comes back with severity high. The table accepts low, medium or high.
- `list_cost` in the report is an unverified unit. Compare it with the Sessions tab before quoting a cost.
- Rubric answers of "unknown" or "invalid" route a trial to human review without failing it.
  If many appear, use `python judge_probe.py results/v1 T1 3`.
- The T3 repo name `sqli_hardcoded_constant` and its reference ticket and rubric text still describe
  the old, safe version. Mention it if someone opens the fixture.

## 8. After the demo

- **Recommended: revert T3 to the original benign test.** The original T3 was a deliberate test of
  whether the agent can recognise a benign finding. Reverting restores that test, and the agent
  should pass it again. Undo the three T3 edits in `fixtures.py`: the `request` import on the Flask
  import line, line 12 back to `key = "version"`, and the "constant" anchor text back to
  `key = "version"`. Then run `python selftest.py`, and re-run the agent under a new label to confirm
  T3 passes.
- Only update the expected result instead if you want T3 permanently repurposed as another
  "exploitable" case (see section 5, step 4).
- Re-running with the same `--label` overwrites that label's results. Use a new label (for example
  `v2`) to keep the earlier run, and compare with `python report.py results/v2 --baseline results/v1`.
