"""Disk-pressure guard (watchdog Job 40, #834) — classifier + drain ladder +
fork-no-merge worktree reclaim criterion + uploads age-out + scope fence +
footer segment.

The guard AUTO-reclaims OUR own reclaimable classes on a per-USER basis when a
box crosses a disk-usage/inode threshold, fail-loud (every action AND skip
logged), and never deletes on uncertainty. These tests assert on PLANS and
skip-reasons and injected seams — never on a real deletion path.
"""

import os
import types

import watchdog.disk_guard as dg
import cli_worktree_sweep as wt
import statusbar


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def fake_statvfs(used_pct, inode_pct, frsize=4096, blocks=1_000_000, files=1_000_000):
    """An os.statvfs_result-like object yielding exactly `used_pct` bytes-used
    and `inode_pct` inodes-used under the df formula (no root-reserved slack)."""
    bfree = int(blocks * (100 - used_pct) / 100)
    ffree = int(files * (100 - inode_pct) / 100)
    return types.SimpleNamespace(
        f_frsize=frsize, f_bsize=frsize,
        f_blocks=blocks, f_bfree=bfree, f_bavail=bfree,
        f_files=files, f_ffree=ffree, f_favail=ffree,
    )


def statvfs_map(mapping):
    """A statvfs_fn that returns fake_statvfs(*mapping[path])."""
    def _sv(path):
        if path not in mapping:
            raise FileNotFoundError(path)
        return fake_statvfs(*mapping[path])
    return _sv


def dev_map(mapping):
    def _dev(path):
        if path not in mapping:
            raise FileNotFoundError(path)
        return mapping[path]
    return _dev


# --------------------------------------------------------------------------- #
# classifier
# --------------------------------------------------------------------------- #
def test_level_for_thresholds():
    assert dg.level_for(0) == "ok"
    assert dg.level_for(74) == "ok"
    assert dg.level_for(75) == "notice"
    assert dg.level_for(79) == "notice"
    assert dg.level_for(80) == "drain"
    assert dg.level_for(89) == "drain"
    assert dg.level_for(90) == "critical"
    assert dg.level_for(100) == "critical"


def test_disk_status_worst_mount_and_dim():
    # / at 82% bytes, /home at 60% bytes but 91% inodes → worst is /home inodes.
    sv = statvfs_map({"/": (82, 20), "/home": (60, 91), "/tmp": (50, 10)})
    dev = dev_map({"/": 1, "/home": 2, "/tmp": 3})
    st = dg.disk_status(statvfs_fn=sv, dev_fn=dev,
                        mounts=("/", "/home", "/tmp"), now=1000.0)
    assert st["worst_pct"] == 91
    assert st["dim"] == "inodes"
    assert st["level"] == "critical"
    assert st["ts"] == 1000.0
    # every distinct mount is represented
    assert {m["mount"] for m in st["mounts"]} == {"/", "/home", "/tmp"}


def test_disk_status_dedup_by_device():
    # /home shares the device of / (not a separate mount) → collapsed to one row.
    sv = statvfs_map({"/": (70, 10), "/home": (70, 10), "/tmp": (40, 5)})
    dev = dev_map({"/": 1, "/home": 1, "/tmp": 2})
    st = dg.disk_status(statvfs_fn=sv, dev_fn=dev,
                        mounts=("/", "/home", "/tmp"), now=1.0)
    devs = {m["mount"] for m in st["mounts"]}
    # exactly two distinct devices survive
    assert len(st["mounts"]) == 2
    assert "/tmp" in devs


def test_disk_status_skips_unstattable_mount():
    sv = statvfs_map({"/": (70, 10)})           # /home + /tmp absent
    dev = dev_map({"/": 1})
    st = dg.disk_status(statvfs_fn=sv, dev_fn=dev,
                        mounts=("/", "/home", "/tmp"), now=1.0)
    assert [m["mount"] for m in st["mounts"]] == ["/"]
    assert st["worst_pct"] == 70


# --------------------------------------------------------------------------- #
# scope fence
# --------------------------------------------------------------------------- #
def test_reclaimable_classes_is_the_fence():
    # The literal allowlist holds the own-home reclaimable classes (#834) plus
    # the bounded cache-class reclaims #854 added (see test_854_* below). The
    # bare/ambiguous root-level names stay OUT — the executor acts only on the
    # specific bounded classes, never a raw "docker"/"runner"/"var-log".
    fence = dg.RECLAIMABLE_CLASSES
    assert "worktree" in fence and "cli-version" in fence and "uploads" in fence
    assert "transcript" in fence and "toolchain" in fence and "scratch" in fence
    for forbidden in ("runner", "var-log", "other-home", "gh-runner", "docker"):
        assert forbidden not in fence


def test_execute_refuses_as_root(tmp_path):
    calls = []
    logs = dg.execute_drain(
        status={"worst_pct": 95, "dim": "bytes", "level": "critical",
                "mounts": [{"mount": "/", "worst_pct": 95}]},
        home=str(tmp_path),
        planners=[("worktree", lambda: (_ for _ in ()).throw(AssertionError("planner ran as root")))],
        recheck_fn=lambda: 95,
        do_action_fn=lambda a: calls.append(a) or 0,
        geteuid_fn=lambda: 0,
        log_path=str(tmp_path / "disk-guard.log"),
        now=1.0,
    )
    assert calls == []                       # nothing was acted on
    assert any("euid" in ln.lower() or "root" in ln.lower() for ln in logs)


def test_execute_never_touches_class_outside_fence(tmp_path):
    # A rogue planner emitting an out-of-fence class must be skipped+logged,
    # never acted on.
    acted = []

    def rogue():
        return [{"cls": "gh-runner", "path": "/home/gh-runner/_work",
                 "bytes": 9_000_000_000, "kind": "delete", "reason": None}]

    logs = dg.execute_drain(
        status={"worst_pct": 92, "dim": "bytes", "level": "critical",
                "mounts": [{"mount": "/", "worst_pct": 92}]},
        home=str(tmp_path),
        planners=[("gh-runner", rogue)],
        recheck_fn=lambda: 92,
        do_action_fn=lambda a: acted.append(a) or a["bytes"],
        geteuid_fn=lambda: 1000,
        log_path=str(tmp_path / "disk-guard.log"),
        now=1.0,
    )
    assert acted == []
    assert any("gh-runner" in ln and ("fence" in ln.lower() or "skip" in ln.lower())
               for ln in logs)


