#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — HARD gate on the SHAPE of every ❓ question turn.
#
# The ❓ device ping delivers the final contiguous question block to the
# user's phone (notify-discord-pending.sh), and a Discord REPLY to it is typed
# back into this session (watchdog job 7). Four live failures this gate kills
# (user, 2026-07-05 + 2026-07-25 + 2026-08-24, after the block-delivery fix):
#   1. NO ÚVOD — a question block with no briefing ("Po zmazaní hneď overím…"
#      — deleting WHAT? which project? why?). The reader is on a phone with
#      ZERO terminal context; user-questions-slovak.md mandates the briefing,
#      sessions kept skipping it → enforce the template line.
#   2. MULTI-QUESTION PILE — one ping carrying several decisions ("Odpovedz
#      na ktorékoľvek z 3 … (1) … (2) … (3) …"). Unanswerable over the
#      Discord-reply routing: the reply lands in the session as ONE prompt and
#      nobody knows which sub-question it answers. ONE ping = ONE decision;
#      ask the NEXT question after the first answer arrives.
#   3. HISTORY ALLUSION (#45) — a still-unanswered question, after an
#      intervening conversation, referenced by allusion ("pýtal som sa skôr",
#      "jediné otvorené rozhodnutie je X") instead of restated in full. This is
#      DIFFERENT from the VERBATIM-repeat re-poke bypass below (no user input
#      since the last ask) — anything reaching Check 5 already failed that
#      bypass, so it is either a genuinely new ask or a lazy reference to an
#      old one; user-questions-slovak.md mandates the full block either way.
#   4. CLIENT-POSTING WITHOUT A NAMED THREAD (#650) — a ❓ approval ping to
#      SEND/APPROVE a client Discuss message (incl. a reply into an EXISTING
#      thread, and a closing/handover message) that names its target only by a
#      GENERIC description ("výrobné vlákno"), never the exact quoted thread
#      name. The owner reads the ping on their phone and cannot tell WHICH
#      thread it goes to (montalu1 2026-08-24: "napriek pokynu … to tu znova
#      nie je!!!"). The prose rule (skills/odoo-discuss-xmlrpc/handover-
#      compose.md #632, of the #596/#609/#628 tool-call-gate family) failed
#      AGAIN; Check 6 escalates it to the CHAT surface — the block validated
#      here IS the phone ping, so the name is checked exactly where it must
#      appear. Only fires on a client-posting INTENT (a send/reply verb
#      adjacent to a Discuss/thread token, or a closing/handover phrase) with
#      NO thread name — an ordinary question never trips it.
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
#
# #292 — every `grep -q`/`grep -m1` decision below reads from a HERE-STRING
# (`<<<"$var"`), never from a `printf … | grep -q` PIPE. `grep -q` exits at
# its FIRST match without draining the rest of stdin; if it is fed by a pipe,
# the still-writing `printf` can take SIGPIPE, and `set -o pipefail` then
# reports the WRITER's 141 as the whole pipeline's exit status — flipping a
# genuine match into an apparent "no match" (the exact `stop-check-prose-
# violations.sh` mechanism #190/#194 already fixed once, reproduced live
# here: `test_verbatim_repeat_of_the_same_blocked_question_still_passes`
# flaking under full-suite load, 1-in-500-to-8000 CPU-saturated runs, rc=141
# captured directly). A here-string has no live WRITER PROCESS the reader
# can SIGPIPE — bash materializes it as a temp file on older builds, or an
# already-fully-written anonymous pipe under capacity on bash 5.1+; either
# way the whole document exists before the reader's first read, so the race
# cannot exist structurally, regardless of which check fires early. The
# ONE exception is the BLOCK-extraction `awk` below (the paragraph-pull that
# builds `$BLOCK` itself): its only `exit` calls live inside `END { … }`,
# which by definition never runs until awk has already consumed all of
# stdin — genuinely safe, left as a pipe on purpose (`tests/
# test_question_gate_pipeline_race.py` locks this distinction explicitly).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"
[ -z "$MSG" ] && exit 0

# _qhash for the delivery-log fingerprint, shared with notify-discord-pending.sh
# via hooks/lib-qhash.sh (#740) — a repeat-asked-question BLOCK below logs the
# SAME qhash the pending hook logs for that question's suppressed ping, so the
# two lines up. Diagnostic-only: every use is guarded with `type _qhash`, so a
# missing lib degrades the log's qhash to empty, never the Stop decision.
_LIB_QHASH="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib-qhash.sh"
[ -r "$_LIB_QHASH" ] && . "$_LIB_QHASH"

