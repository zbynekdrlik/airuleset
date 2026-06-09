#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Enforces no-dropped-work.md: any work IDENTIFIED but not completed this session
# MUST be captured as a tracked GitHub issue (gh issue create -> cite #N) before
# stopping. Unlike the completion-report ghost-deferral check (which only fires on
# completion reports), this fires on EVERY message — the user's three loss patterns
# happen mid-session, not in the final report:
#   1. Decomposition-shedding — high-level prompt split, some parts silently dropped.
#   2. Review findings acknowledged but neither fixed nor filed.
#   3. "Pre-existing / known / unrelated / out-of-scope" dismissals during testing.
#   4. Drafting an issue backlog and ASKING permission to create it instead of
#      running gh issue create (filing issues is non-destructive — never ask).
# HARD-blocks via {"decision":"block"} with a per-session retry cap (avoids loops
# when a mention is genuinely meta / unfileable).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-untracked-work-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

# --- Escape: proof the work was FILED as a tracked issue, or is being FIXED now ---
# A bare PR-title "#N" must NOT escape (it doesn't prove the dismissed work was filed).
# Require a filing verb adjacent to a #N, an explicit "issue #N", a gh issue create
# command, or an issues/N URL. Plus a narrow fix-now allowance.
ESCAPE=0
if echo "$MSG" | grep -qiE "(filed|filing|file it|tracked|tracking|tracker|logged|opened|created|recorded)[^.]{0,40}#[0-9]+|gh issue create|/issues/[0-9]+|issue\s+#[0-9]+|todo[: ]+#[0-9]+|tracked (in|as|under)\s+#[0-9]+|address(ed)?\s+(in|by|via)\s+#[0-9]+|(fixing|fix) (it|this|that)?\s*(now|immediately|in this (pr|commit|session))|let me fix|i'?ll fix (it|this|that)( now)?|fixing it now|\b(fixed|resolved|patched|corrected|repaired)\b"; then
    ESCAPE=1
fi

VIOLATION=""

# --- Group 1: dismissal of a discovered problem ---
# pre-existing / known / unrelated / out-of-scope used to justify NOT acting.
if [ "$ESCAPE" = "0" ] && echo "$MSG" | grep -qiE "pre.?existing|already (broken|failing|present|there) before|known (issue|bug|problem|failure|limitation|defect)|not (related|relevant) to (my|this|the) (change|pr|fix|work|task|commit)|unrelated to (my|this|the) (change|pr|fix|work|task|commit|edit)|(this|that|it) (is|was|'s) (a )?(separate|different|pre.?existing) (issue|problem|bug|failure)|\b(separate|different) (issue|problem|bug|concern|failure)\b|out of scope (for|of|here)|outside (the |this )?scope"; then
    VIOLATION="dismissal"
fi

# --- Group 2: leftover sub-work from a decomposed request, dropped without filing ---
if [ -z "$VIOLATION" ] && [ "$ESCAPE" = "0" ] && echo "$MSG" | grep -qiE "remaining (parts|items|tasks|work|pieces|sub.?tasks|features|requests|steps|asks)|the (other|rest of the) (parts|items|tasks|requests|features|asks|pieces)|(other|remaining) (parts|items|pieces) of (your|the) (request|prompt|ask)|the rest (can|will|should|could) (wait|follow|come later|be (done|handled|addressed|tackled|implemented) later)|(handled|did|implemented|finished|completed|covered) (only|just) (part|some|one|a (few|couple)) of (what|your|the)"; then
    VIOLATION="leftover"
fi

# --- Group 3: drafting an issue backlog but ASKING permission to create it ---
# Filing issues is non-destructive tracking — it NEVER needs approval. A drafted
# issue that was never `gh issue create`'d is a dropped issue. Uses a filing-ONLY
# escape (not the fix-inclusive global ESCAPE): a "fixed X" verb must not excuse a
# never-filed backlog.
ISSUE_FILED=0
if echo "$MSG" | grep -qiE "gh issue create|/issues/[0-9]+|\b(filed|created|opened|logged)\b[^.]{0,40}#[0-9]+|filed as #[0-9]+"; then
    ISSUE_FILED=1
