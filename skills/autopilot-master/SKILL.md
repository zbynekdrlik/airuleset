---
name: autopilot-master
description: "Usage: /autopilot-master. GATEKEEPER umbrella loop — ONE armed /goal multiplexing every lane of the pipeline so the session NEVER idles while any lane has work: sub-dev hand-off reviews (/process-subdev body), release prep ANYTIME with prod deploys held for the repo's declared release window, the gatekeeper's own core backlog (/autopilot body), and user questions asked one at a time via ask-and-continue. Replaces running /autopilot and /process-subdev as separate loops that each parked the whole session while waiting (the 2026-07-20 stalls). airuleset owns it (#22)."
argument-hint: ""
user-invocable: true
disable-model-invocation: true
---

# Autopilot Master — Gatekeeper Umbrella Loop (all lanes, one /goal, never idle)

**Usage:** `/autopilot-master` on the gatekeeper / full-authority box. No argument — it
covers ALL streams plus the gatekeeper's own backlog. Sub-dev boxes never get this skill.

**Why it exists (2026-07-20):** the gatekeeper ran `/autopilot` (own backlog) and
`/process-subdev <stream>` (hand-off queues) as SEPARATE armed loops, and each loop's
wait — a deploy window not yet open, tickets bounced back to a sub-dev — parked the
WHOLE session. Meanwhile other lanes had plenty of workable items and questions for the
user went unasked, so tickets stalled and `Issues 0 · skipped 0` never got closer. The
master loop fixes the shape: waiting parks only the ITEM that waits, never the loop.

**This skill orchestrates; the lane BODIES stay canonical elsewhere** — the review /
release pipeline is the `process-subdev` skill (steps 1–6, verdicts, bounce lane), the
core-backlog cycle is the `autopilot` skill (Step 3 loop body: ticket-validator gate,
bundling, background `autopilot-worker` dispatch), and the shared rules live in the
autopilot skill's `## Cross-stream protocol`. Load those skills for the lane you are
executing; never re-derive or fork their content here. Repo parameters (stream matrix,
`airuleset:release-window`, `airuleset:prod-approval`, review dimensions, release
scripts) come from the repo CLAUDE.md exactly as `process-subdev` defines them.

## Step 1 — Preflight: print the LANE STATUS board

One pass, then print a compact board so the user sees where the work is:

```bash
git fetch origin && gh auth status
# Per stream: hand-offs waiting = ready-for-review OR needs-gatekeeper (a
# carve-out stream hands off via needs-gatekeeper). Count as a REVIEW hand-off
# only a row ALSO carrying a stream:<user> label (the --json labels tells you) —
# a bare needs-gatekeeper with NO stream:<user> is a rule-7 action-request, not a
# review hand-off, so keep it OUT of the REVIEW total (gh --search cannot express
# "any stream:* label" in one query, so this filter is applied when reading rows).
gh issue list --state open --search "label:ready-for-review,needs-gatekeeper" --json number,title,labels
gh issue list --label prio:bounce --state open --json number,title,labels
# Release debt: slices merged to the integration branch but not contained in origin/main
# (per-repo release scripts/preflight per the repo CLAUDE.md)
# Core backlog + questions
gh issue list --state open --search "-label:autopilot-skip -label:ops-channel" -L 200 --json number,title,labels
```

Board lines: `REVIEW: <stream>=N hand-offs · RELEASE: N staged (window <state>) ·
CORE: N open · BOUNCE OUT: N awaiting sub-dev · QUESTIONS: N needs-decision`. For a
windowed instance also print the window and whether NOW (TZ=Europe/Bratislava) is
inside it. Never prod/hardware-classify anything (`approval-scope.md`).

## Step 2 — Print the /goal and STOP (only the user arms the loop)

Print the `/goal` line below in a code block, then the arm question, and STOP — do NOT
start dispatching lanes yourself; Step 3 is the loop body the armed /goal runs each turn.

