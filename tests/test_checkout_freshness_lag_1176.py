"""#1176 fleet census (30.9.2026) — six streams ran on STALE project rules.

montalu1/montalu3 sat on a DETACHED HEAD for days, david2-4 and montalu6 on
work branches 4 000-16 000 commits behind; Job 53 and the SessionStart hook
only REPORTED the lag, and nobody read the report. The fix (ticket "Fix"
items 1-4):

  1. detached HEAD, clean, HEAD an ancestor of the base, the local base not
     ahead -> reattach to the base and fast-forward (the SAME detached-unit
     path as the starvation fix);
  2. a clean work branch with 0 commits outside the base -> switch to the base
     and fast-forward; the branch ref is kept;
  3. a work branch WITH unmerged commits, or a dirty tree -> never touched;
     the stream's OWN claude pane gets ONE rate-limited machine-channel
     message (never the owner);
  4. the session-start hook (startup + resume) prints the same one-liner.

Real temp repos; `systemd-run` is faked through the `unit_run` seam. A
reattach additionally needs HEAD to have been idle (no checkout / commit) for
REATTACH_IDLE_S — the reflog time is set with GIT_COMMITTER_DATE.
"""

import json
import os
import subprocess
import sys
import types
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from test_checkout_freshness_1176 import FETCH_HOOK, T0, _cf, _job, _Repos  # noqa: E402

LONG_AGO = T0 - 3 * 3600


def _notice():
    import watchdog.checkout_notice as n
    return n


def _ok():
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


class _Lag(_Repos):

    def setUp(self):
        super().setUp()
        self.calls = []

    def g_at(self, ts, cwd, *args):
        """git with the reflog / commit time pinned to `ts`."""
        env = dict(self.env, GIT_COMMITTER_DATE="@%d +0000" % ts,
                   GIT_AUTHOR_DATE="@%d +0000" % ts)
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r

    def executing_unit(self, argv, **kw):
        self.calls.append(list(argv))
        child = argv[argv.index("--") + 1:]
        r = subprocess.run(child, cwd=argv[argv.index("--working-directory") + 1],
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.child_rc, self.child_err = r.returncode, r.stderr
        return _ok()

    def branch(self):
        return self.g(self.clone, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()

    def job(self, now=T0, **kw):
        kw.setdefault("unit_run", self.executing_unit)
        return self.run_job(now, **kw)

    def detach(self, ts=LONG_AGO):
        self.g_at(ts, self.clone, "checkout", "-q", "--detach", "HEAD")


class TestItem1DetachedReattach(_Lag):

    def test_clean_detached_head_is_reattached_and_fast_forwarded(self):
        self.detach()
        self.advance_origin(n=2)
        self.job()
        self.assertEqual(self.child_rc, 0, self.child_err)
        self.assertEqual(self.branch(), "develop")
        self.assertEqual(self.head(), self.origin_head())
        self.job(T0 + 60)                          # the next sweep collects it
        e = self.entry()
        self.assertEqual(e["state"], "current", e)
        self.assertIn("reattached", e["reason"])
        self.assertEqual(e["branch"], "develop")

    def test_the_merge_unit_is_the_same_detached_path(self):
        self.detach()
        self.advance_origin()
        self.job(unit_run=lambda argv, **kw: self.calls.append(list(argv)) or _ok())
        (argv,) = [c for c in self.calls if c[0] == "systemd-run"]
        self.assertTrue(argv[argv.index("--unit") + 1].startswith("airuleset-ff-"))
        child = argv[argv.index("--") + 1:]
        self.assertEqual(child[child.index("--mode") + 1], "reattach")
        self.assertEqual(child[child.index("--base") + 1], "develop")
        self.assertIn("ff_pending", self.entry())

    def test_local_base_ahead_is_never_reattached(self):
        self.write(self.clone, "app.py", "local develop commit\n")
        self.g(self.clone, "commit", "-qam", "local")
        self.g(self.clone, "checkout", "-q", "--detach", "HEAD~1")
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "--detach", "HEAD")
        self.advance_origin()
        before = self.head()
        self.job()
        self.assertEqual(self.head(), before)
        self.assertEqual(self.branch(), "")
        self.assertIn("ahead", self.entry()["reason"])

    def test_dirty_detached_head_is_never_touched(self):
        self.detach()
        self.advance_origin()
        self.write(self.clone, "notes.txt", "session edit\n")
        before = self.head()
        self.job()
        self.assertEqual(self.head(), before)
        with open(os.path.join(self.clone, "notes.txt")) as fh:
            self.assertEqual(fh.read(), "session edit\n")

    def test_recently_moved_head_is_left_alone(self):
        self.detach(ts=T0 - 60)
        self.advance_origin()
        before = self.head()
        self.job()
        self.assertEqual(self.head(), before)
        self.assertIn("active", self.entry()["reason"])

    def test_detached_head_off_the_base_history_is_never_touched(self):
        self.write(self.clone, "app.py", "a side commit\n")
        self.g(self.clone, "commit", "-qam", "side")
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "--detach", "HEAD")
        self.g(self.clone, "branch", "-q", "-f", "develop", "HEAD~1")
        self.advance_origin()
        before = self.head()
        self.job()
        self.assertEqual(self.head(), before)

    def test_an_ignored_file_in_the_way_is_never_overwritten(self):
        self.write(self.clone, ".gitignore", "cache.bin\n")
        self.g(self.clone, "add", ".gitignore")
        self.g(self.clone, "commit", "-qm", "ignore")
        self.g(self.clone, "push", "-q", "origin", "develop")
        self.detach()
        self.advance_origin(rel="cache.bin", text="origin version\n")
        self.write(self.clone, "cache.bin", "LOCAL IGNORED\n")
        self.job()
        with open(os.path.join(self.clone, "cache.bin")) as fh:
            self.assertEqual(fh.read(), "LOCAL IGNORED\n")