# Durable trace of a BLOCKED repeat-asked-question (#740) into the SAME
# notify-delivery.log the pending hook writes — a suppressed ping and a blocked
# repeat for the SAME question then share a qhash and line up. Guarded so a
# read-only $HOME can never turn logging into a failed Stop decision.
_delivery_log_blocked_repeat() {
    local log stamp qh
    qh="${1:-}"
    log="$HOME/.claude/notify-delivery.log"
    mkdir -p "$(dirname "$log")" 2>/dev/null || true
    stamp=$(date -Iseconds 2>/dev/null || echo '?')
    { printf '%s blocked kind=question-quality reason=repeat-asked-question key=%s qhash=%s\n' \
        "$stamp" "$SID" "$qh" >>"$log"; } 2>/dev/null || true
    return 0
}

# The /goal ARM question is a MACHINE question — the api-watchdog auto-arm
# types the printed /goal itself and the Discord ping is suppressed for it, so
# the away-user Slovak template has no audience here. Enforcing it looped the
# Stop hook and killed the gk session (2026-07-20). 'ž' via ALTERNATION, never
# a bracket class (grep splits multibyte chars inside []).
if grep -qiE '❓.*(vlož|vloz|pastni|paste).*/goal' <<<"$MSG"; then
    exit 0
fi

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
if grep -qiE "$ASKED_RX" <<<"$MSG"; then
    N=$(printf '%s\n' "$MSG" | grep -inE "$ASKED_RX" | tail -1 | cut -d: -f1)
    MARKER_RAW=$(printf '%s\n' "$MSG" | grep -iE "$ASKED_RX" | tail -1 \
        | sed -E 's/.*❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:[[:space:]]*//I')
