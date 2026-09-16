"""Periodic per-box cross-target CONFORMANCE check (#535) — watchdog job 34.

Uniformity of ``~/.claude/CLAUDE.md`` + the airuleset repo across the fleet is
guaranteed today ONLY by push-time deploy + a prose/hook ban on hand-edits;
nothing reads the post-deploy state, so drift after a deploy (a box that never
got the push, a hand-edited CLAUDE.md, a repo behind origin/main, a dead
api-watchdog.timer) is invisible. This job is the per-box self-check that
surfaces such drift LOUD + deduped.

The safety-critical invariant under test (the #535 design): NEVER a FALSE drift
alarm. Every dimension is a pure decider returning ``ok`` ∈ {True conformant /
False drift / None undetermined}; a git error, a fetch failure, a missing
baseline, or an obscured local-dev state ALL fail safe to ``None`` (logged,
never pinged). The dedup is per-dimension (bounded set of 4, no leak) and never
permanently silent (a re-remind cadence) yet never a daily re-spam — and an
UNDETERMINED sweep must NOT drop a prior divergence's episode (#486-G5).

A FAKE git seam (``fake_git``) + a fake ``timer_check`` give deterministic
control over every branch, including the fail-safe edges a real repo cannot be
coaxed into on demand; a ``TestRealGit`` class then proves the real git commands
against a temporary repo so the fake can never drift from real ``git`` semantics.
"""
import contextlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog.conformance as conf      # noqa: E402

NOW = 1786000000.0
DAY = 86400.0
ROOT = "/repo/airuleset"


def fake_git(head="aaaaaaaa1111", origin="aaaaaaaa1111", fetch_rc=0,
             status="", status_rc=0, ancestor_rc=0, head_rc=0, origin_rc=0):
    """A deterministic ``git_run(args, cwd) -> (rc, stdout)`` seam. Dispatches on
    the subcommand exactly as ``run_conformance_check`` issues it."""
    def g(args, cwd, timeout=None):
        sub = args[0]
        if sub == "fetch":
            return (fetch_rc, "")
        if sub == "rev-parse":
            ref = args[-1]
            if ref == "HEAD":
                return (head_rc, (head or "") + "\n")
            if ref == "origin/main":
                return (origin_rc, (origin or "") + "\n")
        if sub == "merge-base":
            return (ancestor_rc, "")
        if sub == "status":
            return (status_rc, status)
        return (0, "")
    return g


def fake_timer(status="active"):
    return lambda unit=None: status


def _surfaced(logs, dim):
    """#1032: the job no longer pings — a genuine drift emits a `[<dim>] SURFACED`
    JOURNAL escalation line (new / changed-sig / past-reping), deduped per
    dimension exactly as the removed owner ping was. Count them for `dim`."""
    return sum(1 for ln in logs if ("[%s] SURFACED" % dim) in ln)


def _baseline(tmp, md5=None, head="aaaaaaaa1111"):
    """Write a baseline JSON in ``tmp`` and return its path."""
    p = os.path.join(tmp, conf.CONFORMANCE_BASELINE_NAME)
    with open(p, "w") as fh:
        json.dump({"claude_md_md5": md5, "head_sha": head}, fh)
    return p


def _claude_md(tmp, content="managed content\n"):
    p = os.path.join(tmp, "CLAUDE.md")
    with open(p, "w") as fh:
        fh.write(content)
    return p


def _run(state, tmp, dry_run=False, git=None, timer=None,
         claude_content="managed content\n", baseline_md5="MATCH",
         baseline_head="aaaaaaaa1111", now=NOW, is_target=True, **git_kw):
    """Drive ``run_conformance_check`` with fully-controlled I/O seams. By default
    every dimension is CONFORMANT (uniform state): HEAD==origin, clean, timer
    active, and the baseline md5 is stamped to the on-disk file's real md5.
    ``is_target`` defaults True (a DEPLOY TARGET, where the dirty dimension runs);
    pass False to model the deploy SOURCE box (dev1), where dirty is skipped.

    #1032: the job no longer takes a `send_fn` — it never pings. It SURFACES drift
    to the returned `logs` (a `[<dim>] SURFACED` line) + the persisted
    `state["conformance"]` snapshot. Tests assert on those, never a send."""
    cmd = _claude_md(tmp, claude_content)
    real_md5 = conf._md5_file(cmd)
    md5 = real_md5 if baseline_md5 == "MATCH" else baseline_md5
    base = _baseline(tmp, md5=md5, head=baseline_head)
    return conf.run_conformance_check(
        now, state, dry_run=dry_run, repo_root=ROOT,
        claude_md_path=cmd, baseline_path=base,
        git_run=git or fake_git(**git_kw),
        timer_check=timer or fake_timer(),
        is_target_check=lambda: is_target,
        # #972 REOPEN: inject a clean symlink scan so the default-on dimension
        # never reads the developer's REAL ~/.claude here (its own dimension is
        # covered by tests/test_conformance_symlinks_972.py).
        symlink_scan=lambda: [],
        # #1028: same hermetic injection for the doctrine-drift dimension — its
        # own behaviour is covered by tests/test_doctrine_audit_1028.py.
        doctrine_scan=lambda: {"high": 0, "medium": 0},
        persist=lambda: None)


