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
# Gate ONLY for `subagent_type` == autopilot-worker — never ticket-validator /
# Explore / general-purpose / an ad-hoc review consult (those are read-only /
# judgment, not I-lane dispatches). (#991: the pinned tier-agent types are gone;
# the single I-lane worker type is autopilot-worker.)
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

# Only gate Agent and Task tool calls (#868 review YELLOW-4: Task is a
# live dispatch surface — block-main-implementation.sh registers under both)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")
case "$TOOL_NAME" in
    Agent|Task) ;;
    *) exit 0 ;;
esac

# Only gate implementation-worker types
SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")
case "$SUBAGENT_TYPE" in
    autopilot-worker) ;;
    *) exit 0 ;;
esac

# Read cwd from the hook payload
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -n "$CWD" ] || exit 0   # fail-open: no cwd

# Compute cwd-key (sha1[:12]) — must match statusbar.cwd_key
CWD_KEY=$(printf '%s' "$CWD" | sha1sum | cut -c1-12)

# ---------------------------------------------------------------------------
# #992/#993 — INDEPENDENCE-CHECK GATE (runs for EVERY autopilot-worker dispatch,
# independent of the W-drain gate below). Before dispatching a lane the
# supervisor must run `airuleset.py lane-overlap … --issue <N>`, which writes a
# per-cwd receipt (~/.claude/lane-overlap/<cwd-key>.json). This gate BLOCKS the
# dispatch unless a FRESH receipt covers every issue named in the prompt — the
# mechanical half of #992 req 2 (no new hook). Fail-OPEN when the prompt carries
# no parseable issue number (can't verify → allow; the `Independence:` ticket
# record is the durable authority). `OVERLAP-BYPASS: <reason>` in the prompt
# allows + logs, exactly like WDRAIN-BYPASS below.
OVERLAP_TTL=1800
PROMPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null || echo "")
# Extract EVERY issue number on the "issue(s) #…" line (the autopilot dispatch
# shape "Work issue #N" / "Work issues #A #B #C" / "#A, #B and #C") — the whole
# line, not a space-only run, so a comma/"and"-separated batch is fully covered
# (#993-review: a truncated span left later members independence-unchecked).
ISSUE_LINE=$(printf '%s' "$PROMPT" | grep -oiE '(work[[:space:]]+)?issues?[[:space:]]+#[0-9].*' | head -1 || true)
ISSUES=$(printf '%s' "$ISSUE_LINE" | grep -oE '#[0-9]+' | tr -d '#' || true)
if [ -n "$ISSUES" ]; then
    # OVERLAP-BYPASS escape (line-anchored, logged)
    if printf '%s' "$PROMPT" | grep -qE '^OVERLAP-BYPASS:'; then
        OV_DIR="$HOME/.claude/lane-overlap"
        mkdir -p "$OV_DIR" 2>/dev/null || true
        OV_REASON=$(printf '%s' "$PROMPT" | grep -oiE '^OVERLAP-BYPASS:[[:space:]]*.*' | head -1 || true)
        printf '%s\t%s\t%s\n' "$(date -Iseconds)" "$CWD_KEY" "$OV_REASON" \
            >> "$OV_DIR/bypass.log" 2>/dev/null || true
        exit 0
    fi
    OV_RECEIPT="$HOME/.claude/lane-overlap/$CWD_KEY.json"
    OV_BLOCK=""
    if [ ! -f "$OV_RECEIPT" ]; then
        OV_BLOCK="no overlap-check receipt for this box"
    else
        OV_TS=$(jq -r '.ts // empty' "$OV_RECEIPT" 2>/dev/null || echo "")
        OV_TS_INT=${OV_TS%%.*}
        case "$OV_TS_INT" in
            ''|*[!0-9]*) OV_BLOCK="unreadable receipt timestamp" ;;
            *)
                NOW_OV=$(date +%s)
                if [ $(( NOW_OV - OV_TS_INT )) -gt "$OVERLAP_TTL" ]; then
                    OV_BLOCK="overlap-check receipt is stale (> ${OVERLAP_TTL}s)"
                fi
                ;;
        esac
        if [ -z "$OV_BLOCK" ]; then
            for _iss in $ISSUES; do
                COVERED=$(jq -e --argjson n "$_iss" \
                    '(.checked_issues // []) | index($n) != null' \
                    "$OV_RECEIPT" >/dev/null 2>&1 && echo 1 || echo 0)
                if [ "$COVERED" != "1" ]; then
                    OV_BLOCK="receipt does not cover issue #$_iss"
                    break
                fi
            done
        fi
    fi
    if [ -n "$OV_BLOCK" ]; then
        {
            echo "BLOCKED: independence check not run for this dispatch — $OV_BLOCK."
            echo ""
            echo "  Before dispatching a lane, run the overlap check so the supervisor"
            echo "  records the unit's touched paths + topic vs every live lane + open PR"
            echo "  (#992/#993 — an overlapping lane must WAIT, not race):"
            echo ""
            echo "    python3 ~/devel/airuleset/airuleset.py lane-overlap \\"
            echo "      --paths <p1,p2> --topics <topic> --issue $(printf '%s' "$ISSUES" | tr '\n' ' ')"
            echo ""
            echo "  Then re-dispatch. Or bypass with OVERLAP-BYPASS: <reason> in the prompt (logged)."
        } >&2
        exit 2
    fi
