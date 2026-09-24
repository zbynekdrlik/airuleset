"""#1067 slice 1g — the stale-agent-worktree rung remembers unchanged verdicts.

Live (montalu1, 25.9.2026 01:26 CEST): ``disk-guard: step stale-agent-worktree
took 71.7s``. ``discover_stale_agent_worktrees`` re-ran ``git rev-parse`` +
``git status --porcelain`` + ``git branch -r --contains HEAD`` for EVERY
``agent-*`` worktree on every pass, with no memory between passes.

The design (issue 1067, Approach 1) pinned here:

* a per-account verdict cache keyed by the worktree path, reused while a cheap
  fingerprint (HEAD sha with ref resolution, the gitdir ``index`` mtime, the
  main repo's ``packed-refs`` and ``refs/remotes/origin`` mtimes) is
  unchanged — a second pass over an unchanged worktree makes ZERO git calls;
* the lock check (a) and the live-use check (d) always run fresh;
* a cached reclaimable (``worktree-remove``) verdict is never reused — it is
  re-validated with the full fresh checks, so the cache can only skip work,
  never cause a removal;
* a corrupt cache is a full recompute; vanished worktrees' entries drop;
* a dry-run poll neither reads nor writes the cache.

Real temp git repos give the fingerprint its realism; the verdict git calls
go through a recording ``git_run_fn`` (the one end-to-end test counts the real
``git`` subprocesses instead). No ``gh``, no remote, no network.
"""
import json
import os
import shutil
import subprocess
import types
from pathlib import Path

import pytest

import watchdog.disk_guard as dg
from watchdog import disk_guard_worktrees as dgw

NOW = 1_800_000_000.0
T0 = 1_700_000_000          # backdated mtime baseline (seconds)

_GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True, env=_GIT_ENV).stdout.strip()


