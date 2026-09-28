"""gates.ticketrefs -- the ticket numbers a dispatch prompt names.

Split out of gates/designdispatch.py (#1165) so the prompt-parsing concern has
its own module; `gates.designdispatch.issue_numbers` re-exports it unchanged.
STDLIB ONLY.

The fleet's real prompts on this controller write the ticket WITHOUT `#`
("Work airuleset issue 1061 …") because the lane-overlap dispatch hook refuses
`#N` mentions outside its receipt, so a `#N`-only extractor was VACUOUS — every
real dispatch parsed to [] and the gate fail-opened (the #1028
vacuous-classifier class; #1061 supervisor review-3). So parse `#N` AND bare
`issue N` / `issues N, M` / `issue #N` / `issue-N` / `issue: N` /
`issues #A #B`. Require the `issue`/`#` PREFIX so a version string (0.1.326),
a date (2026-09-17), a commit hash, or an "items 1, 2, 3" run is NEVER
mistaken for a ticket. Scoped to ONE lead line (below), mirroring the sibling
block-dispatch-over-wdrain gate, so a folded / related "issue N" on a LATER
body line ("5b. folded from issue 1046") never triggers a false precondition
check on a ticket the worker is not working.

#1165: the number is 1-6 digits (it was 2-6, so a young repo's `issue 4` /
`#4` parsed to NO ticket and a correct dispatch was refused "names no
ticket"). The PREFIX is what disambiguates, never the width: a follower in an
`issue` run is a ticket at any width (`issue 4, 7` -> [4, 7]; over-checking
blocks, which is the safe direction for this fail-closed gate). `_TICKET_NUM`
refuses a leading zero (no ticket 0 / 007) and a trailing `.<digit>`
(`issue 4.2`, `#1.5` read as versions); `\\bissues?` refuses `tissue 5`; `&#N`
is an HTML entity, never a ticket. A 1-digit `#N` OUTSIDE an `issue` run is
weak evidence (`step #1`, `bounce #2`, `PR #7`), so it never outranks a
stronger reference: it is read only on a lead line that has no `issue` run,
and a line whose ONLY references are such 1-digit `#N`s becomes the lead line
only when no line carries a stronger one. Every prompt that parsed before
#1165 therefore picks the same lead line.
"""
import re

_TICKET_NUM = r"[1-9]\d{0,5}\b(?!\.\d)"
_HASH_RE = re.compile(r"(?<!&)#(" + _TICKET_NUM + r")")
_TICKET_RUN_RE = re.compile(
    r"\bissues?\s*[#:-]?\s*(" + _TICKET_NUM
    + r"(?:\s*(?:,|and)\s*#?" + _TICKET_NUM + r"|\s+#" + _TICKET_NUM + r")*)",
    re.IGNORECASE)
_RUN_MEMBER_RE = re.compile(_TICKET_NUM)


def _line_refs(line):
    """(run_refs, hash_refs) of one line, each a list of (position, number).
    `run_refs` come from `issue`/`issues` runs; `hash_refs` are every `#N`."""
    run_refs = []
    for m in _TICKET_RUN_RE.finditer(line):
        base = m.start(1)
        for nm in _RUN_MEMBER_RE.finditer(m.group(1)):
            run_refs.append((base + nm.start(), int(nm.group())))
    hash_refs = [(m.start(1), int(m.group(1))) for m in _HASH_RE.finditer(line)]
    return run_refs, hash_refs


def _is_strong(run_refs, hash_refs):
    """A line carrying an `issue` run or a >= 2-digit `#N` (the pre-#1165 set)."""
    return bool(run_refs) or any(n >= 10 for _, n in hash_refs)


def issue_numbers(prompt):
    """Ticket numbers named in the dispatch's LEAD line (`#N` and bare `issue N`),
    de-duped, first-seen order. Empty when no line names a ticket. The lead is
    the first line with a strong reference, else the first line with any."""
    lead = weak = None
    for line in (prompt or "").splitlines():
        run_refs, hash_refs = _line_refs(line)
        if _is_strong(run_refs, hash_refs):
            lead = (run_refs, hash_refs)
            break
        if hash_refs and weak is None:
            weak = (run_refs, hash_refs)
    lead = lead or weak
    if lead is None:
        return []
    run_refs, hash_refs = lead
    found = list(run_refs)
    for pos, n in hash_refs:
        if n >= 10 or not run_refs:
            found.append((pos, n))
    found.sort(key=lambda t: t[0])
    out, seen = [], set()
    for _, n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out
