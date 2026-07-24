#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop (airuleset #28)
# A SUBAGENT that ends its turn with in-flight background work TERMINATES —
# the detached task's completion fires to the PARENT, never to the now-gone
# subagent (ci-monitoring.md; ~40% of autopilot-worker failures; odoo-erp
# worker #2061/PR #2063 died mid-CI-monitor 2026-07-24). The rule exists in
# prose and workers violate it anyway — this hook is the mechanism: it BLOCKS
# the stop while background work is live, telling the worker to wait
# FOREGROUND and TaskStop every stray task before returning.
#
# PRIMARY detection (live-fired E2E 2026-07-24, CC 2.1.x): the payload's
# `background_tasks` array — the harness's OWN live-task list (shells /
# monitors / child subagents) with current statuses. Authoritative and
# lag-free. live = entries with status "running" MINUS the stopping
# subagent's SELF entry (id == agent_id — always present and "running" at
# its own stop). When the key exists, the transcript is never consulted.
#
# FALLBACK (CC versions without the field): parse the subagent's OWN
# transcript — `agent_transcript_path`; `transcript_path` is the PARENT
# session's file (verified live — parsing it missed every subagent launch):
#   launched  = toolUseResult.backgroundTaskId (Bash run_in_background)
#             | toolUseResult.taskId           (Monitor — always async)
#             | toolUseResult.agentId if isAsync (background child Agent)
#             | the tool_result CONTENT string ("Command running in
#               background with ID: X" / "Monitor started (task X" /
#               "Async agent launched … agentId: X") — a SUBAGENT
#               transcript's launch entry carries NO toolUseResult sidecar
#               (the restreamer specimen; caught live 2026-07-24). Only
#               tool_result blocks are scanned — assistant text merely
#               QUOTING the harness wording never counts as a launch.
#   terminal  = a task-notification line carrying BOTH <task-id>ID</task-id>
#               AND a <status> tag (a Monitor MID-STREAM <event> has no
#               <status> — the task is still live), or a TaskStop/KillShell
#               tool_use naming the id.
# Fail-open everywhere: no jq/python, missing/unreadable transcript, parse
# errors, and after MAX_BLOCKS blocks per (session, agent) — the fallback
# transcript is written asynchronously and may lag a completion (observed
# live: a lagged launch missed on run 1, an over-block after cleanup on
# run 2 — the payload path has neither problem).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // "unknown"' 2>/dev/null || echo "unknown")

BLOCK_FILE="/tmp/airuleset-subagent-bgwork-block-${SESSION_ID}-${AGENT_ID}"
BLOCKS=$(cat "$BLOCK_FILE" 2>/dev/null || echo 0)
MAX_BLOCKS=3
if [ "$BLOCKS" -ge "$MAX_BLOCKS" ] 2>/dev/null; then
    exit 0     # fail open — never wedge a subagent in an endless block loop
fi

HAS_BG=$(echo "$INPUT" | jq -r 'has("background_tasks")' 2>/dev/null || echo "false")
if [ "$HAS_BG" = "true" ]; then
    # PRIMARY: the harness's live list; exclude the subagent's own entry.
    LIVE=$(echo "$INPUT" | jq -r --arg a "$AGENT_ID" \
        '[.background_tasks[]? | select(.status == "running") | .id
          | strings | select(. != $a and . != "")] | unique | join(" ")' \
        2>/dev/null || echo "")
else
    # FALLBACK: parse the subagent's OWN transcript (never the parent's).
    command -v python3 &>/dev/null || exit 0
    TRANSCRIPT=$(echo "$INPUT" | jq -r \
        '.agent_transcript_path // .transcript_path // empty' \
        2>/dev/null || echo "")
    [ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0
    LIVE=$(python3 - "$TRANSCRIPT" <<'PYEOF' 2>/dev/null || echo ""
import json
import re
import sys

launched = []          # ordered, deduped
terminal = set()
NOTIF_ID = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
LAUNCH_SIGS = (
    re.compile(r"Command running in background with ID: ([A-Za-z0-9_-]+)"),
    re.compile(r"Monitor started \(task ([A-Za-z0-9_-]+)"),
)
AGENT_ID_SIG = re.compile(r"agentId: ([A-Za-z0-9_-]+)")
PREFILTER = ("running in background with ID:", "Monitor started (task",
             "Async agent launched", '"toolUseResult"')


def result_texts(e):
    """Content strings of tool_result blocks ONLY — a subagent transcript's
    launch entry has no toolUseResult sidecar, so the harness wording in the
    tool_result content IS the launch record; assistant TEXT quoting the same
    wording must never count."""
    for blk in ((e.get("message") or {}).get("content") or []) \
            if isinstance((e.get("message") or {}).get("content"), list) else []:
        if not (isinstance(blk, dict) and blk.get("type") == "tool_result"):
            continue
        c = blk.get("content")
        if isinstance(c, str):
            yield c
        elif isinstance(c, list):
            for b2 in c:
                if isinstance(b2, dict) and isinstance(b2.get("text"), str):
                    yield b2["text"]


def note_launch(tid):
    if isinstance(tid, str) and tid and tid not in launched:
        launched.append(tid)

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
    # background launches — the toolUseResult sidecar (main-session shape)
    # OR the tool_result content string (subagent shape, no sidecar)
    if not any(p in line for p in PREFILTER):
        continue
    try:
        e = json.loads(line)
    except Exception:
        continue
    tur = e.get("toolUseResult")
    if isinstance(tur, dict):
        tid = tur.get("backgroundTaskId") or tur.get("taskId")
        if not tid and tur.get("isAsync"):
            tid = tur.get("agentId")
        note_launch(tid)
    for txt in result_texts(e):
        for sig in LAUNCH_SIGS:
            m = sig.search(txt)
            if m:
                note_launch(m.group(1))
        if "Async agent launched" in txt:
            m = AGENT_ID_SIG.search(txt)
            if m:
                note_launch(m.group(1))

live = [t for t in launched if t not in terminal]
if len(live) > 6:      # a fire-and-forget worker can pile up dozens (85 in
    live = live[:6] + ["(+%d more)" % (len(live) - 6)]   # the real specimen)
print(" ".join(live))
PYEOF
)
fi
LIVE=$(echo "$LIVE" | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')

[ -z "$LIVE" ] && { rm -f "$BLOCK_FILE"; exit 0; }

echo $((BLOCKS + 1)) > "$BLOCK_FILE"

REASON="You still have IN-FLIGHT background work: task(s) ${LIVE}. You are a \
SUBAGENT — if you end your turn now you TERMINATE and the completion \
notification fires to your PARENT, not to you (ci-monitoring.md; this killed \
~40% of autopilot workers). Finish the work FIRST, then clean up, then end: \
(1) wait FOREGROUND until the underlying work is done — a bounded poll loop \
of plain foreground Bash calls (e.g. 'sleep 300 && gh run view <id> --json \
status,conclusion', repeated until terminal), NEVER run_in_background; \
(2) then TaskStop EVERY task listed above that has not itself finished \
(fetch any output you need via TaskOutput first) — a TaskStop'd task no \
longer blocks you; (3) only if your dispatch contract hands the wait to the \
supervisor: TaskStop the task(s) and report the run-id + current state in \
your final message instead of waiting. A detached background task must \
never outlive your turn."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