def test_execute_stops_when_back_under_target(tmp_path):
    # Rung 1 brings the worst mount under 75% → rung 2 must NOT run.
    ran = {"r1": False, "r2": False}

    def r1():
        ran["r1"] = True
        return [{"cls": "scratch", "path": str(tmp_path / "s"), "bytes": 10,
                 "kind": "delete", "reason": None}]

    def r2():
        ran["r2"] = True
        return []

    seq = iter([85, 70])          # before rung1: 85 ; after rung1: 70

    dg.execute_drain(
        status={"worst_pct": 85, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 85}]},
        home=str(tmp_path),
        planners=[("scratch", r1), ("cli-version", r2)],
        recheck_fn=lambda: next(seq),
        do_action_fn=lambda a: a["bytes"],
        geteuid_fn=lambda: 1000,
        log_path=str(tmp_path / "disk-guard.log"),
        now=1.0,
    )
    assert ran["r1"] is True
    assert ran["r2"] is False


def test_decision_log_records_action_and_skip(tmp_path):
    logp = tmp_path / "disk-guard.log"

    def planner():
        return [
            {"cls": "uploads", "path": "/h/uploads/old.mp4", "bytes": 500,
             "kind": "delete", "reason": None},
            {"cls": "uploads", "path": "/h/uploads/new.mp4", "bytes": 5,
             "kind": "skip", "reason": "too recent (2.0d < 14d)"},
        ]

    dg.execute_drain(
        status={"worst_pct": 82, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 82}]},
        home=str(tmp_path),
        planners=[("uploads", planner)],
        recheck_fn=lambda: 82,
        do_action_fn=lambda a: a["bytes"],
        geteuid_fn=lambda: 1000,
        log_path=str(logp),
        now=1.0,
    )
    text = logp.read_text()
    assert "old.mp4" in text and "new.mp4" in text
    assert "SKIP" in text and "too recent" in text
    # the acted line records the planned bytes
    assert "500" in text


def test_dry_run_action_lines_are_tagged_would(tmp_path):
    # #834 review 🟡: a dry-run must NOT write an unmarked DELETE line to the
    # audit log (it records a deletion that never happened).
    logp = tmp_path / "disk-guard.log"

    def planner():
        return [{"cls": "uploads", "path": "/h/uploads/old.mp4", "bytes": 500,
                 "kind": "delete", "reason": None}]

    dg.execute_drain(
        status={"worst_pct": 82, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 82}]},
        home=str(tmp_path),
        planners=[("uploads", planner)],
        recheck_fn=lambda: 82,
        do_action_fn=lambda a: a["bytes"],
        geteuid_fn=lambda: 1000,
        log_path=str(logp),
        now=1.0,
        dry_run=True,
    )
    text = logp.read_text()
    assert "WOULD-DELETE" in text
    assert "\nDELETE " not in text and not text.startswith("DELETE ")


# --------------------------------------------------------------------------- #
# uploads age-out planner
# --------------------------------------------------------------------------- #
def test_discover_stale_uploads(tmp_path):
    up = tmp_path / "uploads"
    up.mkdir()
    old = up / "old-call.mp4"
    old.write_bytes(b"x" * 100)
    new = up / "fresh.wav"
    new.write_bytes(b"y" * 100)
    now = 1_000_000_000.0
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))   # 30d old
    os.utime(new, (now - 2 * 86400, now - 2 * 86400))      # 2d old
    rows = dg.discover_stale_uploads(home=str(tmp_path), now=now, max_age_days=14)
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["old-call.mp4"]["reason"] is None            # reclaimable
    assert by["fresh.wav"]["reason"] is not None           # too recent
    assert by["old-call.mp4"]["cls"] == "uploads"


def test_discover_stale_uploads_no_dir(tmp_path):
    # No ~/uploads at all → simply nothing to do, not an error.
    assert dg.discover_stale_uploads(home=str(tmp_path), now=1.0) == []


# --------------------------------------------------------------------------- #
# shared-stream toolchain planner
# --------------------------------------------------------------------------- #
def test_toolchain_removed_on_shared_stream_box(tmp_path):
    (tmp_path / "Android").mkdir()
    (tmp_path / ".gradle").mkdir()
    rows = dg.discover_toolchain_dirs(
        home=str(tmp_path),
        box_class_fn=lambda: "shared-stream",
        pgrep_fn=lambda user_re: "",          # no live gradle/java process
    )
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["Android"]["kind"] == "delete"
    assert by[".gradle"]["kind"] == "delete"
    assert all(r["cls"] == "toolchain" for r in rows)


def test_toolchain_reported_not_deleted_on_workstation(tmp_path):
    (tmp_path / "Android").mkdir()
    rows = dg.discover_toolchain_dirs(
        home=str(tmp_path),
        box_class_fn=lambda: "workstation",
        pgrep_fn=lambda user_re: "",
    )
    assert rows and all(r["kind"] == "report" for r in rows)


def test_toolchain_skipped_when_build_process_live(tmp_path):
    (tmp_path / "Android").mkdir()
    rows = dg.discover_toolchain_dirs(
        home=str(tmp_path),
        box_class_fn=lambda: "shared-stream",
        pgrep_fn=lambda user_re: "12345 java org.gradle...",   # live build
    )
    assert rows and all(r["kind"] == "skip" for r in rows)
    assert any("live" in (r.get("reason") or "").lower() for r in rows)


# --------------------------------------------------------------------------- #
# fork-no-merge worktree reclaim criterion — the highest-value, highest-risk rung
# --------------------------------------------------------------------------- #
def _git_run_factory(responses):
    """responses: dict keyed on a tuple prefix of the git args → return value
    (a string for rc0, None for rc!=0). Longest matching prefix wins."""
    def _run(args, cwd, timeout=15):
        args = tuple(args)
        best = None
        for key, val in responses.items():
            if args[:len(key)] == key and (best is None or len(key) > len(best[0])):
                best = (key, val)
        return best[1] if best else None
    return _run


def test_worktree_reclaimable_when_head_reachable_from_origin(tmp_path):
    # HEAD is preserved on origin via the refs/autopilot-wip/<branch> backup —
    # proven ONLY through its origin SHA (ls-remote), NOT the local ref name
    # (#834 review: a local-only wip ref whose push failed is not origin proof).
    # → the DIRECTORY is disk we can free; the branch ref is kept.
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc123\n",
        ("status", "--porcelain"): "",           # clean
        ("ls-remote", "origin", "refs/autopilot-wip/david/x"): "abc123\trefs/autopilot-wip/david/x\n",
        # HEAD is its own ancestor → reachable from the resolved origin wip SHA
        ("merge-base", "--is-ancestor", "abc123", "abc123"): "",
        # origin/main and origin/david/x are NOT ancestors here (no response → None)
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/x", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,   # 5d idle
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is None                 # reclaimable
    assert row["kind"] == "worktree"
    assert "autopilot-wip" in (row.get("reachable_via") or "")


