"""Poll-loop detectors must read CONTROL FLOW, not the bytes a command CARRIES
(airuleset #124).

Observed: a `gh issue comment 119 -F body.md` whose heredoc DOCUMENTED poll-loop
shapes tripped `nudge-poll-loop-timeout.sh`. There was no loop. Reproduced three
more times while working this ticket, on `gh issue comment`/`gh issue create`
calls that polled nothing.

Reproduced from the shipped code, the part that is NOT cosmetic: a heredoc write
that merely QUOTES the sanctioned background waiter, with no `gh issue comment`
in the same call, is a hard `exit 2` from `block-ci-poll-repeat.sh` on its second
occurrence in a session (call1 rc=0, call2 rc=2). The waiter body carries
`gh run view`, so the CI signature matches; `cat >` is not in the mutating-action
list, so narrowing 1 does not fire; the body carries `do … sleep … done`, so the
#111 detector matches. A `gh issue comment` in the SAME call is already exempt —
so the reported nudge shape is nudge-only, but the write-only shape blocks.

Root cause: both detectors match a normalised token stream of the WHOLE command
(`tr -c 'A-Za-z0-9_' ' '`), which has no notion of what is executed and what is
merely carried. Same class as #80 (`grep -c` read as a bulk read).

#112 closed "strip all heredoc bodies" won't-fix with numbers, and was right to:
re-measured over 8,107 transcripts / 251,551 Bash commands, of 147 foreground
loop-matches that vanish when EVERY body is stripped, 75 are LIVE (55 are
`cat > x.sh <<EOF … EOF` + running that script in the SAME command, 20 are
interpreter bodies) and only 72 are inert. #112 also named what a correct
classifier would need — "follow the written PATH to a later execution" — and
rejected it as too hard. Within ONE command it is neither hard nor a heuristic,
and that is the fix: strip a body only when its owner is not an interpreter, it
is a plain redirect to a file, and that file is not executed anywhere in the same
command.

EVERY "payload does not fire" assertion here is PAIRED with the same body as bare
control flow, which MUST fire. Without that pin a toothless fixture (one that
never carried the token order at all) would pass for the wrong reason — the
self-referential trap this repo has hit seven tickets running.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOKS = REPO / "hooks"
LIB = HOOKS / "lib-poll-payload.sh"
LIBPY = HOOKS / "lib_poll_payload.py"
NUDGE = HOOKS / "nudge-poll-loop-timeout.sh"
CIBLOCK = HOOKS / "block-ci-poll-repeat.sh"

sys.path.insert(0, str(HOOKS))
import lib_poll_payload as lpp                                 # noqa: E402

# Every fixture is built as DATA here, never read back from a repo file, so no
# test can pass or fail because of prose that happens to sit near it.
RUN = "30326991380"

#  the loop body under test, in its bare control-flow form
BARE_WAITER = (
    'timeout 10800 bash -c \'while :; do\n'
    '  s=$(gh run view %s --json status,conclusion)\n'
    '  case "$s" in completed*) exit 0 ;; esac\n'
    '  sleep 60\n'
    'done\'' % RUN)
BARE_LOCAL = (
    'for i in $(seq 1 36); do\n'
    '  grep -q "Loaded Modules" /tmp/obs.log && break\n'
    '  sleep 5\n'
    'done')


def heredoc(owner, body, delim="XEOF"):
    return "%s <<'%s'\n%s\n%s" % (owner, delim, body, delim)


def payload(command, session="sess-124", timeout_ms=None, agent_id=None):
    """A real PreToolUse(Bash) payload — always via json.dumps."""
    ti = {"command": command}
    if timeout_ms is not None:
        ti["timeout"] = timeout_ms
    d = {"session_id": session, "tool_name": "Bash", "tool_input": ti}
    if agent_id:
        d["agent_id"] = agent_id
    return json.dumps(d)


class _HookRunner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def nudges(self, command, hook=None, **kw):
        """True iff nudge-poll-loop-timeout.sh injects its corrective context.

        `timeout` is left UNSET so the nudge fires iff the loop-shape detector
        matched — the threshold branch is not what this ticket is about.
        """
        out = subprocess.run(
            ["bash", str(hook or NUDGE)], input=payload(command, **kw),
            text=True, capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        # the library's own header credits exactly this assertion with catching
        # the unterminated-heredoc-in-$() bug, and this file exercises the
        # library hardest — a hook that pollutes stderr while exiting 0 is
        # otherwise invisible
        self.assertEqual(out.stderr.strip(), "", "hook must be silent on stderr")
        return "additionalContext" in out.stdout

    def ci_rc(self, command, hook=None, **kw):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        return subprocess.run(
            ["bash", str(hook or CIBLOCK)], input=payload(command, **kw),
            text=True, env=env, capture_output=True, timeout=60)


class WiringTest(_HookRunner):
    def test_the_shared_stripper_exists(self):
        self.assertTrue(LIB.exists(), "hooks/lib-poll-payload.sh missing")

    def test_both_existing_poll_detectors_source_the_shared_stripper(self):
        """One classifier, N consumers — a private copy in any of them is how
        they drift apart. (#119's new guard asserts the same for itself, in its
        own test file, so each commit stays green on its own.)"""
        for h in (NUDGE, CIBLOCK):
            self.assertIn("lib-poll-payload.sh", h.read_text(),
                          "%s must source the shared stripper" % h.name)


class PayloadIsNotControlFlowTest(_HookRunner):
    """Each case asserts BOTH directions on the SAME body."""

    def test_a_heredoc_written_to_a_doc_file_is_not_a_loop(self):
        doc = heredoc("cat > /tmp/body.md", BARE_WAITER)
        self.assertTrue(self.nudges(BARE_WAITER),
                        "control pin: the bare loop MUST still nudge")
        self.assertFalse(self.nudges(doc),
                         "documenting a poll loop is not running one")

    def test_the_reported_gh_issue_comment_shape_is_not_a_loop(self):
        cmd = (heredoc("cat > /tmp/body.md", BARE_LOCAL)
               + "\ngh issue comment 119 -F /tmp/body.md")
        self.assertTrue(self.nudges(BARE_LOCAL),
                        "control pin: the bare loop MUST still nudge")
        self.assertFalse(self.nudges(cmd), "this is the #124 report verbatim")

    def test_an_appended_playbook_entry_is_not_a_loop(self):
        doc = heredoc("cat >> docs/autopilot-log.md", BARE_LOCAL, "MDEOF")
        self.assertTrue(self.nudges(BARE_LOCAL))
        self.assertFalse(self.nudges(doc))

    def test_a_quoted_body_argument_is_not_a_loop(self):
        cmd = 'gh issue comment 119 --body "the shape is: %s"' % (
            BARE_LOCAL.replace('"', "'").replace("\n", " "))
        self.assertFalse(self.nudges(cmd))

    def test_a_heredoc_read_straight_by_a_text_sink_is_not_a_loop(self):
        """`gh-cli-recipes.md`'s own mandated recipe (`-F -` reads the body
        from stdin). None of these commands executes its stdin — unlike
        `ssh host 'bash -s' <<EOF`, which the interpreter check catches
        first."""
        for owner in ("git commit -q -F -",
                      "gh issue comment 119 -F -",
                      "gh pr create -B main --body-file -"):
            cmd = heredoc(owner, BARE_LOCAL, "EOF")
            self.assertTrue(self.nudges(BARE_LOCAL), "control pin")
            self.assertFalse(self.nudges(cmd), owner)

    def test_a_remote_shell_reading_stdin_is_still_live(self):
        """The sink rule must not swallow this: `bash -s` DOES execute its
        stdin, and the interpreter check has to win."""
        self.assertTrue(self.nudges(heredoc("ssh host 'bash -s'",
                                            BARE_LOCAL, "EOF")))
        self.assertTrue(self.nudges(heredoc("psql -f -", BARE_LOCAL, "EOF")))

    def test_a_commit_message_quoting_a_loop_is_not_a_loop(self):
        cmd = ("git commit -m 'fix(hook): a poll loop is `sleep` in a "
               "do..done BODY — %s'" % BARE_LOCAL.replace("\n", " "))
        self.assertFalse(self.nudges(cmd))


class LiveBodiesStillFireTest(_HookRunner):
    """#112's measured majority: most heredoc bodies genuinely execute. All of
    these MUST keep firing, or the fix has deleted real detections."""

    def test_write_then_run_in_the_same_command_still_fires(self):
        cmd = (heredoc("cat > /tmp/poll.sh", BARE_LOCAL, "EOF")
               + "\nbash /tmp/poll.sh")
        self.assertTrue(self.nudges(cmd),
                        "the body is written AND run here — it really waits")

    def test_write_then_chmod_and_exec_in_the_same_command_still_fires(self):
        cmd = (heredoc("cat > /tmp/poll.sh", BARE_LOCAL, "EOF")
               + "\nchmod +x /tmp/poll.sh && /tmp/poll.sh")
        self.assertTrue(self.nudges(cmd))

    def test_write_then_feed_to_a_remote_shell_on_stdin_still_fires(self):
        """Found by the corpus replay, not by hand: this shape names no
        interpreter next to the path and never uses `bash <path>` — it feeds
        the file to a remote shell's STDIN. Three real specimens ran 119s,
        135s and 183s, so treating them as inert would delete the very
        detections the nudge exists for."""
        cmd = (heredoc("cat > /tmp/petromin.sh", BARE_LOCAL, "EOF")
               + "\nsshpass -p x ssh -o ConnectTimeout=600 host 'bash -s'"
                 " < /tmp/petromin.sh 2>&1 | grep -v added")
        self.assertTrue(self.nudges(cmd))

    def test_write_then_scp_and_run_still_fires(self):
        cmd = (heredoc("cat > /tmp/deploy.sh", BARE_LOCAL, "EOF")
               + "\nscp /tmp/deploy.sh host:/tmp/ && ssh host /tmp/deploy.sh")
        self.assertTrue(self.nudges(cmd))

    def test_the_target_reappearing_anywhere_but_a_text_sink_means_live(self):
        """The general rule the three specimens forced: a written file is
        inert only if every LATER mention of it in the shell-visible text is
        the argument of an unambiguous text-file flag."""
        cmd = (heredoc("cat > /tmp/w.sh", BARE_LOCAL, "EOF")
               + "\nnohup /tmp/w.sh &")
        self.assertTrue(self.nudges(cmd))

    def test_a_text_sink_reference_keeps_it_inert(self):
        cmd = (heredoc("cat > /tmp/body.md", BARE_LOCAL, "EOF")
               + "\ngh issue comment 119 --body-file /tmp/body.md")
        self.assertFalse(self.nudges(cmd))

    def test_an_interpreter_heredoc_still_fires(self):
        self.assertTrue(self.nudges(heredoc("python3 -", BARE_LOCAL, "PY")))

    def test_a_remote_shell_heredoc_still_fires(self):
        cmd = heredoc("ssh dev2 'bash -s'", BARE_LOCAL, "EOF")
        self.assertTrue(self.nudges(cmd))

    def test_a_loop_living_entirely_inside_quotes_still_fires(self):
        """The sanctioned background waiter IS a quoted span. Stripping quoted
        spans wholesale would silence the very command the hooks recommend."""
        self.assertTrue(self.nudges(BARE_WAITER))

    def test_a_bash_dash_c_script_still_fires(self):
        cmd = ("bash -c 'until [ -s /tmp/x ]; do sleep 20; done; echo ok'")
        self.assertTrue(self.nudges(cmd))

    def test_a_plain_foreground_poll_still_fires(self):
        self.assertTrue(self.nudges(BARE_LOCAL))


class CiBlockNoLongerBlocksDocumentationTest(_HookRunner):
    """The non-cosmetic half of #124, reproduced from the shipped code."""

    DOC = heredoc("cat > /tmp/body.md", BARE_WAITER, "DEOF")

    def test_repeated_documentation_writes_are_never_blocked(self):
        for i in (1, 2, 3):
            out = self.ci_rc(self.DOC)
            self.assertEqual(
                out.returncode, 0,
                "write #%d of a doc quoting the waiter must not block:\n%s"
                % (i, out.stderr))

    def test_a_real_repeat_ci_poll_is_still_blocked(self):
        """Control pin: the block itself must survive the fix."""
        self.assertEqual(self.ci_rc(BARE_WAITER).returncode, 0)
        out = self.ci_rc(BARE_WAITER)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#118", out.stderr)

    def test_a_doc_write_does_not_consume_the_free_first_loop(self):
        """A stripped command must be OUT of contract entirely — not merely
        allowed — or it burns the one free slot a real wait is owed."""
        self.ci_rc(self.DOC)
        self.ci_rc(self.DOC)
        self.assertEqual(self.ci_rc(BARE_WAITER).returncode, 0,
                         "the real wait's first loop must still be free")


