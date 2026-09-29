"""#1176 — checkout freshness is a continuously enforced property.

The recurrence (#1127, then the gk FLOW checkout ~2 600 commits behind from
22.9. to 29.9.2026): the only fast-forward path was the SessionStart
`startup` hook, every managed session starts with `claude -c` (a `resume`
source) and then lives for days. These tests drive REAL temp git repos (a bare
origin, a clone, commits pushed to origin afterwards) through:

  * the SessionStart commands `settings/hooks.json` registers for `resume`
    (the launcher's real start mode);
  * the watchdog Job 53 leaf `watchdog/checkout_freshness.py`, which must
    bring a clean base checkout current within ONE job period with no session
    event at all, and never touch a dirty / diverged / work-branch checkout —
    only report it;
  * the footer `stale N` (only past N hours) and the `status` rows;
  * the ONE shared safety predicate (`cli_checkout_freshness.ff_verdict`)
    used by both the hook and the job.
"""

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

HOOKS_JSON = REPO / "settings" / "hooks.json"
FETCH_HOOK = REPO / "hooks" / "session-start-fetch.sh"
T0 = 1_790_000_000.0


def _cf():
    import cli_checkout_freshness
    return cli_checkout_freshness


def _job():
    import watchdog.checkout_freshness as job
    return job


class _Repos(unittest.TestCase):
    """bare origin (branch `develop`) + a clone on `develop` + a second
    clone used to push new origin commits after the first clone exists."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="t1176-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = hermetic_hook_env(self)
        self.home = self.env["HOME"]
        self.bare = os.path.join(self.root, "origin.git")
        self.clone = os.path.join(self.root, "clone")
        self.other = os.path.join(self.root, "other")
        self.g(self.root, "init", "-q", "--bare", "-b", "develop", self.bare)
        self.g(self.root, "clone", "-q", self.bare, self.clone)
        self._ident(self.clone)
        self.g(self.clone, "checkout", "-q", "-b", "develop")
        self.write(self.clone, "CLAUDE.md", "rules v1\n")
        self.write(self.clone, "app.py", "v1\n")
        self.write(self.clone, "notes.txt", "tracked\n")
        self.g(self.clone, "add", "-A")
        self.g(self.clone, "commit", "-qm", "init")
        self.g(self.clone, "push", "-q", "origin", "develop")
        self.g(self.root, "clone", "-q", "--branch", "develop", self.bare, self.other)
        self._ident(self.other)

    def g(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=self.env)
        return r

    def _ident(self, repo):
        self.g(repo, "config", "user.email", "t@t")
        self.g(repo, "config", "user.name", "t")

    @staticmethod
    def write(repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def advance_origin(self, rel="app.py", text=None, n=1):
        """Push `n` commits touching `rel` to origin/develop from the OTHER
        clone — the managed checkout's tree is never touched."""
        self.g(self.other, "pull", "-q", "--ff-only")
        for i in range(n):
            self.write(self.other, rel, text or "origin change %d\n" % i)
            self.g(self.other, "add", "-f", "--", rel)   # -f: an ignored rel too
            r = self.g(self.other, "commit", "-qm", "origin %s %d" % (rel, i))
            assert r.returncode == 0, r.stderr
        r = self.g(self.other, "push", "-q", "origin", "develop")
        assert r.returncode == 0, r.stderr

    def head(self, repo=None):
        return self.g(repo or self.clone, "rev-parse", "HEAD").stdout.strip()

    def origin_head(self):
        return self.g(self.other, "rev-parse", "HEAD").stdout.strip()

    def run_job(self, now, **kw):
        kw.setdefault("checkouts", [{"path": self.clone, "bases": ["develop"],
                                     "source": "test"}])
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            return _job().run_job(now, home=self.home, **kw)

    def entry(self):
        status = _cf().read_status(self.home)
        return (status or {}).get("checkouts", {}).get(self.clone)


