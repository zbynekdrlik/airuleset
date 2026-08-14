#!/usr/bin/env bash
set -euo pipefail

# Shared Discord SEND path — the single place that composes the structured device
# line and delivers it. Both notify hooks call this so the curl/compose logic
# lives in ONE file (no patchwork duplication):
#   - notify-discord-pending.sh (Stop)  → fires it IMMEDIATELY on ❓ NEEDS YOU
#     (the user is blocked on us → ping now; do NOT wait for an `idle_prompt`
#     event, which Claude Code emits unreliably over tmux/SSH).
#   - notify-discord.sh (Notification: idle_prompt) → fires it for a pending ✅
#     when the user is genuinely idle/away (the unchanged mobile-app model for
#     "done" — a finished turn is less urgent than a question).
#
# Inputs (env):
#   ND_EMOJI  — ❓ or ✅
#   ND_TEXT   — cleaned Slovak content (already stripped of markers/markdown)
#   ND_CWD    — project dir, for the "**emoji PROJECT — status**" header
# Modes:
#   DISCORD_NOTIFY_DRYRUN=1 + ND_DRYRUN_FILE=<path> → write CONTENT to that file,
#       NOTHING to stdout (lets the silent Stop hook be tested hermetically).
#   DISCORD_NOTIFY_DRYRUN=1 (no file)               → print CONTENT to stdout
#       (the idle hook's existing test contract).
#   otherwise                                        → POST to Discord, backgrounded
#       and silent (so the Stop pipeline is never polluted / blocked).
#   ND_CONFIRM=1 (the ❓ immediate path)             → POST in the FOREGROUND and
#       exit non-zero unless every attempted delivery got HTTP 2xx. The caller
#       records the question as pinged ONLY on success — a transient Discord
#       failure on the FIRST ask must stay retryable by the next identical
#       re-emit, never be silently recorded as sent (review finding, 2026-07-04).
# Without ND_CONFIRM: always exit 0.

EMOJI="${ND_EMOJI:-}"
TEXT="${ND_TEXT:-}"
CWD="${ND_CWD:-}"
CONFIRM="${ND_CONFIRM:-0}"
DELIVERY_FAILED=0
[ -n "$EMOJI" ] || exit 0
[ -n "$TEXT" ]  || exit 0

# #296: a ❓ question ping routes to the owner's SEPARATE questions thread
# (claude-<owner>-q); every other emoji (✅, and anything future) stays on
# the existing claude-<owner> thread — resolved via --kind on the SAME
# --channel-id call below, never a second channel-resolution mechanism.
KIND="default"
[ "$EMOJI" = "❓" ] && KIND="questions"

# AIRULESET_PY resolved EARLY (moved up from below `emit_one`) — --project-
# label (#369) is the SAME canonical, origin-derived + stream-qualified
# label `project_label_for()` computes for a run-card header and for the
# project Discord thread's own name, so this header and the routing call
# below (in `emit_one`) can never disagree about which project a message
# is for.
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
# #369 review m3 (TRIGGERED): the OLD git-toplevel-basename recipe this
# call replaced was NEVER re-added as a fallback, so any --project-label
# failure (python3 missing, airuleset.py unreadable) silently collapsed
# PROJECT to the literal "unknown" for this repo's own HIGHEST-traffic
# notification path — mirrors notify-api-error.sh's own fallback.
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(python3 "$AIRULESET_PY" notify --project-label --cwd "$CWD" 2>/dev/null || echo "")
    if [ -z "$PROJECT" ]; then
        PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null \
                  | xargs basename 2>/dev/null || basename "$CWD")
    fi
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

case "$EMOJI" in
    "❓") STATUS="otázka" ;;
    "✅") STATUS="hotovo" ;;
    *)    STATUS="" ;;
esac
HEADER="**${EMOJI} ${PROJECT}**"
[ -n "$STATUS" ] && HEADER="${HEADER} — ${STATUS}"
# BASE body WITHOUT the @mention — the mention is per-target, because each
# recipient gets THEIR OWN @mention in THEIR OWN thread.
#   ND_BLOCK=1 (the ❓ question path): TEXT is a structured markdown block
#   (bold header, `- ` list, spacing) — post it UNQUOTED under the header with
#   a blank separator; a `> ` blockquote renders it as one gray text wall (the
#   camera-box complaint, 2026-07-05).
#   Otherwise (✅ one-liners): keep the classic `> `-quoted line.
if [ "${ND_BLOCK:-0}" = "1" ]; then
    CONTENT_BASE=$(printf '%s\n\n%s' "$HEADER" "$TEXT")
