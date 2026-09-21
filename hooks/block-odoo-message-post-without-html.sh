#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash|Write|Edit) -- #915 Odoo message_post without
# body_is_html, EXTENDED by #1054 to close two live bypasses.
#
# #915 core: when a tool-call payload contains `message_post` AND HTML tags but
# LACKS `body_is_html`, BLOCK -- the omission makes Odoo escape the HTML so
# clients see raw `<p>`, `<b>` etc. instead of formatted text.
#
# #1054 gap 1 (double wrapping): a body already entity-escaped (`&lt;p&gt;`)
# passed whenever `body_is_html` was present -- the flag whitewashed an escaped
# body Odoo renders as raw tags. Now an escaped TAG anywhere in the payload
# BLOCKS with a dedicated reason, BEFORE the body_is_html early-exit.
#
# #1054 gap 2 (shipped-file drivers): a driver written locally, moved with
# `scp` and run with `ssh ... odoo shell < driver.py` bypassed the hook -- the
# Bash payload was the `scp`/`ssh` command line, the posting call lived in the
# FILE the hook never opened (odoo-erp #4650, montalu PROD thread 283, 16.9.,
# mail.message 1847975, gk driver send_handover_283_v5.py). Now a ship-a-local-
# file command has its local `.py` operands resolved (against `.cwd`, `~`
# expanded), and any readable driver that posts is run through the SAME checks
# on the file text PLUS a read-back requirement (a driver must read the stored
# mail.message body and assert on it -- heuristic: `mail.message` AND `assert`
# after the posting call). The read-back requirement is scoped to DRIVER-shaped
# content (a connection idiom present) so product code / module methods are not
# false-positived. Unreadable/absent file, no `.py` operand, or a non-ship
# command => fail-open.
#
# The Stop-side evidence half stays in stop-check-prose-violations.sh (#916) --
# a REPORT claiming an Odoo post must carry read-back evidence. NOT duplicated.
#
# Incidents: montalu 6.9.2026 (mail.message 1794757-1794759); earlier
# discuss.channel on montalu/miva (odoo-erp #6409); odoo-erp #4650 (16.9.).
#
# Bypass (rare, logged): `airuleset:html-ok` in the payload OR in a shipped
# driver file.
#
# Reads the payload on STDIN (.tool_input.command for Bash, .content for Write,
# .new_string for Edit; .cwd for local-path resolution), exits 2 with the
# reason on STDERR. Fail-open on any unmeasurable state (no jq, empty content).

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0

CONTENT=$(printf '%s' "$INPUT" | jq -r \
    '.tool_input.command // .tool_input.content // .tool_input.new_string // empty' \
    2>/dev/null || echo "")
# The raw Bash command line (empty for Write/Edit) -- decides Path A (inline)
# vs Path B (a Bash ship-a-file shape whose command line lacks message_post).
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
# The tool's cwd -- relative local paths in a ship command resolve against it.
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

[ -n "$CONTENT" ] || exit 0

# Bypass marker in the payload text (command or content) short-circuits both
# paths.
case "$CONTENT" in
    *airuleset:html-ok*)
        LOG="/tmp/airuleset-html-ok-bypass-${EUID:-$(id -u)}.log"
        { echo "$(date -Iseconds)  html-ok-bypass" >> "$LOG"; } 2>/dev/null || true
        exit 0
        ;;
esac

# Escaped-tag pattern (#1054 double-escape): an HTML tag that has ALREADY been
# entity-escaped -- `&lt;p&gt;`, `&lt;a href=...`, `&lt;br/&gt;`, `&lt;/p&gt;`.
# The follow-set (`&`, space, `/`, end) keeps `&lt; y` (a literal less-than)
# and `&lt;abbr...` (a non-listed tag) from matching.
ESC_TAG_RE='&lt;/?(p|br|div|span|a|b|strong|em|ul|ol|li|table|tr|td|th|h[1-6]|img)(&|/|[[:space:]]|$)'
# Raw HTML tag pattern (unchanged from #915).
RAW_TAG_RE='</?[a-zA-Z][a-zA-Z0-9]*(\s|/|>)'

# =========================================================================== #
# Path A -- the payload text itself carries the posting call.
# =========================================================================== #
if printf '%s' "$CONTENT" | grep -q 'message_post'; then

    # (#1054) Double-escape: an already-escaped tag => BLOCK, before the
    # body_is_html early-exit, so the flag cannot whitewash it.
    if printf '%s' "$CONTENT" | grep -qE "$ESC_TAG_RE"; then
        cat >&2 <<'MSG'

