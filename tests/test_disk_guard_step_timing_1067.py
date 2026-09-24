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

import json
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


def _poll(tmp_path, clock, planners, now=10_000.0, used_pct=82, dry_run=False, **kw):
    """One workstation poll in the 80-89 % drain band with the drain's
    planners injected. Every external seam is faked."""
    kw.setdefault("scratch_discover_fn", lambda _now, _home: [])
    return dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=dry_run,
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


def test_budget_spent_before_the_ladder_still_runs_one_rung(tmp_path):
    """Review finding (#1067 slice 1e): deferring EVERY rung when an earlier
    step spent the budget means a poll whose scratch discovery alone is slow
    never drains anything, forever. Each poll runs at least one rung (the
    `_SweepBudget` first-op guarantee), then defers the rest."""
    clock, calls = FakeClock(), []

    def _slow_discover(_now, _home):
        clock.advance(50.0)
        return []

    planners = [_rung(clock, 0.0, calls, "a"), _rung(clock, 0.0, calls, "b")]
    logs = _poll(tmp_path, clock, planners, scratch_discover_fn=_slow_discover)
    assert calls == ["a"]
    assert ("disk-guard: budget exceeded after a (50s) — 1 rung(s) "
            "deferred to next poll" in logs)


def test_ladder_that_reaches_target_is_never_reported_as_deferred(tmp_path):
    """A ladder that STOPS under target is complete — even over budget it must
    not claim deferred rungs, and the timer must not mark the poll cut short
    (so run_disk_guard stamps the drain as completed)."""
    clock, calls = FakeClock(), []
    seq = iter([82, 70])       # rung a starts at 82 %; rung b starts under target
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
    assert calls2 == ["b"], "the next poll drains, starting at the deferred rung"


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


# --------------------------------------------------------------------------- #
# review round 1 (#1067 slice 1e) — progress, sweep coupling, kill breadcrumb
# --------------------------------------------------------------------------- #
def test_next_poll_resumes_at_the_deferred_rung(tmp_path):
    """Without a resume point every poll restarts at rung 0, so when the head
    rungs alone exceed the budget the tail rungs never run."""
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 30.0, calls, "a"), _rung(clock, 20.0, calls, "b"),
                _rung(clock, 0.0, calls, "c"), _rung(clock, 0.0, calls, "d")]
    _poll(tmp_path, clock, planners, now=10_000.0)
    assert calls == ["a", "b"]
    clock2, calls2 = FakeClock(), []
    logs2 = _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, x) for x in "abcd"],
                  now=10_060.0)
    assert calls2 == ["c", "d"], "the next poll starts at the first deferred rung"
    assert "disk-guard: resuming ladder at c (deferred by the previous poll)" in logs2
    # that drain completed → no resume point left; a later drain starts at rung 0
    clock3, calls3 = FakeClock(), []
    _poll(tmp_path, clock3, [_rung(clock3, 0.0, calls3, x) for x in "abcd"],
          now=10_060.0 + 700)
    assert calls3 == ["a", "b", "c", "d"]


def test_stale_or_foreign_resume_point_is_ignored(tmp_path):
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")],
          now=10_000.0)
    # two hours later the resume point is stale: the ladder starts at rung 0
    clock2, calls2 = FakeClock(), []
    _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, x) for x in "ab"],
          now=10_000.0 + 7200)
    assert calls2 == ["a", "b"]
    # a resume label that is not in this ladder is ignored too
    clock3, calls3 = FakeClock(), []
    _poll(tmp_path, clock3, [_rung(clock3, 50.0, calls3, "a"), _rung(clock3, 0.0, calls3, "b")],
          now=20_000.0)
    clock4, calls4 = FakeClock(), []
    _poll(tmp_path, clock4, [_rung(clock4, 0.0, calls4, x) for x in "xyz"], now=20_060.0)
    assert calls4 == ["x", "y", "z"]


def test_budget_is_capped_by_the_sweep_remaining_budget(tmp_path):
    """disk_guard may start late in the sweep (its floor is 30 s of the 100 s
    soft cap), so its own budget is the smaller of 45 s and what the sweep has
    left — otherwise a late start still runs into the systemd kill."""
    clock, calls = FakeClock(), []
    planners = [_rung(clock, 12.0, calls, "a"), _rung(clock, 0.0, calls, "b")]
    logs = _poll(tmp_path, clock, planners, budget_s=10)
    assert calls == ["a"]
    assert ("disk-guard: budget exceeded after a (12s) — 1 rung(s) deferred to next poll"
            in logs)


