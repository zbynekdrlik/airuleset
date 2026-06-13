---
name: autopilot
description: "Usage: /autopilot [fleet|solo|status] [manual]. fleet (default) = /loop supervisor in this session dispatches each issue to a fresh claude --bg full-session worker (visible in agent view); solo = single-session /goal loop; status = show mode + backlog + currently-skipped issues, run nothing. manual modifier = stop at green PR this run. Merge/deploy follow pr-merge-policy.md default auto-merge (opt-out marker airuleset:merge=manual)."
argument-hint: "[fleet | solo | status] [manual]"
user-invocable: true
disable-model-invocation: true
---

# Autopilot — Hands-off Backlog Loop (Fleet)

> **Usage:** `/autopilot [fleet|solo|status] [manual]`
> • `fleet` *(default)* — orchestrator/worker: this session supervises via `/loop`; each issue runs in a fresh `claude --bg` daemon session (its own full window, visible + steerable in agent view)
> • `solo` — single-session `/goal` loop (no worker windows)
> • `status` — print resolved mode + backlog count + the currently-skipped issues (`gh issue list --state open --label autopilot-skip`), run nothing
> • `manual` modifier — stop every PR at green for the user's "merge it", this run only
> • **skip picker at start** — you pick existing issues to exclude (persistent `autopilot-skip` label); issues filed by workers mid-run are always worked

Works the GitHub issue backlog hands-off: pick issue → fresh worker implements (TDD) → PR → CI green → auto-merge → deploy verified → issue closed → orchestrator independently verifies → next issue. The main session's context holds only dispatch + verify summaries — no manual `/compact`, no degradation across a long backlog.

**Context gate — this skill IS the orchestration of these rules, apply all:**
- `pr-merge-policy.md` — default auto-merge; `airuleset:merge=manual` marker = stop at green PR
- `autonomous-batch-issue-development.md` — bundling gate; one PR per worker
- `tdd-workflow.md` / `regression-test-first.md` — calibrated TDD per issue
- `ci-monitoring.md` — workers monitor their OWN CI; the orchestrator NEVER polls CI (the /loop carve-out)
- `post-deploy-verification.md` / `version-on-dashboard.md` — deploys verified via the live DOM version
- `milestone-notifications.md` — ping per merged+deployed issue and on every stop-for-question
- `no-dropped-work.md` — workers file issues for everything identified but unfinished
- `ask-before-assuming.md` — real design questions stop the loop; process questions never

## Mode resolution

- `fleet` (default) requires the daemon: `claude agents --json` must work. Unavailable (remote/cloud session, Bedrock-style platform) → fall back to `solo` and say so in the banner.
- Merge behavior comes from `pr-merge-policy.md`: default auto; `airuleset:merge=manual` marker in the project CLAUDE.md or the `manual` modifier → every PR stops green for "merge it".
- The old `airuleset:autopilot=auto-merge` marker is superseded (auto is the default now); remove it when touching a CLAUDE.md that still has one.

## FLEET mode

### Step F1 — Preflight

```bash
git fetch origin && git rev-parse --abbrev-ref HEAD && git status --porcelain   # dev, clean
gh auth status && gh issue list --state open -L 50
claude daemon status && claude agents --json | head -5                          # daemon available
grep -n "airuleset:merge=manual" CLAUDE.md || true                              # merge mode
```

- **Print the mode banner FIRST**, e.g. `autopilot · FLEET · merge=auto (no manual marker) · /loop expires after 7 days, session must stay open`.
- **Backlog scope:** open issues NOT labeled `blocked` / `needs-design` / `needs-decision` / `question` / `wontfix` / `discussion` / `autopilot-skip`. Zero qualify → "backlog empty", STOP.
- **Skip picker (start-of-run exclusion — the one interactive step the user opted into).** Ensure the label exists once: `gh label create autopilot-skip --color ededed --description "Excluded from autopilot runs" 2>/dev/null || true`. First PRINT the FULL eligible open-issue list (after the label filter above) to the transcript — `#N <title> (Xd old)`, one per line — so the user can read off ANY number, not just the ones shown as checkboxes. Then ask which to EXCLUDE via `AskUserQuestion` with `multiSelect: true`, one option per issue (`#N <title> (Xd old)`). AskUserQuestion renders ~4 options per question, so either split the eligible issues across multiple ~4-option questions (up to the multi-question total) so all are selectable as checkboxes, or — when the backlog exceeds that — show the oldest/highest-priority subset and tell the user to add any other numbers via "Other" (comma-separated); the full printed list above lets them pick any issue, not only the shown subset. Apply to each selected/typed issue: `gh issue edit <N> --add-label autopilot-skip`, then print `skipping #A #B … · working N issues`. Selecting none = work all. The label is PERSISTENT — it survives restarts/`--resume` until removed (`gh issue edit <N> --remove-label autopilot-skip`), and the picker reappears each start so the set can be adjusted (`status` mode lists the currently-skipped issues). NEW issues filed by workers during the run never carry this label → always picked up on a later cycle.
- **One-time machine prerequisites** (tell the user once if missing):
  - `--permission-mode auto` must have been accepted interactively once on this machine (`claude --permission-mode auto`), else dispatched workers cannot run unattended.
  - Repo setting `worktree.bgIsolation: "none"` so workers commit directly on `dev` — no stray `.claude/worktrees/` branches (`two-branch-workflow.md`).
