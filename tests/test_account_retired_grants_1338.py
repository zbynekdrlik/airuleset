"""#1338: an account whose declaration is removed from ``SERVICE_ACCOUNTS`` is
never rendered again, so its per-account root grants (the #1337 userns profile
and unshare copy, the #1281 rtprio limit, the #1282 device rules and NIC
dispatcher) used to stay on the host. Every root bootstrap render now carries a
sweep step that removes the grants of accounts NOT declared on that host, and
only files that carry the signature our own renderers write."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402

_ROOTS = {
    hardening.USERNS_DIR: "userns",
    hardening.APPARMOR_DIR: "apparmor.d",
    hardening.LIMITS_DIR: "limits.d",
    hardening.UDEV_RULES_DIR: "rules.d",
    hardening.NM_DISPATCHER_DIR: "dispatcher.d",
}
_TOOLS = ("apparmor_parser", "setfacl", "udevadm", "tc", "nmcli")


def _world(tmp):
    """Create the fake root dirs under ``tmp``; return {name: Path}."""
    dirs = {}
    for name in _ROOTS.values():
        dirs[name] = Path(tmp, name)
        dirs[name].mkdir()
    return dirs


def _run(step, tmp):
    """Run the rendered step under ``set -euo pipefail`` with every root path
    moved under ``tmp`` and the root tools stubbed as argv loggers. Returns
    (stdout, [logged argv lines])."""
    body = step
    # longest first: USERNS_DIR is not a prefix of the others, but be exact
    for real in sorted(_ROOTS, key=len, reverse=True):
        body = body.replace(real, str(Path(tmp, _ROOTS[real])))
    log = Path(tmp, "tools.log")
    log.touch()
    stubs = "".join(
        '%s() { printf "%%s\\n" "%s $*" >> "%s"; %s }\n'
        % (t, t, log, 'echo dev0;' if t == "nmcli" else ":;") for t in _TOOLS)
    # the test user owns the fake tree; the real one is root's
    stubs += ('stat() { if [ "$2" = "%U %a" ]; then echo "root $(command stat -c %a "$3")"; '
              'else command stat "$@"; fi; }\n')
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + stubs + body],
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("step failed rc=%d\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    return r.stdout, log.read_text().splitlines()


def _grant(dirs, account, devices=("kvm",), conn="lan0"):
    """Write exactly what the per-account renderers write for ``account``."""
    (dirs["apparmor.d"] / ("airuleset-userns-" + account)).write_text(
        hardening.userns_profile(account))
    (dirs["userns"] / account).mkdir()
    (dirs["userns"] / account).chmod(0o750)
    (dirs["userns"] / account / "unshare").write_bytes(b"copy")
    (dirs["limits.d"] / ("90-%s-rtprio.conf" % account)).write_text(
        "%s - rtprio 20\n" % account)
    (dirs["rules.d"] / ("90-%s-devices.rules" % account)).write_text("".join(
        'KERNEL=="%s", RUN+="/usr/bin/setfacl -m u:%s:rw /dev/%%k"\n' % (d, account)
        for d in devices))
    (dirs["dispatcher.d"] / ("90-%s-noqueue" % account)).write_text(
        "#!/bin/sh\n# airuleset-managed (#1282). Do not edit.\n"
        'if [ "$2" = up ] && [ "$CONNECTION_ID" = %s ]; then\n'
        '  exec /usr/sbin/tc qdisc replace dev "$1" root noqueue\nfi\n' % conn)


def _paths(dirs, account):
    return [dirs["apparmor.d"] / ("airuleset-userns-" + account),
            dirs["userns"] / account,
            dirs["limits.d"] / ("90-%s-rtprio.conf" % account),
            dirs["rules.d"] / ("90-%s-devices.rules" % account),
            dirs["dispatcher.d"] / ("90-%s-noqueue" % account)]


class TestRender(unittest.TestCase):

    def test_declared_names_are_validated(self):
        for bad in (["ok", "a b"], ["$(x)"], ["Upper"], []):
            with self.assertRaises(ValueError, msg=repr(bad)):
                hardening.render_retired_grants_step(bad)

    def test_wired_into_the_full_script_with_the_host_set(self):
        for account, host, foreign in (("dcontroller", "dev1", " varos "),
                                       ("varos", "dev2", " fohmixer ")):
            script = bootstrap.render_root_bootstrap(account)
            # before the account's own grant steps, so it can never undo them
            self.assertLess(script.index("# 3d-rg."), script.index("# 3d-rt."))
            self.assertLess(script.index("# 3d-rg."), script.index("/.ssh/authorized_keys"))
            here = sorted(a for a in bootstrap.SERVICE_ACCOUNTS
                          if bootstrap.account_spec(a)["host"] == host)
            self.assertIn(account, here)
            step = hardening.render_retired_grants_step(here)
            self.assertIn(step, script)
            self.assertNotIn(foreign, step)

    def test_one_name_rule(self):
        self.assertIs(bootstrap._ACCOUNT_RE, hardening.ACCOUNT_RE)


class TestSweep(unittest.TestCase):

    def test_a_retired_account_loses_every_grant(self):
        step = hardening.render_retired_grants_step(["keep"])
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            _grant(dirs, "keep", conn="lan9")     # its own link (shared: below)
            _grant(dirs, "gone-acct", devices=("kvm", "cpu_dma_latency"))
            out, log = _run(step, tmp)
            for p in _paths(dirs, "gone-acct"):
                self.assertFalse(p.exists(), p)
            for p in _paths(dirs, "keep"):
                self.assertTrue(p.exists(), p)
            prof = dirs["apparmor.d"] / "airuleset-userns-gone-acct"
            self.assertIn("apparmor_parser -R %s" % prof, log)
            self.assertIn("setfacl -x u:gone-acct /dev/kvm", log)
            self.assertIn("setfacl -x u:gone-acct /dev/cpu_dma_latency", log)
            self.assertIn("udevadm control --reload", log)
            self.assertIn("nmcli -g GENERAL.DEVICES connection show lan0", log)
            self.assertIn("tc qdisc del dev dev0 root", log)
            self.assertFalse([line for line in log if "keep" in line], log)
            self.assertIn("gone-acct", out)
            # idempotent: a second run touches nothing and says so
            out, log2 = _run(step, tmp)
            self.assertEqual(log2, log)
            self.assertIn("retired-grants: none", out)

    def test_a_name_is_matched_whole_never_as_a_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            _grant(dirs, "keep")
            _run(hardening.render_retired_grants_step(["keep-old", "kee"]), tmp)
            for p in _paths(dirs, "keep"):
                self.assertFalse(p.exists(), p)

    def test_a_shared_connection_keeps_its_qdisc(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            _grant(dirs, "keep")
            _grant(dirs, "gone")
            out, log = _run(hardening.render_retired_grants_step(["keep"]), tmp)
            self.assertFalse((dirs["dispatcher.d"] / "90-gone-noqueue").exists())
            self.assertNotIn("tc qdisc del dev dev0 root", log)
            self.assertIn("lan0 stays", out)

    def test_nothing_retired_is_a_quiet_none(self):
        step = hardening.render_retired_grants_step(["keep"])
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            _grant(dirs, "keep")
            out, log = _run(step, tmp)
            self.assertEqual(log, [])
            self.assertIn("retired-grants: none", out)

    def test_a_file_without_our_signature_is_kept(self):
        """A generic name (90-*-rtprio.conf) may be an admin's own file: only
        the exact shape our renderer writes is ours (#1323 lesson)."""
        step = hardening.render_retired_grants_step(["keep"])
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            foreign = {
                dirs["limits.d"] / "90-admin-rtprio.conf": "admin - rtprio 20\n@audio - rtprio 95\n",
                dirs["limits.d"] / "90-other-rtprio.conf": "someone - rtprio 20\n",
                dirs["rules.d"] / "90-admin-devices.rules":
                    'KERNEL=="kvm", GROUP="kvm"\n',
                dirs["dispatcher.d"] / "90-admin-noqueue": "#!/bin/sh\nexit 0\n",
                dirs["apparmor.d"] / "airuleset-userns-admin": "profile x /bin/true {}\n",
                dirs["limits.d"] / "90-tail-rtprio.conf": "tail - rtprio 20\nextra",
                dirs["limits.d"] / "90-two-rtprio.conf": "two - rtprio 20\n\n",
            }
            for p, text in foreign.items():
                p.write_text(text)
            # undeclared dirs that are not our 0750 dir holding an unshare copy
            for name, mode, copy in (("open", 0o755, True), ("empty", 0o750, False)):
                d = dirs["userns"] / name
                d.mkdir()
                d.chmod(mode)
                if copy:
                    (d / "unshare").write_text("x")
                foreign[d] = ""
            out, log = _run(step, tmp)
            for p in foreign:
                self.assertTrue(p.exists(), p)
            self.assertEqual(log, [])
            self.assertIn("kept", out)

    def test_symlinks_and_bad_names_are_never_followed(self):
        step = hardening.render_retired_grants_step(["keep"])
        with tempfile.TemporaryDirectory() as tmp:
            dirs = _world(tmp)
            target = Path(tmp, "precious")
            target.mkdir()
            target.chmod(0o750)     # looks like ours: only the symlink check guards it
            (target / "f").write_text("x")
            (target / "unshare").write_text("x")
            (dirs["userns"] / "linked").symlink_to(target)
            (dirs["limits.d"] / "90-linked-rtprio.conf").symlink_to(target / "f")
            bad = [dirs["limits.d"] / ("90-%s-rtprio.conf" % n)
                   for n in ("Bad$x", "1digit", "x", "a" * 33)]
            for b in bad:
                b.write_text("%s - rtprio 20\n" % b.name[3:-len("-rtprio.conf")])
            _run(step, tmp)
            self.assertTrue((target / "f").exists())
            self.assertTrue((dirs["userns"] / "linked").is_symlink())
            self.assertTrue((dirs["limits.d"] / "90-linked-rtprio.conf").is_symlink())
            for b in bad:
                self.assertTrue(b.exists(), b)

    def test_empty_dirs_and_missing_dirs_are_fine(self):
        step = hardening.render_retired_grants_step(["keep"])
        with tempfile.TemporaryDirectory() as tmp:     # no dirs at all
            out, log = _run(step, tmp)
            self.assertEqual(log, [])
            self.assertIn("retired-grants: none", out)


if __name__ == "__main__":
    unittest.main()
