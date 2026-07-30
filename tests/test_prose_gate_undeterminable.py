"""#194 — an UNDETERMINABLE check must be resolved at the point the unknown
arises, in the direction that call site declares — never by a downstream
mechanism that discards other checks' verdicts.

#190 replaced a fail-OPEN that depended on process scheduling. The error
handling it shipped alongside re-opened the same hole DETERMINISTICALLY, driven
by message content:

* `msg_has` returned 1 on a grep ERROR — the value meaning "the pattern is NOT
  in the message". That is a fabricated verdict, the same class as #190's own
  `141 -> 0` collapse. For a PRESENCE-triggered gate it means no violation is
  ever added, `HARD_VIOLATIONS` stays empty, and the hook exits clean.
* The suppression at the foot of the hook asked "did ANY check error?" and, on
  yes, discarded EVERY hard violation — including ones decided by a different
  pattern, on a different regex engine, that never errored.

The trigger is reachable from message text alone: the `/review` audit probe was
a `-qP` pattern of the shape `.*A.*B.*C`, whose split-point space is quadratic
in the number of candidate `A`s.

MEASURED on GNU grep 3.11 against the real pattern, so the fixtures below are
sized from data rather than guessed. Filler repeated after a `/review: ` prefix:

    filler     5 KB   30 KB   60 KB   180 KB   600 KB
    "0 RED"    rc=1   rc=1    rc=1    rc=1     rc=1
    "0 U+1F534" rc=1  rc=2    rc=2    rc=2     rc=2     <- backtracking limit
    "a"        rc=1   rc=1    rc=1    rc=1     rc=1

Note that the ticket's own reproducer used `"0 RED"`, which never trips PCRE,
and a heading without the U+2705 — so `IS_COMPLETION` stayed 0 and the audit
block holding the failing check never ran at all. Both are corrected here.

The two polarities need OPPOSITE answers, and the reason is a cost asymmetry:

* INCRIMINATING pattern (a banned phrase): unknown -> assumed PRESENT, the gate
  fires. FAIL CLOSED. Wrong-open ships a merge bypass silently; wrong-closed
  tells the agent to trim the message, which is actionable and also removes the
  trigger, since the trigger is size.
* EXONERATING pattern (a match would SUPPRESS an established violation):
  unknown -> assumed MISSING, the exemption is denied. FAIL CLOSED.
* REQUIRED REPORT FIELD: unknown -> assumed PRESENT, no violation asserted.
  FAIL OPEN, deliberately — #190 measured that "missing canonical heading" on a
  message whose first line IS that heading is an accusation the agent cannot
  act on.
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

RED = "\U0001f534"
YELLOW = "\U0001f7e1"
BLUE = "\U0001f535"

# 5000 is the measured threshold at which the shipped `-qP` review probe exits 2
# (30 KB); 1000 (6 KB) still returns a clean verdict. Both are used, so a test
# that passes because nothing errored is distinguishable from one that passes
# because the error was handled.
_TRIPS_PCRE = 5000
_DOES_NOT_TRIP_PCRE = 1000

# A hard violation decided by a `-qiE` grep that CANNOT hit a PCRE limit — the
# unrelated check whose verdict the suppression used to discard.
BYPASS_LINE = "I will merge despite the failing check.\n"
BYPASS_VIOLATION = "Quality-bypass shortcut"

TAIL = ("plan-check: 3/3 fulfilled\n"
        "requesting-code-review: clean\n"
        "Goal: x\nWhat changed: y\n\n" + BYPASS_LINE)


class _HookCase(unittest.TestCase):

    def _run(self, msg, env=None):
        sid = "undet-%s" % uuid.uuid4().hex[:12]
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, env=env, timeout=300)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"].replace("\\n", "\n")

    def _violations(self, r):
        return [ln.strip()[2:] for ln in self._reason(r).splitlines()
                if ln.startswith("- ")]

    def _grep_stub(self, exit_code=2):
        """Every grep in the hook ERRORS — the resource-failure case, which is
        the only one that reaches gates a bounded PCRE pattern cannot."""
        d = Path(tempfile.mkdtemp(prefix="airuleset-undet-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        stub = d / "grep"
        stub.write_text("#!/bin/sh\nexit %d\n" % exit_code)
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}

    def _report(self, reps, review_line=""):
        """A completion report whose `/review:` line carries `reps` repeated
        counters — message content only, no environment manipulation."""
        return ("## ✅ Work Complete\n\n"
                + "/review: " + ("0 " + RED) * reps + "\n"
                + review_line
                + TAIL)


# --------------------------------------------------------------------------- #
# The reported defect: one errored check discards an unrelated verdict
# --------------------------------------------------------------------------- #

class TestAnErroredCheckNeverSuppressesAnUnrelatedViolation(_HookCase):

    def test_control_the_bypass_gate_fires_when_no_check_errors(self):
        """Fixture check: below the PCRE threshold the gate has always fired.
        Without this, a failure below could mean the fixture is wrong."""
        r = self._run(self._report(_DOES_NOT_TRIP_PCRE))
        self.assertTrue(self._blocked(r),
                        "fixture is wrong, not the hook: the bypass gate does"
                        " not fire even with no check erroring")
        self.assertNotIn("undeterminable", r.stderr.lower())

    def test_a_backtracking_review_probe_does_not_discard_the_bypass_block(self):
        r = self._run(self._report(_TRIPS_PCRE))
        self.assertTrue(
            self._blocked(r),
            "an undeterminable check discarded a hard block decided by a"
            " DIFFERENT pattern on a DIFFERENT regex engine that never errored"
            " (stderr: %r)" % r.stderr[:300])
        self.assertTrue(
            any(BYPASS_VIOLATION in v for v in self._violations(r)),
            "the surviving block is not the quality-bypass one: %s"
            % self._violations(r))

    def test_it_still_holds_far_past_the_threshold(self):
        r = self._run(self._report(_TRIPS_PCRE * 4))
        self.assertTrue(self._blocked(r), self._violations(r))

    def test_a_valid_review_line_is_not_reported_missing(self):
        """The sharpest form: the report carries a byte-correct audit line, and
        the check that reads it is the one that errors."""
        good = "✅ /review: clean — 0 %s 0 %s 0 %s\n" % (RED, YELLOW, BLUE)
        r = self._run(self._report(_TRIPS_PCRE, review_line=good))
        self.assertTrue(self._blocked(r), "the bypass block was discarded")
        self.assertEqual(
            [v for v in self._violations(r) if "/review audit line" in v], [],
            "a report carrying a correct /review audit line was accused of"
            " missing it: %s" % self._violations(r))


# --------------------------------------------------------------------------- #
# Companion 1: a presence-triggered gate must not read an error as "no match"
# --------------------------------------------------------------------------- #

class TestAPresenceGateNeverReadsAnErrorAsNoViolation(_HookCase):
    """With every grep erroring the hook is blind. An INCRIMINATING pattern must
    then be assumed PRESENT (fail closed) — the alternative is a gate that
    silently does not guard, which is #190's own "worse half"."""

    MSG = ("Everything is green apart from one check, so I will merge despite"
           " the failing check and fix it in a follow-up.\n"
           "Could you test it on your end and let me know if it works?\n")

    def test_the_quality_bypass_gate_still_fires(self):
        r = self._run(self.MSG, env=self._grep_stub())
        self.assertTrue(
            self._blocked(r),
            "every check was undeterminable and the hook exited CLEAN — a"
            " merge bypass passes with the hook reporting success")
        self.assertTrue(any(BYPASS_VIOLATION in v for v in self._violations(r)),
                        self._violations(r))

    def test_an_unsubstantiated_exemption_does_not_disarm_a_gate(self):
        """`UNVERIFIED:` EXONERATES the tester-handoff gate. An undeterminable
        exemption check must be assumed MISSING, or an established violation is
        disarmed by evidence the hook never obtained."""
        r = self._run(self.MSG, env=self._grep_stub())
        self.assertTrue(any("Tester-handoff" in v for v in self._violations(r)),
                        "the exemption was granted on evidence the hook could"
                        " not evaluate: %s" % self._violations(r))

    def test_no_required_field_is_reported_missing(self):
        """The opposite polarity, in the same run: a REQUIRED FIELD probe that
        could not be evaluated must NOT become an accusation the agent cannot
        act on. This is the fail-OPEN half, and it must coexist with the
        fail-CLOSED half above."""
        r = self._run("## ✅ Work Complete\n\n**Goal:** x\n"
                      "**What changed:** y\n\n✅ DONE: #1 hotové",
                      env=self._grep_stub())
        self.assertEqual(
            [v for v in self._violations(r) if v.startswith("Missing")], [],
            "a correct report was accused of missing fields on checks the hook"
            " could not evaluate: %s" % self._violations(r))

    def test_the_block_reason_says_a_check_was_undeterminable(self):
        """Companion 2: a non-blocking Stop hook's stderr never reaches the
        model, so the caveat has to travel on the block REASON, which does."""
        r = self._run(self.MSG, env=self._grep_stub())
        self.assertIn("undeterminable", self._reason(r).lower(),
                      "the model is told it offered a bypass but never that a"
                      " check could not be evaluated; reason was: %r"
                      % self._reason(r)[:400])