class EditsArePositionalNotByValueTest(_HookRunner):
    """Four ways a value-based edit deleted LIVE control flow. Every one was
    found by adversarial review of the first green implementation, not by the
    suite above — each is reproduced here so it cannot come back. All four fail
    in the same direction: a real, executing loop goes silent, which is the
    worst outcome this classifier can produce."""

    def test_a_flag_value_that_is_also_a_loop_token_keeps_the_loop(self):
        """`grep -F "done"` is the most idiomatic wait-for-a-marker poll there
        is. A global replace of the flag's VALUE deleted the loop's own `done`
        keyword along with it."""
        cmd = 'until grep -F "done" /tmp/build.log; do sleep 30; done'
        self.assertTrue(self.nudges(cmd))

    def test_a_body_flag_value_does_not_delete_the_same_word_elsewhere(self):
        cmd = ('gh pr comment 7 --body "done"; '
               'until curl -sf http://x; do sleep 30; done')
        self.assertTrue(self.nudges(cmd))

    def test_ssh_dash_t_is_not_stripped_because_git_appears_elsewhere(self):
        """`ssh -t '<script>'` EXECUTES its argument remotely. Gating the
        message-flag strip on a WHOLE-COMMAND git/gh test let any unrelated
        `git status` license stripping it — and remote/process waits are the
        LARGEST class this family is meant to catch."""
        cmd = ('git status --short; '
               'ssh dev2 -t "until [ -f /tmp/x ]; do sleep 30; done"')
        self.assertTrue(self.nudges(cmd))
        self.assertTrue(self.nudges(
            'ssh dev2 -t "until [ -f /tmp/x ]; do sleep 30; done"'))

    def test_a_git_commit_message_is_still_stripped(self):
        """Control pin for the case above: the scoping must not disable the
        strip where it is genuinely a message."""
        self.assertFalse(self.nudges(
            "git commit -m 'ran until [ -f x ]; do sleep 30; done'"))

    def test_two_identical_bodies_are_judged_independently(self):
        """Document the script, then write and run it — a natural shape. A
        value-based body strip blanked BOTH from the inert one's verdict."""
        body = BARE_LOCAL
        cmd = (heredoc("cat > /tmp/doc.md", body, "A") + "\n"
               + heredoc("cat > /tmp/run.sh", body, "B") + "\nbash /tmp/run.sh")
        self.assertTrue(self.nudges(cmd),
                        "the second body genuinely runs")

    def test_a_here_string_does_not_swallow_the_rest_of_the_command(self):
        """`<<<'x'` matches the heredoc pattern at offset 1 of `<<<`; no line
        ever equals that delimiter, so the 'body' ran to end of command."""
        cmd = ('cat > out.txt <<<"hello"\n'
               'until curl -sf http://x; do sleep 30; done')
        self.assertTrue(self.nudges(cmd))


