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

# #682: Claude Code surfaces ONLY stderr to the model on a PreToolUse deny
# (exit 2); stdout is invisible (transcript-only). Every explanation this
# hook prints is a block reason or a diagnostic warning -- it has no
# machine-readable stdout contract -- so route all of fd1 to stderr, and
# the model reads WHY the call was blocked instead of "No stderr output".
# Command substitutions capture their own stdout and are unaffected.
exec 1>&2

# Only act on REAL `git push` commands. Strip quoted substrings FIRST so a
# command that merely CONTAINS the words "git push" inside a commit message,
# echo string, or file path does NOT falsely trigger the lint (that bug wrongly
# blocked/stalled non-push commands like `git commit -m "...git push..."`).
CMD_NOQUOTES=$(printf '%s' "$INPUT" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")
if ! printf '%s' "$CMD_NOQUOTES" | grep -qE 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]]|$)'; then
    exit 0
fi

# #503 -- a durability BACKUP push to the refs/autopilot-wip/* namespace (the
# ONE push a worktree worker makes, to survive a lost worktree) triggers NO CI
# (a push to any ref outside refs/heads/*/refs/tags/* fires no GitHub Actions
# workflow), so this lint gate -- which exists solely to keep a CI-triggering
# push clean -- must not block it. A mid-work snapshot is legitimately
# lint-dirty; the backup's whole job is to preserve it AS-IS. Anchor on the push
# DESTINATION refspec (`:refs/autopilot-wip/` or `--delete`/`-d refs/autopilot-wip/`),
# NOT a bare substring anywhere in the command -- a real push whose commit
# message / echo merely MENTIONS the namespace must stay gated (#503 review).
printf '%s' "$INPUT" | grep -qE ':refs/autopilot-wip/|(--delete|[[:space:]]-d)[[:space:]]+refs/autopilot-wip/' && exit 0

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Detect project type and run appropriate linters
FAILED=0

