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
import re as _re
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
            "✅ Hand-off: READY-FOR-REVIEW komentár na #1393 (kiosk) + karta\n"
            # The '✅ Výstup:' content-verification line became MANDATORY for
            # every completion report (montalu3 0 € email incident) — a
            # "genuinely clean" report now carries it by definition.
            "✅ Výstup: kiosk obrazovka zobrazuje meno zamestnanca a čas 07:45\n\n")
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
    only a hook that noticed says so."""

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

    def test_a_failing_grep_never_fabricates_a_missing_field(self):
        """REPLACES `test_a_failing_grep_never_produces_a_hard_block` (#194).

        That test asserted the hook must not block AT ALL when every check
        errors. #194 showed that contract is the fail-OPEN half of this same
        defect: it is satisfied by a hook whose PRESENCE-triggered gates read an
        errored check as "no violation", so `merge --admin` ships with the hook
        reporting success. The assertion is not weakened — it is split by
        polarity and both halves are now required, here and in
        `tests/test_prose_gate_undeterminable.py`:

        * a REQUIRED-FIELD probe that could not be evaluated must NOT become an
          accusation (this test — the half the old assertion was protecting);
        * an INCRIMINATING pattern that could not be evaluated MUST still fire
          (`TestAPresenceGateNeverReadsAnErrorAsNoViolation`).
        """
        r = self._run("## ✅ Work Complete\n\n**Goal:** x\n"
                      "**What changed:** y\n\n✅ DONE: #1 hotové",
                      env=self._grep_stub())
        self.assertEqual(
            [v for v in self._violations(r) if v.startswith("- Missing")], [],
            "accused a correct report of missing a field on evidence the hook"
            " itself could not evaluate: %s" % self._violations(r))


# --------------------------------------------------------------------------- #
# Structural lock — the booby trap itself
# --------------------------------------------------------------------------- #

# Quoted spans are stripped before FLAG matching, so a grep whose PATTERN
# happens to contain `-q` is not misread as a quiet grep (#194 companion 4).
_QUOTED = _re.compile(r"'[^']*'|\"[^\"]*\"")

# A feeder process piping into grep. The variable's spelling is deliberately
# NOT part of the shape: `"$MSG"`, `"${MSG}"` and a bare `$MSG` are the same
# bug, and the original lock only matched the first (#194 companion 4).
_FEEDER_INTO_GREP = _re.compile(r"\b(?:echo|printf|cat)\b[^|]*\|\s*grep\b")

# Every spelling of "this grep will exit before draining stdin": short `-q`
# in any flag cluster, split flags, the long forms, and max-count in all its
# spellings. `-m1` is the one the #190 fix itself recommends, so a lock that
# misses it certifies the very replacement it proposes.
_EARLY_EXIT_FLAG = _re.compile(
    r"(?:^|\s)(?:-[A-Za-z]*q[A-Za-z]*|--quiet|--silent"
    r"|-m\s*[0-9]+|--max-count(?:[= ][0-9]+)?)(?=\s|$)")

# A grep whose OUTPUT is consumed by a reader that exits early has the same
# defect one stage along. `head` was the only one covered; `sed q` (in any of
# its spellings) and an `awk` early exit do it too. Readers that genuinely
# drain (`cut`, `tail`, `tr`, `wc`) are deliberately absent.
#
# Quoted spans are NOT stripped here — unlike the flag check, the early-exit
# signal lives INSIDE the quoted script (`sed -n '1p;q'`). The `q` is required
# to be the sed script's LAST command, so a substitution that merely contains
# the letter (`sed 's/q/x/'`) is not mistaken for one.
_EARLY_EXIT_CONSUMER = _re.compile(
    r"\bgrep\b[^|]*\|\s*(?:head\b"
    r"|sed\b[^|]*\bq\b['\"]?\s*(?:\||\)|$)"
    r"|awk\b[^|]*\bexit\b)")


def _feeds_early_exiting_grep(line):
    """The #190 idiom: a feeder piped into a grep that will not drain stdin."""
    m = _FEEDER_INTO_GREP.search(line)
    if not m:
        return False
    args = _QUOTED.sub(" ", line[m.end():])
    return bool(_EARLY_EXIT_FLAG.search(args))


def _greps_into_early_exiting_consumer(line):
    return bool(_EARLY_EXIT_CONSUMER.search(line))


class TestTheHookNeverPipesIntoAnEarlyExitingGrep(unittest.TestCase):
    """Locks the SHAPE of the bug, never a token the correct fix must contain.

    The fix's own header comment necessarily QUOTES the banned idiom to explain
    it, so comment lines are stripped before matching — the same self-reference
    trap this repo has hit with lock tests three times before. Offending LINE
    NUMBERS are reported rather than dumping the file into the assertion.

    #192 will apply the #190 conversion to 15 more hooks and reuse this lock to
    certify them, so a lock matching ONE spelling would certify all of them
    (#194 companion 4). `TestTheStructuralLockHasTeeth` below proves it does
    not."""

    def _code_lines(self, path):
        out = []
        for n, raw in enumerate(path.read_text().splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            out.append((n, raw))
        return out

    def test_no_feeder_into_early_exiting_grep(self):
        hits = [n for n, ln in self._code_lines(HOOK)
                if _feeds_early_exiting_grep(ln)]
        self.assertEqual(
            hits, [],
            "lines %s still pipe into an early-exiting grep — under `pipefail`"
            " the writer's SIGPIPE (141) becomes the gate's verdict (#190)" % hits)

    def test_no_grep_piped_into_an_early_exiting_consumer(self):
        hits = [n for n, ln in self._code_lines(HOOK)
                if _greps_into_early_exiting_consumer(ln)]
        self.assertEqual(
            hits, [],
            "lines %s pipe grep into a reader that exits early (head / sed q /"
            " awk exit), which SIGPIPEs grep — use `grep -m1` on a here-string"
            " instead (#190, #194)" % hits)

    def test_the_hook_feeds_grep_from_a_here_string_only(self):
        """Stronger than the two locks above, and available because the hook
        now has no feeder pipes at all: a here-string has no writer PROCESS, so
        the race cannot exist regardless of which flags the grep carries."""
        hits = [n for n, ln in self._code_lines(HOOK)
                if _FEEDER_INTO_GREP.search(ln)]
        self.assertEqual(
            hits, [],
            "lines %s feed grep from a process instead of a here-string (#194)"
            % hits)


class TestTheStructuralLockHasTeeth(unittest.TestCase):
    """A lock that matches nothing passes forever. Each evading spelling the
    #194 review enumerated is asserted to be CAUGHT, and each safe shape to be
    left alone — so the lock cannot silently degrade into a no-op."""

    EVADES_FEEDER_LOCK = (
        'X=$(echo "$MSG" | grep -q foo && echo 1 || echo 0)',
        'X=$(echo "$MSG" | grep -i -q foo && echo 1 || echo 0)',      # split flags
        'X=$(echo "$MSG" | grep --quiet foo && echo 1 || echo 0)',    # long flag
        'X=$(echo "$MSG" | grep --silent foo && echo 1 || echo 0)',
        'X=$(echo "${MSG}" | grep -q foo && echo 1 || echo 0)',       # braced
        'X=$(echo $MSG | grep -q foo && echo 1 || echo 0)',           # unquoted
        'X=$(printf "%s" "$MSG" | grep -qiE "foo" && echo 1)',        # printf feeder
        'X=$(echo "$MSG" | grep -m1 foo)',                            # max-count
        'X=$(echo "$MSG" | grep -m 1 foo)',
        'X=$(echo "$MSG" | grep --max-count=1 foo)',
        'X=$(cat "$F" | grep -q foo)',
    )
    SAFE_FOR_FEEDER_LOCK = (
        'grep -qE "pat" <<<"$text"',                     # here-string, no writer
        'X=$(echo "$MSG" | grep -cE "🌐.*https?://")',    # -c drains stdin
        'X=$(printf "%s\\n" "$MSG" | grep -v "^/goal")',  # -v drains stdin
        'X=$(echo "$MSG" | grep -E "^DONE:" | tail -1)',  # -E drains stdin
        'grep -q "q-and-a -q inside a quoted pattern" <<<"$t"',
    )
    EVADES_CONSUMER_LOCK = (
        'L=$(grep -nE "pat" <<<"$MSG" | head -1)',
        'L=$(grep -nE "pat" <<<"$MSG" | sed q)',
        "L=$(grep -nE 'pat' <<<\"$MSG\" | sed -n '1p;q')",
        'L=$(grep -nE "pat" <<<"$MSG" | awk \'NR==1{print;exit}\')',
    )
    SAFE_FOR_CONSUMER_LOCK = (
        'L=$(grep -m1 -nE "pat" <<<"$MSG" | cut -d: -f1)',   # cut drains
        'L=$(grep -E "^DONE:" <<<"$MSG" | tail -1)',         # tail drains
        'N=$(grep -cE "pat" <<<"$MSG" | tr -d " ")',         # tr drains
        'L=$(grep -nE "head of the report" <<<"$MSG")',      # "head" in a pattern
        "echo \"$X\" | sed 's/^/    /' >&2",                 # sed, but no grep
        'L=$(grep -E "pat" <<<"$MSG" | sed \'s/q/Q/\')',     # q is substituted, not a command
    )

    def test_every_evading_spelling_of_the_feeder_idiom_is_caught(self):
        for line in self.EVADES_FEEDER_LOCK:
            with self.subTest(line=line):
                self.assertTrue(_feeds_early_exiting_grep(line),
                                "evading spelling not caught by the lock")

    def test_safe_feeder_shapes_are_not_flagged(self):
        for line in self.SAFE_FOR_FEEDER_LOCK:
            with self.subTest(line=line):
                self.assertFalse(_feeds_early_exiting_grep(line),
                                 "a grep that drains stdin was flagged")

    def test_every_early_exiting_consumer_is_caught(self):
        for line in self.EVADES_CONSUMER_LOCK:
            with self.subTest(line=line):
                self.assertTrue(_greps_into_early_exiting_consumer(line),
                                "early-exiting consumer not caught by the lock")

    def test_draining_consumers_are_not_flagged(self):
        for line in self.SAFE_FOR_CONSUMER_LOCK:
            with self.subTest(line=line):
                self.assertFalse(_greps_into_early_exiting_consumer(line),
                                 "a reader that drains stdin was flagged")


if __name__ == "__main__":
    unittest.main()
