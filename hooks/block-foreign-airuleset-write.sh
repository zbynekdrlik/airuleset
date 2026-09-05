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
#   Write-op detection (#790, 2026-09-01) is SEGMENT-AWARE, cd-tracking,
#   target-verified via hooks/foreign_repo_guard.py — never "a write verb
#   appears anywhere in the composite command". The round-1 bash regex flagged
#   ANY git write verb present anywhere in the whole command once "devel/
#   airuleset" appeared anywhere too (even just as the PATH to airuleset.py
#   itself in an unrelated `airuleset.py autopilot-lock` call), which
#   false-blocked a git write op on a totally different (foreign) repo sitting
#   alongside that call in the same composite — a routine shape during a
#   foreign-repo integration cycle. See foreign_repo_guard.py's own docstring.
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
#   Rule B keys on cwd + agent_id only. A subagent WITHOUT a worktree cwd is
#   handled by RULE B2 below (#817), NOT here: on airuleset an `autopilot-
#   worker` whose isolation failed is blocked from mutating the shared checkout
#   (a genuine serial-fallback dispatch sets the STANDING env override); a NON-
#   autopilot-worker subagent (SDD implementer, cavecrew, general-purpose, fork)
#   is untouched by B2 and works the shared tree freely. The MAIN session (no
#   agent_id — the supervisor's own integration merges) is ALLOWED. Override
#   (rare, logged): AIRULESET_ALLOW_WORKTREE_ESCAPE=1 as a STANDING env export
#   (a per-command `VAR=1 …` prefix never reaches this hook's own process env).
#
# RULE B2 (#817) — an `autopilot-worker` whose `isolation:"worktree"` SILENTLY
#   did not apply runs in the SHARED airuleset checkout (cwd is NOT a worktree)
#   and can hijack HEAD during the supervisor's serial `git merge --no-ff`
#   integration (a merge commit was LOST). RULE B is BLIND to it (it only
#   engages on a worktree cwd). B2 blocks any git branch-state write / file-
#   write whose resolved target is the shared checkout (CHECKOUT = this hook's
#   own REPO_ROOT), keyed on worktree_guard.py's target RESOLUTION. Scoped to
#   agent_type=="autopilot-worker" (the incident class) so no other subagent is
#   false-blocked. Per-Bash escape: `# airuleset:worktree-ok <reason>`.
#
# Reads the tool payload on STDIN. Exit 2 = block; exit 0 = allow.

