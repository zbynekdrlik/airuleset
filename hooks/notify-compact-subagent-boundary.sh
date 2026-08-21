#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — two jobs, NEITHER of which records a `/compact`
# request any more (#610, 2026-08-21):
#   (1) writes the #486 G1 structured session heartbeat for THIS subagent
#       (~/.claude/session-status/<sid>__<agent_id>.json, kind=subagent) —
#       for EVERY subagent stop; and
#   (2) for an `autopilot-worker` return, appends ONE explicit DECLINE line
#       to the decision log (~/.claude/compact-decisions.log) recording that
#       the `/compact`-request channel is RETIRED — an observable decision,
#       never a silent branch.
#
# WHY THE RECORD CHANNEL IS RETIRED (#610). This hook USED to record a
# `/compact` request on every autopilot-worker return, on the theory that a
# returning worker IS a completed ticket whose result is already durable in
# git / GitHub — true under the OLD model, where the worker ITSELF merged and
# reported (return == ticket done). Under the FLEET model (issues 317/456) that
# theory is false: a worker RETURNS a branch and the SUPERVISOR integrates it
# SERIALLY (merge, test, push, close, report) in a LATER turn — so the worker
# return is NOT the supervisor's ticket boundary, the integration is. Recording
# a request here fired a `/compact` at the first idle instant, which on a
# multi-lane supervisor is exactly the gap BETWEEN a worker return and its
# integration — i.e. mid-flow. Once issue 599 gave a recorded request a standing
# (refreshing) claim and dropped the `⏳` veto, these false-boundary requests
# never aged out (each later worker return refreshed them) and delivered at
# every idle window: montalu6 took 5 mid-flow compacts with ZERO
# `## ✅ Work Complete` between them (owner report; forensics on issue 610).
#
# WHAT REPLACES IT. Under supervisor-serial-integration EVERY integrated ticket
# ends with the supervisor's own `## ✅ Work Complete` report, which records a
# `/compact` request via the `self-callback` origin (`compact-request --self`,
# with issue 411's Stop-hook backstop firing even when the session forgets the
# explicit call). That is exactly ONE request per genuinely-completed ticket —
# the designed cadence — anchored to the supervisor's REAL boundary, not a
# worker's. Native Claude Code auto-compaction remains the context-pressure
# backstop for a supervisor that never reports. So the subagent-stop channel was
# redundant where it was correct (old worker-merges model) and harmful where it
# was not (fleet model); it is REMOVED rather than re-gated (issue 486: net LOC
# down, no new heuristic, no re-added marker veto).
#
# REOPEN TRIGGER. If the autopilot model ever reverts to workers that
# merge + report their OWN tickets (return == ticket done, no separate
# supervisor integration), a per-return boundary becomes correct again and
# this channel should be restored (record `origin=subagent-stop` per the git
# history of this file before #610).
#
# THE DECISION LOG itself (the `_decide_log*` helpers, the once-per-(session,
# agent_type, reason) suppression for the high-volume non-worker class, the
# 512 KB rotation) is UNCHANGED — only the autopilot-worker branch's OUTCOME
# changed from RECORD to an explicit DECLINE (#123/#146: a guard whose correct
# operation is indistinguishable from its total absence can only be checked in
# replay, so the retirement is logged, not silent). Silent + non-blocking:
# never writes to stdout, always exits 0 — a SubagentStop hook that emitted
# anything could interfere with the worker's own stop decision
# (subagent-stop-check-bg-work.sh owns that).

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

DECISION_LOG="$HOME/.claude/compact-decisions.log"
DECISION_SEEN_DIR="$HOME/.claude/.compact-decisions-seen"
DECISION_CAP=512000
DECISION_SEEN_TTL_DAYS=14

