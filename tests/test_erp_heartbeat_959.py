"""#959 — watchdog Job 51: the erp-test box heartbeat.

Each subdev stream account's OWN watchdog, while that stream's Claude is alive,
calls odoo-erp's contracted `scripts/dev-box-heartbeat-remote.sh <stream>` to
extend the box's TTL. The leaf `watchdog/erp_heartbeat.py` gates on box-class
(shared-stream) + authority (reduced) + liveness + a resolvable script, then
calls the wrapper and journals its one stdout line + records `state`. run_once
wires it as a cadence-gated (10-min `_sweep_due`) registry job, enabled off by
default (hermetic for unit tests; cmd_watchdog turns it on in production).

Every seam is injectable, so NOTHING here spawns a real ssh/subprocess.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd                      # noqa: E402
from watchdog import erp_heartbeat as eh   # noqa: E402


# --------------------------------------------------------------------------- #
# A recording call_fn: records (script, user, timeout) and returns a fixed
# (rc, line); a subclass raises TimeoutExpired to exercise the timeout branch.
# --------------------------------------------------------------------------- #
class _Call:
    def __init__(self, rc=0, line=""):
        self.rc, self.line, self.calls = rc, line, []

    def __call__(self, script, user, timeout):
        self.calls.append((script, user, timeout))
        return self.rc, self.line


def _run_job(call=None, *, box="shared-stream", authority="fork-no-merge",
             cwds=("/home/montalu1/devel/odoo/odoo-slovnormal",),
             script="/home/montalu1/devel/odoo/odoo-slovnormal/scripts/"
                    "dev-box-heartbeat-remote.sh",
             user="montalu1", now=1000.0, dry_run=False, state=None):
    """Drive the leaf with every gate injected OPEN unless overridden."""
    if call is None:
        call = _Call(0, "heartbeat stream=%s box=erp-test-%s relay=OK "
                        "expires_at=2026-09-22T12:30:00" % (user, user))
    st = {} if state is None else state
    logs = eh.run_erp_heartbeat(
        now, st,
        run=lambda *a, **k: "",
        dry_run=dry_run,
        box_class_fn=lambda: box,
        authority_fn=lambda: authority,
        user_fn=lambda: user,
        live_claude_fn=lambda: list(cwds),
        find_script_fn=lambda _cwds: script,
        call_fn=call,
    )
    return logs, st, call


class TestGate(unittest.TestCase):
    def test_full_authority_box_does_not_call(self):
        call = _Call()
        logs, st, call = _run_job(call, authority="full")
        self.assertEqual(call.calls, [])
        self.assertNotIn("erp_heartbeat", st)
        self.assertTrue(any("skip" in ln and "full-authority" in ln for ln in logs))

    def test_non_shared_stream_box_does_not_call(self):
        for box in ("workstation", "controller", None):
            with self.subTest(box=box):
                call = _Call()
                logs, st, call = _run_job(call, box=box)
                self.assertEqual(call.calls, [])
                self.assertNotIn("erp_heartbeat", st)
                self.assertTrue(any("skip" in ln and "shared-stream" in ln
                                    for ln in logs))

    def test_reduced_authority_shared_stream_calls(self):
        for authority in ("fork-no-merge", "branch-merge"):
            with self.subTest(authority=authority):
                call = _Call()
                logs, st, call = _run_job(call, authority=authority)
                self.assertEqual(len(call.calls), 1)


class TestLiveness(unittest.TestCase):
    def test_no_live_claude_skips_without_calling(self):
        call = _Call()
        logs, st, call = _run_job(call, cwds=())
        self.assertEqual(call.calls, [])
        self.assertNotIn("erp_heartbeat", st)
        self.assertTrue(any("skip" in ln and "no live claude" in ln
                            and "montalu1" in ln for ln in logs))


class TestScriptResolution(unittest.TestCase):
    def test_no_script_resolved_skips_without_calling(self):
        call = _Call()
        # a live cwd exists, but find_script resolves nothing
        logs = eh.run_erp_heartbeat(
            1000.0, {}, run=lambda *a, **k: "",
            box_class_fn=lambda: "shared-stream",
            authority_fn=lambda: "fork-no-merge",
            user_fn=lambda: "montalu1",
            live_claude_fn=lambda: ["/home/montalu1/x"],
            find_script_fn=lambda _c: None,
            call_fn=call)
        self.assertEqual(call.calls, [])
        self.assertTrue(any("skip" in ln and "script" in ln for ln in logs))

    def test_default_find_script_resolves_odoo_erp_checkout(self):
        with TemporaryDirectory() as d:
            top = Path(d) / "odoo-slovnormal"
            (top / "scripts").mkdir(parents=True)
            script = top / "scripts" / "dev-box-heartbeat-remote.sh"
            script.write_text("#!/usr/bin/env bash\n")
            cwd = str(top / "addons")

            def run(argv, timeout=8):
                if argv[:3] == ["git", "-C", cwd] and "rev-parse" in argv:
                    return str(top) + "\n"
                if argv[:3] == ["git", "-C", str(top)] and "remote" in argv:
                    return "git@github.com:zbynekdrlik/odoo-erp.git\n"
                return ""

            found = eh._default_find_script([cwd], run)
            self.assertEqual(found, str(script))

    def test_default_find_script_non_odoo_cwd_falls_through_to_none(self):
        # a live cwd whose origin is NOT odoo-erp and no ~/devel/odoo/* match →
        # None (never a guess).
        def run(argv, timeout=8):
            if "rev-parse" in argv:
                return "/home/montalu1/devel/some-other-repo\n"
            if "remote" in argv:
                return "git@github.com:zbynekdrlik/airuleset.git\n"
            return ""

        with mock.patch.object(eh, "_ODOO_CHECKOUT_GLOB",
                               "/nonexistent-erp-heartbeat-probe/*/"):
            self.assertIsNone(
                eh._default_find_script(["/home/montalu1/devel/some-other-repo"],
                                        run))

    def test_slug_matches_url_variants(self):
        for origin in ("git@github.com:zbynekdrlik/odoo-erp.git",
                       "https://github.com/zbynekdrlik/odoo-erp.git",
                       "https://github.com/zbynekdrlik/odoo-erp"):
            self.assertTrue(eh._slug_matches(origin, eh.ODOO_ERP_SLUG), origin)
        for origin in ("git@github.com:zbynekdrlik/airuleset.git", "", "x"):
            self.assertFalse(eh._slug_matches(origin, eh.ODOO_ERP_SLUG), origin)


class TestReturnCodes(unittest.TestCase):
    def _drive(self, rc, line="the-wrapper-line"):
        call = _Call(rc, line)
        return _run_job(call, now=4242.0)

    def test_rc0_ok(self):
        logs, st, _ = self._drive(0, "heartbeat stream=montalu1 relay=OK "
                                     "expires_at=2026-09-22T12:30:00")
        self.assertTrue(any("ok rc=0" in ln for ln in logs))
        self.assertTrue(any("expires_at=" in ln for ln in logs))  # line passed 1:1
        self.assertEqual(st["erp_heartbeat"]["rc"], 0)
        self.assertEqual(st["erp_heartbeat"]["ts"], 4242.0)
        self.assertIn("expires_at=", st["erp_heartbeat"]["line"])

    def test_rc2_retry_no_alarm(self):
        logs, st, _ = self._drive(2, "noop reason=relay-error")
        self.assertTrue(any("retry rc=2" in ln for ln in logs))
        self.assertFalse(any("ALARM" in ln for ln in logs))
        self.assertEqual(st["erp_heartbeat"]["rc"], 2)

    def test_rc3_alarm(self):
        logs, st, _ = self._drive(3, "auth refused")
        self.assertTrue(any("ALARM" in ln and "rc=3" in ln for ln in logs))
        self.assertEqual(st["erp_heartbeat"]["rc"], 3)

    def test_other_rc_is_error_retry(self):
        logs, st, _ = self._drive(5, "weird")
        self.assertTrue(any("error rc=5" in ln for ln in logs))
        self.assertEqual(st["erp_heartbeat"]["rc"], 5)

    def test_timeout_is_error(self):
        def boom(script, user, timeout):
            raise subprocess.TimeoutExpired(cmd="bash", timeout=timeout)
        logs, st, _ = _run_job(boom, now=7.0)
        self.assertTrue(any("timeout" in ln for ln in logs))
        self.assertEqual(st["erp_heartbeat"]["rc"], eh._TIMEOUT_RC)
        self.assertEqual(st["erp_heartbeat"]["ts"], 7.0)


class TestDryRun(unittest.TestCase):
    def test_dry_run_calls_nothing_and_persists_nothing(self):
        call = _Call()
        logs, st, call = _run_job(call, dry_run=True)
        self.assertEqual(call.calls, [])
        self.assertNotIn("erp_heartbeat", st)
        self.assertTrue(any("would call" in ln for ln in logs))


# --------------------------------------------------------------------------- #
# run_once wiring: cadence via _sweep_due + per-sweep isolation.
# --------------------------------------------------------------------------- #
def _drive_run_once(now, state_path, recorder):
    with mock.patch.object(wd, "list_claude_panes", lambda *a, **k: []), \
         mock.patch.object(wd, "_owner_disabled", lambda kind: False), \
         mock.patch.object(wd.erp_heartbeat, "run_erp_heartbeat", recorder):
        return wd.run_once(
            now=now, dry_run=False,
            run=lambda *a, **k: "",
            send_fn=lambda *a, **k: None,
            projects_dir=Path(state_path).parent / "proj",
            state_path=state_path,
            erp_heartbeat_enabled=True,
        )


class TestRunOnceWiring(unittest.TestCase):
    def test_cadence_two_ticks_inside_10min_call_once(self):
        seen = []

        def rec(now, state, **k):
            seen.append(now)
            return ["erp-heartbeat: (recorder)"]

        with TemporaryDirectory() as d:
            sp = str(Path(d) / "state.json")
            _drive_run_once(1000.0, sp, rec)          # due (first)
            _drive_run_once(1300.0, sp, rec)          # 300s < 600 → not due
            self.assertEqual(seen, [1000.0])
            _drive_run_once(1700.0, sp, rec)          # 700s > 600 → due again
            self.assertEqual(seen, [1000.0, 1700.0])

    def test_disabled_by_default_does_not_run(self):
        seen = []

        def rec(now, state, **k):
            seen.append(now)
            return []

        with TemporaryDirectory() as d, \
             mock.patch.object(wd, "list_claude_panes", lambda *a, **k: []), \
             mock.patch.object(wd, "_owner_disabled", lambda kind: False), \
             mock.patch.object(wd.erp_heartbeat, "run_erp_heartbeat", rec):
            wd.run_once(now=1000.0, dry_run=False, run=lambda *a, **k: "",
                        send_fn=lambda *a, **k: None,
                        projects_dir=Path(d) / "proj",
                        state_path=str(Path(d) / "state.json"))
            # erp_heartbeat_enabled defaults False → never invoked.
            self.assertEqual(seen, [])

    def test_a_raise_inside_the_job_never_breaks_run_once(self):
        def boom(now, state, **k):
            raise RuntimeError("erp boom")

        with TemporaryDirectory() as d:
            logs = _drive_run_once(1000.0, str(Path(d) / "state.json"), boom)
        self.assertTrue(any("erp-heartbeat error" in ln for ln in logs),
                        "run_once must log the isolated job error, not crash")


class TestConstants(unittest.TestCase):
    def test_cadence_is_ten_minutes(self):
        self.assertEqual(eh.ERP_HEARTBEAT_INTERVAL_S, 600)

    def test_registry_label_present(self):
        import inspect
        import re
        labels = re.findall(r'_add\(\s*"([^"]+)"', inspect.getsource(wd.run_once))
        self.assertIn("erp_heartbeat", labels)


if __name__ == "__main__":
    unittest.main()
