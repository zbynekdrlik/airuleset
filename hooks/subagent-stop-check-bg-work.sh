#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop (airuleset #28, ownership filter #29)
# A SUBAGENT that ends its turn with in-flight background work TERMINATES —
# the detached task's completion fires to the PARENT, never to the now-gone
# subagent (ci-monitoring.md; ~40% of autopilot-worker failures; odoo-erp
# worker #2061/PR #2063 died mid-CI-monitor 2026-07-24). The rule exists in
# prose and workers violate it anyway — this hook is the mechanism: it BLOCKS
# the stop while the subagent's OWN background work is live, telling the
# worker to wait FOREGROUND and TaskStop every stray task before returning.
#
# LIVENESS = the payload (live-fired E2E 2026-07-24, CC 2.1.x): the
# `background_tasks` array is the harness's live-task list with current
# statuses — authoritative and lag-free. But it is SESSION-WIDE (#29): it
# lists SIBLING workers' tasks too, which the stopping subagent cannot
# TaskStop (not the owner) — counting them deadlocked healthy workers in
# every parallel multi-worker setup (odoo-erp review subagent blocked over
# 5 sibling tasks, 2026-07-24).
# OWNERSHIP = the PostToolUse ledger ∪ the subagent's OWN transcript. The
# ledger (/tmp/airuleset-bgtasks-<session>-<agent>, written SYNCHRONOUSLY by
# post-record-subagent-bg-launch.sh at launch time) is the primary source —
# the transcript is written ASYNC and a launch seconds before the stop is
# often not flushed yet (live E2E let an abandoning worker through). The
# transcript (`agent_transcript_path`; `transcript_path` is the PARENT
# session's file — parsing it missed every subagent launch) remains the
# secondary source (covers a session whose recorder was added mid-flight):
#   launched  = toolUseResult.backgroundTaskId (Bash run_in_background)
#             | toolUseResult.taskId           (Monitor — always async)
#             | toolUseResult.agentId if isAsync (background child Agent)
#             | the tool_result CONTENT string ("Command running in
#               background with ID: X" / "Monitor started (task X" /
#               "Async agent launched … agentId: X") — a SUBAGENT
#               transcript's launch entry carries NO toolUseResult sidecar
#               (the restreamer specimen). Only tool_result blocks are
#               scanned — assistant text merely QUOTING the harness wording
#               never counts as a launch.
# BLOCK = live ∩ owned. A blocked worker can therefore ALWAYS get out:
# TaskStop works on a task it owns.
#
# FALLBACK (CC versions without `background_tasks`): the transcript alone —
# launched minus terminal, where terminal = a task-notification line
# carrying BOTH <task-id>ID</task-id> AND a <status> tag (a Monitor
# MID-STREAM <event> has no <status> — still live), or a TaskStop/KillShell
# tool_use naming the id. Inherently ownership-scoped (own transcript).
#
# UNBACKED-MONITORING-CLAIM CHECK (airuleset #343, `_check_unbacked_
# monitoring_claim`). The block above catches a subagent that OWNS live
# background work and terminates without waiting — the mirror-image failure
# has no check anywhere: a subagent whose final message CLAIMS ongoing
# monitoring/watching ("monitoring shadow E2E to terminal" — the odoo-erp
# incident this ticket documents) while owning NOTHING live. A SubagentStop
# is terminal by construction — nothing resumes a stopped subagent — so an
# un-backed "still watching" claim is a genuine lie with no mechanism
# catching it: neither `stop-check-working-liveness.sh` nor
# `stop-check-status-marker.sh` reaches a SubagentStop at all (both are
# `Stop`-only). Called from BOTH places this script concludes nothing is
# live — the CANDIDATES-empty early exit below (a MODERN payload, self-only
# or empty `background_tasks` — the incident's own exact shape) AND the
# post-scan LIVE-empty check — never only one (adversarial-review CRITICAL-1:
# a first draft wired only the second site, so the SOLO-subagent modern-
# payload case, tested nowhere by the original suite, bypassed the check
# entirely). Scans `last_assistant_message` for an ONGOING-tense monitor/
# watch claim (present participle or bare/future form: "monitoring",
# "monitors", "will monitor", "watching" — deliberately narrow to the
# ticket's own vocabulary, English only — a Slovak "monitorujem"/"sledujem"
# equivalent is a known, undetected residual). Past-tense forms ("monitored",
# "watched") never match: the `\b` word boundary immediately after
# "monitor"/"watch" fails to hold when the very next character is "ed" (both
# are word characters — no transition), so a genuinely COMPLETED report
# ("I monitored the deploy, it succeeded") is untouched — though a genuinely
# present-tense but ALREADY-RESOLVED report ("I was monitoring the run until
# it finished; it succeeded") or a mere recommendation ("recommend
# monitoring the deploy after merge") CAN still false-match; accepted,
# bounded by MAX_BLOCKS below, not chased further. Gated on a non-empty
# `agent_type` (adversarial-review MAJOR-3): a SubagentStop ALSO fires once
# per PARALLEL TOOL-CALL BRANCH with `agent_type: ""` (far more often than a
# genuine dispatched-agent stop, #123) whose `last_assistant_message` is the
# Bash tool's own DESCRIPTION string (e.g. "Monitor CI run status") — that
# population owns nothing live by construction and would false-positive on
# nearly every such branch without the gate. Reuses the SAME per-(session,
# agent) BLOCK_FILE the check above already writes — a repeated unbacked
# claim still fails open past MAX_BLOCKS, same as every other reason in
# this hook. The message match uses a HERE-STRING (`<<<`), never
# `echo "$MSG" | grep -q` (adversarial-review MAJOR-2): the piped form is
# this repo's own documented-banned idiom — `grep -q` exits at its first
# match without draining stdin, SIGPIPEs the echo writer, and under
# `pipefail` that reads as "not found" for any genuine claim on an early
# line of a message at or past ~64KiB.
#
# Fail-open everywhere: no jq/python, missing/unreadable transcript
# (ownership unprovable → nothing blocks), parse errors, and after
# MAX_BLOCKS blocks per (session, agent) — the transcript is written
# asynchronously and may lag (observed live: a lagged launch missed on one
# run, an over-block after cleanup on another — the payload liveness path
# has neither problem).
#
# #346 — AIRULESET_BGTASKS_DIR (default /tmp, unchanged production
# behavior): the base directory for BOTH the ledger and the retry-cap
# BLOCK_FILE, mirroring post-record-subagent-bg-launch.sh's own override —
# MUST resolve to the SAME directory as that hook's, or the ledger it wrote
# is never found here. See that hook's own comment for why this exists.
#
# #346 review residual (THEORETICAL, no fix under FREEZE): if AIRULESET_
# BGTASKS_DIR points at a nonexistent/unwritable directory (test/dev-env
# misuse only — production always uses the real /tmp default), the retry-
# cap write below (`echo ... > "$BLOCK_FILE"`) can die under `set -e`
# BEFORE the jq verdict prints — the write-before-verdict ordering this
# repo's own #196 fix already established elsewhere in this file. This is
# a pre-existing pattern (an unwritable /tmp BLOCK_FILE already had this
# same theoretical gap before #346), not something #346 introduces; the
# env var only adds one more way to reach a bad directory.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // "unknown"' 2>/dev/null || echo "unknown")
# empty for a PARALLEL TOOL-CALL BRANCH stop (#123 -- fires far more often
# than a real dispatched-agent stop; last_assistant_message there is the
# Bash tool's own DESCRIPTION string, e.g. "Monitor CI run status") --
# _check_unbacked_monitoring_claim below gates its own check on this being
# non-empty, so that population is never scanned for a "monitoring" claim.
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null || echo "")
# ids land in /tmp paths — strip to [A-Za-z0-9_-] (defensive; MUST match the
# recorder's sanitization or the launch ledger is never found at stop). NB:
# the RAW agent_id stays what the payload's background_tasks self-entry
# carries — sanitize only the PATH copies.
SAFE_SESSION=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9_-')
SAFE_AGENT=$(printf '%s' "$AGENT_ID" | tr -cd 'A-Za-z0-9_-')
[ -n "$SAFE_SESSION" ] || SAFE_SESSION="unknown"
[ -n "$SAFE_AGENT" ] || SAFE_AGENT="unknown"

