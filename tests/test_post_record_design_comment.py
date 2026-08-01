"""Behaviour test for hooks/post-record-design-comment.sh (#136, half 1/3).

This is the ONLY place a design marker is ever written -- and only from
evidence RE-READ from GitHub after the fact, never trusted from the command
that was about to run (the #135 lesson: a marker's presence must be backed
by an observed delivery). Hermetic: a fake `gh` is PATH-injected, configured
via FAKE_GH_COMMENTS_JSON / FAKE_GH_FAIL, mirroring the existing
test_fork_no_merge_close_guard.py pattern.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "post-record-design-comment.sh"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                   # noqa: E402

_FAKE_GH = """#!/usr/bin/env bash
# NB: a `${VAR:-default}` whose default TEXT itself contains unescaped
# braces breaks bash's own brace-matching for the substitution -- even when
# VAR *is* set, the parser mis-scans where the expansion ends and appends
# stray characters (reproduced live while writing this fixture). Avoid the
# pitfall entirely: resolve the default in a separate statement.
echo "$@" >> "${FAKE_GH_LOG:-/dev/null}"
[ "${FAKE_GH_FAIL:-0}" = "1" ] && exit 1
JSON="${FAKE_GH_COMMENTS_JSON:-}"
[ -z "$JSON" ] && JSON='{"comments":[]}'
case "$1 $2" in
  "issue view") echo "$JSON";;
  *) exit 1;;
