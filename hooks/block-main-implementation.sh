#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Edit | Write) — airuleset #32, generalized by #54
#
# A MAIN session (no agent_id) is a COORDINATOR, not an implementer — under
# TWO independent conditions, either one alone is enough to block:
#
# 1. FABLE MODEL (#32, unchanged): a main running on Fable re-reads the FULL
#    conversation at Fable prices every turn — an implementation loop there
#    (write code, run test, fix, repeat) is the single biggest burn the user
#    has (3 Max subscriptions exhausted; the presenter session implemented a
#    whole issue in its Fable main, 2026-07-24, despite the ADVISOR-shape
#    rule in prose).
# 2. ARMED /goal (#54, new): the autopilot contract is main = coordinator,
#    dispatched WORKER = implementer, on ANY model — Opus, Sonnet, whatever.
#    Measured live (david@subdev, odoo-erp transcript): a goal-armed Opus
#    main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches;
#    context grew 0 -> 271K in ~7 minutes of inline work after a /compact.
#    That is the exact "main writes code instead of dispatching a worker"
#    complaint this hook now blocks regardless of model.
#
# So: a MAIN-session Edit/Write whose written content exceeds
# AIRULESET_FABLE_EDIT_MAX (~20 lines) is BLOCKED when EITHER holds. Small
# edits pass (oversight is legitimate). Subagents ALWAYS pass — a subagent's
# payload carries `agent_id`; execution is exactly what belongs there.
#
# Fable-model detection (unchanged from #32): the LAST real assistant
# entry's `"model"` in the transcript tail (the /model choice can change
# mid-session; see the KNOWN caveat below). Fail-open: unreadable transcript
# / unknown model / no jq → allow.
#
# Goal-armed detection (#54): reads the SESSION TRANSCRIPT, never a pane
# capture — a hook has no reliable pane access, only the payload's
# `transcript_path`. Claude Code itself writes a plain
# `<local-command-stdout>Goal set: ...` / `Goal cleared: ...` marker as a
# top-level "user"/"system" transcript entry whenever `/goal` arms or
# resolves/clears. The LATEST such marker in the file decides: "set" with no
# later "cleared" = armed. Deliberately restricted to TOP-LEVEL string
# content (`.message.content` for "user", `.content` for "system") — NEVER
# inside a `tool_result` array entry — so a session that greps or pastes
# ANOTHER session's transcript (containing the same marker text) is never
# mistaken for its OWN goal state. No byte/line bound: an armed goal can
# have been set arbitrarily far back in a long session, so the whole
# transcript is scanned (grep/jq are fast; correctness matters more than
# shaving a full-file scan here).
#
# KNOWN, NOT fixed here (#38): the Fable-model detection above can read a
# STALE model off the transcript tail right after a `/model` switch,
# causing a false Fable-block. The goal-armed path added by #54 is fully
# INDEPENDENT of model detection — it never reads or depends on `MODEL` — so
# it neither triggers nor worsens #38; a stale-model false-block is exactly
# as likely (no more, no less) as it was before this change.
#
# Bypass (rare, logged): touch /tmp/airuleset-main-exec-ok-<session_id>
# (generalized name). The original Fable-only marker
# /tmp/airuleset-fable-exec-ok-<session_id> is STILL honored for backward
# compatibility (nothing outside this hook + its own tests referenced the
# literal path, but an old habit or a stale note shouldn't silently stop
# working).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -z "$AGENT_ID" ] || exit 0            # subagent — execution belongs there

LEN=$(echo "$INPUT" | jq -r \
    '(.tool_input.new_string // .tool_input.content // "") | length' \
    2>/dev/null || echo 0)
MAX="${AIRULESET_FABLE_EDIT_MAX:-800}"
[ "$LEN" -gt "$MAX" ] 2>/dev/null || exit 0     # surgical edit — oversight

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9_-')

BYPASS_MARK=""
if [ -e "/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="main-exec-ok"
elif [ -e "/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="fable-exec-ok(legacy)"
fi
if [ -n "$BYPASS_MARK" ]; then
    echo "$(date -Is) main-exec bypass session=$SESSION_ID len=$LEN marker=$BYPASS_MARK" \
        >> /tmp/airuleset-main-exec-bypass.log 2>/dev/null || true
    exit 0
fi

TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
[ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0

# ---- condition 1: Fable-model main (#32) ----
# newest claude-* model in the transcript tail = the session's CURRENT model
MODEL=$(tail -c 400000 "$TRANSCRIPT" 2>/dev/null \
    | grep -oE '"model"[[:space:]]*:[[:space:]]*"claude-[a-z0-9.-]+"' \
    | tail -1 | grep -oE 'claude-[a-z0-9.-]+' || echo "")
IS_FABLE=0
case "$MODEL" in
    claude-fable-*) IS_FABLE=1 ;;
esac

# ---- condition 2: armed /goal main (#54) ----
GOAL_MARK=$(jq -r '
    if .type == "user" and (.message.content | type) == "string" then .message.content
    elif .type == "system" and (.content | type) == "string" then .content
    else empty end
' "$TRANSCRIPT" 2>/dev/null \
    | grep -oE '<local-command-stdout>Goal (set|cleared):' | tail -1 || echo "")
GOAL_ARMED=0
case "$GOAL_MARK" in
    *"Goal set:") GOAL_ARMED=1 ;;
esac

if [ "$IS_FABLE" != "1" ] && [ "$GOAL_ARMED" != "1" ]; then
    exit 0                               # neither condition holds — allow
fi

if [ "$GOAL_ARMED" = "1" ] && [ "$IS_FABLE" = "1" ]; then
    REASON="this MAIN session runs FABLE *and* has an ARMED /goal"
elif [ "$GOAL_ARMED" = "1" ]; then
    REASON="this MAIN session has an ARMED /goal"
else
    REASON="this MAIN session runs FABLE"
fi

cat >&2 <<MSG
BLOCKED: ${REASON} and this ${LEN}-char write is IMPLEMENTATION work. Under
the autopilot contract the MAIN session COORDINATES — decisions, oversight,
short surgical edits (under ${MAX} chars) — a dispatched WORKER types settled
code (model-awareness.md ADVISOR shape; the /goal generalization is #54,
david@subdev inline-354-edits incident):

  • dispatch the implementation to a worker NOW — an Agent
    (subagent_type: general-purpose, model: sonnet, effort: high) whose
    prompt carries the FULL context you hold (files, decisions, exact
    diffs to make, test expectations) — "I have it in my head" is not a
    reason; the prompt is how the head is handed over. For issue-shaped
    work under an armed /goal use the autopilot-worker; for plan execution
    use superpowers:subagent-driven-development.
  • then REVIEW the worker's diff here — that is the coordinator's job.

Deliberate exception (logged): touch /tmp/airuleset-main-exec-ok-<session_id>
MSG
exit 2
