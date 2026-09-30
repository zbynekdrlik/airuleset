#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a per-account developer-tool install, but ONLY
# when the invoking unix user is a declared PROJECT account (#1184, a
# cli_account_bootstrap.SERVICE_ACCOUNTS member). #1201, owner 30.9.2026: the
# fohmixer project account installed its own rustup (895 MB), Playwright
# browsers and pipx ruff into its home on a disk at 90 %. Project accounts
# share ONE system toolchain that the root bootstrap installs per box
# (cli_project_toolchain); a missing tool is added by airuleset — the block
# message points at `gh issue create -R zbynekdrlik/airuleset`.
#
# Blocked shapes (hooks/project_account_install_guard.py is the classifier and
# documents each): rustup installs/toolchain changes, `curl … sh.rustup.rs`,
# non-global `pipx install`, `uv tool install`, `pip install --user` /
# `--break-system-packages`, `npx playwright install` (and its runner
# variants), `npm i -g`, `yarn global add`, `pnpm add -g`, `cargo install`.
# A project venv inside the repo stays allowed.
#
# On every other account (newlevel, the streams, the controller) it is a total
# NO-OP. Reads `.tool_input.command` on STDIN; exit 2 = block (reason on
# STDERR), exit 0 = allow. A classifier malfunction FAILS OPEN. Deliberately no
# bypass marker (the owner's hard stop on duplication).

INPUT=$(cat)
# Cheap pre-filter: no install-shaped word at all -> nothing to classify.
case "$INPUT" in
    *rustup*|*pipx*|*pip*|*playwright*|*npm*|*yarn*|*pnpm*|*cargo*|*uv*) ;;
    *) exit 0 ;;
esac

ME=$(id -un 2>/dev/null || true)
[ -n "$ME" ] || exit 0
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

RC=0
REASON=$(printf '%s' "$INPUT" | python3 "$HERE/project_account_install_guard.py" \
    "$ME" "$(dirname "$HERE")") || RC=$?
[ "$RC" -eq 2 ] || exit 0
printf '%s\n' "$REASON" >&2
exit 2
