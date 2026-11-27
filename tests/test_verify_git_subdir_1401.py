"""#1401 (mikrotik-nlm 27.11.2026): the project dir `devel/mikrotik` is no git repo;
its one repo (zbynekdrlik/network) is the `network/` subdir, the shape the footer
already resolves (#61 fallback: exactly one `.git` child). `accounts verify`
git-identity read nothing there and FAILed "cannot read the visibility"."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import cli_account_verify as verify


def _git_block(spec):
    probe = verify.render_probe("acct", spec)
    return probe[probe.index("echo @@git"):probe.index("echo @@session")]


class GitProbeSubdir(unittest.TestCase):

    def _run(self, layout):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            proj = home / "devel" / "mikrotik"
            for sub in layout:
                repo = proj / sub
                repo.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.email",
                                "x@users.noreply.github.com"], check=True)
            proj.mkdir(parents=True, exist_ok=True)
            bin_ = Path(tmp) / "bin"
            bin_.mkdir()
            gh = bin_ / "gh"
            gh.write_text("#!/bin/sh\necho PUBLIC\n")
            gh.chmod(0o755)
            spec = {"project_dir": "devel/mikrotik", "repo": "o/network"}
            env = dict(os.environ, HOME=str(home), PATH="%s:/usr/bin:/bin" % bin_)
            r = subprocess.run(["bash", "-c", _git_block(spec)], env=env,
                               capture_output=True, text=True, timeout=30)
        return r.stdout

    def test_single_child_repo_is_read(self):
        out = self._run(["network"])
        self.assertIn("visibility=PUBLIC", out)
        self.assertIn("email=x@users.noreply.github.com", out)

    def test_two_child_repos_stay_unread(self):
        self.assertNotIn("visibility=", self._run(["a", "b"]))


if __name__ == "__main__":
    unittest.main()
