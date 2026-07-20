"""Watchdog job 9 — /goal auto-arm (user directive 2026-07-20: 'dost mi vadi
ze musim pracne vsade chodit a zadavat goal — malo by sa to samo').

/autopilot and /process-subdev end by PRINTING the /goal template and asking
the user to paste it — the ONE manual step left in every stream. The watchdog
now performs the paste itself: an IDLE pane whose tail asks to paste a /goal
(the arm question) and carries a printed `/goal ` line gets that exact line
typed + submitted. Safety gates: bare empty prompt only (never over user
text), never when a goal is already armed (`◎ /goal` in the statusline),
never into a busy pane, one arm per pane per window (dedup)."""

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
import statusbar
import json as _json


def seed_repo_cache(home, root, name):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(_json.dumps(
        {"open": 1, "name": name, "root": root, "ts": int(time.time())}))

GOAL_LINE = ("/goal STOP CONDITIONS — the loop is DONE ... (B) SLICE DONE ... "
             "REVIEW-WATCH ... never park silently ...")

ARM_PANE = ("● autopilot · merge=auto · authority=branch-merge · 7 ticketov\n"
            + GOAL_LINE + "\n"
            "**Otázka — projekt odoo-erp (Money→Odoo):** autopilot je pripravený.\n"
            "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám\n"
            "• Nič nevkladaj — autopilot sa nespustí\n"
            "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
            "❯ \n  ctx ███░  caveman\n")

ARMED_PANE = ARM_PANE.replace("  ctx ███░  caveman",
                              "  ctx ███░  caveman  ◎ /goal active (1m)")
BUSY_PANE = ARM_PANE.replace("❯ \n", "✳ Baking… (2m · esc to interrupt)\n❯ \n")
USER_TEXT_PANE = ARM_PANE.replace("❯ \n", "❯ rozpisany draft\n")
NO_QUESTION_PANE = ("● Bežná odpoveď bez arm otázky.\n❯ \n  ctx ███░\n")


class FakeTmux:
    def __init__(self, captured):
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "%1\tclaude\t/home/x/devel/demo"
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


def go(captured, state=None, now=None):
    tmux = FakeTmux(captured)
    logs = wd.goal_autoarm(now or time.time(), tmux, state if state is not None
                           else {})
    return tmux, logs


class TestGoalAutoarm(unittest.TestCase):
    def test_arm_question_gets_the_goal_typed(self):
        tmux, logs = go(ARM_PANE)
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertTrue(typed[0].startswith("/goal STOP CONDITIONS"), typed[0])
        self.assertIn("REVIEW-WATCH", typed[0])
        self.assertTrue(any("goal-autoarm" in ln for ln in logs), logs)

    def test_rearm_question_arms_even_with_stale_goal_indicator(self):
        # gk incident 2026-07-20: a resolved-blocked /goal cycle re-prints the
        # arm question while the OLD ◎ /goal indicator is still lit — the
        # indicator alone must not block (typing /goal safely replaces the old
        # one); the arm question at the tail IS the session asking for it.
        tmux, _ = go(ARMED_PANE)
        self.assertTrue(tmux.typed())

    def test_busy_pane_is_skipped(self):
        tmux, _ = go(BUSY_PANE)
        self.assertFalse(tmux.typed())

    def test_user_typed_text_is_never_overwritten(self):
        tmux, _ = go(USER_TEXT_PANE)
        self.assertFalse(tmux.typed())

    def test_no_arm_question_no_typing(self):
        tmux, _ = go(NO_QUESTION_PANE)
        self.assertFalse(tmux.typed())

    def test_dedup_one_arm_per_window(self):
        state = {}
        now = time.time()
        t1, _ = go(ARM_PANE, state, now)
        self.assertTrue(t1.typed())
        t2, _ = go(ARM_PANE, state, now + 60)
        self.assertFalse(t2.typed(), "re-arm within the window must be deduped")

    def test_rearm_after_window_passes(self):
        state = {}
        now = time.time()
        go(ARM_PANE, state, now)
        t2, _ = go(ARM_PANE, state, now + wd.GOAL_ARM_WINDOW_S + 5)
        self.assertTrue(t2.typed())


if __name__ == "__main__":
    unittest.main()


