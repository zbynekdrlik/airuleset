"""#1387: the read-only `fleetbackup@subdev` rrsync login (fleet-backup's own
default key, forced command + from= enforced on subdev) passes the subdev ssh
guard over the tailnet only; every other shape stays refused."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class FleetbackupSubdevLogin1387(unittest.TestCase):
    def _run(self, command):
        d, home = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(REPO / "hooks" / "block-subdev-ssh-misuse.sh")],
                              input=payload, text=True, capture_output=True, cwd=d,
                              timeout=30, env=dict(os.environ, HOME=home))

    def test_rsync_pull_over_the_tailnet_passes(self):
        for cmd in ("rsync -rlt --list-only fleetbackup@100.118.174.27:./",
                    "rsync -a fleetbackup@100.118.174.27:david3/uploads/ /srv/b/david3/uploads/",
                    "rsync -a -e 'ssh -i ~/.ssh/id_ed25519' fleetbackup@subdev:david3/uploads/ /x/",
                    "ssh-keyscan 100.118.174.27"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 0, cmd + r.stdout + r.stderr)

    def test_public_address_and_gatekeeper_key_stay_refused(self):
        for cmd in ("rsync -a fleetbackup@116.203.108.177:david3/uploads/ /x/",
                    "rsync -a fleetbackup@subdev.newlevel.media:david3/uploads/ /x/",
                    "ssh fleetbackupx@100.118.174.27 id"):
            r = self._run(cmd)
            self.assertEqual(r.returncode, 2, cmd + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
