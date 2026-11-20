"""#1381 -- how many input-box rows a `/goal` line takes in Claude Code.

Claude Code 2.1.327 shows at most `GOAL_BOX_MAX_ROWS` rows of input at the fleet
tmux geometry and COLLAPSES a longer buffer (`─── ↑ N more ───`, `❯` head row
hidden) -- the render the dasio/dcontroller arms stranded on (20.11.2026).
`goal_registry.variant_check` holds every rendered variant to this budget, so a
clause that grows a goal past the box fails at commit time.

Measured on a private tmux server (`claude --bare`, fake API key, nothing
submitted) and reproduced row-exact by `box_rows` for 16 captures (176x50/40/
30/24 and 100x50; full, infra, review, quality, fork-no-merge, branch-merge,
two foreign drafts):
  * a row holds `cols - 4` display cells (2-col indent + right margin);
  * East Asian wide chars and emoji are 2 cells, and so is `→` (an Arrows-block
    char CC draws wide; the Math Operators `≤ ≥` count 2 here too, the safe
    direction for a budget);
  * words wrap greedily; a token longer than a row is hard-broken (#1303);
  * the box shows 20 rows at 176x50 with the managed 2-row footer (statusline +
    mode row); 19/14/9/6 rows at 50/40/30/24 with a 2-row statusline.
"""
import unicodedata

GOAL_BOX_COLS = 176      # cli_tmux_provisioning.TMUX_DEFAULT_SIZE width (fleet pin)
GOAL_BOX_MAX_ROWS = 20   # CC 2.1.327 at 176x50 with the managed 2-row footer

# The variants that already exceed the box, pinned at their measured rows: a
# DOWN-ONLY ratchet. Shortening them without dropping an operative clause was
# not possible (#1381 decision note), so growth fails here and a shorter text
# must lower its pin (`test_goal_box_rows_1381` fails on a stale pin).
OVER_BUDGET_ROWS = {("full", None): 22, ("full", "infra"): 23,
                    ("full", "review"): 22, ("full", "quality"): 21}


def cells(s):
    """Display cells of `s` as CC's input box counts them."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "‍︎️":
            continue
        wide = (unicodedata.east_asian_width(ch) in ("W", "F")
                or 0x2190 <= ord(ch) <= 0x22FF)
        n += 2 if wide else 1
    return n


def box_rows(text, cols=GOAL_BOX_COLS):
    """The input-box rows `text` wraps to in a `cols`-wide pane."""
    width = cols - 4
    out, cur = [], ""
    for word in text.split(" "):
        cand = (cur + " " + word) if cur else word
        if cells(cand) <= width:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = word
        while cells(cur) > width:
            k, used = 0, 0
            while k < len(cur) and used + cells(cur[k]) <= width:
                used += cells(cur[k])
                k += 1
            out.append(cur[:k])
            cur = cur[k:]
    out.append(cur)
    return out


def row_errors(authority, role, line):
    """`variant_check`'s box-budget leg: [] or one error naming the overflow."""
    n = len(box_rows(line))
    cap = OVER_BUDGET_ROWS.get((authority, role), GOAL_BOX_MAX_ROWS)
    if n <= cap:
        return []
    return ["%s/%s collapses the input box: %d rows > %d at %d cols (#1381)"
            % (authority, role, n, cap, GOAL_BOX_COLS)]
