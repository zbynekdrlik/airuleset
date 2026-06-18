#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher) — runs AFTER a git push.
#
# Two jobs:
#   1. Cancel SUPERSEDED CI runs — in-progress/queued runs whose commit is a
#      strict ANCESTOR of the just-pushed HEAD. Those runs test stale code that
#      this push replaced; with NO concurrency group on the workflow (the common
#      case here) GitHub does NOT auto-cancel them, so they run to completion and
#      waste runner time — the recurring "Claude pushes again without cancelling
#      the old run" churn. Ancestor-only is fail-SAFE: the current push's own runs
#      (headSha == HEAD, incl. the pull_request-event run) are KEPT; a run whose
#      sha is unknown locally or has diverged is left alone.
#   2. Emit the MANDATORY ci-monitoring instruction for the current run(s).
#
# The cancel only runs when the push ACTUALLY LANDED — proven by the local
# remote-tracking ref (@{u}) now equalling HEAD. A failed/rejected push leaves
# @{u} != HEAD, so we do NOT cancel (avoids killing a live run that is still the
# remote tip). Reads the payload from STDIN (current CC contract; $TOOL_INPUT is
# the dead old env var). The previous $TOOL_INPUT-only version was a silent no-op,
# which is why superseded runs were never cancelled.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
# Fall back to the raw payload ONLY when JSON parsing produced nothing (not when
# it parsed to an empty command) — so an empty command never makes us grep JSON.
if [ -z "$INPUT" ]; then
    case "$PAYLOAD" in
        *'"tool_input"'*) INPUT="" ;;   # valid JSON, just no command -> nothing
        *) INPUT="$PAYLOAD" ;;          # raw-string payload (old env contract)
    esac
fi

# Only act on a REAL git push invocation at a statement boundary (comment stripped)
# — not a command that merely mentions "git push" in a string/commit message/grep
# (same anchoring as pre-push-base-sync.sh).
CMD_NOCMT=${INPUT%%#*}
echo "$CMD_NOCMT" | grep -qE '(^|[;&|]|&&)[[:space:]]*(sudo[[:space:]]+|env[[:space:]]+)?git[[:space:]]+push\b' || exit 0

# Must be in a git repo with gh CLI and a GitHub remote.
git rev-parse --is-inside-work-tree &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0
gh repo view --json name &>/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
[ -z "$BRANCH" ] && exit 0
HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
[ -z "$HEAD_SHA" ] && exit 0

# Did the push LAND? On success git updates the remote-tracking ref
# refs/remotes/origin/<branch> to HEAD (no upstream-tracking config required —
# more robust than @{u}). If it != HEAD (push failed/rejected, or the ref is
# absent) we must NOT cancel — the in-progress runs may still be the live tip.
REMOTE_TIP=$(git rev-parse "refs/remotes/origin/${BRANCH}" 2>/dev/null || echo "")
PUSH_LANDED=0
[ -n "$REMOTE_TIP" ] && [ "$REMOTE_TIP" = "$HEAD_SHA" ] && PUSH_LANDED=1

# Active runs on this branch with their commit sha (one gh call).
RUNS_JSON=$(gh run list --branch "$BRANCH" --limit 30 \
    --json databaseId,status,headSha,event 2>/dev/null || echo "[]")

# Superseded cancel candidates: in_progress/queued runs whose sha != HEAD.
# NOTE: pure double-quoted python (the script is inside single shell quotes, so the
# python body must contain NO single quotes and NO f-string backslash-escapes —
# the bug that made the previous version a silent no-op).
CANDIDATES=""
if [ "$PUSH_LANDED" = "1" ]; then
    CANDIDATES=$(printf '%s' "$RUNS_JSON" | python3 -c 'import json,sys
head=sys.argv[1]
try: runs=json.load(sys.stdin)
except Exception: runs=[]
for r in runs:
    if r.get("status") in ("in_progress","queued"):
        sha=r.get("headSha") or ""
        if sha and sha!=head:
            print(str(r.get("databaseId"))+"\t"+sha)
' "$HEAD_SHA" 2>/dev/null || echo "")
fi

# All runs at the CURRENT HEAD (to monitor — a push+pull_request pair => two).
HEAD_RUNS=$(printf '%s' "$RUNS_JSON" | python3 -c 'import json,sys
head=sys.argv[1]
try: runs=json.load(sys.stdin)
except Exception: runs=[]
for r in runs:
    if r.get("headSha")==head:
        print(r.get("databaseId"))
' "$HEAD_SHA" 2>/dev/null || echo "")

CANCELLED=0
if [ -n "$CANDIDATES" ]; then
    while IFS=$'\t' read -r RID SHA; do
        [ -z "$RID" ] && continue
        # Cancel ONLY if SHA is a strict ANCESTOR of HEAD (this push superseded it).
        # is-ancestor returns non-zero when not an ancestor / sha unknown locally —
        # guarded so set -e doesn't abort and we NEVER cancel a non-superseded run.
        if git merge-base --is-ancestor "$SHA" "$HEAD_SHA" 2>/dev/null; then
            gh run cancel "$RID" &>/dev/null 2>&1 && CANCELLED=$((CANCELLED + 1)) || true
        fi
    done <<< "$CANDIDATES"
fi

[ "$CANCELLED" -gt 0 ] && echo "CI: cancelled ${CANCELLED} superseded run(s) on ${BRANCH} (older commits this push replaced)."

# Monitor instruction for the current-HEAD run(s).
LATEST=$(printf '%s' "$HEAD_RUNS" | grep -v '^$' | head -1 || echo "")
[ -z "$LATEST" ] && exit 0
MONITOR_LIST=$(printf '%s' "$HEAD_RUNS" | grep -v '^$' | paste -sd' ' - 2>/dev/null || echo "$LATEST")

cat <<MONITOR

⚠️ MANDATORY (ci-monitoring.md): you just pushed to ${BRANCH}. Now:
1. Monitor in the background until terminal: sleep 300 && gh run view ${LATEST} --json status,conclusion,jobs
2. If a push+pull_request pair fired, monitor BOTH runs: ${MONITOR_LIST}
3. Do NOT start any new task / brainstorm / issue selection until CI is terminal.
4. Do NOT send a completion report until CI is green.
5. On failure: gh run view ${LATEST} --log-failed — collect ALL failures, fix in ONE
   commit (ci-push-discipline.md), then push ONCE.

Run(s) #${MONITOR_LIST} on ${BRANCH} — monitor now.
MONITOR
