#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — airuleset #119.
#
# A REPEATED foreground poll loop with NO CI signature is hard-blocked; the
# FIRST one is free. This is #118's sibling, for everything #118 structurally
# cannot see.
#
# WHY A SECOND HOOK RATHER THAN WIDENING #118. block-ci-poll-repeat.sh narrows
# on a CI-wait signature BEFORE any loop-shape detection:
#
#   grep -qE 'gh +run +(view|watch|list)|gh +pr +checks' || exit 0
#
# so a loop carrying no such token exits 0 and is never reached. That narrowing
# is deliberate, corpus-audited and load-bearing for #118 — it also hands out a
# CI-specific background waiter as its fix, which would be the wrong command for
# two thirds of the scope a widening would add. So #118 keeps CI, alone, and
# this hook exits the moment it sees a CI signature. Two hooks can never both
# block one command, which would give the model two different corrective
# commands for one mistake.
#
# THE POPULATION, re-derived rather than carried forward from the ticket (all
# 8,107 local transcripts, 251,551 Bash commands, replayed through a Python
# mirror of the #111 detector that was diffed against the real hook on all 6,188
# unique candidates with 0 mismatches). Of 4,430 foreground loop-shape commands,
# 2,326 carry a CI signature and 2,104 do not. The uncovered remainder:
# remote/process 814, subagent-result 746, other 191, local-log 185, endpoint
# 106, file-appears 62. In the ticket's own 24h window the population is 335
# across SIX projects, not the 118 across two that the body cites, and only 41
# (12%) poll a subagent-result-shaped path — so a guard keyed on `result.json`
# would miss about two thirds of its own family. The purest specimen carries no
# condition at all: `END=$((SECONDS+300)); while (( SECONDS < END )); do sleep 5;
# done` — repeated, a full context re-send per five minutes of nothing.
#
# KEY: session + a DIGIT-BLIND hash of the normalised loop shape. #118 could use
# a run-id; there is no such id here, and keying on a path or filename is exactly
# the over-narrow mistake the validator's comment warned about. The shape hash is
# stronger than an id in one way — it cannot collide across two different waits —
# and absorbs `seq 1 30` -> `seq 1 60` and `sleep 15` -> `sleep 20`, which are
# the same wait retuned. Consequence, stated plainly: a session that varies the
# loop's WORDS between polls evades. Per #118's own finding the observed failure
# mode is defaulting, not adversarial circumvention, and every block is logged
# for corpus review.
#
# WHY LOOP 1 STAYS FREE — #118's reasoning, unchanged: wait length is unknowable
# at decision time, which is why every rule asking the model to PREDICT it has
# failed; but a first loop that came back without its condition met is MEASURED
# proof the wait is long, and a genuinely short wait ends inside loop 1 and never
# reaches this hook.
#
# THE MESSAGE BRANCHES BY WHAT IS WAITED ON, because the honest advice differs
# and handing out the wrong one is worse than saying nothing:
#   • a real Claude Code background artefact (`…/tasks/<id>.output`, an agent's
#     own `subagents/agent-*.jsonl`) ALREADY has a task-notification coming, so
#     the poll cannot make anything arrive sooner;
#   • everything else — a log line, a file appearing, ssh/remote state, process
#     liveness — has NO notification at all, so "trust the notification" would be
#     a lie and the answer is one blocking waiter instead.
# A user-invented `result.json` written by a detached `nohup` is deliberately in
# the SECOND group: it looks like a result file and has no notification whatever.
# Neither branch ever prints #118's CI waiter.
#
# SUBAGENTS get a third message. A subagent with no pending FOREGROUND tool call
# is returned as "completed" and TERMINATES, so it must never be offered a
# background waiter (that is why block-subagent-bg-ci-poll.sh exists) AND must
# never be told to "end your turn and let the notification arrive" — the
# task-branch advice that is correct in main context is fatal there.
#
# BLAST RADIUS, characterised item by item rather than as a count: 292 blocks
# corpus-wide, 0.116% of all Bash calls, across 36 sessions (top session 88, so
# no single runaway drives it). Branch split: task artefact 167, generic 125. By
# what is polled: subagent/task artefact 167, local log 40, remote/ssh 37, bare
# timed sleep 31, process liveness 14, endpoint 2, file 1. Explicitly hunted for
# the wrong-block class #118 found — a FAN-OUT over items with a rate-limit sleep
# rather than a wait: 0. The 14 with no condition-polling primitive at all were
# read by hand and are all genuine waits.
#
# NEVER BLOCKED, in any state: a `run_in_background` waiter (the compliant path),
# a single status check, a mutating command with a wait bolted on the tail, a
# sub-5s tight retry, a loop whose sleep interval is unmeasurable, and the logged
# inline escape hatch `# airuleset:poll-ok <reason>`.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

