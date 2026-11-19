"""#1353 -- the design commit gate derives ticket refs from the commit MESSAGE only.

`gates/commitdesign.py` used to run `issue_refs` over the WHOLE Bash command, so a
worker that appended `#N` lines to an in-repo log via a heredoc and then committed
(`cat >> docs/autopilot-log.md <<'EOF' ... EOF && git commit -m "... (#41)"`) was
blocked for every `#N` in the FILE CONTENT (remoteos-mcp report, 17.11.). Now only
the `git commit` segment's `-m`/`--message` values, a `-F`/`--file` file, and a
heredoc feeding `-F -` (or `-m "$(cat <<EOF ...)"`) are scanned. When the message
cannot be isolated, the gate falls back to the old whole-command scan (the safe,
over-check direction).

Every case drives the REAL hook (hooks/block-commit-without-design.sh) with an
`agent_type: autopilot-worker` payload, reusing the #136 test harness (`_Base`:
isolated HOME, a repo with an origin, a stubbed `gh`).
"""
import time
from unittest import main

from test_block_commit_without_design import _Base

LOG_APPEND = ("cat >> docs/autopilot-log.md <<'EOF'\n"
              "- #101 shipped, see #102 and (#103)\n"
              "EOF\n")


class TestHeredocFileContentIsNotTheMessage(_Base):

    def test_log_heredoc_refs_do_not_block_a_designed_commit(self):
        self.mark(41)
        cmd = LOG_APPEND + 'git add docs && git commit -m "docs: log entry (#41)"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_undesigned_ref_in_the_message_still_blocks(self):
        self.mark(41)
        cmd = LOG_APPEND + 'git add docs && git commit -m "docs: log entry (#42)"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)
        self.assertNotIn("#101", r.stderr)
        self.assertNotIn("#102", r.stderr)

    def test_echo_append_refs_do_not_block(self):
        self.mark(41)
        cmd = ("echo '- #101 shipped' >> docs/autopilot-log.md && "
               "printf '%s\\n' '#102' >> docs/x.md && "
               'git commit -am "log (#41)"')
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_clustered_am_flag_message_is_scanned(self):
        cmd = LOG_APPEND + 'git commit -am "log (#42)"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_message_equals_form_is_scanned(self):
        cmd = LOG_APPEND + 'git commit --message="log (#42)"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_every_m_value_is_scanned(self):
        self.mark(41)
        cmd = LOG_APPEND + 'git commit -m "log (#41)" -m "also (#42)"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)


class TestFileAndStdinMessagesAreStillScanned(_Base):

    def test_dash_F_file_on_disk_is_scanned(self):
        (self.repo / "msg.txt").write_text("fix: thing (#43)\n")
        cmd = LOG_APPEND + "git commit -F msg.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#43", r.stderr)
        self.assertNotIn("#101", r.stderr)

    def test_dash_F_file_on_disk_designed_passes(self):
        self.mark(43)
        (self.repo / "msg.txt").write_text("fix: thing (#43)\n")
        cmd = LOG_APPEND + "git commit --file=msg.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dash_F_file_after_cd_resolves_from_the_new_dir(self):
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / "m.txt").write_text("fix (#43)\n")
        cmd = LOG_APPEND + "cd sub && git commit -F m.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#43", r.stderr)

    def test_dash_F_file_written_by_a_heredoc_in_this_command(self):
        self.mark(41)
        cmd = (LOG_APPEND +
               "cat > m.txt <<'EOF'\nfix (#44)\nEOF\n"
               "git commit -F m.txt")
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#44", r.stderr)
        self.assertNotIn("#101", r.stderr)

    def test_dash_F_stdin_heredoc_is_scanned(self):
        cmd = LOG_APPEND + "git commit -F - <<'EOF'\nfix: thing (#44)\nEOF"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#44", r.stderr)
        self.assertNotIn("#101", r.stderr)

    def test_dash_F_stdin_heredoc_designed_passes(self):
        self.mark(44)
        cmd = LOG_APPEND + "git commit -F - <<'EOF'\nfix: thing (#44)\nEOF"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cat_heredoc_substitution_message_is_scanned(self):
        self.mark(41)
        cmd = (LOG_APPEND +
               "git commit -m \"$(cat <<'EOF'\nfix (#41) and (#45)\nEOF\n)\"")
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#45", r.stderr)
        self.assertNotIn("#101", r.stderr)

    def test_cat_heredoc_substitution_designed_passes(self):
        self.mark(41)
        cmd = (LOG_APPEND +
               "git commit -m \"$(cat <<'EOF'\nfix (#41)\nEOF\n)\"")
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestUnisolatableMessageFallsBackToWholeCommand(_Base):
    """On any doubt the old whole-command scan applies (over-check)."""

    def test_variable_message_falls_back(self):
        self.mark(41)
        cmd = 'MSG="fix (#46)"; git commit -m "$MSG"'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#46", r.stderr)

    def test_file_rewritten_in_this_command_by_printf_falls_back(self):
        # The on-disk copy is STALE (no ref); this command rewrites it first.
        (self.repo / "m.txt").write_text("stale message\n")
        cmd = "printf 'fix (#46)\\n' > m.txt && git commit -F m.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#46", r.stderr)

    def test_unreadable_message_file_falls_back(self):
        cmd = "git commit -F /nonexistent/dir/m.txt -m 'x' ; echo 'fix (#46)'"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#46", r.stderr)

    def test_duplicate_heredoc_delimiter_falls_back(self):
        self.mark(47)
        cmd = ("python3 - <<'EOF'\nprint('#101')\nEOF\n"
               "git commit -F - <<'EOF'\nfix (#47)\nEOF")
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#101", r.stderr)

    def test_editor_commit_with_no_message_flag_falls_back(self):
        cmd = "echo '#46' >> notes.md && git commit"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#46", r.stderr)


class TestTimingBound(_Base):

    def test_large_heredoc_body_stays_fast(self):
        self.mark(41)
        body = "\n".join("- #%d entry %s" % (i, "x" * 200) for i in range(100, 2100))
        cmd = ("cat >> docs/autopilot-log.md <<'EOF'\n" + body + "\nEOF\n"
               'git commit -m "log (#41)"')
        t0 = time.monotonic()
        r = self.run_hook(cmd)
        dt = time.monotonic() - t0
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(dt, 4.0)

    def test_long_unbroken_token_stays_fast(self):
        self.mark(41)
        cmd = "echo " + "a" * 200000 + " >> f && git commit -m 'x (#41)'"
        t0 = time.monotonic()
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 4.0)


if __name__ == "__main__":
    main()
