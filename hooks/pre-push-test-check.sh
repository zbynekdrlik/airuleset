#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# BLOCKS git push if:
#   1. Feature code changed but no test files modified
#   2. Test files have too few meaningful assertions (shallow test detection)
#   3. Bug-fix commits exist but NO test commit precedes them in this PR
# Bypass: [no-test: <reason>] in latest commit message — bare [no-test] no longer accepted.
# Every bypass logged to ~/devel/airuleset/audits/no-test-skips.log.
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

# Detect default branch
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")
PROJECT=$(basename "$(git rev-parse --show-toplevel)")

# Audit log for bypasses
AUDIT_LOG="$HOME/devel/airuleset/audits/no-test-skips.log"
mkdir -p "$(dirname "$AUDIT_LOG")"

# Check for [no-test: <reason>] bypass on latest commit
LAST_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
LAST_SHA=$(git log -1 --pretty=%h 2>/dev/null || echo "unknown")

# Reject bare [no-test] without a reason
if echo "$LAST_MSG" | grep -qE '\[no-test\](\s|$)'; then
    echo ""
    echo "🚫 BLOCKED: Bare [no-test] is no longer accepted."
    echo ""
    echo "  Use [no-test: <reason>] explaining WHY a test is not feasible."
    echo "  Valid reasons: 'config-only change, no logic', 'release tag',"
    echo "                  'auto-generated file', 'docs only'."
    echo "  NEVER use this for bug fixes — see regression-test-first.md."
    echo ""
    exit 2
fi

# Honor [no-test: <reason>] but log it
if echo "$LAST_MSG" | grep -qE '\[no-test:\s*[^]]+\]'; then
    REASON=$(echo "$LAST_MSG" | grep -oE '\[no-test:\s*[^]]+\]' | head -1)
    echo "$(date -Iseconds)  project=$PROJECT  sha=$LAST_SHA  $REASON" >> "$AUDIT_LOG"
    exit 0
fi

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
    echo "  Bypass: add [no-test: <reason>] to your commit message"
    echo "          (the reason is logged to audits/no-test-skips.log)."
    echo ""
    exit 2
fi

# Gate 2: Bug-fix commits require RED-BEFORE-GREEN order
# A bug-fix commit is identified by:
#   - Subject line containing fix:, fix(, bug:, bugfix:, regression:, hotfix:, patch:, repair:
#   - OR body containing Closes/Fixes/Resolves #N
# For each bug-fix commit, a TEST commit (touching tests/, e2e/, *test*, *spec*) must exist
# EARLIER in the branch's history (older commit timestamp).
COMMITS=$(git log --reverse --pretty='%H' "origin/${DEFAULT_BRANCH}..HEAD" 2>/dev/null || echo "")

if [ -n "$COMMITS" ]; then
    # Walk commits in order; track whether we've seen a test commit yet
    SEEN_TEST_COMMIT=0
    BUG_FIX_BEFORE_TEST=""

    for SHA in $COMMITS; do
        SUBJECT=$(git log -1 --pretty='%s' "$SHA" 2>/dev/null || echo "")
        BODY=$(git log -1 --pretty='%b' "$SHA" 2>/dev/null || echo "")
        FILES=$(git diff-tree --no-commit-id --name-only -r "$SHA" 2>/dev/null || echo "")

        # Does this commit add/modify a test file?
        if echo "$FILES" | grep -qiE '(test|spec|e2e|playwright)'; then
            SEEN_TEST_COMMIT=1
        fi

        # Is this commit a bug fix?
        IS_BUGFIX=0
        if echo "$SUBJECT" | grep -qiE '^(fix\(|fix:|bug:|bugfix:|regression:|hotfix:|patch:|repair:)'; then
            IS_BUGFIX=1
        fi
        if echo "$BODY" | grep -qiE '(closes|fixes|resolves)\s+#[0-9]+'; then
            IS_BUGFIX=1
        fi

        # Bug-fix commit before any test commit = violation
        if [ "$IS_BUGFIX" = "1" ] && [ "$SEEN_TEST_COMMIT" = "0" ]; then
            BUG_FIX_BEFORE_TEST="${BUG_FIX_BEFORE_TEST}\n    $(git log -1 --pretty='%h %s' "$SHA")"
        fi
    done

    if [ -n "$BUG_FIX_BEFORE_TEST" ]; then
        echo ""
        echo "🚫 BLOCKED: Bug-fix commit appears BEFORE any test commit in this PR."
        echo ""
        echo "  Per regression-test-first.md, every bug fix needs:"
        echo "    1. RED commit (test first) — adds failing test that asserts correct behavior"
        echo "    2. GREEN commit (fix second) — fix that makes the test pass"
        echo ""
        echo "  Bug-fix commits without a preceding test commit:"
        echo -e "$BUG_FIX_BEFORE_TEST"
        echo ""
        echo "  Fix: amend the branch so a test commit precedes each fix commit."
        echo "       (Reorder commits, or add a new test commit before the fix.)"
        echo "  Bypass: [no-test: <reason>] — but NEVER use this for real bug fixes."
        echo ""
        exit 2
    fi
fi

# Gate 3: Check test files for meaningful assertions (shallow test detection)
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
