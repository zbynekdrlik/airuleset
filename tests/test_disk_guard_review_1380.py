"""#1380 review round 1 — two fresh adversarial reviews broke the first cut.

Each test reproduces one finding on real temp git repos (local bare remote,
no network): ignored owner files deleted with a "clean" lane, the root drain
deleting archives in ``~/uploads``, ``status.showUntrackedFiles=no`` hiding
untracked work, a foreign tree behind a stale worktree entry, submodules, a
detached lane moving after the plan, the #834 rung judging the same lanes by
other rules, the owner-data fence failing open on a relative path, the footer
still red on a moving ceiling, orphan-gitdir trees deleted unmeasured, nested
checkouts, Cargo targets inside ``~/uploads``, signing keys in a toolchain dir.
"""
import os
import shutil
import subprocess
import time
import types
from pathlib import Path

import pytest

import cli_disk_guard_root as g
import statusbar
import watchdog.disk_guard as dg
from cli_lane_live_gate import LiveLaneGate
from watchdog import disk_guard_lane_wt as lane
from watchdog import disk_guard_stale_build as dsb
from watchdog import disk_guard_timing as dgt
from watchdog import disk_guard_worktrees as dgw

_LATER = time.time() + 3 * 86400


def _git(cwd, *args):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def _commit(cwd, name, text="x"):
    (Path(cwd) / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    home = tmp_path / "home"
    (home / "devel").mkdir(parents=True)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    r = home / "devel" / "x"
    _git(home / "devel", "init", "-q", "x")
    (r / ".gitignore").write_text("target/\n__pycache__/\nprivate/\ncredentials.json\n"
                                  "backups/\n*.tfstate\n.tox/\n")
    _git(r, "add", ".gitignore")
    _commit(r, "README")
    _git(r, "remote", "add", "origin", str(origin))
    _git(r, "push", "-q", "origin", "main")
    return types.SimpleNamespace(home=home, root=r, tmp=tmp_path)


def _lane(repo, name, where="cycles-wt", push=True):
    path = repo.home / where / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "t-" + name)
    _commit(path, name + ".txt")
    if push:
        _git(path, "push", "-q", "origin", "t-" + name)
    return path


def _rows(repo, now=_LATER):
    return dgw.discover_stale_agent_worktrees(
        home=str(repo.home), now=now, live_check_fn=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=now))


def _row(rows, path):
    found = [r for r in rows if r["path"] == str(path)]
    assert found, (path, [r["path"] for r in rows])
    return found[0]


# --- 🔴 ignored owner files go with a "clean" tree ----------------------- #
@pytest.mark.parametrize("ignored", ["private/worldline/POS2ECR.pdf", "credentials.json",
                                     "backups/db.dump", "infra.tfstate"])
def test_a_lane_holding_a_non_regenerable_ignored_file_is_kept(repo, ignored):
    path = _lane(repo, "7598")
    (path / ignored).parent.mkdir(parents=True, exist_ok=True)
    (path / ignored).write_text("owner data\n")
    row = _row(_rows(repo), path)
    assert row["kind"] == "skip" and "ignored" in row["reason"], row