# #218 -- `git diff --name-only` always returns paths ROOT-relative,
# regardless of this hook's own process cwd. On a nested-repo layout (git
# root at the repo top, the actual project one level down -- pyproject.toml
# lives in a subdirectory, not at the root) this hook's cwd is that
# subdirectory, so a root-relative path piped straight into a linter is
# resolved against the WRONG base and silently doesn't exist. Resolve the
# git root once here and make every changed-file path ABSOLUTE before
# handing it to a linter -- correct regardless of which directory the hook
# itself is running from.
GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

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
        # #951 item 2: detect CI-pinned ruff version and compare to local.
        # When they differ, a lint failure may be version-specific (a rule
        # behaviour change within the pinned select set). Demote BLOCK to
        # WARN on mismatch — fail-open with a diagnostic, not a false block.
        _RUFF_VERSION_MISMATCH=false
        _LOCAL_RUFF_VER=$(ruff --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "")
        _CI_RUFF_PIN=""
        for _WF in .github/workflows/*.yml .github/workflows/*.yaml; do
            [ -f "$_WF" ] || continue
            _CI_RUFF_PIN=$(grep -oE 'ruff==[0-9]+\.[0-9]+\.[0-9]+' "$_WF" 2>/dev/null \
                | head -1 | sed 's/ruff==//' || true)
            [ -n "$_CI_RUFF_PIN" ] && break
        done
        if [ -n "$_CI_RUFF_PIN" ] && [ -n "$_LOCAL_RUFF_VER" ] \
           && [ "$_CI_RUFF_PIN" != "$_LOCAL_RUFF_VER" ]; then
            _RUFF_VERSION_MISMATCH=true
            echo "WARNING: local ruff ${_LOCAL_RUFF_VER} differs from CI pin ${_CI_RUFF_PIN} — lint failures will be warnings, not blocks."
        fi

        # Lint ONLY the Python files this push introduces — NEVER `ruff check .`
        # over the whole repo. A blanket whole-repo check false-positives on
        # pre-existing tech debt the pusher didn't touch (and that CI may not even
        # gate), wrongly blocking every push. The hook's job is "don't push NEW
        # lint errors", not "the entire repo must be clean".
        #
        # #951: the range must be THREE-DOT against the PR TARGET (the branch
        # this work merges into), not TWO-DOT against @{u}. Two-dot diffs the
        # two TIPS — after `git merge origin/develop` it includes files that
        # came FROM upstream (already linted upstream) and falsely blocks. The
        # three-dot range `BASE_REF...HEAD` diffs from the merge-base and gives
        # exactly "files changed on MY side". Same PR-target base resolution
        # as pre-push-test-check.sh / block-test-skips.sh (#847/#909).
        DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
            | sed 's@^refs/remotes/origin/@@' || echo "main")
        CUR_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")
        BASE_REF="origin/${DEFAULT_BRANCH}"
        _CASE_RESOLVED=false
        case "$CUR_BRANCH" in
            HEAD|"$DEFAULT_BRANCH"|staging) ;;
            develop)
                if git rev-parse -q --verify origin/staging >/dev/null; then
                    BASE_REF="origin/staging"
                    _CASE_RESOLVED=true
                fi ;;
            *)
                for CAND in develop dev; do
                    if [ "$CAND" != "$CUR_BRANCH" ]; then
                        # #847: prefer upstream/<CAND> on fork-no-merge streams
                        # (origin = personal fork, stale) when BOTH exist.
                        if git rev-parse -q --verify "upstream/${CAND}" >/dev/null && \
                           git rev-parse -q --verify "origin/${CAND}" >/dev/null; then
                            BASE_REF="upstream/${CAND}"
                            _CASE_RESOLVED=true
                            break
                        elif git rev-parse -q --verify "origin/${CAND}" >/dev/null; then
                            BASE_REF="origin/${CAND}"
                            _CASE_RESOLVED=true
                            break
                        fi
                    fi
                done ;;
        esac
        # #909/#951: fallbacks when the case block found nothing (new-branch-
        # push). NOTE: the origin/<branch> override that block-test-skips.sh
        # applies for per-added-line semantics is deliberately OMITTED here.
        # After `git merge origin/develop`, origin/<branch> points at the OLD
        # push tip, and the three-dot range from it includes merged upstream
        # files — the exact bug #951 fixes. The PR-target base from the case
        # block is the correct scope for lint (files the branch changes
        # relative to the merge target).
        if [ "$_CASE_RESOLVED" = false ]; then
            _TRACKING=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2>/dev/null || echo "")
            if [ -n "$_TRACKING" ] && git rev-parse -q --verify "$_TRACKING" >/dev/null 2>&1; then
                BASE_REF="$_TRACKING"
            else
                for _LOCAL_CAND in develop dev; do
                    if [ "$_LOCAL_CAND" != "$CUR_BRANCH" ] && \
                       git rev-parse -q --verify "$_LOCAL_CAND" >/dev/null 2>&1; then
                        BASE_REF="$_LOCAL_CAND"
                        break
                    fi
                done
            fi
        fi

        # Fall back to HEAD~1 if BASE_REF doesn't resolve (local-only repo
        # with no remotes — the old pre-#951 ultimate fallback).
        CHANGED=$(git diff --name-only --diff-filter=d "${BASE_REF}...HEAD" 2>/dev/null \
            | grep -E '\.py$' || true)
        if [ -z "$CHANGED" ] && ! git rev-parse -q --verify "$BASE_REF" >/dev/null 2>&1; then
            CHANGED=$(git diff --name-only --diff-filter=d "HEAD~1..HEAD" 2>/dev/null \
                | grep -E '\.py$' || true)
        fi
        if [ -n "$CHANGED" ]; then
            echo "Pre-push lint: ruff on $(echo "$CHANGED" | wc -l) changed Python file(s)..."
            # #218 -- $CHANGED paths are ROOT-relative; resolve to absolute
            # paths via $GIT_ROOT so ruff finds them regardless of this
            # hook's own invocation cwd (nested-repo layout).
            ABS_CHANGED=$(printf '%s\n' "$CHANGED" | sed "s|^|${GIT_ROOT}/|")
            if ! printf '%s\n' "$ABS_CHANGED" | xargs -r ruff check 2>&1; then
                if [ "$_RUFF_VERSION_MISMATCH" = true ]; then
                    # #951 item 2: version mismatch — demote to warning.
                    echo ""
                    echo "WARNING: ruff found issues, but local ruff ${_LOCAL_RUFF_VER} differs from CI pin ${_CI_RUFF_PIN}. CI may not see these errors — continuing (not blocking)."
                else
                    echo ""
                    echo "BLOCKED: ruff found issues in files you're pushing. Fix them before pushing."
                    FAILED=1
                fi
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
