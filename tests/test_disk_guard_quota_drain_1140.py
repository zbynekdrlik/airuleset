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
    then repeating the last one. Records every call."""
    calls = []

    def _fn():
        pct = pcts[min(len(calls), len(pcts) - 1)]
        calls.append(pct)
        return (pct * 100, 10000)
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
        severe_run_fn=lambda *a, **k: None)


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
    _run(tmp_path, box="shared-stream", fs=69, quota=_quota_seq(95), euid=0,
         planners=_recording_planners(ran))
    assert ran == []


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
    logs = _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
                planners=_recording_planners([]), du_fn=_du)
    assert du_calls == [[str(tmp_path), "/tmp/claude-%d" % uid]]
    drift = _cache(tmp_path)["quota_drift"]
    assert drift["drift_mb"] == 5 * 1024
    assert drift["finding"] is True
    assert any("QUOTA-DRIFT" in ln for ln in logs), logs

    def _du_forbidden(_paths):
        raise AssertionError("du ran again inside the hour")
    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
         planners=_recording_planners([]), du_fn=_du_forbidden, now=10_000.0 + 600)
    assert _cache(tmp_path)["quota_drift"]["drift_mb"] == 5 * 1024, "not carried forward"

    _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
         planners=_recording_planners([]), du_fn=_du, now=10_000.0 + 3601)
    assert len(du_calls) == 2, "drift must be recomputed after an hour"


def test_quota_drift_small_is_not_a_finding(tmp_path, no_prevention):
    charged = (10 * 1024 * 1024, 30 * 1024 * 1024)
    logs = _run(tmp_path, box="shared-stream", fs=50, quota=lambda: charged,
                planners=_recording_planners([]),
                du_fn=lambda _p: 10 * 1024 * 1024 - 500 * 1024)
    drift = _cache(tmp_path)["quota_drift"]
    assert drift["drift_mb"] == 500
    assert drift["finding"] is False
    assert not any("QUOTA-DRIFT" in ln for ln in logs)
