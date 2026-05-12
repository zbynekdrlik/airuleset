### Local Build Policy — Three Tiers

**Compilation runs in CI; local checkouts run lint + cheap-compile checks. Heavy builds (release binaries, bundles, Tauri/Trunk) run in CI by default, but per-project escalation tiers exist for legitimate needs.** This rule complements `ci-push-discipline.md`.

#### Why this matters

- A single Rust workspace's `target/` reaches 5–15 GB in full-build mode. Five projects = 50+ GB. The dev machine fills up silently.
- CI runs are reproducible (clean image, pinned toolchain). Full local builds aren't — they can hide "works on my machine" bugs.
- But: pushing for every compile-error roundtrip wastes 15 min per CI cycle when `cargo check` would catch it in 90s locally. Default tier permits cheap-compile checks for exactly this reason.

#### Tier 0 — DEFAULT (no marker in project CLAUDE.md)

**Allowed locally — REQUIRED before push after multi-file changes:**

| Language | Cheap-compile commands (run before push) |
|---|---|
| Rust | `cargo fmt --all --check`, `cargo check --workspace`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --no-run --workspace` |
| Python | `ruff check .`, `ruff format`, `mypy --no-incremental` |
| Node.js | `npm run lint`, `prettier --check`, `tsc --noEmit` |
| Go | `gofmt -l .`, `go vet ./...` |

`cargo check` parses + typechecks + borrow-checks (no codegen, no linking). Cost: ~90s cold, ~15s warm. Disk delta ~150-200 MB. `cargo clippy --workspace -- -D warnings` adds ~50 MB. `cargo test --no-run` compiles tests without running them, adds ~300 MB. Total Tier-0 disk: ~500 MB per Rust project — acceptable trade-off vs 15-min CI roundtrips.

**MANDATORY pre-push gate after multi-file refactor:**

```bash
# Rust
cargo fmt --all --check && \
cargo check --workspace && \
cargo clippy --workspace --all-targets -- -D warnings && \
cargo test --no-run --workspace
```

If ANY of these fail → fix locally, NEVER push the broken code. Each E0xxx caught locally saves a 15-min CI cycle.

**Banned locally (Tier 0):**

- `cargo build`, `cargo build --release` — produces deployable binary; CI builds it
- `cargo tauri build`, `trunk build`, `wasm-pack build` — heavy bundler builds
- `cargo test` (runs the tests; compile-only via `--no-run` is allowed) — let CI run them
- `npm run build`, `vite build`, `next build`, `webpack`, `rollup`, `esbuild --bundle`
- `docker build` of project images
- `pyinstaller`, `nuitka`, any Python freezer

If a Tier-0 ban blocks you and the work genuinely needs full builds → escalate to Tier 1 (permanent) or Tier 2 (temporary fast-iterate).

#### Tier 1 — `=allowed` (permanent opt-in)

For projects that legitimately need local compilation forever — heavy ML/GPU/CUDA, embedded toolchains, or projects where the dev machine IS the build target.

Declare in the project's `CLAUDE.md`:

```markdown
## Local Build Policy

<!-- airuleset:local-builds=allowed -->

**Local builds (Tier 1) ENABLED.** Full `cargo build` / `cargo test` / `cargo tauri build` allowed.
Reason: <one-line — e.g. "GPU-bound CUDA training requires local toolchain.">
```

Both markers MUST appear: heading + HTML comment. Tooling detects the comment as canonical signal.

When Tier 1 is active:
- All Tier-0 commands stay mandatory pre-push (still cheap, still fast)
- Full builds allowed
- 24h `target/` purge rule does NOT apply
- Disk audits SKIP this project

#### Tier 2 — `=fast-iterate` (temporary fast-iteration mode)

For when a single project needs aggressive local iteration to avoid 10-20 min CI cycles for small UI/code tweaks. Example: restreamer multi-arch (Linux + Windows) where free GitHub Windows runners take 10-15 min cold and dwarf 1-3 min local cross-compile time.

Declare in the project's `CLAUDE.md`:

```markdown
## Local Build Policy

<!-- airuleset:local-builds=fast-iterate -->

**Fast-iterate mode (Tier 2) ENABLED.** Iterate locally; push to CI only when feature works end-to-end.
Reason: <one-line — e.g. "GitHub free Windows runner cold-start is 10+ min vs 2 min local cargo-xwin.">
Activated: <YYYY-MM-DD>. Revert with `/fast-iterate off` once feature stabilises.
```

Use `/fast-iterate on` slash command to add the marker; `/fast-iterate off` to remove.

When Tier 2 is active, agent MUST:

1. **Iterate locally — no push between iterations.** Build → test → fix → build → test → fix. Only push when feature works end-to-end (compiles + tests pass + manual verify if applicable).
2. **Use cross-compile for foreign targets.** Linux → Windows: `cargo xwin build --target x86_64-pc-windows-msvc --release` (install: `cargo install cargo-xwin`). Avoid the GitHub free Windows runner whenever possible.
3. **Run the full pre-push gate before pushing** — same as Tier 0 (fmt + check + clippy + test) PLUS the actual build + test commands.
4. **Disk hygiene still applies but relaxed** — `target/` may grow to 10+ GB during a fast-iterate session; that's expected. After feature ships and `/fast-iterate off` runs, agent should purge `target/` and return to Tier 0.
5. **Revert when stable** — fast-iterate is TEMPORARY. After the feature merges to main and stabilises (≥1 day of green CI on dev), run `/fast-iterate off` to revert. Don't leave it on permanently — that's what Tier 1 is for.

**Cross-compile cookbook (Rust, Linux → Windows):**

```bash
# Install once
cargo install cargo-xwin
rustup target add x86_64-pc-windows-msvc

