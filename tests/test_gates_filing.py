"""#1020 Part 2 -- fast, subprocess-free UNIT tests for the ungated-issue-filing
classifier now that it is an importable `gates/filing` package (it used to be
embedded in a bash heredoc, reachable only through a shell). The end-to-end
behaviour stays covered by test_scope_gate.py (the oracle) + test_gates_filing_char.py
(the block-reason pins); this file locks the pure classifier helpers at the module
level so a regression is caught in milliseconds.

It ALSO locks the ONE #1020 unification: `gates.filing.parse.split_top_level`
IS `gates.shellcmd.split_top_level` (the fourth hand-written copy is gone).
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import shellcmd                       # noqa: E402
from gates.filing import parse, presence, render  # noqa: E402


class TestUnificationLock(unittest.TestCase):
    def test_split_top_level_is_shellcmd(self):
        # The ONE allowed unification: parse re-exports gates.shellcmd's
        # splitter -- the same object, not a re-implemented copy.
        self.assertIs(parse.split_top_level, shellcmd.split_top_level)


class TestTokenising(unittest.TestCase):
    def test_tokens_of_and_strip_prefix(self):
        tk = parse.strip_prefix(parse.tokens_of("sudo env A=b gh issue create -t x"))
        self.assertEqual(tk[:3], ["gh", "issue", "create"])

    def test_flag_value_forms(self):
        self.assertEqual(parse.flag_value(["-t", "hi"], ("-t", "--title")), "hi")
        self.assertEqual(parse.flag_value(["--title=hi"], ("-t", "--title")), "hi")
        self.assertIsNone(parse.flag_value(["-x", "y"], ("-t", "--title")))

    def test_is_issue_create(self):
        self.assertTrue(parse.is_issue_create(["gh", "issue", "create"]))
        self.assertFalse(parse.is_issue_create(["gh", "issue", "edit"]))

    def test_is_api_issues_post(self):
        self.assertTrue(parse.is_api_issues_post(
            ["gh", "api", "repos/o/r/issues", "-X", "POST"]))
        self.assertFalse(parse.is_api_issues_post(
            ["gh", "api", "repos/o/r/issues/5/comments"]))


class TestCdResolution(unittest.TestCase):
    def test_cd_target_and_apply(self):
        self.assertEqual(parse._cd_target(["cd", "/abs"]), "/abs")
        self.assertIsNone(parse._cd_target(["cd", "$VAR"]))
        self.assertEqual(parse._apply_cd("/base", ["cd", "sub"]), "/base/sub")
        self.assertEqual(parse._apply_cd("/base", ["cd", "/other"]), "/other")
        self.assertIsNone(parse._apply_cd("/base", ["cd", "$VAR"]))


class TestHeredocsAndBody(unittest.TestCase):
    def test_extract_heredocs_file_attached(self):
        cmd = "cat > body.md <<'EOF'\nScope-gate: cross-cutting\nEOF\ngh issue create -F body.md"
        file_bodies, direct_bodies, skeleton = parse.extract_heredocs(cmd)
        self.assertIn("body.md", file_bodies)
        self.assertIn("Scope-gate: cross-cutting", file_bodies["body.md"])
        # the body span is blanked out of the skeleton
        self.assertNotIn("Scope-gate:", skeleton)

    def test_resolve_body_from_file_heredoc(self):
        cmd = "cat > body.md <<'EOF'\nhi body\nEOF\ngh issue create -F body.md"
        file_bodies, direct_bodies, _sk = parse.extract_heredocs(cmd)
        tk = ["gh", "issue", "create", "-F", "body.md"]
        body, err = parse.resolve_body(tk, "gh issue create -F body.md", False,
                                       "/tmp", file_bodies, direct_bodies, cmd)
        self.assertEqual(body, "hi body")
        self.assertIsNone(err)

    def test_resolve_body_inline_flag(self):
        tk = ["gh", "issue", "create", "--body", "inline text"]
        body, err = parse.resolve_body(tk, "", False, "/tmp", {}, {}, "")
        self.assertEqual(body, "inline text")

    def test_resolve_body_unreadable_disk_path_gives_reason(self):
        tk = ["gh", "issue", "create", "-F", "nope.md"]
        body, err = parse.resolve_body(tk, "", False, "/nonexistent", {}, {},
                                       "gh issue create -F nope.md")
        self.assertIsNone(body)
        self.assertIn("not readable", err)


class TestChainAndDedup(unittest.TestCase):
    def test_chain_parents_order_dedup(self):
        text = "fix (#10 follow-up) and #10 follow-up again, #20 follow-up"
        self.assertEqual(parse._chain_parents(text), ["10", "20"])

    def test_chain_parent_backref_only(self):
        # a root ticket linking its own (higher-numbered) children is NOT chained
        self.assertIsNone(parse._chain_parent("spawned #99 follow-up", own_number="50"))
        self.assertEqual(parse._chain_parent("child of #5 follow-up", own_number="50"), "5")

    def test_title_jaccard_identifying_tokens(self):
        # different box numbers -> never a duplicate regardless of overlap
        self.assertEqual(parse._title_jaccard("cam4 restart loop", "cam5 restart loop"), 0.0)
        self.assertGreaterEqual(
            parse._title_jaccard("retry queue drops messages under load",
                                 "retry queue drops messages under heavy load"), 0.7)


class TestFieldHardening(unittest.TestCase):
    def test_clean_field_strips_control_and_collapses(self):
        self.assertEqual(parse._clean_field("a\tb\nc\x1bd"), "a b c d")

    def test_no_field_decoy_neutralises_equals(self):
        self.assertEqual(parse._no_field_decoy("parents=999"), "parents:999")

    def test_all_labels_forms(self):
        tk = ["gh", "issue", "create", "-l", "bug,stream:david2", "--label=core",
              "-lstream:x"]
        self.assertEqual(
            parse._all_labels(tk, False),
            ["bug", "stream:david2", "core", "stream:x"])


class TestTargetRepo(unittest.TestCase):
    def test_explicit_repo_flag_wins(self):
        self.assertEqual(
            parse._target_repo_for_segment(
                ["gh", "issue", "create", "-R", "o/r"], False, "cwd/repo"),
            "o/r")

    def test_api_path_repo(self):
        self.assertEqual(
            parse._target_repo_for_segment(
                ["gh", "api", "repos/owner/name/issues"], True, "cwd/repo"),
            "owner/name")


class TestPresence(unittest.TestCase):
    def test_is_away_absent_marker_is_present(self):
        self.assertFalse(presence.is_away("no-such-session-xyz"))

    def test_is_away_empty_session(self):
        self.assertFalse(presence.is_away(""))

    def test_is_away_stale_marker(self):
        sid = "unit-away-%d" % os.getpid()
        mark = Path("/tmp/claude-user-active-%s" % sid)
        mark.write_text("")
        old = time.time() - 1000
        os.utime(mark, (old, old))
        self.addCleanup(lambda: mark.unlink(missing_ok=True))
        self.assertTrue(presence.is_away(sid))

    def test_is_away_fresh_marker_is_present(self):
        sid = "unit-fresh-%d" % os.getpid()
        mark = Path("/tmp/claude-user-active-%s" % sid)
        mark.write_text("")
        self.addCleanup(lambda: mark.unlink(missing_ok=True))
        self.assertFalse(presence.is_away(sid))

    def test_dismissal_word(self):
        self.assertEqual(presence._dismissal_word("this is flaky"), "flaky")
        self.assertIsNone(presence._dismissal_word("a clean description"))

    def test_is_away_garbage_env_falls_back_to_900_like_bash(self):
        # lib-presence.sh maps any non-digit-only AIRULESET_MAIN_GUARD_AWAY_S
        # (incl. a negative) to 900 (AWAY-enabled), NOT to disabled. A stale
        # marker under a "-5" env must still read AWAY (review 🔵 parity).
        sid = "unit-neg-%d" % os.getpid()
        mark = Path("/tmp/claude-user-active-%s" % sid)
        mark.write_text("")
        old = time.time() - 1000
        os.utime(mark, (old, old))
        self.addCleanup(lambda: mark.unlink(missing_ok=True))
        orig = os.environ.get("AIRULESET_MAIN_GUARD_AWAY_S")
        os.environ["AIRULESET_MAIN_GUARD_AWAY_S"] = "-5"
        try:
            self.assertTrue(presence.is_away(sid))
        finally:
            if orig is None:
                os.environ.pop("AIRULESET_MAIN_GUARD_AWAY_S", None)
            else:
                os.environ["AIRULESET_MAIN_GUARD_AWAY_S"] = orig


class TestRender(unittest.TestCase):
    def test_render_block_shape(self):
        results = [("BLOCK", "my title", "no-scope-gate", "none", "o/r", "")]
        out = render.render_block(results)
        self.assertTrue(out.startswith(render.SUMMARY_HEADER))
        self.assertIn('  - "my title" -> no-scope-gate', out)
        self.assertIn("must be EXACTLY", out)  # the MSG wall follows

    def test_worker_msg_present(self):
        self.assertIn("followup_candidates", render.WORKER_MSG)


class TestCwdRepoOf(unittest.TestCase):
    """#1020 fix-forward: cwd_repo_of resolves the FALLBACK target repo from
    the cwd's `origin`. When that git call fails, it falls back to the cwd
    basename -- a fail-OPEN for every cap. This locks that the fail-open is
    now VISIBLE (a stderr journal line) while its RETURN value is unchanged.
    The exact CI failure (run 34920539429) was this fallback happening
    SILENTLY under `dubious ownership`."""

    class _FakeCompleted:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run_capture(self, cwd):
        import io
        from contextlib import redirect_stderr
        from gates.filing import caps
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = caps.cwd_repo_of(cwd)
        return result, buf.getvalue()

    def test_git_nonzero_rc_logs_and_falls_back_to_basename(self):
        from unittest import mock
        from gates.filing import caps
        fake = self._FakeCompleted(
            128, stdout="",
            stderr="fatal: detected dubious ownership in repository at '/x'\n")
        with mock.patch.object(caps.subprocess, "run", return_value=fake):
            result, err = self._run_capture("/some/where/myrepo")
        # RETURN unchanged: the cwd basename fallback.
        self.assertEqual(result, "myrepo")
        # The fail-open is now VISIBLE, and names the first git-stderr line.
        self.assertIn("filing: target repo unresolvable from cwd", err)
        self.assertIn("basename fallback", err)
        self.assertIn('"myrepo"', err)
        self.assertIn("dubious ownership", err)

    def test_git_exception_logs_and_falls_back_to_basename(self):
        from unittest import mock
        from gates.filing import caps
        with mock.patch.object(caps.subprocess, "run",
                               side_effect=OSError("git not found")):
            result, err = self._run_capture("/some/where/other")
        self.assertEqual(result, "other")
        self.assertIn("filing: target repo unresolvable from cwd", err)
        self.assertIn("basename fallback", err)
        self.assertIn('"other"', err)

    def test_git_success_resolves_slug_without_journal_line(self):
        from unittest import mock
        from gates.filing import caps
        fake = self._FakeCompleted(
            0, stdout="https://github.com/zbynekdrlik/airuleset.git\n", stderr="")
        with mock.patch.object(caps.subprocess, "run", return_value=fake):
            result, err = self._run_capture("/whatever")
        # Resolved from origin; no fallback -> no journal line (unchanged).
        self.assertEqual(result, "zbynekdrlik/airuleset")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
