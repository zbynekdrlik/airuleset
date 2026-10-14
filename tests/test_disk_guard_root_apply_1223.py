"""#1223 — run the rendered root apply script against a scratch root.

String checks cannot prove the journald restart is tied to the cap file, the
seed guard works, or the logrotate duplicate check fires (review A of 1223
found it dead: logrotate exits 1 on a duplicate, so under pipefail the
pipeline is false even when grep matched). Every absolute path the script
writes is moved under a temp root and every system tool is a logging stub.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_disk_guard_root as g   # noqa: E402

PREFIXES = ("/etc/", "/usr/local/lib/airuleset", "/run/airuleset")

STUBS = {
    "systemctl": 'echo "systemctl $*" >> "$LOG"; exit 0\n',
    "journalctl": 'echo "journalctl $*" >> "$LOG"; exit 0\n',
    "systemd-tmpfiles": "exit 0\n",
    "fail2ban-client": "exit 0\n",
    "du": 'for a; do case "$a" in -*) ;; *) printf "0\\t%s\\n" "$a" ;; esac; done\n',
    "ionice": 'echo "ionice $*" >> "$LOG"; shift; shift; exec "$@"\n',
    "logrotate": ('if [ -n "${DUP:-}" ]; then echo "error: duplicate log entry for /var/log/btmp"; '
                  'exit 1; fi\nexit 0\n'),
}


class TestApplyScriptRuns(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        (self.root / "etc").mkdir()
        (self.root / "etc" / "logrotate.conf").write_text("include /etc/logrotate.d\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            p = self.bin / name
            p.write_text("#!/bin/bash\n" + body)
            p.chmod(0o755)
        self.log = self.tmp / "calls.log"
        script = g.build_apply_script(drain=False, journal_max="2G")
        for prefix in PREFIXES:
            script = script.replace(prefix, str(self.root) + prefix)
        self.script = script

    def apply(self, **env):
        self.log.write_text("")
        e = dict(os.environ, PATH="%s:/usr/bin:/bin" % self.bin, LOG=str(self.log), **env)
        r = subprocess.run(["bash", "-c", self.script], env=e, capture_output=True,
                           text=True, timeout=120)
        return r, self.log.read_text()

    def test_journald_restarts_once_then_never_again(self):
        r, calls = self.apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("try-restart systemd-journald.service", calls)
        self.assertIn("journalctl --rotate", calls)
        r, calls = self.apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("try-restart systemd-journald", calls)
        self.assertIn("unchanged", r.stdout)

    def test_the_cap_written_is_the_host_cap(self):
        self.apply()
        cap = self.root / g.JOURNALD_CAP_PATH.lstrip("/")
        self.assertIn("SystemMaxUse=2G", cap.read_text())

    def test_the_seed_runs_niced_once_then_waits_for_staleness(self):
        _r, calls = self.apply()
        self.assertIn("ionice -c3 nice", calls)
        _r, calls = self.apply()
        self.assertNotIn("ionice", calls)

    def test_a_duplicate_logrotate_entry_fails_the_apply(self):
        r, _calls = self.apply(DUP="1")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("duplicate log entry", r.stderr)


if __name__ == "__main__":
    unittest.main()
