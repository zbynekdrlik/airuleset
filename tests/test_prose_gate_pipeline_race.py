"""#190 — `stop-check-prose-violations.sh` must decide from the MESSAGE, never
from a pipeline race.

Every boolean in that hook was computed as::

    HAS_REVIEW=$(echo "$MSG" | grep -qP '<pattern>' && echo 1 || echo 0)

under `set -euo pipefail`. `grep -q` exits at its FIRST match without draining
stdin, so the `echo` writer takes SIGPIPE, `pipefail` reports the WRITER's 141
instead of grep's verdict, and `&& echo 1 || echo 0` collapses that into `0` —
the value that means "this line is absent". The verdict then depends on process
scheduling rather than on the text.

The ticket reported the LOAD-triggered form (one failure in a 3363-test run,
`rc=141` captured directly under CPU saturation on a ~350-byte message). These
tests use the DETERMINISTIC forcing condition instead, because a probabilistic
test is exactly the thing this ticket exists to remove: once the message exceeds
the 64 KiB pipe buffer the writer can no longer finish in a single `write()`,
and the race becomes certain. Measured against the shipped hook before the fix —
0 false verdicts at 360 B / 4.5 KB / 17 KB, 100% at 70 KB and 140 KB.

Both directions are covered, because the same idiom drives both polarities:

* FAIL-CLOSED — a byte-for-byte correct completion report is BLOCKED (five false
  violations, including "missing canonical heading" on a message whose first
  line is that heading). This is the reported symptom.
* FAIL-OPEN — a banned quality-bypass phrase is NOT blocked. This is the worse
  half: the guard silently does not guard, which is the #181 defect class (a
  guard trusting a QUERY SHAPE — the pipeline exit status — rather than the
  thing being trusted).
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# Comfortably past the 64 KiB pipe buffer, so the writer provably cannot
# complete in one write() and the SIGPIPE is deterministic rather than lucky.
_FILLER_BYTES = 128 * 1024


def _filler(nbytes=_FILLER_BYTES):
    """Neutral narration that matches NO pattern in the hook — its only job is
    to push the message past the pipe buffer."""
    line = "neutral narration line %d, nothing the gate matches\n"
    return "".join(line % i for i in range(nbytes // 50))


class _HookCase(unittest.TestCase):

    def _run(self, msg, env=None):
        sid = "prace-%s" % uuid.uuid4().hex[:12]
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, env=env)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _violations(self, r):
        if not self._blocked(r):
            return []
        reason = json.loads(r.stdout)["reason"].replace("\\n", "\n")
        return [ln.strip() for ln in reason.splitlines() if ln.startswith("- ")]


# --------------------------------------------------------------------------- #
# FAIL-CLOSED — the reported symptom
# --------------------------------------------------------------------------- #

class TestLargeCorrectReportIsNotFalselyBlocked(_HookCase):

    # The fork-shaped Work Complete report from TestProseViolationsPrlessCompletion
    # — the exact fixture the flake was reported against — carrying every line
    # the gate requires.
    HEAD = ("## ✅ Work Complete\n\n"
            "**Audits & deploy:**\n"
            "✅ /plan-check: 3/3 fulfilled\n"
            "✅ /review: clean — 0 🔴 0 🟡 0 🔵\n"
            "✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵\n"
            "✅ Lokálne overenie: testy + lint zelené (fork vetva david/kiosk)\n"
            "✅ Hand-off: READY-FOR-REVIEW komentár na #1393 (kiosk) + karta\n\n")
    TAIL = ("\n---\n\n"
            "**Goal:** Dochádzkový kiosk pre výrobu.\n"
            "**What changed:** Kiosk beží na erp-test-david.\n\n"
            "✅ DONE: #1393 (kiosk) odovzdané na review, nič ďalšie nečaká.")

    def _report(self, filler=""):
        return self.HEAD + filler + self.TAIL

    def test_small_report_is_clean(self):
        """Control: the same report below the pipe buffer has always passed."""
        r = self._run(self._report())
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self._blocked(r), self._violations(r))

    def test_large_report_is_still_clean(self):
        """The report is byte-for-byte correct; only its SIZE changed. Any
        violation here is manufactured by the pipeline, not by the message."""
        r = self._run(self._report(_filler()))
        self.assertEqual(r.returncode, 0)
        self.assertFalse(
            self._blocked(r),
            "a correct report was blocked once it crossed the pipe buffer: %s"
            % self._violations(r))


# --------------------------------------------------------------------------- #
# FAIL-OPEN — the worse half: the guard silently does not guard
# --------------------------------------------------------------------------- #

class TestLargeMessageStillTriggersTheHardGates(_HookCase):

    # (name, message-before-filler, message-after-filler)
    CASES = (
        ("quality-bypass",
         "CI has one red check but I will merge despite the failing check"
         " and fix it in a follow-up.",
         "\n\n✅ DONE: merged."),
        ("subagent-vs-inline",
         "Two execution options: subagent-driven or inline. Which approach"
         " do you want?",
         "\n\n⏳ WORKING: waiting on your pick."),
    )

    def _both_sizes(self, head, tail):
        small = self._run(head + tail)
        large = self._run(head + "\n\n" + _filler() + tail)
        return small, large

    def test_every_hard_gate_survives_a_large_message(self):
        for name, head, tail in self.CASES:
            with self.subTest(gate=name):
                small, large = self._both_sizes(head, tail)
                self.assertTrue(
                    self._blocked(small),
                    "%s: the gate does not fire even on a small message —"
                    " fixture is wrong, not the hook" % name)
                self.assertTrue(
                    self._blocked(large),
                    "%s: the gate SILENTLY DID NOT GUARD once the message"
                    " crossed the pipe buffer" % name)


# --------------------------------------------------------------------------- #
# A grep ERROR is not a verdict
# --------------------------------------------------------------------------- #

class TestGrepErrorIsNotTreatedAsAVerdict(_HookCase):
    """`grep` exits 2 on an ERROR (broken pattern, resource failure), which the
    `&& echo 1 || echo 0` idiom cannot tell from exit 1 (no match). That is the
    same "trust the query shape, not the thing being trusted" defect one layer
    down, so it must at minimum be VISIBLE rather than silently mis-decided.

    Asserting on the diagnostic — not merely on "did not block" — is deliberate:
    with every grep failing, the hook reaches no violation at all and therefore
    does not block for the WRONG reason too. Only a hook that noticed says so."""

    def _grep_stub(self, exit_code=2):
        d = Path(tempfile.mkdtemp(prefix="airuleset-greperr-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        stub = d / "grep"
        stub.write_text("#!/bin/sh\nexit %d\n" % exit_code)
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}

    def test_a_failing_grep_is_reported_not_silently_believed(self):
        r = self._run("## ✅ Work Complete\n\n**Goal:** x\n"
                      "**What changed:** y\n\n✅ DONE: #1 hotové",
                      env=self._grep_stub())
        self.assertEqual(r.returncode, 0)
        self.assertIn("undeterminable", r.stderr.lower(),
                      "a grep that ERRORED was silently taken as 'no match';"
                      " stderr was: %r" % r.stderr[:400])

    def test_a_failing_grep_never_produces_a_hard_block(self):
        r = self._run("## ✅ Work Complete\n\n**Goal:** x\n"
                      "**What changed:** y\n\n✅ DONE: #1 hotové",
                      env=self._grep_stub())
        self.assertFalse(self._blocked(r),
                         "blocked on evidence the hook itself could not evaluate")


# --------------------------------------------------------------------------- #
# Structural lock — the booby trap itself
# --------------------------------------------------------------------------- #

class TestTheHookNeverPipesIntoAnEarlyExitingGrep(unittest.TestCase):
    """Locks the SHAPE of the bug, never a token the correct fix must contain.

    The fix's own header comment necessarily QUOTES the banned idiom to explain
    it, so comment lines are stripped before matching — the same self-reference
    trap this repo has hit with lock tests three times before. Offending LINE
    NUMBERS are reported rather than dumping the file into the assertion."""

    import re as _re
    # `<feeder> "$VAR" | grep …` where grep will exit early: -q (quiet) or -m
    # (max-count). Either way the writer is left with a closed pipe.
    BAD = _re.compile(r'(echo|printf[^|]*)\s+"\$[A-Za-z_]+"\s*\|\s*grep\s+-[A-Za-z]*q')
    # A grep whose output is consumed by an early-exiting reader has the same
    # defect one stage along.
    BAD_HEAD = _re.compile(r'\|\s*grep\s[^|]*\|\s*head\b')

    def _code_lines(self, path):
        out = []
        for n, raw in enumerate(path.read_text().splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            out.append((n, raw))
        return out

    def test_no_echo_into_grep_q(self):
        hits = [n for n, ln in self._code_lines(HOOK) if self.BAD.search(ln)]
        self.assertEqual(
            hits, [],
            "lines %s still pipe into an early-exiting grep — under `pipefail`"
            " the writer's SIGPIPE (141) becomes the gate's verdict (#190)" % hits)

    def test_no_grep_piped_into_head(self):
        hits = [n for n, ln in self._code_lines(HOOK) if self.BAD_HEAD.search(ln)]
        self.assertEqual(
            hits, [],
            "lines %s pipe grep into `head`, which exits early and SIGPIPEs"
            " grep — use `grep -m1` instead (#190)" % hits)


if __name__ == "__main__":
    unittest.main()