fi
# ---------------------------------------------------------------------------

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

# Threshold check
THRESHOLD=8

# #986: gate on STALE W count when available (ops_wait_stale), not total W.
# A well-maintained W bucket with actively-recited acceptance waits should
# not block dispatch; only genuinely stale (un-pushed) members matter.
# STALE_THRESHOLD is lower than THRESHOLD (3 vs 8) because stale members
# indicate a real drain failure. Falls back to ops_wait for legacy caches.
STALE_THRESHOLD=3

OPS_WAIT_STALE=$(jq -r '.ops_wait_stale // empty' "$CACHE_FILE" 2>/dev/null || echo "")
case "$OPS_WAIT_STALE" in ''|*[!0-9]*) OPS_WAIT_STALE="" ;; esac

# #953: deploy-target exempt — W members blocked on a release/deploy train
# do not count toward the threshold. The effective W is reduced by the exempt
# count. Hard ceiling at 2*THRESHOLD prevents unbounded W growth.
DEPLOY_WAIT=$(jq -r '.ops_wait_deploy_wait // empty' "$CACHE_FILE" 2>/dev/null || echo "")
case "$DEPLOY_WAIT" in ''|*[!0-9]*) DEPLOY_WAIT=0 ;; esac

HARD_CEILING=$(( THRESHOLD * 2 ))
if [ -n "$OPS_WAIT_STALE" ]; then
    # #986: stale-aware path — gate on stale count, not total W.
    # When per-member evidence is available, the hard ceiling is
    # stale-aware too — a W=20 with stale=0 is genuinely healthy.
    if [ "$OPS_WAIT_STALE" -le "$STALE_THRESHOLD" ]; then
        exit 0   # stale count under threshold — allow
    fi
elif [ "$OPS_WAIT" -gt "$HARD_CEILING" ]; then
    : # fall through to receipt/bypass/block — hard ceiling breached
elif [ "$DEPLOY_WAIT" -gt 0 ] 2>/dev/null; then
    EFFECTIVE_W=$(( OPS_WAIT - DEPLOY_WAIT ))
    if [ "$EFFECTIVE_W" -lt 0 ]; then EFFECTIVE_W=0; fi
    if [ "$EFFECTIVE_W" -le "$THRESHOLD" ]; then
        exit 0   # under threshold after exemption — allow
    fi
elif [ "$OPS_WAIT" -le "$THRESHOLD" ]; then
    exit 0   # under threshold — allow (legacy cache fallback)
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

# Check for WDRAIN-BYPASS: token in the prompt (line-anchored to avoid
# ticket-body injection — #868 review YELLOW-3)
PROMPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null || echo "")
if printf '%s' "$PROMPT" | grep -qE '^WDRAIN-BYPASS:'; then
    # Log the bypass and allow
    mkdir -p "$RECEIPT_DIR"
    BYPASS_REASON=$(printf '%s' "$PROMPT" | grep -oP '^WDRAIN-BYPASS:\s*\K.*' | head -1)
    printf '%s\t%s\t%s\t%s\n' "$(date -Iseconds)" "$CWD_KEY" "$SUBAGENT_TYPE" "$BYPASS_REASON" \
        >> "$RECEIPT_DIR/bypass.log" 2>/dev/null || true
    exit 0
fi

# BLOCK the dispatch
{
    if [ -n "$OPS_WAIT_STALE" ]; then
        echo "BLOCKED: W-drain gate — stale=$OPS_WAIT_STALE > threshold $STALE_THRESHOLD (total W=$OPS_WAIT)."
    else
        echo "BLOCKED: W-drain gate — |W|=$OPS_WAIT > threshold $THRESHOLD."
    fi
    echo ""
    echo "  The parked-W bucket exceeds the drain threshold."
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