# --------------------------------------------------------------------------
# PURE DECIDERS
# --------------------------------------------------------------------------
class TestClassifyHead(unittest.TestCase):
    def test_equal_is_conformant(self):
        dim, ok, _ = conf.classify_head("abc", "abc", False)
        self.assertEqual((dim, ok), ("head", True))

    def test_behind_is_drift(self):
        dim, ok, _ = conf.classify_head("aaa", "bbb", True)
        self.assertEqual((dim, ok), ("head", False))

    def test_ahead_or_diverged_is_undetermined(self):
        # local dev / mid-integration on dev1 — NOT a drift
        _, ok, _ = conf.classify_head("aaa", "bbb", False)
        self.assertIsNone(ok)

    def test_missing_shas_undetermined(self):
        self.assertIsNone(conf.classify_head(None, "bbb", True)[1])
        self.assertIsNone(conf.classify_head("aaa", None, True)[1])


class TestClassifyDirty(unittest.TestCase):
    def test_empty_is_conformant(self):
        self.assertIs(conf.classify_dirty("", False, True)[1], True)
        self.assertIs(conf.classify_dirty("   \n", False, True)[1], True)

    def test_nonempty_is_drift(self):
        dim, ok, detail = conf.classify_dirty(" M airuleset.py\n?? x\n", False, True)
        self.assertEqual((dim, ok), ("dirty", False))
        self.assertIn("2", detail)

    def test_git_error_undetermined(self):
        self.assertIsNone(conf.classify_dirty("", True, True)[1])

    def test_not_at_baseline_undetermined(self):
        # dev1 / a stale box: a dirty tree off the fleet baseline is local dev, not
        # drift (#535 MAJOR-1) — even a genuinely dirty tree must NOT alarm.
        self.assertIsNone(conf.classify_dirty(" M airuleset.py\n", False, False)[1])


class TestClassifyMd5(unittest.TestCase):
    def test_match_is_conformant(self):
        self.assertIs(conf.classify_md5("h", "h", "H1", "H1")[1], True)

    def test_mismatch_is_drift(self):
        self.assertIs(conf.classify_md5("x", "y", "H1", "H1")[1], False)

    def test_no_baseline_undetermined(self):
        self.assertIsNone(conf.classify_md5("x", None, None, "H1")[1])

    def test_head_mismatch_skips_midpush(self):
        # baseline recorded at HEAD H0, repo now at H1 -> install pending -> skip
        self.assertIsNone(conf.classify_md5("x", "y", "H0", "H1")[1])

    def test_on_disk_unreadable_undetermined(self):
        self.assertIsNone(conf.classify_md5(None, "y", "H1", "H1")[1])


class TestClassifyTimer(unittest.TestCase):
    def test_active_conformant(self):
        self.assertIs(conf.classify_timer("active")[1], True)

    def test_inactive_drift(self):
        self.assertIs(conf.classify_timer("inactive")[1], False)
        self.assertIs(conf.classify_timer("failed")[1], False)

    def test_unavailable_undetermined(self):
        self.assertIsNone(conf.classify_timer(None)[1])

    def test_transient_state_undetermined(self):
        # a sweep landing during a daemon-reload/restart must not false-alarm (NIT-1)
        self.assertIsNone(conf.classify_timer("activating")[1])
        self.assertIsNone(conf.classify_timer("reloading")[1])
        self.assertIsNone(conf.classify_timer("deactivating")[1])