esac
"""


def _fake_gh_dir():
    d = tempfile.mkdtemp(prefix="airuleset-designhook-fakegh-")
    gh = Path(d) / "gh"
    gh.write_text(_FAKE_GH)
    gh.chmod(0o755)
    return d


def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


GOOD_BODY = (
    "Root cause: the retry loop never reset its backoff counter after a "
    "successful call. Chosen approach: reset the counter on the first "
    "success after any failure. Rejected alternative: replacing the whole "
    "backoff strategy with a token bucket -- too big a change for this bug."
)
BAD_BODY = "still looking into this, will update soon"

# #213 -- validation-shaped body (no design content at all).
GOOD_VALIDATED_BODY = (
    "Reproduced the bug live against current HEAD: ran the exact steps "
    "from the ticket against staging and the retry loop still resets the "
    "counter improperly -- the issue is still valid and still real today."
)

# #214 -- review-shaped body (no design/validation content at all).
GOOD_REVIEWED_BODY = (
    "Ran /review and requesting-code-review over the diff -- both came "
    "back 0 🔴 0 🟡 0 🔵 after fixing the one finding in commit "
    "1234567abcdef, then everything was clean."
)

# Adversarial-review finding (post-#214) -- a SINGLE comment plausibly
# carrying all THREE shapes at once (design + validation language +
# review-sounding language with an incidental sha), which must NOT be
# allowed to grant all three markers from one posted comment.
ALL_THREE_SHAPES_BODY = (
    "Root cause: the retry loop never reset its backoff counter after a "
    "successful call, so a single blip left every later call throttled. "
    "Chosen approach: I will implement a reset on the first success after "
    "any failure, confirming this still happens on the current code after "
    "reviewing the retry path closely, similar to the earlier regression "
    "fixed in a1b2c3d. Rejected alternative: replacing the whole backoff "
    "strategy with a token bucket -- too big a change for this bug."
)


def _iso(delta_seconds=0):
    import datetime
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=delta_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _comments_json(comments):
    return json.dumps({"comments": comments})


class _Base(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-designhook-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-designhook-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/airuleset.git")
        self.gh_dir = _fake_gh_dir()
        self.addCleanup(shutil.rmtree, self.gh_dir, True)

    def run_hook(self, command, comments_json="{\"comments\":[]}", gh_fail=False):
        payload = json.dumps({"tool_input": {"command": command},
                              "cwd": str(self.repo)})
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_COMMENTS_JSON"] = comments_json
        env["FAKE_GH_FAIL"] = "1" if gh_fail else "0"
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=env)

    def marker(self, issue, kind="design"):
        os.environ["HOME"] = str(self.home)
        return dg.read_marker("airuleset", issue, kind)


class TestWritesMarkerOnDeliveredDesignComment(_Base):

    def test_a_fresh_authored_comment_that_classifies_writes_the_marker(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-1",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        info = self.marker(41)
        self.assertIsNotNone(info)
        self.assertIn("issuecomment-1", info["url"])

    def test_url_form_of_the_issue_arg_is_resolved(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-2",
        }])
        cmd = 'gh issue comment https://github.com/zbynekdrlik/airuleset/issues/41 --body "x"'
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41))

    def test_explicit_dash_R_repo_is_used_for_the_key(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/other/parovanie-produktov/issues/9#issuecomment-3",
        }])
        cmd = 'gh issue comment 9 -R other/parovanie-produktov --body "x"'
        self.run_hook(cmd, comments)
        os.environ["HOME"] = str(self.home)
        self.assertIsNotNone(dg.read_marker("parovanie-produktov", 9))
        self.assertIsNone(dg.read_marker("airuleset", 9))


class TestNeverWritesOnNonEvidence(_Base):

    def test_a_short_non_design_comment_does_not_write(self):
        comments = _comments_json([{
            "body": BAD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://x/issues/41#issuecomment-4",
        }])
        self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertIsNone(self.marker(41))

    def test_a_comment_by_someone_else_does_not_write(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": False,
            "url": "https://x/issues/41#issuecomment-5",
        }])
        self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertIsNone(self.marker(41))

    def test_a_stale_old_comment_does_not_count_as_fresh_evidence(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(3600), "viewerDidAuthor": True,
            "url": "https://x/issues/41#issuecomment-6",
        }])
        self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertIsNone(self.marker(41))

    def test_a_failed_gh_query_does_not_write(self):
        self.run_hook('gh issue comment 41 --body "x"', gh_fail=True)
        self.assertIsNone(self.marker(41))

    def test_a_non_comment_gh_command_is_ignored(self):
        r = self.run_hook("gh issue create -t x --body y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.marker(41))

    def test_a_totally_unrelated_command_is_ignored(self):
        r = self.run_hook("git status")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_garbage_stdin_does_not_crash(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        r = subprocess.run(["bash", str(HOOK)], input="not json",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestClassifiesEveryKindFromOneComment(_Base):
    """#213 -- a validation-shaped comment writes ONLY the validated marker;
    a design-shaped comment writes ONLY the design marker. `dg.ALL_KINDS`
    is checked, not a hardcoded pair, so this test survives the #214
    extension that adds "reviewed" to it."""

    def test_a_validation_shaped_comment_writes_only_validated(self):
        comments = _comments_json([{
            "body": GOOD_VALIDATED_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-20",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41, "validated"))
        self.assertIsNone(self.marker(41, "design"))

    def test_a_design_shaped_comment_writes_only_design(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-21",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41, "design"))
        self.assertIsNone(self.marker(41, "validated"))

    def test_a_bad_body_writes_neither_kind(self):
        comments = _comments_json([{
            "body": BAD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://x/issues/41#issuecomment-22",
        }])
        self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertIsNone(self.marker(41, "design"))
        self.assertIsNone(self.marker(41, "validated"))

    def test_one_comment_satisfying_all_three_shapes_grants_only_one_marker(self):
        # Adversarial-review finding: a rich comment posted BEFORE any code
        # exists could plausibly satisfy design + validated + reviewed at
        # once, defeating the whole premise that each kind proves its own
        # step actually happened. Confirm the classifiers really do all
        # match this body (the exploit precondition)...
        self.assertTrue(dg.classify_design_comment(ALL_THREE_SHAPES_BODY)[0])
        self.assertTrue(dg.classify_validation_comment(ALL_THREE_SHAPES_BODY)[0])
        self.assertTrue(dg.classify_review_comment(ALL_THREE_SHAPES_BODY)[0])
        # ... then confirm the HOOK grants at most ONE marker from it.
        comments = _comments_json([{
            "body": ALL_THREE_SHAPES_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-30",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        granted = [k for k in dg.ALL_KINDS if self.marker(41, k) is not None]
        self.assertEqual(len(granted), 1,
                         "one comment granted %d markers, expected exactly "
                         "1: %r" % (len(granted), granted))

    def test_the_same_comment_url_cannot_grant_a_second_kind_later(self):
        # Even across TWO separate hook invocations that both see the SAME
        # (unchanged) latest comment as "fresh" -- e.g. a worker re-running
        # a trivial `gh issue comment` call to re-trigger the recorder
        # without posting anything new -- the SAME url must not be able to
        # claim a second kind.
        comments = _comments_json([{
            "body": ALL_THREE_SHAPES_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-31",
        }])
        self.run_hook('gh issue comment 41 --body "x"', comments)
        granted_after_first = {k for k in dg.ALL_KINDS if self.marker(41, k)}
        self.assertEqual(len(granted_after_first), 1)
        # Same url, same fresh comment, a SECOND invocation.
        self.run_hook('gh issue comment 41 --body "x"', comments)
        granted_after_second = {k for k in dg.ALL_KINDS if self.marker(41, k)}
        self.assertEqual(granted_after_first, granted_after_second,
                         "a second invocation against the SAME comment url "
                         "granted an additional kind -- distinct evidence "
                         "kinds must come from distinct comments")

    def test_a_review_shaped_comment_writes_only_reviewed(self):
        # #214
        comments = _comments_json([{
            "body": GOOD_REVIEWED_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-24",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41, "reviewed"))
        self.assertIsNone(self.marker(41, "design"))
        self.assertIsNone(self.marker(41, "validated"))


class TestSkipsTheNetworkWhenAlreadyRecorded(_Base):

    def test_existing_marker_skips_the_gh_call_entirely(self):
        os.environ["HOME"] = str(self.home)
        for kind in dg.ALL_KINDS:
            dg.write_marker("airuleset", 41,
                            "https://x/issues/41#issuecomment-old", kind=kind)
        log = self.home / "gh.log"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_LOG"] = str(log)
        env["FAKE_GH_FAIL"] = "1"     # would blow up the hook if it were called
        payload = json.dumps({"tool_input": {"command": 'gh issue comment 41 --body "x"'},
                              "cwd": str(self.repo)})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(log.exists(), "gh should never have been invoked")

    def test_partial_marking_still_hits_the_network(self):
        # #213 -- only DESIGN marked, validated still missing: a LATER
        # comment might supply it, so the hook must still re-read GitHub
        # rather than treating the issue as fully settled.
        os.environ["HOME"] = str(self.home)
        dg.write_marker("airuleset", 41, "https://x/issues/41#issuecomment-old")
        comments = _comments_json([{
            "body": GOOD_VALIDATED_BODY, "createdAt": _iso(5),
            "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-23",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41, "validated"))


if __name__ == "__main__":
    main()
