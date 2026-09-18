"""#1056 L2 (f) — the composer hand-off pre-flight.

Before posting a READY-FOR-REVIEW comment, `cmd_handoff` /
`_cmd_handoff_post_body_file` call the gk-watch classifier and REFUSE when:
  * gk comments newer than the previous RFR carry finding ids the body does not
    disposition (exact-id match against Closes-finding: rows / a disposition
    row per id) — `handoff BLOCK: needs-disposition <ids>`; or
  * the newest gk comment is a BOUNCE and Evidence-HEAD is not newer than it —
    `handoff BLOCK: no new commit since BOUNCE <comment-id> @<sha>`.
`unknown` from gk-watch → fail-OPEN (a printed notice, never a wrong block).

The disposition SHAPE check is one primitive, `cli_gk_watch.missing_dispositions`,
shared by the pre-flight and by `validate_passthrough_body` (the pass-through
mirror). RED-first: the pre-flight helper, the primitive, and the
`required_disposition_ids` param do not exist on the base tree.
"""
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_gk_watch
import cli_handoff_template as ht


def _watch(state, *, ids=None, gk_ts=None, head_ts=None, gk_id="C1", sha="deadbee",
           branch=None):
    return {
        "issue": 1, "state": state, "gk_login": "zbynekdrlik",
        "gk_latest": {"id": gk_id, "verdict": "BOUNCE", "created_at": gk_ts,
                      "sha": sha, "ids": list(ids or []), "branch": branch},
        "rfr": None, "head_ts": head_ts, "age_seconds": 0.0,
        "undispositioned_ids": list(ids or []),
    }


class MissingDispositions(unittest.TestCase):
    def test_reports_ids_the_body_does_not_disposition(self):
        body = ("READY-FOR-REVIEW: x\n"
                "Closes-finding: 1 — fixed in abc1234\n"
                "Disposition: 2 wontfix (out of scope)\n")
        # 1 and 2 dispositioned; 3 is not.
        self.assertEqual(
            cli_gk_watch.missing_dispositions(body, ["1", "2", "3"]), ["3"])

    def test_all_dispositioned_is_empty(self):
        body = "🔴 1 fixed\n🟡 2 finding addressed\n"
        self.assertEqual(cli_gk_watch.missing_dispositions(body, ["1", "2"]), [])

    def test_empty_ids_is_empty(self):
        self.assertEqual(cli_gk_watch.missing_dispositions("anything", []), [])
        self.assertEqual(cli_gk_watch.missing_dispositions("anything", None), [])

    def test_casual_mention_does_not_disposition(self):
        # "fixed 2 typos" must NOT count as dispositioning finding 2.
        self.assertEqual(
            cli_gk_watch.missing_dispositions("fixed 2 typos in the readme",
                                              ["2"]), ["2"])


