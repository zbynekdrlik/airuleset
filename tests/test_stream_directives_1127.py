"""Behaviour tests for hooks/session-start-stream-directives.sh (issue 1127).

Background: a stream session (david1..4, montalu*, miva1) reads its project
CLAUDE.md and `.claude/**` straight off the WORKING TREE. Streams park on
long-lived FEATURE branches, so a stream directive the project writes later
to `.claude/streams/<unix-user>.md` on its integration branch (odoo-erp issue
8078, on `develop`) is invisible to every session whose branch predates it.
The #314 fetch hook cannot help: it only fast-forwards the CURRENT branch
against its own remote twin, which is "up to date".

The fix (Design-by: main, Approach 1, with the anchor corrections recorded on
the ticket) delivers the base ref's copy of the stream file at SessionStart
(startup via session-start-fetch.sh's EXIT trap, after its fetch; compact /
resume / clear standalone) when the base's last change to it is not in HEAD
and the working-tree copy is absent or differs, plus one WARNING line when the
branch forked from the base more than 7 days ago and the base has moved on.
It never touches the working tree, HEAD, the user's remote-tracking refs or
FETCH_HEAD, and non-stream repos pay no fetch at all.

Every test drives the REAL hooks in throwaway git repos.
"""

import json
import os
import pwd
import shutil
import signal
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
SHARED = ".claude/streams/_shared-contract.md"
HEADER = "Stream directives from"
DIRECTIVE = "gk relay is retired: file your own tickets directly\n"


