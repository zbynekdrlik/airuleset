"""#1380 review round 2 — the fixes were narrower than claimed.

Real temp git repos, local bare remote, no network. Each test reproduces one
round-2 finding: the allowlist's ancestor rule (an ignored key inside a
tracked ``build/``), self-ignoring cache dirs (pytest/ruff write a ``*``
``.gitignore``), a nested repo inside an allowlisted dir the checkout walk
skips, skip-worktree / assume-unchanged edits ``git status`` never shows,
populated submodules (git refuses them) vs a bare ``.gitmodules`` (git does
not), the Job 20 lane prune and the #345 install sweep deleting ignored owner
files, the checkout walk entering ``~/uploads``, root rung (d) outside the
pull dirs, file-shaped test globs, the journal losing a detached HEAD, signing
passwords in ``gradle.properties``, and quota ``exhausted`` judged by 1 %.
"""
import json
import os
import subprocess
import time
import types
from pathlib import Path

import pytest

import cli_disk_guard_root as g
import watchdog.disk_guard as dg
from cli_lane_live_gate import LiveLaneGate
from watchdog import disk_guard_quota as dgq
from watchdog import disk_guard_worktrees as dgw

_LATER = time.time() + 3 * 86400


def _git(cwd, *args):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
         "-c", "protocol.file.allow=always", *args],
        cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def _commit(cwd, name, text="x"):
    p = Path(cwd) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path):
    home = tmp_path / "home"
    (home / "devel").mkdir(parents=True)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    r = home / "devel" / "x"
    _git(home / "devel", "init", "-q", "x")
    (r / ".gitignore").write_text("*.key\n.env\ntarget/\n.cache/\nprivate/\n"
                                  "test-results*\n")
    _git(r, "add", ".gitignore")
    _commit(r, "build/Dockerfile")
    _git(r, "remote", "add", "origin", str(origin))
    _git(r, "push", "-q", "origin", "main")
    return types.SimpleNamespace(home=home, root=r, origin=origin, tmp=tmp_path)


def _lane(repo, name, where="cycles-wt"):
    path = repo.home / where / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "t-" + name)
    _commit(path, name + ".txt")
    _git(path, "push", "-q", "origin", "t-" + name)
    return path


def _lane_row(repo, path):
    rows = dgw.discover_stale_agent_worktrees(
        home=str(repo.home), now=_LATER, live_check_fn=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=_LATER))
    return [r for r in rows if r["path"] == str(path)][0]


# --- the allowlist ------------------------------------------------------- #
def test_an_ignored_key_inside_a_tracked_build_dir_is_kept(repo):
    path = _lane(repo, "k1")
    (path / "build" / "deploy.key").write_text("k")
    row = _lane_row(repo, path)
    assert row["kind"] == "skip" and "build/deploy.key" in row["reason"], row


def test_self_ignoring_cache_dirs_are_regenerable(repo):
    path = _lane(repo, "c1")
    pc = path / ".pytest_cache" / "v" / "cache"
    pc.mkdir(parents=True)
    (path / ".pytest_cache" / ".gitignore").write_text("# Created by pytest\n*\n")
    (pc / "lastfailed").write_text("{}")
    assert _lane_row(repo, path)["kind"] == "worktree-remove"


def test_a_repo_nested_in_an_allowlisted_dir_keeps_the_lane(repo):
    path = _lane(repo, "n1")
    (path / ".cache").mkdir()
    _git(path / ".cache", "init", "-q", "inner")
    _commit(path / ".cache" / "inner", "unpushed.txt")
    row = _lane_row(repo, path)
    assert row["kind"] == "skip" and "another checkout" in row["reason"], row


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_hidden_local_edits_keep_the_lane(repo, flag):
    path = _lane(repo, "h" + flag[2:6])
    _git(path, "update-index", flag, "build/Dockerfile")
    (path / "build" / "Dockerfile").write_text("local edit\n")
    row = _lane_row(repo, path)
    assert row["kind"] == "skip" and "build/Dockerfile" in row["reason"], row


