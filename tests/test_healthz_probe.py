"""presenter /healthz.ai EXTERNAL health-check — watchdog job 47 (#1005).

presenter AI login was dead 14 days on the SNV prod and nobody found out — the
only signals (journal WARN, operator chip, deploy ``::warning::``) never left the
box. presenter#760 added ``/healthz.ai = {connected, error, model}`` precisely so
an EXTERNAL watchdog could detect the outage; this is that watchdog.

Safety-critical invariants under test (the #1005 design):
  (a) two consecutive ``connected:false`` samples → EXACTLY ONE owner alert; a
      third → none; recovery → one "AI back" line;
  (b) a single ``connected:false`` sample → nothing (arming, no alert);
  (c) an HTTP error / timeout / non-JSON / bad-schema read → journal
      ``unmeasurable`` only, NEVER an alert (fail-safe: unmeasurable ≠ down,
      and the down-streak state is untouched);
  (d) a box with NO declaration → one skip line, and nothing else (no fetch,
      no send);
  (e) a NEW error text while still down → a fresh re-alert.

A FAKE ``fetch`` seam (never the network) + a recording ``send_fn`` give
deterministic control over every branch. The compose functions, the fleet
declaration accessor/validator, and the real dev2 declaration are locked too.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog.healthz_probe as hp      # noqa: E402
import notify                            # noqa: E402
import cli_fleet                         # noqa: E402

NOW = 1786000000.0
FIVE_MIN = 300.0

SNV = {"name": "presenter-snv", "url": "http://10.77.9.205/healthz",
       "path": ".ai", "owner": "zbynek"}


def ok_body(connected=True, error=None, model="google/gemini-3.8-flash"):
    import json
    return json.dumps({"ai": {"connected": connected, "error": error,
                              "model": model},
                       "channel": "release", "status": "ok",
                       "version": "0.4.280"})


def fetch_returning(status, body):
    """A deterministic ``fetch(url) -> (status, body)`` seam."""
    def f(url, timeout=None):
        return status, body
    return f


def fetch_raising(exc):
    def f(url, timeout=None):
        raise exc
    return f


class Recorder:
    """A recording ``send_fn`` matching notify.send's kwargs shape."""
    def __init__(self):
        self.calls = []

    def __call__(self, body, owner=None, dedup_key=None, dry_run=False, **kw):
        self.calls.append({"body": body, "owner": owner,
                           "dedup_key": dedup_key, "dry_run": dry_run})
        return "sent"


def run(state, fetch, send, probes=(SNV,), now=NOW, dry_run=False):
    # each call is a distinct "sample" — advance `now` past the cadence so a
    # direct-call test never trips the internal sweep gate.
    return hp.healthz_probe_job(now, state, list(probes),
                                fetch=fetch, send_fn=send, dry_run=dry_run,
                                persist=None)


