---
name: autopilot
description: "Usage: /autopilot [status] [manual]. Hands-off loop that solves the WHOLE GitHub backlog. To cut long-CI cost it BUNDLES bundle-safe small issues into ONE worker run → ONE PR closing all → ONE CI cycle (the bundling gate decides; big/schema/API/security/cross-cut issues run solo). Each run is a FOREGROUND autopilot-worker subagent (fresh context, visible in the agent strip) that can ASK YOU the important questions directly. Never pre-filters needs-input issues and never refuses to start; after each run (incl. after merge) it picks the next batch. status = show backlog + skipped, run nothing. manual = stop every PR at green for your merge. Merge/deploy follow pr-merge-policy.md (opt-out airuleset:merge=manual). Start-of-run it reviews the skip set (asks which already-skipped issues to un-skip), lets you exclude more (autopilot-skip), and lets you interactively CLOSE obsolete issues. End-of-run (backlog empty) it does a reconciliation sweep over ALL remaining open issues INCLUDING skips — while context is fresh — closing/rescoping any ticket the run overcame (hard-overcome auto-closes with evidence; uncertain asks). You can also close any issue anytime via 'close #N (reason)'."
argument-hint: "[status] [manual]"
user-invocable: true
disable-model-invocation: true
---

# Autopilot — Hands-off Backlog Loop

> Solves the **ENTIRE** open backlog, one issue at a time. Each issue is handed to a
> **foreground `autopilot-worker` subagent** — fresh context (your main session stays thin),
> visible in the agent strip, and **able to ask you the genuinely-important questions
> directly**. After each issue completes (merged + deployed, or a question resolved), the loop
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
- `ci-monitoring.md` — the worker monitors its OWN CI to terminal; the main loop just verifies the result
- `post-deploy-verification.md` / `version-on-dashboard.md` — deploys verified via the live DOM version
- `milestone-notifications.md` — device pings ONLY on a worker's ❓ question or the FINAL ✅ (mobile-app model); per-phase progress (each merge/deploy) → the BOARD, never a per-issue device ping
- `no-dropped-work.md` — workers file issues for everything identified but unfinished
- `verify-issue-still-valid.md` — the worker FIRST proves the issue still reproduces against current code + live system; obsolete/already-solved tickets get closed with evidence, never blindly implemented
- `ask-before-assuming.md` — a genuine per-issue question is a CONVERSATION with you, NOT a reason to abandon the issue or stop the loop

## How it works

- **Live board at `http://10.77.9.21:8787/`.** Workers self-report each phase; the supervisor reports
  the planned queue + its verify verdicts. The board shows the live tickets, the review-gate audit, and
  the planned "Up next" queue. Reporting is fire-and-forget — it never blocks or gates the loop.
- **Engine = a `/goal` loop you paste once.** Each turn the main agent assembles the next BATCH
  (one bundle-safe issue, or several bundled into one PR — see Step 3.1) and dispatches ONE
  foreground `autopilot-worker` for it; the worker runs the full cycle on one `dev` branch / one PR
  / one CI run (and asks you if needed); the main agent verifies the result from GitHub; the next
  turn picks the next batch — until the backlog is empty.
- **Bundling cuts CI cost.** CI is long here, so the loop spends ONE CI cycle on as many
  bundle-safe issues as the gate allows (`autonomous-batch-issue-development.md`) instead of
  one-PR-per-issue. Issues that fail the gate (large / schema / API / security / cross-cut) run solo.
- **Worker = foreground `autopilot-worker` subagent** (user-level, installed by airuleset).
  Foreground so its questions and prompts reach YOU; fresh context so your main session never
  degrades; it returns only a short evidence block to the main agent.
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
- **Print a one-line banner:** `autopilot · merge=auto (no manual marker) · N issues · solving the whole backlog · board http://10.77.9.21:8787/`.
- **Report the planned queue** so the board's "Up next" is current — after computing the ordered backlog
  (open issues minus `autopilot-skip`), at loop START and after each issue completes:
  `python3 ~/devel/airuleset/airuleset.py report --queue --repo <repo> --items '[[<issue>,"<title>"],…]'`.
  `<repo>` MUST be the canonical **`owner/name`** (`gh repo view --json nameWithOwner -q .nameWithOwner`) — a bare name is rejected by the board.
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
backlog + planned queue, and report the closures to the board (`milestone-notifications.md` — no per-issue device ping). **Default =
close none.** Same ~4-options-per-question / "Other" handling as the picker above. (You can ALSO close any
issue at any time — in `/autopilot` or normal chat — by telling Claude `close #N (reason)`; it runs
`gh issue close <N> --comment "<reason>"` + ping. Closing an issue is non-destructive tracking and never
needs extra approval.)

