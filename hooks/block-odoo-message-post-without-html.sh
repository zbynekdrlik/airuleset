#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash|Write|Edit) -- #915 Odoo message_post without
# body_is_html, EXTENDED by #1054 to close two live bypasses.
#
# #915 core: when a tool-call payload contains `message_post` AND HTML tags but
# LACKS `body_is_html`, BLOCK -- the omission makes Odoo escape the HTML so
# clients see raw `<p>`, `<b>` etc. instead of formatted text.
#
# #1054 gap 1 (double wrapping): a body already entity-escaped passed whenever
# `body_is_html` was present -- the flag whitewashed an escaped body Odoo
# renders as raw tags. Now an escaped TAG anywhere in the payload BLOCKS with a
# dedicated reason, BEFORE the body_is_html early-exit.
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
# after the FIRST posting call). The read-back requirement is scoped to
# DRIVER-shaped content (a connection idiom present) so product code / module
# methods are not false-positived. The ship-file check runs for a Bash command
# BEFORE the inline check, so a driver whose FILENAME or a trailing comment
# happens to contain `message_post` still has its FILE opened (not just the
# command line inspected). Unreadable/absent file, no `.py` operand, or a
# non-ship command => fail-open.
#
# RESIDUAL (documented, not closed): a driver RUN LOCALLY (`python3 driver.py`
# against a client Odoo over xmlrpc, no ssh) or one already resident on the
# remote (`ssh host 'odoo shell < /remote/driver.py'`) is NOT a ship shape /
# not locally readable, so the file is not opened. The real mitigation for
# those is that creating the driver via Write/Edit IS gated (Path A read-back).
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
# The raw Bash command line (empty for Write/Edit) -- decides the ship-file
# check (Bash only) vs the inline read-back scope.
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
# The tool's cwd -- relative local paths in a ship command resolve against it.
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

[ -n "$CONTENT" ] || exit 0

# Bypass marker in the payload text (command or content) short-circuits every
# path.
case "$CONTENT" in
    *airuleset:html-ok*)
        LOG="/tmp/airuleset-html-ok-bypass-${EUID:-$(id -u)}.log"
        { echo "$(date -Iseconds)  html-ok-bypass" >> "$LOG"; } 2>/dev/null || true
        exit 0
        ;;
esac

# Escaped-tag pattern (#1054 double-escape): an HTML tag that has ALREADY been
# entity-escaped -- `&lt;p&gt;`, `&lt;a href=...`, `&lt;br/&gt;`, `&lt;/p&gt;`,
# `&lt;code&gt;`, `&lt;i&gt;`, ... Generic (mirrors the raw detector), so a
# non-common tag is not missed. The follow-set (`&`, `/`, space, end) keeps
# `&lt; y` (a literal less-than) from matching (a letter must follow `&lt;`).
ESC_TAG_RE='&lt;/?[a-zA-Z][a-zA-Z0-9]*(&|/|[[:space:]]|$)'
# Raw HTML tag pattern (unchanged from #915).
RAW_TAG_RE='</?[a-zA-Z][a-zA-Z0-9]*(\s|/|>)'

