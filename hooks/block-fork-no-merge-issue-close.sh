#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks `gh issue close` (and the equivalent `gh api ... PATCH state=closed`) when
# THIS stream's autopilot authority is `fork-no-merge` — UNLESS the issue being
# closed is the stream's OWN (self-authored). Exit 2 = block; Claude sees stderr.
#
# Semantics (refined by the gatekeeper, 2026-07-11):
#   - ASSIGNED / foreign-authored tickets: NEVER closed by a fork-no-merge stream —
#     the gatekeeper maintainer closes them at cross-fork review/merge. Self-closing
#     one removes the READY-FOR-REVIEW hand-off event and bypasses review.
#   - SELF-AUTHORED sub-findings (tickets the stream itself filed while working,
#     e.g. kvaskodev-authored kiosk sub-issues): closing them WITH evidence is the
#     stream's normal bookkeeping — ALLOWED. The 2026-07-10 "drift" suspicion was
#     falsified: those ~10 closes were David's own sub-findings, review was NOT
#     bypassed (the hand-off tickets stayed open).
#   The check is mechanical: issue author == the stream's authenticated gh login.
#   Undeterminable (gh error, no auth) → fail-SAFE: block, with the hand-off recipe.
#
# Scope: only a `fork-no-merge` stream is gated. `full` / `branch-merge` streams
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

# fork-no-merge: allow closing a SELF-AUTHORED issue (the stream's own sub-finding).
# Extract the issue number + optional -R/--repo from the `gh issue close` form; the
# `gh api PATCH` form is never exempted (use `gh issue close` for legit self-closes).
ISSUE_NUM=$(printf '%s' "$CMD" | grep -oE 'gh[[:space:]]+issue[[:space:]]+close[[:space:]]+"?#?([0-9]+)' | grep -oE '[0-9]+' | head -1 || echo "")
REPO_ARG=$(printf '%s' "$CMD" | grep -oE '(-R|--repo)[[:space:]=]+"?[A-Za-z0-9._/-]+' | head -1 | sed -E 's/^(-R|--repo)[[:space:]=]+"?//' || echo "")
if [ -n "$ISSUE_NUM" ]; then
    ME=$(gh api user -q .login 2>/dev/null || echo "")
    if [ -n "$REPO_ARG" ]; then
        AUTHOR=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json author -q .author.login 2>/dev/null || echo "")
    else
        AUTHOR=$(gh issue view "$ISSUE_NUM" --json author -q .author.login 2>/dev/null || echo "")
    fi
    if [ -n "$ME" ] && [ -n "$AUTHOR" ] && [ "$ME" = "$AUTHOR" ]; then
        exit 0   # self-authored sub-finding — the stream's own bookkeeping, allowed
    fi
fi

echo "BLOCKED: fork-no-merge stream — you may close ONLY your OWN (self-authored) issues." >&2
echo "" >&2
echo "  This issue is assigned / foreign-authored (or its author could not be verified):" >&2
echo "  the gatekeeper MAINTAINER closes it at cross-fork review/merge. Closing it yourself" >&2
echo "  removes the READY-FOR-REVIEW hand-off event and bypasses the review this authority" >&2
echo "  stream exists to enforce. (Self-authored sub-findings ARE closable — the hook" >&2
echo "  verifies author == your gh login; if gh failed just now, fix auth and retry.)" >&2
echo "" >&2
echo "  HAND OFF instead, leaving the issue OPEN:" >&2
echo "    - DONE ticket:     gh issue comment <N> --body \"READY-FOR-REVIEW: <branch> — <local verify evidence>\"" >&2
echo "                       then fire the card:" >&2
echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
echo "" >&2
echo "  See agents/autopilot-worker.md (fork-no-merge) + pr-merge-policy.md (reduced-authority scope)." >&2
exit 2