class TestDedupStateMachine(unittest.TestCase):
    def test_two_consecutive_down_fire_exactly_one_alert_then_silent(self):
        state = {}
        send = Recorder()
        down = fetch_returning(200, ok_body(connected=False,
                                            error="invalid_grant"))
        # (b) one false sample → nothing
        logs1 = run(state, down, send, now=NOW)
        self.assertEqual(len(send.calls), 0, "one false sample must not alert")
        self.assertTrue(any("1/2" in ln and "arming" in ln for ln in logs1), logs1)
        # (a) second consecutive false → EXACTLY ONE alert
        logs2 = run(state, down, send, now=NOW + FIVE_MIN)
        self.assertEqual(len(send.calls), 1, "2nd consecutive false → one alert")
        self.assertTrue(any("2/2" in ln and "alerted" in ln for ln in logs2), logs2)
        self.assertIn("presenter-snv", send.calls[0]["body"])
        self.assertEqual(send.calls[0]["owner"], "zbynek")
        self.assertTrue(send.calls[0]["dedup_key"].startswith("healthz:"))
        # (a) third false → NO new alert
        run(state, down, send, now=NOW + 2 * FIVE_MIN)
        self.assertEqual(len(send.calls), 1, "third down must not re-alert")

    def test_recovery_sends_one_back_line(self):
        state = {}
        send = Recorder()
        down = fetch_returning(200, ok_body(connected=False, error="e"))
        run(state, down, send, now=NOW)
        run(state, down, send, now=NOW + FIVE_MIN)          # armed + alerted
        self.assertEqual(len(send.calls), 1)
        up = fetch_returning(200, ok_body(connected=True))
        logs = run(state, up, send, now=NOW + 2 * FIVE_MIN)
        self.assertEqual(len(send.calls), 2, "recovery → one back line")
        self.assertTrue(any("recover" in ln.lower() or "connected=true" in ln
                            for ln in logs), logs)
        # and after recovery a fresh down streak must arm from scratch again
        run(state, down, send, now=NOW + 3 * FIVE_MIN)      # 1/2, no alert
        self.assertEqual(len(send.calls), 2)

    def test_new_error_text_while_down_re_alerts(self):
        state = {}
        send = Recorder()
        run(state, fetch_returning(200, ok_body(connected=False, error="err-A")),
            send, now=NOW)
        run(state, fetch_returning(200, ok_body(connected=False, error="err-A")),
            send, now=NOW + FIVE_MIN)                        # alert #1
        self.assertEqual(len(send.calls), 1)
        logs = run(state, fetch_returning(200,
                   ok_body(connected=False, error="err-B")),
                   send, now=NOW + 2 * FIVE_MIN)             # new error → re-alert
        self.assertEqual(len(send.calls), 2, "changed error text → re-alert")
        self.assertIn("err-B", send.calls[1]["body"])
        self.assertTrue(any("re-alert" in ln for ln in logs), logs)


class TestUnmeasurableNeverAlerts(unittest.TestCase):
    def test_timeout_is_unmeasurable_not_down(self):
        state = {}
        send = Recorder()
        logs = run(state, fetch_raising(TimeoutError("timed out")), send)
        self.assertEqual(len(send.calls), 0)
        self.assertTrue(any("unmeasurable" in ln for ln in logs), logs)

    def test_http_error_status_is_unmeasurable(self):
        state = {}
        send = Recorder()
        logs = run(state, fetch_returning(503, "Service Unavailable"), send)
        self.assertEqual(len(send.calls), 0)
        self.assertTrue(any("unmeasurable" in ln for ln in logs), logs)

    def test_non_json_is_unmeasurable(self):
        state = {}
        send = Recorder()
        logs = run(state, fetch_returning(200, "<html>not json</html>"), send)
        self.assertEqual(len(send.calls), 0)
        self.assertTrue(any("unmeasurable" in ln for ln in logs), logs)

    def test_missing_ai_field_is_unmeasurable(self):
        state = {}
        send = Recorder()
        import json
        logs = run(state, fetch_returning(200, json.dumps({"status": "ok"})),
                   send)
        self.assertEqual(len(send.calls), 0)
        self.assertTrue(any("unmeasurable" in ln for ln in logs), logs)

    def test_unmeasurable_between_downs_does_not_reset_or_advance_streak(self):
        # fail-safe: an unmeasurable sample is "no sample" — it neither counts
        # as a down (advancing toward the alert) nor clears a partial streak.
        state = {}
        send = Recorder()
        down = fetch_returning(200, ok_body(connected=False, error="e"))
        run(state, down, send, now=NOW)                       # 1/2
        run(state, fetch_raising(OSError("net")), send, now=NOW + FIVE_MIN)
        self.assertEqual(len(send.calls), 0, "unmeasurable must not alert")
        run(state, down, send, now=NOW + 2 * FIVE_MIN)        # now 2/2 → alert
        self.assertEqual(len(send.calls), 1,
                         "streak preserved across an unmeasurable gap")


class TestNoDeclaration(unittest.TestCase):
    def test_no_probes_logs_one_skip_and_does_nothing(self):
        state = {}
        send = Recorder()
        called = {"n": 0}

        def fetch(url, timeout=None):
            called["n"] += 1
            return 200, ok_body()

        logs = hp.healthz_probe_job(NOW, state, [], fetch=fetch, send_fn=send)
        self.assertEqual(called["n"], 0, "no fetch on an undeclared box")
        self.assertEqual(len(send.calls), 0)
        skip = [ln for ln in logs if "skip" in ln]
        self.assertEqual(len(skip), 1, "exactly one skip line: %r" % logs)


