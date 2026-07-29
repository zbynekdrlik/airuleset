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
import unittest.mock as m
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

        def _fake(pid, text, run, captured=None, logs=None):
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
        _calls, patcher = self._stub(False, "stash-abort: not idle-with-draft")
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
