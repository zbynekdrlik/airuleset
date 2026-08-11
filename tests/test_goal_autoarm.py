"""Watchdog job 9 — /goal auto-arm (user directive 2026-07-20: 'dost mi vadi
ze musim pracne vsade chodit a zadavat goal — malo by sa to samo').

/autopilot and /process-subdev end by PRINTING the /goal template and asking
the user to paste it — the ONE manual step left in every stream. The watchdog
now performs the paste itself: an IDLE pane whose tail asks to paste a /goal
(the arm question) and carries a printed `/goal ` line gets that exact line
typed + submitted. Safety gates: bare empty prompt only (never over user
text), never when a goal is already armed (`◎ /goal` in the statusline),
never into a busy pane, one arm per pane per window (dedup)."""

import os
import re
import sys
import time
import unittest
import unittest.mock as m
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
import statusbar
import airuleset
import json as _json

# #266 Defect 1: goal_autoarm's plain (bare-box) branch now calls
# `_send_goal_verified`, which bounded-polls (`time.sleep`) waiting for the
# type to render before it will press Enter. Every pre-existing test in
# this file drives the DEFAULT (non-model_stash) `FakeTmux`, whose static
# capture never reflects a typed keystroke -- so the real, unmocked
# primitive would exhaust its whole settle window (real sleeps) on EVERY
# such test. None of those tests assert on the verified OK/FAIL outcome
# (only that a `send-keys -l` happened at all, which still fires
# unconditionally before the verify poll), so patching `time.sleep`
# globally for this file's run is correct, not just convenient -- it
# changes no test's semantics, only how long a genuinely-never-verifying
# fake pane takes to give up.
_time_sleep_patcher = None


def setUpModule():
    global _time_sleep_patcher
    _time_sleep_patcher = m.patch("time.sleep", lambda s: None)
    _time_sleep_patcher.start()


def tearDownModule():
    if _time_sleep_patcher is not None:
        _time_sleep_patcher.stop()


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
    """Static capture by default. `model_stash=True` additionally models the
    input box and Claude Code's SINGLE-SLOT prompt stash (Ctrl+S), so the
    stash-around delivery path reacts to keystrokes the way a real pane does.

    A frozen capture makes every Ctrl+S look like a no-op, which made a pane
    holding a draft LOOK like it refused the arm — when in production the
    draft is parked and the goal is delivered around it (issue 35).

    `capture_seq` (optional): a SCRIPTED sequence of successive capture-pane
    replies, consumed one per call in order, falling back to `captured` once
    exhausted (mirrors `test_goal_rearm.py`'s own `FakeTmux.cap_seq`) — for
    proving a race between the loop's TOP-of-iteration capture and a LATER
    re-capture immediately before a send (#266 adversarial-review finding)."""

    def __init__(self, captured, model_stash=False, capture_seq=()):
        self.captured = captured
        self.sent = []
        self.model_stash = model_stash
        self.stash = None
        self.submitted = []
        self._box = self._box_of(captured)
        self._capture_seq = list(capture_seq)

    @staticmethod
    def _box_of(cap):
        for ln in cap.splitlines():
            if ln.strip().startswith("❯"):
                return ln.strip()[1:].strip()
        return ""

    def _render(self):
        out = []
        for ln in self.captured.splitlines():
            if ln.strip().startswith("❯"):
                out.append("❯\xa0" + self._box if self._box else "❯\xa0")
            elif ln.strip().startswith("ctx ") and self.stash is not None:
                out.append(ln + "  " + wd.STASH_MARKER)
            else:
                out.append(ln)
        return "\n".join(out) + "\n"

    def _key(self, k):
        if k == "C-s":
            if self._box and self.stash is None:
                self.stash, self._box = self._box, ""
            elif not self._box and self.stash is not None:
                self._box, self.stash = self.stash, None
        elif k == "Enter" and self._box:
            self.submitted.append(self._box)
            self._box = ""

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "%1\tclaude\t/home/x/devel/demo"
        if "capture-pane" in j:
            if self._capture_seq:
                return self._capture_seq.pop(0)
            return self._render() if self.model_stash else self.captured
        if "display" in j:
            return "0"
        if self.model_stash and argv[:2] == ["tmux", "send-keys"]:
            if "-l" in argv:
                self._box += argv[-1]
            else:
                for k in argv[4:]:
                    self._key(k)
        return ""

    def typed(self):
        # #322 — `_type_literal` sends a long payload as SEVERAL consecutive
        # `-l` calls (chunked) rather than one — join a consecutive RUN of
        # them into a single logical "typed" entry (mirrors
        # `test_goal_rearm.py`'s own `FakeTmux.typed()` fix) so every
        # existing `tmux.typed()[0] == FULL_GOAL`-style assertion keeps
        # working for both the short single-burst path and the long
        # chunked one.
        out = []
        buf = []
        for a in self.sent:
            if "-l" in a:
                buf.append(a[-1])
            elif buf:
                out.append("".join(buf))
                buf = []
        if buf:
            out.append("".join(buf))
        return out


def go(captured, state=None, now=None, model_stash=False, projects_dir=None,
      templates_path=None, dry_run=False, capture_seq=()):
    tmux = FakeTmux(captured, model_stash=model_stash, capture_seq=capture_seq)
    logs = wd.goal_autoarm(now or time.time(), tmux, state if state is not None
                           else {}, dry_run=dry_run, projects_dir=projects_dir,
                           templates_path=templates_path)
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

    def test_user_typed_text_is_stashed_around_never_overwritten(self):
        # The draft is PARKED (Ctrl+S), the goal is delivered into the box the
        # park emptied, and Claude Code auto-restores the draft when that turn
        # ends — issue 35's whole purpose. This used to assert that NOTHING was
        # typed, which only held because a frozen capture made the park
        # invisible to the delivery's own verify.
        tmux, _ = go(USER_TEXT_PANE, model_stash=True)
        self.assertTrue(tmux.typed(), tmux.sent)
        self.assertEqual(tmux.submitted, [GOAL_LINE], tmux.sent)
        self.assertEqual(tmux.stash, "rozpisany draft",
                         "the user's draft must stay parked, never overwritten")

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


class TestGoalAutoarmSweepBudget(unittest.TestCase):
    """#255 Fix 1: goal_autoarm's own per-pane loop previously had no
    wall-clock self-bound either -- the SAME gap class as bounce_backstop
    (job 8), closed the same way: an optional time_fn/sweep_deadline pair,
    checked strictly BETWEEN panes so a pane already being processed always
    finishes; only a NOT-YET-STARTED pane is deferred to the next sweep."""

    class MultiPaneTmux:
        def __init__(self, n, captured, clock):
            self.n = n
            self.captured = captured
            self.clock = clock
            self.sent = []

        def __call__(self, argv, timeout=8):
            j = " ".join(argv)
            self.sent.append(argv)
            if "list-panes" in j:
                return "\n".join(
                    "%%%d\tclaude\t/home/x/devel/demo%d" % (i, i)
                    for i in range(self.n))
            if "capture-pane" in j:
                self.clock[0] += 10   # each capture "costs" wall-clock time
                return self.captured
            if "display" in j:
                return "0"
            return ""

        def typed(self):
            return [a[-1] for a in self.sent if "-l" in a]

    def test_stops_before_a_new_pane_once_budget_exhausted(self):
        clock = [0.0]
        tmux = self.MultiPaneTmux(2, ARM_PANE, clock)

        def time_fn():
            return clock[0]

        logs = wd.goal_autoarm(time.time(), tmux, {},
                               time_fn=time_fn, sweep_deadline=5)
        self.assertEqual(len(tmux.typed()), 1, tmux.sent)
        self.assertTrue(any("goalarm-budget-exceeded" in ln for ln in logs), logs)

    def test_no_deadline_given_means_unbounded_as_before(self):
        clock = [0.0]
        tmux = self.MultiPaneTmux(2, ARM_PANE, clock)
        logs = wd.goal_autoarm(time.time(), tmux, {})
        self.assertEqual(len(tmux.typed()), 2, tmux.sent)
        self.assertFalse(any("goalarm-budget-exceeded" in ln for ln in logs), logs)


