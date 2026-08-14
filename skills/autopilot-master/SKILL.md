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
# Per stream: hand-offs waiting + bounced-out tickets
gh issue list --label ready-for-review --state open --json number,title,labels
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
/goal MASTER LOOP — this repo's WHOLE pipeline is DONE only when ALL hold, provable from the transcript: (1) `gh issue list --state open --search "-label:autopilot-skip -label:ops-channel"` shows ZERO open issues repo-wide (core + every stream + prio:bounce + needs-decision — nothing parked), (2) every processed slice is RELEASED (integration→staging→main merged, contained in origin/main), (3) every prod deploy completed per the repo parameters — a windowed instance deployed INSIDE its airuleset:release-window (TZ=Europe/Bratislava; a window spanning midnight wraps) and an approval-gated instance only after my explicit approval — and each deploy post-deploy VERIFIED with evidence in the transcript, (4) main CI green. Until then EVERY turn runs ALL LANES CONCURRENTLY (no round-robin — review, release and core dispatched in parallel; the sub-dev review queue NEVER starves): LANE 1 REVIEW — any stream's ready-for-review hand-off or re-handoff gets the FULL /process-subdev pipeline (cold diff-first review, own CI/release gates, verdict posted to the tickets BEFORE any merge; FINDINGS → the prio:bounce ticket-first bounce lane), depth NEVER degrades across iterations — the 5th hand-off exactly like the 1st. LANE 2 RELEASE — merged-but-unreleased slices run release PREP anytime (preflight, integration→staging with shadow verification, staging→main); a windowed instance's PROD step is STAGED and deploys the moment a turn lands inside the window (then verify); an approval-gated instance is asked the moment its release is STAGED via ❓ ASKED (ask-and-continue; a granted approval carries into the window — no re-ask) and deploys inside the window after approval; a window that OPENS while the deploy is still blocked (gate red / release not staged) raises ONE ❓ ASKED notice naming the blockers — never a silent missed window. LANE 3 CORE — the gatekeeper's own open backlog per the autopilot loop body: validate each ticket (ticket-validator), bundle bundle-safe issues, dispatch a worktree autopilot-worker lane for EVERY workable bundle-safe unit, refilling continuously (bounded only by real resource signals, never a fixed cap); INTEGRATION is serialized by the #8 integration mutex (one merge/test/push at a time across ALL sessions — dispatch never waits on it; ready branches integrate without waiting for stragglers); falls back to the serial single-worker shape when worktree isolation is unavailable. LANES-FULL: keep saturating continuously — every workable unit (core ticket, review hand-off, release step) gets its own lane the moment it is free, bounded only by real resource signals (rate-limit errors, box memory, CC max-subagents), never a fixed number; a CI-waiting lane never blocks new dispatch; only INTEGRATION serializes (the #8 integration mutex). LANE 4 QUESTIONS — open tickets needing my decision (needs-decision / needs-answer / design forks) are asked ONE at a time as self-contained Slovak questions via ❓ ASKED + ⏳ WORKING (ask-and-continue, tracked on the ticket; next question after my answer; 00:00–06:00 Europe/Bratislava defer questions ONLY while other lanes are workable — a NECESSARY question is asked even at night as ❓ NEEDS YOU). ONLY when EVERY lane is empty (waiting solely on sub-dev fixes, my answers, or a deploy window) hold the turn OPEN with a FOREGROUND sleep-poll — repeated short sleep+re-check tool calls that re-check ALL lanes each pass (bounce returns, new hand-offs, the window opening); NEVER a wakeup/schedule mechanism inside this armed /goal (the loop fires the next turn immediately and spins tokens); end held turns ⏳ WORKING. Waiting IS the designed state — never ask me whether to keep waiting. Never gate on prod-usage/events beyond the repo's declared window/approval parameters. Stop only on a blocking ❓ NEEDS YOU decision (after I answer, resolve it, then re-print this /goal + the arm question with empty input so auto-arm re-arms the loop) or a CI failure unfixable after two real attempts.
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
another — dispatch saturates every lane in parallel, bounded only by real resource signals (below).

- **LANE 1 REVIEW** — `ready-for-review` present for any stream (or a re-handoff after
  a bounce)? Run the `process-subdev` pipeline for that stream (its steps 2–6: pin the
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
- **LANE 3 CORE** — open non-skip core-slice issues remain? Per the `autopilot` skill
  Step 3, **CONTINUOUSLY refill** worktree `autopilot-worker` lanes (#456, supersedes
  the #442 fill-the-cap round mandate): validate each ticket (ticket-validator), bundle
  bundle-safe issues, and dispatch a lane for EVERY workable bundle-safe unit the moment
  it is free — NO fixed cap, bounded only by real resource signals (see the `autopilot`
  skill's saturation section), skipping only a batch that file-overlaps a lane already
  in flight (a guaranteed merge conflict). A CI-blocked lane never holds up new dispatch.
  INTEGRATION is the ONLY thing serialized: the supervisor merges each returned branch
  under the #8 **integration mutex** (one merge/test/push cycle at a time across ALL
  sessions — per the `autopilot` skill's repo-flow policy, a direct `push` to `main`
  here, a `dev`→`main` PR on a PR-flow repo), integrating ready branches without waiting
  for stragglers; the integration mutex NEVER blocks dispatch, and lanes 1/2/4 run
  concurrently throughout. Falls back to the serial single-worker shape when worktree
  isolation is unavailable.
  **Each merged ticket fires its own run-card** (the `autopilot-worker` agent does
  this itself, per `agents/autopilot-worker.md`, once per merged+deploy-verified
  issue) — never silent (#47). A completed integration in this lane
  ends with a FULL completion report + `✅ DONE` (never `⏳` — 2026-07-25 revision,
  `autopilot` skill Step 3 item 5); the MASTER `/goal` still re-fires the next turn
  regardless, so the scheduler simply re-evaluates all lanes fresh.
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
serialized and refills lanes continuously, never blocked by the mutex. One release in
flight per instance (LANE 2); reviews (LANE 1) and running core lanes coexist — the
review object is the pinned slice, per the parallel-run rule in `process-subdev`. **There
is NO fixed concurrent-agent cap (#456): saturate every lane (workers + every Step 1b
`ticket-validator` + LANE 1's review/validator dispatches + anything a DIFFERENT
concurrent lane or session under this account runs) until a REAL resource signal — a
server-side rate-limit error, box memory pressure, or CC's max-subagents ceiling — then
stagger/back off. This is ACCOUNT-WIDE (the account has ONE rate limit shared across every
lane and session), never per lane. The canonical, measured back-off doctrine lives in the
`autopilot` skill's own saturation section (#332/#456); never re-derive it here.**

**Single-lane commands stay:** `/process-subdev <stream>` and `/autopilot` remain valid
for a deliberate single-lane run; on the gatekeeper the master is the default because
one armed /goal covering all lanes is what keeps the session from parking.
