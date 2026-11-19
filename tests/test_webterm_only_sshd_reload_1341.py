"""#1341: `webterm-only --apply-sshd` must reload the unit that exists on the box.

Ubuntu 24.04 (subdev) names the OpenSSH unit `ssh.service`; there is no
`sshd.service`. On 19.11.2026 the apply moved the new drop-in into place and
then died on `systemctl reload sshd` ("Unit sshd.service not found"), so the
david5-8 password lockdown sat on disk without being live.
"""
import contextlib
import io
import os
import stat
import subprocess
import tempfile
import unittest

import cli_webterm_only as wo


def _dry_run_script():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wo._cmd_webterm_only_apply_sshd(dry_run=True)
    out = buf.getvalue()
    return out.split("--- script ---\n", 1)[1].split("--- end script ---", 1)[0]


def _reload_lines(script):
    return [ln.strip() for ln in script.splitlines()
            if "systemctl reload" in ln]


class SshdReloadUnit1341(unittest.TestCase):
    def test_reload_runs_against_a_box_with_only_ssh_service(self):
        lines = _reload_lines(_dry_run_script())
        self.assertEqual(len(lines), 1, lines)
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "systemctl")
            with open(stub, "w") as f:
                f.write('#!/bin/bash\n[ "$1 $2" = "reload ssh" ] && exit 0\n'
                        'echo "Failed to reload $2.service: Unit $2.service '
                        'not found." >&2\nexit 5\n')
            os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + lines[0]],
                               env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reload_still_works_where_the_unit_is_sshd(self):
        lines = _reload_lines(_dry_run_script())
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "systemctl")
            with open(stub, "w") as f:
                f.write('#!/bin/bash\n[ "$1 $2" = "reload sshd" ] && exit 0\nexit 5\n')
            os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + lines[0]],
                               env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
