---
name: autopilot
description: Work an entire GitHub issue backlog hands-off — pick bundle-safe issues, implement with TDD, open a PR, drive CI green, then (only on projects that opted in via the airuleset:autopilot=auto-merge marker) merge dev→main and pick the next, looping via /goal until the backlog is empty or a genuine decision is needed. On projects without the marker it runs one batch to a green PR and stops for your merge. Run when you want autonomous batch development across a project's open issues without per-batch prompts.
user-invocable: true
disable-model-invocation: true
---

# Autopilot — Hands-off Backlog Loop

Removes the three per-batch interruptions: re-invoking `/issue-planner`, approving
each merge, and manual `/compact`. Picks issues, implements (TDD), PRs, drives CI
green, merges (if the project opted in), and continues to the next — until the
backlog is empty or a **genuine** question is needed.

**Context gate — this skill IS the orchestration of these rules, apply all:**
- `autonomous-batch-issue-development.md` — bundling gate, one PR per batch, no between-issue prompts
- `pr-merge-policy.md` — the per-project auto-merge exception + absolute no-bypass gates
- `complete-planned-work.md` / `autonomous-quality-discipline.md` — keep going until gates green; never shortcut
- `tdd-workflow.md` / `regression-test-first.md` — RED-before-GREEN per issue
- `ci-monitoring.md` / `ci-push-discipline.md` — one push per batch, monitor to terminal
- `ask-before-assuming.md` — what counts as a real question vs a pre-answered process pause
- `post-deploy-verification.md` / `version-on-dashboard.md` — verify after any merge that deploys
- `milestone-notifications.md` — ping the user (Discord/push) at each phase, not just at the end
- claude-code-tooling.md `#### Autonomous Goals (/goal)` — the loop engine

## Two pieces — and the one manual step

1. **This skill** = the loop body + guardrails (what the agent does each cycle).
2. **`/goal`** = the native engine that re-runs the agent turn-after-turn with no
   prompts, until a fast evaluator confirms the condition (reading ONLY the
   transcript). **The agent cannot set a goal — only you can type `/goal`.** So in
   hands-off mode this skill PRINTS the exact, repo-tuned `/goal` line; you paste it
   once and step away. That single paste is the only manual step.

## Arguments & mode (auto vs manual)

`/autopilot` accepts an optional first argument that sets the mode FOR THIS RUN:

- `/autopilot auto` (aliases `auto-merge`, `hands-off`) → **auto-merge** — the loop
  merges `dev`→`main` itself when every gate is green. Typing it IS your explicit merge
  authorization for this run (`pr-merge-policy.md`), the same as the persistent marker.
- `/autopilot manual` → **manual** — run one batch to a green PR, stop for your merge.
- `/autopilot` (no arg) → mode comes from the project marker if present, else **manual**.
- `/autopilot status` → print mode + marker + backlog count and STOP (run nothing).

