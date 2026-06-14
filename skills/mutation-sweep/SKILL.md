---
name: mutation-sweep
description: "Run the FULL-TREE mutation sweep ON DEMAND — catches up all mutation coverage the diff-scoped PR gates skipped across the week's merges. Run it when active work is done and there's capacity (no cron, no collision with your nonstop dev). Fires the repo's workflow_dispatch full-mutation workflow, monitors to terminal, confirms survivors filed as test-quality issues. Usage: /mutation-sweep [all]"
argument-hint: "[all]"
user-invocable: true
disable-model-invocation: true
---

# Mutation Sweep — on-demand full-tree catch-up

Run this **when you've finished active work and have room** for the heavy mutation run. It catches
up everything the fast diff-scoped PR gates skipped across the week's merged PRs — the full tree —
WITHOUT any scheduled cron that could collide with your nonstop development. **You** pick the moment.

**Context gate:**
- `mutation-testing.md` — the two-tier shape: diff-scoped ≤20-min PR gate (blocking) + this on-demand full-tree sweep
- `ci-monitoring.md` — monitor the dispatched run to terminal state (all shards)
- `no-dropped-work.md` — every surviving mutant becomes a tracked `test-quality` issue
- `feedback-stay-in-repo-lane.md` — this skill only DISPATCHES + monitors the project's workflow; it does NOT edit the project's CI

## Why on-demand, not cron

The PR gate is diff-scoped and fast. The full tree is heavy — a cron run would queue on a shared
self-hosted runner and block your dev CI. Triggering it yourself when there's capacity removes the
collision entirely. (Per-project prereq: the full-tree mutation lives in a `workflow_dispatch`
workflow, NOT `schedule`/`push`/`pull_request` — see `mutation-testing.md`. If the repo doesn't have
one yet, that's tracked in its mutation-alignment issue.)

## Steps

1. **Resolve scope.** No arg → the current repo. `all` → every active repo (process serially).
2. **Find the full-mutation workflow:** `gh workflow list` → the `workflow_dispatch` mutation
   workflow (e.g. `mutation-full.yml`). If none exists, STOP and report that the repo's
   mutation-alignment issue must add it first (don't edit the CI yourself).
3. **Confirm you have capacity** — this is the whole point; you ran the command, so proceed. For a
   hardware-bound repo (GPU/devices), make sure the hardware is idle (it's your call).
4. **Dispatch:** `gh workflow run <mutation-full.yml>` (pass inputs if the workflow defines any).
   Grab the run id (`gh run list --workflow <file> -L1`).
5. **Monitor to terminal** (`ci-monitoring.md`): `sleep N && gh run view <id> --json status,conclusion,jobs`
   in the background; wait for ALL shards. Don't claim done while shards run.
6. **Survivors → issues:** the workflow files surviving mutants as `test-quality` issues. Verify:
   `gh issue list --label test-quality --state open`. If the workflow didn't auto-file, collect
   survivors from the run logs and `gh issue create` per area.
7. **Report:** mutants run, survivors found, the `#N` issues filed/confirmed. With `all`, one summary
   line per repo.

## Notes

- The agent NEVER auto-runs this (it's `disable-model-invocation`) — only the user types `/mutation-sweep`.
- The sweep's workflow should `runs-on: ubuntu-latest` (hosted) so even on demand it doesn't tie up
  self-hosted dev runners; hardware-bound repos run it on a dedicated/idle runner.
- Surviving mutants are worked through the normal backlog (e.g. `/autopilot`), not fixed inside this skill.