class TestItem2MergedWorkBranch(_Lag):

    def test_clean_merged_work_branch_switches_to_the_base(self):
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "-b", "feature/done")
        self.advance_origin(n=2)
        self.job()
        self.assertEqual(self.child_rc, 0, self.child_err)
        self.assertEqual(self.branch(), "develop")
        self.assertEqual(self.head(), self.origin_head())
        self.assertEqual(self.g(self.clone, "rev-parse", "--verify", "-q",
                                "refs/heads/feature/done").returncode, 0,
                         "the branch ref is kept")

    def test_work_branch_with_unmerged_commits_is_never_touched(self):
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "-b", "feature/wip")
        self.write(self.clone, "app.py", "work\n")
        self.g_at(LONG_AGO, self.clone, "commit", "-qam", "work")
        self.advance_origin(rel=".claude/rules/lens.md", text="new lens\n")
        before = self.head()
        self.job()
        self.assertEqual(self.head(), before)
        self.assertEqual(self.branch(), "feature/wip")


class TestItem3Notice(_Lag):

    def _wip(self, n_rules=2):
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "-b", "david2/7520-vip")
        self.write(self.clone, "app.py", "work\n")
        self.g_at(LONG_AGO, self.clone, "commit", "-qam", "work")
        for i in range(n_rules):
            self.advance_origin(rel=".claude/rules/r%d.md" % i, text="rule %d\n" % i)

    def test_the_notice_names_branch_count_and_the_fix(self):
        self._wip()
        self.job()
        e = self.entry()
        self.assertEqual(e["rule_lag"], 2)
        n = e["notice"]
        self.assertEqual(n, _cf().notice_line("david2/7520-vip", 2, "origin", "develop"))
        for part in ("david2/7520-vip", "2", "origin/develop",
                     "zmerguj origin/develop", "prejdi na develop"):
            self.assertIn(part, n)

    def test_a_dirty_base_with_rule_lag_gets_a_notice(self):
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        self.write(self.clone, "notes.txt", "session edit\n")
        self.job()
        e = self.entry()
        self.assertEqual(e["reason"], "dirty")
        self.assertEqual(e["notice"], _cf().notice_line("develop", 1, "origin", "develop"))

    def test_no_notice_without_rule_lag_or_while_a_fix_runs(self):
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "-b", "feature/wip")
        self.write(self.clone, "app.py", "work\n")
        self.g_at(LONG_AGO, self.clone, "commit", "-qam", "work")
        self.advance_origin(rel="notes.txt", text="not a rule\n")
        self.job()
        self.assertNotIn("notice", self.entry())

    def test_the_detached_notice_says_detached(self):
        line = _cf().notice_line(None, 3, "upstream", "develop")
        self.assertIn("detached HEAD", line)
        self.assertIn("zmerguj upstream/develop", line)

    def test_the_job_hands_panes_to_the_notice_delivery(self):
        self._wip()
        seen = []
        with mock.patch.object(_notice(), "run_job",
                               side_effect=lambda *a, **kw: seen.append((a, kw)) or ["n"]):
            logs = self.job(state={}, panes=[("%1", self.clone)], run=None,
                            sleep_fn=None, projects_dir="/p", handled=set())
        self.assertEqual(len(seen), 1)
        self.assertIn("n", logs)

    def test_run_once_passes_the_panes_and_state(self):
        from test_run_once_characterization import _drive
        import watchdog as wd
        calls = []

        def _rec(now, **kw):
            calls.append(sorted(kw))
            return []

        with mock.patch.object(wd.checkout_freshness, "run_job", side_effect=_rec):
            _drive({"checkout_freshness_enabled": True})
        for key in ("panes", "state", "run", "sleep_fn", "projects_dir", "handled"):
            self.assertIn(key, calls[0])


