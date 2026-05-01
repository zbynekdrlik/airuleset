---
name: issue-planner
description: Select GitHub issues, check for already-solved ones, audit CI health, and create an implementation plan. Use when starting work on a project to pick what to work on next.
user-invocable: true
disable-model-invocation: true
---

# Issue Planner

## Step 0: Close previous work context

**Any previous plan, task list, or implementation context from this session is NOW CLOSED.** Do not carry forward assumptions, partial work, or "remaining items" from earlier work. You are starting fresh.

1. If there is a task list from previous work, mark all remaining tasks as `deleted` — they belong to the old context.
2. Do NOT reference previous plans or continue where you "left off."
3. The user invoked `/issue-planner` because they want to pick NEW work. Treat this as a clean slate.

## Step 1: CI Health Audit

Before looking at issues, check if the project's CI meets airuleset standards:

### 1a. Quality gates

```bash
# Mutation testing
grep -rE "cargo-mutants|cargo mutants|stryker|StrykerJS" .github/workflows/ 2>/dev/null
# Playwright E2E tests
ls e2e/ tests/e2e/ playwright/ 2>/dev/null
# Assertion density / test-integrity gates
grep -rE "assertion|mutation|test-integrity" .github/workflows/ 2>/dev/null
```

### 1b. Build cache audit (CRITICAL — every minute of CI counts)

**Cold-compile CI is one of the top sources of wasted developer time.** A Rust workspace with no cache costs 5–15 min per push; with cache it's 30s–2 min. Detect by language and verify cache is wired up on EVERY job that compiles, not just one.

```bash
# Detect project language(s)
ls Cargo.toml package.json pyproject.toml go.mod 2>/dev/null

# Inspect ALL workflow files
ls .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null

# Rust: needs Swatinem/rust-cache@v2 on every Rust job (test, build, coverage, mutants, tauri, wasm)
grep -E "Swatinem/rust-cache|actions/cache.*\.cargo|actions/cache.*target" .github/workflows/*.y*ml 2>/dev/null

# Node.js: needs setup-node with cache: 'npm'|'yarn'|'pnpm', OR actions/cache for node_modules / .pnpm-store
grep -E "setup-node.*cache|cache: ['\"](npm|yarn|pnpm)|actions/cache.*node_modules|actions/cache.*pnpm-store" .github/workflows/*.y*ml 2>/dev/null

# Python: needs setup-python with cache: 'pip'|'poetry', OR actions/cache for ~/.cache/pip / .venv
grep -E "setup-python.*cache|cache: ['\"](pip|poetry|pipenv)|actions/cache.*\.cache/pip|actions/cache.*\.venv" .github/workflows/*.y*ml 2>/dev/null

# Go: needs setup-go with cache: true (default in v4+) OR actions/cache for ~/go/pkg/mod and ~/.cache/go-build
grep -E "setup-go|actions/cache.*go-build|actions/cache.*go/pkg" .github/workflows/*.y*ml 2>/dev/null

# Per-job verification: count compile jobs vs cache steps
grep -cE "runs-on:|^  [a-z].*:$" .github/workflows/*.y*ml | head
```

**Audit each compile-heavy job individually.** A workflow can have cache on the test job but not the coverage job — that means coverage still cold-compiles every run. For Rust: every job that calls `cargo` (build, test, clippy, llvm-cov, mutants, tauri build, trunk build, cross) MUST have `Swatinem/rust-cache@v2` BEFORE the cargo step.

If cache is missing or partial, present the gap with concrete numbers:

> "CI cache audit: 4 of 5 Rust jobs missing `Swatinem/rust-cache@v2` (Test ✅, Build Windows ❌, Coverage ❌, Build Tauri ❌, Build WASM ❌). Estimated waste: ~12 min per push. Fix proposal: add `Swatinem/rust-cache@v2` step before each cargo invocation, with `key: ${{ runner.os }}-${{ matrix.target }}` so different targets don't collide."

### 1c. Block on missing gates or missing cache

If quality gates OR cache are missing, you MUST use AskUserQuestion before proceeding:

- **"Yes, fix CI first"** — Add missing gates / cache as the first task BEFORE any issue work (recommended — pays back within 1-2 PRs)
- **"Skip for now, show issues"** — Proceed without fixing CI (user explicitly chose to skip)

**Do NOT proceed to Step 2 until the user has answered.** A one-line mention like "cache is missing" without AskUserQuestion is NOT acceptable — block and ask. Bad CI compounds: every issue you plan will pay the cold-compile tax until the cache is fixed.

### 1d. Foundation gate — version display on dashboard (web projects only)

If the project has a web UI (frontend, dashboard, admin panel), check whether it displays the deployed version. This is a global rule — see `version-on-dashboard.md`. Without a visible version label, post-deploy verification is impossible and frontend/backend drift ships silently.

**Detection (run silently):**

