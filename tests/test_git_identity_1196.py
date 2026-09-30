"""#1196 — public repos commit under the owner's GitHub noreply identity by
default; the old history stays; the question is pre-answered.

Origin: 22 public fohmixer commits went out under a dev box's personal global
identity (fohmixer#3), and several project sessions asked the owner what to do
about it. Owner, 30.9.2026: keep the old history, noreply from now on, and
"netreba z toho robit vedu".

Offline: git runs for real on temp repos; every `gh` call goes through the
injected `FakeGh` runner, so no real GitHub account, repo or config is read or
written. `GIT_CONFIG_GLOBAL` points at a temp file so a write to global config
would be seen (and fails the test) instead of landing in the box's real one.
"""

import ast
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "pre-ask-auto-answer.sh"
DEEP = REPO / "skills" / "ask-before-assuming-deep" / "DEEP.md"

USER = {"id": 26905282, "login": "zbynekdrlik", "name": None}
NOREPLY = "26905282+zbynekdrlik@users.noreply.github.com"


class FakeGh:
    """`gh` answered from a fixture; git and everything else run for real."""

    def __init__(self, visibility=None, user=USER, user_rc=0, repo_rc=0,
                 gh_raises=None):
        self.visibility = visibility or {}
        self.user = user
        self.user_rc = user_rc
        self.repo_rc = repo_rc
        self.gh_raises = gh_raises
        self.calls = []
        self.gh_calls = []
        self.gh_kwargs = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv and argv[0] == "gh":
            self.gh_calls.append(list(argv))
            self.gh_kwargs.append(kw)
            if self.gh_raises is not None:
                raise self.gh_raises
            if argv[1:3] == ["api", "user"]:
                if self.user_rc:
                    return subprocess.CompletedProcess(
                        argv, self.user_rc, "", "HTTP 401: Bad credentials")
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(self.user), "")
            if argv[1:3] == ["repo", "view"]:
                if self.repo_rc:
                    return subprocess.CompletedProcess(
                        argv, self.repo_rc, "", "HTTP 502")
                vis = self.visibility.get(argv[3], "PRIVATE")
                return subprocess.CompletedProcess(argv, 0, vis + "\n", "")
            return subprocess.CompletedProcess(argv, 1, "", "unexpected gh")
        return subprocess.run(argv, **kw)

    def config_writes(self):
        """Every `git config` call that WRITES (not a --get read)."""
        out = []
        for c in self.calls:
            if c[:1] == ["git"] and "config" in c and "--get" not in c:
                out.append(c)
        return out


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args],
                          capture_output=True, text=True)


def make_repo(root, name, origin):
    path = Path(root) / name
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                        origin], check=True)
    return str(path)


def local(path, key):
    return _git(path, "config", "--local", "--get", key).stdout.strip()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gitid-1196-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.global_cfg = os.path.join(self.tmp, "global.gitconfig")
        env = m.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": self.global_cfg,
                                        "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        import cli_git_identity
        self.gi = cli_git_identity

    def install(self, roots, run):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = self.gi.install_step(home=self.home, roots=roots, run=run,
                                          app_shim=lambda: False)
        return result, out.getvalue(), err.getvalue()


