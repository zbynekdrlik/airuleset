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

    def test_unknown_authority_on_shared_stream_proceeds(self):
        # fail-safe: authority_fn returning None (an error / unmapped user) on a
        # shared-stream box is treated as reduced and PROCEEDS (only == "full"
        # skips) — a shared-stream box only ever hosts reduced stream accounts.
        call = _Call()
        logs, st, call = _run_job(call, authority=None)
        self.assertEqual(len(call.calls), 1)


class TestCmdlineClaudeCli(unittest.TestCase):
    def test_matches_both_fleet_launch_shapes(self):
        for cmd in ("claude --resume",
                    "/home/montalu1/.local/bin/claude",
                    "node /home/montalu1/.npm/claude-code/cli.js",
                    "node /home/montalu1/.local/share/x/claude",
                    "nodejs /opt/claude-code/cli.js --print"):
            self.assertTrue(eh._cmdline_is_claude_cli(cmd), cmd)

    def test_rejects_non_cli_processes_mentioning_claude(self):
        for cmd in ("python3 /home/montalu1/claude-notes/gen.py",
                    "vim /home/montalu1/claude.md",
                    "grep -r claude /home/montalu1",
                    "node /home/montalu1/mcp-server/index.js",
                    "", "   "):
            self.assertFalse(eh._cmdline_is_claude_cli(cmd), cmd)


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

    def test_default_find_script_cwd_is_the_toplevel_root(self):
        # #959 fix-forward 2 (RED): the live-stream case — the Claude cwd IS the
        # git toplevel of an odoo-erp checkout with the script present (live
        # montalu1: cwd == /home/montalu1/devel/odoo/odoo-slovnormal). Must
        # resolve the script; returned None before the seen_cwds/checked_tops
        # split conflated "cwd already de-duped" with "toplevel already checked".
        with TemporaryDirectory() as d:
            top = Path(d) / "odoo-slovnormal"
            (top / "scripts").mkdir(parents=True)
            script = top / "scripts" / "dev-box-heartbeat-remote.sh"
            script.write_text("#!/usr/bin/env bash\n")
            root = str(top)  # cwd == toplevel

            def run(argv, timeout=8):
                if argv[:3] == ["git", "-C", root] and "rev-parse" in argv:
                    return root + "\n"
                if argv[:3] == ["git", "-C", root] and "remote" in argv:
                    return "git@github.com:zbynekdrlik/odoo-erp.git\n"
                return ""

            self.assertEqual(eh._default_find_script([root], run), str(script))

    def test_default_find_script_two_cwds_same_repo_checks_top_once(self):
        # De-duplication survives the fix: two live cwds in the SAME repo resolve
        # to one toplevel handed to _script_at exactly once (one `remote get-url`)
        # — no repeated git calls against the #1055-P2 subprocess budget. Script
        # ABSENT so the loop visits BOTH cwds (an early return on the first would
        # mask the dedup), glob pinned to nothing → returns None.
        remote_calls = []
        top = "/home/montalu1/devel/odoo/odoo-slovnormal"

        def run(argv, timeout=8):
            if "rev-parse" in argv:
                return top + "\n"                    # both cwds → same top
            if "remote" in argv:
                remote_calls.append(argv[2])         # the -C target
                return "git@github.com:zbynekdrlik/odoo-erp.git\n"
            return ""

        with mock.patch.object(eh, "_ODOO_CHECKOUT_GLOB",
                               "/nonexistent-erp-heartbeat-probe/*/"):
            found = eh._default_find_script(
                [top + "/addons", top + "/server"], run)
        self.assertIsNone(found)                     # script file never created
        self.assertEqual(remote_calls, [top])        # top checked exactly once

    def test_default_find_script_non_odoo_cwd_falls_through_to_glob(self):
        # A live cwd whose repo is NOT odoo-erp; the ~/devel/odoo/* glob DOES
        # hold an odoo-erp checkout with the script → the glob fallback resolves
        # it, and a glob dir already checked as a toplevel (the non-odoo repo)
        # is skipped via checked_tops, not wrongly re-checked.
        with TemporaryDirectory() as d:
            other = Path(d) / "airuleset"
            other.mkdir()
            erp = Path(d) / "odoo-erp"
            (erp / "scripts").mkdir(parents=True)
            script = erp / "scripts" / "dev-box-heartbeat-remote.sh"
            script.write_text("#!/usr/bin/env bash\n")

            def run(argv, timeout=8):
                target = argv[2] if len(argv) > 2 else ""
                if "rev-parse" in argv:
                    return str(other) + "\n"          # cwd's toplevel = non-odoo
                if "remote" in argv and target == str(other):
                    return "git@github.com:zbynekdrlik/airuleset.git\n"
                if "remote" in argv and target == str(erp):
                    return "git@github.com:zbynekdrlik/odoo-erp.git\n"
                return ""

            with mock.patch.object(eh, "_ODOO_CHECKOUT_GLOB",
                                   str(Path(d) / "*/")):
                found = eh._default_find_script([str(other / "sub")], run)
            self.assertEqual(found, str(script))

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

        # Derive the ticks from the LIVE constants so the "not due" middle tick
        # is proven by the erp cadence gate, NOT masked by a calm-sweep skip
        # (#959 review NIT): the middle tick must be a FULL sweep
        # (gap > SWEEP_CALM_S) yet inside the 10-min interval (gap < INTERVAL),
        # and the third must be BOTH a full sweep since the middle AND cadence-due
        # since the first. This holds for the current 300/600; guard it explicitly.
        calm = wd.SWEEP_CALM_S
        interval = eh.ERP_HEARTBEAT_INTERVAL_S
        margin = 30.0
        assert calm + margin < interval, "test needs SWEEP_CALM_S+margin < INTERVAL"
        base = 1000.0
        mid = base + calm + margin          # FULL sweep, cadence NOT due
        third = base + 2 * calm + 2 * margin  # FULL sweep (vs mid) AND cadence due
        assert third - base > interval, "third tick must be cadence-due"

        with TemporaryDirectory() as d:
            sp = str(Path(d) / "state.json")
            _drive_run_once(base, sp, rec)            # due (first, bootstrap full)
            _drive_run_once(mid, sp, rec)             # full sweep, < interval → not due
            self.assertEqual(seen, [base])
            _drive_run_once(third, sp, rec)           # full sweep, > interval → due
            self.assertEqual(seen, [base, third])

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


