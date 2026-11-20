"""#1380 — the disk guard reclaims stale lane worktrees OUTSIDE ``.claude/worktrees``.

david3@subdev: 66 worktrees of ``~/devel/odoo/odoo-erp`` in ``~/cycles-wt/<n>``
(~33 GB) were never drained, while the guard deleted the owner's documents.
The stale-agent-worktree rung now has a second source, the repo's own
``git worktree list --porcelain``. A lane worktree goes only when it is clean
(untracked files count), its HEAD is on a remote ref (``upstream/*`` counts),
no process sits in it, it is idle > 24 h, it holds no precious ignored file,
and it is not the main checkout. Removal is ``git worktree remove`` without
``--force``, so git refuses a tree that turned dirty after planning.

Real temp git repos, local bare remotes, no network.
"""
import os
import subprocess
import time

import pytest

import watchdog.disk_guard as dg
from cli_lane_live_gate import LiveLaneGate
from watchdog import disk_guard_worktrees as dgw

_LATER = time.time() + 3 * 86400        # every fixture file is 3 days idle then


def _git(cwd, *args):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def _commit(cwd, name, text="x"):
    (cwd / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def box(tmp_path):
    """``~/devel/x`` with ``origin`` and ``upstream`` bare remotes outside the
    home, and lane worktrees in ``~/cycles-wt``."""
    home = tmp_path / "home"
    (home / "devel").mkdir(parents=True)
    origin, upstream = tmp_path / "origin.git", tmp_path / "upstream.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(tmp_path, "init", "-q", "--bare", str(upstream))
    repo = home / "devel" / "x"
    _git(home / "devel", "init", "-q", "x")
    (repo / ".gitignore").write_text(".env\ntarget/\n")
    _git(repo, "add", ".gitignore")
    _commit(repo, "README")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "remote", "add", "upstream", str(upstream))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "push", "-q", "upstream", "main")
    # a commit that lives ONLY on upstream/develop (the fork-stream base)
    _git(repo, "checkout", "-q", "-b", "tmp-develop")
    up_sha = _commit(repo, "upstream-only")
    _git(repo, "push", "-q", "upstream", "tmp-develop:develop")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-q", "-D", "tmp-develop")
    _git(repo, "fetch", "-q", "upstream")
    cyc = home / "cycles-wt"
    cyc.mkdir()
    wts = {}

    def lane(name, *, push=True, detached_at=None):
        path = cyc / name
        if detached_at:
            _git(repo, "worktree", "add", "-q", "--detach", str(path), detached_at)
        else:
            _git(repo, "worktree", "add", "-q", str(path), "-b", "t-" + name)
            _commit(path, name + ".txt")
            if push:
                _git(path, "push", "-q", "origin", "t-" + name)
        wts[name] = path
        return path

    lane("10028")                                    # clean, pushed → reclaimed
    lane("pin-10101", detached_at=up_sha)            # detached, upstream only → reclaimed
    (lane("dirty") / "notes.md").write_text("uncommitted work\n")
    lane("unpushed", push=False)                     # commit only local → kept
    lane("locked")
    _git(repo, "worktree", "lock", str(wts["locked"]))
    (lane("secret") / ".env").write_text("placeholder\n")   # precious ignored file
    lane("busy")                                     # a live process sits in it
    lane("built")
    (wts["built"] / "target").mkdir()
    (wts["built"] / "target" / "app.bin").write_bytes(b"\0" * 64)   # ignored build output
    agent = repo / ".claude" / "worktrees" / "agent-abc"
    _git(repo, "worktree", "add", "-q", str(agent), "-b", "worktree-agent-abc")
    return {"home": home, "repo": repo, "wts": wts, "agent": agent}


def _discover(box, now=_LATER, **kw):
    kw.setdefault("live_gate", LiveLaneGate(ids=set(), now=now))
    return dgw.discover_stale_agent_worktrees(home=str(box["home"]), now=now, **kw)


def _lanes(rows):
    return {os.path.basename(r["path"]): r for r in rows if r.get("cls") == "lane-worktree"}


@pytest.fixture
def sleeper(box):
    p = subprocess.Popen(["sleep", "120"], cwd=str(box["wts"]["busy"]))
    try:
        yield p
    finally:
        p.kill()
        p.wait()


def test_clean_pushed_idle_lanes_are_planned(box, sleeper):
    lanes = _lanes(_discover(box))
    for name in ("10028", "pin-10101", "built"):
        assert lanes[name]["kind"] == "worktree-remove", lanes[name]
        assert lanes[name]["reason"] is None
        assert lanes[name]["repo"] == str(box["repo"])
    assert "upstream/develop" in lanes["pin-10101"]["contained_in"]


