#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash | Write | Edit | NotebookEdit) — WRITE-OWNERSHIP guard.
#
# TWO independent rules; each blocks a session/agent from writing a checkout it
# does NOT own, while a write it DOES own always passes. Both exit 2 on a block
# with the reason on STDERR (stdout is invisible to the model — repo memory) and
# fail OPEN on any unreadable/ambiguous signal (never brick an unknown context).
#
# RULE A (2026-07-23) — only the airuleset SESSION writes the airuleset repo.
#   The user mistyped an airuleset complaint into the RESTREAMER session (wrong
#   tmux window) and that session fixed watchdog code in ~/devel/airuleset,
#   committed and DEPLOYED via airuleset.py push — the airuleset stream learned
#   about it only afterwards. Wanted: a foreign project session FILES A TICKET
#   in the airuleset repo (gh issue create — stays open) or tells the user they
#   typed into the wrong window; it never commits/pushes/pulls there and never
#   runs airuleset.py push/install. Bash-only (a git write / airuleset.py push
#   only ever arrives as a Bash command). Session identity = the payload
#   transcript_path (its parent dir encodes the LAUNCH dir) or CLAUDE_PROJECT_DIR.
#
# RULE B (#496, 2026-08-15) — a worktree-isolated SUBAGENT must write ONLY
#   inside its OWN worktree, never the shared main checkout. Live incident
#   (worker #433 step 12): dispatched with `isolation: "worktree"` (worktree
#   correctly created), it still Edited the MAIN checkout via ABSOLUTE paths
#   (`__init__.py` −247 lines + a new `wedge.py`), which blocked the serial
#   merge of another lane. Write/Edit never `cd`, so the harness's own cwd-based
#   worktree guard (which resets cwd every Bash call and refuses complex `cd`s
#   out of the worktree) does not catch an absolute-path Write/Edit at all —
#   this is the airuleset-level backstop for that vector, plus the named Bash
#   mutation shapes (`cd <main> && git commit/apply/checkout`, `git -C <main>`,
#   a redirect/tee/sed -i writing a main path).
#
#   Detection (live-verified from a real worktree-subagent payload, never
#   assumed): `.agent_id` present ⇒ subagent (a main session has none — same
#   discriminator block-subagent-bg-ci-poll.sh uses); `.cwd` is the STABLE
#   session cwd = the worktree `<main>/.claude/worktrees/<name>` and does NOT
#   track an in-Bash `cd` (agent threads reset cwd between calls), so it points
#   at the worker's OWN worktree even after a `cd`. `.transcript_path` is SHARED
#   across sibling workers (the supervisor's) — never usable to distinguish, so
#   Rule B keys on cwd + agent_id only. A subagent WITHOUT a worktree cwd (the
#   serial-fallback dispatch) legitimately works the shared tree — ALLOWED. The
#   MAIN session (no agent_id — the supervisor's own integration merges) is
#   ALLOWED. Override (rare, logged): AIRULESET_ALLOW_WORKTREE_ESCAPE=1.
#
# Reads the tool payload on STDIN. Exit 2 = block; exit 0 = allow.

INPUT=$(cat)
jqr() { printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""; }
TOOL=$(jqr '.tool_name // empty')
CMD=$(jqr '.tool_input.command // empty')
CWD=$(jqr '.cwd // empty')
TRP=$(jqr '.transcript_path // empty')
AGENT_ID=$(jqr '.agent_id // empty')

