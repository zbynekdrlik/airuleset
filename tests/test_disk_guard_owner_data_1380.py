"""#1380 (owner 20.11.2026) — the disk guard never deletes owner data.

On david3@subdev the drain ladder deleted the owner's Worldline documents from
``~/uploads`` (rung ``uploads``) and a whole 1.9 GB
``~/.claude/work-products/7598`` (rung ``work-products-snapshot``), both at level
``quota``. ``home-snapshot`` deletes top-level home dirs of the same shape. A
drain may delete only regenerable bytes, so:

- no ladder (fs drain at every level, quota, prevention) carries those rungs;
- the executor refuses any action inside, or above, ``~/uploads`` and
  ``~/.claude/work-products``, whatever rung or class emits it.

Everything runs on a temp home. The sparse 1.9 GB file costs no disk.
"""
import os
import time
import types

import pytest

import watchdog.disk_guard as dg
from watchdog import disk_guard_quota as dgq

OWNER_DATA_RUNGS = ("uploads", "work-products-snapshot", "home-snapshot")
WP_BYTES = 1_935_195_857          # the 18.11. david3 ``work-products/7598``


def _sv(used_pct):
    free = 1000 - int(1000 * used_pct / 100)
    return lambda _m: types.SimpleNamespace(
        f_blocks=1000, f_bfree=free, f_bavail=free, f_frsize=4096,
        f_files=100000, f_ffree=90000)


def _owner_home(tmp_path):
    """A home holding the three owner-data shapes, written ``now``; tests read
    them 30 days later (``_LATER``)."""
    home = tmp_path / "home"
    up = home / "uploads" / "worldline"
    up.mkdir(parents=True)
    (up / "POS2ECR_PRSK05-EN_v1.00_rev07.pdf").write_bytes(b"%PDF" + b"x" * 2000)
    wp = home / ".claude" / "work-products" / "7598"
    wp.mkdir(parents=True)
    (wp / "runbook.md").write_text("certification runbook\n")
    with open(wp / "sdk-pack.tar", "wb") as f:
        f.truncate(WP_BYTES)
    vendor = home / "vendor" / "worldline"
    vendor.mkdir(parents=True)
    with open(vendor / "spec-pack.zip", "wb") as f:
        f.truncate(WP_BYTES)
    return home, {
        "upload": up / "POS2ECR_PRSK05-EN_v1.00_rev07.pdf",
        "work-product": wp / "runbook.md",
        "work-product-pack": wp / "sdk-pack.tar",
        "home-dir": vendor / "spec-pack.zip",
    }


_LATER = time.time() + 30 * 86400


def _assert_all_exist(files):
    gone = [k for k, p in files.items() if not p.exists()]
    assert not gone, "owner data deleted by the drain: %s" % gone


# --------------------------------------------------------------------------- #
# the ladders
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("level", [None, "drain", "critical"])
def test_no_fs_ladder_carries_an_owner_data_rung(tmp_path, level):
    labels = [lab for lab, _p in dg._default_planners(str(tmp_path), _LATER, level=level)]
    assert not set(labels) & set(OWNER_DATA_RUNGS), labels


def test_no_prevention_or_quota_ladder_carries_an_owner_data_rung(tmp_path):
    prev = [lab for lab, _p in dg._prevention_planners(str(tmp_path), _LATER)]
    quota = [lab for lab, _p in dg._default_planners(str(tmp_path), _LATER)
             if lab not in dgq.QUOTA_PASS_EXCLUDED_RUNGS]
    assert not set(prev) & set(OWNER_DATA_RUNGS), prev
    assert not set(quota) & set(OWNER_DATA_RUNGS), quota


def test_owner_data_classes_are_outside_the_fence():
    assert not set(OWNER_DATA_RUNGS) & dg.RECLAIMABLE_CLASSES


# --------------------------------------------------------------------------- #
# the real rungs on a real (temp) home, quota pass then critical fs pass
# --------------------------------------------------------------------------- #
def test_owner_data_survives_quota_then_critical_drain(tmp_path, monkeypatch):
    """The 18.11. shape: quota 96 % and the filesystem at 97 %. The home-scoped
    real rungs (the three former owner-data rungs, while they exist, plus both
    worktree rungs) run through the REAL executor; nothing of the owner's data
    may be deleted."""
    home, files = _owner_home(tmp_path)
    monkeypatch.setattr(dg, "_default_box_class", lambda: "shared-stream")
    keep = set(OWNER_DATA_RUNGS) | {"stale-agent-worktree", "worktree"}

    def planners(h, n):
        return [(lab, p) for lab, p in
                dg._default_planners(h, n, level="critical") if lab in keep]

    q = dgq.QuotaState(96, True, False, False, (9600, 10000), lambda: (9600, 10000))
    status = dg.disk_status(statvfs_fn=_sv(97), dev_fn=lambda _p: 1,
                            mounts=("/",), now=_LATER)
    logs = dgq.run_drain_passes(
        status, str(home), _LATER, False, planners(str(home), _LATER), planners, q,
        True, _sv(97), lambda _p: 1, ("/",), lambda: 1000, lambda: False)
    _assert_all_exist(files)
    assert not [ln for ln in logs if " DELETE " in ln and str(home) in ln], logs


