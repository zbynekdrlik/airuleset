"""question_attribution — which ticket ONE unanswered ❓ map entry counts for (#1218).

The question map (`~/.claude/discord-questions.json`, one entry per ❓ ping,
`notify.record_question`) is read by the footer `U`, `core-quals`/`slice-quals
--waiting`, the #1213 asked refs, the #948 supplement and the #539/#622 display
flags. A ping often names CONTEXT tickets ("po nasadení #8952") next to the
ticket its question lives on, and reading every `#N` as "the question is about
#N" made one question count once per context ticket (odoo-erp gk 7./8.10.2026:
U=2 for one question). So ONE entry is at most ONE U member:

1. an explicit tracking marker (`sledované na #N` and its inflections,
   `tracked on #N`, `tracking #N`, any case; the LAST one wins) → ticket N only;
2. else every referenced ticket in `cover` — the tickets that are U members by
   their OWN labels on this box (`cli_ticket_route.question_cover`) — covers the
   entry, which then adds nothing to U;
3. else the entry counts ONCE as a ticketless ping, never as its context tickets.

`cover=None` means the label state is unknown (no rows read: a legacy footer
cache, a direct call): rule 2 then treats every reference as covering — the
pre-#1218 #512 dedup — so a caller without rows keeps the old numbers.

Pure, stdlib only, no I/O: the readers that scope and load the map live in
`statusbar`.
"""
import re

TRACKED, COVERED, TICKETLESS = "tracked", "covered", "ticketless"

TICKET_REF_RE = re.compile(r"#(\d+)")
TRACKING_RE = re.compile(r"(?:sledovan\w*\s+na|tracked\s+on|tracking)\s*#(\d+)",
                         re.IGNORECASE)


def attribute(entry, cover=None):
    """`(kind, tickets)` for one question-map `entry` (a dict with `block` and/or
    `question` text): `(TRACKED, {N})`, `(COVERED, {the label-U refs})` or
    `(TICKETLESS, ∅)` — the order in the module docstring. `tickets` is a
    frozenset of ints; a COVERED entry names every label-U ticket it mentions
    (one family acceptance draft delivers each member, #755) yet adds none."""
    text = "%s %s" % (entry.get("block") or "", entry.get("question") or "")
    marks = TRACKING_RE.findall(text)
    if marks:
        return TRACKED, frozenset({int(marks[-1])})
    refs = {int(n) for n in TICKET_REF_RE.findall(text)}
    hit = refs if cover is None else refs & set(cover)
    if hit:
        return COVERED, frozenset(hit)
    return TICKETLESS, frozenset()
