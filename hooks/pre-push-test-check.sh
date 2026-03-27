#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Warns if UI/feature code was changed but no test files were modified.
# Does NOT block (exit 0) — just injects a warning into Claude's context.

INPUT="${TOOL_INPUT:-}"

# Only act on git push commands
if ! echo "$INPUT" | grep -qE 'git\s+push'; then
    exit 0
fi

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Get the default branch (main or master)
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")

# Get files changed compared to the default branch
CHANGED_FILES=$(git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD" 2>/dev/null || git diff --name-only HEAD~1 2>/dev/null || echo "")

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# Check if UI/feature code was changed
UI_CHANGES=$(echo "$CHANGED_FILES" | grep -E '\.(rs|ts|tsx|js|jsx|svelte|vue|py)$' | grep -vE '(test|spec|e2e|playwright)' | head -5)

# Check if test files were changed
TEST_CHANGES=$(echo "$CHANGED_FILES" | grep -iE '(test|spec|e2e|playwright)' | head -5)

if [ -n "$UI_CHANGES" ] && [ -z "$TEST_CHANGES" ]; then
    echo ""
    echo "⚠️ TEST CHECK: You modified feature code but NO test files:"
    echo "  Changed: $(echo "$UI_CHANGES" | tr '\n' ', ' | sed 's/,$//')"
    echo "  Tests:   NONE modified"
    echo ""
    echo "  Did you write a Playwright E2E test for this change?"
    echo "  Did you verify the feature works by clicking through it in a real browser?"
    echo "  If this is a UI feature, a permanent Playwright CI test is REQUIRED."
    echo ""
fi

exit 0
