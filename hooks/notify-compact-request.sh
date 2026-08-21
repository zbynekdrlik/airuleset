#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — PERMANENT NO-OP (#400, 2026-08-12).
#
# This hook USED TO record a `/compact` REQUEST by sniffing the turn's
# LAST-ASSISTANT-MESSAGE TEXT for a `## ✅ Work Complete` heading or a
# terminal `✅ DONE:` marker, and attempting a synchronous delivery on a
# match. That text-sniffing channel is REMOVED ENTIRELY, in both
# directions — it must NEVER again be the thing that creates a `/compact`
# request, for any message shape, on any pane.
#
# WHY: `/compact` was meant to fire ONLY at a boundary the SESSION ITSELF
# proves — its own explicit `compact-request --self` callback (the
# session's own Bash tool call, right after IT decides a turn is a genuine
# completed-ticket boundary), or the SubagentStop EVENT hook
# (`notify-compact-subagent-boundary.sh`, gated on
# `agent_type == "autopilot-worker"` and reading the harness's own
# `background_tasks` registry directly — never message prose). A passive
# Stop-hook TEXT match is neither: it fires on the SHAPE of a message, with
# no way to tell "a genuinely completed ticket, safe to discard context at"
# from "a bare `✅ DONE:` one-liner that happens to end an ordinary turn" —
# and a bare-`✅ DONE:` trigger refreshing a PENDING request's own `ts` on
# every ordinary turn is exactly what let a stale request keep looking
# "fresh" for 11.2+ hours in the live incident this removal responds to
# (#400). Reserving `/compact` delivery to the two structural origins above
# — never to text-sniffing — is what makes every OTHER #400 fix (the
# non-refreshable boundary anchor, the live-tasks safety gate, the
# marker-hold grace) actually hold: none of them can protect a request that
# should never have existed in the first place.
#
# The remaining origins are UNCHANGED by this removal (their implementation
# collapsed into `watchdog/compact.py` by #402, 2026-08-12):
#   - `compact-request --self` (a session's own mid-turn Bash tool call,
#     `watchdog.compact.resolve_self_pane` + `deliver_compact`) — origin
#     `self-callback`; also fired via `--record --origin self-callback` by
#     `stop-check-prose-violations.sh` at a `## ✅ Work Complete` report
#     (issue 411's backstop). This is now the SOLE production `/compact`
#     recorder.
#   - `notify-compact-subagent-boundary.sh` (the SubagentStop EVENT hook)
#     USED to record `--origin subagent-stop` per autopilot-worker return, but
#     #610 RETIRED that channel — a worker return is not the SUPERVISOR's ticket
#     boundary under the fleet model (issues 317/456). The hook still runs (the
#     #486 G1 heartbeat + an explicit DECLINE log line) but records nothing.
#
# This file is KEPT (rather than deleted) and its Stop-hook registration
# in settings/hooks.json is KEPT — per this repo's own established
# convention for a permanently-neutered hook (mirrors how the ci-*
# poll-repeat and similar guards stay registered as inert placeholders
# rather than silently vanishing from the chain and leaving a gap a future
# editor might not notice). It reads and discards its stdin, does nothing
# else, and always exits 0 — silent and non-blocking, exactly like before.
#
# Historical note, for anyone reading this file's own git history: every
# prior revision of this file (Stop hook, text-sniffing `## ✅ Work
# Complete` / `✅ DONE:` detection, synchronous delivery, the #71 msg-hash
# dedup, the #125 decision log) is still in `git log -p -- hooks/
# notify-compact-request.sh` — nothing about how that channel worked is
# lost, it is simply retired.

cat >/dev/null 2>&1 || true
exit 0