BGDIR="${AIRULESET_BGTASKS_DIR:-/tmp}"
BGDIR="${BGDIR%/}"
BLOCK_FILE="${BGDIR}/airuleset-subagent-bgwork-block-${SAFE_SESSION}-${SAFE_AGENT}"
LEDGER_FILE="${BGDIR}/airuleset-bgtasks-${SAFE_SESSION}-${SAFE_AGENT}"
# an unreadable/corrupt counter reads as 0 — deliberate fail-open direction
BLOCKS=$(cat "$BLOCK_FILE" 2>/dev/null || echo 0)
MAX_BLOCKS=3
if [ "$BLOCKS" -ge "$MAX_BLOCKS" ] 2>/dev/null; then
    exit 0     # fail open — never wedge a subagent in an endless block loop
fi

# #343 -- SHARED "nothing is live" handler, called from EVERY place this
# script concludes the subagent owns no live work (there are TWO such
# places below: the CANDIDATES-empty early exit in intersect mode, and the
# LIVE-empty check after the Python ownership scan in either mode) --
# review finding CRITICAL-1: a first draft only wired the check into the
# SECOND site, so the intersect-mode early exit (which is what a MODERN
# payload hits for a SOLO subagent with self-only/empty background_tasks —
# the incident's own exact shape) bypassed the new check entirely. ALWAYS
# exits; never returns to its caller.
_check_unbacked_monitoring_claim() {
    rm -f "$LEDGER_FILE"
    if [ -n "$AGENT_TYPE" ]; then
        # here-string, NOT `echo "$MSG" | grep -q` -- the piped form is the
        # repo's own banned #190 idiom: grep -q exits at its first match
        # without draining stdin, SIGPIPEs the echo writer, and under
        # `pipefail` a genuine claim on an early line of a >=64KiB message
        # silently reads as "not found" (measured: reliable false-PASS from
        # 64KiB up). A here-string has no separate writer process, so the
        # race cannot exist.
        MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
        # #413 -- exclude a bare FILENAME/PATH mention (e.g. "ci-monitoring.md",
        # "hooks/ci-monitor") from counting as a claim: `\b` alone treats a
        # hyphen/dot/slash as a genuine word boundary (they are non-word
        # characters), so "ci-monitoring.md" satisfied the OLD
        # `\bmonitoring\b` just as readily as a real claim ("still monitoring
        # the deploy"). Requires GNU grep -P (lookaround) -- already relied on
        # elsewhere in this hook family (stop-check-prose-violations.sh
        # documents the same `grep -qP` recipe). A match is refused when it is
        # immediately preceded by `-`/`.`/`/` (a hyphenated/pathy compound:
        # "ci-monitor", "hooks/monitoring") OR immediately followed by a known
        # file extension or `/` (the whole-token filename shape). Deliberately
        # narrow, per this repo's own "fix the reported corpus, don't chase
        # every theoretical shape" discipline -- a genuinely exotic hyphenated
        # CLAIM ("self-monitoring the deploy") is an accepted, documented
        # residual, the same class as this hook's other MAX_BLOCKS-bounded
        # false-positive tolerances.
        MONITOR_RE='(?<![-./])\b(?:monitor(?:ing|s)?|watching)\b(?!\.(?:md|py|sh|ya?ml|json|txt|js|ts|rs|toml|cfg|ini|conf|log)\b)(?!/)'
        if grep -qiP "$MONITOR_RE" <<<"$MSG"; then
            echo $((BLOCKS + 1)) > "$BLOCK_FILE"
            REASON2="Your final message claims you are still monitoring/watching \
something, but you have NO live tracked background work — you are a \
SUBAGENT, and your SubagentStop is TERMINAL: nothing resumes a stopped \
subagent, so nothing continues watching once this turn ends. This is the \
exact odoo-erp incident airuleset #343 documents: a lane agent's last words \
were 'monitoring shadow E2E to terminal', the run failed 20 minutes later, \
and nothing woke the coordinator. Pick ONE: (1) if the resource genuinely \
has not resolved yet, hold this turn with a real bounded FOREGROUND poll \
(e.g. 'sleep 300 && gh run view <id> --json status,conclusion,jobs', repeated \
until terminal) and report the ACTUAL outcome, never a promise to keep \
checking; (2) launch genuinely trackable background work (Bash \
run_in_background / Monitor / an async Agent dispatch) so this SAME hook's \
own live-task check covers you on your next stop; (3) if your dispatch \
contract hands the watch back to your caller, drop the 'monitoring'/\
'watching' language entirely and instead report the CURRENT status as a \
plain fact (e.g. 'run <id> was in-progress as of <time>; status unknown \
after this — re-check needed') so the coordinator's own transcript captures \
something it can act on, not an open-ended claim nobody is honoring."
            jq -n --arg r "$REASON2" '{"decision":"block","reason":$r}'
            exit 0
        fi
    fi
    rm -f "$BLOCK_FILE"
    exit 0
}

