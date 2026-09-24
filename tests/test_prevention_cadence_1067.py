"""#1067 slice 1f — the prevention pass gets its OWN cadence stamp.

Root cause (design issuecomment-5821343997): ``run_disk_guard`` gated the
prevention pass on ``_drain_due`` — the FULL drain's ``last-drain`` stamp —
while ``_run_prevention_pass`` deliberately never stamps it (#920: that would
delay the real >= 80 % drain). Once ``last-drain`` was older than the interval
the gate stayed true, so the 7-rung prevention ladder ran on EVERY poll — on a
shared-stream box (#939: any pressure, ``target_pct=0``) every minute for each
of the 15 subdev accounts instead of once an hour.

Fix (Approach 1): a separate ``last-prevention`` stamp read by
``_prevention_due``; ``growth_boost`` still bypasses it; the pass stamps only
after a real (non-dry-run) run the 1e budget did NOT cut short, so a cut-short
pass resumes at its deferred rung on the next poll. ``last-drain`` stays owned
by the full drain only.

Every external seam is faked; the clock is injected (no real sleeps).
"""

import types

import pytest

import watchdog.disk_guard as dg


def _statvfs(used_pct):
    blocks = 1_000_000
    bfree = int(blocks * (100 - used_pct) / 100)
    return lambda _m: types.SimpleNamespace(
        f_frsize=4096, f_bsize=4096, f_blocks=blocks, f_bfree=bfree,
        f_bavail=bfree, f_files=1_000_000, f_ffree=800_000, f_favail=800_000)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _ladder(clock, calls, took=None):
    """The prevention ladder, each rung recording its label and advancing the
    fake clock by ``took[label]`` (default 0)."""
    took = took or {}

    def _rung(label):
        def _p():
            calls.append(label)
            clock.advance(took.get(label, 0.0))
            return []
        return (label, _p)
    return lambda _h, _n, scratch_rows=None: [_rung(x) for x in ("tmp-test", "scratch", "worktree")]


# box shape -> (box class, used %) that takes the prevention branch
SHARED = ("shared-stream", 40)       # #939: any pressure, target_pct=0, hourly cadence
WORKSTATION = ("workstation", 72)    # #920: 70-79 %, 600 s cadence


def _poll(tmp_path, now, box=SHARED, used_pct=None, dry_run=False, clock=None):
    box_class, pct = box
    return dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=dry_run,
        statvfs_fn=_statvfs(pct if used_pct is None else used_pct),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        box_class_fn=lambda: box_class,
        quota_usage_fn=lambda: (100_000, 1_000_000), du_fn=lambda _p: None,
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock or FakeClock())


def _stamp(tmp_path, name):
    return dg._guard_dir(str(tmp_path)) / name


# --------------------------------------------------------------------------- #
# the stamp helpers
# --------------------------------------------------------------------------- #
def test_prevention_stamp_helpers_are_siblings_of_the_drain_stamp(tmp_path):
    home = str(tmp_path)
    assert dg.LAST_PREVENTION_NAME == "last-prevention"
    assert dg.LAST_PREVENTION_NAME != dg.LAST_DRAIN_NAME
    assert dg._prevention_due(home, 10_000.0, 3600) is True       # never stamped
    dg._mark_prevented(home, 10_000.0)
    assert dg._prevention_due(home, 10_060.0, 3600) is False
    assert dg._prevention_due(home, 13_600.0, 3600) is True
    assert dg._drain_due(home, 10_060.0, 3600) is True           # the drain's stamp untouched
    assert not _stamp(tmp_path, dg.LAST_DRAIN_NAME).exists()


def test_a_corrupt_prevention_stamp_reads_as_due(tmp_path):
    p = _stamp(tmp_path, dg.LAST_PREVENTION_NAME)
    p.parent.mkdir(parents=True)
    p.write_text("not-a-number")
    assert dg._prevention_due(str(tmp_path), 10_000.0, 3600) is True