class TestFlowWindowRecurrence(_Repos):
    """The 22.–29.9 gk FLOW case: a clean `develop` checkout, a session
    started via `claude -c` (SessionStart source `resume`) that never
    restarts, origin moving on underneath it."""

    def _run_resume_session_start(self):
        groups = json.loads(HOOKS_JSON.read_text())["hooks"]["SessionStart"]
        cmds = [h["command"] for grp in groups if grp.get("matcher") == "resume"
                for h in grp["hooks"]]
        self.assertTrue(cmds, "SessionStart must register commands for resume")
        payload = json.dumps({"session_id": "t1176", "source": "resume",
                              "cwd": self.clone})
        for cmd in cmds:
            cmd = cmd.replace("~/devel/airuleset", str(REPO))
            subprocess.run(["bash", "-c", cmd], cwd=self.clone, input=payload,
                           capture_output=True, text=True, env=self.env,
                           timeout=60)

    def test_claude_c_start_fast_forwards_the_checkout(self):
        self.advance_origin(n=3)
        self._run_resume_session_start()
        self.assertEqual(self.head(), self.origin_head(),
                         "a `claude -c` (resume) start must fast-forward a "
                         "clean base checkout — the launcher's real start mode")

    def test_long_lived_session_is_current_within_one_job_period(self):
        # boot: current, nothing to do
        self.run_job(T0)
        self.assertEqual(self.entry()["state"], "current")
        # the session keeps living — no SessionStart event ever again —
        # while origin moves on (the FLOW window's week)
        self.advance_origin(n=4)
        behind_head = self.head()
        self.run_job(T0 + 60)          # not due yet: cadence respected
        self.assertEqual(self.head(), behind_head)
        self.run_job(T0 + _job().INTERVAL_S)
        self.assertEqual(self.head(), self.origin_head(),
                         "within ONE job period the checkout must be current")
        e = self.entry()
        self.assertEqual(e["state"], "current")
        self.assertEqual(e["behind"], 0)
        self.assertEqual(e["ff"]["commits"], 4)
        with open(os.path.join(self.clone, "app.py")) as fh:
            self.assertEqual(fh.read(), "origin change 3\n")


class TestNeverTouchedOnlyReported(_Repos):

    def test_dirty_checkout_untouched_and_reported(self):
        self.advance_origin()
        # an uncommitted edit to an UNRELATED tracked file: --ff-only alone
        # would NOT refuse, so only the predicate can keep it untouched
        self.write(self.clone, "notes.txt", "LOCAL UNCOMMITTED\n")
        before = self.head()
        logs = self.run_job(T0)
        self.assertEqual(self.head(), before)
        with open(os.path.join(self.clone, "notes.txt")) as fh:
            self.assertEqual(fh.read(), "LOCAL UNCOMMITTED\n")
        e = self.entry()
        self.assertEqual((e["state"], e["reason"], e["behind"]),
                         ("lagging", "dirty", 1))
        self.assertTrue(any("dirty" in ln and self.clone in ln for ln in logs), logs)

    def test_diverged_base_is_never_merged(self):
        self.advance_origin()
        self.write(self.clone, "app.py", "LOCAL COMMIT\n")
        self.g(self.clone, "commit", "-qam", "local")
        before = self.head()
        self.run_job(T0)
        self.assertEqual(self.head(), before, "a diverged base must never move")
        parents = self.g(self.clone, "rev-list", "--parents", "-n1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 2, "no merge commit may be created")
        e = self.entry()
        self.assertEqual((e["state"], e["reason"]), ("lagging", "diverged"))

    def test_work_branch_rule_file_lag_reported(self):
        self.g(self.clone, "checkout", "-q", "-b", "feature/x")
        self.write(self.clone, "app.py", "work\n")
        self.g(self.clone, "commit", "-qam", "work")
        self.advance_origin(rel=".claude/rules/lens.md", text="new lens\n")
        before = self.head()
        self.run_job(T0)
        self.assertEqual(self.head(), before, "a work branch is never moved")
        e = self.entry()
        self.assertEqual(e["state"], "lagging")
        self.assertEqual(e["branch"], "feature/x")
        self.assertEqual(e["base"], "develop")
        self.assertIn("1 rule file(s) behind origin/develop", e["reason"])
        self.assertIsInstance(e["behind_since"], int)

    def test_work_branch_without_rule_changes_is_current(self):
        self.g(self.clone, "checkout", "-q", "-b", "feature/y")
        self.advance_origin(rel="app.py")
        self.run_job(T0)
        e = self.entry()
        self.assertEqual(e["state"], "current", e)

    def test_mid_merge_checkout_untouched(self):
        self.advance_origin()
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        Path(gd, "MERGE_HEAD").write_text(self.head() + "\n")
        before = self.head()
        self.run_job(T0)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.entry()["reason"], "in-progress (MERGE_HEAD)")

    def test_absent_path_recorded_never_an_alarm(self):
        missing = os.path.join(self.root, "nope")
        self.run_job(T0, checkouts=[{"path": missing, "bases": ["develop"],
                                     "source": "test"}])
        status = _cf().read_status(self.home)
        self.assertEqual(status["checkouts"][missing]["state"], "absent")