# `_decide_log <OUTCOME> [extra k=v tokens]` — one line, never fatal.
_decide_log() {
    local outcome="$1"
    local extra="${2:-}"
    local size
    mkdir -p "$(dirname "$DECISION_LOG")" 2>/dev/null || true
    size=$(stat -c %s "$DECISION_LOG" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    if [ "$size" -gt "$DECISION_CAP" ]; then
        mv -f "$DECISION_LOG" "$DECISION_LOG.1" 2>/dev/null || true
    fi
    {
        printf '%s %s %stype=%s agent=%s sid=%s cwd=%s\n' \
            "$(date -Iseconds 2>/dev/null || echo '?')" "$outcome" \
            "${extra:+$extra }" "${AGENT_TYPE:--}" "${AGENT_ID:--}" \
            "${SID:--}" "${CWD_LOG:--}" >>"$DECISION_LOG"
    } 2>/dev/null || true
}

# The high-volume class: log the FIRST decline for a (session, agent_type,
# reason) triple, then suppress every later one for that SAME triple — a
# repeat of the exact same branch shape is pure noise (#146). The marker
# write is ATOMIC (`set -o noclobber`, #146 review finding 4 — the earlier
# `[ -e ] && … ; touch` shape raced: verified concurrent, more than one
# winner) so concurrent stops for the identical triple still produce
# exactly one line, never zero and never more than one.
_decide_log_once_per_session() {
    local outcome="$1"
    local extra="${2:-}"
    local key seen_file
    key=$(printf '%s|%s|%s' "${SID:-}" "${AGENT_TYPE:-}" "$extra" \
        | tr -c 'A-Za-z0-9=_|-' '_')
    # clamp well under NAME_MAX (255 bytes) so a pathological session_id
    # can never make marker creation fail and silently re-enable the flood
    # (#146 review finding 2 — this used to fail ENAMETOOLONG, unnoticed).
    key=${key:0:200}
    seen_file="$DECISION_SEEN_DIR/$key"
    [ -e "$seen_file" ] && return 0
    mkdir -p "$DECISION_SEEN_DIR" 2>/dev/null || true
    # bound the directory's growth -- a marker no session will ever revisit
    # must not survive forever. `-mtime +N` matches files whose age exceeds
    # N+1 FULL days (a find quirk), so N-1 here is what actually enforces a
    # TRUE $DECISION_SEEN_TTL_DAYS-day bound.
    find "$DECISION_SEEN_DIR" -maxdepth 1 -type f \
        -mtime "+$((DECISION_SEEN_TTL_DAYS - 1))" -delete 2>/dev/null || true
    # atomic create-if-absent: noclobber's `>` fails (silently, `2>/dev/null`)
    # if the file already exists, so at most one concurrent racer ever wins.
    ( set -o noclobber; : > "$seen_file" ) 2>/dev/null || return 0
    _decide_log "$outcome" "$extra"
}

_field() {
    printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""
}

AGENT_TYPE=$(_field '.agent_type // empty')
SID=$(_field '.session_id // empty')
AGENT_ID=$(_field '.agent_id // empty')
CWD=$(_field '.cwd // empty')
# the log is whitespace-delimited, so only the logged COPY is squeezed
CWD_LOG=${CWD// /_}

# #486 G1 — structured session heartbeat for THIS subagent
# (~/.claude/session-status/<sid>__<agent_id>.json, kind=subagent — the
# complement to G2's transcript-mtime worker count). Placed BEFORE this hook's
# own autopilot-worker/agent-type branch so EVERY subagent stop is recorded;
# the producer keys by agent_id so it can never clobber the main <sid>.json.
# Best-effort + non-blocking; no consumer yet (G1).
_HB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
printf '%s' "$INPUT" | PYTHONPATH="$_HB_DIR" \
    python3 -m watchdog.session_status --event subagent_stop >/dev/null 2>&1 || true

# The high-volume non-worker class (SubagentStop fires once per parallel
# tool-call branch as well as per dispatched subagent) stays suppressed to one
# line per (session, agent_type, reason) triple (#146).
[ "$AGENT_TYPE" = "autopilot-worker" ] || {
    _decide_log_once_per_session DECLINE "reason=not-autopilot-worker"
    exit 0
}

# #610 — the subagent-stop RECORD channel is RETIRED (see the header). A worker
# RETURN is not the SUPERVISOR's ticket boundary under the fleet model; the
# per-ticket compact is now the supervisor's own `## ✅ Work Complete` ->
# `self-callback` record. Log the retirement explicitly (an observable decision,
# never a silent branch) and record nothing.
_decide_log DECLINE "reason=record-channel-retired-610"
exit 0