# --------------------------------------------------------------------------
# INSTALL BASELINE RECORDING
# --------------------------------------------------------------------------
class TestRecordBaseline(unittest.TestCase):
    def test_records_md5_and_head(self):
        with TemporaryDirectory() as d:
            dest = os.path.join(d, conf.CONFORMANCE_BASELINE_NAME)
            rec = conf.record_conformance_baseline(
                "hello world\n", ROOT, dest,
                git_run=fake_git(head="deadbeef9999"))
            self.assertEqual(rec["head_sha"], "deadbeef9999")
            self.assertEqual(rec["claude_md_md5"], conf._md5_hex("hello world\n"))
            # readable back through the job's own reader
            back = conf._read_baseline(dest)
            self.assertEqual(back["claude_md_md5"], rec["claude_md_md5"])
            self.assertEqual(back["head_sha"], "deadbeef9999")

    def test_head_none_on_git_error(self):
        with TemporaryDirectory() as d:
            dest = os.path.join(d, conf.CONFORMANCE_BASELINE_NAME)
            rec = conf.record_conformance_baseline(
                "x\n", ROOT, dest, git_run=fake_git(head_rc=128))
            self.assertIsNone(rec["head_sha"])


# --------------------------------------------------------------------------
# ORCHESTRATOR
# --------------------------------------------------------------------------
class TestRunConformance(unittest.TestCase):
    # #1032: the job never pings the owner. A genuine drift SURFACES to the
    # journal (`[<dim>] SURFACED`) + the persisted `state["conformance"]`
    # snapshot cmd_status reads; every fail-safe (undetermined) SURFACES nothing.
    def test_uniform_state_surfaces_nothing(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d)
            self.assertTrue(any("[head] OK" in ln for ln in logs))
            self.assertTrue(any("[timer] OK" in ln for ln in logs))
            self.assertFalse(any("SURFACED" in ln for ln in logs),
                             "a conformant fleet surfaces no drift")

    def test_head_behind_surfaces(self):
        with TemporaryDirectory() as d:
            state = {}
            logs = _run(state, d, head="aaa11111", origin="bbb22222",
                        ancestor_rc=0)   # HEAD is ancestor of origin = behind
            self.assertEqual(_surfaced(logs, "head"), 1)
            self.assertIn("head", state.get("conformance", {}))

    def test_head_ahead_does_not_surface(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d, head="aaa11111", origin="bbb22222",
                        ancestor_rc=1)   # HEAD NOT ancestor of origin = ahead/diverged
            self.assertEqual(_surfaced(logs, "head"), 0)

    def test_dirty_tree_surfaces(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d, status=" M airuleset.py\n")
            self.assertEqual(_surfaced(logs, "dirty"), 1)

    def test_dirty_tree_off_baseline_does_not_surface(self):
        # dev1 (HEAD ahead of origin = local dev) with a dirty tree must be SILENT
        # on the dirty dimension — the MAJOR-1 false-alarm the review caught.
        with TemporaryDirectory() as d:
            logs = _run({}, d, status=" M airuleset.py\n",
                        head="aaa11111", origin="bbb22222", ancestor_rc=1)
            self.assertEqual(_surfaced(logs, "dirty"), 0,
                             "a dirty dev-box tree off baseline must not surface")

    def test_dirty_on_source_box_does_not_surface(self):
        # MAJOR-A: the deploy SOURCE box (dev1, not a REMOTE_HOSTS target) sits at
        # HEAD==origin with a dirty main checkout during normal dev — is_target=False
        # must silence the dirty dimension even though it is at baseline and dirty.
        with TemporaryDirectory() as d:
            logs = _run({}, d, status=" M airuleset.py\n", is_target=False)
            self.assertEqual(_surfaced(logs, "dirty"), 0,
                             "a dirty deploy-SOURCE box at baseline must not surface")

    def test_md5_mismatch_surfaces(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d, baseline_md5="DIFFERENT_HASH")
            self.assertEqual(_surfaced(logs, "claude_md"), 1)

    def test_md5_midpush_head_mismatch_does_not_surface(self):
        # baseline recorded at a DIFFERENT head than HEAD -> install pending -> skip
        with TemporaryDirectory() as d:
            logs = _run({}, d, baseline_md5="DIFFERENT_HASH",
                        baseline_head="00000000ffff",
                        head="aaaaaaaa1111", origin="aaaaaaaa1111")
            self.assertEqual(_surfaced(logs, "claude_md"), 0,
                             "mid-push md5 mismatch must be skipped, not surfaced")

    def test_timer_inactive_surfaces(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d, timer=fake_timer("inactive"))
            self.assertEqual(_surfaced(logs, "timer"), 1)

    def test_fetch_failure_logs_no_alarm(self):
        with TemporaryDirectory() as d:
            logs = _run({}, d, fetch_rc=1, head="aaa", origin="aaa")
            self.assertFalse(any("SURFACED" in ln for ln in logs),
                             "a fetch failure must never surface a drift")
            self.assertTrue(any("fetch zlyhal" in ln for ln in logs))

    def test_dedup_same_divergence_no_resurface(self):
        with TemporaryDirectory() as d:
            state = {}
            logs1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(_surfaced(logs1, "timer"), 1)
            # next daily check, unchanged divergence, within reping window -> no
            # NEW surface line, but the episode is kept (status still shows it)
            state["conformance_last_check"] = 0    # make it due again
            logs2 = _run(state, d, timer=fake_timer("inactive"), now=NOW + DAY)
            self.assertEqual(_surfaced(logs2, "timer"), 0,
                             "unchanged divergence must not re-surface daily")
            self.assertIn("timer", state.get("conformance", {}))

    def test_dedup_changed_divergence_resurfaces(self):
        with TemporaryDirectory() as d:
            state = {}
            logs1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(_surfaced(logs1, "timer"), 1)
            state["conformance_last_check"] = 0
            # a DIFFERENT drift signature (failed vs inactive) re-surfaces at once
            logs2 = _run(state, d, timer=fake_timer("failed"), now=NOW + DAY)
            self.assertEqual(_surfaced(logs2, "timer"), 1,
                             "a materially different drift must re-surface")

    def test_dedup_past_reping_resurfaces(self):
        with TemporaryDirectory() as d:
            state = {}
            logs1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(_surfaced(logs1, "timer"), 1)
            state["conformance_last_check"] = 0
            logs2 = _run(state, d, timer=fake_timer("inactive"),
                         now=NOW + 4 * DAY)   # past the 3-day reping
            self.assertEqual(_surfaced(logs2, "timer"), 1,
                             "never permanently silent: re-surface past reping")

    def test_resolved_clears_episode_so_redivergence_resurfaces(self):
        with TemporaryDirectory() as d:
            state = {}
            _run(state, d, timer=fake_timer("inactive"), now=NOW)
            state["conformance_last_check"] = 0
            _run(state, d, timer=fake_timer("active"), now=NOW + DAY)   # resolved
            self.assertNotIn("timer", state.get("conformance", {}))
            state["conformance_last_check"] = 0
            logs = _run(state, d, timer=fake_timer("inactive"), now=NOW + 2 * DAY)
            self.assertEqual(_surfaced(logs, "timer"), 1,
                             "a re-divergence after a fix must re-surface immediately")

    def test_undetermined_preserves_prior_episode(self):
        # a real drift surfaces; a following UNDETERMINED sweep (systemctl
        # unreadable) must NOT clear the episode, so a flake can't drop the status.
        with TemporaryDirectory() as d:
            state = {}
            _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertIn("timer", state.get("conformance", {}))
            state["conformance_last_check"] = 0
            _run(state, d, timer=fake_timer(None), now=NOW + DAY)   # undetermined
            self.assertIn("timer", state.get("conformance", {}),
                          "an UNDETERMINED sweep must not drop the prior episode (#486-G5)")

    def test_dry_run_never_surfaces_or_mutates_state(self):
        with TemporaryDirectory() as d:
            state = {}
            logs = _run(state, d, timer=fake_timer("inactive"), dry_run=True)
            self.assertFalse(any("[timer] SURFACED" in ln for ln in logs),
                             "dry-run never surfaces")
            self.assertNotIn("conformance", state, "dry-run mutates no state")
            self.assertNotIn("conformance_last_check", state, "dry-run never advances cadence")
            self.assertTrue(any("WOULD-SURFACE" in ln for ln in logs))

    def test_cadence_not_due_returns_early(self):
        with TemporaryDirectory() as d:
            state = {"conformance_last_check": NOW - 100}   # just checked
            logs = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(logs, [])

    def test_no_repo_root_is_noop(self):
        self.assertEqual(conf.run_conformance_check(NOW, {}, repo_root=None), [])