```
/goal MASTER LOOP — this repo's WHOLE pipeline is DONE only when ALL hold, provable from the transcript: (1) `gh issue list --state open --search "-label:autopilot-skip -label:ops-channel"` shows ZERO open issues repo-wide (core + every stream + prio:bounce + needs-decision), (2) every processed slice is RELEASED (integration→staging→main merged, contained in origin/main), (3) every prod deploy completed per the repo parameters — a windowed instance deployed INSIDE its airuleset:release-window (TZ=Europe/Bratislava; a window spanning midnight wraps) and an approval-gated instance only after my explicit approval — and each deploy post-deploy VERIFIED with evidence in the transcript, (4) main CI green. Until then EVERY turn runs ALL LANES CONCURRENTLY (no round-robin — review, release and core dispatched in parallel; the sub-dev review queue NEVER starves — a hand-off waits ≤1 batch-boundary compact, bounded pacing not starvation): LANE 1 REVIEW — any stream's ready-for-review/needs-gatekeeper hand-off or re-handoff gets the FULL /process-subdev pipeline (cold diff-first review, own CI/release gates, verdict posted to the tickets BEFORE any merge; FINDINGS → the prio:bounce ticket-first bounce lane), depth NEVER degrades across iterations — the 5th hand-off exactly like the 1st. LANE 2 RELEASE — merged-but-unreleased slices run release PREP anytime (preflight, integration→staging with shadow verification, staging→main); a windowed instance's PROD step is STAGED and deploys the moment a turn lands inside the window (then verify); an approval-gated instance is asked the moment its release is STAGED via ❓ ASKED (ask-and-continue; a granted approval carries into the window — no re-ask) and deploys inside the window after approval; a window that OPENS while the deploy is still blocked (gate red / release not staged) raises ONE ❓ ASKED notice naming the blockers — never a silent missed window. LANE 3 CORE — the gatekeeper's own open backlog per the autopilot loop body: validate each ticket (ticket-validator), bundle bundle-safe issues, dispatch a BATCH of up to 5 PARALLEL worktree autopilot-worker lanes, NO refill while a batch is open; INTEGRATION is serialized by the #8 integration mutex (one merge/test/push at a time across ALL sessions — dispatch never waits on it; ready branches integrate without waiting for stragglers); falls back to the serial single-worker shape when worktree isolation is unavailable. BATCH+COMPACT: when LANE 3's batch has integrated, enter a DRAIN WINDOW — no new background task in ANY lane until ZERO live background subagents/Bash remain, then `compact-request --self` last, next batch after. LANE 4 QUESTIONS — open tickets needing my decision (needs-decision / needs-answer / design forks) are asked ONE at a time as self-contained Slovak questions via ❓ ASKED + ⏳ WORKING (ask-and-continue, tracked on the ticket; next question after my answer; 00:00–06:00 Europe/Bratislava defer questions ONLY while other lanes are workable — a NECESSARY question is asked even at night as ❓ NEEDS YOU). ONLY when EVERY lane is empty (waiting solely on sub-dev fixes, my answers, or a deploy window) hold the turn OPEN with a FOREGROUND sleep-poll — repeated short sleep+re-check tool calls that re-check ALL lanes each pass (bounce returns, new hand-offs, the window opening); NEVER a wakeup/schedule mechanism inside this armed /goal (the loop fires the next turn immediately and spins tokens); end held turns ⏳ WORKING. Waiting IS the designed state — never ask me whether to keep waiting. Never gate on prod-usage/events beyond the repo's declared window/approval parameters. Stop only on a blocking ❓ NEEDS YOU decision (after I answer, resolve it, then re-print this /goal + the arm question with empty input so auto-arm re-arms the loop) or a CI failure unfixable after two real attempts.
```

End the message with the arm question block (machine question — it neither pings
Discord nor trips the quality gate):

```
**Otázka — projekt <repo> (<čo projekt robí>):** autopilot-master je pripravený — board vyššie ukazuje prácu vo všetkých lane-och.
• Vlož /goal riadok vyššie (odporúčam) — master loop sa rozbehne a ide sám
• Nič nevkladaj — nespustí sa
❓ NEEDS YOU: vlož /goal riadok vyššie a master loop sa rozbehne
```

## Step 3 — The LANE SCHEDULER (loop body — run BY the armed /goal, never by the bare command)

Each turn, act on EVERY lane that has a workable item — **CONCURRENTLY, never round-robin
one-lane-per-turn**. All lanes run in parallel: a review unblocks a whole sub-dev stream
(the review queue has PRECEDENCE — subdevs waiting on gk reviews is the worst starvation
case, so it must NEVER starve behind core work), a release ships finished work, core
progresses the backlog, questions keep the user's decisions flowing.
The loop **NEVER idles while ANY lane has work**, and never lets a busy lane starve
another — every lane dispatches in parallel, LANE 3 in BATCHES (up to 5, no refill while a
batch is open), bounded by real resource signals (below).

