#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) on `git commit` — #136, design-before-code
# gate, half 2/3.
#
# Active ONLY inside an `autopilot-worker` subagent (payload `agent_type`,
# same field block-main-implementation.sh already reads at PreToolUse — CC
# 2.1.220 builds it as {session_id, transcript_path, cwd, prompt_id,
# permission_mode, agent_id, agent_type, effort}). A MAIN session (no
# agent_type at all), an ad-hoc human session, or any OTHER subagent type
# (Explore, ticket-validator, general-purpose, ...) passes untouched — the
# ticket's own explicit requirement: "a hook that blocks ordinary human
# commits is a worse bug than the one it fixes".
#
# Blocks the commit when it references an issue (`(#N)`, `Closes #N`,
# `#A/#B`, ... — design_gate.issue_refs) that has no DELIVERED design
# marker yet (hooks/post-record-design-comment.sh is the only writer).
# Every commit after the FIRST one for an issue passes for free, since the
# marker persists once written.
#
# ISSUE-REFERENCE SCOPE is deliberately the WHOLE command text, not just a
# parsed -m/-F body — unlike block-ungated-issue-filing.sh's heavy
# heredoc/quote-aware body resolution, this only needs to know WHICH issue
# numbers are mentioned, not the exact message string, and a heredoc body
# (the standard `git commit -m "$(cat <<'EOF' ... EOF)"` recipe this
# environment itself uses) is already literal text inside the command — no
# separate extraction needed. The cost of the simplification in either
# direction is low: an accidental extra match just requires one more
# (already-required) design comment; a missed one leaves the gate silent
# for that commit, same as any other "unmeasurable -> never guess" case.
#
# Bypass: `[no-design: <reason>]` anywhere in the command, logged to
# $HOME/devel/airuleset/audits/no-design-skips.log — same fixed-path
# convention and bare-tag-rejected/reasoned-tag-logged shape as
# pre-push-test-check.sh's `[no-test: <reason>]`.

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -n "$CMD" ] || exit 0

# Scope: autopilot-worker only.
[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0

# Must be a real `git commit` invocation at a statement boundary.
echo "$CMD" | grep -qE '(^|[;&|]|&&)[[:space:]]*(sudo[[:space:]]+|env[[:space:]]+)?git[[:space:]]+commit\b' || exit 0

AUDIT_LOG="$HOME/devel/airuleset/audits/no-design-skips.log"
mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true

# Reject a bare bypass with no reason.
if echo "$CMD" | grep -qE '\[no-design\](\s|$)'; then
    echo "" >&2
    echo "🚫 BLOCKED: Bare [no-design] is not accepted." >&2
    echo "" >&2
    echo "  Use [no-design: <reason>] explaining WHY the design comment cannot" >&2
    echo "  be posted right now (the reason is logged to" >&2
    echo "  $AUDIT_LOG)." >&2
    echo "" >&2
    exit 2
fi

# Honor a reasoned bypass, but log it (newline-flattened so a reason that
# wraps a line still has its opening/closing brackets on one grep-able line,
# the same fix pre-push-test-check.sh's own bypass parsing needed).
CMD_FLAT=$(printf '%s' "$CMD" | tr '\n' ' ')
if echo "$CMD_FLAT" | grep -qE '\[no-design:\s*[^]]+\]'; then
    REASON=$(echo "$CMD_FLAT" | grep -oE '\[no-design:\s*[^]]+\]' | head -1)
    echo "$(date -Iseconds)  session=$SID  $REASON" >> "$AUDIT_LOG" 2>/dev/null || true
    exit 0
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into a `python3 -` heredoc (this repo's own
# recurring trap — see subagent-stop-check-run-card.sh).
OUT=$(python3 - "$REPO_ROOT" "$CMD" "$CWD" <<'PYEOF' 2>/dev/null || true
import sys

repo_root, cmd, cwd = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo_root)
try:
    import design_gate as dg
    import notify
except Exception:
    sys.exit(0)

if not cwd:
    sys.exit(0)

refs = dg.issue_refs(cmd)
if not refs:
    sys.exit(0)

repo_key = notify.repo_name_for(cwd)
if not repo_key:
    sys.exit(0)          # unmeasurable (no origin) -> never guess, never block

missing = [n for n in refs if not dg.marker_exists(repo_key, n)]
if missing:
    print(repo_key)
    print(" ".join(str(n) for n in missing))
PYEOF
)
[ -n "$OUT" ] || exit 0

REPO=$(printf '%s\n' "$OUT" | sed -n '1p')
NUMS=$(printf '%s\n' "$OUT" | sed -n '2p')
[ -n "$REPO" ] && [ -n "$NUMS" ] || exit 0

LIST=$(printf '%s' "$NUMS" | sed 's/\([0-9][0-9]*\)/#\1/g')

cat >&2 <<MSG

🚫 BLOCKED: no design comment posted yet for ${LIST} (repo ${REPO}).

Per autonomous-batch-issue-development.md's design-before-code step, post
root cause + chosen approach + rejected alternative to the ticket BEFORE
this commit:

  gh issue comment <N> --body "<root cause> ... <chosen approach> ... <rejected alternative> ..."

(or -F a body file). One honest paragraph is enough for a scoped fix — it
just has to exist, and it has to come before this commit. Once it posts,
hooks/post-record-design-comment.sh records it automatically and this
commit will go through.

Bypass (rare, logged): add [no-design: <reason>] to this commit's message
— never for a real feature/fix with a genuine design decision behind it.
MSG
exit 2
