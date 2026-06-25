### Verify Launched Work Stays Alive — Poll It; a Dead Process Sends No "Done"

**Context gate — related rules you MUST also apply:**
- `autonomous-verification.md` — you have eyes (ps, logs, the dashboard); a stalled job is YOUR work to detect, never the user's to flag
- `ci-monitoring.md` — the CI-run case of this rule (monitor a `gh run` to terminal); this generalizes it to ANY launched work
- `message-status-marker.md` — `⏳ WORKING` means YOU keep checking; it is a promise to re-verify, not a licence to wait blindly
- `complete-planned-work.md` — work isn't done until verified; a silently-dead background job is unfinished work, not a finished one
- `subagent-continuation.md` — a subagent is NOT pollable mid-flight (`SendMessage` is gated); its liveness signal is its DURABLE state (branch / PR / files / gh), not an in-process check

**Anything long-running you LAUNCH — a background Bash, a build, a processing/verdict/encode job, a `Monitor`, a dispatched subagent/worker/workflow — you OWN keeping alive. Verify on a cadence that it is STILL RUNNING. NEVER infer "still running" from the ABSENCE of a completion signal: a process that crashed, was OOM-killed, segfaulted, or hung — or a subagent that died — emits no "done" and no "failed", so silence looks IDENTICAL to healthy progress. This rule exists because (1) a worker waited on a job's success event, the job died silently, the event never came, and the session sat for 8 HOURS believing it was "still decoding"; and (2) one morning the user had to hand-type "stucked?" into NEARLY EVERY running session — each was parked on `⏳ WORKING` claiming a subagent/subprocess was running, but NONE had internally re-checked why that subagent/subprocess had been silent for HOURS. Silence is not success. Silence is not even liveness. A turn that ends `⏳ WORKING` is a PROMISE to re-verify on a cadence — not a licence to wait until the user pokes you.**

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

#### A subagent / subprocess that has gone SILENT is presumed DEAD — re-check it, don't trust it

The specific failure that keeps recurring: you dispatch a subagent (a worker / workflow / `Agent`) or launch a subprocess, end the turn `⏳ WORKING`, and then **never internally re-check why it has not reported back**. Hours pass. The thing died silently and you sat waiting because nothing told you it died.

- **A dispatched subagent's liveness signal is its DURABLE state, NOT a notification** (`subagent-continuation.md`): its transcript advancing, the branch/PR/files/gh it should be producing. If that state has not moved past the subagent's expected duration, presume the subagent is DEAD — re-dispatch a fresh one with the full context, do NOT keep waiting on the silent one. (`SendMessage` to "poll" it is gated off — you cannot ping a running subagent; you re-check its output.)
- **A foreground subagent BLOCKS you** — you cannot poll while it runs. So before a long dispatch, know its expected duration; a foreground worker that runs WAY past it is the case you CANNOT self-detect (you're blocked) — which is exactly why the watchdog backstop below exists. Prefer a bounded dispatch and durable-state resumption over an unbounded foreground block.
- **"It said it was working N hours ago" is NOT evidence it is working NOW.** The last progress line may have been its dying breath. Re-derive liveness from current state every cadence, never from the staleness of the last update.

#### Safety net — the watchdog now NUDGES you to self-check (the autonomous "stucked?")

The api-watchdog backstops this — and it no longer merely pings. A turn that ended `⏳ WORKING` and then sits idle ≥30 min with no advancing subagent gets an automatic **`stuck-check` self-check NUDGE** typed into the session (the autonomous equivalent of the user hand-typing "stucked?"): "verify the liveness of your launched work — ps / log mtime / subagent transcript / dashboard / gh run — and intervene if it died." This is safe where a blind `continue` was not: it does not auto-fix or decide liveness for you — it DELEGATES the healthy-vs-dead judgment back to YOU (you have eyes: the PID, the logs, the subagent's durable state). A landed nudge that you answer resets the idle clock, so the episode self-resolves in one nudge with NO Discord ping; only if the nudge produces NO response across 3 retries (your Claude process is itself wedged) does it escalate to ONE "needs you" ping. The user explicitly WANTS this even when it fires on a still-healthy wait — answering "checked, still alive, CI at 60%" is a welcome confirmation, far better than hoping nothing is wedged and losing a whole day; so an occasional nudge that resolves to "not stuck" is working as intended, never a false alarm to suppress. Consequences for your discipline: (1) the net keys STRICTLY on a `⏳` last-line marker — if you mislabel a still-running job `✅ DONE` (banned by `message-status-marker.md`), the watchdog will NOT nudge you; honest `⏳` is what arms it; (2) the nudge fires at 30 min and only triggers the self-check you should already run yourself on a tighter cadence. **Build your in-session liveness poll so the nudge never has to fire — but when it does, treat that `stuck-check` prompt as a hard order to re-derive liveness NOW and intervene, not as a cue to re-assert "still working" without checking.**

#### Anti-patterns (intent — all rewordings and semantic equivalents)

- Inferring "still running / still processing / still decoding" from the absence of a notification — **WRONG.** Run `ps`. Read the log's mtime. Prove it's alive.
- `until grep -q SUCCESS file` (or a success-only `Monitor` filter) with no death / timeout branch — **WRONG.** Waits forever on a silent death.
- `Monitor` with `persistent: true` for an "until it finishes" wait — **WRONG.** Bound it with `timeout_ms`.
- Ending `⏳ WORKING` and then never re-checking the thing — **WRONG.** `⏳` is a promise to re-verify on a cadence.
- Trusting a subagent's "it's processing" / a launched job's last "progress" line as proof it's STILL alive minutes later — **WRONG.** Re-poll; the last line may be its dying breath.
- "Poll the running subagent for liveness" via `SendMessage` — **WRONG.** It's gated off (`subagent-continuation.md`); a foreground dispatch blocks until it returns, a background one re-invokes you on exit. For a subagent you can't observe live, the liveness signal is its DURABLE state (the branch/PR/files/gh it should be producing), not an in-process ping.
- Treating absence-of-error as proof-of-progress — **WRONG.** A hung process emits neither.
- Answering the watchdog's `stuck-check` nudge by re-asserting "⏳ still working" WITHOUT re-deriving liveness — **WRONG.** The nudge is an order to `ps` / read the log mtime / check the subagent's durable state RIGHT NOW and intervene if dead, not a cue to repeat your last claim.
- Waiting on a dispatched subagent/worker whose durable state (transcript / branch / PR / gh) has not moved past its expected duration — **WRONG.** Silent past-duration = presumed dead → re-dispatch fresh, don't keep waiting.
- Making the user type "stucked?" to discover a stall you should have caught — **WRONG.** That is the exact failure this rule kills; the watchdog now types it for them, but you reaching that state at all is the failure.

The intent: every long thing you launch is polled for real liveness on a bounded cadence, and a silent death is caught in minutes — never hours, never by the user. Applies to all rewordings and semantic equivalents.
