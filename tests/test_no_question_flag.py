"""#539 — the U-bucket acceptance side-branches (doctrine) + the mechanized
#527 delivered-question invariant.

Two layers:

  (a) DOCTRINE — `statusline-vocabulary.md` (#526 block) + `skills/autopilot/
      SKILL.md` Step 1 must NAME the two missing acceptance-automat branches
      (fix-class no-thread close, deferred-thread) and emphasize the
      third-party-wait case in the acceptance context. The ROUTING already
      supports them (`needs-acceptance`+`ops-wait` → W tagged `acceptance`); a
      regression lock pins that so the doctrine's mechanical claim can't drift.

  (b) MECHANIZATION — `core-quals`/`slice-quals --waiting` tags a U member that
      carries NO delivered question `no-question!`. A delivered question is a
      ❓ ping in the question map referencing `#N`, OR a needs-answer/
      needs-decision comment carrying an ask-flow marker. Fail-safe: an
      unreadable map or a failed gh comment fetch tags NOTHING (never a false
      accusation — "nikdy falošný").
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import statusbar  # noqa: E402

SKILL = airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md"
VOCAB = airuleset.REPO_DIR / "modules" / "core" / "statusline-vocabulary.md"


def _labels(*names):
    return [{"name": n} for n in names]


# --------------------------------------------------------------------------- #
# (a) DOCTRINE — the two side-branches are NAMED in both governance surfaces.
# --------------------------------------------------------------------------- #
class DoctrineNamesAcceptanceSideBranches(unittest.TestCase):
    """#539(a): both surfaces must name the fix-class and deferred-thread
    acceptance branches (→ W via supervisor-set `ops-wait` with evidence) and
    the third-party-wait case in the acceptance context. Content-lock with
    TEETH: each distinct operative phrase is asserted, so a partial revert of
    one branch's prose fails."""

    def _norm(self, text):
        return " ".join(text.split())

    def test_skill_md_names_both_side_branches(self):
        t = self._norm(SKILL.read_text(encoding="utf-8")).lower()
        self.assertTrue("fix-class" in t,
                        "SKILL.md must NAME the fix-class acceptance branch (#539)")
        self.assertTrue("no-thread" in t or "no thread" in t,
                        "SKILL.md must name the owner-ruled NO-THREAD close (#539)")
        self.assertTrue("deferred" in t and "go-live" in t,
                        "SKILL.md must name the deliberately-deferred thread "
                        "(go-live) branch (#539)")

    def test_vocab_names_both_side_branches(self):
        t = self._norm(VOCAB.read_text(encoding="utf-8")).lower()
        self.assertTrue("fix-class" in t,
                        "statusline-vocabulary.md must NAME the fix-class branch (#539)")
        self.assertTrue("no-thread" in t or "no thread" in t,
                        "statusline-vocabulary.md must name the no-thread close (#539)")
        self.assertTrue("deferred" in t and "go-live" in t,
                        "statusline-vocabulary.md must name the deferred-thread "
                        "(go-live) branch (#539)")

    def test_doctrine_ties_side_branches_to_ops_wait_evidence(self):
        # The mechanism the doctrine prescribes: supervisor-set ops-wait WITH
        # evidence = W even before any thread is sent. Assert the tie in each
        # file (never auto-labelled — the supervisor sets AND clears it).
        for f in (SKILL, VOCAB):
            t = self._norm(f.read_text(encoding="utf-8")).lower()
            i = t.find("fix-class")
            self.assertGreaterEqual(i, 0)
            window = t[i:i + 700]
            self.assertIn("ops-wait", window,
                          "%s: fix-class branch must tie to ops-wait" % f.name)
            self.assertIn("evidence", window,
                          "%s: fix-class branch must require evidence" % f.name)


