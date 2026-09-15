"""#1019 — the controller fleet-guard must ALSO watch the age of the feed claudy
CONSUMES vs the producer `/var/lib/airuleset/fleet.jsonl`, and `cmd_install` must
verify (one-time migration check) that claudy's home feed points at the shared
feed. #971 moved the PRODUCER without any consumer check, so a frozen consumer
(the manual symlink ops-fix undone / never applied) went unnoticed for 3.5 days.

Unit (1) extends the EXISTING controller-only job 35
(`watchdog/conformance_heartbeat.py`) — no new job/timer/store: a new pure
decider `classify_feed_lag` + a feed-lag section in
`run_conformance_heartbeat_check`, on its OWN hourly cadence, reusing the
job's send_fn / persist / dedup / `_sweep_due` seams. A >2h producer→consumer
gap = ONE deduped owner alarm; unmeasurable = LOGGED, never a false alarm.

Unit (2) is a pure verdict `_classify_claudy_feed` + `_verify_claudy_feed_migration`
called in `cmd_install` right after `_provision_shared_fleet_dir()`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchdog import conformance_heartbeat as hb  # noqa: E402

NOW = 1_700_000_000
H = 3600


class _Send:
    def __init__(self):
        self.calls = []

    def __call__(self, msg, dedup_key=None, dry_run=False, owner=None):
        self.calls.append({"msg": msg, "dedup_key": dedup_key, "dry_run": dry_run})
        return "sent"

    @property
    def msgs(self):
        return [c["msg"] for c in self.calls]


def _run_feed(state, producer_mtime, consumer_mtime, send=None, now=NOW,
              dry_run=False, persist=None, **kw):
    """Drive job 35 with ONLY the feed-lag path live: empty fleet rows/hosts so
    the dead-box scan pings nothing, an injected feed-mtimes seam, tight
    deterministic thresholds."""
    return hb.run_conformance_heartbeat_check(
        now, state, send_fn=send, dry_run=dry_run,
        fleet_rows_fn=lambda: [], hosts_fn=lambda: [],
        persist=persist or (lambda: None),
        interval=kw.get("interval", 6 * H), stale=kw.get("stale", 36 * H),
        reping=kw.get("reping", 72 * H),
        collection_stale=kw.get("collection_stale", 12 * H),
        lookback=kw.get("lookback", 72 * H),
        feed_mtimes_fn=lambda: (producer_mtime, consumer_mtime),
        feed_lag_interval=kw.get("feed_lag_interval", 1 * H),
        feed_lag_threshold=kw.get("feed_lag_threshold", 2 * H))


# --------------------------------------------------------------------------- #
# PURE DECIDER — classify_feed_lag
# --------------------------------------------------------------------------- #

class TestClassifyFeedLag(unittest.TestCase):
    def test_lag_over_threshold_is_alarm(self):
        name, ok, detail = hb.classify_feed_lag(NOW, NOW - 3 * H, NOW, 2 * H)
        self.assertEqual(name, "feed")
        self.assertIs(ok, False)
        self.assertTrue(detail)

    def test_lag_within_threshold_is_ok(self):
        _, ok, _ = hb.classify_feed_lag(NOW, NOW - 1 * H, NOW, 2 * H)
        self.assertIs(ok, True)

    def test_consumer_newer_than_producer_is_ok(self):
        # a consumer somehow AHEAD of producer is not a lag -> never an alarm
        _, ok, _ = hb.classify_feed_lag(NOW - 1 * H, NOW, NOW, 2 * H)
        self.assertIs(ok, True)

    def test_producer_unreadable_is_undetermined(self):
        _, ok, _ = hb.classify_feed_lag(None, NOW - 3 * H, NOW, 2 * H)
        self.assertIsNone(ok)

    def test_consumer_unreadable_is_undetermined(self):
        _, ok, _ = hb.classify_feed_lag(NOW, None, NOW, 2 * H)
        self.assertIsNone(ok)


# --------------------------------------------------------------------------- #
# ORCHESTRATOR — feed-lag section inside job 35
# --------------------------------------------------------------------------- #

class TestFeedLagOrchestrator(unittest.TestCase):
    def test_producer_fresh_consumer_3h_older_pings_once(self):
        send = _Send()
        _run_feed({}, producer_mtime=NOW, consumer_mtime=NOW - 3 * H, send=send)
        self.assertEqual(len(send.calls), 1)
        # the alarm is about the consumed feed, not a dead box / collector
        self.assertNotIn("dead-box", send.msgs[0])
        self.assertRegex(send.msgs[0].lower(), r"feed|konzum|feede")

    def test_consumer_within_2h_no_ping(self):
        send = _Send()
        _run_feed({}, producer_mtime=NOW, consumer_mtime=NOW - 1 * H, send=send)
        self.assertEqual(send.calls, [])

    def test_lag_not_repinged_within_episode(self):
        send = _Send()
        state = {}
        _run_feed(state, NOW, NOW - 3 * H, send=send, now=NOW)
        self.assertEqual(len(send.calls), 1)
        # a later run (past the 1h feed cadence), producer advanced but consumer
        # STILL frozen at the same instant -> same episode -> no second ping
        _run_feed(state, NOW + 90 * 60, NOW - 3 * H, send=send, now=NOW + 90 * 60)
        self.assertEqual(len(send.calls), 1)

    def test_catches_up_then_new_lag_repings(self):
        send = _Send()
        state = {}
        _run_feed(state, NOW, NOW - 3 * H, send=send, now=NOW)
        self.assertEqual(len(send.calls), 1)
        # consumer catches up (lag ~0) -> episode cleared, no ping
        _run_feed(state, NOW + 2 * H, NOW + 2 * H, send=send, now=NOW + 2 * H)
        self.assertEqual(len(send.calls), 1)
        # a NEW lag episode (consumer frozen at a DIFFERENT instant) -> re-ping
        _run_feed(state, NOW + 6 * H, NOW + 3 * H, send=send, now=NOW + 6 * H)
        self.assertEqual(len(send.calls), 2)

    def test_unreadable_consumer_logs_no_false_alarm(self):
        send = _Send()
        logs = _run_feed({}, producer_mtime=NOW, consumer_mtime=None, send=send)
        self.assertEqual(send.calls, [])
        self.assertTrue(any("feed" in ln.lower() for ln in logs))

    def test_unreadable_producer_no_alarm(self):
        send = _Send()
        _run_feed({}, producer_mtime=None, consumer_mtime=NOW - 3 * H, send=send)
        self.assertEqual(send.calls, [])

    def test_dry_run_never_sends_or_persists_feed(self):
        send = _Send()
        persisted = []
        state = {}
        _run_feed(state, NOW, NOW - 3 * H, send=send, dry_run=True,
                  persist=lambda: persisted.append(1))
        self.assertEqual(send.calls, [])
        self.assertEqual(persisted, [])
        self.assertNotIn("feed_lag_last_check", state)

    def test_feed_cadence_gate_skips_when_not_due(self):
        send = _Send()
        state = {"feed_lag_last_check": NOW - 10 * 60}  # ran 10 min ago
        _run_feed(state, NOW, NOW - 3 * H, send=send, now=NOW,
                  feed_lag_interval=1 * H)
        self.assertEqual(send.calls, [])  # not due -> no feed work


# --------------------------------------------------------------------------- #
# UNIT (2) — install-time migration verdict
# --------------------------------------------------------------------------- #

import airuleset  # noqa: E402

SHARED = "/var/lib/airuleset/fleet.jsonl"


class TestClassifyClaudyFeed(unittest.TestCase):
    def test_symlink_to_shared_is_ok(self):
        ok, line = airuleset._classify_claudy_feed(SHARED, SHARED, None)
        self.assertIs(ok, True)
        self.assertTrue(line)

    def test_plain_file_wrong_target_is_mismatch(self):
        ok, line = airuleset._classify_claudy_feed(
            "/home/claudy/.claude/burn-history/fleet.jsonl", SHARED, None)
        self.assertIs(ok, False)
        # a loud line naming the shared feed the operator must repoint to
        self.assertIn(SHARED, line)

    def test_claudy_fleet_env_pointing_at_shared_is_ok(self):
        ok, line = airuleset._classify_claudy_feed(
            "/home/claudy/.claude/burn-history/fleet.jsonl", SHARED,
            claudy_fleet_env=SHARED)
        self.assertIs(ok, True)

    def test_unreadable_is_caveat_not_mismatch(self):
        ok, line = airuleset._classify_claudy_feed(None, SHARED, None)
        self.assertIsNone(ok)
        self.assertTrue(line)


class TestVerifyClaudyFeedMigration(unittest.TestCase):
    def test_prints_and_never_raises(self):
        # injected facts seam -> pure behaviour, no sudo / filesystem needed
        out = airuleset._verify_claudy_feed_migration(
            read_facts=lambda: (SHARED, None),
            box_class="controller")
        # returns the (ok, line) verdict; ok True for a shared symlink
        self.assertIsInstance(out, tuple)
        self.assertIs(out[0], True)

    def test_non_controller_is_noop(self):
        out = airuleset._verify_claudy_feed_migration(
            read_facts=lambda: (_ for _ in ()).throw(AssertionError("read on non-controller")),
            box_class="workstation")
        self.assertIsNone(out)

    def test_controller_raising_read_facts_returns_none_never_raises(self):
        # #1019 review R2 🔵: on the controller a FAILING facts read must be
        # caught -> None (never propagate -> never affect install rc).
        def boom():
            raise RuntimeError("sudo blew up")
        out = airuleset._verify_claudy_feed_migration(
            read_facts=boom, box_class="controller")
        self.assertIsNone(out)


class TestFeedLagFallbackBranches(unittest.TestCase):
    """#1019 review R2 🔵: lock the fail-safe fallback branches the seams'
    internal try/except otherwise hide."""

    def test_feed_mtimes_seam_raising_logs_no_alarm(self):
        # the section's OWN try/except (not the seam's internal one) must catch a
        # raising seam -> LOG, never an alarm.
        def boom():
            raise RuntimeError("seam blew up")
        send = _Send()
        logs = hb.run_conformance_heartbeat_check(
            NOW, {}, send_fn=send, fleet_rows_fn=lambda: [], hosts_fn=lambda: [],
            interval=6 * H, stale=36 * H, reping=72 * H,
            collection_stale=12 * H, lookback=72 * H,
            feed_mtimes_fn=boom, feed_lag_interval=1 * H, feed_lag_threshold=2 * H)
        self.assertEqual(send.calls, [])
        self.assertTrue(any("feed" in ln.lower() for ln in logs))


class TestWiringLocks(unittest.TestCase):
    """#1019 review R2 🔵: a wiring drop is the #618/#623 'deployed != effective'
    class — lock the two production wirings so a future edit can't silently drop
    the install verdict print or the guard's real I/O seam with no red test."""

    def test_install_calls_the_migration_verdict(self):
        import inspect
        src = inspect.getsource(airuleset)
        self.assertIn("_verify_claudy_feed_migration()", src,
                      "cmd_install must call the migration verdict")

    def test_guard_defaults_to_the_real_feed_seam(self):
        import inspect
        src = inspect.getsource(hb)
        self.assertIn("feed_mtimes_fn = _default_feed_mtimes", src,
                      "run_conformance_heartbeat_check must default to the "
                      "production feed-mtimes seam")


