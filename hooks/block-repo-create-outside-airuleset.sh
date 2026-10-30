#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK creating a GitHub repository from any session
# whose checkout is not airuleset (#1270: the iemmixer session created dante-asio
# by itself under `newlevel`, with no account, registry row, webterm lane or
# channels). A new project goes through airuleset. Reads .tool_input.command and
# .cwd on STDIN. Exit 2 = block. Bypass (rare, logged): # airuleset:repo-create-ok <reason>

INPUT=$(cat)
RESULT=$(printf '%s' "$INPUT" | python3 -I -c '
import json, re, shlex, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command") or ""
if "airuleset:repo-create-ok" in cmd:
    print("bypass"); sys.exit(0)
cmd = re.sub(r"<<-?\s*[\x27\"]?(\w+)[\x27\"]?.*?^\s*\1\s*$", " ", cmd, flags=re.S | re.M)
def segments(text, depth=0):
    for seg in re.split(r"&&|\|\||[;|\n]", text):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if depth < 3 and len(toks) >= 3 and toks[0] in ("bash", "sh") and toks[1] == "-c":
            yield from segments(toks[2], depth + 1)
        else:
            yield toks
def creates(t):
    if "gh" not in t:
        return False
    t = t[t.index("gh") + 1:]
    if t[:2] in (["repo", "create"], ["repo", "fork"]):
        return True
    if t[:1] != ["api"]:
        return False
    path = [x.lstrip("/") for x in t[1:] if not x.startswith("-")]
    if not any(re.fullmatch(r"(user|orgs/[^/]+)/repos", p) for p in path):
        return False
    return any(x in ("-f", "-F", "--field", "--raw-field", "--input") or x.startswith(("-f", "-F"))
               for x in t[1:]) or "POST" in t
if any(creates(t) for t in segments(cmd)):
    print("create")
' 2>/dev/null || true)

[ -z "$RESULT" ] && exit 0
if [ "$RESULT" = "bypass" ]; then
  echo "$(date -Is) repo-create bypass" >> "${HOME}/.claude/repo-create-bypass.log" 2>/dev/null || true
  exit 0
fi
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