# ---- never touch the compliant path ------------------------------------
BG=$(echo "$INPUT" | jq -r '.tool_input.run_in_background // false' 2>/dev/null || echo "false")
[ "$BG" = "true" ] && exit 0

# ---- #124: payload is not control flow ---------------------------------
# A doc write QUOTING a poll loop polls nothing. Stripping here, before any
# other gate, is what keeps this guard from inheriting #124 on day one — the
# ticket's own constraint 3.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib-poll-payload.sh"
if poll_payload_carrier "$CMD"; then
    CMD=$(poll_payload_strip "$CMD")
fi

STATE_DIR="${AIRULESET_LOCALPOLL_STATE_DIR:-/tmp}"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

FLAT=$(printf '%s' "$CMD" | tr '\n' ' ')

# ---- #118 owns CI, alone -----------------------------------------------
# Exits BEFORE any state is written, so a CI loop does not even consume a slot.
if printf '%s' "$FLAT" \
        | grep -qE 'gh[[:space:]]+run[[:space:]]+(view|watch|list)|gh[[:space:]]+pr[[:space:]]+checks'; then
    exit 0   # airuleset:ci-owned-by-118
fi

RAW_SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$RAW_SID" | tr -cd 'A-Za-z0-9_-')
SID="${SID:-unknown}"
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")

BLOCK_LOG="$STATE_DIR/airuleset-localpoll-block.log"
BYPASS_LOG="$STATE_DIR/airuleset-localpoll-bypass.log"
EXEMPT_LOG="$STATE_DIR/airuleset-localpoll-exempt.log"
SNIPPET=$(printf '%s' "$FLAT" | cut -c1-160)

log_exempt() {
    printf '%s localpoll EXEMPT session=%s why=%s cmd=%s\n' \
        "$(date -Is)" "$SID" "$1" "$SNIPPET" >> "$EXEMPT_LOG" 2>/dev/null || true
}

# ---- logged escape hatch (never a dead end) ----------------------------
if printf '%s' "$FLAT" | grep -qE '#[[:space:]]*airuleset:poll-ok'; then
    REASON=$(printf '%s' "$FLAT" | sed -n 's/.*#[[:space:]]*airuleset:poll-ok[[:space:]]*//p' | cut -c1-160)
    printf '%s localpoll BYPASS session=%s reason=%s\n' \
        "$(date -Is)" "$SID" "$REASON" >> "$BYPASS_LOG" 2>/dev/null || true
    exit 0
fi

# ---- a command that MUTATES is not primarily a wait --------------------
# #118's narrowing 1, and for the same reason: blocking `git push … && until …;
# do sleep 30; done` blocks the PUSH, and on these boxes the working tree is
# production. It also cannot be the burn shape — you push once, and the repeat
# polls that follow are pure waits, still caught.
if printf '%s' "$FLAT" | grep -qE '(^|[^A-Za-z0-9_-])(git[[:space:]]+(push|commit|merge|tag)|gh[[:space:]]+pr[[:space:]]+(merge|create)|gh[[:space:]]+issue[[:space:]]+(create|close|comment|edit)|rsync|scp|systemctl[[:space:]]+(restart|start|stop))([^A-Za-z0-9_-]|$)'; then
    log_exempt "mutating-action"
    exit 0
fi

# ---- shape: the #111 loop detector, verbatim ---------------------------
printf ' %s ' "$CMD" | tr -c 'A-Za-z0-9_' ' ' | tr -s ' ' \
    | grep -qE ' do( .*)? sleep( .*)? done ' || exit 0

# ---- a tight retry is not a long wait ----------------------------------
# Corpus: 248 of 1,831 candidates sleep under 5s. Those are retry/settle loops
# measured in seconds, not the per-turn context re-send this hook exists for. An
# UNMEASURABLE interval (`sleep "$T"`) counts as short, because this hook must
# never be the thing that stops real work.
MIN_SLEEP="${AIRULESET_LOCALPOLL_MIN_SLEEP_S:-5}"
case "$MIN_SLEEP" in ''|*[!0-9]*) MIN_SLEEP=5 ;; esac
MAX_SLEEP=$(printf '%s' "$FLAT" \
    | grep -oE '(^|[^A-Za-z0-9_-])sleep[[:space:]]+[0-9]+' \
    | grep -oE '[0-9]+$' | sort -rn | head -1 || true)
if [ -z "$MAX_SLEEP" ] || [ "$MAX_SLEEP" -lt "$MIN_SLEEP" ]; then
    log_exempt "short-wait(sleep=${MAX_SLEEP:-none})"
    exit 0
fi