class TestNoticeDelivery(unittest.TestCase):
    """`checkout_notice.notice_job` with injected gates (the watch-trigger
    rider's shared readiness set is its production `ready`)."""

    PATH = "/home/s/devel/odoo-erp"
    TEXT = "checkout-freshness: pozadu"

    def setUp(self):
        self.sent = []
        self.state = {}
        self.status = {"checkouts": {self.PATH: {
            "state": "lagging", "branch": "david2/x", "base": "develop",
            "notice": self.TEXT}}}

    def run_notice(self, now, panes=None, ready=None, enabled=True, outcome=True,
                   dry_run=False):
        def deliver(pid, tpath, text):
            self.sent.append((pid, text))
            return outcome
        return _notice().notice_job(
            now, self.state, panes if panes is not None else [("%1", self.PATH)],
            status=self.status,
            ready=ready or (lambda pid, cwd: (True, "", "sid1", "/t.jsonl")),
            deliver=deliver, mark_sent=lambda *a: None,
            nudges_enabled=lambda kind: enabled, dry_run=dry_run)

    def test_one_message_then_rate_limited(self):
        n = _notice()
        self.run_notice(T0)
        self.assertEqual(self.sent, [("%1", self.TEXT)])
        self.run_notice(T0 + 3600)
        self.assertEqual(len(self.sent), 1)
        self.run_notice(T0 + n.REPEAT_S + 1)
        self.assertEqual(len(self.sent), 2)

    def test_a_new_branch_is_told_at_once(self):
        self.run_notice(T0)
        self.status["checkouts"][self.PATH]["branch"] = "david2/y"
        self.run_notice(T0 + 60)
        self.assertEqual(len(self.sent), 2)

    def test_kind_off_sends_nothing(self):
        logs = self.run_notice(T0, enabled=False)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("kind-off" in ln for ln in logs), logs)

    def test_a_busy_pane_holds_without_using_the_slot(self):
        self.run_notice(T0, ready=lambda pid, cwd: (False, "busy-pane", None, None))
        self.assertEqual(self.sent, [])
        self.run_notice(T0 + 60)
        self.assertEqual(len(self.sent), 1)

    def test_an_undelivered_message_does_not_use_the_slot(self):
        self.run_notice(T0, outcome=False)
        self.run_notice(T0 + 60)
        self.assertEqual(len(self.sent), 2)

    def test_only_a_pane_inside_the_checkout_is_told(self):
        self.run_notice(T0, panes=[("%2", "/home/s/devel/other")])
        self.assertEqual(self.sent, [])
        self.run_notice(T0 + 1, panes=[("%3", self.PATH + "/addons")])
        self.assertEqual(self.sent, [("%3", self.TEXT)])

    def test_two_panes_in_one_checkout_are_ambiguous(self):
        logs = self.run_notice(T0, panes=[("%1", self.PATH), ("%2", self.PATH)])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("ambiguous" in ln for ln in logs), logs)

    def test_dry_run_sends_and_records_nothing(self):
        self.run_notice(T0, dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state, {})

    def test_never_the_owner(self):
        src = (REPO / "watchdog" / "checkout_notice.py").read_text(encoding="utf-8")
        self.assertNotIn("notify", src)
        self.assertNotIn("discord", src.lower())