class TestScrollbackNeverArms(unittest.TestCase):
    def test_stale_scrollback_goal_is_ignored(self):
        # gk incident 2026-07-20: a FRESH claude session started in a pane
        # whose tmux SCROLLBACK still held the dead session's arm question +
        # /goal line — job 9 armed the stale (wrong) goal into the new session.
        # The arm question + goal must come from the VISIBLE viewport only
        # (capture WITHOUT -S); scrollback history never arms anything.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo")

            class SplitTmux(FakeTmux):
                def __init__(self):
                    super().__init__("")

                def __call__(self, argv, timeout=8):
                    j = " ".join(argv)
                    self.sent.append(argv)
                    if "list-panes" in j:
                        return "%1\tclaude\t" + root
                    if "capture-pane" in j:
                        # viewport capture (no -S) = fresh boot screen;
                        # only a -S history capture would show the old goal
                        if "-S" in j:
                            return ARM_PANE          # stale history
                        return ("✻ Welcome back!\n❯ \n  ctx ░░░\n")
                    if "display" in j:
                        return "0"
                    return ""
            tmux = SplitTmux()
            wd.goal_autoarm(time.time(), tmux, {})
            self.assertFalse(tmux.typed(),
                             "scrollback content must never arm a fresh session")


class TestWrappedGoalUsesTranscript(unittest.TestCase):
    """2026-07-20 gk incident: the /autopilot-master goal RENDERS hard-wrapped
    in the pane (a code block re-flowed by the CC renderer), so the viewport
    regex captured only the FIRST visual line — 166 of 3100 chars got armed
    and the evaluator lost the release/window/depth conditions. The full goal
    must come from the session TRANSCRIPT (exact bytes); the viewport fragment
    is trusted only when it is provably unwrapped."""

    FULL_GOAL = ("/goal MASTER LOOP — DONE only when ALL hold: (1) `gh issue "
                 "list --state open` shows ZERO ... LANE 1 REVIEW ... LANE 4 "
                 "QUESTIONS ... airuleset:release-window ... depth NEVER "
                 "degrades ... FOREGROUND sleep-poll ... two real attempts.")
    FRAG = "/goal MASTER LOOP — DONE only when ALL hold: (1) `gh issue"

    WRAPPED_PANE = (
        "● /autopilot-master — board vyššie.\n"
        + FRAG + "\n"
        "  list --state open` shows ZERO ... LANE 1 REVIEW ... continuation\n"
        "**Otázka — projekt odoo-erp (ERP):** master je pripravený.\n"
        "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám\n"
        "❓ NEEDS YOU: vlož /goal riadok vyššie a master loop sa rozbehne\n"
        "❯ \n  ctx ███░  caveman\n")

    def _projects(self, with_goal=True):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / wd.encode_project_dir("/home/x/devel/demo")
        d.mkdir(parents=True)
        if with_goal:
            entry = {"type": "assistant", "message": {"content": [
                {"type": "text",
                 "text": "Report.\n\n```\n" + self.FULL_GOAL + "\n```\n"}]}}
            (d / "sess.jsonl").write_text(_json.dumps(entry) + "\n")
        return tmp.name

    def test_wrapped_goal_arms_full_transcript_bytes(self):
        tmux = FakeTmux(self.WRAPPED_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=self._projects())
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertEqual(typed[0], self.FULL_GOAL)
        self.assertIn("depth NEVER degrades", typed[0])

    def test_wrapped_goal_without_transcript_never_arms_truncated(self):
        tmux = FakeTmux(self.WRAPPED_PANE)
        logs = wd.goal_autoarm(time.time(), tmux, {},
                               projects_dir=self._projects(with_goal=False))
        self.assertFalse(tmux.typed(),
                         "a truncated fragment must never be armed")
        self.assertTrue(any("wrap" in ln.lower() for ln in logs), logs)

    def test_transcript_goal_preferred_even_for_unwrapped_viewport(self):
        # exact transcript bytes always beat the rendered viewport when present
        pane = (self.FRAG + "\n"
                "**Otázka — projekt x:** pripravený.\n"
                "• Vlož /goal riadok vyššie (odporúčam)\n"
                "❓ NEEDS YOU: vlož /goal riadok vyššie\n"
                "❯ \n  ctx ███░\n")
        tmux = FakeTmux(pane)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=self._projects())
        self.assertEqual(tmux.typed()[0], self.FULL_GOAL)
