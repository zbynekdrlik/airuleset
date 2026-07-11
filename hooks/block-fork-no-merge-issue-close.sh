#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks `gh issue close` (and the equivalent `gh api ... PATCH state=closed`) when
# THIS stream's autopilot authority is `fork-no-merge`. Exit 2 = block; Claude sees
# stderr as the reason.
#
# Why this exists (incident 2026-07-10, david@gk / odoo-erp):
#   A fork-no-merge worker's OWN instructions forbid closing issues ("the maintainer
#   does at merge") — but the worker drifted mid-session and ran `gh issue close`
#   directly on ~10 issues (#1400, #1408, …). Self-closing an issue short-circuits
#   the cross-fork gatekeeper review this authority stream exists to enforce AND
#   removes the READY-FOR-REVIEW hand-off event the per-ticket Discord card keys off
#   — so the user got no proper work-completed evaluation, just a terse "✅ DONE".
#   A rule in prose drifted; a deterministic hook cannot. (rule-intake gate:
#   mechanically checkable -> hook.)
#
# Scope: only a `fork-no-merge` stream is blocked. `full` / `branch-merge` streams
# legitimately close issues (obsolete tickets, or via a merged PR's `Closes #N`), so
# they pass untouched — resolved per-stream via `airuleset.py authority` (marker-aware).

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Is this an issue-CLOSE action? Match `gh issue close` at a command boundary, plus
# the REST-API PATCH form `gh api .../issues/<N> ... state ... closed`. (A quoted
# mention of the phrase inside e.g. a commit message could false-positive; that is
# fail-SAFE — the worker simply rephrases — and far cheaper than missing a real close.)
is_close=0
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:](])gh[[:space:]]+issue[[:space:]]+close([[:space:]]|$)'; then
    is_close=1
# REST-API PATCH form: require BOTH the PATCH method AND `state=closed` (the field-set
# form) — so a READ predicate like `gh api .../issues/N --jq '.state=="closed"'`
# (which has neither `-X PATCH` nor a bare `state=closed`) is NOT mistaken for a write.
elif printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+api[^|]*issues/[0-9]+' \
     && printf '%s' "$CMD" | grep -qE '(-X|--method)[[:space:]]*[Pp][Aa][Tt][Cc][Hh]' \
     && printf '%s' "$CMD" | grep -qE 'state=closed'; then
    is_close=1
fi
[ "$is_close" -eq 0 ] && exit 0

# Resolve THIS stream's authority (marker-aware; the python reads the cwd project
# CLAUDE.md override, else the per-user map). The hook's cwd IS the session's cwd,
# i.e. the project dir — so the project's authority marker (if any) is honored.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(dirname "$SCRIPT_DIR")
AUTH=$(python3 "$REPO_DIR/airuleset.py" authority 2>/dev/null || echo "")

# Fail-SAFE: a close is being attempted but authority could not be resolved (the CLI
# errored / is missing). Do NOT silently allow it (that would re-enable the exact
# self-close drift on any infra breakage) — BLOCK with a clear diagnostic. This only
# bites when airuleset.py itself is broken, which already breaks the whole stream.
if [ -z "$AUTH" ]; then
    echo "BLOCKED (fail-safe): could not resolve autopilot authority — 'python3 $REPO_DIR/airuleset.py authority' produced no output (CLI missing/broken?)." >&2
    echo "  Refusing 'gh issue close' until authority can be verified. Fix airuleset.py, then retry — or hand off via a comment and let the maintainer close." >&2
    exit 2
fi
[ "$AUTH" != "fork-no-merge" ] && exit 0

echo "BLOCKED: this is a fork-no-merge stream — it must NEVER close a GitHub issue." >&2
echo "" >&2
echo "  The gatekeeper MAINTAINER closes the issue at review/merge. Closing it yourself" >&2
echo "  short-circuits the cross-fork review this authority stream exists to enforce, and" >&2
echo "  removes the READY-FOR-REVIEW hand-off event the per-ticket Discord card keys off" >&2
echo "  (incident 2026-07-10: a worker self-closed ~10 odoo-erp issues, so the user got no" >&2
echo "  proper work-completed evaluation — just a terse '✅ DONE')." >&2
echo "" >&2
echo "  HAND OFF instead, leaving the issue OPEN:" >&2
echo "    - DONE ticket:     gh issue comment <N> --body \"READY-FOR-REVIEW: <branch> — <local verify evidence>\"" >&2
echo "                       then fire the card:" >&2
echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
echo "" >&2
echo "  See agents/autopilot-worker.md (fork-no-merge) + pr-merge-policy.md (reduced-authority scope)." >&2
exit 2