- **Write `.claude/loop.md`** (template in Step F3) and commit it.
- **Version-on-dashboard foundation gate** (web projects): no version label → that foundation issue is the FIRST work item (`version-on-dashboard.md`).

### Step F2 — Start the engine (the one manual paste)

The agent cannot type `/loop` — print this line and let the user paste it once:

```
/loop
```

Bare `/loop` runs `.claude/loop.md` self-paced (1m–60m adaptive). The loop ends itself when the backlog is empty and no worker is active (it simply stops scheduling the next wakeup).

### Step F3 — `.claude/loop.md` template (written by Step F1)

```markdown
# Autopilot fleet supervisor — one iteration

You are the ORCHESTRATOR. Never implement issues yourself; never poll CI yourself.

Backlog = `gh issue list --state open` MINUS issues labeled blocked/needs-design/needs-decision/question/wontfix/discussion/autopilot-skip. NEW issues filed by workers (no autopilot-skip label) join the backlog automatically on the next cycle — that is intended. NEVER add autopilot-skip yourself; it is the user's start-of-run exclusion only.

1. `claude agents --json` — list worker sessions (name prefix "ap-").
2. For each worker finished since the last iteration (state done/failed): INDEPENDENTLY
   verify from primary sources — never trust the worker's claim:
   - `gh pr view <PR> --json state,mergedAt,mergeCommit`   (merged?)
   - `gh run list -b main -L 1 --json conclusion`          (main CI green?)
   - deployed version read from the live target (curl /api/version or Playwright DOM read)
   - `gh issue view <N> --json state`                      (closed?)
   All confirmed → milestone ping (Discord reply if chat_id known, else PushNotification):
   "#N <title> merged → v<X> deployed, CI green"; append one line to docs/autopilot-log.md.
   Anything NOT confirmed → treat as stuck (step 4).
3. No active worker for this repo AND backlog non-empty → dispatch the next issue
   (highest priority first; bundling gate respected — bundle-safe singles by default,
   2-3 trivially related issues may share one worker):
   `cd <repo> && claude --bg --name "ap-<repo>-<N>" --permission-mode auto "<WORKER CONTRACT for issue #N>"`
4. Stuck/failed workers:
   - state blocked / waitingFor input → read it (`claude agents --json`, `claude logs <id>`);
     genuine design question → ❓ ping the user with the question text.
   - failed, or working > 3 h on a bundle-safe issue → read the log tail; ONE respawn with
     a refined contract MAX, then stop dispatching and ❓ ping the user. Never silently kill.
5. Backlog empty AND no active workers → final completion report; do NOT schedule the next
   wakeup (this ends the loop).
6. Otherwise schedule the next wakeup ~20–30 min (a worker typically needs 30–90 min — don't poll hot).
```

### Step F4 — Worker contract (the dispatch prompt template)

```
Work GitHub issue #<N> in <repo> end-to-end. You are a full autonomous session — all
global and project rules apply.

READ FIRST: this repo's CLAUDE.md, docs/autopilot-log.md (conventions + decisions so far),
then `gh issue view <N>` (body + all comments).

CYCLE (no pauses, no process questions):
1. git fetch origin; confirm dev branch + clean tree; version bump FIRST (version-bumping.md).
2. Implement issue #<N> ONLY — one issue, nothing else. TDD per tdd-workflow.md: bug →
   RED test commit then GREEN fix commit; feature → tests in the same PR; UI → Playwright E2E.
3. Search the codebase before assuming anything is missing — never re-implement what exists.
   NO placeholder or stub implementations. No scope creep.
4. Commit on dev with "Closes #<N>", push once, monitor YOUR CI run to terminal state
   (background `sleep N && gh run view <id>` — ci-monitoring.md).
5. PR dev→main; drive every gate green: CI all jobs, mergeable:true + mergeable_state:clean,
   /review AND /requesting-code-review both 0 🔴 0 🟡 0 🔵.
6. Merge per pr-merge-policy.md (default auto-merge; airuleset:merge=manual marker → stop
   at the green PR and report it instead). Monitor main CI + deploy workflow to terminal.
7. Post-deploy verification: open the live app, read the version label from the DOM,
   exercise the changed feature (post-deploy-verification.md).
8. Anything identified but not finished → gh issue create NOW (no-dropped-work.md). Use `needs-design` if its design is genuinely ambiguous; NEVER apply `autopilot-skip` (the user's start-of-run exclusion only) — an unlabeled new issue is meant to be worked next cycle.
9. Append ONE line to docs/autopilot-log.md (issue, SHAs, RED→GREEN test names, decisions).

FINAL MESSAGE = exactly this evidence block (the orchestrator re-verifies every line):
issue: #<N> <title>
pr: #<M> <url>
merge_sha: <sha | "NOT MERGED (manual marker)">
main_ci: <run-id> <conclusion>
deployed_version: <string read from DOM | "no deploy">
issue_state: <open|closed>
unverified: <list | "none">
filed: <#K list | "none">
```

