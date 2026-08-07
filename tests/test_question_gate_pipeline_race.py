"""#292 — `stop-check-question-quality.sh` must decide from the MESSAGE, never
from a pipeline race.

Every shape check in that hook was computed as::

    if ! printf '%s' "$BLOCK" | grep -qiE '<pattern>'; then VIOLATION=...; fi

under `set -euo pipefail` — the same idiom `stop-check-prose-violations.sh`
had and #190/#194 fixed. `grep -q` exits at its FIRST match without draining
stdin, so the `printf` writer takes SIGPIPE, `pipefail` reports the WRITER's
141 instead of grep's verdict, and `if ! CMD` reads that as "no match" even
though grep genuinely matched. Check 3's briefing-length computation has the
SAME hazard one level up: `printf ... | awk '{ ... exit }'` — the awk
program calls `exit` on its own first bullet/marker line, closing stdin
before draining the rest, with the identical writer-SIGPIPE consequence.

The ticket reported the LOAD-triggered form: `test_verbatim_repeat_of_the_
same_blocked_question_still_passes` (tests/test_notify_question_block.py)
failed under a full-suite run, with a DIFFERENT check firing each time
(reproduced independently on dev1: 1/500 and 1/1000 CPU-saturated runs of
the exact test scenario, both times Check 1 (briefing) false-positived on
a message that plainly opens with `**Otázka — projekt …:**`; isolated to a
standalone `printf | grep -q` micro-benchmark: 1/8000 under load, rc=141 =
SIGPIPE). These tests use the DETERMINISTIC forcing condition instead
(#190's own established recipe, reused verbatim): once the message exceeds
the 64 KiB pipe buffer the writer can no longer finish in a single
`write()`, and the race becomes certain — verified locally: 30/30 rc=141
against the pre-fix pipe form, 10/10 rc=0 (correct) against the here-string
fix, for the SAME >128 KB fixture.
"""

import json
import re as _re
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"

# Comfortably past the 64 KiB pipe buffer, so the writer provably cannot
# complete in one write() and the SIGPIPE is deterministic rather than lucky.
_FILLER_BYTES = 128 * 1024


def _filler(nbytes=_FILLER_BYTES):
    """Neutral narration that matches NO pattern in the hook — its only job
    is to push the message past the pipe buffer."""
    line = "neutral narration line %d, nothing the gate matches\n"
    return "".join(line % i for i in range(nbytes // 50))


class _HookCase(unittest.TestCase):

    def _run(self, msg):
        sid = "qgate-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"]


# --------------------------------------------------------------------------- #
# FAIL-CLOSED — the reported symptom
# --------------------------------------------------------------------------- #

class TestLargeCorrectQuestionIsNotFalselyBlocked(_HookCase):

    # A genuinely template-compliant question — briefing + two bullets + one
    # decision — the exact FULL_REASK_MSG shape from
    # tests/test_notify_question_block.py's own reported flake.
    HEAD = ("**Otázka — projekt airuleset (nástroj na správu Claude Code "
            "pravidiel):** Pracujem na tickete #45 (opravuje sa, ako sa "
            "kladú nezodpovedané otázky) a potrebujem rozhodnutie: má "
            "tento konkrétny beh použiť ultracode na paralelizáciu "
            "práce?\n\n")
    TAIL = ("• Použiť ultracode (odporúčam) — spustí sa paralelný "
            "workflow, rýchlejšie\n"
            "• Zostať pri sekvenčnom postupe — pomalšie, ale jednoduchšie "
            "na sledovanie\n"
            "\n"
            "❓ NEEDS YOU: mám zapnúť ultracode pre tento beh?\n")

    def _padding_options(self, nbytes=_FILLER_BYTES, nlines=30):
        """EXTRA, genuinely valid bullet-option lines — FEW but LONG, never
        many short ones. Two independent size limits are in play, and this
        padding must clear the pipe buffer without tripping either of the
        OTHER ones: (a) Check 3's briefing-length computation excludes a
        bullet line the moment its own awk hits the first one, so a bullet
        never counts as "briefing" regardless of length; (b) the BLOCK
        extraction's own head-anchored search only looks back 40 LINES from
        the marker (`hooks/stop-check-question-quality.sh`'s `for (i = m;
        i >= 1 && i > m - 40; …)`) — MANY short padding lines would push the
        real head past that window and make the paragraph-pull fallback
        (capped at 3 pulls / 600cp) exhaust its own budget on the padding
        alone, silently dropping the head from $BLOCK before Check 1 even
        runs — a genuine, pre-existing extraction-heuristic edge case, not
        the SIGPIPE bug this test targets. Staying at `nlines` comfortably
        under 40, with each line long enough to reach `nbytes` in total,
        avoids that confound entirely."""
        per_line = max(1, nbytes // nlines)
        line_tpl = "• filler option %d — " + ("x" * per_line) + "\n"
        return "".join(line_tpl % i for i in range(nlines))

    def _msg(self, extra_options=""):
        return self.HEAD + extra_options + self.TAIL

    def test_small_question_is_clean(self):
        """Control: the same question below the pipe buffer has always
        passed."""
        r = self._run(self._msg())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self._blocked(r), self._reason(r))

    def test_large_question_is_still_clean(self):
        """The question is byte-for-byte correct; only its SIZE changed
        (many valid extra options — see Check 3's own camera-box
        precedent for why a long OPTIONS list is realistic and never
        counted as the briefing). Any violation here is manufactured by
        the pipeline, not by the message."""
        r = self._run(self._msg(self._padding_options()))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a correct question was blocked once it crossed the pipe "
            "buffer: %s" % self._reason(r))


