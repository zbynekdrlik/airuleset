"""#1067 slice 1e — disk_guard (watchdog Job 40) names its slow step and never
lets one poll eat the whole sweep.

Live incident (montalu1@subdev, 24.9.2026 20:11): the sweep logged
``job start: disk_guard`` and then nothing for 115 s until systemd killed the
unit — the journal could only say "somewhere inside disk_guard". These tests
pin the two halves of the chosen design:

(a) every step of ``run_disk_guard`` (each drain/prevention rung, the scratch
    discovery, the quota drift ``du``, the prevention pass, the drain as a
    whole) is timed, and a step >= ``STEP_LOG_MIN_S`` logs
    ``disk-guard: step <label> took <N.N>s`` — a healthy poll logs nothing extra;
(b) a wall budget (``DISK_GUARD_BUDGET_S``) measured from the start of
    ``run_disk_guard`` is checked between rungs; once exceeded, the remaining
    rungs are deferred with ONE ``budget exceeded`` line, and the drain is NOT
    stamped as completed, so the next due poll drains again.

The clock is injected (``clock_fn``) and advanced by the fake steps — no real
sleeps, no real deletion paths, no real ``gh``/``sudo``/``quota``/``du``.
"""

import os
import types

import watchdog.disk_guard as dg
from watchdog import disk_guard_timing as dgt


def _statvfs(used_pct):
    blocks = 1_000_000
    bfree = int(blocks * (100 - used_pct) / 100)
    return lambda _m: types.SimpleNamespace(
        f_frsize=4096, f_bsize=4096, f_blocks=blocks, f_bfree=bfree,
        f_bavail=bfree, f_files=1_000_000, f_ffree=800_000, f_favail=800_000)


