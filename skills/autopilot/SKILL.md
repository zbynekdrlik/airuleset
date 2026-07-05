---
name: autopilot
description: "Usage: /autopilot [status] [manual]. Hands-off loop that solves the WHOLE GitHub backlog. To cut long-CI cost it BUNDLES bundle-safe small issues into ONE worker run → ONE PR closing all → ONE CI cycle (the bundling gate decides; big/schema/API/security/cross-cut issues run solo). Each run is an in-session BACKGROUND autopilot-worker subagent (run_in_background — your main session stays FREE + thin, the worker stays visible in the agent strip) that can still ASK YOU the important questions directly. Never pre-filters needs-input issues and never refuses to start; after each run (incl. after merge) it picks the next batch. status = show backlog + skipped, run nothing. manual = stop every PR at green for your merge. Merge/deploy follow pr-merge-policy.md (opt-out airuleset:merge=manual). Start-of-run it reviews the skip set (asks which already-skipped issues to un-skip), lets you exclude more (autopilot-skip), and lets you interactively CLOSE obsolete issues. End-of-run (backlog empty) it does a reconciliation sweep over ALL remaining open issues INCLUDING skips — while context is fresh — closing/rescoping any ticket the run overcame (hard-overcome auto-closes with evidence; uncertain asks). You can also close any issue anytime via 'close #N (reason)'."
argument-hint: "[status] [manual]"
user-invocable: true
disable-model-invocation: true
---

# Autopilot — Hands-off Backlog Loop

> Solves the **ENTIRE** open backlog, one issue at a time. Each issue is handed to an
> **in-session background `autopilot-worker` subagent** (`run_in_background: true`) — fresh
> context (your main session stays thin AND interactive — you can keep messaging it), visible in
> the agent strip, and **able to ask you the genuinely-important questions directly**. After each issue completes (merged + deployed, or a question resolved), the loop
> picks the **next** — including right after a merge. It **NEVER** pre-filters "needs input"
> issues and **NEVER** refuses to start. The goal is to finish everything; your only job is to
> answer the important per-issue questions when a worker raises one.

> **Usage:** `/autopilot [status] [manual]`
> • *(no arg)* — run the loop over the whole backlog
> • `status` — print the backlog + currently-skipped issues, run nothing
> • `manual` — stop every PR at green for your "merge it" this run (else default auto-merge)

**What it removes (the old pain):** no more re-running `/issue-planner`, no manual `/compact`,
no "nothing is hands-off so I'm stopping". You answer the important questions; everything else runs.

**Context gate — apply all:**
- `autonomous-batch-issue-development.md` — bundle bundle-safe issues into ONE PR/CI cycle (the gate + ceiling below)
- `pr-merge-policy.md` — default auto-merge; `airuleset:merge=manual` marker (or the `manual` arg) = stop at green PR
- `tdd-workflow.md` / `regression-test-first.md` — calibrated TDD per issue
- `ci-monitoring.md` — 2-branch single-CI repo: the worker monitors its OWN CI **foreground** (NEVER `run_in_background` — that ends the subagent), the main loop verifies the result; long / multi-stage pipeline (3-branch): the SUPERVISOR owns the CI waits and the worker returns per stage (Step 3 multi-stage note)
- `post-deploy-verification.md` / `version-on-dashboard.md` — deploys verified via the live DOM version
- `milestone-notifications.md` — short `❓`/`✅` idle pings only on a worker's ❓ question or the FINAL ✅ (mobile model); BUT each finished+deployed ticket ALSO sends ONE structured Discord completion card (the worker fires it directly at merge — the user's explicit per-ticket ask); every device message @mentions the tmux owner (zbynek/marek)
- `no-dropped-work.md` — workers file issues for everything identified but unfinished
- `verify-issue-still-valid.md` — the worker FIRST proves the issue still reproduces against current code + live system; obsolete/already-solved tickets get closed with evidence, never blindly implemented
- `ask-before-assuming.md` — a genuine per-issue question is a CONVERSATION with you, asked the MOMENT the ticket needs it and it ALWAYS pings; then either BLOCK (`❓ NEEDS YOU`) or ask-and-continue (`❓ ASKED` + track on the issue, work other tickets meanwhile) — never buried, never a reason to abandon the issue, never a reason to reproach you (except the 00:00–06:00 — hours `00..05` — Europe/Bratislava sleep window, when it defers with no ping)
- `user-questions-slovak.md` — HOW to phrase it: SELF-CONTAINED (a person with ZERO terminal context understands it — which project, what happened, every cross-project/ticket link explained), plain Slovak, no jargon; delivered as the `❓` text marker (waits UNLIMITED), NEVER a 60-second `AskUserQuestion` dialog for an away user; structured template + ONE decision per ping is hook-enforced