### Fleet rules (hard — never relax)

- **Serial per repo.** ONE active worker per repo — the two-branch workflow makes parallel same-repo workers collide on `dev` (shared push target; pushes cancel each other's CI). Parallel across DIFFERENT repos is fine: run this pattern from each repo's own session.
- **The orchestrator never implements and never polls CI.** Implementation churn belongs in worker windows; CI watching belongs to the worker that pushed.
- **Independent verification is mandatory.** A worker saying "merged and deployed" counts ONLY after the orchestrator re-reads PR/CI/version/issue state from primary sources — premature-done claims are the #1 long-running-agent failure.
- **Continuity lives in durable files, not transcripts.** Workers read CLAUDE.md + docs/autopilot-log.md FIRST; lasting conventions get written back to CLAUDE.md; per-issue notes to the log. In-session `Agent`/`Task` subagents remain BANNED for issue implementation — they boot with a reduced system prompt and no rules. A daemon worker is a FULL session (full system prompt, all rules, hooks), which is exactly why it may implement.
- **Gates are absolute** — no `--admin`, no bypass, no merge-despite (`autonomous-quality-discipline.md`).

## SOLO mode (fallback engine)

Single-session loop — for remote/cloud sessions without the daemon, or 1–2-issue backlogs.

1. Preflight as F1 minus the daemon checks (includes the start-of-run skip picker).
2. Print the `/goal` line for the user to paste:
   ```
   /goal Every open issue in this repo not labeled blocked/needs-design/needs-decision/autopilot-skip is closed via a merged PR — proven in the transcript by `gh issue list --state open --search "-label:autopilot-skip -label:blocked -label:needs-design -label:needs-decision"` showing none remain AND `gh run list -b main -L 1` showing main green — or stop after 30 turns, or stop and ask the moment a genuine design choice, a destructive action, or a CI failure unfixable in two attempts arises.
   ```
3. Loop body per cycle: pick the bundle-safe batch (same label exclusions as F1, incl. `autopilot-skip`; new worker-filed issues are picked up) → implement on the MAIN loop (TDD) → one push → monitor CI → PR → gates green → merge per `pr-merge-policy.md` → verify deploy → milestone ping → re-print `gh issue list --state open --search "-label:autopilot-skip -label:blocked -label:needs-design -label:needs-decision"` (evidence) → next batch. When the MAIN loop files an issue mid-run (`no-dropped-work.md`), NEVER apply `autopilot-skip` to it — that label is the user's start-of-run exclusion only; use `needs-design` only if its design is genuinely ambiguous, so the new issue stays in the backlog and is worked on a later cycle.
4. Context hygiene: GitHub-as-state + `docs/autopilot-log.md` re-read at the top of every cycle. The transcript carries summaries only; compaction is harmless because GitHub + the log hold the truth. Read-only `Explore` subagents MAY take heavy searches; implementation stays on the main loop.

## Stop-and-ask gate (both modes — the only real questions)

- Genuine design choice on an issue (`ask-before-assuming.md`)
- Destructive remote action needed (`no-destructive-remote-actions.md`)
- Same CI failure twice after a real fix attempt — surface it, never bypass
- A gate that won't go clean — surface it; never merge "despite"
- Backlog exhausted → final completion report

Every stop ALSO pings the user (`milestone-notifications.md`): `autopilot paused — #51 needs a design call: <question>` / `autopilot done — backlog empty, N issues merged`.

NOT questions (pre-answered, `ask-before-assuming.md`): "ready for the next issue?", "should I bundle?", "push now?", "monitor CI?", "verify with Playwright?", "merge now?" — decide and proceed.