class TestAGoalTheUserClearedIsNotReArmed(unittest.TestCase):
    """#170 — job 9 decided purely from what is VISIBLE in the pane.

    A `/goal clear` does not wipe the screen: the arm question and the printed
    `/goal` line are still there, so the next sweep matched them again and
    typed the goal straight back in. The user reported it as goals "starting
    themselves where I had them switched off".

    The discriminator is in the transcript, not the viewport. Claude Code
    writes a `Goal cleared:` marker ONLY for an explicit clear — an achieved
    goal prints `✔ Goal achieved` to the screen and persists nothing (measured
    across 8329 local transcripts: 86 `Goal set:` markers against 32 genuine
    `Goal cleared:` ones, every one of the latter an explicit clear). So the
    LATEST goal marker being a clear means the user turned it off, and the
    session is left alone until they arm it again themselves.

    Deliberately asymmetric: re-enabling costs the user one paste, while
    re-arming a loop they stopped costs them tokens and control.
    """

    def _projects(self, cwd, marker_line):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        d = root / wd.encode_project_dir(cwd)
        d.mkdir(parents=True)
        (d / "sess-170.jsonl").write_text(marker_line + "\n", encoding="utf-8")
        return root

    @staticmethod
    def _marker(state, payload="STOP CONDITIONS — the loop is DONE ..."):
        return _json.dumps({
            "type": "system",
            "timestamp": "2026-07-29T09:55:51.000Z",
            "content": "<local-command-stdout>Goal %s: %s</local-command-stdout>"
                       % (state, payload),
        })

    def test_cleared_goal_is_left_alone(self):
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"))
        tmux = FakeTmux(ARM_PANE)
        logs = wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(tmux.typed(),
                         "a goal the user cleared must not be re-armed")
        self.assertTrue(any("cleared" in ln for ln in logs), logs)

    def test_an_armed_session_still_gets_re_armed(self):
        """The resolved-cycle case job 9 exists for — must keep working."""
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("set"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed())

    def test_no_transcript_behaves_exactly_as_before(self):
        """Fail-open: not provably cleared must never start blocking a pane
        that used to arm (a sudo-hosted stream reads no markers at all)."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=Path(tmp.name))
        self.assertTrue(tmux.typed())

    def test_re_arming_drops_the_bookkeeping(self):
        """The state file is long-lived; a key per cleared session would only
        ever grow. Once the session is armed again the entry goes."""
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"))
        state, now = {}, time.time()
        wd.goal_autoarm(now, FakeTmux(ARM_PANE), state, projects_dir=pd)
        self.assertIn("sess-170", state.get("goalarm_cleared", {}))

        tr = pd / wd.encode_project_dir(cwd) / "sess-170.jsonl"
        tr.write_text(self._marker("set") + "\n", encoding="utf-8")
        wd.goal_autoarm(now + wd.GOAL_ARM_WINDOW_S + 5, FakeTmux(ARM_PANE),
                        state, projects_dir=pd)
        self.assertNotIn("sess-170", state.get("goalarm_cleared", {}))

    def test_the_skip_is_logged_once_not_every_sweep(self):
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"))
        state, now = {}, time.time()
        n = []
        for i in range(3):
            tmux = FakeTmux(ARM_PANE)
            logs = wd.goal_autoarm(now + i * (wd.GOAL_ARM_WINDOW_S + 5),
                                   tmux, state, projects_dir=pd)
            n.append(sum(1 for ln in logs if "cleared" in ln))
        self.assertEqual(n, [1, 0, 0], "the skip must be logged once")


class TestAnExitedSessionIsNotReArmed(unittest.TestCase):
    """#335 -- the user's OWN stated way of stopping a session (`ja ked
    nerobim v niektorom projekte exitnem sa z claude`) leaves NO
    `Goal cleared:` (or any other goal-marker) that
    `_goal_was_cleared_by_user` would ever see, so without this an exited
    session gets re-armed on the very next sweep exactly like a healthy
    resolved cycle. Mirrors `TestAGoalTheUserClearedIsNotReArmed` verbatim,
    substituting an explicit `/exit` slash-command entry for the
    `Goal cleared:` marker."""

    def _projects(self, cwd, *lines):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        d = root / wd.encode_project_dir(cwd)
        d.mkdir(parents=True)
        (d / "sess-exit.jsonl").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
        return root

    @staticmethod
    def _marker(state, payload="STOP CONDITIONS — the loop is DONE ...",
                ts="2026-07-29T09:00:00.000Z"):
        return _json.dumps({
            "type": "system",
            "timestamp": ts,
            "content": "<local-command-stdout>Goal %s: %s</local-command-stdout>"
                       % (state, payload),
        })

    @staticmethod
    def _exit(ts):
        return _json.dumps({
            "type": "user", "timestamp": ts,
            "message": {"content":
                        "<command-name>/exit</command-name>\n"
                        "            <command-message>exit</command-message>\n"
                        "            <command-args></command-args>"}})

    @staticmethod
    def _exit_caveat(ts):
        # #335-review F1 ROUND 2 -- the real, live-verified companion CC
        # writes IMMEDIATELY BEFORE the actual /exit command entry, also
        # type=="user" -- the shape this repo's own real corpus shows.
        return _json.dumps({
            "type": "user", "timestamp": ts,
            "message": {"content":
                        "<local-command-caveat>Caution: /exit ends this "
                        "session.</local-command-caveat>"}})

    @staticmethod
    def _exit_stdout(ts):
        # the companion CC writes IMMEDIATELY AFTER the exit command,
        # routinely a few milliseconds LATER than the command entry itself
        # -- the exact reason the round-1 release check (built on the
        # shared _last_real_turn_ts) was defeated on the exit's own
        # bookkeeping.
        return _json.dumps({
            "type": "user", "timestamp": ts,
            "message": {"content":
                        "<local-command-stdout>Bye!</local-command-stdout>"}})

    def test_exited_session_is_left_alone(self):
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("set"),
                            self._exit("2026-07-29T09:05:00.000Z"))
        tmux = FakeTmux(ARM_PANE)
        logs = wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(tmux.typed(),
                         "a session the user exited must not be re-armed")
        self.assertTrue(any("cleared" in ln for ln in logs), logs)

    def test_an_exit_before_a_later_set_marker_does_not_block(self):
        # something happened AFTER the exit (a fresh arm) -- the exit no
        # longer governs, the SAME release the `Goal cleared:` case gets.
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd,
                            self._marker("set", ts="2026-07-29T08:00:00.000Z"),
                            self._exit("2026-07-29T08:30:00.000Z"),
                            self._marker("set", ts="2026-07-29T09:00:00.000Z"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed())

    def test_a_nested_exit_inside_a_tool_result_is_never_treated_as_real(self):
        cwd = "/home/x/devel/demo"
        nested = _json.dumps({
            "type": "user", "timestamp": "2026-07-29T09:05:00.000Z",
            "message": {"content": [
                {"type": "tool_result", "content": [
                    {"type": "text",
                     "text": "<command-name>/exit</command-name>"}]}]}})
        pd = self._projects(cwd, self._marker("set"), nested)
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed(),
                        "a QUOTED /exit inside a tool_result must never "
                        "count as this session's own exit")

    @staticmethod
    def _turn(ts, role="assistant"):
        # a genuine, real transcript turn (never a `/goal` marker) -- the
        # #335-review F1 release signal, distinct from `_marker` above.
        return _json.dumps({"type": role, "timestamp": ts,
                            "message": {"content": "ordinary conversation"}})

    def test_an_exit_released_by_a_later_real_turn_with_no_new_marker(self):
        # #335-review F1 (CRITICAL) -- the FIRST shipped version had no
        # release condition at all besides a NEWER goal marker, and `/exit`
        # itself writes no marker, so this exact case (the session comes
        # back and does ordinary work, no `/goal`/`/goal clear` involved)
        # was permanently blocked forever. A later real turn (user OR
        # assistant) postdating the exit must release it, same as a
        # `Goal clear:`'s own `arm_after` release.
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd,
                            self._marker("set", ts="2026-07-29T09:00:00.000Z"),
                            self._exit("2026-07-29T09:05:00.000Z"),
                            self._turn("2026-07-29T09:10:00.000Z"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed(),
                        "ordinary activity after the exit must release it, "
                        "even with no new /goal marker of any kind")

    def test_an_exit_with_no_later_activity_stays_blocked(self):
        # the companion control: the SAME transcript, minus the trailing
        # real turn, must still refuse -- proves the release above is
        # genuinely conditional on the later activity, not a side effect
        # of anything else in the fixture.
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd,
                            self._marker("set", ts="2026-07-29T09:00:00.000Z"),
                            self._exit("2026-07-29T09:05:00.000Z"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(tmux.typed())

    def test_a_marker_with_an_unmeasurable_timestamp_never_lets_the_exit_stand(
            self):
        # #335-review F1 sub-finding -- a marker exists but its own `ts`
        # cannot be parsed, so whether it postdates the exit is genuinely
        # unknown either way; the function must fail OPEN (never block)
        # rather than trust an ordering it cannot establish, matching its
        # own documented default.
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd,
                            self._marker("set", ts="not-a-real-timestamp"),
                            self._exit("2026-07-29T09:05:00.000Z"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed(),
                        "an unmeasurable marker timestamp must never make "
                        "the exit look decisive")

    def test_a_message_merely_quoting_the_exit_marker_is_never_a_real_exit(
            self):
        # #335-review F4 (MINOR) -- `startswith`, not `in`: a longer
        # message merely QUOTING the marker text (a pasted runbook
        # excerpt, a review comment) must never count as a real /exit.
        cwd = "/home/x/devel/demo"
        quoting = _json.dumps({
            "type": "user", "timestamp": "2026-07-29T09:05:00.000Z",
            "message": {"content":
                        "See the docs: <command-name>/exit</command-name> "
                        "is how you stop a session."}})
        pd = self._projects(cwd, self._marker("set"), quoting)
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(tmux.typed(),
                        "a message merely QUOTING the /exit marker text "
                        "must never count as a real exit")

    def test_a_real_exit_triple_is_not_defeated_by_its_own_companions(self):
        # #335-review F1 ROUND 2 (fresh-context adversarial review, executed
        # proof over the real transcript corpus) -- a genuine /exit is
        # written as a TRIPLE of type=="user" entries: a
        # <local-command-caveat>, the actual <command-name>/exit</...> the
        # marker matches, and a <local-command-stdout>Bye!</...> -- and the
        # stdout companion routinely carries a timestamp a few
        # MILLISECONDS NEWER than the exit command entry itself (same
        # command batch). The round-1 release check (built on the SHARED
        # _last_real_turn_ts, which counts ANY real turn) read that
        # companion as "activity postdating the exit" and released the
        # suppression on the exit's own bookkeeping -- measured against
        # the real corpus, this defeated the protection on 13 of 15
        # genuinely-exited-and-never-resumed sessions. This fixture
        # reproduces the exact real shape: caveat 100ms BEFORE the exit
        # command, stdout 50ms AFTER it -- and the session must still stay
        # blocked.
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("set"),
                            self._exit_caveat("2026-07-29T09:04:59.900Z"),
                            self._exit("2026-07-29T09:05:00.000Z"),
                            self._exit_stdout("2026-07-29T09:05:00.050Z"))
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(tmux.typed(),
                         "the exit's own companion bookkeeping entries "
                         "must never count as activity that releases the "
                         "suppression")


class TestAClearedGoalStopsSuppressingOnceTheSessionAsksAgain(
        unittest.TestCase):
    """The #170 suppression had no exit condition, so it never expired.

    Live regression (montalu@subdev, 2026-07-31): the user cleared a goal at
    some point, later came back, asked the session to print the arm question
    again — and job 9 refused forever, one `skip cleared (goal-autoarm) %0
    (odoo)` line per sweep, with a fresh `/goal` line sitting unarmed on
    screen. The user reported it as the watchdog no longer arming anything.

    `_goal_was_cleared_by_user` asked only "is the NEWEST marker a clear?".
    That is true for the rest of the session's life: a clear is the last
    marker until something arms again, and the only thing that would arm it
    is the very job the clear is blocking. A suppression whose exit condition
    is the action it suppresses can never release — the same shape as the
    #134 device-ping guard that produced five silent days.

    The discriminator both cases already carry: WHERE the arm question sits
    relative to the clear. #170's arm question was printed BEFORE the clear
    (it survives on screen untouched, which is exactly why the viewport
    could not decide). A session that asks again does so AFTER it. So a
    clear suppresses until the session prints a NEW arm question, and the
    user's one paste is replaced by their one `/goal clear` — symmetric, and
    neither costs them a loop they did not choose.
    """

    def _projects(self, cwd, *lines):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        d = root / wd.encode_project_dir(cwd)
        d.mkdir(parents=True)
        (d / "sess-relapse.jsonl").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
        return root

    @staticmethod
    def _marker(state):
        return _json.dumps({
            "type": "system",
            "timestamp": "2026-07-31T09:55:51.000Z",
            "content": "<local-command-stdout>Goal %s: STOP CONDITIONS — the "
                       "loop is DONE ...</local-command-stdout>" % state,
        })

    @staticmethod
    def _asks_again():
        """An assistant turn re-printing the arm question — a TOP-LEVEL text
        block, the only shape that counts as this session asking."""
        return _json.dumps({
            "type": "assistant",
            "timestamp": "2026-07-31T11:02:03.000Z",
            "message": {"content": [{
                "type": "text",
                "text": "Autopilot je pripravený.\n"
                        "• Vlož /goal riadok vyššie (odporúčam)\n"
                        "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot "
                        "sa rozbehne",
            }]},
        })

    @staticmethod
    def _quoted_ask():
        """The SAME words arriving as a tool_result — one session grepping
        another's transcript. Structurally excluded, exactly as the marker
        reader excludes a quoted `Goal set:` (#54)."""
        return _json.dumps({
            "type": "user",
            "timestamp": "2026-07-31T11:02:03.000Z",
            "message": {"content": [{
                "type": "tool_result",
                "content": "❓ NEEDS YOU: vlož /goal riadok vyššie",
            }]},
        })

    def test_the_session_asking_again_releases_the_suppression(self):
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"), self._asks_again())
        tmux = FakeTmux(ARM_PANE)
        logs = wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertTrue(
            tmux.typed(),
            "the session printed a NEW arm question after the clear — the "
            "user is being asked again, so the old clear no longer governs")
        self.assertFalse([ln for ln in logs if "skip cleared" in ln], logs)

    def test_170_stays_fixed_when_the_ask_predates_the_clear(self):
        """The arm question #170 re-armed from was on screen BEFORE the
        clear. Order is the whole discriminator — reversing it must still
        suppress, or this fix simply reopens #170."""
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._asks_again(), self._marker("cleared"))
        tmux = FakeTmux(ARM_PANE)
        logs = wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(tmux.typed(),
                         "a goal cleared AFTER the ask must stay off")
        self.assertTrue(any("cleared" in ln for ln in logs), logs)

    def test_a_quoted_arm_question_never_releases_the_suppression(self):
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"), self._quoted_ask())
        tmux = FakeTmux(ARM_PANE)
        wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertFalse(
            tmux.typed(),
            "a tool_result quoting another session's arm question is not "
            "this session asking for anything")

    def test_the_release_is_readable_from_the_marker_alone(self):
        """`scan_goal_markers` carries the flag, so any future reader of goal
        state gets the same answer without re-deriving it."""
        cwd = "/home/x/devel/demo"
        pd = self._projects(cwd, self._marker("cleared"), self._asks_again())
        tr = pd / wd.encode_project_dir(cwd) / "sess-relapse.jsonl"
        _off, mark = wd.scan_goal_markers(tr)
        self.assertEqual(mark["state"], "cleared")
        self.assertTrue(mark.get("arm_after"))
        self.assertFalse(wd._goal_was_cleared_by_user(tr))


if __name__ == "__main__":
    unittest.main()


class TestDraftGoesThroughStashDelivery(unittest.TestCase):
    """#100 live incident (dev2, 2026-07-27): `/autopilot` printed the /goal
    line and the arm question at `10:42:39Z`; the pane held the user's own
    half-typed `/goal` paste the whole time, so the OLD job 9 (bare-prompt-
    only, never touch a draft) just sat there for 43 minutes until the user
    submitted it by hand. A held draft must not be a dead end: it goes
    through the SAME `deliver_with_stash` primitive job 20's revival path
    already uses — the shared mechanism, never a second invented one — and a
    refusal that never touched the pane must not burn the whole
    `GOAL_ARM_WINDOW_S` dedup window (the #101 lesson, applied here too)."""

    def _stub(self, ok, reason=None):
        calls = []

        def _fake(pid, text, run, captured=None, logs=None, sleep_fn=None):
            calls.append((pid, text, captured))
            if reason and isinstance(logs, list):
                logs.append(reason)
            return ok
        return calls, m.patch.object(wd, "deliver_with_stash", side_effect=_fake)

    def test_successful_stash_arms_and_consumes_the_window(self):
        calls, patcher = self._stub(True)
        with patcher:
            tmux, logs = go(USER_TEXT_PANE)
        self.assertEqual(len(calls), 1, "the draft must go through the stash "
                         "primitive, not be skipped outright")
        self.assertEqual(calls[0][1], GOAL_LINE)
        self.assertFalse(tmux.typed(),
                         "the draft is stashed, not typed over directly — "
                         "deliver_with_stash owns the actual keystrokes")
        self.assertTrue(any("goal-autoarm" in ln and "stash" in ln
                            for ln in logs), logs)

    def test_transient_refusal_is_retried_next_sweep_not_after_the_window(self):
        state = {}
        now = time.time()
        _calls, patcher = self._stub(False, "stash-abort: no free prompt")
        with patcher:
            _tmux, logs = go(USER_TEXT_PANE, state, now)
        self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        # a transient refusal must NOT consume the dedup window — an
        # IMMEDIATE next sweep (not GOAL_ARM_WINDOW_S later) must retry
        calls2, patcher2 = self._stub(True)
        with patcher2:
            go(USER_TEXT_PANE, state, now + 5)
        self.assertEqual(len(calls2), 1,
                         "a transient pre-send refusal must not burn the "
                         "10-minute dedup window")

    def test_permanent_refusal_still_consumes_the_window(self):
        state = {}
        now = time.time()
        _calls, patcher = self._stub(False, "stash-abort: type-verify-failed")
        with patcher:
            _tmux, logs = go(USER_TEXT_PANE, state, now)
        self.assertTrue(any(ln.startswith("goal-autoarm FAIL") for ln in logs),
                        logs)
        calls2, patcher2 = self._stub(True)
        with patcher2:
            go(USER_TEXT_PANE, state, now + 5)
        self.assertEqual(len(calls2), 0,
                         "a real (post-send) delivery failure DOES consume "
                         "the dedup window, same as any other real attempt")


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


class TestBackgroundAgentWaitDoesNotBlock(unittest.TestCase):
    """2026-07-24 restreamer incident: the session printed the /goal template +
    arm question and ended `⏳ WORKING` while an autopilot-worker ran in the
    BACKGROUND — CC renders the persistent ambient status line `✻ Waiting for
    1 background agent to finish` even though the turn has ENDED and the main
    prompt is a free bare `❯` (typing /goal there is exactly what the user
    would do by hand; `pane_at_idle_prompt` passes by design). Job 9's busy
    guard (`"Waiting for" in tail`) false-matched that ambient line, so the
    goal was NEVER auto-armed while any background worker ran ('cakam uz
    minuty a nic'). The background-agents status line alone must not block;
    every OTHER `Waiting for` (and `esc to interrupt`) still does."""

    BG_WAIT_PANE = ARM_PANE.replace(
        "❯ \n", "✻ Waiting for 1 background agent to finish\n❯ \n")
    BG_WAIT_PLURAL = ARM_PANE.replace(
        "❯ \n", "✻ Waiting for 3 background agents to finish\n❯ \n")
    OTHER_WAIT_PANE = ARM_PANE.replace(
        "❯ \n", "✻ Waiting for permission approval\n❯ \n")

    def test_bg_agent_wait_still_arms(self):
        tmux, logs = go(self.BG_WAIT_PANE)
        typed = tmux.typed()
        self.assertTrue(typed, "background-agent wait must not block the arm")
        self.assertTrue(typed[0].startswith("/goal STOP CONDITIONS"), typed[0])
        self.assertTrue(any("goal-autoarm" in ln for ln in logs), logs)

    def test_bg_agents_plural_still_arms(self):
        tmux, _ = go(self.BG_WAIT_PLURAL)
        self.assertTrue(tmux.typed(),
                        "plural background-agents wait must not block the arm")

    def test_other_waiting_for_still_blocks(self):
        tmux, _ = go(self.OTHER_WAIT_PANE)
        self.assertFalse(tmux.typed(),
                         "a non-background-agents Waiting-for must still block")


class TestAgentStripNeverBlocks(unittest.TestCase):
    """2026-07-24 gatekeeper incident (the SAME goal-never-arms class, third
    variant in two days): the pane showed the arm question + a free bare `❯`,
    but the AGENT STRIP below the prompt listed the running background workers
    with their model-generated activity labels — one read `◯ autopilot-worker
    Waiting for deploy-prod.yml jobs`, which false-matched the busy guard's
    `"Waiting for" in tail`. A strip label is ARBITRARY text (any phrase can
    appear); the strip also grows one ~160-char row PER worker, so a big strip
    crowds the arm question out of a fixed tail window taken from the RAW
    capture. Everything below the input box is CHROME (statusline / mode hint
    / borders / agent strip — `_is_bottom_chrome`), never turn state: job 9
    must scan ONLY the conversation above the input box."""

    CHROME = (
        "✻ Waiting for 3 background agents to finish\n"
        "──────────────────────────────────────────────────────"
        "────────────────────────────────────────── ultracode ─\n"
        "❯ \n"
        "──────────────────────────────────────────────────────"
        "──────────────────────────────────────────────────────\n"
        "  ctx █████░░░░░  5h 59% (41m)  wk 6% (7d)  Issues 30 · skipped 8  caveman\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        "  ● main\n"
        "  ◯ autopilot-worker  Locating Deploy to DEV run"
        "                                     33m 21s · ↓ 340.0k tokens\n"
        "  ◯ autopilot-worker  Polling PR #2078 CI status"
        "                                     16m 5s · ↓ 293.6k tokens\n"
        "  ◯ autopilot-worker  Waiting for deploy-prod.yml jobs"
        "                                     10m 18s · ↓ 191.5k tokens\n")

    GK_PANE = ARM_PANE.replace("❯ \n  ctx ███░  caveman\n", CHROME)

    # 15 workers ≈ 2400 chars of strip rows — with a raw-capture tail window the
    # arm question falls entirely OUT of it; above-input-box scoping is immune.
    BIG_STRIP = ARM_PANE.replace(
        "❯ \n  ctx ███░  caveman\n",
        "❯ \n  ctx ███░  caveman\n" + "".join(
            "  ◯ autopilot-worker  Polling job %02d of the deploy pipeline"
            "                                %2dm 10s · ↓ 100.0k tokens\n" % (i, i)
            for i in range(15)))

    def test_strip_waiting_label_still_arms(self):
        tmux, logs = go(self.GK_PANE)
        typed = tmux.typed()
        self.assertTrue(typed,
                        "an agent-strip 'Waiting for …' label must not block")
        self.assertTrue(typed[0].startswith("/goal STOP CONDITIONS"), typed[0])
        self.assertTrue(any("goal-autoarm" in ln for ln in logs), logs)

    def test_big_strip_never_crowds_question_out(self):
        tmux, _ = go(self.BIG_STRIP)
        self.assertTrue(tmux.typed(),
                        "strip bulk must not push the arm question out of the "
                        "detection window")

    def test_over_40_row_strip_skips_safely(self):
        # the chrome peel caps at 40 lines (mirrors _has_free_prompt): a
        # taller strip means the input box is never located — the pane is
        # SKIPPED this sweep (missed arm = safe direction), never armed off
        # a misread region and never crashed
        huge = ARM_PANE.replace(
            "❯ \n  ctx ███░  caveman\n",
            "❯ \n  ctx ███░  caveman\n" + "".join(
                "  ◯ autopilot-worker  Polling job %02d"
                "                                1m 10s · ↓ 1.0k tokens\n" % i
                for i in range(45)))
        tmux, _ = go(huge)
        self.assertFalse(tmux.typed(),
                         "an unpeelable >40-row strip must skip, not misarm")


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


class TestPlainBranchUsesVerifiedDelivery(unittest.TestCase):
    """#266 Defect 1: the plain (non-draft) branch of goal_autoarm used
    `send_continue` — an UNVERIFIED `send-keys -l` + immediate Enter, no
    settle/verify poll — while job 20 and job 9's OWN stash branch already
    use a verified primitive (`_send_goal_verified` / `deliver_with_stash`).
    A long payload hitting the documented paste-collapse render race then
    left an unsubmitted pasted draft in the box forever (live, spinbike
    2026-08-06). The plain branch must go through the SAME verified-
    delivery primitive, never a blind fire-and-forget send."""

    def test_bare_box_arm_uses_the_verified_primitive_not_blind_send(self):
        calls = []

        def _fake(pid, text, run, captured=None, sleep_fn=None, logs=None):
            calls.append((pid, text))
            return True

        with m.patch.object(wd, "_send_goal_verified", side_effect=_fake), \
                m.patch.object(wd, "send_continue") as fake_send_continue:
            _tmux, logs = go(ARM_PANE)
        self.assertEqual(len(calls), 1, "the bare-box arm must go through "
                         "the verified-delivery primitive, not a direct send")
        self.assertEqual(calls[0][1], GOAL_LINE)
        fake_send_continue.assert_not_called()
        self.assertTrue(any(ln.startswith("goal-autoarm OK") for ln in logs),
                        logs)

    def test_a_type_that_never_verifies_is_reported_failed_never_silently_armed(self):
        with m.patch.object(wd, "_send_goal_verified", return_value=False):
            _tmux, logs = go(ARM_PANE)
        self.assertTrue(any(ln.startswith("goal-autoarm FAIL") for ln in logs),
                        logs)

    def test_dry_run_never_calls_the_verified_primitive(self):
        with m.patch.object(wd, "_send_goal_verified") as fake:
            tmux = FakeTmux(ARM_PANE)
            logs = wd.goal_autoarm(time.time(), tmux, {}, dry_run=True)
        fake.assert_not_called()
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln for ln in logs), logs)

    def test_a_pane_that_stops_being_bare_between_capture_and_send_is_never_typed_into(self):
        # Adversarial-review finding (post-#266): the pane was bare at the
        # TOP of its loop iteration (that capture is what let arming reach
        # this far at all -- transcript reads / a foreign-transcript sudo
        # call can genuinely take a moment), but a FRESH re-capture right
        # before the send shows a draft has since appeared. Must refuse
        # WITHOUT ever calling the verified primitive, and must NOT consume
        # the 10-minute dedup window -- a zero-keystroke refusal.
        now_typed_pane = ARM_PANE.replace("❯ \n", "❯ time-critical draft\n")
        tmux = FakeTmux(ARM_PANE, capture_seq=[ARM_PANE, now_typed_pane])
        state = {}
        with m.patch.object(wd, "_send_goal_verified") as fake:
            logs = wd.goal_autoarm(time.time(), tmux, state)
        fake.assert_not_called()
        self.assertFalse(tmux.typed())
        self.assertTrue(any(ln.startswith("goal-autoarm SKIP-TRANSIENT")
                            for ln in logs), logs)
        self.assertEqual(
            state.get("goalarm", {}), {},
            "a pre-send (zero-keystroke) refusal must not consume the "
            "per-pane dedup window")


class TestGoalsOnScreenGateNoLongerPermanentlyVetoes(unittest.TestCase):
    """#266 Defect 2: the viewport `/goal ...` regex used to be a HARD
    requirement, checked BEFORE any attempt to resolve the payload from the
    session transcript — so a held draft (including job 9's OWN earlier
    stuck paste, defect 1) that visually pushes the printed `/goal` line
    out of the viewport turned arming into a PERMANENT dead end: `ga[pid]`
    only gets set once arming actually proceeds, so the identical doomed
    viewport check re-runs every sweep forever with zero journal trace
    (live, spinbike 2026-08-06)."""

    STUCK_DRAFT_PANE = (
        "● autopilot · merge=auto · authority=branch-merge · 7 ticketov\n"
        "**Otázka — projekt odoo-erp (Money→Odoo):** autopilot je pripravený.\n"
        "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám\n"
        "• Nič nevkladaj — autopilot sa nespustí\n"
        "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
        "❯\xa0[Pasted text #1 +1 lines]\n  ctx ███░  caveman\n")

    def _projects_with_goal(self, cwd="/home/x/devel/demo"):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / wd.encode_project_dir(cwd)
        d.mkdir(parents=True)
        entry = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Report.\n\n" + GOAL_LINE + "\n"}]}}
        (d / "sess-266.jsonl").write_text(_json.dumps(entry) + "\n")
        return tmp.name

    def test_no_goal_line_on_screen_never_blocks_when_transcript_resolves(self):
        # sanity: this pane genuinely has NO literal `/goal ` line anywhere —
        # the exact condition defect 2 used to veto on, permanently.
        self.assertEqual(
            re.findall(r"^\s*(/goal \S.*)$", self.STUCK_DRAFT_PANE, re.M), [])
        calls = []

        def _fake(pid, text, run, captured=None, logs=None, sleep_fn=None):
            calls.append((pid, text))
            return True

        pd = self._projects_with_goal()
        tmux = FakeTmux(self.STUCK_DRAFT_PANE)
        with m.patch.object(wd, "deliver_with_stash", side_effect=_fake):
            logs = wd.goal_autoarm(time.time(), tmux, {}, projects_dir=pd)
        self.assertEqual(
            len(calls), 1,
            "the draft branch must still reach deliver_with_stash even "
            "though no /goal line is visible in the viewport — the "
            "TRANSCRIPT resolves the payload regardless")
        self.assertEqual(calls[0][1], GOAL_LINE)
        self.assertTrue(any("goal-autoarm" in ln for ln in logs), logs)

    def test_the_veto_now_logs_a_reason_when_nothing_resolves(self):
        # genuinely nothing to arm (no transcript, no viewport /goal line) —
        # still refuses, but the refusal is LOGGED, never a silent continue.
        tmux = FakeTmux(self.STUCK_DRAFT_PANE)
        with TemporaryDirectory() as empty_pd:
            logs = wd.goal_autoarm(time.time(), tmux, {},
                                   projects_dir=Path(empty_pd))
        self.assertFalse(tmux.typed())
        self.assertTrue(any("no-goal-on-screen" in ln for ln in logs), logs)

    def test_the_no_goal_on_screen_skip_logs_once_per_streak_not_every_sweep(self):
        # Adversarial-review finding (post-#266, cosmetic): a pane that can
        # NEVER resolve a goal must keep RETRYING every sweep (defect 2's
        # whole point -- recovery the instant a transcript appears), but
        # must not re-LOG the identical refusal every 60s forever.
        tmux = FakeTmux(self.STUCK_DRAFT_PANE)
        state = {}
        now = time.time()
        with TemporaryDirectory() as empty_pd:
            logs1 = wd.goal_autoarm(now, tmux, state,
                                    projects_dir=Path(empty_pd))
            logs2 = wd.goal_autoarm(now + 60, tmux, state,
                                    projects_dir=Path(empty_pd))
        self.assertTrue(any("no-goal-on-screen" in ln for ln in logs1), logs1)
        self.assertFalse(any("no-goal-on-screen" in ln for ln in logs2), logs2)

    def test_existing_wrapped_no_transcript_behavior_is_unchanged(self):
        # regression control: a WRAPPED (truncated) /goal fragment IS on
        # screen but no transcript resolves the full payload -- still
        # refused (never arm a truncated fragment, the #36 lesson), still
        # logs the pre-existing "wrap" reason, unaffected by the lazy move
        # of the goals/frag computation.
        wrapped_pane = self.STUCK_DRAFT_PANE.replace(
            "❯\xa0[Pasted text #1 +1 lines]\n",
            "/goal STOP CONDITIONS — the loop is DONE the moment EIT\n"
            "  list --state open` shows ZERO ... continuation\n"
            "❯\xa0\n")
        tmux = FakeTmux(wrapped_pane)
        with TemporaryDirectory() as empty_pd:
            logs = wd.goal_autoarm(time.time(), tmux, {},
                                   projects_dir=Path(empty_pd))
        self.assertFalse(tmux.typed(),
                         "a truncated wrapped fragment must never be armed")
        self.assertTrue(any("wrap" in ln.lower() for ln in logs), logs)


