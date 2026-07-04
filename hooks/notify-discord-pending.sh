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
    printf '%s\n' "$MSG" | awk -v m="$1" '
        # Codepoint length, portable across mawk (bytes) and gawk (chars):
        # UTF-8 continuation bytes are 0x80-0xBF, so bytes minus continuations
        # = characters. mawk length() counts BYTES — gating the context-pull
        # on it misjudged a short diacritic-heavy Slovak marker as "long" and
        # silently dropped its briefing (review finding, 2026-07-04).
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
        }'
}

clean_q() {
    # Clean a question payload for the device: markdown bold stripped, the
    # marker label (NEEDS YOU / ASKED / Question) reduced to a bare ❓ on its
    # line, then a CODEPOINT-safe cap (jq slices by codepoints — `cut -c` counts
    # bytes and chopped multi-byte Slovak mid-character): ≤1800 chars pass
    # WHOLE; an oversize block keeps its head and re-appends the tail of the
    # final DECISION line — truncation must never cut the question itself off
    # (the live bug: intro delivered, question gone).
    printf '%s' "$1" \
        | sed -E 's/\*\*//g' \
        | sed -E 's/^([[:space:]]*[*_>~-]*[[:space:]]*)❓[[:space:]]*(NEEDS[[:space:]]+YOU|ASKED|Question)[[:space:]]*:?[[:space:]]*/❓ /I' \
        | jq -Rrs 'rtrimstr("\n")
                   | if length <= 1800 then .
                     else (split("\n") | last) as $d
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
    local key payload send
    key=$(strip_md "$1" | jq -Rrs 'rtrimstr("\n") | .[0:1500]')
    payload=$(clean_q "$2")
    [ -z "$payload" ] && payload="$key"
    [ -z "$key" ] && key="$payload"
    [ -z "$key" ] && return 0
    if [ -f "$LASTQ" ] && [ "$(cat "$LASTQ" 2>/dev/null)" = "$key" ]; then
        return 0
    fi
    send="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/notify-discord-send.sh"
    # ND_CONFIRM: the send runs FOREGROUND and exits 0 only on confirmed HTTP 2xx
    # delivery. LASTQ is recorded ONLY then — a transient Discord failure on the
    # FIRST ask must leave the question retryable by the next identical re-emit,
    # never be silently marked as pinged (review finding, 2026-07-04; the /goal
    # re-poke's re-emit is the natural retry, and job-2 has no backstop for a
    # text-marker ❓).
    # ND_SESSION_ID lets the send path record this ❓ ping's Discord message id →
    # THIS session, so a Discord REPLY routes the answer back here (watchdog job 7).
    if ND_EMOJI="❓" ND_TEXT="$payload" ND_CWD="$CWD" ND_CONFIRM=1 ND_SESSION_ID="$SID" \
            bash "$send"; then
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
