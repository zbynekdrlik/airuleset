#!/usr/bin/env bash
set -euo pipefail
# =============================================================================
# #1500 — precise sub-dev readiness matcher for subdev-handoff-label.yml.
# #331  — hardened: (a) here-string matching instead of piped printf — the
#         piped form is a live SIGPIPE/pipefail race (this repo's own
#         .claude/rules/airuleset-internals.md already documents the same
#         class in stop-check-prose-violations.sh/block-commit-without-
#         design.sh): `grep -q` exits at its FIRST match without draining
#         stdin, and when the match sits on line 1 (the canonical,
#         RECOMMENDED READY-FOR-REVIEW: form) of a large body, `printf` is
#         still writing when the pipe closes, gets SIGPIPEd (141), and under
#         `set -o pipefail` the caller's `if` reads the WRITER's failure
#         instead of grep's real verdict — a genuinely, correctly-anchored
#         marker silently reads as absent. Live-reproduced deterministically
#         10/10 on an 80 KB body (well under GitHub's real 65536-char comment
#         cap) against the pre-#331 piped version; a downstream consumer of
#         this template independently hit the identical live CI false-
#         negative and fixed it the same way 15 days before this template
#         was synced. A here-string has no separate writer PROCESS, so
#         SIGPIPE cannot occur. (b) a GATEKEEPER-ACTION: mode and a
#         BOUNCE-RESOLVED: mode, each its own line-anchored marker with its
#         own label — a stream's infra request or bounce-clear notice must
#         never collide with, or get silently absorbed into, the
#         ready-for-review hand-off signal (#331's own reported landmine:
#         a readiness comment that also carries GATEKEEPER-ACTION content
#         got mislabeled needs-gatekeeper instead of ready-for-review,
#         because nothing gave the two signals separate matcher paths).
#
# WHY: the workflow's `if:` used a bare substring contains() on the comment
# body, so a GATEKEEPER comment merely MENTIONING the marker mid-sentence
# ("**Po READY-FOR-REVIEW pokračujem hneď.**", live incident 2026-07-14 on
# #1489) falsely re-added the ready-for-review label. GitHub Actions
# expressions have no anchoring/regex, so the precise decision lives here in
# shell; the workflow keeps only a coarse contains() pre-filter and calls this.
#
# MODE ($1, optional): "ready-for-review" (default, unchanged behavior — the
#   existing workflow step calls this script with no args), "gatekeeper-action",
#   or "bounce-resolved".
#
# INPUT:  the full comment body on stdin.
# EXIT:   0 = genuine match for the selected mode (add/remove the label)
#         1 = mere mention / quote / gatekeeper prose (do NOT label)
#         2 = unknown mode argument (caller bug, not a body-matching result)
#
# Readiness contract (mode=ready-for-review, matches every real hand-off form
# observed to date):
#   - a LINE starting with READY-FOR-REVIEW, allowing markdown emphasis/header
#     /list prefixes (* _ # -) but NOT blockquote '>' (a quoted marker is
#     someone ELSE's hand-off) — montalu's and david's lead-marker style;
#   - the full phrase "Ready for gatekeeper cross-fork review" at the END of
#     a line (David's CLAUDE.md template closes the comment with it). The
#     end-anchor kills the mention class ("toto este NIE JE Ready for
#     gatekeeper cross-fork review, oprav to").
# Everything else is a mention: mid-sentence marker, quoted "> READY-…",
# review prose like "po oprave napíš READY-FOR-REVIEW".
#
# Gatekeeper-action contract (mode=gatekeeper-action, #331 — a sub-dev stream
# is blocked on something only the gatekeeper can do: access, dispatch,
# infra; this is the SAME `GATEKEEPER-ACTION:` convention airuleset.py's own
# cmd_gk_request degrades to when it cannot apply the needs-gatekeeper label
# directly):
#   - a LINE starting with GATEKEEPER-ACTION: (same emphasis/header/list
#     prefixes allowed, blockquote '>' NOT — same anchoring discipline as
#     READY-FOR-REVIEW above).
#
# Bounce-resolved contract (mode=bounce-resolved, #331 — a sub-dev stream
# resolved a bounce that lives on a DEDICATED tracking issue (e.g. "BOUNCE
# #123/#456: ...") rather than on the issue carrying the actual fix, so the
# ready-for-review step's own same-issue prio:bounce clear (below) never
# fires for it, and the stream cannot remove the label itself (read-only
# collaborator, 403)):
#   - a LINE starting with BOUNCE-RESOLVED: (same emphasis/header/list
#     prefixes allowed, blockquote '>' NOT — same anchoring discipline as
#     the other two markers above).
#
# All three modes share ONE exception: a body whose first line opens with
# **GATEKEEPER (gatekeeper's own finding/review comments) never matches
# any marker, whatever it contains.
# =============================================================================