def test_worktree_NOT_reclaimable_via_local_only_wip_ref(tmp_path):
    # A wip backup whose push to origin FAILED leaves a LOCAL ref only; it must
    # NOT read as reachable-from-origin (#834 review 🟡). ls-remote finds nothing
    # on origin → not reclaimable, even though a local ref name would match.
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc123\n",
        ("status", "--porcelain"): "",
        # a LOCAL-only wip ref would match this, but the code never tries it:
        ("merge-base", "--is-ancestor", "abc123", "refs/autopilot-wip/david/x"): "",
        ("ls-remote", "origin", "refs/autopilot-wip/david/x"): "",   # not on origin
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/x", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "origin" in row["reason"].lower() or "reachable" in row["reason"].lower()


def test_worktree_NOT_reclaimable_when_locked(tmp_path):
    # A locked worktree is a live session's (#348) — never removed (review 🔴).
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc\n",
        ("merge-base", "--is-ancestor", "abc", "origin/main"): "",
        ("status", "--porcelain"): "",
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/z", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400, locked=True,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "lock" in row["reason"].lower()


def test_worktree_NOT_reclaimable_when_head_unreachable(tmp_path):
    # No origin ref has HEAD as an ancestor → real, unbacked work → NEVER touched.
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "deadbeef\n",
        ("status", "--porcelain"): "",
        ("ls-remote", "origin", "refs/autopilot-wip/david/y"): "",   # no backup
        # every merge-base --is-ancestor returns None (not an ancestor)
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/y", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "origin" in row["reason"].lower() or "reachable" in row["reason"].lower()


def test_worktree_NOT_reclaimable_when_dirty(tmp_path):
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc\n",
        ("merge-base", "--is-ancestor", "abc", "origin/main"): "",   # reachable...
        ("status", "--porcelain"): " M file.py\n",                    # ...but dirty
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/z", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "dirty" in row["reason"].lower() or "clean" in row["reason"].lower()


def test_worktree_NOT_reclaimable_when_recent(tmp_path):
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc\n",
        ("merge-base", "--is-ancestor", "abc", "origin/main"): "",
        ("status", "--porcelain"): "",
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/z", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 600,           # 10 min idle → too recent
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "recent" in row["reason"].lower() or "24" in row["reason"]


def test_worktree_NOT_reclaimable_with_precious_ignored_file(tmp_path):
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc\n",
        ("merge-base", "--is-ancestor", "abc", "origin/main"): "",
        ("status", "--porcelain"): "",
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/z", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: True,                 # a .env / *.key present
    )
    assert row["reason"] is not None
    assert "precious" in row["reason"].lower() or "ignored" in row["reason"].lower()


def test_worktree_NOT_reclaimable_when_live_cwd(tmp_path):
    p = tmp_path / "wt"
    p.mkdir()
    gr = _git_run_factory({
        ("rev-parse", "HEAD"): "abc\n",
        ("merge-base", "--is-ancestor", "abc", "origin/main"): "",
        ("status", "--porcelain"): "",
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch="david/z", base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: True,                 # a live process cwd inside
        recency_fn=lambda _r, _p, _n: 5 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is not None
    assert "live" in row["reason"].lower()


def test_worktree_orphan_gitdir_is_reclaimable(tmp_path):
    # A #537 rename-litter dir: .git points nowhere → git can't read it at all.
    p = tmp_path / "agent-orphan"
    p.mkdir()
    (p / ".git").write_text("gitdir: /home/david/gone/.git/worktrees/agent-orphan\n")
    gr = _git_run_factory({
        # rev-parse HEAD fails (None) — no readable git metadata
        ("status", "--porcelain"): None,     # git errors
    })
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch=None, base="main",
        git_run=gr, now=1_000_000_000.0, min_idle_s=86400,
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 30 * 86400,
        precious_fn=lambda _p: False,
    )
    assert row["reason"] is None
    assert row["kind"] == "orphan-gitdir"


def test_worktree_orphan_gitdir_reclaimable_with_REAL_default_precious_fn(tmp_path):
    # #834 review 🟡: the orphan branch must run BEFORE the precious check AND the
    # real default precious-check must not fail-True on an orphan (git is
    # unreadable → fs-walk fallback). No precious file present → reclaimed.
    p = tmp_path / "agent-orphan"
    p.mkdir()
    (p / ".git").write_text("gitdir: /home/david/gone/.git/worktrees/agent-orphan\n")
    (p / "harmless.txt").write_text("x")
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch=None, base="main",
        git_run=None, now=1_000_000_000.0, min_idle_s=86400,   # REAL git + precious
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 30 * 86400,
    )
    assert row["reason"] is None
    assert row["kind"] == "orphan-gitdir"


def test_worktree_orphan_gitdir_kept_when_precious_file_present(tmp_path):
    # An orphan holding a .env must be KEPT even though its git state is
    # unreadable (the fs-walk precious fallback finds it; #834 review 🟡).
    p = tmp_path / "agent-orphan2"
    p.mkdir()
    (p / ".git").write_text("gitdir: /home/david/gone/.git/worktrees/agent-orphan2\n")
    (p / ".env").write_text("SECRET=1")
    row = wt._worktree_reclaimable(
        root=str(tmp_path), path=str(p), branch=None, base="main",
        git_run=None, now=1_000_000_000.0, min_idle_s=86400,   # REAL git + precious
        in_live_use=lambda _p: False,
        recency_fn=lambda _r, _p, _n: 30 * 86400,
    )
    assert row["reason"] is not None
    assert "precious" in row["reason"].lower()


# --------------------------------------------------------------------------- #
# footer segment
# --------------------------------------------------------------------------- #
def _write_disk_cache(home, worst_pct, ts):
    import json
    d = home / ".claude" / "disk-guard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(
        {"worst_pct": worst_pct, "dim": "bytes", "level": dg.level_for(worst_pct),
         "ts": ts, "mounts": [{"mount": "/", "worst_pct": worst_pct}]}))


def test_disk_segment_hidden_below_notice(tmp_path):
    _write_disk_cache(tmp_path, 60, 1000.0)
    assert statusbar.disk_segment(home=str(tmp_path), now=1000.0) == ""


def test_disk_segment_hidden_in_drain_band(tmp_path):
    # #854: the footer is narrowed to show ONLY at >= 90 % (red) — a 80-95 %
    # "drain"-band reading is HIDDEN so the footer isn't cluttered while gk
    # oscillates 80-90 %. This overturns the #834 "yellow 75-89 %" behavior.
    _write_disk_cache(tmp_path, 82, 1000.0)
    assert statusbar.disk_segment(home=str(tmp_path), now=1000.0) == ""
    _write_disk_cache(tmp_path, 89, 1000.0)
    assert statusbar.disk_segment(home=str(tmp_path), now=1000.0) == ""


def test_disk_segment_shown_red_at_critical(tmp_path):
    # #854: shown ONLY at >= 90 %, and RED (colour 196), never yellow.
    _write_disk_cache(tmp_path, 95, 1000.0)
    seg = statusbar.disk_segment(home=str(tmp_path), now=1000.0)
    assert "95%" in seg and "disk" in seg
    assert "38;5;196m" in seg          # red, not yellow (214)
    assert "214m" not in seg
    # exactly at the 90 % boundary it is shown
    _write_disk_cache(tmp_path, 90, 1000.0)
    assert "90%" in statusbar.disk_segment(home=str(tmp_path), now=1000.0)


