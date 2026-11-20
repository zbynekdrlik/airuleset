"""#1380 review round 3 — the remaining holes in the one final gate.

Real temp git repos, local bare remote, no network: an ignored directory
proven "build output" by its NAME alone under a catch-all ignore rule, a
detached lane whose reflog holds a commit no ref keeps, the scratch-worktree
rung (a live session's scratch checkout, ignored secrets), an unbounded
nested-checkout walk, the #345 dry run disagreeing with its live run, owner
files dressed as build/test outputs, and root rung (d) at any depth.
"""
import os
import subprocess
import time
import types
from pathlib import Path

import pytest

import cli_disk_guard_root as g
import watchdog.disk_guard as dg
from cli_lane_live_gate import LiveLaneGate
from watchdog import disk_guard_lane_wt as lane
from watchdog import disk_guard_worktrees as dgw

_LATER = time.time() + 3 * 86400


def _git(cwd, *args):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
         *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def _commit(cwd, name, text="x"):
    p = Path(cwd) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(cwd, "add", "-f", name)
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
    (r / ".gitignore").write_text("*.key\n.env\n")
    _git(r, "add", ".gitignore")
    _commit(r, "README")
    _git(r, "remote", "add", "origin", str(origin))
    _git(r, "push", "-q", "origin", "main")
    return types.SimpleNamespace(home=home, root=r, tmp=tmp_path)


def _lane(repo, name):
    path = repo.home / "cycles-wt" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "t-" + name)
    _commit(path, name + ".txt")
    _git(path, "push", "-q", "origin", "t-" + name)
    return path


def _row(repo, path):
    rows = dgw.discover_stale_agent_worktrees(
        home=str(repo.home), now=_LATER, live_check_fn=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=_LATER))
    return [r for r in rows if r["path"] == str(path)][0]


# --- a dir name is no proof under a catch-all ignore rule ---------------- #
def test_a_catch_all_gitignore_never_proves_dist_is_build_output(repo):
    path = _lane(repo, "wl")
    _commit(path, ".gitignore", "/*\n!/.gitignore\n!/wl.txt\n")
    _git(path, "push", "-q", "origin", "t-wl")
    (path / "dist").mkdir()
    (path / "dist" / "signed-contract-2026.pdf").write_text("pdf")
    row = _row(repo, path)
    assert row["kind"] == "skip" and "dist/" in row["reason"], row


def test_a_catch_all_info_exclude_never_proves_build_is_build_output(repo):
    path = _lane(repo, "ie")
    gitdir = Path(_git(path, "rev-parse", "--git-common-dir"))
    if not gitdir.is_absolute():
        gitdir = path / gitdir
    (gitdir / "info").mkdir(exist_ok=True)
    (gitdir / "info" / "exclude").write_text("*\n")
    (path / "build").mkdir()
    (path / "build" / "handover-notes.md").write_text("notes")
    row = _row(repo, path)
    assert row["kind"] == "skip" and "build/" in row["reason"], row


def test_a_named_build_dir_rule_still_proves_build_output(repo):
    path = _lane(repo, "nb")
    _commit(path, ".gitignore", "*.key\n.env\nbuild/\n")
    _git(path, "push", "-q", "origin", "t-nb")
    (path / "build").mkdir()
    (path / "build" / "app.o").write_text("o")
    assert _row(repo, path)["kind"] == "worktree-remove"


# --- a detached lane's reflog commit no ref keeps ------------------------ #
def test_a_detached_lane_with_an_orphaned_reflog_commit_is_kept(repo):
    main_sha = _git(repo.root, "rev-parse", "HEAD")
    path = repo.home / "cycles-wt" / "pin-9"
    path.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", "--detach", str(path), main_sha)
    _commit(path, "only-here.txt")                        # never pushed, no branch
    _git(path, "checkout", "-q", "--detach", main_sha)
    row = _row(repo, path)
    assert row["kind"] == "skip" and "reflog" in row["reason"], row


# --- the scratch-worktree rung ------------------------------------------- #
def test_scratch_worktree_rows_pass_the_final_gate(repo):
    wt = repo.tmp / "claude-x" / "key" / "0aa0aa0a-1111-2222-3333-444444444444" / "scratchpad" / "wt1"
    wt.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    (wt / ".env").write_text("placeholder\n")
    rows = [{"cls": "scratch-worktree", "path": str(wt), "bytes": 1,
             "kind": "scratch-worktree-remove", "reason": None}]
    gated = dg._gate_scratch_worktrees(rows, _LATER)
    assert gated[0]["kind"] == "skip" and ".env" in gated[0]["reason"], gated
    with pytest.raises(OSError):
        dg._perform_action(rows[0], now=_LATER)
    assert (wt / ".env").exists()


