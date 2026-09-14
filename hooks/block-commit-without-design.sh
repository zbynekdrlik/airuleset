#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) on `git commit` -- THIN ADAPTER (#1020
# gate-family rework; #136 design-before-code gate, half 2/3).
#
# Active ONLY inside an autopilot-worker subagent. All logic lives in
# gates/commitdesign.py: it blocks a `git commit` that references an issue with
# no DELIVERED design marker yet (written solely by
# hooks/post-record-design-comment.sh), honours the `[no-design: <reason>]`
# bypass (rejecting a bare `[no-design]`), exempts a merge commit (#1003),
# resolves the target repo via an inline `cd` (#187), drops already-CLOSED refs
# (#206), quarantines a stale write-then-`-F` msgfile (#310), and surfaces the
# last reject reason (#414). The reason is printed to STDERR (the model-visible
# deny channel). Fails OPEN on any python malfunction, exactly like the previous
# `python3 ... || true; [ -n "$OUT" ] || exit 0` embedded flow.
# Dry-run: echo '{"tool_input":{"command":"git commit -m x (#1)"},"agent_type":"autopilot-worker","cwd":"/repo"}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.commitdesign <<<"$PAYLOAD" || RC=$?
[ "$RC" -eq 2 ] && exit 2
exit 0