class FailOpenWithoutTheLibraryTest(_HookRunner):
    """Under `set -e` a failed `source` exits the hook non-zero, which the
    harness reports as a hook ERROR on every Bash tool call across six boxes.
    Every other dependency here (jq, python3, md5sum, the state dir) is
    fail-open; the library must be too."""

    def test_all_three_hooks_exit_zero_when_the_library_is_missing(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "hooks").mkdir()
        for f in list(HOOKS.glob("*.sh")) + list(HOOKS.glob("*.py")):
            if f.name in (LIB.name, LIBPY.name):
                continue                       # the library is NOT copied
            shutil.copy2(f, d / "hooks" / f.name)
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        env["AIRULESET_LOCALPOLL_STATE_DIR"] = self.state
        for name in ("nudge-poll-loop-timeout.sh", "block-ci-poll-repeat.sh",
                     "block-local-poll-repeat.sh"):
            out = subprocess.run(
                ["bash", str(d / "hooks" / name)],
                input=payload(BARE_LOCAL), text=True, env=env,
                capture_output=True, timeout=60)
            self.assertIn(out.returncode, (0, 2),
                          "%s must not ERROR without the library: rc=%s %s"
                          % (name, out.returncode, out.stderr[:200]))
            self.assertNotIn("No such file", out.stderr, name)


