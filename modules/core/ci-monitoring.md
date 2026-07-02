### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running
- `verify-launched-work-liveness.md` — the general form: ANY launched job (not just `gh` runs) must be polled for liveness with a death/timeout branch — a dead process sends no "done", so a success-only wait hangs forever

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.**

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state — success or failure). Do NOT use `gh run watch` — it polls every 3 seconds and causes GitHub API rate limiting on long runs. Instead, run `gh run view` in the background with a reasonable sleep: `sleep 300 && gh run view <run-id>`. Do NOT spam empty "Waiting" messages.

**Pick a monitoring mechanism that SURVIVES session events — a bare `run_in_background` poll does NOT.** A detached `run_in_background` bash poll (`sleep N && gh run view`) is **silently KILLED (SIGTERM) on context compaction** — which fires as the conversation grows, i.e. exactly during a long CI wait — and on session end, with **NO re-invocation**: the task just disappears and CI monitoring dies unnoticed. This is confirmed CC behavior (the live-observed "background polly ma harness zabíja / background polls keep getting killed by session events"; corroborated by anthropics/claude-code #25188 compaction-kills-background, #43944 session-end-orphan). So do NOT rely on a detached background poll for a long wait. The robust options, in order:

- **Foreground bounded poll loop — the DEFAULT for CI.** Repeat `sleep 300 && gh run view <id> --json status,conclusion,jobs` as FOREGROUND Bash calls (each well under the 10-min tool cap), read each return, until a terminal state. There is NO background task to kill, so it survives compaction (it runs in-turn). It ties up the session during the wait — fine for CI, where you are waiting on the run anyway.
- **`ScheduleWakeup` with a PLAIN prompt — for a very long wait you want to free the session for.** A scheduled re-invoke survives because it is not an in-flight process. **NEVER pass a slash command as the wakeup prompt** (CC #54086 re-fires the slash command → duplicate runs); use plain text ("check the erp-test rebuild status + verify").
- **`Monitor` / `/loop` / Cloud Routines** where they fit — `Monitor` streams output live (better than a bare poll, still session-scoped); Cloud Routines run on Anthropic infra (survive everything) when configured.

The only hard requirements: (1) monitor until EVERY job reaches a terminal state, (2) the result must come back to your session so you react in-conversation, (3) never claim done while a run is still going. Notifications fire on their own — the mobile app surfaces "waiting on you", Discord idle pings fire when you go idle.

**CRITICAL — `run_in_background` CI-polling is FRAGILE in the main session and outright BROKEN in a subagent.** In the main session it re-invokes you on completion ONLY if no compaction / session-end intervenes first — and across a long wait one WILL (compaction SIGTERMs it silently, above), so prefer the foreground bounded loop or `ScheduleWakeup`-with-a-plain-prompt above, not a detached poll. **A SUBAGENT (e.g. an `autopilot-worker`) that launches a `run_in_background` CI poll and then ends its turn TERMINATES** — a subagent with no pending FOREGROUND tool call is returned as "completed", and the detached background task's completion fires to the PARENT (supervisor) session, NOT to the now-gone subagent. So the subagent silently dies after every push (this was the single dominant autopilot-worker failure — ~40% of workers). **Inside a subagent, wait FOREGROUND** — a blocking `gh run view <id>` poll loop (each Bash call well under the 10-min tool cap — e.g. `sleep 300`, repeated until terminal), which keeps the subagent alive — **or, for a long / multi-stage wait, hand the run-id back to the supervisor and RETURN** (the supervisor is the long-lived component that survives the wait via `run_in_background` + re-invocation). Applies to all rewordings and semantic equivalents.
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run AND any release/deploy workflows until they complete

**ALL jobs must pass — not just lint and test.** Deploy jobs, e2e jobs, release jobs — everything in the pipeline must be green. If a deploy job is "skipped" or still running, you are NOT done. If a job shows as green but others are still pending, you are NOT done. Wait for the entire workflow run to reach a terminal state.

**Never stop at partial green.** Celebrating "lint and tests pass!" while the deploy job is failing or pending is a critical error.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.

**Never ask the user "want me to wait?"** — the answer is always yes. CI monitoring is not optional. Just do it.

**Never blindly rerun failed CI.** If a job fails, investigate WHY it failed (`gh run view --log-failed`). Rerunning without fixing the root cause is wasting time — if it failed once, it will fail again. One rerun is acceptable to rule out transient issues. Two reruns of the same failure means the problem is real — investigate and fix.

**Self-hosted runners are YOUR responsibility.** If you set up or configured a local runner (GitHub Actions self-hosted, Playwright on LAN, etc.) and it has issues (offline, stale, misconfigured), YOU must diagnose and fix it. Do not ask the user to fix runner infrastructure you maintain. SSH to the runner machine, check logs, restart the service, fix the config.
