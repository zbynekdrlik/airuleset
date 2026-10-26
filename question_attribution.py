"""question_attribution — which ticket ONE unanswered ❓ map entry counts for (#1218).

The question map (`~/.claude/discord-questions.json`, one entry per ❓ ping,
`notify.record_question`) is read by the footer `U`, `core-quals`/`slice-quals
--waiting`, the #1213 asked refs, the #948 supplement and the #539/#622 display
flags. A ping often names CONTEXT tickets ("po nasadení #8952") next to the
ticket its question lives on, and reading every `#N` as "the question is about
#N" made one question count once per context ticket (odoo-erp gk 7./8.10.2026:
U=2 for one question). So ONE entry is at most ONE U member:

1. an explicit tracking marker (`sledované na #N` and its inflections,
   `tracked on #N`, `tracking #N`, any case, starting a word; the LAST one
   wins) → ticket N only;
2. else every referenced ticket in `cover` — the tickets that are U members by
   their OWN labels on this box (`cli_ticket_route.question_cover`) — covers the
   entry: it adds no ping, those tickets count by their own labels;
3. else an entry naming exactly ONE ticket that this box COUNTS (one of the
   cover's `rows`) is that ticket's question (the #1213 asked case, odoo-erp
   8715: it leaves I for U);
4. else the entry counts ONCE as a ticketless ping, never as its context
   tickets — nor vanishes behind a ticket nothing counts.

`cover=None` means the label state is unknown (no rows read: a legacy footer
cache, a direct call): rule 2 then treats every reference as covering — the
pre-#1218 #512 dedup — so a caller without rows keeps the old numbers. A cover
without `rows` (a plain set) applies rule 3 to any sole ref.

The marker sits on a question's LAST line while the map caps its texts, so
`notify.record_question` stores `tracked` from the full text; `entry_tracked`
reads that field first. Pure, stdlib only, no I/O: the readers that scope and
load the map live in `statusbar`; the watchdog's primary-ticket readers use
`entry_tracked` / `tracked_ticket`.
"""
import re

TRACKED, COVERED, SOLE, TICKETLESS = "tracked", "covered", "sole", "ticketless"

TICKET_REF_RE = re.compile(r"#(\d+)")
TRACKING_RE = re.compile(
    r"(?<!\w)(?:sledovan\w*\s+na|tracked\s+on|tracking)\s*#(\d+)", re.IGNORECASE)


class QuestionCover(frozenset):
    """The label-U `cover` of a box plus its counted row numbers (`rows`, None
    = unknown) — rules 2 and 3 in one value, so every reader passes ONE thing
    (`cli_ticket_route.question_cover`)."""

    def __new__(cls, label_u=(), rows=None):
        obj = super().__new__(cls, label_u)
        obj.rows = None if rows is None else frozenset(rows)
        return obj


def _text(entry):
    return "%s %s" % (entry.get("block") or "", entry.get("question") or "")


def tracked_ticket(text):
    """The ticket an explicit tracking marker in `text` names (the LAST
    marker), or None."""
    marks = TRACKING_RE.findall(str(text or ""))
    return int(marks[-1]) if marks else None


def entry_tracked(entry, text=None):
    """The tracked ticket of a map `entry`: its stored `tracked` field (taken
    from the untruncated question), else a marker in `text` (default: its
    stored texts), else None."""
    stored = entry.get("tracked") if isinstance(entry, dict) else None
    if isinstance(stored, int) and not isinstance(stored, bool):
        return stored
    return tracked_ticket(_text(entry) if text is None else text)


def attribute(entry, cover=None):
    """`(kind, tickets)` for one question-map `entry` (a dict with `block` and/or
    `question` text): `(TRACKED, {N})`, `(COVERED, {the label-U refs})`,
    `(SOLE, {N})` or `(TICKETLESS, ∅)` — the order in the module docstring.
    `tickets` is a frozenset of ints; a COVERED entry names every label-U
    ticket it mentions (one family acceptance draft delivers each member,
    #755)."""
    text = _text(entry)
    tracked = entry_tracked(entry, text)
    if tracked is not None:
        return TRACKED, frozenset({tracked})
    refs = {int(n) for n in TICKET_REF_RE.findall(text)}
    hit = refs if cover is None else refs & set(cover)
    if hit:
        return COVERED, frozenset(hit)
    rows = getattr(cover, "rows", None)
    if len(refs) == 1 and (rows is None or refs <= rows):
        return SOLE, frozenset(refs)
    return TICKETLESS, frozenset()
