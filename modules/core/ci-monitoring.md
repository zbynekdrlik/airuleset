### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running
- `verify-launched-work-liveness.md` — the general form: ANY launched job must be polled with a death/timeout branch — a dead process sends no "done", so a success-only wait hangs forever

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.** **ALL jobs must pass — not just lint and test.** Deploy, e2e and release jobs count. **Never stop at partial green.**

**Wait INSIDE ONE Bash call — never one tool call per poll.** Do NOT use `gh run watch`. Hook-enforced (#118): `block-ci-poll-repeat.sh` HARD-BLOCKS the 2nd foreground loop for the same run.

**CRITICAL — a `run_in_background` CI poll is outright BROKEN in a subagent.** A subagent with no pending FOREGROUND tool call is returned as "completed" and TERMINATES; the detached task's completion then fires to the PARENT. Wait FOREGROUND, or hand the run-id back to the supervisor. Hook-enforced (#28): `block-subagent-bg-ci-poll.sh` denies the launch, `subagent-stop-check-bg-work.sh` blocks the stop while your own background work is live.

If any job fails: `gh run view <run-id> --log-failed` — investigate and fix the root cause immediately. **Never blindly rerun.** One rerun is acceptable to rule out a transient; two reruns of the same failure means the problem is real.

The full foreground bounded poll loop recipe, long-wait background waiter recipe (#29193), deploy/version-live watch classifier, recovery protocol, memory-pressure REAP mitigation, and fail-fast on job-level failure are in the situational companion `skills/ci-monitoring-deep/DEEP.md` — loaded automatically on `gh run view`/`list`/`watch`/`gh pr checks` commands. History + rationale: `.claude/rules-reference/ci-monitoring-history.md` (#859).