class TestInstallStep(_Base):
    def test_public_repo_without_identity_gets_noreply(self):
        pub = make_repo(self.tmp, "fohmixer",
                        "https://github.com/zbynekdrlik/fohmixer.git")
        run = FakeGh({"zbynekdrlik/fohmixer": "PUBLIC"})
        _, out, _ = self.install([pub], run)
        self.assertEqual(local(pub, "user.email"), NOREPLY)
        # name null on the GitHub account → the login
        self.assertEqual(local(pub, "user.name"), "zbynekdrlik")
        self.assertIn("Git identity", out)

    def test_public_repo_with_different_identity_is_corrected(self):
        pub = make_repo(self.tmp, "iemmixer",
                        "git@github.com:zbynekdrlik/iemmixer.git")
        _git(pub, "config", "user.name", "Some Person")
        _git(pub, "config", "user.email", "student@school.example")
        run = FakeGh({"zbynekdrlik/iemmixer": "PUBLIC"})
        self.install([pub], run)
        self.assertEqual(local(pub, "user.email"), NOREPLY)
        self.assertEqual(local(pub, "user.name"), "zbynekdrlik")

    def test_name_from_account_when_set(self):
        pub = make_repo(self.tmp, "p", "https://github.com/o/p")
        user = {"id": 7, "login": "octo", "name": "Octo Cat"}
        self.install([pub], FakeGh({"o/p": "PUBLIC"}, user=user))
        self.assertEqual(local(pub, "user.email"),
                         "7+octo@users.noreply.github.com")
        self.assertEqual(local(pub, "user.name"), "Octo Cat")

    def test_private_repo_is_untouched(self):
        priv = make_repo(self.tmp, "secret",
                         "https://github.com/zbynekdrlik/secret.git")
        run = FakeGh({"zbynekdrlik/secret": "PRIVATE"})
        self.install([priv], run)
        self.assertEqual(local(priv, "user.email"), "")
        self.assertEqual(local(priv, "user.name"), "")
        self.assertEqual(run.config_writes(), [])

    def test_global_config_is_never_written(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        priv = make_repo(self.tmp, "priv", "https://github.com/zbynekdrlik/priv")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})
        self.install([pub, priv], run)
        self.assertFalse(os.path.exists(self.global_cfg),
                         "the global git config was written")
        for c in run.calls:
            self.assertNotIn("--global", c)
            self.assertNotIn("--system", c)

    def test_already_correct_repo_is_a_noop(self):
        pub = make_repo(self.tmp, "ok", "https://github.com/zbynekdrlik/ok")
        run = FakeGh({"zbynekdrlik/ok": "PUBLIC"})
        self.install([pub], run)
        # second install: identity cached, repo already correct
        run2 = FakeGh({"zbynekdrlik/ok": "PUBLIC"})
        self.install([pub], run2)
        self.assertEqual(run2.gh_calls, [], "a no-op box must cost zero gh calls")
        self.assertEqual(run2.config_writes(), [])
        self.assertEqual(local(pub, "user.email"), NOREPLY)

    def test_non_github_origin_is_untouched(self):
        other = make_repo(self.tmp, "gl", "https://gitlab.com/x/y.git")
        bare = make_repo(self.tmp, "noorigin", None)
        run = FakeGh()
        self.install([other, bare], run)
        self.assertEqual(run.gh_calls, [])
        self.assertEqual(run.config_writes(), [])

    def test_private_visibility_is_cached(self):
        priv = make_repo(self.tmp, "priv", "https://github.com/zbynekdrlik/priv")
        self.install([priv], FakeGh())
        run2 = FakeGh()
        self.install([priv], run2)
        self.assertEqual(
            [c for c in run2.gh_calls if c[1:3] == ["repo", "view"]], [],
            "a private repo's visibility is re-queried on every install")

    def test_gh_user_failure_is_loud_and_not_fatal(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"}, user_rc=1)
        result, _, err = self.install([pub], run)   # must not raise
        self.assertIn("WARNING", err)
        self.assertIn("gh api user", err)
        self.assertEqual(local(pub, "user.email"), "")
        # one failing identity lookup is reported once, not per checkout
        self.assertEqual(len([c for c in run.gh_calls
                              if c[1:3] == ["api", "user"]]), 1)
        self.assertIsNotNone(result)

    def test_gh_visibility_failure_is_loud_and_not_fatal(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh(repo_rc=1)
        _, _, err = self.install([pub], run)
        self.assertIn("WARNING", err)
        self.assertEqual(local(pub, "user.email"), "")

    def test_unexpected_exception_never_breaks_install(self):
        def boom():
            raise RuntimeError("walk exploded")
        _, _, err = self.install(boom, FakeGh())
        self.assertIn("FAILED", err)

    def test_roots_may_be_a_callable(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        self.install(lambda: [pub], FakeGh({"zbynekdrlik/pub": "PUBLIC"}))
        self.assertEqual(local(pub, "user.email"), NOREPLY)

    def test_real_runner_under_test_touches_nothing(self):
        called = []

        def roots():
            called.append(1)
            return []
        out = io.StringIO()
        with redirect_stdout(out), \
                m.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}):
            self.gi.install_step(home=self.home, roots=roots, run=None)
        self.assertEqual(called, [], "the real runner walked checkouts under test")
        self.assertIn("skipped under test", out.getvalue())


class TestReviewFixes(_Base):
    """Round-1 review findings on #1196 (F3–F7)."""

    def test_app_token_shim_box_is_skipped_quietly(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.gi.install_step(home=self.home, roots=[pub], run=run,
                                 app_shim=lambda: True)
        self.assertEqual(run.gh_calls, [])
        self.assertEqual(local(pub, "user.email"), "")
        self.assertIn("app-token", out.getvalue())
        self.assertNotIn("WARNING", err.getvalue())

    def test_onboard_step_with_real_home_is_inert_under_test(self):
        pub = make_repo(self.tmp, "proj", "https://github.com/zbynekdrlik/proj")
        run = FakeGh({"zbynekdrlik/proj": "PUBLIC"})
        with m.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}):
            step = self.gi.onboard_step(pub, run=run)       # no home given
        self.assertEqual(run.gh_calls, [])
        self.assertEqual(step["status"], "skipped")
        self.assertIn("under test", step["detail"])
        self.assertEqual(local(pub, "user.email"), "")

    def test_claude_code_plugin_clones_are_never_touched(self):
        plug = make_repo(os.path.join(self.home, ".claude", "plugins",
                                      "marketplaces"), "caveman",
                         "https://github.com/JuliusBrussee/caveman.git")
        run = FakeGh({"JuliusBrussee/caveman": "PUBLIC"})
        self.install([plug], run)
        self.assertEqual(run.gh_calls, [])
        self.assertEqual(local(plug, "user.email"), "")

    def test_origin_regex_edge_cases(self):
        cases = {
            "ssh://git@github.com:22/o/r.git": "o/r",
            "ssh://git@github.com/o/r": "o/r",
            "git@github.com:o/r.git": "o/r",
            "https://github.com/o/r/": "o/r",
            "https://token@github.com/o/r.git": "o/r",
            "https://notgithub.com/o/r": None,
            "https://github.company.example/o/r": None,
        }
        for i, (url, want) in enumerate(cases.items()):
            path = make_repo(self.tmp, "edge%d" % i, url)
            self.assertEqual(self.gi.origin_slug(path), want, url)

    def test_identity_cache_expires(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        old = {"identity": {"name": "stale", "email": "1+stale@x", "ts": 0}}
        self.gi.save_cache(self.home, old)
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})
        self.install([pub], run)
        self.assertEqual(len([c for c in run.gh_calls
                              if c[1:3] == ["api", "user"]]), 1)
        self.assertEqual(local(pub, "user.email"), NOREPLY)