# --------------------------------------------------------------------------- #
# acceptance 1 — runs once, a second poll inside the interval runs no rung
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("box,inside,after", [
    (SHARED, 10_060.0, 13_600.0),          # hourly on a shared-stream box
    (WORKSTATION, 10_060.0, 10_600.0),     # MIN_DRAIN_INTERVAL_S at 70-79 %
])
def test_prevention_runs_once_per_interval(tmp_path, monkeypatch, box, inside, after):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    _poll(tmp_path, 10_000.0, box=box, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
    assert float(_stamp(tmp_path, dg.LAST_PREVENTION_NAME).read_text()) == 10_000.0

    calls.clear()
    logs = _poll(tmp_path, inside, box=box, clock=clock)
    assert calls == [], "a poll inside the interval executes no prevention rung"
    assert not any("step prevention" in ln for ln in logs)

    _poll(tmp_path, after, box=box, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"], "due again once the interval elapsed"


# --------------------------------------------------------------------------- #
# acceptance 2 — a cut-short pass is not stamped; the next poll resumes it
# --------------------------------------------------------------------------- #
def test_cut_short_prevention_is_not_stamped_and_resumes(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners",
                        _ladder(clock, calls, took={"tmp-test": 50.0}))
    logs = _poll(tmp_path, 10_000.0, clock=clock)
    assert calls == ["tmp-test"]
    assert any("budget exceeded" in ln for ln in logs)
    assert not _stamp(tmp_path, dg.LAST_PREVENTION_NAME).exists()

    calls.clear()
    clock2 = FakeClock()
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock2, calls))
    logs2 = _poll(tmp_path, 10_060.0, clock=clock2)
    assert calls == ["scratch", "worktree"], "the unstamped pass resumes at the deferred rung"
    assert "disk-guard: resuming ladder at scratch (deferred by the previous poll)" in logs2
    assert float(_stamp(tmp_path, dg.LAST_PREVENTION_NAME).read_text()) == 10_060.0

    calls.clear()
    _poll(tmp_path, 10_120.0, clock=clock2)
    assert calls == [], "the completed resume stamped the cadence"


# --------------------------------------------------------------------------- #
# acceptance 3 — growth_boost bypasses the gate
# --------------------------------------------------------------------------- #
def test_growth_boost_bypasses_the_prevention_gate(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    _poll(tmp_path, 10_000.0, used_pct=40, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]

    calls.clear()
    _poll(tmp_path, 10_060.0, used_pct=45, clock=clock)       # worst_pct rose
    assert calls == ["tmp-test", "scratch", "worktree"], "growth bypasses the cadence"

    calls.clear()
    _poll(tmp_path, 10_120.0, used_pct=45, clock=clock)       # no growth any more
    assert calls == [], "the boosted pass stamped the cadence too"


# --------------------------------------------------------------------------- #
# acceptance 4 — last-drain is untouched; prevention never delays the drain
# --------------------------------------------------------------------------- #
def test_prevention_never_touches_last_drain(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    drain = _stamp(tmp_path, dg.LAST_DRAIN_NAME)
    drain.parent.mkdir(parents=True)
    drain.write_text("%f" % 1_000.0)
    _poll(tmp_path, 10_000.0, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
    assert drain.read_text() == "%f" % 1_000.0


def test_a_fresh_prevention_stamp_never_delays_the_real_drain(tmp_path, monkeypatch):
    """#920 invariant: a prevention pass one poll earlier must not cadence-gate
    the >= 80 % drain."""
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    _poll(tmp_path, 10_000.0, box=WORKSTATION, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
    drained = []
    logs = dg.run_disk_guard(
        now=10_060.0, home=str(tmp_path), dry_run=False, statvfs_fn=_statvfs(82),
        dev_fn=lambda _m: 1, mounts=("/",), geteuid_fn=lambda: 1000,
        planners_fn=lambda _h, _n: [("user-cache", lambda: drained.append(1) or [])],
        sudo_probe_fn=lambda: False, box_class_fn=lambda: "workstation",
        scratch_discover_fn=lambda _n, _h: [], clock_fn=clock)
    assert drained == [1]
    assert not any("cadence-gated" in ln for ln in logs)


# --------------------------------------------------------------------------- #
# acceptance 5 — a dry-run neither reads-as-stamped nor writes the stamp
# --------------------------------------------------------------------------- #
def test_dry_run_does_not_write_the_prevention_stamp(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    _poll(tmp_path, 10_000.0, dry_run=True, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
    assert not _stamp(tmp_path, dg.LAST_PREVENTION_NAME).exists()

    calls.clear()
    _poll(tmp_path, 10_060.0, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"], "a dry-run never stamps a real pass"


def test_dry_run_is_not_gated_by_a_fresh_stamp(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    dg._mark_prevented(str(tmp_path), 10_000.0)
    _poll(tmp_path, 10_060.0, dry_run=True, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
    assert float(_stamp(tmp_path, dg.LAST_PREVENTION_NAME).read_text()) == 10_000.0


# --------------------------------------------------------------------------- #
# the lock held by another drain: nothing ran, nothing stamped
# --------------------------------------------------------------------------- #
def test_a_skipped_pass_under_a_held_lock_is_not_stamped(tmp_path, monkeypatch):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(dg, "_prevention_planners", _ladder(clock, calls))
    held = dg._acquire_lock(str(tmp_path))
    try:
        _poll(tmp_path, 10_000.0, clock=clock)
    finally:
        dg._release_lock(held)
    assert calls == []
    assert not _stamp(tmp_path, dg.LAST_PREVENTION_NAME).exists()
    _poll(tmp_path, 10_060.0, clock=clock)
    assert calls == ["tmp-test", "scratch", "worktree"]
