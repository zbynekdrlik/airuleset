#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop -- the owner writes Slovak, so the final message is Slovak (#1379).
#
# Owner 20.11.2026: devbridge answered the owner's Slovak prompts in English for
# hours; the always-on rule covered only questions and no Stop hook read the
# language of the final message. gates.ownerlang blocks (exit 2) when the newest
# owner prompt is Slovak and the final message's prose (code, inline code, URLs,
# paths and `> ` quotes stripped) is mainly English. It blocks at most once per
# owner prompt (a continuation after a Stop-hook block is never judged), so it
# can never wedge a session.
#
# THIN ADAPTER (gate family #1020): the Stop payload goes straight to
# `python3 -P -m gates.ownerlang` on stdin (PYTHONPATH from the hook dir, -P so
# a stale lane checkout in the cwd never shadows the module, #1046). The module
# is fail-open on every error; a missing python3 here also exits 0. A block is
# also recorded in the unified hook-blocks log.
# Dry-run: echo '{"last_assistant_message":"...","transcript_path":"..."}' | bash <this hook>

_OL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
_OL_ROOT="$(dirname "$_OL_DIR")"

_OL_RC=0
env PYTHONPATH="${_OL_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.ownerlang 1>&2 || _OL_RC=$?

if [ "$_OL_RC" -eq 2 ]; then
    _OL_LOG_LIB="${_OL_DIR}/lib_hook_block_log.sh"
    # shellcheck source=/dev/null
    [ -r "$_OL_LOG_LIB" ] && . "$_OL_LOG_LIB"
    type log_hook_block >/dev/null 2>&1 \
        && log_hook_block "stop-check-owner-language" "english reply to a slovak owner prompt"
    exit 2
fi

exit 0
