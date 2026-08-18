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


def collect_send():
    """A recording ``send_fn`` that HONORS ``dedup_key`` exactly like the real
    ``notify.send`` (persistent per-key dedup with a TTL far longer than the job's
    own reping): a second call with a dedup_key already seen returns ``"dedup"`` and
    does NOT deliver. This is what makes the dedup tests genuine teeth — a fake that
    ignored dedup_key would false-green a bucketed key that swallows real re-pings
    (#535 review MAJOR-2). ``calls`` records only DELIVERED sends."""
    calls = []
    seen_keys = set()

    def send(body, dedup_key=None, dry_run=False):
        if dedup_key is not None and dedup_key in seen_keys:
            return "dedup"
        if dedup_key is not None:
            seen_keys.add(dedup_key)
        calls.append({"body": body, "dedup_key": dedup_key})
        return "sent"
    return calls, send


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


def _run(state, tmp, send=None, dry_run=False, git=None, timer=None,
         claude_content="managed content\n", baseline_md5="MATCH",
         baseline_head="aaaaaaaa1111", now=NOW, **git_kw):
    """Drive ``run_conformance_check`` with fully-controlled I/O seams. By default
    every dimension is CONFORMANT (uniform state): HEAD==origin, clean, timer
    active, and the baseline md5 is stamped to the on-disk file's real md5."""
    cmd = _claude_md(tmp, claude_content)
    real_md5 = conf._md5_file(cmd)
    md5 = real_md5 if baseline_md5 == "MATCH" else baseline_md5
    base = _baseline(tmp, md5=md5, head=baseline_head)
    calls = None
    if send is None:
        calls, send = collect_send()
    logs = conf.run_conformance_check(
        now, state, send_fn=send, dry_run=dry_run, repo_root=ROOT,
        claude_md_path=cmd, baseline_path=base,
        git_run=git or fake_git(**git_kw),
        timer_check=timer or fake_timer(),
        persist=lambda: None)
    return logs, calls


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
    def test_uniform_state_is_silent(self):
        with TemporaryDirectory() as d:
            logs, calls = _run({}, d)
            self.assertEqual(calls, [], "conformant fleet must not ping")
            self.assertTrue(any("[head] OK" in ln for ln in logs))
            self.assertTrue(any("[timer] OK" in ln for ln in logs))

    def test_head_behind_pings(self):
        with TemporaryDirectory() as d:
            logs, calls = _run({}, d, head="aaa11111", origin="bbb22222",
                               ancestor_rc=0)   # HEAD is ancestor of origin = behind
            self.assertEqual(len(calls), 1)
            self.assertIn("head", calls[0]["dedup_key"])

    def test_head_ahead_does_not_ping(self):
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, head="aaa11111", origin="bbb22222",
                            ancestor_rc=1)       # HEAD NOT ancestor of origin = ahead/diverged
            self.assertEqual(calls, [])

    def test_dirty_tree_pings(self):
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, status=" M airuleset.py\n")
            self.assertEqual(len(calls), 1)
            self.assertIn("dirty", calls[0]["dedup_key"])

    def test_dirty_tree_off_baseline_does_not_ping(self):
        # dev1 (HEAD ahead of origin = local dev) with a dirty tree must be SILENT
        # on the dirty dimension — the MAJOR-1 false-alarm the review caught.
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, status=" M airuleset.py\n",
                            head="aaa11111", origin="bbb22222", ancestor_rc=1)
            self.assertEqual(calls, [], "a dirty dev-box tree off baseline must not alarm")

    def test_md5_mismatch_pings(self):
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, baseline_md5="DIFFERENT_HASH")
            self.assertEqual(len(calls), 1)
            self.assertIn("claude_md", calls[0]["dedup_key"])

    def test_md5_midpush_head_mismatch_does_not_ping(self):
        # baseline recorded at a DIFFERENT head than HEAD -> install pending -> skip
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, baseline_md5="DIFFERENT_HASH",
                            baseline_head="00000000ffff",
                            head="aaaaaaaa1111", origin="aaaaaaaa1111")
            self.assertEqual(calls, [], "mid-push md5 mismatch must be skipped, not pinged")

    def test_timer_inactive_pings(self):
        with TemporaryDirectory() as d:
            _, calls = _run({}, d, timer=fake_timer("inactive"))
            self.assertEqual(len(calls), 1)
            self.assertIn("timer", calls[0]["dedup_key"])

    def test_fetch_failure_logs_no_alarm(self):
        with TemporaryDirectory() as d:
            logs, calls = _run({}, d, fetch_rc=1, head="aaa", origin="aaa")
            self.assertEqual(calls, [], "a fetch failure must never be a drift alarm")
            self.assertTrue(any("fetch zlyhal" in ln for ln in logs))

    def test_dedup_same_divergence_no_reping(self):
        with TemporaryDirectory() as d:
            state = {}
            _, calls1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(len(calls1), 1)
            # next daily check, unchanged divergence, within reping window -> silent
            state["conformance_last_check"] = 0    # make it due again
            _, calls2 = _run(state, d, timer=fake_timer("inactive"), now=NOW + DAY)
            self.assertEqual(calls2, [], "unchanged divergence must not re-spam daily")

    def test_dedup_changed_divergence_repings(self):
        with TemporaryDirectory() as d:
            state = {}
            _, calls1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(len(calls1), 1)
            state["conformance_last_check"] = 0
            # a DIFFERENT drift signature (failed vs inactive) re-pings immediately
            _, calls2 = _run(state, d, timer=fake_timer("failed"), now=NOW + DAY)
            self.assertEqual(len(calls2), 1, "a materially different drift must re-ping")

    def test_dedup_past_reping_repings(self):
        with TemporaryDirectory() as d:
            state = {}
            _, calls1 = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(len(calls1), 1)
            state["conformance_last_check"] = 0
            _, calls2 = _run(state, d, timer=fake_timer("inactive"),
                             now=NOW + 4 * DAY)   # past the 3-day reping
            self.assertEqual(len(calls2), 1, "never permanently silent: re-remind past reping")

    def test_send_layer_dedup_key_is_fresh_per_decision_not_bucketed(self):
        # MAJOR-2: a changed-sig re-ping the primary `seen` ALLOWS must not be
        # swallowed by the send-layer dedup_key. Share ONE send fake (persistent
        # seen_keys, like real notify) across two divergences in the SAME reping
        # bucket — both must DELIVER because the key is fresh per decision instant
        # (int(now)); a bucketed int(now//reping) key would collide and swallow the
        # second. This is the send-LAYER teeth the per-_run fresh fakes cannot give.
        with TemporaryDirectory() as d:
            state = {}
            calls, send = collect_send()
            _run(state, d, send=send, timer=fake_timer("inactive"), now=NOW)
            state["conformance_last_check"] = 0
            _run(state, d, send=send, timer=fake_timer("failed"), now=NOW + DAY)
            self.assertEqual(len(calls), 2,
                             "both drifts in one reping bucket must deliver — the send "
                             "dedup_key must be fresh per decision, not bucketed")

    def test_resolved_clears_dedup_so_redivergence_repings(self):
        with TemporaryDirectory() as d:
            state = {}
            _run(state, d, timer=fake_timer("inactive"), now=NOW)
            state["conformance_last_check"] = 0
            _run(state, d, timer=fake_timer("active"), now=NOW + DAY)   # resolved
            self.assertNotIn("timer", state.get("conformance", {}))
            state["conformance_last_check"] = 0
            _, calls = _run(state, d, timer=fake_timer("inactive"), now=NOW + 2 * DAY)
            self.assertEqual(len(calls), 1, "a re-divergence after a fix must re-ping immediately")

    def test_undetermined_preserves_prior_episode(self):
        # a real drift pings; a following UNDETERMINED sweep (systemctl unreadable)
        # must NOT clear the dedup, so a fetch-flake style recovery can't re-spam.
        with TemporaryDirectory() as d:
            state = {}
            _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertIn("timer", state.get("conformance", {}))
            state["conformance_last_check"] = 0
            _run(state, d, timer=fake_timer(None), now=NOW + DAY)   # undetermined
            self.assertIn("timer", state.get("conformance", {}),
                          "an UNDETERMINED sweep must not drop the prior episode (#486-G5)")

    def test_dry_run_never_pings_or_mutates_state(self):
        with TemporaryDirectory() as d:
            state = {}
            logs, calls = _run(state, d, timer=fake_timer("inactive"), dry_run=True)
            self.assertEqual(calls, [], "dry-run never sends")
            self.assertNotIn("conformance", state, "dry-run mutates no state")
            self.assertNotIn("conformance_last_check", state, "dry-run never advances cadence")
            self.assertTrue(any("WOULD-PING" in ln for ln in logs))

    def test_cadence_not_due_returns_early(self):
        with TemporaryDirectory() as d:
            state = {"conformance_last_check": NOW - 100}   # just checked
            logs, calls = _run(state, d, timer=fake_timer("inactive"), now=NOW)
            self.assertEqual(logs, [])
            self.assertEqual(calls, [])

    def test_no_repo_root_is_noop(self):
        self.assertEqual(
            conf.run_conformance_check(NOW, {}, send_fn=lambda *a, **k: "sent",
                                       repo_root=None), [])


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


if __name__ == "__main__":
    unittest.main()
