"""#1270: a session outside airuleset cannot create a GitHub repository.

The iemmixer session created dante-asio by itself under `newlevel`. A new
project goes through airuleset (`onboard-project` + a project account)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "block-repo-create-outside-airuleset.sh"


def _git_dir(url):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "remote", "add", "origin", url], check=True)
    return d


def _run(cmd, cwd):
    payload = json.dumps({"tool_input": {"command": cmd}, "cwd": cwd})
    return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                          text=True, timeout=20, env=dict(os.environ))


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
                    "gh api -X POST /user/repos -f name=x"):
            r = _run(cmd, self.foreign)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("airuleset", r.stderr)

    def test_airuleset_session_may(self):
        self.assertEqual(_run("gh repo create zbynekdrlik/x", self.home).returncode, 0)

    def test_reads_and_docs_pass(self):
        for cmd in ("gh repo view zbynekdrlik/x", "gh api user/repos --jq '.[].name'",
                    "gh repo list", "echo 'never run gh repo create here'",
                    "cat > b.md <<'EOF'\ngh repo create x\nEOF"):
            self.assertEqual(_run(cmd, self.foreign).returncode, 0, cmd)

    def test_it_is_wired_as_a_bash_hook(self):
        text = (REPO / "settings" / "hooks.json").read_text()
        self.assertIn("block-repo-create-outside-airuleset.sh", text)


if __name__ == "__main__":
    unittest.main()
