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

#### Before pushing — review and batch

1. Review ALL changes holistically. Batch into ONE clean commit.
2. Check in-progress runs: `gh run list --branch dev --status in_progress --limit 3`
3. Your push cancels in-progress runs via concurrency groups — expected.

#### After pushing

1. Identify the LATEST run triggered by your push.
2. Monitor ONLY that run (see `ci-monitoring.md`).
3. If duplicate runs appear (push + pull_request events), monitor both.

#### Anti-patterns (all rewordings apply)

- Pushing code that fails local lint
- Pushing multiple "fix CI" commits in a row (each cancels the previous run)
- Pushing a fix mid-run instead of waiting for the run to fail, collecting ALL errors, fixing in one commit
- Blaming CI cancellations when you caused them

**The rule: one push, one CI cycle, monitor to completion.** If CI fails, collect ALL failures, fix in ONE commit, push ONCE.