# Build (faster than cross because it uses MSVC stdlib via xwin)
cargo xwin build --target x86_64-pc-windows-msvc --release

# Output: target/x86_64-pc-windows-msvc/release/<binary>.exe
# Smoke test in Wine OR scp to Windows machine for verification
```

Alternative for GNU toolchain:

```bash
sudo apt install mingw-w64
rustup target add x86_64-pc-windows-gnu
cargo build --target x86_64-pc-windows-gnu --release
```

When CI is still needed for Tier 2: only push when the local build + test green. CI does the FINAL verification (real Windows runner, real artifacts, deploy). Don't push to "let CI build it for me" — that defeats the point.

#### Tier escalation decision tree

```
Touching only fmt/comments/text?              → Tier 0 (no compile needed)
Multi-file refactor of types/traits?          → Tier 0 (cargo check + clippy catches)
Need to run tests with side effects locally?  → Tier 0 → Tier 2 if recurring
Iterating on Windows binary repeatedly?       → Tier 2 (cross-compile)
Project IS the build target (GPU/CUDA)?       → Tier 1 (permanent)
Feature stable, no longer iterating?          → /fast-iterate off → Tier 0
```

#### Purge `target/` AGGRESSIVELY (Tier 0 only)

When you encounter a `target/`, `node_modules/`, `dist/`, `.next/`, or `build/` directory in a Tier-0 project that's older than 24 h, delete it. CI rebuilds. The artifact is disposable.

```bash
# Rust
rm -rf target/

# Node.js / web
rm -rf node_modules/ dist/ .next/ .nuxt/ build/ .turbo/ .svelte-kit/

# Python
rm -rf __pycache__/ .pytest_cache/ .mypy_cache/ .ruff_cache/ build/ dist/ *.egg-info/

# Multi-project sweep (excludes Tier 1 + Tier 2 — see /issue-planner step 1e)
du -sh ~/devel/*/target ~/devel/*/node_modules ~/devel/*/dist 2>/dev/null | sort -h
```

Tier 1 + Tier 2 projects are EXEMPT — their `target/` is a working asset, not waste.

#### Cargo / global caches

- `~/.cargo/registry/` and `~/.cargo/git/` are SHARED across all projects — leave alone unless they exceed 5 GB. Trim: `cargo cache --autoclean`.
- `~/.npm/`, `~/.cache/pnpm/`, `~/.cache/pip/` — same rule, shared, only purge if oversized.

#### Anti-patterns (all banned)

- **Tier 0:** "Skip `cargo check`, just push and let CI tell me" — **WRONG.** 90s local vs 15 min CI cycle. Run check + clippy first.
- **Tier 0:** "I'll just `cargo build` to verify it compiles" — **WRONG.** Use `cargo check` (no codegen, no linking, 10× faster).
- **Tier 0:** "I ran `cargo test` locally, ready to push" — **WRONG.** Use `cargo test --no-run` (compile-only). Let CI run the tests.
- **Tier 2:** "Push each iteration to let CI build the Windows binary" — **WRONG.** Cross-compile locally with `cargo-xwin`. Push when feature works end-to-end.
- **Tier 2:** Leaving fast-iterate on after feature stabilises — **WRONG.** `/fast-iterate off` once green on dev for ≥1 day.
- "5 GB target/ is fine, I have 500 GB" — **WRONG.** Across 10 projects = 50 GB silently accumulating. Purge per Tier-0 rules.

#### Enforcement

- `/issue-planner` step 1e audits `~/devel/*/target` etc. before issue selection. Tier 1 (`=allowed`) AND Tier 2 (`=fast-iterate`) projects are EXEMPT from the waste calculation.
- `/fast-iterate` skill toggles the Tier 2 marker on/off in the current project's CLAUDE.md.
- Pre-push hook runs Tier-0 fmt check; agent runs `cargo check` + `cargo clippy` + `cargo test --no-run` manually before invoking `git push`.

#### The principle

**Default is fast-feedback locally + reproducible CI for shipping artifacts.** Tier 0 gives 90s compile-check vs 15-min CI roundtrip — use it. Tier 2 gives temporary full-build escape hatch when CI is the bottleneck (Windows runners) — use it sparingly, turn it off when done. The dev machine is for fast iteration; CI is the source of shipping truth.