class TestFetchIsBounded(_Repos):

    def test_a_hanging_fetch_cannot_hang_the_job(self):
        fake_ssh = os.path.join(self.root, "fake-ssh")
        with open(fake_ssh, "w") as fh:
            fh.write("#!/bin/sh\nsleep 120 &\nsleep 120\n")
        os.chmod(fake_ssh, 0o755)
        self.g(self.clone, "remote", "set-url", "origin", "ssh://nohost/x.git")
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": fake_ssh}):
            started = time.monotonic()
            e = _job().check_checkout(
                {"path": self.clone, "bases": ["develop"], "source": "t"},
                T0, fetch_timeout=2)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 15, "the fetch bound must hold (process group killed)")
        self.assertEqual(e["state"], "lagging")
        self.assertIn("fetch origin failed (rc 124)", e["reason"])

    def test_job_stops_starting_checkouts_when_budget_is_low(self):
        logs = self.run_job(T0, budget_left=lambda: 5)
        self.assertEqual(self.entry(), None)
        self.assertTrue(any("hold:budget" in ln for ln in logs), logs)


class TestSharedPredicate(_Repos):

    def test_hook_delegates_to_the_shared_predicate(self):
        text = FETCH_HOOK.read_text()
        self.assertIn('cli_checkout_freshness.py" hook', text)
        for inline in ("merge --ff-only", "merge-base --is-ancestor",
                       "status --porcelain", "MERGE_HEAD"):
            self.assertNotIn(inline, text,
                             "the predicate must live ONLY in cli_checkout_freshness")

    def test_job_uses_the_same_predicate(self):
        self.advance_origin()
        cf = _cf()
        with mock.patch.object(cf, "ff_verdict", wraps=cf.ff_verdict) as spy, \
                mock.patch.object(cf, "fast_forward", wraps=cf.fast_forward) as ff:
            self.run_job(T0)
        spy.assert_called_once_with(self.clone, "origin")
        self.assertEqual(ff.call_count, 1)
        self.assertEqual(self.head(), self.origin_head())

    def test_hook_uses_the_same_predicate_live(self):
        self.advance_origin()
        self.write(self.clone, "notes.txt", "dirty\n")
        r = subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.clone,
                           capture_output=True, text=True, env=self.env,
                           timeout=60, input="{}")
        self.assertIn("working tree dirty — not fast-forwarding", r.stdout)

    def test_upstream_remote_preferred_for_fork_checkouts(self):
        self.g(self.clone, "remote", "rename", "origin", "upstream")
        self.advance_origin()
        self.run_job(T0)
        self.assertEqual(self.head(), self.origin_head())
        self.assertEqual(self.entry()["remote"], "upstream")


class TestHooksJson(unittest.TestCase):

    def _by_matcher(self):
        by = {}
        for grp in json.loads(HOOKS_JSON.read_text())["hooks"]["SessionStart"]:
            by.setdefault(grp["matcher"], []).extend(
                h["command"] for h in grp["hooks"])
        return by

    def test_fetch_hook_runs_on_startup_and_resume(self):
        by = self._by_matcher()
        for source in ("startup", "resume"):
            self.assertTrue(any("session-start-fetch.sh" in c
                                for c in by.get(source, [])), (source, by))
        # on resume the directives step rides the fetch hook's EXIT trap
        # (after its fetch) — never a racing parallel sibling
        self.assertFalse(any("session-start-stream-directives.sh" in c
                             for c in by.get("resume", [])), by)


