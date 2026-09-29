#!/usr/bin/env bash
set -euo pipefail

# Hook: SessionStart (startup + resume matchers)
# Fetches origin at session start and, WHEN PROVABLY SAFE, fast-forwards the
# local branch to match — so a new session's CLAUDE.md / project files (read
# straight off the working tree at boot) never sit stale behind origin just
# because nobody happened to `git pull` on this particular checkout (#314).
# Registered for `resume` too (#1176): every managed session starts with
# `claude -c`, a resume source. Watchdog Job 53 (checkout freshness) is the
# continuous guarantee for a session that then lives for days; this hook is
# the cheap immediate path.
# Fast-forward ONLY — never `reset --hard`, never `checkout -f`, never any
# history rewrite. Any unsafe state (dirty tree, an in-progress git
# operation, a genuinely diverged branch, detached HEAD) is left completely
# untouched and only reported via a WARNING line. The safety predicate lives
# in ONE place, cli_checkout_freshness.ff_verdict, shared with Job 53.

# #486 G1 — register a structured session heartbeat at startup
# (~/.claude/session-status/<sid>.json), so the reader sees a session as soon
# as it boots — before the first turn ends. Reads the SessionStart payload from
# stdin (nothing else in this hook consumes stdin). Placed BEFORE the git checks
# so it fires regardless of whether the cwd is a git repo. Best-effort +
# non-blocking. A `resume` start (#1176 added that matcher) writes NO
# heartbeat, exactly as before: the heartbeat semantics stay startup-only.
_HB_INPUT=$(cat 2>/dev/null || echo "")
_HB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
if ! [[ $_HB_INPUT =~ \"source\"[[:space:]]*:[[:space:]]*\"resume\" ]]; then
    printf '%s' "$_HB_INPUT" | PYTHONPATH="$_HB_DIR" \
        python3 -m watchdog.session_status --event session_start >/dev/null 2>&1 || true
fi

# issue 1127 — on EVERY exit path below, deliver the project's stream
# directives from the BASE ref via session-start-stream-directives.sh. An EXIT
# trap, not a sibling hooks.json entry: hooks of one event run in parallel,
# and the step must read the base AFTER this hook's `git fetch origin`.
# Best-effort, never changes our exit. _ORIGIN_FETCHED marks the fetch as
# ATTEMPTED (ok or not): the step then never re-contacts origin inside this
# hook's one timeout budget. A signal (Claude Code's timeout kill, a closed
# pipe, a hangup) clears the trap so nothing new is started. Accepted cost of
# any trap: bash now finishes the running foreground `git fetch` before it
# exits on that signal.
_ORIGIN_FETCHED=""
_stream_directives_step() {
    AIRULESET_STREAM_BASE_FETCHED="$_ORIGIN_FETCHED" \
        bash "$_HB_DIR/hooks/session-start-stream-directives.sh" </dev/null 2>/dev/null || true
}
trap _stream_directives_step EXIT
trap 'trap - EXIT; exit 143' TERM
trap 'trap - EXIT; exit 130' INT
trap 'trap - EXIT; exit 129' HUP
trap 'trap - EXIT; exit 141' PIPE

# Only run if we're in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Only run if origin is configured
if ! git remote get-url origin &>/dev/null; then
    exit 0
fi

# Fetch latest from origin (suppress output to avoid noise). Bounded (#1176):
# it now runs on every `claude -c` too, and must leave room inside Claude
# Code's 30 s hook budget for the fast-forward and the EXIT-trap directives
# step (its own upstream fetch is bounded at 10 s); `-k` stops a fetch that
# ignores the TERM. A slow fetch cut here is caught up by watchdog Job 53.
_ORIGIN_FETCHED=origin
GIT_TERMINAL_PROMPT=0 timeout -k 2 8 git fetch origin --quiet 2>/dev/null || true

# The ONE shared safety predicate + the fast-forward step (#1176): an
# in-progress operation, a detached HEAD, a dirty or unmeasurable tree, a
# divergence and an origin commit adding a path that already exists locally
# (#314 F1-F4) are all decided by cli_checkout_freshness.ff_verdict, the same
# function watchdog Job 53 runs. It prints the historical #314 lines. Run as a
# SCRIPT path, so its own dir (never the checkout's cwd, which could shadow
# the module) leads sys.path. Best-effort: never fails the session start.
python3 "$_HB_DIR/cli_checkout_freshness.py" hook 2>/dev/null || true
