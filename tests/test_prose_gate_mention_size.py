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


if __name__ == "__main__":
    unittest.main()
