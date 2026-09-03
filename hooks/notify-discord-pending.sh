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
# #668: the ✅ RECORD-time cwd, carried to the idle DELIVERY. The idle hook
# otherwise re-derives the project from its OWN (possibly empty) event cwd and
# mislabels the ping "unknown"; a sibling file keeps the pending line format
# untouched, and emit() always overwrites it, so a stale value cannot deliver.
PENDING_CWD="/tmp/claude-discord-pending-cwd-${SID}"
# Last-pinged ❓ content for this session — the dedup state. Cleared by
# clear-question-dedup.sh (UserPromptSubmit) whenever the user actually types.
LASTQ="/tmp/claude-discord-lastq-${SID}"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"

# #486 G1 — structured session heartbeat (~/.claude/session-status/<sid>.json),
# the STRUCTURED replacement for render-text liveness/armed/marker detection.
# The producer re-parses $INPUT itself (marker from last_assistant_message,
# armed-goal from the transcript via the canonical scan_goal_markers). Runs at
# the TOP so it fires on EVERY Stop regardless of this hook's own later branches.
# Best-effort + non-blocking: guarded so a heartbeat failure can never affect
# this hook's notify job or the Stop decision pipeline. NO consumer yet (G1).
printf '%s' "$INPUT" | PYTHONPATH="${AIRULESET_PY%/airuleset.py}" \
    python3 -m watchdog.session_status --event stop >/dev/null 2>&1 || true

LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)

# #466 fail-loud: send_q() sets this to 1 when it runs. A ❓-carrying turn that
# never reaches send_q (marker not on the last line → the `else` branch; the
# arm-question skip) must still leave a delivery-log line — the backstop before
# the final exit consults this flag so the silence class becomes impossible.
SENDQ_CALLED=0

# Strip markdown emphasis + a leading marker label so the phone line is clean
# Slovak prose (e.g. "❓ **Question:** approve merge?" -> "approve merge?").
strip_md() {
    printf '%s' "$1" \
        | sed -E 's/\*\*//g' \
        | sed -E 's/^[[:space:]]*(NEEDS[[:space:]]+YOU|Question|DONE)[[:space:]]*:?[[:space:]]*//I' \
        | sed -E 's/^[[:space:]]+//'
}

goal_armed() {
    # Is a /goal loop ARMED for THIS session (2026-07-25 revision)? A
    # per-ticket/per-batch ✅ DONE inside an armed autopilot loop must NOT
    # queue a SECOND idle Discord ping — the sanctioned per-ticket run-card
    # already gives phone visibility, and a second ping per ticket is
    # exactly the per-phase noise the user removed
    # (milestone-notifications.md).
    #
    # Reuses the EXACT SAME signal the watchdog's own goal jobs key on
    # (`"◎ /goal" in captured` — _safe_to_bounce_nudge in
    # watchdog/__init__.py; pane_goal_armed, used by deliver_goal, in
    # watchdog/goal.py) — never a second, invented detector. This hook
    # runs as a child of the live Claude Code process; when that session
    # runs inside tmux (the standing setup on every managed box) it
    # inherits tmux's own $TMUX_PANE env var, so THIS pane can be captured
    # directly — no cross-session pane search needed.
    #
    # ND_FAKE_PANE_CAPTURE lets tests inject a fixed capture instead of
    # shelling out to a real tmux (and keeps every OTHER test in this file
    # deterministic without depending on whatever pane this suite happens
    # to run inside).
    local cap
    if [ -n "${ND_FAKE_PANE_CAPTURE+x}" ]; then
        cap="$ND_FAKE_PANE_CAPTURE"
    elif [ -n "${TMUX_PANE:-}" ] && command -v tmux >/dev/null 2>&1; then
        cap=$(tmux capture-pane -p -t "$TMUX_PANE" 2>/dev/null || echo "")
    else
        cap=""
    fi
    printf '%s' "$cap" | grep -qF "◎ /goal"
}

# Checkpoint of the PREVIOUS ✅ boundary in this session — the anchor the card
# check measures against. First ✅ ever seen for a session bootstraps to a
# lookback window (the same 6h `statusbar.AUTOPILOT_RUN_WINDOW_S` treats as
# "an active autopilot run").
CARDCHK="/tmp/claude-discord-cardchk-${SID}"
CARD_LOOKBACK_S=21600