class TestReviewRound2(_Base):
    """Round-2 review findings on #1196 (gh timeout, crash paths, TTL)."""

    def test_every_gh_call_is_bounded_by_a_timeout(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        run = FakeGh({"zbynekdrlik/pub": "PUBLIC"})
        self.install([pub], run)
        self.assertTrue(run.gh_kwargs)
        for kw in run.gh_kwargs:
            self.assertIsInstance(kw.get("timeout"), (int, float), kw)

    def test_hung_gh_is_loud_and_not_fatal(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        hung = subprocess.TimeoutExpired(["gh", "api", "user"], 10)
        _, _, err = self.install([pub], FakeGh(gh_raises=hung))
        self.assertIn("WARNING", err)
        self.assertNotIn("FAILED", err)
        self.assertEqual(local(pub, "user.email"), "")

    def test_onboard_survives_a_missing_gh_binary(self):
        pub = make_repo(self.tmp, "proj", "https://github.com/zbynekdrlik/proj")
        err = io.StringIO()
        with redirect_stderr(err):
            step = self.gi.onboard_step(
                pub, run=FakeGh(gh_raises=FileNotFoundError("gh")),
                home=self.home)
        self.assertEqual(step["status"], "skipped")
        self.assertIn("WARNING", err.getvalue())

    def test_corrupt_cache_shapes_do_not_crash(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        self.gi.save_cache(self.home, {"identity": "junk", "visibility": []})
        step = self.gi.onboard_step(pub, run=FakeGh({"zbynekdrlik/pub": "PUBLIC"}),
                                    home=self.home)
        self.assertEqual(step["status"], "applied")
        self.assertEqual(local(pub, "user.email"), NOREPLY)

    def test_corrupt_visibility_entry_is_requeried(self):
        pub = make_repo(self.tmp, "pub", "https://github.com/zbynekdrlik/pub")
        self.gi.save_cache(self.home, {
            "identity": {"name": "zbynekdrlik", "email": NOREPLY,
                         "ts": time.time()},
            "visibility": {"zbynekdrlik/pub": [5, time.time()]}})
        step = self.gi.onboard_step(pub, run=FakeGh({"zbynekdrlik/pub": "PUBLIC"}),
                                    home=self.home)
        self.assertEqual(step["status"], "applied")
        self.assertEqual(local(pub, "user.email"), NOREPLY)

    def test_stale_private_visibility_is_requeried(self):
        priv = make_repo(self.tmp, "priv", "https://github.com/zbynekdrlik/priv")
        self.gi.save_cache(self.home, {
            "identity": {"name": "zbynekdrlik", "email": NOREPLY,
                         "ts": time.time()},
            "visibility": {"zbynekdrlik/priv": ["PRIVATE", 0]}})
        run = FakeGh({"zbynekdrlik/priv": "PUBLIC"})    # it went public
        self.install([priv], run)
        self.assertEqual(local(priv, "user.email"), NOREPLY)


class TestWiring(_Base):
    def test_cmd_install_calls_the_step_helper(self):
        src = inspect.getsource(airuleset.cmd_install)
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else
                 getattr(n.func, "id", None)
                 for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
        self.assertIn("_git_identity_install_step", calls)

    def test_helper_uses_the_checkout_walk(self):
        seen = {}

        def fake_step(home=None, roots=None, run=None):
            seen["home"], seen["roots"] = home, roots
        with m.patch.object(self.gi, "install_step", side_effect=fake_step):
            airuleset._git_identity_install_step()
        self.assertEqual(seen["home"], str(airuleset.CLAUDE_DIR.parent))
        self.assertTrue(callable(seen["roots"]))

    def test_onboard_sets_identity_on_a_public_repo(self):
        import cli_onboard as ob
        pub = make_repo(self.tmp, "proj", "https://github.com/zbynekdrlik/proj")
        run = FakeGh({"zbynekdrlik/proj": "PUBLIC"})
        step = self.gi.onboard_step(pub, run=run, home=self.home)
        self.assertEqual(step["step"], "git_identity")
        self.assertEqual(step["status"], "applied")
        self.assertEqual(local(pub, "user.email"), NOREPLY)
        # second run is satisfied, zero writes
        run2 = FakeGh({"zbynekdrlik/proj": "PUBLIC"})
        again = self.gi.onboard_step(pub, run=run2, home=self.home)
        self.assertEqual(again["status"], "satisfied")
        self.assertEqual(run2.config_writes(), [])
        self.assertIn("git_identity", ob.STEP_ORDER)

    def test_onboard_dry_run_writes_nothing(self):
        pub = make_repo(self.tmp, "proj", "https://github.com/zbynekdrlik/proj")
        run = FakeGh({"zbynekdrlik/proj": "PUBLIC"})
        step = self.gi.onboard_step(pub, run=run, dry_run=True, home=self.home)
        self.assertEqual(step["status"], "would-apply")
        self.assertEqual(local(pub, "user.email"), "")

    def test_onboard_gh_failure_is_a_skipped_step(self):
        pub = make_repo(self.tmp, "proj", "https://github.com/zbynekdrlik/proj")
        err = io.StringIO()
        with redirect_stderr(err):
            step = self.gi.onboard_step(pub, run=FakeGh(user_rc=1),
                                        home=self.home)
        self.assertEqual(step["status"], "skipped")
        self.assertIn("WARNING", err.getvalue())

    def test_onboard_project_runs_the_step(self):
        import cli_onboard
        src = inspect.getsource(cli_onboard.onboard_project)
        self.assertIn("cli_git_identity.onboard_step", src)


class TestPreAnswered(unittest.TestCase):
    def _ask(self, text):
        payload = json.dumps({"tool_input": {"questions": [{"question": text}]}})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True,
                              env=hermetic_hook_env(self))

    def test_english_old_commit_email_question_blocked(self):
        r = self._ask("22 old commits in the public repo carry a personal "
                      "author email. Should I rewrite history or keep it?")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("noreply", r.stderr)
        self.assertIn("keep", r.stderr.lower())

    def test_english_author_identity_question_blocked(self):
        r = self._ask("Which author identity should commits in this public "
                      "repo use from now on?")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_slovak_question_blocked(self):
        r = self._ask("Staré commity vo verejnom repe majú osobný e-mail "
                      "autora. Prepísať históriu, alebo nechať?")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_slovak_identity_question_blocked(self):
        r = self._ask("Pod akou identitou autora commitovať vo verejnom repe?")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unrelated_commit_question_allowed(self):
        r = self._ask("Which wording for the commit summary shown on the "
                      "dashboard: 'Changes' or 'History'?")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_history_view_ux_question_allowed(self):
        r = self._ask("Should the history view show the author avatar or "
                      "initials?")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_commit_list_ux_question_allowed(self):
        r = self._ask("Should the commit list show the author name or login?")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_review_false_positives_allowed(self):
        for q in (
            "Should the public changelog list each commit's author?",
            "Chceš verejnú stránku s históriou objednávok aj s menom autora?",
            "Should the public API require authorization before committing "
            "the transaction?",
            "Mám prepísať autorizáciu v commite, ktorý rozbil verejné API?",
            "Should I rewrite the identity provider integration? The last "
            "commit broke login.",
            "The public repo history contains a leaked API token in the "
            "author's config file — rewrite history to purge it?",
        ):
            with self.subTest(q=q):
                self.assertEqual(self._ask(q).returncode, 0, q)

    def test_review_incident_phrasings_blocked(self):
        for q in (
            "Old commits show my personal e-mail. Should I force-push to "
            "fix them?",
            "Staré commity majú môj súkromný e-mail, prepísať ich?",
            "The fohmixer repo is public and 22 commits carry a student "
            "email — what should I do?",
            "Old commits leaked my personal name. Rewrite or leave?",
        ):
            with self.subTest(q=q):
                self.assertEqual(self._ask(q).returncode, 2, q)

    def test_review2_false_positives_allowed(self):
        for q in (
            "Odteraz má história faktúr zobrazovať meno účtovníka?",
            "Should the release notes for the public repo credit each "
            "commit author by name?",
            "Which name should the public GitHub repo have after the "
            "rename? The last commit already uses the new one.",
            "Which file name should the public repo's changelog use? I "
            "will commit it next.",
            "From now on, should commit messages include the ticket name?",
            "Should the committers page on our public repo site list "
            "names alphabetically?",
            "Rewrite the history section of the README to mention the "
            "original author name?",
            "Should the e-mail digest list old commits in the public repo?",
        ):
            with self.subTest(q=q):
                self.assertEqual(self._ask(q).returncode, 0, q)

    def test_action_keyed_probes(self):
        allowed = (
            "Should I send personal email alerts for failed commits?",
            "Should private email notifications list commits?",
            "Should I amend the last commit message typo?",
            "Should the commit author field show full name in the new UI?",
            "Should I force-push the rebased feature branch?",
            # round 3: no public-repo / old-commit context — may be a
            # private repo, where the pre-answer does not apply
            "Which git identity should I use for commits here?",
        )
        blocked = (
            "Mám prepísať históriu commitov s mojím osobným e-mailom?",
            "Majú staré commity ostať s mojím študentským e-mailom?",
        )
        for q in allowed:
            with self.subTest(allowed=q):
                self.assertEqual(self._ask(q).returncode, 0, q)
        for q in blocked:
            with self.subTest(blocked=q):
                self.assertEqual(self._ask(q).returncode, 2, q)

    def test_review3_false_positives_allowed(self):
        for q in (
            "Should the history tab show the author's personal name or "
            "username?",
            "Mám v histórii zmien zobraziť osobné meno autora úpravy?",
            "Chceš, aby autor v histórii dokumentu bol zobrazený osobným "
            "menom?",
            "Should the CHANGELOG credit the author by personal name or "
            "GitHub handle? I'll commit it next.",
            "Old commits contain customer personal e-mails from a fixture "
            "file. Rewrite history to remove them (GDPR)?",
            "Staré commity obsahujú osobné e-maily zákazníkov z testovacích "
            "dát. Prepísať históriu kvôli GDPR?",
            "Commits contain student names in the test fixtures. Rewrite "
            "history before publishing?",
            "Which user.email should the CI bot use for its version-bump "
            "commits?",
            "Should the release bot commit under the noreply identity or a "
            "dedicated bot account?",
            "Should the Discord notification show the commit author's "
            "personal name or the noreply login?",
            "Commits by the external author David carry his personal "
            "e-mail. Should his future commits use his work e-mail?",
            "Should I use git filter-repo to split the monorepo history "
            "into two repos? The author metadata stays.",
            "Mám prepísať históriu commitov, aby sme odstránili veľký "
            "binárny súbor? Autor je bot.",
            "Should I amend the commit to add the missing Co-Authored-By "
            "trailer for the author?",
            "Should I rebase and force-push to drop the WIP commits? The "
            "author is me.",
        ):
            with self.subTest(q=q):
                self.assertEqual(self._ask(q).returncode, 0, q)

    def test_review3_natural_phrasings_blocked(self):
        for q in (
            "Some commits in fohmixer were authored with a personal e-mail. "
            "Rewrite or keep?",
            "Should I change the author e-mail on the old commits?",
            "Mám zmeniť e-mail autora v starých commitoch?",
            "Which e-mail should I commit with from now on in this public "
            "repo?",
            "Aký e-mail mám odteraz používať pri commitoch vo verejnom repe?",
            "The public repo's history has 22 commits under the student "
            "e-mail. What now?",
            "Old commits show my university e-mail. Keep them?",
            "22 public commits carry my Gmail address. Rewrite them?",
        ):
            with self.subTest(q=q):
                self.assertEqual(self._ask(q).returncode, 2, q)

    def test_terms_split_across_options_still_judged_as_one(self):
        payload = json.dumps({"tool_input": {"questions": [{
            "question": "Old commits in the public repo carry my personal "
                        "e-mail — what now?",
            "options": [{"label": "Rewrite history"},
                        {"label": "Keep it"}]}]}})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True,
                           env=hermetic_hook_env(self))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_uppercase_slovak_blocks_in_the_c_locale(self):
        payload = json.dumps({"tool_input": {"questions": [{
            "question": "STARÉ COMMITY MAJÚ MÔJ SÚKROMNÝ E-MAIL, PREPÍSAŤ "
                        "ICH?"}]}}, ensure_ascii=False)
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True,
                           env=hermetic_hook_env(self, LC_ALL="C", LANG="C"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unrelated_email_feature_question_allowed(self):
        r = self._ask("Should the order confirmation email show the "
                      "customer's name or the company name?")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDeepRow(unittest.TestCase):
    def test_pre_answered_row_present(self):
        text = DEEP.read_text(encoding="utf-8")
        row = [ln for ln in text.splitlines()
               if ln.startswith("|") and "noreply" in ln]
        self.assertEqual(len(row), 1, "exactly one pre-answered noreply row")
        self.assertIn("old history", row[0])
        self.assertIn("never ask", row[0].lower())
        self.assertIn("#1196", row[0])


if __name__ == "__main__":
    unittest.main()
