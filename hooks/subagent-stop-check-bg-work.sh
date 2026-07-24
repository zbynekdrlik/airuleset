#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop (airuleset #28)
# A SUBAGENT that ends its turn with in-flight background work TERMINATES —
# the detached task's completion fires to the PARENT, never to the now-gone
# subagent (ci-monitoring.md; ~40% of autopilot-worker failures; odoo-erp
# worker #2061/PR #2063 died mid-CI-monitor 2026-07-24). The rule exists in
# prose and workers violate it anyway — this hook is the mechanism: it parses
# the subagent's OWN transcript for background launches without a terminal
# completion and BLOCKS the stop, telling the worker to wait FOREGROUND,
# fetch the result (TaskOutput), or TaskStop the task before returning.
#
# Detection (shapes verified on real transcripts, 2026-07-24):
#   launched  = toolUseResult.backgroundTaskId (Bash run_in_background)
#             | toolUseResult.taskId           (Monitor — always async)
#             | toolUseResult.agentId if isAsync (background child Agent)
#   terminal  = a task-notification line carrying BOTH <task-id>ID</task-id>
#               AND a <status> tag (a Monitor MID-STREAM <event> has no
#               <status> — the task is still live), or a TaskStop/KillShell
#               tool_use naming the id.
# Fail-open everywhere: no jq/python, missing/unreadable transcript, parse
# errors, and after MAX_BLOCKS blocks per (session, agent) — the transcript
# is written asynchronously and may lag a completion that already happened.

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // "unknown"' 2>/dev/null || echo "unknown")

[ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0

BLOCK_FILE="/tmp/airuleset-subagent-bgwork-block-${SESSION_ID}-${AGENT_ID}"
BLOCKS=$(cat "$BLOCK_FILE" 2>/dev/null || echo 0)
MAX_BLOCKS=3
if [ "$BLOCKS" -ge "$MAX_BLOCKS" ] 2>/dev/null; then
    exit 0     # fail open — the async transcript may lag a real completion
fi

LIVE=$(python3 - "$TRANSCRIPT" <<'PYEOF' 2>/dev/null || echo ""
import json
import re
import sys

launched = []          # ordered, deduped
terminal = set()
NOTIF_ID = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")

try:
    fh = open(sys.argv[1], encoding="utf-8", errors="replace")
except OSError:
    sys.exit(0)

for line in fh:
    # terminal completions / kills — raw-text scan (the notification XML sits
    # inside a JSON string; '<' is never escaped by json.dumps)
    if "<task-id>" in line:
        ids = NOTIF_ID.findall(line)
        if ids and "<status>" in line:
            terminal.update(ids)
    if '"TaskStop"' in line or '"KillShell"' in line:
        try:
            e = json.loads(line)
            for blk in (e.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use" \
                        and blk.get("name") in ("TaskStop", "KillShell"):
                    for v in (blk.get("input") or {}).values():
                        if isinstance(v, str):
                            terminal.add(v)
        except Exception:
            pass
    # background launches — the toolUseResult sidecar on the tool_result entry
    if '"toolUseResult"' not in line:
        continue
    try:
        e = json.loads(line)
    except Exception:
        continue
    tur = e.get("toolUseResult")
    if not isinstance(tur, dict):
        continue
    tid = tur.get("backgroundTaskId") or tur.get("taskId")
    if not tid and tur.get("isAsync"):
        tid = tur.get("agentId")
    if isinstance(tid, str) and tid and tid not in launched:
        launched.append(tid)

live = [t for t in launched if t not in terminal]
print(" ".join(live))
PYEOF
)
LIVE=$(echo "$LIVE" | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')

[ -z "$LIVE" ] && { rm -f "$BLOCK_FILE"; exit 0; }

echo $((BLOCKS + 1)) > "$BLOCK_FILE"

REASON="You still have IN-FLIGHT background work: task(s) ${LIVE}. You are a \
SUBAGENT — if you end your turn now you TERMINATE and the completion \
notification fires to your PARENT, not to you (ci-monitoring.md; this killed \
~40% of autopilot workers). Do ONE of these NOW, then finish: (1) wait \
FOREGROUND — a bounded poll loop of plain foreground Bash calls (e.g. \
'sleep 300 && gh run view <id> --json status,conclusion', repeated until \
terminal), NEVER run_in_background; (2) fetch the finished result with \
TaskOutput(task_id); (3) if your dispatch contract hands the wait to the \
supervisor, TaskStop the task(s) first and report the run-id/state in your \
final message. A detached background task must never outlive your turn."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