class TestDryRun(unittest.TestCase):
    def test_dry_run_never_sends(self):
        state = {}
        send = Recorder()
        down = fetch_returning(200, ok_body(connected=False, error="e"))
        run(state, down, send, now=NOW, dry_run=True)
        run(state, down, send, now=NOW + FIVE_MIN, dry_run=True)
        self.assertEqual(len(send.calls), 0, "dry_run must not send")


class TestComposeFunctions(unittest.TestCase):
    def test_compose_alert_is_slovak_one_line_with_host_and_error(self):
        body = notify.compose_healthz_alert("presenter-snv",
                                            "invalid_grant", "2026-09-14 03:00")
        self.assertNotIn("\n", body.strip("\n"),
                         "alert is one line (no interior newline)")
        self.assertIn("presenter-snv", body)
        self.assertIn("invalid_grant", body)
        self.assertIn("2026-09-14 03:00", body)

    def test_compose_alert_handles_null_error(self):
        body = notify.compose_healthz_alert("presenter-pp", None, "t")
        self.assertIn("presenter-pp", body)

    def test_compose_recovery_names_the_host(self):
        body = notify.compose_healthz_recovery("presenter-snv")
        self.assertIn("presenter-snv", body)


class TestFleetDeclaration(unittest.TestCase):
    def test_validate_accepts_a_good_probe(self):
        self.assertEqual(cli_fleet.validate_health_probes([
            {"name": "presenter-snv", "url": "http://10.77.9.205/healthz",
             "path": ".ai", "owner": "zbynek"}]), [])

    def test_validate_rejects_bad_shapes(self):
        errs = cli_fleet.validate_health_probes([
            {"name": "a b", "url": "ftp://nope", "path": ".ai"}])
        self.assertTrue(errs, "bad name + non-http url must be rejected")

    def test_validate_rejects_duplicate_names(self):
        errs = cli_fleet.validate_health_probes([
            {"name": "x", "url": "http://a/healthz", "path": ".ai"},
            {"name": "x", "url": "http://b/healthz", "path": ".ai"}])
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_validate_not_a_list(self):
        self.assertTrue(cli_fleet.validate_health_probes("nope"))

    def test_box_health_probes_scopes_to_the_user(self):
        # dev2 (newlevel, hostname dev2) is the box that reaches both prod hosts
        # (STEP-0). Scoping is by user AND hostname (see the hostname-scoped test).
        probes = cli_fleet.box_health_probes("newlevel", hostname="dev2")
        names = [p["name"] for p in probes]
        self.assertIn("presenter-snv", names)
        self.assertIn("presenter-pp", names)
        # a non-declaring account inherits nothing.
        self.assertEqual(cli_fleet.box_health_probes("gatekeeper", hostname="gk"), [])
        self.assertEqual(cli_fleet.box_health_probes("", hostname="dev2"), [])

    def test_box_health_probes_is_hostname_scoped_for_shared_user(self):
        # #1005 must-fix: the unix user "newlevel" is SHARED by dev2, dev1 AND
        # spinbike-vps (all three are REMOTE_HOSTS entries with user newlevel),
        # and every managed box runs the api-watchdog. The health_probes
        # declaration lives on dev2 ONLY (the STEP-0 probe proved dev2 is the one
        # box reaching both prods). A by-USER accessor returns dev2's probes on
        # EVERY newlevel box → dev1 would double-alert the owner on a PP outage
        # (PP is tailscale-reachable from dev1) and dev1+spinbike would issue
        # needless 5-min prod GETs. So the declaration MUST be scoped to the
        # declaring box's OWN hostname: dev2 gets the probes; dev1/spinbike get [].
        self.assertTrue(cli_fleet.box_health_probes("newlevel", hostname="dev2"))
        self.assertEqual(cli_fleet.box_health_probes("newlevel", hostname="dev1"), [])
        self.assertEqual(
            cli_fleet.box_health_probes("newlevel", hostname="spinbike-vps"), [])
        # an FQDN / trailing domain still matches on the first label.
        self.assertTrue(
            cli_fleet.box_health_probes("newlevel", hostname="dev2.local"))

    def test_dev2_declaration_validates(self):
        self.assertEqual(
            cli_fleet.validate_health_probes(
                cli_fleet.box_health_probes("newlevel", hostname="dev2")), [])


if __name__ == "__main__":
    unittest.main()
