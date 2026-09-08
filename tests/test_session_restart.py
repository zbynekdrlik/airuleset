"""Tests for watchdog/session_restart.py (Job 46, #947).

RED->GREEN: each test is written to fail on a specific behavior, then the
module makes it pass.  Fixtures use fake /proc data and injected run — no
real tmux, no ssh, no live processes.
"""

from watchdog import session_restart


# --------------------------------------------------------------------------- #
# read_health
# --------------------------------------------------------------------------- #

class TestReadHealth:
    """read_health parses /proc/<pid>/status for VmRSS + VmSwap."""

    def test_normal_rss_and_swap(self):
        """RSS 800000 kB + Swap 100000 kB = 900000."""
        def proc_read(path):
            return (
                "Name:\tnode\n"
                "VmRSS:\t800000 kB\n"
                "VmSwap:\t100000 kB\n"
            )
        assert session_restart.read_health("123", proc_read) == 900000

    def test_rss_only_no_swap(self):
        """No VmSwap line → swap defaults to 0."""
        def proc_read(path):
            return "Name:\tnode\nVmRSS:\t500000 kB\n"
        assert session_restart.read_health("123", proc_read) == 500000

    def test_empty_proc(self):
        """Empty /proc/status → None (fail-safe)."""
        assert session_restart.read_health("123", lambda p: "") is None

    def test_proc_read_raises(self):
        """A raising proc_read → None (fail-safe, never raises)."""
        def boom(p):
            raise OSError("gone")
        assert session_restart.read_health("123", boom) is None

    def test_no_vmrss_line(self):
        """Status without VmRSS → None."""
        def proc_read(path):
            return "Name:\tnode\nVmSwap:\t100 kB\n"
        assert session_restart.read_health("123", proc_read) is None


# --------------------------------------------------------------------------- #
# _is_degraded
# --------------------------------------------------------------------------- #

class TestIsDegraded:
    def test_below_both_thresholds(self):
        assert not session_restart._is_degraded(500000, 3600)

    def test_rss_above_threshold(self):
        assert session_restart._is_degraded(
            session_restart.SESSION_RESTART_RSS_SWAP_KB + 1, 100)

    def test_uptime_above_threshold(self):
        assert session_restart._is_degraded(
            100, session_restart.SESSION_RESTART_UPTIME_S + 1)

    def test_both_above(self):
        assert session_restart._is_degraded(
            session_restart.SESSION_RESTART_RSS_SWAP_KB + 1,
            session_restart.SESSION_RESTART_UPTIME_S + 1)

    def test_none_inputs(self):
        """None inputs → not degraded (fail-safe)."""
        assert not session_restart._is_degraded(None, None)
        assert not session_restart._is_degraded(None, 100)

    def test_exact_threshold_rss(self):
        """Exactly at the threshold → degraded (>=)."""
        assert session_restart._is_degraded(
            session_restart.SESSION_RESTART_RSS_SWAP_KB, 100)

    def test_exact_threshold_uptime(self):
        assert session_restart._is_degraded(
            100, session_restart.SESSION_RESTART_UPTIME_S)


# --------------------------------------------------------------------------- #
# decide — Phase 1 (exit decision)
# --------------------------------------------------------------------------- #