class TestRealGit(unittest.TestCase):
    """Prove the real git commands the fake stands in for actually behave as the
    orchestrator assumes — so the fake can never silently drift from git."""
    def _git(self, *args, cwd):
        subprocess.run(["git", "-C", str(cwd)] + list(args),
                       check=True, capture_output=True, text=True)

    def test_real_clean_and_dirty_and_ancestor(self):
        with TemporaryDirectory() as d:
            self._git("init", "-q", cwd=d)
            self._git("config", "user.email", "t@t", cwd=d)
            self._git("config", "user.name", "t", cwd=d)
            (Path(d) / "a.txt").write_text("1\n")
            self._git("add", "a.txt", cwd=d)
            self._git("commit", "-qm", "c1", cwd=d)
            gr = conf._conf_git
            # clean tree
            rc, out = gr(["status", "--porcelain"], d)
            self.assertEqual((rc, out.strip()), (0, ""))
            # HEAD readable
            rc, head = gr(["rev-parse", "HEAD"], d)
            self.assertEqual(rc, 0)
            self.assertTrue(head.strip())
            # is-ancestor of itself -> rc 0 (behind-or-equal semantics)
            rc, _ = gr(["merge-base", "--is-ancestor", head.strip(), head.strip()], d)
            self.assertEqual(rc, 0)
            # now dirty
            (Path(d) / "a.txt").write_text("2\n")
            rc, out = gr(["status", "--porcelain"], d)
            self.assertEqual(rc, 0)
            self.assertNotEqual(out.strip(), "")


