"""#1140 part C — disk_guard drains on per-account QUOTA pressure (shared-stream
only) and reports quota-vs-real drift.

The montalu1 EDQUOT incident: fs 69 % (``level=ok``) while the account sat at
its quota — every drain branch keyed on the filesystem level, so nothing ever
drained. These tests inject the quota reading, the drain ladder and ``du``;
nothing here runs a real ``quota``/``du`` or deletes a real path.
"""
import json
import os
import types
from pathlib import Path

import pytest

import watchdog.disk_guard as dg


def _sv(used_pct):
    """A statvfs_fn for ONE mount at ``used_pct`` % bytes used."""
    free = 1000 - int(1000 * used_pct / 100)
    return lambda _m: types.SimpleNamespace(
        f_blocks=1000, f_bfree=free, f_bavail=free, f_frsize=4096,
        f_files=100000, f_ffree=90000)


def _quota_seq(*pcts):
    """A quota_usage_fn returning ``(used_kib, hard_kib)`` for each pct in turn,
    then repeating the last one (None = an unreadable quota). Records calls."""
    calls = []

    def _fn():
        pct = pcts[min(len(calls), len(pcts) - 1)]
        calls.append(pct)
        return None if pct is None else (pct * 100, 10000)
    _fn.calls = calls
    return _fn


def _recording_planners(ran, labels=("r1", "r2", "r3"), actions=None):
    actions = actions or {}

    def _planners(_home, _now):
        out = []
        for lab in labels:
            def _p(lab=lab):
                ran.append(lab)
                return list(actions.get(lab, []))
            out.append((lab, _p))
        return out
    return _planners


@pytest.fixture
def no_prevention(monkeypatch):
    """The prevention pass (real age-out rungs) is replaced by a recorder."""
    ran = []

    def _prev(_home, _now, scratch_rows=None):
        return [("prev", lambda: ran.append("prev") or [])]
    monkeypatch.setattr(dg, "_prevention_planners", _prev)
    return ran


def _run(tmp_path, *, box, fs, quota, planners, dry_run=False, now=10_000.0,
         euid=1000, du_fn=None):
    return dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=dry_run,
        statvfs_fn=_sv(fs), dev_fn=lambda _p: 1,
        geteuid_fn=lambda: euid, planners_fn=planners,
        box_class_fn=lambda: box, quota_usage_fn=quota,
        du_fn=du_fn or (lambda _paths: None),
        sudo_probe_fn=lambda: False, scratch_discover_fn=lambda _n, _h: [],
        severe_run_fn=lambda *a, **k: None)


def _drift_now(base):
    """A ``now`` in THIS uid's drift minute (the first computation is staggered
    by ``uid % QUOTA_DRIFT_STAGGER_MIN`` so accounts never du in lockstep)."""
    m = dg._dgq.QUOTA_DRIFT_STAGGER_MIN
    n = int(base) - int(base) % (60 * m)
    return float(n + 60 * (os.getuid() % m))


def _cache(tmp_path):
    return json.loads((Path(tmp_path) / ".claude" / "disk-guard" / "status.json").read_text())


def test_quota_pressure_drains_on_shared_stream_even_at_fs_ok(tmp_path, no_prevention):
    ran = []
    q = _quota_seq(92)
    logs = _run(tmp_path, box="shared-stream", fs=69, quota=q,
                planners=_recording_planners(ran))
    assert ran, "quota 92 %% at fs 69 %% never ran the drain ladder: %r" % logs
    assert no_prevention == [], "quota pressure must run the FULL ladder, not prevention"
    assert len(q.calls) >= 2, "the ladder's recheck must re-read the quota"
    rec = _cache(tmp_path)["quota_drain"]
    assert rec["before_pct"] == 92
    assert rec["target_pct"] == dg.QUOTA_TARGET_PCT == 80


def test_quota_drain_stops_once_quota_below_target(tmp_path, no_prevention):
    ran = []
    act = {"r1": [{"cls": "tmp-test", "path": str(tmp_path / "x"), "bytes": 5,
                   "kind": "delete"}]}
    # initial read 92, recheck before r1 = 92, recheck before r2 = 79 → STOP
    q = _quota_seq(92, 92, 79)
    logs = _run(tmp_path, box="shared-stream", fs=69, quota=q, dry_run=True,
                planners=_recording_planners(ran, actions=act))
    assert ran == ["r1"], ran
    stop = [ln for ln in logs if " STOP " in ln]
    assert stop and "quota" in stop[0] and "80" in stop[0], stop
    assert _cache(tmp_path)["quota_drain"]["after_pct"] == 79


