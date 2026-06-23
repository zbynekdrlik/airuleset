### Verify Launched Work Stays Alive — Poll It; a Dead Process Sends No "Done"

**Context gate — related rules you MUST also apply:**
- `autonomous-verification.md` — you have eyes (ps, logs, the dashboard); a stalled job is YOUR work to detect, never the user's to flag
- `ci-monitoring.md` — the CI-run case of this rule (monitor a `gh run` to terminal); this generalizes it to ANY launched work
- `message-status-marker.md` — `⏳ WORKING` means YOU keep checking; it is a promise to re-verify, not a licence to wait blindly
- `complete-planned-work.md` — work isn't done until verified; a silently-dead background job is unfinished work, not a finished one
- `subagent-continuation.md` — a subagent is NOT pollable mid-flight (`SendMessage` is gated); its liveness signal is its DURABLE state (branch / PR / files / gh), not an in-process check

**Anything long-running you LAUNCH — a background Bash, a build, a processing/verdict/encode job, a `Monitor`, a wait on a subagent — you OWN keeping alive. Verify on a cadence that it is STILL RUNNING. NEVER infer "still running" from the ABSENCE of a completion signal: a process that crashed, was OOM-killed, segfaulted, or hung emits no "done" and no "failed" — silence looks IDENTICAL to healthy progress. This rule exists because a worker waited on a job's success event, the job died silently, the event never came, and the session sat for 8 HOURS believing it was "still decoding". Silence is not success. Silence is not even liveness.**

#### The hard rule — never wait on a success-ONLY condition

A wait that only terminates on SUCCESS waits FOREVER when the work dies, because death is not success. Every wait MUST also terminate on failure / death / timeout, so you get re-invoked and NOTICE:

1. **`Monitor`** — the filter MUST cover failure signatures, not just the happy marker (per the tool's own "silence is not success"): `grep -E "progress=|Traceback|Error|FAILED|panic|Killed|OOM|exit"`. For an OUTCOME wait set a bounded `timeout_ms` (default 5 min) — do NOT use `persistent: true` for "wait until X finishes" (persistent removes the timeout → a dead job hangs the watch indefinitely). `timeout_ms` maxes at 1 h (3600000 ms); for a job that can run longer than 1 h, a single `Monitor` cannot span the wait — use a self-re-invoking poll (`ScheduleWakeup` or a `run_in_background` loop that re-arms) instead.
2. **Background Bash `until` loop** — bound it so it ALWAYS exits and re-invokes you (on success, death, OR timeout), never only on the marker that a dead process can't write:
   ```bash
   PID=$!; START=$SECONDS
   until grep -q DONE out.log || ! kill -0 "$PID" 2>/dev/null || (( SECONDS-START > 1800 )); do sleep 10; done
   ```
   `until grep -q DONE out.log` ALONE is the trap — it spins forever if the job dies before writing `DONE`.
3. **Explicit liveness poll** — check the process is ALIVE (`kill -0 $PID` / `ps -p $PID`) AND PROGRESSING (output file growing, heartbeat / mtime advancing, row count climbing). Dead OR stalled (no progress for N minutes) → **INTERVENE NOW** (read the log, find why it died, restart or re-route) — do NOT keep waiting.

#### Bound the wait, schedule the re-check, return to the session

- **Every wait has an expected duration.** Exceeding it by a margin is a SIGNAL to check, not a reason to assume "still going". A 2-minute job silent for 30 minutes is dead, not slow.
- **End a turn `⏳ WORKING` only with a scheduled re-check armed** — a `ScheduleWakeup`, a self-re-invoking background poll, or a bounded `Monitor` that returns. NEVER a blind indefinite wait on a single notification that a dead process can't send.
- **The result must come BACK to your session** (re-invoking mechanism), so a silent death is caught within a bounded time — never a fully-detached wait you can't observe.

#### Safety net (don't rely on it — the discipline is yours)

The api-watchdog backstops this weakly: a turn that ended `⏳ WORKING` and then sits idle ≥45 min with no advancing subagent gets ONE Discord ping ("this session may be stuck on dead launched work — check it"). It PINGS, it does NOT auto-fix — an external poller cannot tell a healthy CI/encode wait from a dead job (both freeze the transcript), so it must not act, only alert. Two consequences: (1) the net keys STRICTLY on a `⏳` last-line marker — if you mislabel a still-running job `✅ DONE` (banned by `message-status-marker.md`), the watchdog will NOT catch the silent death; honest `⏳` is what arms it; (2) it fires late and only flags — the in-session liveness poll below is the actual fix. Build it so the net never has to fire.

#### Anti-patterns (intent — all rewordings and semantic equivalents)

- Inferring "still running / still processing / still decoding" from the absence of a notification — **WRONG.** Run `ps`. Read the log's mtime. Prove it's alive.
- `until grep -q SUCCESS file` (or a success-only `Monitor` filter) with no death / timeout branch — **WRONG.** Waits forever on a silent death.
- `Monitor` with `persistent: true` for an "until it finishes" wait — **WRONG.** Bound it with `timeout_ms`.
- Ending `⏳ WORKING` and then never re-checking the thing — **WRONG.** `⏳` is a promise to re-verify on a cadence.
- Trusting a subagent's "it's processing" / a launched job's last "progress" line as proof it's STILL alive minutes later — **WRONG.** Re-poll; the last line may be its dying breath.
- "Poll the running subagent for liveness" via `SendMessage` — **WRONG.** It's gated off (`subagent-continuation.md`); a foreground dispatch blocks until it returns, a background one re-invokes you on exit. For a subagent you can't observe live, the liveness signal is its DURABLE state (the branch/PR/files/gh it should be producing), not an in-process ping.
- Treating absence-of-error as proof-of-progress — **WRONG.** A hung process emits neither.
- Making the user type "stucked?" to discover a stall you should have caught — **WRONG.** That is the exact failure this rule kills.

The intent: every long thing you launch is polled for real liveness on a bounded cadence, and a silent death is caught in minutes — never hours, never by the user. Applies to all rewordings and semantic equivalents.
