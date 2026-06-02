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

## Step 1 — Preflight (always)

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD          # must be dev (or project's dev-equivalent)
git status --porcelain                   # must be empty (clean tree)
gh auth status                           # must be authenticated
gh run list -L 3                         # no run we'd fight; note in-progress
```

- **Mode detection** — read the project's own `CLAUDE.md`:
  ```bash
  grep -n "airuleset:autopilot=auto-merge" CLAUDE.md
  ```
  Present → `MODE=auto-merge` (hands-off loop). Absent → `MODE=manual` (one batch,
  stop at green PR).
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

## Step 3 — Per-issue cycle (inside a batch)

For each issue, no prompts between issues (`autonomous-batch-issue-development.md`):

1. `gh issue view <N>` — read body + comments.
2. Brainstorm only if genuinely non-trivial; plan only if multi-step. These may ask
   **design** questions when scope is ambiguous — never **process** questions.
3. **TDD** — bug fix → RED test commit first, then GREEN fix commit (`regression-test-first.md`).
   Feature → tests alongside. UI → Playwright E2E (`e2e-real-user-testing.md`).
4. Local cheap checks only (fmt/lint/`cargo check`); CI compiles/tests (`no-local-builds.md`).
5. Commit with `Closes #<N>`. Immediately start the next issue in the batch.

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

Auto-compact keeps the session alive — the active `/goal` persists; no manual `/compact`
needed. If the session ends, `--resume` restores the goal (turn/timer/token counters
reset; bound it again if needed). **Never `/clear` mid-loop** — `/clear` and `/goal stop`
cancel the goal. Compaction-survival is not yet documented by Anthropic; `--resume`
restores regardless, so the loop is recoverable either way.
