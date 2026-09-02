"""#851 — a `"paused": "<why + date>"` REMOTE_HOSTS flag (simap1@subdev): the
owner froze this stream (customer/access dispute) until it decides. A paused
account must NEVER be auto-contacted (deploy, re-heal, soniox key delivery,
webterm access, the hourly fleet-burn/dead-fleet-alarm liveness expectation),
must be SKIPPED before any ssh with a visible line, counted in its own
"paused" bucket (never "failed"), and never affect the push's exit code.

Resume = delete the `paused` key — no other mechanism.

Deliberately OUT of scope (see the #851 design comment + cli_resource_guards.py
docstring): the resource-guards / tmux-cutover-via-gatekeeper legs are HOST-
scoped (one SHARED_STREAM_GUARD_HOSTS entry = the whole subdev box, covering
every present-and-future stream), so a per-account pause must NOT skip them —
skipping would strip cgroup-OOM protection from every OTHER active stream
sharing that box.
"""
import io
import contextlib
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_fleet  # noqa: E402


class TestPausedFlagOnSimap1(TestCase):
    """(c) lock test — simap1@subdev is paused with a non-empty reason. A
    future edit that un-pauses it (deletes the key) must touch this test
    deliberately, not silently."""

    def _simap1(self):
        entries = [h for h in airuleset.REMOTE_HOSTS if h["name"] == "simap1@subdev"]
        self.assertEqual(len(entries), 1, "simap1@subdev target missing")
        return entries[0]

    def test_simap1_is_paused_with_a_dated_reason(self):
        s = self._simap1()
        self.assertTrue(cli_fleet.is_paused(s))
        reason = cli_fleet.paused_reason(s)
        self.assertTrue(reason)
        self.assertIn("2026-09-02", reason)

    def test_is_paused_false_for_an_unflagged_entry(self):
        self.assertFalse(cli_fleet.is_paused({"name": "x", "host": "1.2.3.4"}))
        self.assertEqual(cli_fleet.paused_reason({"name": "x"}), "")


class TestDeployableHostsExcludesPaused(TestCase):
    """The single choke-point every REMOTE_HOSTS ssh path routes through
    (`_deployable_hosts`, #537's `pending` idiom) must ALSO exclude a paused
    entry — this is what protects the deploy loop, soniox leg, hourly
    fleet-burn collector (job 16 -> job 35's dead-fleet alarm default
    `hosts_fn`), `cmd_burn --host`, and `webterm_inventory()` all at once."""

    def _hosts(self):
        return [
            {"name": "live@subdev", "host": "9.9.9.1", "user": "liveu",
             "repo_path": "~/devel/airuleset"},
            {"name": "paused@subdev", "host": "9.9.9.2", "user": "pausedu",
             "repo_path": "~/devel/airuleset", "paused": "test pause reason"},
        ]

    def test_paused_entry_excluded_from_deployable_hosts(self):
        deployable = airuleset._deployable_hosts(self._hosts())
        names = {h["name"] for h in deployable}
        self.assertIn("live@subdev", names)
        self.assertNotIn("paused@subdev", names)

    def test_real_registry_excludes_simap1(self):
        # (d) dead-fleet consumer ignores paused — job 35's `hosts_fn`
        # defaults to `airuleset._deployable_hosts`, so simap1 is simply not
        # in the "expected alive" set and never dead-fleet-alarms.
        names = {h["name"] for h in airuleset._deployable_hosts()}
        self.assertNotIn("simap1@subdev", names)

    def test_webterm_inventory_excludes_paused_account(self):
        # "webterm access" (#851) is satisfied for free — webterm_inventory
        # already builds its stream-account entries from _deployable_hosts().
        import cli_webterm
        inv = cli_webterm.webterm_inventory()
        self.assertFalse(
            any("simap1" in (e.get("id") or "") or "simap1" in (e.get("user") or "")
                for e in inv),
            "a paused account must not appear in the webterm inventory")


