### CI Push Discipline

**Every push to `dev` triggers a CI run that may take 15-20 minutes. Wasted CI runs cost time and delay feedback.**

#### Before pushing — MANDATORY local checks

**Run these BEFORE every `git push`. Not optional. Not "if you remember." Every single time.**

For Rust projects:

```bash
cargo fmt --all --check    # Fix: cargo fmt --all
```

**Do NOT run `cargo clippy`, `cargo test`, `cargo build`, or `cargo check` locally** unless the project CLAUDE.md explicitly allows it. These compile the project and generate 10-20GB of build artifacts. Clippy and tests run on CI.

For Python projects:

```bash
ruff check .
```

For Node.js projects:

```bash
npm run lint
```

**If any check fails, fix it BEFORE pushing.** Do not push and "hope CI catches it" — you already know it will fail. Pushing code that fails local lint is wasting a 15-minute CI run on something you could have fixed in 5 seconds.

**Only `cargo fmt` runs locally by default.** Everything that compiles Rust (`clippy`, `test`, `build`, `check`, `trunk build`, `cargo tauri build`) belongs on CI unless the project CLAUDE.md says otherwise. Check project-specific build policies.

#### Before pushing — review and batch

1. **Review ALL code holistically** — do not push one fix at a time. Read through your changes, check for dead code and test gaps. Batch everything into one clean commit.
2. **Check if CI is already running**: `gh run list --branch dev --status in_progress --limit 3`
3. If runs are in progress for old commits, they will be cancelled by your push (concurrency groups). This is expected — but only if you have genuinely new changes to push.

#### After pushing

1. Verify stale runs were cancelled: `gh run list --branch dev --limit 5`
2. Identify the LATEST run (triggered by your push) and monitor ONLY that one.
3. If you see duplicate runs (push event + pull_request event), both must pass — monitor both.

**Anti-patterns to avoid:**

- Pushing code that fails `cargo fmt --check` — run it locally first, always.
- Pushing 5 small "fix CI" commits in a row — each one cancels the previous run before it finishes.
- Spotting a typo mid-CI-run and immediately pushing a fix — wait for the run to fail first, then fix ALL issues at once.
- Claiming "CI keeps getting cancelled" when you are the one causing the cancellations by pushing.

**The rule: one push, one CI cycle, monitor to completion.** If CI fails, collect ALL failures, fix them in ONE commit, push ONCE, monitor again.
