#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop (airuleset #120)
#
# ⏳ WORKING must PROVE something will wake this session — a Stop turn is
# allowed to end on the WORKING marker only when the harness's own live-
# task registry shows something genuinely still running. Two real specimens
# forced this (2026-07-28, one hour apart): restreamer ended its turn
# "⏳ WORKING: dobehnutie release buildu 0.29.24 — nič od teba netreba." on
# an IDLE pane with nothing that would ever wake it. eft5000 ended
# "⏳ WORKING: beží nový test suite ... strážca zachytí aj dokončenie aj
# zaseknutie" whose only live process was an ORPHAN — a `timeout 1200
# pytest ...` launched inside a Bash tool call the session had already
# abandoned; its result can never reach the session because nothing is
# awaiting it. verify-launched-work-liveness.md and message-status-marker.md
# already say exactly the right thing in prose and have for months; neither
# stopped either case. Same conversion this repo has made repeatedly
# (#107/#110 -> CI-poll hooks, #118 -> repeat-poll hook): a mechanically
# checkable invariant left as an always-on paragraph.
#
# GROUND TRUTH (captured live 2026-07-28, headless `claude -p`, CC 2.1.220 --
# debug `tee` hook temporarily wired into Stop, removed after capture; full
# payloads in .claude/rules/airuleset-internals.md). Contrary to "unresearched
# for a plain Stop event" -- a plain TOP-LEVEL Stop event ALSO carries
# `background_tasks`, the SAME harness-authoritative live-task list
# SubagentStop already relies on (subagent-stop-check-bg-work.sh, #28/#29):
# `{id, type: shell|subagent, status, description, command?|agent_type?}`.
# Confirmed for all three waiter shapes the ticket names:
#   - a `run_in_background` Bash job  -> type "shell",    status "running"
#   - an async-dispatched Agent       -> type "subagent", status "running"
#   - nothing in flight               -> background_tasks: [] (key present,
#                                        empty array)
# Registration is SYNCHRONOUS in every capture -- the entry existed the
# instant the launching tool call returned, no observed lag.
#
# NO OWNERSHIP FILTERING NEEDED. SubagentStop's background_tasks is
# session-wide and lists SIBLING subagents' tasks too (#29 -- a healthy
# worker was blocked over tasks it could not TaskStop), so that hook must
# intersect the payload against the subagent's OWN launch ledger/transcript.
# A top-level Stop has no such siblings -- its background_tasks is this
# session's own list, full stop. Any entry with status=="running" is proof
# enough; no ledger, no transcript parse, no "MODE=scan" fallback.
#
# THE ORPHAN CASE NEEDS NO SPECIAL-CASING. background_tasks is populated
# only by launches the harness itself is tracking (Bash run_in_background,
# Monitor, an async Agent dispatch). A shell that got detached because its
# owning tool call was ABANDONED (the eft5000 shape) was never entered into
# that registry in the first place -- it is structurally invisible to the
# very field this hook reads, which is exactly the ticket's own
# requirement: "an orphaned process ... must NOT count as satisfying the
# invariant."
#
# BLOCK, not nudge (decision on #120). The same field is already
# hard-blocked on at SubagentStop in production for weeks; this fresh
# capture reproduced the identical shape synchronously and reliably across
# three separate live specimens, with no observed registration lag in any
# of them. A corpus replay against real historical transcripts (this repo's
# playbook, bypass-log-first / corpus-replay precedent) found the false-⏳
# pattern real but rare (~0.3% of real ⏳-terminated turns in the local
# corpus) and never misclassified a genuinely-live wait. Retry-capped and
# fail-open on anything unparseable or absent, matching every other Stop
# hook in this repo -- a false block here wedges every session on every
# box, so the fail direction is always "allow", never "block blind".
#
# FAIL-OPEN, deliberately, when:
#   - jq/stdin missing or unparseable
#   - the message does not end on the WORKING marker at all
#   - the `background_tasks` KEY is absent entirely (an older harness that
#     doesn't expose it at Stop -- nothing to check against, so nothing to
#     block on; forward-compatible if the harness ever drops the field)
#   - the per-session retry cap is exceeded (never wedge a session)
# The key being PRESENT but EMPTY (or holding only non-"running" entries)
# is the one case that blocks -- that is the actual lie this hook exists to
# catch.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# --- is the LAST non-blank line the ⏳ WORKING marker? (message-status-marker.md:
# the marker must be the LAST line; loosened corpus check confirmed requiring
# "working" near the hourglass avoids false triggers on unrelated ⏳ mentions
# in prose/code, e.g. a UI label discussing a "⏳ toggle") ---
LAST_LINE=$(echo "$MSG" | grep -vE '^[[:space:]]*$' | tail -1)
if ! echo "$LAST_LINE" | grep -qiE "⏳.*working"; then
    exit 0
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SAFE_SESSION=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9_-')
[ -n "$SAFE_SESSION" ] || SAFE_SESSION="unknown"

RETRY_FILE="/tmp/airuleset-working-liveness-block-${SAFE_SESSION}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=2
if [ "$RETRIES" -ge "$MAX_RETRIES" ] 2>/dev/null; then
    exit 0   # fail open -- never wedge a session in an endless block loop
fi

HAS_KEY=$(echo "$INPUT" | jq -r 'has("background_tasks")' 2>/dev/null || echo "false")
if [ "$HAS_KEY" != "true" ]; then
    exit 0   # harness on this version doesn't expose it -- nothing to check
fi

RUNNING=$(echo "$INPUT" | jq -r \
    '[.background_tasks[]? | select(.status == "running")] | length' \
    2>/dev/null || echo "0")
[ -n "$RUNNING" ] || RUNNING=0

if [ "$RUNNING" -gt 0 ] 2>/dev/null; then
    rm -f "$RETRY_FILE"
    exit 0
fi

echo "$((RETRIES+1))" > "$RETRY_FILE"

REASON="Your turn ends on the ⏳ WORKING marker, but the harness's OWN \
live-task list (background_tasks) is EMPTY -- nothing the harness is \
tracking is running, so nothing will ever wake this session. This is the \
exact lie #120 was filed over: a pane that goes idle forever after a \
WORKING claim, or a process that is alive on the box but was launched \
inside a Bash tool call this session already abandoned -- nothing is \
awaiting its result. Fix it one of two ways: (1) if work genuinely needs to \
continue, launch it so the harness can track it -- Bash with \
run_in_background:true, Monitor, or an async Agent dispatch -- THEN end on \
⏳ WORKING; the harness will wake you when it completes. (2) if nothing is \
actually running right now, end honestly instead: finish the work inline, \
or use ✅ DONE if it is genuinely finished, or ❓ NEEDS YOU if you are \
blocked on the user. Never end on ⏳ WORKING without a launch the harness \
can see. See message-status-marker.md and verify-launched-work-liveness.md."

jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
exit 0
