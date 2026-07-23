#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — only the airuleset SESSION writes the airuleset repo.
#
# 2026-07-23 incident: the user mistyped an airuleset complaint into the
# RESTREAMER session (wrong tmux window) and that session fixed watchdog code
# in ~/devel/airuleset, committed and DEPLOYED via airuleset.py push — the
# airuleset stream learned about it only afterwards. Wanted behavior: a
# foreign project session FILES A TICKET in the airuleset repo (gh issue
# create — stays open) or tells the user they typed into the wrong window; it
# never commits/pushes/pulls there and never runs airuleset.py push/install.
#
# Session identity = the payload transcript_path (its parent dir encodes the
# session's LAUNCH dir) or CLAUDE_PROJECT_DIR; missing both = fail-open.
# Reads the tool payload on STDIN (.tool_input.command + .cwd).
# Exit 2 = block (stderr shown to the agent); exit 0 = allow.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
TRP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# --- is THIS the airuleset session? (then everything is allowed) -----------
case "$TRP" in
  */-*devel-airuleset/*) exit 0 ;;
esac
case "${CLAUDE_PROJECT_DIR:-}" in
  */devel/airuleset) exit 0 ;;
esac
# no identity signal at all → fail-open (never brick an unknown context)
if [ -z "$TRP" ] && [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  exit 0
fi

[ "${AIRULESET_ALLOW_FOREIGN_WRITE:-0}" = "1" ] && exit 0

# --- does the command TARGET an airuleset checkout? -------------------------
# Raw-string path match (quoted or not) OR the tool call's cwd inside one.
TARGETS=0
case "$CMD" in *devel/airuleset*) TARGETS=1 ;; esac
case "$CWD" in */devel/airuleset|*/devel/airuleset/*) TARGETS=1 ;; esac
[ "$TARGETS" = "1" ] || exit 0

# --- strip quoted spans, then match the WRITE ops + the bypass marker -------
STRIPPED=$(printf '%s' "$CMD" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g')
case "$STRIPPED" in *"airuleset:foreign-ok"*)
  echo "[block-foreign-airuleset-write] bypass marker used: $CMD" \
    >> /tmp/airuleset-foreign-write-bypass.log 2>/dev/null || true
  exit 0 ;;
esac

is_write() {
  printf '%s' "$STRIPPED" | grep -qE \
    '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+(commit|push|pull|merge|rebase|cherry-pick|revert|reset|add|rm|mv|stash|tag|am|apply)([[:space:]]|$)' \
    && return 0
  printf '%s' "$STRIPPED" | grep -qE 'airuleset\.py[[:space:]]+(push|install)([[:space:]]|$)' \
    && return 0
  return 1
}
is_write || exit 0

cat >&2 <<'EOF'
🚫 BLOCKED: the airuleset repo is written ONLY from its own session.

This session belongs to a DIFFERENT project. If you found an airuleset
problem (watchdog, hooks, statusline, rules, deploy targets), do ONE of:

  1. File a ticket for the airuleset stream (stays fully allowed):
       gh issue create -R zbynekdrlik/airuleset -t "<problem>" -F body.md
  2. Tell the user they typed into the wrong tmux window and the prompt
     belongs to the airuleset session.

Never commit/push/pull */devel/airuleset or run airuleset.py push/install
from a foreign session — the airuleset stream must own its own changes
(incident 2026-07-23). Deliberate one-off bypass (logged):
append  # airuleset:foreign-ok <reason>
EOF
exit 2
