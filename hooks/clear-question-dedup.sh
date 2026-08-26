#!/usr/bin/env bash
set -euo pipefail

# Hook: UserPromptSubmit — clear the per-session ❓ dedup state, but ONLY for a
# GENUINE HUMAN prompt (#712).
#
# notify-discord-pending.sh (Stop) dedups the device ping for a question that is
# REPEATED with identical content and NO user input in between (a /goal-loop
# re-poke of a session still blocked on the same unanswered question — the 9×
# "rovnaká otázka ako predtým" restreamer spam, 2026-07-04). The moment the user
# actually TYPES a prompt, that conversation moved on: whatever is asked next is
# a FRESH ask and must ping even if its text happens to be byte-identical to the
# old one. So every real HUMAN prompt clears the LASTQ state.
#
# #712 — NOT every UserPromptSubmit is a human prompt. Empirically PROVEN on
# david1 (issue #712 VALIDATED comment): CC background *task-notification*
# re-invocations fire UserPromptSubmit too (this file used to claim the
# opposite), and watchdog-typed machine nudges (lane-check:/stuck-check:/…)
# arrive through the same channel — an hourly background re-check waiter wiped
# LASTQ every hour and the identical ❓ re-posted 9× overnight into David's
# thread. An AUTOMATED submission must NOT clear the dedup and must NOT stamp
# the presence marker (which stop-check-question-quality.sh, goal_scan's
# recent-human gate and block-main-implementation.sh all read as "the user is
# AT the terminal right now"). Classification:
#   AUTOMATED — empty/missing .prompt (a human cannot submit an empty prompt in
#     CC — this also covers a payload-less firing shape), a `<task-notification>`
#     re-invocation, or a machine-nudge prefix from the catalog
#     watchdog/stash.py::_JANITOR_OWN_PREFIXES documents as "unambiguous OWN
#     payload no human ever types" (lane-check: / stuck-check: /
#     bounce-backstop: / gk-request backstop: ), plus oauth-resume: and the
#     exact bare api-error resume nudge "continue" (watchdog NUDGE_TEXT). The
#     bash list deliberately DUPLICATES the python catalog (a bash hook cannot
#     import python constants) — tests/test_question_dedup_automated_prompt_712.py
#     locks the two together against drift.
#   HUMAN — everything else, INCLUDING arbitrary Discord-reply text typed into
#     the pane on the user's behalf (watchdog job 7): an answer means the
#     conversation moved on, so it must re-open the dedup exactly as before.
# An AUTO skip is a LOGGED decision (notify-delivery.log, kind=promptclear) —
# never a silent drop. Machine texts with a natural-language head (the #505
# class, e.g. the card-flags prompt) have no stable fingerprint and stay
# classified human — an accepted, documented residual.
#
# Silent + non-blocking: no stdout, always exit 0 — never interferes with
# prompt processing. (Stop-hook feedback re-invocations do not fire
# UserPromptSubmit; background task-notification re-invocations DO — #712.)

INPUT=$(cat)

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
# Defang the session id so it can never escape the /tmp prefix (same
# belt-and-suspenders as notify-discord-pending.sh).
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"

PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // ""' 2>/dev/null || echo "")
# Review hardening (#712): classify on the LEADING-whitespace-trimmed prompt —
# a payload variant delivering the task-notification tag after a leading
# newline must not dodge the classifier. (Matching the tag ANYWHERE would
# over-match a human prompt QUOTING one, so trim-then-prefix stays exact;
# trailing whitespace is deliberately kept, narrowing the bare-"continue"
# match to the watchdog's exact NUDGE_TEXT.)
TRIMMED="${PROMPT#"${PROMPT%%[![:space:]]*}"}"

# #712 — classify the submission; only a genuine human prompt falls through to
# the clears below.
AUTO=""
if [ -z "$TRIMMED" ]; then
    AUTO="empty-prompt"
else
    case "$TRIMMED" in
        "<task-notification>"*)   AUTO="task-notification" ;;
        "lane-check: "*)          AUTO="machine-nudge-lane-check" ;;
        "stuck-check: "*)         AUTO="machine-nudge-stuck-check" ;;
        "bounce-backstop: "*)     AUTO="machine-nudge-bounce-backstop" ;;
        "gk-request backstop: "*) AUTO="machine-nudge-gk-request-backstop" ;;
        "oauth-resume:"*)         AUTO="machine-nudge-oauth-resume" ;;
        "continue")               AUTO="machine-nudge-continue" ;;
    esac
fi

if [ -n "$AUTO" ]; then
    # A suppression is an explicit, logged decision — never silent (#134/#466
    # doctrine). Same log path + rotation shape as notify-discord-pending.sh.
    log="$HOME/.claude/notify-delivery.log"
    { mkdir -p "$(dirname "$log")"; } 2>/dev/null || true
    size=$(stat -c %s "$log" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    [ "$size" -gt 512000 ] && mv -f "$log" "$log.1" 2>/dev/null || true
    stamp=$(date -Iseconds 2>/dev/null || echo '?')
    { printf '%s skipped kind=promptclear key=%s reason=auto:%s\n' \
        "$stamp" "$SID" "$AUTO" >>"$log"; } 2>/dev/null || true
    exit 0
fi

rm -f "/tmp/claude-discord-lastq-${SID}" 2>/dev/null || true
# #668: the ✅ idle-ping dedup (LASTOK) follows the SAME rule as the ❓ dedup —
# a real user prompt means the conversation moved on, so a later identical ✅ is
# a fresh completion and must re-ping even if byte-identical.
rm -f "/tmp/claude-discord-lastok-${SID}" 2>/dev/null || true

# Presence marker: a REAL user prompt means the user is AT the terminal right
# now. stop-check-question-quality.sh reads its mtime and skips the phone-shape
# template enforcement while it is fresh (<10 min) — gating a live dialog
# re-printed questions + hook errors into the user's chat (the camera-box
# "Hruza", 2026-07-05). Automated submissions (task-notifications, machine
# nudges — the #712 classifier above) never reach this line, so an away
# session never looks "present".
touch "/tmp/claude-user-active-${SID}" 2>/dev/null || true

exit 0