# --------------------------------------------------------------------------- #
# Companion 3: the mktemp-failure path must not invert the fail direction
# --------------------------------------------------------------------------- #

class TestAnUnwritableTmpdirDoesNotInvertTheFailDirection(_HookCase):
    """`UNDET_FILE` is a DIAGNOSTIC accumulator, not evidence. Whether `mktemp`
    succeeds must therefore change what the operator is TOLD and nothing about
    what the hook DECIDES.

    Before #194 it decided the verdict outright: with `UNDET_FILE` empty the
    suppression's own `[ -n "$UNDET_FILE" ]` guard was false, so the hook
    blocked on exactly the unevaluable evidence the mechanism existed to refuse
    blocking on."""

    UNWRITABLE = "/nonexistent-airuleset-194"

    MSG = ("## ✅ Work Complete\n\n**Goal:** x\n**What changed:** y\n\n"
           "I will merge despite the failing check.\n")

    def _env(self, tmpdir=None):
        env = self._grep_stub()
        if tmpdir:
            env["TMPDIR"] = tmpdir
        return env

    def test_the_verdict_does_not_depend_on_tmpdir(self):
        """The differential IS the invariant: same message, same grep
        behaviour, only `TMPDIR` differs — the decision must be identical.

        Two modes, because they exercise different halves. CONTENT-driven uses
        real greps and the measured PCRE trigger, so exactly ONE check errors
        while the rest return real verdicts — that is the shape that reaches
        the suppression with a non-empty violation list. TOTAL uses the stub."""
        modes = (
            ("content-driven", self._report(_TRIPS_PCRE), lambda t: {
                **os.environ, **({"TMPDIR": t} if t else {})}),
            ("total", self.MSG, self._env),
        )
        for name, msg, envf in modes:
            with self.subTest(mode=name):
                ok = self._run(msg, env=envf(None))
                broken = self._run(msg, env=envf(self.UNWRITABLE))
                self.assertEqual(
                    (self._blocked(ok), self._violations(ok)),
                    (self._blocked(broken), self._violations(broken)),
                    "an unwritable TMPDIR changed the VERDICT, not just the"
                    " diagnostic: writable=%r/%s unwritable=%r/%s"
                    % (self._blocked(ok), self._violations(ok),
                       self._blocked(broken), self._violations(broken)))

    def test_a_correct_report_is_not_falsely_blocked(self):
        """With `mktemp` failing the accumulator is unavailable. That must cost
        the operator a NOTE, never flip a required-field probe into an
        accusation the agent cannot act on."""
        r = self._run("## ✅ Work Complete\n\n**Goal:** x\n"
                      "**What changed:** y\n\n✅ DONE: #1 hotové",
                      env=self._env(self.UNWRITABLE))
        self.assertEqual(
            [v for v in self._violations(r) if v.startswith("Missing")], [],
            "an unwritable TMPDIR turned unevaluable checks into a hard block:"
            " %s" % self._violations(r))

    def test_a_real_violation_still_blocks(self):
        r = self._run("I will merge despite the failing check.\n",
                      env=self._env(self.UNWRITABLE))
        self.assertTrue(self._blocked(r),
                        "an unwritable TMPDIR disarmed the bypass gate")