def test_workstation_ignores_quota(tmp_path, no_prevention):
    ran = []
    q = _quota_seq(99)
    _run(tmp_path, box="workstation", fs=69, quota=q,
         planners=_recording_planners(ran))
    assert ran == []
    assert q.calls == [], "a workstation must never even read the quota"
    assert "quota_pct" not in _cache(tmp_path)


def test_quota_below_trigger_does_not_run_full_ladder(tmp_path, no_prevention):
    ran = []
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(85),
         planners=_recording_planners(ran))
    assert ran == []
    assert "quota_drain" not in _cache(tmp_path)


def test_root_never_quota_drains(tmp_path, no_prevention):
    ran = []
    q = _quota_seq(95)
    _run(tmp_path, box="shared-stream", fs=69, quota=q, euid=0,
         planners=_recording_planners(ran))
    assert ran == []
    assert q.calls == [], "root must never even read a quota"


def test_quota_drain_hourly_cadence_below_95(tmp_path, no_prevention):
    """92 %, drained 700 s ago, no growth since the cached reading → gated."""
    gd = Path(tmp_path) / ".claude" / "disk-guard"
    gd.mkdir(parents=True)
    (gd / "last-drain").write_text("%f" % (10_000.0 - 700))
    (gd / "status.json").write_text(json.dumps({"worst_pct": 69, "quota_pct": 92}))
    ran = []
    logs = _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
                planners=_recording_planners(ran))
    assert ran == []
    assert any("cadence-gated" in ln for ln in logs), logs


def test_quota_growth_bypasses_cadence(tmp_path, no_prevention):
    gd = Path(tmp_path) / ".claude" / "disk-guard"
    gd.mkdir(parents=True)
    (gd / "last-drain").write_text("%f" % (10_000.0 - 700))
    (gd / "status.json").write_text(json.dumps({"worst_pct": 69, "quota_pct": 90}))
    ran = []
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
         planners=_recording_planners(ran))
    assert ran, "a rising quota must bypass the hourly cadence"


def test_quota_at_95_drains_every_poll(tmp_path, no_prevention):
    gd = Path(tmp_path) / ".claude" / "disk-guard"
    gd.mkdir(parents=True)
    (gd / "last-drain").write_text("%f" % (10_000.0 - 60))
    (gd / "status.json").write_text(json.dumps({"worst_pct": 69, "quota_pct": 96}))
    ran = []
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(96),
         planners=_recording_planners(ran))
    assert ran, "quota >= 95 %% must drain every poll (severity beats cadence)"


def test_fs_and_quota_pressure_run_both_passes(tmp_path, no_prevention):
    """fs 85 % AND quota 92 %: the quota pass, then the filesystem pass."""
    ran = []
    _run(tmp_path, box="shared-stream", fs=85, quota=_quota_seq(92), dry_run=True,
         planners=_recording_planners(ran, labels=("r1",)))
    assert ran.count("r1") == 2, ran


def test_quota_drift_finding_hourly(tmp_path, no_prevention):
    uid = os.getuid()
    du_calls = []

    def _du(paths):
        du_calls.append(list(paths))
        return 15 * 1024 * 1024          # 15 GiB in KiB really on disk

    charged = (20 * 1024 * 1024, 30 * 1024 * 1024)   # 20 GiB charged, 67 %
    t0 = _drift_now(10_000.0)
    logs = _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
                planners=_recording_planners([]), du_fn=_du, now=t0)
    assert du_calls == [[str(tmp_path), "/tmp/claude-%d" % uid]]
    drift = _cache(tmp_path)["quota_drift"]
    assert drift["drift_mb"] == 5 * 1024
    assert drift["finding"] is True
    assert any("QUOTA-DRIFT" in ln for ln in logs), logs

    def _du_forbidden(_paths):
        raise AssertionError("du ran again inside the hour")
    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
         planners=_recording_planners([]), du_fn=_du_forbidden, now=t0 + 600)
    assert _cache(tmp_path)["quota_drift"]["drift_mb"] == 5 * 1024, "not carried forward"

    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
         planners=_recording_planners([]), du_fn=_du, now=t0 + 3601)
    assert len(du_calls) == 2, "drift must be recomputed after an hour"


def test_quota_drift_small_is_not_a_finding(tmp_path, no_prevention):
    charged = (10 * 1024 * 1024, 30 * 1024 * 1024)
    logs = _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
                planners=_recording_planners([]), now=_drift_now(10_000.0),
                du_fn=lambda _p: 10 * 1024 * 1024 - 500 * 1024)
    drift = _cache(tmp_path)["quota_drift"]
    assert drift["drift_mb"] == 500
    assert drift["finding"] is False
    assert not any("QUOTA-DRIFT" in ln for ln in logs)


