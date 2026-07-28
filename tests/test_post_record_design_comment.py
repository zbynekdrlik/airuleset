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
echo "$@" >> "${FAKE_GH_LOG:-/dev/null}"
[ "${FAKE_GH_FAIL:-0}" = "1" ] && exit 1
case "$1 $2" in
  "issue view") echo "${FAKE_GH_COMMENTS_JSON:-{\\"comments\\":[]}}";;
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

    def marker(self, issue):
        os.environ["HOME"] = str(self.home)
        return dg.read_marker("airuleset", issue)


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


class TestSkipsTheNetworkWhenAlreadyRecorded(_Base):

    def test_existing_marker_skips_the_gh_call_entirely(self):
        os.environ["HOME"] = str(self.home)
        dg.write_marker("airuleset", 41, "https://x/issues/41#issuecomment-old")
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


if __name__ == "__main__":
    main()
