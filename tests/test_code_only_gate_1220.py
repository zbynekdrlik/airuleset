"""#1220 review: the varos repo must never become public and its accounting
documents must never reach GitHub. The App token can write code, so the
repo's .gitignore alone is not enough: a force-added document would be pushed
by the credential helper. GitHub push rulesets exist only for org-owned repos
(live 422 on zbynekdrlik/varos).

Review round 3 showed a command-text parser is trivially bypassed (`env X=1
git add -f`, `/usr/bin/git`, `--fo`, aliases, `cd B && git commit`, a chain
that stages after the check). So the guard is git itself: root-owned
pre-commit/pre-push hooks on the account's core.hooksPath, run in the right
repo on the real index. The staging gate only refuses turning them off. And
no project token may ever carry `administration` (it can change visibility).
"""
import json
import os
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
_GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _codeonly_gate():
    from gates import codeonly
    return codeonly


def _hooks():
    import cli_account_codeonly
    return cli_account_codeonly


class _Repo:
    """A temp repo whose core.hooksPath holds the REAL rendered hook."""

    def __init__(self, base):
        self.dir = Path(base, "repo")
        self.hooks = Path(base, "hooks")
        self.hooks.mkdir()
        pre = self.hooks / "pre-commit"
        pre.write_text(_hooks().HOOK)
        pre.chmod(0o755)
        (self.hooks / "pre-push").symlink_to("pre-commit")
        self.remote = Path(base, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)],
                       check=True, env=_GIT_ENV)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", str(self.hooks))
        self.git("remote", "add", "origin", str(self.remote))
        (self.dir / ".gitignore").write_text("*.pdf\ndoklady/\n")
        (self.dir / "tool.py").write_text("print(1)\n")

    def git(self, *args, check=True):
        self.dir.mkdir(exist_ok=True)
        return subprocess.run(["git", "-C", str(self.dir), *args], env=_GIT_ENV,
                              capture_output=True, text=True, check=check)


class TestHooks(unittest.TestCase):

    def test_pre_commit_refuses_a_force_added_document_however_staged(self):
        with tempfile.TemporaryDirectory() as base:
            r = _Repo(base)
            r.git("add", ".gitignore", "tool.py")
            self.assertEqual(r.git("commit", "-q", "-m", "code").returncode, 0)
            (r.dir / "doklady").mkdir()
            (r.dir / "doklady" / "faktura.pdf").write_bytes(b"%PDF-1.4\n")
            r.git("add", "--fo", "doklady/faktura.pdf")      # a long-option prefix
            done = r.git("commit", "-q", "-m", "oops", check=False)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("BLOCKED (airuleset #1220)", done.stderr)
            self.assertIn("doklady/faktura.pdf", done.stderr)
            self.assertEqual(r.git("rev-list", "--count", "HEAD").stdout.strip(), "1")

    def test_pre_push_refuses_a_commit_that_skipped_the_pre_commit(self):
        with tempfile.TemporaryDirectory() as base:
            r = _Repo(base)
            r.git("add", ".gitignore", "tool.py")
            r.git("commit", "-q", "-m", "code")
            self.assertEqual(r.git("push", "-q", "origin", "main").returncode, 0)
            (r.dir / "vypis.pdf").write_bytes(b"%PDF-1.4\n")
            r.git("add", "-f", "vypis.pdf")
            r.git("commit", "-q", "--no-verify", "-m", "skipped the hook")
            pushed = r.git("push", "-q", "origin", "main", check=False)
            self.assertNotEqual(pushed.returncode, 0)
            self.assertIn("vypis.pdf", pushed.stderr)
            self.assertEqual(r.git("--git-dir", str(r.remote), "rev-list", "--count",
                                   "main").stdout.strip(), "1")

    def test_a_first_push_checks_every_commit(self):
        with tempfile.TemporaryDirectory() as base:
            r = _Repo(base)
            (r.dir / "a.pdf").write_bytes(b"%PDF\n")
            r.git("add", "-f", ".gitignore", "tool.py", "a.pdf")
            r.git("commit", "-q", "--no-verify", "-m", "first")
            (r.dir / "b.py").write_text("x = 1\n")
            r.git("add", "b.py")
            r.git("commit", "-q", "-m", "clean on top")
            self.assertNotEqual(r.git("push", "-q", "origin", "main",
                                      check=False).returncode, 0)

    def test_clean_code_commits_and_pushes(self):
        with tempfile.TemporaryDirectory() as base:
            r = _Repo(base)
            (r.dir / "doklady").mkdir()
            (r.dir / "doklady" / "x.pdf").write_bytes(b"%PDF\n")   # untracked, ignored
            r.git("add", "-A")
            self.assertEqual(r.git("commit", "-q", "-m", "code").returncode, 0)
            self.assertEqual(r.git("push", "-q", "origin", "main").returncode, 0)
            self.assertEqual(r.git("ls-files").stdout.split(), [".gitignore", "tool.py"])


class TestRender(unittest.TestCase):

    def test_only_a_code_only_account_gets_the_step(self):
        self.assertEqual(_hooks().render_step({"code_only": False}), "")
        self.assertEqual(_hooks().render_step({}), "")

    def test_the_real_varos_bootstrap_installs_shim_and_hooks_before_the_checkout(self):
        script = bootstrap.render_root_bootstrap("varos")
        checkout = script.index("# 9. Project checkout")
        self.assertLess(script.index("# 11. Project-account GitHub App token shim"),
                        checkout)
        self.assertIn("credential.https://github.com.helper", script)
        hooks = script.index("# 11c. code-only git hooks")
        self.assertLess(hooks, checkout)
        self.assertIn("core.hooksPath /etc/airuleset/git-hooks/code-only", script)
        self.assertIn(_hooks().HOOK, script)

    def test_the_rendered_step_is_valid_bash(self):
        step = _hooks().render_step({"code_only": True})
        self.assertEqual(subprocess.run(["bash", "-n"], input=step, text=True,
                                        capture_output=True).returncode, 0)


class TestGate(unittest.TestCase):

    def test_turning_the_hooks_off_is_refused_for_a_code_only_account(self):
        gate = _codeonly_gate()
        for cmd in ("git commit --no-verify -m x", "git push --no-verify",
                    "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x",
                    "git config core.hookspath /tmp/h",
                    "GIT_CONFIG_GLOBAL=/dev/null git commit -m x",
                    "env GIT_CONFIG_PARAMETERS=x git push"):
            self.assertIn("1220", gate.violation(cmd, user="acct", accounts=CODE_ONLY),
                          cmd)
            self.assertEqual(gate.violation(cmd, user="other", accounts=CODE_ONLY), "",
                             cmd)

    def test_ordinary_work_is_left_to_the_hooks(self):
        gate = _codeonly_gate()
        for cmd in ("git commit -m x", "git add -A", "git push origin main",
                    "head -n 5 a && git commit -m x", "ls -la"):
            self.assertEqual(gate.violation(cmd, user="acct", accounts=CODE_ONLY), "",
                             cmd)

    def test_an_unknown_uid_never_breaks_the_hook(self):
        gate = _codeonly_gate()
        with mock.patch.object(gate, "_current_user", side_effect=KeyError(4242)):
            self.assertEqual(gate.violation("git commit --no-verify -m x"), "")

    def test_the_secret_ok_marker_does_not_bypass_it(self):
        from gates import secrets
        payload = json.dumps({"tool_input": {
            "command": "git commit --no-verify -m x  # airuleset:secret-ok owner said so"}})
        with mock.patch.object(_codeonly_gate(), "_current_user", return_value="varos"), \
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