def test_build_output_alone_still_reclaims(repo):
    path = _lane(repo, "built")
    (path / "target").mkdir()
    (path / "target" / "app.bin").write_bytes(b"\0")
    (path / "pkg" / "__pycache__").mkdir(parents=True)
    (path / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    assert _row(_rows(repo), path)["kind"] == "worktree-remove"


def test_an_agent_worktree_with_ignored_credentials_is_kept(repo):
    path = _lane(repo, "agent-abc", where="devel/x/.claude/worktrees")
    (path / "credentials.json").write_text("{}\n")
    row = _row(_rows(repo), path)
    assert row["cls"] == "stale-agent-worktree" and row["kind"] == "skip", row
    assert "ignored" in row["reason"]


def test_the_834_rung_keeps_ignored_owner_files_too(repo):
    from cli_worktree_reclaim import discover_reclaimable_worktrees
    path = _lane(repo, "agent-def", where="devel/x/.claude/worktrees")
    (path / "backups").mkdir()
    (path / "backups" / "db.dump").write_text("d")
    rows = discover_reclaimable_worktrees(home=str(repo.home), now=_LATER,
                                          in_live_use=lambda _p: False,
                                          live_gate=LiveLaneGate(ids=set(), now=_LATER))
    row = [r for r in rows if r["path"] == str(path)][0]
    assert row["reason"] and "ignored" in row["reason"], row


# --- 🔴 root drain rung (d) inside ~/uploads ----------------------------- #
def test_root_drain_never_deletes_archives_in_owner_dirs(tmp_path):
    root = tmp_path / "root"
    u = root / "home" / "david3"
    pairs = [u / "uploads" / "sdk", u / ".claude" / "work-products" / "7598" / "pack",
             u / "montalu-web-pull" / "app"]
    for stem in pairs:
        stem.mkdir(parents=True)
        tgz = stem.parent / (stem.name + ".tgz")
        tgz.write_text("archive")
        os.utime(tgz, (0, 0))
    (root / "tmp").mkdir()
    log = root / "drain.log"
    script = (g.render_drain_script().replace("/tmp", str(root / "tmp"))
              .replace("/home", str(root / "home")).replace(g.DRAIN_LOG_PATH, str(log))
              .replace("mkdir -p '/run/airuleset'", "mkdir -p '%s'" % root))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, AIRULESET_DGROOT_DRAIN_DRY="1"))
    assert r.returncode == 0, r.stderr
    would = [ln for ln in log.read_text().splitlines() if "WOULD-DELETE" in ln]
    assert any("montalu-web-pull/app.tgz" in ln for ln in would), would
    assert not [ln for ln in would if "/uploads/" in ln or "/work-products/" in ln], would


# --- 🔴 status.showUntrackedFiles=no hides untracked work ---------------- #
def test_untracked_work_survives_showuntrackedfiles_no(repo):
    from cli_worktree_reclaim import discover_reclaimable_worktrees
    _git(repo.root, "config", "status.showUntrackedFiles", "no")
    path = _lane(repo, "agent-u1", where="devel/x/.claude/worktrees")
    (path / "owner-notes.md").write_text("notes\n")
    rows = discover_reclaimable_worktrees(home=str(repo.home), now=_LATER,
                                          in_live_use=lambda _p: False,
                                          live_gate=LiveLaneGate(ids=set(), now=_LATER))
    row = [r for r in rows if r["path"] == str(path)][0]
    assert row["reason"] and "dirty" in row["reason"], row
    with pytest.raises(OSError):
        dg._remove_worktree_dir({"repo": str(repo.root), "path": str(path)})
    assert (path / "owner-notes.md").exists()


# --- 🟡 a stale entry whose path now holds another tree ------------------ #
def test_a_foreign_tree_behind_a_stale_entry_is_never_planned(repo):
    path = _lane(repo, "reused")
    shutil.rmtree(path)
    _git(repo.home / "cycles-wt", "init", "-q", "reused")       # another repo, same path
    _commit(path, "foreign.txt")
    row = _row(_rows(repo), path)
    assert row["kind"] == "skip" and "not a worktree of" in row["reason"], row


# --- 🟡 submodules make `git worktree remove` refuse --------------------- #
def test_a_lane_with_submodules_is_a_cacheable_skip(repo, tmp_path):
    path = _lane(repo, "subm", push=False)
    _commit(path, ".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ../x\n')
    _git(path, "push", "-q", "origin", "t-subm")
    rows = dgw.discover_stale_agent_worktrees(
        home=str(repo.home), now=_LATER, live_check_fn=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=_LATER), cache_path=tmp_path / "v.json")
    row = _row(rows, path)
    assert row["kind"] == "skip" and "submodule" in row["reason"], row
    assert not row.get("error")


# --- 🟡 a detached lane that moves after the plan ------------------------ #
def test_a_detached_lane_that_moves_after_the_plan_is_refused(repo):
    sha = _git(repo.root, "rev-parse", "HEAD")
    path = repo.home / "cycles-wt" / "pin-1"
    path.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", "--detach", str(path), sha)
    row = _row(_rows(repo), path)
    assert row["kind"] == "worktree-remove" and row.get("head") == sha, row
    late = _commit(path, "late-work.txt")                      # only HEAD knows it now
    with pytest.raises(OSError):
        dg._remove_worktree_dir(row)
    assert _git(path, "rev-parse", "HEAD") == late


