"""#1244 — the disk-guard root leg must never make a second logrotate stanza.

logrotate 3.21 rejects a second stanza for a log it already rotates ("duplicate
log entry"), drops it with the rest of that file and exits 1. On gk, airuleset's
#841 btmp/wtmp files and odoo-erp's `/etc/logrotate.d/gk-auth` (odoo-erp issue
5872) both rotate btmp + wtmp, so logrotate.service failed every night and the
push read-back exited 4.

These tests pin the fix:
  * the on-box detector (`cli_logrotate_owner.DETECTOR_PY`, run with python3)
    reads only the PATH LINES of logrotate configs — never comments, directives
    or postrotate scripts — and matches globs per path segment;
  * the apply-script step yields a log another file rotates: airuleset's OWN
    file (header marker on line 1) becomes a stanza-free yield note, a foreign
    file is never touched, and the stanza comes back once the other owner is
    gone; a detection error writes nothing and fails the read-back;
  * `build_apply_script` installs btmp/wtmp only through that step.

The behavioural tests run the real rendered bash (with the real `_install`)
against a temp copy of gk's live logrotate tree; nothing touches /etc.
"""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_disk_guard_root as g  # noqa: E402

# gk's live /etc/logrotate.d/gk-auth (read 26.10.2026, odoo-erp issue 5872).
# Its comments mention btmp/wtmp in prose — the parser must not count those.
GK_AUTH = """\
# /etc/logrotate.d/gk-auth — installed by scripts/deploy-worktree-reaper.sh (#5872).
# Do not edit on the host; edit deploy-worktree-reaper.sh in the repo and re-deploy.
#
# Follow-up to #5872: every stanza below carries an explicit `su root root`.
# ALSO why the pre-#5872 unbounded btmp/wtmp rotation likely never actually
# ran via cron in the first place, not just why it lacked a size cap.
/var/log/btmp {
    missingok
    notifempty
    daily
    maxsize 100M
    rotate 2
    compress
    create 0660 root utmp
    su root root
}
/var/log/wtmp {
    missingok
    notifempty
    daily
    maxsize 100M
    rotate 2
    compress
    create 0664 root utmp
    su root root
}
/var/log/auth.log {
    missingok
    notifempty
    daily
    maxsize 100M
    rotate 2
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
    create 0640 syslog adm
    su root root
}
"""

# gk's live /etc/logrotate.d/rsyslog: a multi-line path list, `{` on its own line.
GK_RSYSLOG = """\
/var/log/syslog
/var/log/mail.log
/var/log/kern.log
/var/log/user.log
/var/log/cron.log
{
\trotate 4
\tweekly
\tmissingok
\tsharedscripts
\tpostrotate
\t\t/usr/lib/rsyslog/rsyslog-rotate
\tendscript
}
"""

# gk's /etc/logrotate.conf (global directives + the include; no stanza).
GK_CONF_TEMPLATE = """\
# see "man logrotate" for details
weekly
su root adm
rotate 4
create
include {lr_dir}
"""


def _lr():
    """The new leaf, imported per test so this file collects before it exists."""
    return importlib.import_module("cli_logrotate_owner")


def _detector():
    """The detector's own namespace: the SAME source the box runs."""
    ns = {"__name__": "airuleset_lr_detector_test"}
    exec(_lr().DETECTOR_PY, ns)  # noqa: S102 — the shipped detector source
    return ns


