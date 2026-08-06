"""#195 — `strip_mentions()` dies at 131072 bytes, so a backticked/quoted/
fenced MENTION past that boundary becomes a hard merge-bypass accusation with
no diagnostic.

`strip_mentions()` (hooks/stop-check-prose-violations.sh) used to pass the
WHOLE assistant message to `python3` as a single argv entry
(`python3 - "$1" <<'PYEOF'`). Linux `execve` enforces `MAX_ARG_STRLEN`
(measured: exec succeeds at 131,000 bytes, E2BIG at 140,000 — the exact
threshold is 131072 bytes on this fleet's kernel page size). Above it,
`python3` never starts, and the caller's bare `2>/dev/null || printf` fallback
silently substituted the RAW, UNSTRIPPED message — so a phrase sitting inside
backticks / a fenced code block / a double-quoted span (a MENTION, per #96)
read exactly like a bare, unquoted OFFER to every downstream `msg_has
"$MSG_MENTION" ...` incriminating-pattern check. No note ever reached
`UNDET_FILE`, so the block reason gave the agent zero hint that a derivation
step had failed rather than a genuine violation being found.

Two invariants, verified against the REAL hook (subprocess, no stubbing of
its own logic):

* A message whose ONLY banned-phrase occurrence is a MENTION must not be
  hard-blocked, at ANY size — the fix removes the size ceiling entirely
  rather than moving it further out.
* A genuine BARE, unquoted offer must still block at the same size (the
  positive control — the fix must narrow, never widen, what fires).
* When the derivation step genuinely cannot run (independent of size), the
  fail direction does not invert (still blocks on the raw, unstripped text —
  the established EXONERATING-signal-denied direction), but now the block
  reason carries an "undeterminable" note, per #194's own diagnostic channel.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# The measured threshold from the ticket's own reproduction: 131036 bytes
# never trips it, 131111 does, and it brackets Linux's MAX_ARG_STRLEN exactly.
BELOW_MAX_ARG_STRLEN = 131036
ABOVE_MAX_ARG_STRLEN = 131111
FAR_ABOVE_MAX_ARG_STRLEN = 400000

QUALITY_BYPASS = "Quality-bypass shortcut"


def _padded(head, size, filler="Filler paragraph describing neutral progress "
                              "on the parser refactor today.\n"):
    """head + repeated filler until the UTF-8 byte length reaches `size`."""
    msg = head
    while len(msg.encode("utf-8")) < size:
        msg += filler
    return msg


class _HookCase(unittest.TestCase):

    def _run(self, msg, env=None, session=None):
        sid = session or ("mentsize-%s" % uuid.uuid4().hex[:12])
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, env=env, timeout=60)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"].replace("\\n", "\n")

    def _violations(self, r):
        return [ln.strip()[2:] for ln in self._reason(r).splitlines()
                if ln.startswith("- ")]


# --------------------------------------------------------------------------- #
# Removing the trigger: a MENTION past MAX_ARG_STRLEN must not be blocked,
# and a genuine bare offer at the same size must still block.
# --------------------------------------------------------------------------- #

class TestAMentionPastTheArgvBoundaryIsNeverBlocked(_HookCase):

    def test_the_tickets_own_threshold_pair_no_longer_flips(self):
        """The ticket's own bisection: 131036 bytes never blocked (strip
        already worked below the boundary), 131111 bytes wrongly DID before
        this fix. Both must read the same now — the message never carries a
        bare offer, only a backticked mention."""
        head = ("Status update: the phrase `admin-merge` is now refused at"
                " Stop. Nothing else changed.\n")
        for size in (BELOW_MAX_ARG_STRLEN, ABOVE_MAX_ARG_STRLEN):
            with self.subTest(size=size):
                msg = _padded(head, size)
                r = self._run(msg)
                self.assertFalse(
                    self._blocked(r),
                    "a %d-byte message whose only banned phrase is a"
                    " backticked MENTION was hard-blocked: %s"
                    % (size, self._violations(r)))

    def test_a_backtick_mention_stays_unblocked_far_past_the_boundary(self):
        head = ("Note for the reader: `merge despite the failing check` is"
                " one of the phrases this hook now refuses. Nothing else to"
                " report.\n")
        msg = _padded(head, FAR_ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertFalse(self._blocked(r), self._violations(r))

    def test_a_double_quoted_mention_stays_unblocked_past_the_boundary(self):
        head = ('Documenting the gate: it blocks a message containing'
                ' "merge despite the failing check" anywhere in the text.\n')
        msg = _padded(head, ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertFalse(self._blocked(r), self._violations(r))

    def test_a_fenced_code_block_mention_stays_unblocked_past_the_boundary(self):
        head = ("Example of a banned line, quoted for documentation:\n"
                "```\nI will merge despite the failing check.\n```\n"
                "Nothing else to report.\n")
        msg = _padded(head, ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertFalse(self._blocked(r), self._violations(r))

    def test_a_bare_offer_still_blocks_at_the_same_size(self):
        """Positive control: the fix must narrow what fires, never widen it.
        A genuine unquoted offer, padded to the same size the mention tests
        use, must still be caught."""
        head = "I will merge despite the failing check and fix it later.\n"
        msg = _padded(head, ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertTrue(self._blocked(r), "a real bypass offer stopped"
                        " blocking once the message crossed the argv"
                        " boundary")
        self.assertTrue(any(QUALITY_BYPASS in v for v in self._violations(r)),
                        self._violations(r))

    def test_a_bare_offer_still_blocks_far_past_the_boundary(self):
        head = "I will merge despite the failing check and fix it later.\n"
        msg = _padded(head, FAR_ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertTrue(self._blocked(r), self._violations(r))
        self.assertTrue(any(QUALITY_BYPASS in v for v in self._violations(r)),
                        self._violations(r))

    def test_the_nogoal_mention_path_is_fixed_too(self):
        """MSG_NOGOAL_MENTION (line 232) feeds the design-review gate (@416)
        through the identical strip_mentions()/argv shape — both call sites
        must be fixed, not just the first one."""
        head = ('Recording a decision for later: the design-review gate'
                ' looks for phrases like "does this design look right, ok to'
                ' proceed?" and blocks on them. Nothing else to report.\n')
        msg = _padded(head, ABOVE_MAX_ARG_STRLEN)
        r = self._run(msg)
        self.assertFalse(
            self._blocked(r),
            "the MSG_NOGOAL_MENTION call site still falls back to the raw,"
            " unstripped message past the argv boundary: %s"
            % self._violations(r))


# --------------------------------------------------------------------------- #
# Adversarial-review finding (#195 follow-up): reading the message from a
# FILE must be byte-equivalent to the old argv read — never silently apply
# universal-newline translation.
# --------------------------------------------------------------------------- #

class TestTheFileReadDoesNotTranslateNewlines(_HookCase):
    """`sys.argv[1]` never applied newline translation — argv strings are not
    read through Python's text-mode I/O layer at all. `open(path, "r")`
    DOES, by default (`newline=None`): a lone `\\r` or a `\\r\\n` pair both
    become `\\n`. That silently SPLITS a line grep would otherwise see as
    one — a genuine bare offer whose two halves straddle a lone `\\r` used
    to block (argv preserved the `\\r`, so the phrase stayed on one grep
    "line") and, reading via `open()` with the default `newline=None`,
    stopped blocking (the `\\r` became a `\\n`, splitting the phrase across
    two lines `grep -qE "merge.*despite"` no longer matches as one)."""

    def test_a_bare_offer_split_by_a_lone_cr_still_blocks(self):
        msg = "I will merge\rdespite the failing check.\n"
        r = self._run(msg)
        self.assertTrue(
            self._blocked(r),
            "a lone \\r inside a genuine bare bypass offer silently"
            " unblocked it — the file read translated \\r to \\n and split"
            " the phrase across what grep sees as two lines")
        self.assertTrue(any(QUALITY_BYPASS in v for v in self._violations(r)),
                        self._violations(r))

    def test_a_bare_offer_with_crlf_line_endings_still_blocks(self):
        """CRLF is the more common real-world case (a Windows-authored
        paste). Today's patterns don't anchor `^`/`$` so this one already
        passed even with translation — kept as an explicit control so a
        future anchored pattern is covered too."""
        msg = "I will merge despite the failing check.\r\n"
        r = self._run(msg)
        self.assertTrue(self._blocked(r), self._violations(r))
        self.assertTrue(any(QUALITY_BYPASS in v for v in self._violations(r)),
                        self._violations(r))


# --------------------------------------------------------------------------- #
# A genuine (non-size) strip failure must still fail closed AND leave a note.
# --------------------------------------------------------------------------- #

class TestAGenuineStripFailureRecordsUndeterminable(_HookCase):
    """Forcing strip_mentions() to fail for a reason OTHER than message size
    (python3 unavailable) isolates the derivation-failure path from the
    argv-size fix above. A SHORT message (well under any size boundary) still
    fails to strip when python3 itself cannot run."""

    def _no_python3(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-nopy3-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        stub = d / "python3"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}

    def _python3_fails_on_call(self, n):
        """A python3 stub that fails ONLY its Nth invocation in one hook run
        and delegates to the REAL python3 (via an absolute path, since PATH
        is overridden) on every other call — so exactly ONE of the two
        strip_mentions() call sites (MSG_MENTION is call 1, MSG_NOGOAL_MENTION
        is call 2) fails while the other succeeds normally. Isolates each
        call site's OWN record_undet() from the other's (adversarial-review
        finding: a stub that fails BOTH calls can't tell which call site's
        record_undet() actually fired)."""
        d = Path(tempfile.mkdtemp(prefix="airuleset-py3n-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        counter = d / "count"
        stub = d / "python3"
        stub.write_text(
            "#!/bin/sh\n"
            "N=0\n"
            "[ -f %s ] && N=$(cat %s)\n"
            "N=$((N+1))\n"
            "echo $N > %s\n"
            'if [ "$N" = "%d" ]; then exit 1; fi\n'
            "exec /usr/bin/python3 \"$@\"\n"
            % (counter, counter, counter, n))
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}

    MSG = "I will merge despite the failing check.\n"

    def test_the_fail_direction_does_not_invert(self):
        """A genuine derivation failure must fail CLOSED (the same direction
        as an unresolvable EXONERATING signal) — never silently disarm the
        gate."""
        r = self._run(self.MSG, env=self._no_python3())
        self.assertTrue(
            self._blocked(r),
            "with python3 unavailable strip_mentions() cannot run at all,"
            " and the gate silently stopped firing on a real bare offer")
        self.assertTrue(any(QUALITY_BYPASS in v for v in self._violations(r)),
                        self._violations(r))

    def test_the_block_reason_says_a_check_was_undeterminable(self):
        """The note must travel on the block REASON — this hook's stderr
        never reaches the model (#194's own "companion 2")."""
        r = self._run(self.MSG, env=self._no_python3())
        self.assertIn(
            "undeterminable", self._reason(r).lower(),
            "the model is told it offered a bypass but never that the"
            " mention-strip step itself failed; reason was: %r"
            % self._reason(r)[:400])

    def test_a_clean_message_is_not_falsely_accused_by_the_diagnostic_alone(self):
        """A message with no banned phrase at all must still pass, even with
        python3 unavailable — the derivation failure alone must not
        manufacture a violation out of nothing."""
        r = self._run("Nothing to report; the refactor is done.\n",
                      env=self._no_python3())
        self.assertFalse(self._blocked(r), self._violations(r))

    def test_the_first_call_sites_own_record_undet_is_reached(self):
        """Isolates the MSG_MENTION call site: only the FIRST strip_mentions()
        invocation fails, the second (MSG_NOGOAL_MENTION) succeeds normally.
        Without this call site's OWN record_undet(), the note would depend
        entirely on the second call site failing too."""
        r = self._run(self.MSG, env=self._python3_fails_on_call(1))
        self.assertIn(
            "undeterminable", self._reason(r).lower(),
            "the FIRST strip_mentions() call (MSG_MENTION) failed alone, and"
            " no undeterminable note reached the block reason: %r"
            % self._reason(r)[:400])

    def test_the_second_call_sites_own_record_undet_is_reached(self):
        """Isolates the MSG_NOGOAL_MENTION call site: only the SECOND
        strip_mentions() invocation fails, the first (MSG_MENTION) succeeds
        normally."""
        r = self._run(self.MSG, env=self._python3_fails_on_call(2))
        self.assertIn(
            "undeterminable", self._reason(r).lower(),
            "the SECOND strip_mentions() call (MSG_NOGOAL_MENTION) failed"
            " alone, and no undeterminable note reached the block reason:"
            " %r" % self._reason(r)[:400])

    def test_the_reported_exit_code_is_not_clobbered_by_bookkeeping(self):
        """Self-caught while verifying the review fixes: a first draft built
        the `${#MSG}`-note string as a SEPARATE assignment statement BEFORE
        reading `$?`, so `record_undet` reported "check exited 0" for a
        strip_mentions() call that had genuinely returned 1 — the plain
        assignment's own (successful) exit status silently overwrote `$?`
        between the failed command substitution and the read. `$?` must be
        captured as the FIRST statement inside the fallback block."""
        env = {**os.environ, "TMPDIR": "/nonexistent-airuleset-195-exitcode"}
        r = self._run(self.MSG, env=env)
        self.assertTrue(self._blocked(r), self._violations(r))
        self.assertNotIn(
            "check exited 0", r.stderr,
            "a genuine strip_mentions() failure (mktemp on an unwritable"
            " TMPDIR) was reported as exit code 0 -- $? was clobbered by a"
            " bookkeeping statement before record_undet read it: %r"
            % r.stderr[:400])


if __name__ == "__main__":
    unittest.main()
