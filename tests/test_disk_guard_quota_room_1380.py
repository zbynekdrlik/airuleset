"""#1380 — quota pressure is judged on the real ROOM, not on a percentage of a
ceiling that follows usage.

The hourly quota ceiling (`cli_resource_guards_quota._render_quota_limits_block`,
#1140 D / #1315) sets ``hard = used + an equal share of 50 % of the free disk``
(floored at min(5 GiB, 10 % of free)), so a busy account sits near 95-96 % of
its hard limit BY DESIGN: david3 on 20.11. had 56385 MiB used of 58888 MiB
(2.4 GiB of room, 96 %). The guard read that 96 % as pressure (trigger 90 %),
ran the quota drain every poll (>= 95 % bypasses the cadence) toward 80 %,
which the moving ceiling never lets it reach, and treated the account as
critical. Pressure now also needs less than ``QUOTA_MIN_ROOM_KIB`` of real room,
and the pass stops once ``QUOTA_TARGET_ROOM_KIB`` is free again.
"""
import json
import types

import watchdog.disk_guard as dg
from watchdog import disk_guard_quota as dgq

MIB = 1024


def _sv(used_pct):
    free = 1000 - int(1000 * used_pct / 100)
    return lambda _m: types.SimpleNamespace(
        f_blocks=1000, f_bfree=free, f_bavail=free, f_frsize=4096,
        f_files=100000, f_ffree=90000)


def _usage_seq(*pairs):
    """``(used_mib, hard_mib)`` per read, then the last one repeated."""
    calls = []

    def _fn():
        used, hard = pairs[min(len(calls), len(pairs) - 1)]
        calls.append((used, hard))
        return used * MIB, hard * MIB
    _fn.calls = calls
    return _fn


def _run(tmp_path, quota, fs=60, ran=None, now=10_000.0):
    ran = [] if ran is None else ran

    def planners(_h, _n):
        return [(lab, lambda lab=lab: ran.append(lab) or []) for lab in ("r1", "r2", "r3")]
    logs = dg.run_disk_guard(
        now=now, home=str(tmp_path), dry_run=False, statvfs_fn=_sv(fs),
        dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000, planners_fn=planners,
        box_class_fn=lambda: "shared-stream", quota_usage_fn=quota,
        du_fn=lambda _paths: None, sudo_probe_fn=lambda: False,
        scratch_discover_fn=lambda _n, _h: [])
    return ran, logs


def test_david3_numbers_are_not_pressure(tmp_path):
    st = dgq.quota_state({}, str(tmp_path), 10_000.0, True,
                         usage_fn=_usage_seq((56385, 58888)))
    assert st.pct == 96
    assert st.pressure is False, st


def test_little_real_room_is_pressure(tmp_path):
    st = dgq.quota_state({}, str(tmp_path), 10_000.0, True,
                         usage_fn=_usage_seq((58400, 58888)))
    assert st.pressure is True, st


def test_a_moving_ceiling_at_96_pct_runs_no_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "_prevention_planners", lambda *_a, **_k: [])
    ran, logs = _run(tmp_path, _usage_seq((56385, 58888)))
    assert ran == [], logs
    assert "quota_drain" not in json.loads(
        (tmp_path / ".claude" / "disk-guard" / "status.json").read_text())


def test_the_quota_pass_stops_once_the_room_is_back(tmp_path, monkeypatch):
    """0.5 GiB of room drains; after r1 freed 2.5 GiB the room is 3 GiB (95 % of
    the same ceiling, still above the 80 % target) and the pass stops."""
    monkeypatch.setattr(dg, "_prevention_planners", lambda *_a, **_k: [])
    q = _usage_seq((58400, 58888), (58400, 58888), (55900, 58888))
    ran, logs = _run(tmp_path, q)
    assert ran == ["r1"], (ran, logs)
    assert any(" STOP " in ln and "room" in ln for ln in logs), logs


def test_a_quota_drain_with_room_is_not_critical(tmp_path):
    q = dgq.quota_state({}, str(tmp_path), 10_000.0, True,
                        usage_fn=_usage_seq((56385, 58888)))
    status = {"worst_pct": 60, "level": "ok"}
    assert dg._lane_grace_level(status, q) != "critical"