# --------------------------------------------------------------------------- #
# (a) ROUTING regression lock — the doctrine's mechanical claim.
# --------------------------------------------------------------------------- #
class FixClassAcceptanceRoutesToW(unittest.TestCase):
    """#539(a): a fix-class OR deferred-thread acceptance ticket carries
    `needs-acceptance` + supervisor-set `ops-wait`, so it routes to W and is
    tagged `acceptance` — the mechanical claim the new doctrine rests on. Green
    with existing routing (#526); this pins it so the doctrine can't silently
    break."""

    def test_fix_class_acceptance_plus_ops_wait_routes_to_W_tagged_acceptance(self):
        rows = {70: {"number": 70, "labels": _labels("needs-acceptance", "ops-wait")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(workable), set())
        self.assertEqual(set(user_waiting), set(),
                         "#539: a fix-class/deferred acceptance parked on an "
                         "external event must NOT sit in U")
        self.assertEqual(set(ops_wait), {70},
                         "#539: needs-acceptance + ops-wait routes to W")
        self.assertEqual(airuleset._ops_wait_reason(
            _labels("needs-acceptance", "ops-wait")), "acceptance",
            "#539: the fix-class/deferred W member is tagged `acceptance`")


# --------------------------------------------------------------------------- #
# (b) MECHANIZATION — pure helpers.
# --------------------------------------------------------------------------- #
class QuestionMapTicketRefs(unittest.TestCase):
    """#539(b): `statusbar.question_map_ticket_refs` returns the SET of #N
    referenced by any ❓ ping, distinguishing ABSENT (empty set) from
    UNREADABLE (None) — the caller's fail-safe depends on it."""

    def _write_map(self, home, content):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(content)

    def test_absent_map_is_empty_set_not_none(self):
        fn = getattr(statusbar, "question_map_ticket_refs", None)
        self.assertIsNotNone(fn, "question_map_ticket_refs must exist (#539)")
        with TemporaryDirectory() as home:
            self.assertEqual(fn(home), set(),
                             "an ABSENT map = readable-empty (never pinged), "
                             "NOT unreadable")

    def test_corrupt_map_is_none(self):
        fn = getattr(statusbar, "question_map_ticket_refs", None)
        self.assertIsNotNone(fn, "question_map_ticket_refs must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, "{ not valid json")
            self.assertIsNone(fn(home),
                              "a CORRUPT map = UNREADABLE = None (fail-safe)")

    def test_valid_map_collects_referenced_numbers(self):
        fn = getattr(statusbar, "question_map_ticket_refs", None)
        self.assertIsNotNone(fn, "question_map_ticket_refs must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps({
                "a": {"cwd": "/x", "block": "otazka k #4 prosim", "ts": 1},
                "b": {"cwd": "/x", "question": "co s #7 a #4?", "ts": 2},
                "c": {"cwd": "/x", "block": "ziadny ticket", "ts": 3},
            }))
            self.assertEqual(fn(home), {4, 7})


class CommentCarriesQuestion(unittest.TestCase):
    """#539(b): `_comment_carries_question` recognizes a real owner-question
    comment by the repo's own ask-flow markers (❓/otázka/NEEDS YOU/ASKED) — NOT
    a bare `?`, which a routine review comment carries incidentally."""

    def test_ask_flow_markers_are_questions(self):
        fn = getattr(airuleset, "_comment_carries_question", None)
        self.assertIsNotNone(fn, "_comment_carries_question must exist (#539)")
        self.assertTrue(fn("❓ NEEDS YOU: schváliš to?"))
        self.assertTrue(fn("**Otázka — projekt X:** ...\n❓ ASKED: co?"))
        self.assertTrue(fn("Máme na teba otázku ohľadom X"))

    def test_routine_comment_is_not_a_question(self):
        fn = getattr(airuleset, "_comment_carries_question", None)
        self.assertIsNotNone(fn, "_comment_carries_question must exist (#539)")
        self.assertFalse(fn("gatekeeper merged, looks good"))
        self.assertFalse(fn("does this look right? yes, shipping"),
                         "a bare `?` in a routine comment is NOT an ask-flow "
                         "question (else the tag is toothless)")
        self.assertFalse(fn(None))
        self.assertFalse(fn(123))


class NoQuestionFlagged(unittest.TestCase):
    """#539(b): `_no_question_flagged` — for each U member, HAS a question if
    map-referenced OR a question comment exists; flag `no-question!` only when
    confidently neither; fail-safe on an unreadable map / gh error."""

    def _write_map(self, home, content):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(content)

    def _rows(self, *nums):
        return {n: {"number": n, "labels": _labels("needs-answer")} for n in nums}

    def test_corrupt_map_tags_nothing(self):
        fn = getattr(airuleset, "_no_question_flagged", None)
        self.assertIsNotNone(fn, "_no_question_flagged must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, "{ corrupt")
            # comment_state_fn would flag everything, but the unreadable map
            # fail-safe wins: tag NOTHING.
            got = fn(self._rows(4, 5), cwd="/x", home=home,
                     comment_state_fn=lambda n, cwd=None: False)
            self.assertEqual(got, set(),
                             "an UNREADABLE map must never produce a warning")

    def test_map_reference_clears_a_member(self):
        fn = getattr(airuleset, "_no_question_flagged", None)
        self.assertIsNotNone(fn, "_no_question_flagged must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps(
                {"a": {"cwd": "/x", "block": "otazka k #4", "ts": 1}}))
            got = fn(self._rows(4, 5), cwd="/x", home=home,
                     comment_state_fn=lambda n, cwd=None: False)
            self.assertEqual(got, {5},
                             "#4 has a map ping → cleared; #5 has neither → flagged")

    def test_gh_failure_never_flags(self):
        fn = getattr(airuleset, "_no_question_flagged", None)
        self.assertIsNotNone(fn, "_no_question_flagged must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps({}))   # empty but readable
            got = fn(self._rows(4), cwd="/x", home=home,
                     comment_state_fn=lambda n, cwd=None: None)
            self.assertEqual(got, set(),
                             "a FAILED gh comment fetch (None) never flags")

    def test_question_comment_clears_a_member(self):
        fn = getattr(airuleset, "_no_question_flagged", None)
        self.assertIsNotNone(fn, "_no_question_flagged must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps({}))
            got = fn(self._rows(4), cwd="/x", home=home,
                     comment_state_fn=lambda n, cwd=None: True)
            self.assertEqual(got, set(),
                             "a question-shaped comment (True) clears the member")

    def test_neither_map_nor_comment_flags(self):
        fn = getattr(airuleset, "_no_question_flagged", None)
        self.assertIsNotNone(fn, "_no_question_flagged must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps({}))
            got = fn(self._rows(4, 5), cwd="/x", home=home,
                     comment_state_fn=lambda n, cwd=None: False)
            self.assertEqual(got, {4, 5},
                             "no map ping AND no question comment → flagged")


# --------------------------------------------------------------------------- #
# (b) MECHANIZATION — `--waiting` render, end-to-end via the CLI subprocess.
# --------------------------------------------------------------------------- #
def _run_quals(subcmd, flag, repo, home, bindir):
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True, cwd=repo,
        env={**os.environ, "HOME": home, "PATH": f"{bindir}:{os.environ['PATH']}"})


# #4 needs-answer, #5 needs-decision — both parked in U.
_TWO_WAITING = json.dumps([
    {"number": 1, "labels": _labels("bug")},
    {"number": 4, "labels": _labels("needs-answer")},
    {"number": 5, "labels": _labels("needs-decision")},
])


class WaitingTagsQuestionLessMember(unittest.TestCase):
    """#539(b): end-to-end `core-quals --waiting` output tags a U member that
    carries no delivered question `no-question!` in its reason column, and
    clears one that DOES (map ping or question comment)."""

    def _fake_gh(self, bindir, comments='{"comments":[]}'):
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            "  *\"issue view\"*\"comments\"*) echo '%s';;\n" % comments +
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % _TWO_WAITING +
            'esac\n')
        gh.chmod(0o755)

    def _seed_questions(self, home, entries):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def _reasons(self, stdout):
        out = {}
        for ln in stdout.splitlines():
            if not ln.strip():
                continue
            f = ln.split("\t")
            if len(f) >= 4:
                out[f[0]] = f[3]
        return out

    def test_question_less_members_are_tagged(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)   # empty comments, no map ping
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            reasons = self._reasons(r.stdout)
            self.assertIn("no-question!", reasons.get("4", ""),
                          "#4 (needs-answer, no ping, no question comment) must "
                          "be tagged no-question!")
            self.assertIn("no-question!", reasons.get("5", ""),
                          "#5 (needs-decision, likewise) must be tagged")
            # the reason keeps its base tag alongside the warning
            self.assertIn("answer", reasons.get("4", ""))

    def test_map_ping_clears_a_member(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            self._seed_questions(home, {
                "p": {"cwd": repo, "block": "otazka k #4", "ts": 1}})
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            reasons = self._reasons(r.stdout)
            self.assertNotIn("no-question!", reasons.get("4", ""),
                             "#4 has a delivered ping (#4 in the map) → NOT tagged")
            self.assertIn("no-question!", reasons.get("5", ""),
                          "#5 still has neither → tagged")

    def test_question_comment_clears_all(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir,
                          comments='{"comments":[{"body":"❓ NEEDS YOU: co s tym?"}]}')
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("no-question!", r.stdout,
                             "every U member has a question comment → none tagged")


if __name__ == "__main__":
    unittest.main()
