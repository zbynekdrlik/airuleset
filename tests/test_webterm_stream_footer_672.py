"""#672: the owner's webterm client CROPS a foreign-stream tab's footer.

Root cause + full record: the #672 design comment on the issue, and the
stream-grid getter block in cli_webterm.py. In short: a foreign-stream
tab's tmux window is sized by the STREAM developer's OWN client (David's
305x57 -> window 305x56), NOT the owner's fixed 176x50. The owner's webterm
client, forced to the owner's 176x51 grid (fitFixedGrid) and attached with
`-f ignore-size`, is SMALLER than that window, so tmux gives it a
cursor-following CROP that clips everything below the cursor -- the CC
I/U/gk statusline footer + agent strip. The fix gives stream tabs their own
larger grid (WEBTERM_STREAM_TERM_GRID) so the owner client is >= the stream
window; `-f ignore-size` is KEPT so the stream developer's own window is
never resized (the #648 no-degradation invariant, mirrored).

tmux SAFETY: every tmux invocation runs on a throwaway per-test `-S` socket
with TMUX/TMUX_PANE stripped (tests/test_tmux_test_isolation_lock.py
enforces it), via the shared _IsolatedTmuxServer harness. Never attaches to
/ resizes any live session.
"""
import json
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_webterm as w  # noqa: E402

# Reuse the proven isolated-tmux + pty-client harness (own -S socket, env
# stripped, TIOCSWINSZ-pinned pty clients, wall-clock drain). #613's harness.
# Importing it also enforces the repo-wide "tmux MUST be on PATH" convention
# (it raises at import if tmux is absent) -- so this module never SKIPS on a
# missing tmux (test-strictness.md: fail, never skip), it simply cannot import.
from test_webterm_ctrlbw_darkening import (  # noqa: E402
    _IsolatedTmuxServer, _drain, _visible,
)


def _cfg_from_html(html):
    """The `const CFG = {...};` object the dashboard embeds (json.dumps is a
    single line; `\\u003c`-style escapes decode back cleanly via json.loads)."""
    m = re.search(r'(?m)^const CFG = (.+);\s*$', html)
    assert m, "CFG literal not found in rendered dashboard HTML"
    return json.loads(m.group(1))


# A minimal OWNER dashboard inventory: one owner box (dev1) + one foreign
# stream box (david1@subdev) -- the exact d1 case from the ticket.
_INV = [
    {"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
     "local": True, "host": None, "user": None},
    {"id": "david1-subdev", "label": "david1@subdev", "kind": "stream",
     "local": False, "host": "10.0.0.5", "user": "david1"},
]


def _render_cfg():
    return _cfg_from_html(w.render_dashboard_html(_INV, ttyd_base="/t"))


def _grid_for(cfg, tab_id):
    """(cols, rows) the render assigns to `tab_id`, derived from the ACTUAL
    render: pre-fix a stream tab carries no override and falls back to the
    global base; post-fix it carries the stream grid. So the SAME helper is
    RED pre-fix and GREEN post-fix -- it never hard-codes the expected grid."""
    tab = next(t for t in cfg["sessions"] if t["id"] == tab_id)
    return (tab.get("tcols", cfg["term_cols"]),
            tab.get("trows", cfg["term_rows"]))


class TestStreamTabGridInRender(unittest.TestCase):
    """The Python-side RED->GREEN lock: stream tabs get a grid large enough
    to contain the stream developer's window (David 305x57); owner tabs keep
    the fixed 176x51 (no dark-border regression on the owner's own boxes)."""

    def test_stream_tab_grid_covers_the_stream_window(self):
        cols, rows = _grid_for(_render_cfg(), "david1-subdev")
        # David works at 305x57 -> window 305x56; the owner's ignore-size
        # client must be >= that or tmux crops the footer.
        self.assertGreaterEqual(
            cols, 305, "stream-tab grid cols must cover David's 305-col terminal")
        self.assertGreaterEqual(
            rows, 57, "stream-tab grid rows must cover David's 57-row terminal")

    def test_owner_tab_keeps_the_fixed_owner_grid(self):
        # The owner's own boxes are pinned window-size manual + default-size
        # 176x50, and the browser must match EXACTLY (no dark unused region --
        # the whole #613 saga). A stream override must never leak onto them.
        self.assertEqual(_grid_for(_render_cfg(), "dev1"),
                         w._webterm_term_grid())

    def test_stream_override_is_the_named_constant(self):
        cols, rows = _grid_for(_render_cfg(), "david1-subdev")
        self.assertEqual((cols, rows), tuple(w.WEBTERM_STREAM_TERM_GRID))

    def test_per_current_tab_grid_getter_is_present(self):
        # Structural lock: CFG.term_cols/term_rows resolve to the CURRENT
        # tab's override so fitFixedGrid/fillFixedGrid (the churned injected-JS
        # region) read them UNCHANGED. Keep this in sync if the getter form
        # is refactored.
        html = w.render_dashboard_html(_INV, ttyd_base="/t")
        self.assertIn("Object.defineProperty(CFG, 'term_cols'", html)
        self.assertIn("Object.defineProperty(CFG, 'term_rows'", html)


