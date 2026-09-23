"""Issue 1129: own-comment detection must recognise the stream App that
actually minted THIS box's token.

Root cause: on a genuine App-token box `cli_quals._stream_self_login()`
returned the constant `STREAM_APP_BOT_LOGIN = "app/odoo-erp-stream-tokens"`.
The david1-4 / montalu5-8 streams are minted by a SECOND App
(`odoo-erp-stream-tokens-2`, odoo-erp streams.conf `app=` column), so every
own comment (authored `odoo-erp-stream-tokens-2`) read as foreign in
`_ages_from_comments()` → permanent `no-target!` / `stale!` on W members.

Fix (Design-by: main, Approach 1): resolve the self login from the slug
recorded NEXT TO the token actually in use — the `.app` sidecar of the file
`~/.config/gh-app-tokens/primary` resolves to (sibling of the minting path's
existing `.expires` sidecar). No record / an invalid record → the constant
(today's behaviour, unchanged for single-App boxes).

Hermetic: every test points `GH_APP_TOKEN_DIR` and `HOME` at a temp dir —
never the real `~/.config/gh-app-tokens`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import airuleset
import cli_quals
from _hook_state_cleanup import hermetic_hook_env

REPO = Path(__file__).resolve().parent.parent


APP1 = "odoo-erp-stream-tokens"
APP2 = "odoo-erp-stream-tokens-2"
TOKEN_NAME = "zbynekdrlik__odoo-erp"


class _TokenDirCase(unittest.TestCase):
    """A temp App-token dir laid out exactly like push-stream-tokens.sh
    delivers it: `<owner>__<name>` token file + `.expires` sidecar + an
    ABSOLUTE `primary` symlink to the token file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name) / "home"
        self.token_dir = home / ".config" / "gh-app-tokens"
        self.token_dir.mkdir(parents=True)
        self.token_file = self.token_dir / TOKEN_NAME
        self.token_file.write_text("ghs_fake_token_value")
        (self.token_dir / (TOKEN_NAME + ".expires")).write_text(
            "2026-09-23T20:00:00Z\n")
        (self.token_dir / "primary").symlink_to(self.token_file.resolve())
        env = mock.patch.dict(os.environ, {
            "GH_APP_TOKEN_DIR": str(self.token_dir),
            "HOME": str(home)})
        env.start()
        self.addCleanup(env.stop)
        # A genuine App-token box: `gh api user` 403s → _gh_login() is None.
        gl = mock.patch.object(airuleset, "_gh_login", return_value=None)
        gl.start()
        self.addCleanup(gl.stop)

    def write_sidecar(self, content, name=TOKEN_NAME + ".app"):
        (self.token_dir / name).write_text(content)


class TestSelfLoginFromAppSidecar(_TokenDirCase):

    def test_second_app_sidecar_counts_its_comments_as_own(self):
        self.write_sidecar(APP2 + "\n")
        self_login = cli_quals._stream_self_login()
        self.assertEqual("app/" + APP2, self_login)
        self.assertTrue(cli_quals._is_own_login(APP2, self_login))
        self.assertTrue(cli_quals._is_own_login("app/" + APP2, self_login))
        # The FIRST App's comments are another stream's — never own here.
        self.assertFalse(cli_quals._is_own_login(APP1, self_login))

    def test_first_app_sidecar_does_not_own_second_app_comments(self):
        self.write_sidecar(APP1)
        self_login = cli_quals._stream_self_login()
        self.assertEqual("app/" + APP1, self_login)
        self.assertFalse(cli_quals._is_own_login(APP2, self_login))

    def test_no_sidecar_falls_back_to_constant(self):
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                         cli_quals._stream_self_login())

    def test_invalid_sidecar_falls_back_to_constant(self):
        for bad in ("", "   \n", "bad slug", "a/b", "x;rm -rf /",
                    APP2 + "\nextra-line"):
            with self.subTest(bad=bad):
                self.write_sidecar(bad)
                self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                                 cli_quals._stream_self_login())

    def test_regular_primary_file_reads_its_own_sidecar(self):
        (self.token_dir / "primary").unlink()
        (self.token_dir / "primary").write_text("ghs_fake_token_value")
        self.write_sidecar(APP2, name="primary.app")
        self.assertEqual("app/" + APP2, cli_quals._stream_self_login())

    def test_pat_box_with_stray_dir_still_returns_pat_login(self):
        """#918 guard stays first: a real PAT login wins over any sidecar."""
        self.write_sidecar(APP2)
        with mock.patch.object(airuleset, "_gh_login",
                               return_value="kvaskodev"):
            self.assertEqual("kvaskodev", cli_quals._stream_self_login())


