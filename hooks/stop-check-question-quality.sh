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
# MARKER_RAW = the marker line's content (same extraction as the pending
# hook's dedup key source) — used for the verbatim-repeat bypass below.
N=""
MARKER_RAW=""
if printf '%s\n' "$MSG" | grep -qiE "$ASKED_RX"; then
    N=$(printf '%s\n' "$MSG" | grep -inE "$ASKED_RX" | tail -1 | cut -d: -f1)
    MARKER_RAW=$(printf '%s\n' "$MSG" | grep -iE "$ASKED_RX" | tail -1 \
        | sed -E 's/.*❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:[[:space:]]*//I')
elif printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*❓'; then
    N=$(printf '%s\n' "$MSG" | grep -nvE '^[[:space:]]*$' | tail -1 | cut -d: -f1)
    MARKER_RAW=$(printf '%s' "$LAST_LINE" | sed -E 's/.*❓[[:space:]]*//')
fi
if [ -z "$N" ]; then
    rm -f "$RETRY_FILE" 2>/dev/null || true
    exit 0                       # not a question turn — nothing to gate
fi

# VERBATIM REPEAT of the already-delivered question → PASS without shape
# checks. A /goal re-poke while still blocked replies with EXACTLY the one
# previous ❓ line (message-status-marker.md) — the device path dedups it, and
# re-gating it on shape would force a rewrite = the block→rewrite→ping churn
# this whole pipeline exists to kill (camera-box chat wall, 2026-07-05).
# LASTQ holds the delivered question's dedup key (same derivation as the
# pending hook's strip_md + codepoint cap).
if [ -n "$MARKER_RAW" ]; then
    LASTQF="/tmp/claude-discord-lastq-${SID}"
    if [ -f "$LASTQF" ]; then
        KEYLINE=$(printf '%s' "$MARKER_RAW" \
            | sed -E 's/\*\*//g' \
            | sed -E 's/^[[:space:]]*(NEEDS[[:space:]]+YOU|Question|DONE)[[:space:]]*:?[[:space:]]*//I' \
            | sed -E 's/^[[:space:]]+//' \
            | jq -Rrs 'rtrimstr("\n") | .[0:1500]')
        if [ -n "$KEYLINE" ] && [ "$(cat "$LASTQF" 2>/dev/null)" = "$KEYLINE" ]; then
            rm -f "$RETRY_FILE" 2>/dev/null || true
            exit 0
        fi
    fi
fi

