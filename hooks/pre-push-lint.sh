#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Blocks git push if local lint checks fail.
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
        echo "Pre-push lint: checking Python with ruff..."
        if ! ruff check . 2>&1; then
            echo ""
            echo "BLOCKED: ruff found issues. Fix them before pushing."
            FAILED=1
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
