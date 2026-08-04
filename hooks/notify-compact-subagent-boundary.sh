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
#
# #225 (2026-08-04): `origin="subagent-stop"` (below) is no longer the ONLY
# proven-boundary origin — `compact-request --self` (a session explicitly
# asking to compact its OWN pane, right now) carries its own new
# `"self-callback"` origin, trusted identically at every gate. The two never
# collide: this hook always passes `subagent-stop` itself, unchanged, and
# `record_compact_request` now refuses to let either PROVEN origin be
# silently downgraded by a LATER blank-origin call for the same session
# (the plain Stop-hook channel's own default shape) — so a request THIS
# hook records keeps its proof even if that other channel also fires
# moments later for the same session.
#
# ---------------------------------------------------------------------------
# THE DECISION LOG (#123, 2026-07-28) — why a silent guard had to grow one.
#
# As shipped by #121 this hook wrote NOTHING on a decline, and its only
# success artefact — an entry in `compact-requests.json` — is DELETED again
# the instant `deliver_compact_now` succeeds. So three completely different
# states produced one identical observation: the hook never ran, the hook ran
# and declined, or the hook ran, fired and delivered perfectly. A guard whose
# correct operation is indistinguishable from its total absence can only be
# checked in replay, never in the field — which is exactly the failure class
# #121 itself was filed to fix.
#
# (For the record, "never ran" was never the answer: a hook added to
# settings.json mid-session DOES take effect in that already-running session
# — proven live on 2026-07-28 by appending a capture hook to this very
# SubagentStop block at 09:54:58 and catching a subagent's stop at 09:55:08,
# in a session started 36 h earlier. No operator restart is needed for #121.)
#
# Every decision now appends ONE line naming the predicate that failed:
#   <iso8601> RECORD  result=<recorded|sent|claim-queued|queued-compact|dropped-no-work|dropped-small-context|dup|skip|error> type=… agent=… sid=… cwd=…
#   <iso8601> DECLINE reason=<predicate> [n=<live>] type=… agent=… sid=… cwd=…
# `result=` is the word `cmd_compact_request` already printed and this hook
# used to discard with `>/dev/null 2>&1` — an accepted boundary that was then
# dropped downstream used to be untraceable from here too. #125 (2026-07-28):
# `result=` used to collapse FIVE different dispositions (a real send, both
# #78 SKIP branches, and both the #99/#48 DROP branches) onto one generic
# "delivered" word, so this log's own `result=` field could not tell a real
# send from a downstream drop either — it had to be read side-by-side with
# `compact-sync.log`'s SEND/DROP/SKIP lines at the same timestamp. The word
# vocabulary below is the SAME set `cmd_compact_request` now prints.
#
# BOUNDED, because an unbounded log on every SubagentStop of every session is
# a new problem, not a fix. The two populations differ by orders of magnitude:
# SubagentStop fires once per parallel tool-call branch as well as per
# dispatched subagent (live-captured: four `agent_type: ""` payloads inside
# three minutes), so `not-autopilot-worker` runs to thousands a day while the
# decisions this ticket cares about run to a few dozen. Therefore every
# `autopilot-worker` decision is logged unconditionally, and the non-worker
# class is logged ONCE PER (session, reason) — never again for that session,
# however long it runs (#146, 2026-08-04). This replaces a GLOBAL 60s
# heartbeat (#123): a served session's `agent_type` never changes mid-
# session, so re-logging the SAME already-established decline every ~60s is
# pure noise once the fact is known — live evidence, one 8.5h+ forestshop
# session alone wrote 1465 of the shared log's 2842 lines (51.5%) and
# rotated it on its own. A tiny marker file per (session, reason) pair under
# `.compact-decisions-seen/` remembers what has already been logged, pruned
# past a 14-day TTL so the directory cannot grow forever. A `stat`-size
# check still rotates the decision log itself to `.log.1` at 512 KB
# (ceiling ≈ 1 MB, two generations, no cron).
#
# Logging is diagnostics: every write is `|| true`-guarded so a read-only
# $HOME can never turn it into a blocked subagent stop. The ACCEPT CONDITION
# is untouched by all of this — only observability changed.
# ---------------------------------------------------------------------------

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

DECISION_LOG="$HOME/.claude/compact-decisions.log"
DECISION_SEEN_DIR="$HOME/.claude/.compact-decisions-seen"
DECISION_CAP=512000
DECISION_SEEN_TTL_DAYS=14