class TestReservedKeyCollisionGuard(unittest.TestCase):
    """#1019 review R2 🔵: a deployable host whose NAME collides with a reserved
    dedup key (incl. the new _FEEDLAG_KEY) is skipped, never pinged."""

    def test_host_named_like_reserved_key_is_skipped(self):
        send = _Send()
        rows = [_row_hb(NOW - 48 * H, {hb._FEEDLAG_KEY: {"total_usd": 1.0}}),
                _row_hb(NOW - 1 * H, {hb._FEEDLAG_KEY: {"error": "dead"}})]
        hb.run_conformance_heartbeat_check(
            NOW, {}, send_fn=send, fleet_rows_fn=lambda: rows,
            hosts_fn=lambda: [{"name": hb._FEEDLAG_KEY}],
            interval=6 * H, stale=36 * H, reping=72 * H,
            collection_stale=12 * H, lookback=72 * H,
            feed_mtimes_fn=lambda: (None, None),
            feed_lag_interval=1 * H, feed_lag_threshold=2 * H)
        # the reserved-name host is skipped -> no dead-box ping despite being dead
        self.assertEqual(send.calls, [])


def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).astimezone().isoformat()


def _row_hb(epoch, per_host):
    return {"ts": _iso(epoch), "per_host": dict(per_host)}


