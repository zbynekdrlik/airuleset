#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher) — runs AFTER a git push.
#
# Three jobs:
#   1. Cancel SUPERSEDED CI runs — in-progress/queued runs whose commit is a
#      strict ANCESTOR of the just-pushed HEAD. Those runs test stale code that
#      this push replaced; with NO concurrency group on the workflow (the common
#      case here) GitHub does NOT auto-cancel them, so they run to completion and
#      waste runner time — the recurring "Claude pushes again without cancelling
#      the old run" churn. Ancestor-only is fail-SAFE: the current push's own runs
#      (headSha == HEAD, incl. the pull_request-event run) are KEPT; a run whose
#      sha is unknown locally or has diverged is left alone.
#   2. Escalate to FORCE-CANCEL a run the normal cancel above had NO visible
#      effect on (#24, live incident: restreamer 2026-07-21 — an old run kept
#      starting new self-hosted jobs for 50+ min and starved the successor
#      after a normal `gh run cancel`; only `POST .../force-cancel` actually
#      terminated it). A synchronous 120s wait-and-recheck inside THIS
#      invocation would slow down every single push — unacceptable, since
#      most cancels DO work. So every run this hook cancels gets its id +
#      timestamp appended to a small per-repo pending file
#      (`.git/airuleset-pending-cancels.json`, never committed); on the
#      hook's NEXT invocation (this repo's own next `git push`), any entry
#      ≥120s old is re-checked and, if still not `completed`, force-cancelled
#      — one-shot, never retried again. Entries <120s old are kept for a
#      later invocation. This costs nothing on the common (already-cancelled)
#      path and only does real work when there's something left to check.
#   3. Emit the MANDATORY ci-monitoring instruction for the current run(s).
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

# #24 — escalate any run cancelled on a PRIOR invocation that is still not
# terminal ~120s later. Lives inside .git/ (never committed), so it's
# naturally per-repo and needs no extra state plumbing.
GIT_DIR_ABS=$(git rev-parse --git-dir 2>/dev/null || echo "")
PENDING_FILE=""
[ -n "$GIT_DIR_ABS" ] && PENDING_FILE="${GIT_DIR_ABS}/airuleset-pending-cancels.json"

if [ -n "$PENDING_FILE" ] && [ -f "$PENDING_FILE" ]; then
    python3 - "$PENDING_FILE" <<'PYEOF' || true
import json, subprocess, sys, time

path = sys.argv[1]
try:
    with open(path) as f:
        pending = json.load(f)
    if not isinstance(pending, list):
        pending = []
except Exception:
    pending = []

now = time.time()
still = []
for entry in pending:
    try:
        rid, cancelled_at = entry
    except Exception:
        continue
    age = now - float(cancelled_at)
    if age < 120:
        still.append([rid, cancelled_at])
        continue
    try:
        out = subprocess.run(["gh", "run", "view", str(rid), "--json", "status",
                              "--jq", ".status"], capture_output=True, text=True,
                             timeout=15)
        status = out.stdout.strip()
    except Exception:
        status = ""
    if status and status != "completed":
        try:
            fc = subprocess.run(
                ["gh", "api", "repos/{owner}/{repo}/actions/runs/%s/force-cancel" % rid,
                 "-X", "POST"], capture_output=True, text=True, timeout=15)
            if fc.returncode == 0:
                print("CI: force-cancelled run %s — normal cancel had no visible "
                     "effect after %ds (still %s)." % (rid, int(age), status))
            else:
                print("CI: force-cancel escalation for run %s FAILED (rc=%s): %s"
                     % (rid, fc.returncode, (fc.stderr or "").strip()[:200]))
        except Exception as e:
            print("CI: force-cancel escalation for run %s errored: %r" % (rid, e))
    # one-shot: whether escalated, already terminal, or unreadable -- never
    # re-check the same entry again.
try:
    with open(path, "w") as f:
        json.dump(still, f)
except Exception:
    pass
PYEOF
fi

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
NEWLY_CANCELLED=""
if [ -n "$CANDIDATES" ]; then
    while IFS=$'\t' read -r RID SHA; do
        [ -z "$RID" ] && continue
        # Cancel ONLY if SHA is a strict ANCESTOR of HEAD (this push superseded it).
        # is-ancestor returns non-zero when not an ancestor / sha unknown locally —
        # guarded so set -e doesn't abort and we NEVER cancel a non-superseded run.
        if git merge-base --is-ancestor "$SHA" "$HEAD_SHA" 2>/dev/null; then
            if gh run cancel "$RID" &>/dev/null 2>&1; then
                CANCELLED=$((CANCELLED + 1))
                NEWLY_CANCELLED="${NEWLY_CANCELLED}${RID}"$'\n'
            fi
        fi
    done <<< "$CANDIDATES"
fi

[ "$CANCELLED" -gt 0 ] && echo "CI: cancelled ${CANCELLED} superseded run(s) on ${BRANCH} (older commits this push replaced)."

# #24 — record every run just cancelled so a LATER invocation can check
# whether the cancel actually took effect (see the header comment). The
# id list goes via ARGV, never a pipe into stdin — `python3 - <<'PYEOF'`
# already claims stdin for the SCRIPT SOURCE, so a piped payload would be
# silently swallowed (the exact #96 trap: any embedded-Python helper that
# needs bash-variable data alongside a heredoc source takes it via argv).
if [ -n "$NEWLY_CANCELLED" ] && [ -n "$PENDING_FILE" ]; then
    python3 - "$PENDING_FILE" "$NEWLY_CANCELLED" <<'PYEOF' || true
import json, sys, time

path = sys.argv[1]
ids_blob = sys.argv[2]
try:
    with open(path) as f:
        d = json.load(f)
    if not isinstance(d, list):
        d = []
except Exception:
    d = []
now = time.time()
for line in ids_blob.splitlines():
    line = line.strip()
    if line:
        d.append([int(line), now])
with open(path, "w") as f:
    json.dump(d, f)
PYEOF
fi

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