def write_transcript(entries, root, cwd, sid):
    d = Path(root) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    with open(p, "w") as f:
        for e in entries:
            f.write(_json.dumps(e) + "\n")
    return p


def goal_marker(kind, payload="x", ts="2026-08-01T09:00:00.000Z"):
    body = "<local-command-stdout>Goal %s: %s</local-command-stdout>" % (
        kind, payload)
    return {"type": "user", "timestamp": ts, "message": {"content": body}}


TEMPLATE_FULL = "/goal FULL-AUTHORITY TEMPLATE TEXT"
TEMPLATE_BM = "/goal BRANCH-MERGE TEMPLATE TEXT"
TEMPLATE_FNM = "/goal FORK-NO-MERGE TEMPLATE TEXT"


def write_templates(root):
    """A minimal stand-in SKILL.md carrying the 3 `/goal ` lines in the SAME
    order as `airuleset.AUTHORITY_PROFILES` (full, branch-merge,
    fork-no-merge) -- exactly what `load_goal_templates` reads."""
    p = Path(root) / "SKILL.md"
    p.write_text("\n".join([TEMPLATE_FULL, "", TEMPLATE_BM, "",
                            TEMPLATE_FNM]) + "\n")
    return p


VIRGIN_CWD = "/home/x/devel/demo"      # matches FakeTmux's hardcoded cwd


