"""#1349 — the disk-guard root leg manages an sshd ``MaxStartups`` drop-in.

``airuleset.py push`` fans out ~20 parallel ssh sessions to dev1 within ~2 s.
OpenSSH's default ``MaxStartups 10:30:100`` starts dropping 30 % of new
unauthenticated connections above 10, so a project's own deploy landing in
that window failed with ``kex_exchange_identification: Connection reset``.

The root leg now writes ``/etc/ssh/sshd_config.d/50-airuleset-maxstartups.conf``
(``MaxStartups 60:30:200``), gates it with ``sshd -t`` (a refused config is
removed and the leg fails loudly), reloads sshd ONLY when the file changed,
and reads the effective value back from ``sshd -T``.

The step is executed in bash with PATH stubs for ``sshd`` and ``systemctl``
(the root-script pattern): the ``sshd`` stub is STATEFUL — ``-T`` reports the
drop-in's value when the file is there, else the default — so a removed or
shadowed file really fails the read-back.
"""
import os
import subprocess
import tempfile
import unittest

import cli_disk_guard_root as g
import cli_sshd_maxstartups as ms

FOREIGN_60_DROPIN = "Match User airuleset\n    KbdInteractiveAuthentication yes\n"
FOREIGN_MONTALU = "Match User montalu*\n    DenyUsers *\n"


class TestRender(unittest.TestCase):
    def test_content_sets_maxstartups_60_30_200(self):
        body = ms.render_sshd_maxstartups_conf()
        lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(lines, ["MaxStartups 60:30:200",
                                 "PerSourceMaxStartups 30"])

    def test_first_line_is_the_managed_marker(self):
        first = ms.render_sshd_maxstartups_conf().splitlines()[0]
        self.assertTrue(first.startswith("# Managed by airuleset"), first)
        self.assertIn("#1349", first)

    def test_path_sorts_after_the_legacy_60_drop_in(self):
        # sshd keeps the LAST MaxStartups it reads, so the drop-in sorts
        # after the hand-made 60-airuleset-maxstartups.conf on gk (#358).
        self.assertEqual(ms.SSHD_MAXSTARTUPS_PATH,
                         "/etc/ssh/sshd_config.d/90-airuleset-maxstartups.conf")

    def test_never_the_985_password_drop_in(self):
        self.assertNotEqual(ms.SSHD_MAXSTARTUPS_PATH, g.SSHD_PASSWORD_PATH)

    def test_render_is_importable_from_the_root_module(self):
        self.assertIs(g.render_sshd_maxstartups_conf,
                      ms.render_sshd_maxstartups_conf)


class TestApplyScriptWiring(unittest.TestCase):
    def setUp(self):
        self.s = g.build_apply_script()

    def test_step_is_in_the_apply_script(self):
        self.assertIn(ms.SSHD_MAXSTARTUPS_PATH, self.s)
        self.assertIn("MaxStartups 60:30:200", self.s)

    def test_config_test_comes_before_the_reload(self):
        self.assertLess(self.s.index("sshd -t"),
                        self.s.index("systemctl reload ssh"))

    def test_readback_reads_the_effective_value(self):
        rb = self.s[self.s.index("fail=0"):]
        self.assertIn("sshd -T", rb)

    def test_workstation_variant_carries_it_too(self):
        # dev1/dev2 (sudo, drain=False) are exactly the boxes the burst hits.
        self.assertIn(ms.SSHD_MAXSTARTUPS_PATH,
                      g.build_apply_script(drain=False, journal_max="2G"))