# --- 🟡 one source per path; staging is protected ------------------------ #
def test_the_834_rung_leaves_out_of_tree_lanes_to_the_lane_source(repo):
    from cli_worktree_reclaim import discover_reclaimable_worktrees
    path = _lane(repo, "10028")
    rows = discover_reclaimable_worktrees(home=str(repo.home), now=_LATER,
                                          in_live_use=lambda _p: False,
                                          live_gate=LiveLaneGate(ids=set(), now=_LATER))
    assert not [r for r in rows if r["path"] == str(path)], rows
    assert _row(_rows(repo), path)["kind"] == "worktree-remove"


def test_the_lane_rung_runs_in_the_prevention_ladder_before_the_834_rung(tmp_path):
    labels = [lab for lab, _p in dg._prevention_planners(str(tmp_path), _LATER)]
    assert labels.index("stale-agent-worktree") < labels.index("worktree"), labels


def test_a_staging_lane_is_protected(repo):
    path = repo.home / "cycles-wt" / "staging-copy"
    path.parent.mkdir(parents=True)
    _git(repo.root, "branch", "staging")
    _git(repo.root, "push", "-q", "origin", "staging")
    _git(repo.root, "worktree", "add", "-q", str(path), "staging")
    row = _row(_rows(repo), path)
    assert row["kind"] == "skip" and "protected branch staging" in row["reason"], row


# --- 🟡 the fence on a relative path or a Path object -------------------- #
def test_the_fence_refuses_relative_and_path_object_targets(tmp_path, monkeypatch):
    home = tmp_path / "h"
    doc = home / "uploads" / "a.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_text("pdf")
    monkeypatch.chdir(home)

    def plan():
        return [{"cls": "user-cache", "path": "uploads/a.pdf", "bytes": 1, "kind": "delete",
                 "reason": None},
                {"cls": "user-cache", "path": Path(doc), "bytes": 1, "kind": "delete",
                 "reason": None}]
    logs = dg.execute_drain(
        {"worst_pct": 97, "dim": "bytes", "level": "critical",
         "mounts": [{"mount": "/", "worst_pct": 97}]},
        str(home), [("user-cache", plan)], lambda: 97, dg._make_do_action(False, now=_LATER),
        geteuid_fn=lambda: 1000, log_path=str(tmp_path / "l.log"), now=_LATER, target_pct=0)
    assert doc.exists(), logs
    assert len([ln for ln in logs if "REFUSE-PROTECTED" in ln]) == 2, logs


# --- 🟡 the footer on a ceiling that follows usage ----------------------- #
@pytest.mark.parametrize("room_mb,shown", [(2400, False), (500, True), (None, True)])
def test_the_quota_footer_needs_little_real_room(tmp_path, room_mb, shown):
    d = tmp_path / ".claude" / "disk-guard"
    d.mkdir(parents=True)
    cache = {"quota_pct": 96, "ts": 1000.0}
    if room_mb is not None:
        cache["quota_room_mb"] = room_mb
    (d / "status.json").write_text(__import__("json").dumps(cache))
    seg = statusbar.quota_segment(home=str(tmp_path), now=1001.0)
    assert bool(seg) is shown, seg


# --- 🟡 an orphan-gitdir tree cannot be measured ------------------------- #
def test_an_orphan_gitdir_tree_is_kept(repo):
    from cli_worktree_reclaim import discover_reclaimable_worktrees
    orphan = repo.root / ".claude" / "worktrees" / "agent-old"
    orphan.mkdir(parents=True)
    (orphan / ".git").write_text("gitdir: /nonexistent/.git/worktrees/agent-old\n")
    (orphan / "uncommitted-work.py").write_text("x = 1\n")
    rows = discover_reclaimable_worktrees(home=str(repo.home), now=_LATER,
                                          in_live_use=lambda _p: False,
                                          live_gate=LiveLaneGate(ids=set(), now=_LATER))
    row = [r for r in rows if r["path"] == str(orphan)][0]
    assert row["reason"] and "orphan" in row["reason"], row