INPUT=$(cat)
jqr() { printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""; }
TOOL=$(jqr '.tool_name // empty')
CMD=$(jqr '.tool_input.command // empty')
CWD=$(jqr '.cwd // empty')
TRP=$(jqr '.transcript_path // empty')
AGENT_ID=$(jqr '.agent_id // empty')
AGENT_TYPE=$(jqr '.agent_type // empty')

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
    *)
      # RULE B2 (#817) — an `autopilot-worker` whose `isolation:"worktree"`
      # SILENTLY did not apply: agent_id present, cwd is NOT a worktree, so it
      # runs in the SHARED airuleset checkout. RULE B above is BLIND to this (it
      # only engages on a `*/.claude/worktrees/*` cwd), which is exactly how a
      # worker hijacked the shared HEAD during the supervisor's `git merge
      # --no-ff` integration and a merge commit was LOST. Block any git
      # branch-state write / file-write whose RESOLVED target is this shared
      # checkout. CHECKOUT = the installed hook's OWN checkout (REPO_ROOT,
      # dirname-dirname of this script) — inherently airuleset-scoped (a worker
      # on another repo targets a different tree → not under CHECKOUT → allowed).
      # Keying on worktree_guard.py's target RESOLUTION (cwd + `cd`-tracking +
      # `-C`) rather than a cwd string catches a subdir cwd AND `git -C
      # <checkout> …` from any cwd. SCOPED to agent_type=="autopilot-worker" (the
      # incident class): a NON-autopilot-worker subagent (SDD/cavecrew/general-
      # purpose/fork doing sanctioned shared-tree work) is never false-blocked,
      # and the per-command hot path only reaches the python3 spawn for a
      # (rare) isolation-FAILED autopilot worker. The MAIN session (no agent_id)
      # is exempt by the enclosing `if`.
      CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || CHECKOUT=""
      if [ "$AGENT_TYPE" = "autopilot-worker" ] && [ -n "$CHECKOUT" ] && [ -n "$CWD" ]; then
        CO=$(realpath -m -- "$CHECKOUT" 2>/dev/null) || CO="$CHECKOUT"
        # #492: per-uid log path — a FIXED /tmp name collides across users on a
        # shared box (first user owns it, others' append EACCES leaks to stderr).
        B2LOG="/tmp/airuleset-worktree-escape-block-${EUID:-$(id -u)}.log"

        b2_is_under() {  # is $1 under (or equal to) $2 ?
          local p="${1%/}/" base="${2%/}/"
          case "$p" in "$base"*) return 0 ;; *) return 1 ;; esac
        }
        b2_deny() {  # $1 = human description of the offending target
          cat >&2 <<EOF
🚫 BLOCKED: your isolation:"worktree" did NOT apply — you (autopilot-worker
$AGENT_ID) are running in the SHARED airuleset checkout, not an isolated
worktree. A dispatched worker must NEVER mutate the shared checkout's HEAD /
branches / tree: it hijacks the supervisor's serial-integration merge and can
LOSE a merge commit (#817).

  shared checkout : $CO
  refused         : $1

STOP now. Your FIRST step is the isolation self-check: 'git rev-parse
--show-toplevel' must be a .claude/worktrees/ path (NOT the bare main checkout)
and 'git symbolic-ref --short HEAD' must NOT be main/dev (a worktree branch:
worktree-agent-* / worktree-issue-*). If it is the shared checkout on main/dev,
return "ISOLATION FAILED" so the supervisor re-dispatches — never work here.
Genuine serial-fallback override: a STANDING env export
AIRULESET_ALLOW_WORKTREE_ESCAPE=1 (a per-command VAR=1 prefix does NOT reach
this hook), or per-Bash-command append '# airuleset:worktree-ok <reason>'.
EOF
          { echo "[block-foreign-airuleset-write:ruleB2] $AGENT_ID -> $1" \
            >> "$B2LOG"; } 2>/dev/null || true
          exit 2
        }

        # --- Write / Edit / NotebookEdit : ANY target under the shared checkout.
        # No worktree carve-out here (#817 review): an isolation-failed worker
        # owns NO worktree, so a write into `.claude/worktrees/*` would corrupt a
        # SIBLING's checkout. Relative paths resolve against the worker's cwd.
        RAW=""
        case "$TOOL" in
          Write|Edit)   RAW=$(jqr '.tool_input.file_path // empty') ;;
          NotebookEdit) RAW=$(jqr '.tool_input.notebook_path // .tool_input.file_path // empty') ;;
        esac
        if [ -n "$RAW" ]; then
          case "$RAW" in /*) _t="$RAW" ;; *) _t="$CWD/$RAW" ;; esac
          ABS=$(realpath -m -- "$_t" 2>/dev/null) || ABS="$_t"
          if b2_is_under "$ABS" "$CO"; then
            b2_deny "$ABS"
          fi
        fi

        # --- Bash : a git branch-state op / file-write targeting the shared
        # checkout. worktree_guard.py --shared resolves the effective target
        # (newline-split + cd-tracking + -C + env/wrapper prefix strip) and also
        # covers rm/cp/mv/sed -i/tee/redirect writes.
        if [ "$TOOL" = "Bash" ] && [ -n "$CMD" ]; then
          STRIPPED=$(printf '%s' "$CMD" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g') || STRIPPED="$CMD"
          case "$STRIPPED" in
            *"airuleset:worktree-ok"*)
              { echo "[block-foreign-airuleset-write:ruleB2] bypass marker: $CMD" \
                >> "$B2LOG"; } 2>/dev/null || true
              ;;
            *)
              WG="$(dirname "${BASH_SOURCE[0]}")/worktree_guard.py"
              if command -v python3 >/dev/null 2>&1 && [ -r "$WG" ]; then
                rc=0
                printf '%s' "$CMD" | python3 "$WG" --shared - "$CO" "$CWD" >/dev/null 2>&1 || rc=$?
                if [ "$rc" = "2" ]; then
                  b2_deny "Bash git/file op mutating the shared checkout: $CMD"
                fi
              fi
              ;;
          esac
        fi
      fi
      ;;
  esac
fi

# ======================= RULE C — controller push-origin (#870 F3) =========
# When CONTROLLER_CUTOVER_DONE is True, block `airuleset.py push` from a
# non-controller box. When the flag is False (the dev1-safe commit-A
# default), this is a no-op. The python3 -c import runs ONLY when CMD
# already passed the "airuleset" prefilter below (line ~305), so zero cost
# for non-airuleset commands.
case "$CMD" in *airuleset*)
  _CUTOVER_DONE="$(python3 -c 'import sys; sys.path.insert(0,"'"$REPO_DIR"'"); from cli_fleet import CONTROLLER_CUTOVER_DONE; print(int(CONTROLLER_CUTOVER_DONE))' 2>/dev/null || echo 0)"
  if [ "$_CUTOVER_DONE" = "1" ]; then
    _BOX_CLASS="$(cat "${HOME:-/nonexistent}/.claude/airuleset-box-class" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
    if [ "$_BOX_CLASS" != "controller" ]; then
      case "$CMD" in
        *"airuleset.py push"*|*"airuleset.py"*" push"*)
          echo "[block-foreign-airuleset-write:ruleC] push from non-controller box blocked (class=$_BOX_CLASS)" >&2
          exit 2 ;;
      esac
    fi
  fi
  ;;
esac

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

# --- strip quoted spans, then check the bypass marker -----------------------
STRIPPED=$(printf '%s' "$CMD" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g')
case "$STRIPPED" in *"airuleset:foreign-ok"*)
  # #492: per-USER log path — a FIXED /tmp name collides across users on a
  # shared box (first user owns it, others' append fails EACCES and the
  # error LEAKS to stderr past `2>/dev/null`). ${EUID} isolates each user;
  # the brace group makes the write silent even if the file is unwritable.
  FOREIGN_BYPASS_LOG="/tmp/airuleset-foreign-write-bypass-${EUID:-$(id -u)}.log"
  { echo "[block-foreign-airuleset-write] bypass marker used: $CMD" \
    >> "$FOREIGN_BYPASS_LOG"; } 2>/dev/null || true
  exit 0 ;;
esac

# --- cheap prefilter: "airuleset" absent from BOTH the command text and the
# tool call's cwd means NO possible target the analyzer could resolve can
# ever contain "devel/airuleset" either (a -C/--git-dir/cd target is always
# either a literal token in $CMD or resolved relative to $CWD — normpath/
# join cannot conjure the substring from nothing) — so skip the python3 spawn
# entirely on the hot path of every OTHER foreign-session Bash call fleet-
# wide (review finding #790 2/6: the old TARGETS-style textual prefilter was
# deleted outright instead of demoted to a prefilter, so every non-airuleset
# Bash call was paying a sed + a python3 startup for nothing).
case "$CMD$CWD" in
  *airuleset*) : ;;
  *) exit 0 ;;
esac

# --- does the command ACTUALLY WRITE a devel/airuleset checkout? -----------
# Segment-aware, cd-tracking, target-verified (#790) — never "a write verb
# appears somewhere in the composite" (that false-blocked a git write on a
# totally different repo sitting alongside an unrelated airuleset.py CLI
# call, e.g. `autopilot-lock`, in the SAME composite command — autopilot-lock
# is called routinely right next to a foreign repo's own integration git
# steps). See hooks/foreign_repo_guard.py for the full rationale; it mirrors
# the RULE B segment-aware shape (worktree_guard.py, #496) with a different
# target test. Fail-open: no python3 / any parse error → allow (RULE A's own
# documented "never brick an unknown context" stance).
FRG="$(dirname "${BASH_SOURCE[0]}")/foreign_repo_guard.py"
if command -v python3 >/dev/null 2>&1 && [ -r "$FRG" ]; then
  rc=0
  printf '%s' "$CMD" | python3 "$FRG" - "$CWD" >/dev/null 2>&1 || rc=$?
  [ "$rc" = "2" ] || exit 0
else
  exit 0
fi

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
