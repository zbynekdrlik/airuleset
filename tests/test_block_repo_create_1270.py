"""#1270: a session outside airuleset cannot create a GitHub repository.

The iemmixer session created dasio by itself under `newlevel`. A new
project goes through airuleset (`onboard-project` + a project account)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "block-repo-create-outside-airuleset.sh"


def _git_dir(url):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "remote", "add", "origin", url], check=True)
    return d


def _run(tc, cmd, cwd):
    payload = json.dumps({"tool_input": {"command": cmd}, "cwd": cwd})
    return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                          text=True, timeout=20, env=hermetic_hook_env(tc))


class TestRepoCreateHook(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.foreign = _git_dir("https://github.com/zbynekdrlik/iemmixer.git")
        cls.home = _git_dir("git@github.com:zbynekdrlik/airuleset.git")

    def test_foreign_session_is_blocked(self):
        for cmd in ("gh repo create zbynekdrlik/x --private",
                    "cd /tmp && gh repo create x --public --source .",
                    "bash -c 'gh repo create x'",
                    "gh repo fork zbynekdrlik/x --clone",
                    "gh api user/repos -f name=x",
                    "gh api -X POST /user/repos -f name=x",
                    "/usr/bin/gh repo create x", "~/.local/bin/gh repo create y",
                    "x=$(gh repo create y)", "echo $(gh repo create y)",
                    "(gh repo create y)", "`gh repo create y`",
                    "bash -lc 'gh repo create y'", "ssh dev1 'gh repo create y'",
                    "GH_TOKEN=x gh repo create y", "sudo -u a gh repo create y",
                    "echo a\ngh repo create y", "gh api repos/a/tmpl/generate -f name=x",
                    "gh api repos/a/b/forks -X POST"):
            r = _run(self, cmd, self.foreign)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("airuleset", r.stderr)

    def test_airuleset_session_may(self):
        self.assertEqual(_run(self, "gh repo create zbynekdrlik/x", self.home).returncode, 0)

    def test_reads_and_docs_pass(self):
        for cmd in ("gh repo view zbynekdrlik/x", "gh api user/repos --jq '.[].name'",
                    "gh repo list", "echo 'never run gh repo create here'",
                    "cat > b.md <<'EOF'\ngh repo create x\nEOF",
                    'git commit -m "x; gh repo create y"',
                    'gh issue comment 1 --body "a; gh repo create y"',
                    'gh issue comment 1 --body "line1\ngh repo create y\nline3"',
                    "echo gh repo create x", "gh api -X GET user/repos -f per_page=100"):
            self.assertEqual(_run(self, cmd, self.foreign).returncode, 0, cmd)

    def test_bypass_logs_the_reason(self):
        env = hermetic_hook_env(self)
        payload = json.dumps({"tool_input": {
            "command": "gh repo create x # airuleset:repo-create-ok owner order"},
            "cwd": self.foreign})
        r = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                           text=True, timeout=20, env=env)
        self.assertEqual(r.returncode, 0)
        log = Path(env["HOME"], ".claude", "repo-create-bypass.log").read_text()
        self.assertIn("owner order", log)

    def test_it_is_wired_as_a_bash_hook(self):
        text = (REPO / "settings" / "hooks.json").read_text()
        self.assertIn("block-repo-create-outside-airuleset.sh", text)


if __name__ == "__main__":
    unittest.main()