class TestNudgeKind(unittest.TestCase):

    def test_checkout_lag_is_a_staged_machine_kind_on_the_stream_profile(self):
        import cli_fleet
        import watchdog as wd
        self.assertIn("checkout-lag", wd.MACHINE_NUDGE_KINDS)
        self.assertEqual(_notice().NUDGE_KIND, "checkout-lag")
        self.assertIn("checkout-lag", cli_fleet.NUDGE_PROFILES["stream"])
        for other in ("gk", "controller", "workstation"):
            self.assertNotIn("checkout-lag", cli_fleet.NUDGE_PROFILES[other])


class TestItem4SessionStart(_Lag):

    def _hook(self):
        return subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.clone,
                              capture_output=True, text=True, env=self.env,
                              timeout=60, input=json.dumps({"source": "resume"}))

    def test_the_hook_prints_the_same_one_liner_on_a_work_branch(self):
        self.g(self.clone, "checkout", "-q", "-b", "david3/5235")
        self.write(self.clone, "app.py", "work\n")
        self.g(self.clone, "commit", "-qam", "work")
        self.advance_origin(rel=".claude/rules/a.md", text="a\n")
        r = self._hook()
        self.assertIn(_cf().notice_line("david3/5235", 1, "origin", "develop"), r.stdout)

    def test_the_hook_prints_it_on_a_detached_head(self):
        self.g(self.clone, "checkout", "-q", "--detach", "HEAD")
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        r = self._hook()
        self.assertIn(_cf().notice_line(None, 1, "origin", "develop"), r.stdout)

    def test_the_hook_is_silent_without_rule_lag(self):
        self.g(self.clone, "checkout", "-q", "-b", "feature/z")
        self.write(self.clone, "app.py", "work\n")
        self.g(self.clone, "commit", "-qam", "work")
        self.advance_origin(rel="notes.txt", text="x\n")
        r = self._hook()
        self.assertNotIn("pozadu", r.stdout)