def test_disk_segment_hidden_when_cache_stale(tmp_path):
    # A dead watchdog must not paint a frozen % forever.
    _write_disk_cache(tmp_path, 95, 1000.0)
    assert statusbar.disk_segment(home=str(tmp_path), now=1000.0 + 3600) == ""


def test_disk_segment_absent_cache(tmp_path):
    assert statusbar.disk_segment(home=str(tmp_path), now=1.0) == ""


# --------------------------------------------------------------------------- #
# #854 — severity beats cadence
# --------------------------------------------------------------------------- #
def test_cadence_allows_drain_severity_beats_cadence():
    # >= DISK_CRITICAL_PCT: drain EVERY poll even when the cadence gate says
    # "not due"; below it, honour the cadence gate; dry-run always proceeds.
    assert dg._cadence_allows_drain(dg.DISK_CRITICAL_PCT, due=False, dry_run=False) is True
    assert dg._cadence_allows_drain(97, due=False, dry_run=False) is True
    assert dg._cadence_allows_drain(85, due=False, dry_run=False) is False
    assert dg._cadence_allows_drain(85, due=True, dry_run=False) is True
    assert dg._cadence_allows_drain(50, due=False, dry_run=True) is True


def _seed_last_drain(home, when):
    d = home / ".claude" / "disk-guard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "last-drain").write_text("%f" % when)


def test_run_disk_guard_critical_bypasses_cadence(tmp_path):
    # 96 % with a last-drain 5 s ago (cadence NOT due) → the drain STILL runs,
    # never a "cadence-gated" line. A no-op planner keeps the test safe (no real
    # deletion, no real system path).
    _seed_last_drain(tmp_path, 1000.0)
    ran = {"drained": False}

    def _noop(_home, _now):
        def _p():
            ran["drained"] = True
            return []
        return [("noop", _p)]

    logs = dg.run_disk_guard(
        now=1005.0, home=str(tmp_path), dry_run=False,
        statvfs_fn=statvfs_map({"/": (96, 20)}), dev_fn=dev_map({"/": 1}),
        geteuid_fn=lambda: 1000, mounts=("/",), planners_fn=_noop)
    assert not any("cadence-gated" in ln for ln in logs)
    assert ran["drained"] is True


def test_run_disk_guard_drain_band_stays_cadence_gated(tmp_path):
    # 85 % with a recent last-drain → cadence-gated, the ladder does NOT run.
    _seed_last_drain(tmp_path, 1000.0)
    ran = {"drained": False}

    def _noop(_home, _now):
        def _p():
            ran["drained"] = True
            return []
        return [("noop", _p)]

    logs = dg.run_disk_guard(
        now=1005.0, home=str(tmp_path), dry_run=False,
        statvfs_fn=statvfs_map({"/": (85, 20)}), dev_fn=dev_map({"/": 1}),
        geteuid_fn=lambda: 1000, mounts=("/",), planners_fn=_noop)
    assert any("cadence-gated" in ln for ln in logs)
    assert ran["drained"] is False


def test_execute_logs_per_rung_freed_summary(tmp_path):
    # #854: every drain logs `disk-guard: NN% → drain rung=<name> freed=<b> → MM%`.
    logp = tmp_path / "disk-guard.log"

    def planner():
        return [{"cls": "user-cache", "path": str(tmp_path / "c"), "bytes": 2048,
                 "kind": "delete", "reason": None}]

    seq = iter([97, 88])           # before rung: 97 ; after (next start): 88
    dg.execute_drain(
        status={"worst_pct": 97, "dim": "bytes", "level": "critical",
                "mounts": [{"mount": "/", "worst_pct": 97}]},
        home=str(tmp_path),
        planners=[("user-cache", planner), ("noop", lambda: [])],
        recheck_fn=lambda: next(seq),
        do_action_fn=lambda a: a["bytes"],
        geteuid_fn=lambda: 1000, log_path=str(logp), now=1.0)
    text = logp.read_text()
    assert "drain rung=user-cache" in text
    assert "97%" in text and "88%" in text and "freed=" in text


def test_execute_summary_for_last_acting_rung_uses_after_loop_recheck(tmp_path):
    # #854 review 🔵: the LAST acting rung's summary is emitted after the loop
    # via one final recheck_fn() — exercise that path (acting rung is last).
    logp = tmp_path / "disk-guard.log"
    seq = iter([90, 84])           # rung start=90 (>=75, run) ; after-loop recheck=84
    dg.execute_drain(
        status={"worst_pct": 90, "dim": "bytes", "level": "critical",
                "mounts": [{"mount": "/", "worst_pct": 90}]},
        home=str(tmp_path),
        planners=[("apt-cache", lambda: [{"cls": "apt-cache", "path": "apt-get clean",
                                          "bytes": 1024, "kind": "apt-clean", "reason": None}])],
        recheck_fn=lambda: next(seq),
        do_action_fn=lambda a: a["bytes"],
        geteuid_fn=lambda: 1000, log_path=str(logp), now=1.0)
    text = logp.read_text()
    assert "drain rung=apt-cache" in text and "84%" in text


# --------------------------------------------------------------------------- #
# #854 — the fence now INCLUDES the bounded cache-class reclaims
# --------------------------------------------------------------------------- #
def test_854_cache_classes_are_in_the_fence():
    fence = dg.RECLAIMABLE_CLASSES
    for c in ("apt-cache", "rotated-log", "runner-update", "docker-image",
              "runner-checkout", "user-cache", "claude-version", "oneoff-venv"):
        assert c in fence, c
    # the bare/ambiguous root-level class names are still NOT in the fence — the
    # executor acts only on the specific bounded classes above.
    for forbidden in ("runner", "var-log", "other-home", "gh-runner", "docker"):
        assert forbidden not in fence


# --------------------------------------------------------------------------- #
# #854 — new cache-class planners (pure selection on fake trees / fake seams)
# --------------------------------------------------------------------------- #
def test_apt_cache_selects_when_nonempty(tmp_path):
    arch = tmp_path / "archives"
    arch.mkdir()
    (arch / "pkg.deb").write_bytes(b"x" * 4096)
    rows = dg.discover_apt_cache(archives_dir=str(arch))
    assert len(rows) == 1
    assert rows[0]["kind"] == "apt-clean" and rows[0]["cls"] == "apt-cache"
    assert rows[0]["bytes"] > 0
    # no dir / empty → nothing
    assert dg.discover_apt_cache(archives_dir=str(tmp_path / "absent")) == []