class Box:
    """A temp home with one real repo, origin refs and real agent worktrees."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.repo = self.home / "devel" / "proj"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("1")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "c1")
        self.c1 = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.txt").write_text("2")
        _git(self.repo, "commit", "-q", "-am", "c2")
        self.c2 = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.c2)
        _git(self.repo, "update-ref", "refs/remotes/origin/feature/x", self.c1)
        self.cache = self.home / ".claude" / "disk-guard" / "stale-wt-verdicts.json"
        self.wts = {}

    def add_wt(self, name="agent-one"):
        wt = self.repo / ".claude" / "worktrees" / name
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-" + name, str(wt))
        self.wts[name] = wt
        self.backdate()
        return wt

    def gitdir(self, name="agent-one"):
        return self.repo / ".git" / "worktrees" / name

    def backdate(self):
        """Every fingerprint mtime to a fixed past value, so a later real git
        write is always a visible change (coarse fs clocks)."""
        paths = [self.gitdir(n) / "index" for n in self.wts]
        origin = self.repo / ".git" / "refs" / "remotes" / "origin"
        paths += [Path(dp) for dp, _dn, _fn in os.walk(origin)]
        packed = self.repo / ".git" / "packed-refs"
        if packed.exists():
            paths.append(packed)
        for p in paths:
            os.utime(p, ns=(T0 * 10**9, T0 * 10**9))

    def entries(self):
        return json.loads(self.cache.read_text())["entries"]


class FakeGit:
    """A recording ``git_run_fn`` with canned answers per command."""

    def __init__(self, status="", contains="", branch="worktree-agent-one", on_call=None):
        self.calls = []
        self.answers = {("rev-parse", "--abbrev-ref", "HEAD"): branch,
                        ("status", "--porcelain"): status,
                        ("branch", "-r", "--contains", "HEAD"): contains}
        self.on_call = on_call

    def __call__(self, cmd, wt):
        self.calls.append((tuple(cmd), wt))
        if self.on_call:
            self.on_call(tuple(cmd), wt)
        return self.answers.get(tuple(cmd), "")


@pytest.fixture
def box(tmp_path):
    b = Box(tmp_path)
    b.add_wt()
    return b


def _pass(box, git, now=NOW, live=False, cache=True, sizes=None):
    live_calls, sizes = [], ([] if sizes is None else sizes)

    def _live(p):
        live_calls.append(p)
        return live
    rows = dgw.discover_stale_agent_worktrees(
        home=str(box.home), now=now, git_run_fn=git,
        dir_stats_fn=lambda p: (sizes.append(p) or 4096, 0),
        live_check_fn=_live, cache_path=box.cache if cache else None)
    return rows, live_calls


def _row(rows, name="agent-one"):
    [r] = [r for r in rows if r["path"].endswith("/" + name)]
    return r


# --------------------------------------------------------------------------- #
# the reuse itself
# --------------------------------------------------------------------------- #
def test_second_pass_over_unchanged_worktree_makes_zero_git_calls(box):
    git = FakeGit(contains="")                      # HEAD not on origin → skip
    rows1, _ = _pass(box, git)
    assert len(git.calls) == 3, git.calls
    assert box.cache.exists(), "the verdict cache file must be written"
    git2 = FakeGit(contains="")
    rows2, _ = _pass(box, git2)
    assert git2.calls == [], "an unchanged worktree must reuse its verdict"
    r1, r2 = _row(rows1), _row(rows2)
    for k in ("cls", "path", "repo", "bytes", "kind", "reason"):
        assert r1[k] == r2[k], k
    assert r2["reason"] == "HEAD not contained in any origin ref — kept"


def test_dirty_and_protected_verdicts_are_reused_too(box):
    _pass(box, FakeGit(status=" M a.txt\n"))
    git = FakeGit(status=" M a.txt\n")
    rows, _ = _pass(box, git)
    assert git.calls == [] and _row(rows)["reason"] == "dirty worktree — kept"


def test_no_cache_path_keeps_the_old_stateless_behaviour(box):
    _pass(box, FakeGit(), cache=False)
    git = FakeGit()
    _pass(box, git, cache=False)
    assert len(git.calls) == 3
    assert not box.cache.exists()


# --------------------------------------------------------------------------- #
# fingerprint invalidation
# --------------------------------------------------------------------------- #
def test_head_sha_change_invalidates_via_ref_resolution(box):
    _pass(box, FakeGit())
    head_before = (box.gitdir() / "HEAD").read_text()
    _git(box.repo, "update-ref", "refs/heads/worktree-agent-one", box.c1)
    assert (box.gitdir() / "HEAD").read_text() == head_before, \
        "the HEAD file itself is unchanged — only ref resolution sees the move"
    git = FakeGit()
    _pass(box, git)
    assert len(git.calls) == 3


def test_packed_head_ref_is_resolved(box):
    _git(box.repo, "pack-refs", "--all")
    box.backdate()
    _pass(box, FakeGit())
    git = FakeGit()
    _pass(box, git)
    assert git.calls == [], "a packed branch ref still yields a stable fingerprint"


def test_index_mtime_change_invalidates(box):
    _pass(box, FakeGit())
    os.utime(box.gitdir() / "index", ns=((T0 + 5) * 10**9, (T0 + 5) * 10**9))
    git = FakeGit()
    _pass(box, git)
    assert len(git.calls) == 3


@pytest.mark.parametrize("change", [
    ("update-ref", "refs/remotes/origin/new", "C1"),         # new top-level ref
    ("update-ref", "refs/remotes/origin/feature/x", "C2"),   # nested namespace
    ("pack-refs", "--all"),                                  # packed-refs appears
])
def test_origin_refs_change_invalidates(box, change):
    _pass(box, FakeGit())
    args = [{"C1": box.c1, "C2": box.c2}.get(a, a) for a in change]
    _git(box.repo, *args)
    git = FakeGit()
    _pass(box, git)
    assert len(git.calls) == 3, change


def test_ttl_expiry_recomputes(box):
    from watchdog import disk_guard_wt_cache as wtc
    _pass(box, FakeGit())
    git = FakeGit()
    _pass(box, git, now=NOW + wtc.VERDICT_TTL_S + 1)
    assert len(git.calls) == 3


def test_fingerprint_moving_during_the_check_is_not_stored(box):
    """A real ``git status`` can rewrite the index (or a commit can race the
    check): the verdict is then not stored, and the next pass recomputes."""
    bumped = []

    def _bump(cmd, _wt):
        if cmd[0] == "status" and not bumped:
            bumped.append(1)
            os.utime(box.gitdir() / "index", ns=((T0 + 9) * 10**9, (T0 + 9) * 10**9))
    _pass(box, FakeGit(on_call=_bump))
    git2 = FakeGit()
    _pass(box, git2)
    assert len(git2.calls) == 3, "a moved fingerprint must not be trusted"
    git3 = FakeGit()
    _pass(box, git3)
    assert git3.calls == []


def test_unreadable_fingerprint_is_never_cached(tmp_path):
    """A worktree whose gitdir has no HEAD (the 968 suite's fake shape) is
    computed fresh on every pass and never stored."""
    home = tmp_path / "home"
    repo = home / "devel" / "proj"
    (repo / ".git").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "agent-fake"
    wt.mkdir(parents=True)
    gd = repo / ".git" / "worktrees" / "agent-fake"
    gd.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: %s" % gd)
    fake = types.SimpleNamespace(home=home, cache=home / "g" / "v.json")
    for _ in range(2):
        git = FakeGit()
        _pass(fake, git)
        assert len(git.calls) == 3
    entries = json.loads(fake.cache.read_text())["entries"] if fake.cache.exists() else {}
    assert entries == {}


# --------------------------------------------------------------------------- #
# never cached: lock (a), live use (d), a reclaimable verdict
# --------------------------------------------------------------------------- #
def test_lock_is_always_rechecked_and_keeps_the_entry(box):
    _pass(box, FakeGit())
    (box.gitdir() / "locked").write_text("held")
    git = FakeGit()
    rows, _ = _pass(box, git)
    assert _row(rows)["reason"] == "locked worktree — kept" and git.calls == []
    (box.gitdir() / "locked").unlink()
    git = FakeGit()
    rows, _ = _pass(box, git)
    assert git.calls == [], "a locked pass must not drop the entry"
    assert _row(rows)["reason"] == "HEAD not contained in any origin ref — kept"


def test_live_use_is_always_rechecked(box):
    _, live1 = _pass(box, FakeGit())
    git = FakeGit()
    rows, live2 = _pass(box, git, live=True)
    assert live1 and live2, "live-use must run on every pass, cached or not"
    assert _row(rows)["reason"] == "live process cwd inside — kept"
    git = FakeGit()
    _, live3 = _pass(box, git)
    assert live3 and git.calls == []


def test_cached_reclaimable_verdict_is_revalidated_fresh(box):
    rows, _ = _pass(box, FakeGit(contains="  origin/main\n"))
    assert _row(rows)["kind"] == "worktree-remove"
    git = FakeGit(status=" M a.txt\n", contains="  origin/main\n")
    rows, _ = _pass(box, git)
    assert ("status", "--porcelain") in [c for c, _ in git.calls], \
        "a reclaimable verdict must never be reused"
    r = _row(rows)
    assert r["kind"] == "skip" and r["reason"] == "dirty worktree — kept"


def test_reclaimable_verdict_reruns_every_check_and_the_size_walk(box):
    _pass(box, FakeGit(contains="  origin/main\n"))
    git, sizes = FakeGit(contains="  origin/main\n"), []
    rows, _ = _pass(box, git, sizes=sizes)
    assert len(git.calls) == 3 and sizes
    r = _row(rows)
    assert r["kind"] == "worktree-remove" and r["contained_in"] == "origin/main"
    assert r["branch"] == "worktree-agent-one"


# --------------------------------------------------------------------------- #
# cache file hygiene
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("garbage", [
    "{not json",
    "[]",
    '{"v": 1, "entries": []}',
    '{"v": 999, "entries": {}}',
    "ENTRY-BAD-FP",
    "ENTRY-BAD-VERDICT",
])
def test_corrupt_cache_means_full_recompute(box, garbage):
    wt = str(box.wts["agent-one"])
    if garbage == "ENTRY-BAD-FP":
        garbage = json.dumps({"v": 1, "entries": {wt: {"fp": "x", "ts": NOW,
                                                       "verdict": {"kind": "skip"}}}})
    elif garbage == "ENTRY-BAD-VERDICT":
        _pass(box, FakeGit())
        data = json.loads(box.cache.read_text())
        data["entries"][wt]["verdict"] = 5
        garbage = json.dumps(data)
    box.cache.parent.mkdir(parents=True, exist_ok=True)
    box.cache.write_text(garbage)
    git = FakeGit()
    rows, _ = _pass(box, git)
    assert len(git.calls) == 3, "a corrupt cache must never be trusted"
    assert _row(rows)["reason"] == "HEAD not contained in any origin ref — kept"
    git = FakeGit()
    _pass(box, git)
    assert git.calls == [], "the recompute rewrites a valid cache"


def test_vanished_worktree_entry_is_dropped(box):
    box.add_wt("agent-two")
    _pass(box, FakeGit())
    assert len(box.entries()) == 2
    shutil.rmtree(box.wts["agent-two"])
    _pass(box, FakeGit())
    assert list(box.entries()) == [str(box.wts["agent-one"])]


def test_real_git_end_to_end_reaches_zero_git_calls(box, monkeypatch):
    """No fake git: the real ``git status`` refreshes the aged stat info and
    rewrites the index on the first pass, so that verdict is not stored. The
    second pass stores it, and by the third no git is spawned at all."""
    wt = box.wts["agent-one"]
    (wt / "b.txt").write_text("new")
    _git(wt, "add", "b.txt")
    _git(wt, "commit", "-q", "-m", "c3")            # HEAD not on origin → skip
    # A finished worktree's files are older than its index. Files written in
    # the index's own second are "racily clean", and git status then rewrites
    # the index on EVERY call until the clock moves on.
    for dp, _dn, fns in os.walk(wt):
        for fn in fns:
            os.utime(os.path.join(dp, fn), ns=(T0 * 10**9, T0 * 10**9))
    spawned = []
    real_run = subprocess.run

    def _run(cmd, *a, **k):
        spawned.append(cmd)
        return real_run(cmd, *a, **dict(k, env=_GIT_ENV))
    monkeypatch.setattr(dgw.subprocess, "run", _run)
    per_pass = []
    for _ in range(3):
        n = len(spawned)
        rows, _ = _pass(box, None)
        per_pass.append(len(spawned) - n)
        assert _row(rows)["reason"] == "HEAD not contained in any origin ref — kept"
    assert per_pass[0] == 3 and per_pass[-1] == 0, per_pass


# --------------------------------------------------------------------------- #
# wiring: guard-dir path, dry-run isolation
# --------------------------------------------------------------------------- #
def test_planner_passes_the_guard_dir_cache_only_when_asked(tmp_path, monkeypatch):
    from watchdog import disk_guard_wt_cache as wtc
    seen = []
    monkeypatch.setattr(dgw, "discover_stale_agent_worktrees",
                        lambda **kw: seen.append(kw.get("cache_path")) or [])
    dg._plan_stale_agent_worktrees(str(tmp_path), NOW)
    dg._plan_stale_agent_worktrees(str(tmp_path), NOW, wt_cache=True)
    assert seen == [None, dg._guard_dir(str(tmp_path)) / wtc.VERDICTS_NAME]
    assert wtc.VERDICTS_NAME == "stale-wt-verdicts.json"


def test_default_planners_wire_the_cache_into_the_rung(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(dg, "_plan_stale_agent_worktrees",
                        lambda h, n, wt_cache=False: seen.append(wt_cache) or [])
    for flag in (False, True):
        planners = dict(dg._default_planners(str(tmp_path), NOW, wt_cache=flag))
        planners["stale-agent-worktree"]()
    dict(dg._default_planners(str(tmp_path), NOW))["stale-agent-worktree"]()
    assert seen == [False, True, False]


def _sv(used_pct):
    free = 1000 - int(1000 * used_pct / 100)
    return lambda _m: types.SimpleNamespace(
        f_blocks=1000, f_bfree=free, f_bavail=free, f_frsize=4096,
        f_files=100000, f_ffree=90000)


@pytest.mark.parametrize("dry_run", [True, False])
def test_dry_run_poll_never_uses_the_cache(tmp_path, monkeypatch, dry_run):
    """Both ``_default_planners`` call sites (the fs drain and the quota pass's
    re-plan) get ``wt_cache = not dry_run``."""
    kws = []
    monkeypatch.setattr(dg, "_default_planners",
                        lambda _h, _n, scratch_rows=None, **kw: kws.append(kw) or [])
    monkeypatch.setattr(dg, "_prevention_planners",
                        lambda _h, _n, scratch_rows=None: [])
    monkeypatch.setattr(dg, "_collect_top_consumers", lambda *a, **k: [])
    dg.run_disk_guard(
        now=10_000.0, home=str(tmp_path), dry_run=dry_run, statvfs_fn=_sv(82),
        dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000, planners_fn=None,
        box_class_fn=lambda: "shared-stream",
        quota_usage_fn=lambda: (9200, 10000), du_fn=lambda _paths: None,
        sudo_probe_fn=lambda: False, scratch_discover_fn=lambda _n, _h: [],
        severe_run_fn=lambda *a, **k: None)
    assert len(kws) == 2, kws
    assert all(kw.get("wt_cache") is (not dry_run) for kw in kws), kws
    assert not (dg._guard_dir(str(tmp_path)) / "stale-wt-verdicts.json").exists()
