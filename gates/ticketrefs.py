"""gates.ticketrefs -- the ticket numbers a dispatch prompt names.

Split out of gates/designdispatch.py (#1165) so the prompt-parsing concern has
its own module; `gates.designdispatch.issue_numbers` re-exports it unchanged.
STDLIB ONLY.

The fleet's real prompts on this controller write the ticket WITHOUT `#`
("Work airuleset issue 1061 …") because the lane-overlap dispatch hook refuses
`#N` mentions outside its receipt, so a `#N`-only extractor was VACUOUS — every
real dispatch parsed to [] and the gate fail-opened (the #1028
vacuous-classifier class; #1061 supervisor review-3). So parse `#N` AND bare
`issue N` / `issues N, M` / `issue #N` / `issue-N` / `issue: N`. Require the
`issue`/`#` PREFIX so a version string (0.1.326), a date (2026-09-17), a
commit hash, or an "items 1, 2, 3" run is NEVER mistaken for a ticket. Scoped
to the FIRST ticket-bearing LINE (the dispatch's lead), mirroring the sibling
block-dispatch-over-wdrain gate, so a folded / related "issue N" on a LATER
body line ("5b. folded from issue 1046") never triggers a false precondition
check on a ticket the worker is not working.

#1165: the number is 1-6 digits (it was 2-6, so a young repo's `issue 4` /
`#4` parsed to NO ticket and a correct dispatch was refused "names no
ticket"). The PREFIX is what disambiguates, never the width. `_TICKET_NUM`
also refuses a leading zero (no ticket 0 / 007) and a trailing `.<digit>`
(`issue 4.2`, `#1.5` read as versions), and `\\bissues?` refuses a word that
merely ends in "issue" (`tissue 5`). In a comma/`and` RUN, a 1-digit member
with no `#` after a SINGULAR `issue` is a count, not a ticket
(`issue 1061, 3 lanes`) -- see `issue_numbers`.
"""
import re

_TICKET_NUM = r"[1-9]\d{0,5}\b(?!\.\d)"
_TICKET_ANY_RE = re.compile(
    r"(?:\bissues?\s*[#:-]?\s*|#)" + _TICKET_NUM, re.IGNORECASE)
_HASH_RE = re.compile(r"#(" + _TICKET_NUM + r")")
_TICKET_RUN_RE = re.compile(
    r"\b(issues?)\s*[#:-]?\s*(" + _TICKET_NUM
    + r"(?:\s*(?:,|and)\s*#?" + _TICKET_NUM + r")*)", re.IGNORECASE)
_RUN_MEMBER_RE = re.compile(r"(#?)(" + _TICKET_NUM + r")")


def issue_numbers(prompt):
    """Ticket numbers named in the dispatch's LEAD line (`#N` and bare `issue N`),
    de-duped, first-seen order. Empty when no line names a ticket."""
    text = prompt or ""
    lead = None
    for line in text.splitlines():
        if _TICKET_ANY_RE.search(line):
            lead = line
            break
    if lead is None:
        return []
    found = []  # (position, number) so #N and issue-N keep left-to-right order
    for m in _HASH_RE.finditer(lead):
        found.append((m.start(), int(m.group(1))))
    for m in _TICKET_RUN_RE.finditer(lead):
        singular = m.group(1).lower() == "issue"
        base = m.start(2)
        for i, nm in enumerate(_RUN_MEMBER_RE.finditer(m.group(2))):
            num = nm.group(2)
            # #1165: `issue 1061, 3 lanes` -- a bare 1-digit follower of a
            # SINGULAR `issue` is a count; the plural (`issues 4, 7`) or an
            # explicit `#` (`issue 4, #7`) marks a real batch member. A >= 2-digit
            # follower keeps its pre-#1165 reading (never narrowed).
            if i and singular and not nm.group(1) and len(num) < 2:
                continue
            found.append((base + nm.start(2), int(num)))
    found.sort(key=lambda t: t[0])
    out, seen = [], set()
    for _, n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out