class TestOwnTargetVisibleEndToEnd(_TokenDirCase):
    """The ticket's observed symptom: a fresh `Ops-wait-target:` comment by
    the -2 App must set `own_target` (no false `no-target!`)."""

    COMMENTS = [{
        "author": {"login": APP2},
        "createdAt": "2026-09-23T10:00:00Z",
        "body": "Ops-wait-target: client confirms in Discuss by 2026-09-30",
    }]

    def test_second_app_target_is_own_with_sidecar(self):
        self.write_sidecar(APP2)
        ages = cli_quals._ages_from_comments(
            self.COMMENTS, cli_quals._stream_self_login())
        self.assertIsNotNone(ages["own"])
        self.assertIsNotNone(ages["own_target"])

    def test_second_app_target_not_own_on_first_app_box(self):
        self.write_sidecar(APP1)
        ages = cli_quals._ages_from_comments(
            self.COMMENTS, cli_quals._stream_self_login())
        self.assertIsNone(ages["own"])
        self.assertIsNone(ages["own_target"])


class TestSidecarReaderHardening(_TokenDirCase):
    """Review findings (2 adversarial passes): the reader sits on the footer
    and PreToolUse-hook paths, so it must never hang, never read a huge file,
    never trust a stale record, and only accept a real GitHub App slug."""

    def test_dangling_primary_ignores_orphan_sidecar(self):
        self.write_sidecar(APP2)
        self.token_file.unlink()          # primary now dangles
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                         cli_quals._stream_self_login())

    def test_missing_primary_ignores_orphan_primary_sidecar(self):
        (self.token_dir / "primary").unlink()
        self.write_sidecar(APP2, name="primary.app")
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                         cli_quals._stream_self_login())

    def test_relative_primary_symlink_reads_sibling_sidecar(self):
        (self.token_dir / "primary").unlink()
        (self.token_dir / "primary").symlink_to(TOKEN_NAME)
        self.write_sidecar(APP2)
        self.assertEqual("app/" + APP2, cli_quals._stream_self_login())

    def test_non_slug_values_fall_back(self):
        for bad in (".", "..", "-lead", "Odoo-Erp-Stream-Tokens-2",
                    "odoo_erp", "a" * 300):
            with self.subTest(bad=bad):
                self.write_sidecar(bad)
                self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                                 cli_quals._stream_self_login())

    def test_directory_sidecar_falls_back(self):
        (self.token_dir / (TOKEN_NAME + ".app")).mkdir()
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                         cli_quals._stream_self_login())

    def test_fifo_sidecar_never_blocks(self):
        """A FIFO with no writer would block a plain open() forever."""
        os.mkfifo(self.token_dir / (TOKEN_NAME + ".app"))
        code = ("import cli_quals, airuleset;"
                "airuleset._gh_login=lambda *a, **k: None;"
                "print(cli_quals._stream_self_login())")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True, timeout=20,
                           env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN, r.stdout.strip())


def _fake_gh_403_dir(testcase):
    d = tempfile.mkdtemp(prefix="fake-gh-1129-")
    testcase.addCleanup(shutil.rmtree, d, True)
    gh = Path(d) / "gh"
    gh.write_text("#!/usr/bin/env bash\n"
                  "echo 'HTTP 403: Resource not accessible by integration' >&2\n"
                  "exit 1\n")
    gh.chmod(0o755)
    return d


class TestAuthorityCliOnSecondAppBox(_TokenDirCase):
    """The hook reads identity through `airuleset.py authority` subprocesses:
    both `--self-login` and the #773 fallback `--app-bot-login` must name the
    App that minted THIS box's token."""

    def _authority(self, flag):
        env = hermetic_hook_env(self, GH_APP_TOKEN_DIR=str(self.token_dir))
        env["PATH"] = _fake_gh_403_dir(self) + os.pathsep + env["PATH"]
        r = subprocess.run([sys.executable, str(REPO / "airuleset.py"),
                            "authority", flag], cwd=str(REPO),
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_self_login_cli_names_second_app(self):
        self.write_sidecar(APP2)
        self.assertEqual("app/" + APP2, self._authority("--self-login"))

    def test_app_bot_login_cli_names_second_app(self):
        self.write_sidecar(APP2)
        self.assertEqual("app/" + APP2, self._authority("--app-bot-login"))

    def test_app_bot_login_cli_without_record_is_constant(self):
        self.assertEqual(airuleset.STREAM_APP_BOT_LOGIN,
                         self._authority("--app-bot-login"))


if __name__ == "__main__":
    unittest.main()
