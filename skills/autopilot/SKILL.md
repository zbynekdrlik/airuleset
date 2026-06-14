---
name: autopilot
description: "Usage: /autopilot [status] [manual]. Hands-off loop that solves the WHOLE GitHub backlog one issue at a time. Each issue runs in a FOREGROUND autopilot-worker subagent (fresh context, visible in the agent strip) that can ASK YOU the important questions directly. Never pre-filters needs-input issues and never refuses to start; after each issue (incl. after merge) it picks the next. status = show backlog + skipped, run nothing. manual = stop every PR at green for your merge. Merge/deploy follow pr-merge-policy.md (opt-out airuleset:merge=manual). Issues you don't want touched at all: label autopilot-skip (start-of-run picker)."
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
- `pr-merge-policy.md` — default auto-merge; `airuleset:merge=manual` marker (or the `manual` arg) = stop at green PR
- `tdd-workflow.md` / `regression-test-first.md` — calibrated TDD per issue
- `ci-monitoring.md` — the worker monitors its OWN CI to terminal; the main loop just verifies the result
- `post-deploy-verification.md` / `version-on-dashboard.md` — deploys verified via the live DOM version
- `milestone-notifications.md` — ping per merged+deployed issue and on every stop-for-approval
- `no-dropped-work.md` — workers file issues for everything identified but unfinished
- `verify-issue-still-valid.md` — the worker FIRST proves the issue still reproduces against current code + live system; obsolete/already-solved tickets get closed with evidence, never blindly implemented
- `ask-before-assuming.md` — a genuine per-issue question is a CONVERSATION with you, NOT a reason to abandon the issue or stop the loop

## How it works

- **Engine = a `/goal` loop you paste once.** Each turn the main agent dispatches ONE foreground
  `autopilot-worker` for the next open issue; the worker runs the full cycle (and asks you if
  needed); the main agent verifies the result from GitHub; the next turn picks the next issue —
  until the backlog is empty.
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
- **Print a one-line banner:** `autopilot · merge=auto (no manual marker) · N issues · solving the whole backlog`.
- **Version-on-dashboard foundation gate** (web projects): no version label → that foundation
  issue is the FIRST work item (`version-on-dashboard.md`).

### Step 1b — Skip picker (OPTIONAL start-of-run exclusion)

Only for issues you genuinely do **not** want touched at all this run — the default is *work
everything*. Ensure the label exists once: `gh label create autopilot-skip --color ededed
--description "Excluded from autopilot runs" 2>/dev/null || true`. PRINT the full open-issue list
(`#N <title> (Xd old)`, one per line) so the user can read off any number, then ask which to
EXCLUDE via `AskUserQuestion` with `multiSelect: true` (one option per issue). AskUserQuestion
renders ~4 options per question, so split across multiple ~4-option questions, or for a large
backlog show the oldest subset and let the user add any other numbers via "Other" (comma-separated)
— the printed list backs that. Apply to each chosen issue: `gh issue edit <N> --add-label
autopilot-skip`, then print `skipping #A #B … · working N issues`. **Selecting none = work all
(the normal case).** The label is PERSISTENT until removed (`gh issue edit <N> --remove-label
autopilot-skip`); the picker reappears each start so the set can be adjusted (`status` lists the
currently-skipped issues). NEW issues filed by workers never carry this label → always worked.

## Step 2 — Start the engine (the one manual paste)

The agent cannot type `/goal` — print this line for the user to paste once:

```
/goal Every open issue in this repo not labeled autopilot-skip is closed via a merged PR — proven in the transcript by `gh issue list --state open --search "-label:autopilot-skip"` showing none remain AND `gh run list -b main -L 1` showing main green — or stop only when I must answer a design choice, approve a destructive/prod action, or a CI failure stays unfixable after two real attempts. Do NOT stop merely because an issue needs my input: dispatch its foreground autopilot-worker, which asks me directly, and after every merge immediately pick the next issue.
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

1. Pick the next open issue not labeled `autopilot-skip` (highest priority / oldest first). 2-3
   trivially-related small issues MAY share one worker (one PR).
1b. **VALIDATE FIRST — hard gate** (`verify-issue-still-valid.md`). Before dispatching any worker,
   dispatch the read-only **`ticket-validator`** subagent (`subagent_type: ticket-validator`,
   prompt `Validate issue #<N> in <repo>`). Branch on its verdict:
   - **STILL_VALID** → proceed to step 2. **PARTIAL** → proceed but pass `still_to_do` as the worker's scope.
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR resolved it OR a passing repro proves it) →
     do NOT implement; **auto-close** the issue with the validator's evidence as a closing comment,
     milestone-ping it (it's reopenable in one click), and pick the next issue (skip step 2).
   - **OVERCOME + `overcome_confidence: soft`** → do NOT auto-close — ask the user ("looks overcome by
     <evidence> — close it?") with the validator's evidence; act on their answer.
   - **UNCLEAR** → ask the user, quoting the validator's `premise_check` so nothing already-answered is
     re-asked; do not dispatch the worker until resolved.
   (Hybrid close policy: auto-close ONLY clear-cut hard-overcome; everything uncertain goes to the user.)
   This stops the recurring failure (working / re-asking on an already-overcome ticket).
2. **Dispatch a FOREGROUND `autopilot-worker`** via the Agent tool: `subagent_type:
   autopilot-worker`, **NOT** run in the background (foreground lets it ask you), prompt =
   `Work issue #<N> in <repo>.` plus any repo-specific note. It shows in the agent strip as
   `autopilot-worker`.
3. The worker FIRST **validates the issue is still real** (`verify-issue-still-valid.md`) —
   reproduces it against current code + the live system; an obsolete / already-solved ticket is
   closed with evidence instead of implemented. Then it runs the full cycle (version bump → TDD →
   PR → CI green → merge per `pr-merge-policy.md` → deploy verify) and **asks you directly** on any
   genuine design / scope / authorization call. Answer it; the worker continues. **A question is a
   conversation, NOT an abandoned issue.**
4. When the worker returns its evidence block, **independently verify** from primary sources
   (never trust the claim):
   - `gh pr view <PR> --json state,mergedAt,mergeCommit`
   - `gh run list -b main -L 1 --json conclusion`
   - deployed version read from the live target (if there is a deploy)
   - `gh issue view <N> --json state`
   Confirmed → milestone ping (`milestone-notifications.md`) + one line to `docs/autopilot-log.md`.
5. **Immediately pick the next issue** — including right after a merge. Do NOT stop to report
   between issues, do NOT re-run `/issue-planner`, do NOT `/compact`.

## Step 4 — When to actually STOP (only these)

- **Backlog empty** (no open non-skip issues) → final completion report (`completion-report.md`).
- **Destructive / prod action** a worker surfaced that needs your approval
  (`no-destructive-remote-actions.md`, `approval-scope.md`).
- **A gate that won't go clean / the same CI failure twice** after a real fix attempt → surface
  it, never bypass (`autonomous-quality-discipline.md`).

A per-issue **design question is NOT a stop** — the worker asks you inline and continues. "Nothing
is hands-off" is **NOT a stop** — work it WITH your input. Finishing a merge is **NOT a stop** —
pick the next issue.

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
  workers collide on `dev`. (Different repos can each run their own `/autopilot`.)
- **Independent verification is mandatory** — a worker's "merged and deployed" counts only after
  the main loop re-reads PR/CI/version/issue state from primary sources (premature-done is the #1
  long-running-agent failure).
- **Gates are absolute** — no `--admin`, no bypass, no merge-despite (`autonomous-quality-discipline.md`).
