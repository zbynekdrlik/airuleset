### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running
- `verify-launched-work-liveness.md` — the general form: ANY launched job (not just `gh` runs) must be polled for liveness with a death/timeout branch — a dead process sends no "done", so a success-only wait hangs forever

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.**

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state — success or failure). Do NOT use `gh run watch` — it polls every 3 seconds and causes GitHub API rate limiting on long runs. Poll from inside ONE bounded Bash call (the loop shape below), not one tool call per poll.

**Pick a monitoring mechanism that SURVIVES session events — a bare `run_in_background` poll does NOT.** A detached `run_in_background` bash poll (`sleep N && gh run view`) is **silently KILLED (SIGTERM) on context compaction** — which fires as the conversation grows, i.e. exactly during a long CI wait — and on session end, with **NO re-invocation**: the task just disappears and CI monitoring dies unnoticed. This is confirmed CC behavior (the live-observed "background polly ma harness zabíja / background polls keep getting killed by session events"; corroborated by anthropics/claude-code #25188 compaction-kills-background, #43944 session-end-orphan). So do NOT rely on a detached background poll for a long wait. The robust options, in order:

- **Foreground bounded poll loop — the DEFAULT for CI. The WAITING happens INSIDE ONE Bash call, never one tool call per poll.** Use a single FOREGROUND Bash call carrying a bounded shell loop that returns only on a terminal state (or its own budget), e.g.:

  ```bash
  DEADLINE=$((SECONDS + ${AIRULESET_POLL_BUDGET_S:-100}))
  for i in $(seq 1 18); do
    s=$(gh run view <id> --json status,conclusion --jq '.status+" "+(.conclusion//"")')
    case "$s" in completed*) echo "TERMINAL: $s"; break;; esac
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
      echo "POLL BUDGET REACHED (not yet terminal): $s"; break
    fi
    sleep 30
  done
  ```

  Set the Bash tool `timeout` near its 600000 ms cap so one call covers ~9 minutes of waiting; repeat the call only if the run is still going. There is NO background task to kill, so it survives compaction (it runs in-turn).

  **The loop bounds ITSELF via `SECONDS` (bash's own elapsed-time counter) — never trust the Bash tool `timeout` PARAMETER alone (#90).** That parameter is easy to forget, since the loop body looks complete without it — live-hit 2026-07-26: a poll written exactly per this shape, with no `timeout` param raised, got SIGTERM'd (exit 143) at the harness's own **default (observed: 120000 ms / 2 minutes)**, mid-poll, with NO output — three tool calls where one was intended, because the dead call had to be silently retried twice before the timeout finally got set correctly. `AIRULESET_POLL_BUDGET_S` (default **100** — safely under that observed 120 s default) makes the loop exit CLEANLY and print its last-known status BEFORE an unset/forgotten tool `timeout` ever SIGTERMs it — a graceful "not yet terminal" beats a silent kill every time. Raising the tool's own `timeout` param is STILL required to cover the full ~9 minutes usefully — no in-process logic can out-run an external SIGTERM — so when you DO raise it near the 600000 ms cap, also raise the budget to match (e.g. `AIRULESET_POLL_BUDGET_S=540 bash -c '...'`, or export it inline before the loop). A PreToolUse nudge (`nudge-poll-loop-timeout.sh`, never blocking — the poll must always pass) reminds you to raise the tool `timeout` whenever it sees a bounded sleep/poll loop shape without one.

  **Why the loop must live inside the call: every returned tool call is another TURN, and every turn re-sends the WHOLE conversation context.** One poll per tool call turns a normal CI wait into hundreds of full-context turns — measured 2026-07-26 on the gatekeeper box: 121 `gh run view` + 116 `gh run list` calls in the MAIN agent, each one paying for the entire context again. Same total waiting, an order of magnitude fewer turns. Do NOT spam empty "Waiting" messages between polls either — that is the same waste without even a status read.
- **`Monitor` / `/loop` / Cloud Routines** where they fit — `Monitor` streams output live (better than a bare poll, still session-scoped); Cloud Routines run on Anthropic infra (survive everything) when configured.

The only hard requirements: (1) monitor until EVERY job reaches a terminal state, (2) the result must come back to your session so you react in-conversation, (3) never claim done while a run is still going. Notifications fire on their own — the mobile app surfaces "waiting on you", Discord idle pings fire when you go idle.

**CRITICAL — `run_in_background` CI-polling is FRAGILE in the main session and outright BROKEN in a subagent.** In the main session it re-invokes you on completion ONLY if no compaction / session-end intervenes first — and across a long wait one WILL (compaction SIGTERMs it silently, above), so prefer the foreground bounded loop above, not a detached poll. **A SUBAGENT (e.g. an `autopilot-worker`) that launches a `run_in_background` CI poll and then ends its turn TERMINATES** — a subagent with no pending FOREGROUND tool call is returned as "completed", and the detached background task's completion fires to the PARENT (supervisor) session, NOT to the now-gone subagent. So the subagent silently dies after every push (this was the single dominant autopilot-worker failure — ~40% of workers). **Inside a subagent, wait FOREGROUND** — a blocking `gh run view <id>` poll loop whose sleeping happens INSIDE one Bash call (the bounded `for`/`sleep` shape above, tool `timeout` near the 600000 ms cap), repeated only while the run is still going, which keeps the subagent alive without paying a full-context turn per poll — **or, for a long / multi-stage wait, hand the run-id back to the supervisor and RETURN** (the supervisor is the long-lived component that survives the wait via `run_in_background` + re-invocation; TaskStop any background task you launched BEFORE returning). Hook-enforced since 2026-07-24 (#28): `block-subagent-bg-ci-poll.sh` denies a background CI poll launched from subagent context, and `subagent-stop-check-bg-work.sh` blocks a subagent from ending its turn while ANY launched background task (Bash / Monitor / child agent) is still in flight. Applies to all rewordings and semantic equivalents.
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run AND any release/deploy workflows until they complete

**ALL jobs must pass — not just lint and test.** Deploy jobs, e2e jobs, release jobs — everything in the pipeline must be green. If a deploy job is "skipped" or still running, you are NOT done. If a job shows as green but others are still pending, you are NOT done. Wait for the entire workflow run to reach a terminal state.

**Never stop at partial green.** Celebrating "lint and tests pass!" while the deploy job is failing or pending is a critical error.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.

**Never ask the user "want me to wait?"** — the answer is always yes. CI monitoring is not optional. Just do it.

**Never blindly rerun failed CI.** If a job fails, investigate WHY it failed (`gh run view --log-failed`). Rerunning without fixing the root cause is wasting time — if it failed once, it will fail again. One rerun is acceptable to rule out transient issues. Two reruns of the same failure means the problem is real — investigate and fix.

**Self-hosted runners are YOUR responsibility.** If you set up or configured a local runner (GitHub Actions self-hosted, Playwright on LAN, etc.) and it has issues (offline, stale, misconfigured), YOU must diagnose and fix it. Do not ask the user to fix runner infrastructure you maintain. SSH to the runner machine, check logs, restart the service, fix the config.