elif grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*❓' <<<"$LAST_LINE"; then
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
            # KEYLINE matches the already-delivered, still-unanswered question.
            # #740 recidíva (miva1 re-emitted ONE question 27x in 8 h): a
            # delivered question is carried by the footer U N + the needs-answer
            # label — it is NOT re-typed into the chat every wake. Split the
            # match by SHAPE:
            #   * BARE BLOCKED re-poke — the ONLY ❓ marker is a trailing
            #     `❓ NEEDS YOU:` line (no `❓ ASKED:`, no `**Otázka — projekt`
            #     block, no `⏳`/`✅` marker): the ONE re-emission still allowed,
            #     because /goal stop-condition (A) needs the marker as the turn's
            #     last line to hold a genuinely BLOCKED loop -> exit 0 (#740).
            #   * ANY OTHER shape (a `❓ ASKED:` line, the full block, or the
            #     matching marker alongside `⏳`/`✅`) -> BLOCK (exit 2): re-emitting
            #     an already-delivered question is pure noise the owner already
            #     has. This runs BEFORE the present-user bypass below, so the
            #     repeat-block applies REGARDLESS of presence (the owner sees the
            #     chat spam in the webterm even when present); only the shape
            #     checks below stay presence-aware.
            IS_BARE_REPOKE=1
            if grep -qiE "$ASKED_RX" <<<"$MSG"; then IS_BARE_REPOKE=0; fi
            if LC_ALL=C.UTF-8 grep -qiE 'Ot[áa]zka[[:space:]]*[—–-][[:space:]]*projekt' <<<"$MSG"; then IS_BARE_REPOKE=0; fi
            if grep -qF '⏳' <<<"$MSG"; then IS_BARE_REPOKE=0; fi
            if grep -qF '✅' <<<"$MSG"; then IS_BARE_REPOKE=0; fi
            if [ "$IS_BARE_REPOKE" = 1 ]; then
                rm -f "$RETRY_FILE" 2>/dev/null || true
                exit 0
            fi
            # Non-bare repeat of an already-delivered question -> BLOCK. Capped by
            # the SAME retry mechanism as the shape checks so a Stop hook can
            # never wedge the session forever (after MAX_RETRIES it lets through).
            if [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
                echo "$((RETRIES+1))" > "$RETRY_FILE"
                QH=""
                if type _qhash >/dev/null 2>&1; then QH=$(_qhash "$KEYLINE"); fi
                _delivery_log_blocked_repeat "$QH"
                printf '%s\n' "Otázka už bola doručená a je nezodpovedaná (qhash ${QH:-?}) — nesie ju footer U N + label needs-answer, takže ju NEOPAKUJ. Zmaž riadok \`❓ ASKED:\` / celý \`**Otázka — projekt …**\` blok a ukonči ťah len \`⏳ WORKING\` / \`✅ DONE\` podľa reálneho stavu ostatnej práce. Ak si NAOZAJ blokovaný (nič iné nie je workable), napíš IBA holý riadok \`❓ NEEDS YOU: <ten istý text>\` a nič iné (#740)." >&2
                exit 2
            fi
            # retry cap reached — let it through so the Stop hook can never loop
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
        # HEAD-ANCHORED extraction first (2026-07-18) — SAME as the pending
        # hook: a structured question (briefing / options / decision as
        # separate paragraphs) is bounded by its "**Otazka —" head line; when
        # present within 40 lines, block = head..marker verbatim (blank lines
        # kept, chrome dropped), no 600cp pull gate — a long options paragraph
        # never drops the briefing, so the gate validates the same block the
        # device ping will actually carry.
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
# #735 — LC_ALL=C.UTF-8 forced (same convention as Check 6 below): this
# bracket class contains multibyte chars ([áa], [—–-]) and, under a
# POSIX/C-locale calling env with no locale forced, grep splits them into
# raw bytes and the class silently stops matching a genuine diacritic head.
# Residual (adversarial review, shared by Check 6): a box with NO C.utf8
# locale installed at all makes grep silently fall back to plain C, which
# reopens this same byte-splitting bug with zero warning — accepted, same
# exposure Check 6 already carries fleet-wide.
if ! LC_ALL=C.UTF-8 grep -qiE '^[[:space:]]*\**[[:space:]]*Ot[áa]zka[[:space:]]*[—–-][[:space:]]*projekt' <<<"$BLOCK"; then
    VIOLATION="briefing"
fi

# Check 2 — one ping = one decision. An enumerated (1)/(2) list WITH multiple
# question marks, or "ktorékoľvek z N", is a multi-question pile. (1)/(2)
# STEP descriptions with a single final '?' stay allowed.
if [ -z "$VIOLATION" ]; then
    QMARKS=$(printf '%s' "$BLOCK" | tr -cd '?' | wc -c)
    # #735 — LC_ALL=C.UTF-8 forced: [éú]/[ľl] are multibyte bracket classes,
    # broken under a POSIX-locale calling env exactly like Check 1's above.
    if LC_ALL=C.UTF-8 grep -qiE 'ktor[éú]ko[ľl]vek[[:space:]]+z' <<<"$BLOCK"; then
        VIOLATION="pile"
    elif grep -q '(1)' <<<"$BLOCK" \
            && grep -q '(2)' <<<"$BLOCK" \
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
    BRIEF=$(awk '
        /^[[:space:]]*((•|-)[[:space:]]|[0-9]+[.)][[:space:]])/ { exit }
        /^[[:space:]]*[*_>~-]*[[:space:]]*❓/ { exit }
        { print }' <<<"$BLOCK")
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
    if ! grep -qE '^[[:space:]]*((•|-)[[:space:]]|[0-9]+[.)][[:space:]])' <<<"$BLOCK"; then
        VIOLATION="options"
    fi
fi

# Check 5 — never reference an old unanswered question by ALLUSION instead of
# restating it (#45). The VERBATIM-repeat bypass above already exits for the
# ONE legitimate case that reuses old wording (a re-poke with no user input
# since the last ask) — so anything still reaching this check is a genuinely
# NEW ask, and a shorthand reference here means the model skipped restating
# the full question after an intervening conversation. Banned Slovak
# referencing phrases (user-questions-slovak.md), all rewordings apply.
if [ -z "$VIOLATION" ]; then
    # #735 — LC_ALL=C.UTF-8 forced: every alternative below carries multibyte
    # bracket classes ([ýy], [ôo], [žz], [šs], [íi], [áa], [čc], [ďd], [ée]) —
    # same broken-under-POSIX-locale bug as Check 1/2 above.
    if LC_ALL=C.UTF-8 grep -qiE \
        '(p[ýy]tal[a]?[[:space:]]+som[[:space:]]+sa[[:space:]]+(sk[ôo]r|u[žz]|vy[šs][šs]ie))'\
'|(ako[[:space:]]+som[[:space:]]+(sa[[:space:]]+)?(u[žz][[:space:]]+)?(p[ýy]tal|spom[íi]nal|p[íi]sal|uviedol))'\
'|(vr[áa][ťt].{0,12}(sa[[:space:]]+)?k[[:space:]].{0,20}ot[áa]zke)'\
'|(st[áa]le[[:space:]]+[čc]ak[áa]m[[:space:]]+na[[:space:]]+odpove[ďd])'\
'|(ot[áa]zka.{0,40}st[áa]le[[:space:]]+plat[íi])'\
'|(jedin[éy][[:space:]]+otvoren[ée][[:space:]]+rozhodnutie[[:space:]]+je)' <<<"$BLOCK"; then
        VIOLATION="reference"
    fi
fi

# Check 6 — a CLIENT-POSTING approval question must NAME the exact target
# thread (#650). Owner incident (montalu1, 2026-08-24): an approval ping to
# SEND a client Discuss message into an EXISTING thread named its target only
# by a generic description ("výrobné vlákno"), not the exact quoted name — the
# prose rule (skills/odoo-discuss-xmlrpc/handover-compose.md #632, in the
# tool-call-gate #596/#609/#628 family) failed AGAIN. This is the CHAT-surface
# escalation: the $BLOCK validated here IS the phone ping, so the name the owner
# reads is checked at the exact place it must appear.
#
# Fires only when the delivered block carries CLIENT-POSTING INTENT — a
# send/reply VERB present together with the DIRECTIONAL "do … vlákn/discuss"
# ("into the thread"), OR a closing/handover-message phrase — AND the block does
# NOT name the thread with a QUOTED name ending in the stream number (the #632
# form). Deliberately narrow: it runs only on ❓ question turns past Checks 1-5,
# and the directional "do …" is the discriminator that keeps the omnipresent
# montalu Discuss-thread MENTIONS ("v/vo vlákne …", a status/design question)
# from tripping the gate — only a genuine posting says "do … vlákna" (reviewer-A
# false-positive finding). LC_ALL=C.UTF-8 per the repo's #319 diacritic-safe
# convention; here-strings not `printf|grep -q` pipes per #292.
#
# Accepted residuals (this is a WORD-FAMILY heuristic, not a parser — a genuine
# occurrence outside these families needs its own follow-up, never a blanket
# rewrite): (1) a client-posting approval that mentions NO thread word at all
# ("chcem to poslať klientovi") — nothing signals Discuss, so it is
# indistinguishable from an ordinary e-mail/other send; the closing-message arm
# still catches an "uzavieracej správy" phrasing (reviewer-A thread-word-
# dependency FN); (2) a thread named only by its INTERNAL channel number
# ("„vlákno 250"", a bare "Vlákno: 288", or the #697-accepted deep URL
# discuss.channel_<N> with no human name next to it) — accepted as a specific
# identifier, though #632 prefers the human name (the create-time gate #596 +
# prose enforce that separately); (3) intent SPLIT ACROSS LINES, or a posting phrased with the
# locative "vo vlákne" rather than "do vlákna"; (4) a `Vlákno:` line placed
# ABOVE the "Otázka —" head, which the head-anchored $BLOCK extraction drops —
# the standard compose ordering puts the name AFTER the briefing, so it is
# in-block. The present-user (~10 min) bypass above also still applies BY
# DESIGN: these approval pings are away-user autonomous asks whose ACTIVE file
# is never stamped, so the bypass does not reach the montalu1 case.
if [ -z "$VIOLATION" ]; then
    # A send / post / reply VERB (kept to genuine posting verbs; the earlier
    # broad list's inform/posun/ozn[áa]m collided with common nouns —
    # informácia / posunúť termín / oznámenie — reviewer-A finding).
    SEND_VERB_RX='po[šs]l|odo[šs]l|posiel|odosiel|zverej|odoslan|zasl|nap[ií][šs]|napis|odpoved|odp[ií][šs]|reaguj'
    # … directed INTO a thread: the DIRECTIONAL preposition "do … vlákn/discuss"
    # (accusative "into the thread") — NOT the LOCATIVE "v/vo vlákne" ("in the
    # thread") that ordinary status/design questions use. This is the clean
    # discriminator (reviewer-A): montalu client comms run through Discuss, so
    # away-user ❓ questions constantly MENTION a thread ("v Discuss vlákne …");
    # only a genuine posting says "do … vlákna". `.` not `[^\n]` (the grep-
    # bracket newline trap: `[^\n]` excludes the letter 'n', not a newline).
    DIR_THREAD_RX='(^|[^[:alpha:]])do[[:space:]]+.{0,25}(vl[áa]kn|discuss)'
    # … OR a closing / handover CLIENT message (which always targets a thread).
    CLOSING_RX='uzavierac.{0,25}spr[áa]v|(odovzd[áa]v|handover).{0,25}spr[áa]v'
    # NAMED = a QUOTED thread name ending in the stream-number digit (the #632
    # canonical „<human name> <N>"). A bare `Vlákno:` LABEL with a generic value
    # („výrobné vlákno") is deliberately NOT accepted — accepting the label
    # alone let the fix instruction's own remedy defeat the check (reviewer-A
    # DODGE-1).
    THREAD_NAMED_RX='[„“"][^„”“"]{0,60}[0-9][[:space:]]*[”“"]'
    # #697 — two MORE satisfying-evidence forms, aligned with the #657 doctrine
    # (owner-facing thread references carry name + deep URL). Both only ADD
    # acceptance (a block can only stop firing, never start), so the false-
    # BLOCK risk is zero and the analyzed direction is under-block:
    #   (a) the machine-exact deep URL discuss.channel_<N> — the #657 canonical
    #       identifier; it cannot appear by accident, and it is MORE specific
    #       than a human name (a block carrying only it was over-blocked);
    #   (b) an explicit `Vlákno:` marker LINE whose value carries a digit (the
    #       stream-number suffix of the #632 „<name> <N>" form) within 60
    #       chars, with no `#` before the digit — so the natural unquoted
    #       `Vlákno: Tabula objednavok 1` passes while DODGE-1 stays dead
    #       (a digit-less generic label still blocks; the digit is the SAME
    #       discriminator THREAD_NAMED_RX already uses) and a ticket-ref
    #       `Vlákno: pozri ticket #650` still blocks (the [^#] exclusion).
    THREAD_DEEPURL_RX='discuss\.channel_[0-9]+'
    #       Reviewer 🔵-1: `\**` also admitted around the colon so the bold
    #       markdown variants (`**Vlákno:**`, `**Vlákno**:`) pass too.
    #       Reviewer 🔵-2/-3 residuals (probed, accepted — same word-family
    #       class as the list above): an INCIDENTAL digit in a generic label
    #       ("Vlákno: výrobné vlákno (odpoviem do 2 dní)") satisfies the digit
    #       discriminator — identical weakness to THREAD_NAMED_RX's „…do 2
    #       dní"; and evidence is PRESENCE-checked, not bound to the TARGET
    #       thread (a different thread's deep URL elsewhere in the block
    #       satisfies) — semantic binding is beyond a word-family heuristic,
    #       and the quoted-name form always shared this property.
    THREAD_VLAKNO_RX='^[[:space:]]*\**[[:space:]]*vl[áa]kno[[:space:]]*\**[[:space:]]*:[^#]{0,60}[0-9]'
    intent=""
    if LC_ALL=C.UTF-8 grep -qiE "$CLOSING_RX" <<<"$BLOCK"; then
        intent=1
    elif LC_ALL=C.UTF-8 grep -qiE "$SEND_VERB_RX" <<<"$BLOCK" \
        && LC_ALL=C.UTF-8 grep -qiE "$DIR_THREAD_RX" <<<"$BLOCK"; then
        intent=1
    fi
    if [ -n "$intent" ] \
        && ! LC_ALL=C.UTF-8 grep -qiE "$THREAD_NAMED_RX" <<<"$BLOCK" \
        && ! LC_ALL=C.UTF-8 grep -qiE "$THREAD_DEEPURL_RX" <<<"$BLOCK" \
        && ! LC_ALL=C.UTF-8 grep -qiE "$THREAD_VLAKNO_RX" <<<"$BLOCK"; then
        VIOLATION="thread"
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
        reference)
            REASON="Your ❓ block references an OLD question by allusion (\"pýtal som sa skôr\" / \"ako som spomínal\" / \"jediné otvorené rozhodnutie je X\") instead of restating it. If a conversation happened since it was last asked, this is a NEW ask — write the FULL self-contained block again (briefing + options + decision); the away user cannot see your history. A byte-identical VERBATIM repeat of the SAME still-blocked question is fine and does not hit this check.${TEMPLATE}" ;;
        thread)
            REASON="Your ❓ block asks to SEND/APPROVE a client Discuss message (or a closing/handover message) but does NOT name the exact target thread — the away owner sees only a generic description on their phone. Name the thread on its OWN line: Vlákno: „<presný názov vlákna vrátane čísla streamu>\" — a per #657 pridaj aj deep URL …/odoo/discuss?active_id=discuss.channel_<N> (samotný deep URL tiež stačí) — aj pri EXISTUJÚCOM vlákne, nie len druhový opis ako „výrobné vlákno\". See skills/odoo-discuss-xmlrpc/handover-compose.md (#632/#650/#697)." ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE" 2>/dev/null || true
exit 0
