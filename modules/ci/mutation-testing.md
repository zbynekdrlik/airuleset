### Mutation Testing (Test Quality Gate) — Bounded

**Context gate — related rules you MUST also apply:**
- `test-strictness.md` — coverage proves tests execute; mutation proves tests VERIFY
- `no-timeout-band-aids.md` — a slow mutation gate is a setup bug; never fix it by raising the timeout
- `no-dropped-work.md` — surviving mutants from the weekly run become tracked `#N` issues, never silent
- `no-continue-on-error.md` — the weekly job is binary on its own contract (run + report + file issues)

**Line coverage proves tests execute code. Mutation testing proves tests VERIFY behavior.** Frontier models hard-code tests far less than the 2025 generation that motivated this gate — but reward hacking has not vanished and agent-written suites still over-report quality. Keep the deterministic check; keep it SMALL.

#### The two-tier shape (MANDATORY)

1. **PR gate — diff-scoped, blocking, HARD-BOUNDED.** Target < 15 min; job `timeout-minutes: 20` MAX.
2. **Weekly full-tree run — async, scheduled, sharded.** NOT on the PR path. Survivors → GitHub issues.

A blocking mutation job that can run for hours is BANNED. The 6-hour-cap gate this policy replaces blocked one project's development for days (cancel/restart cycles ≈ 30 h wall-clock).

#### Rust PR gate (cargo-mutants) — required speed levers, ALL of them

```yaml
mutation-testing:
  needs: [test]          # baseline already proven green by the test job
  timeout-minutes: 20    # HARD CAP — budget rule below
  steps:
    - uses: actions/checkout@v4
      with: { fetch-depth: 0 }
    - uses: taiki-e/install-action@v2
      with: { tool: cargo-mutants,cargo-nextest }   # prebuilt binaries, never `cargo install` in CI
    - run: |
        git diff origin/${{ github.base_ref || 'main' }}...HEAD > pr.diff
        cargo mutants --in-diff pr.diff --baseline=skip --test-tool=nextest --jobs 2 -- --all-targets
```

Plus in the repo:
- `.cargo/mutants.toml`: `profile = "mutants"`; `exclude_globs` for generated code; per-package tests only (never `test_workspace = true`)
- `Cargo.toml`: `[profile.mutants]` with `inherits = "test"`, `debug = "none"`
- Slow integration/E2E tests stay OUT of the per-mutant suite (separate package or excluded) — otherwise they re-run for EVERY mutant
- `-- --all-targets` includes all test binary targets; nextest does not run doctests regardless — which is wanted here (each doctest would compile a separate binary per mutant)

#### Budget overrun = setup bug — STOP THE LINE

If the PR gate exceeds ~15 min: fix the CONFIG — apply missing levers, shard across 2-4 matrix jobs (`--shard 1/2`, `--shard 2/2`, …), narrow scope. NEVER raise `timeout-minutes` as a band-aid, NEVER wait it out, NEVER silently delete the gate. Also: long-lived dev branches grow `--in-diff` scope toward full-tree — merge small PRs frequently (`pr-merge-policy.md` default auto-merge keeps diffs small).

#### Weekly full-tree run (async)

Scheduled workflow (weekend), sharded: `cargo mutants --shard ${{ matrix.shard }}/8 --baseline=skip` across a matrix of parallel jobs (`matrix.shard` = 1..8; the flag takes `index/total`). Surviving mutants → `gh issue create` (batched per module/area, label `test-quality`), worked through the normal backlog loop. The job FAILS only when the tooling fails to run; survivors become issues, not red CI — nothing is silently green because every survivor is a tracked `#N`. This is NOT `continue-on-error`: the job's declared contract is "mutation ran + report published + survivors filed", and it is binary on that contract.

#### Weekly run must NOT collide with active development

The user develops NONSTOP, so the heavy weekly run must never compete with active dev CI for runners (a multi-hour mutation job queued on a shared self-hosted runner blocks ALL dev CI):

- **Run the weekly on GitHub-HOSTED runners** (`runs-on: ubuntu-latest`), sharded — NEVER on the project's self-hosted / dev runner. Hosted = separate infra → zero interference; dev CI keeps flowing on the self-hosted runners untouched.
- **Own concurrency group:** `concurrency: { group: mutation-weekly-${{ github.repository }}, cancel-in-progress: true }` — weekly runs never stack; a fresh trigger supersedes a stuck one.
- **Hardware-bound projects** whose mutation tests REQUIRE self-hosted hardware (GPU / cameras / devices — e.g. RF-DETR, camera-box): give the weekly a **DEDICATED runner label** (not the dev runner). If that's impossible, **gate the weekly to skip while dev is busy** — first step checks `gh run list --status in_progress` (or `queued`); if any dev run is active, exit 0 early and let the next scheduled window retry, so the weekly NEVER preempts active development.
- Cron at a low-traffic hour is a nicety, NOT the safeguard — the infra separation (hosted, or dedicated runner, or skip-if-dev-active) is what guarantees no collision.

#### TypeScript: StrykerJS

PR: `--incremental` with the incremental report restored from the main-branch artifact (only mutants for changed code run). Same budget discipline. Keep `thresholds.break` (≥ 50). Schedule a periodic `--force` full run so incremental state can't drift.

#### The rule

Surviving mutants in YOUR diff = the work is NOT done — write assertions that kill them, push again. A slow gate = fix the gate's setup, never the budget. Applies to all rewordings and semantic equivalents.
