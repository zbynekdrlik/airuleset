"""#1195 item 4 — the worktree reclaimers refuse the REAL home under pytest.

Live (the #1194 split commit): a test missed a patch seam, so a real
non-dry-run ``sweep_stale_worktrees`` ran against the controller's real
``~/.claude``. All 200 rows were SKIP, but ``worktree-sweep-state.json``
``last_run`` was stamped. The disk filer already refuses its real default
under pytest (#1136, ``disk_guard_escalation``); the reclaimers did not.

Now both ``cli_worktree_stale.sweep_stale_worktrees`` and
``cli_lane_target_reclaim.purge_merged_lane_targets`` RAISE on a non-dry-run
call under pytest whose home / log / state paths resolve to the real account
home, before anything is read, walked or written. Explicit tmp paths keep
working.

Safety of this file itself: every call that reaches a reclaimer with its real
defaults also patches discovery to raise and the log writer to a mock. Without
the guard (the RED phase) discovery then fails, which skips the state write,
so nothing real is ever touched even before the fix exists.
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_lane_target_reclaim as ltr  # noqa: E402
import cli_worktree_common as wc  # noqa: E402
import cli_worktree_stale as ws  # noqa: E402
from watchdog import disk_guard_escalation as esc  # noqa: E402

REFUSED = r"refused under pytest"


def _stale_net():
    """Discovery raises (so an unguarded call stamps no state), log is a mock."""
    return (mock.patch.object(ws, "discover_stale_worktrees",
                              side_effect=AssertionError("reached discovery")),
            mock.patch.object(ws, "_log_stale_worktree_results"))


def _lane_net():
    return (mock.patch.object(ltr, "_iter_lane_target_dirs",
                              side_effect=AssertionError("reached discovery")),
            mock.patch.object(ltr, "_log_lane_target_results"))


# --------------------------------------------------------------------------- #
# the #1136 detection is reused, not duplicated
# --------------------------------------------------------------------------- #

def test_running_under_pytest_is_the_1136_detection(monkeypatch):
    assert esc.running_under_pytest() is True
    assert esc.default_filer_refused() is True
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert esc.running_under_pytest() is False
    assert esc.default_filer_refused() is False


def test_the_guard_calls_the_1136_detection():
    with mock.patch.object(esc, "running_under_pytest", return_value=False) as det:
        wc.refuse_real_paths_under_pytest("x", False, home=None,
                                          state_path=ws.STALE_WORKTREE_STATE_PATH)
    det.assert_called_once_with()


# --------------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------------- #

def test_real_default_paths_are_refused():
    with pytest.raises(wc.RealPathUnderPytest, match=REFUSED) as ei:
        wc.refuse_real_paths_under_pytest(
            "sweep_stale_worktrees", False, home=None,
            log_path=ws.STALE_WORKTREE_LOG_PATH,
            state_path=ws.STALE_WORKTREE_STATE_PATH)
    assert "state_path" in str(ei.value) and "#1195" in str(ei.value)
    assert isinstance(ei.value, RuntimeError)


@pytest.mark.parametrize("name", ["LANE_TARGET_LOG_PATH", "LANE_TARGET_STATE_PATH",
                                  "LANE_TARGET_TIER0_BYPASS_STATE_PATH"])
def test_every_lane_target_default_is_real(name):
    with pytest.raises(wc.RealPathUnderPytest):
        wc.refuse_real_paths_under_pytest("purge", False, **{"p": getattr(ltr, name)})


def test_real_home_and_its_ancestors_are_refused(tmp_path):
    for home in (wc.CLAUDE_DIR.parent, wc.CLAUDE_DIR.parent.parent):
        with pytest.raises(wc.RealPathUnderPytest):
            wc.refuse_real_paths_under_pytest("x", False, home=home)
    # a tmp home is fine, and so are tmp files
    wc.refuse_real_paths_under_pytest("x", False, home=tmp_path,
                                      state_path=tmp_path / "s", log_path=tmp_path / "l")


def test_home_env_repointed_to_tmp_is_not_the_real_home(tmp_path, monkeypatch):
    """A test that points $HOME at a tmp dir and leaves ``home=None`` resolves
    to that tmp dir, never to the account's real home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    out = ws.sweep_stale_worktrees(force=True, log_path=tmp_path / "l",
                                   state_path=tmp_path / "s")
    assert out == []
    assert (tmp_path / "s").exists()
    out = ltr.purge_merged_lane_targets(
        force=True, log_path=tmp_path / "l2", state_path=tmp_path / "s2",
        bypass_state_path=tmp_path / "b", issue_filer=lambda *a: None,
        tier0_fn=lambda cwd: True, proc_dir=tmp_path / "proc")
    assert out == []
    assert (tmp_path / "s2").exists()