# --------------------------------------------------------------------------- #
# Remove the trigger, not only the amplifier
# --------------------------------------------------------------------------- #

class TestTheReviewProbeCannotBeDrivenPastPcresLimit(_HookCase):

    def test_repeated_counters_do_not_make_the_probe_undeterminable(self):
        r = self._run(self._report(_TRIPS_PCRE))
        self.assertNotIn(
            "undeterminable", r.stderr.lower(),
            "message content alone still drives the /review probe past PCRE's"
            " backtracking limit — the amplifier was fixed but not the trigger")

    def test_a_correct_audit_line_still_matches(self):
        """The bound must not cost a legitimate line its match — that would be
        the false 'missing /review line' this change exists to prevent."""
        for line in (
            "✅ /review: clean — 0 %s 0 %s 0 %s" % (RED, YELLOW, BLUE),
            "✅ /review: all findings addressed",
            "✅ /review: addressed in commit a1b2c3d",
            "/review: clean, every finding fixed — 0 %s 0 %s 0 %s"
            % (RED, YELLOW, BLUE),
        ):
            with self.subTest(line=line):
                msg = ("## ✅ Work Complete\n\n"
                       "✅ /plan-check: 1/1 fulfilled\n" + line + "\n"
                       "✅ /requesting-code-review: clean — 0 %s 0 %s 0 %s\n"
                       % (RED, YELLOW, BLUE)
                       + "\n---\n\n**Goal:** x\n**What changed:** y\n")
                r = self._run(msg)
                self.assertEqual(
                    [v for v in self._violations(r) if "/review audit line" in v],
                    [], "a legitimate audit line stopped matching: %s"
                    % self._violations(r))


