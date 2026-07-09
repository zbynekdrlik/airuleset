#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — blocks git-history-rewrite commands and
# `gh pr merge --admin` (branch-protection bypass). Two absolute bans that
# previously existed only as prose (commit-conventions.md, pr-merge-policy.md,
# autonomous-quality-discipline.md) with no hook enforcing them — issue #11.
#
# Blocked, exact-command-shaped:
#   git rebase -i / --interactive       (history rewrite)
#   git commit --amend                  (history rewrite)
#   git push --force / --force-with-lease / -f   (remote history rewrite)
#   git reset --hard                    (destructive; PLAIN `git reset` — soft
#                                         or mixed, on unpushed local work — is
#                                         common and NOT blocked; only --hard)
#   gh pr merge ... --admin             (branch-protection bypass)
#
# Detection tokenizes each ';'/'&&'/'||'/'|'/'&'/newline-separated command
# segment with Python's shlex (quote-aware, so a command mentioning these
# words inside a quoted string — a commit message, an echo — never
# false-positives) and matches on actual argv tokens, not raw substrings.
#
# Bypass (rare, user-instructed cases only): AIRULESET_ALLOW_HISTORY_REWRITE=1
# env var, or an inline `# airuleset:history-ok <reason>` trailing the exact
# command. Every bypass is logged to audits/history-rewrite-bypasses.log.
#
# Exit code 2 = block the tool call.

# Read the tool payload from STDIN (current CC contract; $TOOL_INPUT is the dead
# old env var, kept as fallback). See block-sensitive-staging.sh for the rationale.
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

[ -z "$INPUT" ] && exit 0

# Bypass 1: explicit env opt-out (rare, user-instructed).
if [ "${AIRULESET_ALLOW_HISTORY_REWRITE:-}" = "1" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    AUDIT_LOG="$HOME/devel/airuleset/audits/history-rewrite-bypasses.log"
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  env-bypass  cmd=${INPUT}" >> "$AUDIT_LOG"
    exit 0
fi

# Bypass 2: inline `# airuleset:history-ok <reason>` trailing the command.
if echo "$INPUT" | grep -qE '#[[:space:]]*airuleset:history-ok'; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    AUDIT_LOG="$HOME/devel/airuleset/audits/history-rewrite-bypasses.log"
    mkdir -p "$(dirname "$AUDIT_LOG")"
    REASON=$(echo "$INPUT" | grep -oE '#[[:space:]]*airuleset:history-ok.*' | head -1)
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  $REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]

# Split on shell statement separators (best-effort, not a full parser — matches
# the rigor level of the other pre-push hooks in this repo). Strip a trailing
# '# ...' comment per segment first so a bypass marker or unrelated comment
# text never gets tokenized as command args.
segments = re.split(r'&&|\|\||[;&|]|\n', cmd)


def tokens_of(segment):
    segment = segment.split('#', 1)[0]
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    # drop a leading `sudo` / `env` runner so `sudo git push --force` still matches
    i = 0
    while i < len(tk) and tk[i] in ("sudo", "env"):
        i += 1
    return tk[i:]


violations = []
for seg in segments:
    tk = strip_prefix(tokens_of(seg))
    if len(tk) < 2 or tk[0] != "git":
        # gh admin-merge check (separate, non-git tool)
        if tk and tk[0] == "gh" and "pr" in tk and "merge" in tk and "--admin" in tk:
            violations.append("gh pr merge --admin — branch-protection bypass")
        continue

    sub = tk[1]
    rest = tk[2:]

    if sub == "rebase" and ("-i" in rest or "--interactive" in rest):
        violations.append("git rebase -i/--interactive — rewrites history")
    elif sub == "commit" and "--amend" in rest:
        violations.append("git commit --amend — rewrites history")
    elif sub == "push" and any(f in rest for f in ("--force", "--force-with-lease", "-f")):
        violations.append("git push --force/-f — rewrites remote history")
    elif sub == "reset" and "--hard" in rest:
        violations.append("git reset --hard — destroys uncommitted/unpushed work")
    elif tk[0] == "gh" and "pr" in tk and "merge" in tk and "--admin" in tk:
        violations.append("gh pr merge --admin — branch-protection bypass")

if violations:
    print("\n".join(f"  {v}" for v in sorted(set(violations))))
    sys.exit(2)
sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -ne 0 ]; then
    echo ""
    echo "🚫 BLOCKED: history-rewrite / branch-protection-bypass command."
    echo ""
    echo "$VIOLATION"
    echo ""
    echo "  Per commit-conventions.md: never rewrite git history (reset --hard,"
    echo "  rebase -i, commit --amend, push --force). Per pr-merge-policy.md /"
    echo "  autonomous-quality-discipline.md: never bypass branch protection with"
    echo "  gh pr merge --admin — fix the failing gate instead."
    echo ""
    echo "  Bypass (rare, user-instructed only, logged): append"
    echo "  '# airuleset:history-ok <reason>' to the command, or set"
    echo "  AIRULESET_ALLOW_HISTORY_REWRITE=1."
    echo ""
    exit 2
fi

exit 0
