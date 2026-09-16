"""#1052 — the gh rate-guard must ALSO read the GraphQL `rateLimit` object and
take the LOWER of {REST bucket, GraphQL object} for the graphql resource.

RED-first: the incident (controller 2026-09-16 20:26–20:30) — the REST
`rate_limit` endpoint reported `graphql remaining 5000/5000 used 0` while the
GraphQL `rateLimit` object reported `remaining 0 used 5000 resetAt 18:30:01Z`,
the truth (every GraphQL-backed `gh` call failed until exactly that resetAt).
#1040's guard read ONLY the REST bucket, so it stayed green (100 %), never
backed off, never alerted. These tests pin: the incident pair classifies
graphql as exhausted (0 %, backoff 60) with a once-per-episode alert; both
sources agreeing leaves the numbers unchanged; the object probe erroring falls
back to the REST reading with no alert flap; the object's resetAt wins the
reset column; the source is tagged per row; and the extra probe rides the 60 s
refresh only (<= 1 GraphQL probe per CACHE_TTL_S, none on a cache hit).
"""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_gh_rate  # noqa: E402

# The incident's authoritative reset (GraphQL object resetAt), as epoch.
_RESET_AT = "2026-09-16T18:30:01Z"
_RESET_EPOCH = int(datetime.datetime(2026, 9, 16, 18, 30, 1,
                                     tzinfo=datetime.timezone.utc).timestamp())


def _rest_body(core_rem, gql_rem, limit=5000,
               core_reset=1_800_000_000, gql_reset=1_700_000_000):
    """A `gh api rate_limit` REST response body."""
    return json.dumps({"resources": {
        "core": {"limit": limit, "remaining": core_rem, "reset": core_reset},
        "graphql": {"limit": limit, "remaining": gql_rem, "reset": gql_reset},
    }})


def _gql_body(remaining, limit=5000, reset_at=_RESET_AT):
    """A `gh api graphql '{ rateLimit { … } }'` object response body."""
    return json.dumps({"data": {"rateLimit": {
        "limit": limit, "remaining": remaining,
        "resetAt": reset_at, "used": limit - remaining}}})


class _PairRun:
    """subprocess.run stand-in answering `gh api rate_limit` with a REST body
    and `gh api graphql …` with a GraphQL rateLimit-object body, counting each
    kind separately so the value-lock (<= 1 GraphQL probe per refresh) and the
    fail-open path are both observable."""

    def __init__(self, rest_body, gql_body, rest_rc=0, gql_rc=0):
        self.rest_body = rest_body
        self.gql_body = gql_body
        self.rest_rc = rest_rc
        self.gql_rc = gql_rc
        self.rest_calls = 0
        self.gql_calls = 0

    def __call__(self, argv, **kwargs):
        is_graphql = "graphql" in argv

        class _R:
            pass

        r = _R()
        r.stderr = ""
        if is_graphql:
            self.gql_calls += 1
            r.returncode = self.gql_rc
            r.stdout = self.gql_body
        else:
            self.rest_calls += 1
            r.returncode = self.rest_rc
            r.stdout = self.rest_body
        return r


class _GhRateTmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_sp = cli_gh_rate.status_path
        self._orig_gd = cli_gh_rate.gh_rate_dir
        cli_gh_rate.gh_rate_dir = lambda: self.tmp
        cli_gh_rate.status_path = lambda: os.path.join(self.tmp, "status.json")

    def tearDown(self):
        cli_gh_rate.status_path = self._orig_sp
        cli_gh_rate.gh_rate_dir = self._orig_gd

    def _read(self, run, now=1000.0, force=True):
        return cli_gh_rate.read_status(now=now, run=run, real_gh="/usr/bin/gh",
                                       force=force)


class TestIncidentPair(_GhRateTmpCase):
    def test_graphql_exhausted_object_wins(self):
        # REST says graphql 100 % (5000/5000); the object says 0 % (0/5000).
        run = _PairRun(_rest_body(4000, 5000), _gql_body(0))
        st = self._read(run)
        # The LOWER (object) graphql reading drives pct + backoff.
        self.assertEqual(cli_gh_rate.remaining_pct("graphql", st), 0.0)
        self.assertEqual(cli_gh_rate.backoff_seconds("graphql", st),
                         cli_gh_rate.BACKOFF_CAP_S)      # 60
        # core stays REST (80 %) — never touched by the object read.
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("core", st), 80.0)
        # The GraphQL object's resetAt wins the graphql reset column.
        self.assertEqual(st["resources"]["graphql"]["reset"], _RESET_EPOCH)
        # Source tag records where the winning graphql reading came from.
        self.assertEqual(st["resources"]["graphql"].get("source"),
                         "graphql-object")

    def test_second_consecutive_low_fires_one_alert(self):
        run = _PairRun(_rest_body(4000, 5000), _gql_body(0))
        st1 = self._read(run, now=1000.0)
        self.assertEqual(cli_gh_rate.pending_alerts(st1), [])   # 1st low, debounce
        st2 = self._read(run, now=1100.0)
        self.assertIn("graphql", cli_gh_rate.pending_alerts(st2))  # 2nd -> ALERT
        st3 = self._read(run, now=1200.0)
        self.assertEqual(cli_gh_rate.pending_alerts(st3), [])   # latched, no re-fire


class TestBothAgree(_GhRateTmpCase):
    def test_numbers_unchanged_when_sources_agree(self):
        # Both report graphql 750/5000 (15 %). The merge must be a no-op on pct
        # and on the (non-capped) backoff band the REST-only guard produced.
        run = _PairRun(_rest_body(4000, 750), _gql_body(750))
        st = self._read(run)
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("graphql", st), 15.0)
        # Same backoff the REST-only guard produced for 15 %.
        self.assertEqual(cli_gh_rate.backoff_seconds("graphql", st), 30)


