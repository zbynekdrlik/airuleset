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


# --------------------------------------------------------------------------- #
# #539 addendum — the THIRD acceptance branch: a bare needs-acceptance waiting
# on the STREAM'S OWN sequenced work belongs in I (chained), not U. The
# partition routes a bare needs-acceptance to U only when a DRAFT was delivered
# (a question-map ping references #N); else it stays workable (I).
# --------------------------------------------------------------------------- #
class PartitionGatesBareAcceptanceOnDraftPresence(unittest.TestCase):
    """#539 addendum: `_partition_workable(rows, acceptance_present=...)` — a
    bare needs-acceptance routes to U only if its number is in the injected
    `acceptance_present` set (a draft was delivered), else to I. `None` (the
    default, every existing unit-test caller) keeps the #526 routing."""

    def test_default_no_signal_keeps_526_bare_acceptance_in_U(self):
        rows = {4: {"number": 4, "labels": _labels("needs-acceptance")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(user_waiting), {4},
                         "#526 default (no signal) keeps a bare needs-acceptance in U")
        self.assertEqual(set(workable), set())

    def test_bare_acceptance_with_draft_stays_in_U(self):
        rows = {4: {"number": 4, "labels": _labels("needs-acceptance")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(
            rows, acceptance_present={4})
        self.assertEqual(set(user_waiting), {4},
                         "a bare needs-acceptance WITH a delivered draft is a "
                         "live owner-approval question → U")

    def test_bare_acceptance_without_draft_routes_to_I(self):
        rows = {4: {"number": 4, "labels": _labels("needs-acceptance")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(
            rows, acceptance_present=set())
        self.assertEqual(set(workable), {4},
                         "a bare needs-acceptance with NO delivered draft is the "
                         "stream's own chained work → I (workable), never U")
        self.assertEqual(set(user_waiting), set())
        self.assertEqual(set(ops_wait), set())

    def test_answer_and_decision_ignore_the_acceptance_gate(self):
        # needs-answer / needs-decision route to U by LABEL — the acceptance
        # gate must NOT move them, even when absent from the present set.
        rows = {
            4: {"number": 4, "labels": _labels("needs-answer")},
            5: {"number": 5, "labels": _labels("needs-decision")},
        }
        workable, user_waiting, ops_wait = airuleset._partition_workable(
            rows, acceptance_present=set())
        self.assertEqual(set(user_waiting), {4, 5},
                         "the acceptance gate is scoped to reason=='acceptance' "
                         "only; answer/decision stay in U by label")

    def test_acceptance_plus_ops_wait_still_routes_to_W(self):
        # ops-wait wins for a needs-acceptance regardless of the acceptance gate.
        rows = {8: {"number": 8, "labels": _labels("needs-acceptance", "ops-wait")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(
            rows, acceptance_present=set())
        self.assertEqual(set(ops_wait), {8},
                         "a SENT acceptance (ops-wait) stays W, not I")


class AcceptancePresentSet(unittest.TestCase):
    """#539 addendum: `_acceptance_present_set(rows, home)` — the set of
    bare-needs-acceptance numbers with a delivered draft (map ping), cheap +
    local (no gh), fail-safe on an unreadable map to ALL bare-acceptance
    (preserve the #526 U default)."""

    def _write_map(self, home, content):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(content)

    def _rows(self):
        return {
            4: {"number": 4, "labels": _labels("needs-acceptance")},
            5: {"number": 5, "labels": _labels("needs-acceptance")},
            6: {"number": 6, "labels": _labels("needs-answer")},        # not acceptance
            8: {"number": 8, "labels": _labels("needs-acceptance", "ops-wait")},  # W
        }

    def test_only_bare_acceptance_with_a_ping_is_present(self):
        fn = getattr(airuleset, "_acceptance_present_set", None)
        self.assertIsNotNone(fn, "_acceptance_present_set must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, json.dumps(
                {"a": {"cwd": "/x", "block": "draft k #4", "ts": 1}}))
            got = fn(self._rows(), home=home)
            self.assertEqual(got, {4},
                             "only bare needs-acceptance #4 (has a ping) is present; "
                             "#5 no ping, #6 not acceptance, #8 is W (ops-wait)")

    def test_unreadable_map_returns_all_bare_acceptance(self):
        fn = getattr(airuleset, "_acceptance_present_set", None)
        self.assertIsNotNone(fn, "_acceptance_present_set must exist (#539)")
        with TemporaryDirectory() as home:
            self._write_map(home, "{ corrupt")
            got = fn(self._rows(), home=home)
            self.assertEqual(got, {4, 5},
                             "unreadable map → ALL bare needs-acceptance present "
                             "(fail-safe to the #526 U default; #8 is W, #6 not "
                             "acceptance)")


class WaitingRoutesChainedAcceptanceOutOfU(unittest.TestCase):
    """#539 addendum, end-to-end: a bare needs-acceptance with NO delivered
    draft is NOT listed by `core-quals --waiting` (it is the stream's chained I
    work) and IS included in `--count`; one WITH a draft ping stays in U."""

    def _fake_gh(self, bindir, rows):
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            '  *"issue view"*"comments"*) echo \'{"comments":[]}\';;\n'
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % rows +
            'esac\n')
        gh.chmod(0o755)

    def _seed(self, home, entries):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def _nums(self, stdout):
        return {ln.split("\t", 1)[0] for ln in stdout.splitlines() if ln.strip()}

    def test_chained_acceptance_is_not_in_waiting_and_is_counted(self):
        rows = json.dumps([
            {"number": 1, "labels": _labels("bug")},
            {"number": 4, "labels": _labels("needs-acceptance")},  # no draft ping
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)   # no question map seeded → #4 has no draft
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertNotIn("4", self._nums(w.stdout),
                             "#4 bare needs-acceptance with no draft is chained I "
                             "work → NOT listed in --waiting (U)")
            c = _run_quals("core-quals", "--count", repo, home, bindir)
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertEqual(c.stdout.strip(), "2",
                             "#1 + #4 are both workable I (the chained acceptance "
                             "counts as this box's own responsibility)")

    def test_presented_acceptance_stays_in_waiting(self):
        rows = json.dumps([
            {"number": 1, "labels": _labels("bug")},
            {"number": 4, "labels": _labels("needs-acceptance")},
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)
            self._seed(home, {"d": {"cwd": repo, "block": "draft na schvalenie #4", "ts": 1}})
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertIn("4", self._nums(w.stdout),
                          "#4 with a delivered draft (ping references #4) is a "
                          "live owner-approval question → listed in --waiting (U)")


class DoctrineNamesChainedAcceptanceBranch(unittest.TestCase):
    """#539 addendum: both surfaces must name the THIRD branch (acceptance
    waiting on the stream's OWN sequenced work → I) and state the owner-UX
    invariant ("otázky na mňa?" never NIE while U > 0)."""

    def _norm(self, f):
        return " ".join(f.read_text(encoding="utf-8").split()).lower()

    def test_skill_names_chained_branch_and_invariant(self):
        # "chained" is absent from both surfaces today, so it has TEETH (a bare
        # pre-existing "sequenced" at SKILL.md:384 must NOT satisfy this).
        t = self._norm(SKILL)
        i = t.find("chained")
        self.assertGreaterEqual(i, 0,
                                "SKILL.md must name the CHAINED (own-sequenced-work) "
                                "acceptance branch (#539)")
        window = t[i:i + 400]
        self.assertIn("acceptance", window,
                      "the chained branch must be in the acceptance context")

    def test_skill_states_the_owner_ux_invariant(self):
        t = self._norm(SKILL)
        self.assertIn("otázky na mňa", t,
                      "SKILL.md must state the owner-UX invariant — "
                      '"otázky na mňa?" never NIE while U > 0 (#539)')

    def test_vocab_names_chained_branch(self):
        t = self._norm(VOCAB)
        i = t.find("chained")
        self.assertGreaterEqual(i, 0,
                                "statusline-vocabulary.md must name the CHAINED "
                                "acceptance branch (#539)")
        self.assertIn("acceptance", t[i:i + 400])


if __name__ == "__main__":
    unittest.main()
