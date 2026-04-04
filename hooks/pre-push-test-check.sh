#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# BLOCKS git push if:
#   1. Feature code changed but no test files modified
#   2. Test files have too few meaningful assertions (shallow test detection)
# Bypass: [no-test] in latest commit message.
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

# Get files changed compared to default branch
CHANGED_FILES=$(git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD" 2>/dev/null || git diff --name-only HEAD~1 2>/dev/null || echo "")

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# Feature code files (these REQUIRE test changes)
FEATURE_CHANGES=$(echo "$CHANGED_FILES" | grep -E '\.(rs|ts|tsx|js|jsx|py)$' | grep -vE '(test|spec|e2e|playwright|_test\.|\.test\.)' || echo "")

# Test/E2E files
TEST_CHANGES=$(echo "$CHANGED_FILES" | grep -iE '(test|spec|e2e|playwright)' || echo "")

# Gate 1: Feature code changed but no test files
if [ -n "$FEATURE_CHANGES" ] && [ -z "$TEST_CHANGES" ]; then
    echo ""
    echo "🚫 BLOCKED: Feature code changed but NO test files modified."
    echo ""
    echo "  Changed feature files:"
    echo "$FEATURE_CHANGES" | head -10 | sed 's/^/    /'
    echo ""
    echo "  To fix: write a Playwright E2E test or unit test for your changes."
    echo "  Bypass: add [no-test] to your commit message (makes the skip auditable)."
    echo ""
    exit 2
fi

# Gate 2: Check test files for meaningful assertions (shallow test detection)
if [ -n "$TEST_CHANGES" ]; then
    SHALLOW_WARNINGS=""
    MIN_ASSERTIONS=2

    for tf in $TEST_CHANGES; do
        [ -f "$tf" ] || continue

        # Count meaningful assertion patterns
        ASSERTION_COUNT=0
        # Playwright/Jest: expect(...).to*
        ASSERTION_COUNT=$((ASSERTION_COUNT + $(grep -cE 'expect\(.+\)\.(to|not)' "$tf" 2>/dev/null || echo 0)))
        # Rust: assert!, assert_eq!, assert_ne!
        ASSERTION_COUNT=$((ASSERTION_COUNT + $(grep -cE 'assert(_eq|_ne)?!' "$tf" 2>/dev/null || echo 0)))
        # Python: assert, self.assert
        ASSERTION_COUNT=$((ASSERTION_COUNT + $(grep -cE '^\s*assert\s|self\.assert' "$tf" 2>/dev/null || echo 0)))

        if [ "$ASSERTION_COUNT" -lt "$MIN_ASSERTIONS" ]; then
            SHALLOW_WARNINGS="${SHALLOW_WARNINGS}\n  ⚠️ $tf: only $ASSERTION_COUNT assertions (minimum: $MIN_ASSERTIONS)"
        fi

        # Check for shallow anti-patterns with few assertions
        if [ "$ASSERTION_COUNT" -le 2 ]; then
            if grep -qE 'toBeVisible\(\)\s*;?\s*$' "$tf" 2>/dev/null && ! grep -qE 'toHaveText|toContainText|toHaveValue|boundingBox' "$tf" 2>/dev/null; then
                SHALLOW_WARNINGS="${SHALLOW_WARNINGS}\n  ⚠️ $tf: only checks visibility, not content or behavior"
            fi
            if grep -qE "response\.status.*200|statusCode.*200" "$tf" 2>/dev/null && ! grep -qE 'toContain|toMatch|toHaveProperty|response\.body|\.json\(\)' "$tf" 2>/dev/null; then
                SHALLOW_WARNINGS="${SHALLOW_WARNINGS}\n  ⚠️ $tf: only checks HTTP 200, not response content"
            fi
        fi
    done

    if [ -n "$SHALLOW_WARNINGS" ]; then
        echo ""
        echo "⚠️ SHALLOW TEST WARNING: Test files have weak assertions."
        echo -e "$SHALLOW_WARNINGS"
        echo ""
        echo "  Tests must verify actual behavior (text, values, state changes),"
        echo "  not just that pages load or APIs return 200."
        echo ""
        # Warning only for assertion quality — don't block, but make it visible
    fi
fi

exit 0