MODE="${1:-ready-for-review}"

# =============================================================================
# #1056 L2 (g) — bounce-clear-guard: is it SAFE to clear prio:bounce?
#
# A stream signals a resolved bounce by re-posting READY-FOR-REVIEW / a
# BOUNCE-RESOLVED marker, and this workflow clears prio:bounce on that comment.
# The blind re-flag class (montalu, odoo-erp #5613/#6890, 16.-17.9.2026): the
# RFR is re-posted minutes AFTER a gk BOUNCE verdict with the PR head UNCHANGED
# since before it, so clearing prio:bounce hands a still-broken ticket back as
# "ready". This mode gates the clear: clear ONLY when the RFR comment is NEWER
# than the newest gk BOUNCE comment AND a commit on the PR branch is NEWER than
# that BOUNCE. Timestamps are compared DIRECTLY (the design's replay guard =
# "max(comment created_at) of the processed set", never a now-2h wall-clock
# window). The workflow does the gh api reads (author = the gk login variable)
# and passes the three ISO timestamps here; this mode is a pure comparison so
# the test suite drives it hermetically.
#
# ARGS: $2 = RFR comment created_at (ISO), $3 = newest gk BOUNCE created_at
#       (ISO, empty when no BOUNCE stands), $4 = newest PR-branch commit date
#       (ISO, empty when unresolvable).
# EXIT: 0 = safe to clear prio:bounce, 1 = blind re-flag / unverifiable → do NOT
#       clear. Handled BEFORE reading stdin (this mode takes no body).
# =============================================================================
if [ "$MODE" = "bounce-clear-guard" ]; then
  RFR_TS="${2:-}"
  GK_TS="${3:-}"
  COMMIT_TS="${4:-}"
  # No gk BOUNCE stands → nothing to guard; clearing is safe.
  if [ -z "$GK_TS" ]; then
    echo "bounce-clear-guard: no gk BOUNCE verdict — safe to clear"
    exit 0
  fi
  gk_epoch="$(date -u -d "$GK_TS" +%s 2>/dev/null || echo "")"
  rfr_epoch="$(date -u -d "$RFR_TS" +%s 2>/dev/null || echo "")"
  commit_epoch="$(date -u -d "$COMMIT_TS" +%s 2>/dev/null || echo "")"
  # An unparseable BOUNCE timestamp cannot be compared → conservative (a real
  # BOUNCE we cannot time is never proven answered) → do NOT clear.
  if [ -z "$gk_epoch" ]; then
    echo "bounce-clear-guard: unparseable gk BOUNCE timestamp — not clearing"
    exit 1
  fi
  # Clear ONLY when a genuine response landed: the RFR AND a commit are both
  # STRICTLY newer than the BOUNCE. A missing/unparseable RFR or commit cannot
  # prove a fix → do NOT clear (the safe-against-blind-flip direction).
  if [ -n "$rfr_epoch" ] && [ -n "$commit_epoch" ] \
       && [ "$rfr_epoch" -gt "$gk_epoch" ] \
       && [ "$commit_epoch" -gt "$gk_epoch" ]; then
    echo "bounce-clear-guard: RFR and a commit are both newer than the newest gk BOUNCE — safe to clear"
    exit 0
  fi
  echo "bounce-clear-guard: RFR/commit not newer than the newest gk BOUNCE (blind re-flag / unverifiable) — NOT clearing prio:bounce"
  exit 1
fi

BODY="$(cat)"

# NO `printf "$BODY" | grep -q` pipelines here — see the #331 header note
# above for why that shape is a live SIGPIPE/pipefail race. Here-strings
# have no writer process, so there is nothing for SIGPIPE to kill.
FIRST_LINE="${BODY%%$'\n'*}"

