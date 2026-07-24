#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash) — airuleset #28, layer B (prevent at the source)
# In SUBAGENT context (the payload carries agent_id) a `run_in_background`
# CI poll is NEVER right: the subagent ends its turn, terminates, and the
# poll's completion fires to the PARENT (ci-monitoring.md — "inside a
# subagent, wait FOREGROUND"). Deny the launch itself with the foreground
# pattern. Deliberately NARROW: only CI-wait signatures (`gh run …`,
# `gh pr checks`) are denied — legitimate background use (a dev server to
# test against, a build you foreground-poll) passes and is caught by the
# SubagentStop gate only if still live at stop. Main session (no agent_id)
# is untouched: a background CI poll there is the supervisor pattern.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -n "$AGENT_ID" ] || exit 0

BG=$(echo "$INPUT" | jq -r '.tool_input.run_in_background // false' 2>/dev/null || echo "false")
[ "$BG" = "true" ] || exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
# tr flattens newlines so a signature split across continuation lines
# ("gh run \\\n view") still matches — grep is line-scoped otherwise
echo "$CMD" | tr '\n' ' ' | grep -qE 'gh[[:space:]]+run[[:space:]]+(view|watch|list)|gh[[:space:]]+pr[[:space:]]+checks' || exit 0

cat >&2 <<'MSG'
BLOCKED: you are a SUBAGENT launching a BACKGROUND CI poll. A subagent that
backgrounds a wait and ends its turn TERMINATES — the completion notification
fires to your PARENT, never to you, and the work silently dies mid-CI
(ci-monitoring.md; ~40% of autopilot-worker failures). Wait FOREGROUND
instead:

  • repeat plain foreground Bash calls until the run is terminal:
      sleep 300 && gh run view <run-id> --json status,conclusion,jobs
    (each call well under the 10-min tool cap; keep the turn alive)
  • for a long / multi-stage pipeline wait your dispatch contract hands to
    the supervisor: do NOT launch any background poll — report the run-id +
    current stage in your final message and return.
MSG
exit 2
