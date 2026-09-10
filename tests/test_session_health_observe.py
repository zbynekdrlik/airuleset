"""Tests for watchdog/session_health_observe.py (Job 46, #947 reversal).

Ported from the deleted test_session_restart.py (12 tests on kept code:
read_health + _is_degraded) plus new tests for observe() and
cleanup_session_restart_dropins().
"""

from watchdog import session_health_observe as sho
from cli_filedrop_watchdog import cleanup_session_restart_dropins


# --------------------------------------------------------------------------- #
# read_health (ported from test_session_restart.py::TestReadHealth)
# --------------------------------------------------------------------------- #

class TestReadHealth:
    """read_health parses /proc/<pid>/status for VmRSS + VmSwap."""

    def test_normal_rss_and_swap(self):
        def proc_read(path):
            return "Name:\tnode\nVmRSS:\t800000 kB\nVmSwap:\t100000 kB\n"
        assert sho.read_health("123", proc_read) == 900000

    def test_rss_only_no_swap(self):
        def proc_read(path):
            return "Name:\tnode\nVmRSS:\t500000 kB\n"
        assert sho.read_health("123", proc_read) == 500000

    def test_empty_proc(self):
        assert sho.read_health("123", lambda p: "") is None

    def test_proc_read_raises(self):
        def boom(p):
            raise OSError("gone")
        assert sho.read_health("123", boom) is None

    def test_no_vmrss_line(self):
        def proc_read(path):
            return "Name:\tnode\nVmSwap:\t100 kB\n"
        assert sho.read_health("123", proc_read) is None


# --------------------------------------------------------------------------- #
# _is_degraded (ported from test_session_restart.py::TestIsDegraded)
# --------------------------------------------------------------------------- #

class TestIsDegraded:
    def test_below_both_thresholds(self):
        assert not sho._is_degraded(500000, 3600)

    def test_rss_above_threshold(self):
        assert sho._is_degraded(sho.SESSION_HEALTH_RSS_SWAP_KB + 1, 100)

    def test_uptime_above_threshold(self):
        assert sho._is_degraded(100, sho.SESSION_HEALTH_UPTIME_S + 1)

    def test_both_above(self):
        assert sho._is_degraded(
            sho.SESSION_HEALTH_RSS_SWAP_KB + 1,
            sho.SESSION_HEALTH_UPTIME_S + 1)

    def test_none_inputs(self):
        assert not sho._is_degraded(None, None)
        assert not sho._is_degraded(None, 100)

    def test_exact_threshold_rss(self):
        assert sho._is_degraded(sho.SESSION_HEALTH_RSS_SWAP_KB, 100)

    def test_exact_threshold_uptime(self):
        assert sho._is_degraded(100, sho.SESSION_HEALTH_UPTIME_S)


# --------------------------------------------------------------------------- #
# observe (new)
# --------------------------------------------------------------------------- #

class TestObserve:
    def test_degraded_returns_log_line(self):
        line = sho.observe("%42", sho.SESSION_HEALTH_RSS_SWAP_KB + 1, 100)
        assert line is not None
        assert "degraded" in line
        assert "%42" in line

    def test_healthy_returns_none(self):
        line = sho.observe("%42", 100000, 3600)
        assert line is None


# --------------------------------------------------------------------------- #
# Constants sanity (ported)
# --------------------------------------------------------------------------- #

class TestConstants:
    def test_rss_swap_threshold(self):
        assert sho.SESSION_HEALTH_RSS_SWAP_KB == 700 * 1024

    def test_uptime_threshold(self):
        assert sho.SESSION_HEALTH_UPTIME_S == 24 * 3600


# --------------------------------------------------------------------------- #
# cleanup_session_restart_dropins (new, Y2)
# --------------------------------------------------------------------------- #

class TestCleanupDropins:
    def test_removes_present_files_and_reloads(self, tmp_path):
        f1 = tmp_path / "50-airuleset-session-restart.conf"
        f2 = tmp_path / "50-session-restart-947.conf"
        f1.write_text("[Service]\n")
        f2.write_text("[Service]\n")
        reloads = []
        cleanup_session_restart_dropins(
            paths=[f1, f2],
            daemon_reload_fn=lambda: reloads.append(1),
        )
        assert not f1.exists()
        assert not f2.exists()
        assert len(reloads) == 1

    def test_nothing_present_no_reload(self, tmp_path):
        f1 = tmp_path / "50-airuleset-session-restart.conf"
        f2 = tmp_path / "50-session-restart-947.conf"
        reloads = []
        cleanup_session_restart_dropins(
            paths=[f1, f2],
            daemon_reload_fn=lambda: reloads.append(1),
        )
        assert len(reloads) == 0

    def test_oserror_logged_not_raised(self, tmp_path, capsys):
        # A directory cannot be unlinked — OSError logged, not raised.
        d = tmp_path / "50-airuleset-session-restart.conf"
        d.mkdir()
        reloads = []
        cleanup_session_restart_dropins(
            paths=[d],
            daemon_reload_fn=lambda: reloads.append(1),
        )
        err = capsys.readouterr().err
        assert "failed to remove" in err
