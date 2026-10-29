"""#1267 — a #1184 project account gets the owner's noreply identity on install.

Origin: on a project account gh runs through the #1190 App-token shim, which
cannot answer ``gh api user``; ``cli_git_identity.install_step`` skipped every
app-shim box, so wall-bridge@dev1 and songplayer@dev1 (29.10.2026) failed
``accounts verify``'s git-identity check until the identity was set by hand.
A project account only works on the owner's repos, so its identity is the
owner's noreply identity — known without asking GitHub.

Offline: git runs for real on temp repos, every gh call goes through the #1196
``FakeGh`` fixture, global git config is a temp file.
"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from test_git_identity_1196 import NOREPLY, FakeGh, _Base, local, make_repo  # noqa: E402

ORIGIN = "https://github.com/zbynekdrlik/wall-bridge.git"


class TestProjectAccount(_Base):
    def run_step(self, roots, run, project_account):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = self.gi.install_step(home=self.home, roots=roots, run=run,
                                          app_shim=lambda: True,
                                          project_account=project_account)
        return result, out.getvalue(), err.getvalue()

    def test_project_account_public_repo_gets_owner_noreply(self):
        pub = make_repo(self.tmp, "wall-bridge", ORIGIN)
        run = FakeGh({"zbynekdrlik/wall-bridge": "PUBLIC"}, user_rc=1)
        _, out, err = self.run_step([pub], run, lambda: True)
        self.assertEqual(local(pub, "user.email"), NOREPLY)
        self.assertEqual(local(pub, "user.name"), "zbynekdrlik")
        self.assertNotIn(["gh", "api", "user"],
                         [c[:3] for c in run.gh_calls],
                         "the app-token shim can never answer gh api user")
        self.assertNotIn("WARNING", err)
        self.assertIn("1 set", out)

    def test_project_account_private_repo_is_untouched(self):
        priv = make_repo(self.tmp, "secret",
                         "https://github.com/zbynekdrlik/secret.git")
        run = FakeGh({"zbynekdrlik/secret": "PRIVATE"}, user_rc=1)
        self.run_step([priv], run, lambda: True)
        self.assertEqual(local(priv, "user.email"), "")

    def test_project_account_does_not_cache_an_identity(self):
        pub = make_repo(self.tmp, "wall-bridge", ORIGIN)
        self.run_step([pub], FakeGh({"zbynekdrlik/wall-bridge": "PUBLIC"}),
                      lambda: True)
        self.assertNotIn("identity", self.gi.load_cache(self.home),
                         "the owner identity is a constant, never a cached lookup")

    def test_stream_shim_box_keeps_the_quiet_skip(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})
        _, out, err = self.run_step([pub], run, lambda: False)
        self.assertEqual(run.gh_calls, [])
        self.assertEqual(local(pub, "user.email"), "")
        self.assertIn("app-token", out)
        self.assertNotIn("WARNING", err)

    def test_default_detector_is_the_live_project_account_probe(self):
        pub = make_repo(self.tmp, "wall-bridge", ORIGIN)
        run = FakeGh({"zbynekdrlik/wall-bridge": "PUBLIC"}, user_rc=1)
        import cli_project_toolchain
        with m.patch.object(cli_project_toolchain, "runs_as_project_account",
                            return_value=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.gi.install_step(home=self.home, roots=[pub], run=run,
                                 app_shim=lambda: True)
        self.assertEqual(local(pub, "user.email"), NOREPLY)

    def test_detector_failure_keeps_the_skip(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})

        def boom():
            raise RuntimeError("pwd exploded")
        _, _, err = self.run_step([pub], run, boom)
        self.assertEqual(local(pub, "user.email"), "")
        self.assertIn("FAILED", err)


class TestOwnerIdentityDriftLock(unittest.TestCase):
    def setUp(self):
        import cli_git_identity
        self.gi = cli_git_identity

    def test_owner_login_is_the_maintainer(self):
        import cli_onboard
        self.assertEqual(self.gi.OWNER_GH_LOGIN, airuleset.MAINTAINER_GH_LOGIN)
        self.assertEqual(self.gi.OWNER_GH_LOGIN, cli_onboard.GITHUB_OWNER)

    def test_owner_identity_is_the_live_fohmixer_value(self):
        # The value set on fohmixer, wall-bridge and songplayer (#1267, 29.10.).
        self.assertEqual(self.gi.owner_identity(),
                         ("zbynekdrlik",
                          "26905282+zbynekdrlik@users.noreply.github.com"))
        self.assertEqual(self.gi.owner_identity()[1], NOREPLY)

    def test_owner_identity_satisfies_accounts_verify(self):
        import cli_account_verify
        self.assertTrue(self.gi.owner_identity()[1].endswith(
            cli_account_verify.NOREPLY_SUFFIX))


if __name__ == "__main__":
    unittest.main()
