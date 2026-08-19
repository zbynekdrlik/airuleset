#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) on `git commit` -- #567, commit-time close-trigger ban.
#
# A worktree / autopilot WORKER must never emit a GitHub close-trigger in a
# commit message (close/closes/closed | fix/fixes/fixed | resolve/resolves/
# resolved, optional colon, optional whitespace, then `#N` or `owner/repo#N`).
# GitHub auto-closes the referenced issue the instant the supervisor merges the
# worker's branch to the default branch -- BYPASSING the evidence-based review
# the supervisor is supposed to close it with (MEMORY.md #152/#348). Live
# incident #564 (2026-08-19): worker commit `e2933ca0` titled `fix: #564 review
# -- ...` auto-closed #564 because the grammar accepts the OPTIONAL COLON
# (`fix: #564`) -- a form the documented post-hoc scan (which required a literal
# space) missed. 3rd incident of the class; the rule-intake gate says
# mechanically checkable -> hook.
#
# SCOPE (worker/worktree ONLY -- the supervisor's or an ordinary project's
# deliberate `Closes #N` MUST stay possible): fires only when the payload is an
# `autopilot-worker` subagent (agent_type -- catches a serial-fallback worker)
# OR the session cwd is inside an isolated worktree (`.claude/worktrees/` -- the
# #564 vector; `.cwd` is the STABLE session path, never a mid-Bash `cd`). A MAIN
# session (no agent_type, cwd NOT under a worktree) passes untouched.
#
# Detection + message extraction live in the importable `close_trigger.py`
# (the design_gate.py / block-commit-without-design.sh module+thin-hook split),
# reusing block-ungated-issue-filing.sh's -F body resolution / cd-tracking /
# heredoc capture. Reads the payload on STDIN (`.tool_input.command`/`.cwd`/
# `.agent_type`), exits 2 with the reason on STDERR (stdout is invisible to the
# model). Fail-open on any unmeasurable state (no jq/python3, unbalanced quotes,
# unreadable -F file) -- the supervisor's post-hoc scan + review are the backstop.
#
# Bypass (rare, logged): `# airuleset:close-trigger-ok <reason>` in the command.

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

# Cheap pre-filter: only a command that could contain a `git commit` at all.
case "$CMD" in
    *git*commit*) ;;
    *) exit 0 ;;
esac

# Context gate FIRST (cheap, local) -- worker/worktree only. A MAIN session (no
# agent_type, cwd not under a worktree) never pays the python spawn below.
IS_WORKER=0
[ "$AGENT_TYPE" = "autopilot-worker" ] && IS_WORKER=1
case "$CWD" in */.claude/worktrees/*) IS_WORKER=1 ;; esac
[ "$IS_WORKER" = "1" ] || exit 0

# Deliberate bypass, checked OUTSIDE quotes (a marker written INTO the commit
# message must not disable the guard).
STRIPPED=$(printf '%s' "$CMD" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g') || STRIPPED="$CMD"
case "$STRIPPED" in
    *"airuleset:close-trigger-ok"*)
        LOG="/tmp/airuleset-close-trigger-bypass-${EUID:-$(id -u)}.log"
        { echo "$(date -Iseconds)  bypass: $CMD" >> "$LOG"; } 2>/dev/null || true
        exit 0 ;;
esac

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into `python3 -`'s own stdin (this repo's own
# recurring trap -- see block-commit-without-design.sh / subagent-stop-check-run-card.sh).
HIT=$(python3 - "$REPO_ROOT" "$CMD" "$CWD" "$AGENT_TYPE" <<'PYEOF' 2>/dev/null || true
import sys
repo_root, cmd, cwd, agent_type = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, repo_root)
try:
    import close_trigger as ct
except Exception:
    sys.exit(0)
if not ct.is_worker_context(cwd, agent_type):
    sys.exit(0)
hit = ct.scan_commit_command(cmd, cwd)
if hit:
    print(hit)
PYEOF
)
[ -n "$HIT" ] || exit 0

cat >&2 <<MSG

🚫 BLOCKED: your commit message contains a GitHub close-trigger ("$HIT").

You are a worktree / autopilot WORKER. GitHub auto-closes the referenced issue
the instant the supervisor merges your branch to the default branch — which
BYPASSES the evidence-based review the supervisor closes the ticket with
(MEMORY.md #152/#348; live incident #564). Workers NEVER close tickets.

Reword the message so no close keyword (close/closes/closed, fix/fixes/fixed,
resolve/resolves/resolved) sits next to a "#N" ref:

  * reference the ticket in PARENTHESES:  "... review (#564)"
  * or keep the keyword away from the ref:  "fix the parser — see #564"
                                            "review [green] #564"  (a token between them)

The FULL close grammar GitHub honours is: KEYWORD + optional ":" + optional
whitespace + "#N" (also "owner/repo#N") — so "fix #N", "fix: #N", "Fixes:#N"
all auto-close and are all blocked; "(#N)" never does.

Bypass (rare, logged): add  # airuleset:close-trigger-ok <reason>  to the command
— never for a real worker commit (the supervisor owns closing the ticket).
MSG
exit 2