# `_decide_log <OUTCOME> [extra k=v tokens]` — one line, never fatal.
_decide_log() {
    local outcome="$1"
    local extra="${2:-}"
    local size
    mkdir -p "$(dirname "$DECISION_LOG")" 2>/dev/null || true
    size=$(stat -c %s "$DECISION_LOG" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    if [ "$size" -gt "$DECISION_CAP" ]; then
        mv -f "$DECISION_LOG" "$DECISION_LOG.1" 2>/dev/null || true
    fi
    {
        printf '%s %s %stype=%s agent=%s sid=%s cwd=%s\n' \
            "$(date -Iseconds 2>/dev/null || echo '?')" "$outcome" \
            "${extra:+$extra }" "${AGENT_TYPE:--}" "${AGENT_ID:--}" \
            "${SID:--}" "${CWD_LOG:--}" >>"$DECISION_LOG"
    } 2>/dev/null || true
}

# The high-volume class: log the FIRST decline for a (session, reason) pair,
# then suppress every later one for that SAME pair — the cause cannot change
# mid-session (agent_type is fixed at launch), so a repeat is pure noise
# (#146). O(1) — one `[ -e ]` check, no read-modify-write, so concurrent
# stops cannot corrupt anything (the worst case is two markers written for
# the same pair in the same instant, which is harmless).
_decide_log_once_per_session() {
    local outcome="$1"
    local extra="${2:-}"
    local key seen_file
    key=$(printf '%s|%s' "${SID:-}" "$extra" | tr -c 'A-Za-z0-9=_|-' '_')
    seen_file="$DECISION_SEEN_DIR/$key"
    [ -e "$seen_file" ] && return 0
    mkdir -p "$DECISION_SEEN_DIR" 2>/dev/null || true
    # bound the directory's growth -- a marker no session will ever revisit
    # must not survive forever.
    find "$DECISION_SEEN_DIR" -maxdepth 1 -type f \
        -mtime "+$DECISION_SEEN_TTL_DAYS" -delete 2>/dev/null || true
    _decide_log "$outcome" "$extra"
    touch "$seen_file" 2>/dev/null || true
}

_field() {
    printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""
}

AGENT_TYPE=$(_field '.agent_type // empty')
SID=$(_field '.session_id // empty')
# the RAW agent_id — it must match what the payload's background_tasks self
# entry carries, so it is never sanitized here (unlike the /tmp path copies
# subagent-stop-check-bg-work.sh builds)
AGENT_ID=$(_field '.agent_id // empty')
CWD=$(_field '.cwd // empty')
# the log is whitespace-delimited, so only the logged COPY is squeezed
CWD_LOG=${CWD// /_}

[ "$AGENT_TYPE" = "autopilot-worker" ] || {
    _decide_log_once_per_session DECLINE "reason=not-autopilot-worker"
    exit 0
}

[ -n "$SID" ] || { _decide_log DECLINE "reason=no-session-id"; exit 0; }
[ -n "$AGENT_ID" ] || { _decide_log DECLINE "reason=no-agent-id"; exit 0; }

# Absent field ⇒ unprovable ⇒ never compact (see the header). It must be an
# actual ARRAY, not merely PRESENT: `has("background_tasks")` is true for an
# explicit `null`, and iterating null (or any non-array) yields nothing, which
# would read as "zero live workers" and fire the compact on a payload that
# proved nothing at all. `type` alone cannot tell an absent field from an
# explicit null (both report "null"), and those are different diagnoses — the
# first means an older Claude Code, the second a malformed payload — so the
# reason is built from `has()` as well.
BG_HAS=$(printf '%s' "$INPUT" | jq -r 'has("background_tasks")' 2>/dev/null || echo "false")
BG_TYPE=$(printf '%s' "$INPUT" | jq -r '.background_tasks | type' 2>/dev/null || echo "null")
if [ "$BG_TYPE" != "array" ]; then
    if [ "$BG_HAS" = "true" ]; then
        _decide_log DECLINE "reason=registry-$BG_TYPE"
    else
        _decide_log DECLINE "reason=registry-absent"
    fi
    exit 0
fi

# Every entry that is not the self entry counts as live, whatever its status
# or type — the harness has already filtered the array to in-flight work. An
# entry with no usable id cannot be proven to BE the self entry, so it counts
# too (defer, never compact on a guess).
OTHERS=$(printf '%s' "$INPUT" | jq -r --arg a "$AGENT_ID" \
    '[.background_tasks[]? | select(((.id // "") | tostring) != $a)] | length' \
    2>/dev/null || echo "1")
[ "$OTHERS" = "0" ] || {
    _decide_log DECLINE "reason=live-tasks n=$OTHERS"
    exit 0
}

# #71 dedup key = this worker, so a repeat SubagentStop for the SAME worker is
# a no-op. Never let a failing sha256sum kill this `set -e` script (the repo's
# documented `VAR=$(failing_cmd)` gotcha) — the `||` fallback keeps it safe.
MSG_HASH=$(printf 'subagent:%s' "$AGENT_ID" | sha256sum 2>/dev/null | cut -d' ' -f1) \
    || MSG_HASH=""

AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
RESULT=$(python3 "$AIRULESET_PY" compact-request --record --session "$SID" \
    --cwd "$CWD" --msg-hash "$MSG_HASH" --origin "subagent-stop" 2>/dev/null) \
    || RESULT=""
case "$RESULT" in
    recorded|sent|claim-queued|queued-compact|dropped-no-work|dropped-small-context|dup|skip) ;;
    *) RESULT="error" ;;
esac
_decide_log RECORD "result=$RESULT"

exit 0
