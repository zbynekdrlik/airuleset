---
name: local-builds
description: Local Build Policy tiers 0/1/2 — what may compile locally, cheap-check pre-push gate, cross-compile cookbook, target/ purge rules. Load BEFORE running or considering a local build (cargo build/test, npm build, docker build, tauri/trunk), when the tier0 build hook blocks a command, when toggling /fast-iterate, or when auditing build-artifact disk usage.
user-invocable: false
---

### Local Build Policy — Three Tiers

**Tier-0 (the default): ZERO local cargo compilation — ALL cargo compilation runs in CI. Local checkouts run only NON-compiling checks (rustfmt; non-cargo lint for other languages). Heavy builds AND every compiling cargo shape (build/test/bench/run/check/clippy/doc/rustc/install/…) run in CI by default; per-project escalation tiers exist for legitimate local-iteration needs.** This rule complements `ci-push-discipline.md`.

#### Why this matters

- A single Rust workspace's `target/` reaches 5–15 GB in full-build mode. Five projects = 50+ GB. The dev machine fills up silently. **Even a "cheap" `cargo check` / `cargo clippy` / a scoped `cargo test --no-run -p <crate>` compiles the WHOLE dependency tree into `target/` — 1+ GB per worktree lane on a big workspace like camera-box** — so on Tier-0 there is no such thing as a cheap local cargo compile (owner directive 2026-08-18, #557, reported ~100×).
- CI runs are reproducible (clean image, pinned toolchain). Local builds aren't — they can hide "works on my machine" bugs.
- Compile-checking a Tier-0 Rust change happens in CI (the free GitHub runners) or, if you genuinely need a fast local edit→compile loop, by ESCALATING that repo to Tier 1 (`=allowed`) / Tier 2 (`=fast-iterate`) — never by compiling on a Tier-0 checkout.

#### Tier 0 — DEFAULT (no marker in project CLAUDE.md)

**Allowed locally — the NON-compiling checks (run before push):**

