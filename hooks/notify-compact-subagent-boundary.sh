#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — records a /compact REQUEST at the COMPLETED-TICKET
# boundary of an autopilot run (#121, 2026-07-28).
#
# THE REQUIREMENT (user, verbatim): "autopilot ide ticket za ticketom a po
# kazdom tickete ma prebehnut compact." Ticket done -> compact runs. Always.
#
# WHY A SECOND CHANNEL EXISTS AT ALL. The Stop hook next door
# (notify-compact-request.sh) keys the boundary to the SUPERVISOR'S OWN
# MESSAGE: it refuses any turn whose last line is `⏳`. For a supervisor whose
# work is performed by DISPATCHED workers that is structurally unreachable —
# it reports batch N and dispatches batch N+1 in the SAME turn, so the turn
# carrying the completed-ticket report ALWAYS ends `⏳`. Measured
# (forestshop/parovanie_produktov, 2026-07-27/28): 19 hours with no
# compaction at 375K context and ~$0.19 per turn, across FIVE completed
# tickets — five turns carrying a `## ✅ Work Complete` heading inside a
# `⏳`-terminated message — and `~/.claude/compact-requests.json` empty: no
# request was ever even created. The marker refers to the NEXT batch, never
# to the ticket that just landed, so it is simply the wrong signal here.
#
# THE BOUNDARY IS THE TICKET. An `autopilot-worker` returning IS the completed
# ticket, and at that instant its result is already durable in git / GitHub /
# the issue — which is the entire justification for compacting at a boundary.
#
# THE ONE ALLOWED DEFERRAL is that the session still has one of its OWN
# workers running (the next ticket is genuinely already in flight). That is a
# FACT read out of the payload, never a marker, prose, or an estimate:
# `background_tasks` is this session's own task registry (the harness filters
# it to status ∈ {running, pending}), and it carries a SELF entry at
# `id == agent_id` that must be excluded (#28/#29 — live-captured payload
# shapes). Zero entries other than self ⇒ nothing of this session's is live
# ⇒ compact. Any other entry — sibling worker OR a stray shell task, any
# status — defers; the next worker's SubagentStop is the next chance.
#
# NEVER COMPACT ON A GUESS. No `background_tasks` field at all (an older
# Claude Code) ⇒ zero live workers cannot be PROVEN ⇒ exit 0, record nothing.
# This is deliberately the same fail-direction subagent-stop-check-bg-work.sh
# uses, which is why the two SubagentStop hooks cannot disagree: that gate
# BLOCKS a stop exactly when live OWNED tasks exist, and every such task is a
# non-self entry here, so whenever it blocks, this hook has already deferred.
#
# This does NOT reinstate #109 (a `/compact` fired INTO live work): there the
# only evidence available was the status marker, which cannot tell "`⏳`
# because batch N+1 was just dispatched" from "`⏳` because the ticket is
# still being worked" — the same eight characters. Here the discriminating
# evidence is the task registry itself, read at the one instant it is
# authoritative. The request carries that proof forward as
# `--origin subagent-stop`, which is what lets the delivery-time gate
# (`_compact_not_at_boundary`) stop letting the supervisor's `⏳` decide,
# while leaving #102's `❓` gate and #109's gate for every other origin
# untouched.
#
# Dedup: the worker's own `agent_id` is fingerprinted into the existing #71
# `--msg-hash` channel, so a REPEATED SubagentStop for the SAME worker is a
# no-op while every ticket keeps its own slot.
#
# Silent + non-blocking: never writes to stdout, always exits 0 — a
# SubagentStop hook that emitted anything could interfere with the worker's
# own stop decision (subagent-stop-check-bg-work.sh owns that).

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

_field() {
    printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""
}

AGENT_TYPE=$(_field '.agent_type // empty')
[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0

SID=$(_field '.session_id // empty')
[ -n "$SID" ] || exit 0

# the RAW agent_id — it must match what the payload's background_tasks self
# entry carries, so it is never sanitized here (unlike the /tmp path copies
# subagent-stop-check-bg-work.sh builds)
AGENT_ID=$(_field '.agent_id // empty')
[ -n "$AGENT_ID" ] || exit 0

# Absent field ⇒ unprovable ⇒ never compact (see the header).
HAS_BG=$(printf '%s' "$INPUT" | jq -r 'has("background_tasks")' 2>/dev/null || echo "false")
[ "$HAS_BG" = "true" ] || exit 0

# Every entry that is not the self entry counts as live, whatever its status
# or type — the harness has already filtered the array to in-flight work. An
# entry with no usable id cannot be proven to BE the self entry, so it counts
# too (defer, never compact on a guess).
OTHERS=$(printf '%s' "$INPUT" | jq -r --arg a "$AGENT_ID" \
    '[.background_tasks[]? | select(((.id // "") | tostring) != $a)] | length' \
    2>/dev/null || echo "1")
[ "$OTHERS" = "0" ] || exit 0

CWD=$(_field '.cwd // empty')

# #71 dedup key = this worker, so a repeat SubagentStop for the SAME worker is
# a no-op. Never let a failing sha256sum kill this `set -e` script (the repo's
# documented `VAR=$(failing_cmd)` gotcha) — the `||` fallback keeps it safe.
MSG_HASH=$(printf 'subagent:%s' "$AGENT_ID" | sha256sum 2>/dev/null | cut -d' ' -f1) \
    || MSG_HASH=""

AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
python3 "$AIRULESET_PY" compact-request --record --session "$SID" --cwd "$CWD" \
    --msg-hash "$MSG_HASH" --origin "subagent-stop" >/dev/null 2>&1 || true

exit 0
