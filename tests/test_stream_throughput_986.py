"""RED tests for #986 items 1, 3, 5 — stream throughput.

Item 1: block-ci-poll-repeat.sh allows autopilot-worker ONE long foreground wait.
Item 3: block-dispatch-over-wdrain.sh gates on stale W count, not total W.
Item 5: cmd_handoff validates shas in Closes-finding lines.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CI_POLL_HOOK = REPO / "hooks" / "block-ci-poll-repeat.sh"
WDRAIN_HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"

sys.path.insert(0, str(REPO))

RUN_A = "30326991380"


def _ci_poll_payload(command, session="sess-986", agent_id=None,
                     agent_type=None, background=False):
    d = {
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if background:
        d["tool_input"]["run_in_background"] = True
    if agent_id:
        d["agent_id"] = agent_id
    if agent_type:
        d["agent_type"] = agent_type
    return json.dumps(d)


def _long_foreground_loop(run_id, sleep_s=120, iters=45):
    """The sanctioned long foreground wait shape."""
    return (
        'DEADLINE=$((SECONDS + %d))\n'
        'for i in $(seq 1 %d); do\n'
        '  s=$(gh run view %s --json status,conclusion,jobs '
        '--jq \'if .status=="completed" then "TERMINAL "+.status+" "'
        '+(.conclusion//"") else "PENDING "+.status end\')\n'
        '  case "$s" in "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; break;; esac\n'
        '  if [ "$SECONDS" -ge "$DEADLINE" ]; then echo "BUDGET"; break; fi\n'
        '  sleep %d\n'
        'done' % (sleep_s * iters, iters, run_id, sleep_s)
    )


def _short_poll_loop(run_id):
    """The default short foreground poll loop."""
    return (
        'DEADLINE=$((SECONDS + 540))\n'
        'for i in $(seq 1 18); do\n'
        '  s=$(gh run view %s --json status,conclusion '
        '--jq \'.status+" "+(.conclusion//"")\')\n'
        '  case "$s" in completed*) echo "TERMINAL: $s"; break;; esac\n'
        '  if [ "$SECONDS" -ge "$DEADLINE" ]; then echo "BUDGET"; break; fi\n'
        '  sleep 30\n'
        'done' % run_id
    )


# ---------- Item 1: CI poll repeat — worker long foreground wait ----------

class TestWorkerLongForegroundWait(unittest.TestCase):
    """An autopilot-worker gets ONE long bounded foreground wait per CI run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _run_ci_hook(self, command, **kw):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        return subprocess.run(
            ["bash", str(CI_POLL_HOOK)],
            input=_ci_poll_payload(command, **kw), text=True, env=env,
            capture_output=True, timeout=30)

    def test_worker_first_short_loop_allowed(self):
        """Loop 1 (short) for a worker is free — unchanged behavior."""
        r = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_worker_second_long_loop_allowed(self):
        """After loop 1 returns non-terminal, a worker gets ONE long
        foreground wait (loop 2) — the #986 change."""
        # Loop 1 (short) — free
        r1 = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        # Loop 2 (long) — must be ALLOWED for autopilot-worker
        r2 = self._run_ci_hook(
            _long_foreground_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        self.assertEqual(r2.returncode, 0,
                         "Worker's long foreground wait was blocked: " + r2.stderr)

    def test_worker_third_loop_still_blocked(self):
        """Loop 3 for a worker is blocked — the burn shape."""
        # Loop 1
        self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        # Loop 2 (long) — free
        self._run_ci_hook(
            _long_foreground_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        # Loop 3 — must be blocked
        r3 = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker")
        self.assertEqual(r3.returncode, 2,
                         "Worker's 3rd loop was not blocked")

    def test_non_worker_subagent_still_blocked_at_loop_2(self):
        """A non-worker subagent (e.g. general-purpose) is still blocked
        at loop 2 — no change for them."""
        # Loop 1
        self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-456", agent_type="general-purpose")
        # Loop 2 — must be blocked (non-worker subagent)
        r2 = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-456", agent_type="general-purpose")
        self.assertEqual(r2.returncode, 2,
                         "Non-worker subagent loop 2 was not blocked")

    def test_sonnet_implementer_also_gets_extra_loop(self):
        """sonnet-implementer gets the same extra loop as autopilot-worker."""
        r1 = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-789", agent_type="sonnet-implementer")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = self._run_ci_hook(
            _long_foreground_loop(RUN_A),
            agent_id="agent-789", agent_type="sonnet-implementer")
        self.assertEqual(r2.returncode, 0,
                         "sonnet-implementer loop 2 was blocked: " + r2.stderr)

    def test_bg_waiter_still_allowed_through_this_hook(self):
        """A background CI poll passes this hook (block-subagent-bg-ci-poll.sh
        handles it)."""
        r = self._run_ci_hook(
            _short_poll_loop(RUN_A),
            agent_id="agent-123", agent_type="autopilot-worker",
            background=True)
        self.assertEqual(r.returncode, 0)


# ---------- Item 3: W-drain gate — stale W only --------------------------

def _cwd_key(cwd):
    sys.path.insert(0, str(REPO))
    try:
        import statusbar
        return statusbar.cwd_key(cwd)
    finally:
        sys.path.pop(0)


def _make_wdrain_cache(tmpdir, cwd, ops_wait, ops_wait_stale=None,
                       ts=None, extra=None):
    """Write a tickets-status cache with ops_wait and ops_wait_stale."""
    key = _cwd_key(cwd)
    cache_dir = pathlib.Path(tmpdir) / ".claude" / "tickets-status"
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = {"ops_wait": ops_wait, "ts": ts or time.time()}
    if ops_wait_stale is not None:
        entry["ops_wait_stale"] = ops_wait_stale
    if extra:
        entry.update(extra)
    with open(cache_dir / (key + ".json"), "w") as f:
        json.dump(entry, f)


def _wdrain_payload(cwd, subagent_type="autopilot-worker"):
    return {
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": subagent_type,
            "prompt": "work issue #1",
        },
        "cwd": str(cwd),
    }


