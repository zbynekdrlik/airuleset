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


class TestEnvVarPrefixedGhCallIsRecognized(_Base):
    """#277/#274 -- an inline env-var assignment ahead of `gh` (the
    documented david-stream `GH_TOKEN=$(...) gh issue comment ...` recipe,
    used because that stream has no persistently-exported GH_TOKEN) must
    not defeat the cheap prefilter. The prefilter used to require `gh` be
    preceded by start-of-string/`;`/`&`/`|`/`&&` (optionally `sudo `/
    `env `) -- a leading `VAR=$(...)` assignment satisfies none of those,
    so the hook exited 0 without ever touching python/gh and no marker
    was ever written, even though the posted comment classified clean."""

    def test_a_gh_token_prefixed_call_still_writes_the_marker(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-40",
        }])
        cmd = ("GH_TOKEN=$(grep -oP '(?<=kvaskodev:)[^@]+' ~/.git-credentials) "
               'gh issue comment 41 --body "x"')
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41))

    def test_a_simple_var_assignment_prefix_also_works(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-41",
        }])
        r = self.run_hook('GH_TOKEN=abc123 gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41))

    def test_sudo_prefix_still_works_under_the_widened_boundary(self):
        # regression control: the previously-explicit sudo/env handling
        # must keep working once the boundary is widened to a plain \b.
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-42",
        }])
        r = self.run_hook('sudo gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41))

    def test_unrelated_command_still_skips_the_network(self):
        # widening the boundary must not turn the prefilter into "match
        # anything" -- a command that never mentions gh issue comment at
        # all stays a fast, no-network no-op.
        log = self.home / "gh.log"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_LOG"] = str(log)
        env["FAKE_GH_FAIL"] = "1"
        payload = json.dumps({"tool_input": {"command": "git status && echo done"},
                              "cwd": str(self.repo)})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(log.exists(), "gh should never have been invoked")


class TestFallbackExceptionLogging(_Base):
    """#274 -- a genuinely unexpected exception (not one of the routine
    "nothing to do" sys.exit(0) cases) must leave a diagnosable trail
    instead of vanishing into the blanket `2>/dev/null || true`."""

    def test_an_import_failure_is_logged_not_silently_swallowed(self):
        # A copy of the real hook under a REPO_ROOT that has no sibling
        # design_gate.py/notify.py -- forces a genuine ImportError. The
        # process cwd is ALSO pointed at that same bare root (never the
        # real repo), so sys.path[0]="" (CWD, for a script read from
        # stdin) cannot accidentally rescue the import.
        bad_root = Path(tempfile.mkdtemp(prefix="airuleset-designhook-badroot-"))
        self.addCleanup(shutil.rmtree, bad_root, True)
        (bad_root / "hooks").mkdir()
        bad_hook = bad_root / "hooks" / "post-record-design-comment.sh"
        bad_hook.write_text(HOOK.read_text())
        bad_hook.chmod(0o755)

        payload = json.dumps({"tool_input": {"command": 'gh issue comment 41 --body "x"'},
                              "cwd": str(self.repo)})
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONPATH", None)
        r = subprocess.run(["bash", str(bad_hook)], input=payload,
                           capture_output=True, text=True, env=env,
                           cwd=str(bad_root))
        self.assertEqual(r.returncode, 0, r.stderr)   # never blocks
        log_path = self.home / ".claude" / "design-gate-errors.log"
        self.assertTrue(log_path.exists(),
                        "an import failure must be logged, not silently swallowed")
        text = log_path.read_text()
        self.assertIn("import", text.lower())

    def test_a_clean_run_never_writes_the_error_log(self):
        # positive control -- the log stays absent on the ordinary
        # happy path, so its presence is a genuine, meaningful signal.
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-43",
        }])
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        log_path = self.home / ".claude" / "design-gate-errors.log"
        self.assertFalse(log_path.exists())

    def test_an_exception_in_the_main_body_is_also_logged(self):
        # Adversarial-review finding: the earlier import-failure test only
        # exercises the NARROW import try/except -- the larger MAIN
        # try/except (everything after imports) had zero coverage; a
        # mutant replacing it with `except Exception: pass` still passed
        # every prior test. An unexpected non-string "body" from `gh`
        # (comments[].body an int, not caught by any inner try/except)
        # raises inside the classifier -- `(123 or "").strip()` ->
        # AttributeError -- and must still be logged and never crash the
        # hook.
        comments = json.dumps({"comments": [{
            "body": 123, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://x/issues/41#issuecomment-44",
        }]})
        r = self.run_hook('gh issue comment 41 --body "x"', comments)
        self.assertEqual(r.returncode, 0, r.stderr)   # never blocks
        self.assertIsNone(self.marker(41))             # never crash-writes
        log_path = self.home / ".claude" / "design-gate-errors.log"
        self.assertTrue(log_path.exists(),
                        "an unexpected exception in the main body must be "
                        "logged, not silently swallowed")
        self.assertIn("main", log_path.read_text())