class TestFooterAndStatus(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="t1176-home-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def _write(self, entries, ts=T0):
        p = Path(_cf().status_path(self.home))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": ts, "checkouts": entries}))

    def _lag(self, since, behind=2600, behind_since=None, reason="dirty"):
        return {"state": "lagging", "since": since, "behind": behind,
                "behind_since": behind_since, "reason": reason,
                "branch": "develop", "base": "develop"}

    def test_stale_shown_only_past_n_hours(self):
        cf = _cf()
        self._write({"/c": self._lag(since=T0 - 3600)})
        self.assertEqual(cf.footer_segment(self.home, now=T0), "",
                         "1 h of lag < the 6 h default: hidden")
        self._write({"/c": self._lag(since=T0 - 7 * 3600)})
        seg = cf.footer_segment(self.home, now=T0)
        self.assertIn("stale 2.6k", seg)

    def test_stale_is_measured_from_first_observed_lag_only(self):
        # review 1 finding 8: an old COMMIT date says nothing about when the
        # checkout fell behind — a week-old commit pushed now must not turn a
        # 15-min dirty snapshot into `stale`.
        cf = _cf()
        week = T0 - 7 * 86400
        self._write({"/c": self._lag(since=T0 - 1000, behind_since=week)})
        self.assertEqual(cf.footer_segment(self.home, now=T0), "")

    def test_hidden_when_current_or_watchdog_dead(self):
        cf = _cf()
        self._write({"/c": {"state": "current", "since": T0 - 86400}})
        self.assertEqual(cf.footer_segment(self.home, now=T0), "")
        self._write({"/c": self._lag(since=T0 - 86400)}, ts=T0 - 3 * 3600)
        self.assertEqual(cf.footer_segment(self.home, now=T0), "")

    def test_threshold_env_override_and_more_counter(self):
        cf = _cf()
        self._write({"/a": self._lag(since=T0 - 2 * 3600, behind=12),
                     "/b": self._lag(since=T0 - 2 * 3600, behind=5)})
        with mock.patch.dict(os.environ, {"AIRULESET_STALE_CHECKOUT_H": "1"}):
            seg = cf.footer_segment(self.home, now=T0)
        self.assertIn("stale 12 +1", seg)

    def test_status_lines_name_path_and_reason(self):
        cf = _cf()
        self._write({"/c": self._lag(since=T0 - 7 * 3600, reason="diverged"),
                     "/ok": {"state": "current"}})
        lines = cf.status_lines(self.home, now=T0)
        self.assertIn("2 checkout(s), 1 lagging, 1 stale", lines[0])
        self.assertTrue(any("STALE /c" in ln and "diverged" in ln for ln in lines))

    def test_statusline_shim_renders_the_segment(self):
        src = (REPO / "cli_deployer_glue.py").read_text()
        self.assertIn("cli_checkout_freshness.footer_segment()", src)


class TestDiscoveryAndWiring(unittest.TestCase):

    def test_discovery_windows_stream_registry_deduped(self):
        home = tempfile.mkdtemp(prefix="t1176-disc-")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "devel/odoo/odoo-erp/.git"))
        os.makedirs(os.path.join(home, "devel/other/.git"))  # registry = existing paths only
        reg = os.path.join(home, "reg.json")
        with open(reg, "w") as fh:
            json.dump([
                {"name": "odoo-erp", "host": "gk", "path": "~/devel/odoo/odoo-erp",
                 "work_branch": "develop", "default_branch": "main"},
                {"name": "other", "host": "gk", "path": "~/devel/other",
                 "work_branch": "dev", "default_branch": "main"},
                {"name": "elsewhere", "host": "dev1", "path": "~/devel/x"}], fh)
        wins = [{"name": "gk", "cwd": "~/devel/odoo/odoo-erp"},
                {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra",
                 "branch": "develop"}]
        got = _job().discover_checkouts(home=home, user="gatekeeper",
                                        windows=wins, registry_path=reg)
        paths = [c["path"] for c in got]
        self.assertEqual(paths, [os.path.join(home, "devel/odoo/odoo-erp"),
                                 os.path.join(home, "devel/odoo/odoo-erp-infra"),
                                 os.path.join(home, "devel/other")])
        self.assertEqual(got[1]["bases"], ["develop"])
        self.assertEqual(got[2]["bases"], ["dev", "main"])

    def test_stream_account_checkout_is_discovered(self):
        home = tempfile.mkdtemp(prefix="t1176-stream-")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "devel/odoo/odoo-erp/.git"))
        got = _job().discover_checkouts(home=home, user="montalu2", windows=[],
                                        registry_path=os.path.join(home, "none"))
        self.assertEqual([c["source"] for c in got], ["stream:montalu2"])

    def test_run_once_reaches_the_job_only_when_enabled(self):
        from test_run_once_characterization import _drive
        import watchdog as wd
        calls = []

        def _rec(now, **kw):
            calls.append(sorted(kw))
            return ["checkout-freshness: probe"]

        with mock.patch.object(wd.checkout_freshness, "run_job", side_effect=_rec):
            _drive({})
            self.assertEqual(calls, [])
            _labels, _calls, logs = _drive({"checkout_freshness_enabled": True})
        self.assertEqual(len(calls), 1)
        self.assertIn("budget_left", calls[0])
        self.assertIn("checkout-freshness: probe", logs)

    def test_cmd_watchdog_enables_job_53_in_production(self):
        import airuleset
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("checkout_freshness_enabled=True", src)

    def test_status_command_prints_the_rows(self):
        import airuleset
        self.assertIn("cli_checkout_freshness", inspect.getsource(airuleset.cmd_status))