## Step 2 — Start the engine (the one manual paste)

The agent cannot type `/goal` — print this line for the user to paste once:

```
/goal Every open issue in this repo not labeled autopilot-skip is closed via a merged PR — proven in the transcript by `gh issue list --state open --search "-label:autopilot-skip"` showing none remain AND `gh run list -b main -L 1` showing main green — or stop only when I must answer a design choice, approve a genuinely-irreversible action (host reboot / data deletion / DB drop — NOT a deploy, a prod test, or restarting the app/device you're testing), or a CI failure stays unfixable after two real attempts. Never gate, classify, skip, or warn based on prod-usage / events / off-air / hardware — I alone guard whether prod is live. Do NOT stop merely because an issue needs my input: dispatch its foreground autopilot-worker, which asks me directly, and after every merge immediately pick the next issue.
```

The condition lists ONLY `autopilot-skip` as the exclusion, so `needs-design` / `needs-decision`
/ `question` issues all count toward "must be closed" — the loop works them WITH your input.

**This is the LAST thing `/autopilot` does.** Present the `/goal` line prominently in a code block,
tell the user to paste it to start the loop, and **STOP** — end your message with
`❓ NEEDS YOU: paste the /goal line above to start the autopilot loop`. Do **NOT** proceed to
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
     Report the chosen batch members on the planned queue so the board's "Up next" reflects the bundling.