def test_rotated_logs_select_rotations_never_live(tmp_path):
    vl = tmp_path / "var-log"
    vl.mkdir()
    now = 1_000_000_000.0
    for name in ("btmp.1", "syslog.1", "auth.log.2.gz"):
        p = vl / name
        p.write_bytes(b"x" * 100)
        os.utime(p, (now - 5 * 86400, now - 5 * 86400))       # 5d old
    live = vl / "syslog"           # LIVE log — never a rotation
    live.write_bytes(b"y" * 100)
    fresh = vl / "messages.1"      # rotation but too recent
    fresh.write_bytes(b"z" * 100)
    os.utime(fresh, (now - 3600, now - 3600))
    (vl / "journal").mkdir()       # journald owns this — skipped
    rows = dg.discover_rotated_logs(varlog_dir=str(vl), now=now)
    by = {os.path.basename(r["path"]): r for r in rows}
    assert set(by) == {"btmp.1", "syslog.1", "auth.log.2.gz", "messages.1"}
    assert by["btmp.1"]["reason"] is None
    assert by["auth.log.2.gz"]["reason"] is None
    assert by["messages.1"]["reason"] is not None        # too recent → kept
    assert "syslog" not in by                            # live log never selected


def test_runner_update_selects_update_and_temp(tmp_path):
    root = tmp_path / "gh-runner"
    upd = root / "actions-runner-1" / "_work" / "_update"
    tmp = root / "actions-runner-1" / "_work" / "_temp"
    upd.mkdir(parents=True)
    tmp.mkdir(parents=True)
    (upd / "f").write_bytes(b"x" * 50)
    # no live Runner.Worker → both _update and _temp reclaimable
    rows = dg.discover_runner_update(runner_root=str(root), pgrep_fn=lambda _re: "")
    kinds = {os.path.basename(r["path"]): r["kind"] for r in rows if r.get("kind")}
    assert kinds.get("_update") == "delete" and kinds.get("_temp") == "delete"
    assert dg.discover_runner_update(runner_root=str(tmp_path / "absent")) == []


def test_runner_update_temp_gated_on_runner_worker(tmp_path):
    # #854 review 🟡: _temp is the LIVE job's $RUNNER_TEMP — kept while a worker
    # runs; _update (self-update staging) stays reclaimable.
    root = tmp_path / "gh-runner"
    (root / "actions-runner-1" / "_work" / "_update").mkdir(parents=True)
    (root / "actions-runner-1" / "_work" / "_temp").mkdir(parents=True)
    rows = dg.discover_runner_update(
        runner_root=str(root), pgrep_fn=lambda _re: "42 Runner.Worker")
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["_update"]["kind"] == "delete"
    assert by["_temp"]["kind"] == "skip"
    assert "live" in (by["_temp"].get("reason") or "").lower()


def test_runner_checkouts_gated_on_runner_worker(tmp_path):
    root = tmp_path / "gh-runner"
    work = root / "actions-runner-1" / "_work"
    (work / "odoo-erp").mkdir(parents=True)
    (work / "_actions").mkdir(parents=True)        # reserved, never a checkout
    now = 1_000_000_000.0
    os.utime(work / "odoo-erp", (now - 10 * 86400, now - 10 * 86400))
    # a live Runner.Worker → the whole rung skips
    rows_live = dg.discover_stale_runner_checkouts(
        runner_root=str(root), now=now, pgrep_fn=lambda _re: "999 Runner.Worker")
    assert rows_live and all(r["kind"] == "skip" for r in rows_live)
    # no live worker → the stale checkout is selected; reserved dir untouched
    rows = dg.discover_stale_runner_checkouts(
        runner_root=str(root), now=now, pgrep_fn=lambda _re: "")
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["odoo-erp"]["reason"] is None
    assert "_actions" not in by


def test_docker_selects_untagged_and_old_never_inuse(tmp_path):
    now = 1_000_000_000.0
    images = [
        {"id": "aaa", "repo": "<none>", "tag": "<none>", "size": 3_000_000_000,
         "created_ts": now - 60 * 86400},                       # untagged → rmi
        {"id": "bbb", "repo": "postgres", "tag": "16", "size": 600_000_000,
         "created_ts": now - 2 * 86400},                        # tagged + recent → keep
        {"id": "ccc", "repo": "old", "tag": "v1", "size": 400_000_000,
         "created_ts": now - 30 * 86400},                       # tagged but >14d → rmi
        {"id": "ddd", "repo": "used", "tag": "now", "size": 100_000_000,
         "created_ts": now - 40 * 86400},                       # in-use → keep
    ]
    rows = dg.discover_docker_images(
        now=now, images_fn=lambda: images, ps_fn=lambda: {"used:now"},
        pgrep_fn=lambda _re: "")
    by = {r["path"]: r for r in rows}
    assert by["aaa"]["kind"] == "docker-rmi"
    assert by["bbb"]["kind"] == "skip"
    assert by["ccc"]["kind"] == "docker-rmi"
    assert by["ddd"]["kind"] == "skip"          # referenced by a container


def test_docker_in_use_by_bare_and_sha256_id_is_kept(tmp_path):
    # #854 review 🟡/🔵: an untagged image referenced by a container BY ID (bare
    # 12-hex or sha256:-prefixed) must read as in-use → kept, never docker-rmi.
    now = 1_000_000_000.0
    images = [
        {"id": "sha256:abcdef012345aaaa", "repo": "<none>", "tag": "<none>",
         "size": 500_000_000, "created_ts": now - 60 * 86400},
        {"id": "sha256:99998888ffff0000", "repo": "<none>", "tag": "<none>",
         "size": 500_000_000, "created_ts": now - 60 * 86400},
    ]
    # ps shows a BARE 12-hex short id for the first and a full sha256: for the 2nd
    rows = dg.discover_docker_images(
        now=now, images_fn=lambda: images,
        ps_fn=lambda: {"abcdef012345", "sha256:99998888ffff0000"},
        pgrep_fn=lambda _re: "")
    by = {r["path"]: r for r in rows}
    assert by["sha256:abcdef012345aaaa"]["kind"] == "skip"
    assert by["sha256:99998888ffff0000"]["kind"] == "skip"
    # a live Runner.Worker skips the whole rung
    rows_live = dg.discover_docker_images(
        now=now, images_fn=lambda: images, ps_fn=lambda: set(),
        pgrep_fn=lambda _re: "1 Runner.Worker")
    assert rows_live and all(r["kind"] == "skip" for r in rows_live)


def test_user_cache_selects_stale_only(tmp_path):
    cache = tmp_path / ".cache"
    (cache / "pip").mkdir(parents=True)
    (cache / "fresh").mkdir(parents=True)
    now = 1_000_000_000.0
    os.utime(cache / "pip", (now - 60 * 86400, now - 60 * 86400))
    os.utime(cache / "fresh", (now - 5 * 86400, now - 5 * 86400))
    rows = dg.discover_stale_user_cache(home=str(tmp_path), now=now)
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["pip"]["reason"] is None
    assert by["fresh"]["reason"] is not None