| Language | Non-compiling local checks (run before push) |
|---|---|
| Rust | `cargo fmt --all --check` ONLY (rustfmt does not compile). Also fine: `cargo metadata`, `cargo tree`, `cargo update`, `cargo clean` — none compile. **`cargo check` / `cargo clippy` / `cargo test`/`bench` (`--no-run` or not, scoped or whole-workspace) / `cargo doc` / `cargo rustc` all COMPILE → CI-only on Tier-0 (#557).** |
| Python | `ruff check .`, `ruff format`, `mypy --no-incremental` |
| Node.js | `npm run lint`, `prettier --check`, `tsc --noEmit` |
| Go | `gofmt -l .`, `go vet ./...` |

On Tier-0 the ONLY local Rust check is `cargo fmt --all --check` (formatting, no compilation). EVERY compiling cargo shape — including a "cheap" `cargo check --workspace --all-targets`, a `cargo clippy`, and even a genuinely-narrow `cargo test --no-run -p <crate>` / `--lib` / `--test <name>` — compiles the whole dependency tree into `target/` (1+ GB per worktree lane on camera-box), so **all of it runs in CI, not locally** (#557, owner directive 2026-08-18; forensics: camera-box lanes kept compiling locally after #544 via exactly these narrow shapes + unmatched `clippy`/`check`/`doc`). Type/borrow/compile errors surface in the CI `check`/`clippy`/`test` jobs — or, if you genuinely need a fast local edit→compile loop, escalate the repo to Tier 1/2 (below). The non-Rust rows are unchanged (the hook only gates cargo/tauri/trunk/wasm-pack/cmake).

**MANDATORY pre-push gate after multi-file refactor:**

```bash
# Rust (Tier-0): formatting only — compilation is CI's job (#557)
cargo fmt --all --check
# (cargo check / clippy / test all COMPILE the dep tree into target/ and are
#  Tier-0 CI-only. Need a local compile loop? -> escalate to Tier 1/2.)
```

If it fails → fix locally, NEVER push the unformatted code. Compile/type errors are caught by CI's own check/clippy/test jobs.

**Banned locally (Tier 0):**

- ANY compiling cargo shape: `cargo build`/`build --release`, `cargo test`/`bench` (runs OR `--no-run`, scoped OR whole-workspace), `cargo run`, `cargo check`, `cargo clippy`, `cargo doc`, `cargo rustc`, `cargo install`, `cargo nextest run`/`list`, `cargo mutants`, and any third-party compiling subcommand (`tarpaulin`/`miri`/`llvm-cov`/…) — all compile the dep tree into `target/`; CI compiles instead (#557)
- `cargo tauri build`, `trunk build`, `wasm-pack build`, `cmake --build` — heavy bundler/native builds
- `npm run build`, `vite build`, `next build`, `webpack`, `rollup`, `esbuild --bundle`
- `docker build` of project images
- `pyinstaller`, `nuitka`, any Python freezer

If a Tier-0 ban blocks you and the work genuinely needs local compilation → escalate to Tier 1 (permanent) or Tier 2 (temporary fast-iterate).

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
Touching only fmt/comments/text?              → Tier 0 (fmt only, no compile)
Multi-file refactor of types/traits?          → Tier 0 (CI's check/clippy catches) → Tier 1 if you need a fast local compile loop
Need to run tests/compile locally at all?     → Tier 1 (permanent) or Tier 2 (fast-iterate); Tier-0 compiles ONLY in CI (#557)
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

- **Tier 0:** "I'll just `cargo check` / `cargo clippy` locally before pushing" — **WRONG (#557).** Both COMPILE the dep tree into `target/`; on Tier-0 compile-checking is CI's job. Locally run only `cargo fmt --all --check`. Need a local compile loop? Escalate the repo to Tier 1.
- **Tier 0:** "I'll just `cargo build` / a scoped `cargo test --no-run -p x` to verify it compiles" — **WRONG (#557).** Every compiling cargo shape (narrow or wide) is Tier-0 CI-only. Let CI's check/clippy/test jobs surface compile errors.
- **Tier 0:** "I ran `cargo test` locally, ready to push" — **WRONG.** All cargo tests/compiles run in CI on Tier-0. Push and let CI compile + run them (or escalate to Tier 1/2 for a local loop).
- **Tier 2:** "Push each iteration to let CI build the Windows binary" — **WRONG.** Cross-compile locally with `cargo-xwin`. Push when feature works end-to-end.
- **Tier 2:** Leaving fast-iterate on after feature stabilises — **WRONG.** `/fast-iterate off` once green on dev for ≥1 day.
- "5 GB target/ is fine, I have 500 GB" — **WRONG.** Across 10 projects = 50 GB silently accumulating. Purge per Tier-0 rules.

#### Enforcement

- **`block-tier0-local-build.sh` PreToolUse(Bash) hook HARD-BLOCKS ALL local cargo compilation** in a Tier-0 project (#557) — the rule alone let presenter's `target/` balloon to 97 GB on dev2, and even after #544 camera-box lanes kept compiling via narrow shapes. Detection is an ALLOWLIST inversion: EVERY cargo subcommand blocks EXCEPT a curated non-compiling class (`fmt`/`metadata`/`tree`/`clean`/`search`/`update`/`add`/`version`/`help`/`config`/…), so `cargo check` / `cargo clippy` / `cargo doc` / `cargo test`/`bench` (`--no-run` or not, scoped or wide) / `cargo run` / `cargo install` / `cargo nextest` / third-party compiling plugins (`tarpaulin`/`miri`/`llvm-cov`/…) ALL block, as do non-cargo heavy builds (`cargo tauri build` / `trunk build` / `wasm-pack build` / `cmake --build`). Tier-1/2 (allow / fast-iterate marker) lift the ban entirely and are UNCHANGED. An inline `# airuleset:build-ok` / `AIRULESET_ALLOW_LOCAL_BUILD=1` bypass is exempt on a normal Tier-0 repo but DISABLED for camera-box (#477); an unmanaged dir (no CLAUDE.md) is not enforced.
- `/issue-planner` step 1e audits `~/devel/*/target` etc. before issue selection. Tier 1 (`=allowed`) AND Tier 2 (`=fast-iterate`) projects are EXEMPT from the waste calculation.
- `/fast-iterate` skill toggles the Tier 2 marker on/off in the current project's CLAUDE.md.
- Pre-push hook runs the Tier-0 `cargo fmt --all --check` (formatting only, no compilation). Compile/type/lint checking runs in CI's own check/clippy/test jobs — on Tier-0 no cargo compilation runs locally (#557).

#### The principle

**Default is NO local cargo compilation + reproducible CI for every compile/test/artifact.** Tier 0 keeps the dev machine's `target/` from ballooning by running ALL cargo compilation in CI (#557); locally you only format. Tier 1/2 are the deliberate escape hatch when a repo genuinely needs a local edit→compile loop (e.g. slow Windows CI runners) — opt in per project, and turn Tier 2 off when done. CI is the source of shipping truth AND the place compilation happens.
