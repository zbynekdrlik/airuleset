"""#1083 — `tickets-status --refresh` wires the git-derived `M` set through the
partition into the per-cwd cache: a merged-to-develop-not-main ticket LEAVES `I`
(the workable open count) into `merged_unreleased` (count + numbers).

RED-first: the base tree neither computes the merged set nor persists it, so
`merged_unreleased` is absent and the merged ticket still counts in `open`.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar


_FAKE_GH = r"""#!/usr/bin/env bash
case "$*" in
  *"repo view"*) echo "zbynekdrlik/demo";;
  *"api"*pulls/5*) echo '{"title":"PR five","body":"Closes #105"}';;
  *created:*) echo 3;;
  *closed:*) echo 1;;
  *length*) echo 0;;
  *) echo '[{"number":105},{"number":2},{"number":3}]';;
esac
"""


class RefreshWiresMergedSet(unittest.TestCase):
    def _mkrepo(self, repo):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}

        def g(*args):
            subprocess.run(["git", "-C", repo, *args], check=True, env=env,
                           capture_output=True)
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                       env=env, capture_output=True)
        g("commit", "--allow-empty", "-q", "-m", "base")
        g("update-ref", "refs/remotes/origin/main", "HEAD")
        g("checkout", "-q", "-b", "devwork")
        g("commit", "--allow-empty", "-q", "-m",
          "Merge pull request #5 from stream/5-fix")
        g("update-ref", "refs/remotes/origin/develop", "HEAD")

    def test_merged_ticket_leaves_I_into_merged_unreleased(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._mkrepo(repo)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(_FAKE_GH)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            # #105 (fix merged to develop via PR #5, not yet in main) leaves I.
            self.assertEqual(cache.get("merged_unreleased"), 1, cache)
            self.assertEqual(cache.get("merged_unreleased_numbers"), [105])
            # I now counts only the two tickets with no merged fix (2, 3).
            self.assertEqual(cache["open"], 2)
            # and the footer renders the M badge.
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 2", seg)
            self.assertIn("· M 1", seg)

    def test_two_branch_repo_has_no_merged_bucket(self):
        # No origin/develop -> M empty -> open unchanged, no crash.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            env = {**os.environ, "GIT_AUTHOR_NAME": "t",
                   "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@x"}
            subprocess.run(["git", "init", "-q", "-b", "main", repo],
                           check=True, env=env, capture_output=True)
            subprocess.run(["git", "-C", repo, "commit", "--allow-empty",
                            "-q", "-m", "base"], check=True, env=env,
                           capture_output=True)
            subprocess.run(["git", "-C", repo, "update-ref",
                            "refs/remotes/origin/main", "HEAD"], check=True,
                           env=env, capture_output=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(_FAKE_GH)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache.get("merged_unreleased"), 0, cache)
            self.assertEqual(cache["open"], 3)


if __name__ == "__main__":
    unittest.main()
