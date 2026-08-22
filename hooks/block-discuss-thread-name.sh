#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash|Write|Edit) -- #596 + #597 client Discuss thread naming,
# AND #609 client Discuss message SIGNATURE.
#
# A sub-dev stream (montaluN / davidN / simapN / mivaN) creating a client Odoo
# `discuss.channel` (a top-level channel or a `parent_channel_id` sub-thread)
# MUST name it so the name ENDS with the stream number ("... N", #596) and is
# <= 30 CHARACTERS incl. that number (#597, so it isn't truncated behind the
# Odoo Discuss sidebar's first page, hiding the very number #596 shows). The
# prose rule (handover-compose.md #532/#537/#598) failed TWICE on montalu2 PROD;
# the owner escalated to a hook ("nemal dovolene robit taku chybu").
#
# #609 adds a SIBLING check on the SAME payload: a sub-dev stream `message_post`
# to a `discuss.channel` MUST carry the mandatory `ZbynekAI <N>` stream signature
# (#598) somewhere in the posting content, else it is BLOCKED. This closes the
# hole that let montalu6 post an UNSIGNED client message from odoo-erp's own
# `discuss-client-posting` skill, which never carried the prose rule -- the guard
# scans the actual tool-call content, so it fires regardless of which skill the
# stream loaded. A `create` / `write`/rename is a DIFFERENT method and is never
# touched by the signature check (it invokes its own #596/#597 name check).
#
# SCOPE (by DERIVATION, not an authority flag): the stream number comes from the
# unix user via cli_aliases.stream_number -- a NON-stream user (owner /
# gatekeeper / marek / unknown) derives None and the guard stays SILENT. So the
# guard is active only for a numbered/base stream user, exactly as the ticket
# specifies. FAIL-SAFE (NAME check): a `write` (rename) is NOT a create -- the
# name-correction path must stay possible; a `message_post` is NOT a create so
# the NAME check never fires on it -- but #609 runs its own SIGNATURE check on a
# message_post (a create/write is a DIFFERENT method and the signature check
# never fires on those).
#
# All create/message_post-detection, name-extraction, compliance, signature and
# number-derivation live in the importable `discuss_thread_guard.py` (+
# `cli_aliases.stream_number`), the design_gate.py / block-commit-without-design.sh
# module+thin-hook split. Reads the payload on STDIN (`.tool_input.command` for
# Bash, `.content` for Write, `.new_string` for Edit), exits 2 with the reason on
# STDERR (stdout is invisible to the model). Fail-open on any unmeasurable state
# (no jq/python3, spawn error) -- the prose rule + review are the backstop.
#
# Bypass (rare, logged): `airuleset:discuss-name-ok` waives the NAME check (a
# legacy thread the owner accepted); `airuleset:discuss-sig-ok` waives the
# SIGNATURE check (a genuine internal/legacy post the owner accepts unsigned).
#
# Test seam: AIRULESET_DISCUSS_STREAM_USER overrides the derived unix user (the
# stream identity is the unix account, not derivable from cwd/payload).

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

# The content to scan: a Bash command, a Write's full file content, or an Edit's
# inserted text -- a given call carries exactly one, so the `//` chain picks it.
CONTENT=$(printf '%s' "$INPUT" | jq -r \
    '.tool_input.command // .tool_input.content // .tool_input.new_string // empty' \
    2>/dev/null || echo "")
[ -n "$CONTENT" ] || exit 0

# Cheap pre-filter: only content that mentions the model at all can be a create.
case "$CONTENT" in
    *discuss.channel*) ;;
    *) exit 0 ;;
esac

# The stream identity is the unix USER (whoami) -- overridable for tests.
STREAM_USER="${AIRULESET_DISCUSS_STREAM_USER:-}"
[ -n "$STREAM_USER" ] || STREAM_USER=$(id -un 2>/dev/null || whoami 2>/dev/null || echo "")

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into `python3 -`'s own stdin (this repo's own
# recurring trap). Output protocol: "BYPASS" | "HIT"+number+offending+suggestion.
OUT=$(python3 - "$REPO_ROOT" "$STREAM_USER" "$CONTENT" <<'PYEOF' 2>/dev/null || true
import sys
repo_root, user, content = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo_root)
try:
    import discuss_thread_guard as g
except Exception:
    sys.exit(0)
name_bypassed = g.has_bypass_marker(content)
sig_bypassed = g.has_sig_bypass_marker(content)
approval_bypassed = g.has_approval_bypass_marker(content)
# #596/#597 create-NAME check -- skipped when the name is deliberately bypassed.
if not name_bypassed:
    v = g.evaluate(content, user)
    if v:
        print("HIT")
        print(v.number)
        print(" | ".join(v.names))
        print(v.suggestion)
        sys.exit(0)
# #609 message_post STREAM-SIGNATURE check -- skipped when sig-bypassed. Runs
# even if the name was bypassed: a legacy-NAME create does not waive the SIGNATURE.
if not sig_bypassed:
    mv = g.evaluate_message_post(content, user)
    if mv:
        print("HITSIG")
        print(mv.number)
        print(mv.expected)
        sys.exit(0)
# #628 message_post OWNER-APPROVAL check -- skipped only when approval-bypassed.
# Runs even if the name OR the signature was bypassed: neither of those waives
# the owner-approval requirement (checked AFTER the signature so an unsigned
# post still reports the #609 signature issue first, preserving that behaviour).
if not approval_bypassed:
    av = g.evaluate_message_post_approval(content, user)
    if av:
        print("HITAPPROVAL")
        print(av.number)
        sys.exit(0)
# nothing blocked -- surface a bypass for logging.
if name_bypassed:
    print("BYPASS")
