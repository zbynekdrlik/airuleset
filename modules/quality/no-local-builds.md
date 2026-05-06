### No Local Heavy Builds — CI Compiles, Local Lints

**Compilation runs in CI, not on the developer machine. The local checkout exists for editing, lint, and verification — not for `cargo build`, `cargo test`, `npm run build`, `cargo tauri build`, `trunk build`, or any compile-heavy command.** This rule complements `ci-push-discipline.md` ("Only lint/format runs locally by default") by making the disk-space consequences explicit.

#### Why this matters

- A single Rust workspace's `target/` reaches 5–15 GB. Five projects = 50+ GB. The dev machine fills up silently.
- CI runs are reproducible (clean image, pinned toolchain). Local builds aren't — they hide "works on my machine" bugs.
- Local compile artifacts go stale within hours; reusing them while CI rebuilds anyway just costs disk.
- `target/`, `node_modules/`, `dist/`, `.next/`, `build/` are all in `.gitignore` for a reason — they're disposable.

#### What runs locally (allowed)

| Language | Local-allowed commands |
|---|---|
| Rust | `cargo fmt --all --check`, `cargo fmt --all`, `cargo check` ONLY when explicitly debugging a type error |
| Python | `ruff check .`, `ruff format`, `mypy --no-incremental` for type checks |
| Node.js | `npm run lint`, `prettier --check`, `tsc --noEmit` for type checks |
| Go | `gofmt -l .`, `go vet ./...` |

Anything that produces a deployable artifact (binary, bundle, container image) runs in CI.

#### What does NOT run locally (banned without explicit user approval)

- `cargo build`, `cargo build --release`, `cargo tauri build`, `trunk build`, `wasm-pack build`
- `cargo test` — use CI; if you must repro a single test locally, `cargo nextest run --test <single>` is the only acceptable form, and you delete `target/` after
- `npm run build`, `vite build`, `next build`, `webpack`, `rollup`, `esbuild --bundle`
- `docker build` of project images (use CI-built images via `docker pull`)
- `pyinstaller`, `nuitka`, any Python freezer

If you find yourself reaching for a compile command "just to check", STOP. Push and let CI run. CI is what ships, so CI is what you trust.

#### Purge stale build artifacts AGGRESSIVELY

**When you encounter a `target/`, `node_modules/`, `dist/`, `.next/`, or `build/` directory in a project — and you didn't just create it for a documented reason — purge it.** The user's machine is not a build cache.

```bash
# Rust
rm -rf target/

# Node.js / web
rm -rf node_modules/ dist/ .next/ .nuxt/ build/ .turbo/ .svelte-kit/

# Python
rm -rf __pycache__/ .pytest_cache/ .mypy_cache/ .ruff_cache/ build/ dist/ *.egg-info/
find . -name __pycache__ -type d -exec rm -rf {} +

# Multi-project sweep (run from ~/devel)
du -sh ~/devel/*/target ~/devel/*/node_modules ~/devel/*/dist 2>/dev/null | sort -h
```

**Decision rule:** if `target/` (or equivalent) is older than 24 hours, delete it. If it's newer but you don't remember creating it intentionally, delete it. CI rebuilds. The artifact is disposable.

#### Cargo / global caches

- `~/.cargo/registry/` and `~/.cargo/git/` are SHARED across all projects — leave them alone unless they exceed 5 GB. To trim: `cargo cache --autoclean` (install `cargo-cache` once).
- `~/.npm/`, `~/.cache/pnpm/`, `~/.cache/pip/` — same rule, shared, only purge if oversized.
- The per-project `target/` / `node_modules/` is the part that bloats. Purge those, leave the global caches.

#### When local compile is justified (rare)

You may compile locally when:
1. The user explicitly asked you to ("can you reproduce X locally?")
2. CI is broken and you need to reproduce the failure to fix CI itself
3. You're debugging a runtime bug that requires a local debugger (`lldb`, `gdb`, `dlv`) attached to a binary

In all three cases: document why in a comment, and **delete `target/` (or equivalent) when done**. Don't leave the artifacts behind for the next session to wonder about.

#### Anti-patterns (all banned)

- "I'll just `cargo build` to check it compiles before pushing" — **WRONG.** That's what CI is for. Push, CI runs in 5 min.
- "I ran `cargo test` locally, all green, ready to push" — **WRONG.** Push and let CI run; local toolchain may differ from CI's.
- "Let me `npm run build` and serve `dist/` to verify the bundle" — **WRONG.** Use the deployed dev environment via `no-localhost-urls.md`. Don't bundle locally.
- Leaving `target/` / `node_modules/` behind after one-off debug work — **WRONG.** Purge when done. Disk space is a finite resource.
- Treating `target/` as a cache to speed up future builds — **WRONG.** CI is the build environment. Local `target/` is junk.
- "5 GB is fine, I have 500 GB" — **WRONG.** Across 10 projects this is 50 GB of silently-accumulating waste. Then 100 GB. Then a full disk.

#### Enforcement

- `/issue-planner` step 1e audits `~/devel/*/target`, `~/devel/*/node_modules` etc. before issue selection. If totals exceed 10 GB, blocks via AskUserQuestion to purge first.
- The expectation is that a healthy dev machine has near-zero per-project build artifacts. The default state of `target/` should be "doesn't exist".

#### The principle

**The dev machine is for editing. CI is for compiling.** Disk space wasted on stale artifacts is disk space not available for new projects, recordings, datasets, or anything else the user actually needs. Purge aggressively, push frequently, trust CI.
