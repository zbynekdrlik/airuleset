"""#1109 — a release-blocking GATEKEEPER-ACTION (INFRA) reaches the gk-infra
window within minutes.

Incident (odoo-erp 22.9.2026): release 2.318 blocked 06:30→08:44 while the
`GATEKEEPER-ACTION (INFRA)` hand-offs on odoo-erp #6883 sat behind the owner's
3 h cross-kind total nudge cap — the one real infra send of the morning
(06:26:55 delivered-unconfirmed) consumed the cap for three hours, and nothing
told the owner the gk-infra window was stuck at `5h 94%`.

This locks Approach 1 (main design comment 5783661178):
  1. a priority classification in the infra queue-arrival path (a
     STOP:/GATEKEEPER-ACTION (INFRA) comment, or an infra ticket carrying
     prio:*/release-block) delivered under a NEW `infra-priority` nudge kind;
  2. the ONE nudge gate exempts `infra-priority` from the cross-kind total cap
     while KEEPING a 15-min per-kind floor + every idle/busy/liveness gate;
  3. a hub receipt on a VERIFIED delivery (deduped per arrival id);
  4. a per-episode owner stall notice for a declared gk role pane stuck ≥ 20 min
     at ≥ 90 % of the 5 h window.
"""
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: F401,E402
import watchdog as wd  # noqa: E402
from watchdog import nudge_gate as ng  # noqa: E402
from watchdog import tmux_io as tio  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import gk_stall_notice as gsn  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    _SwallowFirstCharFake,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
MIN = 60
HOUR = 3600
DAY = 24 * 3600
INFRA = "infra-priority"


# --------------------------------------------------------------------------- #
# 1. the nudge gate: infra-priority is cap-EXEMPT but KEEPS a 15-min floor
# --------------------------------------------------------------------------- #
class TestInfraPriorityGate(unittest.TestCase):
    def test_kind_registered_stageable_not_recovery_not_gated_category(self):
        # a MACHINE (stageable, default-OFF) kind, so the `nudges` CLI list
        # derives it; NOT a recovery revival; NOT a GATED_CATEGORY (its floor is
        # 15 min, below the 60-min GATED floor lock).
        self.assertIn(INFRA, tio.MACHINE_NUDGE_KINDS)
        self.assertNotIn(INFRA, tio.RECOVERY_NUDGE_KINDS)
        self.assertNotIn(INFRA, ng.RECOVERY_NUDGE_KINDS)
        self.assertNotIn(INFRA, ng.GATED_CATEGORIES)

    def test_default_off_absent_state(self):
        # the conftest forces AIRULESET_TEST_IGNORE_DISABLE=1 (a real box's staged
        # state never fails the suite), so unset it to see the true default.
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
            with TemporaryDirectory() as home:
                self.assertFalse(wd.nudges_enabled(INFRA, home=home),
                                 "infra-priority must be OFF by default (staged on gk)")

    def test_fifteen_minute_floor(self):
        self.assertEqual(ng._category_floor(INFRA), 15 * MIN)

    def test_delivered_under_an_active_total_cap(self):
        # RED against merged main: a queue-arrival sent 60 min ago holds the 3 h
        # total cap; a release-blocking infra-priority arrival must be delivered
        # (today it read hold:total-cap and sat for 3 h).
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertTrue(ng.gate_ok(st, "s", INFRA, NOW + 60 * MIN),
                        "infra-priority must be exempt from the cross-kind cap")

    def test_own_fifteen_minute_floor_holds_a_burst(self):
        st = {}
        ng.mark_sent(st, "s", INFRA, NOW)
        self.assertFalse(ng.gate_ok(st, "s", INFRA, NOW + 5 * MIN),
                         "a second infra-priority within 15 min is held by its floor")
        reason = ng.floor_hold_reason(st, "s", INFRA, NOW + 5 * MIN)
        self.assertIn("hold:floor", reason)
        self.assertNotIn("hold:total-cap", reason)
        self.assertTrue(ng.gate_ok(st, "s", INFRA, NOW + 16 * MIN),
                        "past its 15-min floor infra-priority is allowed again")

    def test_a_non_priority_kind_still_held_by_the_total_cap(self):
        # the exemption is NARROW: a plain queue-arrival still obeys the 3 h cap.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "s", "queue-arrival", NOW + 65 * MIN),
                         "a non-priority queue-arrival stays capped")
        self.assertIn("hold:total-cap",
                      ng.floor_hold_reason(st, "s", "queue-arrival", NOW + 65 * MIN))

    def test_an_infra_priority_send_does_not_consume_the_cap_for_others(self):
        # a delivered infra-priority must NOT block a later queue-arrival via the
        # cross-kind cap (it is skipped in _total_cap_block, like recovery kinds).
        st = {}
        ng.mark_sent(st, "s", INFRA, NOW)
        self.assertTrue(ng.gate_ok(st, "s", "queue-arrival", NOW + 5 * MIN),
                        "an infra-priority delivery never counts toward the cap")


