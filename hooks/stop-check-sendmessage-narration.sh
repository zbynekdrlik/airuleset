#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Enforces subagent-continuation.md: NEVER narrate that SendMessage is
# unavailable, and never frame a fresh dispatch as a fallback for a failed
# continuation. SendMessage (continue a subagent) is gated behind
# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 and is off by default — so the model
# keeps writing "SendMessage to that worker isn't available here, so I'm
# dispatching a fresh worker" (the user has flagged this repeatedly). Just
# dispatch the fresh worker silently with full context embedded.
#
# Blocks via {"decision":"block"} with a per-session retry cap. ESCAPE skips the
# block when the message is META (discussing/prohibiting the rule — editing the
# module, this hook, or quoting the user's complaint).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-sendmessage-narration-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

# --- ESCAPE: meta / rule-discussion / prohibition ---
# Editing the rule, explaining the gating, or quoting the user's complaint is not
# a violation. Keep generous — false-blocking a rule edit is worse than a miss.
ESCAPE=0
if echo "$MSG" | grep -qiE "subagent-continuation|stop-check-sendmessage|CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS|agent.?teams (flag|feature)|gated behind|(do ?not|don'?t|never|nikdy|must not|banned|forbid|zakáz|zakaz)[^.]{0,40}(narrat|sendmessage|send message|mention)|known (cc|claude.?code) (bug|limitation|issue)|the rule\b|this rule|governance|why (is(n.?t)?|sendmessage)"; then
    ESCAPE=1
fi

VIOLATION=""
if [ "$ESCAPE" = "0" ]; then
    # The narration: SendMessage (un)availability paired with a fallback dispatch,
    # or "send to the (existing|running) worker". Match the intent, not one phrasing.
    if echo "$MSG" | grep -qiE "sendmessage|send message" \
       && echo "$MSG" | grep -qiE "(isn'?t|is not|not|won'?t|can'?t|cannot|unavailable|no such tool)[^.]{0,40}(available|here|work|exist)|so (i'?m|i am|i'?ll|i will)[^.]{0,40}(dispatch|spawn|start|launch)[^.]{0,30}(fresh|new|another)|dispatch[^.]{0,20}(a )?(fresh|new|another)[^.]{0,30}worker"; then
        VIOLATION="sendmessage_narration"
    fi
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    REASON="You narrated that SendMessage is unavailable and framed a fresh subagent dispatch as a fallback (subagent-continuation.md — the user has flagged this repeatedly). SendMessage (continue a spawned subagent) is gated behind CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 and is OFF by default, so it never works — treat EVERY dispatch as one-shot. Rewrite the message: DROP the 'SendMessage isn't available here, so I'm dispatching a fresh worker' narration entirely. Just dispatch the fresh worker with ALL needed context (the finding, the decision, the skill to enforce, the prior state) embedded in its prompt, and have it resume from durable state (git branch / open PR / gh / files / board). Do not mention SendMessage or the continuation fallback at all."
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE"
exit 0