class TestGoalAutoarmVirginCandidate(unittest.TestCase):
    """#320 shape 2 (montalu2): job 9's arm-question branch only ever fires
    for a session that PRINTS the `/autopilot` arm question -- a session
    doing ORDINARY work never does, so it never reaches ANY arming path at
    all and sits idle, silently, once its current work concludes. This is
    the companion candidate path job 9 takes instead, ONLY when the arm
    question does not match: a genuinely at-rest pane whose WHOLE
    transcript has NEVER shown a `/goal` marker of any kind gets armed
    ONCE with the authority-appropriate template. #170 stays untouched by
    construction: ANY prior arm or clear permanently excludes a session,
    forever, from this path."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tpl_dir = TemporaryDirectory()
        self.addCleanup(self.tpl_dir.cleanup)
        self.templates_path = write_templates(self.tpl_dir.name)
        self.sid = "virginsess"

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name, VIRGIN_CWD, self.sid)

    def test_never_touched_session_gets_armed_with_full_authority_template(self):
        self._write([{"type": "assistant",
                     "timestamp": "2026-08-01T09:00:00.000Z",
                     "message": {"content": "hi"}}])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL], logs)
        self.assertTrue(any("virgin" in ln for ln in logs), logs)

    def test_branch_merge_authority_picks_the_second_template(self):
        self._write([])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="branch-merge"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_BM])

    def test_fork_no_merge_authority_picks_the_third_template(self):
        self._write([])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="fork-no-merge"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_FNM])

    def test_a_session_that_was_ever_armed_before_is_never_touched(self):
        self._write([goal_marker("set")])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_a_session_that_was_ever_cleared_before_is_never_touched(self):
        # #170 -- a genuine standing user-off signal must never be re-armed
        # by this NEW path either, no matter how idle the pane looks now.
        self._write([goal_marker("cleared")])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_an_exited_session_with_no_prior_goal_history_is_never_touched(
            self):
        # #335-review F2 (MAJOR) -- this whole path is reachable for a
        # session that has NEVER shown a `/goal` marker of any kind, which
        # includes a session the user deliberately `/exit`ed (CC writes no
        # marker for that either) -- without this, an exited-but-never-
        # armed session sailed straight through `_goal_never_armed`'s own
        # check untouched, bypassing #335's whole fix one path over.
        self._write([{"type": "user",
                     "timestamp": "2026-08-01T09:00:00.000Z",
                     "message": {"content":
                                 "<command-name>/exit</command-name>\n"
                                 "            <command-message>exit"
                                 "</command-message>\n"
                                 "            <command-args></command-args>"}}])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed(),
                         "a session the user exited, even one that never "
                         "had a /goal before, must never be auto-armed")

    def test_an_exited_virgin_session_arms_once_released_by_later_activity(
            self):
        # the companion control: the SAME exit, released by a later real
        # turn (no `/goal` marker involved at all) -- proves the refusal
        # above is genuinely conditional, not a side effect of anything
        # else in the fixture.
        self._write([
            {"type": "user", "timestamp": "2026-08-01T09:00:00.000Z",
             "message": {"content":
                         "<command-name>/exit</command-name>\n"
                         "            <command-message>exit</command-message>\n"
                         "            <command-args></command-args>"}},
            {"type": "assistant", "timestamp": "2026-08-01T09:30:00.000Z",
             "message": {"content": "ordinary conversation"}}])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_no_transcript_at_all_is_never_touched(self):
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_busy_pane_is_never_a_candidate(self):
        self._write([])
        busy = "✳ Baking… (2m · esc to interrupt)\n  ctx ███░\n"
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(busy, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_a_pane_holding_a_draft_is_never_a_candidate(self):
        self._write([])
        draft = "❯ rozpisany draft\n  ctx ███░\n"
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(draft, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_busy_pane_is_refused_even_under_dry_run(self):
        # #320-review — the busy/draft fixtures above are ALSO caught by
        # the later fresh-recapture SKIP-TRANSIENT guard (a busy/draft
        # capture has no bare `❯` box, which THAT guard independently
        # refuses too), so removing the PRIMARY `kind != "input" or draft`
        # gate left both tests above green for the wrong reason. `dry_run`
        # returns BEFORE that later guard is ever reached, isolating the
        # primary gate specifically.
        self._write([])
        busy = "✳ Baking… (2m · esc to interrupt)\n  ctx ███░\n"
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = go(busy, projects_dir=self.tmp.name,
                            templates_path=self.templates_path, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertFalse(any("READY" in ln for ln in logs), logs)

    def test_a_draft_pane_is_refused_even_under_dry_run(self):
        self._write([])
        draft = "❯ rozpisany draft\n  ctx ███░\n"
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = go(draft, projects_dir=self.tmp.name,
                            templates_path=self.templates_path, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertFalse(any("READY" in ln for ln in logs), logs)

    def test_one_shot_never_retried_even_past_the_dedup_window(self):
        # #320's own open marker-writing mystery: if the arm itself never
        # produces a marker, the transcript stays "virgin" forever -- this
        # must NOT become an infinite retry loop. Proven PAST the ordinary
        # 10-minute dedup window, to isolate the one-shot flag specifically.
        self._write([])
        state = {}
        now0 = time.time()
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            t1, _ = go(NO_QUESTION_PANE, state=state, now=now0,
                      projects_dir=self.tmp.name,
                      templates_path=self.templates_path)
            self.assertTrue(t1.typed())
            t2, _ = go(NO_QUESTION_PANE, state=state,
                      now=now0 + wd.GOAL_ARM_WINDOW_S + 5,
                      projects_dir=self.tmp.name,
                      templates_path=self.templates_path)
        self.assertFalse(t2.typed(),
                         "never retried once already attempted, even past "
                         "the ordinary re-arm window")

    def test_a_confirmed_non_virgin_session_is_never_rescanned(self):
        # Cost discipline (#320's own design rationale): the full-file scan
        # must be paid AT MOST ONCE per session's lifetime -- this branch
        # runs for basically every idle non-arm-question pane fleet-wide,
        # so a session already known to have SOME goal history must never
        # be rescanned on a later sweep.
        self._write([goal_marker("set")])
        calls = []
        real = wd._goal_never_armed

        def spy(tpath):
            calls.append(tpath)
            return real(tpath)

        state = {}
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"), \
             m.patch.object(wd, "_goal_never_armed", side_effect=spy):
            go(NO_QUESTION_PANE, state=state, projects_dir=self.tmp.name,
              templates_path=self.templates_path)
            go(NO_QUESTION_PANE, state=state,
              now=time.time() + wd.GOAL_ARM_WINDOW_S + 5,
              projects_dir=self.tmp.name,
              templates_path=self.templates_path)
        self.assertEqual(len(calls), 1, calls)

    def test_no_templates_loaded_is_never_a_candidate(self):
        self._write([])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                             templates_path=None)
        self.assertFalse(tmux.typed())

    def test_dry_run_never_types_but_still_logs_ready(self):
        self._write([])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                            templates_path=self.templates_path, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln for ln in logs), logs)

    def test_two_panes_sharing_one_cwd_refuse_the_ambiguous_pairing(self):
        # #320-review MAJOR-2 -- `find_active_transcript` resolves the
        # NEWEST transcript for a cwd regardless of which pane actually
        # hosts it; with two live panes sharing one cwd, arming EITHER one
        # off that shared lookup can type into the WRONG pane. `go()`'s
        # FakeTmux always reports exactly one pane, so this drives
        # `goal_autoarm` directly with a REAL two-pane list-panes reply.
        self._write([])

        class TwoPaneTmux(FakeTmux):
            def __call__(self, argv, timeout=8):
                j = " ".join(argv)
                if "list-panes" in j:
                    return ("%1\tclaude\t" + VIRGIN_CWD + "\n"
                            "%2\tclaude\t" + VIRGIN_CWD)
                return super().__call__(argv, timeout)

        tmux = TwoPaneTmux(NO_QUESTION_PANE)
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            logs = wd.goal_autoarm(time.time(), tmux, {},
                                   projects_dir=self.tmp.name,
                                   templates_path=self.templates_path)
        self.assertFalse(tmux.typed(), logs)

    def test_a_second_pane_in_a_different_cwd_still_arms_normally(self):
        # the ambiguity guard must be scoped to the CWD, never trip on an
        # unrelated pane elsewhere.
        self._write([])

        class TwoCwdTmux(FakeTmux):
            def __call__(self, argv, timeout=8):
                j = " ".join(argv)
                if "list-panes" in j:
                    return ("%1\tclaude\t" + VIRGIN_CWD + "\n"
                            "%2\tclaude\t/home/x/devel/other")
                return super().__call__(argv, timeout)

        tmux = TwoCwdTmux(NO_QUESTION_PANE)
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            wd.goal_autoarm(time.time(), tmux, {}, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_a_copy_mode_pane_never_pays_the_full_file_scan(self):
        # #320-review MAJOR-3 -- `pane_in_mode` must be checked BEFORE the
        # expensive scan; a pane parked in copy-mode used to re-pay the
        # WHOLE-transcript read every single sweep, forever, since neither
        # cache is written on that return path.
        self._write([])
        calls = []
        real = wd._goal_never_armed

        def spy(tpath):
            calls.append(tpath)
            return real(tpath)

        class InModeTmux(FakeTmux):
            def __call__(self, argv, timeout=8):
                j = " ".join(argv)
                if "pane_in_mode" in j:
                    return "1"
                return super().__call__(argv, timeout)

        tmux = InModeTmux(NO_QUESTION_PANE)
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"), \
             m.patch.object(wd, "_goal_never_armed", side_effect=spy):
            wd.goal_autoarm(time.time(), tmux, {}, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertEqual(calls, [], "a copy-mode candidate must never reach "
                                    "the full-file scan")

    def test_an_already_armed_footer_with_no_marker_is_never_pasted_over(self):
        # #320-review MINOR-8 -- CC can arm WITHOUT writing any transcript
        # marker (the same open mystery #320's own dev1 forensics surfaced,
        # three times in one day); a session like that reads as
        # transcript-virgin but is NOT actually goal-less.
        self._write([])
        armed_pane = NO_QUESTION_PANE.replace(
            "  ctx ███░\n", "  ctx ███░  ◎ /goal active (1m)\n")
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = go(armed_pane, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_a_broken_authority_lookup_degrades_loudly_not_silently(self):
        # #320-review MINOR-6 -- an authority/template mismatch must LOG
        # its degraded mode (mirroring the `backlog_marker_gate` sibling
        # import), never disable the whole path with zero journal trace.
        self._write([])
        with m.patch.object(airuleset, "resolve_authority",
                           side_effect=RuntimeError("boom")):
            tmux, logs = go(NO_QUESTION_PANE, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("degraded" in ln for ln in logs), logs)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def human_entry(ts_epoch, text="kto si?"):
    return {"type": "user", "timestamp": _iso(ts_epoch),
            "message": {"content": text}}


class TestGoalAutoarmVirginCandidateRecentHuman(unittest.TestCase):
    """#339 -- montalu3: a virgin (never-`/goal`-touched) session with a
    LIVE human at the terminal must never get an auto-armed `/goal` loop
    pasted into it. `_goal_autoarm_recent_human_activity` combines the
    `/tmp/claude-user-active-<sid>` presence marker with the transcript's
    own `_last_human_prompt_ts` -- exercised independently here, plus the
    retry-once-the-human-leaves proof, the genuinely-headless positive
    control (proving this does not regress #320's own feature), cost
    ordering (never pays the full-file scan while a human is present),
    and the future-timestamp clamp."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tpl_dir = TemporaryDirectory()
        self.addCleanup(self.tpl_dir.cleanup)
        self.templates_path = write_templates(self.tpl_dir.name)
        self.sid = "recenthuman-%s" % uuid.uuid4().hex[:12]

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name, VIRGIN_CWD, self.sid)

    def _touch_active(self, age=0):
        f = "/tmp/claude-user-active-%s" % self.sid
        Path(f).write_text("")
        if age:
            old = time.time() - age
            os.utime(f, (old, old))
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        return f

    def _go(self, now=None, state=None, dry_run=False):
        return go(NO_QUESTION_PANE, state=state, now=now,
                  projects_dir=self.tmp.name,
                  templates_path=self.templates_path, dry_run=dry_run)

    def test_recent_transcript_human_prompt_refuses_with_no_marker_at_all(self):
        now = time.time()
        self._write([human_entry(now - 120)])  # the incident's own ~2min gap
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = self._go(now=now)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "recent human" in ln
               for ln in logs), logs)

    def test_recent_presence_marker_refuses_even_with_no_transcript_hint(self):
        now = time.time()
        self._write([])  # transcript itself shows no human line at all
        self._touch_active(age=60)
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = self._go(now=now)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "presence marker" in ln
               for ln in logs), logs)

    def test_a_stale_marker_and_stale_prompt_still_arms_normally(self):
        # a marker/transcript that both exist but are OLDER than the
        # window are not "recent" -- must not be confused with "no marker
        # at all" and still allow the genuinely-at-rest session through.
        now = time.time()
        self._write([human_entry(now - wd.GOAL_AUTOARM_RECENT_HUMAN_S - 300)])
        self._touch_active(age=wd.GOAL_AUTOARM_RECENT_HUMAN_S + 300)
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = self._go(now=now)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_retries_and_arms_the_instant_the_human_leaves(self):
        # SAME transcript, never rewritten -- proves the refusal is a
        # genuine per-sweep re-evaluation (#101/#266 transient discipline),
        # not a cached "this session is disqualified forever" decision.
        now0 = time.time()
        self._write([human_entry(now0)])
        state = {}
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            t1, logs1 = self._go(now=now0, state=state)
            self.assertFalse(t1.typed(), logs1)
            t2, logs2 = self._go(
                now=now0 + wd.GOAL_AUTOARM_RECENT_HUMAN_S + 5, state=state)
        self.assertEqual(t2.typed(), [TEMPLATE_FULL], logs2)

    def test_a_genuinely_headless_session_still_arms_normally(self):
        # positive control -- no marker, transcript has no human prompt at
        # all -- this new gate must not regress the #320 feature it sits
        # inside of.
        now = time.time()
        self._write([{"type": "assistant", "timestamp": _iso(now - 60),
                     "message": {"content": "hi"}}])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = self._go(now=now)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_a_grossly_future_dated_marker_beyond_the_window_still_arms(self):
        # #339-review MAJOR -- the clamp is SYMMETRIC now (a near-future
        # value counts as recent, see the mid-sweep-drift tests below), but
        # a value well BEYOND the window in the future (clock skew, a
        # transcript synced off another box) must still be bounded, never
        # treated as recent forever.
        now = time.time()
        self._write([])
        f = "/tmp/claude-user-active-%s" % self.sid
        Path(f).write_text("")
        future = now + 3600  # 1h, well past the 30-min window either side
        os.utime(f, (future, future))
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = self._go(now=now)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_a_grossly_future_dated_transcript_prompt_still_arms(self):
        now = time.time()
        self._write([human_entry(now + 3600)])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = self._go(now=now)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_a_near_future_marker_from_mid_sweep_drift_refuses(self):
        # #339-review MAJOR (the CRITICAL live-reproduced finding): `now`
        # is captured once at sweep start; job 9 runs at the sweep's TAIL,
        # so a human prompt landing seconds AFTER `now` was captured (but
        # before this pane's own turn in the per-pane loop) stamps a
        # timestamp NEWER than `now` -- a small NEGATIVE age. The old
        # asymmetric `0 <= age` clamp read that as "not recent" and armed
        # into a conversation that had, in wall-clock terms, already
        # started. The fix must refuse here.
        now = time.time()
        self._write([])
        f = "/tmp/claude-user-active-%s" % self.sid
        Path(f).write_text("")
        near_future = now + 10  # the incident's own observed ~2min gap
                                 # scaled down -- well inside the window
        os.utime(f, (near_future, near_future))
        self.addCleanup(lambda: os.path.exists(f) and os.remove(f))
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = self._go(now=now)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "presence marker" in ln
               for ln in logs), logs)

    def test_a_near_future_transcript_prompt_from_mid_sweep_drift_refuses(self):
        now = time.time()
        self._write([human_entry(now + 10)])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = self._go(now=now)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "transcript human prompt" in ln
               for ln in logs), logs)

    def test_a_stop_hook_feedback_entry_is_never_read_as_human(self):
        # #339-review MINOR -- a Stop-hook-rejected turn writes a real,
        # routine top-level `user` entry starting "Stop hook feedback:"
        # (#108/#109 measured thousands of these corpus-wide). It is
        # machine-injected, not human-typed, and must never delay a
        # genuinely-headless virgin arm.
        now = time.time()
        self._write([{
            "type": "user", "timestamp": _iso(now - 5),
            "message": {"content": "Stop hook feedback: [some-hook.sh] "
                                    "blocked this turn"}}])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, _logs = self._go(now=now)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL])

    def test_recent_human_activity_never_pays_the_full_file_scan(self):
        now = time.time()
        self._write([human_entry(now)])
        calls = []
        real = wd._goal_never_armed

        def spy(tpath):
            calls.append(tpath)
            return real(tpath)

        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"), \
             m.patch.object(wd, "_goal_never_armed", side_effect=spy):
            self._go(now=now)
        self.assertEqual(calls, [], "recent-human-activity candidates must "
                                    "never reach the full-file scan")

    def test_skip_transient_logs_once_per_streak_not_every_sweep(self):
        now0 = time.time()
        self._write([human_entry(now0)])
        state = {}
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            _t1, logs1 = self._go(now=now0, state=state)
            _t2, logs2 = self._go(now=now0 + 60, state=state)
        self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs1), logs1)
        self.assertFalse(any("SKIP-TRANSIENT" in ln for ln in logs2), logs2)

    def test_dry_run_also_refuses_recent_human_activity(self):
        now = time.time()
        self._write([human_entry(now)])
        with m.patch.object(airuleset, "resolve_authority",
                           return_value="full"):
            tmux, logs = self._go(now=now, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertFalse(any("READY" in ln for ln in logs), logs)


class TestArmQuestionBranchRefusesDuringRecentHumanActivity(unittest.TestCase):
    """#392 -- job 9's MAIN arm-question-visible branch (both the `draft`
    stash sub-branch and the plain bare-box sub-branch) had NO recent-
    human-activity hard gate at all, unlike `_goal_autoarm_virgin_
    candidate` (#339) and job 14's `_compact_recent_human_activity`
    (#377) -- the exact gap that let a delivery land in a pane the user
    was ACTIVELY TYPING in (the live dev1 incident this ticket was filed
    from). Reuses `_goal_autoarm_recent_human_activity`, checked ONCE
    right after a transcript resolves, before either delivery sub-branch,
    so a genuine recent human prompt refuses BOTH shapes with zero
    keystrokes sent."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sid = "armq-recenthuman"

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name, VIRGIN_CWD, self.sid)

    def test_bare_box_refuses_with_a_recent_human_prompt(self):
        now = time.time()
        self._write([human_entry(now - 120)])
        tmux, logs = go(ARM_PANE, now=now, projects_dir=self.tmp.name)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "recent human" in ln
               for ln in logs), logs)

    def test_held_draft_refuses_before_the_stash_primitive_is_ever_called(
            self):
        now = time.time()
        self._write([human_entry(now - 120)])
        calls = []

        def _fake(pid, text, run, captured=None, logs=None, sleep_fn=None):
            calls.append((pid, text, captured))
            return True

        with m.patch.object(wd, "deliver_with_stash", side_effect=_fake):
            tmux, logs = go(USER_TEXT_PANE, now=now,
                            projects_dir=self.tmp.name)
        self.assertEqual(calls, [], "the stash primitive must never be "
                         "invoked while the user is actively typing")
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(
            any("SKIP-TRANSIENT" in ln and "recent human" in ln
               for ln in logs), logs)

    def test_refusal_does_not_consume_the_dedup_window(self):
        now = time.time()
        self._write([human_entry(now)])
        state = {}
        go(ARM_PANE, state=state, now=now, projects_dir=self.tmp.name)
        t2, logs2 = go(ARM_PANE, state=state,
                       now=now + wd.GOAL_AUTOARM_RECENT_HUMAN_S + 5,
                       projects_dir=self.tmp.name)
        self.assertTrue(t2.typed(), logs2)

    def test_no_recent_activity_still_arms_normally(self):
        # positive control -- must not regress job 9's own existing
        # feature: a genuinely at-rest arm-question pane keeps arming.
        now = time.time()
        self._write([])
        tmux, logs = go(ARM_PANE, now=now, projects_dir=self.tmp.name)
        self.assertTrue(tmux.typed(), logs)


class TestGoalAutoarmRecentHumanActivityUnit(unittest.TestCase):
    """Direct unit coverage of `_goal_autoarm_recent_human_activity` itself
    -- independent of the whole `goal_autoarm` sweep, so the clamp/fallback
    logic is provable without a full FakeTmux/transcript round-trip too."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sid = "unit-%s" % uuid.uuid4().hex[:12]

    def _write(self, entries):
        p = Path(self.tmp.name) / (self.sid + ".jsonl")
        with open(p, "w") as f:
            for e in entries:
                f.write(_json.dumps(e) + "\n")
        return str(p)

    def test_no_marker_no_human_prompt_is_not_recent(self):
        p = self._write([])
        recent, reason = wd._goal_autoarm_recent_human_activity(
            self.sid, p, time.time())
        self.assertFalse(recent)
        self.assertEqual(reason, "")

    def test_unreadable_transcript_is_not_treated_as_recent(self):
        recent, _reason = wd._goal_autoarm_recent_human_activity(
            self.sid, "/nonexistent/path-339.jsonl", time.time())
        self.assertFalse(recent)

    def test_a_discord_relayed_answer_is_still_not_recent_by_default(self):
        # #377-review MINOR-1 -- `_last_human_prompt_ts` gained an OPT-IN
        # `extra_human_prefixes=` parameter so compact's own gate can treat
        # a Discord-relayed answer as recent (mirrors #350's own "opposite
        # exclusion set" for the identical two prefixes) -- but job 9's OWN
        # call here passes NOTHING, so its existing, reviewed "did a human
        # type this DIRECTLY" semantics must stay byte-for-byte unchanged:
        # a Discord relay must NOT count as recent for THIS gate.
        p = self._write([human_entry(
            time.time() - 5, text="Odpoveď z Discordu: áno")])
        recent, _reason = wd._goal_autoarm_recent_human_activity(
            self.sid, p, time.time())
        self.assertFalse(recent)


class TestGoalNeverArmedReadFailure(unittest.TestCase):
    """#320-review MAJOR-1 (fresh-context adversarial review, executed
    proof) -- `scan_goal_markers` fails SAFE on a read error by returning
    `(off, None)`, byte-identical to "read fine, genuinely found nothing"
    -- so `_goal_never_armed` needs an INDEPENDENT readability check before
    trusting a `mark is None` result as "virgin". Without it, a deleted or
    permission-flipped transcript (including one that DOES carry a real
    `Goal cleared:` marker the read simply could not reach) read as
    virgin -- the #170-critical direction, wrong."""

    def test_nonexistent_path_is_not_virgin(self):
        self.assertFalse(wd._goal_never_armed("/nonexistent/path-320.jsonl"))

    def test_unreadable_file_containing_a_real_cleared_marker_is_not_virgin(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file mode bits")
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "sess.jsonl"
        p.write_text(_json.dumps(goal_marker("cleared")) + "\n")
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        self.assertFalse(wd._goal_never_armed(str(p)))

    def test_a_genuinely_empty_readable_transcript_is_virgin(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "sess.jsonl"
        p.write_text("")
        self.assertTrue(wd._goal_never_armed(str(p)))


def _asks_to_arm_entry(ts="2026-08-10T19:17:00.000Z"):
    """An assistant turn printing the /autopilot arm question — a TOP-LEVEL
    text block, matching `_entry_asks_to_arm` exactly the way
    `TestAClearedGoalStopsSuppressingOnceTheSessionAsksAgain._asks_again`
    already does for a sibling test class."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{
            "type": "text",
            "text": "Autopilot je pripravený.\n"
                    "• Vlož /goal riadok vyššie (odporúčam)\n"
                    "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot "
                    "sa rozbehne",
        }]},
    }


class TestTranscriptRecentlyAskedToArm(unittest.TestCase):
    """#361 -- unit tests for the new bounded-tail helper, mirroring
    `_transcript_goal_line`'s own established shape."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, entries):
        p = Path(self.tmp.name) / "sess.jsonl"
        with open(p, "w") as f:
            for e in entries:
                f.write(_json.dumps(e) + "\n")
        return str(p)

    def test_a_recent_ask_is_found(self):
        p = self._write([_asks_to_arm_entry()])
        self.assertTrue(wd._transcript_recently_asked_to_arm(p))

    def test_ordinary_conversation_with_no_ask_is_not_found(self):
        p = self._write([{"type": "assistant",
                          "timestamp": "2026-08-10T19:17:00.000Z",
                          "message": {"content": "just ordinary work"}}])
        self.assertFalse(wd._transcript_recently_asked_to_arm(p))

    def test_a_quoted_ask_inside_a_tool_result_is_not_a_real_ask(self):
        # the SAME structural exclusion `_entry_asks_to_arm` already applies
        # (#54) -- a session grepping another's transcript must never read
        # as itself asking.
        p = self._write([{
            "type": "user", "timestamp": "2026-08-10T19:17:00.000Z",
            "message": {"content": [{
                "type": "tool_result",
                "content": "❓ NEEDS YOU: vlož /goal riadok vyššie",
            }]},
        }])
        self.assertFalse(wd._transcript_recently_asked_to_arm(p))

    def test_nonexistent_path_is_not_found(self):
        self.assertFalse(
            wd._transcript_recently_asked_to_arm("/nonexistent/x.jsonl"))

    def test_empty_transcript_is_not_found(self):
        p = self._write([])
        self.assertFalse(wd._transcript_recently_asked_to_arm(p))


class TestVirginCandidateBusyBackgroundAgent(unittest.TestCase):
    """#361 (gk incident, 2026-08-10): the /autopilot skill printed the arm
    question, the session kept dispatching subagents, and the printed line
    scrolled off `goal_autoarm`'s own 1500-char tail window within a sweep
    or two -- every LATER sweep routed into `_goal_autoarm_virgin_candidate`,
    whose FIRST gate (`_pane_has_bg_agent`) is over-cautious for a session
    that DID ask (the arm-question branch's own precedent already proves a
    bare, idle main turn is safe to arm regardless of background agents
    still listed) -- 36 minutes with zero journal trace, then a correct but
    very late self-heal once every last subagent finally cleared."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tpl_dir = TemporaryDirectory()
        self.addCleanup(self.tpl_dir.cleanup)
        self.templates_path = write_templates(self.tpl_dir.name)
        self.sid = "gksession"

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name, VIRGIN_CWD, self.sid)

    BUSY_BG_NO_QUESTION_PANE = (
        "● Bežná odpoveď bez arm otázky.\n"
        "✻ Waiting for 1 background agent to finish\n"
        "❯ \n"
        "  ctx ███░\n")

    BUSY_MID_TURN_BG_NO_QUESTION_PANE = (
        "● Bežná odpoveď bez arm otázky.\n"
        "✻ Waiting for 1 background agent to finish\n"
        "✳ Baking… (2m · esc to interrupt)\n"
        "  ctx ███░\n")

    def test_a_recently_asked_session_arms_despite_background_agents(self):
        # the CORE fix: background-agent presence alone must not block a
        # genuinely-asked session once its own MAIN turn is idle (matches
        # the arm-question branch's own already-proven-safe precedent).
        self._write([_asks_to_arm_entry()])
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            tmux, logs = go(self.BUSY_BG_NO_QUESTION_PANE,
                            projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertEqual(tmux.typed(), [TEMPLATE_FULL], logs)
        self.assertTrue(any("busy" in ln and "virgin" in ln for ln in logs),
                        logs)

    def test_an_ordinary_never_asked_busy_session_stays_silent(self):
        # the population `_pane_has_bg_agent` was actually built to guard
        # (#320 shape 2's real target) is UNTOUCHED -- no ask anywhere in
        # this session's transcript, so the original silent caution stands
        # exactly as before this ticket.
        self._write([{"type": "assistant",
                     "timestamp": "2026-08-10T19:00:00.000Z",
                     "message": {"content": "ordinary interactive work"}}])
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            tmux, logs = go(self.BUSY_BG_NO_QUESTION_PANE,
                            projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertFalse(tmux.typed(), logs)
        self.assertEqual(logs, [], "an ordinary busy session unrelated to "
                         "/autopilot must never be logged about")

    def test_recently_asked_but_main_turn_still_mid_turn_does_not_arm_yet(self):
        # background agents alone are excused, but a genuinely busy MAIN
        # turn (esc to interrupt) still refuses -- exactly like the
        # arm-question branch's own main-loop busy check.
        self._write([_asks_to_arm_entry()])
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            tmux, logs = go(self.BUSY_MID_TURN_BG_NO_QUESTION_PANE,
                            projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("busy" in ln and "virgin" in ln for ln in logs),
                        logs)

    def test_the_busy_log_is_throttled_once_per_streak(self):
        self._write([_asks_to_arm_entry()])
        state = {}
        now = time.time()
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            _t1, logs1 = go(self.BUSY_MID_TURN_BG_NO_QUESTION_PANE, state,
                            now, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
            _t2, logs2 = go(self.BUSY_MID_TURN_BG_NO_QUESTION_PANE, state,
                            now + 60, projects_dir=self.tmp.name,
                            templates_path=self.templates_path)
        self.assertTrue(any("busy" in ln for ln in logs1), logs1)
        self.assertFalse(any("busy" in ln for ln in logs2),
                         "the SAME continuous busy streak must not log "
                         "again every sweep")

    def test_arms_and_clears_the_streak_once_the_main_turn_goes_idle(self):
        # the same session, tracked as busy across one sweep, then the
        # SAME sid genuinely goes idle (bg-agent still shown, main turn
        # bare) on a later sweep -- must arm normally, and the busy-streak
        # bookkeeping must not leak forever.
        self._write([_asks_to_arm_entry()])
        state = {}
        now = time.time()
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            t1, logs1 = go(self.BUSY_MID_TURN_BG_NO_QUESTION_PANE, state,
                           now, projects_dir=self.tmp.name,
                           templates_path=self.templates_path)
            self.assertFalse(t1.typed())
            self.assertIn("gksession", state.get("goalarm_busybg", {}))
            t2, logs2 = go(self.BUSY_BG_NO_QUESTION_PANE, state, now + 60,
                           projects_dir=self.tmp.name,
                           templates_path=self.templates_path)
        self.assertEqual(t2.typed(), [TEMPLATE_FULL], logs2)
        self.assertNotIn("gksession", state.get("goalarm_busybg", {}),
                         "the busy streak must clear once the pane is no "
                         "longer busy, not leak forever")


class TestVirginCandidateReviewFixRound(unittest.TestCase):
    """#361-review (fresh-context adversarial review) -- MAJOR + MINOR-2
    fixes: an ALREADY-armed pane must never pay the bounded transcript
    read at all (the steady state of a healthy, long-lived /goal loop,
    which shows bg-agent rows for most of its life and would otherwise
    re-pay this every 60s sweep forever), and `goal_autoarm`'s own
    arm-question-branch busy streak (`goalarm_busy[pid]`) must not
    outlive a routing change into the virgin-candidate path."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tpl_dir = TemporaryDirectory()
        self.addCleanup(self.tpl_dir.cleanup)
        self.templates_path = write_templates(self.tpl_dir.name)
        self.sid = "reviewfixsess"

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name, VIRGIN_CWD, self.sid)

    def test_an_already_armed_bg_busy_pane_never_reads_the_transcript(self):
        self._write([_asks_to_arm_entry()])
        armed_bg_pane = (
            "● Bežná odpoveď bez arm otázky.\n"
            "✻ Waiting for 1 background agent to finish\n"
            "❯ \n"
            "  ctx ███░  ◎ /goal active (5m)\n")

        def _boom(_path, max_lines=400):
            raise AssertionError(
                "the transcript must never be read for an already-armed "
                "pane")

        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"), \
             m.patch.object(wd, "_transcript_recently_asked_to_arm",
                            side_effect=_boom):
            tmux, _logs = go(armed_bg_pane, projects_dir=self.tmp.name,
                             templates_path=self.templates_path)
        self.assertFalse(tmux.typed())

    def test_undeterminable_footer_still_reads_the_transcript(self):
        # the companion control -- ONLY the confirmed-True case is fast
        # -pathed; an undeterminable footer (no bare `❯` at all, so
        # `pane_goal_armed` is None) must still be allowed to reach the
        # helper -- proving the fast-path did not over-reach to this
        # case too.
        self._write([_asks_to_arm_entry()])
        calls = []
        real = wd._transcript_recently_asked_to_arm

        def spy(path, max_lines=400):
            calls.append(path)
            return real(path, max_lines)

        undeterminable_bg_pane = (
            "✻ Waiting for 1 background agent to finish\n"
            "  ctx ███░\n")
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"), \
             m.patch.object(wd, "_transcript_recently_asked_to_arm",
                            side_effect=spy):
            go(undeterminable_bg_pane, projects_dir=self.tmp.name,
              templates_path=self.templates_path)
        self.assertEqual(len(calls), 1, calls)

    def test_the_arm_question_branchs_busy_streak_clears_when_routing_changes(
            self):
        self._write([_asks_to_arm_entry()])
        state = {}
        now = time.time()
        pid = "%1"
        t1, _logs1 = go(BUSY_PANE, state, now)
        self.assertFalse(t1.typed())
        self.assertIn(pid, state.get("goalarm_busy", {}))
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="full"):
            t2, logs2 = go(NO_QUESTION_PANE, state,
                           now + wd.GOAL_ARM_WINDOW_S + 5,
                           projects_dir=self.tmp.name,
                           templates_path=self.templates_path)
        self.assertEqual(t2.typed(), [TEMPLATE_FULL], logs2)
        self.assertNotIn(pid, state.get("goalarm_busy", {}),
                         "the arm-question branch's own busy streak must "
                         "clear once routing changes to the virgin path "
                         "and it goes genuinely idle")


