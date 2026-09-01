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
# after a tool that ran (including a ran-and-errored Bash command), and does
# NOT fire it for a call a PreToolUse hook denied — exactly the behaviour the
# one-shot needs, so a sibling-blocked call preserves the marker for the
# retry. (Fail-safe: run_in_background Bash fires PostToolUse at LAUNCH =
# "the command started" = consume, the fail-safe direction; a user Ctrl+C
# mid-Bash may skip PostToolUse, leaving the marker one call longer — a
# bounded, rare residual.)
#
# The pending flag is written ONLY by the guard's marker block (a genuine
# marker-exempted call), so the arming echo (which exits early, before the
# marker block) and small/allow-listed calls never set it — this consumer
# never wrongly consumes for them. It simply keys on the pending flag's
# PRESENCE, mirroring the guard's exact consumption point with zero
# duplicated classification.

command -v jq >/dev/null 2>&1 || exit 0
INPUT=$(cat)

# #819 change 2 — subagent guard: a background subagent (autopilot-worker)
# runs Bash CONCURRENTLY with the main session and its PostToolUse fires with
# the same <sid>. It must NEVER consume the main session's pending marker
# (which would nondeterministically re-break the very bug this hook fixes on
# worker-heavy boxes). Mirrors block-main-implementation.sh's own agent_id
# early-exit — only the MAIN session ever writes or consumes the marker.
AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -z "$AGENT_ID" ] || exit 0

SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
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