class TestInstallWiring(unittest.TestCase):
    """The install-baseline step is EXTRACTED (#410-F2) so this exercises the REAL
    cmd_install step function (dest computation + REPO_DIR passing), not a
    re-implementation — a mutation to the dest/repo wiring fails it."""
    def test_step_computes_dest_and_passes_repo_dir(self):
        import airuleset
        calls = []

        def rec(content, repo, dest):
            calls.append((content, repo, dest))
            return {"claude_md_md5": "x"}
        out = airuleset._record_conformance_baseline_step("MANAGED\n", record_fn=rec)
        self.assertEqual(len(calls), 1)
        content, repo, dest = calls[0]
        self.assertEqual(content, "MANAGED\n")
        self.assertEqual(repo, airuleset.REPO_DIR)
        self.assertEqual(str(dest),
                         str(airuleset.CLAUDE_DIR / conf.CONFORMANCE_BASELINE_NAME))
        self.assertEqual(out, {"claude_md_md5": "x"})

    def test_cmd_install_source_calls_the_step(self):
        # cheap guard that the one-line call site is not dropped (the step is only
        # useful if cmd_install actually invokes it).
        import inspect
        import airuleset
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("_record_conformance_baseline_step(", src)


class TestIsDeployTarget(unittest.TestCase):
    """`_watchdog_is_deploy_target` (#535 review MAJOR-A) — positively confirms a
    deploy TARGET by tailscale-IP∈REMOTE_HOSTS membership, fail-safe to False (skip
    the dirty dimension, never a false alarm) on any error."""
    def _run_ips(self, stdout, rc=0, pw_name=None):
        import airuleset
        import unittest.mock as m
        fake = m.Mock(returncode=rc, stdout=stdout)
        patches = [m.patch("subprocess.run", return_value=fake)]
        if pw_name is not None:
            pw = m.Mock(pw_name=pw_name)
            patches.append(m.patch("pwd.getpwuid", return_value=pw))
        ctx = contextlib.ExitStack()
        for p in patches:
            ctx.enter_context(p)
        with ctx:
            return airuleset._watchdog_is_deploy_target()

    def test_target_when_ip_in_remote_hosts(self):
        import airuleset
        entry = next(e for e in airuleset.REMOTE_HOSTS
                     if e.get("host") and not e.get("dev_workstation")
                     and not e.get("pending") and not e.get("paused"))
        self.assertTrue(
            self._run_ips(entry["host"] + "\n",
                          pw_name=entry.get("user", "newlevel")),
            "a box whose tailscale IP is a REMOTE_HOSTS host is a target")

    def test_controller_airuleset_not_a_target(self):
        """#960 R2: airuleset account on the controller must NOT classify as a
        deploy target even though a DIFFERENT account (claudy) on the same IP
        IS a target."""
        import airuleset
        claudy = next((e for e in airuleset.REMOTE_HOSTS
                       if e.get("user") == "claudy"), None)
        if claudy is None:
            self.skipTest("no claudy entry in REMOTE_HOSTS")
        self.assertFalse(
            self._run_ips(claudy["host"] + "\n", pw_name="airuleset"),
            "airuleset account on controller is the push SOURCE, not a target")

    def test_source_when_ip_not_in_remote_hosts(self):
        # dev1 (the source) — an IP not in any REMOTE_HOSTS entry
        self.assertFalse(self._run_ips("192.168.99.99\n"))

    def test_failsafe_on_error(self):
        import airuleset
        import unittest.mock as m
        with m.patch("subprocess.run", side_effect=OSError("no tailscale")):
            self.assertFalse(airuleset._watchdog_is_deploy_target())

    def test_failsafe_on_nonzero_rc(self):
        self.assertFalse(self._run_ips("", rc=1))


if __name__ == "__main__":
    unittest.main()
