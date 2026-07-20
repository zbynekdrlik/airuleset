#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — device notification on ❓ NEEDS YOU / ❓ ASKED (immediate) / ✅ DONE (idle).
#
# Mobile-app notification model — the device is pinged ONLY when Claude genuinely
# ASKS the user (❓ NEEDS YOU) or FULLY completed work (✅ DONE); never on
# ⏳ WORKING, never on routine progress. Split delivery by urgency:
#   - ❓ NEEDS YOU (blocked, last line) OR ❓ ASKED (raised while continuing other
#     answer-independent work; turn ends ⏳ WORKING) → SENT IMMEDIATELY from here. A
#     genuine question must reach the phone even over tmux/SSH, where Claude Code's
#     `idle_prompt` event is unreliable, and is NEVER suppressed — the old "❓ +
#     continuing language → swallow the ping" logic was the reported bug (the user
#     asked, no ping came, then got reproached hours later). One ping per DISTINCT
#     question though: an IDENTICAL repeat with no user input in between (a
#     /goal-loop re-poke of a still-blocked session) is deduped via LASTQ — see
#     send_q() and clear-question-dedup.sh (UserPromptSubmit).
#   - ✅ DONE → recorded to a per-session pending file; notify-discord.sh delivers
#     it ONLY when the user is genuinely idle/away (a finished turn is less urgent,
#     and pinging every completed turn while the user watches the terminal = spam).
#
# This hook runs on EVERY turn (it has last_assistant_message). ⏳ / no-marker
# CLEARS any stale pending so nothing fires.
#
# Marker detection scans the WHOLE message (not just the last line): a completion
# report puts `## ✅ Work Complete` at the TOP and ends with a PR/URL or a
# `❓ Question:` line, so last-line-only detection would miss the most important
# "done" event. Precedence: an ACTIVE question (❓ on the last non-blank line) wins
# over a ✅ heading elsewhere (a report can have both — the trailing ❓ means it is
# waiting on the user).
#
# Silent + non-blocking: writes NOTHING to stdout and always exit 0, so it never
# interferes with the Stop decision pipeline (the other stop-check-*.sh gates).

INPUT=$(cat)

MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
# Defang the session id so it can never escape the /tmp prefix (CC ids are uuids;
# this is belt-and-suspenders against a crafted payload).
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"
PENDING="/tmp/claude-discord-pending-${SID}"
# Last-pinged ❓ content for this session — the dedup state. Cleared by
# clear-question-dedup.sh (UserPromptSubmit) whenever the user actually types.
LASTQ="/tmp/claude-discord-lastq-${SID}"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"

LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)

# Strip markdown emphasis + a leading marker label so the phone line is clean
# Slovak prose (e.g. "❓ **Question:** approve merge?" -> "approve merge?").
strip_md() {
    printf '%s' "$1" \
        | sed -E 's/\*\*//g' \
        | sed -E 's/^[[:space:]]*(NEEDS[[:space:]]+YOU|Question|DONE)[[:space:]]*:?[[:space:]]*//I' \
        | sed -E 's/^[[:space:]]+//'
}

emit() {
    # $1 = emoji, $2 = raw content; clean + truncate to keep the device line
    # short. ✅ stays ONE short line (only the ❓ question carries a full block);
    # jq slices by CODEPOINTS so multi-byte Slovak never gets chopped mid-char.
    local c
    c=$(strip_md "$2" | jq -Rrs 'rtrimstr("\n") | .[0:250]')
    printf '%s %s' "$1" "$c" > "$PENDING"
}

