#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — issue #945.
#
# Blocks a direct `gh pr merge` command on REDUCED-authority sub-dev streams
# (authority != `full`). Sub-dev streams must merge via their project's
# `scripts/stream_merge_guard.py`, which enforces stream-specific merge
# preconditions before calling `gh pr merge` internally.
#
# When a model runs `python3 scripts/stream_merge_guard.py --repo X --pr N`,
# PreToolUse sees THAT command (no `gh pr merge` tokens) and this hook exits 0
# naturally — the guard's internal subprocess is invisible at PreToolUse time.
# Only a bare `gh pr merge ...` typed directly by the model is caught.
#
# Scope:
#   - ONLY reduced-authority boxes are gated (`airuleset.resolve_authority(cwd)`
#     != `full`; same single-source-of-truth as sibling hooks).
#   - Full-authority boxes (gk/controller/dev1) are UNAFFECTED (exit 0).
#   - Authority resolution failure -> exit 0 (fail-open, degrade-to-allow,
#     same bias as block-gk-request-without-selfservice.sh / #390).
#   - `gh pr merge --admin` is ALREADY blocked universally by
#     block-history-rewrite.sh — this hook is additive (bare merge).
#
# Detection: tokenizes the command with Python's shlex (quote-aware, same
# approach as block-history-rewrite.sh) and checks for `gh`, `pr`, `merge`
# as actual argv tokens in any command segment. Heredoc bodies are stripped
# before segment splitting to prevent false blocks on documentation text.
#
# Accepted residuals (under-block, model-discipline not adversary boundary):
#   - `bash -c "gh pr merge 42"` (interpreter payload)
#   - `eval "gh pr merge 42"` (eval)
#   - `gh api -X PUT repos/o/r/pulls/42/merge` (API merge)
#   - subshell `(gh pr merge 42)` / brace group `{ gh pr merge 42; }`
#
# Exit code 2 = block the tool call.
# Bypass: `# airuleset:stream-merge-ok <reason>` inline, logged to
#   ~/devel/airuleset/audits/stream-merge-bypasses.log.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: only classify commands that could plausibly contain
# `gh pr merge`.
case "$CMD" in
    *gh*merge*) ;;
    *) exit 0 ;;
esac

# Bypass: inline marker — quote-stripped before matching (F1 fix, porting the
# block-history-rewrite.sh:62-81 pattern so a marker MENTIONED inside a quoted
# string never disarms the guard for a real `gh pr merge` elsewhere).
BYPASS_REASON=$(printf '%s' "$CMD" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)
m=None
for mm in re.finditer(r"#[ \t]*airuleset:stream-merge-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    AUDIT_LOG="$HOME/devel/airuleset/audits/stream-merge-bypasses.log"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    {
        echo "$(date -Iseconds)  cwd=$(pwd)  inline-bypass  # airuleset:stream-merge-ok $BYPASS_REASON"
    } >> "$AUDIT_LOG" 2>/dev/null || true
    exit 0
fi

# #682: route all stdout to stderr so the model reads the block reason.
exec 1>&2

# Resolve REPO_ROOT_DIR for the authority import (same pattern as
# block-gk-request-without-selfservice.sh).
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
REPO_ROOT_DIR=""
[ -n "$HOOK_DIR" ] && REPO_ROOT_DIR="$(dirname "$HOOK_DIR")"

RC=0
python3 - "$CMD" "$(pwd)" "$REPO_ROOT_DIR" <<'PYEOF' || RC=$?
import re
import shlex
import sys

cmd = sys.argv[1]
cwd = sys.argv[2]
repo_dir = sys.argv[3] if len(sys.argv) > 3 else ""


# --- authority gate -------------------------------------------------------
# Engage ONLY for a reduced sub-dev stream account. Full-authority or
# unresolvable -> exit 0 (degrade-to-allow, same bias as #390/#516).

def _reduced_authority():
    try:
        if repo_dir and repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        import airuleset as _ar
        profile = _ar.resolve_authority(cwd)
        return profile is not None and profile != "full"
    except Exception:
        return None  # unresolvable -> caller skips


reduced = _reduced_authority()
if not reduced:
    # full-authority, or authority unresolvable -> not gated.
    sys.exit(0)


# --- heredoc body stripping (F2 fix) --------------------------------------
# Strip heredoc bodies BEFORE segment splitting so that documentation text
# like `gh pr merge 42` inside a heredoc does not false-block.
# Non-executing consumers (cat/tee) have their bodies blanked; executing
# consumers (bash/sh) are left scanned (documented residual).

_HEREDOC_RE = re.compile(
    r"<<-?[ \t]*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?[ \t]*\n"
    r"(.*?)\n[ \t]*\1(?:\n|$)",
    re.DOTALL,
)
_EXEC_CONSUMERS = {"bash", "sh", "zsh", "dash", "ksh", "python3", "python", "perl", "ruby"}


def _strip_heredocs(text):
    """Blank non-executing heredoc bodies to prevent false blocks."""
    def _replacer(m):
        # Check the consumer: if it's an executing consumer, keep the body
        # for scanning. Otherwise blank it.
        start = m.start()
        # Look backwards for the consumer command word
        prefix = text[:start].rstrip()
        consumer = prefix.split()[-1] if prefix.split() else ""
        consumer = consumer.split("/")[-1]  # strip path
        if consumer in _EXEC_CONSUMERS:
            return m.group(0)  # keep for scanning
        # Blank the body, keep the delimiters
        delim = m.group(1)
        return "<<" + delim + "\n" + delim + "\n"
    return _HEREDOC_RE.sub(_replacer, text)


cmd_clean = _strip_heredocs(cmd)


# --- command classification ------------------------------------------------
# Split on shell statement separators (quote-aware via _strip_comment +
# shlex), check for `gh pr merge` as actual argv tokens.

segments = re.split(r'&&|\|\||[;&|]|\n', cmd_clean)

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

# F3 fix: extended wrapper set (per #817 lesson from git_write_classify.py).
_WRAPPERS = {"sudo", "env", "command", "nohup", "time", "nice", "exec"}


def _strip_comment(text):
    """Truncate at the first '#' outside any quoted span."""
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


def _tokens(segment):
    segment = _strip_comment(segment)
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _strip_prefix(tk):
    """Drop leading wrappers and VAR=val assignments."""
    i = 0
    while i < len(tk) and (tk[i] in _WRAPPERS or ASSIGN_RE.match(tk[i])):
        i += 1
    return tk[i:]


found = False
for seg in segments:
    tk = _strip_prefix(_tokens(seg))
    if len(tk) < 3:
        continue
    # Match: gh pr merge (positional — gh as command, pr as subcommand,
    # merge as sub-subcommand, matching the exact gh CLI grammar).
    if tk[0] == "gh" and tk[1] == "pr" and tk[2] == "merge":
        found = True
        break

if not found:
    sys.exit(0)

# Blocked.
print("")
print("\U0001f6ab BLOCKED: sub-dev streams must not run `gh pr merge` directly.")
print("")
print("  Use the sanctioned merge guard instead:")
print("    python3 scripts/stream_merge_guard.py --repo <owner/name> --pr <N>")
print("")
print("  The guard enforces stream-specific merge preconditions before merging.")
print("  See odoo-erp issue 6558.")
print("")
print("  Bypass (rare, logged): append")
print("  '# airuleset:stream-merge-ok <reason>' to the command.")
print("")
sys.exit(2)
PYEOF

exit "$RC"
