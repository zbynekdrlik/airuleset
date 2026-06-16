#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Enforces approval-scope.md "NEVER gate on events / prod-usage / hardware / off-air".
# The user's HARDEST rule, repeatedly violated: Claude frets about needing prod
# machines / off-air windows, pre-classifies issues as "🔴 PROD/HARDWARE", warns
# "you must be present / be at the rig", and recommends autopilot-skip for
# prod/hardware issues — instead of just doing the work and letting the USER stop
# it when prod is live. Fires on EVERY message (the violation happens at autopilot
# backlog-triage time, not only at deploy). Bilingual (English + Slovak).
#
# Blocks via {"decision":"block"} with a per-session retry cap. A generous ESCAPE
# skips the block when the message is META (discussing/prohibiting the rule, e.g.
# editing approval-scope) so rule-work and "I worked the prod issue" reports pass.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-prod-gating-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

# --- ESCAPE: meta / rule-discussion / prohibition, or a plain work report ---
# If the message references the rule, prohibits the behavior, or just reports that
# the prod/hardware work WAS done, it is not a violation. Keep this generous —
# false-blocking a rule-edit or a legit "tested on the rig, green" report is worse
# than missing one borderline case (the rule itself still governs).
ESCAPE=0
if echo "$MSG" | grep -qiE "approval-scope|no-destructive-remote|stop-check-prod-gating|deliver-files-as-urls|user'?s hardest rule|(do ?not|don'?t|never|nikdy|must not|banned|forbid|zakáz|zakaz)[^.]{0,40}(gate|classif|recommend|warn|skip|off.?air|be present|pýta|pyta|event|prod)|never gate|the rule\b|this rule|governance|i (worked|implemented|tested|shipped|fixed|deployed|verified)[^.]{0,60}(on the rig|on prod|end-to-end|all green|live)"; then
    ESCAPE=1
fi

VIOLATION=""

if [ "$ESCAPE" = "0" ]; then
    # 1) recommending a skip/defer for a prod/hardware issue (Claude's initiative)
    if echo "$MSG" | grep -qiE "(recommend|suggest|propose|advis|odporúč|odporuc|navrhuj)[^.]{0,50}(autopilot-skip|skip[^.]{0,12}(#|issue|it|this)|defer|do (it|them|these) (later|guided|together)|be there for)"; then
        VIOLATION="recommend_skip"
    fi
    # 2) off-air window framing (English + Slovak)
    if [ -z "$VIOLATION" ] && echo "$MSG" | grep -qiE "off.?air[^.]{0,30}(window|okná|okna|needed|required|wait|must|musíš|musis|present|hold)|(window|okná|okna|wait|hold|until|after)[^.]{0,20}off.?air|off.?air (window|okná|okna)"; then
        VIOLATION="off_air"
    fi
    # 3) "you must be present / be at the rig" framing (English + Slovak)
    if [ -z "$VIOLATION" ] && echo "$MSG" | grep -qiE "(you|user|musíš|musis)[^.]{0,20}(must|should|need to|have to|byť|byt)[^.]{0,20}(present|there|at the rig|pri tom|by it)|be (present|there)[^.]{0,15}(at|for|during)[^.]{0,15}(rig|prod|stream|show|event|live)|be at the rig|musíš byť pri tom|musis byt pri tom|vedene so mnou[^.]{0,12}(nie )?naslepo|guided[^.]{0,12}not blindly"; then
        VIOLATION="be_present"
    fi
    # 4) asking / waiting on prod being live / off-air / safe
    if [ -z "$VIOLATION" ] && echo "$MSG" | grep -qiE "is it (off.?air|safe|live)( (now|right now))?\?|is prod (live|in use|busy|being used)|are we (live|on air|off.?air)|(should i|do i|shall i|want me to)[^.]{0,30}(wait|hold)[^.]{0,30}(off.?air|event|show|window|stream|prod|live)|(wait|hold)[^.]{0,20}(until|for)[^.]{0,20}(off.?air|the (event|show|window|stream)|prod (is )?free)|confirm[^.]{0,30}(no (live )?event|off.?air|prod (is )?(free|quiet))"; then
        VIOLATION="ask_prod_timing"
    fi
    # 5) "CI can't verify (manual/self-hosted) so you must watch/be present"
    if [ -z "$VIOLATION" ] && echo "$MSG" | grep -qiE "(ci|pipeline)[^.]{0,40}(can'?t|cannot|won'?t|doesn'?t|won't)[^.]{0,30}verif[^.]{0,60}(you|user|must|present|watch|be there|manual)|manual[^.]{0,20}(self.?hosted|gate)[^.]{0,40}(you must|be present|watch|be there)"; then
        VIOLATION="ci_cant_verify_gate"
    fi
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    REASON="You gated / classified / skipped / warned based on prod-usage, events, off-air windows, hardware, or 'you must be present' — the user's HARDEST, most-repeated rule (approval-scope.md → 'NEVER gate on events / prod-usage / hardware / off-air'). Guarding whether prod is LIVE is 100% the USER's job; they stop you in the moment. You must NOT: pre-classify an issue as 🔴 PROD/HARDWARE/off-air/invasive/risky, recommend autopilot-skip for it, warn about off-air windows / 'be present at the rig' / 'CI can't verify so you must watch', or ask 'is it off-air / is prod live / is it safe now'. Work every hardware/prod/streaming/OBS/HDMI/DRM issue end-to-end on the rig like any other issue — implement, test on the rig/prod, restart the app/service/device you're testing, verify, ship. (Only a genuinely-irreversible action — host reboot, data deletion, DB drop — is asked, at the command itself.) Rewrite the message: drop the prod-timing/hardware gating entirely and just state the work (or do it). Same in any language (Slovak: no off-air okná / musíš byť pri tom / odporúčam autopilot-skip / vedene so mnou nie naslepo)."
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE"
exit 0