extract_block() {
    # $1 = 1-based line number of the ❓ marker line within $MSG. Prints the
    # question BLOCK the device ping carries: the contiguous non-blank paragraph
    # ENDING at the marker line (briefing + options + decision, per
    # user-questions-slovak.md). When that alone is short (<200 chars — a bare
    # marker), the paragraph directly above is prepended as context, minus
    # markdown headings / horizontal rules (report chrome, not question text).
    # This is the fix for the live truncation/context-free complaint
    # (codex-bridge 2026-07-04): the phone must get the WHOLE question, with its
    # úvod, never a 250-char fragment ("…sklad zač").
    printf '%s\n' "$MSG" | LC_ALL=C awk -v m="$1" '
        # Codepoint length, portable across mawk (bytes) and gawk (chars):
        # UTF-8 continuation bytes are 0x80-0xBF, so bytes minus continuations
        # = characters. mawk length() counts BYTES — gating the context-pull
        # on it misjudged a short diacritic-heavy Slovak marker as "long" and
        # silently dropped its briefing (review finding, 2026-07-04).
        function cplen(s,  t) { t = s; return length(s) - gsub(/[\200-\277]/, "", t) }
        NR <= m { L[NR] = $0 }
        END {
            if (m < 1 || !(m in L)) exit
            # HEAD-ANCHORED extraction first (2026-07-18): a STRUCTURED question
            # — briefing / options / decision as SEPARATE paragraphs (terminal-
            # readable; the odoo-erp #1173 "je to necitatelne" wall complaint) —
            # is bounded by its "**Otazka —" head line above the marker. When
            # the head exists within 40 lines, the block = head..marker VERBATIM
            # (blank lines kept, report chrome dropped) with NO 600cp pull gate,
            # so a long options paragraph can never drop the briefing again.
            h = 0
            for (i = m; i >= 1 && i > m - 40; i--)
                if (L[i] ~ /Ot(\303\241|a)zka[[:space:]]*(\342\200\224|\342\200\223|-)/) { h = i; break }
            if (h) {
                blk = ""
                for (i = h; i <= m; i++) {
                    if (L[i] ~ /^[[:space:]]*(#|---)/) continue
                    blk = blk (i > h ? "\n" : "") L[i]
                }
                print blk
                exit
            }
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
        }'
}

clean_q() {
    # Prepare a question payload for the device — the phone must see
    # STRUCTURE, never a text wall ("ziadne odrazky, ziadne zvyraznenia",
    # camera-box screenshot 2026-07-05):
    #   - markdown BOLD is PRESERVED (Discord renders it; the old blanket
    #     `s/\*\*//g` flattened the question into unformatted prose)
    #   - the marker label (NEEDS YOU / ASKED / Question, bold or not) is
    #     reduced to a bare ❓ on its line
    #   - `• `/`- ` option lines become NUMBERED `1.`/`2.` list items (Discord
    #     ordered list) + a small reply hint below the decision — the user
    #     answers with just the number; a reply "áno" to a two-option question
    #     was ambiguous (user, 2026-07-05). Already-numbered options are kept.
    #   - the `Otázka — projekt …:` briefing head is auto-bolded when the
    #     session forgot the **
    #   - a blank line goes before the first option and before the final ❓
    #     decision line, and the decision text is bolded
    #   - CODEPOINT-safe cap (jq slices by codepoints — `cut -c` counts bytes
    #     and chopped multi-byte Slovak mid-character): ≤1800 chars pass
    #     WHOLE; an oversize block keeps its head and re-appends the tail of
    #     the final DECISION line (truncation must never cut the question off)
    printf '%s' "$1" \
        | sed -E 's/^([[:space:]]*[*_>~-]*[[:space:]]*)❓[[:space:]]*\**(NEEDS[[:space:]]+YOU|ASKED|Question)\**[[:space:]]*:?\**[[:space:]]*/❓ /I' \
        | sed -E 's/^[[:space:]]*•[[:space:]]*/- /' \
        | sed -E '1s/^(Ot[áa]zka[[:space:]]*[—–-][^:*]*:)/**\1**/' \
        | awk '
            { L[NR] = $0 }
            END {
                optspaced = 0; opt = 0
                for (i = 1; i <= NR; i++) {
                    l = L[i]
                    isopt = (l ~ /^- /) || (l ~ /^[0-9]+[.)] /)
                    if (isopt && !optspaced && i > 1) { print ""; optspaced = 1 }
                    if (l ~ /^- /) { opt++; sub(/^- /, "", l); l = opt ". " l }
                    else if (isopt) { opt++ }
                    if (i == NR && l ~ /^❓ /) {
                        if (i > 1) print ""
                        if (l !~ /\*\*/) { sub(/^❓ /, "", l); l = "❓ **" l "**" }
                    }
                    print l
                }
                if (opt > 0) {
                    print ""
                    print "-# Odpovedz reply-om — stačí číslo možnosti (1/" opt ")."
                }
            }' \
        | jq -Rrs 'rtrimstr("\n")
                   | if length <= 1800 then .
                     else (split("\n")) as $ls
                          | (($ls | map(select(startswith("❓"))) | last)
                             // ($ls | last)) as $d
                          | .[0:1500] + "\n… " + ($d | .[-280:])
                     end'
}

send_q() {
    # $1 = raw ❓ marker-LINE content (the dedup KEY), $2 = the full question
    # BLOCK payload (from extract_block; falls back to the key when empty).
    # Cleans both, DEDUPs against the last-pinged question, delivers IMMEDIATELY
    # via the shared send path (no pending file, no waiting for an idle_prompt
    # that may never arrive over tmux/SSH).
    #
    # DEDUP — one ping per DISTINCT question, not per turn, KEYED ON THE MARKER
    # LINE (a /goal-loop re-poke repeats the ❓ line verbatim while the
    # surrounding prose differs — the block may change, the question didn't).
    # A re-poke of a session STILL blocked on the SAME unanswered question
    # re-emits the SAME ❓ line every turn; without this guard every one of them
    # re-pinged the phone (the 9× "rovnaká otázka ako predtým" restreamer spam,
    # 2026-07-04). The FIRST ask ALWAYS pings; only a repeat with an IDENTICAL
    # marker line and NO user input in between is suppressed. Any real user
    # prompt clears LASTQ (clear-question-dedup.sh, UserPromptSubmit), so a
    # fresh ask after the user spoke pings again even if byte-identical. A
    # DIFFERENT question always pings. This is NOT the removed "❓ + continuing
    # language → swallow" bug: no new question is ever suppressed — only the
    # already-pinged one, repeated verbatim to a user who already has it.
    local key payload send f now m
    key=$(strip_md "$1" | jq -Rrs 'rtrimstr("\n") | .[0:1500]')
    payload=$(clean_q "$2")
    [ -z "$payload" ] && payload="$key"
    [ -z "$key" ] && key="$payload"
    [ -z "$key" ] && return 0
    if [ -f "$LASTQ" ] && [ "$(cat "$LASTQ" 2>/dev/null)" = "$key" ]; then
        return 0
    fi

    # A Stop attempt a blocking gate just REJECTED must NOT ping — the session
    # is rewriting the message and the accepted rewrite delivers the final
    # question. Every airuleset stop gate writes /tmp/airuleset-*-block-<sid>
    # BEFORE emitting its block decision; Stop hooks run in PARALLEL, so
    # settle briefly, then treat a freshly-touched block file as "this attempt
    # was rejected". Without this, every rejected draft pinged the phone —
    # camera-box got 3 pings in 3 minutes for ONE reworded question
    # (05:05 blocked draft, 05:07 blocked rewrite, 05:08 final; 2026-07-05).
    # The suppressed draft writes NO LASTQ, so the final version still pings.
    sleep "${ND_BLOCK_SETTLE:-3}"
    now=$(date +%s)
    for f in /tmp/airuleset-*-block-"${SID}"; do
        [ -e "$f" ] || continue
        m=$(stat -c %Y "$f" 2>/dev/null || echo 0)
        if [ $((now - m)) -lt 12 ]; then
            return 0
        fi
    done

    send="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/notify-discord-send.sh"
    # REWORD of a still-unanswered question (LASTQ exists = a ❓ was already
    # pinged and the user has NOT typed since) → EDIT the existing Discord
    # message in place. Edits do not push-ping: the phone got its push on the
    # FIRST ask; the card text just converges to the newest wording (a /goal
    # re-poke reword, a gate-retry rewrite). A genuinely NEW ask (the user
    # typed in between → clear-question-dedup.sh removed LASTQ) posts fresh.
    if [ -f "$LASTQ" ]; then
        if [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ]; then
            { printf '[edit]\n'; printf '%s\n' "$payload"; } \
                >> "${ND_DRYRUN_FILE:-/dev/null}"
            printf '%s' "$key" > "$LASTQ"
            return 0
        fi
        if printf '%s' "$payload" | python3 "$AIRULESET_PY" \
                notify --edit-question --session "$SID" >/dev/null 2>&1; then
            printf '%s' "$key" > "$LASTQ"
            return 0
        fi
        # nothing recent/editable (expired, deleted) → fall through to a POST
    fi
    # ND_CONFIRM: the send runs FOREGROUND and exits 0 only on confirmed HTTP 2xx
    # delivery. LASTQ is recorded ONLY then — a transient Discord failure on the
    # FIRST ask must leave the question retryable by the next identical re-emit,
    # never be silently marked as pinged (review finding, 2026-07-04; the /goal
    # re-poke's re-emit is the natural retry, and job-2 has no backstop for a
    # text-marker ❓).
    # ND_SESSION_ID lets the send path record this ❓ ping's Discord message id →
    # THIS session, so a Discord REPLY routes the answer back here (watchdog job 7).
    # ND_BLOCK=1: the payload is a structured markdown block — the send path
    # must NOT '> '-blockquote it (a quote renders the question as one gray
    # wall); it posts header + blank line + the block as-is.
    if ND_EMOJI="❓" ND_TEXT="$payload" ND_CWD="$CWD" ND_CONFIRM=1 ND_BLOCK=1 \
            ND_SESSION_ID="$SID" bash "$send"; then
        printf '%s' "$key" > "$LASTQ"
    fi
}

# A genuine question to the user ALWAYS fires the device ping — NO suppression,
# ever. Two honest forms (message-status-marker.md):
#   ❓ ASKED: <q>      — a body line; the turn ENDS ⏳ WORKING because you keep
#     doing OTHER answer-independent work. The question is pinged NOW and tracked
#     durably on its ticket; you resume that ticket whenever the user answers.
#   ❓ NEEDS YOU: <q>  — the LAST line; you are BLOCKED (no other useful work) and
#     STOP. Pinged NOW.
# Either way the phone is pinged. The removed "❓ + continuing language → swallow
# the ping" logic was the exact bug the user reported: a mid-loop question that
# never reached the phone, then a reproach hours later. Continuing is fine; the
# ping is not optional. (An ❓ ASKED line takes precedence over the terminal ⏳:
# a question you raise this turn must ping even though the turn keeps working.)
# EXCEPTION — the /goal ARM question is a MACHINE question, never a phone ping
# (gk incident 2026-07-20): the api-watchdog auto-arm types the printed /goal
# itself within a minute, so pinging the user is pure noise — and a Discord
# reply cannot arm anything anyway (only external keystrokes type /goal). Only
# the exact arm shape is skipped: a ❓ line asking to paste a /goal.
# NB: 'ž' via ALTERNATION, never a bracket class — grep splits a multibyte
# char inside [] (the same class of bug as the LC_ALL=C awk octal gotcha).
if printf '%s\n' "$MSG" | grep -qiE '❓.*(vlož|vloz|pastni|paste).*/goal'; then
    rm -f "$PENDING" 2>/dev/null || true
    echo "arm-question — skipped (watchdog auto-arm handles it)" >&2
    exit 0
fi

ASKED_LINE=$(printf '%s\n' "$MSG" | grep -iE '❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:' | tail -1 || true)

if [ -n "$ASKED_LINE" ]; then
    # ask-and-continue: ping the freshly-raised question NOW; the turn keeps
    # working (⏳). No pending left → idle hook won't re-send. The payload is the
    # question BLOCK ending at the ASKED line (its explanation paragraph rides
    # along when the marker is bare) — never the ⏳ continuation below it.
    C=$(printf '%s' "$ASKED_LINE" | sed -E 's/.*❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:[[:space:]]*//I')
    N=$(printf '%s\n' "$MSG" | grep -inE '❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:' | tail -1 | cut -d: -f1)
    rm -f "$PENDING" 2>/dev/null || true
    send_q "$C" "$(extract_block "${N:-0}")"
elif printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*❓'; then
    # ❓ NEEDS YOU on the last line, genuinely blocked on the user → fire the device
    # ping IMMEDIATELY (the question must reach the phone even over SSH, where the
    # idle_prompt event is unreliable). No pending left → idle hook won't re-send.
    # The marker must START the line (markdown prefixes allowed) — a ❓ character
    # MID-SENTENCE is prose, not a marker: a `✅ DONE: … Discord ❓ ping …` line was
    # mis-pinged as "otázka" with garbled content (live incident, 2026-07-04).
    # Payload = the whole question block ending at the marker (extract_block).
    C=$(printf '%s' "$LAST_LINE" | sed -E 's/.*❓[[:space:]]*//')
    N=$(printf '%s\n' "$MSG" | grep -nvE '^[[:space:]]*$' | tail -1 | cut -d: -f1)
    rm -f "$PENDING" 2>/dev/null || true
    send_q "$C" "$(extract_block "${N:-0}")"
elif printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*⏳'; then
    # ⏳ WORKING is the last line → still going (even if a "✅ DONE:" appears
    # earlier in the turn, e.g. autopilot "merged #5 … now ⏳ working #6"). Clear
    # any stale pending so nothing fires while Claude keeps working. Same
    # line-START anchoring as the ❓ branch — a ⏳ mid-sentence is prose.
    rm -f "$PENDING" 2>/dev/null || true
elif printf '%s' "$MSG" | grep -qiE '✅[[:space:]]*DONE:|#+[[:space:]]*✅[[:space:]]*work complete|✅[[:space:]]*work complete'; then
    # Fully-done state. Prefer an explicit "✅ DONE: <outcome>" line; else the
    # report's "What changed" / "Goal" one-liner; else a generic Slovak fallback.
    DLINE=$(printf '%s\n' "$MSG" | grep -iE '✅[[:space:]]*DONE:' | tail -1 || true)
    if [ -n "$DLINE" ]; then
        C=$(printf '%s' "$DLINE" | sed -E 's/.*✅[[:space:]]*DONE:[[:space:]]*//I')
    else
        C=$(printf '%s\n' "$MSG" | grep -iE '^\*\*(What changed|Goal)\b' | head -1 \
            | sed -E 's/^\*\*(What changed|Goal):?\*\*:?[[:space:]]*//I' || true)
        [ -z "$C" ] && C="práca dokončená"
    fi
    emit "✅" "$C"
else
    # No marker → nothing to notify. Clear any stale pending.
    rm -f "$PENDING" 2>/dev/null || true
fi

exit 0