def test_run_once_passes_the_sweep_remaining_budget(tmp_path):
    import unittest.mock as mock
    import watchdog as wd
    seen = {}

    def _rec(*_a, **k):
        seen.update(k)
        return []

    n = {"i": 0}

    def _clock():
        n["i"] += 1
        return 5000.0 if n["i"] == 1 else 5020.0

    with mock.patch.object(wd.disk_guard, "run_disk_guard", _rec):
        wd.run_once(now=1_000_000, dry_run=True, run=lambda *a, **k: "",
                    send_fn=lambda *a, **k: None, projects_dir=tmp_path / "proj",
                    state_path=str(tmp_path / "state.json"), time_fn=_clock,
                    disk_guard_enabled=True)
    assert seen.get("budget_s") == wd.SWEEP_SOFT_CAP_S - 20


def test_budget_line_rounds_elapsed_up(tmp_path):
    clock, calls = FakeClock(), []
    logs = _poll(tmp_path, clock, [_rung(clock, 45.4, calls, "a"), _rung(clock, 0.0, calls, "b")])
    assert "disk-guard: budget exceeded after a (46s) — 1 rung(s) deferred to next poll" in logs


def test_drain_step_is_named_when_slow(tmp_path):
    clock, calls = FakeClock(), []
    logs = _poll(tmp_path, clock, [_rung(clock, 3.0, calls, "a"), _rung(clock, 3.0, calls, "b")])
    assert "disk-guard: step drain took 6.0s" in logs
    assert not any("step a " in ln or "step b " in ln for ln in logs)


def test_rung_actions_count_toward_the_rung_step(tmp_path):
    clock = FakeClock()
    timer = dgt.PollTimer(clock_fn=clock)

    def _plan():
        return [{"cls": "user-cache", "path": str(tmp_path / "c"), "bytes": 1,
                 "kind": "delete", "reason": None}]

    def _slow_action(_a):
        clock.advance(6.0)
        return 1

    seq = iter([82, 82])
    logs = dg.execute_drain(
        {"worst_pct": 82, "dim": "bytes", "level": "drain"}, str(tmp_path),
        [("user-cache", _plan)], recheck_fn=lambda: next(seq),
        do_action_fn=_slow_action, geteuid_fn=lambda: 1000, log_path=None,
        now=1.0, dry_run=True, timer=timer)
    assert "disk-guard: step user-cache took 6.0s" in logs


def test_slow_quota_read_is_named(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(dg, "_prevention_planners", lambda _h, _n, scratch_rows=None: [])

    def _slow_usage():
        clock.advance(7.0)
        return (100_000, 1_000_000)

    logs = dg.run_disk_guard(
        now=61.0, home=str(tmp_path), dry_run=True, statvfs_fn=_statvfs(40),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        box_class_fn=lambda: "shared-stream", quota_usage_fn=_slow_usage,
        du_fn=lambda _p: None, planners_fn=lambda _h, _n: [],
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert "disk-guard: step quota-read took 7.0s" in logs


def test_killed_step_is_named_on_the_next_poll(tmp_path):
    """A step that spins until systemd kills the sweep never reaches its
    `took` line — the journal must still name it: the next poll reports the
    step the previous poll died in."""
    clock = FakeClock()
    timer = dgt.PollTimer(clock_fn=clock, state_dir=dg._guard_dir(str(tmp_path)),
                          now=10_000.0)
    outer = timer.step("drain", [])
    outer.__enter__()
    inner = timer.step("home-worktree", [])
    inner.__enter__()               # never exits: the process was killed here
    clock2, calls2 = FakeClock(), []
    logs = _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, "a")], now=10_060.0)
    assert any(ln.startswith("disk-guard: previous poll") and "home-worktree" in ln
               for ln in logs), logs
    # reported once, then gone
    logs3 = _poll(tmp_path, FakeClock(), [], now=10_060.0 + 700)
    assert not any("previous poll" in ln for ln in logs3)
    # `inner`/`outer` stay referenced as locals until here, so their generators
    # are not closed (which would run the step's exit) before the polls above.


def test_finished_steps_leave_no_breadcrumb(tmp_path):
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 0.0, calls, "a")], now=10_000.0)
    logs = _poll(tmp_path, FakeClock(), [], now=10_700.0)
    assert not any("previous poll" in ln for ln in logs)


def test_cut_short_drain_does_not_count_as_exhausted(tmp_path):
    """A poll that deferred its rungs freed nothing — that is not the ladder
    being exhausted, so the owner-facing exhausted streak must not grow."""
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")],
          used_pct=86)
    cache = dg._read_status_cache(str(tmp_path))
    assert cache.get("drain_exhausted_streak", 0) == 0
    assert cache.get("drain_exhausted") is not True
    # control: a completed drain at the same pressure that frees nothing IS exhausted
    clock2, calls2 = FakeClock(), []
    _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, x) for x in "ab"],
          used_pct=86, now=10_060.0)
    assert dg._read_status_cache(str(tmp_path)).get("drain_exhausted_streak") == 1


