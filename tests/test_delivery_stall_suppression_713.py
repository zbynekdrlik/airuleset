"""#713 — delivery-stall & friends stay suppressed END-TO-END at the send() chokepoint.

Verdict the issue settled (see its VALIDATED/design comments): the 2026-08-25
23:29 David ping was DEPLOY-LAG — #704's denylist merged to main only at 23:55
and reached dev2 at 00:47 — NOT a send()-bypass: every watchdog Discord alert
path routes through run_once's send_fn, which defaults to notify.send
(watchdog/__init__.py::run_once), and no producer posts to Discord directly.

What these locks add over test_state_stall_suppression_704 (prefix-level):
END-TO-END coverage — the RUNTIME dedup key `delivery_stall_watch` actually
composes must land in the suppressed family at the REAL notify.send chokepoint
(a prefix-only test stays green if the producer renames its key format, which
would silently resurrect the ping) — plus the boundary-matcher guarantee the
issue asked for: the denylist catches every key SHAPE of a class listed in it,
including the shapes a future #706 net-drift entry would have to cover.
Whether net-drift SHOULD be suppressed is NOT decided here (that is #706,
owner-pending) — today's un-suppressed status is locked as exactly that.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import notify                                                # noqa: E402
import watchdog as wd                                        # noqa: E402
from test_delivery_stall import DAY, NOW, _git               # noqa: E402
from test_state_stall_suppression_704 import _HomeIsolated   # noqa: E402


class DeliveryStallEndToEnd(_HomeIsolated):
    """The real producer + the real chokepoint, wired exactly as run_once wires
    them (send_fn = notify.send) — the observed incident's path, post-#704."""

    def _repo(self):
        """A real stalled repo (test_delivery_stall's own recipe): base branch
        frozen 20 days, 5 fresh undelivered commits on dev — clears every
        detection gate (stall 48h / work-fresh 24h / min-undelivered 3)."""
        r = self.home / "codex-bridge"
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        (r / "f").write_text("0\n")
        _git(r, "add", "-A")
        _git(r, "commit", "-qm", "base", ts=NOW - 20 * DAY)
        _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(r, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/main")
        _git(r, "checkout", "-qb", "dev")
        for i in range(5):
            (r / "f").write_text("%d\n" % (i + 1))
            _git(r, "add", "-A")
            _git(r, "commit", "-qm", "work %d" % i, ts=NOW - 1800)
        return r

    def test_runtime_delivery_stall_ping_is_suppressed_at_the_chokepoint(self):
        self._write_env()          # fully configured — a normal key WOULD post
        r = self._repo()
        logs = wd.delivery_stall_watch(
            NOW, None, {}, {"sid": str(r)}, send_fn=notify.send,
            delivery_probe=lambda root, base: None)
        pings = [ln for ln in logs if "PING" in ln]
        self.assertTrue(pings, "fixture never reached the ping: %r" % logs)
        self.assertIn("suppressed", pings[-1],
                      "the RUNTIME delivery-stall key escaped the denylist")
        self.assertEqual(self.posts, [],
                         "a suppressed class reached the network layer")

    def test_stuck_main_runtime_keys_are_in_the_family_too(self):
        # The sibling git-drift alarm named alongside delivery-stall in the
        # #704 comment — its live key shapes (repo_health.py:769/:778).
        for k in ("stuck-main-open:odoo-erp:1000000",
                  "stuck-main-recover:odoo-erp:1000900"):
            self.assertIsNotNone(notify._suppressed_alert_class(k), k)


class DenylistBoundaryCatchesWhatIsListed(unittest.TestCase):
    """#713's second ask: prove the denylist actually catches everything IN it
    — never deciding what BELONGS in it (that stays the owner's #706 call)."""

    def test_observed_incident_key_is_a_suppressed_class(self):
        # The EXACT key from dev2's delivery log at the incident:
        #   2026-08-25T23:29:50+0200 sent kind=python
        #   key=delivery-stall:codex-bridge:20690
        self.assertEqual(
            notify._suppressed_alert_class("delivery-stall:codex-bridge:20690"),
            "structural-delivery-stall (#704)")

    def test_net_drift_is_not_suppressed_today_pending_706(self):
        # The class decision belongs to #706 (owner-pending) — lock today's
        # deliberate absence so a drive-by "fix" cannot pre-empt the owner.
        for k in ("net-drift-open:zbynekdrlik/codex-bridge:1787695479",
                  "net-drift-recover:zbynekdrlik/codex-bridge:1787695479"):
            self.assertIsNone(notify._suppressed_alert_class(k), k)

    def test_a_706_net_drift_entry_would_catch_both_runtime_shapes(self):
        # net_drift_alarm's live keys are `net-drift-open:…` /
        # `net-drift-recover:…` (repo_health.py:598/:604) — the boundary
        # matcher's `prefix + "-"` arm must catch BOTH from a single
        # ("net-drift", …) entry, so when #706 decides, listing the class is
        # sufficient and nothing in it can be missed.
        synthetic = notify.SUPPRESSED_ALERT_PREFIXES + (
            ("net-drift", "net-drift (#706 synthetic)"),)
        with mock.patch.object(notify, "SUPPRESSED_ALERT_PREFIXES", synthetic):
            for k in ("net-drift-open:zbynekdrlik/x:1",
                      "net-drift-recover:zbynekdrlik/x:1"):
                self.assertEqual(notify._suppressed_alert_class(k),
                                 "net-drift (#706 synthetic)", k)


if __name__ == "__main__":
    unittest.main()
