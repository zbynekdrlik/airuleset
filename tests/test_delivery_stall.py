"""Job 24 — DELIVERY-STALL WATCH (#138).

The failure this locks: camera-box's merge end was BLOCKED for 15 days (PR #704
`mergeStateStatus=BLOCKED` on one permanently-red required check), `origin/main`
frozen at 2026-07-11, 422 commits of finished work stranded on `dev`, and issue
closure — which is merge-driven there — at exactly zero. Nothing in airuleset
measured repo-level DELIVERY, so the state was indistinguishable from health.

Every fixture here is a REAL git repository built with real `git` calls and
commit dates pinned through `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`, per this
repo's own "build real repos, never a hand-typed git log string" discipline —
the job's whole job is reading git, so mocking git would test nothing.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402


DAY = 86400
NOW = 1785200000.0          # fixed; never time.time() (hour-bucket jitter rule)


def _git(repo, *args, ts=None):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if ts is not None:
        stamp = "%d +0000" % int(ts)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-delivery-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "owner": owner, "dedup": dedup_key,
                          "dry_run": dry_run})
        return "sent"

    def repo(self, name="proj", base_ts=NOW - 20 * DAY, work_ts=NOW - 3600,
             undelivered=0, base_name="main"):
        """A repo whose base branch last moved at `base_ts` and which carries
        `undelivered` commits (dated `work_ts`) on a separate work branch.
        `undelivered=0` leaves HEAD ON the base branch — this repo's own shape,
        which pushes straight to main."""
        r = self.tmp / name
        r.mkdir()
        _git(r, "init", "-q", "-b", base_name)
        (r / "f").write_text("0\n")
        _git(r, "add", "-A")
        _git(r, "commit", "-qm", "base", ts=base_ts)
        _git(r, "update-ref", "refs/remotes/origin/" + base_name, "HEAD")
        _git(r, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/" + base_name)
        if undelivered:
            _git(r, "checkout", "-qb", "dev")
            for i in range(undelivered):
                (r / "f").write_text("%d\n" % (i + 1))
                _git(r, "add", "-A")
                _git(r, "commit", "-qm", "work %d" % i, ts=work_ts)
        return r

    def watch(self, cwds, now=NOW, probe=None, **kw):
        """Drive the shipped job. A probe is always supplied unless a test is
        specifically about its absence — the job is gated on it (the confirming
        fetch is what makes a ping trustworthy)."""
        if probe is None:
            probe = lambda root, base: None            # noqa: E731
        cwd_by_sid = {("sid%d" % i): str(c) for i, c in enumerate(cwds)}
        return wd.delivery_stall_watch(
            now, None, self.state, cwd_by_sid, send_fn=self.send,
            delivery_probe=probe, **kw)


class TestDeliveryMeasurement(_Base):
    """`delivery_state` — the pure git read the whole job rests on."""

    def test_it_reports_the_undelivered_backlog_and_both_ages(self):
        r = self.repo(base_ts=NOW - 17 * DAY, work_ts=NOW - 1800, undelivered=5)
        st = wd.delivery_state(str(r), NOW)
        self.assertEqual(st["undelivered"], 5)
        self.assertEqual(st["base"], "origin/main")
        self.assertAlmostEqual(st["delivery_age"], 17 * DAY, delta=90)
        self.assertAlmostEqual(st["work_age"], 1800, delta=90)

    def test_head_on_the_base_branch_has_nothing_undelivered(self):
        st = wd.delivery_state(str(self.repo(undelivered=0)), NOW)
        self.assertEqual(st["undelivered"], 0)

    def test_a_master_repo_resolves_its_own_base(self):
        r = self.repo(base_name="master", undelivered=4)
        self.assertEqual(wd.delivery_state(str(r), NOW)["base"], "origin/master")

    def test_a_non_git_directory_is_unmeasurable(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.assertIsNone(wd.delivery_state(str(d), NOW))

    def test_git_unavailable_is_unmeasurable_never_a_guess(self):
        r = self.repo(undelivered=4)
        self.assertIsNone(
            wd.delivery_state(str(r), NOW, git_run=lambda *a, **k: None))


class TestDeliveryStallWatch(_Base):

    def test_a_frozen_base_with_fresh_work_pings(self):
        """The camera-box shape: work landing daily, base frozen for weeks."""
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 1800, undelivered=6)
        logs = self.watch([r])
        self.assertEqual(len(self.sent), 1, logs)
        self.assertIn("camera-box", self.sent[0]["msg"])
        self.assertIn("6", self.sent[0]["msg"])

    def test_a_repo_whose_head_is_its_base_never_fires(self):
        """This repo's own shape — direct pushes to main. Structurally silent:
        nothing is undelivered, whatever the thresholds are."""
        r = self.repo(name="airuleset", base_ts=NOW - 30 * DAY, undelivered=0)
        self.watch([r])
        self.assertEqual(self.sent, [])

    def test_a_parked_repo_with_no_recent_work_is_silent(self):
        """2026-07-20..26: deliberate pause, zero commits. Nothing is being
        spent, so there is nothing to warn about."""
        r = self.repo(base_ts=NOW - 17 * DAY, work_ts=NOW - 7 * DAY,
                      undelivered=40)
        self.watch([r])
        self.assertEqual(self.sent, [])

    def test_a_repo_that_delivered_recently_is_silent(self):
        r = self.repo(base_ts=NOW - 3600, work_ts=NOW - 600, undelivered=9)
        self.watch([r])
        self.assertEqual(self.sent, [])

    def test_below_the_undelivered_floor_is_silent(self):
        r = self.repo(base_ts=NOW - 17 * DAY, work_ts=NOW - 600, undelivered=1)
        self.watch([r])
        self.assertEqual(self.sent, [])

    def test_an_unmeasurable_repo_is_silent(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.watch([d])
        self.assertEqual(self.sent, [])


class TestDedupAndRecovery(_Base):

    def stalled(self):
        return self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                         work_ts=NOW - 1800, undelivered=6)

    def test_one_ping_per_reping_window_not_one_per_sweep(self):
        r = self.stalled()
        for _ in range(5):
            self.watch([r])
        self.assertEqual(len(self.sent), 1)

    def test_it_repings_once_the_window_passes(self):
        r = self.stalled()
        self.watch([r], now=NOW)
        self.watch([r], now=NOW + wd.DELIVERY_REPING_S + 60)
        self.assertEqual(len(self.sent), 2)

    def test_a_delivery_clears_the_state_so_a_later_stall_pings_again(self):
        r = self.stalled()
        self.watch([r])
        self.assertEqual(len(self.sent), 1)
        # the base advances — the stall is over
        _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.watch([r], now=NOW + 60)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.state.get("delivery_stall"))

    def test_two_panes_in_one_repo_ping_once(self):
        r = self.stalled()
        self.watch([r, r])
        self.assertEqual(len(self.sent), 1)

    def test_dry_run_pings_nothing_and_records_nothing(self):
        r = self.stalled()
        logs = self.watch([r], dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state.get("delivery_stall"))
        self.assertTrue(any("delivery-stall" in ln for ln in logs))


class TestConfirmingProbe(_Base):
    """The probe carries the confirming `git fetch`, so a stale
    remote-tracking ref can never on its own produce a ping."""

    def test_the_probe_runs_only_for_a_candidate(self):
        calls = []

        def probe(root, base):
            calls.append(root)
            return None

        healthy = self.repo(name="ok", base_ts=NOW - 600, undelivered=0)
        self.watch([healthy], probe=probe)
        self.assertEqual(calls, [])

        stalled = self.repo(name="stuck", base_ts=NOW - 17 * DAY,
                            work_ts=NOW - 600, undelivered=6)
        self.watch([stalled], probe=probe)
        self.assertEqual(len(calls), 1)

    def test_a_fetch_that_reveals_a_delivery_cancels_the_ping(self):
        """The whole anti-false-positive guarantee: the probe fetches, the
        job RE-measures, and a base that had merely gone stale locally is
        never reported as a stall."""
        r = self.repo(name="stuck", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)

        def probe(root, base):
            _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
            return None

        logs = self.watch([r], probe=probe)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("confirmed-clear" in ln for ln in logs), logs)

    def test_the_ping_names_the_blocking_pr_and_its_failing_check(self):
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)
        self.watch([r], probe=lambda root, base: {
            "pr": 704, "check": "Full-path E2E (rig zero-loss gate)"})
        self.assertIn("704", self.sent[0]["msg"])
        self.assertIn("Full-path E2E (rig zero-loss gate)", self.sent[0]["msg"])

    def test_a_probe_that_raises_still_pings(self):
        """Enrichment is best-effort; losing it must never lose the alert."""
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)

        def probe(root, base):
            raise RuntimeError("gh exploded")

        self.watch([r], probe=probe)
        self.assertEqual(len(self.sent), 1)

    def test_without_a_probe_the_job_does_not_run_at_all(self):
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)
        logs = wd.delivery_stall_watch(NOW, None, self.state, {"s": str(r)},
                                       send_fn=self.send, delivery_probe=None)
        self.assertEqual(self.sent, [])
        self.assertEqual(logs, [])


class TestPingContent(_Base):

    def test_the_ping_is_slovak_and_phone_readable(self):
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)
        self.watch([r], probe=lambda root, base: {"pr": 704, "check": "E2E"})
        msg = self.sent[0]["msg"]
        self.assertIn("camera-box", msg)
        self.assertIn("dní", msg)
        self.assertIn("commit", msg.lower())
        # deduped per repo + window, never per sweep
        self.assertIn("camera-box", self.sent[0]["dedup"] or "")

    def test_the_ping_goes_to_the_repo_s_own_owner(self):
        r = self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                      work_ts=NOW - 600, undelivered=6)
        wd.delivery_stall_watch(NOW, None, self.state, {"sid": str(r)},
                                send_fn=self.send,
                                delivery_probe=lambda *a: None,
                                owner_by_sid={"sid": "zbynek"})
        self.assertEqual(self.sent[0]["owner"], "zbynek")


class TestDetectionOnly(unittest.TestCase):

    def test_the_job_never_sends_a_keystroke(self):
        """Job 21's discipline: deciding to interrupt is the user's call. A
        `run` proxy that refuses everything must never be asked to act."""
        src = Path(wd.__file__).read_text()
        start = src.index("def delivery_stall_watch(")
        body = src[start:src.index("\ndef ", start + 10)]
        for banned in ("send-keys", "send_continue", "deliver_with_stash",
                       "_restart_pane"):
            self.assertNotIn(banned, body,
                             "job 24 is detection-only; found %r" % banned)


class TestRunOnceWiring(unittest.TestCase):

    def test_run_once_accepts_and_documents_the_delivery_probe(self):
        import inspect
        sig = inspect.signature(wd.run_once)
        self.assertIn("delivery_probe", sig.parameters)
        self.assertIsNone(sig.parameters["delivery_probe"].default)
        self.assertIn("(24)", wd.run_once.__doc__)

    def test_airuleset_wires_a_real_probe(self):
        src = (Path(wd.__file__).resolve().parent.parent / "airuleset.py").read_text()
        self.assertIn("delivery_probe=", src)
        self.assertIn("_watchdog_delivery_probe", src)


if __name__ == "__main__":
    unittest.main()
