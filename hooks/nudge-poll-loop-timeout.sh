#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — airuleset #90.
#
# ci-monitoring.md's bounded poll-loop shape (`for i in $(seq ...); do
# <poll>; sleep N; done`) is only safe against the harness's own SIGTERM if
# the Bash TOOL CALL's own `timeout` parameter is raised near its 600000ms
# cap — that parameter is easy to forget, since the loop body reads as
# complete without it. Live-hit (gk subagent, 2026-07-26): a poll written
# exactly per that shape, with no `timeout` param raised, was SIGTERM'd
# (exit 143) at the harness's OBSERVED default (120000ms / 2 minutes),
# mid-poll, with NO output — three tool calls where one was intended,
# because the dead call had to be silently retried before the timeout was
# finally set correctly.
#
# This hook NEVER blocks — the poll itself must always be allowed to run
# (the ticket's own explicit requirement). It only ever prints a
# corrective NUDGE via `hookSpecificOutput.additionalContext` (the same
# non-blocking injection shape `inject-situational-rule.sh` already uses)
# so the reminder actually reaches the model's context on THIS call,
# rather than depending on prose the model has already forgotten once.
#
# Detection is intentionally generic, not `gh run view`-specific (#90 point
# 3: other waiting loops — build, deploy, remote — deserve the same
# nudge): the shape is a `sleep` sitting in a loop BODY, i.e. the tokens
# `do` … `sleep` … `done` in that ORDER. A one-shot `sleep 5 && curl ...`
# has no loop and is not this pattern.
#
# #111: it used to be two INDEPENDENT existence greps (`sleep` anywhere
# AND `done` anywhere), which answer "does this token appear?" and never
# "is this sleep the loop's body" — so writing a note about a poll loop,
# grepping the rule file that documents one, or a settle `sleep` next to
# an unrelated fan-out loop all took the full nudge. Replaying every
# Bash call in all 94 local transcripts (77,365 calls) through the hook:
# 983 nudges before, 780 after — and of the 41 nudged calls the harness
# actually KILLED, 39 still nudge (the 2 lost are bare `sleep 200`-style
# calls with no loop at all, already out of this hook's contract). The
# order matters more than it looks: `does`/`done` are not `do`, so prose
# loses on the `do` token while every real for/while/until body matches.
# Ordering — not "the sleep must precede the NEXT done" — because that
# stricter form silently stops nudging NESTED loops, a real poll shape.
#
# Matched on a normalized copy: every non-word byte becomes a space and
# runs are squeezed, so the command is one flat, single-space token
# stream. That is what makes ONE portable ERE work — `grep` is
# line-oriented (a raw multi-line command can never match a single-line
# pattern), and PCRE's `-Pz` is not an option (`-z` means NUL-separated
# data in GNU grep but *decompress* in ugrep, and the managed boxes are
# not guaranteed uniform). It also removes the boundary-SHARING trap:
# with consuming character classes, ` do sleep 5; done` cannot match
# `…do[^w]…[^w]sleep…`, because the ONE space between `do` and `sleep`
# would have to serve as both boundaries at once. Caught by the corpus
# replay, not by the unit tests (whose loops all happen to have a
# separate token between `do` and `sleep`): that first draft went quiet
# on 243 real `until …; do sleep 5; done` polls. Hence the ` do( .*)?
# sleep( .*)? done ` form, where each separator is claimed exactly once.
#
# Threshold: nudge when the call's own `tool_input.timeout` is missing or
# below AIRULESET_POLL_NUDGE_MIN_MS (default 400000 — comfortably above
# the harness's observed 120000ms default, comfortably below the 600000ms
# cap ci-monitoring.md recommends).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

printf ' %s ' "$CMD" | tr -c 'A-Za-z0-9_' ' ' | tr -s ' ' \
    | grep -qE ' do( .*)? sleep( .*)? done ' || exit 0

# #107: a `run_in_background: true` call detaches immediately -- the Bash
# tool's own `timeout` parameter only bounds the FOREGROUND call and is
# irrelevant to a backgrounded loop's own internal runtime. Nudging "raise
# your timeout" on one is wrong advice (ci-monitoring.md now sanctions a
# background waiter as the LONG-wait mechanism, with its own death/budget
# self-bound, not the tool timeout).
BG=$(echo "$INPUT" | jq -r '.tool_input.run_in_background // false' 2>/dev/null || echo "false")
[ "$BG" = "true" ] && exit 0

TIMEOUT_MS=$(echo "$INPUT" | jq -r '.tool_input.timeout // empty' 2>/dev/null || echo "")
MIN_MS="${AIRULESET_POLL_NUDGE_MIN_MS:-400000}"
case "$MIN_MS" in ''|*[!0-9]*) MIN_MS=400000 ;; esac

NEEDS_NUDGE=0
case "$TIMEOUT_MS" in
    ''|*[!0-9]*) NEEDS_NUDGE=1 ;;
    *) [ "$TIMEOUT_MS" -lt "$MIN_MS" ] && NEEDS_NUDGE=1 ;;
esac

[ "$NEEDS_NUDGE" = "1" ] || exit 0

MSG="NUDGE (not a block — this poll WILL run, #90): this Bash call looks like a bounded poll/wait loop (a \`sleep\` in a \`do\`…\`done\` body) but its own \`timeout\` tool parameter is ${TIMEOUT_MS:-unset}. The harness's own default (observed: 120000ms) will SIGTERM a multi-minute loop mid-poll with NO output, costing extra tool-call round-trips instead of the one call ci-monitoring.md intends. Set the Bash tool's own \`timeout\` parameter near its 600000ms cap on this call — and, per ci-monitoring.md, also raise AIRULESET_POLL_BUDGET_S to match so the loop's own SECONDS-based self-bound uses the extra budget instead of giving up early at its conservative default."

python3 - "$MSG" <<'PYEOF' 2>/dev/null || exit 0
import json
import sys

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": sys.argv[1],
    }
}))
PYEOF

exit 0