# Gatekeeper finding/review comments open with **GATEKEEPER — never a match,
# in any mode. #331 (adversarial-review fix): a "next char is not a literal
# hyphen" carve-out is too narrow an exception in EITHER direction — it must
# exclude every real gatekeeper-authored opening ("**GATEKEEPER " /
# "**GATEKEEPER:" / "**GATEKEEPER —") while NEVER excluding the one
# legitimate hyphenated marker a sub-dev stream can open a comment with,
# **GATEKEEPER-ACTION:**. grep -E has no negative lookahead, so this is
# split into two greps: "opens with **GATEKEEPER" AND "is not the
# **GATEKEEPER-ACTION: marker" — a hyphen-glued opening OTHER than that one
# marker (e.g. **GATEKEEPER-BOUNCE**, the review's own live repro) is now
# correctly excluded, which the earlier single-lookahead-substitute regex
# missed.
#
# #340: a real gatekeeper-authored review comment can ALSO open with a
# markdown HEADING instead of bold text ("## Gatekeeper review — BOUNCE",
# odoo-erp#2878, the exact shape locked as a fixture in this repo's own
# test suite) — the bold-only check above never matched that shape, so
# such a comment quoting/mentioning a READY-FOR-REVIEW:/GATEKEEPER-
# ACTION:/BOUNCE-RESOLVED: line for context got mislabelled. Matched as a
# SEPARATE, ORed grep (never folded into the bold check's own regex) —
# the corpus's exact observed casing ("Gatekeeper", title case) is
# required, never bare "GATEKEEPER", so a sub-dev's own legitimate
# heading-decorated ALL-CAPS marker (e.g. "# GATEKEEPER-ACTION: ...")
# stays unaffected without needing its own negated exception branch (no
# real marker is ever written as a heading whose text starts with
# "Gatekeeper" in title case). Plain ASCII, no \b — the locale-dependent
# \b-near-multibyte bug this repo's own playbook documents (#316/#319)
# does not apply here.
if { grep -qE '^[[:space:]]*\*\*GATEKEEPER' <<<"$FIRST_LINE" \
       && ! grep -qE '^[[:space:]]*\*\*GATEKEEPER-ACTION:' <<<"$FIRST_LINE"; } \
   || grep -qE '^[[:space:]]*#{1,6}[[:space:]]*Gatekeeper' <<<"$FIRST_LINE"; then
  echo "gatekeeper finding/review comment — no label (#1500, #340)"
  exit 1
fi

case "$MODE" in
  ready-for-review)
    if grep -qE '^[[:space:]]*([#*_-]+[[:space:]]*)?READY-FOR-REVIEW' <<<"$BODY"; then
      echo "readiness: line-start READY-FOR-REVIEW marker"
      exit 0
    fi
    if grep -qE 'Ready for gatekeeper cross-fork review[.!]?[[:space:]]*$' <<<"$BODY"; then
      echo "readiness: cross-fork review phrase closing a line"
      exit 0
    fi
    echo "not a readiness comment (marker mentioned mid-text or absent) — no label (#1500)"
    exit 1
    ;;
  gatekeeper-action)
    if grep -qE '^[[:space:]]*([#*_-]+[[:space:]]*)?GATEKEEPER-ACTION:' <<<"$BODY"; then
      echo "gatekeeper-action: line-start GATEKEEPER-ACTION: marker"
      exit 0
    fi
    echo "not a gatekeeper-action request (marker mentioned mid-text or absent) — no label (#331)"
    exit 1
    ;;
  bounce-resolved)
    if grep -qE '^[[:space:]]*([#*_-]+[[:space:]]*)?BOUNCE-RESOLVED:' <<<"$BODY"; then
      echo "bounce-resolved: line-start BOUNCE-RESOLVED: marker"
      exit 0
    fi
    echo "not a bounce-resolved notice (marker mentioned mid-text or absent) — no label change (#331)"
    exit 1
    ;;
  *)
    echo "unknown mode: $MODE (expected ready-for-review|gatekeeper-action|bounce-resolved)" >&2
    exit 2
    ;;
esac