def _run_wdrain_hook(payload, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        ["bash", str(WDRAIN_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    return p.returncode, p.stderr


class TestWdrainStaleOnly(unittest.TestCase):
    """W-drain gate blocks on stale W count, not total W."""

    def test_high_total_w_low_stale_passes(self):
        """W=15 (under hard ceiling 16) but stale=2 — must PASS."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            _make_wdrain_cache(td, cwd, ops_wait=15, ops_wait_stale=2)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 0,
                             "High W with low stale was blocked: " + stderr)

    def test_high_stale_blocks(self):
        """stale=5 — must BLOCK (above stale threshold 3)."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            _make_wdrain_cache(td, cwd, ops_wait=15, ops_wait_stale=5)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 2,
                             "High stale W was not blocked")

    def test_missing_stale_field_falls_back_to_total(self):
        """A legacy cache without ops_wait_stale falls back to ops_wait."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            # No ops_wait_stale → fall back to ops_wait=10 > threshold=8
            _make_wdrain_cache(td, cwd, ops_wait=10)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 2,
                             "Missing stale field did not fall back to total W")

    def test_stale_at_threshold_passes(self):
        """stale=3 exactly — threshold is > 3, so 3 passes."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            _make_wdrain_cache(td, cwd, ops_wait=15, ops_wait_stale=3)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 0,
                             "Stale at threshold was blocked: " + stderr)

    def test_stale_aware_passes_even_at_hard_ceiling(self):
        """W=20 (above hard ceiling 16) but stale=0 — passes because
        stale-aware path supersedes the hard ceiling when per-member
        evidence is available (#986 MAJOR-1 fix)."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            _make_wdrain_cache(td, cwd, ops_wait=20, ops_wait_stale=0)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 0,
                             "Stale-aware path did not supersede ceiling: " + stderr)

    def test_legacy_hard_ceiling_still_blocks(self):
        """Without ops_wait_stale, W=20 > hard ceiling 16 still blocks."""
        with tempfile.TemporaryDirectory() as td:
            cwd = pathlib.Path(td) / "repo"
            cwd.mkdir()
            _make_wdrain_cache(td, cwd, ops_wait=20)
            p = _wdrain_payload(cwd)
            rc, stderr = _run_wdrain_hook(p, {"HOME": td})
            self.assertEqual(rc, 2,
                             "Legacy hard ceiling did not block")


# ---------- Item 5: Closes-finding sha validation -------------------------

class TestClosesFindingShaValidation(unittest.TestCase):
    """cmd_handoff validates that shas in Closes-finding lines exist in the
    branch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        # Create a minimal git repo with one commit
        subprocess.run(["git", "init", self.td], capture_output=True,
                       text=True, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            capture_output=True, text=True, cwd=self.td, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Test",
                 "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "Test",
                 "GIT_COMMITTER_EMAIL": "t@t"})
        self.head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            cwd=self.td).stdout.strip()

    def test_validate_function_exists(self):
        """The validation function for Closes-finding shas must exist in
        airuleset.py."""
        import airuleset
        self.assertTrue(
            hasattr(airuleset, '_validate_closes_finding_shas'),
            "airuleset._validate_closes_finding_shas does not exist")

    def test_validate_rejects_bad_sha(self):
        """_validate_closes_finding_shas rejects a non-reachable sha."""
        import airuleset
        findings = ["F1 — fixed in deadbeefcafe, symbol kept because rename"]
        ok, reason = airuleset._validate_closes_finding_shas(
            findings, cwd=self.td)
        self.assertFalse(ok, "Bad sha was not rejected: %s" % reason)
        self.assertIn("not reachable", reason)

    def test_validate_accepts_good_sha(self):
        """_validate_closes_finding_shas accepts a sha that exists."""
        import airuleset
        findings = ["F1 — fixed in %s, symbol kept because rename"
                    % self.head_sha[:10]]
        ok, reason = airuleset._validate_closes_finding_shas(
            findings, cwd=self.td)
        self.assertTrue(ok, "Good sha was rejected: %s" % reason)

    def test_finding_without_sha_passes(self):
        """A Closes-finding line without a sha pattern passes (no validation
        needed — legacy format)."""
        import airuleset
        findings = ["F1 -- resolved by refactor"]
        ok, reason = airuleset._validate_closes_finding_shas(
            findings, cwd=self.td)
        self.assertTrue(ok, "Line without sha was rejected: %s" % reason)

    def test_empty_findings_passes(self):
        """No Closes-finding lines → nothing to validate."""
        import airuleset
        ok, reason = airuleset._validate_closes_finding_shas([], cwd=self.td)
        self.assertTrue(ok, "Empty findings rejected: %s" % reason)


if __name__ == "__main__":
    unittest.main()