class TestReviewOneCollisions(_Repos):
    """Review 1 (🔴 2): ignored local files the old A-only, quoted, full-path
    collision check let `--ff-only` overwrite."""

    def _ignored(self, rel, text="MY LOCAL DATA\n"):
        self.write(self.clone, ".gitignore", "%s\n" % rel.split("/")[0])
        self.g(self.clone, "add", ".gitignore")
        self.g(self.clone, "commit", "-qm", "ignore")
        self.g(self.clone, "push", "-q", "origin", "develop")
        self.write(self.clone, rel, text)

    def _assert_untouched(self, rel):
        before = self.head()
        self.run_job(T0)
        self.assertEqual(self.head(), before)
        with open(os.path.join(self.clone, rel)) as fh:
            self.assertEqual(fh.read(), "MY LOCAL DATA\n")
        self.assertTrue(self.entry()["reason"].startswith("collision"), self.entry())

    def test_rename_onto_an_ignored_file(self):
        self._ignored("new.txt")
        self.g(self.other, "pull", "-q", "--ff-only")
        self.g(self.other, "mv", "notes.txt", "new.txt")
        self.g(self.other, "commit", "-qm", "rename")
        self.g(self.other, "push", "-q", "origin", "develop")
        self._assert_untouched("new.txt")

    def test_non_ascii_path_onto_an_ignored_file(self):
        self._ignored("déjà.txt")
        self.advance_origin(rel="déjà.txt", text="origin\n")
        self._assert_untouched("déjà.txt")

    def test_directory_over_an_ignored_file(self):
        self._ignored("cfg")
        self.advance_origin(rel="cfg/settings.ini", text="origin\n")
        self._assert_untouched("cfg")

    def test_held_index_lock_is_never_raced(self):
        self.advance_origin()
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        Path(gd, "index.lock").write_text("")
        self.addCleanup(lambda: Path(gd, "index.lock").unlink(missing_ok=True))
        before = self.head()
        self.run_job(T0)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.entry()["reason"], "in-progress (index.lock)")


class TestReviewOneRemotes(_Repos):
    """Review 1 (🔴 3 / 🟡 4): the remote is the branch's own, and a checkout
    that cannot be proven current is never recorded `current`."""

    def _second_remote(self, name, branches=("develop",)):
        bare = os.path.join(self.root, "%s.git" % name)
        self.g(self.root, "clone", "-q", "--bare", self.bare, bare)
        self.g(self.clone, "remote", "add", name, bare)
        self.g(self.clone, "fetch", "-q", name)
        return bare

    def test_tracking_remote_wins_over_an_upstream_without_the_base(self):
        up = self._second_remote("upstream")
        self.g(self.clone, "push", "-q", "upstream", "develop:main")
        self.g(up, "symbolic-ref", "HEAD", "refs/heads/main")
        self.g(up, "branch", "-D", "develop")    # upstream carries only main
        self.g(self.clone, "fetch", "-q", "--prune", "upstream")
        self.g(self.clone, "branch", "-q", "--set-upstream-to=origin/develop")
        self.advance_origin(n=2)
        self.run_job(T0, checkouts=[{"path": self.clone, "source": "t",
                                     "bases": ["develop", "main"]}])
        self.assertEqual(self.head(), self.origin_head())
        self.assertEqual(self.entry()["remote"], "origin")

    def test_upstream_tracked_base_is_followed(self):
        up = self._second_remote("upstream")
        self.g(self.clone, "branch", "-q", "--set-upstream-to=upstream/develop")
        work = os.path.join(self.root, "upwork")
        self.g(self.root, "clone", "-q", "--branch", "develop", up, work)
        self._ident(work)
        self.write(work, "app.py", "upstream only\n")
        self.g(work, "commit", "-qam", "up")
        self.g(work, "push", "-q", "origin", "develop")
        self.advance_origin(text="origin only\n")   # origin moves elsewhere
        self.run_job(T0)
        with open(os.path.join(self.clone, "app.py")) as fh:
            self.assertEqual(fh.read(), "upstream only\n")
        self.assertEqual(self.entry()["remote"], "upstream")

    def test_no_remote_branch_is_lagging_not_current(self):
        self.g(self.clone, "checkout", "-q", "-b", "dev")
        self.run_job(T0, checkouts=[{"path": self.clone, "bases": ["dev"],
                                     "source": "t"}])
        e = self.entry()
        self.assertEqual((e["state"], e["reason"]),
                         ("lagging", "no origin/dev to compare with"))

    def test_failed_fetch_on_an_up_to_date_checkout_is_lagging(self):
        self.g(self.clone, "remote", "set-url", "origin",
               os.path.join(self.root, "gone.git"))
        self.run_job(T0)
        e = self.entry()
        self.assertEqual(e["state"], "lagging")
        self.assertIn("fetch origin failed", e["reason"])

    def test_repo_ssh_command_is_respected(self):
        cf = _cf()
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "", "GIT_SSH": ""}):
            self.assertIn("BatchMode", cf.ssh_batch_env(self.clone)["GIT_SSH_COMMAND"])
            self.g(self.clone, "config", "core.sshCommand", "ssh -i deploykey")
            self.assertEqual(cf.ssh_batch_env(self.clone), {})