class TestArmQuestionBranchBusySkipIsLoud(unittest.TestCase):
    """#361 -- the arm-question branch's own three busy `continue`s used to
    be completely silent. Every pane reaching them has ALREADY had
    `_ARM_QUESTION_RX` match in its tail, so it IS a confirmed candidate by
    construction -- logging here carries zero fleet-noise risk, unlike the
    virgin-candidate path's own `_pane_has_bg_agent` gate."""

    def test_esc_to_interrupt_busy_now_logs(self):
        tmux, logs = go(BUSY_PANE)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("skip busy" in ln and "goal-autoarm" in ln
                            for ln in logs), logs)

    def test_the_busy_log_is_throttled_once_per_streak(self):
        state = {}
        now = time.time()
        _t1, logs1 = go(BUSY_PANE, state, now)
        _t2, logs2 = go(BUSY_PANE, state, now + 60)
        self.assertTrue(any("skip busy" in ln for ln in logs1), logs1)
        self.assertFalse(any("skip busy" in ln for ln in logs2),
                         "the SAME continuous busy streak must not log "
                         "again every sweep")

    def test_clears_and_arms_once_no_longer_busy(self):
        state = {}
        now = time.time()
        pid = "%1"
        t1, _logs1 = go(BUSY_PANE, state, now)
        self.assertFalse(t1.typed())
        self.assertIn(pid, state.get("goalarm_busy", {}))
        t2, _logs2 = go(ARM_PANE, state, now + wd.GOAL_ARM_WINDOW_S + 5)
        self.assertTrue(t2.typed())
        self.assertNotIn(pid, state.get("goalarm_busy", {}),
                         "the busy streak must clear once the pane is no "
                         "longer busy, not leak forever")


if __name__ == "__main__":
    unittest.main()