class _R:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out


class TestDefaultFeedMtimesSeam(unittest.TestCase):
    """#1019 review 🟡: the SAFETY-CRITICAL `-L` symlink deref in the real I/O
    seam is otherwise untested (the orchestrator tests always inject
    feed_mtimes_fn). Without `-L`, a healthy symlink reports its CREATION time as
    the consumer mtime → a permanent FALSE lag alarm (the opposite of intent)."""

    def test_consumer_stat_dereferences_symlink_with_dash_L(self):
        import unittest.mock as m
        captured = []

        def fake_run(argv, **kw):
            captured.append(argv)          # only the consumer stat reaches here
            return _R(0, "1700000000\n")   # producer is read via os.stat

        with m.patch("subprocess.run", side_effect=fake_run):
            _producer, consumer = hb._default_feed_mtimes()
        self.assertTrue(captured, "no subprocess call captured")
        argv = captured[-1]
        self.assertIn("stat", argv)
        self.assertIn("-L", argv)          # MUST dereference — the linchpin
        self.assertIn("%Y", argv)
        self.assertEqual(consumer, 1700000000.0)

    def test_unreadable_consumer_returns_none(self):
        import unittest.mock as m
        with m.patch("subprocess.run", return_value=_R(1, "")):
            _producer, consumer = hb._default_feed_mtimes()
        self.assertIsNone(consumer)        # rc!=0 (broken symlink / no sudo)

    def test_stat_exception_returns_none(self):
        import unittest.mock as m
        with m.patch("subprocess.run", side_effect=OSError("boom")):
            _producer, consumer = hb._default_feed_mtimes()
        self.assertIsNone(consumer)


class TestReadClaudyFeedFactsSeam(unittest.TestCase):
    def test_readlink_resolves_target_with_dash_f(self):
        import unittest.mock as m
        captured = []

        def fake_run(argv, **kw):
            captured.append(argv)
            if "readlink" in argv:
                return _R(0, SHARED + "\n")
            return _R(1, "")               # grep: no CLAUDY_FLEET set
        with m.patch("subprocess.run", side_effect=fake_run):
            target, env = airuleset._read_claudy_feed_facts()
        readlink_argv = [a for a in captured if "readlink" in a][0]
        self.assertIn("-f", readlink_argv)
        self.assertEqual(target, SHARED)
        self.assertIsNone(env)

    def test_all_reads_failing_returns_none_none(self):
        import unittest.mock as m
        with m.patch("subprocess.run", side_effect=OSError("boom")):
            target, env = airuleset._read_claudy_feed_facts()
        self.assertIsNone(target)
        self.assertIsNone(env)


if __name__ == "__main__":
    unittest.main()
