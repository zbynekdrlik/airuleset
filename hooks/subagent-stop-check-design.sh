#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — #136, design-before-code gate, half 3/3 (BACKSTOP).
#
# hooks/block-commit-without-design.sh is the in-band gate; this is what
# catches it MISSING — not deployed on some box yet, a commit made outside
# the Bash tool, an unjustified direct git operation. Same family, same
# bound-by-construction shape as hooks/subagent-stop-check-run-card.sh: a
# worker that stops having claimed a REAL merge and at least one CLOSED
# issue, with no design marker for one of them, is blocked ONCE per
# (session, repo#issue) and told to post the comment now — a worker that
# genuinely cannot deliver still finishes, it is never wedged.
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
import sys, os
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
missing = [n for n in ev["closed"] if not dg.marker_exists(repo, n)]
if missing:
    print(repo)
    print(" ".join(str(n) for n in missing))
PYEOF
)
[ -n "$MISSING" ] || exit 0

REPO=$(printf '%s\n' "$MISSING" | sed -n '1p')
NUMS=$(printf '%s\n' "$MISSING" | sed -n '2p')
[ -n "$REPO" ] && [ -n "$NUMS" ] || exit 0

# One block per (session, repo#issue).
STATE="/tmp/airuleset-designgate-$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')"
SEEN=$(cat "$STATE" 2>/dev/null || echo "")
FRESH=""
for n in $NUMS; do
    case " $SEEN " in *" ${REPO}#${n} "*) continue ;; esac
    FRESH="${FRESH}${FRESH:+ }${n}"
done
[ -n "$FRESH" ] || exit 0
for n in $FRESH; do SEEN="${SEEN}${SEEN:+ }${REPO}#${n}"; done
printf '%s' "$SEEN" > "$STATE" 2>/dev/null || true

LIST=$(printf '%s' "$FRESH" | sed 's/\([0-9][0-9]*\)/#\1/g')
REASON="Ticket(s) ${LIST} were merged and closed, but no design comment was ever
recorded for them -- hooks/block-commit-without-design.sh should have
caught this in-band and did not (not deployed yet, a commit outside the
Bash tool, or an unjustified bypass).

Post the design comment NOW, per autonomous-batch-issue-development.md:
root cause + chosen approach + rejected alternative, for EACH ticket in
${LIST}:

  gh issue comment <N> --body \"<root cause> ... <chosen approach> ... <rejected alternative> ...\"

Then add/confirm the approach: line in your evidence block citing this
comment. You are blocked once per issue; if the comment genuinely cannot be
posted, report that and stop."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
