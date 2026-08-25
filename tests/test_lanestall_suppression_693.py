"""#693 — the `lanestall:` give-up owner ping is the 4th frozen-goal-class spam;
suppress it (#546/#676/#688 pattern) AND classify the CAUSE of empty lanes.

Owner ruling (2026-08-25, issue #693 ROZHODNUTÉ): "tie spamy na discord su
hlupost … spravny postup by mal byt analyzovat preco bola taka situacia a ci
naozaj bol problem alebo len uz claude nevedel z ticketov v zasobniku vytlacit
ziadnu dalsiu pracu". Two halves:

1. SUPPRESS: add `lanestall:` to the EXISTING #546 owner-suppression list
   (`notify.send()` chokepoint) — the give-up send POSTs nothing, returns
   "suppressed", and leaves an explicit `suppressed` delivery-log line (never
   a silent drop). Exactly the #688 stuckalert shape; `acctblock:` + job 35
   stay the only phone alarms for a coverage outage.
2. CLASSIFY: before writing its machine-channel verdict, the give-up branch
   classifies WHY the lanes stayed empty from the per-cwd tickets-status
   cache (the SAME cache the footer renders — never a parallel derivation):
     workable==0 and U+W+gk==0  -> backlog-exhausted   (NORMAL state)
     workable==0 and U+W+gk> 0  -> parked              (NORMAL state)
     workable> 0                -> stall               (airuleset-bug signal,
                                                        machine-channel too)
     cache unreadable/stale     -> unknown             (honest, never a guess)
   The verdict line names the class + the counts.

RED against the pre-#693 tree (no `lanestall` suppression entry, no
`one_glance.lane_giveup_cause_decision`, no `statusbar.obligation_partition`,
no `cause=` on the GAVE UP journal line).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402
import statusbar                                         # noqa: E402
from watchdog import goal, one_glance                    # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    _encode,
    _write_marker_transcript,
    DeliverGoalFakeTmux,
)


class _HomeIsolated(unittest.TestCase):
    """#688 harness shape: isolated $HOME + a network spy on _post_discord."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-lane693-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        # A suppressed send must NEVER touch the network — a spy proves it.
        self._orig_post = notify._post_discord
        self.posts = []
        notify._post_discord = lambda *a, **k: self.posts.append((a, k)) or "999"
        self.addCleanup(lambda: setattr(notify, "_post_discord", self._orig_post))

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]

    def _write_env(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")


# The live lanestall key _lane_giveup_decision emits
# (`dedup_key="lanestall:%s:%d"` in watchdog/goal.py).
LANESTALL_KEYS = [
    "lanestall:sid:1000000",
    "lanestall:sess-lane-nudge-1:99880",
]


class TestLanestallIsSuppressed(_HomeIsolated):
    def test_classifier_recognises_lanestall_as_suppressed(self):
        for k in LANESTALL_KEYS:
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be a #546/#693 owner-suppressed class" % k)

    def test_lanestall_send_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        for k in LANESTALL_KEYS:
            self.posts.clear()
            r = notify.send("⚠️ /goal armovaný, lány sa nezaplnili", dedup_key=k)
            self.assertEqual(r, "suppressed", "%r should be suppressed" % k)
            self.assertEqual(self.posts, [], "%r must POST nothing" % k)

    def test_suppression_is_a_logged_decision_not_silent(self):
        # #486/#134: a suppressed send leaves an explicit delivery-log line — it
        # is a logged DECISION, never a silent drop.
        self._write_env()
        notify.send("body", dedup_key="lanestall:sid:1")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed lanestall send must be LOGGED")
        self.assertIn("lanestall", lines[-1], "the log line names the key")

    def test_prefix_boundary_no_false_match(self):
        # boundary-matched on ':'/'-' — a same-letters-but-different-namespace
        # key must NOT be swept in.
        self.assertIsNone(notify._suppressed_alert_class("lanestallother:1"))
        self.assertIsNone(notify._suppressed_alert_class("lane:1"))

    def test_acctblock_still_delivers(self):
        # #693 is scoped to lanestall — acctblock (needs a human) + job 35 stay
        # the only phone alarms for a coverage outage.
        self.assertIsNone(notify._suppressed_alert_class("acctblock:s:9"))
        self._write_env()
        r = notify.send("body", dedup_key="acctblock:s:9")
        self.assertEqual(r, "sent")


class TestLaneGiveupCauseDecision(unittest.TestCase):
    """The PURE classifier (one_glance): counts + cache age in, cause out."""

    def _dec(self, **kw):
        base = dict(workable=None, user_waiting=None, ops_wait=None, gk=None,
                    age_s=60, max_age_s=900)
        base.update(kw)
        return one_glance.lane_giveup_cause_decision(**base)

    def test_backlog_exhausted(self):
        d = self._dec(workable=0, user_waiting=0, ops_wait=0, gk=0)
        self.assertEqual(d.cause, "backlog-exhausted")

    def test_backlog_exhausted_full_authority_no_gk_bucket(self):
        # a full-authority cache entry has NO gk key (None) — still class (a).
        d = self._dec(workable=0, user_waiting=0, ops_wait=0, gk=None)
        self.assertEqual(d.cause, "backlog-exhausted")

    def test_parked_on_user_waiting(self):
        d = self._dec(workable=0, user_waiting=2, ops_wait=0, gk=0)
        self.assertEqual(d.cause, "parked")

    def test_parked_on_gk_only(self):
        d = self._dec(workable=0, user_waiting=0, ops_wait=0, gk=3)
        self.assertEqual(d.cause, "parked")

    def test_genuine_stall(self):
        d = self._dec(workable=7, user_waiting=1, ops_wait=0, gk=0)
        self.assertEqual(d.cause, "stall")

    def test_unknown_on_unreadable_workable(self):
        d = self._dec(workable=None, user_waiting=2, ops_wait=0, gk=0)
        self.assertEqual(d.cause, "unknown")

    def test_unknown_on_missing_age(self):
        d = self._dec(workable=0, user_waiting=0, ops_wait=0, gk=0, age_s=None)
        self.assertEqual(d.cause, "unknown")

    def test_unknown_on_stale_cache(self):
        d = self._dec(workable=0, user_waiting=0, ops_wait=0, gk=0,
                      age_s=901, max_age_s=900)
        self.assertEqual(d.cause, "unknown")

    def test_detail_names_the_counts(self):
        d = self._dec(workable=0, user_waiting=2, ops_wait=1, gk=None)
        for frag in ("workable=0", "U=2", "W=1", "gk=-"):
            self.assertIn(frag, d.detail, d.detail)


class TestObligationPartition(_HomeIsolated):
    """The statusbar reader: full I/U/W/gk partition off the SAME cache file
    `obligation_count` reads — never a parallel derivation."""

    CWD = "/home/newlevel/devel/lane693"

    def _write_cache(self, entry):
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / (statusbar.cwd_key(self.CWD) + ".json")).write_text(
            json.dumps(entry))

    def test_reads_all_buckets(self):
        self._write_cache({"ts": 12345, "open": 0, "user_waiting": 2,
                           "ops_wait": 1, "gk": 3, "name": "x", "root": "/x"})
        w, u, o, g, ts = statusbar.obligation_partition(self.CWD, home=self.home)
        self.assertEqual((w, u, o, g, ts), (0, 2, 1, 3, 12345))

    def test_absent_cache_is_all_none(self):
        self.assertEqual(
            statusbar.obligation_partition(self.CWD, home=self.home),
            (None, None, None, None, None))

    def test_non_int_fields_read_none_never_raise(self):
        self._write_cache({"ts": "junk", "open": None, "user_waiting": "x"})
        w, u, o, g, ts = statusbar.obligation_partition(self.CWD, home=self.home)
        self.assertEqual((w, u, o, g, ts), (None, None, None, None, None))