@pytest.mark.parametrize("name,why", [
    ("dirty", "dirty"), ("unpushed", "not contained"), ("locked", "locked"),
    ("secret", "ignored file"), ("busy", "live process")])
def test_unsafe_lanes_are_never_planned(box, sleeper, name, why):
    row = _lanes(_discover(box))[name]
    assert row["kind"] == "skip", row
    assert why in row["reason"], row


def test_main_checkout_and_agent_worktrees_are_not_lane_rows(box, sleeper):
    rows = _discover(box)
    lane_paths = {r["path"] for r in rows if r.get("cls") == "lane-worktree"}
    assert str(box["repo"]) not in lane_paths
    assert str(box["agent"]) not in lane_paths
    assert not [r for r in rows if r["path"] == str(box["repo"])]


def test_a_recent_lane_is_kept(box, sleeper):
    row = _lanes(_discover(box, now=time.time()))["10028"]
    assert row["kind"] == "skip" and "too recent" in row["reason"], row


def test_classification_past_the_budget_is_deferred_and_never_cached(box, sleeper, tmp_path):
    cache = tmp_path / "verdicts.json"
    rows = _lanes(_discover(box, cache_path=cache, lane_classify_budget_s=0))
    assert rows["10028"]["kind"] == "skip" and "deferred" in rows["10028"]["reason"]
    again = _lanes(_discover(box, cache_path=cache))
    assert again["10028"]["kind"] == "worktree-remove", again["10028"]


def test_the_drain_removes_a_planned_lane_and_keeps_its_branch(box, sleeper):
    rows = [r for r in _discover(box) if r.get("cls") == "lane-worktree"]
    home = box["home"]
    logs = dg.execute_drain(
        {"worst_pct": 97, "dim": "bytes", "level": "critical",
         "mounts": [{"mount": "/", "worst_pct": 97}]},
        str(home), [("stale-agent-worktree", lambda: rows)], lambda: 97,
        dg._make_do_action(False, now=_LATER), geteuid_fn=lambda: 1000,
        log_path=str(home / "dg.log"), now=_LATER, target_pct=0)
    wts = box["wts"]
    assert not wts["10028"].exists() and not wts["pin-10101"].exists(), logs
    for name in ("dirty", "unpushed", "locked", "secret", "busy"):
        assert wts[name].exists(), name
    assert (wts["dirty"] / "notes.md").read_text() == "uncommitted work\n"
    listed = _git(box["repo"], "worktree", "list", "--porcelain")
    assert str(wts["10028"]) not in listed
    assert _git(box["repo"], "rev-parse", "--verify", "t-10028")   # the branch stays
    journal = list((home / ".claude" / "disk-guard").glob("deletions-*.jsonl"))
    assert journal and str(wts["10028"]) in journal[0].read_text()


def test_a_tree_that_turns_dirty_after_planning_is_refused(box):
    """TOCTOU: the act runs ``git worktree remove`` without ``--force``."""
    agent = box["agent"]
    (agent / "work.txt").write_text("w")
    _git(agent, "add", "work.txt")
    _git(agent, "commit", "-q", "-m", "w")
    _git(agent, "push", "-q", "origin", "worktree-agent-abc")
    rows = [r for r in _discover(box) if r["path"] == str(agent)]
    assert rows and rows[0]["kind"] == "worktree-remove", rows
    (agent / "late.md").write_text("written after the plan\n")
    with pytest.raises(OSError):
        dg._remove_worktree_dir(rows[0])
    assert (agent / "late.md").read_text() == "written after the plan\n"


def test_worktree_rung_keeps_a_dirty_reachable_tree(box):
    """#939 reclaimed a dirty tree when HEAD was reachable (``--force``); a
    dirty tree is uncommitted work and is kept now. (The #834 rung judges only
    ``.claude/worktrees`` entries since review round 1, so the agent tree.)"""
    from cli_worktree_reclaim import discover_reclaimable_worktrees
    agent = box["agent"]
    _commit(agent, "w.txt")
    _git(agent, "push", "-q", "origin", "worktree-agent-abc")
    (agent / "notes.md").write_text("uncommitted\n")
    rows = discover_reclaimable_worktrees(
        home=str(box["home"]), now=_LATER, in_live_use=lambda _p: False,
        live_gate=LiveLaneGate(ids=set(), now=_LATER))
    row = [r for r in rows if r["path"] == str(agent)][0]
    assert row["reason"] is not None and "dirty" in row["reason"], row