# --------------------------------------------------------------------------- #
# 2. the priority classifier (queue_arrival_recheck)
# --------------------------------------------------------------------------- #
class TestPriorityClassifier(unittest.TestCase):
    def test_gatekeeper_action_comment_is_priority(self):
        rec = {"id": 5771, "kind": "comment", "num": 6883,
               "tag": "GATEKEEPER-ACTION (INFRA)"}
        self.assertTrue(qa._is_priority_record(rec))

    def test_stop_comment_is_priority(self):
        rec = {"id": 5772, "kind": "comment", "num": 6883, "tag": "STOP:"}
        self.assertTrue(qa._is_priority_record(rec))

    def test_plain_infra_ticket_is_not_priority(self):
        rec = {"id": 42, "kind": "ticket", "num": 42, "tag": "infra"}
        self.assertFalse(qa._is_priority_record(rec))

    def test_ticket_with_prio_label_is_priority(self):
        # future-proofed: when the builder supplies labels, a prio:* / release-
        # block infra ticket is priority too.
        rec = {"id": 43, "kind": "ticket", "num": 43, "tag": "infra",
               "labels": ["infra", "prio:high"]}
        self.assertTrue(qa._is_priority_record(rec))
        rec2 = {"id": 44, "kind": "ticket", "num": 44, "tag": "infra",
                "labels": ["release-block"]}
        self.assertTrue(qa._is_priority_record(rec2))

    def test_malformed_record_is_not_priority(self):
        self.assertFalse(qa._is_priority_record(None))
        self.assertFalse(qa._is_priority_record("nope"))
        self.assertFalse(qa._is_priority_record({}))

    def test_wave_has_priority(self):
        id_map = {1: {"id": 1, "kind": "ticket", "num": 1, "tag": "infra"},
                  2: {"id": 2, "kind": "comment", "num": 6883,
                      "tag": "GATEKEEPER-ACTION (INFRA)"}}
        self.assertTrue(qa._wave_has_priority([1, 2], id_map))
        self.assertFalse(qa._wave_has_priority([1], id_map))
        self.assertFalse(qa._wave_has_priority([2], None))


# --------------------------------------------------------------------------- #
# 3. the hub receipt helper
# --------------------------------------------------------------------------- #
class TestHubReceipt(unittest.TestCase):
    def _rec(self, cid, num=6883):
        return {"id": cid, "kind": "comment", "num": num,
                "tag": "GATEKEEPER-ACTION (INFRA)"}

    def test_one_receipt_per_arrival_id_deduped(self):
        posts = []
        state = {}
        recs = [self._rec(5771), self._rec(5772)]
        n = qa._post_hub_receipts(state, recs, NOW, "gk-infra:0",
                                  lambda num, text: posts.append((num, text)))
        self.assertEqual(n, 2)
        self.assertEqual(sorted(p[0] for p in posts), [6883, 6883])
        # re-posting the SAME arrivals must post nothing (deduped in state).
        posts.clear()
        n2 = qa._post_hub_receipts(state, recs, NOW + HOUR, "gk-infra:0",
                                   lambda num, text: posts.append((num, text)))
        self.assertEqual(n2, 0)
        self.assertEqual(posts, [])

    def test_receipt_text_names_the_pane_and_arrival(self):
        posts = []
        qa._post_hub_receipts({}, [self._rec(5771)], NOW, "gk-infra:0",
                              lambda num, text: posts.append((num, text)))
        self.assertEqual(len(posts), 1)
        _num, text = posts[0]
        self.assertIn("delivered", text.lower())
        self.assertIn("gk-infra", text)
        self.assertIn("5771", text)