class TestStepExecution(unittest.TestCase):
    """Run the real step + read-back with the real ``_install``."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name
        self.conf_d = os.path.join(self.d, "sshd_config.d")
        os.mkdir(self.conf_d)
        self.path = os.path.join(self.conf_d, "90-airuleset-maxstartups.conf")
        self.legacy = os.path.join(self.conf_d, "50-airuleset-maxstartups.conf")
        self.calls = os.path.join(self.d, "calls")
        self.bin = os.path.join(self.d, "bin")
        os.mkdir(self.bin)
        self.pw = os.path.join(self.conf_d, "60-airuleset-password.conf")
        self.mo = os.path.join(self.conf_d, "99-block-montalu.conf")
        with open(self.pw, "w") as f:
            f.write(FOREIGN_60_DROPIN)
        with open(self.mo, "w") as f:
            f.write(FOREIGN_MONTALU)

    def _stubs(self, t_rc=0, reload_rc=0, shadow=None, mv_fails=False,
               ps_shadow=None):
        # sshd -T: the FIRST MaxStartups wins in sshd, so a "shadow" value
        # models a foreign drop-in sorting before 50-; else the managed file's
        # value when it exists; else the OpenSSH default.
        sshd = (
            '#!/bin/bash\n'
            'echo "sshd $*" >> "%(calls)s"\n'
            'case "$1" in\n'
            '  -t) [ %(t_rc)d -ne 0 ] && echo "bad option" >&2; exit %(t_rc)d;;\n'
            '  -T) echo "port 22"\n'
            '      v=$(cat "%(conf_d)s"/*.conf 2>/dev/null | awk '
            '\'$1=="MaxStartups"{v=$2} END{print v}\')\n'
            '      [ -n "$v" ] || v=10:30:100\n'
            '      [ -n "%(shadow)s" ] && v="%(shadow)s"\n'
            '      echo "maxstartups $v"\n'
            '      p=$(cat "%(conf_d)s"/*.conf 2>/dev/null | awk '
            '\'$1=="PerSourceMaxStartups"{print $2; exit}\')\n'
            '      [ -n "$p" ] || p=none\n'
            '      [ -n "%(ps_shadow)s" ] && p="%(ps_shadow)s"\n'
            '      echo "persourcemaxstartups $p"; exit 0;;\n'
            'esac\nexit 0\n'
            % {"calls": self.calls, "t_rc": t_rc, "shadow": shadow or "",
               "ps_shadow": ps_shadow or "", "conf_d": self.conf_d})
        systemctl = ('#!/bin/bash\necho "systemctl $*" >> "%s"\nexit %d\n'
                     % (self.calls, reload_rc))
        stubs = [("sshd", sshd), ("systemctl", systemctl)]
        if mv_fails:
            stubs.append(("mv", "#!/bin/bash\nexit 1\n"))
        for name, body in stubs:
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)

    def run_step(self, pre="", **kw):
        self._stubs(**kw)
        stamp = os.path.join(self.d, "run", "sshd-maxstartups.reloaded")
        script = ('set -uo pipefail\nJOURNALD_CHANGED=""\n' + pre
                  + g.render_install_fn() + "\n"
                  + ms.build_sshd_maxstartups_step("EOF_MARK", path=self.path,
                                                   stamp=stamp)
                  + "\nfail=0\n" + ms.build_sshd_maxstartups_readback()
                  + '\necho "fail=$fail"\n')
        env = dict(os.environ, PATH=self.bin + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = open(self.calls).read() if os.path.exists(self.calls) else ""
        return r.stdout, r.stderr, calls

    def assert_foreign_untouched(self):
        self.assertEqual(open(self.pw).read(), FOREIGN_60_DROPIN)
        self.assertEqual(open(self.mo).read(), FOREIGN_MONTALU)

    def test_fresh_box_writes_tests_reloads_and_verifies(self):
        out, err, calls = self.run_step()
        self.assertEqual(open(self.path).read(),
                         ms.render_sshd_maxstartups_conf())
        self.assertIn("sshd -t", calls)
        self.assertEqual(calls.count("systemctl reload ssh"), 1, calls)
        self.assertLess(calls.index("sshd -t"),
                        calls.index("systemctl reload ssh"))
        self.assertIn("fail=0", out)
        self.assert_foreign_untouched()

    def test_second_run_is_idempotent_and_reloads_nothing(self):
        self.run_step()
        os.remove(self.calls)
        out, err, calls = self.run_step()
        self.assertIn("unchanged %s" % self.path, out)
        self.assertNotIn("systemctl reload", calls)
        self.assertIn("fail=0", out)

    def test_a_changed_file_is_rewritten_and_reloaded(self):
        with open(self.path, "w") as f:
            f.write("MaxStartups 10:30:100\n")
        out, err, calls = self.run_step()
        self.assertEqual(open(self.path).read(),
                         ms.render_sshd_maxstartups_conf())
        self.assertEqual(calls.count("systemctl reload ssh"), 1, calls)
        self.assertIn("fail=0", out)

    def test_refused_config_removes_the_file_and_fails_loudly(self):
        out, err, calls = self.run_step(t_rc=255)
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn("systemctl reload", calls)
        self.assertIn("fail=1", out)
        self.assertIn("sshd config test failed", err)
        self.assert_foreign_untouched()

    def test_failed_reload_fails_the_leg(self):
        out, err, calls = self.run_step(reload_rc=1)
        self.assertIn("fail=1", out)
        self.assertIn("sshd reload failed", err)

    def test_a_failed_reload_is_retried_on_the_next_run(self):
        # review 🟡: the file is unchanged on run 2, but the daemon never
        # loaded it, so the reload stamp (sha256) must trigger a retry.
        self.run_step(reload_rc=1)
        os.remove(self.calls)
        out, err, calls = self.run_step()
        self.assertIn("unchanged %s" % self.path, out)
        self.assertEqual(calls.count("systemctl reload ssh"), 1, calls)
        self.assertIn("fail=0", out)
        os.remove(self.calls)
        out, err, calls = self.run_step()
        self.assertNotIn("systemctl reload", calls)

    def test_a_foreign_broken_config_leaves_our_unchanged_file(self):
        self.run_step()
        out, err, calls = self.run_step(t_rc=255)
        self.assertEqual(open(self.path).read(),
                         ms.render_sshd_maxstartups_conf())
        self.assertIn("did not change", err)
        self.assertIn("fail=1", out)
        self.assert_foreign_untouched()

    def test_an_earlier_write_in_the_script_does_not_reload_sshd(self):
        # the journald cap (or any other file) changed, ours did not
        self.run_step()
        os.remove(self.calls)
        out, err, calls = self.run_step(pre="DGROOT_WROTE=1\n")
        self.assertNotIn("systemctl reload", calls)
        self.assertIn("fail=0", out)

    def test_a_failed_install_reloads_nothing_and_fails(self):
        # review round 2: `_install` must not flag a write it never made
        out, err, calls = self.run_step(mv_fails=True)
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn("systemctl reload", calls)
        self.assertFalse(os.path.exists(os.path.join(self.d, "run")))
        self.assertIn("install of %s failed" % self.path, err)
        self.assertNotIn("wrote %s" % self.path, out)
        self.assertIn("fail=1", out)
        self.assertFalse([f for f in os.listdir(self.conf_d)
                          if f.startswith(".airuleset-dgroot")])

    def test_shadowed_effective_value_fails_the_readback(self):
        out, err, calls = self.run_step(shadow="10:30:100")
        self.assertIn("fail=1", out)
        self.assertIn("MaxStartups", err)
        self.assertIn("10:30:100", err)

    def test_missing_per_source_cap_fails_the_readback(self):
        # A public box (gk, subdev) must not let ONE source hold every one
        # of the 200 pre-auth slots: the effective per-source cap is 30.
        out, err, calls = self.run_step(ps_shadow="none")
        self.assertIn("fail=1", out)
        self.assertIn("PerSourceMaxStartups", err)

    def test_a_legacy_60_drop_in_does_not_win(self):
        # gk carries a hand-made `60-airuleset-maxstartups.conf` with
        # MaxStartups 30:30:150 (#358). sshd keeps the LAST MaxStartups.
        with open(os.path.join(self.conf_d, "60-airuleset-maxstartups.conf"),
                  "w") as f:
            f.write("MaxStartups 30:30:150\n")
        out, err, calls = self.run_step()
        self.assertIn("fail=0", out, err)

    def test_a_later_foreign_value_still_fails_the_readback(self):
        with open(os.path.join(self.conf_d, "95-foreign.conf"), "w") as f:
            f.write("MaxStartups 10:30:100\n")
        out, err, calls = self.run_step()
        self.assertIn("fail=1", out)
        self.assertIn("10:30:100", err)

    def test_our_first_50_drop_in_is_retired(self):
        # The first rollout wrote 50-airuleset-maxstartups.conf (sorts before
        # 60-, so it lost). Only our own marked file is removed.
        with open(self.legacy, "w") as f:
            f.write(ms.LEGACY_MARKER + " old\nMaxStartups 60:30:200\n")
        out, err, calls = self.run_step()
        self.assertFalse(os.path.exists(self.legacy))
        self.assertIn("fail=0", out, err)
        self.assert_foreign_untouched()

    def test_a_foreign_50_file_is_left_alone(self):
        with open(self.legacy, "w") as f:
            f.write("MaxStartups 20:30:100\n")
        self.run_step()
        self.assertEqual(open(self.legacy).read(), "MaxStartups 20:30:100\n")


if __name__ == "__main__":
    unittest.main()