class TestReviewOneProcessSafety(_Repos):
    """Review 1 (🔴 1 / 🟡 5-6 / 🟡 12 / 🟡 13): bounded calls end in SIGTERM
    first, a pipe-holding grandchild cannot hang the call, reads take no
    optional index lock, merge hooks do not run, the budget holds."""

    def _fake_git(self, body):
        d = os.path.join(self.root, "fakebin")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "git")
        with open(p, "w") as fh:
            fh.write("#!/bin/bash\n" + body)
        os.chmod(p, 0o755)
        return {"PATH": d + os.pathsep + os.environ["PATH"]}

    def test_pipe_holding_grandchild_cannot_hang_a_call(self):
        # a background child inherits OUR stdout pipe: a plain
        # subprocess.run(timeout=) kills `git` and then blocks on the pipe
        with mock.patch.dict(os.environ, self._fake_git("sleep 60 &\nsleep 60\n")):
            started = time.monotonic()
            rc, _ = _cf().run_git(self.clone, ["fetch"], timeout=2)
        self.assertEqual(rc, 124)
        self.assertLess(time.monotonic() - started, 15)

    def test_timeout_sends_sigterm_first(self):
        marker = os.path.join(self.root, "got-term")
        body = "trap 'touch %s; exit 143' TERM\nsleep 60 & wait\n" % marker
        with mock.patch.dict(os.environ, self._fake_git(body)):
            rc, _ = _cf().run_git(self.clone, ["merge"], timeout=1)
        self.assertEqual(rc, 124)
        self.assertTrue(os.path.exists(marker), "git must get SIGTERM to clean its locks")

    def test_reads_take_no_optional_index_lock(self):
        self.assertEqual(_cf().git_env()["GIT_OPTIONAL_LOCKS"], "0")

    def test_merge_hooks_do_not_run(self):
        hooks = os.path.join(self.root, "hooks")
        os.makedirs(hooks)
        marker = os.path.join(self.root, "post-merge-ran")
        with open(os.path.join(hooks, "post-merge"), "w") as fh:
            fh.write("#!/bin/sh\ntouch %s\n" % marker)
        os.chmod(os.path.join(hooks, "post-merge"), 0o755)
        self.g(self.clone, "config", "core.hooksPath", hooks)
        self.advance_origin()
        self.run_job(T0)
        self.assertEqual(self.head(), self.origin_head())
        self.assertFalse(os.path.exists(marker))

    def test_merge_deferred_when_the_sweep_cannot_afford_it(self):
        self.advance_origin()
        before = self.head()
        job = _job()
        self.run_job(T0, budget_left=lambda: job.PER_CHECKOUT_S + 1)
        self.assertEqual(self.head(), before)
        e = self.entry()
        self.assertIn("deferred", e["reason"])
        self.assertEqual(e["checked"], 0, "due again next sweep")
        self.run_job(T0 + 60)
        self.assertEqual(self.head(), self.origin_head())

    def test_job_wall_clock_holds_the_rest(self):
        ticks = iter([0, 0, 10 ** 6, 10 ** 6])
        two = [{"path": self.clone, "bases": ["develop"], "source": "a"},
               {"path": self.other, "bases": ["develop"], "source": "b"}]
        logs = self.run_job(T0, checkouts=two, clock=lambda: next(ticks))
        self.assertTrue(any("hold:budget — 1 of 2" in ln for ln in logs), logs)

    def test_timed_out_fetch_gets_the_longer_retry(self):
        # review 2 (M10): the effective bound of the fetch call itself — its
        # timeout AND the deadline it runs under — not only the recorded value
        job, cf = _job(), _cf()
        seen, real = [], cf.run_git

        def spy(cwd, args, timeout=10, env_extra=None, honor_deadline=True):
            if "fetch" in args:
                seen.append((timeout, cf.time_left()))
            return real(cwd, args, timeout, env_extra, honor_deadline)

        with mock.patch.object(cf, "run_git", spy):
            e = job.check_checkout({"path": self.clone, "bases": ["develop"],
                                    "source": "t"}, T0, prev={"fetch_rc": 124})
        self.assertEqual(e["fetch_timeout"], job.FETCH_TIMEOUT_LONG_S)
        (timeout, left), = seen
        self.assertEqual(timeout, job.FETCH_TIMEOUT_LONG_S)
        self.assertGreaterEqual(left, job.FETCH_TIMEOUT_LONG_S - 2,
                                "the per-checkout deadline must not clip the long retry")

    def test_budget_constants_value_lock(self):
        job, cf = _job(), _cf()
        self.assertGreaterEqual(job.PER_CHECKOUT_S,
                                job.FETCH_TIMEOUT_S + cf.TERM_GRACE_S + 5)
        self.assertGreaterEqual(job.LONG_RETRY_RESERVE_S,
                                job.FETCH_TIMEOUT_LONG_S + 2 * cf.TERM_GRACE_S + 5)
        self.assertGreaterEqual(job.MIN_BUDGET_S, job.PER_CHECKOUT_S)
        # review 2: the merge is unbounded once started, so its reserve must
        # leave the unit's 120 s kill >= 80 s away (reserve counts to the 100 s soft cap)
        self.assertGreaterEqual(job.MERGE_RESERVE_S, 60)
        self.assertLess(job.MERGE_RESERVE_S, 100)
        self.assertFalse(hasattr(job, "MERGE_TIMEOUT_S"), "a started merge is never bounded")