class TestDecidePhase1:
    """Phase 1 decide: degraded session → /exit."""

    # Shortcuts for a degraded session.
    HIGH_RSS = session_restart.SESSION_RESTART_RSS_SWAP_KB + 10000
    LOW_RSS = 100000
    HIGH_UPTIME = session_restart.SESSION_RESTART_UPTIME_S + 100
    LOW_UPTIME = 3600
    NOW = 1000000.0

    def test_not_degraded_skips(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.LOW_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "not-degraded" in line

    def test_all_gates_pass_exit(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action == "exit"
        assert "-> exit" in line
        assert entry["phase"] == "exit-sent"

    def test_defer_not_idle(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:not-idle" in line

    def test_defer_bg_agents_live(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=True, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:bg-agents-live" in line

    def test_defer_busy_waiting(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=True,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:busy-waiting" in line

    def test_defer_compacting(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=True, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:compacting" in line

    def test_defer_recent_human(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=True, human_reason="marker",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:recent-human" in line
        assert "marker" in line

    def test_defer_cooldown(self):
        entry = {"last_restart_ts": self.NOW - 3600}  # 1h ago (< 6h cooldown)
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "defer:cooldown" in line

    def test_cooldown_expired_allows_exit(self):
        # Cooldown expired (> 6h ago).
        entry = {"last_restart_ts": self.NOW - session_restart.SESSION_RESTART_COOLDOWN_S - 1}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action == "exit"

    def test_disabled_flag(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=False, dry_run=False, now=self.NOW)
        assert action is None
        assert "disabled" in line

    def test_dry_run(self):
        entry = {}
        line, action = session_restart.decide(
            entry, self.HIGH_RSS, self.LOW_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=True, now=self.NOW)
        assert action is None
        assert "dry-run" in line

    def test_uptime_triggers_exit(self):
        """Uptime alone (low RSS) triggers exit."""
        entry = {}
        line, action = session_restart.decide(
            entry, self.LOW_RSS, self.HIGH_UPTIME,
            pane_idle=True, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action == "exit"


# --------------------------------------------------------------------------- #
# decide — Phase 2 (relaunch decision)
# --------------------------------------------------------------------------- #

class TestDecidePhase2:
    """Phase 2: after /exit was sent, waiting for PID disappearance."""

    NOW = 1000000.0

    def test_pid_still_alive_waits(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "waiting for PID" in line
        assert entry["exit_sweeps"] == 1

    def test_pid_alive_abandon_after_max_sweeps(self):
        entry = {"phase": "exit-sent", "exit_sweeps": session_restart.EXIT_CONFIRM_SWEEPS - 1}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "exit-not-taken" in line
        assert entry["phase"] is None
        assert entry.get("last_restart_ts") == self.NOW

    def test_pid_gone_pane_not_bare_defers(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0,
                 "pid_gone": True, "pane_bare_idle": False}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "not at bare idle" in line

    def test_pid_gone_pane_bare_relaunches(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0,
                 "pid_gone": True, "pane_bare_idle": True}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=False, now=self.NOW)
        assert action == "relaunch"
        assert "-> relaunch" in line
        assert entry["phase"] == "relaunched"
        assert entry["last_restart_ts"] == self.NOW

    def test_relaunch_deferred_by_human(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0,
                 "pid_gone": True, "pane_bare_idle": True}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=True, human_reason="signal-1",
            enabled=True, dry_run=False, now=self.NOW)
        assert action is None
        assert "recent-human" in line

    def test_relaunch_disabled(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0,
                 "pid_gone": True, "pane_bare_idle": True}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=False, dry_run=False, now=self.NOW)
        assert action is None
        assert "disabled" in line

    def test_relaunch_dry_run(self):
        entry = {"phase": "exit-sent", "exit_sweeps": 0,
                 "pid_gone": True, "pane_bare_idle": True}
        line, action = session_restart.decide(
            entry, None, None,
            pane_idle=False, bg_live=False, busy_waiting=False,
            compacting=False, human_recent=False, human_reason="",
            enabled=True, dry_run=True, now=self.NOW)
        assert action is None
        assert "dry-run" in line


# --------------------------------------------------------------------------- #
# execute_exit / execute_relaunch
# --------------------------------------------------------------------------- #

class TestExecute:
    def test_execute_exit_sends_keys(self):
        calls = []
        def fake_run(argv):
            calls.append(argv)
            return ""
        assert session_restart.execute_exit("%42", fake_run)
        assert len(calls) == 1
        assert calls[0] == ["tmux", "send-keys", "-t", "%42", "/exit", "Enter"]

    def test_execute_exit_no_run(self):
        assert not session_restart.execute_exit("%42", None)

    def test_execute_exit_no_pane(self):
        assert not session_restart.execute_exit("", lambda a: "")

    def test_execute_exit_exception(self):
        def boom(argv):
            raise RuntimeError("fail")
        assert not session_restart.execute_exit("%42", boom)

    def test_execute_relaunch_sends_keys(self):
        calls = []
        def fake_run(argv):
            calls.append(argv)
            return ""
        assert session_restart.execute_relaunch("%42", fake_run)
        assert len(calls) == 1
        assert calls[0] == ["tmux", "send-keys", "-t", "%42",
                             "claude --continue", "Enter"]

    def test_execute_relaunch_exception(self):
        def boom(argv):
            raise RuntimeError("fail")
        assert not session_restart.execute_relaunch("%42", boom)


# --------------------------------------------------------------------------- #
# action_enabled
# --------------------------------------------------------------------------- #

class TestActionEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("AIRULESET_SESSION_RESTART_ACTION", raising=False)
        assert not session_restart.action_enabled()

    def test_on(self, monkeypatch):
        monkeypatch.setenv("AIRULESET_SESSION_RESTART_ACTION", "1")
        assert session_restart.action_enabled()

    def test_true_string(self, monkeypatch):
        monkeypatch.setenv("AIRULESET_SESSION_RESTART_ACTION", "true")
        assert session_restart.action_enabled()

    def test_empty_string(self, monkeypatch):
        monkeypatch.setenv("AIRULESET_SESSION_RESTART_ACTION", "")
        assert not session_restart.action_enabled()


# --------------------------------------------------------------------------- #
# Constants sanity
# --------------------------------------------------------------------------- #

class TestConstants:
    def test_rss_swap_threshold(self):
        assert session_restart.SESSION_RESTART_RSS_SWAP_KB == 700 * 1024

    def test_uptime_threshold(self):
        assert session_restart.SESSION_RESTART_UPTIME_S == 24 * 3600

    def test_cooldown(self):
        assert session_restart.SESSION_RESTART_COOLDOWN_S == 6 * 3600

    def test_exit_confirm_sweeps(self):
        assert session_restart.EXIT_CONFIRM_SWEEPS == 3