# =========================================================================== #
# Ship-file check (Bash only) -- FIRST, so a ship command whose command line
# happens to contain `message_post` (a driver filename, a trailing comment)
# still has its FILE opened, not just the command line inspected (#1054 F1).
# The Python resolver returns exit 0 (allow) for a non-ship command, so a
# plain inline Bash post falls straight through to the inline check below.
# =========================================================================== #
if [ -n "$CMD" ] && command -v python3 &>/dev/null; then
    SHIP_RC=0
    REASON=$(HOOK_CMD="$CMD" HOOK_CWD="$CWD" python3 - <<'PY'
import os, re, sys

cmd = os.environ.get("HOOK_CMD", "")
cwd = os.environ.get("HOOK_CWD", "") or os.getcwd()

# Ship-a-local-file shapes: a transfer (scp/rsync/sftp), a stdin redirect of a
# .py (ssh ... < driver.py / docker compose ... odoo shell < driver.py /
# ssh host python3 - < driver.py), or a cat-pipe (cat driver.py | ssh ...).
is_ship = (
    re.search(r"\b(scp|rsync|sftp)\b", cmd)
    or re.search(r"<\s*[^\s|;&<>]+\.py\b", cmd)
    or re.search(r"\bcat\s+[^\s|;&<>]+\.py\b\s*\|", cmd)
)
if not is_ship:
    sys.exit(0)          # not a ship shape -> fail open

# Collect every local .py operand by regex (no shlex: an unbalanced quote or a
# trailing `#` comment must never lose the operand and silently fail-open, #1054
# F3). A remote `host:/path.py` token is collected here and skipped below.
pys = re.findall(r"""[^\s|;&<>'"]+\.py\b""", cmd)

ESC_RE = re.compile(r"&lt;/?[a-zA-Z][a-zA-Z0-9]*(&|/|\s|$)")
RAW_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s|/|>)")
DRIVER_RE = re.compile(r"execute_kw|odoo\s+shell|xmlrpc|ServerProxy|odoorpc")

seen = set()
for p in pys:
    if p in seen:
        continue
    seen.add(p)
    # A remote operand like `gk:/tmp/driver.py` / `user@gk:/x.py` is not local.
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
        continue          # in-file bypass
    if ESC_RE.search(body):
        print("DOUBLE_ESCAPE\t" + p)
        sys.exit(2)
    if not RAW_RE.search(body):
        continue          # plain-text post -> nothing to guard
    if not ("body_is_html" in body
            or re.search(r"subtype_xmlid.*mail\.mt_comment", body)):
        print("MISSING_FLAG\t" + p)
        sys.exit(2)
    # HTML + flag. Read-back required for a DRIVER-shaped file. Anchor the scan
    # to the FIRST post so a trailing `message_post` mention (a comment / print)
    # after a genuine read-back does not re-trigger the block (#1054 F2).
    if DRIVER_RE.search(body):
        idx = body.find("message_post")
        tail = body[idx:] if idx != -1 else body
        if not ("mail.message" in tail and "assert" in tail):
            print("READBACK\t" + p)
            sys.exit(2)
sys.exit(0)
PY
    ) || SHIP_RC=$?
    SHIP_RC=${SHIP_RC:-0}

    if [ "$SHIP_RC" = "2" ]; then
        KIND=${REASON%%$'\t'*}
        FILE=${REASON#*$'\t'}
        case "$KIND" in
            DOUBLE_ESCAPE)
                printf '\n🚫 BLOCKED: a shipped Odoo driver (%s) has an ALREADY HTML-escaped body -- double wrapping (airuleset #1054).\n' "$FILE" >&2
                cat >&2 <<'MSG'
The local driver you are shipping/piping posts a body containing an escaped tag
(such as `&lt;p&gt;` / `&lt;a` / `&lt;br`). Odoo will show the client raw tags.
Send SUROVE (un-escaped) HTML with body_is_html=True and read the stored body
back (#916). This is the odoo-erp #4650 class -- a driver moved with scp + run
via `odoo shell <`. Bypass (rare, logged): airuleset:html-ok in the file.
MSG
                exit 2
                ;;
            MISSING_FLAG)
                printf '\n🚫 BLOCKED: a shipped Odoo driver (%s) posts HTML but lacks body_is_html (airuleset #915/#1054).\n' "$FILE" >&2
                cat >&2 <<'MSG'