class TestReviewOneSurfaces(unittest.TestCase):
    """Review 1 (🟡 9-11): the footer never blanks the line, resume writes no
    heartbeat, the registry is scoped to this account."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="t1176-s-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_broken_footer_module_never_blanks_the_statusline(self):
        import airuleset
        fake_repo = os.path.join(self.home, "repo")
        os.makedirs(fake_repo)
        for name in os.listdir(REPO):
            if name != "cli_checkout_freshness.py" and not name.startswith("."):
                os.symlink(os.path.join(REPO, name), os.path.join(fake_repo, name))
        Path(fake_repo, "cli_checkout_freshness.py").write_text(
            "raise RuntimeError('boom')\n")
        shim = os.path.join(self.home, "shim.sh")
        Path(shim).write_text(airuleset.CAVEMAN_SHIM_CONTENT
                              .replace("{{REPO_DIR}}", fake_repo)
                              .replace("{{MANAGED_MODEL}}", airuleset.MANAGED_MODEL))
        Path(self.home, ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "t1176@example.test"}}))
        env = dict(os.environ, HOME=self.home)
        env.pop("TMUX_PANE", None)
        r = subprocess.run(["bash", shim], input=json.dumps({"workspace": {
            "current_dir": "/tmp/nowhere"}}), capture_output=True, text=True,
            env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("t1176@example.test", r.stdout)
        log = Path(self.home, ".claude", "tickets-status", "shim-errors.log")
        self.assertIn("stale-segment", log.read_text())

    def test_resume_start_writes_no_heartbeat(self):
        from _hook_state_cleanup import hermetic_hook_env as _env
        status_dir = os.path.join(self.home, "hb")
        for source, expect in (("resume", 0), ("startup", 1)):
            env = _env(self, AIRULESET_SESSION_STATUS_DIR=status_dir)
            subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.home, env=env,
                           input=json.dumps({"session_id": "hb-%s" % source,
                                             "source": source}),
                           capture_output=True, text=True, timeout=60)
            got = os.listdir(status_dir) if os.path.isdir(status_dir) else []
            self.assertEqual(len([f for f in got if source in f]), expect, got)

    def test_registry_scoped_to_this_accounts_home(self):
        os.makedirs(os.path.join(self.home, "devel/claudy/.git"))
        reg = os.path.join(self.home, "reg.json")
        Path(reg).write_text(json.dumps([
            {"name": "claudy", "host": "controller", "path": "~/devel/claudy",
             "work_branch": "dev", "default_branch": "main"},
            {"name": "other", "host": "controller", "path": "~/devel/other"}]))
        skipped = []
        got = _job().discover_checkouts(home=self.home, user="claudy",
                                        hostname="airuleset", windows=[],
                                        registry_path=reg, skipped=skipped)
        self.assertEqual([c["source"] for c in got], ["registry:claudy"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("registry:other", skipped[0])


class TestReviewTwo(_Repos):
    """Review 2: a started merge is never signalled, fork streams compare
    against the project, nested deadlines only shorten, the session's
    FETCH_HEAD is never clobbered, the hook and the job share the remote
    rule, and the round-1 fixes get job-level locks."""

    def _fork_layout(self):
        """self.bare = the PROJECT (upstream); the clone's origin = a stale
        fork of it; upstream fetched once (the box can read it)."""
        fork = os.path.join(self.root, "fork.git")
        self.g(self.root, "clone", "-q", "--bare", self.bare, fork)
        self.g(self.clone, "remote", "rename", "origin", "upstream")
        self.g(self.clone, "remote", "add", "origin", fork)
        self.g(self.clone, "fetch", "-q", "origin")
        self.g(self.clone, "branch", "-q", "--set-upstream-to=origin/develop")
        return fork

    def test_started_merge_is_never_signalled(self):
        cf, calls, real = _cf(), [], _cf().run_git

        def spy(cwd, args, timeout=10, env_extra=None, honor_deadline=True):
            if "merge" in args:
                calls.append((timeout, honor_deadline))
            return real(cwd, args, timeout, env_extra, honor_deadline)

        self.advance_origin()
        with mock.patch.object(cf, "run_git", spy):
            self.run_job(T0)
        self.assertEqual(calls, [(None, False)])
        self.assertEqual(self.head(), self.origin_head())

    def test_fork_stream_work_branch_compares_against_the_project(self):
        self._fork_layout()
        self.g(self.clone, "checkout", "-q", "-b", "feature/x")
        self.advance_origin(rel=".claude/streams/me.md", text="directive\n")
        self.g(self.clone, "fetch", "-q", "upstream")   # the job's evidence rule
        self.run_job(T0, checkouts=[{"path": self.clone, "source": "t",
                                     "bases": list(_job().DEFAULT_BASES)}])
        e = self.entry()
        self.assertEqual((e["state"], e["remote"]), ("lagging", "upstream"), e)
        self.assertIn("1 rule file(s) behind upstream/develop", e["reason"])

    def test_hook_and_job_share_the_remote_rule(self):
        self._fork_layout()
        self.advance_origin()                             # the PROJECT moves
        self.g(self.clone, "fetch", "-q", "upstream")
        subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.clone, env=self.env,
                       input="{}", capture_output=True, text=True, timeout=60)
        self.assertEqual(self.head(), self.origin_head(),
                         "the hook fast-forwards to the project, like the job")

    def test_nested_deadline_only_shortens(self):
        cf = _cf()
        with cf.deadline(10):
            with cf.deadline(100):
                self.assertLessEqual(cf.time_left(), 10)
            with cf.deadline(1):
                self.assertLessEqual(cf.time_left(), 1)

    def test_session_fetch_head_is_never_clobbered(self):
        self.g(self.clone, "fetch", "-q", "origin")
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        fh = Path(gd, "FETCH_HEAD")
        before = fh.read_text()
        self.advance_origin()
        self.write(self.clone, "notes.txt", "dirty, so no merge\n")
        self.run_job(T0)
        self.assertEqual(fh.read_text(), before)
        self.assertEqual(self.entry()["behind"], 1, "the fetch itself still happened")

    def test_since_is_kept_across_lagging_sweeps(self):
        self.advance_origin()
        self.write(self.clone, "notes.txt", "dirty\n")
        self.run_job(T0)
        self.run_job(T0 + _job().INTERVAL_S)
        self.assertEqual(self.entry()["since"], T0)

    def test_status_entries_of_gone_checkouts_are_pruned(self):
        self.run_job(T0, checkouts=[{"path": self.clone, "bases": ["develop"], "source": "a"},
                                    {"path": self.other, "bases": ["develop"], "source": "b"}])
        self.run_job(T0 + 60)
        self.assertEqual(set(_cf().read_status(self.home)["checkouts"]), {self.clone})

    def test_unreadable_git_dir_fails_closed(self):
        self.advance_origin()
        before = self.head()
        with mock.patch.object(_cf(), "_git_dir", return_value=None):
            self.run_job(T0)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.entry()["reason"], "git-dir-unmeasurable")

    def test_nested_claude_md_counts_as_a_rule_file(self):
        self.g(self.clone, "checkout", "-q", "-b", "feature/n")
        self.advance_origin(rel="sub/CLAUDE.md", text="nested rules\n")
        self.run_job(T0)
        self.assertIn("1 rule file(s) behind", self.entry()["reason"])

    def test_directory_inside_a_parent_repo_is_not_a_checkout(self):
        sub = os.path.join(self.clone, "subdir")
        os.makedirs(sub)
        self.run_job(T0, checkouts=[{"path": sub, "bases": ["develop"], "source": "t"}])
        e = _cf().read_status(self.home)["checkouts"][sub]
        self.assertEqual((e["state"], e["reason"]), ("absent", "not a git checkout root"))

    def test_held_sweep_refreshes_the_alive_stamp(self):
        self.run_job(T0)
        self.run_job(T0 + _job().INTERVAL_S, budget_left=lambda: 1)
        self.assertEqual(_cf().read_status(self.home)["ts"], T0 + _job().INTERVAL_S)

    def test_hook_names_a_held_index_lock(self):
        self.advance_origin()
        self.g(self.clone, "fetch", "-q", "origin")
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        Path(gd, "index.lock").write_text("")
        self.addCleanup(lambda: Path(gd, "index.lock").unlink(missing_ok=True))
        r = subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.clone, env=self.env,
                           input="{}", capture_output=True, text=True, timeout=60)
        self.assertIn("holds the index lock", r.stdout)

    def test_hook_fetch_leaves_the_directives_step_room(self):
        # review 2 finding 4: fetch + ff + the directives step must fit in
        # Claude Code's 30 s hook budget on resume
        self.assertIn("timeout -k 2 8 git fetch origin", FETCH_HOOK.read_text())

    def test_paused_box_never_runs_the_job(self):
        from test_run_once_characterization import _drive
        import watchdog as wd
        calls = []
        with mock.patch.object(wd.checkout_freshness, "run_job",
                               side_effect=lambda *a, **k: calls.append(1) or []):
            _drive({"checkout_freshness_enabled": True, "box_paused": True})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
