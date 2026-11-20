"""#1381 -- Claude Code's COLLAPSED input box, read for the `/goal` arm only.

Claude Code 2.1.327 caps the input box at about half the pane (20 rows at the
fleet pin 176x50 with the managed 2-row footer, measured on a private tmux
server) and draws a longer buffer COLLAPSED: the box's top border becomes
`─── ↑ N more ───`, only the LAST rows are shown, and the `❯` head row is not
drawn at all. The shared box readers (`pane_classify._input_box_rows_raw`,
`goal_scan.pane_goal_armed`) find the box by its `❯` row, so to them a collapsed
box is "no box": `_classify_boundary` busy, `_input_line_text` None, armed
None. That stays right for every job that would TYPE into a box (a draft it
cannot read whole is left alone). It was wrong for the `/goal` arm, the one
payload long enough to collapse at the fleet geometry: a perfect type read as
HOLD (no Enter) and the stranded text was never submitted (dasio/dcontroller,
20.11.2026).

So this leaf reads the collapsed shape and answers "are the visible rows the
END of this exact text?"; only the goal-arm path asks. Every shared primitive is
read as `watchdog.<name>` at call time (the leaf idiom), so existing seams hold.
"""
import re

import watchdog

_TOP_RX = re.compile(r"^─{2,}\s*↑\s*(\d+)\s+more\b")
_MAX_ROWS = 200          # a box is at most about half a pane: bound the walk


def span(lines):
    """`(top, bottom)` indexes into the STRIPPED `lines` of a collapsed box at
    the pane's chrome boundary, else None. The bottom border is the last strict
    separator with only chrome below it (no `❯` row, no `esc to interrupt`: the
    rule that keeps `_input_box_rows_raw` off a QUOTED box); walking up from it,
    the first `─ ↑ N more ─` row is the top. A strict separator or a `❯` row met
    first is an ordinary box (or none): None."""
    seps = [i for i, ln in enumerate(lines) if watchdog._is_separator_line(ln)]
    if not seps:
        return None
    bot = seps[-1]
    if any(not watchdog._is_bottom_chrome(ln)
           and (ln.startswith("❯") or "esc to interrupt" in ln)
           for ln in lines[bot + 1:]):
        return None
    for top in range(bot - 1, max(-1, bot - 1 - _MAX_ROWS), -1):
        if _TOP_RX.match(lines[top]):
            return (top, bot)
        if watchdog._is_separator_line(lines[top]) or lines[top].startswith("❯"):
            return None
    return None


def rows(captured):
    """`(hidden, visible_rows)` of a collapsed input box, else None."""
    lines = [ln.strip() for ln in (captured or "").splitlines()]
    lines = [ln for ln in lines if ln]
    got = span(lines)
    if got is None or got[1] - got[0] < 2:
        return None
    top, bot = got
    return int(_TOP_RX.match(lines[top]).group(1)), lines[top + 1:bot]


def _ends_with(got, text):
    from watchdog import send_outcome
    if not got or not text:
        return False
    hidden, body = got
    vis = sum(len(r) for r in body)
    return (hidden >= 1 and watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR <= vis < len(text)
            and send_outcome._rows_end_with(body, text))


def own_tail(captured, text):
    """True when the box is collapsed and its visible rows are, row-exact, the
    END of `text`: only a wrap break may sit between two rows and a dropped byte
    inside a row fails (`send_outcome._rows_end_with`, #1157). The rows must hold
    at least `GOAL_ARM_LEFTOVER_MIN_SUBSTR` chars and fewer than `text` (a
    collapsed box hides at least one row)."""
    return _ends_with(rows(captured), text)


def box_kind(captured, text):
    """`"ours"` / `"other"` for a collapsed box, None when it is not collapsed:
    our own request text or any shipped /goal variant (the janitor's verbatim
    template proof, #1113) is ours, anything else is a foreign draft."""
    got = rows(captured)
    if got is None:
        return None
    from watchdog import stash
    own = _ends_with(got, text) or any(
        _ends_with(got, v) for v in stash._goal_template_variants())
    return "ours" if own else "other"


def _spinner_above(captured):
    """#1104: a running-turn spinner above the collapsed box's top border."""
    lines = [ln.strip() for ln in (captured or "").splitlines()]
    lines = [ln for ln in lines if ln]
    got = span(lines)
    from watchdog import send_outcome
    return bool(got) and send_outcome.spinner_above(lines, got[0])


def boundary(captured, text):
    """`deliver_goal`'s boundary read: `watchdog._classify_boundary` unchanged,
    except an idle collapsed box holding our own `text` reads as an input whose
    draft is its last row (the stranded-submit branch then takes it), and as busy
    under a running-turn spinner. Any other collapsed box stays busy: no key."""
    got = rows(captured)
    if _ends_with(got, text):
        return ("busy", None) if _spinner_above(captured) else ("input", got[1][-1])
    return watchdog._classify_boundary(captured)


def holds(captured, text):
    """The box still shows `text` (the not-yet-submitted check after an Enter):
    the shared tail contract (`_typed_landed`) or our own collapsed tail."""
    return bool(watchdog._typed_landed(text, watchdog._input_line_text(captured))
                or own_tail(captured, text))