class StripperTeethTest(_HookRunner):
    """These assertions can only be trusted if they FAIL on the tempting wrong
    fixes. Both mutants are built by mutating the REAL shipped stripper, so a
    mutation that stops applying fails loudly rather than certifying nothing."""

    def _mutant_tree(self, transform):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "hooks").mkdir()
        for f in list(HOOKS.glob("*.sh")) + list(HOOKS.glob("*.py")):
            shutil.copy2(f, d / "hooks" / f.name)
        lib = d / "hooks" / LIBPY.name
        src = LIBPY.read_text()
        out = transform(src)
        self.assertNotEqual(
            out, src, "the mutation did not apply to the shipped stripper "
                      "(its shape changed?) — this test would certify nothing")
        lib.write_text(out)
        return d / "hooks" / NUDGE.name

    def test_stripping_every_heredoc_body_lets_a_real_loop_through(self):
        """#112's own rejected proposal, and the broad-exemption failure the
        ticket names: wrap a real loop in a heredoc and it vanishes."""
        # Pinned to the GATE's semantics, not its formatting: the shortest
        # stable fragment that IS the live/inert decision. If the shipped
        # stripper's shape changes, _mutant_tree fails loudly rather than
        # certifying teeth it no longer has.
        mut = self._mutant_tree(lambda s: s.replace(
            "if not is_live(lines[owner_i],", "if True:  #", 1))
        cmd = (heredoc("cat > /tmp/poll.sh", BARE_LOCAL, "EOF")
               + "\nbash /tmp/poll.sh")
        self.assertTrue(self.nudges(cmd), "shipped stripper keeps this")
        self.assertFalse(self.nudges(cmd, hook=mut),
                         "the over-broad mutant must lose it — proving the "
                         "shipped narrowing is what keeps it")

    def test_a_stripper_that_strips_nothing_fails_the_payload_assertions(self):
        # a GENUINE no-op stripper, not the fail-open path: renaming `strip`
        # would only prove that a Python NameError falls back to the original
        # command, which is a different property
        mut = self._mutant_tree(lambda s: s.replace(
            "    for a, b in sorted(flag_spans(res), reverse=True):",
            "    return text\n    for a, b in sorted(flag_spans(res), reverse=True):",
            1))
        doc = heredoc("cat > /tmp/body.md", BARE_WAITER, "DEOF")
        self.assertFalse(self.nudges(doc), "shipped stripper drops this")
        self.assertTrue(self.nudges(doc, hook=mut),
                        "a no-op stripper must reproduce the #124 bug")