class _RepoFixture(TestCase):
    """A bare remote + a clone whose default branch is `main` (origin/HEAD
    set), with helpers to backdate commits and fork a feature branch."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="t1127-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = hermetic_hook_env(self)
        self.trace = os.path.join(self.root, "git-trace.log")

    # -- git plumbing ---------------------------------------------------- #
    def g(self, cwd, *args, when=None):
        env = dict(self.env)
        if when is not None:
            stamp = f"@{int(when)} +0000"
            env["GIT_AUTHOR_DATE"] = stamp
            env["GIT_COMMITTER_DATE"] = stamp
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, env=env)

    def ok(self, cwd, *args, when=None):
        r = self.g(cwd, *args, when=when)
        self.assertEqual(r.returncode, 0, f"git {args}: {r.stderr}")
        return r.stdout.strip()

    def make_remote(self, name, head="main"):
        bare = os.path.join(self.root, f"{name}.git")
        self.ok(self.root, "init", "-q", "--bare", bare)
        self.ok(bare, "symbolic-ref", "HEAD", f"refs/heads/{head}")
        return bare

    def make_clone(self, bare, name="repo", branch="main"):
        repo = os.path.join(self.root, name)
        self.ok(self.root, "clone", "-q", bare, repo)
        self.ok(repo, "config", "user.email", "t@t")
        self.ok(repo, "config", "user.name", "t")
        self.ok(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        return repo

    def commit_file(self, repo, rel, text, msg, when=None):
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path) or repo, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.ok(repo, "add", rel)
        self.ok(repo, "commit", "-qm", msg, when=when)

    def push_from_other(self, bare, rel, text, branch="main", name="other"):
        """The project writes on its base branch from a separate clone."""
        other = os.path.join(self.root, name)
        if not os.path.isdir(other):
            other = self.make_clone(bare, name)
            self.ok(other, "fetch", "-q", "origin")
        self.ok(other, "checkout", "-q", "-B", branch, f"origin/{branch}")
        self.commit_file(other, rel, text, f"write {rel}")
        self.ok(other, "push", "-q", "origin", branch)
        return other

    def base_repo(self, fork_age_days=0.0):
        """origin/main: `init` (dated `fork_age_days` ago) already carrying a
        `.claude/streams/` dir (as odoo-erp has for months) but no file for
        this account. The local checkout sits on `feature`, forked at `init`.
        origin/main then advances (today) with this account's directive; the
        local clone has NOT fetched it yet."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        when = time.time() - fork_age_days * 86400 if fork_age_days else None
        self.commit_file(repo, SHARED, "shared contract\n", "init", when=when)
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.ok(repo, "push", "-q", "-u", "origin", "feature")
        self.push_from_other(bare, REL, DIRECTIVE)
        return repo, bare

    # -- hook runners ---------------------------------------------------- #
    def run_stream(self, repo, extra_env=None):
        env = dict(self.env)
        env["GIT_TRACE"] = self.trace
        env.update(extra_env or {})
        return subprocess.run(["bash", str(STREAM_HOOK)], cwd=repo,
                              capture_output=True, text=True, timeout=60,
                              env=env, input=json.dumps({"source": "compact"}))

    def run_fetch(self, repo, extra_env=None):
        env = dict(self.env)
        env["GIT_TRACE"] = self.trace
        env.update(extra_env or {})
        payload = json.dumps({"session_id": "t1127-sid", "source": "startup"})
        return subprocess.run(["bash", str(FETCH_HOOK)], cwd=repo,
                              capture_output=True, text=True, timeout=60,
                              env=env, input=payload)

    def fetch_count(self):
        if not os.path.exists(self.trace):
            return 0
        with open(self.trace, encoding="utf-8", errors="replace") as fh:
            return sum(1 for ln in fh if "built-in: git fetch" in ln)

    def trace_has(self, needle):
        if not os.path.exists(self.trace):
            return False
        with open(self.trace, encoding="utf-8", errors="replace") as fh:
            return any(needle in ln for ln in fh)

    def assert_silent(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout, "")

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

    def test_startup_step_runs_after_the_parent_fetch_and_adds_none(self):
        """The step reads the base AFTER the parent's `git fetch origin`
        (the directive only exists remotely, and the step itself performs NO
        fetch of origin on this path) — so it must be sequenced after it."""
        repo, _ = self.base_repo()
        r = self.run_fetch(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.fetch_count(), 1,
                         "only the parent's own fetch may run on startup")

    def test_startup_no_streams_dir_is_silent(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, "f", "v1\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        r = self.run_fetch(repo)
        self.assert_silent(r)

    def test_signal_kill_does_not_start_the_step(self):
        """Claude Code's timeout kill (SIGTERM) must not launch the step
        (and its fetch) after the hook was already given up on."""
        repo, _ = self.base_repo()
        fake_ssh = os.path.join(self.root, "slow-ssh.sh")
        with open(fake_ssh, "w") as fh:
            fh.write("#!/usr/bin/env bash\nsleep 3\nexit 1\n")
        os.chmod(fake_ssh, 0o755)
        self.ok(repo, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        env = dict(self.env)
        env.update({"GIT_TRACE": self.trace, "GIT_SSH_COMMAND": fake_ssh})
        proc = subprocess.Popen(["bash", str(FETCH_HOOK)], cwd=repo,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env)
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 143)
        self.assertEqual(out, "")
        self.assertFalse(self.trace_has("get-url upstream"),
                         "the stream step must not run after a signal kill")

    def test_step_runs_on_a_normal_failed_fetch(self):
        """Control for the signal test: same slow failing remote, no signal
        -> the step does run (from local refs) on the normal exit path."""
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        fake_ssh = os.path.join(self.root, "fail-ssh.sh")
        with open(fake_ssh, "w") as fh:
            fh.write("#!/usr/bin/env bash\nexit 1\n")
        os.chmod(fake_ssh, 0o755)
        self.ok(repo, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        r = self.run_fetch(repo, {"GIT_SSH_COMMAND": fake_ssh})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.fetch_count(), 1,
                         "an attempted-but-failed origin fetch is never retried")


class TestStandaloneCompactPath(_RepoFixture):
    def test_absent_copy_is_injected(self):
        repo, _ = self.base_repo()
        head = self.ok(repo, "rev-parse", "HEAD")
        r = self.run_stream(repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(HEADER, r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assert_tree_untouched(repo, head)

    def test_compact_fetch_is_private(self):
        """The compact fetch must not race the session's own git: no
        remote-tracking ref update, no FETCH_HEAD."""
        repo, _ = self.base_repo()
        tracking = self.ok(repo, "rev-parse", "refs/remotes/origin/main")
        fetch_head = os.path.join(repo, ".git", "FETCH_HEAD")
        if os.path.exists(fetch_head):
            os.unlink(fetch_head)
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.fetch_count(), 1)
        self.assertEqual(self.ok(repo, "rev-parse", "refs/remotes/origin/main"),
                         tracking, "origin/main must not be moved by the hook")
        self.assertFalse(os.path.exists(fetch_head), "FETCH_HEAD must not be written")

    def test_already_fetched_remote_is_not_refetched(self):
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        r = self.run_stream(repo, {"AIRULESET_STREAM_BASE_FETCHED": "origin"})
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.fetch_count(), 0)

    def test_non_stream_repo_pays_no_fetch(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, "f", "v1\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        r = self.run_stream(repo)
        self.assert_silent(r)
        self.assertEqual(self.fetch_count(), 0)

    def test_identical_copy_no_injection(self):
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "main")
        self.ok(repo, "merge", "-q", "--ff-only", "origin/main")
        self.assertTrue(os.path.exists(os.path.join(repo, REL)))
        self.assert_silent(self.run_stream(repo))

    def test_differing_copy_is_injected(self):
        repo, _ = self.base_repo()
        self.commit_file(repo, REL, "old rule from the fork point\n", "old copy")
        head = self.ok(repo, "rev-parse", "HEAD")
        r = self.run_stream(repo)
        self.assertIn(HEADER, r.stdout)
        self.assertIn("DIFFERS", r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertNotIn("old rule from the fork point", r.stdout)
        self.assertEqual(self.ok(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.ok(repo, "status", "--porcelain"), "")
        with open(os.path.join(repo, REL)) as fh:
            self.assertEqual(fh.read(), "old rule from the fork point\n",
                             "the working-tree copy must never be overwritten")

    def test_newer_working_copy_is_never_overridden(self):
        """A branch forked AFTER the base's directive commit and then edited
        the file holds a NEWER copy: the older base copy must not be
        delivered as 'current'."""
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "-b", "feature2", "origin/main")
        self.commit_file(repo, REL, DIRECTIVE + "a newer rule\n", "newer")
        self.assert_silent(self.run_stream(repo))

    def test_other_users_file_is_not_delivered(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, ".claude/streams/someone-else.md", "x\n", "other")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.assert_silent(self.run_stream(repo))

    def test_not_a_git_repo_is_silent(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        self.assert_silent(self.run_stream(plain))

    def test_no_resolvable_base_is_silent(self):
        """Default branch `trunk`, no origin/HEAD symref -> no base resolves
        -> silent. Setting origin/HEAD is the positive control."""
        bare = self.make_remote("origin", head="trunk")
        repo = self.make_clone(bare, branch="trunk")
        self.commit_file(repo, SHARED, "shared\n", "init")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.ok(repo, "checkout", "-q", "trunk")
        self.commit_file(repo, REL, DIRECTIVE, "directive")
        self.ok(repo, "push", "-q", "origin", "trunk")
        self.ok(repo, "checkout", "-q", "feature")
        self.g(repo, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        self.assert_silent(self.run_stream(repo))
        self.ok(repo, "remote", "set-head", "origin", "trunk")
        self.assertIn(DIRECTIVE.strip(), self.run_stream(repo).stdout)

    def test_detached_head_still_delivers(self):
        repo, _ = self.base_repo()
        self.ok(repo, "checkout", "-q", "--detach", "HEAD")
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("detached", r.stdout)

    def test_oversized_file_truncates_on_a_utf8_boundary(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, SHARED, "shared\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        # 'ž' is 2 bytes: an odd prefix puts the 12000-byte cut mid-character
        self.push_from_other(bare, REL, "x" + "ž" * 9000 + "\n")
        env = dict(self.env)
        env["LC_ALL"] = "C"
        r = subprocess.run(["bash", str(STREAM_HOOK)], cwd=repo,
                           capture_output=True, timeout=60, env=env, input=b"")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = r.stdout.decode("utf-8")  # strict: raises on a broken sequence
        self.assertIn("truncated", text)
        self.assertLess(len(r.stdout), 13500)


class TestIntegrationBranchBase(_RepoFixture):
    def test_develop_preferred_over_default_branch(self):
        """odoo-erp: default branch `main` (old copy), directives written on
        `develop` -> the develop copy is delivered, never main's."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, REL, "old main copy\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "push", "-q", "origin", "main:develop")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.push_from_other(bare, REL, DIRECTIVE, branch="develop")
        r = self.run_stream(repo)
        self.assertIn("origin/develop", r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertNotIn("old main copy", r.stdout)


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
        self.assert_silent(self.run_stream(repo))

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
        self.push_from_other(bare, "CLAUDE.md", "new project rule\n")
        r = self.run_stream(repo)
        self.assertNotIn(HEADER, r.stdout)
        warn = [ln for ln in r.stdout.splitlines() if ln.startswith("WARNING")]
        self.assertEqual(len(warn), 1, r.stdout)


class TestUpstreamBase(_RepoFixture):
    def _fork_layout(self):
        """upstream = the project, origin = the fork. Both carry the streams
        dir at the fork point; the local clone fetched upstream once."""
        up = self.make_remote("upstream")
        seed = self.make_clone(up, "seed")
        self.commit_file(seed, SHARED, "shared\n", "init")
        self.ok(seed, "push", "-q", "origin", "main")
        fork = self.make_remote("fork")
        self.ok(seed, "push", "-q", fork, "main")
        repo = self.make_clone(fork)
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "remote", "add", "upstream", up)
        self.ok(repo, "fetch", "-q", "upstream")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        return repo, seed, fork

    def test_upstream_default_preferred_over_origin(self):
        """The project adds the directive on upstream only (after the local
        clone's last upstream fetch) -> delivered from upstream/main, with no
        upstream/HEAD symref set."""
        repo, seed, _ = self._fork_layout()
        self.commit_file(seed, REL, DIRECTIVE, "stream directive")
        self.ok(seed, "push", "-q", "origin", "main")
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("upstream/main", r.stdout)

    def test_unresolvable_upstream_never_falls_back_to_the_fork(self):
        """An upstream remote with no usable ref: the fork's own copy is
        never delivered as the project's binding directive."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, SHARED, "shared\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.push_from_other(bare, REL, "fork-owned text\n")
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "remote", "add", "upstream",
                os.path.join(self.root, "does-not-exist.git"))
        self.assert_silent(self.run_stream(repo))


class TestReviewRound2(_RepoFixture):
    """Findings of the re-review of the reworked hook."""

    def test_subdirectory_cwd_never_overrides_a_newer_copy(self):
        """Pathspecs are repo-root relative: a session started in a
        subdirectory must behave exactly like one started at the root."""
        repo, _ = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "-b", "feature2", "origin/main")
        self.commit_file(repo, REL, DIRECTIVE + "a newer rule\n", "newer")
        sub = os.path.join(repo, "sub")
        os.makedirs(sub)
        self.assert_silent(self.run_stream(sub))

    def test_subdirectory_cwd_still_delivers(self):
        repo, _ = self.base_repo()
        sub = os.path.join(repo, "sub")
        os.makedirs(sub)
        r = self.run_stream(sub)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("ABSENT", r.stdout)

    def test_stale_upstream_ref_predating_streams_is_still_fetched(self):
        """The local upstream ref predates `.claude/streams/` (nothing else
        ever fetches upstream), but origin's twin carries it -> this is a
        stream project, so upstream is fetched and the directive delivered."""
        up = self.make_remote("upstream")
        seed = self.make_clone(up, "seed")
        self.commit_file(seed, "f", "v1\n", "init")
        self.ok(seed, "push", "-q", "origin", "main")
        fork = self.make_remote("fork")
        repo = self.make_clone(fork)
        self.ok(repo, "remote", "add", "upstream", up)
        self.ok(repo, "fetch", "-q", "upstream")
        # the project adds the streams dir; the fork syncs it
        self.commit_file(seed, SHARED, "shared\n", "streams dir")
        self.ok(seed, "push", "-q", "origin", "main")
        self.ok(seed, "push", "-q", fork, "main")
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "-B", "main", "origin/main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.commit_file(seed, REL, DIRECTIVE, "stream directive")
        self.ok(seed, "push", "-q", "origin", "main")
        r = self.run_stream(repo)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("upstream/main", r.stdout)

    def _stale_develop_repo(self):
        """A dev/main-style repo: `develop` once existed (with an OLD copy of
        the stream file) and was deleted on the remote, but the local
        origin/develop tracking ref was never pruned. main has the current
        copy."""
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, REL, "OLD-DEVELOP-TEXT\n", "init")
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "push", "-q", "origin", "main:develop")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "fetch", "-q", "origin")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.ok(repo, "rm", "-q", REL)
        self.ok(repo, "commit", "-qm", "drop local copy")
        # deleted from ANOTHER clone, so this clone's tracking ref goes stale
        other = self.push_from_other(bare, REL, DIRECTIVE)
        self.ok(other, "push", "-q", "origin", "--delete", "develop")
        self.assertTrue(self.ok(repo, "rev-parse", "refs/remotes/origin/develop"))
        return repo

    def test_stale_unpruned_develop_is_skipped_on_compact(self):
        repo = self._stale_develop_repo()
        r = self.run_stream(repo)
        self.assertNotIn("OLD-DEVELOP-TEXT", r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertIn("origin/main", r.stdout)

    def test_stale_unpruned_develop_is_skipped_on_startup(self):
        repo = self._stale_develop_repo()
        r = self.run_fetch(repo)
        self.assertNotIn("OLD-DEVELOP-TEXT", r.stdout)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.fetch_count(), 1)

    def test_private_fetch_never_recurses_into_submodules(self):
        allow = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
                 "GIT_CONFIG_VALUE_0": "always"}
        self.env.update(allow)
        sub_bare = self.make_remote("sub")
        subseed = self.make_clone(sub_bare, "subseed")
        self.commit_file(subseed, "s", "s1\n", "s1")
        self.ok(subseed, "push", "-q", "origin", "main")
        bare = self.make_remote("origin")
        seed = self.make_clone(bare, "seed")
        self.ok(seed, "submodule", "add", "-q", sub_bare, "sm")
        self.commit_file(seed, SHARED, "shared\n", "init")
        self.ok(seed, "push", "-q", "origin", "main")
        repo = os.path.join(self.root, "repo")
        self.ok(self.root, "clone", "-q", "--recurse-submodules", bare, repo)
        self.ok(repo, "config", "user.email", "t@t")
        self.ok(repo, "config", "user.name", "t")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        # the submodule advances and the superproject bumps it on main
        self.commit_file(subseed, "s", "s2\n", "s2")
        self.ok(subseed, "push", "-q", "origin", "main")
        self.ok(os.path.join(seed, "sm"), "pull", "-q", "origin", "main")
        self.ok(seed, "add", "sm")
        self.commit_file(seed, REL, DIRECTIVE, "bump + directive")
        self.ok(seed, "push", "-q", "origin", "main")
        sm = os.path.join(repo, "sm")
        before = self.ok(sm, "for-each-ref", "refs/remotes")
        r = self.run_stream(repo, allow)
        self.assertIn(DIRECTIVE.strip(), r.stdout)
        self.assertEqual(self.ok(sm, "for-each-ref", "refs/remotes"), before,
                         "the submodule's remote-tracking refs must not move")

    def test_newer_private_ref_wins_over_stale_tracking_ref_offline(self):
        """An earlier compact fetched a newer base into the private ref; the
        remote is now unreachable -> the newer private copy is read, not the
        older remote-tracking one."""
        repo, bare = self.base_repo()
        self.ok(repo, "fetch", "-q", "origin")          # tracking has V1
        self.push_from_other(bare, REL, "V2 directive\n")
        self.assertIn("V2 directive", self.run_stream(repo).stdout)  # private = V2
        self.ok(repo, "remote", "set-url", "origin",
                os.path.join(self.root, "gone.git"))
        r = self.run_stream(repo)
        self.assertIn("V2 directive", r.stdout)

    def test_output_stays_under_claude_code_inline_limit(self):
        bare = self.make_remote("origin")
        repo = self.make_clone(bare)
        self.commit_file(repo, SHARED, "shared\n", "init",
                         when=time.time() - 30 * 86400)
        self.ok(repo, "push", "-q", "origin", "main")
        self.ok(repo, "remote", "set-head", "origin", "-a")
        self.ok(repo, "checkout", "-q", "-b", "feature")
        self.push_from_other(bare, REL, "r" * 20000 + "\n")
        r = self.run_stream(repo)
        self.assertIn("WARNING", r.stdout)
        self.assertIn("truncated", r.stdout)
        self.assertLess(len(r.stdout), 10000)

    def test_hangup_does_not_start_the_step(self):
        repo, _ = self.base_repo()
        fake_ssh = os.path.join(self.root, "slow-ssh.sh")
        with open(fake_ssh, "w") as fh:
            fh.write("#!/usr/bin/env bash\nsleep 3\nexit 1\n")
        os.chmod(fake_ssh, 0o755)
        self.ok(repo, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        env = dict(self.env)
        env.update({"GIT_TRACE": self.trace, "GIT_SSH_COMMAND": fake_ssh})
        proc = subprocess.Popen(["bash", str(FETCH_HOOK)], cwd=repo,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env)
        time.sleep(1.0)
        proc.send_signal(signal.SIGHUP)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 129)
        self.assertFalse(self.trace_has("get-url upstream"))