# --------------------------------------------------------------------------- #
# #959 fix-forward: Job 51 must actually RUN on a busy stream account (was held
# by `hold:budget` because min_budget was the 65s ssh-fleet class while the sweep
# soft cap is 100s and earlier jobs eat 30-60s). The fix sizes the budget to the
# real one-wrapper-call cost.
# --------------------------------------------------------------------------- #
class TestBudgetSizing(unittest.TestCase):
    def test_timeout_sized_to_the_ssh_connect_plus_relay(self):
        # The wrapper's ssh uses ConnectTimeout=10; 25s covers connect + relay
        # round-trip with margin (the old 60s over-sized it ~6x).
        self.assertEqual(eh.ERP_HEARTBEAT_TIMEOUT_S, 25)

    def test_job51_min_budget_sized_to_the_wrapper_call_not_ssh_fleet(self):
        # min_budget must be the wrapper-call class (timeout + 5), NOT the 65s
        # ssh-fleet class — else Job 51 holds forever on a busy account.
        self.assertLessEqual(wd._BUDGET_MIN_ERP_HEARTBEAT_S, 30)
        self.assertGreaterEqual(wd._BUDGET_MIN_ERP_HEARTBEAT_S,
                                eh.ERP_HEARTBEAT_TIMEOUT_S + 5)
        # and it must NOT be the ssh-fleet class it used to alias.
        self.assertLess(wd._BUDGET_MIN_ERP_HEARTBEAT_S,
                        wd._BUDGET_MIN_SSH_FLEET_S)

    def test_call_receives_the_25s_timeout(self):
        call = _Call(0, "heartbeat relay=OK")
        _run_job(call)
        self.assertEqual(len(call.calls), 1)
        self.assertEqual(call.calls[0][2], eh.ERP_HEARTBEAT_TIMEOUT_S)
        self.assertEqual(call.calls[0][2], 25)

    def test_timeout_at_25s_maps_to_timeout_rc(self):
        def boom(script, user, timeout):
            self.assertEqual(timeout, 25)
            raise subprocess.TimeoutExpired(cmd="bash", timeout=timeout)
        logs, st, _ = _run_job(boom, now=9.0)
        self.assertEqual(st["erp_heartbeat"]["rc"], eh._TIMEOUT_RC)
        self.assertTrue(any("timeout" in ln for ln in logs))