class BackslashAndSpliceIdiomDoNotDefeatBlankingTest(unittest.TestCase):
    """#282 (F3 of #280's own adversarial review, pre-existing -- not
    introduced by that fix): both `BODYFLAG`/`MSGFLAG` used a naive
    `(['"])(.*?)\\1` quote-span match with no backslash-awareness and no
    understanding of the shell's `'"'"'` single-quote-splice idiom (the
    standard way to embed a literal `'` inside what is, at the bash
    tokenization level, ONE continuous single-quoted argument). Either
    shape closes the "blanked" span at the FIRST quote character seen,
    leaving everything after it -- a fake `gh`/loop mention embedded in
    the flag's own value -- as live, unblanked text. Direct import, not
    the subprocess hook: this is the shared library both consumers of
    `flag_spans()`/`strip()` depend on, and a unit-level pin is cheaper
    and more precise than driving it through a full hook subprocess."""

    FAKE_LOOP = "for i in 1 2 3; do sleep 5; done"

    def test_an_escaped_quote_does_not_leak_a_fake_loop_past_the_blank(self):
        cmd = 'gh pr comment 7 --body "see \\"the doc\\" then %s"' % self.FAKE_LOOP
        out = lpp.strip(cmd)
        self.assertNotIn("do sleep 5", out,
                         "an escaped quote inside --body must not let the "
                         "fake loop text survive blanking: %r" % out)

    def test_a_single_quote_splice_idiom_does_not_leak_a_fake_loop(self):
        cmd = "gh pr comment 7 --body 'it'\"'\"'s: %s'" % self.FAKE_LOOP
        out = lpp.strip(cmd)
        self.assertNotIn("do sleep 5", out,
                         "the shell splice idiom inside --body must not "
                         "let the fake loop text survive blanking: %r" % out)

    def test_a_message_flag_splice_idiom_also_blanks(self):
        # MSGFLAG (-m/--message/--title/-t) is the sibling regex, gated on
        # GITGH -- `git commit` supplies it.
        cmd = "git commit -m 'it'\"'\"'s: %s'" % self.FAKE_LOOP
        out = lpp.strip(cmd)
        self.assertNotIn("do sleep 5", out,
                         "the splice idiom must also be closed for -m: %r" % out)

    def test_a_message_flag_escaped_quote_also_blanks(self):
        cmd = 'git commit -m "see \\"the doc\\" then %s"' % self.FAKE_LOOP
        out = lpp.strip(cmd)
        self.assertNotIn("do sleep 5", out,
                         "an escaped quote must also be closed for -m: %r" % out)

    def test_a_genuinely_live_loop_outside_any_quoted_value_survives(self):
        # Control pin: the fix must never blank text that is NOT inside a
        # BODYFLAG/MSGFLAG value at all -- only the flag's own value.
        cmd = self.FAKE_LOOP + '; gh pr comment 7 --body "unrelated \\"note\\""'
        out = lpp.strip(cmd)
        self.assertIn("do sleep 5", out,
                      "a real loop living outside the flag value must "
                      "not be touched by the fix: %r" % out)

    def test_a_plain_unescaped_value_is_still_blanked_as_before(self):
        # Regression control: the ordinary, already-covered case must keep
        # working -- this is not a case the fix should ever touch.
        cmd = 'gh pr comment 7 --body "%s"' % self.FAKE_LOOP
        out = lpp.strip(cmd)
        self.assertNotIn("do sleep 5", out, repr(out))


class FailOpenTest(_HookRunner):
    def test_unparseable_payload_is_silent_and_exit_zero(self):
        out = subprocess.run(["bash", str(NUDGE)], input="not json at all",
                             text=True, capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("additionalContext", out.stdout)

    def test_a_command_with_no_loop_at_all_is_silent(self):
        self.assertFalse(self.nudges("gh run list --limit 5"))


if __name__ == "__main__":
    unittest.main()