class TestPathLineParser(unittest.TestCase):

    def test_gk_auth_names_exactly_its_three_logs_not_the_prose(self):
        pats = _detector()["log_patterns"](GK_AUTH)
        self.assertEqual(pats, ["/var/log/btmp", "/var/log/wtmp",
                                "/var/log/auth.log"])

    def test_multiline_path_list_with_brace_on_its_own_line(self):
        pats = _detector()["log_patterns"](GK_RSYSLOG)
        self.assertEqual(pats, ["/var/log/syslog", "/var/log/mail.log",
                                "/var/log/kern.log", "/var/log/user.log",
                                "/var/log/cron.log"])

    def test_global_directives_and_include_are_not_paths(self):
        conf = GK_CONF_TEMPLATE.format(lr_dir="/etc/logrotate.d")
        self.assertEqual(_detector()["log_patterns"](conf), [])

    def test_a_path_inside_a_postrotate_script_is_not_a_stanza(self):
        text = ("/var/log/app.log {\n    postrotate\n"
                "        /var/log/btmp\n        test -f /var/log/btmp && true\n"
                "    endscript\n}\n")
        self.assertEqual(_detector()["log_patterns"](text), ["/var/log/app.log"])

    def test_quoted_paths_and_a_one_line_stanza(self):
        text = '"/var/log/btmp" /var/log/other { weekly }\n/var/log/wtmp {\n}\n'
        self.assertEqual(_detector()["log_patterns"](text),
                         ["/var/log/btmp", "/var/log/other", "/var/log/wtmp"])

    def test_glob_matches_per_path_segment(self):
        rotates = _detector()["rotates"]
        self.assertTrue(rotates("/var/log/*tmp", "/var/log/btmp"))
        self.assertTrue(rotates("/var/log/btmp", "/var/log/btmp"))
        self.assertFalse(rotates("/var/log/*.log", "/var/log/btmp"))
        # glob(3): `*` never crosses a `/` — /var/* names /var/log, not btmp
        self.assertFalse(rotates("/var/*", "/var/log/btmp"))
        self.assertFalse(rotates("/var/log/wtmp", "/var/log/btmp"))

    def test_the_yield_note_rotates_nothing_and_carries_the_marker(self):
        lr = _lr()
        note = lr.render_yield_note("/var/log/btmp")
        self.assertTrue(note.startswith(lr.MARKER), note)
        self.assertEqual(_detector()["log_patterns"](note), [])
        self.assertIn("/var/log/btmp", note)


