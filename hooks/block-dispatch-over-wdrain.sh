#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent) — #868 (W-drain gate, fleet-wide).
#
# Mechanically enforces #754's W-drain threshold: when the parked-W set
# exceeds OPS_WAIT_WDRAIN_THRESHOLD (=8, lock-tested), an implementation-
# worker dispatch is BLOCKED until the supervisor records a W-drain verdict
# via `airuleset.py wdrain-pass --record`. The hook interposes at the exact
# dispatch action the montalu3 W=34 incident abused — dispatching new I-lanes
# for days while finished W tickets rotted.
#
# Gate ONLY for `subagent_type` in {autopilot-worker, sonnet-implementer} —
# never ticket-validator / fable-advisor / sonnet-mechanical / Explore /
# general-purpose (those are read-only / judgment / mechanical, not I-lane
# dispatches).
#
# Fail-OPEN on missing jq, missing/unparseable cache, stale cache (>30 min),
# non-int ops_wait (#539/#570 "never a false accusation").
#
# Exit 2 = block; Claude reads STDERR as the reason. Stdin contract: the JSON
# payload arrives on STDIN (.tool_input.*), never $TOOL_INPUT.
#
# Threshold 8 is lock-tested == OPS_WAIT_WDRAIN_THRESHOLD (cli_quals.py).

# Fail-open: missing jq
command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

# Only gate Agent tool calls
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")
[ "$TOOL_NAME" = "Agent" ] || exit 0

# Only gate implementation-worker types
SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")
case "$SUBAGENT_TYPE" in
    autopilot-worker|sonnet-implementer) ;;
    *) exit 0 ;;
esac

# Read cwd from the hook payload
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -n "$CWD" ] || exit 0   # fail-open: no cwd

# Compute cwd-key (sha1[:12]) — must match statusbar.cwd_key
CWD_KEY=$(printf '%s' "$CWD" | sha1sum | cut -c1-12)

# Read ops_wait from tickets-status cache
CACHE_DIR="$HOME/.claude/tickets-status"
CACHE_FILE="$CACHE_DIR/$CWD_KEY.json"
[ -f "$CACHE_FILE" ] || exit 0   # fail-open: no cache

# Read ops_wait and ts from the cache
OPS_WAIT=$(jq -r '.ops_wait // empty' "$CACHE_FILE" 2>/dev/null || echo "")
CACHE_TS=$(jq -r '.ts // empty' "$CACHE_FILE" 2>/dev/null || echo "")

# Fail-open: non-int ops_wait
case "$OPS_WAIT" in
    ''|*[!0-9]*) exit 0 ;;
esac

# Fail-open: stale cache (> 30 min = 1800s)
if [ -n "$CACHE_TS" ]; then
    NOW=$(date +%s)
    # CACHE_TS may be a float — truncate to int
    CACHE_TS_INT=${CACHE_TS%%.*}
    case "$CACHE_TS_INT" in
        ''|*[!0-9]*) exit 0 ;;   # fail-open: non-numeric ts
    esac
    AGE=$(( NOW - CACHE_TS_INT ))
    if [ "$AGE" -gt 1800 ]; then
        exit 0   # fail-open: stale cache
    fi
else
    exit 0   # fail-open: no ts
fi

# Threshold check: W > 8
THRESHOLD=8
if [ "$OPS_WAIT" -le "$THRESHOLD" ]; then
    exit 0   # under threshold — allow
fi

# Check for a valid wdrain receipt
RECEIPT_DIR="$HOME/.claude/wdrain"
RECEIPT_FILE="$RECEIPT_DIR/$CWD_KEY.json"
if [ -f "$RECEIPT_FILE" ]; then
    EXPIRES_AT=$(jq -r '.expires_at // empty' "$RECEIPT_FILE" 2>/dev/null || echo "")
    case "$EXPIRES_AT" in
        ''|*[!0-9]*) ;;   # non-int expires_at — treat as no receipt
        *)
            NOW=${NOW:-$(date +%s)}
            if [ "$EXPIRES_AT" -gt "$NOW" ]; then
                exit 0   # valid receipt — allow
            fi
            ;;
    esac
fi

# Check for WDRAIN-BYPASS: token in the prompt
PROMPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null || echo "")
if printf '%s' "$PROMPT" | grep -qF 'WDRAIN-BYPASS:'; then
    # Log the bypass and allow
    mkdir -p "$RECEIPT_DIR"
    BYPASS_REASON=$(printf '%s' "$PROMPT" | grep -oP 'WDRAIN-BYPASS:\s*\K.*' | head -1)
    printf '%s\t%s\t%s\t%s\n' "$(date -Iseconds)" "$CWD_KEY" "$SUBAGENT_TYPE" "$BYPASS_REASON" \
        >> "$RECEIPT_DIR/bypass.log" 2>/dev/null || true
    exit 0
fi

# BLOCK the dispatch
{
    echo "BLOCKED: W-drain gate — |W|=$OPS_WAIT > threshold $THRESHOLD."
    echo ""
    echo "  The parked-W bucket exceeds OPS_WAIT_WDRAIN_THRESHOLD ($THRESHOLD)."
    echo "  Drain it BEFORE dispatching a new implementation lane:"
    echo ""
    echo "  1. Review:  python3 ~/devel/airuleset/airuleset.py core-quals --ops-wait"
    echo "              (reduced authority: slice-quals --ops-wait)"
    echo "  2. Per-member verdict: close / unpark / re-cite the blocker"
    echo "  3. Record:  python3 ~/devel/airuleset/airuleset.py wdrain-pass --record --verdicts-file F"
    echo ""
    echo "  Or bypass with WDRAIN-BYPASS: <reason> in the dispatch prompt (logged)."
} >&2
exit 2
