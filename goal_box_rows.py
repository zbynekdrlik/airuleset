"""#1381 -- how many input-box rows a `/goal` line takes in Claude Code.

Claude Code 2.1.327 shows at most `GOAL_BOX_MAX_ROWS` rows of input at the fleet
tmux geometry and COLLAPSES a longer buffer (`─── ↑ N more ───`, `❯` head row
hidden) -- the render the dasio/dcontroller arms stranded on (20.11.2026).
`goal_registry.variant_check` holds every rendered variant to this budget, so a
clause that grows a goal past the box fails at commit time.

Measured on private tmux servers (`claude --bare`, fake API key, nothing
submitted). `tests/test_goal_box_rows_1381.py` locks `box_rows` against the
committed real renders: every row of uncollapsed boxes (the 8 shipped variants
at 176 cols plus width / wrap probes) and the visible rows + `↑ N` count of
collapsed ones. The rules those renders show:
  * CC wraps like wrap-ansi with `trim: false`: a row holds `cols - 4` cells,
    and a row that ends exactly full carries the next row's leading space, so
    that row holds one cell less;
  * a cell count is the East-Asian width (W/F = 2, everything else = 1: `→`,
    `≤`, `—`, `é` are 1) and a VS16 emoji (`⚠️`) is 2;
  * a token longer than a row is hard-broken, starting on the current row when
    that saves a row (#1303);
  * the box shows at most `(height - footer - 8) // 2` rows: 20 at 176x50 with
    the managed 2-row footer (statusline + mode row), 19/14/9/6 at 50/40/30/24
    rows with a 2-row statusline.
The budget is for the pinned 176x50 geometry; an unpinned pane (tmux < 3.5, a
phone webterm there) can collapse any variant and is handled by the
collapsed-box reader (`watchdog/collapsed_box.py`), not by this budget.
"""
import unicodedata

GOAL_BOX_COLS = 176      # cli_tmux_provisioning.TMUX_DEFAULT_SIZE width (fleet pin)
GOAL_BOX_ROWS = 50       # ... and its height
FOOTER_ROWS = 2          # the managed statusline + CC's mode row


def box_max_rows(height, footer_rows):
    """The input rows CC 2.1.327 shows before it collapses the box."""
    return (height - footer_rows - 8) // 2


GOAL_BOX_MAX_ROWS = box_max_rows(GOAL_BOX_ROWS, FOOTER_ROWS)   # 20

# The variants that already exceed the box, pinned at their measured rows: a
# DOWN-ONLY ratchet (the #1381 decision: no operative clause is dropped to fit;
# the collapsed-box reader makes a collapse harmless). Growth fails here; a
# shorter text must lower its pin, and the test freezes these values as maxima.
OVER_BUDGET_ROWS = {("full", None): 22, ("full", "infra"): 23,
                    ("full", "review"): 22, ("full", "quality"): 21}


def cells(s):
    """Display cells of `s` as CC's input box counts them."""
    n = 0
    for i, ch in enumerate(s):
        if unicodedata.combining(ch) or ch in "‍︎":
            continue
        if ch == "️":       # VS16: the narrow symbol before it becomes an emoji
            if i and unicodedata.east_asian_width(s[i - 1]) not in ("W", "F"):
                n += 1
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def box_rows(text, cols=GOAL_BOX_COLS):
    """The input-box rows `text` wraps to in a `cols`-wide pane (wrap-ansi with
    `trim: false, hard: true`, the model review B measured live)."""
    width = cols - 4
    rows = [""]
    for idx, word in enumerate(text.split(" ")):
        used = cells(rows[-1])
        if idx:
            if used >= width:        # a full row carries the separating space over
                rows.append("")
                used = 0
            rows[-1] += " "
            used += 1
        wl = cells(word)
        if wl > width:               # hard-break; start here when it saves a row
            if (wl - 1) // width < 1 + (wl - (width - used) - 1) // width:
                rows.append("")
            for ch in word:
                if cells(rows[-1]) + cells(ch) > width:
                    rows.append("")
                rows[-1] += ch
            continue
        if used + wl > width and used > 0 and wl > 0:
            rows.append("")
        rows[-1] += word
    return [r.strip() for r in rows]


def row_errors(authority, role, line):
    """`variant_check`'s box-budget leg: [] or one error naming the overflow."""
    n = len(box_rows(line))
    cap = OVER_BUDGET_ROWS.get((authority, role), GOAL_BOX_MAX_ROWS)
    if n <= cap:
        return []
    return ["%s/%s collapses the input box: %d rows > %d at %d cols (#1381)"
            % (authority, role, n, cap, GOAL_BOX_COLS)]