class TestObjectProbeError(_GhRateTmpCase):
    def test_object_error_falls_back_to_rest_no_flap(self):
        # The object probe fails (rc != 0) — the guard must use the REST reading
        # and NOT invent an exhaustion (no alert flap).
        run = _PairRun(_rest_body(4000, 4000), _gql_body(0), gql_rc=1)
        st = self._read(run)
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("graphql", st), 80.0)
        self.assertEqual(st["resources"]["graphql"].get("source"), "rest")
        self.assertEqual(cli_gh_rate.pending_alerts(st), [])
        # A REST reset survives when the object is unavailable.
        self.assertEqual(st["resources"]["graphql"]["reset"], 1_700_000_000)

    def test_object_garbage_body_falls_back_to_rest(self):
        run = _PairRun(_rest_body(4000, 4000), "not json at all {[")
        st = self._read(run)
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("graphql", st), 80.0)
        self.assertEqual(st["resources"]["graphql"].get("source"), "rest")


class TestAlertLatchNoFlapOnObjectError(_GhRateTmpCase):
    """#1052 review MAJOR-1: a transient object-probe error during a real
    exhaustion makes the graphql reading fall back to the REST bucket, which
    LIES HIGH — that must NOT clear the once-per-episode alert latch and re-fire
    it once the object recovers. RED against the first-cut #1052 code (which let
    the REST-fallback 100 %% reading clear the latch)."""

    def test_object_error_does_not_clear_the_latch_or_reflap(self):
        # 1) Two consecutive authoritative object-low reads -> the alert fires ONCE.
        low = _PairRun(_rest_body(4000, 5000), _gql_body(0))
        self._read(low, now=1000.0)
        st2 = self._read(_PairRun(_rest_body(4000, 5000), _gql_body(0)), now=1100.0)
        self.assertIn("graphql", cli_gh_rate.pending_alerts(st2))
        # 2) The object probe errors while REST reports a (misleading) healthy
        #    100 %% — the latch must HOLD, not clear.
        st3 = self._read(
            _PairRun(_rest_body(4000, 5000), _gql_body(0), gql_rc=1), now=1200.0)
        self.assertEqual(st3["resources"]["graphql"].get("source"), "rest")
        self.assertTrue(st3["alert"]["graphql"]["alerted"],
                        "a REST-fallback high reading must NOT clear the latch")
        # 3) The object recovers (still exhausted) — NO new alert (still latched).
        st4 = self._read(_PairRun(_rest_body(4000, 5000), _gql_body(0)), now=1300.0)
        self.assertEqual(cli_gh_rate.pending_alerts(st4), [],
                         "the latch must not re-fire after an object-error blip")


class TestSymmetryRestLower(_GhRateTmpCase):
    def test_rest_lower_keeps_rest_pct_object_reset_wins(self):
        # The reverse of the incident: REST low (2 %), object high (100 %).
        # Lower-pct wins -> REST pct; the object's resetAt STILL wins the column.
        run = _PairRun(_rest_body(4000, 100), _gql_body(5000))
        st = self._read(run)
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("graphql", st), 2.0)
        self.assertEqual(st["resources"]["graphql"].get("source"), "rest")
        self.assertEqual(st["resources"]["graphql"]["reset"], _RESET_EPOCH)


class TestValueLockOneProbePerTTL(_GhRateTmpCase):
    def test_at_most_one_graphql_probe_per_cache_ttl(self):
        run = _PairRun(_rest_body(4000, 4000), _gql_body(4000))
        # First read: one REST fetch + exactly one GraphQL probe.
        self._read(run, now=1000.0, force=False)
        self.assertEqual(run.rest_calls, 1)
        self.assertEqual(run.gql_calls, 1)
        # A cache hit within TTL spends NOTHING (no probe of either kind).
        self._read(run, now=1000.0 + cli_gh_rate.CACHE_TTL_S - 1, force=False)
        self.assertEqual(run.gql_calls, 1)
        self.assertEqual(run.rest_calls, 1)
        # After the TTL a refresh spends exactly one more of each.
        self._read(run, now=1000.0 + cli_gh_rate.CACHE_TTL_S + 1, force=False)
        self.assertEqual(run.rest_calls, 2)
        self.assertEqual(run.gql_calls, 2)


class TestCmdGhRatePrintsSource(_GhRateTmpCase):
    def test_source_printed_per_row(self):
        # Seed a fresh cache with the merged incident status, then print with
        # --no-refresh so cmd_gh_rate spends no gh call.
        run = _PairRun(_rest_body(4000, 5000), _gql_body(0))
        self._read(run, now=2000.0)   # writes the merged status to the cache

        class _Args:
            json = False
            no_refresh = True

        import time
        # Freeze "now" inside cmd_gh_rate's freshness check by making the cache
        # look fresh: it was written at now=2000.0 with a real fetched_at, but
        # _cache_is_fresh uses time.time(); rewrite fetched_at to a real recent
        # stamp so no refresh is attempted.
        path = cli_gh_rate.status_path()
        data = json.load(open(path, encoding="utf-8"))
        data["fetched_at"] = time.time()
        json.dump(data, open(path, "w", encoding="utf-8"))

        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_gh_rate.cmd_gh_rate(_Args())
        out = buf.getvalue()
        # The graphql row names its authoritative source; the core row is REST.
        self.assertRegex(out, r"graphql.*graphql-object")
        self.assertRegex(out, r"core.*rest")


if __name__ == "__main__":
    unittest.main()