HAS_BG=$(echo "$INPUT" | jq -r 'has("background_tasks")' 2>/dev/null || echo "false")
TRANSCRIPT=$(echo "$INPUT" | jq -r \
    '.agent_transcript_path // .transcript_path // empty' \
    2>/dev/null || echo "")

MODE="scan"
CANDIDATES=""
if [ "$HAS_BG" = "true" ]; then
    # liveness from the harness's list; exclude the subagent's own entry
    CANDIDATES=$(echo "$INPUT" | jq -r --arg a "$AGENT_ID" \
        '[.background_tasks[]? | select(.status == "running") | .id
          | strings | select(. != $a and . != "")] | unique | join(" ")' \
        2>/dev/null || echo "")
    [ -z "$CANDIDATES" ] && _check_unbacked_monitoring_claim
    MODE="intersect"
fi

command -v python3 &>/dev/null || exit 0
if [ "$MODE" = "scan" ]; then
    # the fallback has no other source — an unreadable transcript fails open
    [ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0
fi
# intersect mode proceeds regardless: the ledger alone can prove ownership
# (the transcript is async and may lag the launch — the live-E2E slip)

# shellcheck disable=SC2086
LIVE=$(python3 - "$TRANSCRIPT" "$MODE" "$LEDGER_FILE" $CANDIDATES <<'PYEOF' 2>/dev/null || echo ""
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


def scan(line):
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
        return
    try:
        e = json.loads(line)
    except Exception:
        return
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


mode = sys.argv[2] if len(sys.argv) > 2 else "scan"

try:
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            scan(line)
except OSError:
    if mode != "intersect":
        sys.exit(0)      # fallback has no other ownership source
    # intersect: the synchronous ledger below can still prove ownership

# the PostToolUse ledger — synchronous launch records; the transcript is
# async and may lag a launch made seconds before the stop (#29 follow-up)
try:
    with open(sys.argv[3], encoding="utf-8", errors="replace") as lf:
        for ln in lf:
            note_launch(ln.strip())
except (OSError, IndexError):
    pass

if mode == "intersect":
    # payload liveness ∩ own launches — sibling tasks (#29) never block
    live = [c for c in sys.argv[4:] if c in launched]
else:
    live = [t for t in launched if t not in terminal]
if len(live) > 6:      # a fire-and-forget worker can pile up dozens (85 in
    live = live[:6] + ["(+%d more)" % (len(live) - 6)]   # the real specimen)
print(" ".join(live))
PYEOF
)
LIVE=$(echo "$LIVE" | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')

[ -z "$LIVE" ] && _check_unbacked_monitoring_claim

echo $((BLOCKS + 1)) > "$BLOCK_FILE"

REASON="You still have IN-FLIGHT background work YOU launched: task(s) \
${LIVE}. You are a SUBAGENT — if you end your turn now you TERMINATE and the \
completion notification fires to your PARENT, not to you (ci-monitoring.md; \
this killed ~40% of autopilot workers). Finish the work FIRST, then clean \
up, then end: (1) wait FOREGROUND until the underlying work is done — a \
bounded poll loop of plain foreground Bash calls (e.g. 'sleep 300 && gh run \
view <id> --json status,conclusion,jobs', repeated until terminal), NEVER \
run_in_background; (2) then TaskStop EVERY task listed above that has not \
itself finished (fetch any output you need via TaskOutput first) — you own \
these tasks, TaskStop works, and a TaskStop'd task no longer blocks you; \
(3) only if your dispatch contract hands the wait to the supervisor: \
TaskStop the task(s) and report the run-id + current state in your final \
message instead of waiting. A detached background task must never outlive \
your turn."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
