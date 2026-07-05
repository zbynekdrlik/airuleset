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

# Project name for the header: git toplevel basename, else cwd basename.
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
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
# reads the owner from the tmux session group + the channel .env). Path is relative
# to THIS file.
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
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
emit_one() {
    # $1 = owner (may be empty for the primary). Forces AIRULESET_NOTIFY_OWNER onto
    # both resolver calls so mention+thread ALWAYS agree (the Python send() invariant).
    local T="$1"
    local MENTION CH CONTENT
    MENTION=$(AIRULESET_NOTIFY_OWNER="$T" python3 "$AIRULESET_PY" notify --mention-prefix 2>/dev/null || echo "")
    CH=$(AIRULESET_NOTIFY_OWNER="$T" python3 "$AIRULESET_PY" notify --channel-id 2>/dev/null | tr -d '\r\n' || echo "")
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
                # Record message-id → session so a Discord REPLY to this ❓ ping
                # routes the answer back into the asking session (watchdog job 7).
                # Only the ❓ Stop-hook path sets ND_SESSION_ID; a bad parse is a
                # no-op (reply routing just won't work for this one message).
                if [ -n "${ND_SESSION_ID:-}" ]; then
                    local mid
                    mid=$(printf '%s' "$body" | jq -r '.id // empty' 2>/dev/null || echo "")
                    if [ -n "$mid" ]; then
                        python3 "$AIRULESET_PY" notify --record-question \
                            --message-id "$mid" --channel "$CH" \
                            --session "$ND_SESSION_ID" --cwd "$CWD" \
                            >/dev/null 2>&1 || true
                    fi
                fi
                ;;
            *)   DELIVERY_FAILED=1 ;;
        esac
        return 0
    fi

    (curl -s --max-time 5 -X POST \
        "https://discord.com/api/v10/channels/${CH}/messages" \
        -H "Authorization: Bot ${BOT_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg content "$CONTENT" '{content: $content, flags: 4}')" \
        >/dev/null 2>&1) &
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