```bash
# Heuristic: project has a web UI?
HAS_FRONTEND=$(test -d frontend -o -d web -o -d ui -o -f index.html -o -f vite.config.* -o -f next.config.* && echo yes || echo no)
[ -f package.json ] && grep -qE '"(react|vue|svelte|leptos|preact|next|nuxt|remix|astro)"' package.json && HAS_FRONTEND=yes
[ -f Cargo.toml ] && grep -qE '(leptos|yew|dioxus|sycamore)' Cargo.toml && HAS_FRONTEND=yes

# If web UI: check if a version display exists
if [ "$HAS_FRONTEND" = "yes" ]; then
  HAS_VERSION_DISPLAY=$(grep -rE 'data-testid="version"|class="version"|id="version"|build_version|gitDescribe|GIT_VERSION|VERSION_LABEL' --include='*.tsx' --include='*.jsx' --include='*.ts' --include='*.js' --include='*.html' --include='*.rs' --include='*.svelte' --include='*.vue' . 2>/dev/null | head -1 | wc -l)
  HAS_VERSION_TEST=$(grep -rE 'version.*toMatch.*\\\\d.*\\\\d.*\\\\d|expect.*version.*v\\\\d' --include='*.spec.*' --include='*.test.*' tests/ e2e/ 2>/dev/null | head -1 | wc -l)
fi
```

**Decision rules:**

- **Web UI + version display present + Playwright assertion present** → silent pass. No action.
- **Web UI + version display missing** → AskUserQuestion BEFORE Step 2:
  - **"Yes, file foundation issue first"** — Create the issue per `version-on-dashboard.md` template, plan that as the next PR before feature work
  - **"Skip for now, show issues"** — Proceed without fixing (user explicitly chose to skip)
- **Web UI + version display present but NO Playwright test** → propose adding the test as part of the next PR's work (does not block; just a note in Step 4 presentation).

**Issue body template (when filing the foundation issue):**

```
Add a version display to the dashboard per the global version-on-dashboard.md rule.

Format: `v<semver>(-dev.<n>)?` (e.g. `v1.0.97-dev.9`), build-time injected from `git describe`, matching the deployed binary. Place it in a footer or navbar visible on every route.

Add a Playwright test asserting the label exists, is visible, and matches the format. The frontend version must equal the backend `/api/version` (or equivalent) — single git-tag source.

Without this, post-deploy verification cannot confirm new code is live and frontend/backend drift ships invisibly. See ~/devel/airuleset/modules/quality/version-on-dashboard.md.
```

**Do NOT proceed to Step 2 until the foundation gate is resolved** (either issue filed and queued, or user explicitly chose skip).

## Step 2: Fetch open issues

```bash
gh issue list --state open --limit 30 --json number,title,labels,assignees,createdAt,updatedAt
```

## Step 3: Per-issue "already overcome by other work" check (MANDATORY)

**This step runs FOR EVERY OPEN ISSUE — not a sample, not a spot-check.** Old issues are the most common to be silently solved by unrelated refactors, dependency bumps, or feature work. Skipping this wastes a planning cycle.

For each open issue from Step 2:

```bash
# 1. Read the full issue (title, body, recent comments)
gh issue view <number> --json title,body,comments,labels,createdAt

# 2. Extract 2–4 keywords from the title and body (function names, file paths, error strings)

# 3. Search recent commits that touched the relevant area
git fetch origin
git log --oneline --since="$(gh issue view <number> --json createdAt -q .createdAt)" -- <relevant-paths>
git log --oneline -50 --grep="<keyword>" --all

# 4. Search merged PRs since the issue was opened
gh pr list --state merged --limit 30 --search "<keyword>" --json number,title,mergedAt,body
gh pr list --state merged --search "fixes #<number> OR closes #<number> OR resolves #<number>" --json number,title,mergedAt

# 5. If the issue references specific code (file:line, function name), VERIFY the current state
#    — old issue says "function X panics on empty input"; check if X still exists, still panics
grep -rn "<function or symbol>" src/ 2>/dev/null
# For Rust: cargo check; for behavioral claims: write a quick repro test
```

**Decision rules per issue:**

- **Likely solved** (matching PR title/body, code area changed since issue opened, behavior may have changed) → AskUserQuestion: "Issue #X looks solved by PR #Y (merged YYYY-MM-DD). Close it?"
- **Partially overlaps** (related work happened, scope may have shrunk) → AskUserQuestion: "Issue #X scope has shifted because of PR #Y. Update scope, close as obsolete, or work as-is?"
- **Stale but unsolved** (>90 days old, no related work) → flag in Step 4 presentation: `#X (180d old, no related work)` so the user can deprioritize
- **Clean active issue** (no related work, recent or actively referenced) → present in Step 4 for selection

**Block before Step 4:** if any issues are flagged "likely solved" or "partially overlaps", resolve them via AskUserQuestion FIRST. Do not present issues for selection while there are unconfirmed-solved ones in the queue. Never close an issue without explicit user approval.

## Step 4: Present issues for selection

Use AskUserQuestion to present the open (unsolved) issues as options. Group by priority/labels if available. Let the user select which issue(s) to work on.

Include for each issue:
- Number and title
- Key details from the body (1 line summary)
- Labels and age

## Step 5: Brainstorm and plan

For each selected issue:

1. Read the full issue body and comments: `gh issue view <number>`
2. Explore the relevant code areas
3. Invoke `/superpowers:brainstorming` to design the approach
4. After brainstorming approval, invoke `/superpowers:writing-plans` to create the implementation plan
5. After plan approval, the user can invoke `/superpowers:executing-plans` to start work

## Rules

- Always run the CI health audit FIRST — missing quality gates AND missing build cache are more important than any single issue
- The build-cache audit (Step 1b) is per-job, not per-workflow — one cached job ≠ healthy CI
- The "already overcome" check (Step 3) runs for EVERY open issue, not a sample
- Never close an issue without user confirmation via AskUserQuestion
- Present structured choices, not walls of text
- If no issues exist, say so and ask if the user wants to create one
