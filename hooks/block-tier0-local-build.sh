#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a heavy LOCAL build in a Tier-0 project.
#
# no-local-builds.md: Tier 0 (the default) bans heavy local builds — `cargo build`
# / `cargo build --release` / `cargo test` (runs tests) / `cargo tauri build` /
# `trunk build` / `wasm-pack build` — locally; only the cheap compile-checks
# (`cargo check`, `cargo clippy`, `cargo test --no-run`) are allowed, and full /
# release builds run in CI. Tier 1 (`airuleset:local-builds=allowed`) and Tier 2
# (`airuleset:local-builds=fast-iterate`) projects, declared by a marker in their
# CLAUDE.md, are EXEMPT.
#
# This ENFORCES the ban (the rule alone let presenter's `target/` balloon to 97 GB
# on dev2). Reads the tool payload on STDIN (`.tool_input.command` + `.cwd`).
# Exit 2 = block the tool call (stderr shown to the agent); exit 0 = allow.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Deliberate one-off bypass (a real reason to build locally just this once).
case "$CMD" in *"airuleset:build-ok"*) exit 0 ;; esac
[ "${AIRULESET_ALLOW_LOCAL_BUILD:-0}" = "1" ] && exit 0

# Is it a HEAVY build? (cargo check / clippy / cargo test --no-run are NOT)
is_heavy() {
    local c="$1"
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+build([[:space:]]|$)' && return 0
    printf '%s' "$c" | grep -qE 'cargo[- ]tauri[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])trunk[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE 'wasm-pack[[:space:]]+build' && return 0
    # `cargo test` that RUNS tests (NOT the compile-only `--no-run`)
    if printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+test([[:space:]]|$)'; then
        printf '%s' "$c" | grep -qE -- '--no-run' || return 0
    fi
    return 1
}
# Strip quoted substrings first, so a build command MENTIONED inside a string
# (a git commit message, an echo) is NOT matched — only a real command position.
STRIPPED=$(printf '%s' "$CMD" | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g")
is_heavy "$STRIPPED" || exit 0

# Heavy build. Walk cwd → / for the project's CLAUDE.md. A Tier-1/2 allow marker
# → EXEMPT. A CLAUDE.md with NO marker → Tier 0 → block. No CLAUDE.md anywhere →
# not a managed project → don't enforce.
dir="${CWD:-$PWD}"
found=0
while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/CLAUDE.md" ]; then
        found=1
        grep -qE 'airuleset:local-builds=(allowed|fast-iterate)' "$dir/CLAUDE.md" 2>/dev/null && exit 0
        break
    fi
    dir=$(dirname "$dir")
done
[ "$found" = 0 ] && exit 0

echo "BLOCKED: heavy local build in a Tier-0 project (no-local-builds.md). Compilation + release builds run in CI — locally run only a cheap check: cargo check / cargo clippy / cargo test --no-run. To build locally on purpose: make the project Tier 1 ('<!-- airuleset:local-builds=allowed -->' in its CLAUDE.md) or Tier 2 ('/fast-iterate on'), or append '# airuleset:build-ok' to this one command." >&2
exit 2