fi
if [ -z "$VIOLATION" ] && [ "$ISSUE_FILED" = "0" ] \
    && echo "$MSG" | grep -qiE "\b(create|creating|file|filing|open|opening|add|adding)\b[^.]{0,30}\b(issue|issues|backlog|ticket|tickets)\b|\b(issue|issues|backlog|ticket|tickets)\b[^.]{0,30}\b(create|file|open|add)\b" \
    && echo "$MSG" | grep -qiE "give (me )?the word|say the word|just say|tell me to (hold|go|create|proceed)|or hold\b|hold off|ready to (create|file|open|add|proceed|go)|should i (create|file|open|add|proceed|go ahead)|want me to (create|file|open|add)|shall i (create|file|open|add)|once you (confirm|give|say|approve)|when you (confirm|give|say|approve)|let me know (if|when|whether|to)|approve (the )?(creation|backlog|issues|tickets)|your (go|word|approval|call) (to|before|on)|awaiting your (go|word|approval|confirmation)|no merge without your"; then
    VIOLATION="ask_to_file"
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    case "$VIOLATION" in
        dismissal)
            REASON="You dismissed a discovered problem as 'pre-existing' / 'known issue' / 'unrelated to this change' / 'out of scope' WITHOUT either fixing it now or filing a tracked GitHub issue. Per no-dropped-work.md, a problem you NOTICE for the first time is one you just DISCOVERED — 'pre-existing' describes its age, not its tracking status. A failing test / warning / breakage you spot during testing is a bug you must capture, or it stays unsolved forever and the user rediscovers it every session.\n\nFix NOW (one of):\n  1. Fix it in this session (then say so explicitly), OR\n  2. gh issue create --title '<concise problem>' --body '<exact error/test name/warning, where it happens, why it matters>' — then cite the returned #N (e.g. 'Filed as #N: <title>').\n\n'pre-existing / known / unrelated / out of scope' is allowed ONLY with a filed #N next to it. See no-dropped-work.md (failure mode 3)." ;;
        leftover)
            REASON="Your message indicates leftover sub-work from the user's request ('remaining parts', 'the rest can wait', 'handled only part of...') being dropped WITHOUT a tracked GitHub issue. Per no-dropped-work.md, when you split a high-level prompt into parts, EVERY part you do not complete this session MUST become a GitHub issue BEFORE you stop — otherwise the user has to re-explain work they already asked for (their single biggest complaint).\n\nFix NOW: for each unfinished part, gh issue create --title '<part>' --body '<context from the original request>', then list 'Filed as #N: <title>' for each. There is no 'do it later' fate — only do-now or file-now. See no-dropped-work.md (failure mode 1)." ;;
        ask_to_file)
            REASON="You drafted/proposed GitHub issues but asked the user for PERMISSION to create them ('give the word and I'll create the issues', 'ready to create the backlog?', 'should I file these or hold?', 'once you confirm I'll file them'). Filing issues is NON-DESTRUCTIVE tracking — no code, no production, editable/closeable later — so it NEVER needs approval, and no-dropped-work.md requires identified work to be filed immediately. A drafted issue you never ran 'gh issue create' on is a DROPPED issue; the user then has to say 'yes create them', which is exactly the re-prompting this rule kills.\n\nFix NOW: run 'gh issue create --title ... --body ...' for EACH issue you proposed, then report 'Filed as #N: <title>' for each. Do not ask. If one issue's DESIGN is ambiguous, file it anyway and note the open question on it. See no-dropped-work.md 'Prepared != filed' and ask-before-assuming.md (issue-creation row)." ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

# No violation (or retry budget exhausted) — let Stop succeed; clear counter on clean stop.
[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE"
exit 0