# --------------------------------------------------------------------------- #
# Adversarial-review follow-ups on the #194 fix itself
# --------------------------------------------------------------------------- #

class TestTheReviewProbesBoundAcceptsRealAuditLines(_HookCase):
    """The bound that removed the backtracking TRIGGER must not become a new way
    to accuse a correct report of missing a line it carries.

    The first cut used `.{0,120}` and rejected two ordinary shapes outright.
    Measured against the real pattern, the gap between `/review[: ]` and the
    first counter is 169 chars for a verbose standards sentence and 161 for a
    found-and-fixed summary, so both fell outside — and the accusation reads
    "missing", which the agent cannot act on because nothing names length. The
    twin `requesting-code-review` probe is unbounded ERE (a DFA, so it cannot
    hit a backtracking limit at all), which means the report was blocked on one
    audit line and passed on its identical twin.

    The bound costs nothing: with grep's required-literal pre-filter defeated,
    every candidate from 120 to 1000 answers a 1 MB adversarial line in under
    0.35 s. So it is sized by what real reports look like, not by speed."""

    HEAD = "## ✅ Work Complete\n\n**Audits & deploy:**\n✅ /plan-check: 3/3 fulfilled\n"
    RCR = "✅ /requesting-code-review: clean — 0 %s 0 %s 0 %s\n" % (RED, YELLOW, BLUE)
    TAIL = "\n---\n\n**Goal:** x\n**What changed:** y\n"

    LINES = (
        ("canonical", "✅ /review: clean — 0 %s 0 %s 0 %s" % (RED, YELLOW, BLUE)),
        ("verbose-standards",
         "✅ /review: applied Correctness / Security / Performance / Maintainability"
         " / Style standards across the whole diff and fixed everything found inside"
         " it — 0 %s 0 %s 0 %s" % (RED, YELLOW, BLUE)),
        ("found-and-fixed",
         "✅ /review: 3 %s fixed (null deref in the parser, TOCTOU on the claim file,"
         " off-by-one in the batch cursor), 2 %s fixed (naming, dead branch), 1 %s"
         " fixed (stale comment) — now 0 %s 0 %s 0 %s"
         % (RED, YELLOW, BLUE, RED, YELLOW, BLUE)),
        ("multi-repo",
         "✅ /review: clean across airuleset, camera-box and restreamer after the"
         " shared helper landed in all three — 0 %s 0 %s 0 %s" % (RED, YELLOW, BLUE)),
        ("all-findings", "✅ /review: all findings addressed"),
        ("in-commit", "✅ /review: addressed in commit a1b2c3d"),
    )

    def test_every_real_audit_line_shape_is_accepted(self):
        for name, line in self.LINES:
            with self.subTest(shape=name):
                r = self._run(self.HEAD + line + "\n" + self.RCR + self.TAIL)
                self.assertEqual(
                    [v for v in self._violations(r) if "/review audit line" in v],
                    [], "a real audit line was reported MISSING (%s): %s"
                    % (name, self._violations(r)))

    def test_the_trigger_is_still_removed(self):
        """Control: widening the bound must not re-open the backtracking hole."""
        r = self._run(self._report(_TRIPS_PCRE))
        self.assertNotIn("undeterminable", r.stderr.lower())