# --------------------------------------------------------------------------- #
# FAIL-OPEN — the worse half: the guard silently does not guard
# --------------------------------------------------------------------------- #

class TestLargeBadQuestionStillGetsBlocked(_HookCase):

    # A bare marker with NO briefing at all — Check 1 must fire regardless
    # of how much unrelated filler surrounds it.
    def test_no_briefing_still_blocks_when_large(self):
        small = self._run("❓ NEEDS YOU: schváliš merge PR #5?")
        large = self._run(_filler() + "\n\n❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertTrue(
            self._blocked(small),
            "the gate does not fire even on a small message — fixture is "
            "wrong, not the hook: %s" % small.stdout)
        self.assertTrue(
            self._blocked(large),
            "the gate SILENTLY DID NOT GUARD once the message crossed the "
            "pipe buffer")


# --------------------------------------------------------------------------- #
# Structural lock — the booby trap itself
# --------------------------------------------------------------------------- #

# A feeder process piping into an early-exiting grep OR an early-exiting awk
# — either shape SIGPIPEs the writer under `set -o pipefail` (#190's exact
# mechanism, one level up for the awk form: the program calls `exit` itself
# instead of the caller passing `-q`/`-m1`).
_FEEDER = _re.compile(r"\b(?:echo|printf|cat)\b[^|]*\|\s*(grep|awk)\b")

_QUOTED = _re.compile(r"'[^']*'|\"[^\"]*\"")

_EARLY_EXIT_GREP_FLAG = _re.compile(
    r"(?:^|\s)(?:-[A-Za-z]*q[A-Za-z]*|--quiet|--silent"
    r"|-m\s*[0-9]+|--max-count(?:[= ][0-9]+)?)(?=\s|$)")

_AWK_EXIT_RX = _re.compile(r"\bexit\b")
_AWK_END_RX = _re.compile(r"\bEND\b")