# The block the device ping will carry — SAME extraction as the pending hook
# (contiguous paragraph ending at the marker; a bare marker under 200 chars
# pulls in the one paragraph directly above, minus headings/rules).
BLOCK=$(printf '%s\n' "$MSG" | LC_ALL=C awk -v m="$N" '
    function cplen(s,  t) { t = s; return length(s) - gsub(/[\200-\277]/, "", t) }
    NR <= m { L[NR] = $0 }
    END {
        if (m < 1 || !(m in L)) exit
        s = m
        while (s > 1 && L[s-1] !~ /^[[:space:]]*$/) s--
        blk = ""
        for (i = s; i <= m; i++) blk = blk (i > s ? "\n" : "") L[i]
        # Pull paragraphs ABOVE while the block is short (a bare marker, or
        # marker+options split from their briefing by blank lines) — max 3
        # pulls / 600 cp, stopping once the paragraph carrying the
        # "Otazka —" briefing head is in: the uvod the phone must never
        # lose (david@gk 2026-07-09; the old single-paragraph pull kept the
        # options but dropped the briefing two paragraphs up). NOTE: under
        # LC_ALL=C a bracket class splits multi-byte chars — diacritics and
        # dashes go through ALTERNATIONS, never [aa] classes.
        p = s - 1
        for (pulls = 0; pulls < 3 && cplen(blk) < 600 \
                 && blk !~ /Ot(\303\241|a)zka[[:space:]]*(\342\200\224|\342\200\223|-)/; pulls++) {
            while (p >= 1 && L[p] ~ /^[[:space:]]*$/) p--
            if (p < 1) break
            q = p
            while (q > 1 && L[q-1] !~ /^[[:space:]]*$/) q--
            pre = ""
            for (i = q; i <= p; i++) {
                if (L[i] ~ /^[[:space:]]*(#|---)/) continue
                pre = pre (pre != "" ? "\n" : "") L[i]
            }
            if (pre != "") blk = pre "\n" blk
            if (pre ~ /Ot(\303\241|a)zka[[:space:]]*(\342\200\224|\342\200\223|-)/) break
            p = q - 1
        }
        print blk
    }')

# PRESENT USER → no shape enforcement. The template exists for the AWAY
# user's phone ping (zero context, cold read). When the user typed a REAL
# prompt within the last 10 min they are AT the terminal, mid-conversation —
# hard-gating that dialog re-printed questions + hook errors into their chat
# ("Hruza", camera-box 2026-07-05). clear-question-dedup.sh (UserPromptSubmit)
# stamps the marker; goal re-pokes / hook feedback never do.
ACTIVE="/tmp/claude-user-active-${SID}"
if [ -f "$ACTIVE" ]; then
    AM=$(stat -c %Y "$ACTIVE" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - AM )) -lt 600 ]; then
        rm -f "$RETRY_FILE" 2>/dev/null || true
        exit 0
    fi
fi

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

# Check 3 — the briefing must be SHORT: 2–4 plain sentences, ~600 chars max.
# Live failure (camera-box, 2026-07-05): ~700 chars of thread/lock jargon as
# the intro — a wall of text, not "štruktúrované a ľahko čitateľné". The
# briefing = the block's lines BEFORE the first option bullet / the marker.
# NOTE the option-line regex uses (•|-) ALTERNATION, never a bracket class:
# mawk brackets are BYTE classes, so `[•-]` split the multi-byte `•` and the
# terminator silently never matched — option lines got counted INTO the
# briefing and GOOD ~300-char questions false-positived as walls, looping
# block→rewrite→block live in camera-box (2026-07-05, the user's "velke
# zhorsenie" report).
if [ -z "$VIOLATION" ]; then
    BRIEF=$(printf '%s\n' "$BLOCK" | awk '
        /^[[:space:]]*((•|-)[[:space:]]|[0-9]+[.)][[:space:]])/ { exit }
        /^[[:space:]]*[*_>~-]*[[:space:]]*❓/ { exit }
        { print }')
    BRIEF_LEN=$(printf '%s' "$BRIEF" | jq -Rrs 'rtrimstr("\n") | length')
    if [ "${BRIEF_LEN:-0}" -gt 600 ]; then
        VIOLATION="briefwall"
    fi
fi

# Check 4 — options must be BULLET lines ("ziadne odrazky" complaint): the
# block needs at least one `• `/`- ` option line. Even an open question
# offers candidate answers plus "• iné — napíš vlastnú odpoveď". Same
# alternation-not-bracket rule as Check 3 (locale-independent multibyte `•`).
if [ -z "$VIOLATION" ]; then
    if ! printf '%s\n' "$BLOCK" | grep -qE '^[[:space:]]*((•|-)[[:space:]]|[0-9]+[.)][[:space:]])'; then
        VIOLATION="options"
    fi
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    TEMPLATE="\nShape: **Otázka — projekt <meno> (<čo robí>):** <úvod 2–4 vety> · • <možnosť> (odporúčam) — <dôsledok> · ❓ NEEDS YOU: <jedno rozhodnutie>. See user-questions-slovak.md."
    case "$VIOLATION" in
        briefing)
            REASON="Your ❓ block has no briefing — the away phone reader cannot tell which project or what happened. Open it with the '**Otázka — projekt …:**' line + 2–4 vety kontextu.${TEMPLATE}" ;;
        pile)
            REASON="Your ❓ ping crams MULTIPLE decisions into one question. ONE ping = ONE decision — the Discord reply routes back as ONE prompt, a multi-question ping is unanswerable. Ask only the FIRST question now; the next one after its answer arrives.${TEMPLATE}" ;;
        briefwall)
            REASON="Your ❓ briefing is a wall of text (${BRIEF_LEN:-?} > 600 chars before the options). Úvod = 2–4 KRÁTKE vety; technical detail belongs in the ticket, not the phone ping.${TEMPLATE}" ;;
        options)
            REASON="Your ❓ question has no option bullets (odrážky). Add '• <možnosť> (odporúčam) — <dôsledok>' lines; an open question offers candidates + '• iné — napíš vlastnú odpoveď'.${TEMPLATE}" ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE" 2>/dev/null || true
exit 0
