#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (every tool — the empty matcher) — issue #1153 part (c).
#
# A credential-store value (`~/.claude/secrets/<NAME>.secret`) that reaches ANY
# tool's output — a verbose curl, a config dump, a log file Read, a child of
# `secret exec` that wrote it somewhere and a later `cat` — is replaced with
# `<<REDACTED>>` before Claude sees it AND before the session jsonl stores it.
# Claude Code's PostToolUse `updatedToolOutput` does this for built-in tools
# too; verified 2026-09-25 on Claude Code 2.1.281 that the transcript keeps
# the replacement, not the original (ticket comment 5828264462).
#
# Division of labour with block-vault-store-read.sh: that hook REFUSES the
# reflexive reads of either credential root before they run; this one catches
# a stored value that reaches output by any route the refusal cannot see (a
# computed path, a script, a side channel). The matcher lives in
# hooks/vault_output_redact.py — its docstring states the honest limits
# (telemetry still sees the original; only STORE values are known; a
# deliberately transformed value is not recognised).
#
# Cost: when the store holds no value — the normal state, values live <= 24 h
# — this exits after ONE builtin glob, without starting python. Fail-OPEN by
# design (a PostToolUse hook has nothing left to block): any malfunction exits
# 1 with a one-line, value-free reason on stderr and the original output
# passes through.
#
# Dry run: printf '%s' '{"tool_name":"Bash","tool_response":{"stdout":"x",
#   "stderr":"","interrupted":false,"isImage":false}}' | bash hooks/redact-vault-output.sh

has_values() {
    [ -n "$1" ] && compgen -G "$1/*.secret" >/dev/null 2>&1
}

# The default store, plus the test-only relocation (filedrop/vault.py honours
# it only under the system temp dir; checking BOTH here means a rejected
# override can never hide the real store from this precheck).
if ! has_values "${HOME:-}/.claude/secrets" &&! has_values "${AIRULESET_SECRETS_DIR:-}"; then
    cat >/dev/null 2>&1 || true
    exit 0
fi

HOOK_SRC="${BASH_SOURCE[0]}"
case "$HOOK_SRC" in
    */*) REDACTOR="${HOOK_SRC%/*}/vault_output_redact.py" ;;
    *)   REDACTOR="./vault_output_redact.py" ;;
esac
if [ ! -r "$REDACTOR" ]; then
    echo "redact-vault-output: $REDACTOR is missing — output NOT checked" >&2
    cat >/dev/null 2>&1 || true
    exit 1
fi
exec python3 "$REDACTOR"