## How it works

- **Engine = a `/goal` loop you paste once.** Each turn the main agent assembles the next BATCH
  (one bundle-safe issue, or several bundled into one PR — see Step 3.1) and dispatches ONE
  in-session BACKGROUND `autopilot-worker` (`run_in_background: true`) for it; the dispatch returns
  IMMEDIATELY so your main session stays FREE, and the worker RE-INVOKES the loop when it finishes.
  The worker runs the full cycle on one `dev` branch / one PR / one CI run (and asks you if needed,
  its prompts surfacing in your main session); on completion the main agent verifies the result from
  GitHub; the next turn picks the next batch — until the backlog is empty.
- **Bundling cuts CI cost.** CI is long here, so the loop spends ONE CI cycle on as many
  bundle-safe issues as the gate allows (`autonomous-batch-issue-development.md`) instead of
  one-PR-per-issue. Issues that fail the gate (large / schema / API / security / cross-cut) run solo.
- **Worker = in-session BACKGROUND `autopilot-worker` subagent** (`run_in_background: true`, user-
  level, installed by airuleset). Background so your MAIN session stays FREE (you can keep messaging
  it) and THIN while the worker runs — and since Claude Code's 2026-W26 change the worker's prompts
  and questions still SURFACE in your main session, so it can ask you. It stays VISIBLE in the agent
  strip (it's an in-session subagent — NOT a hidden `claude --bg` daemon). Fresh context so your main
  session never degrades; it returns only a short evidence block to the main agent.
- **Main session stays thin** — it holds only "dispatched #N → verified merged" summaries, so
  there is no `/compact` churn across a long backlog.
- **`/autopilot` itself does ONLY Steps 1–2** — preflight, optional skip-picker, then it PRINTS
  the `/goal` line and **STOPS**. It must **NOT** start dispatching workers on its own. The
  per-issue loop (Step 3) runs **only after YOU paste the `/goal` line** — only the user can type
  `/goal`, and without it nothing re-fires across turns (a directly-dispatched worker would do one
  issue and stop). So `/autopilot` always ends by handing you the `/goal` line to paste.

## Step 1 — Preflight

```bash
git fetch origin && git rev-parse --abbrev-ref HEAD && git status --porcelain   # dev, clean
gh auth status
gh issue list --state open -L 100
grep -n "airuleset:merge=manual" CLAUDE.md || true                              # merge mode
```

- Confirm the `autopilot-worker` subagent is available (`@agent-autopilot-worker` resolves). If
  not, run `python3 ~/devel/airuleset/airuleset.py install` once and restart the session
  (subagents load at session start).
- **Recommended:** run the session with **auto or bypass permissions** (Shift+Tab → auto) so
  routine worker tool-calls don't spam prompts. Genuine clarifying questions still reach you regardless.
- **Backlog scope = ALL open issues EXCEPT those labeled `autopilot-skip`.** That is the ONLY
  exclusion. Do **NOT** filter out `needs-design` / `needs-decision` / `question` / `blocked` —
  those get worked too; the worker raises the question with you. A backlog full of "needs input"
  issues is **NOT** a reason to refuse — start anyway. Only a genuinely empty backlog stops you.
- **NEVER prod/hardware-classify the backlog (the user's hardest rule — `approval-scope.md`).** When
  printing the banner / backlog / queue, do **NOT** flag, colour (🔴), tag, or bucket issues as
  "PROD / HARDWARE / live / off-air / invasive / risky / needs-the-rig / needs-you-present", do
  **NOT** recommend `autopilot-skip` for any of them, and do **NOT** warn about off-air windows, "you
  must be present / be at the rig", or "CI can't verify (manual self-hosted) so you must watch". A
  hardware / prod / streaming / OBS / HDMI / DRM issue is worked end-to-end on the rig like any other;
  the USER alone guards whether prod is live and stops you in the moment. (Same in Slovak: no
  `off-air okná`, `musíš byť pri tom`, `odporúčam autopilot-skip`, `vedene so mnou nie naslepo`.)
- **Print a one-line banner:** `autopilot · merge=auto (no manual marker) · N issues · solving the whole backlog`.
- **Version-on-dashboard foundation gate** (web projects): no version label → that foundation
  issue is the FIRST work item (`version-on-dashboard.md`).

### Step 1b — Skip review + picker (start-of-run; the skip set is RE-WEIGHED, not frozen)

Run BOTH halves every start so a skipped task is reconsidered each run. Ensure the label exists once:
`gh label create autopilot-skip --color ededed --description "Excluded from autopilot runs" 2>/dev/null || true`.

**(i) Un-skip review — reconsider what is ALREADY skipped (do this FIRST).** List the currently-skipped
issues: `gh issue list --state open --label autopilot-skip -L 100`. If ANY exist, PRINT them
(`#N <title> (Xd old)`) and ask via `AskUserQuestion` (`multiSelect: true`, one option per issue) which
to **UN-skip** this run. For each chosen: `gh issue edit <N> --remove-label autopilot-skip` → it
re-enters the backlog. **Default = keep all skipped (un-skip none).** This is how a deliberately-skipped
task gets re-weighed without silently losing the skip. (If none are skipped, say so and move on.)

**(ii) Add-skip picker — exclude anything you do NOT want touched at all this run.** The default is
*work everything*. PRINT the full open-issue list (`#N <title> (Xd old)`, one per line), then ask which
to EXCLUDE via `AskUserQuestion` with `multiSelect: true` (one option per issue). AskUserQuestion renders
~4 options per question, so split across multiple ~4-option questions, or for a large backlog show the
oldest subset and let the user add any other numbers via "Other" (comma-separated) — the printed list
backs that. Apply to each chosen issue: `gh issue edit <N> --add-label autopilot-skip`, then print
`skipping #A #B … · working N issues`. **Selecting none = work all (the normal case).** NEW issues filed
by workers never carry this label → always worked.

### Step 1c — Close obsolete issues (interactive, start-of-run)

You often already know a task no longer makes sense but it lingers with no easy way to close it — this
is that way. From the working backlog (open issues minus `autopilot-skip`), PRINT the full list
(`#N <title> (Xd old)`) and ask via `AskUserQuestion` (`multiSelect: true`) which are **OBSOLETE and
should be CLOSED now**. Present the list NEUTRALLY: do **NOT** recommend which to close, and **NEVER**
classify / flag / colour any issue (especially not prod/hardware — `approval-scope.md`). For each chosen:
`gh issue close <N> --comment "Closed at /autopilot start — obsolete per user."`, drop it from the
backlog, and note the closures (no per-issue device ping — `milestone-notifications.md`). **Default =
close none.** Same ~4-options-per-question / "Other" handling as the picker above. (You can ALSO close any
issue at any time — in `/autopilot` or normal chat — by telling Claude `close #N (reason)`; it runs
`gh issue close <N> --comment "<reason>"` + ping. Closing an issue is non-destructive tracking and never
needs extra approval.)

## Step 2 — Start the engine (the one manual paste)

The agent cannot type `/goal` — print this line for the user to paste once:

```
/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, both checkable from the transcript alone: (A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it. Waiting for my answer IS the terminal state: NEVER continue me past an unanswered `❓ NEEDS YOU` — every forced continuation just re-prints the same question into my chat (the camera-box wall, 2026-07-05). After I answer, Claude resolves that ticket and, if open issues remain, re-prints this /goal line for me to paste and re-arm the loop. (B) BACKLOG DONE — every open issue in this repo not labeled autopilot-skip is closed via a merged PR, proven by `gh issue list --state open --search "-label:autopilot-skip"` showing none remain AND `gh run list -b main -L 1` showing main green. Also stop when I must approve a genuinely-irreversible action (host reboot / data deletion / DB drop — NOT a deploy, a prod test, or restarting the app/device you're testing) or a CI failure stays unfixable after two real attempts. While NEITHER holds, work the backlog: never gate, classify, skip, or warn based on prod-usage / events / off-air / hardware — I alone guard whether prod is live. When an issue needs my input, ASK me the moment it comes up — it ALWAYS pings my phone (the background autopilot-worker's prompts surface in my main session) — preferring ASK-AND-CONTINUE (`❓ ASKED` + track the question on the issue with a `needs-answer` comment, set that issue aside, and work other answer-independent tickets, ending `⏳ WORKING`); `❓ NEEDS YOU` (a full block — the loop then stops per (A)) ONLY when nothing else is workable. NEVER bury a question by continuing without pinging it, and NEVER stop blaming my silence. EXCEPT 00:00–06:00 (hours 00..05) Europe/Bratislava when I'm asleep (then queue it and ask after 06:00, no ping). After every merge immediately pick the next issue.
```

The condition lists ONLY `autopilot-skip` as the exclusion, so `needs-design` / `needs-decision`
/ `question` issues all count toward "must be closed" — the loop works them WITH your input.

**This is the LAST thing `/autopilot` does.** Present the `/goal` line prominently in a code block,
tell the user to paste it to start the loop, and **STOP** — end your message with
a conforming question block (the question-quality gate requires the briefing line):

```
**Otázka — projekt <repo> (<čo projekt robí>):** autopilot je pripravený — backlog má N otvorených ticketov.
• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám
• Nič nevkladaj — autopilot sa nespustí
❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne
```

Do **NOT** proceed to
dispatch any worker yourself — **Step 3 is the LOOP BODY that the `/goal` loop runs each turn AFTER
the user pastes the line**, not part of this initial invocation. Dispatching a worker now (without
`/goal` running) would do one issue and stop — the exact failure this avoids. If you skip printing
the `/goal` line, the loop never starts.

## Step 3 — Per-issue cycle (the loop body — run BY the `/goal` loop each turn, NOT by the initial `/autopilot` call)

> You reach this section only when a turn fires under the `/goal` loop the user pasted in Step 2.
> The plain `/autopilot` invocation STOPS at Step 2 — it never runs Step 3 itself.

Each loop turn:

1. **Assemble the next BATCH — bundle by default to spend ONE CI cycle on many issues**
   (`autonomous-batch-issue-development.md`). CI here is long, so bundling small issues into one PR
   is the main lever to cut CI cost.
   - **Seed:** the next open non-`autopilot-skip` issue (highest priority / oldest first).
   - **Grow greedily** by adding more open backlog issues that EACH pass the **bundling gate** vs the
     seed and the batch-so-far:
       • each member ≤ ~300 LoC estimated, AND cumulative batch ≤ ~600 LoC, AND ≤ 4 issues (keep the
         PR reviewable);
       • no DB schema/migration, no public-API break (routes/exported types/CLI flags), no
         security-boundary change (auth/permissions/secrets), no cross-cutting refactor (rename >5
         files / dep major bump / framework upgrade);
       • independent — no member depends on another member's design choice.
   - An issue that FAILS the gate is NOT added — it becomes the seed of a LATER solo batch (its own PR).
     A large / schema / API / security / cross-cut seed runs SOLO; never force-bundle it.
   - **Best-effort:** if nothing else qualifies, the batch is just the seed (one issue — today's behavior).
1b. **VALIDATE EACH batch member FIRST — hard gate** (`verify-issue-still-valid.md`). Before dispatching
   the worker, dispatch the read-only **`ticket-validator`** subagent
   (`subagent_type: ticket-validator`, prompt `Validate issue #<N> in <repo>`) for EVERY member — they
   are independent, so validate them in parallel. Branch PER member:
   - **STILL_VALID** → keep in the batch. **PARTIAL** → keep, pass its `still_to_do` as that issue's scope.
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR resolved it OR a passing repro proves it) →
     do NOT implement; **auto-close** the issue with the validator's evidence as a closing comment
     (`gh issue close <N> --comment "<validator evidence>"`) — no device ping (reopenable in one
     click) — and DROP it from the batch.
   - **OVERCOME + `overcome_confidence: soft`** → DROP from the batch and ask the user ("looks overcome by
     <evidence> — close it?") with the validator's evidence; act on their answer (close, or run it solo).
   - **UNCLEAR** → DROP from the batch and ask the user, quoting the validator's `premise_check` so nothing
     already-answered is re-asked. **One unclear/overcome member must NOT block the rest of the batch** —
     pull it out and proceed with the surviving STILL_VALID / PARTIAL members.
   (Hybrid close policy: auto-close ONLY clear-cut hard-overcome; everything uncertain goes to the user.)
   After validation, the batch = the surviving STILL_VALID / PARTIAL members. This stops the recurring
   failure (working / re-asking on an already-overcome ticket).
2. **Dispatch ONE in-session BACKGROUND `autopilot-worker`** via the Agent tool for the WHOLE batch:
   `subagent_type: autopilot-worker`, **`run_in_background: true`** — this keeps your main session
   FREE + thin while the worker runs, the worker stays VISIBLE in the agent strip, and (per CC's
   2026-W26 change) its prompts still reach you. prompt = `Work issues #A #B #C in <repo>
   as ONE bundled PR (Closes all).` (or `Work issue #<N> in <repo>.` for a solo batch) plus any
   repo-specific note. ONE worker, ONE `dev` branch, ONE PR, ONE CI cycle.
   - **Model = Sonnet 5 by default; HARD tickets escalate — Fable through the budget gate**
     (`model-awareness.md` ACTIVE policy 2026-07-03). The `autopilot-worker` frontmatter defaults to
     `model: sonnet` — dispatch it AS-IS for a routine ticket (bug fix, scoped feature). When the
     ticket-validator or the issue signals genuinely HARD work — **architectural / cross-cutting /
     ambiguous-design, a multi-component or concurrency bug, or a ticket a prior worker already
     FAILED on** — escalate AUTOMATICALLY: run `python3 ~/devel/airuleset/airuleset.py fable-gate`
     ONCE for the ticket/batch; **gate OPEN (exit 0) → dispatch `model: fable`; gate CLOSED (exit 1)
     → dispatch `model: opus`.** Merely non-trivial (but not HARD-criteria) work → `model: opus`, no
     gate needed. Never dispatch an automatic `model: fable` without the gate check, and do NOT
     reflexively uptier a routine ticket — Sonnet + the Opus review bookend carries it. You (the
     main session) re-verify every line of the worker's evidence block regardless.
   - **The dispatch RETURNS IMMEDIATELY** (background) — do NOT block waiting. End the turn
     `⏳ WORKING`; the worker RE-INVOKES this loop when it completes (then you do Step 4).
   - **Serial per repo (hard).** Before dispatching, if a background `autopilot-worker` for THIS repo
     is STILL running (check the agent strip / running tasks), do **NOTHING** this turn — end
     `⏳ WORKING` and let it finish (it re-invokes you). NEVER dispatch a second worker on the same
     repo while one runs — two would collide on `dev`. (A batch is still ONE worker.)
3. The worker re-validates each batched issue is still real (`verify-issue-still-valid.md` — defense
   in depth on top of 1b), then runs ONE cycle for the whole batch on one `dev` branch: version bump
   → per-issue TDD (each bug RED→GREEN, each member committed with its own `Closes #<n>`) → ONE push
   → ONE CI → ONE PR whose body `Closes` every member → merge per `pr-merge-policy.md` → deploy
   verify. It **asks you directly** on any genuine design / scope / authorization call — but FIRST it
   runs the ownership gate (`ask-before-assuming.md`): a question goes to you ONLY if it is CONCEPTUAL
   (what to build / ambiguous intent / a product decision you have a stake in / irreversible), NEVER a
   TECHNICAL detail the worker should just decide (placement of a diagnostic element, which corner, a
   size, a default, layout of a debug overlay). Asking "súhlasíš s týmto rozmiestnením QR kódov?" is a
   banned over-ask — the worker decides that and proceeds. Answer the conceptual ones; the worker
   continues. **A question is a conversation, NOT an abandoned issue.** If a member turns
   out to violate the gate mid-flight (schema/API/security/cross-cut discovered), the worker DROPS it
   from this PR (leaves its issue open) and finishes the rest — the loop re-dispatches the dropped one
   solo later.

   > **Re-dispatch fresh — NEVER reach for `SendMessage` to "continue" a worker.** `SendMessage`
   > (the documented subagent-continuation tool) is gated behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
   > and is **NOT exposed by default**, so a call returns "no such tool" and you cold-start anyway.
   > Do **not** narrate "SendMessage isn't available here, dispatching a fresh worker" — just dispatch
   > the fresh background `autopilot-worker` for the issue and let it RESUME from durable state: the
   > existing `dev` branch, the open PR, and the issue's current state. It continues from there instead
   > of redoing version-bump→RED. The per-ticket Discord card is deduped on repo-name#issue, so a
   > fresh worker re-dispatched for the same issue does NOT double-post its card. A worker ending
   > mid-issue (turn boundary, error, your answer to its question) is recovered by ONE fresh dispatch
   > with the resume context in the prompt — never by a continuation tool, never by restarting from scratch.

   > **Multi-stage / long pipelines (e.g. a 3-branch `develop→staging→main` flow) — YOU own the CI
   > waits, not the worker.** A single worker cannot safely hold an hour-plus of successive CI waits:
   > a subagent that `run_in_background`-waits and ends its turn TERMINATES (the dominant worker
   > failure — its background task re-invokes YOU, not the dead worker), a foreground wait caps at
   > 10 min/call and bloats the worker's context, and the long lifetime is exposed to api-errors the
   > whole time. So for such repos the worker is BOUNDED PER STAGE — it does its stage's work →
   > pushes / opens the PR → reports the CI run-id + current stage in its evidence block → RETURNS.
   > YOU (the supervisor) then own the wait: poll the reported run-id with a `run_in_background`
   > bounded poll (you ARE the long-lived component — `run_in_background` re-invokes you and
   > `--resume` continues you, exactly why the wait is safe here and fatal inside a subagent), and
   > when CI is green dispatch the next short-lived worker for the next promotion (develop→staging,
   > staging→main, merge→deploy-verify). Serial-per-repo still holds (one active worker at a time);
   > each worker's lifetime just shrinks. This is the SANCTIONED pattern — not an improvisation. For a
   > plain 2-branch single-CI repo it isn't needed: the worker waits FOREGROUND through the one short
   > CI and runs the whole cycle itself.
4. When the worker returns its evidence block, **independently verify** from primary sources
   (never trust the claim). First read the worker's `dropped:` and `obsolete_closed:` lines and
   compute the **SURVIVING set** = batch members MINUS dropped MINUS obsolete-closed. Verify the ONE
   shared PR closed exactly the surviving set — a dropped / obsolete member is NOT a verify failure:
   - `gh pr view <PR> --json state,mergedAt,mergeCommit,closingIssuesReferences` — confirm EVERY
     **surviving** member is in `closingIssuesReferences` (dropped/obsolete members are NOT expected here)
   - `gh run list -b main -L 1 --json conclusion`
   - deployed version read from the live target (if there is a deploy)
   - `gh issue view <N> --json state` for EACH member: **surviving** → `closed`; **obsolete-closed**
     → `closed` (closed-with-evidence outside the PR, fine); **dropped** → `open` is CORRECT (it is
     re-dispatched solo next, not a failure)
   Confirmed → one line per surviving issue to `docs/autopilot-log.md`.
   > **The per-ticket Discord completion card is fired by the WORKER, after merge + post-deploy
   > verification — you do NOT send it by hand.** The worker runs `airuleset.py notify --run-card
   > --repo <owner/name> --issue <N> --goal "<plain goal>" --achieved "<plain what landed>" --version
   > "<deployed version read from the DOM>" --url "<Label=URL where the change shows>"` (one per member).
   > The card header is just `🎫 #N`; `--goal`/`--achieved` are PLAIN, simple, non-technical Slovak (NOT
   > the technical issue title); `--version` is the 📦 line; `--url` is the 🔗 deep link to SEE the change
   > live (the page/dashboard sub-page it's visible on — NOT a PR/diff link, the user doesn't want it).
   > `notify --run-card` gathers the remaining backlog from gh, takes `--achieved` as ✅ Dosiahnuté,
   > @mentions the tmux owner (zbynek/marek), and posts ONE Slovak card — deduped on repo-name#issue
   > (one card per ticket, re-dispatches never double-post). So the supervisor does NOT call `notify`;
   > just confirm the worker carded each merged member. The short `❓`/`✅` idle ping stays suppressed
   > (this loop turn ends `⏳ WORKING`).
5. **Immediately assemble the next batch** — including right after a merge. Do NOT stop to report
   between batches, do NOT re-run `/issue-planner`, do NOT `/compact`.

## Step 4 — When to actually STOP (only these)

- **Backlog empty** (no open non-skip issues) → run the **end-of-run reconciliation sweep (Step 4a)
  FIRST**, then the final completion report (`completion-report.md`).
- **Destructive / prod action** a worker surfaced that needs your approval
  (`no-destructive-remote-actions.md`, `approval-scope.md`).
- **A gate that won't go clean / the same CI failure twice** after a real fix attempt → surface
  it, never bypass (`autonomous-quality-discipline.md`).

A per-issue **design question is NOT a reason to abandon the issue, and NOT a reason to sit
idle-blocked when there is other work — it is a reason to ASK YOU (with a phone PING) the MOMENT the
issue needs it.** The user does NOT watch the terminal 24/7; **the Discord ping is the ONLY way the
question reaches them, so it MUST fire — every time, no exception (waking hours).** A question printed
but never pinged does NOT count as asked, and you may NEVER later stop the loop blaming the user's
silence. **Deliver every question as a SELF-CONTAINED `❓` text marker (NOT a 60-second
`AskUserQuestion` dialog — from a background worker it auto-continues in ~60 s so an away user never
answers; the `❓` marker pings AND waits UNLIMITED). Write it so someone with ZERO terminal context
understands it — which project + what it does, what happened, and EVERY cross-project / cross-ticket
link explained in plain Slovak (`user-questions-slovak.md`); never assume the user read the history or
knows two projects are related.** The SHAPE is hook-enforced (`stop-check-question-quality.sh`): the block opens `**Otázka — projekt …:**` and carries exactly ONE decision per ping — a ticket with several open questions asks them ONE at a time (next one after the previous answer arrives via Discord reply), never a `(1)/(2)/(3)` pile. Handle it BY THE CLOCK:

- **Waking hours — 06:00–23:59 Europe/Bratislava (check `TZ=Europe/Bratislava date +%H` → hour
  `06..23`): ASK NOW — it PINGS — then pick the honest form by whether OTHER work is available:**
  - **Other answer-independent work exists → ASK-AND-CONTINUE (the user's requested model).** Raise
    the question so it pings (`❓ ASKED: <q>` — Slovak, the real decision), track it DURABLY on the
    issue (`gh issue comment <N>` with the question + `gh issue edit <N> --add-label needs-answer`, so
    it is never lost in the scrollback), set THAT issue aside (paused, not abandoned), and move the
    loop to the next answer-independent ticket. End the turn `⏳ WORKING: <what you continue>`. When
    the user answers (any time), resume the paused issue from its DURABLE state (the open branch / PR /
    the `needs-answer` comment) per `subagent-continuation.md`. Give the user a genuine chance (~10
    min) before bulldozing a ticket that hinges on their taste — but do NOT block the whole loop for
    an answer you don't yet need.
  - **Nothing else is workable without the answer → BLOCK.** End the turn `❓ NEEDS YOU` (Slovak, the
    real decision) — it pings, and the `/goal` loop STOPS per its stop-condition (A) (waiting on the
    user is the terminal state; endless re-pokes of a blocked session were the camera-box chat
    wall). When the user answers, resolve THAT ticket first; then, if open non-skip issues remain,
    re-print the /goal line (same block as Step 2) with the conforming start question so the user
    re-arms the loop with one paste. Use the block only when the question truly blocks all
    remaining work.
  Do **NOT** grind on WITHOUT asking (burying the question) — ask-and-continue means you ASKED (pinged
  + tracked) FIRST, then continued. Do **NOT** write `❓ NEEDS YOU` and then move to another ticket
  anyway (that pings "I'm blocked" while you moved on — use `❓ ASKED` + `⏳`).
  - **Re-poked while STILL blocked on the SAME question** (the `/goal` evaluator or a
    task-notification re-fires a turn although nothing changed and the answer hasn't come): reply
    with **EXACTLY ONE LINE — the previous `❓ NEEDS YOU: <q>` line repeated VERBATIM,
    byte-identical.** NOTHING else: no apology, no "stojím a čakám" preamble, and **no re-printed
    question block** — every re-printed wall lands in the user's chat AGAIN (the camera-box chat
    spam, 2026-07-05). The device path dedups the identical line (no re-ping) and the shape gate
    recognizes the repeat (LASTQ match), so the one-liner passes untouched. A REWORDED repeat still
    counts as a new/edited question and is banned. A re-poke is never license to bulldoze the
    pending decision. (Stop-condition (A) in the /goal line means the evaluator should STOP instead
    of re-poking at all — the one-line reply is the damage bound if it misfires anyway.)
- **Sleep window — 00:00–05:59 Europe/Bratislava (hour `00..05`): DEFER, don't wake the user.** Queue
  the question (label `needs-decision`, leave the issue open), keep grinding the rest, end `⏳
  WORKING` with NO `❓ ASKED` line (so nothing pings). Raise the queued questions as ONE `❓ NEEDS YOU`
  once the window ends (after 06:00) or the user is next active.

"Nothing is hands-off" is **NOT a stop** — work the tickets; when one needs your decision, ASK it NOW
(ping) and either continue other work (ask-and-continue) or block if nothing else is workable.
Finishing a merge is **NOT a stop** — pick the next issue. An **unanswered pinged question is NEVER a
reason to stop the loop or blame the user** — it just waits (tracked on its issue, `needs-answer`)
while you work everything else; you reach the end only when the WHOLE backlog is either merged or
blocked on a pinged question, and even then you re-surface those as ONE `❓ NEEDS YOU`, never as a
reproach.

**BANNED rationalizations — both directions, kill both:**
- **Burying:** "there's other workable work so I'll move to the next ticket and ask later",
  "**pokračujem na ďalšom tickete, otázku položím neskôr**", "medzitým robím iné" — **WRONG when you
  did NOT ask+ping+track first.** Continuing is allowed ONLY after the question pinged the phone AND
  was recorded on the issue (`❓ ASKED` + `needs-answer` comment). Moving to a DIFFERENT ticket to
  AVOID asking is the banned defer — the user gets NO question and the important ticket never gets
  solved.
- **Reproach / false-stop:** "the loop is waiting on your answers so I'm stopping", "**skončil som,
  lebo tickety čakajú na tvoje odpovede**", surfacing hours-old questions the user was never pinged
  about as the reason for stopping — **WRONG.** Every question pings when raised; unanswered ones wait
  without blame while you do other work. (Post-MERGE "pick the next issue" is correct and DIFFERENT —
  that is continuing after a ticket is DONE, not skipping a ticket that needs your answer.)

## Step 4a — End-of-run reconciliation sweep (when the backlog goes empty, BEFORE the final report)

When the workable backlog empties, the run has just changed a lot of code while the context is still
fresh. Reconcile the WHOLE tracker NOW — **including the `autopilot-skip` issues** — so no ticket
lingers contradicting what the run achieved (`verify-issue-still-valid.md`, `no-dropped-work.md`).
This is a ONE-TIME sweep at completion, not a per-issue step; it runs once, then the report.

1. **List EVERY still-open issue, skips INCLUDED:** `gh issue list --state open -L 200` (do NOT filter
   out `autopilot-skip` here — the whole point is to re-examine them too). Gather what the run did from
   `docs/autopilot-log.md` (PRs merged this run + their `Closes #N`) so each validation has that context.
2. **Validate EACH remaining open issue against current reality** — dispatch the read-only
   **`ticket-validator`** (`subagent_type: ticket-validator`, prompt `Validate issue #<N> in <repo>;
   this run merged: <PR list + topics>`). They are independent → validate in parallel. Branch PER issue
   on its verdict (same hybrid close policy as Step 1b):
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR this run resolved it, OR a passing
     repro proves it) → **auto-close** with the validator's evidence as the closing comment
     (`gh issue close <N> --comment "<evidence — overcome by PR #M this run>"`) — reopenable in one
     click. This is the core ask: a skip / open ticket the run made moot gets closed.
   - **PARTIAL** (the run did some of it; real work remains) → do NOT close. **Rescope it non-
     destructively:** `gh issue comment <N> --body "Reconciled at /autopilot end: PR #M did <X>;
     remaining scope is <Y>."` so the ticket reflects reality. Leave it open (and, if it was an
     `autopilot-skip`, note the remaining scope on it — the user re-weighs skips at the next start).
   - **OVERCOME + `soft` / UNCLEAR** → do NOT auto-close — **ask the user** with the validator's
     evidence (`#N looks overcome by PR #M — close, rescope, or keep?`), act on the answer.
   - **STILL_VALID** → leave it as-is (a deliberately-skipped, still-relevant ticket stays skipped).
3. **NEVER prod/hardware-classify** any ticket in this sweep, and never close a skip just because it
   touches prod/hardware (`approval-scope.md`) — closure is driven ONLY by the validator's overcome
   evidence, never by a ticket's subject. Closing/commenting is non-destructive tracking → no approval
   needed for hard-overcome; everything uncertain goes to the user.
4. Then write the final completion report — listing what the sweep closed / rescoped / asked about.

## Watching & steering

The worker runs in the **background** (`run_in_background: true`), so your **main session stays FREE
and interactive while it works** (you can keep messaging it) AND it stays VISIBLE in the **agent
strip** (`main` + `autopilot-worker`, `↑/↓` to select, `Enter` to view). Its questions **surface in
your main session**, so you discuss the important calls; everything routine runs without you. (This
uses in-session subagents — the strip mechanism — NOT hidden `claude --bg` daemon sessions. Why
background not foreground: Claude Code 2.1.x makes a FOREGROUND dispatch BLOCK the main session —
you couldn't message it while a worker ran, CC issue #71768 — and its 2026-W26 change made
background-subagent prompts surface in the parent, removing the only reason foreground was used.)

## Context hygiene & resume

GitHub-as-state + `docs/autopilot-log.md` (re-read each cycle) hold the truth; workers return only
summaries so the main session stays thin and auto-compaction is harmless. Lasting conventions a
worker discovers go into the repo `CLAUDE.md`. If the session ends, `--resume` continues the
`/goal`; in-flight work is already on `dev`, so an unclosed issue just gets re-dispatched.

## Guardrails (hard — never relax)

- **Serial per repo.** ONE worker at a time — the two-branch workflow makes parallel same-repo
  workers collide on `dev`. The CI-cost win comes from BUNDLING many issues into that ONE worker's
  single PR/CI cycle (Step 3.1), **not** from running workers in parallel. (Different repos can each
  run their own `/autopilot`.)
- **Independent verification is mandatory** — a worker's "merged and deployed" counts only after
  the main loop re-reads PR/CI/version/issue state from primary sources (premature-done is the #1
  long-running-agent failure).
- **Gates are absolute** — no `--admin`, no bypass, no merge-despite (`autonomous-quality-discipline.md`).
