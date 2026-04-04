#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# BLOCKS git push if feature code changed but no test files were modified.
# Bypass: include [no-test] in the latest commit message.
# Exit code 2 = block the tool call.

INPUT="${TOOL_INPUT:-}"

# Only act on git push commands
if ! echo "$INPUT" | grep -qE 'git\s+push'; then
    exit 0
fi

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Check if latest commit has [no-test] bypass
LAST_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
if echo "$LAST_MSG" | grep -q '\[no-test\]'; then
    exit 0
fi

# Detect default branch
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")

# Get files changed compared to default branch (or last commit if no remote)
CHANGED_FILES=$(git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD" 2>/dev/null || git diff --name-only HEAD~1 2>/dev/null || echo "")

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# Feature code files (these REQUIRE test changes)
FEATURE_CHANGES=$(echo "$CHANGED_FILES" | grep -E '\.(rs|ts|tsx|js|jsx|py)$' | grep -vE '(test|spec|e2e|playwright|_test\.|\.test\.)' || echo "")

# Test/E2E files
TEST_CHANGES=$(echo "$CHANGED_FILES" | grep -iE '(test|spec|e2e|playwright)' || echo "")

if [ -n "$FEATURE_CHANGES" ] && [ -z "$TEST_CHANGES" ]; then
    echo ""
    echo "🚫 BLOCKED: Feature code changed but NO test files modified."
    echo ""
    echo "  Changed feature files:"
    echo "$FEATURE_CHANGES" | head -10 | sed 's/^/    /'
    echo ""
    echo "  Test files modified: NONE"
    echo ""
    echo "  Every feature/fix MUST have a corresponding test."
    echo "  If this is genuinely a config-only change, add [no-test] to your commit message."
    echo ""
    echo "  To fix: write a Playwright E2E test or unit test for your changes."
    echo ""
    exit 2
fi

exit 0
