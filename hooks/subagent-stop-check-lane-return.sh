#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — #844 durable LANE-RETURN gate. A worktree-mode
# autopilot-worker's LAST act before returning is a `gh issue comment <N>`
# carrying a `LANE-RETURN:` block (branch, head sha, worktree, version,
# evidence) — the durable record that survives a lost lane-completion
# notification (the #844 forced-compact residual). This gate BLOCKS a worker
# that stops having claimed a WORKTREE-mode return (a `branch:` naming a
# worktree branch, and NOT merged) with no LANE-RETURN marker for its issue(s),
# ONCE per (session, repo#issue) — the SAME non-wedging bound as
# subagent-stop-check-design.sh: a worker that genuinely cannot post still
# finishes, it is never wedged.
#
# The LANE-RETURN marker (~/.claude/lane-return-posted/<repo>#<issue>) is
# written by hooks/post-record-design-comment.sh (the SAME re-read-from-GitHub
# classifier that writes design/validated/reviewed markers) via
# design_gate.classify_lane_return_comment — deliberately NOT one of
# design_gate.ALL_KINDS, so the MERGE-flow gates never demand a LANE-RETURN of a
# merging worker; this is the worktree-mode counterpart.
#
# NEVER GUESS: an unresolvable repo, a missing jq/python3, an unparsable
# payload, a MERGED (full-flow) return, or a return with no worktree branch —
# every one exits 0 silently.

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

_field() { printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""; }

AGENT_TYPE=$(_field '.agent_type // empty')
[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0

SID=$(_field '.session_id // empty')
CWD=$(_field '.cwd // empty')
MSG=$(_field '.last_assistant_message // empty')
[ -n "$SID" ] || exit 0
[ -n "$CWD" ] || exit 0
[ -n "$MSG" ] || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into a `python3 -` heredoc (the heredoc claims
# stdin for the SCRIPT SOURCE — this repo's own recurring trap).
MISSING=$(python3 - "$REPO_ROOT" "$CWD" "$MSG" <<'PYEOF' 2>/dev/null || true
import re, sys
sys.path.insert(0, sys.argv[1])
try:
    import design_gate as dg
    import notify
except Exception:
    sys.exit(0)
cwd, msg = sys.argv[2], sys.argv[3]

# A worktree-mode return is NOT merged (the supervisor merges) — a MERGED return
# is the full flow, handled by subagent-stop-check-design.sh. Skip a merged one.
ev = notify.parse_worker_evidence(msg)
if ev["merged"]:
    sys.exit(0)

# Skip a NOT-COMPLETED return (a worker that hit a wall / is blocked / lost its
# isolation): its `branch:` line does not mean "lane finished", so demanding a
# LANE-RETURN with green evidence would manufacture a false completion signal.
# The completed worktree stop-point is a clean local-green return; a blocked/
# failed one carries one of these signals instead.
if re.search(r"ISOLATION FAILED|^\s*UNVERIFIED\s*:|❓\s*(?:NEEDS YOU|ASKED)",
             msg, re.I | re.M):
    sys.exit(0)

# It must claim a WORKTREE branch (the worktree-mode return's `branch:` line
# naming a worktree-agent-*/worktree-issue-*/worktree-* branch). No such claim
# -> not a worktree-mode return this gate governs (a fork-no-merge/branch-merge
# hand-off has its own READY-FOR-REVIEW convention, not gated here).
_BRANCH_LINE_RE = re.compile(r"^\s*branch\s*:(.*)$", re.I | re.M)
has_worktree_branch = any(
    re.search(r"worktree-(?:agent|issue)-\w+|worktree-\S+", m.group(1))
    for m in _BRANCH_LINE_RE.finditer(msg))
if not has_worktree_branch:
    sys.exit(0)

# Which issues this return covers — from the `issues:` line (the worktree-mode
# block's own header). Fall back to any issue_state line if present.
issues = []
seen = set()
for line_re in (re.compile(r"^\s*issues\s*:(.*)$", re.I | re.M),
                re.compile(r"^\s*issue_state\s*:(.*)$", re.I | re.M)):
    for m in line_re.finditer(msg):
        for n in dg.issue_refs(m.group(1)):
            if n not in seen:
                seen.add(n)
                issues.append(n)
    if issues:
        break
if not issues:
    sys.exit(0)

# #220 -- prefer the block's own `branch:`/repo signal, but the repo the LANE
# lands in is the worker's cwd repo (the supervisor merges from there). Resolve
# it the same way the design gate does.
repo = notify.resolve_repo_key(cwd, msg=msg)
if not repo:
    sys.exit(0)

# Obsolete/handed-off issues named on the block's own exclusion lines are not
# expected to carry a LANE-RETURN (they were closed/handed off, never worked as
# a lane) — mirror the design gate's exclusion.
_EXCLUDE_LINE_RE = re.compile(
    r"^\s*(?:obsolete_(?:closed|handed_off)|dropped)\s*:(.*)$", re.I | re.M)
excluded = set()
for m in _EXCLUDE_LINE_RE.finditer(msg):
    excluded.update(dg.issue_refs(m.group(1)))

print(repo)
for n in issues:
    if n in excluded:
        continue
    if not dg.marker_exists(repo, n, "lane-return"):
        print(n)
PYEOF
)
[ -n "$MISSING" ] || exit 0

REPO=$(printf '%s\n' "$MISSING" | sed -n '1p')
ITEMS=$(printf '%s\n' "$MISSING" | sed -n '2,$p')
[ -n "$REPO" ] && [ -n "$ITEMS" ] || exit 0

# One block per (session, repo#issue) — the SAME non-wedging bound as the design
# gate: a worker blocked once posts the LANE-RETURN and stops; a worker that
# genuinely cannot post still finishes rather than wedging forever.
STATE="/tmp/airuleset-lanereturn-$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')"
SEEN=$(cat "$STATE" 2>/dev/null || echo "")
FRESH=""
NEW_SEEN="$SEEN"
while IFS= read -r n; do
    [ -n "$n" ] || continue
    case " $SEEN " in *" ${REPO}#${n} "*) continue ;; esac
    FRESH="${FRESH}${n}
"
    NEW_SEEN="${NEW_SEEN}${NEW_SEEN:+ }${REPO}#${n}"
done <<EOF
$ITEMS
EOF
[ -n "$FRESH" ] || exit 0
printf '%s' "$NEW_SEEN" > "$STATE" 2>/dev/null || true

LINES=""
for n in $FRESH; do
    LINES="${LINES}  #${n}
"
done

REASON="Worktree lane(s) below returned a branch + head with NO LANE-RETURN
comment on their ticket (#844). A worktree worker's LAST act before returning
MUST be a durable LANE-RETURN comment, so a lost lane-completion notification
(the #844 forced-compact residual) loses nothing — the supervisor integrates
the lane from that comment + the branch:

${LINES}
Post it NOW, then return:

  gh issue comment <N> --body \"LANE-RETURN: branch <worktree-branch> head <sha> worktree <path> version <v> — <one-line evidence: RED sha -> GREEN sha, local verify green>\"

You are blocked once per issue; if the comment genuinely cannot be posted,
report that and stop."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
