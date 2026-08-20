#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash|Write|Edit) -- #596 + #597, client Discuss thread naming.
#
# A sub-dev stream (montaluN / davidN / simapN / mivaN) creating a client Odoo
# `discuss.channel` (a top-level channel or a `parent_channel_id` sub-thread)
# MUST name it so the name ENDS with the stream number ("... N", #596) and is
# <= 30 CHARACTERS incl. that number (#597, so it isn't truncated behind the
# Odoo Discuss sidebar's first page, hiding the very number #596 shows). The
# prose rule (handover-compose.md #532/#537/#598) failed TWICE on montalu2 PROD;
# the owner escalated to a hook ("nemal dovolene robit taku chybu").
#
# SCOPE (by DERIVATION, not an authority flag): the stream number comes from the
# unix user via cli_aliases.stream_number -- a NON-stream user (owner /
# gatekeeper / marek / unknown) derives None and the guard stays SILENT. So the
# guard is active only for a numbered/base stream user, exactly as the ticket
# specifies. FAIL-SAFE: a `message_post` to an EXISTING channel is NOT a create
# and is never blocked; a `write` (rename) is NOT a create -- the name-
# correction path must stay possible.
#
# All create-detection / name-extraction / compliance / number-derivation live
# in the importable `discuss_thread_guard.py` (+ `cli_aliases.stream_number`),
# the design_gate.py / block-commit-without-design.sh module+thin-hook split.
# Reads the payload on STDIN (`.tool_input.command` for Bash, `.content` for
# Write, `.new_string` for Edit), exits 2 with the reason on STDERR (stdout is
# invisible to the model). Fail-open on any unmeasurable state (no jq/python3,
# spawn error) -- the prose rule + review are the backstop.
#
# Bypass (rare, logged): `airuleset:discuss-name-ok` anywhere in the content
# (a comment / token) -- for a genuine legacy thread the owner already accepted.
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
if g.has_bypass_marker(content):
    print("BYPASS")
    sys.exit(0)
v = g.evaluate(content, user)
if v:
    print("HIT")
    print(v.number)
    print(" | ".join(v.names))
    print(v.suggestion)
PYEOF
)

LINE1=$(printf '%s\n' "$OUT" | sed -n '1p')

if [ "$LINE1" = "BYPASS" ]; then
    LOG="/tmp/airuleset-discuss-name-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  bypass: ${STREAM_USER}" >> "$LOG"; } 2>/dev/null || true
    exit 0
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