class TestRecordsEveryIssueInACompoundCommand(_Base):
    """#208 -- `re.search` only ever extracts the FIRST `gh issue comment
    <N>` occurrence in the command text. A batch worker posting several
    design/validated/reviewed comments in ONE Bash call (e.g. `gh issue
    comment 117 -F a.md && gh issue comment 118 -F b.md && gh issue
    comment 119 -F c.md`) only ever got a marker for the first issue --
    the rest silently got none, even with a fresh, valid, classifying
    comment already posted for each."""

    def test_three_chained_gh_issue_comment_calls_all_get_markers(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/999#issuecomment-50",
        }])
        cmd = ('gh issue comment 117 -F a.md && '
               'gh issue comment 118 -F b.md && '
               'gh issue comment 119 -F c.md')
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(117), "issue #117 (first) missing a marker")
        self.assertIsNotNone(self.marker(118), "issue #118 (second) missing a marker")
        self.assertIsNotNone(self.marker(119), "issue #119 (third) missing a marker")

    def test_the_same_issue_mentioned_twice_only_queries_gh_once(self):
        # dedup, first-seen order -- a repeated mention of the SAME issue
        # in one command should not re-hit the network twice.
        log = self.home / "gh.log"
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/zbynekdrlik/airuleset/issues/41#issuecomment-51",
        }])
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_COMMENTS_JSON"] = comments
        env["FAKE_GH_FAIL"] = "0"
        env["FAKE_GH_LOG"] = str(log)
        cmd = 'gh issue comment 41 -F a.md && gh issue comment 41 -F a.md'
        payload = json.dumps({"tool_input": {"command": cmd}, "cwd": str(self.repo)})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(41))
        calls = [ln for ln in log.read_text().splitlines() if ln.startswith("issue view")]
        self.assertEqual(len(calls), 1,
                         "the same issue mentioned twice should query gh once, saw %r" % calls)

    def test_one_issue_with_only_a_stale_comment_does_not_block_a_sibling(self):
        # A fake `gh` that answers DIFFERENTLY per queried issue number
        # (the shared harness's fake always returns the SAME JSON
        # regardless of which issue is queried, which can't discriminate
        # this case): #41 gets ONLY a stale comment (no marker expected,
        # either pre- or post-fix -- freshness rejects it either way),
        # #42 gets a genuine fresh one. Pre-fix, `re.search` only ever
        # extracts #41 (the first match) and the script exits after
        # finding #41's candidates empty -- #42 is NEVER QUERIED AT ALL,
        # so it gets no marker for a DIFFERENT reason than "stale": it was
        # simply never reached. Post-fix, #42 is independently queried and
        # DOES get a marker from its own genuine fresh comment. This is
        # the discriminating case a same-content-both-issues test cannot
        # be: the two implementations disagree on #42's marker.
        per_issue_gh = """#!/usr/bin/env bash
echo "$@" >> "${FAKE_GH_LOG:-/dev/null}"
ISSUE="$3"
VAR="FAKE_GH_JSON_$ISSUE"
JSON="${!VAR:-}"
[ -z "$JSON" ] && JSON='{"comments":[]}'
case "$1 $2" in
  "issue view") echo "$JSON";;
  *) exit 1;;
esac
"""
        gh_dir = Path(tempfile.mkdtemp(prefix="airuleset-designhook-fakegh-periss-"))
        self.addCleanup(shutil.rmtree, gh_dir, True)
        (gh_dir / "gh").write_text(per_issue_gh)
        (gh_dir / "gh").chmod(0o755)

        stale_only = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(3600), "viewerDidAuthor": True,
            "url": "https://x/issues/41#issuecomment-62",
        }])
        fresh_good = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://x/issues/42#issuecomment-63",
        }])

        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = str(gh_dir) + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_JSON_41"] = stale_only
        env["FAKE_GH_JSON_42"] = fresh_good
        cmd = 'gh issue comment 41 -F a.md && gh issue comment 42 -F b.md'
        payload = json.dumps({"tool_input": {"command": cmd}, "cwd": str(self.repo)})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.marker(41), "issue #41 only had a stale comment")
        self.assertIsNotNone(self.marker(42),
                             "issue #42's own fresh comment must still be "
                             "processed even though #41 (mentioned first) "
                             "had nothing usable")


