#!/usr/bin/env bash
set -euo pipefail
# =============================================================================
# #1500 — precise sub-dev readiness matcher for subdev-handoff-label.yml.
#
# WHY: the workflow's `if:` used a bare substring contains() on the comment
# body, so a GATEKEEPER comment merely MENTIONING the marker mid-sentence
# ("**Po READY-FOR-REVIEW pokračujem hneď.**", live incident 2026-07-14 on
# #1489) falsely re-added the ready-for-review label. GitHub Actions
# expressions have no anchoring/regex, so the precise decision lives here in
# shell; the workflow keeps only a coarse contains() pre-filter and calls this.
#
# INPUT:  the full comment body on stdin.
# EXIT:   0 = genuine readiness hand-off (add the label)
#         1 = mere mention / quote / gatekeeper prose (do NOT label)
#
# Readiness contract (matches every real hand-off form observed to date):
#   - a LINE starting with READY-FOR-REVIEW, allowing markdown emphasis/header
#     /list prefixes (* _ # -) but NOT blockquote '>' (a quoted marker is
#     someone ELSE's hand-off) — montalu's and david's lead-marker style;
#   - the full phrase "Ready for gatekeeper cross-fork review" at the END of
#     a line (David's CLAUDE.md template closes the comment with it). The
#     end-anchor kills the mention class ("toto este NIE JE Ready for
#     gatekeeper cross-fork review, oprav to").
#   - EXCEPT: a body whose first line opens with **GATEKEEPER (gatekeeper
#     finding/review comments) never labels, whatever it contains.
# Everything else is a mention: mid-sentence marker, quoted "> READY-…",
# review prose like "po oprave napíš READY-FOR-REVIEW".
# =============================================================================

BODY="$(cat)"

# Gatekeeper finding/review comments open with **GATEKEEPER — never a hand-off.
if printf '%s\n' "$BODY" | head -1 | grep -qE '^[[:space:]]*\*\*GATEKEEPER'; then
  echo "gatekeeper finding/review comment — no label (#1500)"
  exit 1
fi

if printf '%s\n' "$BODY" | grep -qE '^[[:space:]]*([#*_-]+[[:space:]]*)?READY-FOR-REVIEW'; then
  echo "readiness: line-start READY-FOR-REVIEW marker"
  exit 0
fi

if printf '%s\n' "$BODY" | grep -qE 'Ready for gatekeeper cross-fork review[.!]?[[:space:]]*$'; then
  echo "readiness: cross-fork review phrase closing a line"
  exit 0
fi

echo "not a readiness comment (marker mentioned mid-text or absent) — no label (#1500)"
exit 1