# ---- key: session + digit-blind normalised SHAPE -----------------------
SHAPE=$(printf ' %s ' "$CMD" | tr -c 'A-Za-z0-9_' ' ' | tr -s ' ' \
    | sed -E 's/(^| )[0-9]+( |$)/ N /g; s/[0-9]+/N/g' \
    | md5sum 2>/dev/null | cut -c1-12 || true)
[ -n "$SHAPE" ] || exit 0
FIRST_FILE="$STATE_DIR/airuleset-localpoll-first-${SID}-${SHAPE}"

# ---- the carve-out: loop 1 per shape is free ---------------------------
if [ ! -e "$FIRST_FILE" ]; then
    : > "$FIRST_FILE" 2>/dev/null || true
    exit 0
fi

# ---- which branch: is there really a notification coming? --------------
# Keyed on the REAL Claude Code background-task artefacts, never on
# `result.json` — a user-invented result file written by a detached `nohup` has
# no notification at all, and telling that session "the harness wakes you" is
# the wrong-advice failure this branching exists to prevent.
WAIT_KIND="generic"
if printf '%s' "$FLAT" \
        | grep -qE '/tasks/[A-Za-z0-9_-]+\.output|/subagents/agent-[A-Za-z0-9]+\.jsonl'; then
    WAIT_KIND="task"
fi

printf '%s localpoll BLOCK session=%s shape=%s agent=%s kind=%s cmd=%s\n' \
    "$(date -Is)" "$SID" "$SHAPE" "${AGENT_ID:-main}" "$WAIT_KIND" "$SNIPPET" \
    >> "$BLOCK_LOG" 2>/dev/null || true

if [ -n "$AGENT_ID" ]; then
    MSG=$(cat <<'SUBMSG'
BLOCKED (airuleset #119): this is a REPEAT foreground wait in this session — the
same normalised loop shape already came back without its condition met, which is
measured proof this wait is LONG. Every repeat is another TURN that re-sends
your entire context to learn the condition still does not hold.

You are a SUBAGENT, so the two usual answers are both unavailable to you:

  • do NOT launch a background waiter — a subagent with no pending foreground
    tool call is returned as "completed" and TERMINATES, so your work would
    silently die;
  • do NOT end your turn to wait for a task-notification, for the same reason.

Do this instead, now: raise THIS Bash call's own `timeout` parameter toward its
600000 ms cap and let ONE foreground loop cover the whole wait, self-bounded on
`SECONDS` so it returns gracefully instead of being SIGTERM'd mid-poll. If the
wait is longer than one call can cover, report what you are waiting on in your
FINAL message and RETURN — the supervisor owns long waits (ci-monitoring.md).

Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
SUBMSG
)
elif [ "$WAIT_KIND" = "task" ]; then
    MSG=$(cat <<'TASKMSG'
BLOCKED (airuleset #119): this is a REPEAT foreground poll of a Claude Code
background-task artefact in this session. Loop 1 already came back without the
condition met.

The harness ALREADY wakes you with a task-notification when a dispatched agent
or a `run_in_background` Bash job finishes. This loop cannot make that arrive
sooner — it only costs one full-context turn per iteration batch, which is the
largest single burn shape measured on these boxes.

Do this instead, now:

  • end your turn and let the notification arrive; or
  • if you believe a notification was LOST — a compaction boundary can orphan
    the task handle (anthropics/claude-code#29193) — re-derive ONCE from the
    durable artefact, never in a loop:

      stat -Lc '%s %Y' <the output file>

    `-L` is not optional: that path is a SYMLINK into the agent's own
    transcript, so a plain `stat` describes the link (a constant 139 bytes) and
    reads as a dead agent while it is actively writing.

Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
TASKMSG
)
else
    MSG=$(cat <<'GENMSG'
BLOCKED (airuleset #119): this is a REPEAT foreground wait in this session — the
same normalised loop shape already came back without its condition met, which is
measured proof this wait is LONG. Every repeat is another TURN that re-sends
your entire context to learn the condition still does not hold.

Nothing will wake you for this kind of wait: a log line, a file appearing, an
ssh/remote state or a process exiting raises no task-notification. So ONE call
has to cover the whole wait.

Run THIS instead — ONE background waiter, `run_in_background: true`, which
blocks until the condition holds and wakes you exactly once:

  timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c 'until <your condition>; do
    sleep 30
  done'

On your NEXT turn, re-derive from the durable artefact rather than trusting
silence, and relaunch ONE waiter if the old one is gone.

If you are a SUBAGENT (autopilot-worker or any dispatched worker): do NOT run
the above — backgrounding a wait TERMINATES you. Raise this call's own `timeout`
toward its 600000 ms cap so one foreground loop covers the wait.

Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
GENMSG
)
fi

printf '%s\n' "$MSG" >&2
exit 2