class FakeClock:
    """A monotonic clock the fake steps advance explicitly."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _rung(clock, took, calls, label):
    def _p():
        calls.append(label)
        clock.advance(took)
        return []
    return (label, _p)


def _poll(tmp_path, clock, planners, now=10_000.0, used_pct=82, **kw):
    """One workstation poll in the 80-89 % drain band with the drain's
    planners injected. Every external seam is faked."""
    kw.setdefault("scratch_discover_fn", lambda _now, _home: [])
    return dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=False,
        statvfs_fn=_statvfs(used_pct), dev_fn=lambda _m: 1, mounts=("/",),
        geteuid_fn=lambda: 1000, planners_fn=lambda _h, _n: planners,
        sudo_probe_fn=lambda: False, box_class_fn=lambda: "workstation",
        clock_fn=clock, **kw)


def _timing_lines(logs):
    return [ln for ln in logs if "disk-guard: step " in ln or "budget exceeded" in ln]


# --------------------------------------------------------------------------- #
# module constants — the thresholds the design names
# --------------------------------------------------------------------------- #
def test_constants_match_design():
    assert dgt.STEP_LOG_MIN_S == 5
    assert dgt.DISK_GUARD_BUDGET_S == 45


# --------------------------------------------------------------------------- #
# (a) per-step timing
# --------------------------------------------------------------------------- #
def test_slow_drain_rung_logs_its_label(tmp_path):
    clock, calls = FakeClock(), []
    logs = _poll(tmp_path, clock, [_rung(clock, 6.0, calls, "fast-a"),
                                   _rung(clock, 7.3, calls, "slow-rung")])
    # fast-a took 6.0 s too (>= 5 s threshold) — both named, with one decimal
    assert "disk-guard: step fast-a took 6.0s" in logs
    assert "disk-guard: step slow-rung took 7.3s" in logs
    assert calls == ["fast-a", "slow-rung"]


def test_step_under_threshold_is_silent_and_threshold_is_inclusive(tmp_path):
    clock, calls = FakeClock(), []
    logs = _poll(tmp_path, clock, [_rung(clock, 4.9, calls, "under"),
                                   _rung(clock, 5.0, calls, "exactly")])
    assert not any("step under" in ln for ln in logs)
    assert "disk-guard: step exactly took 5.0s" in logs


def test_fast_poll_logs_no_timing_line(tmp_path):
    clock, calls = FakeClock(), []
    logs = _poll(tmp_path, clock, [_rung(clock, 0.5, calls, "a"),
                                   _rung(clock, 1.0, calls, "b")])
    assert calls == ["a", "b"]
    assert _timing_lines(logs) == []


def test_slow_scratch_discovery_is_named(tmp_path):
    clock, calls = FakeClock(), []

    def _slow_discover(_now, _home):
        clock.advance(8.0)
        return []

    logs = _poll(tmp_path, clock, [_rung(clock, 0.0, calls, "a")],
                 scratch_discover_fn=_slow_discover)
    assert "disk-guard: step scratch-discovery took 8.0s" in logs


def test_slow_planner_that_raises_is_still_timed(tmp_path):
    clock = FakeClock()

    def _boom():
        clock.advance(9.0)
        raise RuntimeError("walk failed")

    logs = _poll(tmp_path, clock, [("boom", _boom)])
    assert "disk-guard: step boom took 9.0s" in logs
    assert any("planner error" in ln for ln in logs)


def test_shared_stream_quota_drift_and_prevention_are_named(tmp_path, monkeypatch):
    """A shared-stream box below any pressure: the quota drift `du` and the
    proactive prevention pass (#939) both run, and a slow one names itself."""
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners",
                        lambda _h, _n, scratch_rows=None: [_rung(clock, 6.0, calls, "tmp-test")])
    uid = os.getuid()
    now = 60.0 * (uid % 10)           # this uid's staggered first-drift minute slot

    def _slow_du(_paths):
        clock.advance(11.0)
        return 50_000

    logs = dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=False,
        statvfs_fn=_statvfs(40), dev_fn=lambda _m: 1, mounts=("/",),
        geteuid_fn=lambda: 1000, box_class_fn=lambda: "shared-stream",
        quota_usage_fn=lambda: (100_000, 1_000_000), du_fn=_slow_du,
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert "disk-guard: step quota-drift took 11.0s" in logs
    assert "disk-guard: step tmp-test took 6.0s" in logs
    assert "disk-guard: step prevention took 6.0s" in logs
    assert calls == ["tmp-test"]


# --------------------------------------------------------------------------- #
# (b) the wall budget
# --------------------------------------------------------------------------- #
def test_budget_defers_remaining_rungs_with_exact_line(tmp_path):
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 30.0, calls, "a"), _rung(clock, 20.0, calls, "b"),
                _rung(clock, 1.0, calls, "c"), _rung(clock, 1.0, calls, "d")]
    logs = _poll(tmp_path, clock, planners)
    assert calls == ["a", "b"], "rungs c and d must be deferred, not run"
    assert ("disk-guard: budget exceeded after b (50s) — 2 rung(s) deferred to next poll"
            in logs)
    assert sum("budget exceeded" in ln for ln in logs) == 1


def test_budget_exactly_reached_is_not_exceeded(tmp_path):
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 45.0, calls, "a"), _rung(clock, 0.0, calls, "b")]
    logs = _poll(tmp_path, clock, planners)
    assert calls == ["a", "b"]
    assert not any("budget exceeded" in ln for ln in logs)


def test_budget_spent_before_the_ladder_defers_every_rung(tmp_path):
    clock, calls = FakeClock(), []

    def _slow_discover(_now, _home):
        clock.advance(50.0)
        return []

    planners = [_rung(clock, 0.0, calls, "a"), _rung(clock, 0.0, calls, "b")]
    logs = _poll(tmp_path, clock, planners, scratch_discover_fn=_slow_discover)
    assert calls == []
    assert ("disk-guard: budget exceeded after scratch-discovery (50s) — 2 rung(s) "
            "deferred to next poll" in logs)


