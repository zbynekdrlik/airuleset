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

def strip_carried_text(cmd):
    """Drop everything the command CARRIES but does not DO.

    A `gh issue comment -F body.md <<'EOF' ... EOF` whose body describes the
    trigger table is not a merge, a push or a deploy — it is a document. Same
    lesson as #80: classify the command, never its payload. Heredoc bodies go
    first, then quoted spans (which also covers `git commit -m "... gh pr
    merge ..."` and `echo 'gh pr merge 5'`).
    """
    out, i, n = [], 0, len(cmd)
    heredocs = re.findall(r"<<-?\s*['\"]?(\w+)['\"]?", cmd)
    if heredocs:
        lines, keep, pending = cmd.split("\n"), [], None
        for line in lines:
            if pending is not None:
                if line.strip() == pending:
                    pending = None
                continue
            keep.append(line)
            m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
            if m:
                pending = m.group(1)
        cmd = "\n".join(keep)
        n = len(cmd)
    while i < n:
        ch = cmd[i]
        if ch in "'\"":
            j = cmd.find(ch, i + 1)
            if j == -1:
                break
            out.append(" ")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


if event == "UserPromptSubmit" or (not tool and payload.get("prompt")):
    surface = "UserPromptSubmit"
    haystack = str(payload.get("prompt") or "")
    raw_haystack = haystack
    out_event = "UserPromptSubmit"
else:
    surface = tool or "Bash"
    tool_input = payload.get("tool_input") or {}
    out_event = "PreToolUse"
    if surface == "Bash":
        # the real command only — not the document it carries
        raw_cmd = str(tool_input.get("command") or "")
        haystack = strip_carried_text(raw_cmd)
        # #631 -- a RAW copy that KEEPS quoted strings + heredoc bodies, for
        # the RAW_BASH_MATCH_TOPICS rows below. strip_carried_text() is the
        # RIGHT default (it stops a `git commit -m "... gh pr merge ..."`
        # MENTION from injecting), but it deletes the api.cloudflare.com URL
        # from a token-verifying script that carries it INSIDE a quoted
        # Python/heredoc string -- exactly the shape that most needs the skill.
        raw_haystack = raw_cmd
        if tool_input.get("run_in_background") is True:
            haystack += " run_in_background=true"
            raw_haystack += " run_in_background=true"
    else:
        try:
            haystack = json.dumps(tool_input, ensure_ascii=False)
            raw_haystack = haystack
        except Exception:
            sys.exit(0)

# A session id is attacker-irrelevant but is used as a filename: keep it tame.
session = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:64]
marker_dir = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "airuleset-situational-" + session
)

MAX_BODY = 24000
# One tool call must never dump a pile of bodies into the context. Anything
# over the budget is DEFERRED, not consumed — its marker stays unset so its
# own action can still load it later.
MAX_TOTAL = 14000

# #631 -- Bash topics whose PATTERN is matched against the RAW command
# (quotes + heredoc bodies intact), NOT the strip_carried_text()-stripped
# haystack every other Bash row uses. A false-positive here is cheap (one
# deferred injection, once per session); a false-NEGATIVE deleted the owner's
# master Cloudflare token (the verify endpoint LIES for cfat_ tokens, a script
# calls it from inside a quoted string, the quote-stripping erased the URL, the
# skill never loaded). So the cloudflare row -- the one shape that most needs
# the skill (a script actually touching the API) -- matches the raw command.
RAW_BASH_MATCH_TOPICS = {"cloudflare-api-tokens"}


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
    if len(parts) not in (4, 5):
        continue
    topic, tool_pat, pattern, body_rel = parts[:4]
    exclude = parts[4] if len(parts) == 5 else ""

    # #631 -- a RAW_BASH_MATCH_TOPICS row on a Bash surface matches (and is
    # excluded) against the un-stripped command, so a quoted/heredoc
    # api.cloudflare.com URL still fires. Every other row keeps the stripped
    # haystack, so the merge/push/deploy MENTION false-positives stay fixed.
    hay = raw_haystack if (surface == "Bash" and topic in RAW_BASH_MATCH_TOPICS) else haystack

    try:
        if not re.fullmatch(tool_pat, surface):
            continue
        if not re.search(pattern, hay):
            continue
        if exclude and re.search(exclude, hay):
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

    if sum(len(c) for c in chunks) + len(body) > MAX_TOTAL and chunks:
        continue  # defer: leave the marker unset so its own action still loads it

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