class TestCensusReview(_Lag):
    """Review of the census items: structured suppression (never prose), no
    endless relaunch, a live session is never moved, and it is told when a
    reattach happened."""

    def _wip(self, branch="feature/wip"):
        self.g_at(LONG_AGO, self.clone, "checkout", "-q", "-b", branch)
        self.write(self.clone, "app.py", "work\n")
        self.g_at(LONG_AGO, self.clone, "commit", "-qam", "work")
        self.advance_origin(rel=".claude/rules/a.md", text="a\n")

    def test_a_branch_named_deferred_is_still_told(self):
        self._wip("fix/deferred-tax")
        self.job()
        self.assertIn("notice", self.entry())

    def test_no_notice_mid_rebase_on_a_work_branch(self):
        self._wip()
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        os.makedirs(os.path.join(gd, "rebase-merge"))
        self.job()
        self.assertNotIn("notice", self.entry())

    def test_no_notice_while_a_reattach_runs(self):
        self.detach()
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        self.job(unit_run=lambda argv, **kw: self.calls.append(list(argv)) or _ok())
        e = self.entry()
        self.assertIn("ff_pending", e)
        self.assertNotIn("notice", e)

    def test_the_hook_says_nothing_mid_rebase(self):
        self.g(self.clone, "checkout", "-q", "--detach", "HEAD")
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        gd = self.g(self.clone, "rev-parse", "--absolute-git-dir").stdout.strip()
        os.makedirs(os.path.join(gd, "rebase-merge"))
        r = subprocess.run(["bash", str(FETCH_HOOK)], cwd=self.clone,
                           capture_output=True, text=True, env=self.env,
                           timeout=60, input="{}")
        self.assertNotIn("pozadu", r.stdout)

    def test_a_base_checked_out_in_another_worktree_is_never_reattached(self):
        other_wt = os.path.join(self.root, "wt-develop")
        self.detach()
        self.g(self.clone, "worktree", "add", "-q", other_wt, "develop")
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        self.job(unit_run=lambda argv, **kw: self.calls.append(list(argv)) or _ok())
        self.assertEqual([c for c in self.calls if c[0] == "systemd-run"], [])
        e = self.entry()
        self.assertIn("other worktree", e["reason"])
        self.assertIn("notice", e)

    def test_a_failed_reattach_is_not_relaunched_for_the_same_head(self):
        job, cf = _job(), _cf()
        self.detach()
        self.advance_origin(rel="CLAUDE.md", text="rules v2\n")
        self.job(unit_run=lambda argv, **kw: self.calls.append(list(argv)) or _ok())
        cf.write_json_atomic(job.ff_result_path(self.clone, self.home),
                             {"ok": False, "reason": "reattach failed", "mode": "reattach",
                              "base": "develop", "finished": T0 + 5})
        self.job(T0 + job.INTERVAL_S,
                 unit_run=lambda argv, **kw: self.calls.append(list(argv)) or _ok())
        self.assertEqual(len([c for c in self.calls if c[0] == "systemd-run"]), 1)
        e = self.entry()
        self.assertIn("notice", e, "the session is told instead")

    def test_a_live_session_in_the_checkout_is_never_moved(self):
        self.detach()
        self.advance_origin(n=2)
        before = self.head()
        pdir = os.path.join(self.root, "projects")
        live_t = os.path.join(pdir, "live.jsonl")
        os.makedirs(pdir)
        Path(live_t).write_text("{}\n")
        with mock.patch("watchdog.find_active_transcript",
                        return_value=(live_t, os.path.getmtime(live_t))), \
                mock.patch.object(_notice(), "run_job", return_value=[]):
            self.job(state={}, panes=[("%1", self.clone)], run=None, sleep_fn=None,
                     projects_dir=pdir, handled=set())
        self.assertEqual(self.head(), before)
        self.assertIn("session-active", self.entry()["reason"])

    def test_the_session_is_told_after_a_reattach(self):
        self.detach()
        self.advance_origin(n=2)
        self.job()
        self.job(T0 + 60)
        e = self.entry()
        self.assertEqual(e["state"], "current")
        self.assertEqual(e["notice"], _cf().reattach_line(None, "develop"))
        self.assertIn("detached HEAD", e["notice"])
        self.assertIn("develop", e["notice"])

    def test_a_fork_base_gets_no_upstream_tracking_to_the_project(self):
        self.g(self.clone, "remote", "rename", "origin", "upstream")
        self.detach()
        self.g(self.clone, "branch", "-q", "-D", "develop")
        self.advance_origin(n=1)
        self.job()
        self.assertEqual(self.branch(), "develop")
        r = self.g(self.clone, "config", "--get", "branch.develop.remote")
        self.assertNotEqual(r.stdout.strip(), "upstream",
                            "a bare push must never target the project repo")

    def test_no_reflog_is_named_as_such(self):
        self.detach()
        self.advance_origin()
        self.g(self.clone, "fetch", "-q", "origin")   # the job fetches first
        with mock.patch.object(_cf(), "_head_moved_at", return_value=None):
            v = _cf().reattach_verdict(self.clone, "origin", "develop", now=T0)
        self.assertEqual(v.reason, "head-activity-unmeasurable")

    def test_a_base_branch_notice_does_not_say_switch_to_it(self):
        line = _cf().notice_line("develop", 2, "origin", "develop")
        self.assertNotIn("prejdi na develop", line)
        self.assertIn("zmerguj origin/develop", line)


class TestNoticeReview(TestNoticeDelivery):

    def test_a_sibling_prefix_path_is_not_inside(self):
        self.run_notice(T0, panes=[("%2", self.PATH + "2")])
        self.assertEqual(self.sent, [])

    def test_a_lane_worktree_pane_is_not_the_session(self):
        self.run_notice(T0, panes=[("%2", self.PATH + "/.claude/worktrees/agent-x")])
        self.assertEqual(self.sent, [])

    def test_kind_off_is_journaled_at_most_hourly(self):
        first = self.run_notice(T0, enabled=False)
        again = self.run_notice(T0 + 60, enabled=False)
        self.assertTrue(any("kind-off" in ln for ln in first))
        self.assertFalse(any("kind-off" in ln for ln in again))


class TestConstants(unittest.TestCase):

    def test_value_lock(self):
        job, n = _job(), _notice()
        self.assertEqual(job.REATTACH_IDLE_S, 3600)
        self.assertEqual(n.REPEAT_S, 24 * 3600)


if __name__ == "__main__":
    unittest.main()