def _drive_run_once_budget(now, state_path, recorder, remaining_s):
    """Drive run_once with the sweep budget pinned so that EVERY registry job is
    evaluated with `remaining_s` seconds left. Injected `time_fn`: the FIRST read
    establishes `_sweep_start` at 0.0, every later read returns the fixed elapsed
    `SWEEP_SOFT_CAP_S - remaining_s`, so `remaining_budget_s()` == remaining_s
    throughout (no real clock, deterministic)."""
    elapsed = float(wd.SWEEP_SOFT_CAP_S - remaining_s)
    calls = {"n": 0}

    def time_fn():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else elapsed

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
            time_fn=time_fn,
        )


class TestRunOnceBudget(unittest.TestCase):
    def test_runs_the_leaf_with_40s_left_instead_of_holding(self):
        # The live montalu1 bug: ~40s left, Job 51 held (`need >=65s`). With the
        # fix (min 30) the leaf RUNS.
        seen = []

        def rec(now, state, **k):
            seen.append(now)
            return ["erp-heartbeat: (recorder)"]

        with TemporaryDirectory() as d:
            logs = _drive_run_once_budget(
                1000.0, str(Path(d) / "state.json"), rec, remaining_s=40)
        self.assertEqual(seen, [1000.0],
                         "Job 51 must RUN with 40s left, not hold:budget")
        self.assertFalse(
            any("erp_heartbeat -> hold:budget" in ln for ln in logs),
            "Job 51 must not be held at 40s left")

    def test_still_holds_when_budget_is_under_the_minimum(self):
        # The guard is not removed — under the (new) minimum it still holds.
        seen = []

        def rec(now, state, **k):
            seen.append(now)
            return []

        with TemporaryDirectory() as d:
            logs = _drive_run_once_budget(
                2000.0, str(Path(d) / "state.json"), rec, remaining_s=20)
        self.assertEqual(seen, [],
                         "Job 51 must still hold when under its minimum budget")
        self.assertTrue(
            any("erp_heartbeat -> hold:budget" in ln for ln in logs),
            "a sub-minimum budget must still journal hold:budget")


if __name__ == "__main__":
    unittest.main()


class TestCmdWatchdogWiresErpHeartbeat(unittest.TestCase):
    """The leaf is inert unless cmd_watchdog passes `erp_heartbeat_enabled`
    to run_once (run_once defaults it to False so unit tests never ssh).
    Drive the real cmd_watchdog and capture the kwarg."""

    class _Args:
        dry_run = False
        verbose = False

    def test_cmd_watchdog_enables_job_51(self):
        import airuleset
        captured = {}

        def fake_run_once(*a, **kw):
            captured.update(kw)
            return []

        with mock.patch.object(wd, "run_once", side_effect=fake_run_once):
            airuleset.cmd_watchdog(self._Args())
        self.assertIs(captured.get("erp_heartbeat_enabled"), True,
                      "cmd_watchdog must wire erp_heartbeat_enabled=True, "
                      "or Job 51 never runs on any box (#959)")
