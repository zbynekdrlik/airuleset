#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Blocks git push if local lint checks fail.
# Exit code 2 = block the tool call.

# Read the tool payload from STDIN (current CC contract; $TOOL_INPUT is the dead
# old env var, kept as fallback). See block-sensitive-staging.sh for the rationale.
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

# Only act on REAL `git push` commands. Strip quoted substrings FIRST so a
# command that merely CONTAINS the words "git push" inside a commit message,
# echo string, or file path does NOT falsely trigger the lint (that bug wrongly
# blocked/stalled non-push commands like `git commit -m "...git push..."`).
CMD_NOQUOTES=$(printf '%s' "$INPUT" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")
if ! printf '%s' "$CMD_NOQUOTES" | grep -qE 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]]|$)'; then
    exit 0
fi

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Detect project type and run appropriate linters
FAILED=0

# Rust project (Cargo.toml exists)
if [ -f "Cargo.toml" ]; then
    echo "Pre-push lint: checking Rust formatting..."
    if ! cargo fmt --all --check 2>&1; then
        echo ""
        echo "BLOCKED: cargo fmt check failed. Run 'cargo fmt --all' to fix."
        FAILED=1
    fi
    # NOTE: clippy is NOT run here — it compiles the project (10-20GB).
    # Clippy runs on CI only. Project CLAUDE.md can override this.
fi

# Python project (pyproject.toml or setup.py or *.py in root)
if [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
    if command -v ruff &>/dev/null; then
        # Lint ONLY the Python files this push introduces (commits ahead of the
        # tracked upstream) — NEVER `ruff check .` over the whole repo. A blanket
        # whole-repo check false-positives on pre-existing tech debt the pusher
        # didn't touch (and that CI may not even gate), wrongly blocking every
        # push. The hook's job is "don't push NEW lint errors", not "the entire
        # repo must be clean". Fall back to the last commit when no upstream.
        UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")
        if [ -n "$UPSTREAM" ]; then
            RANGE="${UPSTREAM}..HEAD"
        else
            RANGE="HEAD~1..HEAD"
        fi
        CHANGED=$(git diff --name-only --diff-filter=d "$RANGE" 2>/dev/null | grep -E '\.py$' || true)
        if [ -n "$CHANGED" ]; then
            echo "Pre-push lint: ruff on $(echo "$CHANGED" | wc -l) changed Python file(s)..."
            if ! echo "$CHANGED" | xargs -r ruff check 2>&1; then
                echo ""
                echo "BLOCKED: ruff found issues in files you're pushing. Fix them before pushing."
                FAILED=1
            fi
        else
            echo "Pre-push lint: no changed Python files in this push (ruff skipped)."
        fi
    fi
fi

# Node.js project (package.json with lint script)
if [ -f "package.json" ] && grep -q '"lint"' package.json 2>/dev/null; then
    echo "Pre-push lint: running npm lint..."
    if ! npm run lint 2>&1; then
        echo ""
        echo "BLOCKED: npm lint failed. Fix issues before pushing."
        FAILED=1
    fi
fi

if [ "$FAILED" -ne 0 ]; then
    echo ""
    echo "Fix the lint issues above, then push again."
    exit 2
fi

# All checks passed (or no linter detected)
exit 0
