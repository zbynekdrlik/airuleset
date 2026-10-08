"""#1220: the root render works for a PRIVATE project repo.

Step 9 cloned with `GIT_TERMINAL_PROMPT=0 git clone https://…` and no
credential: fine for fohmixer (public), fatal for the private varos repo, and
under `set -euo pipefail` it aborted the render before the tmux session, the gh
shim and the toolchain. The App token is delivered by the controller only after
the account exists, so the first render cannot have it. Now, for a
`github_app` account, the gh shim lands BEFORE the checkout and is the account
git's github.com credential helper, and a failed clone DEFERS loudly (dir
created, "re-run after mint") instead of aborting."""
import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402


def _spec(**over):
    spec = copy.deepcopy(bootstrap.account_spec("varos"))
    spec.update(over)
    return spec


class TestCheckoutStep(unittest.TestCase):

    def test_a_failed_clone_defers_instead_of_aborting(self):
        step = bootstrap._render_project_step(_spec())
        self.assertIn("clone deferred", step)
        self.assertIn("project-gh-token mint", step)

    def test_the_deferred_clone_runs_and_exits_zero(self):
        # run the rendered checkout command as a plain shell: git is a stub
        # that fails, as an unauthenticated clone of a private repo does
        step = bootstrap._render_project_step(_spec(tmux_session=None))
        cmd = step.split("runuser -l \"$ACCOUNT\" -c ", 1)[1].split("\n", 1)[0]
        inner = subprocess.run(["bash", "-c", "printf %s " + cmd],
                               capture_output=True, text=True).stdout
        with tempfile.TemporaryDirectory() as home:
            stub = Path(home) / "bin"
            stub.mkdir()
            (stub / "git").write_text("#!/bin/sh\nexit 128\n")
            (stub / "git").chmod(0o755)
            r = subprocess.run(["bash", "-euo", "pipefail", "-c", inner],
                               capture_output=True, text=True,
                               env={"HOME": home, "PATH": "%s:/usr/bin:/bin" % stub})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("clone deferred", r.stdout)
            self.assertTrue((Path(home) / "devel" / "varos" / "uctovnictvo").is_dir())


class TestShimBeforeCheckout(unittest.TestCase):

    def test_a_github_app_account_gets_the_shim_first_and_as_credential_helper(self):
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS,
                             {"varos": _spec(github_app=True)}):
            script = bootstrap.render_root_bootstrap("varos")
        shim = script.index("# 11. Project-account GitHub App token shim")
        checkout = script.index("# 9. Project checkout")
        self.assertLess(shim, checkout)
        self.assertIn("auth git-credential", script)
        self.assertIn("credential.https://github.com.helper", script)

    def test_an_account_without_github_app_has_no_credential_helper(self):
        script = bootstrap.render_root_bootstrap("varos")
        self.assertNotIn("credential.https://github.com.helper", script)


if __name__ == "__main__":
    unittest.main()