class TestHookConventions(TestCase):
    def test_hook_is_executable_and_strict(self):
        self.assertTrue(STREAM_HOOK.exists())
        self.assertTrue(os.access(STREAM_HOOK, os.X_OK))
        text = STREAM_HOOK.read_text()
        self.assertIn("set -euo pipefail", text)

    def test_registered_standalone_and_trapped_on_startup(self):
        data = json.loads(HOOKS_JSON.read_text())
        groups = data["hooks"]["SessionStart"]
        by_matcher = {}
        for grp in groups:
            cmds = [h["command"] for h in grp["hooks"]]
            by_matcher.setdefault(grp["matcher"], []).extend(cmds)
        for source in ("compact", "resume", "clear"):
            self.assertTrue(any("session-start-stream-directives.sh" in c
                                for c in by_matcher.get(source, [])),
                            (source, by_matcher))
            # the fetch hook (fast-forward + session_start heartbeat) must
            # never run mid-session
            self.assertFalse(any("session-start-fetch.sh" in c
                                 for c in by_matcher.get(source, [])), source)
        # startup reaches the step through the fetch hook's EXIT trap (after
        # its fetch), never as a racing parallel sibling
        self.assertFalse(any("session-start-stream-directives.sh" in c
                             for c in by_matcher.get("startup", [])))
        self.assertIn("session-start-stream-directives.sh", FETCH_HOOK.read_text())


if __name__ == "__main__":
    main()
