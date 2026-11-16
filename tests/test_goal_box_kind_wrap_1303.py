"""#1303 open line 2 -- the owner's own /goal stranded in a NARROW pane.

Claude Code wraps the input box at cols-4 with a 2-col indent and HARD-BREAKS
any token longer than a row. The /goal template carries a 61-char token
(`needs-answer`/.../`ops-wait`), so in any pane narrower than ~67 cols (the
phone webterm) the head-first box reconstruction joins the broken token with a
space. `_goal_box_kind` compared only clean-whitespace strings and read our own
box as `other`: the attempt-cap cleanup left it untouched (`leftover
not-ours`) and ARM-CONFIRM-FAIL logged `box=other` (gk 13.11. 11:17Z).
"""
import json
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_marker_transcript,
)

_TEXT = goal_registry.render_goal_line("full", None)
_TOKEN = max(_TEXT.split(" "), key=len)
_AT = _TEXT.index(_TOKEN)
# a stranded own leftover with its `/goal ` head gone (a scrolled or partly
# undone box), spanning the hard-broken token.
_OWN_MID = " ".join(_TEXT[_AT - 150:_AT + len(_TOKEN) + 150].split(" ")[1:-1])
_FOREIGN = ("toto je moja vlastna dlha sprava pre kolegu ktoru pisem uz "
            "dvadsat minut a rozhodne nechcem aby mi ju nejaky watchdog "
            "automaticky zmazal lebo to je moj cisto osobny text a nie goal")


def _cc_rows(buf, width):
    """CC's box rows: greedy word wrap, `❯`+NBSP head, 2-col indent, and a
    token longer than a row HARD-BROKEN across rows."""
    rows, cur, prefix = [], "", "❯\xa0"
    for word in buf.split(" "):
        cand = (cur + " " + word) if cur else word
        if len(prefix) + len(cand) <= width:
            cur = cand
            continue
        if cur:
            rows.append(prefix + cur)
            prefix = "  "
        cur = word
        while len(prefix) + len(cur) > width:
            n = width - len(prefix)
            rows.append(prefix + cur[:n])
            cur, prefix = cur[n:], "  "
    rows.append(prefix + cur)
    return rows


def _capture(rows):
    return "\n".join(["● Hotovo.", "", "─" * 40] + rows
                     + ["─" * 40, "  ctx ███░  caveman:lite"]) + "\n"


def _window_with_long_token(rows, n):
    """The last `n`-row window that still shows the hard-broken token, the
    head row re-glyphed the way a scrolled CC box renders it."""
    idx = max(i for i, r in enumerate(rows) if "`needs-decision`" in r
              or "needs-acceptance" in r)
    end = min(len(rows), idx + n // 2 + 1)
    win = rows[max(0, end - n):end]
    win[0] = "❯\xa0" + win[0].lstrip("❯ \xa0")
    return win


class GoalBoxKindHardWrap(unittest.TestCase):
    WIDTH = 40

    def test_long_token_is_hard_broken_at_this_width(self):
        # guard the fixture: the case is only meaningful while the template
        # really carries a token longer than a 40-col row.
        self.assertTrue(any(len(w) > self.WIDTH for w in _TEXT.split(" ")))

    def test_full_hard_wrapped_own_box_is_ours(self):
        cap = _capture(_cc_rows(_TEXT, self.WIDTH))
        self.assertEqual(goal._goal_box_kind(cap, _TEXT), "ours")

    def test_scrolled_hard_wrapped_own_box_is_ours(self):
        win = _window_with_long_token(_cc_rows(_TEXT, self.WIDTH), 10)
        cap = _capture(win)
        self.assertEqual(goal._goal_box_kind(cap, _TEXT), "ours")

    def test_headless_own_leftover_is_ours(self):
        cap = _capture(_cc_rows(_OWN_MID, self.WIDTH))
        self.assertEqual(goal._goal_box_kind(cap, _TEXT), "ours")

    def test_foreign_draft_stays_other(self):
        cap = _capture(_cc_rows(_FOREIGN, self.WIDTH))
        self.assertEqual(goal._goal_box_kind(cap, _TEXT), "other")

    def test_short_template_words_stay_other(self):
        # a short human draft that shares a few template words (< the 80-char
        # floor) is never proof of ownership.
        short = " ".join(_TEXT.split()[10:14])
        cap = _capture(_cc_rows(short, self.WIDTH))
        self.assertEqual(goal._goal_box_kind(cap, _TEXT), "other")


class _HardWrapFake(DeliverGoalFakeTmux):
    """The shared fake, rendering its box the hard-breaking CC way."""

    def _render_wrapped(self):
        rows = _cc_rows(self.box, self.wrap_width)
        if self.visible_rows and len(rows) > self.visible_rows:
            rows = rows[-self.visible_rows:]
            rows[0] = "❯\xa0" + rows[0].lstrip("❯ \xa0")
        return _capture(rows)


class CapDropHardWrap(unittest.TestCase):
    CWD = "/home/newlevel/devel/capdrop1303"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)

    def _sweep_at_cap(self, sid, box):
        _write_marker_transcript(self.proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, _TEXT, "full", now=1000,
                                 path=self.reqp, origin="self-callback")
        d = goal.load_goal_requests(self.reqp)
        d[sid]["dl_fails"] = goal.GOAL_DELIVERY_ATTEMPT_CAP
        Path(self.reqp).write_text(json.dumps(d))
        tmux = _HardWrapFake([("%9", "claude", self.CWD, "111")],
                             GOAL_IDLE_CAP, model_type=True, initial_box=box,
                             wrap_width=40)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=self.proj,
                                   requests_path=self.reqp,
                                   state={"janitor_watch": {"%9": 2000}},
                                   send_fn=lambda msg, **k: None,
                                   sleep_fn=lambda *a, **k: None)
        return tmux, logs

    def test_cap_drop_clears_own_hard_wrapped_goal(self):
        self.assertFalse(_OWN_MID.startswith("/goal"))
        tmux, logs = self._sweep_at_cap("sess-1303-wrap", _OWN_MID)
        self.assertFalse(any("leftover not-ours" in ln for ln in logs), logs)
        self.assertTrue(any("leftover=cleared" in ln for ln in logs), logs)
        self.assertEqual(tmux.box, "")

    def test_cap_drop_leaves_foreign_draft_untouched(self):
        tmux, logs = self._sweep_at_cap("sess-1303-foreign", _FOREIGN)
        self.assertEqual(tmux.box, _FOREIGN)
        self.assertTrue(any("leftover=not-ours" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
