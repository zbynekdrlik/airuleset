"""Behaviour tests for hooks/session-start-stream-directives.sh (issue 1127).

Background: a stream session (david1..4, montalu*, miva1) reads its project
CLAUDE.md and `.claude/**` straight off the WORKING TREE. Streams park on
long-lived FEATURE branches, so a stream directive the project writes later
to `.claude/streams/<unix-user>.md` on its BASE branch (odoo-erp issue 8078:
"no more gk relay") is invisible to every session whose branch predates it.
The #314 fetch hook cannot help: it only fast-forwards the CURRENT branch
against its own remote twin, which is "up to date".

The fix (Design-by: main, Approach 1) delivers the base ref's copy of the
stream file at SessionStart (startup via session-start-fetch.sh's EXIT trap,
after its fetch; compact via a standalone `compact` matcher) whenever the
working-tree copy is absent or differs, plus one WARNING line when the
branch forked from the base more than 7 days ago and the base has moved on.
It never touches the working tree or HEAD.

Every test drives the REAL hooks in throwaway git repos.
"""

import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

ROOT = Path(__file__).resolve().parent.parent
STREAM_HOOK = ROOT / "hooks" / "session-start-stream-directives.sh"
FETCH_HOOK = ROOT / "hooks" / "session-start-fetch.sh"
HOOKS_JSON = ROOT / "settings" / "hooks.json"

# `id -un` is what the hook uses; pwd gives the same answer without relying
# on LOGNAME/USER env (which getpass.getuser() would prefer).
USER = pwd.getpwuid(os.getuid()).pw_name
REL = f".claude/streams/{USER}.md"
HEADER = "Stream directives from"
DIRECTIVE = "gk relay is retired: file your own tickets directly\n"


class _RepoFixture(TestCase):
    """A bare remote + a clone whose default branch is `main` (origin/HEAD
    set), with helpers to backdate commits and fork a feature branch."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="t1127-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = hermetic_hook_env(self)

    # -- git plumbing ---------------------------------------------------- #
    def g(self, cwd, *args, when=None):
        env = dict(self.env)
        if when is not None:
            stamp = f"@{int(when)} +0000"
            env["GIT_AUTHOR_DATE"] = stamp
            env["GIT_COMMITTER_DATE"] = stamp
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=env)
        return r

    def ok(self, cwd, *args, when=None):
        r = self.g(cwd, *args, when=when)
        self.assertEqual(r.returncode, 0, f"git {args}: {r.stderr}")
        return r.stdout.strip()

    def make_remote(self, name):
        bare = os.path.join(self.root, f"{name}.git")
        self.ok(self.root, "init", "-q", "--bare", bare)
        self.ok(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        return bare

    def make_clone(self, bare, name="repo"):
        repo = os.path.join(self.root, name)
        self.ok(self.root, "clone", "-q", bare, repo)
        self.ok(repo, "config", "user.email", "t@t")
        self.ok(repo, "config", "user.name", "t")
        self.ok(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        return repo

    def commit_file(self, repo, rel, text, msg, when=None):
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path) or repo, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        self.ok(repo, "add", rel)
        self.ok(repo, "commit", "-qm", msg, when=when)

    def base_repo(self, fork_age_days=0.0):
        """origin/main: `init` (dated `fork_age_days` ago). The local
        checkout sits on `feature`, forked at `init`. origin/main then
        advances (today) with the stream directive file."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        when = time.time() - fork_age_days * 86400 if fork_age_days else None
        self.commit_file(repo, "f", "v1\n", "init", when=when)
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.ok(repo, "push", "-q", "-u", "origin", "feature")
        # the base moves on from a separate clone (the project writes the
        # stream file on its base branch)
        other = self.make_clone(bare, "other")
        self.ok(other, "checkout", "-q", "main")
        self.commit_file(other, REL, DIRECTIVE, "stream directive")
        self.ok(other, "push", "-q", "origin", "main")
        return repo, other

    # -- hook runners ---------------------------------------------------- #
    def run_stream(self, repo, *args, extra_env=None):
        env = dict(self.env)
        env.update(extra_env or {})
        return subprocess.run(["bash", str(STREAM_HOOK), *args], cwd=repo,
                              capture_output=True, text=True, timeout=60,
                              env=env, input="")

    def run_fetch(self, repo):
        payload = json.dumps({"session_id": "t1127-sid", "source": "startup"})
        return subprocess.run(["bash", str(FETCH_HOOK)], cwd=repo,
                              capture_output=True, text=True, timeout=60,
                              env=self.env, input=payload)

    def assert_tree_untouched(self, repo, head_before):
        self.assertEqual(self.ok(repo, "rev-parse", "HEAD"), head_before,
                         "HEAD must never move")
        self.assertEqual(self.ok(repo, "status", "--porcelain"), "",
                         "the working tree must never be modified")
        self.assertFalse(os.path.exists(os.path.join(repo, REL)),
                         "the stream file must never be written to the tree")


