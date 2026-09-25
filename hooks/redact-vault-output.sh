#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (every tool — the empty matcher) — issue #1153 part (c).
#
# A credential-store value (`~/.claude/secrets/<NAME>.secret`) — or, since
# slice 2, the value of a regular file directly under the plain key-file root
# (`~/.secrets/<name>`, `*.pub` excluded) — that reaches ANY tool's output — a verbose curl, a config dump, a log file Read, a child of
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
# (telemetry still sees the original; a key file outside the two roots is not
# known; a deliberately transformed value is not recognised).
#
# Cost: when the store holds no value and there is no plain key file, this
# exits after builtin globs, without starting python. A box that keeps key
# files under ~/.secrets pays one python start per tool call: ~60 ms median
# with 20 files and a 20 KB output, 2 ms with none (measured on the slice-2
# lane); the redactor reads at most MAX_PLAIN_FILES files. Fail-OPEN by
# design (a PostToolUse hook has nothing left to block): any malfunction exits
# 1 with a one-line, value-free reason on stderr and the original output
# passes through.
#
# Dry run: printf '%s' '{"tool_name":"Bash","tool_response":{"stdout":"x",
#   "stderr":"","interrupted":false,"isImage":false}}' | bash hooks/redact-vault-output.sh

has_values() {
    [ -n "$1" ] && compgen -G "$1/*.secret" >/dev/null 2>&1
}

# Slice 2: a regular, non-`.pub` file directly under the plain key-file root
# is a needle too (cli_vault_keyfile.plain_root_values applies the full rules;
# this is only the cheap "is there anything at all" test, builtins only).
has_plain_keys() {
    local root="${HOME:-}/.secrets" f
    { [ -n "${HOME:-}" ] && [ -d "$root" ] && [ ! -L "$root" ]; } || return 1
    # Dotfiles too: the loader reads them (review B finding 3).
    for f in "$root"/* "$root"/.[!.]* "$root"/..?*; do
        if [ -f "$f" ] && [ ! -L "$f" ] && [ "${f%.pub}" = "$f" ]; then
            return 0
        fi
    done
    return 1
}

# The default store, plus the test-only relocation (filedrop/vault.py honours
# it only under the system temp dir; checking BOTH here means a rejected
# override can never hide the real store from this precheck).
if ! has_values "${HOME:-}/.claude/secrets" && ! has_values "${AIRULESET_SECRETS_DIR:-}" \
        && ! has_plain_keys; then
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
