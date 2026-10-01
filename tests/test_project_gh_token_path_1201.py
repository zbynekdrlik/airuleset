"""#1201 go-live finding: the controller's project-gh-token timer (#1190) runs
under the systemd user-manager PATH, which has no ``~/.local/bin``. On the
controller ``gh`` lives only there, so the #1199 CI secret sync failed every
run with FileNotFoundError. ``cmd_project_gh_token`` applies the #1178 (c)
entry-point PATH fix before any work, only when gh does not resolve.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402,F401  (the facade the helper lives in)
import cli_project_ci_sync as ci_sync  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402


class TestTimerFindsGh(unittest.TestCase):

    def _run(self, path_extra=""):
        home = tempfile.mkdtemp(prefix="pgt-path-")
        self.addCleanup(shutil.rmtree, home, True)
        local_bin = Path(home) / ".local" / "bin"
        local_bin.mkdir(parents=True)
        gh = local_bin / "gh"
        gh.write_text("#!/bin/sh\nexit 0\n")
        gh.chmod(0o755)
        empty = Path(home) / "empty"
        empty.mkdir()
        seen = {}

        def mint(acct, **_kw):
            seen["mint"] = shutil.which("gh")
            return 0

        def sync(_dry, _started):
            seen["sync"] = shutil.which("gh")
            return 0

        args = mock.Mock(action="mint", account=None, all=True, dry_run=False, key=None)
        with mock.patch.dict(os.environ, {"HOME": home,
                                          "PATH": str(empty) + path_extra}), \
                mock.patch.object(pgt, "github_app_accounts", return_value=["fohmixer"]), \
                mock.patch.object(pgt, "mint_account", side_effect=mint), \
                mock.patch.object(ci_sync, "run_after_mint", side_effect=sync):
            rc = pgt.cmd_project_gh_token(args)
        return rc, seen, str(gh)

    def test_the_timer_run_resolves_gh_from_local_bin(self):
        rc, seen, gh = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(seen["mint"], gh)
        self.assertEqual(seen["sync"], gh)

    def test_a_resolvable_gh_keeps_its_resolution(self):
        other = tempfile.mkdtemp(prefix="pgt-path-other-")
        self.addCleanup(shutil.rmtree, other, True)
        own = Path(other) / "gh"
        own.write_text("#!/bin/sh\nexit 0\n")
        own.chmod(0o755)
        rc, seen, _gh = self._run(path_extra=":" + other)
        self.assertEqual(seen["sync"], str(own))


if __name__ == "__main__":
    unittest.main()
