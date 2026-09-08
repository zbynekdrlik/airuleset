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
#   - Authority resolution failure → exit 0 (fail-open, degrade-to-allow,
#     same bias as block-gk-request-without-selfservice.sh / #390).
#   - `gh pr merge --admin` is ALREADY blocked universally by
#     block-history-rewrite.sh — this hook is additive (bare merge).
#
# Detection: tokenizes the command with Python's shlex (quote-aware, same
# approach as block-history-rewrite.sh) and checks for `gh`, `pr`, `merge`
# as actual argv tokens in any command segment.
#
# Exit code 2 = block the tool call.
# Bypass: `# airuleset:stream-merge-ok <reason>` inline (logged).

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: only classify commands that could plausibly contain
# `gh pr merge`.
case "$CMD" in
    *gh*merge*) ;;
    *) exit 0 ;;
esac

# Bypass: inline marker (logged).
case "$CMD" in *"airuleset:stream-merge-ok"*) exit 0 ;; esac

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
# unresolvable → exit 0 (degrade-to-allow, same bias as #390/#516).

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


# --- command classification ------------------------------------------------
# Split on shell statement separators, tokenize with shlex, check for
# `gh pr merge` as actual argv tokens (not inside quoted strings).

segments = re.split(r'&&|\|\||[;&|]|\n', cmd)

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


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
    """Drop leading sudo/env and VAR=val assignments."""
    i = 0
    while i < len(tk) and (tk[i] in ("sudo", "env") or ASSIGN_RE.match(tk[i])):
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
