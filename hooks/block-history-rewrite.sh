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
# The comment-strip that runs BEFORE shlex is itself quote-aware too: a
# naive `text.split('#', 1)[0]` truncates INSIDE a quoted commit message
# that happens to contain a routine issue reference ("fix #12: adjust"),
# corrupting the segment before shlex even sees it (then --amend etc. is
# silently lost from the token list) — see strip_unquoted_comment below.
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
# The marker must be OUTSIDE any quoted string — a real bash `#` only
# starts a comment when it is not inside quotes, so quoted spans are
# stripped FIRST (same technique as block-sensitive-staging.sh, d1fde9b,
# and block-destructive-remote.sh). Without this, the marker text merely
# being MENTIONED inside an unrelated quoted string (documentation, an
# echo) would bypass the ENTIRE check, including a genuinely dangerous
# UNRELATED command elsewhere on the same line.
BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)     # strip '"'"'...'"'"' spans
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)  # strip "..." spans
m=None
for mm in re.finditer(r"#[ \t]*airuleset:history-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    AUDIT_LOG="$HOME/devel/airuleset/audits/history-rewrite-bypasses.log"
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  # airuleset:history-ok $BYPASS_REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]

# Split on shell statement separators (best-effort, not a full parser — matches
# the rigor level of the other pre-push hooks in this repo).
segments = re.split(r'&&|\|\||[;&|]|\n', cmd)


def strip_unquoted_comment(text):
    """Truncate `text` at the first '#' that is OUTSIDE any quoted span — a
    real bash `#` only starts a comment when it is NOT inside quotes. The
    old naive `text.split('#', 1)[0]` truncated INSIDE a quoted commit
    message that happens to contain a routine issue reference
    ("fix #12: adjust"), corrupting the segment before shlex even sees it
    (shlex then fails on the resulting unmatched quote and falls back to a
    naive .split() that silently drops flags like --amend)."""
    in_sq = in_dq = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_sq:
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if c == '\\' and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == "'":
            in_sq = True
            i += 1
            continue
        if c == '"':
            in_dq = True
            i += 1
            continue
        if c == '#':
            return text[:i]
        i += 1
    return text


def tokens_of(segment):
    segment = strip_unquoted_comment(segment)
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def strip_prefix(tk):
    # drop a leading `sudo` / `env` runner AND any leading `VAR=val`
    # environment-assignment token(s) — `GIT_AUTHOR_DATE=x git push
    # --force` must be detected exactly like `git push --force`.
    i = 0
    while i < len(tk) and (tk[i] in ("sudo", "env") or ASSIGN_RE.match(tk[i])):
        i += 1
    return tk[i:]


violations = []
for seg in segments:
    tk = strip_prefix(tokens_of(seg))
    if len(tk) < 2 or tk[0] != "git":
        # gh admin-merge check (separate, non-git tool) — this is the ONLY
        # reachable place that detects it: `tk[0] == "git"` is guaranteed
        # false or tk is too short whenever we're in this branch, so a
        # second `tk[0] == "gh"` check inside the git-only branch below
        # would be unreachable dead code (removed).
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

if violations:
    print("\n".join(f"  {v}" for v in sorted(set(violations))))
    sys.exit(2)
sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -eq 2 ]; then
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
elif [ "$RC" -ne 0 ]; then
    # A non-2 nonzero exit means the CHECK ITSELF malfunctioned (missing
    # python3, an internal bug) — never a real history-rewrite violation.
    # Fail CLOSED but say so HONESTLY instead of reusing the empty-reason
    # "BLOCKED: history-rewrite" message.
    echo ""
    echo "🚫 BLOCKED (fail-closed): block-history-rewrite.sh internal error"
    echo "  — python3 exited $RC instead of running the check."
    echo "$VIOLATION"
    echo ""
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation —"
    echo "  investigate and fix the hook (or install python3) before retrying."
    echo ""
    exit 2
fi

exit 0