def _awk_has_premature_exit(prog):
    """An `exit` inside `END { … }` is SAFE — END only ever runs after awk
    has already consumed all of stdin, so it can never SIGPIPE the writer.
    An `exit` in a bare per-record pattern-action (this hook's OWN BLOCK
    extraction awk at L107 uses ONLY the safe form; Check 3's BRIEF awk at
    L211 uses ONLY the unsafe form) genuinely runs mid-stream. Everything
    before the FIRST `END` keyword (or the whole program, if there is no
    END block at all) is the region that must not contain `exit`."""
    m = _AWK_END_RX.search(prog)
    early = prog[:m.start()] if m else prog
    return bool(_AWK_EXIT_RX.search(early))


# A feeder piped into a MULTI-LINE `awk '...'` program (the shape Check 3's
# BRIEF computation actually uses — the `exit` keyword lives several lines
# BELOW the `| awk '` opener, inside the quoted program body, so a per-LINE
# scan structurally cannot see it). Non-greedy up to the first closing `'`
# — safe here since none of this hook's awk programs contain an escaped
# quote of their own.
_FEEDER_AWK_PROGRAM_RX = _re.compile(
    r"\b(?:echo|printf|cat)\b[^|\n]*\|\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
    r"awk\s+(?:-v\s+\S+\s+)?'((?:[^']|\n)*?)'", _re.M)


def _feeds_early_exiting_reader_per_line(line):
    m = _FEEDER.search(line)
    if not m:
        return False
    kind = m.group(1)
    tail = line[m.end():]
    if kind == "grep":
        args = _QUOTED.sub(" ", tail)
        return bool(_EARLY_EXIT_GREP_FLAG.search(args))
    # awk on the SAME line as the feeder: an `exit` INSIDE the quoted
    # program, OUTSIDE any END block, is the early-exit signal — unlike the
    # grep-flag check, this one is found WITHIN the quotes, so quoted spans
    # are NOT stripped.
    return _awk_has_premature_exit(tail)


def _feeder_awk_exit_line_numbers(text):
    """The feeder LINE number for every multi-line `feeder | awk '...'`
    program whose body has a PREMATURE `exit` — the shape a per-line scan
    misses. An `exit` confined to `END { … }` (this hook's OWN BLOCK
    extraction awk, L107) never reaches here — see
    `_awk_has_premature_exit`."""
    out = []
    for m in _FEEDER_AWK_PROGRAM_RX.finditer(text):
        if _awk_has_premature_exit(m.group(1)):
            out.append(text.count("\n", 0, m.start()) + 1)
    return out