@pytest.mark.parametrize("drift_mb,finding", [(1024, False), (1025, True)])
def test_quota_drift_finding_boundary(tmp_path, no_prevention, drift_mb, finding):
    charged = (10 * 1024 * 1024, 30 * 1024 * 1024)
    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
         planners=_recording_planners([]), now=_drift_now(10_000.0),
         du_fn=lambda _p: 10 * 1024 * 1024 - drift_mb * 1024)
    assert _cache(tmp_path)["quota_drift"]["finding"] is finding


def test_drift_first_computation_is_staggered(tmp_path, no_prevention):
    calls = []
    other = _drift_now(10_000.0) + 60        # the next minute is not ours
    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: (100, 1000),
         planners=_recording_planners([]), now=other,
         du_fn=lambda p: calls.append(p) or 1)
    assert calls == []


def test_drift_never_runs_du_on_a_full_drain_poll(tmp_path, no_prevention):
    def _du(_paths):
        raise AssertionError("du ran in front of the drain ladder")
    ran = []
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
         planners=_recording_planners(ran), du_fn=_du, now=_drift_now(10_000.0))
    assert ran, "the drain itself must still run"


def test_unreadable_quota_mid_pass_stops_and_never_records_zero(tmp_path, no_prevention):
    ran = []
    act = {"r1": [{"cls": "tmp-test", "path": str(tmp_path / "x"), "bytes": 5,
                   "kind": "delete"}]}
    # initial 92, recheck before r1 = 92, recheck before r2 = unreadable
    logs = _run(tmp_path, box="shared-stream", fs=69, dry_run=True,
                quota=_quota_seq(92, 92, None),
                planners=_recording_planners(ran, actions=act))
    assert ran == ["r1"], "kept draining on an unreadable quota: %r" % ran
    assert any("unreadable" in ln and " STOP " in ln for ln in logs), logs
    cache = _cache(tmp_path)
    assert cache["quota_pct"] == 92, "a failed read must never be recorded as 0 %"
    assert cache["quota_drain"]["after_pct"] is None


def test_quota_pass_skips_box_level_rungs(tmp_path, no_prevention):
    ran = []
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
         planners=_recording_planners(ran, labels=("apt-cache", "docker-image",
                                                   "home-worktree", "transcript")))
    assert ran == ["transcript"], ran


def test_quota_drain_record_survives_the_next_poll(tmp_path, no_prevention):
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
         planners=_recording_planners([]))
    assert "quota_drain" in _cache(tmp_path)
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92),
         planners=_recording_planners([]), now=10_000.0 + 120)
    assert _cache(tmp_path)["quota_drain"]["ts"] == 10_000.0


def test_exhausted_quota_drain_backs_off_to_hourly_even_at_95(tmp_path, no_prevention):
    gd = Path(tmp_path) / ".claude" / "disk-guard"
    gd.mkdir(parents=True)
    (gd / "last-drain").write_text("%f" % (10_000.0 - 60))
    (gd / "status.json").write_text(json.dumps({
        "worst_pct": 69, "quota_pct": 96,
        "quota_drain": {"ts": 10_000.0 - 60, "before_pct": 96, "after_pct": 96}}))
    ran = []
    logs = _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(96),
                planners=_recording_planners(ran))
    assert ran == [], "an exhausted quota drain must not re-run every poll"
    assert any("cadence-gated" in ln for ln in logs), logs


def test_real_path_post_drain_write_keeps_quota_fields(tmp_path, monkeypatch, no_prevention):
    """planners_fn=None: the post-drain `disk_status` rewrite must carry quota."""
    monkeypatch.setattr(dg, "_default_planners",
                        lambda _h, _n, scratch_rows=None: [("r1", lambda: [])])
    monkeypatch.setattr(dg, "_collect_top_consumers", lambda *a, **k: [])
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(92), planners=None)
    cache = _cache(tmp_path)
    for k in ("quota_pct", "quota_drain"):
        assert k in cache, "post-drain write dropped %s: %r" % (k, sorted(cache))


def test_default_du_is_niced_and_reads_the_total(tmp_path, monkeypatch):
    seen = []

    class _R:
        stdout = "10\t%s\n32\ttotal\n" % tmp_path

    def _fake_run(argv, **kw):
        seen.append(argv)
        return _R()
    monkeypatch.setattr(dg._dgq.subprocess, "run", _fake_run)
    assert dg._dgq.default_du_kib([str(tmp_path), str(tmp_path / "missing")]) == 32
    assert seen and seen[0][:5] == ["nice", "-n19", "ionice", "-c3", "du"]
    assert seen[0][-1] == str(tmp_path), "a missing path must not be passed to du"
