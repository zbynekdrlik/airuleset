---
name: fast-iterate
description: Toggle Tier-2 fast-iterate mode in the current project. Enables full local builds + cross-compile + iterate-locally-before-push workflow. Use when CI roundtrips slow down development (e.g. 10-15 min free GitHub Windows runner cycles for tiny code tweaks). Usage: /fast-iterate on | /fast-iterate off | /fast-iterate status
user-invocable: true
---

# /fast-iterate — Toggle Tier-2 Local Build Mode

Per `no-local-builds.md`, projects default to Tier 0 (cheap-compile only — fmt/check/clippy/test --no-run). This skill toggles **Tier 2 (fast-iterate)** in the current project's `CLAUDE.md` — enabling full local builds, cross-compile, and an iterate-locally-before-push workflow.

## When to use

- Refactor takes day instead of hour because every compile-fix roundtrip waits 15 min for CI
- Project compiles to a foreign target (Windows from Linux) and GitHub free runner queue is slow
- You're in an aggressive debugging session and need 1-3 min build-test cycles, not 15 min

## When NOT to use

- Feature is stable and just needs review/merge — Tier 0 is enough
- Project legitimately needs local builds FOREVER (GPU/CUDA/embedded) — use Tier 1 (`=allowed`) instead
- Disk is already full — purge other projects first, then enable

## Arguments

```
/fast-iterate on       Enable Tier 2 in current project
/fast-iterate off      Disable Tier 2, revert to Tier 0
/fast-iterate status   Report current tier of current project
```

If no argument given, default to `status`.

## Step 1 — locate project root

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$PROJECT_ROOT" ] && { echo "Not in a git repo. Run from inside a project."; exit 1; }
CLAUDE_MD="$PROJECT_ROOT/CLAUDE.md"
```

If `CLAUDE.md` doesn't exist, create it with a minimal header before adding the marker.

## Step 2a — `on` mode

1. Check current state. If already `=fast-iterate`, report and exit.
2. If `=allowed` (Tier 1): warn that Tier 1 is permanent — Tier 2 is for temporary mode. AskUserQuestion to confirm switch.
3. If no marker (Tier 0): append the `## Local Build Policy` section to `CLAUDE.md`:

   ```markdown
   ## Local Build Policy

   <!-- airuleset:local-builds=fast-iterate -->

   **Fast-iterate mode (Tier 2) ENABLED.** Iterate locally; push to CI only when feature works end-to-end.
   Reason: <ask user for 1-line reason — e.g. "Windows cross-compile cycle vs 15-min CI Windows runner.">
   Activated: <today's date in YYYY-MM-DD>. Revert with `/fast-iterate off` once feature stabilises.
   ```

   AskUserQuestion for the 1-line reason. Fill the placeholder.

4. Commit the CLAUDE.md change locally with: `chore: enable fast-iterate mode (Tier 2)`. Do NOT push automatically — user pushes when ready.
5. Report: "Tier 2 enabled. You can now run full local builds + cross-compile. Remember to `/fast-iterate off` when feature stabilises."

## Step 2b — `off` mode

1. Check current state. If no marker, report Tier 0 already, exit.
2. If `=allowed` (Tier 1): tell user this skill only manages Tier 2; Tier 1 must be removed manually.
3. If `=fast-iterate` (Tier 2): remove the entire `## Local Build Policy` section from `CLAUDE.md` (find the heading, delete through the next `##` heading or EOF, whichever comes first).
4. After removal, suggest purging `target/`:

   ```bash
   du -sh "$PROJECT_ROOT/target" 2>/dev/null
   ```

   If non-zero, AskUserQuestion whether to `rm -rf target/` now.
5. Commit CLAUDE.md change locally with: `chore: disable fast-iterate mode, revert to Tier 0`.
6. Report: "Tier 0 restored. Cargo target/ purged: <yes/no, size freed>."

## Step 2c — `status` mode

```bash
if grep -qE '<!--\s*airuleset:local-builds=fast-iterate\s*-->' "$CLAUDE_MD" 2>/dev/null; then
    echo "Tier 2 (fast-iterate) — temporary local-build mode ACTIVE"
    grep -E 'Activated:|Reason:' "$CLAUDE_MD"
elif grep -qE '<!--\s*airuleset:local-builds=allowed\s*-->' "$CLAUDE_MD" 2>/dev/null; then
    echo "Tier 1 (allowed) — permanent local-build mode ACTIVE"
elif [ -f "$CLAUDE_MD" ]; then
    echo "Tier 0 (default) — cheap-compile only locally (fmt + check + clippy + test --no-run)"
else
    echo "Tier 0 (default) — no CLAUDE.md found at $PROJECT_ROOT"
fi

# Show target/ size regardless of tier
du -sh "$PROJECT_ROOT/target" 2>/dev/null || echo "No target/ directory"
```

## Cross-compile setup (when enabling Tier 2 for Linux→Windows project)

After enabling Tier 2 on a Rust project that targets Windows, install the cross-compile toolchain ONCE:

```bash
cargo install cargo-xwin
rustup target add x86_64-pc-windows-msvc

# Verify
cargo xwin build --target x86_64-pc-windows-msvc --release 2>&1 | head -5
```

GNU alternative:

```bash
sudo apt install mingw-w64
rustup target add x86_64-pc-windows-gnu
```

The `/fast-iterate on` command MAY offer to install these via AskUserQuestion if it detects the project has Windows targets but the toolchain isn't installed (`rustup target list --installed | grep windows`).

## Rules

- **NEVER toggle other projects.** Skill operates on the current project's git root only.
- **NEVER auto-push the CLAUDE.md change.** Local commit only — user decides when to push.
- **ALWAYS ask for the reason line** when enabling — durable record of why this project needs Tier 2.
- **REMIND user to disable.** After enabling, schedule a reminder (or note in completion report) to run `/fast-iterate off` once the feature ships.
- **No silent disk impact.** When disabling, AskUserQuestion before `rm -rf target/` — that's a destructive action.

## Behavior change for the agent while Tier 2 is active

When the agent reads the project's CLAUDE.md and sees `<!-- airuleset:local-builds=fast-iterate -->`:

1. Skip the Tier-0 "push for every compile-error" pattern. Build locally, fix locally, repeat.
2. For Windows targets on Linux: use `cargo xwin build` instead of pushing to trigger a GitHub Windows runner.
3. Run the FULL pre-push gate (Tier 0 commands + full build + tests) before pushing. CI is the FINAL verification, not the iteration engine.
4. Push ONCE when feature works end-to-end. Not after every fix.
5. After feature merges to main + green on dev for ≥1 day, prompt user to `/fast-iterate off`.