class TestTheHookNeverPipesIntoAnEarlyExitingReader(unittest.TestCase):
    """Locks the SHAPE of the bug, never a token the correct fix must
    contain. The fix's own header comment necessarily QUOTES the banned
    idiom to explain it, so comment lines are stripped before matching —
    the self-reference trap this repo has hit with lock tests before."""

    def _code_lines(self, path):
        out = []
        for n, raw in enumerate(path.read_text().splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            out.append((n, raw))
        return out

    def test_no_feeder_into_an_early_exiting_reader(self):
        text = HOOK.read_text()
        per_line = [n for n, ln in self._code_lines(HOOK)
                    if _feeds_early_exiting_reader_per_line(ln)]
        multi_line = _feeder_awk_exit_line_numbers(text)
        hits = sorted(set(per_line) | set(multi_line))
        self.assertEqual(
            hits, [],
            "lines %s still pipe a writer into an early-exiting grep/awk — "
            "under `pipefail` the writer's SIGPIPE (141) becomes the "
            "gate's verdict (#190, #292)" % hits)


class TestTheStructuralLockHasTeeth(unittest.TestCase):
    """A lock that matches nothing passes forever."""

    EVADES_PER_LINE_LOCK = (
        'if printf "%s\\n" "$MSG" | grep -qiE "pat"; then exit 0; fi',
        'elif printf "%s" "$LAST_LINE" | grep -qE "pat"; then',
        'if ! printf "%s" "$BLOCK" | grep -qiE "pat"; then',
        'if printf "%s" "$BLOCK" | grep -q "(1)"; then',            # bare -q
        'BRIEF=$(printf "%s\\n" "$BLOCK" | awk \'{ exit }\')',      # 1-line awk
        'X=$(echo "$MSG" | grep -qiE "pat")',
        'X=$(cat "$F" | grep -q foo)',
        'X=$(printf "%s" "$BLOCK" | awk \'/x/ { exit }\')',
    )
    SAFE_PER_LINE = (
        'grep -qiE "pat" <<<"$BLOCK"',                    # here-string
        'BRIEF=$(awk \'{ exit }\' <<<"$BLOCK")',           # here-string
        'X=$(echo "$MSG" | grep -cE "pat")',               # -c drains
        'N=$(printf "%s\\n" "$MSG" | grep -nvE "^$")',     # no -q, drains
        'MARKER_RAW=$(printf "%s" "$LAST_LINE" | sed -E "s/.*//")',  # sed
    )

    def test_every_evading_spelling_is_caught(self):
        for line in self.EVADES_PER_LINE_LOCK:
            with self.subTest(line=line):
                self.assertTrue(_feeds_early_exiting_reader_per_line(line),
                                "evading spelling not caught by the lock")

    def test_safe_shapes_are_not_flagged(self):
        for line in self.SAFE_PER_LINE:
            with self.subTest(line=line):
                self.assertFalse(_feeds_early_exiting_reader_per_line(line),
                                 "a reader that drains stdin was flagged")

    def test_the_multi_line_awk_program_shape_is_caught(self):
        """The EXACT real shape (Check 3's own BRIEF computation): the
        feeder line is bare, `exit` lives several lines below it, inside
        the quoted awk program body."""
        text = ("BRIEF=$(printf '%s\\n' \"$BLOCK\" | awk '\n"
                "    /^[[:space:]]*x/ { exit }\n"
                "    { print }')\n")
        self.assertEqual(_feeder_awk_exit_line_numbers(text), [1],
                         "a multi-line feeder|awk program with `exit` in "
                         "its body was not caught")

    def test_a_multi_line_awk_program_with_no_exit_is_not_flagged(self):
        text = ("BRIEF=$(printf '%s\\n' \"$BLOCK\" | awk '\n"
                "    { print }')\n")
        self.assertEqual(_feeder_awk_exit_line_numbers(text), [],
                         "an awk program that genuinely drains stdin (no "
                         "`exit`) was flagged")

    def test_a_multi_line_awk_program_with_exit_only_inside_end_is_not_flagged(self):
        """The REAL, exact shape this hook's OWN BLOCK-extraction awk (L107)
        uses: `NR <= m { L[NR] = $0 }` (no exit — a bare per-record capture
        rule) then `END { ... exit ... exit ... }`. `END` only ever runs
        after awk has consumed ALL of stdin, so it can never SIGPIPE the
        writer — a lock that flagged this would be a FALSE POSITIVE against
        code this ticket's own fix must leave untouched."""
        text = ("BLOCK=$(printf '%s\\n' \"$MSG\" | LC_ALL=C awk -v m=\"$N\" '\n"
                "    NR <= m { L[NR] = $0 }\n"
                "    END {\n"
                "        if (m < 1) exit\n"
                "        print blk\n"
                "        exit\n"
                "    }')\n")
        self.assertEqual(_feeder_awk_exit_line_numbers(text), [],
                         "an awk program whose only `exit` calls live "
                         "inside END (safe — END runs after EOF) was "
                         "wrongly flagged")

    def test_a_multi_line_here_string_fed_awk_is_not_flagged(self):
        """The FIX shape: no feeder pipe at all, so this scan (which only
        matches a `feeder | awk` pipe) correctly has nothing to find."""
        text = ("BRIEF=$(awk '\n"
                "    /^[[:space:]]*x/ { exit }\n"
                "    { print }' <<<\"$BLOCK\")\n")
        self.assertEqual(_feeder_awk_exit_line_numbers(text), [])


if __name__ == "__main__":
    unittest.main()
