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
if grep -qE '^[[:space:]]*\*\*GATEKEEPER' <<<"$FIRST_LINE" \
   && ! grep -qE '^[[:space:]]*\*\*GATEKEEPER-ACTION:' <<<"$FIRST_LINE"; then
  echo "gatekeeper finding/review comment — no label (#1500)"
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
