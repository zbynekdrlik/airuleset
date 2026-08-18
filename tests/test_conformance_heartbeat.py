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


if __name__ == "__main__":
    unittest.main()