The local driver you are shipping/piping calls message_post with HTML content
but no body_is_html=True -- Odoo would escape it and the client would see raw
tags. This is the shipped-file bypass (odoo-erp #4650): the posting call lives
in the FILE, not the command line, so the hook opened the file.

FIX: add body_is_html=True in the driver, then read the stored body back and
assert 0 escaped (#916). For a stream, use the stream-approved poster script.
Bypass (rare, logged): airuleset:html-ok in the file.
MSG
                exit 2
                ;;
            READBACK)
                printf '\n🚫 BLOCKED: a shipped Odoo driver (%s) posts HTML but has no read-back assert (airuleset #1054).\n' "$FILE" >&2
                cat >&2 <<'MSG'
The local driver you are shipping/piping posts with body_is_html=True but never
reads the stored mail.message body back and asserts on it. The 3rd escaped-HTML
incident (odoo-erp #4650) shipped exactly this way -- a driver that "posted OK"
while the client saw raw tags.

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

# =========================================================================== #
# Inline check -- the payload text itself carries the posting call (an inline
# Bash post, or Write/Edit content). Uses here-strings (no pipe) so a large
# payload cannot SIGPIPE grep -q under `set -o pipefail` into a fail-open.
# =========================================================================== #
if grep -q 'message_post' <<<"$CONTENT"; then

    # (#1054) Double-escape: an already-escaped tag => BLOCK, before the
    # body_is_html early-exit, so the flag cannot whitewash it.
    if grep -qE "$ESC_TAG_RE" <<<"$CONTENT"; then
        cat >&2 <<'MSG'

🚫 BLOCKED: the Odoo message body is ALREADY HTML-escaped -- double wrapping (airuleset #1054).

The payload's message_post body contains an entity-escaped tag such as
`&lt;p&gt;` / `&lt;a ...` / `&lt;br&gt;`. With body_is_html=True the flag would
whitewash it and Odoo would show the client raw `<p>` tags; without it Odoo
escapes it a second time. Either way the client sees literal tags.

This is the 3rd incident of this class (odoo-erp #4650, montalu PROD thread 283,
16.9.2026, mail.message 1847975).

FIX: send SUROVE (un-escaped) HTML with body_is_html=True, e.g.
`message_post(body="<p>Dobry den</p>", body_is_html=True)` -- never a body that
already contains `&lt;`/`&gt;`. Then read back and verify 0 escaped (#916).
Bypass (rare, logged): put airuleset:html-ok in the content.
MSG
        exit 2
    fi

    # No raw HTML tags => plain text, nothing to guard.
    if ! grep -qE "$RAW_TAG_RE" <<<"$CONTENT"; then
        exit 0
    fi

    # HTML tags present. body_is_html or subtype_xmlid=mail.mt_comment => OK.
    if grep -q 'body_is_html' <<<"$CONTENT"; then
        FLAGGED=1
    elif grep -q 'subtype_xmlid.*mail\.mt_comment' <<<"$CONTENT"; then
        FLAGGED=1
    else
        FLAGGED=0
    fi

    if [ "$FLAGGED" = "0" ]; then
        cat >&2 <<'MSG'

🚫 BLOCKED: an Odoo message_post contains HTML tags but lacks body_is_html (airuleset #915).

The payload calls message_post with HTML content (<p>, <b>, <br>, etc.) but
does NOT include body_is_html=True. Without this flag, Odoo ESCAPES the HTML
and clients see raw tags like `<p>Dobry den</p>` instead of formatted text.

This happened on montalu PROD on 6.9.2026 (3 project.task comments with raw
HTML tags reaching clients -- mail.message ids 1794757-1794759).

FIX: add body_is_html=True (Python) / "body_is_html": true (JSON) to the
message_post kwargs, then re-run. For example:

  message_post(body="<p>text</p>", body_is_html=True, ...)

Alternatively, set subtype_xmlid="mail.mt_comment" which also implies HTML.

This gates ANY model's message_post (project.task, discuss.channel, sale.order,
...) -- not just discuss.channel. Bypass (rare, logged, only for a genuine
plain-text post that contains literal angle brackets): put airuleset:html-ok
in the content.
MSG
        exit 2
    fi

    # HTML + flag present => OK on the inline path. The read-back requirement is
    # NOT enforced here (at authoring time): a Write/Edit or inline payload with
    # a connection idiom + HTML post is ambiguous (a driver, product-code module
    # method, a DOC discussing driver mechanics, or a test fixture), so a
    # content-based read-back gate over-blocks all of those fleet-wide (it would
    # even block editing THIS doctrine file). The read-back is enforced where a
    # driver is unambiguously a driver being SENT: the ship-file check above
    # (scp / ssh ... odoo shell < driver.py) opens the file and requires it.
    exit 0
fi

exit 0