card_delivered_since_last_boundary() {
    # #134: the ✅ is suppressed because a card was DELIVERED for THIS ticket,
    # never merely because a `/goal` is armed.
    #
    # The armed-goal premise was the design error. It assumed the per-ticket
    # run-card covers the ticket, but nothing enforced the card — and marek's
    # pane has had a goal armed continuously for 18h+ across 7 re-arms with
    # zero `Goal cleared:`, so "armed" is a near-permanent state there and the
    # suppression was TOTAL rather than occasional. A suppression that defers
    # to an unenforced action is a silence generator.
    #
    # DELIVERED, not merely claimed: `_dedup_claim` writes the marker BEFORE
    # the POST, so presence alone proves nothing (#135). `marker_delivered`
    # is the distinction.
    #
    # Anything unprovable — no cwd, not a git repo, no `origin`, no python —
    # means the ping GOES THROUGH. Never suppress on "don't know": the cost of
    # a spurious ping is one extra line on a phone, the cost of a wrong
    # suppression is the five-day silence this ticket exists to end.
    local since repo newest
    command -v python3 >/dev/null 2>&1 || return 1
    [ -n "$CWD" ] || return 1
    since=$(cat "$CARDCHK" 2>/dev/null || echo "")
    case "$since" in ''|*[!0-9.]*) since="" ;; esac
    repo=$(python3 "$AIRULESET_PY" notify --repo-name --cwd "$CWD" 2>/dev/null \
           || echo "")
    [ -n "$repo" ] || return 1
    newest=$(python3 "$AIRULESET_PY" notify --newest-card --repo "$repo" \
             2>/dev/null || echo "")
    case "$newest" in ''|*[!0-9.]*) return 1 ;; esac
    if [ -z "$since" ]; then
        since=$(python3 -c "import time,sys; print(time.time()-float(sys.argv[1]))" \
                "$CARD_LOOKBACK_S" 2>/dev/null || echo "")
        [ -n "$since" ] || return 1
    fi
    python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)" \
        "$newest" "$since" 2>/dev/null
}

