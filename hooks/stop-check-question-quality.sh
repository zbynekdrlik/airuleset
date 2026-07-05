#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — HARD gate on the SHAPE of every ❓ question turn.
#
# The ❓ device ping delivers the final contiguous question block to the
# user's phone (notify-discord-pending.sh), and a Discord REPLY to it is typed
# back into this session (watchdog job 7). Two live failures this gate kills
# (user, 2026-07-05, after the block-delivery fix):
#   1. NO ÚVOD — a question block with no briefing ("Po zmazaní hneď overím…"
#      — deleting WHAT? which project? why?). The reader is on a phone with
#      ZERO terminal context; user-questions-slovak.md mandates the briefing,
#      sessions kept skipping it → enforce the template line.
#   2. MULTI-QUESTION PILE — one ping carrying several decisions ("Odpovedz
#      na ktorékoľvek z 3 … (1) … (2) … (3) …"). Unanswerable over the
#      Discord-reply routing: the reply lands in the session as ONE prompt and
#      nobody knows which sub-question it answers. ONE ping = ONE decision;
#      ask the NEXT question after the first answer arrives.
#
# Required shape of the delivered block (user-questions-slovak.md):
#   **Otázka — projekt <meno> (<čo projekt robí>):** <čo sa deje — 2–4 vety>
#   • <možnosť A> (odporúčam) — <dôsledok>
#   • <možnosť B> — <dôsledok>
#   ❓ NEEDS YOU: <jedno jasné rozhodnutie>
#
# The gate inspects EXACTLY what the pending hook will deliver (same block
# extraction: contiguous paragraph ending at the marker; a bare short marker
# pulls in the one paragraph above). HARD-blocks via {"decision":"block"}
# with a per-session retry cap so it can never loop forever.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-question-quality-block-${SID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)
ASKED_RX='❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:'

# Which line is the ❓ marker? Mirrors notify-discord-pending.sh precedence:
# an ❓ ASKED body line first, else a ❓ starting the last non-blank line.
N=""
if printf '%s\n' "$MSG" | grep -qiE "$ASKED_RX"; then
    N=$(printf '%s\n' "$MSG" | grep -inE "$ASKED_RX" | tail -1 | cut -d: -f1)
elif printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*❓'; then
    N=$(printf '%s\n' "$MSG" | grep -nvE '^[[:space:]]*$' | tail -1 | cut -d: -f1)
fi
if [ -z "$N" ]; then
    rm -f "$RETRY_FILE" 2>/dev/null || true
    exit 0                       # not a question turn — nothing to gate
fi