class TestStartupDelivery(_RepoFixture):
    def test_startup_feature_branch_gets_base_directive(self):
        """RED core: a stale feature branch without the stream file, the
        base carrying it -> the SessionStart (startup) output carries the
        base content, and the tree is untouched."""
        repo, _ = self.base_repo()
        head = self.ok(repo, "rev-parse", "HEAD")
        r = self.run_fetch(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(HEADER, r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("origin/main", r.stdout)
        self.assertIn("feature", r.stdout)
        self.assert_tree_untouched(repo, head)

    def test_startup_sees_base_advanced_after_last_fetch(self):
        """The step runs AFTER the parent's fetch (EXIT trap, not a racing
        sibling): the directive was pushed after the local clone last
        fetched, and still arrives on this very boot."""
        repo, other = self.base_repo()
        # a second directive revision lands after the local clone's view
        self.commit_file(other, REL, DIRECTIVE + "second rule\n", "rev2")
        self.ok(other, "push", "-q", "origin", "main")
        r = self.run_fetch(repo)
        self.assertIn("second rule", r.stdout)

    def test_startup_no_streams_dir_is_silent(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, "f", "v1\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        r = self.run_fetch(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(HEADER, r.stdout)
        self.assertNotIn("WARNING", r.stdout)


class TestStandaloneCompactPath(_RepoFixture):
    def test_absent_copy_is_injected(self):
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        head = self.ok(repo, "rev-parse", "HEAD")
        r = self.run_stream(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(HEADER, r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assert_tree_untouched(repo, head)

    def test_compact_path_fetches_the_base_itself(self):
        """Standalone (compact) there is no parent fetch: the step must
        refresh the base remote-tracking ref on its own."""
        repo, _ = self.base_repo()
        # the local clone has NOT fetched since the directive landed
        self.assertNotEqual(self.g(repo, "cat-file", "-e", f"origin/main:{REL}").returncode, 0)
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)

    def test_identical_copy_no_injection(self):
        repo, _ = self.base_repo()
        self.ok(repo, "checkout", "-q", "main")
        self.ok(repo, "pull", "-q", "--ff-only", "origin", "main")
        self.assertTrue(os.path.exists(os.path.join(repo, REL)))
        r = self.run_stream(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_differing_copy_is_injected(self):
        repo, _ = self.base_repo()
        self.commit_file(repo, REL, "old rule from the fork point\n", "old copy")
        head = self.ok(repo, "rev-parse", "HEAD")
        r = self.run_stream(repo)
        self.assertIn(HEADER, r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertNotIn("old rule from the fork point", r.stdout)
        self.assertEqual(self.ok(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.ok(repo, "status", "--porcelain"), "")
        with open(os.path.join(repo, REL)) as fh:
            self.assertEqual(fh.read(), "old rule from the fork point\n",
                             "the working-tree copy must never be overwritten")

    def test_other_users_file_is_not_delivered(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, ".claude/streams/someone-else.md", "x\n", "other")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        r = self.run_stream(repo)
        self.assertEqual(r.stdout, "")

    def test_not_a_git_repo_is_silent(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        r = self.run_stream(plain)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_no_resolvable_base_is_silent(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, REL, DIRECTIVE, "init")
        self.ok(repo, "push", "-q", "origin", "main")
        # no `remote set-head` -> no origin/HEAD, no upstream remote
        self.g(repo, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.ok(repo, "rm", "-q", REL)
        self.ok(repo, "commit", "-qm", "drop")
        r = self.run_stream(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_detached_head_still_delivers(self):
        repo, _ = self.base_repo()
        self.ok(repo, "checkout", "-q", "--detach", "HEAD")
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("detached", r.stdout)


class TestStaleness(_RepoFixture):
    def test_old_merge_base_warns(self):
        repo, _ = self.base_repo(fork_age_days=30)
        r = self.run_stream(repo)
        warn = [ln for ln in r.stdout.splitlines() if ln.startswith("WARNING")]
        self.assertEqual(len(warn), 1, r.stdout)
        self.assertIn("feature", warn[0])
        self.assertIn("origin/main", warn[0])
        self.assertIn("30 days", warn[0])

    def test_fresh_branch_no_warning(self):
        repo, _ = self.base_repo(fork_age_days=1)
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertNotIn("WARNING", r.stdout)

    def test_old_branch_containing_base_tip_no_warning(self):
        """Age alone is not staleness: a branch that already contains the
        base tip is missing nothing, however old the base commit is."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        old = time.time() - 40 * 86400
        self.commit_file(repo, REL, DIRECTIVE, "init", when=old)
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.commit_file(repo, "g", "work\n", "work")
        r = self.run_stream(repo)
        self.assertEqual(r.stdout, "", "identical copy + base tip contained -> silent")

    def test_warning_even_when_copy_identical(self):
        """A stale branch whose stream file happens to match the base still
        learns it is stale (other base rules may be missing)."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        old = time.time() - 20 * 86400
        self.commit_file(repo, REL, DIRECTIVE, "init", when=old)
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        other = self.make_clone(bare, "other")
        self.ok(other, "checkout", "-q", "main")
        self.commit_file(other, "CLAUDE.md", "new project rule\n", "rule")
        self.ok(other, "push", "-q", "origin", "main")
        r = self.run_stream(repo)
        self.assertNotIn(HEADER, r.stdout)
        warn = [ln for ln in r.stdout.splitlines() if ln.startswith("WARNING")]
        self.assertEqual(len(warn), 1, r.stdout)


class TestUpstreamBase(_RepoFixture):
    def test_upstream_default_preferred_over_origin(self):
        """A fork checkout: origin (the fork) has no stream file, upstream
        (the project) does -> the directive comes from upstream/main even
        with no upstream/HEAD symref set."""
        up = self.make_remote("upstream")
        seed = self.make_clone(up, "seed")
        self.commit_file(seed, "f", "v1\n", "init")
        self.ok(seed, "push", "-q", "origin", "main")
        fork = self.make_remote("fork")
        self.ok(seed, "push", "-q", fork, "main")
        repo = self.make_clone(fork)
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "remote", "add", "upstream", up)
        self.ok(repo, "fetch", "-q", "upstream")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        # the project adds the directive on upstream only
        self.commit_file(seed, REL, DIRECTIVE, "stream directive")
        self.ok(seed, "push", "-q", "origin", "main")
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("upstream/main", r.stdout)


class TestHookConventions(TestCase):
    def test_hook_is_executable_and_strict(self):
        self.assertTrue(STREAM_HOOK.exists())
        self.assertTrue(os.access(STREAM_HOOK, os.X_OK))
        text = STREAM_HOOK.read_text()
        self.assertIn("set -euo pipefail", text)

    def test_registered_for_compact_and_trapped_on_startup(self):
        data = json.loads(HOOKS_JSON.read_text())
        groups = data["hooks"]["SessionStart"]
        by_matcher = {}
        for grp in groups:
            cmds = [h["command"] for h in grp["hooks"]]
            by_matcher.setdefault(grp["matcher"], []).extend(cmds)
        self.assertTrue(any("session-start-stream-directives.sh" in c
                            for c in by_matcher.get("compact", [])),
                        by_matcher)
        # the fetch hook (fast-forward + session_start heartbeat) must never
        # run on compact
        self.assertFalse(any("session-start-fetch.sh" in c
                             for c in by_matcher.get("compact", [])))
        # startup reaches the step through the fetch hook's EXIT trap (after
        # its fetch), never as a racing parallel sibling
        self.assertFalse(any("session-start-stream-directives.sh" in c
                             for c in by_matcher.get("startup", [])))
        self.assertIn("session-start-stream-directives.sh", FETCH_HOOK.read_text())


if __name__ == "__main__":
    main()