def test_dry_run_and_non_pytest_are_never_refused(monkeypatch):
    wc.refuse_real_paths_under_pytest("x", True, home=wc.CLAUDE_DIR.parent,
                                      state_path=ws.STALE_WORKTREE_STATE_PATH)
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    wc.refuse_real_paths_under_pytest("x", False, home=wc.CLAUDE_DIR.parent,
                                      state_path=ws.STALE_WORKTREE_STATE_PATH)


# --------------------------------------------------------------------------- #
# sweep_stale_worktrees
# --------------------------------------------------------------------------- #

def test_sweep_with_real_defaults_raises_before_any_work():
    disc, log = _stale_net()
    with disc as d, log as lg:
        with pytest.raises(RuntimeError, match=REFUSED):
            ws.sweep_stale_worktrees(force=True)
    d.assert_not_called()
    lg.assert_not_called()


def test_sweep_cadence_gated_call_is_refused_too():
    """A non-forced call is refused before the cadence gate reads the real
    state file (it would stamp it on the way out)."""
    disc, log = _stale_net()
    with disc, log:
        with pytest.raises(RuntimeError, match=REFUSED):
            ws.sweep_stale_worktrees()


def test_sweep_real_state_path_alone_is_refused(tmp_path):
    disc, log = _stale_net()
    with disc, log:
        with pytest.raises(RuntimeError, match=REFUSED):
            ws.sweep_stale_worktrees(home=tmp_path, log_path=tmp_path / "l", force=True)


def test_sweep_given_candidates_ignores_home(tmp_path):
    """With explicit candidates, discovery (the only ``home`` reader) never
    runs, so ``home=None`` is not a real-path hit."""
    out = ws.sweep_stale_worktrees(candidates=[], force=True,
                                   log_path=tmp_path / "l", state_path=tmp_path / "s")
    assert out == []
    assert (tmp_path / "s").exists()


def test_sweep_with_explicit_tmp_paths_runs(tmp_path):
    out = ws.sweep_stale_worktrees(home=tmp_path, force=True,
                                   log_path=tmp_path / "l", state_path=tmp_path / "s")
    assert out == []
    assert (tmp_path / "s").exists()


def test_sweep_dry_run_with_defaults_is_allowed():
    with mock.patch.object(ws, "discover_stale_worktrees", return_value=[]), \
            mock.patch.object(ws, "discover_orphaned_worktree_branches", return_value=[]), \
            mock.patch.object(ws, "_log_stale_worktree_results") as lg:
        assert ws.sweep_stale_worktrees(dry_run=True) == []
    lg.assert_called_once()


# --------------------------------------------------------------------------- #
# purge_merged_lane_targets
# --------------------------------------------------------------------------- #

def test_purge_with_real_defaults_raises_before_any_work():
    disc, log = _lane_net()
    with disc as d, log as lg:
        with pytest.raises(RuntimeError, match=REFUSED):
            ltr.purge_merged_lane_targets(force=True)
    d.assert_not_called()
    lg.assert_not_called()


def test_purge_real_bypass_state_alone_is_refused(tmp_path):
    disc, log = _lane_net()
    with disc, log:
        with pytest.raises(RuntimeError, match=REFUSED):
            ltr.purge_merged_lane_targets(home=tmp_path, force=True,
                                          log_path=tmp_path / "l",
                                          state_path=tmp_path / "s")


def test_purge_with_explicit_tmp_paths_runs(tmp_path):
    out = ltr.purge_merged_lane_targets(
        home=tmp_path, force=True, log_path=tmp_path / "l", state_path=tmp_path / "s",
        bypass_state_path=tmp_path / "b", issue_filer=lambda *a: None,
        tier0_fn=lambda cwd: True, proc_dir=tmp_path / "proc")
    assert out == []
    assert (tmp_path / "s").exists()