# The block the device ping will carry — SAME extraction as the pending hook
# (contiguous paragraph ending at the marker; a bare marker under 200 chars
# pulls in the one paragraph directly above, minus headings/rules).
BLOCK=$(printf '%s\n' "$MSG" | awk -v m="$N" '
    function cplen(s,  t) { t = s; return length(s) - gsub(/[\200-\277]/, "", t) }
    NR <= m { L[NR] = $0 }
    END {
        if (m < 1 || !(m in L)) exit
        s = m
        while (s > 1 && L[s-1] !~ /^[[:space:]]*$/) s--
        blk = ""
        for (i = s; i <= m; i++) blk = blk (i > s ? "\n" : "") L[i]
        if (cplen(blk) < 200) {
            p = s - 1
            while (p >= 1 && L[p] ~ /^[[:space:]]*$/) p--
            if (p >= 1) {
                q = p
                while (q > 1 && L[q-1] !~ /^[[:space:]]*$/) q--
                pre = ""
                for (i = q; i <= p; i++) {
                    if (L[i] ~ /^[[:space:]]*(#|---)/) continue
                    pre = pre (pre != "" ? "\n" : "") L[i]
                }
                if (pre != "") blk = pre "\n" blk
            }
        }
        print blk
    }')

VIOLATION=""

# Check 1 — the briefing line. The block must open the question with
# '**Otázka — projekt <meno> (<čo to je>):** …' so a phone reader with zero
# terminal context understands WHAT project and WHAT is going on.
if ! printf '%s' "$BLOCK" | grep -qiE '^[[:space:]]*\**[[:space:]]*Ot[áa]zka[[:space:]]*[—–-][[:space:]]*projekt'; then
    VIOLATION="briefing"
fi

# Check 2 — one ping = one decision. An enumerated (1)/(2) list WITH multiple
# question marks, or "ktorékoľvek z N", is a multi-question pile. (1)/(2)
# STEP descriptions with a single final '?' stay allowed.
if [ -z "$VIOLATION" ]; then
    QMARKS=$(printf '%s' "$BLOCK" | tr -cd '?' | wc -c)
    if printf '%s' "$BLOCK" | grep -qiE 'ktor[éú]ko[ľl]vek[[:space:]]+z'; then
        VIOLATION="pile"
    elif printf '%s' "$BLOCK" | grep -q '(1)' \
            && printf '%s' "$BLOCK" | grep -q '(2)' \
            && [ "$QMARKS" -ge 2 ]; then
        VIOLATION="pile"
    fi
fi

# Check 3 — the briefing must be SHORT: 2–4 plain sentences, ~400 chars max.
# Live failure (camera-box, 2026-07-05): ~700 chars of thread/lock jargon as
# the intro — a wall of text, not "štruktúrované a ľahko čitateľné". The
# briefing = the block's lines BEFORE the first option bullet / the marker.
if [ -z "$VIOLATION" ]; then
    BRIEF=$(printf '%s\n' "$BLOCK" | awk '
        /^[[:space:]]*[•-][[:space:]]/ { exit }
        /^[[:space:]]*[*_>~-]*[[:space:]]*❓/ { exit }
        { print }')
    BRIEF_LEN=$(printf '%s' "$BRIEF" | jq -Rrs 'rtrimstr("\n") | length')
    if [ "${BRIEF_LEN:-0}" -gt 400 ]; then
        VIOLATION="briefwall"
    fi
fi

# Check 4 — options must be BULLET lines ("ziadne odrazky" complaint): the
# block needs at least one `• `/`- ` option line. Even an open question
# offers candidate answers plus "• iné — napíš vlastnú odpoveď".
if [ -z "$VIOLATION" ]; then
    if ! printf '%s\n' "$BLOCK" | grep -qE '^[[:space:]]*[•-][[:space:]]'; then
        VIOLATION="options"
    fi
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    TEMPLATE="\n\nRequired shape of the question block (directly above/ending with the marker, NO blank lines inside — this exact block is what reaches the phone):\n  **Otázka — projekt <meno> (<čo projekt robí>):** <čo sa deje a prečo sa pýtaš — 2–4 vety, po slovensky, bez žargónu>\n  • <možnosť A> (odporúčam) — <dôsledok>\n  • <možnosť B> — <dôsledok>\n  ❓ NEEDS YOU: <jedno jasné rozhodnutie>\nSee user-questions-slovak.md."
    case "$VIOLATION" in
        briefing)
            REASON="Your ❓ question block has NO briefing — the phone reader has ZERO terminal context and cannot tell which project this is or what is going on (the live failure: 'Po zmazaní hneď overím…' — deleting WHAT?). Open the question block with the '**Otázka — projekt <meno> (<čo to je>):**' line followed by 2–4 plain-Slovak sentences of context, then the options, then the ❓ marker line.${TEMPLATE}" ;;
        pile)
            REASON="Your ❓ ping crams MULTIPLE decisions into one question ((1)/(2)/(3) or 'ktorékoľvek z N'). ONE ping = ONE decision: the Discord reply is typed back into this session as ONE prompt, so a multi-question ping is unanswerable — nobody knows which sub-question the reply answers. Ask ONLY the first question now (structured, with its own briefing); ask the next one AFTER the first answer arrives (the user prefers small sequential questions).${TEMPLATE}" ;;
        briefwall)
            REASON="Your ❓ briefing is a WALL OF TEXT (over 400 chars before the options). Štruktúrované a ľahko čitateľné = úvod 2–4 KRÁTKE vety bez žargónu (~400 znakov max) — WHAT project, WHAT happened, WHY you ask. Technical detail (measurements, architecture, code findings) belongs in the ticket/transcript, NOT in the phone ping. Then bullet options, then ONE decision.${TEMPLATE}" ;;
        options)
            REASON="Your ❓ question has NO option bullets (odrážky) — the phone reader needs concrete choices, not prose. Add '• <možnosť> (odporúčam) — <dôsledok>' lines; even an open question offers candidate answers plus '• iné — napíš vlastnú odpoveď'.${TEMPLATE}" ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE" 2>/dev/null || true
exit 0