class PreflightBlocks(unittest.TestCase):
    def _pf(self, body, watch_result):
        return airuleset._handoff_gk_preflight(
            1, "owner/repo", body, watch_result=watch_result)

    def test_bounce_unanswered_no_new_commit_blocks(self):
        # newest gk comment is a BOUNCE, head is NOT newer than it.
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=2000.0, head_ts=1000.0)
        msg = self._pf("READY-FOR-REVIEW: nothing new\n🔴 1 fixed", w)
        self.assertIsNotNone(msg)
        self.assertIn("no new commit since BOUNCE", msg)
        self.assertIn("C1", msg)

    def test_bounce_unanswered_commit_exactly_at_verdict_blocks(self):
        # #1056 review-A boundary: head_ts == gk_ts is NOT "a new commit since"
        # the BOUNCE (strict >), so the pre-flight must BLOCK.
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=1000.0, head_ts=1000.0)
        msg = self._pf("READY-FOR-REVIEW: x\nCloses-finding: 1 — fixed in abc1234",
                       w)
        self.assertIsNotNone(msg)
        self.assertIn("no new commit since BOUNCE", msg)

    def test_bounce_unanswered_new_commit_but_undispositioned_blocks(self):
        # a commit landed since the BOUNCE, but the body dispositions none of
        # the BOUNCE's finding ids.
        w = _watch("bounce-unanswered", ids=["1", "2"], gk_ts=1000.0,
                   head_ts=2000.0)
        msg = self._pf("READY-FOR-REVIEW: reworked", w)
        self.assertIsNotNone(msg)
        self.assertIn("needs-disposition", msg)
        self.assertIn("1", msg)
        self.assertIn("2", msg)

    def test_bounce_unanswered_new_commit_and_dispositioned_passes(self):
        w = _watch("bounce-unanswered", ids=["1", "2"], gk_ts=1000.0,
                   head_ts=2000.0)
        body = ("READY-FOR-REVIEW: reworked\n"
                "Closes-finding: 1 — fixed in abc1234\n"
                "Closes-finding: 2 — fixed in def5678\n")
        self.assertIsNone(self._pf(body, w))

    def test_needs_disposition_state_blocks_when_body_missing_ids(self):
        w = _watch("needs-disposition", ids=["3"], gk_ts=1000.0, head_ts=2000.0)
        msg = self._pf("READY-FOR-REVIEW: no disposition here", w)
        self.assertIsNotNone(msg)
        self.assertIn("needs-disposition", msg)
        self.assertIn("3", msg)

    def test_needs_disposition_state_passes_when_body_dispositions(self):
        w = _watch("needs-disposition", ids=["3"], gk_ts=1000.0, head_ts=2000.0)
        body = "READY-FOR-REVIEW: x\nCloses-finding: 3 — fixed in abc1234\n"
        self.assertIsNone(self._pf(body, w))

    def test_rfr_current_passes(self):
        w = _watch("rfr-current", ids=[], gk_ts=1000.0, head_ts=2000.0)
        self.assertIsNone(self._pf("READY-FOR-REVIEW: x", w))

    def test_no_gk_comment_passes(self):
        w = _watch("no-gk-comment", ids=[], gk_ts=None, head_ts=None)
        self.assertIsNone(self._pf("READY-FOR-REVIEW: x", w))

    def test_unknown_fails_open_with_notice(self):
        w = _watch("unknown", ids=[], gk_ts=None, head_ts=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            msg = self._pf("READY-FOR-REVIEW: x", w)
        self.assertIsNone(msg, "unknown must fail OPEN")
        self.assertIn("gk-watch", buf.getvalue().lower())


class PassthroughMirror(unittest.TestCase):
    """validate_passthrough_body mirrors the disposition shape check via
    required_disposition_ids."""

    _BODY = ("READY-FOR-REVIEW: x\n"
             "Self-review-model: claude-opus-4-8\n"
             "Closes-finding: 1 — fixed in abc1234\n")

    def test_missing_disposition_blocks(self):
        err = ht.validate_passthrough_body(
            self._BODY, bounce_round=1, required_disposition_ids=["1", "2"])
        self.assertIsNotNone(err)
        self.assertIn("needs-disposition", err)
        self.assertIn("2", err)

    def test_all_dispositioned_passes(self):
        err = ht.validate_passthrough_body(
            self._BODY, bounce_round=1, required_disposition_ids=["1"])
        self.assertIsNone(err)

    def test_no_ids_is_the_existing_behaviour(self):
        # Backward-compatible: no required ids → the pre-#1056 shape check only.
        self.assertIsNone(
            ht.validate_passthrough_body(self._BODY, bounce_round=1))


class CommitsSinceReal1070(unittest.TestCase):
    """#1070 review 🔴 — exercise the REAL `_handoff_commits_since` against a
    git repo with a known-UTC commit under a forced NON-UTC $TZ, so the tz-less
    `--since` fail-open (git parsing the string in local time) is caught."""

    def _repo_with_commit(self, committer_utc):
        import os
        import subprocess
        import tempfile
        d = tempfile.mkdtemp(prefix="airuleset-cs-1070-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid",
            "GIT_AUTHOR_DATE": committer_utc, "GIT_COMMITTER_DATE": committer_utc,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True,  # noqa: E731
                                        capture_output=True, text=True, env=env)
        run("init", "-q", "-b", "main")
        (Path(d) / "f.txt").write_text("x")
        run("add", "f.txt")
        run("commit", "-q", "-m", "the commit")
        sha = run("rev-parse", "HEAD").stdout.strip()
        # make an origin/main ref (the helper reads origin/<branch>) with no remote
        subprocess.run(["git", "-C", d, "update-ref", "refs/remotes/origin/main",
                        sha], check=True, capture_output=True, text=True, env=env)
        return d, sha

    def _count_under_tz(self, repo, branch, since_ts, tz):
        import os
        import time
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        if hasattr(time, "tzset"):
            time.tzset()
        try:
            return airuleset._handoff_commits_since(branch, since_ts, repo)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            if hasattr(time, "tzset"):
                time.tzset()

    def test_utc_since_after_commit_counts_zero_on_a_plus_tz_box(self):
        # commit at 12:00:00Z; since = 12:01Z -> the commit is BEFORE -> 0.
        # On a +05:30 box, a tz-less --since string would be read as local
        # (06:31Z) and wrongly count the 12:00Z commit as "after" -> 1.
        repo, _ = self._repo_with_commit("2026-09-19T12:00:00+0000")
        import calendar
        since = calendar.timegm((2026, 9, 19, 12, 1, 0, 0, 0, 0))
        self.assertEqual(
            self._count_under_tz(repo, "main", since, "Asia/Kolkata"), 0)

    def test_since_before_commit_counts_one(self):
        repo, _ = self._repo_with_commit("2026-09-19T12:00:00+0000")
        import calendar
        since = calendar.timegm((2026, 9, 19, 11, 59, 0, 0, 0, 0))
        self.assertEqual(
            self._count_under_tz(repo, "main", since, "Asia/Kolkata"), 1)

    def test_no_branch_or_bad_ts_returns_none(self):
        self.assertIsNone(airuleset._handoff_commits_since(None, 1000.0, "/x"))
        self.assertIsNone(airuleset._handoff_commits_since("main", None, "/x"))


class Preflight1070(unittest.TestCase):
    """#1070 item 6 — the bounce pre-flight (a) counts only a BOUNCE whose
    Branch matches the readiness, (b) treats an unparseable head (@?) as an
    advisory not a block, (c) counts commits via `git log --since=<verdict>`
    (head_ts heuristic as fallback), and (d) journals the verdict/branch/sha."""

    def _pf(self, body, watch_result, **kw):
        return airuleset._handoff_gk_preflight(
            1, "owner/repo", body, watch_result=watch_result, **kw)

    def test_bounce_for_a_different_branch_is_advisory_not_block(self):
        # the newest BOUNCE is for another phase's branch -> not this readiness.
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=2000.0, head_ts=1000.0,
                   branch="worktree-phase-A")
        buf = io.StringIO()
        with redirect_stderr(buf):
            msg = self._pf("READY-FOR-REVIEW: fresh phase B\n🔴 1 fixed",
                           w, branch="worktree-phase-B")
        self.assertIsNone(msg, "a bounce for another branch must not block")
        self.assertIn("advisory", buf.getvalue().lower())

    def test_unparseable_head_no_new_commit_is_advisory_not_block(self):
        # head is @? and no commit is countable -> advisory, not the #1071 FP.
        w = _watch("bounce-unanswered", ids=[], gk_ts=2000.0, head_ts=1000.0,
                   sha=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            msg = self._pf("READY-FOR-REVIEW: x", w)
        self.assertIsNone(msg)
        self.assertIn("advisory", buf.getvalue().lower())

    def test_git_log_since_counts_a_new_commit_and_passes(self):
        # commits_since reports 2 commits after the verdict -> a new commit
        # landed -> with dispositions, PASS (no head_ts needed).
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=1000.0, head_ts=None,
                   branch="b")
        body = "READY-FOR-REVIEW: reworked\nCloses-finding: 1 — fixed in abc1234\n"
        msg = self._pf(body, w, branch="b", commits_since=lambda br, ts, cwd: 2)
        self.assertIsNone(msg, "a commit landed since the BOUNCE -> pass")

    def test_git_log_since_zero_and_parseable_head_blocks(self):
        # no commit since the verdict AND a parseable head -> the real block.
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=1000.0, head_ts=None,
                   sha="deadbee", branch="b")
        msg = self._pf("READY-FOR-REVIEW: nothing new\n🔴 1 fixed", w,
                       branch="b", commits_since=lambda br, ts, cwd: 0)
        self.assertIsNotNone(msg)
        self.assertIn("no new commit since BOUNCE", msg)

    def test_matching_branch_still_blocks_when_not_draining(self):
        # a bounce whose branch MATCHES the readiness, no new commit, parseable
        # head -> still blocks (branch scoping does not weaken the real gate).
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=2000.0, head_ts=1000.0,
                   sha="cafe123", branch="b")
        # commits_since=None (git can't answer) -> head_ts fallback: 1000 < 2000
        # -> no new commit -> parseable head -> block (hermetic, no real git).
        msg = self._pf("READY-FOR-REVIEW: x\n🔴 1 fixed", w, branch="b",
                       commits_since=lambda br, ts, cwd: None)
        self.assertIsNotNone(msg)
        self.assertIn("no new commit since BOUNCE", msg)


if __name__ == "__main__":
    unittest.main()