🚫 BLOCKED: the Odoo message body is ALREADY HTML-escaped -- double wrapping (airuleset #1054).

The payload's `message_post` body contains an entity-escaped tag such as
`&lt;p&gt;` / `&lt;a ...` / `&lt;br&gt;`. With `body_is_html=True` the flag would
whitewash it and Odoo would show the client raw `<p>` tags; without it Odoo
escapes it a second time. Either way the client sees literal tags.

This is the 3rd incident of this class (odoo-erp #4650, montalu PROD thread 283,
16.9.2026, mail.message 1847975).

FIX: send SUROVÉ (un-escaped) HTML with `body_is_html=True`, e.g.
`message_post(body="<p>Dobrý deň</p>", body_is_html=True)` -- never a body that
already contains `&lt;`/`&gt;`. Then read back and verify 0 escaped (#916).
Bypass (rare, logged): put airuleset:html-ok in the content.
MSG
        exit 2
    fi

    # No raw HTML tags => plain text, nothing to guard.
    if ! printf '%s' "$CONTENT" | grep -qE "$RAW_TAG_RE"; then
        exit 0
    fi

    # HTML tags present. body_is_html or subtype_xmlid=mail.mt_comment => OK.
    if printf '%s' "$CONTENT" | grep -q 'body_is_html'; then
        FLAGGED=1
    elif printf '%s' "$CONTENT" | grep -q 'subtype_xmlid.*mail\.mt_comment'; then
        FLAGGED=1
    else
        FLAGGED=0
    fi

    if [ "$FLAGGED" = "0" ]; then
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
    fi

    # HTML + flag present. (#1054) A Write/Edit of a DRIVER-shaped .py that
    # posts HTML but never reads the stored body back and asserts on it is an
    # ad-hoc driver -- BLOCK. Scoped to Write/Edit (CMD empty) and driver-shaped
    # content, so an inline Bash post (unchanged #915) and product-code module
    # methods are untouched.
    if [ -z "$CMD" ]; then
        if HOOK_TEXT="$CONTENT" python3 - <<'PY'
import os, re, sys
text = os.environ.get("HOOK_TEXT", "")
DRIVER_RE = re.compile(r"execute_kw|odoo\s+shell|xmlrpc|ServerProxy|odoorpc|__name__\s*==")
if not DRIVER_RE.search(text):
    sys.exit(0)          # not a driver -> no read-back requirement
idx = text.rfind("message_post")
tail = text[idx:] if idx != -1 else text
if "mail.message" in tail and "assert" in tail:
    sys.exit(0)          # read-back present
sys.exit(3)              # driver, no read-back -> caller blocks
PY
        then
            :
        else
            rc=$?
            if [ "$rc" = "3" ]; then
                cat >&2 <<'MSG'

🚫 BLOCKED: an Odoo driver posts HTML but has no read-back assert (airuleset #1054).

This `.py` calls `message_post` with `body_is_html=True` but never reads the
stored `mail.message` body back and asserts on it. The 3rd escaped-HTML
incident (odoo-erp #4650) shipped exactly this way: a driver that "posted OK"
while the client saw raw tags.

FIX: after posting, read the stored body and assert it, e.g.

  stored = models.execute_kw(db, uid, pwd, "mail.message", "read",
      [[mid]], {"fields": ["body"]})[0]["body"]
  assert stored.startswith("<p>") and "&lt;" not in stored

For posting on a stream's behalf, use the stream-approved poster script (it
carries the read-back). Bypass (rare, logged): airuleset:html-ok in the file.
MSG
                exit 2
            fi
            # rc != 3 (e.g. no python3) -> fail-open.
        fi
    fi
    exit 0
fi

# =========================================================================== #
# Path B -- a Bash ship-a-local-file command (its command line lacks
# message_post; the posting call lives in a shipped/piped .py file).
# =========================================================================== #
if [ -n "$CMD" ]; then
    command -v python3 &>/dev/null || exit 0   # fail-open without python
    REASON=$(HOOK_CMD="$CMD" HOOK_CWD="$CWD" \
             HOOK_ESC_RE="$ESC_TAG_RE" HOOK_RAW_RE="$RAW_TAG_RE" \
             python3 - <<'PY'
import os, re, shlex, sys

cmd = os.environ.get("HOOK_CMD", "")
cwd = os.environ.get("HOOK_CWD", "") or os.getcwd()

# Ship-a-local-file shapes: a transfer (scp/rsync), a stdin redirect of a .py
# (ssh ... < driver.py / docker compose ... odoo shell < driver.py /
# ssh host python3 - < driver.py), or a cat-pipe (cat driver.py | ssh ...).
is_ship = (
    re.search(r"\b(scp|rsync)\b", cmd)
    or re.search(r"<\s*[^\s|;&<>]+\.py\b", cmd)
    or re.search(r"\bcat\s+[^\s|;&<>]+\.py\b\s*\|", cmd)
)
if not is_ship:
    sys.exit(0)          # not a ship shape -> fail open

# Collect candidate local .py operands.
pys = []
try:
    for t in shlex.split(cmd):
        if t.endswith(".py"):
            pys.append(t)
except ValueError:
    pass
for m in re.finditer(r"<\s*([^\s|;&<>]+\.py)\b", cmd):
    pys.append(m.group(1))
for m in re.finditer(r"\bcat\s+([^\s|;&<>]+\.py)\b", cmd):
    pys.append(m.group(1))

ESC_RE = re.compile(r"&lt;/?(p|br|div|span|a|b|strong|em|ul|ol|li|table|tr|td|th|h[1-6]|img)(&|/|\s|$)")
RAW_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s|/|>)")
DRIVER_RE = re.compile(r"execute_kw|odoo\s+shell|xmlrpc|ServerProxy|odoorpc|__name__\s*==")

seen = set()
for p in pys:
    if p in seen:
        continue
    seen.add(p)
    # A remote operand like `gk:/tmp/driver.py` is not a local path.
    if re.match(r"^[A-Za-z0-9_.-]+@?[A-Za-z0-9_.-]*:", p):
        continue
    path = os.path.expanduser(p)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        continue          # unreadable/absent -> fail open on this operand
    if "message_post" not in body:
        continue          # not a posting driver
    if "airuleset:html-ok" in body:
        continue          # in-file bypass (logged by the caller side)
    if ESC_RE.search(body):
        print("DOUBLE_ESCAPE\t" + p)
        sys.exit(2)
    if not RAW_RE.search(body):
        continue          # plain-text post -> nothing to guard
    if not ("body_is_html" in body
            or re.search(r"subtype_xmlid.*mail\.mt_comment", body)):
        print("MISSING_FLAG\t" + p)
        sys.exit(2)
    # HTML + flag. Read-back required for a DRIVER-shaped file.
    if DRIVER_RE.search(body):
        idx = body.rfind("message_post")
        tail = body[idx:] if idx != -1 else body
        if not ("mail.message" in tail and "assert" in tail):
            print("READBACK\t" + p)
            sys.exit(2)
sys.exit(0)
PY
    ) && SHIP_RC=0 || SHIP_RC=$?

    if [ "${SHIP_RC:-0}" = "2" ]; then
        KIND=${REASON%%$'\t'*}
        FILE=${REASON#*$'\t'}
        case "$KIND" in
            DOUBLE_ESCAPE)
                cat >&2 <<MSG

🚫 BLOCKED: a shipped Odoo driver ($FILE) has an ALREADY HTML-escaped body -- double wrapping (airuleset #1054).

The local driver you are shipping/piping posts a body containing an escaped tag
(`&lt;p&gt;` / `&lt;a` / `&lt;br`). Odoo will show the client raw tags. Send
SUROVÉ HTML with body_is_html=True and read the stored body back (#916). This is
the odoo-erp #4650 class -- a driver moved with scp + run via `odoo shell <`.
Bypass (rare, logged): airuleset:html-ok in the file.
MSG
                exit 2
                ;;
            MISSING_FLAG)
                cat >&2 <<MSG

🚫 BLOCKED: a shipped Odoo driver ($FILE) posts HTML but lacks body_is_html (airuleset #915/#1054).

The local driver you are shipping/piping calls `message_post` with HTML content
but no `body_is_html=True` -- Odoo would escape it and the client would see raw
tags. This is the shipped-file bypass (odoo-erp #4650): the posting call lives
in the FILE, not the command line, so the hook opened the file.

FIX: add `body_is_html=True` in the driver, then read the stored body back and
assert 0 escaped (#916). For a stream, use the stream-approved poster script.
Bypass (rare, logged): airuleset:html-ok in the file.
MSG
                exit 2
                ;;
            READBACK)
                cat >&2 <<MSG

🚫 BLOCKED: a shipped Odoo driver ($FILE) posts HTML but has no read-back assert (airuleset #1054).

The local driver you are shipping/piping posts with `body_is_html=True` but
never reads the stored `mail.message` body back and asserts on it. The 3rd
escaped-HTML incident (odoo-erp #4650) shipped exactly this way -- a driver that
"posted OK" while the client saw raw tags.

FIX: after posting, read the stored body and assert, e.g.
  stored = models.execute_kw(db, uid, pwd, "mail.message", "read",
      [[mid]], {"fields": ["body"]})[0]["body"]
  assert stored.startswith("<p>") and "&lt;" not in stored
The gatekeeper box NEVER posts through an ad-hoc driver: use the stream-approved
poster script (it carries the read-back). Bypass (rare, logged):
airuleset:html-ok in the file.
MSG
                exit 2
                ;;
        esac
    fi
fi

exit 0