def test_a_bare_gitmodules_file_does_not_keep_the_lane(repo):
    path = _lane(repo, "gm")
    _commit(path, ".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ../x\n')
    _git(path, "push", "-q", "origin", "t-gm")
    assert _lane_row(repo, path)["kind"] == "worktree-remove"


def test_a_populated_submodule_keeps_the_lane(repo):
    sub = repo.tmp / "sub"
    _git(repo.tmp, "init", "-q", "sub")
    _commit(sub, "s.txt")
    path = _lane(repo, "sm")
    _git(path, "submodule", "add", "-q", str(sub), "vendor/s")
    _git(path, "commit", "-q", "-m", "sub")
    _git(path, "push", "-q", "origin", "t-sm")
    row = _lane_row(repo, path)
    assert row["kind"] == "skip" and "submodule" in row["reason"], row


def test_test_result_globs_never_match_a_file(repo):
    path = _lane(repo, "g1")
    (path / "test-results-client-signoff.xlsx").write_text("signed")
    row = _lane_row(repo, path)
    assert row["kind"] == "skip" and "signoff" in row["reason"], row


# --- the other two worktree removers ------------------------------------- #
def _merged_agent(repo, name):
    path = repo.root / ".claude" / "worktrees" / name
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "worktree-" + name)
    return path


def test_the_job20_prune_keeps_ignored_owner_files(repo):
    import watchdog.lane_reconcile as lr
    path = _merged_agent(repo, "agent-fin")
    (path / "private").mkdir()
    (path / "private" / "POS2ECR.pdf").write_text("pdf")
    logs = lr.prune_finished_worktrees(
        str(repo.root), now=time.time(), handoff_numbers=set(), proc_cwds=set(),
        projects_dir=str(repo.home / ".claude" / "projects"), live_worker_ids=set(),
        age_fn=lambda _r: 3 * 3600, live_use_fn=lambda _p: False)
    assert (path / "private" / "POS2ECR.pdf").exists(), logs


def test_the_job20_prune_sees_untracked_files_under_showuntrackedfiles_no(repo):
    import watchdog.lane_reconcile as lr
    _git(repo.root, "config", "status.showUntrackedFiles", "no")
    path = _merged_agent(repo, "agent-un")
    (path / "draft-for-client.md").write_text("draft")
    logs = lr.prune_finished_worktrees(
        str(repo.root), now=time.time(), handoff_numbers=set(), proc_cwds=set(),
        projects_dir=str(repo.home / ".claude" / "projects"), live_worker_ids=set(),
        age_fn=lambda _r: 3 * 3600, live_use_fn=lambda _p: False)
    assert (path / "draft-for-client.md").exists(), logs


def _sweep(repo, tmp_path):
    from cli_worktree_stale import sweep_stale_worktrees
    return sweep_stale_worktrees(home=str(repo.home), now=_LATER, force=True,
                                 log_path=tmp_path / "s.log", state_path=tmp_path / "s.json")


def test_the_install_sweep_keeps_ignored_owner_files(repo, tmp_path):
    path = repo.home / "cycles-wt" / "7598"
    path.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "t-7598")   # zero ahead
    (path / "private").mkdir()
    (path / "private" / "worldline.pdf").write_text("pdf")
    (path / ".env").write_text("placeholder\n")
    res = _sweep(repo, tmp_path)
    assert (path / "private" / "worldline.pdf").exists(), res
    assert (path / ".env").exists(), res


def test_no_sweep_walks_into_uploads(repo, tmp_path):
    import airuleset
    up = repo.home / "uploads" / "proj"
    up.parent.mkdir(parents=True)
    _git(up.parent, "init", "-q", "proj")
    _commit(up, "a.txt")
    review = repo.home / "uploads" / "proj-review"
    _git(up, "worktree", "add", "-q", str(review), "-b", "review")
    roots = airuleset._checkout_roots(str(repo.home))
    assert not [r for r in roots if "/uploads/" in r + "/"], roots
    _sweep(repo, tmp_path)
    assert review.exists()


def test_the_834_and_345_rungs_protect_staging_and_develop():
    from cli_worktree_common import _STALE_WORKTREE_PROTECTED_BRANCHES
    assert set(_STALE_WORKTREE_PROTECTED_BRANCHES) == set(dgw._PROTECTED_BRANCHES)


