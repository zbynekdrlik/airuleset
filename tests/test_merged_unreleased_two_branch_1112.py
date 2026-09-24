"""#1112 — the `M` (merged-unreleased) bucket must FILL on a TWO-BRANCH repo
(dev -> main). Commits land on `dev` directly carrying the ticket in the
SUBJECT/BODY; when neither <prefix>/develop nor <prefix>/staging exists but
<prefix>/dev does, M is derived from `<main>..<prefix>/dev`'s commit subjects +
bodies (no per-PR REST, no network). The 3-branch path stays byte-identical.

RED-first: on the base tree `cli_release_state` has no two-branch source, so a
two-branch fixture repo yields an empty M and `core-quals --list-dispatchable`
still lists the merged ticket.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_release_state as rs  # noqa: E402


_GENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, env=_GENV,
                   capture_output=True)


def _commit(repo, subject, body=""):
    msg = subject if not body else subject + "\n\n" + body
    _git(repo, "commit", "--allow-empty", "-q", "-m", msg)


def _init_two_branch(repo, *, with_dev=True, with_develop=False, dev_commits=None,
                     main_branch="main"):
    """A REAL git repo with a `<main_branch>` baseline mirrored to
    origin/<main_branch>, then a `dev` branch carrying `dev_commits` mirrored to
    origin/dev. No network: the remote-tracking refs are set via `update-ref`,
    and a local `origin` remote URL lets the LOCAL slug resolver name the cache
    without gh. `with_develop` additionally mirrors origin/develop (the 3-branch
    guard); `main_branch="master"` exercises the master fallback."""
    subprocess.run(["git", "init", "-q", "-b", main_branch, repo], check=True,
                   env=_GENV, capture_output=True)
    _git(repo, "remote", "add", "origin", "https://github.com/o/r")
    _commit(repo, "chore: baseline")
    _git(repo, "update-ref", "refs/remotes/origin/%s" % main_branch, "HEAD")
    if with_develop:
        _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    if with_dev:
        _git(repo, "checkout", "-q", "-b", "dev")
        for c in (dev_commits or []):
            _commit(repo, c[0], c[1] if len(c) > 1 else "")
        _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")


def _ref_exists_fn(present):
    def _f(root, ref):
        return ref in present
    return _f


def _remote_fn(mapping):
    def _f(root, name):
        return mapping.get(name)
    return _f


# --- Acceptance 1: the two-branch derivation ---------------------------------

_DEV_COMMITS = [
    ("test(#1350): [red] reproduce the widget crash",),
    ("feat(#1350): [green] fix the widget crash",),
    ("fix(#1349): correct the off-by-one",),
    ("chore: wire up the follow-up", "Closes #1203"),
    ('Revert "feat(#1331) old broken thing"',),
]


class TwoBranchDerivesMFromDevRange(unittest.TestCase):
    def setUp(self):
        rs._reset_memo()

    def test_dev_range_subjects_and_body_map_to_issues(self):
        with TemporaryDirectory() as repo:
            _init_two_branch(repo, dev_commits=_DEV_COMMITS)
            got = rs.merged_unreleased_issues(repo)
        # subjects #1350 (x2) + #1349, body Closes #1203; the Revert #1331 is
        # EXCLUDED (the reverted ticket stays open, never dropped into M).
        self.assertEqual(set(got), {1350, 1349, 1203})
        self.assertNotIn(1331, set(got),
                         "a reverted ticket must never enter M")

    def test_no_dev_ref_is_empty(self):
        with TemporaryDirectory() as repo:
            _init_two_branch(repo, with_dev=False)
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), set())

    def test_dev_present_but_empty_range_is_empty(self):
        # dev exists but carries no commits beyond main -> `main..dev` empty ->
        # frozenset() (the `if not commits` guard), not a crash.
        with TemporaryDirectory() as repo:
            _init_two_branch(repo, dev_commits=[])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), set())

    def test_master_fallback_when_no_main(self):
        # A repo whose default branch is `master` (no `main`): the derivation
        # falls back to `<prefix>/master..<prefix>/dev`.
        with TemporaryDirectory() as repo:
            _init_two_branch(repo, main_branch="master",
                             dev_commits=[("fix(#1349): repair",)])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), {1349})

    def test_develop_present_keeps_the_pr_based_result_byte_identical(self):
        # A repo WITH develop uses the UNCHANGED PR-based source; the two-branch
        # git_full_fn (which WOULD yield #9999) must NOT be consulted even though
        # a `dev` ref is also present (develop wins).
        rs._reset_memo()

        def _git_fn(root, rng):
            if rng.endswith("origin/main..origin/develop"):
                return [("aaa", "Merge pull request #5 from x/5-fix")]
            return []   # staging empty

        def _git_full_fn(root, rng):
            return [("zzz", "feat(#9999): sneaky two-branch commit", "")]

        got = rs.merged_unreleased_issues(
            "/repo", git_fn=_git_fn, git_full_fn=_git_full_fn,
            pr_meta_fn=lambda pr: ("PR five", "Closes #105") if pr == 5 else None,
            ref_exists_fn=_ref_exists_fn(
                {"origin/main", "origin/develop", "origin/dev"}),
            remote_fn=_remote_fn({"origin": "o/r"}), slug="o/r",
            cache_path=str(Path("/nonexistent-cache.json")))
        self.assertEqual(set(got), {105})
        self.assertNotIn(9999, set(got),
                         "the two-branch source must not fire when develop exists")


class TwoBranchRangeCap(unittest.TestCase):
    """A range far past a normal between-cuts backlog hides M with a journal
    line and makes ZERO further work (the two-branch analog of the #1090 cap)."""

    def _run(self, commits):
        """Drive the two-branch path with an injected `git_full_fn` returning
        exactly `commits` (a list of (oid, subject, body)); capture stderr."""
        rs._reset_memo()

        def _git_full_fn(root, rng):
            return list(commits)

        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = rs.merged_unreleased_issues(
                "/repo", git_full_fn=_git_full_fn,
                ref_exists_fn=_ref_exists_fn({"origin/main", "origin/dev"}),
                remote_fn=_remote_fn({"origin": "o/r"}), slug="o/r",
                cache_path=str(Path("/nonexistent-cache.json")))
        return got, err.getvalue()

    @staticmethod
    def _distinct_ticket_commits(n):
        return [("o%d" % i, "feat(#%d): change" % (3000 + i), "")
                for i in range(n)]

    def test_over_the_cap_is_empty_with_a_journal_line(self):
        got, err = self._run(
            self._distinct_ticket_commits(rs.MERGED_UNRELEASED_MAX_COMMITS + 1))
        self.assertEqual(set(got), set())
        self.assertIn("merged-unreleased", err)
        self.assertIn("M hidden", err)

    def test_exactly_at_the_cap_is_derived(self):
        got, _err = self._run(
            self._distinct_ticket_commits(rs.MERGED_UNRELEASED_MAX_COMMITS))
        # every commit references a distinct ticket -> all derived, not hidden.
        self.assertEqual(len(got), rs.MERGED_UNRELEASED_MAX_COMMITS)

    def test_cap_counts_commits_not_distinct_issues(self):
        # OVER the cap in COMMITS but only ONE distinct ticket: the cap must
        # still hide M. A mutant capping on len(issues) (==1) would derive
        # {3000} instead of empty -> this pins the cap-on-commits semantics.
        over = [("o%d" % i, "feat(#3000): same ticket", "")
                for i in range(rs.MERGED_UNRELEASED_MAX_COMMITS + 1)]
        got, err = self._run(over)
        self.assertEqual(set(got), set())
        self.assertIn("M hidden", err)


# --- Acceptance 1: the split precedence feeds through to the consumer ---------

class TwoBranchFeedsSplitPrecedence(unittest.TestCase):
    def setUp(self):
        rs._reset_memo()

    def test_merged_move_to_M_while_prio_bounce_stays_workable(self):
        import cli_ticket_state
        with TemporaryDirectory() as repo:
            _init_two_branch(repo, dev_commits=_DEV_COMMITS)
            merged = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(merged), {1350, 1349, 1203})
        workable = {
            1350: {"labels": []},
            1349: {"labels": [{"name": "prio:bounce"}]},   # gk returned it
            1203: {"labels": []},
            500: {"labels": []},                            # not merged
        }
        # #1141 slice 3: the M step lives in the ONE route (bucketize)
        b = cli_ticket_state.bucketize(workable, cli_ticket_state.TicketFacts(
            merged=frozenset(merged)), cli_ticket_state.Box())
        new_workable, merged_rows = b["I"], b["M"]
        self.assertEqual(set(merged_rows), {1350, 1203})
        self.assertIn(1349, new_workable, "a prio:bounce member stays workable")
        self.assertIn(500, new_workable, "a non-merged member stays workable")
        self.assertNotIn(1350, new_workable)


