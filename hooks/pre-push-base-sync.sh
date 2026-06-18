#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — GLOBAL conflict-churn guard.
#
# THE PROBLEM: Claude pushes a branch that CONFLICTS with its base. CI burns a
# full 15-20 min cycle, the PR comes back CONFLICTING, Claude merges the base,
# resolves, and pushes AGAIN — a second wasted CI cycle. Repeat = the endless
# "started CI + conflict" loop.
#
# THE FIX: before a push, do a no-op trial merge of the base into HEAD with
# `git merge-tree`. Block ONLY when that trial merge reports a REAL CONFLICT — so
# the FIRST push is conflict-free and only ONE CI cycle runs. It does NOT block on
# a mere "behind" (e.g. the merge-commit-only divergence right after a --no-ff PR
# merge + version bump): a clean trial merge => allowed. Pairs with
# post-push-ci-cleanup (which cancels superseded runs).
#
# Fail-SAFE everywhere: only a proven CONFLICT blocks. No base, detached HEAD,
# fetch failure, deletion/tag push, merge-tree unsupported/error, pushing the base
# itself, or a non-push command => exit 0 (allow).
# Bypass: AIRULESET_ALLOW_BEHIND_PUSH=1, or '# airuleset:push-behind-ok' in the cmd.
#
# Exit 2 = block. Reads the payload from STDIN (current CC contract; $TOOL_INPUT is
# the dead old env var, kept as a fallback).

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
if [ -z "$INPUT" ]; then
    case "$PAYLOAD" in
        *'"tool_input"'*) INPUT="" ;;
        *) INPUT="$PAYLOAD" ;;
    esac
fi

# Strip a trailing comment so quoted/# mentions don't confuse parsing, then test
# for a REAL push invocation at a statement boundary (start, or after ; & | &&).
# This rejects 'grep "git push"', commit messages, echoes that merely contain the
# words (the unanchored-substring false-block).
CMD_NOCMT=${INPUT%%#*}
echo "$CMD_NOCMT" | grep -qE '(^|[;&|]|&&)[[:space:]]*(sudo[[:space:]]+|env[[:space:]]+)?git[[:space:]]+push\b' || exit 0

# Explicit bypasses.
echo "$INPUT" | grep -q 'airuleset:push-behind-ok' && exit 0
[ "${AIRULESET_ALLOW_BEHIND_PUSH:-}" = "1" ] && exit 0

# Ref deletions and tag-only pushes can't conflict with base content — allow.
echo "$CMD_NOCMT" | grep -qE 'git[[:space:]]+push\b.*(--delete|[[:space:]]-d\b|[[:space:]]:[^[:space:]]+)' && exit 0
echo "$CMD_NOCMT" | grep -qE 'git[[:space:]]+push\b.*--tags' && \
    ! echo "$CMD_NOCMT" | grep -qE 'git[[:space:]]+push\b[^#]*[[:space:]][^-:][^[:space:]]*[[:space:]]+[^-:]' && exit 0

git rev-parse --is-inside-work-tree &>/dev/null || exit 0
command -v git &>/dev/null || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
[ -z "$BRANCH" ] && exit 0   # detached HEAD

# Base = default branch (origin/HEAD), else origin/main, else origin/master.
BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "")
if [ -z "$BASE" ]; then
    if git show-ref --verify --quiet refs/remotes/origin/main; then BASE=main
    elif git show-ref --verify --quiet refs/remotes/origin/master; then BASE=master
    fi
fi
[ -z "$BASE" ] && exit 0
[ "$BRANCH" = "$BASE" ] && exit 0   # pushing the base itself
# Push command explicitly targets the base branch (e.g. `git push origin main`
# while on another branch, or a `dev:main` refspec) — that's a base push / release,
# not a feature-branch-conflict case. Allow ONLY when the refspec DESTINATION is the
# base — parse the push command's own positional args (NOT a greedy whole-line match,
# which wrongly allowed `git push origin dev && gh pr create --base main` and a
# branch merely NAMED `feature-main-fix`). The 2nd positional after `git push` is the
# refspec; its destination is the part after a ':' (or the whole token).
PUSH_DST=$(printf '%s' "$CMD_NOCMT" | python3 -c 'import re,sys
m=re.search(r"git\s+push\b(.*)", sys.stdin.read(), re.S)
pos=[a for a in (m.group(1) if m else "").split() if not a.startswith("-")]
print(pos[1].split(":")[-1] if len(pos)>=2 else "")
' 2>/dev/null || echo "")
[ -n "$PUSH_DST" ] && [ "$PUSH_DST" = "$BASE" ] && exit 0

# Refresh ONLY the base ref (cheap, timeout-guarded). Fetch failure => fail-safe.
if command -v timeout &>/dev/null; then
    timeout 15 git fetch origin "$BASE" --quiet 2>/dev/null || exit 0
else
    git fetch origin "$BASE" --quiet 2>/dev/null || exit 0
fi

git show-ref --verify --quiet "refs/remotes/origin/${BASE}" || exit 0

# Already contains the base tip? Nothing to merge — allow (covers in-sync and the
# dev-ahead/clean cases).
if git merge-base --is-ancestor "refs/remotes/origin/${BASE}" HEAD 2>/dev/null; then
    exit 0
fi

# Trial-merge the base into HEAD WITHOUT touching the working tree. merge-tree
# exits non-zero ONLY on a real merge conflict (git >= 2.38). On any error /
# unsupported flag, fail-safe allow.
MT_OUT=$(git merge-tree --write-tree "HEAD" "refs/remotes/origin/${BASE}" 2>/dev/null) || MT_RC=$?
MT_RC=${MT_RC:-0}
# rc 0 = clean merge (just behind, no conflict) -> allow.
[ "$MT_RC" -eq 0 ] && exit 0
# rc 1 = conflicts. rc >1 = merge-tree error/unsupported -> fail-safe allow.
[ "$MT_RC" -ne 1 ] && exit 0

CONFLICTS=$(printf '%s' "$MT_OUT" | grep -iE 'CONFLICT' | head -3 || true)
cat <<MSG
🚫 BLOCKED: pushing '${BRANCH}' now would create a CONFLICTING PR against '${BASE}'.

A trial merge of '${BASE}' into '${BRANCH}' has conflicts:
${CONFLICTS:-  (merge-tree reported conflicts)}

If you push, CI burns a full cycle, the PR comes back CONFLICTING, and you merge +
re-push for a SECOND wasted cycle. Resolve it FIRST, then push once:

    git fetch origin && git merge origin/${BASE}
    # resolve the conflicts, re-run local checks, then push ONCE

(git-fetch-first.md / ci-push-discipline.md.)
Bypass (rare): AIRULESET_ALLOW_BEHIND_PUSH=1 git push …, or add
'# airuleset:push-behind-ok' to the command.
MSG
exit 2
