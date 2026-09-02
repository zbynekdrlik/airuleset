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
    # The literal allowlist must NOT contain any root/cross-user/surface-only
    # class (runner, docker-at-root, /var/log, other users' /tmp).
    fence = dg.RECLAIMABLE_CLASSES
    assert "worktree" in fence and "cli-version" in fence and "uploads" in fence
    assert "transcript" in fence and "toolchain" in fence and "scratch" in fence
    # docker is a box-wide/root op (prunes shared images) → deferred to #841,
    # deliberately NOT in the fence (#834 review 🔴).
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


def test_disk_segment_shown_at_notice(tmp_path):
    _write_disk_cache(tmp_path, 82, 1000.0)
    seg = statusbar.disk_segment(home=str(tmp_path), now=1000.0)
    assert "82%" in seg and "disk" in seg


def test_disk_segment_hidden_when_cache_stale(tmp_path):
    # A dead watchdog must not paint a frozen % forever.
    _write_disk_cache(tmp_path, 95, 1000.0)
    assert statusbar.disk_segment(home=str(tmp_path), now=1000.0 + 3600) == ""


def test_disk_segment_absent_cache(tmp_path):
    assert statusbar.disk_segment(home=str(tmp_path), now=1.0) == ""
