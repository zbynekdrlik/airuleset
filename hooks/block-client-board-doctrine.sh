#!/usr/bin/env bash
set -euo pipefail

# airuleset:html-ok  -- this hook's own source: a dry-run example below names
# message_post as documentation only; it never posts to Odoo, so the #915
# body_is_html hook must not fire on writing/editing this file.
#
# Hook: PreToolUse(Bash|Write|Edit) -- THIN ADAPTER (#1014 / #1018 / #1024
# client-board doctrine enforcement). All logic lives in gates/clientbody.py:
#
#   CHECK A -- client-facing BODY jargon gate: blocks github.com links, bare
#     #NNNN issue refs, and PR/commit/branch/RFR/gk/hand-off/CI/merge/worktree
#     jargon in a client-facing message body (message_post / mail.message.write
#     / odoo_post / odoo-task-sync.py post-message). It scans ONLY the extracted
#     message body (never the surrounding posting code, so env.cr.commit() is
#     never read as jargon), allowlists the sanctioned "GitHub ticket: #N" marker
#     + "(GitHub #N)" trailer, and FAILS OPEN when no body is extractable -- a
#     gate that cannot classify a client message never blocks it. Bypass:
#     airuleset:client-body-ok in the content.
#
#   CHECK B -- per-stream client-board memory-write guard: refuses a NEW
#     ~/.claude/projects/*/memory/*.md whose SUBJECT is client-board chatter
#     doctrine, pointing to client-board-tasks.md + the gk-request relay (owner
#     corrections change the RULE, not a per-stream memory -- #1014/#1028).
#     Bypass: airuleset:client-board-memory-ok in the content.
#
# The module writes its block reason to BOTH stdout and stderr (gates.emit_block)
# and exits 2. This adapter treats ONLY exit 2 as a block; any other exit (a
# python malfunction) FAILS OPEN (exit 0) -- the same fail-open contract every
# gate in this family uses, so a broken speculative check never stalls a stream.
#
# Reads the payload on STDIN (.tool_input.command for Bash, .content for Write,
# .new_string for Edit; .tool_input.file_path for the memory guard).
# Dry-run: printf '%s' '{"tool_input":{"command":"odoo-task-sync.py post-message 42 \"detaily v #7110\""}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
command -v python3 &>/dev/null || exit 0
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.clientbody <<<"$PAYLOAD" || RC=$?
[ "$RC" -eq 2 ] && exit 2
exit 0