# --- Acceptance 1: lane-fill dispatchable listing excludes them ---------------

_FAKE_GH = r'''#!/usr/bin/env python3
import json, sys
FIXTURE = %(fixture)s
args = sys.argv[1:]
if "repo" in args and "view" in args:
    print("o/r"); sys.exit(0)
if args and args[0] == "workflow":
    print("[]"); sys.exit(0)
if args and args[0] == "run":
    print("[]"); sys.exit(0)
if "issue" in args and "list" in args and "--search" in args:
    search = args[args.index("--search") + 1]
    exclude, require = set(), set()
    for tok in search.split():
        if tok.startswith("-label:"):
            exclude.add(tok[len("-label:"):])
        elif tok.startswith("label:"):
            require.add(tok[len("label:"):])
    out = []
    for row in FIXTURE:
        names = {l["name"] for l in row.get("labels") or []}
        if names & exclude:
            continue
        if require and not (require <= names):
            continue
        out.append(row)
    if "length" in args:
        print(len(out)); sys.exit(0)
    print(json.dumps(out)); sys.exit(0)
print("[]")
'''


class LaneFillDispatchableExcludesM(unittest.TestCase):
    def test_two_branch_merged_ticket_is_not_dispatchable(self):
        fixture = [
            {"number": 2, "title": "plain workable",
             "createdAt": "2026-09-01T00:00:00Z", "labels": [{"name": "bug"}]},
            {"number": 1350, "title": "fix merged to dev",
             "createdAt": "2026-09-02T00:00:00Z", "labels": []},
        ]
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            _init_two_branch(repo, dev_commits=[
                ("feat(#1350): [green] fix the merged thing",)])
            gh = Path(bindir) / "gh"
            gh.write_text(_FAKE_GH % {"fixture": json.dumps(fixture)})
            gh.chmod(0o755)
            env = {**os.environ, "HOME": home,
                   "PATH": f"{bindir}:{os.environ['PATH']}"}
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--list-dispatchable"],
                cwd=repo, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [ln for ln in r.stdout.splitlines()
                     if ln and not ln.startswith("#")]
            nums = {ln.split("\t")[0] for ln in lines}
            self.assertIn("2", nums, r.stdout + r.stderr)
            self.assertNotIn(
                "1350", nums,
                "a merged-unreleased two-branch ticket must not be dispatchable")


if __name__ == "__main__":
    unittest.main()