def test_claude_versions_keep_running_delete_rest_failsafe(tmp_path):
    vdir = tmp_path / ".local" / "share" / "claude" / "versions"
    for v in ("2.1.252", "2.1.257", "2.1.258"):
        (vdir / v).mkdir(parents=True)
    rows = dg.discover_stale_claude_versions(
        home=str(tmp_path), running_fn=lambda: "2.1.258")
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["2.1.258"]["kind"] == "skip"          # running → kept
    assert by["2.1.252"]["kind"] == "delete"
    assert by["2.1.257"]["kind"] == "delete"
    # FAIL-SAFE: running unknown → keep everything
    rows_fs = dg.discover_stale_claude_versions(
        home=str(tmp_path), running_fn=lambda: None)
    assert rows_fs and all(r["kind"] == "skip" for r in rows_fs)


def test_root_owned_rung_deletes_via_sudo_when_available():
    # #854: a ROOT-owned cache class (apt-cache / rotated-log / runner-*) deletes
    # via `sudo -n` when NOPASSWD sudo is present. Assert the executor prefixes it.
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    # apt-clean → `sudo -n apt-get clean`
    dg._perform_action({"cls": "apt-cache", "kind": "apt-clean", "path": "apt-get clean",
                        "bytes": 100}, sudo_ok=True, run_fn=fake_run)
    assert calls[-1] == ["sudo", "-n", "apt-get", "clean"]


def test_root_owned_delete_via_sudo_rm(tmp_path):
    # a rotated-log delete of a root-owned file → `sudo -n rm -rf --one-file-system`
    f = tmp_path / "btmp.1"
    f.write_bytes(b"x" * 10)
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    dg._perform_action({"cls": "rotated-log", "kind": "delete", "path": str(f),
                        "bytes": 10}, sudo_ok=True, run_fn=fake_run)
    assert calls[-1][:4] == ["sudo", "-n", "rm", "-rf"]
    assert str(f) in calls[-1]


def test_no_sudo_prefix_when_unavailable_or_own_home(tmp_path):
    # sudo_ok False → NO prefix (unprivileged fall-back). And an OWN-HOME class
    # (user-cache) never gets sudo even when sudo_ok is True.
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    # root class, sudo NOT available → bare apt-get clean
    dg._perform_action({"cls": "apt-cache", "kind": "apt-clean", "path": "apt-get clean",
                        "bytes": 1}, sudo_ok=False, run_fn=fake_run)
    assert calls[-1] == ["apt-get", "clean"]
    # own-home class, sudo AVAILABLE → still no sudo (docker/own-home never sudo)
    d = tmp_path / "cachedir"
    d.mkdir()
    dg._perform_action({"cls": "user-cache", "kind": "delete", "path": str(d),
                        "bytes": 1}, sudo_ok=True, run_fn=fake_run)
    assert calls[-1][0] != "sudo"          # a bare `rm`, no sudo prefix


def test_sudo_available_probe_failsafe():
    # `sudo -n true` succeeds → True; failure/exception → False (fall back, never raise).
    assert dg._sudo_available(probe_fn=lambda: True) is True
    assert dg._sudo_available(probe_fn=lambda: False) is False
    def _boom():
        raise OSError("no sudo")
    assert dg._sudo_available(probe_fn=_boom) is False


def test_norm_action_kept_row_executes_as_skip_never_deleted(tmp_path):
    # #854 review 🟡: the LOAD-BEARING data-loss guard — a `reason`-set (KEPT)
    # #854 discovery row must execute as a SKIP through _norm_action → the ladder,
    # never a delete. Drive it end-to-end and assert do_action sees only the
    # reason=None candidate.
    logp = tmp_path / "disk-guard.log"
    acted = []

    def planner():
        return [
            dg._norm_action("user-cache", {"path": "/h/.cache/pip", "bytes": 900,
                                           "reason": None}, "delete"),
            dg._norm_action("user-cache", {"path": "/h/.cache/fresh", "bytes": 5,
                                           "reason": "too recent (2.0d < 30d)"}, "delete"),
            dg._norm_action("rotated-log", {"path": "/var/log/syslog.1", "bytes": 400,
                                            "reason": "too recent (0.5d < 1d)"}, "delete"),
        ]

    dg.execute_drain(
        status={"worst_pct": 96, "dim": "bytes", "level": "critical",
                "mounts": [{"mount": "/", "worst_pct": 96}]},
        home=str(tmp_path),
        planners=[("user-cache", planner)],
        recheck_fn=lambda: 96,
        do_action_fn=lambda a: acted.append(a["path"]) or a["bytes"],
        geteuid_fn=lambda: 1000, log_path=str(logp), now=1.0)
    # ONLY the reason=None candidate was acted on; both KEPT rows stayed SKIP.
    assert acted == ["/h/.cache/pip"]
    text = logp.read_text()
    assert "SKIP /h/.cache/fresh" in text and "SKIP /var/log/syslog.1" in text


def test_claude_versions_failsafe_when_running_matches_no_dir(tmp_path):
    # #854 review 🔵: a non-None running version that matches NO version dir (and
    # no symlink) must KEEP EVERYTHING, never delete the unidentifiable active one.
    vdir = tmp_path / ".local" / "share" / "claude" / "versions"
    for v in ("2.1.252", "2.1.257"):
        (vdir / v).mkdir(parents=True)
    rows = dg.discover_stale_claude_versions(
        home=str(tmp_path), running_fn=lambda: "9.9.9")   # matches nothing
    assert rows and all(r["kind"] == "skip" for r in rows)


def test_oneoff_venvs_select_numbered_never_stable(tmp_path):
    venvs = tmp_path / ".venvs"
    venvs.mkdir()
    now = 1_000_000_000.0
    for name in ("lint-3907", "ruff31522", "mcp-4574"):
        d = venvs / name
        d.mkdir()
        os.utime(d, (now - 10 * 86400, now - 10 * 86400))
    stable = venvs / "airuleset-lint"       # stable name → never matched
    stable.mkdir()
    os.utime(stable, (now - 10 * 86400, now - 10 * 86400))
    fresh = venvs / "lint-99"               # numbered but too recent → kept
    fresh.mkdir()
    os.utime(fresh, (now - 3600, now - 3600))
    tmpd = tmp_path / "tmp"
    tmpd.mkdir()
    (tmpd / "lintvenv-r5").mkdir()
    os.utime(tmpd / "lintvenv-r5", (now - 10 * 86400, now - 10 * 86400))
    rows = dg.discover_oneoff_venvs(home=str(tmp_path), now=now, tmp_dir=str(tmpd))
    by = {os.path.basename(r["path"]): r for r in rows}
    assert set(by) == {"lint-3907", "ruff31522", "mcp-4574", "lint-99", "lintvenv-r5"}
    assert "airuleset-lint" not in by
    assert by["lint-3907"]["reason"] is None
    assert by["lint-99"]["reason"] is not None      # too recent → kept