# --------------------------------------------------------------------------- #
# the executor fence — a rogue rung at every level
# --------------------------------------------------------------------------- #
def _rogue(home, files):
    """A rung that tries to delete each protected path, and one ancestor."""
    def plan():
        return [
            {"cls": "user-cache", "path": str(files["upload"]), "bytes": 10,
             "kind": "delete", "reason": None},
            {"cls": "user-cache", "path": str(files["upload"].parent.parent),
             "bytes": 10, "kind": "delete", "reason": None},
            {"cls": "user-cache", "path": str(files["work-product"].parent),
             "bytes": 10, "kind": "delete", "reason": None},
            {"cls": "stale-build", "path": str(files["work-product-pack"]),
             "bytes": 10, "kind": "delete", "reason": None},
            {"cls": "claude-metadata", "path": str(home / ".claude"),
             "bytes": 10, "kind": "delete", "reason": None},
        ]
    return plan


@pytest.mark.parametrize("level,pressure,ladder,dry_run", [
    ("drain", None, "fs", False),
    ("critical", None, "fs", False),
    ("critical", "quota", "quota", False),
    ("warn", None, "prevention", False),
    ("critical", None, "fs", True),
])
def test_executor_refuses_owner_data_at_every_level(tmp_path, level, pressure,
                                                    ladder, dry_run):
    home, files = _owner_home(tmp_path)
    status = {"worst_pct": 97, "dim": "bytes", "level": level,
              "mounts": [{"mount": "/", "worst_pct": 97}]}
    log_path = str(tmp_path / "disk-guard.log")
    logs = dg.execute_drain(
        status, str(home), [("rogue", _rogue(home, files))], lambda: 97,
        dg._make_do_action(dry_run, now=_LATER), geteuid_fn=lambda: 1000,
        log_path=log_path, now=_LATER, dry_run=dry_run, target_pct=0,
        pressure=pressure, ladder=ladder)
    _assert_all_exist(files)
    refused = [ln for ln in logs if "REFUSE-PROTECTED" in ln]
    assert len(refused) == 5, logs
    assert not [ln for ln in logs if "DELETE" in ln and "REFUSE" not in ln], logs


def test_a_regenerable_path_beside_the_protected_one_still_goes(tmp_path):
    """The fence is a path rule, not a blanket stop: a cache file next to
    ``~/uploads`` is still deleted, and ``~/uploads-old`` is not ``~/uploads``."""
    home, files = _owner_home(tmp_path)
    cache = home / ".cache" / "old.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"c" * 100)
    lookalike = home / "uploads-old"
    lookalike.write_bytes(b"l")

    def plan():
        return [{"cls": "user-cache", "path": str(cache), "bytes": 100,
                 "kind": "delete", "reason": None},
                {"cls": "user-cache", "path": str(lookalike), "bytes": 1,
                 "kind": "delete", "reason": None}]
    dg.execute_drain(
        {"worst_pct": 97, "dim": "bytes", "level": "critical",
         "mounts": [{"mount": "/", "worst_pct": 97}]},
        str(home), [("user-cache", plan)], lambda: 97,
        dg._make_do_action(False, now=_LATER), geteuid_fn=lambda: 1000,
        log_path=str(tmp_path / "disk-guard.log"), now=_LATER, target_pct=0)
    assert not cache.exists() and not lookalike.exists()
    _assert_all_exist(files)


def test_protected_path_rule_covers_other_homes():
    """A rung acting on another account's home (the runner rungs do) can never
    reach that account's uploads or work-products either."""
    assert dg._protected_owner_path("/home/david3/uploads/worldline/a.pdf", "/home/x")
    assert dg._protected_owner_path("/home/david3/.claude/work-products/7598", "/home/x")
    assert dg._protected_owner_path("/home/david3", "/home/x")
    assert not dg._protected_owner_path("/home/gh-runner/_work/_temp/x", "/home/x")
    assert not dg._protected_owner_path("journalctl --user --vacuum-size=100M",
                                        os.path.expanduser("~"))