**Mode resolution precedence:** explicit argument > project marker
(`airuleset:autopilot=auto-merge` in the repo's `CLAUDE.md`) > default **manual**.

The default is manual on purpose — auto-merge to `main` unattended is safety-sensitive,
so it must be opted into deliberately (per-run arg, or per-repo marker), never assumed
just because `/autopilot` was invoked. Prefer the marker for low-risk MVPs you always
run hands-off; use the `auto` arg for a one-off hands-off run; keep manual for anything
with real production data.

## Step 1 — Preflight (always)

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD          # must be dev (or project's dev-equivalent)
git status --porcelain                   # must be empty (clean tree)
gh auth status                           # must be authenticated
gh run list -L 3                         # no run we'd fight; note in-progress
```

- **Resolve mode** (precedence: arg > marker > manual):
  ```bash
  grep -n "airuleset:autopilot=auto-merge" CLAUDE.md   # marker check
  ```
  Explicit `auto`/`manual` arg wins; else marker present → `MODE=auto-merge`; else
  `MODE=manual`.
- **PRINT the mode banner FIRST** (before any work) so the resolved mode is always
  visible and the user knows how to change it — e.g.:
  ```
  autopilot · MODE=manual  (source: default — no `auto` arg, no marker)
  → switch: `/autopilot auto` for this run, or add `<!-- airuleset:autopilot=auto-merge -->`
    to CLAUDE.md to make it permanent. manual = one batch → green PR → stop for your merge.
  ```
  State the source explicitly (arg / marker / default) so "why this mode?" is answered
  on screen, not hidden in a file.
- **Load the decision log** — `cat docs/autopilot-log.md` if it exists (create it on the
  first run). This + the project `CLAUDE.md` re-load the conventions and decisions from
  earlier work so the loop never re-litigates a settled call (see `## Compaction & resume`).
- **Version-on-dashboard foundation gate** (web projects) — per `version-on-dashboard.md`
  / issue-planner Step 1d. If the dashboard has no version label, the foundation issue
  is the FIRST work item; file it before anything else.
- **Backlog scope** — `gh issue list --state open`. The work-list is every open issue
  that is NOT labeled `blocked` / `needs-design` / `needs-decision` / `question` /
  `wontfix` / `discussion`, and that passes the bundling gate on inspection. If zero
  qualify → report "backlog empty, nothing to do" and STOP (do not set a goal).

## Step 2 — Branch on mode

### MODE=manual (default — no marker)

Merging needs the user, so do NOT set a `/goal` loop (it would spin at the merge
gate). Instead run ONE bundle-safe batch, then stop:

1. Select the next bundle-safe batch (issue-planner bundling gate: ≤300 LoC each, no
   schema/API/security/cross-cut, independent).
2. Run the per-issue cycle (Step 3) for each issue in the batch.
3. One push, monitor CI to green (Step 4).
4. Open/confirm the PR (`dev`→`main`), verify `mergeable: true` + `state: clean`,
   run both audit passes clean.
5. **Stop** and surface the green PR per `completion-report.md`. Wait for the user's
   explicit merge instruction. (To opt this repo into hands-off looping, the user adds
   the marker — see Step 6.)

### MODE=auto-merge (marker present)

Emit the ready-to-paste `/goal` line and let the native loop drive. Print exactly:

```
/goal Every open issue in this repo that is not labeled blocked/needs-design/needs-decision is closed via a merged PR — proven in the transcript by `gh issue list --state open` showing none of those issues remain AND `gh run list -b main -L 1` showing the latest main CI run green — or stop after 30 turns, or stop and ask me the moment a genuine design choice, a destructive action, or a CI failure I can't fix in two attempts arises.
```

Tune the label exclusions and turn bound to the repo before printing. Then run the
loop body (Step 3 + Step 4 + Step 5) every cycle until the condition holds.

## Step 3 — Per-issue cycle (main-loop implementer)

**The implementer is the main loop — NEVER a degraded in-session subagent.** Each
issue's implementation runs in the primary Opus 4.8 `/goal` loop itself, so every
commit is written at full main-loop quality (full system prompt, all rules loaded,
can ask the user, can itself dispatch `superpowers:subagent-driven-development` one
supported level deep). An in-session `Agent`/`Task` subagent boots with a REDUCED
system prompt, isolated fresh context, and no user channel — a different harness, not
a replica of the main loop — and it cannot spawn `subagent-driven-development` (no
`Agent` tool inside a subagent). So implementation is NEVER routed through a subagent.

**Context hygiene comes from GitHub-as-state, not a subagent boundary.** The backlog,
commit SHAs, and PR/CI status are re-derived with `gh` every cycle, so the loop carries
almost no cross-issue memory and re-reads each issue fresh — mush can't accumulate. See
`## Compaction & resume` for why this is safe under auto-compact.

For each issue in the batch, no prompts between issues (`autonomous-batch-issue-development.md`):

1. `gh issue view <N>` — read body + comments; confirm it fits the bundling gate
   (≤300 LoC, no schema/API/security/cross-cut, independent). On inspection it
   doesn't → stop-and-ask or file solo (Step 5); never silently absorb the surprise.
2. **Implement on the main loop** (this is the quality-critical work):
   - **TDD** — bug fix → RED test commit first, then GREEN fix commit
     (`regression-test-first.md`); feature → tests alongside; UI → Playwright E2E
     (`e2e-real-user-testing.md`).
   - For a multi-step issue, the main loop MAY dispatch
     `superpowers:subagent-driven-development` directly — its implementer/reviewer
     subagents are first-level children of the main loop (supported, one level deep).
   - Local cheap checks only (fmt/lint/`cargo check`); CI compiles/tests
     (`no-local-builds.md`).
   - Commit on `dev` with `Closes #<N>`.
3. **Append ONE terse line to `docs/autopilot-log.md`** (issue #, commit SHA(s),
   RED→GREEN test names, any scope/decision note) — NOT the file-reads/edit churn. If the
   issue established a lasting project convention, also write it to `CLAUDE.md`. Then
   immediately start the next issue, re-deriving remaining state from `gh` + the log,
   never from accumulated transcript.

**Read-only fan-out is the ONLY allowed in-session subagent use.** Heavy, low-value
exploration (multi-file code search, cross-issue triage, dependency mapping) MAY be
delegated to read-only `Explore` subagents so their token churn never lands in the
loop's context. Quality-critical implementation NEVER goes to a subagent and NEVER to a
fresh `claude -p` process — only to this continuous main loop (see `## Compaction & resume`
for why fresh/amnesiac workers are banned).

## Step 4 — Push, CI, merge

1. One `git push` for the whole batch.
2. Monitor CI to terminal — `sleep N && gh run view <id>` in background, ALL jobs
   (`ci-monitoring.md`). Print the status into the transcript (evidence for the
   evaluator).
3. **CI red** → `gh run view --log-failed`, fix root cause, repush once. Same failure
   twice → STOP and ask (Step 5).
4. **CI green + `mergeable: true` + `state: clean` + `/review` clean + `/requesting-code-review` clean:**
   - `MODE=auto-merge` → merge `dev`→`main` (merge commit, **never** `--admin` / no
     bypass). Monitor main CI + any deploy to terminal. If the merge deploys a UI,
     run `post-deploy-verification.md` (Playwright, read version label). Sync `dev`,
     bump version (`version-bumping.md`).
   - `MODE=manual` → stop, surface the PR, wait for "merge it".
5. **Milestone ping** (`milestone-notifications.md`) — after an auto-merge lands, notify
   the user: `merged #N+#M to main → deployed vX.Y.Z-dev.k, CI green`. Discord `reply`
   if a chat_id is in session, else `PushNotification` proactive. One line, not every
   commit — once per merged batch.
6. Re-print `gh issue list --state open` (closed issues now gone) — evidence the
   backlog shrank. Loop continues to the next batch.

## Step 5 — Stop-and-ask gate (the "real question")

STOP the loop and ask (or surface clearly) ONLY for:

- **Ambiguous scope** on an issue — a genuine design choice (`ask-before-assuming.md`).
- **Destructive remote action** needed (`no-destructive-remote-actions.md`).
- **CI failure not fixable** after one real fix attempt (2nd identical failure) —
  surface the log, do NOT bypass, do NOT loosen the gate.
- **Design conflict** between two issues in the batch.
- **A gate won't go clean** in auto-merge mode — surface it; never merge "despite".
- **Production deploy** that the project's pipeline does not perform automatically —
  separate approval (`approval-scope.md`).
- **Backlog exhausted** → report done per `completion-report.md`.

Every stop above ALSO fires a milestone ping (`milestone-notifications.md`) so the user
is pulled back: `autopilot paused — #51 needs a design call: <the question>` on a
stop-for-question, or `autopilot done — backlog empty, N issues merged to main` when
finished.

NOT reasons to stop (pre-answered NO — `ask-before-assuming.md`): "ready for the next
issue?", "should I bundle?", "commit before next?", "push now or later?", "should I
monitor CI?", "verify with Playwright?". Decide and proceed.

## Step 6 — Opting a project into hands-off auto-merge

The user enables hands-off merging for one repo by adding to that repo's `CLAUDE.md`
(matching the local-build tier-marker convention):

```markdown
## Autopilot

<!-- airuleset:autopilot=auto-merge -->

**Autopilot auto-merge ENABLED.** `/autopilot` may merge dev→main itself when every
gate is green (CI all-green, mergeable+clean, both audits clean). Reason: <one line>.
```

The marker IS the standing merge authorization for that repo (`pr-merge-policy.md`).
Reserve it for low-risk projects; anything with real users/production data should stay
manual. The agent does NOT add this marker on its own — the user opts in deliberately.

## Guardrails (hard — never relax)

- **Gates are absolute.** Auto-merge requires ALL of: CI every job green; `mergeable: true`
  AND `mergeable_state: "clean"`; `/review` AND `/requesting-code-review` both 0 🔴 0 🟡 0 🔵.
  Any miss → fix or stop. NEVER `--admin`, NEVER bypass branch protection, NEVER merge
  "despite" (`autonomous-quality-discipline.md`).
- **One feature/batch = one PR** — no progressive multi-PR rollouts
  (`autonomous-batch-issue-development.md`).
- **Deploy ≠ merge.** Merging is authorized by the marker; a separate production deploy
  is not, unless the project's own merge→main pipeline does it automatically
  (`approval-scope.md`).
- **Bounded loop.** The `/goal` line MUST include `…or stop after N turns`. No built-in cap.

## Compaction & resume

**Context hygiene = GitHub-as-state + a re-read decision log, not a subagent boundary.**
The loop re-derives its state from durable sources each cycle, so it holds almost no
long-lived transcript memory:

- **Backlog** — `gh issue list --state open` every cycle; the open set IS the to-do
  list. Closed issues vanish from it — that's the durable progress signal.
- **CI** — poll `gh run view --json status,conclusion,jobs`; dump full logs ONLY on a
  failure (`--log-failed`, for the fix decision). Never tail green-run logs into context.
- **Per issue** — append ONE terse line to `docs/autopilot-log.md` (issue #, commit
  SHA(s), RED→GREEN test names, merge status, decision notes). Don't re-summarize the
  whole history into the transcript each turn — the log IS the summary, and it survives
  compaction.

Because the working set stays small, the auto-compact threshold is rarely approached.
And when compaction DOES fire it is harmless here: it lands where the only state is the
terse summary, and even a lossy summary loses nothing real — commits, PR, and CI are all
re-readable from GitHub. (Note: auto-compaction is checked MID-request, NOT at safe task
boundaries, and is lossy — so a design that depended on in-transcript working memory
would be fragile. This design does not; GitHub holds the truth, which is what makes
mid-issue compaction non-catastrophic.)

The agent CANNOT self-`/compact` mid-turn (it is a user/SDK command, not an in-turn
tool) and MUST NEVER `/clear` mid-loop (`/clear` and `/goal stop` both cancel the goal).
If the session ends, `--resume` restores the `/goal` (turn/timer/token counters reset;
re-bind the goal line if needed). The loop is recoverable either way: in-flight work is
already committed on `dev`, so a killed session just re-dispatches the unclosed issues.

**One continuous session — NEVER spawn a fresh/amnesiac worker.** Do NOT drive issues
with `claude -p` child processes, and do NOT route implementation through `Agent`/`Task`
subagents. A fresh process or subagent starts WITHOUT the understanding built up in this
session — the decisions, constraints, and conventions worked out together — so it can
implement an issue in a way THIS session would not allow: wrong pattern, a re-litigated
decision, a violated convention. **Continuity of understanding beats pristine context.**
The single `/goal` loop keeps the whole conversation (compacted as needed), so accumulated
understanding persists across the entire run. There is no "backlog too large" escape that
justifies an amnesiac worker — if a session truly fills, `--resume` continues THIS thread
(summary preserved), it does not start a blank one.

**Durable decision log — so compaction / resume never loses what was settled.** Compaction
is lossy and `--resume` reloads a summary, not the full thread. To stop the loop from
"forgetting what we dealt with", persist durable context to files it RE-READS, not to
transcript memory:

- **Conventions / decisions that outlive the run** (how endpoints are named, why a pattern
  was chosen, a gotcha + its fix) → write to the project's **`CLAUDE.md`**. It loads into
  EVERY future session — the permanent fix to "the new session forgot X".
- **Run-scoped working notes** (per-issue decisions, scope calls — `issue #40 → reset to
  0dB per user`, `deferred Z to #88`) → append to a re-read log: **`docs/autopilot-log.md`**.
  The loop reads this log at the TOP of every cycle, so even right after a compaction it
  re-loads the full picture of what was decided and done.

At each cycle start the loop reconstructs the world from durable sources that no compaction
can erase — GitHub (issue / PR / CI state), the decision log (the WHY + conventions), and
`git log` on `dev` (what code changed). That, not a clean transcript, is what keeps every
cycle consistent with everything agreed earlier in the session.
