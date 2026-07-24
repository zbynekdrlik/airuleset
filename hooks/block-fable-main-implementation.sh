#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Edit | Write) — airuleset #32
# A MAIN session running on Fable re-reads the FULL conversation at Fable
# prices every turn — an implementation loop there (write code, run test,
# fix, repeat) is the single biggest burn the user has (3 Max subscriptions
# exhausted; the presenter session implemented a whole issue in its Fable
# main, 2026-07-24, despite the prose ADVISOR rule). Fable main = decisions,
# oversight, short surgical interventions; TYPING SETTLED CODE is a
# Sonnet-worker job (model-awareness.md ADVISOR shape; the context the main
# "has in its head" is passed to the worker in the dispatch prompt).
#
# So: a MAIN-session (no agent_id in the payload) Edit/Write whose written
# content exceeds AIRULESET_FABLE_EDIT_MAX (~20 lines) while the session's
# CURRENT model is claude-fable-* is BLOCKED with the delegation
# instruction. Small edits pass — oversight is legitimate. Subagents pass —
# execution belongs there. Non-Fable mains pass (Opus main writing code is
# within policy tolerance; the target is the Fable burn multiplier).
#
# Model detection: the LAST real assistant entry's `"model"` in the session
# transcript (the /model choice can change mid-session; synthetic error
# entries carry no claude-* model and are naturally skipped). Fail-open:
# unreadable transcript / unknown model / no jq → allow.
# Deliberate bypass (rare, logged): touch /tmp/airuleset-fable-exec-ok-<sid>.

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
if [ -e "/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}" ]; then
    echo "$(date -Is) fable-exec bypass session=$SESSION_ID len=$LEN" \
        >> /tmp/airuleset-fable-exec-bypass.log 2>/dev/null || true
    exit 0
fi

TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
[ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0

# newest claude-* model in the transcript tail = the session's CURRENT model
MODEL=$(tail -c 400000 "$TRANSCRIPT" 2>/dev/null \
    | grep -oE '"model"[[:space:]]*:[[:space:]]*"claude-[a-z0-9.-]+"' \
    | tail -1 | grep -oE 'claude-[a-z0-9.-]+' || echo "")
case "$MODEL" in
    claude-fable-*) ;;                  # the guarded tier — fall through
    *) exit 0 ;;                        # non-Fable / unknown → allow
esac

cat >&2 <<MSG
BLOCKED: this MAIN session runs on FABLE and this ${LEN}-char write is
IMPLEMENTATION work. A Fable main re-reads the whole conversation at Fable
prices every turn — an implementation loop here is the burn that exhausts
the user's subscriptions (model-awareness.md ADVISOR shape; presenter
incident 2026-07-24). Fable main does DECISIONS, OVERSIGHT and short
surgical edits (under ${MAX} chars) — settled code is typed by a WORKER:

  • dispatch the implementation to a Sonnet worker NOW — an Agent
    (subagent_type: general-purpose, model: sonnet, effort: high) whose
    prompt carries the FULL context you hold (files, decisions, exact
    diffs to make, test expectations) — "I have it in my head" is not a
    reason; the prompt is how the head is handed over. For issue-shaped
    work use the autopilot-worker; for plan execution use
    superpowers:subagent-driven-development.
  • then REVIEW the worker's diff here — that is the master's job.

Deliberate exception (logged): touch /tmp/airuleset-fable-exec-ok-<session_id>
MSG
exit 2
