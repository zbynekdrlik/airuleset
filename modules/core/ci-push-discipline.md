### CI Push Discipline

**Context gate — related rules you MUST also apply:**
- `ci-monitoring.md` — after push, monitor CI until ALL jobs reach terminal state
- `version-bumping.md` — bump version BEFORE first push, not after CI fails on version check
- `test-strictness.md` — don't rerun failed CI blindly; investigate root cause first

**Every push triggers a 15-20 min CI run. Wasted runs cost time.**

#### Before pushing — MANDATORY local checks

Run the right lint for the project. Fix failures BEFORE pushing.

| Project | Command |
|---|---|
| Rust | `cargo fmt --all --check` (fix: `cargo fmt --all`) |
| Python | `ruff check .` |
| Node.js | `npm run lint` |

**Only lint/format runs locally by default.** Everything that compiles (`clippy`, `test`, `build`, `check`, `trunk build`, `cargo tauri build`) runs on CI unless project CLAUDE.md says otherwise. Rust compiles generate 10-20GB of artifacts — CI-only.

#### Before pushing — sync the base, then check for a running CI

1. **Sync the base FIRST so the push can't land behind / conflict** (`git-fetch-first.md`):
   `git fetch origin && git merge origin/<base>` (base = `main` for the two-branch flow).
   Pushing while behind the base produces a PR that is BEHIND/CONFLICTING — CI runs on stale
   state, then you discover the conflict, fix, and push AGAIN: a second full CI cycle wasted on a
   conflict you could have resolved before the first push. The airuleset `pre-push-base-sync` hook
   does a trial merge (`git merge-tree`) and BLOCKS only when the push would create a genuinely
   CONFLICTING PR (it does NOT block a mere "behind") — when it fires, merge the base, resolve the
   conflict, and push once (don't bypass it to "just push").
2. Review ALL changes holistically. Batch into ONE clean commit.
3. **Check for an already-running CI on the branch BEFORE you push** (the `--status` flag takes a
   SINGLE value, so filter client-side): `gh run list --branch <branch> --limit 10 --json
   status,databaseId,headSha,event` then look for any `status` of `in_progress`/`queued`.

#### Most repos do NOT auto-cancel — a new push does NOT supersede a running run

**Do NOT assume a concurrency group exists.** A repo only auto-cancels the previous run if its
workflow declares `concurrency: { group: …, cancel-in-progress: true }` — **most don't**. Without
it, a second push starts ANOTHER run while the first keeps running to completion: two (or four, with
push+pull_request double-fire) full CI runs in parallel, every re-push. This is the recurring
time-sink: re-pushing a fix while the old, now-superseded run is still burning a runner.

So when you re-push (a fix, a follow-up commit):
- The airuleset `post-push-ci-cleanup` hook auto-cancels runs whose commit is an **ancestor of your
  new HEAD** (the superseded ones) — it keeps the current push's runs. Let it; don't fight it.
- If you must cancel manually: `gh run list --branch <branch> --status in_progress --json
  databaseId,headSha` then `gh run cancel <id>` for any run NOT on your current `git rev-parse HEAD`.
- **A repo with no concurrency group, or with a push+pull_request double-fire, is a CI-foundation
  gap** → file an issue to add `concurrency: { group: "ci-${{ github.workflow }}-${{
  github.ref }}", cancel-in-progress: true }` and to stop the duplicate trigger. (That's the repo's
  CI code — file the issue, don't silently leave the waste.)

#### After pushing

1. Identify the LATEST run for your CURRENT HEAD; monitor it (`ci-monitoring.md`).
2. If a push+pull_request pair fired for the SAME commit, both are legitimate — monitor both. Older
   runs whose commit is an ANCESTOR of your HEAD are auto-cancelled by the hook; a run on a DIVERGED
   commit (after a rebase/force-push) is left alone (fail-safe) — cancel those manually:
   `gh run cancel <id>`.

#### Anti-patterns (all rewordings apply)

- Assuming "my push cancels the old run via concurrency groups" when the repo has none → two runs
- Re-pushing a fix WITHOUT cancelling the still-running superseded run (the churn that wastes hours)
- Pushing while behind the base → conflict discovered later → fix → push again → second wasted CI cycle
- Pushing code that fails local lint
- Pushing multiple "fix CI" commits in a row before the run even fails — collect ALL errors, fix in ONE commit
- Blaming CI cancellations when you caused them

**The rule: sync base → one push → one logical CI cycle → cancel superseded runs → monitor to completion.** If CI fails, collect ALL failures, fix in ONE commit, push ONCE.
