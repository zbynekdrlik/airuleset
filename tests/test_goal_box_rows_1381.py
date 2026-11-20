"""#1381 -- the `/goal` length budget: a variant must fit Claude Code's input box.

Claude Code 2.1.327 shows 20 input rows at the fleet tmux pin 176x50 and
COLLAPSES a taller buffer (`─── ↑ N more ───`, head row hidden) -- the render
the dasio/dcontroller arms stranded on. `goal_box_rows.box_rows` models CC's wrap
and is locked here against REAL captures (`tests/fixtures/collapsed_box_1381/`);
`goal_registry.variant_check` then fails any variant that wraps past the box,
except the four full-authority variants pinned at their measured rows (a
down-only ratchet: growth fails, a shorter text must lower its pin).
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_tmux_provisioning  # noqa: E402
import goal_registry  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "collapsed_box_1381"
CAPTURES = {                       # fixture -> pane width it was captured at
    "own_full_176x50": 176, "own_review_100x50": 100, "own_quality_176x50": 176,
    "fits_fnm_infra_176x50": 176, "foreign_176x50": 176, "fits_foreign_176x50": 176,
}


def _gb():
    import goal_box_rows
    return goal_box_rows


def _real_rows(cap):
    """(visible rows, hidden count) of the bottom input box in a real capture."""
    lines = cap.splitlines()
    bot = max(i for i, ln in enumerate(lines) if set(ln.strip()) == {"─"})
    top = bot - 1
    while not lines[top].strip().startswith("─"):
        top -= 1
    rows = [ln.strip() for ln in lines[top + 1:bot]]
    label = lines[top].strip()
    hidden = int(label.split("↑")[1].split()[0]) if "↑" in label else 0
    return [r.lstrip("❯").strip() for r in rows], hidden


class BoxRowsMatchesRealClaudeCode(unittest.TestCase):
    def test_box_rows_reproduces_every_real_capture_row_exact(self):
        for name, cols in CAPTURES.items():
            cap = (FIX / (name + ".txt")).read_text(encoding="utf-8")
            text = (FIX / (name + ".payload")).read_text(encoding="utf-8")
            real, hidden = _real_rows(cap)
            model = [r.strip() for r in _gb().box_rows(text, cols)]
            with self.subTest(name=name):
                self.assertEqual(len(model), len(real) + hidden)
                self.assertEqual(model[-len(real):], real)

    def test_the_captures_show_the_cap_and_the_collapse(self):
        rows = {n: _real_rows((FIX / (n + ".txt")).read_text(encoding="utf-8"))
                for n in CAPTURES}
        self.assertEqual(rows["fits_fnm_infra_176x50"], (rows["fits_fnm_infra_176x50"][0], 0))
        self.assertEqual(len(rows["fits_fnm_infra_176x50"][0]), _gb().GOAL_BOX_MAX_ROWS)
        self.assertEqual(rows["own_quality_176x50"][1], 1)     # 21 rows = 20 + `↑ 1 more`
        self.assertEqual(rows["own_full_176x50"][1], 2)        # the incident's `↑ 2 more`

    def test_display_cells(self):
        cells = _gb().cells
        self.assertEqual([cells(c) for c in "a—é✅❓🏁→"], [1, 1, 1, 2, 2, 2, 2])


class EveryVariantFitsTheBox(unittest.TestCase):
    def test_cols_are_the_fleet_tmux_pin(self):
        self.assertEqual(_gb().GOAL_BOX_COLS,
                         int(cli_tmux_provisioning.TMUX_DEFAULT_SIZE.split("x")[0]))

    def test_no_variant_collapses_beyond_its_budget(self):
        self.assertEqual([e for e in goal_registry.variant_check() if "input box" in e], [])

    def test_unpinned_variants_fit_and_pins_are_exact_and_over_budget(self):
        gb = _gb()
        for authority, role in goal_registry.variant_specs():
            n = len(gb.box_rows(goal_registry.render_goal_line(authority, role)))
            pin = gb.OVER_BUDGET_ROWS.get((authority, role))
            with self.subTest(variant=(authority, role)):
                if pin is None:
                    self.assertLessEqual(n, gb.GOAL_BOX_MAX_ROWS)
                else:   # down-only: a shorter text must lower (or delete) its pin
                    self.assertEqual(n, pin)
                    self.assertGreater(pin, gb.GOAL_BOX_MAX_ROWS)
        self.assertLessEqual(set(gb.OVER_BUDGET_ROWS), set(goal_registry.variant_specs()))

    def test_a_growing_clause_fails_variant_check(self):
        night = next(c for c in goal_registry.CLAUSES if c.id == "night")
        old = night.text
        night.text = old + " " + "word " * 140      # ~4 more rows at 176 cols
        try:
            errs = [e for e in goal_registry.variant_check() if "input box" in e]
        finally:
            night.text = old
        self.assertTrue(any(e.startswith("branch-merge/None") for e in errs), errs)
        self.assertTrue(any(e.startswith("full/None") for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