class _Tree(unittest.TestCase):
    """A temp copy of a logrotate tree + the real rendered step, run by bash."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.lr_dir = self.root / "logrotate.d"
        self.lr_dir.mkdir()
        self.conf = self.root / "logrotate.conf"
        self.conf.write_text(GK_CONF_TEMPLATE.format(lr_dir=self.lr_dir))
        self.btmp = self.lr_dir / "btmp"
        self.wtmp = self.lr_dir / "wtmp"

    def logs(self):
        return [("/var/log/btmp", str(self.btmp), g.render_logrotate_btmp(), "0644"),
                ("/var/log/wtmp", str(self.wtmp), g.render_logrotate_wtmp(), "0644")]

    def run_step(self, lr_dir=None):
        step = _lr().build_logrotate_step(
            self.logs(), lr_conf=str(self.conf),
            lr_dir=str(lr_dir or self.lr_dir))
        script = ('set -uo pipefail\nJOURNALD_CHANGED=""\nLR_FAIL=""\n'
                  + g.render_install_fn() + "\n" + step
                  + '\necho "LR_FAIL=[$LR_FAIL]"\n')
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, r.stderr


class TestStepOnTheGkTree(_Tree):
    """gk today: airuleset's marked btmp/wtmp + odoo-erp's gk-auth."""

    def setUp(self):
        super().setUp()
        self.btmp.write_text(g.render_logrotate_btmp())
        self.wtmp.write_text(g.render_logrotate_wtmp())
        (self.lr_dir / "gk-auth").write_text(GK_AUTH)
        (self.lr_dir / "rsyslog").write_text(GK_RSYSLOG)

    def test_airuleset_yields_both_logs_to_gk_auth(self):
        out, err = self.run_step()
        lr = _lr()
        self.assertEqual(self.btmp.read_text(), lr.render_yield_note("/var/log/btmp"))
        self.assertEqual(self.wtmp.read_text(), lr.render_yield_note("/var/log/wtmp"))
        gk_auth = str(self.lr_dir / "gk-auth")
        self.assertIn("/var/log/btmp yielded to %s" % gk_auth, out)
        self.assertIn("/var/log/wtmp yielded to %s" % gk_auth, out)
        self.assertIn("LR_FAIL=[]", out)

    def test_the_foreign_file_is_never_touched(self):
        self.run_step()
        self.assertEqual((self.lr_dir / "gk-auth").read_text(), GK_AUTH)
        self.assertEqual((self.lr_dir / "rsyslog").read_text(), GK_RSYSLOG)

    def test_no_log_is_named_by_two_files_afterwards(self):
        self.run_step()
        det = _detector()
        for log in ("/var/log/btmp", "/var/log/wtmp", "/var/log/auth.log"):
            owners = [p for p in det["config_files"](str(self.conf), str(self.lr_dir))
                      if any(det["rotates"](pat, log)
                             for pat in det["log_patterns"](Path(p).read_text()))]
            self.assertEqual(owners, [str(self.lr_dir / "gk-auth")], log)

    def test_a_second_run_is_idempotent(self):
        self.run_step()
        out, _err = self.run_step()
        self.assertIn("unchanged %s" % self.btmp, out)
        self.assertIn("unchanged %s" % self.wtmp, out)


class TestStepWithoutAnotherOwner(_Tree):
    """dev1/dev2 today: airuleset is the only owner — the stanza stays."""

    def test_the_stanza_is_written_when_nothing_else_rotates_the_log(self):
        (self.lr_dir / "rsyslog").write_text(GK_RSYSLOG)
        out, _err = self.run_step()
        self.assertEqual(self.btmp.read_text(), g.render_logrotate_btmp())
        self.assertEqual(self.wtmp.read_text(), g.render_logrotate_wtmp())
        self.assertNotIn("yielded", out)

    def test_the_stanza_comes_back_once_the_other_owner_is_gone(self):
        lr = _lr()
        self.btmp.write_text(lr.render_yield_note("/var/log/btmp"))
        self.wtmp.write_text(lr.render_yield_note("/var/log/wtmp"))
        self.run_step()
        self.assertEqual(self.btmp.read_text(), g.render_logrotate_btmp())
        self.assertEqual(self.wtmp.read_text(), g.render_logrotate_wtmp())

    def test_the_distro_file_is_still_overwritten_when_alone(self):
        distro = "/var/log/btmp {\n    missingok\n    monthly\n    rotate 1\n}\n"
        self.btmp.write_text(distro)
        self.run_step()
        self.assertEqual(self.btmp.read_text(), g.render_logrotate_btmp())

    def test_a_dpkg_backup_copy_is_not_an_owner(self):
        # logrotate skips taboo extensions (.dpkg-old …), so must the detector
        (self.lr_dir / "btmp.dpkg-old").write_text(g.render_logrotate_btmp())
        self.run_step()
        self.assertEqual(self.btmp.read_text(), g.render_logrotate_btmp())


class TestStepWithAForeignFileAtOurPath(_Tree):

    def test_a_foreign_file_at_our_path_is_left_alone_and_reported(self):
        distro = "/var/log/btmp {\n    missingok\n    monthly\n    rotate 1\n}\n"
        self.btmp.write_text(distro)
        (self.lr_dir / "gk-auth").write_text(GK_AUTH)
        _out, err = self.run_step()
        self.assertEqual(self.btmp.read_text(), distro)
        self.assertIn("airuleset did not write", err)

    def test_nothing_is_created_when_our_path_is_absent(self):
        (self.lr_dir / "gk-auth").write_text(GK_AUTH)
        self.run_step()
        self.assertFalse(self.btmp.exists())
        self.assertFalse(self.wtmp.exists())


class TestStepDetectionError(_Tree):

    def test_an_unreadable_config_dir_writes_nothing_and_fails_the_read_back(self):
        self.btmp.write_text(g.render_logrotate_btmp())
        not_a_dir = self.root / "not-a-dir"
        not_a_dir.write_text("x")
        out, err = self.run_step(lr_dir=not_a_dir)
        self.assertIn("LR_FAIL=[1]", out)
        self.assertIn("DISK-GUARD-ROOT VERIFY FAIL", err)
        self.assertEqual(self.btmp.read_text(), g.render_logrotate_btmp())
        self.assertFalse(self.wtmp.exists())


class TestApplyScriptWiring(unittest.TestCase):

    def _logs(self):
        return [("/var/log/btmp", g.LOGROTATE_BTMP_PATH, g.render_logrotate_btmp(), "0644"),
                ("/var/log/wtmp", g.LOGROTATE_WTMP_PATH, g.render_logrotate_wtmp(), "0644")]

    def test_the_apply_script_embeds_the_ownership_step(self):
        for drain in (True, False):
            s = g.build_apply_script(drain=drain)
            self.assertIn(_lr().build_logrotate_step(self._logs()), s)

    def test_btmp_wtmp_are_never_installed_before_the_ownership_check(self):
        s = g.build_apply_script()
        for log, conf in (("/var/log/btmp", g.LOGROTATE_BTMP_PATH),
                          ("/var/log/wtmp", g.LOGROTATE_WTMP_PATH)):
            check = s.find("_lr_other %s %s" % (log, conf))
            self.assertNotEqual(check, -1, "no ownership check for %s" % log)
            self.assertGreater(s.index("_install %s " % conf), check, conf)

    def test_a_detection_error_turns_into_exit_4(self):
        s = g.build_apply_script()
        self.assertIn('LR_FAIL=""', s)
        verify = s.index("fail=0")
        self.assertIn('if [ -n "$LR_FAIL" ]; then fail=1; fi', s[verify:])

    def test_the_whole_script_is_still_valid_bash(self):
        r = subprocess.run(["bash", "-n"], input=g.build_apply_script(),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