# --------------------------------------------------------------------------- #
# #862 — runner-superseded: reclaim `bin.<ver>`/`externals.<ver>` version dirs
# left behind by a gh-runner self-update, never the symlink target nor the
# version a live Runner.Listener is executing; skipped while a Runner.Worker
# of that root is live.
# --------------------------------------------------------------------------- #
def _mk_runner_root(root, cur="2.337.0", old="2.336.0"):
    """A fixture gh-runner install: bin/externals symlinks -> the `cur` version,
    plus a superseded `old` version dir for each. Returns the root Path."""
    rd = root / "actions-runner"
    rd.mkdir(parents=True)
    vers = [cur] + ([old] if old else [])
    for name in ("bin", "externals"):
        for ver in vers:
            d = rd / ("%s.%s" % (name, ver))
            d.mkdir()
            (d / "f").write_bytes(b"x" * 100)
        (rd / name).symlink_to(rd / ("%s.%s" % (name, cur)))
    (rd / "_work").mkdir()
    return rd


def test_runner_superseded_selects_old_versions_never_symlink_target(tmp_path):
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)
    # a live Listener executing the CURRENT version (resolved via /proc/exe);
    # no Runner.Worker -> the rung runs
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [listener])
    by = {os.path.basename(r["path"]): r for r in rows}
    # the superseded 2.336.0 dirs are selected for delete
    assert by["bin.2.336.0"]["kind"] == "delete" and by["bin.2.336.0"]["reason"] is None
    assert by["externals.2.336.0"]["kind"] == "delete"
    # the CURRENT (symlink target = live Listener) versions are kept
    assert by["bin.2.337.0"]["kind"] == "skip"
    assert by["externals.2.337.0"]["kind"] == "skip"
    # the bare `bin`/`externals` symlinks are never candidates
    assert "bin" not in by and "externals" not in by
    # absent root -> nothing to do
    assert dg.discover_superseded_runner_versions(
        runner_root=str(tmp_path / "absent"), proc_exes_fn=lambda: []) == []


def test_runner_superseded_keeps_staged_newer_version(tmp_path):
    # 🔴1 the REAL self-update window (the "symlink-new / listener-old" window the
    # old test modelled never actually occurs — update.sh swaps the symlink only
    # AFTER the Listener exits, then relaunches on the new symlink). What DOES
    # happen: the SelfUpdater unpacks bin.2.338.0 / externals.2.338.0 while
    # bin/externals still point at 2.337.0 and no Worker runs. Those STAGED newer
    # dirs (>= current) are what update.sh is about to switch to → keep them.
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root, cur="2.337.0", old=None)      # only the current pair
    for name in ("bin", "externals"):
        d = rd / ("%s.2.338.0" % name)
        d.mkdir()
        (d / "f").write_bytes(b"x" * 100)
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [listener])
    assert rows and all(r["kind"] == "skip" for r in rows)
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["bin.2.338.0"]["kind"] == "skip"
    assert "staged" in (by["bin.2.338.0"]["reason"] or "").lower()


def test_runner_superseded_failsafe_keep_when_unresolved(tmp_path):
    # (a) `bin` symlink missing -> cannot prove the current version -> KEEP all
    root_a = tmp_path / "a"
    rd_a = _mk_runner_root(root_a)
    (rd_a / "bin").unlink()
    rows_a = dg.discover_superseded_runner_versions(
        runner_root=str(root_a), proc_exes_fn=lambda: [])
    assert rows_a and all(r["kind"] == "skip" for r in rows_a)

    # (b) a live Listener of this root whose exe path carries NO resolvable
    # version (launched via the `bin` symlink) -> KEEP all
    root_b = tmp_path / "b"
    rd_b = _mk_runner_root(root_b)
    listener = str(rd_b / "bin" / "Runner.Listener")   # symlink path, no version
    rows_b = dg.discover_superseded_runner_versions(
        runner_root=str(root_b), proc_exes_fn=lambda: [listener])
    assert rows_b and all(r["kind"] == "skip" for r in rows_b)

    # (c) proc scan failed (sentinel) -> KEEP all
    root_c = tmp_path / "c"
    _mk_runner_root(root_c)
    rows_c = dg.discover_superseded_runner_versions(
        runner_root=str(root_c), proc_exes_fn=lambda: ["PROC-ERROR"])
    assert rows_c and all(r["kind"] == "skip" for r in rows_c)


def test_runner_superseded_gated_on_runner_worker(tmp_path):
    # a live Runner.Worker of this root -> the whole root skips (a self-update
    # may be in flight through _work/_update; never race it).
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)
    worker = str(rd / "bin.2.337.0" / "Runner.Worker")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [worker])
    assert rows and all(r["kind"] == "skip" for r in rows)
    assert any("worker" in (r.get("reason") or "").lower() for r in rows)


def test_runner_superseded_scopes_worker_to_its_own_root(tmp_path):
    # a Worker of `actions-runner-2` must NOT gate `actions-runner` (prefix
    # collision guard: match on the trailing-slash path boundary).
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)                       # tmp/gh-runner/actions-runner
    rd2 = root / "actions-runner-2"
    rd2.mkdir()
    other_worker = str(rd2 / "bin.2.337.0" / "Runner.Worker")
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [other_worker, listener])
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["bin.2.336.0"]["kind"] == "delete"    # not gated by the sibling's worker


def test_runner_superseded_in_the_fence_and_sudo_class():
    assert "runner-superseded" in dg.RECLAIMABLE_CLASSES
    assert "runner-superseded" in dg.SUDO_CLASSES     # gh-runner home is a foreign user


def test_runner_superseded_skips_root_during_self_update(tmp_path):
    # 🔴1 a `_work/_update` staging dir (or a live update.sh scoped to this root)
    # means a self-update is mid-flight → skip the WHOLE root, never race the swap.
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)                               # cur 2.337.0 + old 2.336.0
    (rd / "_work" / "_update").mkdir()
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [listener])
    assert rows and all(r["kind"] == "skip" for r in rows)
    assert any("self-update" in (r.get("reason") or "").lower() for r in rows)
    # 🔵10 the whole-root skip carries the summed candidate bytes (not a bare 0)
    assert any(r["bytes"] > 0 for r in rows)

    # a live update.sh scoped under a DIFFERENT root (no _work/_update marker) also skips
    rootb = _mk_runner_root(tmp_path / "b")
    line = "12345 /bin/bash %s/bin.2.337.0/update.sh" % str(rootb)
    rows_b = dg.discover_superseded_runner_versions(
        runner_root=str(tmp_path / "b"),
        proc_exes_fn=lambda: [str(rootb / "bin.2.337.0" / "Runner.Listener")],
        update_argv_fn=lambda: [line])
    assert rows_b and all(r["kind"] == "skip" for r in rows_b)