# --- 🔵 the lane source must not take the agent rows down with it -------- #
def test_a_lane_source_error_keeps_the_agent_rows(repo, monkeypatch, tmp_path):
    agent = _lane(repo, "agent-ok", where="devel/x/.claude/worktrees", push=False)
    _lane(repo, "10028")

    def boom(*_a, **_k):
        raise RuntimeError("lane reader broke")
    monkeypatch.setattr(lane, "_repo_lane_rows", boom)
    from watchdog import disk_guard_wt_cache as wtc
    saved = []
    monkeypatch.setattr(wtc.VerdictCache, "save", lambda self, seen: saved.append(list(seen)))
    rows = dgw.discover_stale_agent_worktrees(
        home=str(repo.home), now=_LATER, live_check_fn=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=_LATER), cache_path=tmp_path / "v.json")
    assert "not contained" in _row(rows, agent)["reason"]       # a cacheable agent verdict
    assert [r for r in rows if r.get("cls") == "lane-worktree" and r["kind"] == "skip"
            and "lane source error" in r["reason"]], rows
    assert saved and str(agent) in saved[0], "the cache must still be saved"


# --- 🔵 a protected row is no runnable action ----------------------------- #
def test_refused_owner_rows_do_not_count_as_runnable(tmp_path):
    lefts = []

    class Timer(type(dgt.NULL_TIMER)):
        def over_budget_in_rung(self, lines, label, ladder, left):
            lefts.append(left)
            return False
    home = tmp_path / "h"
    (home / "uploads").mkdir(parents=True)
    cache = home / ".cache"
    cache.mkdir()
    (cache / "a").write_text("a")
    (cache / "b").write_text("b")

    def plan():
        return [{"cls": "user-cache", "path": str(home / "uploads"), "bytes": 1,
                 "kind": "delete", "reason": None},
                {"cls": "user-cache", "path": str(cache / "a"), "bytes": 1,
                 "kind": "delete", "reason": None},
                {"cls": "user-cache", "path": str(cache / "b"), "bytes": 1,
                 "kind": "delete", "reason": None}]
    dg.execute_drain({"worst_pct": 97, "dim": "bytes", "level": "critical",
                      "mounts": [{"mount": "/", "worst_pct": 97}]},
                     str(home), [("user-cache", plan)], lambda: 97,
                     dg._make_do_action(False, now=_LATER), geteuid_fn=lambda: 1000,
                     log_path=str(tmp_path / "l.log"), now=_LATER, target_pct=0, timer=Timer())
    assert lefts == [1], lefts


# --- 🔵 a lane that holds another checkout ------------------------------- #
def test_a_lane_holding_another_checkout_is_kept(repo):
    path = _lane(repo, "host")
    (path / ".tox").mkdir()
    _git(path / ".tox", "init", "-q", "inner")                  # ignored + allowlisted dir
    _commit(path / ".tox" / "inner", "unpushed.txt")
    row = _row(_rows(repo), path)
    assert row["kind"] == "skip" and "another checkout" in row["reason"], row


# --- 🔵 Cargo targets inside owner data ---------------------------------- #
def test_cargo_target_walk_skips_owner_dirs(tmp_path):
    for t in (tmp_path / "uploads" / "proj" / "target", tmp_path / "devel" / "p" / "target"):
        t.mkdir(parents=True)
        (t / "CACHEDIR.TAG").write_text(dsb._TAG_SIGNATURE + "\n")
        (t / ".rustc_info.json").write_text("{}")
    found = dsb._cargo_targets(str(tmp_path))
    assert found == [str(tmp_path / "devel" / "p" / "target")], found


# --- 🔵 signing material in a toolchain dir ------------------------------ #
@pytest.mark.parametrize("name,kind", [("release.jks", "report"), ("debug.keystore", "delete")])
def test_a_toolchain_dir_with_signing_keys_is_only_reported(tmp_path, name, kind):
    d = tmp_path / ".android"
    d.mkdir()
    (d / name).write_bytes(b"k")
    rows = dg.discover_toolchain_dirs(home=str(tmp_path), box_class_fn=lambda: "shared-stream",
                                      pgrep_fn=lambda _p: "", dir_stats_fn=lambda _p: (1, 1))
    assert [r["kind"] for r in rows] == [kind], rows


# --- 🟡 the lane classification order rotates ---------------------------- #
def test_lane_candidates_rotate_between_passes():
    items = ["a", "b", "c"]
    seen = {tuple(lane.rotate(items, 3600.0 * k)) for k in range(3)}
    assert len(seen) == 3 and all(sorted(s) == items for s in seen)