1b. **VALIDATE EACH batch member FIRST — hard gate** (`verify-issue-still-valid.md`). Before dispatching
   the worker, dispatch the read-only **`ticket-validator`** subagent
   (`subagent_type: ticket-validator`, prompt `Validate issue #<N> in <repo>`) for EVERY member — they
   are independent, so validate them in parallel. Branch PER member:
   - **STILL_VALID** → keep in the batch. **PARTIAL** → keep, pass its `still_to_do` as that issue's scope.
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR resolved it OR a passing repro proves it) →
     do NOT implement; **auto-close** the issue with the validator's evidence as a closing comment,
     report it to the board (`R=$(python3 ~/devel/airuleset/airuleset.py report --start --repo <r> --issue <N>
     --title "<title>") ; python3 ~/devel/airuleset/airuleset.py report --run "$R" --phase obsolete-closed
     --result "<validator evidence>"`) — board only, no device ping (reopenable in one click) — and DROP it from the batch.
   - **OVERCOME + `overcome_confidence: soft`** → DROP from the batch and ask the user ("looks overcome by
     <evidence> — close it?") with the validator's evidence; act on their answer (close, or run it solo).
   - **UNCLEAR** → DROP from the batch and ask the user, quoting the validator's `premise_check` so nothing
     already-answered is re-asked. **One unclear/overcome member must NOT block the rest of the batch** —
     pull it out and proceed with the surviving STILL_VALID / PARTIAL members.
   (Hybrid close policy: auto-close ONLY clear-cut hard-overcome; everything uncertain goes to the user.)
   After validation, the batch = the surviving STILL_VALID / PARTIAL members. This stops the recurring
   failure (working / re-asking on an already-overcome ticket).
2. **Dispatch ONE FOREGROUND `autopilot-worker`** via the Agent tool for the WHOLE batch:
   `subagent_type: autopilot-worker`, **NOT** run in the background (foreground lets it ask you),
   prompt = `Work issues #A #B #C in <repo> as ONE bundled PR (Closes all).` (or
   `Work issue #<N> in <repo>.` for a solo batch) plus any repo-specific note. ONE worker, ONE `dev`
   branch, ONE PR, ONE CI cycle — **serial per repo still holds** (a batch is still ONE worker). It
   shows in the agent strip as `autopilot-worker`.
3. The worker re-validates each batched issue is still real (`verify-issue-still-valid.md` — defense
   in depth on top of 1b), then runs ONE cycle for the whole batch on one `dev` branch: version bump
   → per-issue TDD (each bug RED→GREEN, each member committed with its own `Closes #<n>`) → ONE push
   → ONE CI → ONE PR whose body `Closes` every member → merge per `pr-merge-policy.md` → deploy
   verify. It **asks you directly** on any genuine design / scope / authorization call. Answer it;
   the worker continues. **A question is a conversation, NOT an abandoned issue.** If a member turns
   out to violate the gate mid-flight (schema/API/security/cross-cut discovered), the worker DROPS it
   from this PR (leaves its issue open) and finishes the rest — the loop re-dispatches the dropped one
   solo later.

   > **Re-dispatch fresh — NEVER reach for `SendMessage` to "continue" a worker.** `SendMessage`
   > (the documented subagent-continuation tool) is gated behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
   > and is **NOT exposed by default**, so a call returns "no such tool" and you cold-start anyway.
   > Do **not** narrate "SendMessage isn't available here, dispatching a fresh worker" — just dispatch
   > the fresh foreground `autopilot-worker` for the issue and let it RESUME from durable state: the
   > existing `dev` branch, the open PR, and the issue's current state. It continues from there instead
   > of redoing version-bump→RED. The board collapses re-dispatches to the **newest run per issue**, so
   > a fresh worker does NOT clutter the board with duplicate cards. A worker ending mid-issue (turn
   > boundary, error, your answer to its question) is recovered by ONE fresh dispatch with the resume
   > context in the prompt — never by a continuation tool, never by restarting from scratch.
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
   Report the supervisor's verdict to the board **per member** — the run ids were minted inside the
   worker, so resolve each from durable state by repo+issue (the reporter persists the repo#issue→run
   map): `for N in <surviving members>; do python3 ~/devel/airuleset/airuleset.py report --repo <repo>
   --issue "$N" --review supervisor-verify=ok|fail; done` (the report CLI resolves `--repo --issue` to
   the started run). Dropped / obsolete members were already terminalized by the worker — do not
   re-verdict them. Confirmed → ONE board milestone update naming all bundled issues
   (`merged #A (topic) + #B (topic) → vX`) + one line per surviving issue to `docs/autopilot-log.md`.
   **No per-issue device ping** (`milestone-notifications.md`) — the device pings automatically only
   on a worker's ❓ question or the FINAL ✅ when the whole backlog is done.
5. **Immediately assemble the next batch** — including right after a merge; re-report the planned
   queue (`report --queue …`, see Step 1) so the board's "Up next" stays current. Do NOT stop to
   report between batches, do NOT re-run `/issue-planner`, do NOT `/compact`.

## Step 4 — When to actually STOP (only these)

- **Backlog empty** (no open non-skip issues) → run the **end-of-run reconciliation sweep (Step 4a)
  FIRST**, then the final completion report (`completion-report.md`).
- **Destructive / prod action** a worker surfaced that needs your approval
  (`no-destructive-remote-actions.md`, `approval-scope.md`).
- **A gate that won't go clean / the same CI failure twice** after a real fix attempt → surface
  it, never bypass (`autonomous-quality-discipline.md`).

A per-issue **design question is NOT a stop** — the worker asks you inline and continues. "Nothing
is hands-off" is **NOT a stop** — work it WITH your input. Finishing a merge is **NOT a stop** —
pick the next issue.

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
     (`gh issue close <N> --comment "<evidence — overcome by PR #M this run>"`), report it to the board
     (reopenable in one click). This is the core ask: a skip / open ticket the run made moot gets closed.
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
4. Report each closure to the board (`report --repo <r> --issue <N> --phase obsolete-closed --result
   "<evidence>"`) so a card with a live run is finalized. A never-worked skip has no run, so that
   report is a harmless no-op — once it's closed on GitHub the refresher prunes it from the board's
   open/queue set anyway. Then write the final completion report — listing what the sweep closed /
   rescoped / asked about.

## Watching & steering

The worker is foreground, so its questions appear **inline** in your session and it shows in the
**agent strip** (`main` + `autopilot-worker`, `↑/↓` to select, `Enter` to view). You discuss the
important calls with the worker as it works; everything routine runs without you. (This uses
in-session subagents — the strip mechanism — NOT `claude --bg` daemon sessions.)

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