def test_default_runner_proc_exes_failsafe_on_unresolvable_exe(monkeypatch):
    # 🔴2 a pgrep'd runner pid whose exe can't be resolved (no NOPASSWD sudo /
    # hidepid / a `(deleted)` exe) makes the WHOLE scan fail safe to the sentinel,
    # never silently drops the pid to an empty list (which would let the rung
    # delete a live version).
    monkeypatch.setattr(dg.os, "readlink",
                        lambda p: (_ for _ in ()).throw(OSError("EACCES")))

    def _pg(pat):
        return "424242" if pat == dg.RUNNER_LISTENER_BASENAME else ""

    class _R:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    # sudo readlink fails (rc != 0) → sentinel
    exes = dg._default_runner_proc_exes(pgrep_fn=_pg, run_fn=lambda *a, **k: _R(1, ""))
    assert exes == [dg.PROC_ERROR_SENTINEL]

    # exe resolves to a NON-runner basename (a `... (deleted)` exe) → sentinel
    exes2 = dg._default_runner_proc_exes(
        pgrep_fn=_pg,
        run_fn=lambda *a, **k: _R(0, "/x/bin.2.337.0/Runner.Listener (deleted)\n"))
    assert exes2 == [dg.PROC_ERROR_SENTINEL]

    # pgrep error → sentinel
    assert dg._default_runner_proc_exes(pgrep_fn=lambda pat: "PGREP-ERROR") == [dg.PROC_ERROR_SENTINEL]

    # happy path: a resolvable Runner.Listener exe is returned
    exes4 = dg._default_runner_proc_exes(
        pgrep_fn=_pg, run_fn=lambda *a, **k: _R(0, "/x/bin.2.337.0/Runner.Listener\n"))
    assert exes4 == ["/x/bin.2.337.0/Runner.Listener"]


def test_runner_superseded_ignores_non_version_suffixes(tmp_path):
    # 🟡3 bin.bak / externals.old / bin.2.336.0.bak are NOT version dirs → never
    # delete candidates; the real superseded 2.336.0 still is.
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)
    for junk in ("bin.bak", "externals.old", "bin.2.336.0.bak"):
        (rd / junk).mkdir()
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [listener])
    names = {os.path.basename(r["path"]) for r in rows if r.get("path")}
    assert not (names & {"bin.bak", "externals.old", "bin.2.336.0.bak"})
    by = {os.path.basename(r["path"]): r for r in rows}
    assert by["bin.2.336.0"]["kind"] == "delete"


def test_runner_superseded_realpath_scopes_symlinked_root(tmp_path):
    # 🟡4 /proc exe paths are realpath-resolved; the root-scope prefix must accept
    # the realpath too, or a live Runner.Worker under a SYMLINKED root is never
    # matched → the self-update gate is bypassed and superseded dirs deleted while
    # a job runs.
    real = tmp_path / "real"
    rd = _mk_runner_root(real)
    link = tmp_path / "link"
    link.symlink_to(real)
    worker_real = os.path.realpath(str(rd / "bin.2.337.0" / "Runner.Worker"))
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(link), proc_exes_fn=lambda: [worker_real])
    assert rows and all(r["kind"] == "skip" for r in rows)   # worker gates the root


def test_runner_superseded_dangling_symlink_keeps_all(tmp_path):
    # 🟡5 a bin/externals symlink pointing at a non-existent target (mid-swap)
    # cannot prove the current version → KEEP the whole root.
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)
    (rd / "bin").unlink()
    (rd / "bin").symlink_to(rd / "bin.9.9.9")               # dangling
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [])
    assert rows and all(r["kind"] == "skip" for r in rows)


def test_runner_superseded_real_bin_dir_keeps_all(tmp_path):
    # 🟡7 a fresh tarball extracted `bin` as a REAL directory (not a symlink) →
    # cannot resolve the current version → KEEP the whole root (fail-safe).
    root = tmp_path / "gh-runner"
    rd = root / "actions-runner"
    rd.mkdir(parents=True)
    (rd / "bin").mkdir()                                    # real dir, NOT a symlink
    (rd / "externals.2.337.0").mkdir()
    (rd / "externals.2.336.0").mkdir()
    (rd / "externals").symlink_to(rd / "externals.2.337.0")
    rows = dg.discover_superseded_runner_versions(
        runner_root=str(root), proc_exes_fn=lambda: [])
    assert rows and all(r["kind"] == "skip" for r in rows)


def test_plan_superseded_maps_delete_and_skip_via_norm_action(tmp_path, monkeypatch):
    # 🟡7 the ladder wrapper _plan_superseded_runner_versions must map discovery
    # rows through _norm_action end-to-end (a candidate → delete with bytes > 0;
    # a kept row → skip).
    root = tmp_path / "gh-runner"
    rd = _mk_runner_root(root)
    listener = str(rd / "bin.2.337.0" / "Runner.Listener")
    real = dg.discover_superseded_runner_versions
    monkeypatch.setattr(dg, "discover_superseded_runner_versions",
                        lambda **k: real(runner_root=str(root), proc_exes_fn=lambda: [listener]))
    actions = dg._plan_superseded_runner_versions(str(tmp_path), 0)
    by = {os.path.basename(a["path"]): a for a in actions if a.get("path") not in (None, "-")}
    assert by["bin.2.336.0"]["kind"] == "delete"
    assert by["bin.2.336.0"]["bytes"] > 0                   # 🟡7 delete row carries real bytes
    assert by["bin.2.336.0"]["cls"] == "runner-superseded"
    assert by["bin.2.337.0"]["kind"] == "skip"


def test_runner_superseded_reverify_refuses_now_current_version(tmp_path):
    # 🟡6 between plan and act a self-update may move the symlink onto the dir we
    # planned to reclaim — _perform_action re-resolves and REFUSES, never reaching
    # the rm seam; a genuinely superseded dir still proceeds (non-tautology).
    rd = _mk_runner_root(tmp_path / "r")                    # bin -> bin.2.337.0
    calls = []

    def rec(*a, **k):
        calls.append(a[0])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    act_cur = {"cls": "runner-superseded", "kind": "delete",
               "path": str(rd / "bin.2.337.0"), "bytes": 100}
    raised = False
    try:
        dg._perform_action(act_cur, sudo_ok=False, run_fn=rec)
    except OSError:
        raised = True
    assert raised and calls == []                           # rm seam never reached

    act_old = {"cls": "runner-superseded", "kind": "delete",
               "path": str(rd / "bin.2.336.0"), "bytes": 100}
    dg._perform_action(act_old, sudo_ok=False, run_fn=rec)
    assert any("bin.2.336.0" in " ".join(c) for c in calls)
