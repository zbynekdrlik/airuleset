#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK creating a GitHub repository from any session
# whose checkout is not airuleset (#1270: the iemmixer session created dante-asio
# by itself under `newlevel`, with no account, registry row, webterm lane or
# channels). A new project goes through airuleset. Reads .tool_input.command and
# .cwd on STDIN. Exit 2 = block. Bypass (rare, logged): # airuleset:repo-create-ok <reason>

INPUT=$(cat)
case "$INPUT" in
  *gh*) ;;
  *) exit 0 ;;
esac
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RESULT=$(printf '%s' "$INPUT" | python3 -I "$HERE/repo_create_guard.py" 2>/dev/null || true)

[ -z "$RESULT" ] && exit 0
case "$RESULT" in
  bypass*)
    mkdir -p "${HOME}/.claude" 2>/dev/null || true
    echo "$(date -Is) $RESULT" >> "${HOME}/.claude/repo-create-bypass.log" 2>/dev/null || true
    exit 0
    ;;
esac
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
URL=$(git -C "${CWD:-.}" config --get remote.origin.url 2>/dev/null || echo "")
case "$URL" in
  *zbynekdrlik/airuleset|*zbynekdrlik/airuleset.git) exit 0 ;;
esac
cat >&2 <<'MSG'
BLOCKED: a new repository is created only by airuleset (#1270). A repo made here
has no project account, registry row, webterm tabs or channels.
Ask for it instead: gh issue create -R zbynekdrlik/airuleset (what the project is,
which repo, who it talks to). Rare bypass, logged: # airuleset:repo-create-ok <reason>
MSG
exit 2
