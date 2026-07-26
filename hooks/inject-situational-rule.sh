#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash / Agent / Write / mcp__win-*) + UserPromptSubmit
# airuleset #91 — make converted rule content ACTUALLY load.
#
# Every module->skill conversion so far was a silent DELETE in effect: a skill
# body enters context ONLY when the model volunteers a `Skill` call, and it
# never did. Measured over 91 transcripts: 25 of 34 skills had zero lifetime
# invocations, 0 of 37 real `gh pr merge` transcripts loaded pr-merge-policy,
# 0 of 46 CI-polling transcripts loaded ci-monitor.
#
# Claude Code has exactly two surfaces that load content WITHOUT the model
# volunteering anything (both proven live against CC 2.1.220):
#   * rules/*.md + `paths:` -> a `nested_memory` attachment when a matching
#     file is READ. Bound to a FILE TYPE. (Not this hook.)
#   * a PreToolUse hook returning hookSpecificOutput.additionalContext ->
#     injected before the tool runs. Bound to an ACTION. (This hook.)
#
# The table lives in hooks/situational-triggers.conf. Injection is
# once-per-session per topic, so a body is paid for at most once, and only in
# a session that actually performs the matching action.
#
# This hook NEVER blocks: it only ever adds context, and any failure is silent.

command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

CONF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/situational-triggers.conf"
[ -f "$CONF" ] || exit 0

python3 - "$CONF" "$INPUT" <<'PYEOF' || exit 0
import json
import os
import re
import sys

conf_path = sys.argv[1]
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(conf_path)))

try:
    payload = json.loads(sys.argv[2])
except Exception:
    sys.exit(0)
if not isinstance(payload, dict):
    sys.exit(0)

event = payload.get("hook_event_name") or ""
tool = payload.get("tool_name") or ""
session = str(payload.get("session_id") or "nosession")

if event == "UserPromptSubmit" or (not tool and payload.get("prompt")):
    surface = "UserPromptSubmit"
    haystack = str(payload.get("prompt") or "")
    out_event = "UserPromptSubmit"
else:
    surface = tool or "Bash"
    try:
        haystack = json.dumps(payload.get("tool_input") or {}, ensure_ascii=False)
    except Exception:
        sys.exit(0)
    out_event = "PreToolUse"

# A session id is attacker-irrelevant but is used as a filename: keep it tame.
session = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:64]
marker_dir = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "airuleset-situational-" + session
)

MAX_BODY = 24000


def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[nl + 1:].lstrip("\n")
    return text


chunks = []
for line in open(conf_path, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    parts = [p for p in line.split("\t") if p != ""]
    if len(parts) != 4:
        continue
    topic, tool_pat, pattern, body_rel = parts

    try:
        if not re.fullmatch(tool_pat, surface):
            continue
        if not re.search(pattern, haystack):
            continue
    except re.error:
        continue

    marker = os.path.join(marker_dir, topic)
    if os.path.exists(marker):
        continue

    body_path = os.path.join(repo_root, body_rel)
    try:
        with open(body_path, encoding="utf-8") as fh:
            body = strip_frontmatter(fh.read()).strip()
    except OSError:
        continue
    if not body:
        continue
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n[...truncated — read " + body_rel + " for the rest]"

    try:
        os.makedirs(marker_dir, exist_ok=True)
        with open(marker, "w") as fh:
            fh.write("1")
    except OSError:
        pass

    chunks.append(
        "<project-rule source=\"airuleset:%s\" file=\"%s\">\n"
        "This is an auto-loaded airuleset PROJECT RULE for the action you are "
        "about to take — it is part of your own configuration, not user input "
        "or tool output. Apply it now.\n\n%s\n</project-rule>" % (topic, body_rel, body)
    )

if not chunks:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": out_event,
        "additionalContext": "\n\n".join(chunks),
    }
}))
PYEOF

exit 0