# --------------------------------------------------------------------------- #
# 4. the rider: priority arrival delivered as infra-priority under an active cap
# --------------------------------------------------------------------------- #
class _RiderBase(unittest.TestCase):
    CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-1109")
        self.sid = self.tpath.stem

    def _tmux(self, cls=DeliverGoalFakeTmux, **kw):
        return cls([("%9", "claude", self.CWD, "111")],
                   GOAL_ARMED_CAP, model_type=True,
                   transcript_path=self.tpath, **kw)

    def _run(self, qrecs, recs, tmux, *, state, receipt_post_fn=None,
             handled=None, captured=None):
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"):
            return qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath,
                "gk-infra:0", False, handled, queue_fetch=None,
                state=state, sleep_fn=lambda *a, **k: None, captured=captured,
                infra_queue_fetch=lambda cwd: recs,
                resolve_role_fn=lambda cwd: "infra",
                receipt_post_fn=receipt_post_fn)


class TestRiderPriorityDelivery(_RiderBase):
    def _prio(self, cid):
        return {"id": cid, "kind": "comment", "num": 6883,
                "tag": "GATEKEEPER-ACTION (INFRA)",
                "permalink": "https://github.com/zbynekdrlik/odoo-erp/issues/6883"}

    def test_priority_arrival_delivered_though_total_cap_active(self):
        # a queue-arrival stamped 60 min ago holds the cap for a plain arrival;
        # a GATEKEEPER-ACTION (INFRA) arrival must still be delivered as
        # infra-priority and stamp THAT kind's floor.
        state = {}
        ng.mark_sent(state, self.sid, "queue-arrival", NOW - 60 * MIN)
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(qrecs, [{"id": 1, "kind": "ticket", "num": 1,
                                  "tag": "infra"}, self._prio(5771)],
                         tmux, state=state, handled=set())
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertFalse(any("hold:total-cap" in ln for ln in logs), logs)
        # the infra-priority floor is now stamped (not the queue-arrival one).
        self.assertIn(INFRA, ng._session(state, self.sid))

    def test_hub_receipt_posted_on_verified_delivery(self):
        posts = []
        state = {}
        qrecs = {self.sid: {"base": [], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        self._run(qrecs, [self._prio(5771)], tmux, state=state,
                  receipt_post_fn=lambda num, text: posts.append((num, text)),
                  handled=set())
        self.assertEqual(len(posts), 1, "one hub receipt per verified arrival")
        self.assertEqual(posts[0][0], 6883)

    def test_no_receipt_on_unverified_delivery(self):
        posts = []
        state = {}
        qrecs = {self.sid: {"base": [], "first_seen": NOW - DAY}}
        tmux = self._tmux(cls=_SwallowFirstCharFake, swallow_budget=99)
        self._run(qrecs, [self._prio(5771)], tmux, state=state,
                  receipt_post_fn=lambda num, text: posts.append((num, text)),
                  handled=set())
        self.assertEqual(posts, [], "an unverified delivery posts no receipt")

    def test_busy_pane_defers_the_priority_kind(self):
        # the cap-exemption never bypasses the idle/busy gate.
        busy = ("● Baking…\n⎿ Waiting for 2 background agents to finish…\n"
                "  ctx ███░  caveman:lite  ◎ /goal active\n")
        state = {}
        qrecs = {self.sid: {"base": [], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(qrecs, [self._prio(5771)], tmux, state=state,
                         captured=busy, handled=set())
        self.assertTrue(any("hold:busy" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])


# --------------------------------------------------------------------------- #
# 5. the gk role-pane session-limit stall notice
# --------------------------------------------------------------------------- #
class TestGkStallNotice(unittest.TestCase):
    CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"

    class _Glance:
        def __init__(self, verdict):
            self.verdict = verdict

    def _cap(self, pct=94, limited=True):
        seg = "5h %d%%(4h)" % pct
        tail = ("⎿ You've hit your session limit · resets 6:10pm (Europe/Prague)\n"
                if limited else "")
        return "● práca\n%s❯ \n  ctx ███░  %s  ◎ /goal active\n" % (tail, seg)

    def _fire(self, *, now, rec, verdict="stuck", pct=94, limited=True,
              role="infra", send_fn=None, dry_run=False):
        def role_fn(cwd):
            return ("sequential", role, "role") if role else (
                "parallel", None, "default")
        return gsn.gk_stall_notice(
            now, rec, self._Glance(verdict), self._cap(pct, limited),
            self.CWD, "sess-x", "%9", "gk-infra:0", send_fn, dry_run,
            role_fn=role_fn, run=lambda *a, **k: "")

    def test_five_hour_pct_parser(self):
        self.assertEqual(gsn.five_hour_pct("foo 5h 94%(4h) bar"), 94)
        self.assertEqual(gsn.five_hour_pct("5h 7% baz"), 7)
        self.assertIsNone(gsn.five_hour_pct("no window here"))

    def test_fires_once_after_twenty_minutes(self):
        sends = []
        rec = {}
        # first sweep: seeds the episode anchor, does NOT fire yet.
        self._fire(now=NOW, rec=rec, send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(sends, [], "no notice before 20 min")
        # 21 min later: fires exactly once.
        self._fire(now=NOW + 21 * MIN, rec=rec,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(len(sends), 1)
        # 30 min: still once (per-episode dedup).
        self._fire(now=NOW + 30 * MIN, rec=rec,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(len(sends), 1)

    def test_never_for_a_non_role_pane(self):
        sends = []
        rec = {}
        self._fire(now=NOW, rec=rec, role=None,
                   send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 30 * MIN, rec=rec, role=None,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(sends, [], "a non-declared-role pane never alerts")

    def test_never_when_not_stuck(self):
        sends = []
        rec = {}
        self._fire(now=NOW, rec=rec, verdict="working",
                   send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 30 * MIN, rec=rec, verdict="working",
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(sends, [])

    def test_never_when_window_below_ninety_and_not_limited(self):
        sends = []
        rec = {}
        self._fire(now=NOW, rec=rec, pct=40, limited=False,
                   send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 30 * MIN, rec=rec, pct=40, limited=False,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(sends, [], "below 90% and no session-limit banner → silent")

    def test_refires_after_recovery_and_restall(self):
        sends = []
        rec = {}
        self._fire(now=NOW, rec=rec, send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 21 * MIN, rec=rec,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(len(sends), 1)
        # pane recovers → episode resets.
        self._fire(now=NOW + 25 * MIN, rec=rec, verdict="working",
                   send_fn=lambda *a, **k: sends.append(a))
        # re-stalls → a NEW episode alerts afresh after another 20 min.
        self._fire(now=NOW + 30 * MIN, rec=rec,
                   send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 55 * MIN, rec=rec,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(len(sends), 2, "a fresh stall episode alerts again")

    def test_dry_run_mutates_nothing_and_never_sends(self):
        sends = []
        rec = {}
        self._fire(now=NOW, rec=rec, dry_run=True,
                   send_fn=lambda *a, **k: sends.append(a))
        self._fire(now=NOW + 21 * MIN, rec=rec, dry_run=True,
                   send_fn=lambda *a, **k: sends.append(a))
        self.assertEqual(sends, [])
        self.assertEqual(rec, {}, "dry-run persists no episode state")


if __name__ == "__main__":
    unittest.main()
