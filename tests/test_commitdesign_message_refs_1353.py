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

    def test_echo_append_is_still_over_checked(self):
        # Only a `cat > f <<EOF` heredoc body is provably file content; an
        # echo/printf argument is not proven (review round 2), so it still counts.
        self.mark(41)
        cmd = ("echo '- #101 shipped' >> docs/autopilot-log.md && "
               'git commit -am "log (#41)"')
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#101", r.stderr)

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

    def test_dash_F_file_on_disk_designed_passes(self):
        self.mark(43)
        (self.repo / "msg.txt").write_text("fix: thing (#43)\n")
        cmd = "git commit --file=msg.txt"
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

    def test_dash_F_stdin_heredoc_is_scanned(self):
        cmd = LOG_APPEND + "git commit -F - <<'EOF'\nfix: thing (#44)\nEOF"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#44", r.stderr)

    def test_dash_F_stdin_heredoc_designed_passes(self):
        self.mark(44)
        cmd = "git commit -F - <<'EOF'\nfix: thing (#44)\nEOF"
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


class TestReviewRound1(_Base):
    """The first review's under-check repros: each #42 is in the real message."""

    def test_trailer_value_is_scanned(self):
        self.mark(41)
        r = self.run_hook("git commit -m 'x (#41)' --trailer 'Closes: #42'")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_dev_stdin_message_file_is_never_read_in_the_hook(self):
        r = self.run_hook("printf 'fix (#42)\\n' | git commit -F /dev/stdin")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_file_rewritten_under_another_spelling_is_not_read_stale(self):
        (self.repo / "m.txt").write_text("stale message\n")
        for cmd in ("printf 'fix (#42)\\n' > m.txt && git commit -F ./m.txt",
                    "P=m; printf 'fix (#42)' > $P.txt; git commit -F m.txt",
                    "python3 -c \"open('m'+'.txt','w').write('fix (#42)')\" "
                    "&& git commit -F m.txt"):
            r = self.run_hook(cmd)
            self.assertEqual(r.returncode, 2, cmd + r.stderr)
            self.assertIn("#42", r.stderr, cmd)

    def test_mid_word_hash_does_not_cut_the_rest_of_the_command(self):
        r = self.run_hook("git commit -m v1.2#hotfix -m 'Refs (#42)'")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_cat_heredoc_in_another_dir_is_not_the_message_file(self):
        self.mark(41)
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / "m.txt").write_text("fix (#42)\n")
        cmd = "cat > m.txt <<'EOF'\nfix (#41)\nEOF\ncd sub && git commit -F m.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)

    def test_message_file_next_to_a_log_heredoc_is_read_from_disk(self):
        (self.repo / "msg.txt").write_text("fix (#43)\n")
        cmd = LOG_APPEND + "git add docs && git commit -F msg.txt"
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#43", r.stderr)

    def test_commit_behind_a_wrapper_next_to_a_plain_commit_falls_back(self):
        self.mark(41)
        r = self.run_hook("git commit -m 'a (#41)'; time git commit -m 'b (#42)'")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#42", r.stderr)


