#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash|Write|Edit) -- #915 Odoo message_post without body_is_html.
#
# When a tool-call payload contains `message_post` AND HTML tags (evidence of
# HTML content being sent) but LACKS `body_is_html` (the flag that tells Odoo to
# render HTML), BLOCK -- the omission causes Odoo to escape the HTML so clients
# see raw `<p>`, `<b>` etc. instead of formatted text.
#
# Incident: montalu 6.9.2026, 3 project.task chatter comments with raw HTML
# tags reaching clients (mail.message ids 1794757-1794759); also earlier
# discuss.channel incidents on montalu/miva (odoo-erp issue 6409).
#
# This is CROSS-MODEL: any Odoo model's `message_post` (project.task,
# discuss.channel, sale.order, ...), unlike the sibling
# `block-discuss-thread-name.sh` which scopes to `discuss.channel` only.
#
# Detection: content has `message_post` + an HTML tag (`<p>`, `<br`, `<div`,
# etc.) + neither `body_is_html` nor `subtype_xmlid.*mail.mt_comment` present.
#
# Bypass (rare, logged): `airuleset:html-ok` in the content.
#
# Reads the payload on STDIN (.tool_input.command for Bash, .content for Write,
# .new_string for Edit), exits 2 with the reason on STDERR. Fail-open on any
# unmeasurable state (no jq, empty content).

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0

CONTENT=$(printf '%s' "$INPUT" | jq -r \
    '.tool_input.command // .tool_input.content // .tool_input.new_string // empty' \
    2>/dev/null || echo "")
[ -n "$CONTENT" ] || exit 0

# Cheap pre-filter: only payloads mentioning message_post at all.
case "$CONTENT" in
    *message_post*) ;;
    *) exit 0 ;;
esac

# Check for bypass marker.
case "$CONTENT" in
    *airuleset:html-ok*)
        LOG="/tmp/airuleset-html-ok-bypass-${EUID:-$(id -u)}.log"
        { echo "$(date -Iseconds)  html-ok-bypass" >> "$LOG"; } 2>/dev/null || true
        exit 0
        ;;
esac

# Detect HTML tags in the content.
# Common opening/self-closing tags: <p>, <br>, <div>, <span>, <a ...>, <b>,
# <strong>, <em>, <ul>, <li>, <table>, <tr>, <td>, <th>, <h1>-<h6>, <img ...>
# Also closing tags: </p>, </div>, etc.
# Pattern: <[a-zA-Z] followed by a word char, space, /, or >
if ! printf '%s' "$CONTENT" | grep -qE '</?[a-zA-Z][a-zA-Z0-9]*(\s|/|>)'; then
    # No HTML tags found -- nothing to guard.
    exit 0
fi

# HTML tags are present. Check for body_is_html or subtype_xmlid=mail.mt_comment.
if printf '%s' "$CONTENT" | grep -q 'body_is_html'; then
    exit 0
fi
if printf '%s' "$CONTENT" | grep -q 'subtype_xmlid.*mail\.mt_comment'; then
    exit 0
fi

# HTML tags found, no body_is_html, no subtype_xmlid mail.mt_comment => BLOCK.
cat >&2 <<'MSG'

🚫 BLOCKED: an Odoo message_post contains HTML tags but lacks body_is_html (airuleset #915).

The payload calls `message_post` with HTML content (<p>, <b>, <br>, etc.) but
does NOT include `body_is_html=True`. Without this flag, Odoo ESCAPES the HTML
and clients see raw tags like `<p>Dobrý deň</p>` instead of formatted text.

This happened on montalu PROD on 6.9.2026 (3 project.task comments with raw
HTML tags reaching clients -- mail.message ids 1794757-1794759).

FIX: add `body_is_html=True` (Python) / `"body_is_html": true` (JSON) to the
message_post kwargs, then re-run. For example:

  message_post(body="<p>text</p>", body_is_html=True, ...)

Alternatively, set `subtype_xmlid="mail.mt_comment"` which also implies HTML.

This gates ANY model's message_post (project.task, discuss.channel, sale.order,
...) -- not just discuss.channel. Bypass (rare, logged, only for a genuine
plain-text post that contains literal angle brackets): put airuleset:html-ok
in the content.
MSG
exit 2
