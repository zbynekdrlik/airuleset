### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.**

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state — success or failure). Do NOT use `gh run watch` — it polls every 3 seconds and causes GitHub API rate limiting on long runs. Instead, run `gh run view` in the background with a reasonable sleep: `sleep 300 && gh run view <run-id>`. Do NOT spam empty "Waiting" messages.

**Do NOT use `/loop`, `CronCreate`, or any scheduled/recurring polling for CI monitoring.** These create 30-minute gaps where failures go unnoticed, and they interfere with Discord idle notifications. Use a single `sleep N && gh run view` background command — this is the ONLY acceptable method.

**Do NOT write custom bash monitor scripts** (e.g. `/tmp/main-monitor.sh`, `while true; do ... sleep; done`). These detach from Claude's session, don't return results to Claude, and don't trigger notifications. Only use the Bash tool with `run_in_background: true` running `sleep N && gh run view <run-id>` — this returns the result to Claude's conversation when done, allowing Claude to react and correctly trigger the idle notification.

**The ONE correct monitoring pattern:**
```
Bash(command: "sleep 300 && gh run view <run-id> --json status,conclusion,jobs", run_in_background: true)
```
When done, Claude reads the output via `BashOutput`, acts on it, and goes idle → Discord fires. No other pattern is acceptable.

**For long bg waits where the agent might stop before completion (>5min):** wrap the command with `notify-on-completion.sh` so the Discord ping fires even if the agent has already stopped (Claude Code's idle_prompt only fires while the session is active):
```
Bash(command: "bash ~/devel/airuleset/hooks/notify-on-completion.sh 'sleep 600 && gh run view <run-id> --json status,conclusion,jobs' 'CI run <run-id>'", run_in_background: true)
```
The wrapper runs the command in foreground inside the bg shell, fires Discord on completion (✅ on exit 0, ❌ otherwise) with a tail of the output, and returns the result to Claude via BashOutput.
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run AND any release/deploy workflows until they complete

**ALL jobs must pass — not just lint and test.** Deploy jobs, e2e jobs, release jobs — everything in the pipeline must be green. If a deploy job is "skipped" or still running, you are NOT done. If a job shows as green but others are still pending, you are NOT done. Wait for the entire workflow run to reach a terminal state.

**Never stop at partial green.** Celebrating "lint and tests pass!" while the deploy job is failing or pending is a critical error.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.

**Never ask the user "want me to wait?"** — the answer is always yes. CI monitoring is not optional. Just do it.

**Never blindly rerun failed CI.** If a job fails, investigate WHY it failed (`gh run view --log-failed`). Rerunning without fixing the root cause is wasting time — if it failed once, it will fail again. One rerun is acceptable to rule out transient issues. Two reruns of the same failure means the problem is real — investigate and fix.

**Self-hosted runners are YOUR responsibility.** If you set up or configured a local runner (GitHub Actions self-hosted, Playwright on LAN, etc.) and it has issues (offline, stale, misconfigured), YOU must diagnose and fix it. Do not ask the user to fix runner infrastructure you maintain. SSH to the runner machine, check logs, restart the service, fix the config.
