"""#672 REWORK (owner ruling 2026-08-25): ONE canonical grid for EVERY tab.

Owner (verbatim): "textove rozlysenie kazdeho tmuxu by malo byt rovnake a
vyhovovat hlavne mne ktory mam notebook s najnizsim rozlysenim. david a marek
maju vacsie rozlysenie cize by mali dostavat moje tmux velkosti. ... nie je ani
jeden dovod aby boli tmuxi a windows v nich rozdielne, vsetky musia maximalne
vyhovovat mne!!!"

This REVERSES the original #672 per-tab stream grid (WEBTERM_STREAM_TERM_GRID
320x64): forcing a foreign-stream tab's browser xterm to a grid LARGER than the
owner's viewport made the font micro-tiny on the m1..m6 tabs (unusable). The new
design: every tab renders at the owner's ONE canonical grid
(`_webterm_term_grid()` = TMUX_DEFAULT_SIZE 176x50 + 1 status row = 176x51).

The original footer-crop on foreign-stream tabs is instead solved on the TMUX
side by the fleet-wide `window-size manual` + `default-size 176x50` pin
(cli_tmux_provisioning.apply_tmux_history_limit, on EVERY box), which pins every
window to the owner size regardless of any client -- so the owner's 176x51
`-f ignore-size` client shows every window whole (footer included). David/Marek
get the owner's size (a harmless cosmetic dark border), which the owner
explicitly wants -- this reverses the #648 "never degrade David" invariant by
owner decree. That tmux-pin is already LIVE on the owner box (dev1: `show-options
-g` -> `window-size manual`, every window 176x50); its cross-box convergence + an
isolated-tmux empirical proof are the deferred #685 / tmux-convergence follow-up.
This module locks the RENDER side (the m1..m6 micro-font unblock).
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_webterm as w  # noqa: E402


def _cfg_from_html(html):
    """The `const CFG = {...};` object the dashboard embeds (json.dumps is a
    single line; `\\u003c`-style escapes decode back cleanly via json.loads)."""
    m = re.search(r'(?m)^const CFG = (.+);\s*$', html)
    assert m, "CFG literal not found in rendered dashboard HTML"
    return json.loads(m.group(1))


# A minimal OWNER dashboard inventory: one owner box (dev1) + one foreign
# stream box (david1@subdev) -- the exact d1/m-tab case from the ticket.
_INV = [
    {"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
     "local": True, "host": None, "user": None},
    {"id": "david1-subdev", "label": "david1@subdev", "kind": "stream",
     "local": False, "host": "10.0.0.5", "user": "david1"},
]


def _render_cfg(human=None):
    return _cfg_from_html(
        w.render_dashboard_html(_INV, ttyd_base="/t", human=human))


class TestUniformGridInRender(unittest.TestCase):
    """Owner ruling 2026-08-25: EVERY tab renders at the ONE canonical owner
    grid; no per-tab override, no per-current-tab getter, no stream grid
    constant. RED->GREEN lock for the m1..m6 micro-font unblock."""

    def test_every_tab_carries_the_one_owner_grid(self):
        cfg = _render_cfg()
        canon = w._webterm_term_grid()
        # the CFG base grid IS the owner canonical grid ...
        self.assertEqual((cfg["term_cols"], cfg["term_rows"]), canon)
        # ... and NO tab (owner OR stream) carries a per-tab override.
        for t in cfg["sessions"]:
            self.assertNotIn(
                "tcols", t,
                "tab %r must not carry a per-tab grid override" % t["id"])
            self.assertNotIn(
                "trows", t,
                "tab %r must not carry a per-tab grid override" % t["id"])

    def test_prod_owner_path_is_also_uniform(self):
        # Production's OWNER dashboard renders via human="zbynek" ->
        # entries_for_tab_list + preserve_order=True, a DIFFERENT path than the
        # human=None default. Lock BOTH so a future change can't re-introduce a
        # per-tab grid on either. Both _INV ids are in WEBTERM_DASHBOARD_TABS.
        cfg = _render_cfg(human="zbynek")
        self.assertGreater(len(cfg["sessions"]), 0)   # not a vacuous pass
        for t in cfg["sessions"]:
            self.assertNotIn("tcols", t)
            self.assertNotIn("trows", t)

    def test_stream_grid_constant_is_gone(self):
        # The per-stream grid constant that squeezed 320 cols into the owner
        # viewport (micro fonts) must not exist any more.
        self.assertFalse(
            hasattr(w, "WEBTERM_STREAM_TERM_GRID"),
            "WEBTERM_STREAM_TERM_GRID must be removed (owner ruling 2026-08-25)")

    def test_no_per_current_tab_grid_getter(self):
        # The #672 getter over CFG.term_cols/term_rows (a per-tab defineProperty)
        # is removed -- CFG.term_cols/term_rows are one constant for every tab.
        html = w.render_dashboard_html(_INV, ttyd_base="/t")
        self.assertNotIn("Object.defineProperty(CFG, 'term_cols'", html)
        self.assertNotIn("Object.defineProperty(CFG, 'term_rows'", html)


if __name__ == "__main__":
    unittest.main()