def test_ladder_that_reaches_target_is_never_reported_as_deferred(tmp_path):
    """A ladder that STOPS under target is complete — even over budget it must
    not claim deferred rungs (and must stamp the drain as completed)."""
    clock, calls = FakeClock(), []
    seq = iter([82, 82, 70, 70, 70, 70, 70])
    status = {"worst_pct": 82, "dim": "bytes", "level": "drain",
              "mounts": [{"mount": "/", "worst_pct": 82}]}
    timer = dgt.PollTimer(clock_fn=clock)
    logs = dg.execute_drain(
        status, str(tmp_path),
        [_rung(clock, 60.0, calls, "a"), _rung(clock, 0.0, calls, "b")],
        recheck_fn=lambda: next(seq), do_action_fn=lambda a: 0,
        geteuid_fn=lambda: 1000, log_path=None, now=1.0, timer=timer)
    assert calls == ["a"]
    assert any("drain complete" in ln for ln in logs)
    assert not any("budget exceeded" in ln for ln in logs)
    assert timer.cut_short is False


def test_next_poll_is_still_allowed_to_drain_after_a_cut_short_poll(tmp_path):
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")]
    logs1 = _poll(tmp_path, clock, planners, now=10_000.0)
    assert calls == ["a"]
    assert any("budget exceeded after a" in ln for ln in logs1)
    # the cut-short drain did NOT stamp last-drain → the 600 s cadence gate
    # does not hold the next poll (60 s later) back
    assert dg._drain_due(str(tmp_path), 10_060.0) is True

    clock2, calls2 = FakeClock(), []
    logs2 = _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, "a"),
                                     _rung(clock2, 0.0, calls2, "b")], now=10_060.0)
    assert not any("cadence-gated" in ln for ln in logs2)
    assert calls2 == ["a", "b"]


def test_completed_poll_still_stamps_and_gates_the_next(tmp_path):
    """Control for the test above: a drain that finished inside the budget
    stamps last-drain exactly as before, so the next poll is cadence-gated."""
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 1.0, calls, "a")], now=10_000.0)
    assert dg._drain_due(str(tmp_path), 10_060.0) is False
    calls2 = []
    clock2 = FakeClock()
    logs2 = _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, "a")], now=10_060.0)
    assert any("cadence-gated" in ln for ln in logs2)
    assert calls2 == []


def test_prevention_pass_respects_the_budget(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", lambda _h, _n, scratch_rows=None: [
        _rung(clock, 46.0, calls, "tmp-test"), _rung(clock, 0.0, calls, "scratch"),
        _rung(clock, 0.0, calls, "worktree")])
    logs = dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=False,
        statvfs_fn=_statvfs(72), dev_fn=lambda _m: 1, mounts=("/",),
        geteuid_fn=lambda: 1000, box_class_fn=lambda: "workstation",
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert calls == ["tmp-test"]
    assert ("disk-guard: budget exceeded after tmp-test (46s) — 2 rung(s) deferred "
            "to next poll" in logs)


def test_quota_then_fs_pass_logs_the_budget_line_once(tmp_path):
    """Both pressures at once: the quota pass runs the ladder, then the fs pass
    runs it again — a budget hit in the first must not print a second line."""
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 50.0, calls, "user-cache"), _rung(clock, 0.0, calls, "scratch")]
    logs = dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=False,
        statvfs_fn=_statvfs(82), dev_fn=lambda _m: 1, mounts=("/",),
        geteuid_fn=lambda: 1000, planners_fn=lambda _h, _n: planners,
        sudo_probe_fn=lambda: False, box_class_fn=lambda: "shared-stream",
        quota_usage_fn=lambda: (950_000, 1_000_000),
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert calls == ["user-cache"]
    assert sum("budget exceeded" in ln for ln in logs) == 1
    assert dg._drain_due(str(tmp_path), 10_060.0) is True