- **LANE 1 REVIEW** — a hand-off present for any stream: `ready-for-review`, OR
  `needs-gatekeeper` carried together with a `stream:<user>` label (a carve-out stream
  with no shadow box hands off ONLY via `needs-gatekeeper`, because its hand-off gate
  strips `ready-for-review` structurally) — or a re-handoff after a bounce? A bare
  `needs-gatekeeper` action-request (no `stream:<user>`; `handed-by:<user>` instead —
  Cross-stream rule 7) is NOT a review hand-off and stays out of this lane. Run the
  `process-subdev` pipeline for that stream (its steps 2–6: pin the
  slice, step 2b's repo-parameterized mechanical pre-review gate re-check (if the repo
  declares one), cold diff-first review, own CI, verdict CLEAN → feeds LANE 2 / FINDINGS
  → bounce lane). Dispatch a review for EVERY waiting hand-off in parallel — never one
  stream per pass while others wait; the queue must never starve. **A CLEAN
  verdict that ships fires the per-ticket run-card for EVERY ticket in the slice**
  (`process-subdev` step 5.4, `airuleset.py notify --run-card`) — never silent (#47).
- **LANE 2 RELEASE** — release debt (a CLEAN slice not yet contained in origin/main, or
  a staged prod deploy)? Release PREP runs ANYTIME — no window gates preflight,
  integration→staging shadow verification, or staging→main. Only the PROD deploy step
  of a windowed instance waits:
  - **Window math:** parse `airuleset:release-window=<instance>:HH:MM-HH:MM`; compare
    `TZ=Europe/Bratislava date +%H:%M`. start > end means the window spans midnight
    and wraps: inside = (now ≥ start) OR (now < end). Outside → the deploy is STAGED
    (record what will deploy + the window) and the scheduler moves on; a turn landing
    inside the window with a staged deploy runs it + post-deploy verification first.
  - **Approval (`airuleset:prod-approval=<instance>`):** ask the MOMENT the release is
    STAGED — daytime is fine — via `❓ ASKED` (ask-and-continue), plain Slovak: what is
    staged, that it deploys inside the window at HH:MM. A granted approval carries into
    the window — **no re-ask** at deploy time. Both markers set → approval AND window
    must both hold before the prod step.
  - **Missed-window notice (2026-07-21: the window passed in silence):** the window
    OPENING while the instance's prod deploy is still blocked (release not staged /
    a gate red / bounce fixes pending) sends **ONE deduped notice per window** via
    `❓ ASKED` (ask-and-continue), plain Slovak: okno je otvorené, deploy neprebehne,
    blokuje ho #X/#Y (s témou), fix beží — nechať dobehnúť (odporúčam) / zasiahnuť?
    The user must never wake up to a silently missed window.