def test_cut_short_quota_pass_is_not_exhausted(tmp_path):
    from watchdog import disk_guard_quota as dgq
    clock, calls = FakeClock(), []

    def usage():
        return (950_000, 1_000_000)

    dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=False, statvfs_fn=_statvfs(40),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        planners_fn=lambda _h, _n: [_rung(clock, 50.0, calls, "user-cache"),
                                    _rung(clock, 0.0, calls, "scratch")],
        sudo_probe_fn=lambda: False, box_class_fn=lambda: "shared-stream",
        quota_usage_fn=usage, scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert calls == ["user-cache"]
    q = dgq.quota_state({}, str(tmp_path), 10_060.0, True, usage_fn=usage)
    assert q.exhausted is False


def test_quota_then_fs_pass_plans_once_when_cut_short(tmp_path):
    clock, calls, built = FakeClock(), [], []

    def _planners(_h, _n):
        built.append(1)
        return [_rung(clock, 50.0, calls, "user-cache"), _rung(clock, 0.0, calls, "scratch")]

    dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=False, statvfs_fn=_statvfs(82),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        planners_fn=_planners, sudo_probe_fn=lambda: False,
        box_class_fn=lambda: "shared-stream", quota_usage_fn=lambda: (950_000, 1_000_000),
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert len(built) == 1, "the fs pass must not re-plan after a cut-short quota pass"


def test_top_consumers_walk_is_timed_and_skipped_when_cut_short(tmp_path, monkeypatch):
    clock, walks = FakeClock(), []

    def _walk(_home, _now, limit=5, scratch_rows=None):
        walks.append(limit)
        clock.advance(6.0)
        return []

    monkeypatch.setattr(dg, "_collect_top_consumers", _walk)
    calls = []
    monkeypatch.setattr(dg, "_default_planners", lambda _h, _n, scratch_rows=None: [
        _rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")])
    common = dict(home=str(tmp_path), dry_run=False, statvfs_fn=_statvfs(82),
                  dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
                  sudo_probe_fn=lambda: False, box_class_fn=lambda: "workstation",
                  scratch_discover_fn=lambda _n, _h: [])
    dg.run_disk_guard(now=10_000.0, clock_fn=clock, **common)
    assert walks == [], "a cut-short poll must not start the top-consumers walk"
    monkeypatch.setattr(dg, "_default_planners", lambda _h, _n, scratch_rows=None: [
        _rung(clock, 0.0, calls, "c")])
    logs = dg.run_disk_guard(now=10_060.0, clock_fn=clock, **common)
    assert walks == [3]
    assert "disk-guard: step top-consumers took 6.0s" in logs


# --------------------------------------------------------------------------- #
# review round 2 (#1067 slice 1e) — per-ladder resume, dry-run, live breadcrumb
# --------------------------------------------------------------------------- #
def _resume_file(tmp_path):
    return dg._guard_dir(str(tmp_path)) / dgt.RESUME_NAME


def _crumb_file(tmp_path):
    return dg._guard_dir(str(tmp_path)) / dgt.INFLIGHT_NAME


def test_cut_short_prevention_does_not_skip_rungs_of_the_next_drain(tmp_path, monkeypatch):
    """Ladders share rung labels: a resume point written by the prevention
    ladder must not make the next full drain start in its middle."""
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", lambda _h, _n, scratch_rows=None: [
        _rung(clock, 50.0, calls, "tmp-test"), _rung(clock, 0.0, calls, "scratch")])
    dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=False, statvfs_fn=_statvfs(72),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        box_class_fn=lambda: "workstation", scratch_discover_fn=lambda _n, _h: [],
        clock_fn=clock)
    assert calls == ["tmp-test"]
    clock2, calls2 = FakeClock(), []
    _poll(tmp_path, clock2, [_rung(clock2, 0.0, calls2, x)
                             for x in ("session-scratch", "scratch", "uploads")], now=10_060.0)
    assert calls2 == ["session-scratch", "scratch", "uploads"]


def test_fs_ladder_leaves_another_ladders_resume_point(tmp_path):
    rf = _resume_file(tmp_path)
    rf.parent.mkdir(parents=True)
    rf.write_text(json.dumps({"quota": {"label": "scratch", "ts": 10_000.0}}))
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 0.0, calls, x) for x in ("tmp-test", "scratch")],
          now=10_060.0)
    assert calls == ["tmp-test", "scratch"]
    assert json.loads(rf.read_text())["quota"]["label"] == "scratch"