elif sig_bypassed:
    print("SIGBYPASS")
elif approval_bypassed:
    print("APPROVALBYPASS")
PYEOF
)

LINE1=$(printf '%s\n' "$OUT" | sed -n '1p')

if [ "$LINE1" = "BYPASS" ]; then
    LOG="/tmp/airuleset-discuss-name-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  name-bypass: ${STREAM_USER}" >> "$LOG"; } 2>/dev/null || true
    exit 0
fi

if [ "$LINE1" = "SIGBYPASS" ]; then
    LOG="/tmp/airuleset-discuss-sig-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  sig-bypass: ${STREAM_USER}" >> "$LOG"; } 2>/dev/null || true
    exit 0
fi

if [ "$LINE1" = "APPROVALBYPASS" ]; then
    LOG="/tmp/airuleset-discuss-approval-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  approval-bypass: ${STREAM_USER}" >> "$LOG"; } 2>/dev/null || true
    exit 0
fi

# #628 -- a discuss.channel message_post by a sub-dev stream carrying no
# owner-approval evidence marker.
if [ "$LINE1" = "HITAPPROVAL" ]; then
    NUMBER=$(printf '%s\n' "$OUT" | sed -n '2p')
    cat >&2 <<MSG

🚫 BLOCKED: a client Discuss message has no owner-approval evidence (airuleset #628).

You are sub-dev stream number "${NUMBER}". The owner must APPROVE the exact text
of EVERY client-facing Discuss message BEFORE it is posted -- the OPENING message
AND every follow-up reply / question / reminder into an existing thread, without
exception (owner ruling after montalu6 posted an unapproved message to a live
client thread on PROD, 2026-08-22, thread 283 -- it was deleted within a minute
but the notification had already reached the client, irreversible).

The message_post being sent carries no owner-approval evidence. AFTER the owner
approves the text, record a FALSIFIABLE claim (a reference to HOW/WHEN they
approved this exact text -- never a bare "approved") in the tool-call content and
re-run:

  • airuleset:owner-approved <ref>
    e.g.  # airuleset:owner-approved owner odsúhlasil znenie 2026-08-22 na Discorde

This is the same logged-falsifiable-claim model as Discuss-closed: /
Self-service-checked: -- a bare "airuleset:owner-approved" with no reference is
NOT accepted. How to compose + present the message to the owner for approval
(complete proposal in the chat, per-person address register, react to the
client's previous answer first): skills/odoo-discuss-xmlrpc/handover-compose.md.

This gates only a discuss.channel message_post by a sub-dev stream -- a create /
rename and a non-stream user are never affected. Bypass (rare, logged, only for
a genuine internal / non-client post that needs no owner approval): put
airuleset:discuss-approval-ok in the content.
MSG
    exit 2
fi

# #609 -- a discuss.channel message_post missing its `ZbynekAI <N>` signature.
if [ "$LINE1" = "HITSIG" ]; then
    NUMBER=$(printf '%s\n' "$OUT" | sed -n '2p')
    EXPECTED=$(printf '%s\n' "$OUT" | sed -n '3p')
    cat >&2 <<MSG

🚫 BLOCKED: a client Discuss message is missing its stream signature (airuleset #598/#609).

You are sub-dev stream number "${NUMBER}". EVERY Odoo Discuss message a sub-dev
stream posts MUST end with a stream-identity signature line, so the owner sees
at a glance WHICH subdev sent it and where to go resolve what the thread is about:

  • the LAST line of the message body is:  ${EXPECTED}

The message_post being sent carries no such signature (or a wrong number). Add
"${EXPECTED}" as the final line of the body and re-run.

Why: the shared sender name "zbynekai odovzdavac" tells the owner nothing about
which subdev owns a thread (owner request, #598); montalu6 shipped an UNSIGNED
message to a client because the rule was only in a skill the stream never loaded
(#609). The canonical rule + a copy-paste template are in
skills/odoo-discuss-xmlrpc/handover-compose.md.

This gates only a discuss.channel message_post by a sub-dev stream — a create /
rename and a non-stream user are never affected. Bypass (rare, logged, only for
a genuine internal/legacy post the owner accepts unsigned): put
airuleset:discuss-sig-ok in the content.
MSG
    exit 2
fi

[ "$LINE1" = "HIT" ] || exit 0
NUMBER=$(printf '%s\n' "$OUT" | sed -n '2p')
OFFENDING=$(printf '%s\n' "$OUT" | sed -n '3p')
SUGGESTION=$(printf '%s\n' "$OUT" | sed -n '4p')

cat >&2 <<MSG

🚫 BLOCKED: a client Discuss thread name is not compliant (airuleset #596/#597).

You are sub-dev stream number "${NUMBER}". A client Odoo discuss.channel you
create MUST have a name that:
  • ENDS with your stream number as a trailing token — "… ${NUMBER}"  (#596), and
  • is at most 30 CHARACTERS including that number                     (#597).

Non-compliant name(s) found: ${OFFENDING}

Use a compliant name, e.g.:  ${SUGGESTION}

Why: without the trailing number the owner cannot tell which subdev owns the
thread (montalu2 shipped the un-numbered form on PROD twice); a too-long name is
truncated behind the Odoo Discuss sidebar's first page, hiding the number.
The owner-approval ping must already carry a compliant proposed name — see
skills/odoo-discuss-xmlrpc/handover-compose.md.

This gates only a CREATE. A message_post to an EXISTING channel and a rename
(write) are never blocked. Bypass (rare, logged, only for a legacy thread the
owner already accepted): put  airuleset:discuss-name-ok  in the content.
MSG
exit 2
