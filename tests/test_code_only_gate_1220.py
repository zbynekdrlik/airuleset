"""#1220 review: the varos repo must never become public and its accounting
documents must never reach GitHub. The App token can write code, so the
repo's .gitignore alone is not enough: `git add -f doklady/x.pdf` would
stage a document and the credential helper would push it. GitHub push
rulesets exist only for org-owned repos (live 422 on zbynekdrlik/varos), so
the guard is the Claude-side staging gate, active for an account declared
`code_only`, with no bypass. And no project token may ever carry
`administration` (the only permission that can change a repo's visibility).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402
import test_project_gh_token_1190 as t1190  # noqa: E402

CODE_ONLY = {"acct": {"code_only": True}}


def _codeonly():
    from gates import codeonly
    return codeonly


class TestForceAdd(unittest.TestCase):

    def test_force_adds_are_seen(self):
        for cmd in ("git add -f doklady/a.pdf", "git add --force x.csv",
                    "git add -fA", "git add -Af .", "git -C /r add -f x",
                    "git stage -f x", "cd r && git add -f a.pdf && git commit -m x",
                    "git -c core.x=1 add --force=yes x"):
            self.assertTrue(_codeonly().force_add(cmd), cmd)

    def test_ordinary_adds_are_not(self):
        for cmd in ("git add -A", "git add src/a.py", "git add -p",
                    "git commit -m 'git add -f is banned'", "echo git add -f",
                    "git add -- -f", "git status"):
            self.assertFalse(_codeonly().force_add(cmd), cmd)


class TestViolation(unittest.TestCase):

    def test_a_force_add_is_refused_only_for_a_code_only_account(self):
        co = _codeonly()
        msg = co.violation("git add -f doklady/a.pdf", user="acct",
                           accounts=CODE_ONLY)
        self.assertIn("code-only", msg)
        self.assertIn("1220", msg)
        self.assertEqual(co.violation("git add -f doklady/a.pdf", user="other",
                                      accounts=CODE_ONLY), "")

    def test_a_commit_with_an_ignored_staged_file_is_refused(self):
        co = _codeonly()
        with tempfile.TemporaryDirectory() as repo:
            def git(*a):
                subprocess.run(["git", "-C", repo, *a], check=True,
                               capture_output=True)
            git("init", "-q")
            Path(repo, ".gitignore").write_text("*.pdf\ndoklady/\n")
            Path(repo, "tool.py").write_text("print(1)\n")
            git("add", ".gitignore", "tool.py")
            self.assertEqual(co.violation("git commit -m x", user="acct",
                                          accounts=CODE_ONLY, cwd=repo), "")
            Path(repo, "doklady").mkdir()
            Path(repo, "doklady", "faktura.pdf").write_bytes(b"%PDF-1.4\n")
            git("add", "-f", "doklady/faktura.pdf")
            msg = co.violation("git -C %s commit -m x" % repo, user="acct",
                               accounts=CODE_ONLY, cwd=repo)
            self.assertIn("doklady/faktura.pdf", msg)
            self.assertEqual(co.violation("git commit -m x", user="other",
                                          accounts=CODE_ONLY, cwd=repo), "")

    def test_a_staged_check_that_cannot_run_fails_closed(self):
        def boom(*a, **k):
            raise OSError("no git")
        msg = _codeonly().violation("git commit -m x", user="acct",
                                    accounts=CODE_ONLY, run=boom)
        self.assertIn("could not check", msg)

    def test_the_secret_ok_marker_does_not_bypass_it(self):
        from gates import secrets
        payload = json.dumps({"tool_input": {
            "command": "git add -f a.pdf  # airuleset:secret-ok owner said so"}})
        with mock.patch.object(_codeonly(), "_current_user", return_value="varos"), \
                mock.patch("sys.stdin") as stdin, \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            stdin.read.return_value = payload
            with self.assertRaises(SystemExit) as cm:
                secrets.main()
        self.assertEqual(cm.exception.code, 2)


class TestDeclaration(unittest.TestCase):

    def test_varos_is_code_only(self):
        self.assertIs(bootstrap.account_spec("varos")["code_only"], True)

    def test_code_only_must_be_a_bool(self):
        spec = dict(bootstrap.SERVICE_ACCOUNTS["varos"], code_only="yes")
        self.assertIn("code_only must be True or False",
                      "; ".join(bootstrap.validate_account("varos", spec)))

    def test_the_real_varos_bootstrap_installs_the_shim_before_the_checkout(self):
        script = bootstrap.render_root_bootstrap("varos")
        shim = script.index("# 11. Project-account GitHub App token shim")
        self.assertIn("auth git-credential", script)
        self.assertIn("credential.https://github.com.helper", script)
        self.assertLess(shim, script.index("# 9. Project checkout"))


class TestNoAdministration(unittest.TestCase):

    def test_a_token_carrying_administration_is_refused(self):
        def gh(req, timeout=None):
            return t1190._Resp(201, {
                "token": t1190.TOKEN, "expires_at": t1190.EXPIRES,
                "permissions": {"contents": "write", "administration": "write"},
                "repositories": [{"full_name": t1190.REPO}]})
        with self.assertRaises(pgt.MintError) as cm:
            pgt.mint_token(t1190.INSTALLATION, t1190.REPO, "J.W.T", urlopen=gh)
        self.assertIn("administration", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