emit() {
    # $1 = emoji, $2 = raw content; clean + truncate to keep the device line
    # short. ✅ stays ONE short line (only the ❓ question carries a full block);
    # jq slices by CODEPOINTS so multi-byte Slovak never gets chopped mid-char.
    local c
    c=$(strip_md "$2" | jq -Rrs 'rtrimstr("\n") | .[0:250]')
    # #668: record the reliable Stop-time cwd so the idle delivery resolves the
    # real project even when the idle event carries none. Sibling FIRST, trigger
    # (the pending) LAST — a hook killed between the two must never pair a FRESH
    # pending with a STALE sibling from a previous ✅ (a wrong label). Guarded +
    # brace-grouped so a foreign-owned stale /tmp file can't leak "Permission
    # denied" to stderr or abort the ✅ branch under `set -e` (#492), which would
    # skip the CARDCHK boundary write below.
    { printf '%s' "$CWD" > "$PENDING_CWD"; } 2>/dev/null || true
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

# --- suppression diagnostics (#467) -----------------------------------------
# Every early RETURN in send_q() that DROPS a question ping writes ONE durable
# line to the SAME notify-delivery.log the send path uses. Before this, a
# suppression (a verbatim re-poke dedup, or the question-quality-rewrite
# settle skip) left ZERO trace anywhere — the exact #467 silence (empty
# question map AND empty delivery log) that made a lost ask-and-continue
# question undiagnosable. Diagnostics only: every write is guarded (a
# read-only $HOME can never turn logging into a dropped ping), rotated at the
# same cap the send path uses, and dry-run logs nothing (mirrors the send
# path's own dry-run-logs-nothing contract).
# Short, stable per-question fingerprint for the delivery log — lets two
# DIFFERENT questions be told apart (the per-project `❓:<project>` send key
# never could, #466). sha1sum where present, cksum as a portable fallback;
# both yield a stable short token used ONLY for log identity, never a decision.
# _qhash — canonical definition lives in hooks/lib-qhash.sh, SHARED with
# stop-check-question-quality.sh so both hooks fingerprint the SAME delivered
# question identically (#740; single source, never two drifting copies). The
# guarded inline fallback below runs ONLY on a partial install where the sibling
# lib is absent — an undefined _qhash would abort this hook under
# `set -euo pipefail` and silently drop the ping, so the delivery path must
# never depend on the lib being present.
_LIB_QHASH="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib-qhash.sh"
[ -r "$_LIB_QHASH" ] && . "$_LIB_QHASH"
if ! type _qhash >/dev/null 2>&1; then
    _qhash() {
        printf '%s' "${1:-}" | { sha1sum 2>/dev/null || cksum; } \
            | tr -cd '0-9a-fA-F' | cut -c1-8
    }
fi

_pending_log() {
    # $1 = status, $2 = reason, $3 = per-question hash (which question — #466).
    # set -u safe: all default to "" so a future caller passing <3 args logs a
    # blank field instead of aborting the hook.
    [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ] && return 0
    local log stamp size status reason qhash
    status="${1:-}"
    reason="${2:-}"
    qhash="${3:-}"
    log="$HOME/.claude/notify-delivery.log"
    mkdir -p "$(dirname "$log")" 2>/dev/null || true
    size=$(stat -c %s "$log" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    [ "$size" -gt 512000 ] && mv -f "$log" "$log.1" 2>/dev/null || true
    stamp=$(date -Iseconds 2>/dev/null || echo '?')
    { printf '%s %s kind=pending key=%s reason=%s qhash=%s\n' \
        "$stamp" "$status" "$SID" "$reason" "$qhash" >>"$log"; } 2>/dev/null || true
    return 0
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
    local key payload send f now m QH
    SENDQ_CALLED=1
    key=$(strip_md "$1" | jq -Rrs 'rtrimstr("\n") | .[0:1500]')
    payload=$(clean_q "$2")
    [ -z "$payload" ] && payload="$key"
    [ -z "$key" ] && key="$payload"
    QH=$(_qhash "$key")
    [ -z "$key" ] && { _pending_log "suppressed" "empty-content" "$QH"; return 0; }
    if [ -f "$LASTQ" ] && [ "$(cat "$LASTQ" 2>/dev/null)" = "$key" ]; then
        _pending_log "suppressed" "verbatim-repeat-dedup" "$QH"
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
    # ONLY the question-quality gate's block means "this QUESTION draft is
    # being rewritten and the reworded version re-delivers"; every OTHER stop
    # gate (working-liveness / status-marker / prose / untracked / playbook /
    # sendmessage / prod-gating) blocks for a reason ORTHOGONAL to the
    # question, so its marker must never eat a legitimate ping. #467: an
    # ask-and-continue ⏳ WORKING turn routinely trips working-liveness in a
    # busy batch (no live background_tasks once the dispatched worker has
    # returned) — and the OLD broad `airuleset-*-block-${SID}` glob read that
    # unrelated block as "the question was rejected" and SILENTLY dropped the
    # ping, with no delivery-log line at all. The genuine double-ping of the
    # SAME reworded question is still guarded by the LASTQ dedup + reword-edit
    # below, unchanged. `-L`/`-O` guard a foreign-uid-planted /tmp marker
    # (shared, sticky /tmp) from suppressing a ping — a planted file fails the
    # owner test, so the safe direction is taken (the question pings).
    qqf="/tmp/airuleset-question-quality-block-${SID}"
    if [ -f "$qqf" ] && [ ! -L "$qqf" ] && [ -O "$qqf" ]; then
        m=$(stat -c %Y "$qqf" 2>/dev/null || echo 0)
        case "$m" in ''|*[!0-9]*) m=0 ;; esac
        if [ "$m" -le "$now" ] && [ $((now - m)) -lt 12 ]; then
            _pending_log "suppressed" "question-quality-rewrite" "$QH"
            return 0
        fi
    fi

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
            _pending_log "edit" "reword-dryrun" "$QH"
            printf '%s' "$key" > "$LASTQ"
            return 0
        fi
        if printf '%s' "$payload" | python3 "$AIRULESET_PY" \
                notify --edit-question --session "$SID" >/dev/null 2>&1; then
            # An edit does not push-ping and does NOT record the question map —
            # before #466 it also left ZERO log trace, the exact silence class
            # (an in-place reword vanished from every diagnostic). Log it loud.
            _pending_log "edit" "reword-in-place" "$QH"
            printf '%s' "$key" > "$LASTQ"
            return 0
        fi
        # nothing recent/editable (expired, deleted) → fall through to a POST
        _pending_log "edit-fallthrough" "no-recent-question" "$QH"
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
            ND_SESSION_ID="$SID" ND_QHASH="$QH" bash "$send"; then
        printf '%s' "$key" > "$LASTQ"
    else
        # The send path already logs its own not-delivered reason, but a send
        # that never reached its own logger (a missing/failed script) must not
        # be silent — guarantee a pending-side line either way (#466).
        _pending_log "not-delivered" "send-nonzero" "$QH"
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
    rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
    # No phone ping (the watchdog auto-arm types the /goal itself), but this IS
    # a ❓ turn — leave a trace so it is never a silent path either (#466).
    _pending_log "suppressed" "arm-question" "$(_qhash "$MSG")"
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
    rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
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
    rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
    send_q "$C" "$(extract_block "${N:-0}")"
elif printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*⏳'; then
    # ⏳ WORKING is the last line → still going (even if a "✅ DONE:" appears
    # earlier in the turn, e.g. autopilot "merged #5 … now ⏳ working #6"). Clear
    # any stale pending so nothing fires while Claude keeps working. Same
    # line-START anchoring as the ❓ branch — a ⏳ mid-sentence is prose.
    rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
elif printf '%s' "$MSG" | grep -qiE '✅[[:space:]]*DONE:|#+[[:space:]]*✅[[:space:]]*work complete|✅[[:space:]]*work complete'; then
    # Fully-done state. A per-ticket/per-batch ✅ DONE inside an autopilot
    # loop must not queue a SECOND idle ping when the sanctioned per-ticket
    # run-card ALREADY gave phone visibility for THIS ticket — that second
    # ping is the per-phase noise the user removed (2026-07-25 revision,
    # message-status-marker.md / milestone-notifications.md).
    #
    # But the condition is DELIVERY, not an armed goal (#134). The armed-goal
    # premise deferred to something nothing enforced, and when the card
    # stopped firing it removed the only remaining signal — five days, ~85
    # merged PRs, zero reports. Card delivered for this repo since the
    # previous ✅ boundary → suppress as designed. No card → the ping goes
    # through, exactly as it did before the guard existed.
    if goal_armed && card_delivered_since_last_boundary; then
        SUPPRESSED=1
        rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
    else
        SUPPRESSED=0
        # Prefer an explicit "✅ DONE: <outcome>" line; else the report's
        # "What changed" / "Goal" one-liner; else a generic Slovak fallback.
        DLINE=$(printf '%s\n' "$MSG" | grep -iE '✅[[:space:]]*DONE:' | tail -1 || true)
        if [ -n "$DLINE" ]; then
            C=$(printf '%s' "$DLINE" | sed -E 's/.*✅[[:space:]]*DONE:[[:space:]]*//I')
        else
            C=$(printf '%s\n' "$MSG" | grep -iE '^\*\*(What changed|Goal)\b' | head -1 \
                | sed -E 's/^\*\*(What changed|Goal):?\*\*:?[[:space:]]*//I' || true)
            [ -z "$C" ] && C="práca dokončená"
        fi
        emit "✅" "$C"
    fi
    # Move the boundary anchor forward whichever way it went, so THIS ticket's
    # card can never be counted again for the NEXT ticket. Without it, one
    # delivered card would suppress every later ✅ inside its lookback window
    # — the same "a stale artifact stands in for a fresh one" mistake in
    # miniature.
    # Sub-second precision on purpose: marker mtimes carry fractions, so an
    # integer checkpoint reads as up to a second EARLIER than it is and a
    # card delivered in the same second as the previous boundary would still
    # count for the next ticket.
    date +%s.%N > "$CARDCHK" 2>/dev/null || date +%s > "$CARDCHK" 2>/dev/null || true
    : "$SUPPRESSED"
else
    # No marker → nothing to notify. Clear any stale pending.
    rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
fi

# Fail-loud backstop (#466): a ❓ NEEDS YOU / ❓ ASKED marker present in the turn
# but NOT routed to send_q — the marker was not the last non-empty line (a
# trailing note, or a wrapped long URL leaving a tail fragment), so dispatch
# fell to the `else`/`⏳`/`✅` branch and the question vanished with zero trace,
# the incident's silence class. LOG-ONLY: this never pings, so a false positive
# (a ✅ report quoting "❓ NEEDS YOU" as text) is a harmless diagnostic line,
# never a spurious ping.
# HERE-STRING, never `printf … | grep -q`: under this hook's `set -o pipefail`,
# `grep -q` quits at the first match while `printf` is still writing, so past the
# 64 KiB pipe buffer `printf` takes SIGPIPE, the pipe returns 141, the `if` goes
# false, and the backstop silently fails to log — reintroducing the exact silence
# class it exists to close (the #190/#192/#194 race). A here-string has no
# concurrent writer, so the race cannot exist; still `set -e`-safe in an `if`.
if [ "${SENDQ_CALLED:-0}" = "0" ] \
   && grep -qE '❓[[:space:]]*\**[[:space:]]*(NEEDS[[:space:]]+YOU|ASKED)' <<<"$MSG"; then
    _pending_log "unhandled" "question-marker-not-dispatched" "$(_qhash "$MSG")"
fi

exit 0
