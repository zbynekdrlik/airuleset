#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash | Write | Edit) — airuleset #819.
#
# Companion to block-main-implementation.sh's ONE-SHOT bypass marker
# (/tmp/airuleset-main-exec-ok-<sid>, legacy /tmp/airuleset-fable-exec-ok-
# <sid>). That PreToolUse guard USED to consume (delete) the marker the
# instant IT allowed a call — but "the guard allowed it" is NOT "the command
# ran". A LATER sibling PreToolUse hook (block-local-poll-repeat #119,
# block-ci-poll-repeat, block-history-rewrite, …) can still exit 2 on the
# SAME call, so the command never runs; consuming in PreToolUse then stranded
# the one-shot on a call that never executed, forcing a needless re-echo
# (the #819 bug).
#
# So the guard now DEFERS: on its VALID-reason path it LEAVES the marker and
# writes a session-scoped pending flag (/tmp/airuleset-main-exec-pending-
# <sid>). THIS PostToolUse hook consumes the one-shot — deletes marker +
# pending — only once the tool actually RAN. Claude Code fires PostToolUse
# after a tool that ran, and does NOT fire it for a call a PreToolUse hook
# denied — exactly the behaviour the one-shot needs, so a sibling-blocked
# call preserves the marker for the retry.
#
# What "ran" covers is the fail-safe boundary. It DOES fire for a Bash command
# that ran and exited non-zero (the tool call itself succeeded), and for
# run_in_background Bash it fires at LAUNCH = "the command started" = consume
# (the fail-safe direction). Two residuals leave the marker at most ONE extra
# call, always in the fail-safe (over-survive-briefly, never wrong-allow)
# direction, self-healing on the next executed guarded call: (a) a user Ctrl+C
# mid-Bash may skip PostToolUse; (b) whether PostToolUse fires for a tool CC
# treats as a hard ERROR (e.g. a failed Edit whose old_string was not found)
# is NOT independently verified here — if it does not, that failed edit's
# marker survives one call.
#
# The pending flag is written ONLY by the guard's marker block (a genuine
# marker-exempted call), so the arming echo (which exits early, before the
# marker block) and small/allow-listed calls never WRITE it. This consumer
# keys purely on the pending flag's PRESENCE (zero duplicated classification),
# which is deliberately imprecise in the fail-safe direction: after a
# sibling-block strands a pending flag, the NEXT ran Bash/Write/Edit — even a
# benign one that never itself drew on the marker — consumes it. That costs at
# most one re-echo and never wrongly allows an implementation call (the
# rejected command-hash alternative would have keyed exactly here, but it
# over-survives on a benign-between call, the LESS safe direction).

command -v jq >/dev/null 2>&1 || exit 0
INPUT=$(cat 2>/dev/null || echo "")

# #835: extract a payload field via jq with a FORK-FREE bash fallback used ONLY
# when the jq SPAWN transiently fails under fork pressure — mirrors the sibling
# block-main-implementation.sh `_ai_field`. WITHOUT this, a transient jq failure
# in a SUBAGENT's PostToolUse makes `agent_id` read empty → the consumer treats
# the subagent as the MAIN session and consumes the main's marker (the exact
# wrong-consume the #819 subagent guard forbids); and a session_id jq failure
# leaves the marker un-consumed → the OneShotBypass80 `-n auto` flake (both
# `test_marker_is_consumed_after_the_command_runs` and
# `test_legacy_marker_is_also_consumed`). jq stays PRIMARY (byte-for-byte on
# success); the regex fallback is correct for CC's well-formed payload.
_AI_AGENT_RE='"agent_id"[[:space:]]*:[[:space:]]*"([^"]*)"'
_AI_SID_RE='"session_id"[[:space:]]*:[[:space:]]*"([^"]*)"'
_ai_field() {   # $1 = jq filter, $2 = default, $3 = fallback ERE (capture grp 1)
    local _out
    if _out=$(printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null); then
        printf '%s' "$_out"
    elif [[ "$INPUT" =~ $3 ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    else
        printf '%s' "$2"
    fi
}

# #819 change 2 — subagent guard: a background subagent (autopilot-worker)
# runs Bash CONCURRENTLY with the main session and its PostToolUse fires with
# the same <sid>. It must NEVER consume the main session's pending marker
# (which would nondeterministically re-break the very bug this hook fixes on
# worker-heavy boxes). Mirrors block-main-implementation.sh's own agent_id
# early-exit — only the MAIN session ever writes or consumes the marker.
AGENT_ID=$(_ai_field '.agent_id // empty' '' "$_AI_AGENT_RE")
[ -z "$AGENT_ID" ] || exit 0

SESSION_ID=$(_ai_field '.session_id // empty' '' "$_AI_SID_RE")
# #819 review: sanitize the sid EXACTLY as block-main-implementation.sh does
# (r.245: `tr -cd 'A-Za-z0-9_-'`). The guard writes the pending flag + markers
# under the sanitized sid, so the consumer MUST look them up under the same
# sanitized form — otherwise an exotic sid (any stripped char) would make this
# consumer miss the pending flag and the marker would survive every executed
# call (the wrong-allow direction that recreates the pre-#80 kill switch). It
# also keeps a raw payload string out of the `rm -f` paths below.
SESSION_ID="${SESSION_ID//[!A-Za-z0-9_-]/}"     # #835: fork-free sanitize (was `tr -cd`)
[ -n "$SESSION_ID" ] || exit 0

PENDING="/tmp/airuleset-main-exec-pending-${SESSION_ID}"
[ -e "$PENDING" ] || exit 0     # nothing deferred for this session — no-op

# The audit log dir mirrors block-main-implementation.sh's #732 env seam so a
# test can isolate this consumer's log line. Fail-safe (all #732 gotchas):
# default = /tmp byte-for-byte when unset; strip a trailing slash FIRST then
# require a NON-EMPTY, WRITABLE dir; keep every check in an `if` CONDITION so
# a failure never trips `set -e`; `${VAR:-}` for `set -u`.
_EXEC_LOG_DIR="/tmp"
_EXEC_LOG_OVR="${AIRULESET_MAIN_EXEC_LOG_DIR:-}"
_EXEC_LOG_OVR="${_EXEC_LOG_OVR%/}"          # strip a trailing slash ("/" -> "")
if [ -n "$_EXEC_LOG_OVR" ] && [ -d "$_EXEC_LOG_OVR" ] && [ -w "$_EXEC_LOG_OVR" ]; then
    _EXEC_LOG_DIR="$_EXEC_LOG_OVR"
fi
BYPASS_LOG="${_EXEC_LOG_DIR}/airuleset-main-exec-bypass-${EUID:-$(id -u)}.log"

# a marker-exempted call just RAN — consume the one-shot now (post-exec).
# The reason (written by the guard) rides in the pending file for the log;
# flatten it the same way the guard does so one log line stays one line.
REASON=$(tr '\n\r\t' '   ' < "$PENDING" 2>/dev/null \
    | tr -d '\000-\010\013\014\016-\037\177' \
    | sed 's/  */ /g; s/^ //; s/ $//') || REASON=""

# consume BOTH marker variants (only one is ever present; deleting both is
# simpler than recording which validated) plus the pending flag.
rm -f "/tmp/airuleset-main-exec-ok-${SESSION_ID}" \
      "/tmp/airuleset-fable-exec-ok-${SESSION_ID}" \
      "$PENDING" 2>/dev/null || true

{ echo "$(date -Is) main-exec bypass session=$SESSION_ID (consumed, post-exec) reason=$REASON" \
    >> "$BYPASS_LOG"; } 2>/dev/null || true

exit 0
