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

    def test_full_authority_home_is_unscoped(self):
        self.assertEqual(wd._bounce_quals("/home/newlevel/devel/demo"), [""])


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
