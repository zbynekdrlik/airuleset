### CI Pipeline Monitoring

**Context gate — related rules you MUST also apply:**
- `ci-push-discipline.md` — local checks before push, batch fixes, one push per cycle
- `complete-planned-work.md` — CI monitoring is part of the plan; skipping it = incomplete work
- `completion-report.md` — never send completion report while CI is still running
- `verify-launched-work-liveness.md` — the general form: ANY launched job must be polled with a death/timeout branch — a dead process sends no "done", so a success-only wait hangs forever

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running. **This includes brainstorming, issue selection, or any "next task" planning — NOTHING starts until CI reaches terminal state.**

**ALL jobs must pass — not just lint and test.** Deploy, e2e and release jobs count: a "skipped" or still-queued deploy job means you are NOT done. **Never stop at partial green.** This is the module's load-bearing line, and it is the one thing a model does NOT do unprompted — shown a run still in progress with lint and test passing, a rules-free model reported *"CI is green — no further action needed"* in 8 of 8 probes (#110, 2026-07-27).

1. Check status: `gh run list --limit 3`
2. **Wait for the run INSIDE ONE Bash call — never one tool call per poll.** Every returned tool call is another TURN that re-sends the WHOLE conversation for one line of log (measured on the gatekeeper box — the count is in the playbook). Do NOT spam empty "Waiting" messages between polls either. **Do NOT use `gh run watch`**, which is exactly what an unprompted model reaches for (8/8, #110) and is measurably the wrong tool: watching one real run cost **71 API calls and 9.7 KB of output per minute** — ≈4100/h against GitHub's 5000/h limit — and `--interval 30` only brings that to ~2200/h because it re-polls every job. `gh run view --json status,conclusion` costs 1 call and one line per poll.

**Pick the mechanism BY EXPECTED WAIT LENGTH (#107) — the two cost turns very differently.** A **short wait** (up to ~10 min) fits ONE foreground bounded poll loop. A **long wait** (tens of minutes, a multi-hour suite, a multi-stage pipeline) must NOT be driven by repeating that loop every ~9 minutes — each repeat is a separate full-context TURN, five in a row for one 2-hour run: *"preco monitoring nespravis tak aby si kazdych 9min nemusel spravit dalsi plateny token tah!!"* Launch ONE background waiter instead.

- **Foreground bounded poll loop — the short-wait default:**

  ```bash
  DEADLINE=$((SECONDS + ${AIRULESET_POLL_BUDGET_S:-100}))
  for i in $(seq 1 18); do
    s=$(gh run view <id> --json status,conclusion,jobs --jq 'if .status=="completed" then "TERMINAL "+.status+" "+(.conclusion//"") elif ([.jobs[]?|select(.conclusion=="failure" or .conclusion=="timed_out")]|length)>0 then "JOBFAIL "+([.jobs[]?|select(.conclusion=="failure" or .conclusion=="timed_out")]|map(.name)|join(", ")) else "PENDING "+.status end')
    case "$s" in
      "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; break;;
      "JOBFAIL "*) echo "JOB FAILED (run still in progress): ${s#JOBFAIL }"; break;;
    esac
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
      echo "POLL BUDGET REACHED (not yet terminal): ${s#PENDING }"; break
    fi
    sleep 30
  done
  ```

  Raise the Bash tool's own `timeout` near its 600000 ms cap so one call covers ~9 minutes, and raise `AIRULESET_POLL_BUDGET_S` to match (e.g. `AIRULESET_POLL_BUDGET_S=540`). The loop self-bounds on `SECONDS` because that `timeout` parameter is easy to forget: unset, the harness SIGTERMs the call at its own ~120 s default mid-poll with NO output (#90), and a graceful "not yet terminal" beats a silent kill. `nudge-poll-loop-timeout.sh` reminds you; it never blocks. Nothing is detached, so nothing needs recovery. Hook-enforced (#118): the FIRST such loop per run is free, but `block-ci-poll-repeat.sh` HARD-BLOCKS the 2nd and later one for the same run — a loop that came back non-terminal is proof the wait is long — and hands you the background waiter below, run-id already substituted.

- **Long wait — ONE background waiter (`run_in_background: true`), then RECOVER.** It must BLOCK to a terminal state (never a trailing `&` or `nohup`, which returns immediately and fires the notification at once), self-bound on its own budget the way the foreground loop does, and print nothing along the way — so the harness wakes you with exactly ONE task-notification for the whole wait instead of once per ~9-minute chunk:

  ```
  timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c 'while :; do
    s=$(gh run view <id> --json status,conclusion,jobs --jq "if .status==\"completed\" then \"TERMINAL \"+.status+\" \"+(.conclusion//\"\") elif ([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|length)>0 then \"JOBFAIL \"+([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|map(.name)|join(\", \")) else \"PENDING \"+.status end" 2>/dev/null) || s="ERROR"
    case "$s" in
      "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; exit 0 ;;
      "JOBFAIL "*) echo "JOB FAILED (run still in progress): ${s#JOBFAIL }"; exit 0 ;;
    esac
    sleep 60
  done'
  ```

  **Recovery is not optional.** The notification linkage may not survive a compaction boundary: across compaction the OS process usually keeps running but the session's task-handle registry is dropped and recreated (anthropics/claude-code **#29193**, "has repro"). The two issues this rule used to cite described the OPPOSITE failure mode and were wrong — the archaeology is in the playbook, not here. Either way no notification arrives, so on your NEXT turn re-derive from the DURABLE resource (`gh run view <id>` again) rather than trusting that silence means nothing happened, and relaunch ONE fresh waiter if the old one is gone. Never sit on a blind indefinite wait, and never fall back to chunked foreground polling once a long wait is already the right call.

  **A memory-pressure REAP also kills a MAIN-session `run_in_background` waiter** — Claude Code SIGKILLs it on a `memoryPressure` event, in MINUTES, on a memory-tight box (a subagent's bg shell is EXEMPT), so relaunch re-CREATES a dead process, not re-links a handle. Same recovery. `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1` on the CLI env disables it; fuller mechanism + subagent mitigation: `verify-launched-work-liveness`.

  **Fail-fast on job-level failure, inside the SAME poll.** Both shapes above wake the moment ANY job in a still-running multi-job run reports `conclusion=="failure"` or `conclusion=="timed_out"` (a job hitting its own `timeout-minutes` is arguably the likeliest way this scenario happens; a `cancelled` job is deliberately excluded — either a cascade from a sibling failure, already reported via that sibling, or a genuine `gh run cancel` where waking would be misleading) — not just when the whole run reaches `completed` — via the SAME single `gh run view` call (`,jobs` added to `--json`, no second call), branching in `--jq` before bash ever sees the JSON. A run-level-only wait misses this: a 50+-job shadow-gate run whose critical E2E job already failed can stay `in_progress` for hours before anyone notices (#365, 2026-08-12). `gh run view --json jobs` returns only the LATEST attempt, so a superseded pre-rerun failure never re-fires the wake. (The measured `GH_DEBUG=api` cost of adding `,jobs` is in the playbook.)

**DEPLOY / VERSION-LIVE watch — unblock on DEPLOYED-STATE, not run-terminal (#588).** For a release/deploy wait, the run-level `completed` OVERSHOOTS the deployed state by the whole post-deploy E2E tail (tens of minutes) — a worker "watching the deploy" long after the version is live on PROD is the trust-damaging failure (owner report, montalu5). Drop this DEPLOY-DONE classifier into EITHER loop above (SAME single `gh run view` call — one API call, the `| jq` is local), with `DEPLOY_JOB_RE` = the deploy-completing job set (NEVER the E2E tail): it unblocks the moment that set is all-green even while the tail keeps the run `in_progress`; a deploy-set failure fails fast; a scoped-out E2E failure never masks DEPLOYED.

```bash
DEPLOY_JOB_RE='Deploy to PROD|Disable Maintenance|Smoke'   # deploy-completing set — NEVER the E2E tail; tokens are UNANCHORED regex, so anchor ('^Deploy to PROD$') or pick tail-disjoint tokens
s=$(gh run view <id> --json status,conclusion,jobs | jq -r --arg re "$DEPLOY_JOB_RE" '
  ([.jobs[]?|select((.name // "")|test($re))]) as $dep
  | if   ($dep|length)>0 and any($dep[]; .conclusion=="failure" or .conclusion=="timed_out")                         then "DEPLOYFAIL "+([$dep[]|select(.conclusion=="failure" or .conclusion=="timed_out")|.name]|join(", "))
    elif ($dep|length)>0 and any($dep[]; .conclusion=="success") and all($dep[]; .conclusion=="success" or .conclusion=="skipped") then "DEPLOYED "+([$dep[]|.name]|join(", "))
    elif .status=="completed"                                                                                        then "TERMINAL "+.status+" "+(.conclusion//"")
    else "PENDING "+.status end')
# DEPLOYED -> break (version is live). DEPLOYFAIL -> break (deploy broke). TERMINAL -> break (run ended before a deploy-set match; check DEPLOY_JOB_RE).
```

Prefer/COMBINE the ground truth the owner looks at — a DIRECT version read from the live target (DOM version label / health endpoint / XML-RPC module version): the deploy-set green is the CI signal, the live version read the confirmation. The run's final conclusion (E2E tail) is an OPTIONAL confirmation, **never the re-entry gate** for a parked ticket.

- **`Monitor` / `/loop` / Cloud Routines** where they fit — `Monitor` streams output live (still session-scoped); Cloud Routines run on Anthropic infra (survive everything) when configured.

**CRITICAL — a `run_in_background` CI poll is outright BROKEN in a subagent.** A subagent with no pending FOREGROUND tool call is returned as "completed" and TERMINATES; the detached task's completion then fires to the PARENT, so the worker silently dies after every push (~40% of autopilot-worker failures). Wait FOREGROUND with the loop above, or — for a long / multi-stage wait — hand the run-id back to the supervisor and RETURN, TaskStopping anything you launched. Hook-enforced (#28): `block-subagent-bg-ci-poll.sh` denies the launch, `subagent-stop-check-bg-work.sh` blocks the stop while your own background work is live.

3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix the root cause immediately. **Never blindly rerun.** One rerun is acceptable to rule out a transient; two reruns of the same failure means the problem is real. Infrastructure YOU maintain is part of that work — SSH into your own self-hosted runner and fix it rather than handing it back to the user.
4. Push fixes and monitor again until green.
5. After merge to main: monitor the main-branch run AND any release/deploy workflow until they reach a terminal state.

The only hard requirements: (1) monitor until EVERY job reaches a terminal state, (2) the result must come back to your session so you react in-conversation, (3) never claim done while a run is still going. Notifications fire on their own — the mobile app surfaces "waiting on you", Discord idle pings fire when you go idle. Applies to all rewordings and semantic equivalents.