else
    TEXT_QUOTED=$(printf '%s\n' "$TEXT" | sed 's/^/> /')
    CONTENT_BASE=$(printf '%s\n%s' "$HEADER" "$TEXT_QUOTED")
fi

# @mention + thread routing via `airuleset.py notify` (single source of truth: it
# reads the owner from the tmux session group + the channel .env). AIRULESET_PY
# was already resolved above (needed early for --project-label).
# Resolve the PRIMARY owner ONCE, then its parallel mirror recipients
# (DISCORD_MIRROR_<OWNER> — e.g. david → also zbynek, so a persona's notifications
# ALSO ping a real person). For a normal single-owner box (zbynek / marek) the mirror
# list is EMPTY → the loop runs exactly once → unchanged single-post behavior.
PRIMARY_OWNER="$(python3 "$AIRULESET_PY" notify --owner 2>/dev/null || echo "")"
MIRRORS="$(AIRULESET_NOTIFY_OWNER="$PRIMARY_OWNER" python3 "$AIRULESET_PY" notify --mirror-owners 2>/dev/null || echo "")"

# Real-delivery prerequisites (shared across all targets). Resolved once — and, since
# this now runs BEFORE the dry-run branch, the grep MUST tolerate a tokenless .env
# (no DISCORD_BOT_TOKEN line): `grep` returns 1 on no-match, which under
# `set -euo pipefail` would kill the script before any message is emitted. `|| true`
# keeps BOT_TOKEN="" so dry-run still prints and real delivery falls through the guard.
ENVF=~/.claude/channels/discord/.env
BOT_TOKEN=""
if [ -f "$ENVF" ]; then
    BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' "$ENVF" 2>/dev/null | cut -d'=' -f2- | tr -d "\"'" | tr -d '\r\n' || true)
fi

# Emit ONE message per DISTINCT target thread. The PRIMARY target ALWAYS emits — even
# when the owner is empty/unknown (no @mention, shared-or-no channel) — so a machine
# with no tmux owner still notifies exactly as before. Mirrors are EXTRA and only fire
# when DISCORD_MIRROR_<OWNER> lists them; a target whose thread was ALREADY delivered
# to (the primary's, OR an earlier mirror's — e.g. two owners sharing the fallback
# channel) is skipped, so a misconfig can't double-post into one thread. The same
# de-dup applies in dry-run, so the preview matches real delivery.
POSTED_CHANNELS=" "            # space-delimited set of channels already emitted to