# ======================= RULE B — worktree escape ==========================
# Fires ONLY for a subagent (agent_id) whose session cwd is an isolated worktree.
if [ -n "$AGENT_ID" ] && [ "${AIRULESET_ALLOW_WORKTREE_ESCAPE:-0}" != "1" ]; then
  case "$CWD" in
    */.claude/worktrees/*)
      MAINSTR="${CWD%%/.claude/worktrees/*}"
      _rest="${CWD#*/.claude/worktrees/}"
      WTNAME="${_rest%%/*}"
      if [ -n "$MAINSTR" ] && [ -n "$WTNAME" ]; then
        WTSTR="$MAINSTR/.claude/worktrees/$WTNAME"
        # normalized forms for path CONTAINMENT (realpath -m needs no existence)
        MAIN=$(realpath -m -- "$MAINSTR" 2>/dev/null) || MAIN="$MAINSTR"
        WT=$(realpath -m -- "$WTSTR" 2>/dev/null) || WT="$WTSTR"

        is_under() {  # is $1 under (or equal to) $2 ?
          local p="${1%/}/" base="${2%/}/"
          case "$p" in "$base"*) return 0 ;; *) return 1 ;; esac
        }
        deny_write() {  # $1 = human description of the offending target
          cat >&2 <<EOF
🚫 BLOCKED: you are a worktree-isolated worker (agent $AGENT_ID). You may write
ONLY inside your OWN worktree — never the shared main checkout.

  your worktree : $WTSTR
  refused write : $1

Redo the write with a path INSIDE your worktree ($WTSTR/...). The supervisor
merges your worktree branch into the shared tree; touching the main checkout
directly corrupts the serial-integration merge (incident #496, 2026-08-15).
Deliberate one-off override (logged): prefix env AIRULESET_ALLOW_WORKTREE_ESCAPE=1
EOF
          echo "[block-foreign-airuleset-write:ruleB] $AGENT_ID -> $1" \
            >> /tmp/airuleset-worktree-escape-block.log 2>/dev/null || true
          exit 2
        }

        # --- Write / Edit / NotebookEdit : the target file path ------------
        RAW=""
        case "$TOOL" in
          Write|Edit)   RAW=$(jqr '.tool_input.file_path // empty') ;;
          NotebookEdit) RAW=$(jqr '.tool_input.notebook_path // .tool_input.file_path // empty') ;;
        esac
        if [ -n "$RAW" ]; then
          case "$RAW" in /*) _t="$RAW" ;; *) _t="$WT/$RAW" ;; esac
          ABS=$(realpath -m -- "$_t" 2>/dev/null) || ABS="$_t"
          if is_under "$ABS" "$MAIN" && ! is_under "$ABS" "$WT"; then
            deny_write "$ABS"
          fi
        fi

        # --- Bash : a command whose effective write TARGETS the main checkout.
        # Best-effort layer over the harness's own cwd-based worktree guard; the
        # Write/Edit path above covers the actual #496 incident vector. The
        # SEGMENT-AWARE analysis (per-segment, cd-tracking, target-verified —
        # never a whole-command "main appears somewhere" gate that would false-
        # block `git -C <main> log ; git commit`, R2) lives in worktree_guard.py
        # (shlex tokenization also distinguishes a redirect TARGET from a --body
        # VALUE, so a quoted `> "$MAIN/x"` blocks while `gh issue comment
        # --body "…/main/…"` does not). Fail-open: no python3 / any parse error
        # → allow (the Write/Edit block + the harness remain the primary guard).
        if [ "$TOOL" = "Bash" ] && [ -n "$CMD" ]; then
          # bypass marker, checked OUTSIDE quotes (like RULE A's foreign-ok): a
          # marker written INTO a quoted string must not disable the guard.
          STRIPPED=$(printf '%s' "$CMD" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g') || STRIPPED="$CMD"
          case "$STRIPPED" in
            *"airuleset:worktree-ok"*)
              echo "[block-foreign-airuleset-write:ruleB] bypass marker: $CMD" \
                >> /tmp/airuleset-worktree-escape-block.log 2>/dev/null || true
              ;;
            *)
              WG="$(dirname "${BASH_SOURCE[0]}")/worktree_guard.py"
              if command -v python3 >/dev/null 2>&1 && [ -r "$WG" ]; then
                rc=0
                printf '%s' "$CMD" | python3 "$WG" - "$MAIN" "$WT" >/dev/null 2>&1 || rc=$?
                if [ "$rc" = "2" ]; then
                  deny_write "Bash command mutating the main checkout: $CMD"
                fi
              fi
              ;;
          esac
        fi
      fi
      ;;
  esac
fi

# ======================= RULE A — foreign session ==========================
# Bash-only: a git write / airuleset.py push arrives only as a Bash command.
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