def test_future_dated_resume_point_is_ignored(tmp_path):
    rf = _resume_file(tmp_path)
    rf.parent.mkdir(parents=True)
    rf.write_text(json.dumps({"fs": {"label": "b", "ts": 99_999.0}}))
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 0.0, calls, x) for x in "ab"], now=10_000.0)
    assert calls == ["a", "b"]


def test_dry_run_neither_writes_nor_consumes_the_resume_point(tmp_path):
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")],
          dry_run=True)
    assert not _resume_file(tmp_path).exists()
    # a real cut-short poll writes it; a dry-run poll in between leaves it alone
    clock2, calls2 = FakeClock(), []
    _poll(tmp_path, clock2, [_rung(clock2, 50.0, calls2, "a"), _rung(clock2, 0.0, calls2, "b")],
          now=20_000.0)
    clock3, calls3 = FakeClock(), []
    _poll(tmp_path, clock3, [_rung(clock3, 0.0, calls3, x) for x in "ab"],
          now=20_030.0, dry_run=True)
    assert calls3 == ["a", "b"]
    clock4, calls4 = FakeClock(), []
    _poll(tmp_path, clock4, [_rung(clock4, 0.0, calls4, x) for x in "ab"], now=20_060.0)
    assert calls4 == ["b"]


def test_kill_between_rungs_names_the_enclosing_step(tmp_path):
    clock = FakeClock()
    timer = dgt.PollTimer(clock_fn=clock, state_dir=dg._guard_dir(str(tmp_path)),
                          now=10_000.0)
    outer = timer.step("drain", [])
    outer.__enter__()
    with timer.step("tmp-test", []):
        pass                        # the rung finished; the kill came after it
    logs = _poll(tmp_path, FakeClock(), [], now=10_060.0)
    assert any("killed inside step drain" in ln for ln in logs), logs


def test_breadcrumb_of_a_live_other_process_is_left_alone(tmp_path):
    cf = _crumb_file(tmp_path)
    cf.parent.mkdir(parents=True)
    cf.write_text(json.dumps({"label": "drain", "poll": 9_990.0, "pid": 1}))
    logs = _poll(tmp_path, FakeClock(), [], now=10_000.0)
    assert not any("previous poll" in ln for ln in logs)


def test_status_error_keeps_the_killed_step_report(tmp_path, monkeypatch):
    cf = _crumb_file(tmp_path)
    cf.parent.mkdir(parents=True)
    cf.write_text(json.dumps({"label": "scratch", "poll": 9_990.0}))

    def _boom(**_kw):
        raise OSError("statvfs failed")

    monkeypatch.setattr(dg, "disk_status", _boom)
    logs = dg.run_disk_guard(now=10_000.0, home=str(tmp_path), clock_fn=FakeClock())
    assert any("killed inside step scratch" in ln for ln in logs), logs


def test_cut_short_drain_keeps_top_consumers_and_post_drain_fields(tmp_path):
    home = str(tmp_path)
    dg.write_status_cache({"worst_pct": 81, "top_consumers": [{"path": "/x", "bytes": 5}],
                           "top_consumers_ts": 1.0}, home=home)
    clock, calls = FakeClock(), []

    def _skip_then_slow():
        calls.append("a")
        clock.advance(50.0)
        return [{"cls": "user-cache", "path": "/y", "bytes": 1, "kind": "skip",
                 "reason": "in use"}]

    _poll(tmp_path, clock, [("a", _skip_then_slow), _rung(clock, 0.0, calls, "b")])
    cache = dg._read_status_cache(home)
    assert cache["top_consumers"] == [{"path": "/x", "bytes": 5}]
    assert cache["drain_skipped_rungs"][0]["path"] == "/y"


def test_cut_short_critical_poll_escalates_without_a_new_walk(tmp_path, monkeypatch):
    home = str(tmp_path)
    dg.write_status_cache({"worst_pct": 91, "top_consumers": [{"path": "/x", "bytes": 5}]},
                          home=home)
    seen, walks = [], []
    monkeypatch.setattr(dg, "escalate", lambda post, h, n, d, top_consumers_fn=None:
                        seen.append(top_consumers_fn()) or [])
    monkeypatch.setattr(dg, "_record_root_finding_or_warn", lambda *a, **k: [])
    clock, calls = FakeClock(), []
    _poll(tmp_path, clock, [_rung(clock, 50.0, calls, "a"), _rung(clock, 0.0, calls, "b")],
          used_pct=92, top_consumers_fn=lambda *a, **k: walks.append(1) or [])
    assert walks == [], "a cut-short poll must not start a new top-consumers walk"
    assert seen == [[("/x", 5)]]