- **LANE 3 CORE** — open non-skip core-slice issues remain? Dispatch the backlog in
  **BATCHES** (#723's batch mode, adapted for the master scheduler by #724, superseding
  #456's continuous refill FOR this lane): validate each ticket (ticket-validator), bundle
  bundle-safe issues, and dispatch a BATCH of up to 5 PARALLEL worktree `autopilot-worker`
  lanes — the batch cap is the primary bound, **NO refill while that batch is open** —
  skipping only a unit that file-overlaps a lane already in flight (a guaranteed merge
  conflict). WITHIN a batch the account-wide resource-signal back-off still applies (the
  batch cap + measured back-off doctrine live in the `autopilot` skill's Batch cap section
  (#332/#456/#723); never re-derive it here). A CI-blocked lane never holds up integration
  of the ready ones.
  INTEGRATION is the ONLY thing serialized: the supervisor merges each returned branch
  under the #8 **integration mutex** (one merge/test/push cycle at a time across ALL
  sessions — per the `autopilot` skill's repo-flow policy, a direct `push` to `main`
  here, a `dev`→`main` PR on a PR-flow repo), integrating ready branches without waiting
  for stragglers; the integration mutex NEVER blocks the batch-dispatch decision, and
  lanes 1/2/4 run concurrently throughout. Falls back to the serial single-worker shape
  when worktree isolation is unavailable.
  **Each merged ticket fires its own run-card** (the `autopilot-worker` agent does
  this itself, per `agents/autopilot-worker.md`, once per merged+deploy-verified
  issue) — never silent (#47). A completed integration in this lane
  ends with a FULL completion report + `✅ DONE` (never `⏳` — 2026-07-25 revision,
  `autopilot` skill Step 3 item 5); the MASTER `/goal` still re-fires the next turn
  regardless, so the scheduler simply re-evaluates all lanes fresh.
  **COMPACT BOUNDARY (#724):** LANE 1 (review-watch) and LANE 2 (release) are long-lived
  and rarely drain, so the master's bounded-context boundary is redefined to LANE 3's OWN
  batch. **The moment LANE 3's batch has returned + integrated, the session ENTERS a DRAIN
  WINDOW: it dispatches no new background task in ANY lane** — LANE 1 / 2 / 4 included; the
  freeze STARTS at LANE-3 batch-drain, NOT once everything is already quiet (otherwise LANE
  1's per-hand-off review dispatch could keep ≥1 subagent live forever and the zero-live
  instant would never arrive — the exact unbounded-context root cause, on the lane the text
  itself calls rarely-draining). Running tasks finish, never preempted; when they drain to
  **ZERO live background subagents (any lane's workers / reviews / validators) AND ZERO live
  background Bash (any CI waiter)** — exactly what `watchdog/compact.py`'s live-tasks veto
  measures (READ-ONLY reference here — the veto is NOT changed) — run `python3
  ~/devel/airuleset/airuleset.py compact-request --self` (#402) as the last tool call; the
  armed `/goal` fires the next turn, compacting then dispatching the next batch. NON-blocking
  (they never hold the boundary): a FOREGROUND sleep-poll, an armed window-wait, a pending
  `❓ ASKED`. During the DRAIN WINDOW a LANE 1 hand-off that arrives QUEUES one compact cycle
  (dispatched only after the compact; running reviews finish + integrate, never preempted); a
  LANE 2 near-boundary CI wait runs FOREGROUND (bounded polls), not `run_in_background`; and
  any granted prod-approval + staged-deploy state MUST be persisted as a ticket comment
  BEFORE the boundary (a compact drops in-context-only state).
- **LANE 4 QUESTIONS** — open tickets labeled `needs-decision` / `needs-answer` (or a
  design fork surfaced by any lane) with no question currently pending? Ask the next
  one — **ONE at a time**, self-contained Slovak per `user-questions-slovak.md`, via
  `❓ ASKED` + `⏳ WORKING` (ask-and-continue; the answer routes back through the
  Discord reply path and resolves that ticket; then the next question). Never batch
  several decisions into one ping. **Each scheduler pass RE-READS the asked tickets:**
  an answer may arrive as a TICKET COMMENT instead of a typed prompt (the watchdog's
  ticket-fallback delivers there when the session input is busy/wedged) — a comment
  carrying the user's decision resolves the ticket exactly like a typed answer.
- **HOLD** — every lane empty (waiting only on sub-dev fixes, the user's answers, or a
  deploy window)? Hold the turn open with a FOREGROUND sleep-poll (repeated short
  sleep + re-check tool calls; NEVER a wakeup/schedule mechanism inside the armed
  /goal) and re-check ALL lanes each pass — a bounce return, a new hand-off, a filed
  ticket, or the window opening immediately becomes the next pass's work. End held
  turns `⏳ WORKING`.

**Collision guards:** INTEGRATION is serialized by the #8 **integration mutex** — one
merge/test/push cycle at a time per repo across ALL sessions (LANE 3); DISPATCH is NOT
serialized by the mutex — it is paced by LANE 3's BATCH boundary (up to 5 lanes, no refill
while a batch is open), never blocked by the mutex. One release in flight per instance
(LANE 2); reviews (LANE 1) and running core lanes coexist — the review object is the
pinned slice, per the parallel-run rule in `process-subdev`. **LANE 3's PRIMARY concurrency
bound is the BATCH cap (#723, adapted for the master by #724): up to 5 parallel lanes, NO
refill while a batch is open. WITHIN a batch a SECOND, account-wide bound applies — back
off every lane (workers + every Step 1b `ticket-validator` + LANE 1's review/validator
dispatches + anything a DIFFERENT concurrent lane or session under this account runs) on a
REAL resource signal — a server-side rate-limit error, box memory pressure, or CC's
max-subagents ceiling. This within-batch bound is ACCOUNT-WIDE (the account has ONE rate
limit shared across every lane and session), never per lane. The canonical batch cap +
measured back-off doctrine lives in the `autopilot` skill's own Batch cap section
(#332/#456/#723); never re-derive it here.**

**Single-lane commands stay:** `/process-subdev <stream>` and `/autopilot` remain valid
for a deliberate single-lane run; on the gatekeeper the master is the default because
one armed /goal covering all lanes is what keeps the session from parking.