class TestReviewRound2(_Base):
    """The second review's repros: text the parsers misread keeps its refs."""

    def _blocks_42(self, cmd):
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, cmd + r.stderr)
        self.assertIn("#42", r.stderr, cmd)

    def test_heredoc_looking_line_inside_a_quoted_message(self):
        self._blocks_42('git commit -m "subject\n\nexplains cat <<EOF\nfix #42\nEOF\nend"')
        self._blocks_42('git commit -m "x\ncat >> f.md <<EOF\n#42\nEOF\n"')

    def test_heredoc_operator_in_a_comment(self):
        self.mark(41)
        self._blocks_42('git commit -m "chore (#41)" # <<true\n'
                        'git commit --allow-empty -m "fix #42"\ntrue')

    def test_commit_spelt_with_git_global_options(self):
        self.mark(41)
        self._blocks_42(LOG_APPEND + 'git commit -m "a (#41)" && '
                        'git -c user.name=x commit --allow-empty -m "fix #42"')
        self._blocks_42(LOG_APPEND + 'git commit -m "a (#41)" && git -C . commit -m "fix #42"')

    def test_line_continuation_and_short_abbreviations(self):
        self.mark(41)
        self._blocks_42(LOG_APPEND + 'git commit -m "subject (#41)" \\\n-m "Refs #42"')
        self._blocks_42(LOG_APPEND + 'git commit -m "a (#41)" --tr "Refs: #42"')
        self._blocks_42(LOG_APPEND + 'git commit -m "a (#41)" --me "fix #42"')

    def test_continuation_into_a_writer_line_is_not_file_content(self):
        self._blocks_42("git commit -F - \\\ncat > f.md <<'EOF'\nfix #42\nEOF")

    def test_git_output_flag_rewrite_of_the_message_file(self):
        (self.repo / "m.txt").write_text("old msg\n")
        self._blocks_42("git log -1 --format=%B --output=m.txt HEAD~3 && "
                        "git commit -F m.txt  # HEAD~3 is the #42 fix")

    def test_written_file_read_back_into_the_message(self):
        for tail in ('git commit -m "$(cat m.txt)"', "git commit -F ./m.txt",
                     "git commit -F - < m.txt", "bash m.txt && git commit -am x",
                     "git commit -t m.txt"):
            self._blocks_42("cat > m.txt <<'EOF'\nfix #42\nEOF\n" + tail)

    def test_written_script_that_commits(self):
        self._blocks_42("cat > s.sh <<'EOF'\ngit commit -m 'fix #42'\nEOF\nbash *.sh && git commit -am x")


class TestReviewRound3(_Base):
    """The third review's repros: every shape it found keeps the body's refs."""

    def _blocks(self, cmd, ref):
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, cmd + r.stderr)
        self.assertIn(ref, r.stderr, cmd)

    def test_respelt_message_file_path(self):
        for tail in ("git commit -F docs/autopilot-log.m''d",
                     "git commit -F docs/auto\\pilot-log.md",
                     'git commit -F "docs/autopilot-log".md',
                     "git commit -F docs/autopilot-log.{md,md}"):
            self._blocks(LOG_APPEND + tail, "#101")

    def test_glob_or_copy_read_into_the_message(self):
        for tail in ("cat docs/*.md > m.txt && git commit -F m.txt",
                     "cp docs/auto* m.txt && git commit -F m.txt"):
            self._blocks(LOG_APPEND + tail, "#101")

    def test_written_script_run_under_a_respelt_name(self):
        self.mark(41)
        self._blocks("cat > s.sh <<'EOF'\ng''it commit --allow-empty -m 'ref (#105)'\nEOF\n"
                     "sh s.s''h && git commit -m 'x (#41)'", "#105")

    def test_unquoted_delimiter_body_is_executable(self):
        self.mark(41)
        self._blocks("cat >> docs/autopilot-log.md <<EOF\n"
                     "$(g''it commit --allow-empty -qm 'ref (#104)')\nEOF\n"
                     "git commit -m 'x (#41)'", "#104")

    def test_files_git_itself_reads(self):
        self.mark(41)
        self._blocks("cat > .git/MERGE_MSG <<'EOF'\nref (#102)\nEOF\ngit commit --no-edit", "#102")
        self._blocks("cat > .git/hooks/prepare-commit-msg <<'EOF'\necho '#108' >> \"$1\"\nEOF\n"
                     "git commit -m 'x (#41)'", "#108")

    def test_symlink_into_git_dir(self):
        (self.repo / "log.md").symlink_to(self.repo / ".git" / "MERGE_MSG")
        self._blocks("cat > log.md <<'EOF'\nref (#102)\nEOF\ngit commit --no-edit", "#102")


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

    def test_many_writers_and_cd_chains_stay_fast(self):
        self.mark(41)
        writers = "".join("cat >> f%d.md <<'EOF'\nx\nEOF\n" % i for i in range(900))
        for cmd in (writers + "git commit -m 'x (#41)'",
                    "cd d && " * 20000 + "git commit -F m.txt -m 'x (#41)'"):
            t0 = time.monotonic()
            r = self.run_hook(cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.monotonic() - t0, 3.0)

    def test_long_unbroken_token_stays_fast(self):
        self.mark(41)
        for cmd in ("echo " + "A" * 400000 + " | base64 -d > f.bin && git commit -m 'x (#41)'",
                    "git commit -m 'x (#41) " + "y" * 300000 + "'"):
            t0 = time.monotonic()
            r = self.run_hook(cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.monotonic() - t0, 3.0)


if __name__ == "__main__":
    main()
