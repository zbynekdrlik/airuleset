#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (every tool, matcher "*") -- THIN ADAPTER (#1203).
#
# Blocks every tool call of a MAIN session whose transcript tail shows a Claude
# Code model fallback (the last assistant entry is a `fallback` marker, or its
# model is not the managed MANAGED_MODEL). All logic lives in
# gates/modelfallback.py: subagents (payload `agent_id`) and the #1060
# implementer window are exempt, the transcript read is bounded, and every
# read error FAILS OPEN (logged to ~/.claude/model-fallback-gate.log). The
# watchdog recovery kind `model-restore` types `/model <managed>` into the idle
# pane. Deliberate off-lineup headless runs: `AIRULESET_MODEL_GUARD=off`.
#
# Exit 2 = block; the module writes its reason to STDERR (emit_block_stderr) and
# the `1>&2` below routes any stray stdout there too. A python MALFUNCTION fails
# OPEN (RC not 0/2): a hook bug must never block every tool call of every
# session. `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the
# real package.
# Dry-run: echo '{"transcript_path":"/path/session.jsonl","tool_name":"Bash"}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.modelfallback <<<"$PAYLOAD" 1>&2 || RC=$?
[ "$RC" -eq 2 ] && exit 2
if [ "$RC" -ne 0 ] && [ -n "${HOME:-}" ]; then   # a MALFUNCTION fails open, never silently
    { mkdir -p "$HOME/.claude" && printf '%s\tadapter-crash\trc=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RC" >> "$HOME/.claude/model-fallback-gate.log"; } \
        2>/dev/null || true
fi
exit 0