class TestGiveupVerdictWiring(_HomeIsolated):
    """End-to-end through goal_lane_occupancy_nudge: the GAVE UP journal line
    carries the classified cause, and the send still goes out under the
    (now-suppressed) lanestall dedup key."""

    CWD = "/home/newlevel/devel/lane693wire"
    SID = "sess-lane693-1"

    def _write_cache(self, entry):
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / (statusbar.cwd_key(self.CWD) + ".json")).write_text(
            json.dumps(entry))

    def _call(self, now, send_fn=None, rec=None):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        rec = rec if rec is not None else {"ln": goal.GOAL_LANE_MAX_NUDGES}
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", send_fn, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        return logs, rec

    def test_gave_up_line_carries_parked_cause(self):
        # The stale-state scenario the owner described: the lane machinery's
        # own 10-min backlog cache still says 5, but the FRESH tickets-status
        # partition says 0 workable + parked buckets — a NORMAL state, named.
        now = 100000
        self._write_cache({"ts": now - 60, "open": 0, "user_waiting": 2,
                           "ops_wait": 0, "gk": 0})
        logs, _ = self._call(now)
        gave = [ln for ln in logs if "GAVE UP" in ln]
        self.assertTrue(gave, logs)
        self.assertIn("cause=parked", gave[-1], gave)

    def test_gave_up_line_carries_stall_cause(self):
        now = 100000
        self._write_cache({"ts": now - 60, "open": 4, "user_waiting": 0,
                           "ops_wait": 0})
        logs, _ = self._call(now)
        gave = [ln for ln in logs if "GAVE UP" in ln]
        self.assertTrue(gave, logs)
        self.assertIn("cause=stall", gave[-1], gave)

    def test_gave_up_line_honest_unknown_without_cache(self):
        now = 100000     # no cache file written at all
        logs, _ = self._call(now)
        gave = [ln for ln in logs if "GAVE UP" in ln]
        self.assertTrue(gave, logs)
        self.assertIn("cause=unknown", gave[-1], gave)

    def test_send_still_routed_through_lanestall_key(self):
        # The #688 pattern: the CALLER keeps composing + sending; suppression
        # lives at the send() chokepoint. Lock the key shape so the notify-
        # layer suppression provably engages on the live path.
        now = 100000
        self._write_cache({"ts": now - 60, "open": 0, "user_waiting": 1,
                           "ops_wait": 0})
        sent = []

        def spy(body, **kw):
            sent.append(kw)
            return "suppressed"

        _, rec = self._call(now, send_fn=spy)
        self.assertTrue(sent, "give-up must still route through send_fn")
        self.assertTrue(
            str(sent[-1].get("dedup_key", "")).startswith("lanestall:"),
            sent)
        self.assertTrue(rec.get("lpinged"),
                        "the one-shot latch still records the episode")


if __name__ == "__main__":
    unittest.main()