class TestASelectorsUnknownWidensScopeInsteadOfBecomingAVerdict(_HookCase):
    """A two-stage probe has a SELECTOR stage and a PATTERN stage. The selector
    is neither incriminating nor exonerating — it only chooses what the real
    pattern reads — so an unanswerable selector must WIDEN the scope and let the
    pattern decide, exactly as the `/goal` filter does by falling back to the
    full message. Resolving it as a verdict about a pattern that was never
    attempted accuses a message that contains no 🌐 line at all, and then quotes
    an empty offending-lines block."""

    def _selective_stub(self, needle):
        """grep that ERRORS only for the one pattern containing `needle`, and is
        otherwise the real grep — so exactly ONE check goes undeterminable."""
        d = Path(tempfile.mkdtemp(prefix="airuleset-sel-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        stub = d / "grep"
        stub.write_text(
            '#!/bin/sh\nfor a in "$@"; do\n'
            '  case "$a" in *%s*) exit 2 ;; esac\n'
            'done\nexec /usr/bin/grep "$@"\n' % needle)
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}

    MSG = "Fixed the typo in the README. Nothing else to report.\n"

    def test_an_unanswerable_globe_selector_does_not_accuse(self):
        r = self._run(self.MSG, env=self._selective_stub("\U0001f310"))
        self.assertEqual(
            [v for v in self._violations(r) if "localhost" in v], [],
            "a message with no 🌐 line at all was accused of pointing one at"
            " localhost, because the SELECTOR could not be evaluated: %s"
            % self._violations(r))

    def test_a_real_localhost_line_still_blocks(self):
        """Control: widening the selector must not disarm the gate."""
        r = self._run("Preview is up.\n\n🌐 Dev: http://localhost:3000\n")
        self.assertTrue(any("localhost" in v for v in self._violations(r)),
                        "the localhost gate stopped firing: %s"
                        % self._violations(r))


if __name__ == "__main__":
    unittest.main()
