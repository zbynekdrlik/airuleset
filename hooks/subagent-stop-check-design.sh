#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — #136 design-before-code gate half 3/3 (BACKSTOP),
# EXTENDED by #213 (validated) and #214 (reviewed) to check THREE evidence
# kinds instead of one, from the SAME payload and the SAME marker family
# `design_gate.py` now maintains.
#
# hooks/block-commit-without-design.sh is the in-band gate for DESIGN only;
# this is what catches it MISSING (not deployed on some box yet, a commit
# made outside the Bash tool, an unjustified direct git operation) AND is
# now the ONLY gate at all for validated/reviewed (there is no in-band
# equivalent for those two — they are evidence about WHAT HAPPENED, not
# about a commit, so there is no natural PreToolUse choke point). Same
# family, same bound-by-construction shape as
# hooks/subagent-stop-check-run-card.sh: a worker that stops having claimed
# a REAL merge and at least one CLOSED issue, with any of the three markers
# missing for that issue, is blocked ONCE per (session, repo#issue) and told
# to post the missing comment(s) now — a worker that genuinely cannot
# deliver still finishes, it is never wedged.
#
# Deliberately NO watchdog job alongside this (unlike run-card's job 25) --
# the ticket that created this gate says so explicitly: this is hook-shaped
# (tied to a specific commit/stop event), not timer-shaped, and #102 is the
# standing precedent against answering every gap with more machinery.
#
# NEVER GUESS: a cwd that is not a git repo, an unresolvable `origin`, a
# missing jq/python3, an unparsable payload -- every one of them exits 0
# silently.

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

# Data via ARGV, never a pipe into a `python3 -` heredoc (see
# subagent-stop-check-run-card.sh for why).
MISSING=$(python3 - "$REPO_ROOT" "$CWD" "$MSG" <<'PYEOF' 2>/dev/null || true
import re, sys, os
sys.path.insert(0, sys.argv[1])
try:
    import design_gate as dg
    import notify
except Exception:
    sys.exit(0)
cwd, msg = sys.argv[2], sys.argv[3]
ev = notify.parse_worker_evidence(msg)
if not ev["merged"] or not ev["closed"]:
    sys.exit(0)
repo = notify.repo_name_for(cwd)
if not repo:
    sys.exit(0)

# Adversarial-review finding: an issue closed via STEP 0's documented
# `gh issue close --comment` path (OBSOLETE, never coded at all) can still
# appear in `issue_state:` as closed -- but that command is never
# classified by post-record-design-comment.sh (it only watches
# `gh issue comment`), so demanding design/validated/reviewed evidence for
# it is unmeetable by construction. The evidence block's own
# `obsolete_closed:` (full-authority) / `obsolete_handed_off:`
# (fork-no-merge) line already names exactly which issues these are.
_EXCLUDE_LINE_RE = re.compile(
    r"^\s*obsolete_(?:closed|handed_off)\s*:(.*)$", re.I | re.M)
excluded = set()
for m in _EXCLUDE_LINE_RE.finditer(msg):
    excluded.update(dg.issue_refs(m.group(1)))

print(repo)
for n in ev["closed"]:
    if n in excluded:
        continue
    missing_kinds = [k for k in dg.ALL_KINDS if not dg.marker_exists(repo, n, k)]
    if missing_kinds:
        print("%d:%s" % (n, ",".join(missing_kinds)))
PYEOF
)
[ -n "$MISSING" ] || exit 0

REPO=$(printf '%s\n' "$MISSING" | sed -n '1p')
ITEMS=$(printf '%s\n' "$MISSING" | sed -n '2,$p')
[ -n "$REPO" ] && [ -n "$ITEMS" ] || exit 0

# One block per (session, repo#issue) -- same bound as before the #213/#214
# extension. All missing kinds for that issue go into ONE consolidated
# message rather than a separate block per kind, so a worker that fixes
# only ONE kind is never re-blocked for the others this session (the
# non-wedging guarantee this gate has always made).
STATE="/tmp/airuleset-designgate-$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')"
SEEN=$(cat "$STATE" 2>/dev/null || echo "")
FRESH_LINES=""
NEW_SEEN="$SEEN"
while IFS=: read -r n kinds; do
    [ -n "$n" ] || continue
    case " $SEEN " in *" ${REPO}#${n} "*) continue ;; esac
    FRESH_LINES="${FRESH_LINES}${n}:${kinds}
"
    NEW_SEEN="${NEW_SEEN}${NEW_SEEN:+ }${REPO}#${n}"
done <<EOF
$ITEMS
EOF
[ -n "$FRESH_LINES" ] || exit 0
printf '%s' "$NEW_SEEN" > "$STATE" 2>/dev/null || true

LINES=""
for pair in $FRESH_LINES; do
    n="${pair%%:*}"
    kinds="${pair#*:}"
    LINES="${LINES}  #${n} -- missing: ${kinds}
"
done

REASON="Ticket(s) below were merged and closed, but at least one required
evidence comment was never recorded for them (design / validated /
reviewed -- #136/#213/#214). hooks/block-commit-without-design.sh only
catches a missing DESIGN comment in-band; validated/reviewed have no
in-band equivalent, so this SubagentStop backstop is their only gate:

${LINES}
Post the missing comment(s) NOW, per autonomous-batch-issue-development.md
+ agents/autopilot-worker.md STEP 0 / CYCLE step 6:

  design    -- gh issue comment <N> --body \"<root cause> ... <chosen approach> ... <rejected alternative> ...\"
  validated -- gh issue comment <N> --body \"<what you checked/reproduced> ... <what you observed, e.g. still valid / already fixed> ...\"
  reviewed  -- gh issue comment <N> --body \"<ran /review + /requesting-code-review> ... <N findings, fixed in commit <sha>> ...\"

Then add/confirm the corresponding evidence-block line(s) citing the
comment(s). You are blocked once per issue; if a comment genuinely cannot
be posted, report that and stop."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