class TestAmbiguousRepoIsRefused(_Base):
    """Adversarial-review finding (post-#208): the multi-issue loop shares
    ONE `-R`/`--repo` resolution across the WHOLE command (`re.search`
    finds only the first occurrence). A compound command naming TWO
    DIFFERENT repos would silently write the SECOND issue's marker under
    the FIRST issue's (wrong) repo -- a marker that never should have
    existed there, which can wrongly unblock a `git commit` in that repo.
    A command with more than one DISTINCT explicit repo value must be
    refused entirely (no marker for ANY issue in it) rather than guess."""

    def test_two_distinct_explicit_repos_writes_no_marker_at_all(self):
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/owner/aaa/issues/5#issuecomment-70",
        }])
        cmd = 'gh issue comment 5 -R owner/aaa -F a.md && gh issue comment 7 -R owner/bbb -F b.md'
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        os.environ["HOME"] = str(self.home)
        self.assertIsNone(dg.read_marker("aaa", 5))
        self.assertIsNone(dg.read_marker("aaa", 7),
                          "issue #7's marker must never be written under "
                          "aaa's repo key -- its own command segment named bbb")
        self.assertIsNone(dg.read_marker("bbb", 7))

    def test_a_single_repeated_explicit_repo_still_processes_normally(self):
        # regression control -- the SAME -R value repeated for every issue
        # in the command (the realistic, reported shape) must still work.
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://github.com/owner/aaa/issues/5#issuecomment-71",
        }])
        cmd = 'gh issue comment 5 -R owner/aaa -F a.md && gh issue comment 6 -R owner/aaa -F b.md'
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        os.environ["HOME"] = str(self.home)
        self.assertIsNotNone(dg.read_marker("aaa", 5))
        self.assertIsNotNone(dg.read_marker("aaa", 6))


class TestPerIssueContinueIsNotSysExit(_Base):
    """Adversarial-review finding (post-#208): three of the per-issue
    `continue`s (already-recorded, claimed-url, the gh-view try/except)
    had no test distinguishing them from the pre-fix `sys.exit(0)` they
    replaced -- a mutant reverting any one of them to `sys.exit(0)` passed
    the whole suite. Each test below is built so a SECOND, sibling issue
    in the same command can ONLY get its own marker if the FIRST issue's
    outcome used `continue`, never `sys.exit(0)`."""

    def test_an_already_settled_issue_does_not_block_a_sibling(self):
        os.environ["HOME"] = str(self.home)
        for kind in dg.ALL_KINDS:
            dg.write_marker("airuleset", 41,
                            "https://x/issues/41#issuecomment-old", kind=kind)
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": "https://x/issues/42#issuecomment-81",
        }])
        cmd = 'gh issue comment 41 -F a.md && gh issue comment 42 -F b.md'
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self.marker(42),
                             "issue #42 must still be processed even though "
                             "#41 (mentioned first) was already fully settled")

    def test_a_claimed_url_on_one_issue_does_not_block_a_sibling(self):
        url = "https://x/issues/41#issuecomment-82"
        comments = _comments_json([{
            "body": GOOD_BODY, "createdAt": _iso(5), "viewerDidAuthor": True,
            "url": url,
        }])
        os.environ["HOME"] = str(self.home)
        # #41's OWN comment url is already claimed by an earlier "reviewed"
        # marker -- classifying it again for "design" must be refused
        # (distinct evidence kinds need distinct comments), but that must
        # not stop #42 (a DIFFERENT issue, its own unclaimed url) from
        # getting its marker from the SAME comment content.
        dg.write_marker("airuleset", 41, url, kind="reviewed")
        cmd = 'gh issue comment 41 -F a.md && gh issue comment 42 -F b.md'
        r = self.run_hook(cmd, comments)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.marker(41, "design"),
                          "issue #41's comment url was already claimed by "
                          "its own reviewed marker")
        self.assertIsNotNone(self.marker(42, "design"),
                             "issue #42 must still get its own marker")

    def test_a_gh_view_failure_on_one_issue_does_not_block_a_sibling(self):
        # An explicit `-R` bypasses cwd-based repo resolution (which would
        # itself fail gracefully on a bad cwd, exiting BEFORE the loop --
        # not what this test is about). `cwd=` IS passed straight into the
        # per-issue `subprocess.run(...)` call, so a genuinely nonexistent
        # cwd makes THAT call raise for every issue -- the discriminating
        # question is whether the SECOND issue's own gh-view is still
        # ATTEMPTED (and its own failure logged) after the first raises.
        bad_cwd = "/nonexistent/does-not-exist-" + os.urandom(4).hex()
        payload = json.dumps({"tool_input": {
            "command": 'gh issue comment 41 -R o/r -F a.md && gh issue comment 42 -R o/r -F b.md'},
            "cwd": bad_cwd})
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        log_path = self.home / ".claude" / "design-gate-errors.log"
        self.assertTrue(log_path.exists())
        text = log_path.read_text()
        self.assertIn("gh-view #41", text)
        self.assertIn("gh-view #42", text,
                      "issue #42 must be ATTEMPTED even though #41 "
                      "(processed first) already raised")


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