def test_a_scratch_worktree_used_recently_is_kept(repo):
    wt = repo.tmp / "claude-x" / "key" / "0aa0aa0a-1111-2222-3333-555555555555" / "scratchpad" / "wt2"
    wt.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    rows = [{"cls": "scratch-worktree", "path": str(wt), "bytes": 1,
             "kind": "scratch-worktree-remove", "reason": None}]
    gated = dg._gate_scratch_worktrees(rows, time.time())
    assert gated[0]["kind"] == "skip" and "in use" in gated[0]["reason"], gated


def test_the_scratch_rung_protects_staging():
    from watchdog.disk_guard_shared_stream import SCRATCH_WORKTREE_PROTECTED_BRANCHES
    assert "staging" in SCRATCH_WORKTREE_PROTECTED_BRANCHES


# --- one bounded nested-checkout scan per tree --------------------------- #
def test_the_nested_scan_budget_is_shared_and_cacheable(repo, monkeypatch):
    path = _lane(repo, "big")
    _commit(path, ".gitignore", "*.key\n.env\ntarget/\nnode_modules/\n")
    _git(path, "push", "-q", "origin", "t-big")
    for d in ("target", "node_modules"):
        for i in range(4):
            (path / d / ("f%d" % i)).parent.mkdir(exist_ok=True)
            (path / d / ("f%d" % i)).write_text("x")
    monkeypatch.setattr(lane, "NESTED_SCAN_MAX_ENTRIES", 6)
    kept = lane.final_reason(str(path))
    assert kept and "too large" in kept[0] and kept[1] is False, kept


def test_the_job20_prune_runs_the_gate_only_after_the_cheap_age_check(repo, monkeypatch):
    import watchdog.lane_reconcile as lr
    wt = repo.root / ".claude" / "worktrees" / "agent-young"
    _git(repo.root, "worktree", "add", "-q", str(wt), "-b", "worktree-agent-young")

    def boom(*_a, **_k):
        raise AssertionError("final gate ran before the age check")
    monkeypatch.setattr(lane, "final_reason", boom)
    logs = lr.prune_finished_worktrees(
        str(repo.root), now=time.time(), handoff_numbers=set(), proc_cwds=set(),
        projects_dir=str(repo.home / ".claude" / "projects"), live_worker_ids=set(),
        age_fn=lambda _r: 60, live_use_fn=lambda _p: False)
    assert any("since last commit" in ln for ln in logs), logs


# --- the #345 dry run says what the live run does ------------------------ #
def test_the_install_sweep_dry_run_reports_the_keep(repo, tmp_path):
    from cli_worktree_stale import sweep_stale_worktrees
    path = repo.home / "cycles-wt" / "dry"
    path.parent.mkdir(parents=True)
    _git(repo.root, "worktree", "add", "-q", str(path), "-b", "t-dry")
    (path / ".env").write_text("placeholder\n")
    res = sweep_stale_worktrees(home=str(repo.home), now=_LATER, dry_run=True,
                                log_path=tmp_path / "s.log", state_path=tmp_path / "s.json")
    row = [r for r in res if r.get("path") == str(path)][0]
    assert "left in place" in row["reason"], row


# --- owner files dressed as build / test output -------------------------- #
@pytest.mark.parametrize("rel,ignore", [
    ("dist/contract.pdf", None),                       # tracked dist/ + its own "*" .gitignore
    (".coverage.handoff-2026.md", ".coverage.*\n"),
    ("notes/package-lock.json", "package-lock.json\n"),
])
def test_owner_files_dressed_as_outputs_are_kept(repo, rel, ignore):
    path = _lane(repo, "dr" + str(abs(hash(rel)) % 1000))
    if ignore:
        _commit(path, ".gitignore", "*.key\n.env\n" + ignore)
    else:
        _commit(path, "dist/index.html", "<html>")
        (path / "dist" / ".gitignore").write_text("*\n")
    _git(path, "push", "-q", "origin", "HEAD")
    (path / rel).parent.mkdir(parents=True, exist_ok=True)
    (path / rel).write_text("owner")
    row = _row(repo, path)
    assert row["kind"] == "skip", row


def test_a_package_lock_beside_its_package_json_is_regenerable(repo):
    path = _lane(repo, "pl")
    _commit(path, ".gitignore", "*.key\n.env\npackage-lock.json\n")
    _commit(path, "web/package.json", "{}")
    _git(path, "push", "-q", "origin", "t-pl")
    (path / "web" / "package-lock.json").write_text("{}")
    assert _row(repo, path)["kind"] == "worktree-remove"


# --- root rung (d): a pull dir directly in a home ------------------------ #
def test_root_rung_d_matches_only_a_top_level_pull_dir(tmp_path):
    root = tmp_path / "root"
    u = root / "home" / "david3"
    for stem in (u / "montalu-web-pull" / "app", u / "docs" / "sdk-pull" / "TimSdk"):
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
    assert not [ln for ln in would if "TimSdk" in ln], would