# --- delivery log (#135) ---------------------------------------------------
# Before this, a non-delivery on the NON-confirm path (the idle ✅) set
# DELIVERY_FAILED=1 and returned with no log line and nothing on stderr — so a
# notification that never even attempted its curl left zero trace anywhere,
# and "sent but rejected" / "never sent" / "nothing to send" were one
# observation. Now every non-delivery says WHY, in the SAME durable file the
# Python path writes (notify.log_delivery). Diagnostics only: every write is
# `|| true`-guarded, so a read-only $HOME can never turn logging into a
# dropped ping, and stderr stays clean on the allowed path (#124).
DELIVERY_LOG="$HOME/.claude/notify-delivery.log"
DELIVERY_LOG_CAP=512000
_delivery_log() {
    # $1 = status, $2 = reason
    local size
    mkdir -p "$(dirname "$DELIVERY_LOG")" 2>/dev/null || true
    size=$(stat -c %s "$DELIVERY_LOG" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    if [ "$size" -gt "$DELIVERY_LOG_CAP" ]; then
        mv -f "$DELIVERY_LOG" "$DELIVERY_LOG.1" 2>/dev/null || true
    fi
    {
        printf '%s %s kind=shell key=%s reason=%s qhash=%s\n' \
            "$(date -Iseconds 2>/dev/null || echo '?')" "$1" \
            "${EMOJI}:${PROJECT}" "$2" "${ND_QHASH:-}" >>"$DELIVERY_LOG"
    } 2>/dev/null || true
}

emit_one() {
    # $1 = owner (may be empty for the primary). Forces AIRULESET_NOTIFY_OWNER onto
    # both resolver calls so mention+thread ALWAYS agree (the Python send() invariant).
    local T="$1"
    local MENTION CH CONTENT
    MENTION=$(AIRULESET_NOTIFY_OWNER="$T" python3 "$AIRULESET_PY" notify --mention-prefix 2>/dev/null || echo "")
    # #369: route via the project thread ONLY on the default kind — a ❓
    # question stays on the CENTRALIZED questions thread by design (job 7's
    # Discord-reply routing lives there; --channel-id already ignores
    # --project under --kind questions too, but this keeps the two clauses
    # in the SHELL matching the KNOWN, tested design instead of relying on
    # the Python side's own defensive ignore).
    # --dry-run (a preview call, DISCORD_NOTIFY_DRYRUN=1) is forwarded so
    # --channel-id resolves the PROJECT channel via the plain, side-effect-
    # free notification_channel() instead of resolve_project_channel()
    # (which writes a delivery-log "fallback" line and may spawn a
    # background self-heal) — a mere PREVIEW must never do either. This
    # covers ONLY the default-kind (project) branch below — the KIND
    # "questions" branch's own resolve_questions_channel() has the
    # IDENTICAL pre-existing dry-run-unawareness gap (#330, predates #369)
    # and is unaffected by --dry-run being forwarded here; #369's own
    # adversarial review confirmed it is not reachable in production
    # today (needs a token present AND an unprovisioned -q thread AND a
    # genuine dry-run call simultaneously — no real caller combines those).
    local CID_DRYRUN=()
    [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ] && CID_DRYRUN=(--dry-run)
    if [ "$KIND" = "questions" ]; then
        CH=$(AIRULESET_NOTIFY_OWNER="$T" python3 "$AIRULESET_PY" notify --channel-id --kind "$KIND" "${CID_DRYRUN[@]}" 2>/dev/null | tr -d '\r\n' || echo "")
    else
        CH=$(AIRULESET_NOTIFY_OWNER="$T" python3 "$AIRULESET_PY" notify --channel-id --kind "$KIND" --project "$PROJECT" "${CID_DRYRUN[@]}" 2>/dev/null | tr -d '\r\n' || echo "")
    fi
    # Skip a target whose (non-empty) thread was already emitted to — no double-post.
    if [ -n "$CH" ]; then
        case "$POSTED_CHANNELS" in *" $CH "*) return 0;; esac
    fi
    CONTENT="${MENTION}${CONTENT_BASE}"
    # Discord hard-caps a message at 2000 chars — an oversize POST gets a 400,
    # which in confirm mode would mark the question failed on EVERY retry (it
    # would never reach the phone). Codepoint-safe cap, belt-and-suspenders
    # under the pending hook's own 1800-char payload budget (per-line '> '
    # quoting can inflate a many-line payload past it). TAIL-PRESERVING: a
    # blind head slice would chop the final DECISION line off the end —
    # exactly the truncated-question failure this pipeline exists to prevent
    # (review finding, 2026-07-04) — so oversize keeps the head + the tail of
    # the last line (the quoted '> ❓ <rozhodnutie>'), max 1650+4+280 < 2000.
    if command -v jq &>/dev/null; then
        CONTENT=$(printf '%s' "$CONTENT" | jq -Rrs 'rtrimstr("\n")
            | if length <= 1990 then .
              else (split("\n") | last) as $d | .[0:1650] + "\n… " + ($d | .[-280:])
              end')
    fi

    if [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ]; then
        # dry-run: one block per DISTINCT target (single-owner boxes emit exactly one —
        # the unchanged test contract). File mode appends; stdout mode prints.
        [ -n "$CH" ] && POSTED_CHANNELS="${POSTED_CHANNELS}${CH} "
        if [ -n "${ND_DRYRUN_FILE:-}" ]; then
            printf '%s\n' "$CONTENT" >> "$ND_DRYRUN_FILE"
        else
            printf '%s\n' "$CONTENT"
        fi
        return 0
    fi

    # Real delivery. Skip when we can't post — in confirm mode a skip IS a
    # failed delivery (the caller must be able to retry later).
    if [ -z "$BOT_TOKEN" ] || [ -z "$CH" ] || ! command -v jq &>/dev/null; then
        # Name the SPECIFIC missing prerequisite — the difference between a
        # five-minute diagnosis and a five-day one (#135).
        local why="unknown"
        if   [ -z "$BOT_TOKEN" ];            then why="no-token"
        elif [ -z "$CH" ];                   then why="no-channel"
        elif ! command -v jq &>/dev/null;    then why="no-jq"
        fi
        _delivery_log "not-delivered" "$why"
        DELIVERY_FAILED=1
        return 0
    fi
    POSTED_CHANNELS="${POSTED_CHANNELS}${CH} "

    # flags: 4 = SUPPRESS_EMBEDS — a URL in a notification must never unfurl
    # into a giant link-preview (the codex-bridge card grew a screen-sized Odoo
    # logo under its 🔗 link, 2026-07-04). Links stay clickable.
    if [ "$CONFIRM" = "1" ]; then
        # FOREGROUND, delivery-confirmed (the ❓ path): only a real HTTP 2xx counts.
        # --max-time 5 stays well under the Stop-hook timeout (15s in hooks.json).
        # Capture the BODY too (not -o /dev/null) so we can record the created
        # message id for Discord-reply routing: `<body>\n<http_code>`.
        local resp code body
        resp=$(curl -s --max-time 5 -w '\n%{http_code}' -X POST \
            "https://discord.com/api/v10/channels/${CH}/messages" \
            -H "Authorization: Bot ${BOT_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg content "$CONTENT" '{content: $content, flags: 4}')" \
            2>/dev/null) || resp=""
        code="${resp##*$'\n'}"
        body="${resp%$'\n'*}"
        case "$code" in
            2??)
                # A genuine delivery — logged here too (#184: previously
                # ONLY a non-delivery ever wrote a line, so a healthy box
                # and a broken logger both showed an empty file).
                _delivery_log "sent" ""
                # Record message-id → session so a Discord REPLY to this ❓ ping
                # routes the answer back into the asking session (watchdog job 7).
                # Only the ❓ Stop-hook path sets ND_SESSION_ID; a bad parse is a
                # no-op (reply routing just won't work for this one message).
                if [ -n "${ND_SESSION_ID:-}" ]; then
                    local mid
                    mid=$(printf '%s' "$body" | jq -r '.id // empty' 2>/dev/null || echo "")
                    if [ -n "$mid" ]; then
                        # The posted ❓ CONTENT rides on stdin — the reply
                        # delivery wraps the user's answer with the question it
                        # answers (a bare '1' typed days later is meaningless).
                        printf '%s' "$CONTENT" | \
                        python3 "$AIRULESET_PY" notify --record-question \
                            --question-stdin \
                            --message-id "$mid" --channel "$CH" \
                            --session "$ND_SESSION_ID" --cwd "$CWD" \
                            >/dev/null 2>&1 || true
                    fi
                fi
                ;;
            *)   _delivery_log "not-delivered" "http-${code:-none}"
                 DELIVERY_FAILED=1 ;;
        esac
        return 0
    fi

    # Fire-and-forget (the idle ✅ path): still backgrounded so the caller
    # never blocks, but the outcome is now captured and logged FROM WITHIN
    # the same background subshell (#184) — before this, neither a success
    # nor a failure on this path ever left a trace anywhere.
    (
        bg_resp=$(curl -s --max-time 5 -w '\n%{http_code}' -X POST \
            "https://discord.com/api/v10/channels/${CH}/messages" \
            -H "Authorization: Bot ${BOT_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg content "$CONTENT" '{content: $content, flags: 4}')" \
            2>/dev/null) || bg_resp=""
        bg_code="${bg_resp##*$'\n'}"
        case "$bg_code" in
            2??) _delivery_log "sent" "" ;;
            *)   _delivery_log "not-delivered" "http-${bg_code:-none}" ;;
        esac
    ) &
}

emit_one "$PRIMARY_OWNER"      # primary — always fires (owner may be empty)
for T in $MIRRORS; do          # mirrors — only when DISCORD_MIRROR_<OWNER> lists them
    [ -n "$T" ] || continue
    emit_one "$T"
done

# Confirm mode reports delivery truthfully; the default stays always-0 (the
# background curls are fire-and-forget and cannot be confirmed anyway).
if [ "$CONFIRM" = "1" ] && [ "$DELIVERY_FAILED" = "1" ]; then
    exit 1
fi
exit 0
