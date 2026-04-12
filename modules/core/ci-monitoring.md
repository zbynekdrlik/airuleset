### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.**

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state — success or failure). Do NOT use `gh run watch` — it polls every 3 seconds and causes GitHub API rate limiting on long runs. Instead, run `gh run view` in the background with a reasonable sleep: `sleep 300 && gh run view <run-id>`. Do NOT spam empty "Waiting" messages.
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run AND any release/deploy workflows until they complete

**ALL jobs must pass — not just lint and test.** Deploy jobs, e2e jobs, release jobs — everything in the pipeline must be green. If a deploy job is "skipped" or still running, you are NOT done. If a job shows as green but others are still pending, you are NOT done. Wait for the entire workflow run to reach a terminal state.

**Never stop at partial green.** Celebrating "lint and tests pass!" while the deploy job is failing or pending is a critical error.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.

**Never ask the user "want me to wait?"** — the answer is always yes. CI monitoring is not optional. Just do it.

**Never blindly rerun failed CI.** If a job fails, investigate WHY it failed (`gh run view --log-failed`). Rerunning without fixing the root cause is wasting time — if it failed once, it will fail again. One rerun is acceptable to rule out transient issues. Two reruns of the same failure means the problem is real — investigate and fix.

**Self-hosted runners are YOUR responsibility.** If you set up or configured a local runner (GitHub Actions self-hosted, Playwright on LAN, etc.) and it has issues (offline, stale, misconfigured), YOU must diagnose and fix it. Do not ask the user to fix runner infrastructure you maintain. SSH to the runner machine, check logs, restart the service, fix the config.
