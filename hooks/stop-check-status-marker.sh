#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Enforces message-status-marker.md: every message must end with exactly ONE
# unambiguous state marker so the user never has to guess whether Claude is
#   - asking them something        -> ❓ NEEDS YOU
#   - working in the background     -> ⏳ WORKING
#   - done and idle                 -> ✅ DONE
# Catches the three misleads the user reported:
#   A. background / "standing by" / "in progress" language with NO ⏳ marker
#   B. a question / "your go" / trailing "?" with NO ❓ marker
#   C. a progress/completion claim (done/fixed/pushed/deployed/merged) with NO marker
# HARD-blocks via {"decision":"block"} with a per-session retry cap.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-status-marker-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

# --- which markers are present? ---
HAS_NEEDS=$(echo "$MSG" | grep -qE "❓" && echo 1 || echo 0)
HAS_WORKING=$(echo "$MSG" | grep -qE "⏳" && echo 1 || echo 0)
HAS_DONE=$(echo "$MSG" | grep -qiE "✅\s*(DONE|complete[d]?|work complete)" && echo 1 || echo 0)

# --- last non-empty character (for trailing-question detection) ---
LAST_CHAR=$(echo "$MSG" | tr -d '[:space:]' | tail -c 1)

VIOLATION=""

# Check A — background / in-progress language not marked ⏳ WORKING.
# A background run means YOU keep going; ⏳ is the honest signal. Claiming ✅ DONE
# or leaving it unmarked while something runs is the exact mislead.
if echo "$MSG" | grep -qiE "\bstanding by\b|\bwaiting (on|for) (the|a|an|ci|build|mutation|result|run|it|deploy|test)|\bin the background\b|\brun(ning|s)? in (the )?background\b|\bbackground (run|task|job|process|monitor|build|deploy)\b|\bbackgrounded\b|\bmonitoring (ci|the (run|build|deploy|job))\b|\bwill report (back|when|once|the)\b|\bi'?ll report\b|\bkicked off\b|\blet it run\b|\bpolling\b|\bstill running\b|\bin progress\b|\bcontinu(e|es|ing) to (monitor|run|poll|watch)\b|\bawaiting (the |a |an )?(ci|build|mutation|result|run|completion|deploy|job)"; then
    if [ "$HAS_WORKING" = "0" ]; then
        VIOLATION="background"
    fi
fi

# Check B — a question / approval-seeking to the user, not marked ❓ NEEDS YOU.
if [ -z "$VIOLATION" ] && [ "$HAS_NEEDS" = "0" ]; then
    if [ "$LAST_CHAR" = "?" ] || echo "$MSG" | grep -qiE "\b(should|shall) (i|we)\b|\bdo you (want|prefer|need)\b|\bwant me to\b|\bwould you like\b|\bok to (proceed|merge|deploy|continue|go|push)\b|\byour (go|call|decision|input|approval)\b|\bawait(ing)? your\b|\bwaiting (for|on) (your|you)\b|\bneeds? your\b|\bmerge it\b|\blet me know\b|\bconfirm (before|if|whether|that)\b|\bgo ahead\?|\bproceed\?|\bno merge without your\b"; then
        VIOLATION="question"
    fi
fi

# Check C — a progress / completion claim with NO marker at all.
if [ -z "$VIOLATION" ] && [ "$HAS_NEEDS" = "0" ] && [ "$HAS_WORKING" = "0" ] && [ "$HAS_DONE" = "0" ]; then
    if echo "$MSG" | grep -qiE "\b(done|complete|completed|finished|fixed|deployed|pushed|committed|merged|implemented|shipped|resolved|all green|ci (is )?green|ready to merge|good to go)\b"; then
        VIOLATION="nomarker"
    fi
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    COMMON="\n\nEnd EVERY message with exactly ONE status marker as the LAST line:\n  ❓ NEEDS YOU: <question/decision> — your turn; you cannot proceed without the user.\n  ⏳ WORKING: <what is running> — nothing needed from the user, you'll report when done.\n  ✅ DONE: <one-line outcome> — finished, idle, awaiting the next instruction.\nSee message-status-marker.md."
    case "$VIOLATION" in
        background)
            REASON="Your message describes a background / in-progress / 'standing by' state but does NOT mark it with ⏳ WORKING. The user cannot tell if you are working, stuck, or waiting on THEM. If a task is running and you'll keep going, end with '⏳ WORKING: <what> — nothing needed from you'. If you are actually idle and the only open item is the user's decision, end with '❓ NEEDS YOU: <decision>'. A background task means ⏳, NEVER ✅ DONE.${COMMON}" ;;
        question)
            REASON="You are asking the user something (a question, 'should I', 'your go', 'merge it?', or a trailing '?') but did NOT mark it with ❓ NEEDS YOU. The user scans the terminal and must instantly see when it's their turn. End the message with '❓ NEEDS YOU: <the question in 1-2 sentences>' as the last line.${COMMON}" ;;
        nomarker)
            REASON="Your message claims progress or completion (done / fixed / pushed / deployed / merged / CI green) but has NO status marker, so the user can't tell if you're finished or still working. End with '✅ DONE: <outcome>' if finished and idle, or '⏳ WORKING: <what's running>' if something is still in progress.${COMMON}" ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE"
exit 0
