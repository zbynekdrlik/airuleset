#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Blocks staging of sensitive files (TARGETS.md, .env, credentials).
# Exit code 2 = block the tool call.

# TOOL_INPUT is provided by Claude Code as an environment variable
INPUT="${TOOL_INPUT:-}"

# Only check commands that look like git add
if ! echo "$INPUT" | grep -q "git add"; then
    exit 0
fi

# List of sensitive file patterns to block
SENSITIVE_PATTERNS=(
    "TARGETS.md"
    ".env"
    "credentials"
    "secrets"
    "*.pem"
    "*.key"
    "*.p12"
)

for pattern in "${SENSITIVE_PATTERNS[@]}"; do
    if echo "$INPUT" | grep -qi "$pattern"; then
        echo "BLOCKED: Refusing to stage sensitive file matching '$pattern'."
        echo "If you need to stage this file, do it manually outside of Claude Code."
        exit 2
    fi
done

exit 0