class TestStreamCropFixture(unittest.TestCase):
    """Empirical mechanism lock on an isolated two-client tmux server: the
    owner grid CROPS the stream footer, the stream grid SHOWS it, and the
    stream developer's own window is NEVER resized (ignore-size). The owner
    client size is DERIVED FROM THE RENDER, so this whole test is RED on
    current code (stream tabs fall back to the small owner grid -> footer
    cropped) and GREEN once the fix lands."""

    # "David"'s window. Small enough to keep the test fast; the mechanism is
    # scale-free (owner client rows < window rows -> vertical crop).
    DW_COLS, DW_ROWS = 200, 60
    FOOTER = "ROW60"          # printed on the window's bottom row

    def _build(self):
        """A base session at David's size, unpinned (window-size latest --
        faithful to the subdev box, whose window follows David's own client),
        filled with numbered rows; cursor left on row 40 so the bottom rows
        (the 'footer') sit BELOW the cursor exactly like the CC statusline."""
        srv = _IsolatedTmuxServer()
        srv.tmux("-f", "/dev/null", "new-session", "-d", "-s", "base",
                 "-x", str(self.DW_COLS), "-y", str(self.DW_ROWS))
        srv.tmux("set-option", "-g", "window-size", "latest")
        srv.tmux("set-option", "-g", "default-size",
                 "%dx%d" % (self.DW_COLS, self.DW_ROWS))
        prog = ("clear; for i in $(seq 1 %d); do printf 'ROW%%02d----------\\n' "
                "$i; done; printf '\\033[40;1H'; printf 'CURSORHERE'; sleep 600"
                % (self.DW_ROWS,))
        srv.tmux("send-keys", "-t", "base", prog, "Enter")
        time.sleep(0.6)
        return srv

    def _footer_visible_to_owner(self, srv, ow_cols, ow_rows):
        """Attach David's authoritative (non-ignore-size) client, then the
        owner's `-f ignore-size` webterm client at (ow_cols x ow_rows).
        Returns (owner_sees_footer, window_size_str, footer_in_pane)."""
        srv.attach_client(
            ["tmux", "-S", srv.sock, "attach-session", "-t", "base"],
            rows=self.DW_ROWS + 1, cols=self.DW_COLS)
        time.sleep(0.5)
        ofd = srv.attach_client(
            ["tmux", "-S", srv.sock, "attach-session", "-t", "base",
             "-f", "ignore-size"],
            rows=ow_rows, cols=ow_cols)
        time.sleep(0.6)
        win = srv.tmux("display-message", "-t", "base", "-p",
                       "#{window_width}x#{window_height}").stdout.strip()
        rendered = _visible(_drain(ofd, 1.2))
        cap = srv.tmux("capture-pane", "-t", "base", "-p").stdout
        return (self.FOOTER in rendered), win, (self.FOOTER in cap)

    def test_owner_grid_crops_footer_stream_grid_shows_it_no_degradation(self):
        cfg = _render_cfg()
        owner_grid = _grid_for(cfg, "dev1")            # small fixed owner grid
        stream_grid = _grid_for(cfg, "david1-subdev")  # the render's stream grid
        expected_win = "%dx%d" % (self.DW_COLS, self.DW_ROWS)

        # (A) The small owner grid CROPS the footer (the bug) -- and David's
        #     window is untouched (ignore-size).
        srv = self._build()
        try:
            vis, win, cap = self._footer_visible_to_owner(srv, *owner_grid)
            self.assertTrue(cap, "footer must be IN the pane (capture-pane truth)")
            self.assertEqual(win, expected_win,
                             "ignore-size must keep David's window unchanged")
            self.assertFalse(
                vis, "owner grid %r must CROP the stream footer" % (owner_grid,))
        finally:
            srv.close()

        # (B) The render's stream grid SHOWS the footer, David still untouched.
        srv = self._build()
        try:
            vis, win, cap = self._footer_visible_to_owner(srv, *stream_grid)
            self.assertEqual(
                win, expected_win,
                "stream grid must NOT resize David's window (no degradation)")
            self.assertTrue(
                vis, "stream grid %r must SHOW the footer" % (stream_grid,))
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
