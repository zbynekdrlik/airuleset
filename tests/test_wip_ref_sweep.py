"""Orphaned ``refs/autopilot-wip/*`` backup-ref reclaimer (#504) — the follow-up
both #503 adversarial reviews flagged (A🟡-2, B🟡-3).

#503 has a worktree worker push a CI-neutral durability backup of its branch to
``refs/autopilot-wip/<branch>`` after each commit, and the supervisor delete it
after integrating the branch (SKILL.md Step 4). A worker that committed (so
pushed a backup) and then died on the account cap, whose ticket is re-dispatched
FRESH (a new worktree + branch), leaves ``refs/autopilot-wip/<oldbranch>`` on
origin with no reclaimer. ``watchdog/wip_ref_sweep.py`` is that reclaimer — a
watchdog ``run_once`` job (job 30), the origin-ref counterpart of
``cli_worktree_sweep.py``'s LOCAL worktree sweep.

The core of this file is the DECISION MATRIX driven by a FAKE git seam
(``FakeGit``), which gives deterministic control over the three branches that
matter — merged / aged / uncertain — including the exact fail-safe edges (a
git error, an unresolvable base, an unreadable date) a real repo cannot be
coaxed into on demand. A separate ``TestRealGit*`` class then proves the actual
git commands (``ls-remote`` enumeration, the lease-guarded delete, merged
detection) against REAL temporary repos, so the fake can never silently drift
from real ``git`` semantics.

SAFETY INVARIANT under test (the thing #503 exists to preserve): a wip ref is
deleted ONLY when POSITIVELY proven merged into an origin base OR aged past the
gate; EVERYTHING uncertain or young is KEPT; and every delete is lease-guarded
so a resurrected worker's new push is REFUSED, never destroyed.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                    # noqa: E402
import watchdog.wip_ref_sweep as wrs     # noqa: E402

NOW = 1786000000.0
DAY = 86400.0
MAX_AGE = 7 * DAY


# ---------------------------------------------------------------------------
# FakeGit — an injectable (rc, stdout) seam matching wrs._wip_git's contract.
# ---------------------------------------------------------------------------
class FakeGit:
    """Configurable fake for ``git_run(args, cwd) -> (rc, stdout)``.

    * ``wip``       : {full-ref: sha} — what ``ls-remote`` reports on origin.
    * ``merged``    : set of shas that ARE ancestors of a base (rc 0 for
                      ``merge-base --is-ancestor``); anything else → rc 1.
    * ``local_ct``  : {sha: committer-epoch} readable by a LOCAL ``git show``.
    * ``fetch_ct``  : {ref: committer-epoch} readable only AFTER a per-ref fetch.
    * ``bases``     : the origin/* refs that ``rev-parse --verify`` resolves.
    * ``push_fail`` : refs whose delete push always fails (models a lease-stale
                      refusal or any other push failure).
    * ``ls_rc``     : rc for ``ls-remote`` (non-zero simulates a listing failure).
    Records ``deleted`` (refs actually removed), ``fetched`` (per-ref fetches),
    and ``calls`` (every argv) for assertions.
    """

    def __init__(self, wip=None, merged=(), local_ct=None, fetch_ct=None,
                 bases=("refs/remotes/origin/main",), push_fail=(), ls_rc=0):
        self.wip = dict(wip or {})
        self.merged = set(merged)
        self.local_ct = dict(local_ct or {})
        self.fetch_ct = dict(fetch_ct or {})
        self.bases = set(bases)
        self.push_fail = set(push_fail)
        self.ls_rc = ls_rc
        self.deleted = []
        self.fetched = []
        self.calls = []
        self._fetch_head = None

    def __call__(self, args, cwd, timeout=None):
        a = list(args)
        self.calls.append(a)
        if a[0] == "ls-remote":
            if self.ls_rc != 0:
                return (self.ls_rc, "")
            return (0, "".join("%s\t%s\n" % (sha, ref)
                               for ref, sha in self.wip.items()))
        if a[0] == "rev-parse":
            ref = a[-1]
            return (0, ref + "\n") if ref in self.bases else (1, "")
        if a[:2] == ["merge-base", "--is-ancestor"]:
            return (0, "") if a[2] in self.merged else (1, "")
        if a[0] == "show" and a[-1] != "FETCH_HEAD":
            sha = a[-1]
            return (0, "%d\n" % self.local_ct[sha]) if sha in self.local_ct else (128, "")
        if a[0] == "fetch":
            ref = a[-1]
            self.fetched.append(ref)
            if ref in self.fetch_ct:
                self._fetch_head = self.fetch_ct[ref]
                return (0, "")
            return (1, "")
        if a[0] == "show" and a[-1] == "FETCH_HEAD":
            return (0, "%d\n" % self._fetch_head) if self._fetch_head is not None else (128, "")
        if a[0] == "push":
            # ["push","origin","--force-with-lease=<ref>:<sha>", ":<ref>"]
            lease = a[2]
            _, _, spec = lease.partition("=")
            _, _, expected = spec.rpartition(":")
            target = a[-1][1:] if a[-1].startswith(":") else a[-1]
            if target in self.push_fail:
                return (1, "")
            if self.wip.get(target) != expected:      # lease stale / gone
                return (1, "")
            self.deleted.append(target)
            self.wip.pop(target, None)
            return (0, "")
        return (0, "")


def _ref(name):
    return wrs.WIP_REF_PREFIX + name


# ---------------------------------------------------------------------------
# classify_wip_ref — the pure decision, one ref at a time.
# ---------------------------------------------------------------------------
class TestClassifyWipRef(unittest.TestCase):
    def _classify(self, sha, ref, git):
        bases = wrs._resolve_base_refs("repo", git)
        return wrs.classify_wip_ref("repo", sha, ref, bases, NOW, git, MAX_AGE)

    def test_merged_ref_is_deleted_immediately(self):
        g = FakeGit(merged={"aaa"})
        c = self._classify("aaa", _ref("b1"), g)
        self.assertEqual(c["action"], "delete-merged")

    def test_unmerged_young_local_is_kept(self):
        g = FakeGit(local_ct={"bbb": int(NOW - 2 * DAY)})     # 2 days old
        c = self._classify("bbb", _ref("b2"), g)
        self.assertEqual(c["action"], "keep")

    def test_unmerged_old_local_is_deleted_aged(self):
        g = FakeGit(local_ct={"ccc": int(NOW - 9 * DAY)})     # 9 days old
        c = self._classify("ccc", _ref("b3"), g)
        self.assertEqual(c["action"], "delete-aged")

    def test_unmerged_unknown_age_is_kept(self):
        # not local, and the per-ref fetch fails too → age unknown → KEEP.
        g = FakeGit()
        c = self._classify("ddd", _ref("b4"), g)
        self.assertEqual(c["action"], "keep")
        self.assertIn("age unknown", c["reason"])

    def test_unmerged_foreign_aged_via_fetch_is_deleted(self):
        # not local; only readable after a per-ref fetch; old → delete-aged.
        g = FakeGit(fetch_ct={_ref("b5"): int(NOW - 30 * DAY)})
        c = self._classify("eee", _ref("b5"), g)
        self.assertEqual(c["action"], "delete-aged")
        self.assertIn(_ref("b5"), g.fetched)

    def test_unmerged_foreign_young_via_fetch_is_kept(self):
        g = FakeGit(fetch_ct={_ref("b6"): int(NOW - 1 * DAY)})
        c = self._classify("fff", _ref("b6"), g)
        self.assertEqual(c["action"], "keep")

    def test_exactly_at_gate_is_deleted(self):
        # age == max_age_s → >= gate → delete-aged (boundary).
        g = FakeGit(local_ct={"ggg": int(NOW - MAX_AGE)})
        c = self._classify("ggg", _ref("b7"), g)
        self.assertEqual(c["action"], "delete-aged")

    def test_just_under_gate_is_kept(self):
        g = FakeGit(local_ct={"hhh": int(NOW - MAX_AGE + 60)})
        c = self._classify("hhh", _ref("b8"), g)
        self.assertEqual(c["action"], "keep")

    def test_future_dated_commit_is_kept(self):
        # clock skew: committer date in the future → negative age → KEEP.
        g = FakeGit(local_ct={"iii": int(NOW + 5 * DAY)})
        c = self._classify("iii", _ref("b9"), g)
        self.assertEqual(c["action"], "keep")

    def test_no_base_falls_through_to_age_gate(self):
        # no origin base resolves → merged check skipped → age gate only.
        # An old one is still deletable; a young one is still kept.
        g = FakeGit(bases=(), local_ct={"jjj": int(NOW - 9 * DAY)})
        c = self._classify("jjj", _ref("b10"), g)
        self.assertEqual(c["action"], "delete-aged")

    def test_merged_takes_precedence_over_a_young_age(self):
        # a merged ref is deleted even if its tip is brand new.
        g = FakeGit(merged={"kkk"}, local_ct={"kkk": int(NOW - 60)})
        c = self._classify("kkk", _ref("b11"), g)
        self.assertEqual(c["action"], "delete-merged")


# ---------------------------------------------------------------------------
# discover_orphaned_wip_refs — enumeration + fetch-degrade.
# ---------------------------------------------------------------------------
class TestDiscover(unittest.TestCase):
    def test_no_wip_refs_returns_empty(self):
        g = FakeGit(wip={})
        self.assertEqual(wrs.discover_orphaned_wip_refs("repo", NOW, git_run=g), [])

    def test_ls_remote_failure_returns_empty(self):
        g = FakeGit(wip={_ref("x"): "aaa"}, ls_rc=128)
        self.assertEqual(wrs.discover_orphaned_wip_refs("repo", NOW, git_run=g), [])

    def test_ignores_non_wip_and_peeled_lines(self):
        g = FakeGit()
        # inject a peeled-tag line + a non-wip line into ls-remote output
        g.wip = {}

        def fake(args, cwd, timeout=None):
            if args[0] == "ls-remote":
                return (0, "aaa\t%s\nbbb\t%s^{}\nccc\trefs/heads/main\n"
                        % (_ref("real"), _ref("real")))
            return FakeGit.__call__(g, args, cwd, timeout)
        refs = wrs._ls_remote_wip_refs("repo", fake)
        self.assertEqual(refs, [("aaa", _ref("real"))])

    def test_fetch_is_called_once_when_refs_present(self):
        calls = []

        def fetcher(root):
            calls.append(root)
        g = FakeGit(wip={_ref("x"): "aaa"}, merged={"aaa"})
        wrs.discover_orphaned_wip_refs("repo", NOW, git_run=g, git_fetch=fetcher)
        self.assertEqual(calls, ["repo"])

    def test_fetch_never_called_when_no_refs(self):
        calls = []
        g = FakeGit(wip={})
        wrs.discover_orphaned_wip_refs("repo", NOW, git_run=g,
                                       git_fetch=lambda r: calls.append(r))
        self.assertEqual(calls, [])

    def test_fetch_failure_is_logged_not_swallowed_and_degrades(self):
        logs = []

        def boom(root):
            raise RuntimeError("network down")
        # unmerged, but a LOCAL date exists → still classifiable after degrade.
        g = FakeGit(wip={_ref("x"): "aaa"}, local_ct={"aaa": int(NOW - 1 * DAY)})
        out = wrs.discover_orphaned_wip_refs("repo", NOW, git_run=g,
                                             git_fetch=boom, logs=logs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["action"], "keep")
        self.assertTrue(any("fetch-degraded" in ln for ln in logs),
                        "a fetch failure must be surfaced in logs, never silently swallowed")


# ---------------------------------------------------------------------------
# sweep_orphaned_wip_refs — cadence, batching, deletes, dry-run.
# ---------------------------------------------------------------------------
class TestSweep(unittest.TestCase):
    def _repo_roots(self, *names):
        return list(names)

    def test_not_due_returns_empty_and_touches_nothing(self):
        state = {"wip_ref_last_sweep": NOW - 60}     # 60s ago, interval 6h
        g = FakeGit(wip={_ref("x"): "aaa"}, merged={"aaa"})
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["r"],
                                          git_run=g, interval=6 * 3600)
        self.assertEqual(out, [])
        self.assertEqual(g.deleted, [])

    def test_merged_deleted_young_kept_old_deleted(self):
        g = FakeGit(
            wip={_ref("merged"): "m0", _ref("young"): "y0", _ref("old"): "o0"},
            merged={"m0"},
            local_ct={"y0": int(NOW - 1 * DAY), "o0": int(NOW - 30 * DAY)},
        )
        state = {}
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["myrepo"],
                                          git_run=g, interval=6 * 3600)
        self.assertEqual(sorted(g.deleted), [_ref("merged"), _ref("old")])
        self.assertNotIn(_ref("young"), g.deleted)
        self.assertTrue(any("DELETED" in ln and "merged" in ln for ln in out))
        self.assertTrue(any("KEEP" in ln and "young" in ln for ln in out))

    def test_dry_run_deletes_nothing_and_does_not_persist(self):
        g = FakeGit(wip={_ref("old"): "o0"}, local_ct={"o0": int(NOW - 30 * DAY)})
        state = {}
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["r"], git_run=g,
                                          dry_run=True, interval=6 * 3600)
        self.assertEqual(g.deleted, [])
        self.assertTrue(any("WOULD-DELETE" in ln for ln in out))
        self.assertNotIn("wip_ref_last_sweep", state)     # dry-run mutates nothing

    def test_persist_and_cadence_marker_written_before_any_delete(self):
        # the cadence marker + cursor must reach disk BEFORE any network op (#172).
        order = []
        g = FakeGit(wip={_ref("m"): "m0"}, merged={"m0"})
        orig_call = g.__call__

        def tracking(args, cwd, timeout=None):
            if args[0] == "push":
                order.append("delete")
            return orig_call(args, cwd, timeout)
        state = {}
        wrs.sweep_orphaned_wip_refs(
            NOW, state, repo_roots=["r"], git_run=tracking, interval=6 * 3600,
            persist=lambda: order.append("persist"))
        self.assertIn("persist", order)
        self.assertIn("delete", order)
        self.assertLess(order.index("persist"), order.index("delete"),
                        "cadence/cursor must persist before any network delete")

    def test_discover_error_on_one_repo_is_isolated(self):
        good = FakeGit(wip={_ref("m"): "m0"}, merged={"m0"})

        def flaky(args, cwd, timeout=None):
            if cwd == "bad":
                raise RuntimeError("boom")
            return good(args, cwd, timeout)
        state = {}
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["bad", "good"],
                                          git_run=flaky, interval=6 * 3600,
                                          max_repos=10)
        self.assertTrue(any("discover-error bad" in ln for ln in out))
        # the good repo was still processed despite the bad one raising
        self.assertTrue(any("DELETED good" in ln for ln in out))

    def test_repo_batch_is_bounded(self):
        g = FakeGit(wip={})
        state = {}
        repos = ["r%d" % i for i in range(10)]
        wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=repos, git_run=g,
                                    interval=6 * 3600, max_repos=3)
        # cursor advanced by exactly the batch size
        self.assertEqual(state.get("wip_ref_cursor"), 3)


# ---------------------------------------------------------------------------
# THE SAFETY INVARIANT — never delete a salvageable copy (what #503 protects).
# ---------------------------------------------------------------------------
class TestSafetyInvariant(unittest.TestCase):
    def test_resurrected_worker_push_refuses_delete_and_keeps_ref(self):
        # discovered as aged (tip 30d) → delete verdict — but between ls-remote
        # and the push the ref's remote tip changed (a resurrected worker), so
        # the lease-guarded delete must REFUSE and the ref stays.
        g = FakeGit(wip={_ref("z"): "o0"}, local_ct={"o0": int(NOW - 30 * DAY)},
                    push_fail={_ref("z")})     # models the lease-stale refusal
        state = {}
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["r"], git_run=g,
                                          interval=6 * 3600)
        self.assertEqual(g.deleted, [], "a refused (lease-stale) delete removes nothing")
        self.assertIn(_ref("z"), g.wip, "the ref must remain on origin")
        self.assertTrue(any("DELETE-REFUSED" in ln for ln in out))

    def test_lease_guarded_delete_only_hits_the_classified_sha(self):
        # _delete_wip_ref must refuse when the remote is no longer at `sha`.
        g = FakeGit(wip={_ref("z"): "newsha"})
        self.assertFalse(wrs._delete_wip_ref("r", _ref("z"), "oldsha", g),
                         "delete must refuse when remote tip != classified sha")
        self.assertIn(_ref("z"), g.wip)
        # ...and succeed when they match.
        self.assertTrue(wrs._delete_wip_ref("r", _ref("z"), "newsha", g))
        self.assertNotIn(_ref("z"), g.wip)

    def test_a_git_error_during_merge_check_never_deletes(self):
        # merge-base errors (rc 128, not local) → not merged; no date → keep.
        g = FakeGit(wip={_ref("z"): "aaa"})     # nothing merged, no dates
        state = {}
        out = wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["r"], git_run=g,
                                          interval=6 * 3600)
        self.assertEqual(g.deleted, [])
        self.assertTrue(any("KEEP" in ln for ln in out))

    def test_young_ref_never_deleted_across_the_whole_window(self):
        for days in (0, 1, 3, 6):
            with self.subTest(days=days):
                g = FakeGit(wip={_ref("y"): "y0"},
                            local_ct={"y0": int(NOW - days * DAY)})
                state = {}
                wrs.sweep_orphaned_wip_refs(NOW, state, repo_roots=["r"],
                                            git_run=g, interval=6 * 3600)
                self.assertEqual(g.deleted, [],
                                 "a %dd-old unmerged ref must never be deleted" % days)


# ---------------------------------------------------------------------------
# REAL git — proves the actual commands (ls-remote / lease delete / merged).
# ---------------------------------------------------------------------------
def _git(repo, *args):
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


class TestRealGit(unittest.TestCase):
    """A real bare origin + working clone, so the actual git plumbing (not a
    fake) proves enumeration, the merged check, and the lease-guarded delete."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.origin = base / "origin.git"
        self.work = base / "work"
        _git(base, "init", "-q", "-b", "main", "--bare", str(self.origin))
        _git(base, "init", "-q", "-b", "main", str(self.work))
        _git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / "f").write_text("0\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", "base")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "fetch", "-q", "origin")

    def _push_wip(self, name, content):
        (self.work / "f").write_text(content)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", content)
        sha = _git(self.work, "rev-parse", "HEAD").stdout.strip()
        _git(self.work, "push", "-q", "origin",
             "HEAD:%s%s" % (wrs.WIP_REF_PREFIX, name))
        return sha

    def _remote_wip(self):
        out = _git(self.work, "ls-remote", "origin",
                   wrs.WIP_REF_PREFIX + "*").stdout
        return {ln.split("\t")[1] for ln in out.splitlines() if ln.strip()}

    def test_merged_deleted_young_kept_old_deleted_end_to_end(self):
        # merged: push a wip ref, then merge it into main + push main so its
        # tip becomes an ancestor of origin/main.
        merged_sha = self._push_wip("merged", "1\n")
        _git(self.work, "merge", "-q", "--no-ff", "-m", "integrate", merged_sha)
        _git(self.work, "push", "-q", "origin", "main")
        # young unmerged: a fresh commit, real (recent) committer date, never
        # merged into origin/main.
        self._push_wip("young", "young\n")
        # old unmerged: backdate its committer date well past the 7d gate.
        (self.work / "f").write_text("old\n")
        _git(self.work, "add", "-A")
        subprocess.run(
            ["git", "-C", str(self.work), "commit", "-qm", "old"],
            check=True, capture_output=True, text=True,
            env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
                 "GIT_AUTHOR_DATE": "%d +0000" % int(NOW - 30 * DAY),
                 "GIT_COMMITTER_DATE": "%d +0000" % int(NOW - 30 * DAY),
                 "PATH": "/usr/bin:/bin:/usr/local/bin"})
        _git(self.work, "push", "-q", "origin", "HEAD:%sold" % wrs.WIP_REF_PREFIX)

        self.assertEqual(self._remote_wip(),
                         {wrs.WIP_REF_PREFIX + n for n in ("merged", "young", "old")})

        def fetcher(root):
            _git(root, "fetch", "-q", "origin")
        state = {}
        wrs.sweep_orphaned_wip_refs(
            NOW, state, repo_roots=[str(self.work)], git_run=wrs._wip_git,
            git_fetch=fetcher, interval=0)     # interval 0 → always due

        remaining = self._remote_wip()
        self.assertNotIn(wrs.WIP_REF_PREFIX + "merged", remaining,
                         "a merged wip ref must be reclaimed")
        self.assertNotIn(wrs.WIP_REF_PREFIX + "old", remaining,
                         "an unmerged wip ref older than the gate must be reclaimed")
        self.assertIn(wrs.WIP_REF_PREFIX + "young", remaining,
                      "a young unmerged wip ref must be KEPT (possible dead-worker copy)")

    def test_real_lease_guard_refuses_when_tip_moved(self):
        sha = self._push_wip("z", "a\n")
        # advance the ref on origin (a "resurrected worker" pushes new work).
        (self.work / "f").write_text("b\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", "b")
        _git(self.work, "push", "-q", "origin",
             "HEAD:%sz" % wrs.WIP_REF_PREFIX)
        # a lease-guarded delete against the OLD sha must refuse, not destroy.
        ok = wrs._delete_wip_ref(str(self.work), wrs.WIP_REF_PREFIX + "z", sha,
                                 wrs._wip_git)
        self.assertFalse(ok, "lease-guarded delete must refuse a moved tip")
        self.assertIn(wrs.WIP_REF_PREFIX + "z", self._remote_wip())


# ---------------------------------------------------------------------------
# Facade re-export wiring.
# ---------------------------------------------------------------------------
class TestFacadeWiring(unittest.TestCase):
    def test_reexported_on_the_watchdog_package(self):
        self.assertIs(wd.sweep_orphaned_wip_refs, wrs.sweep_orphaned_wip_refs)
        self.assertIs(wd.classify_wip_ref, wrs.classify_wip_ref)
        self.assertEqual(wd.WIP_REF_PREFIX, "refs/autopilot-wip/")


if __name__ == "__main__":
    unittest.main()