class TestSoniozLegSkipsPaused(TestCase):
    """(b) the soniox key delivery leg skips a paused entry before any ssh —
    never a soniox failure, and the still-live sibling account is unaffected."""

    def test_soniox_delivery_never_ssh_a_paused_account(self):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("SONIOX_API_KEY=FAKE-KEY-NEVER-REAL\n")
        hosts = [
            {"name": "montalu@subdev", "host": "9.9.9.9", "user": "montalu",
             "repo_path": "~/devel/airuleset"},
            {"name": "paused@subdev", "host": "9.9.9.9", "user": "pausedu",
             "repo_path": "~/devel/airuleset", "paused": "test pause reason"},
        ]
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            {"montalu": "branch-merge", "pausedu": "branch-merge"}):
            failed = airuleset.provision_subdev_soniox_key(hosts=hosts, run=run, source=src)
        joined = " ".join(str(a) for a in calls)
        self.assertNotIn("pausedu@9.9.9.9", joined,
                         "a paused account must never be ssh'd")
        self.assertIn("montalu@9.9.9.9", joined,
                      "the still-live sibling account must still be provisioned")
        self.assertEqual(failed, [],
                         "a paused skip is never a soniox delivery failure")


class TestDeployLoopSkipsPaused(TestCase):
    """(a) the deploy loop skips a paused entry before any ssh, prints
    `SKIPPED (paused): <name> — <reason>`, and the run summary shows the
    paused bucket separately — never counted in `failed`, never affects the
    push's exit code."""

    def _plain_host(self):
        return {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                "repo_path": "~/devel/airuleset"}

    def _paused_host(self):
        return {"name": "paused@subdev", "host": "9.9.9.9", "user": "pausedu",
                "repo_path": "~/devel/airuleset",
                "paused": "test pause reason"}

    def test_deploy_loop_reports_and_skips_paused_entry(self):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
            return m.Mock(returncode=0, stdout="ok", stderr="")

        args = m.Mock()
        buf = io.StringIO()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS",
                               [self._plain_host(), self._paused_host()]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                contextlib.redirect_stdout(buf):
            airuleset.cmd_push(args)
        out = buf.getvalue()
        self.assertIn(
            "SKIPPED (paused): paused@subdev — test pause reason", out)
        self.assertIn("1 deployed, 1 paused, 0 failed", out)
        self.assertNotIn("DEPLOY FAILED paused@subdev", out)
        joined = " ".join(str(a) for c in calls for a in c)
        self.assertNotIn("9.9.9.9", joined,
                         "a paused account must never be ssh'd")

    def test_deploy_summary_not_skewed_by_a_non_deployable_failure_name(self):
        # Review W1: `cmd_push` seeds `failed` with `("local(dev1)", ...)`
        # BEFORE `_deploy_to_all_remotes` ever runs -- that name is NOT a
        # REMOTE_HOSTS/deployable-host entry, so it must never be subtracted
        # from the "deployed" count. A local-install failure alongside a
        # cleanly-deployed remote + a paused entry must still report the
        # remote as deployed (1 deployed), not 0 or negative.
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
            return m.Mock(returncode=0, stdout="ok", stderr="")

        def failing_install(args):
            raise SystemExit(1)

        args = m.Mock()
        buf = io.StringIO()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install", side_effect=failing_install), \
                m.patch.object(airuleset, "REMOTE_HOSTS",
                               [self._plain_host(), self._paused_host()]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        out = buf.getvalue()
        self.assertIn("1 deployed, 1 paused, 1 failed", out,
                      "the local(dev1) install failure must not decrement "
                      "the remote deployed count")
        self.assertNotIn("0 deployed", out)
        self.assertNotIn("-1 deployed", out)


class TestResourceGuardsLegStaysHostScoped(TestCase):
    """(#851 deliberate, documented in the design comment and in
    cli_resource_guards.py's own docstring): SHARED_STREAM_GUARD_HOSTS is
    HOST-scoped (one entry = the whole subdev box). A per-account pause on
    ONE REMOTE_HOSTS entry sharing that host must NOT remove/alter the
    guard-hosts entry itself -- doing so would strip cgroup-OOM protection
    from every OTHER active stream on the same box."""

    def test_guard_hosts_entry_is_unaffected_by_account_level_pause(self):
        guard_hosts = cli_fleet.SHARED_STREAM_GUARD_HOSTS
        self.assertEqual(len(guard_hosts), 1)
        self.assertEqual(guard_hosts[0]["name"], "subdev")
        self.assertFalse(cli_fleet.is_paused(guard_hosts[0]))


if __name__ == "__main__":
    unittest.main()
