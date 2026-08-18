"""#543 — central dead-box heartbeat-missing detector (watchdog job 35, dev1-only).

The per-box conformance self-check (#535, job 34) cannot report a DEAD box; this
CENTRAL detector (on dev1) reads the already-collected fleet.jsonl liveness and
LOUD-pings when a box goes silent past a threshold, WITHOUT false-alarming the
whole fleet when the central collection itself is stale.

Covers each dimension the ticket names: drift/dead / fresh / collection-fail
(fail-safe) / dedup / dry-run / cadence.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchdog import conformance_heartbeat as hb  # noqa: E402

NOW = 1_700_000_000  # fixed reference epoch (a Tue)
H = 3600


def _iso(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).astimezone().isoformat()


def _row(epoch, per_host):
    """A minimal fleet.jsonl row: ts + per_host (fresh dict, or {'error':...})."""
    return {"ts": _iso(epoch), "per_host": dict(per_host)}


def _fresh():
    return {"total_usd": 1.0}  # a fresh per-host entry has NO 'error' key


def _err(msg="ssh failed", stale=False):
    d = {"error": msg}
    if stale:
        d["stale"] = True
    return d


class _Send:
    """send_fn recorder mirroring the real notify contract (msg, dedup_key, dry_run)."""
    def __init__(self):
        self.calls = []

    def __call__(self, msg, dedup_key=None, dry_run=False, owner=None):
        self.calls.append({"msg": msg, "dedup_key": dedup_key, "dry_run": dry_run})
        return "sent"

    @property
    def msgs(self):
        return [c["msg"] for c in self.calls]


class _SendDedup:
    """send_fn fake that MODELS notify's set-membership dedup on dedup_key — a
    key already seen is SWALLOWED (returns "deduped", nothing delivered), a NEW
    key DELIVERS. Shared across sweeps so the SEND-LAYER dedup is genuinely
    exercised (#535 review MAJOR-2 / #543 review round-2 F1). A fake that IGNORES
    dedup_key (like _Send) is a FALSE-GREEN for any dedup_key behavior — it only
    tests the primary `state["conformance_heartbeat"]` dedup."""
    def __init__(self):
        self._seen_keys = set()
        self.delivered = []      # msgs that actually went out
        self.attempts = []       # (msg, dedup_key) for every call

    def __call__(self, msg, dedup_key=None, dry_run=False, owner=None):
        self.attempts.append((msg, dedup_key))
        if dedup_key in self._seen_keys:
            return "deduped"
        self._seen_keys.add(dedup_key)
        self.delivered.append(msg)
        return "sent"


def _hosts(*names):
    return lambda: [{"name": n} for n in names]


# --------------------------------------------------------------------------- #
# PURE DECIDERS
# --------------------------------------------------------------------------- #

class TestClassifyCollection(unittest.TestCase):
    def test_fresh_collection_is_ok(self):
        dim, ok, _ = hb.classify_collection(NOW - 1 * H, NOW, 12 * H)
        self.assertEqual(dim, "collection")
        self.assertIs(ok, True)

    def test_stale_collection_is_drift(self):
        _, ok, detail = hb.classify_collection(NOW - 48 * H, NOW, 12 * H)
        self.assertIs(ok, False)
        self.assertTrue(detail)

    def test_no_data_is_undetermined(self):
        _, ok, _ = hb.classify_collection(None, NOW, 12 * H)
        self.assertIsNone(ok)


class TestClassifyBox(unittest.TestCase):
    def test_recent_fresh_is_alive(self):
        _, ok, _ = hb.classify_box("boxA", NOW - 2 * H, True, NOW, 36 * H)
        self.assertIs(ok, True)

    def test_old_fresh_is_dead(self):
        _, ok, detail = hb.classify_box("boxA", NOW - 48 * H, True, NOW, 36 * H)
        self.assertIs(ok, False)
        self.assertIn("boxA", detail)

    def test_present_but_never_fresh_is_dead(self):
        _, ok, _ = hb.classify_box("boxA", None, True, NOW, 36 * H)
        self.assertIs(ok, False)

    def test_absent_box_is_undetermined_grace(self):
        # brand-new box not yet in any fleet row -> NEVER a false alarm
        _, ok, _ = hb.classify_box("boxNew", None, False, NOW, 36 * H)
        self.assertIsNone(ok)


# --------------------------------------------------------------------------- #
# ORCHESTRATOR
# --------------------------------------------------------------------------- #

def _run(state, rows, hosts, send=None, dry_run=False, persist=None, now=NOW,
         **kw):
    return hb.run_conformance_heartbeat_check(
        now, state, send_fn=send, dry_run=dry_run,
        fleet_rows_fn=lambda: rows, hosts_fn=hosts,
        persist=persist or (lambda: None),
        # tight, deterministic thresholds for the tests
        interval=kw.get("interval", 6 * H), stale=kw.get("stale", 36 * H),
        reping=kw.get("reping", 72 * H),
        collection_stale=kw.get("collection_stale", 12 * H),
        lookback=kw.get("lookback", 72 * H))


class TestOrchestratorDeadBox(unittest.TestCase):
    def test_dead_box_pings_once(self):
        send = _Send()
        rows = [_row(NOW - 48 * H, {"boxA": _fresh(), "boxB": _fresh()}),
                _row(NOW - 1 * H, {"boxA": _fresh(), "boxB": _err()})]
        state = {}
        _run(state, rows, _hosts("boxA", "boxB"), send=send)
        # boxB was last fresh 48h ago (> 36h) -> dead -> ONE ping naming boxB
        self.assertEqual(len(send.calls), 1)
        self.assertIn("boxB", send.msgs[0])
        self.assertIn("dead-box", send.msgs[0])  # a dead-box header, not collector
        # boxA is fresh 1h ago -> alive -> no ping about it
        self.assertNotIn("boxA", send.msgs[0])

    def test_live_fleet_no_ping(self):
        send = _Send()
        rows = [_row(NOW - 1 * H, {"boxA": _fresh(), "boxB": _fresh()})]
        _run({}, rows, _hosts("boxA", "boxB"), send=send)
        self.assertEqual(send.calls, [])

    def test_present_only_as_error_pings(self):
        send = _Send()
        # boxB present but ONLY ever as error across the window -> dead
        rows = [_row(NOW - 40 * H, {"boxA": _fresh(), "boxB": _err()}),
                _row(NOW - 1 * H, {"boxA": _fresh(), "boxB": _err()})]
        _run({}, rows, _hosts("boxA", "boxB"), send=send)
        self.assertEqual(len(send.calls), 1)
        self.assertIn("boxB", send.msgs[0])

    def test_brand_new_absent_box_no_ping(self):
        send = _Send()
        # boxNew never appears in fleet rows -> grace, never a false alarm
        rows = [_row(NOW - 1 * H, {"boxA": _fresh()})]
        _run({}, rows, _hosts("boxA", "boxNew"), send=send)
        self.assertEqual(send.calls, [])


class TestCollectionFailSafe(unittest.TestCase):
    def test_stale_collection_pings_collector_and_skips_boxes(self):
        send = _Send()
        # newest fleet row is 48h old -> the COLLECTION itself stalled.
        # Even though boxB would look dead, we must NOT ping per-box: ONE
        # collector ping, no dead-box pings (the fail-safe against
        # false-alarming the whole fleet).
        rows = [_row(NOW - 50 * H, {"boxA": _fresh(), "boxB": _fresh()}),
                _row(NOW - 48 * H, {"boxA": _fresh(), "boxB": _err()})]
        _run({}, rows, _hosts("boxA", "boxB"), send=send)
        self.assertEqual(len(send.calls), 1)
        self.assertNotIn("boxB", send.msgs[0])  # not a per-box dead ping
        # the one ping is about the collector / zber, NOT a "dead-box" header
        self.assertRegex(send.msgs[0].lower(), r"zber|collect|fleet")
        self.assertNotIn("dead-box", send.msgs[0])

    def test_no_fleet_data_is_silent(self):
        send = _Send()
        _run({}, [], _hosts("boxA"), send=send)
        self.assertEqual(send.calls, [])


class TestDedup(unittest.TestCase):
    def _dead_rows(self):
        return [_row(NOW - 48 * H, {"boxB": _fresh()}),
                _row(NOW - 1 * H, {"boxB": _err()})]

    def test_same_dead_box_not_repinged_within_reping(self):
        send = _Send()
        state = {}
        _run(state, self._dead_rows(), _hosts("boxB"), send=send, now=NOW)
        self.assertEqual(len(send.calls), 1)
        # a later sweep well within reping -> no second ping
        _run(state, self._dead_rows(), _hosts("boxB"), send=send,
             now=NOW + 7 * H)
        self.assertEqual(len(send.calls), 1)

    def test_recovered_then_dead_repings_immediately(self):
        send = _Send()
        state = {}
        _run(state, self._dead_rows(), _hosts("boxB"), send=send, now=NOW)
        self.assertEqual(len(send.calls), 1)
        # box recovers (fresh now) -> dedup cleared
        recovered = [_row(NOW + 7 * H, {"boxB": _fresh()})]
        _run(state, recovered, _hosts("boxB"), send=send, now=NOW + 7 * H)
        self.assertEqual(len(send.calls), 1)  # alive -> no ping
        # box dies AGAIN -> a NEW episode re-pings immediately (< reping)
        dead_again = [_row(NOW + 7 * H, {"boxB": _fresh()}),
                      _row(NOW + 8 * H, {"boxB": _err()})]
        _run(state, dead_again, _hosts("boxB"), send=send, now=NOW + 60 * H)
        self.assertEqual(len(send.calls), 2)


class TestDryRunAndCadence(unittest.TestCase):
    def test_dry_run_never_sends_or_persists(self):
        send = _Send()
        persisted = []
        rows = [_row(NOW - 48 * H, {"boxB": _fresh()}),
                _row(NOW - 1 * H, {"boxB": _err()})]
        state = {}
        _run(state, rows, _hosts("boxB"), send=send, dry_run=True,
             persist=lambda: persisted.append(1))
        self.assertEqual(send.calls, [])
        self.assertEqual(persisted, [])
        self.assertNotIn("conformance_hb_last_check", state)

    def test_cadence_gate_skips_when_not_due(self):
        send = _Send()
        rows = [_row(NOW - 48 * H, {"boxB": _fresh()}),
                _row(NOW - 1 * H, {"boxB": _err()})]
        state = {"conformance_hb_last_check": NOW - 1 * H}  # ran 1h ago
        _run(state, rows, _hosts("boxB"), send=send, interval=6 * H)
        self.assertEqual(send.calls, [])  # not due -> no work


class TestPersistBeforeNetwork(unittest.TestCase):
    def test_cadence_marker_stamped_and_persisted(self):
        send = _Send()
        persisted = []
        rows = [_row(NOW - 1 * H, {"boxA": _fresh()})]
        state = {}
        _run(state, rows, _hosts("boxA"), send=send,
             persist=lambda: persisted.append(1))
        self.assertEqual(state.get("conformance_hb_last_check"), NOW)
        self.assertGreaterEqual(len(persisted), 1)


class TestWholeFleetFailSafe(unittest.TestCase):
    """#543 review F1 (🔴, reproduced live): the collection-freshness gate is
    blind to a FRESH-ts collection whose per_host is ALL error (dev1 keeps writing
    rows but lost the tailnet). A SIMULTANEOUS total-fleet death must be ONE
    aggregate ping, never N false dead-box pings."""

    def test_all_boxes_dead_with_fresh_collection_pings_once(self):
        send = _Send()
        # collection is FRESH (latest row 1h old, < collection_stale) but EVERY
        # box is in error there — a dev1-side network failure, not 3 deaths.
        rows = [_row(NOW - 48 * H, {"boxA": _fresh(), "boxB": _fresh(),
                                    "boxC": _fresh()}),
                _row(NOW - 1 * H, {"boxA": _err(), "boxB": _err(), "boxC": _err()})]
        logs = _run({}, rows, _hosts("boxA", "boxB", "boxC"), send=send)
        # ONE aggregate ping, NOT three per-box dead pings
        self.assertEqual(len(send.calls), 1)
        self.assertIn("fleet", send.msgs[0].lower())
        self.assertNotIn("boxA", send.msgs[0])
        self.assertNotIn("boxB", send.msgs[0])
        # and the collection gate itself passed as FRESH (proving the aggregate,
        # not the collection gate, is what caught it)
        self.assertTrue(any("[collection] OK" in ln for ln in logs))

    def test_partial_death_still_pings_per_box(self):
        # boxA alive, boxB+boxC dead -> NOT all-dead -> genuine per-box pings
        send = _Send()
        rows = [_row(NOW - 48 * H, {"boxA": _fresh(), "boxB": _fresh(),
                                    "boxC": _fresh()}),
                _row(NOW - 1 * H, {"boxA": _fresh(), "boxB": _err(),
                                   "boxC": _err()})]
        _run({}, rows, _hosts("boxA", "boxB", "boxC"), send=send)
        self.assertEqual(len(send.calls), 2)
        joined = " ".join(send.msgs)
        self.assertIn("boxB", joined)
        self.assertIn("boxC", joined)
        self.assertNotIn("fleet nedostupný", joined)

    def test_all_dead_recovers_then_all_dead_repings(self):
        # aggregate latch must RE-ARM when the fleet recovers, so a second
        # total death re-pings immediately (not swallowed by the old latch).
        send = _Send()
        state = {}
        allderr = [_row(NOW - 48 * H, {"boxA": _fresh(), "boxB": _fresh()}),
                   _row(NOW - 1 * H, {"boxA": _err(), "boxB": _err()})]
        _run(state, allderr, _hosts("boxA", "boxB"), send=send, now=NOW)
        self.assertEqual(len(send.calls), 1)   # aggregate ping
        # fleet recovers
        ok_rows = [_row(NOW + 8 * H, {"boxA": _fresh(), "boxB": _fresh()})]
        _run(state, ok_rows, _hosts("boxA", "boxB"), send=send, now=NOW + 8 * H)
        self.assertEqual(len(send.calls), 1)   # alive -> no ping, latch cleared
        # fleet dies again -> re-ping immediately
        again = [_row(NOW + 8 * H, {"boxA": _fresh(), "boxB": _fresh()}),
                 _row(NOW + 60 * H, {"boxA": _err(), "boxB": _err()})]
        _run(state, again, _hosts("boxA", "boxB"), send=send, now=NOW + 60 * H)
        self.assertEqual(len(send.calls), 2)


class TestDedupKeyFreshPerDecision(unittest.TestCase):
    """#543 review round-2 F1: the send-layer dedup_key must be FRESH per decision
    instant (int(now)), NOT a coarser reping bucket that would sit inside notify's
    own longer TTL and swallow a legitimate re-ping (the #134/#535-MAJOR-2 anti-
    silence class one layer down). Driven with a dedup-honoring shared fake."""

    def test_two_dead_episodes_same_box_one_bucket_both_deliver(self):
        send = _SendDedup()
        state = {}
        big_reping = 1_000_000  # so NOW and NOW+50h fall in the SAME reping bucket
        # episode 1 (now=NOW): boxB dead (last fresh NOW-48h)
        r1 = [_row(NOW - 48 * H, {"boxB": _fresh()}),
              _row(NOW - 1 * H, {"boxB": _err()})]
        _run(state, r1, _hosts("boxB"), send=send, now=NOW, reping=big_reping)
        # boxB recovers (now=NOW+7h, past the 6h cadence gate)
        r2 = [_row(NOW + 7 * H, {"boxB": _fresh()})]
        _run(state, r2, _hosts("boxB"), send=send, now=NOW + 7 * H, reping=big_reping)
        # episode 2 (now=NOW+50h): boxB dead AGAIN (last fresh NOW+7h, age 43h)
        r3 = [_row(NOW + 7 * H, {"boxB": _fresh()}),
              _row(NOW + 49 * H, {"boxB": _err()})]
        _run(state, r3, _hosts("boxB"), send=send, now=NOW + 50 * H, reping=big_reping)
        # BOTH dead episodes must have DELIVERED — int(now) keys differ. A bucketed
        # int(now//reping) key would collapse them (same bucket, same box) and
        # swallow the second (only 1 delivered) -> this asserts fresh-per-instant.
        self.assertEqual(len(send.delivered), 2,
                         "send-layer dedup_key swallowed a legit re-ping — "
                         "bucketed instead of fresh-per-instant? keys=%s"
                         % [k for _, k in send.attempts])


class TestContinuouslyDeadNoEarlyReping(unittest.TestCase):
    """#543 review F3: a stably-dead box must NOT re-ping early when its last-fresh
    evidence ages out of the lookback window. The unbounded last_fresh scan keeps
    the dedup sig stable across that boundary."""

    def test_dead_box_across_lookback_boundary_no_extra_ping(self):
        send = _Send()
        state = {}
        F = NOW  # the box's last-fresh instant
        # sweep 1 (F+42h): boxB dead (last fresh F, 42h ago, within lookback) -> ping
        s1 = [_row(F, {"boxB": _fresh()}), _row(F + 41 * H, {"boxB": _err()})]
        _run(state, s1, _hosts("boxB"), send=send, now=F + 42 * H,
             stale=36 * H, reping=72 * H, lookback=72 * H)
        self.assertEqual(len(send.calls), 1)
        # sweep 2 (F+100h): F is now OUTSIDE the 72h lookback, but last_fresh is
        # scanned UNBOUNDED so it is still F -> sig unchanged -> within reping (72h
        # since the F+42h ping = 58h) -> NO second ping.
        s2 = [_row(F, {"boxB": _fresh()}), _row(F + 41 * H, {"boxB": _err()}),
              _row(F + 99 * H, {"boxB": _err()})]
        _run(state, s2, _hosts("boxB"), send=send, now=F + 100 * H,
             stale=36 * H, reping=72 * H, lookback=72 * H)
        self.assertEqual(len(send.calls), 1,
                         "spurious early re-ping — last_fresh flipped to None at "
                         "the lookback boundary (sig hb:F -> hb:never)?")


class TestCollectionStaleFloor(unittest.TestCase):
    """#543 review F2: collection_stale is FLOOR-clamped (and hard-clamped below
    stale) so a units-error env override cannot flag a HEALTHY collector as
    stalled and silently disable dead-box detection."""

    def test_sub_hour_env_override_is_floored(self):
        import os
        from unittest import mock
        send = _Send()
        # a HEALTHY collection (latest row 1h old) + a units-error env value of
        # "6" (6h meant, read as 6 SECONDS). Without the floor, 3600s > 6s ->
        # STALLED -> collector ping -> dead-box detection disabled.
        rows = [_row(NOW - 1 * H, {"boxA": _fresh(), "boxB": _fresh()})]
        with mock.patch.dict(
                os.environ,
                {"AIRULESET_CONFORMANCE_HB_COLLECTION_STALE_S": "6"}):
            hb.run_conformance_heartbeat_check(
                NOW, {}, send_fn=send, dry_run=False,
                fleet_rows_fn=lambda: rows, hosts_fn=_hosts("boxA", "boxB"),
                interval=6 * H, stale=36 * H, reping=72 * H, lookback=72 * H,
                collection_stale=None)   # None -> default resolution -> clamp
        # floored to >=1h -> the 1h-old row is FRESH -> per-box runs -> all alive
        # -> NO ping (a mutant that drops the floor makes this a collector ping).
        self.assertEqual(send.calls, [])


if __name__ == "__main__":
    unittest.main()