# --- root drain rung (d) only in pull dirs ------------------------------- #
def test_root_rung_d_only_touches_pull_dirs(tmp_path):
    root = tmp_path / "root"
    u = root / "home" / "david3"
    stems = [u / "vendor" / "worldline" / "TimSdk", u / "backups" / "odoo-db",
             u / "montalu-web-pull" / "app"]
    for stem in stems:
        stem.mkdir(parents=True)
        t = stem.parent / (stem.name + ".tgz")
        t.write_text("a")
        os.utime(t, (0, 0))
    (root / "tmp").mkdir()
    log = root / "drain.log"
    script = (g.render_drain_script().replace("/tmp", str(root / "tmp"))
              .replace("/home", str(root / "home")).replace(g.DRAIN_LOG_PATH, str(log))
              .replace("mkdir -p '/run/airuleset'", "mkdir -p '%s'" % root))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, AIRULESET_DGROOT_DRAIN_DRY="1"))
    assert r.returncode == 0, r.stderr
    would = [ln for ln in log.read_text().splitlines() if "WOULD-DELETE" in ln]
    assert [ln for ln in would if "montalu-web-pull/app.tgz" in ln], would
    assert not [ln for ln in would if "TimSdk" in ln or "odoo-db" in ln], would


# --- the journal keeps what only a detached HEAD knew -------------------- #
def test_the_deletion_journal_records_head_and_ref(tmp_path):
    home = tmp_path / "h"
    target = home / "scratch" / "x"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    row = {"cls": "user-cache", "path": str(target), "bytes": 1, "kind": "delete",
           "reason": None, "head": "abc123", "contained_in": "upstream/develop"}
    dg.execute_drain({"worst_pct": 97, "dim": "bytes", "level": "critical",
                      "mounts": [{"mount": "/", "worst_pct": 97}]},
                     str(home), [("user-cache", lambda: [row])], lambda: 97,
                     lambda a: 1, geteuid_fn=lambda: 1000,
                     log_path=str(tmp_path / "l.log"), now=_LATER, target_pct=0)
    j = list((home / ".claude" / "disk-guard").glob("deletions-*.jsonl"))[0]
    entry = json.loads(j.read_text().splitlines()[-1])
    assert entry["head"] == "abc123" and entry["contained_in"] == "upstream/develop", entry


# --- signing passwords in gradle.properties ------------------------------ #
def test_a_react_native_store_password_keeps_gradle(tmp_path):
    d = tmp_path / ".gradle"
    d.mkdir()
    (d / "gradle.properties").write_text("MYAPP_UPLOAD_STORE_PASSWORD=x\n")
    rows = dg.discover_toolchain_dirs(home=str(tmp_path), box_class_fn=lambda: "shared-stream",
                                      pgrep_fn=lambda _p: "", dir_stats_fn=lambda _p: (1, 1))
    assert [r["kind"] for r in rows] == ["report"], rows


# --- quota exhausted judged by room, not 1 % of a big ceiling ------------- #
def test_a_pass_that_freed_room_is_not_exhausted(tmp_path):
    mib = 1024
    gd = tmp_path / ".claude" / "disk-guard"
    gd.mkdir(parents=True)
    (gd / "status.json").write_text(json.dumps({"quota_drain": {
        "ts": 9_000.0, "before_pct": 100, "after_pct": 100,
        "before_room_mb": 100, "after_room_mb": 600, "dry_run": False}}))
    st = dgq.quota_state({}, str(tmp_path), 10_000.0, True,
                         usage_fn=lambda: (58600 * mib, 58888 * mib))
    assert st.pressure and not st.exhausted, st


# --- git's own act-time refusal, pinned without the final gate ----------- #
@pytest.mark.parametrize("config_no", [False, True])
def test_git_itself_refuses_an_untracked_file_at_remove_time(repo, monkeypatch, config_no):
    """The act-time ignored-entry gate is stubbed out so only `git worktree
    remove` (no --force, -c status.showUntrackedFiles=normal) stands between
    a late untracked file and its deletion."""
    from watchdog import disk_guard_lane_wt as lane
    if config_no:
        _git(repo.root, "config", "status.showUntrackedFiles", "no")
    path = _merged_agent(repo, "agent-late")
    (path / "late.md").write_text("written after the plan\n")
    monkeypatch.setattr(lane, "final_reason", lambda *_a, **_k: None)
    with pytest.raises(OSError):
        dg._remove_worktree_dir({"repo": str(repo.root), "path": str(path)})
    assert (path / "late.md").exists()


def test_the_shared_clean_check_sees_untracked_files_under_showuntrackedfiles_no(repo):
    from cli_worktree_common import _worktree_git, _worktree_is_clean
    _git(repo.root, "config", "status.showUntrackedFiles", "no")
    path = _merged_agent(repo, "agent-cl")
    (path / "notes.md").write_text("n")
    assert _worktree_is_clean(str(path), _worktree_git) is False
