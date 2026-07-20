"""Watchdog job 8 — bounce backstop (2026-07-19).

Incident: gatekeeper review returns tickets (`prio:bounce`) AFTER the sub-dev's
autopilot loop ended — nobody picks them up and the user must intervene (4
bounced david tickets sat re-handed-off; montalu had no tmux server at all, so
the gatekeeper's ssh/tmux nudge had nowhere to land). The watchdog is the
machine-local backstop: every ~30 min it checks the repos this box recently
worked (tickets-status cache roots) for open prio:bounce tickets scoped to this
box's stream; found + live IDLE claude pane → type a nudge (the skill's
nudge-ack dispatches a worker); found + NO pane → ONE deduped Discord ping.
"""

import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar
import watchdog as wd

IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")


def seed_repo_cache(home, root, name):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
        {"open": 1, "name": name, "root": root, "ts": int(time.time())}))


class FakeTmux:
    def __init__(self, panes=None, captured=IDLE):
        self.panes = panes or []            # [(pane_id, cwd)]
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestBounceBackstop(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _go(self, state, tickets, panes=None, captured=IDLE, now=None):
        tmux = FakeTmux(panes if panes is not None else [("%1", self.root)],
                        captured)
        logs = wd.bounce_backstop(
            now or time.time(), tmux, state, self._send, home=self.home,
            gh_fetch=lambda root: tickets)
        return logs, tmux

    def test_nudges_idle_pane_with_ticket_numbers(self):
        logs, tmux = self._go({}, [1705, 1434])
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertIn("#1705", typed[0])
        self.assertIn("#1434", typed[0])
        self.assertIn("autopilot-worker", typed[0])   # points at the skill flow
        self.assertTrue(any("bounce-nudge" in ln for ln in logs), logs)
        self.assertFalse(self.pings)                  # pane existed → no Discord

    def test_busy_pane_is_left_alone(self):
        logs, tmux = self._go({}, [1705], captured=BUSY)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_pane_pings_discord_once(self):
        state = {}
        logs, _ = self._go(state, [1705, 1434], panes=[])
        self.assertEqual(len(self.pings), 1)
        body = self.pings[0][0]
        self.assertIn("demo", body)
        self.assertIn("2", body)                      # count of waiting tickets
        # second sweep, same set → deduped (no second ping)
        state["bounce"]["last_check"] = 0             # re-open the cadence gate
        self._go(state, [1705, 1434], panes=[])
        self.assertEqual(len(self.pings), 1)

    def test_changed_ticket_set_renudges(self):
        state = {}
        _, t1 = self._go(state, [1705])
        state["bounce"]["last_check"] = 0
        logs, t2 = self._go(state, [1705, 1434])
        self.assertTrue(t2.typed(), "new bounce ticket must re-nudge")

    def test_same_set_does_not_renudge_within_window(self):
        state = {}
        self._go(state, [1705])
        state["bounce"]["last_check"] = 0
        _, t2 = self._go(state, [1705])
        self.assertFalse(t2.typed())

    def test_cadence_gated(self):
        state = {}
        calls = []
        now = time.time()
        for _ in range(2):
            wd.bounce_backstop(now, FakeTmux([]), state, self._send,
                               home=self.home,
                               gh_fetch=lambda root: calls.append(root) or [])
        self.assertEqual(len(calls), 1)

    def test_gh_error_is_failsafe(self):
        logs, tmux = self._go({}, None)               # gh_fetch error → None
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_tickets_clears_state(self):
        state = {}
        self._go(state, [1705], panes=[])
        state["bounce"]["last_check"] = 0
        self._go(state, [], panes=[])
        self.assertNotIn("demo", (state.get("bounce") or {}).get("seen", {}))


class TestBounceQuals(unittest.TestCase):
    """Scoping is derived from the PANE's home dir, not the watchdog user:
    montalu's claude runs inside NEWLEVEL's tmux (a `sudo su - montalu`
    window), so newlevel's watchdog serves that pane — its cwd
    /home/montalu/... names the stream, and the stream LABEL (the #1599
    convention) scopes the query; @me is useless (gh identity is the same
    zbynekdrlik account across boxes)."""

    def test_stream_home_scopes_by_label(self):
        self.assertEqual(wd._bounce_quals("/home/montalu/devel/odoo-erp"),
                         ["label:stream:montalu"])
        self.assertEqual(wd._bounce_quals("/home/david/devel/x"),
                         ["label:stream:david"])

    def test_full_authority_home_excludes_subdev_streams(self):
        # Live dry-run finding (2026-07-19): an unscoped full-box query picked
        # up DAVID's stream bounces from newlevel's dev1 checkout and would
        # have pinged the wrong person — the sub-dev's own box nudges those.
        # Full authority = the core slice (same exclusions as tickets-status).
        quals = wd._bounce_quals("/home/newlevel/devel/demo")
        self.assertEqual(len(quals), 1)
        for u in ("david", "marek", "montalu"):
            self.assertIn("-label:stream:%s" % u, quals[0])


class TestGhEnvTokenFallback(unittest.TestCase):
    def test_bashrc_export_is_picked_up(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text(
                "# stuff\nexport GH_TOKEN=ghp_testtoken123\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertEqual(env.get("GH_TOKEN"), "ghp_testtoken123")

    def test_existing_env_token_untouched(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text("export GH_TOKEN=other\n")
            env = wd._gh_env(home, base={"GH_TOKEN": "keepme"})
            self.assertEqual(env.get("GH_TOKEN"), "keepme")

    def test_no_bashrc_is_failsafe(self):
        with TemporaryDirectory() as home:
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertNotIn("GH_TOKEN", env)


if __name__ == "__main__":
    unittest.main()


WORKFLOW_WAIT = ("● Review beží\n"
                 "  ⏳ WORKING: review Workflow beží — verdikt čaká. Nič netreba.\n"
                 "✻ Waiting for 1 dynamic workflow to finish\n"
                 "❯ \n  ctx ███░  ultracode\n")
GOAL_ACTIVE = ("● Hotovo, pokračujem ďalším ticketom.\n"
               "❯ \n  ctx ███░  ◎ /goal active (58m)\n")
NUDGED_ALREADY = ("● predtým prišiel nudge\n"
                  "❯ bounce-backstop: open prio:bounce tickets #1 in demo — x\n"
                  "❯ \n")


class TestNeverTypeIntoWorkingSession(unittest.TestCase):
    """2026-07-19 LIVE incident (user: 'dokolecka sa mu nieco pastuje do
    promptu pocas behu!!!'): the gatekeeper session sat at a `❯` prompt while
    WAITING on a background review Workflow (CC renders a free prompt then) and
    job 8 pasted the same nudge 4×. The safe-to-type gate must refuse: a pane
    showing a background-wait spinner (✻ / esc to interrupt), an armed /goal
    (◎ /goal active in the statusline), a still-visible previous nudge (belt
    against state loss), and a transcript whose last marker is ⏳ WORKING."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")
        self.pings = []

    def _go(self, captured, state=None):
        tmux = FakeTmux([("%1", self.root)], captured)
        wd.bounce_backstop(time.time(), tmux, state if state is not None else {},
                           lambda body, **kw: self.pings.append(body),
                           home=self.home, gh_fetch=lambda root: [1705])
        return tmux

    def test_background_workflow_wait_is_not_typed_into(self):
        self.assertFalse(self._go(WORKFLOW_WAIT).typed())

    def test_armed_goal_loop_is_not_typed_into(self):
        # the label alone is the insertion — the loop re-queries each turn
        self.assertFalse(self._go(GOAL_ACTIVE).typed())

    def test_visible_previous_nudge_blocks_repeat_even_with_lost_state(self):
        self.assertFalse(self._go(NUDGED_ALREADY).typed())

    def test_working_marker_in_transcript_blocks_nudge(self):
        # transcript readable + last marker ⏳ → session mid-flight, never type
        proj = Path(self.home, ".claude", "projects",
                    wd.encode_project_dir(self.root))
        proj.mkdir(parents=True)
        entry = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "robím\n⏳ WORKING: čakám na worker"}]}}
        (proj / "s1.jsonl").write_text(json.dumps(entry) + "\n")
        tmux = FakeTmux([("%1", self.root)], IDLE)
        wd.bounce_backstop(time.time(), tmux, {},
                           lambda body, **kw: self.pings.append(body),
                           home=self.home, gh_fetch=lambda root: [1705],
                           projects_dir=str(Path(self.home, ".claude", "projects")))
        self.assertFalse(tmux.typed())

    def test_truly_resting_session_is_nudged(self):
        self.assertTrue(self._go(IDLE).typed())


class TestGatekeeperNeverBounceNudged(unittest.TestCase):
    def test_gatekeeper_home_is_skipped_entirely(self):
        # bounce lane direction is reviewer→sub-dev; the gatekeeper is the
        # reviewer — nudging IT about bounces it filed is backwards (the live
        # incident). No quals = no query = no nudge for /home/gatekeeper/.
        self.assertEqual(wd._bounce_quals("/home/gatekeeper/devel/odoo-erp"), [])


class TestStatePersistedBeforeTyping(unittest.TestCase):
    def test_persist_callback_fires_before_send(self):
        # TimeoutStartSec killed the run AFTER the nudge but BEFORE run_once's
        # save_state → no dedup memory → the 4× repeat. Job 8 must persist its
        # seen-set BEFORE any keystroke/ping leaves the process.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo")
            order = []
            tmux = FakeTmux([("%1", root)], IDLE)
            real_call = tmux.__call__

            def spy(argv, timeout=8):
                if "-l" in argv:
                    order.append("send")
                return real_call(argv, timeout)
            wd.bounce_backstop(time.time(), spy, {}, lambda b, **k: None,
                               home=home, gh_fetch=lambda r: [7],
                               persist=lambda: order.append("persist"))
            self.assertIn("persist", order)
            self.assertIn("send", order)
            self.assertLess(order.index("persist"), order.index("send"))


DONE_PARKED = ("● Hotový beh.\n"
               "  ✅ DONE: celý backlog odovzdaný — 3 čakajú na review.\n"
               "✻ Worked for 1h 5m · 1 monitor still running\n"
               "❯ \n  ctx ███░  ◎ /goal active (3h)  caveman\n")


class TestDoneParkedLoopIsNudged(unittest.TestCase):
    """2026-07-20 deadlock: david's session sat at ✅ DONE under a SATISFIED
    old /goal — the ◎ /goal indicator stays lit but no turn will ever fire, so
    'the label alone is the insertion' was a dead assumption and the bounce
    rotted while the gatekeeper waited. A pane whose last output is ✅ DONE is
    AT REST — the ◎ /goal + turn-summary ✻ lines must not block the nudge.
    (A pane with USER-TYPED unsubmitted text still always refuses.)"""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")

    def _go(self, captured):
        tmux = FakeTmux([("%1", self.root)], captured)
        wd.bounce_backstop(time.time(), tmux, {}, lambda b, **k: None,
                           home=self.home, gh_fetch=lambda r: [1528])
        return tmux

    def test_done_parked_goal_session_gets_the_nudge(self):
        self.assertTrue(self._go(DONE_PARKED).typed())

    def test_live_workflow_wait_still_refused(self):
        self.assertFalse(self._go(WORKFLOW_WAIT).typed())

    def test_armed_working_loop_still_refused(self):
        self.assertFalse(self._go(GOAL_ACTIVE.replace(
            "● Hotovo, pokračujem ďalším ticketom.",
            "● Dispatchol som workera, pokračujem.")).typed())

    def test_user_typed_text_always_refuses(self):
        parked_with_input = DONE_PARKED.replace(
            "❯ \n", "❯ chekni ci nemas nieco nove\n")
        self.assertFalse(self._go(parked_with_input).typed())


class TestGhEnvCatSubstitution(unittest.TestCase):
    def test_cat_command_substitution_is_resolved(self):
        # david's real .bashrc (found 2026-07-20, the 401 root cause):
        #   export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)
        # A literal-value regex captured the string '$(cat' and gh got 401 —
        # the backstop silently found nothing while #1801 rotted for 3 hours.
        with TemporaryDirectory() as home:
            Path(home, ".config").mkdir()
            Path(home, ".config", "gh-token").write_text("ghp_realtoken42\n")
            Path(home, ".bashrc").write_text(
                "export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertEqual(env.get("GH_TOKEN"), "ghp_realtoken42")

    def test_unresolvable_substitution_is_failsafe(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text(
                "export GH_TOKEN=$(some-helper --fetch)\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertNotIn("GH_TOKEN", env)   # never a garbage literal
