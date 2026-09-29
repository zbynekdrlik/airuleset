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
            self.g(self.other, "add", "-A")
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

    def test_old_missing_commit_counts_after_one_observed_period(self):
        cf = _cf()
        week = T0 - 7 * 86400
        self._write({"/c": self._lag(since=T0 - 60, behind_since=week)})
        self.assertEqual(cf.footer_segment(self.home, now=T0), "",
                         "a single snapshot is never an alarm")
        self._write({"/c": self._lag(since=T0 - 1000, behind_since=week)})
        self.assertIn("stale 2.6k", cf.footer_segment(self.home, now=T0))

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


if __name__ == "__main__":
    unittest.main()
