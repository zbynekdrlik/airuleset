"""#136 -- the design-before-code step becomes a mechanical gate, not prose.

`design_gate.py` is the shared module behind three surfaces:
  * `hooks/post-record-design-comment.sh` writes the DELIVERED marker only
    after re-reading the real posted comment back from GitHub.
  * `hooks/block-commit-without-design.sh` blocks an autopilot-worker's
    `git commit` that references an issue with no marker yet.
  * `hooks/subagent-stop-check-design.sh` backstops the case where the
    PreToolUse hook missed.
  * `scripts/measure_design_compliance.py` (Deliverable 1) uses the exact
    same classifier, so the measured baseline and the enforced gate are
    provably the same yardstick.

This file locks the classifier + marker I/O + issue-reference extraction in
isolation. The hooks that WRAP this module have their own behavioural test
files (`test_block_commit_without_design.py`,
`test_post_record_design_comment.py`, `test_subagent_stop_check_design.py`).
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import design_gate as dg                                  # noqa: E402


# --------------------------------------------------------------------------- #
# classifier
# --------------------------------------------------------------------------- #

# A real "one honest paragraph" -- the worker system prompt's own minimum
# depth -- for a scoped fix. Must PASS.
GOOD_SCOPED = (
    "Root cause: the retry loop never reset its backoff counter after a "
    "successful call, so a single blip left every later call throttled for "
    "the rest of the session. Chosen approach: reset the counter on the "
    "first success after any failure, the smallest change that fixes the "
    "observed symptom. Rejected alternative: replacing the whole backoff "
    "strategy with a token bucket -- that is a bigger behavioural change "
    "than this bug needs and would touch call sites this ticket does not."
)

# Slovak-only phrasing, same shape. Must ALSO pass -- the classifier is
# deliberately bilingual, this repo's tickets are written in both.
GOOD_SLOVAK = (
    "Príčina: retry slučka nikdy neresetovala počítadlo odkladu po úspešnom "
    "volaní, takže jeden výpadok pribrzdil všetky ďalšie volania na zvyšok "
    "session. Zvolený prístup: resetovať počítadlo pri prvom úspechu po "
    "zlyhaní, najmenšia zmena, ktorá opravuje pozorovaný symptóm. Zamietnutá "
    "alternatíva: nahradiť celú stratégiu odkladu token bucketom -- to je "
    "väčšia zmena správania, než tento bug potrebuje, a dotkla by sa "
    "volaní mimo rozsahu tohto ticketu."
)


class TestClassifyDesignComment(unittest.TestCase):

    def test_a_real_paragraph_with_all_three_passes(self):
        ok, reason = dg.classify_design_comment(GOOD_SCOPED)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_slovak_phrasing_also_passes(self):
        ok, reason = dg.classify_design_comment(GOOD_SLOVAK)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_design_comment("")
        self.assertFalse(ok)
        self.assertIn("short", reason)

    def test_none_body_fails(self):
        ok, reason = dg.classify_design_comment(None)
        self.assertFalse(ok)

    def test_trivial_chatter_fails_on_length(self):
        for chatter in ("on it", "Working on this now.", "ok", "investigating"):
            ok, reason = dg.classify_design_comment(chatter)
            self.assertFalse(ok, chatter)
            self.assertIn("short", reason)

    def test_long_but_missing_root_cause_fails(self):
        body = GOOD_SCOPED.replace(
            "Root cause: the retry loop never reset its backoff counter "
            "after a successful call, so a single blip left every later "
            "call throttled for the rest of the session. ", "")
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok)
        self.assertIn("root cause", reason)

    def test_long_but_missing_approach_fails(self):
        body = GOOD_SCOPED.replace(
            "Chosen approach: reset the counter on the first success after "
            "any failure, the smallest change that fixes the observed "
            "symptom. ", "")
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok)
        self.assertIn("chosen approach", reason)

    def test_long_but_missing_alternative_fails(self):
        body = GOOD_SCOPED.replace(
            "Rejected alternative: replacing the whole backoff strategy "
            "with a token bucket -- that is a bigger behavioural change "
            "than this bug needs and would touch call sites this ticket "
            "does not.", "and that is the whole fix.")
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok)
        self.assertIn("rejected alternative", reason)

    def test_a_long_but_purely_narrative_comment_fails(self):
        # Real shape of a NON-compliant comment: substantial, but pure
        # status narration with none of the three concepts -- must not
        # accidentally pass just because it is long.
        body = (
            "Spent the last hour digging through the CI logs for this one. "
            "Tried re-running the job twice, checked the runner disk space, "
            "looked at the recent commits touching this file, and grepped "
            "the whole repo for similar patterns. Still not sure what is "
            "going on here, will keep looking and update this ticket once "
            "I have something concrete to report back on the situation."
        )
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok, reason)


# --------------------------------------------------------------------------- #
# #219 -- _CAUSE_RE missed common Slovak root-cause phrasings
# --------------------------------------------------------------------------- #

class TestCauseRegexSlovakSynonyms(unittest.TestCase):
    """#219: two real design comments (on #132 and #137) were rejected by
    `hooks/block-commit-without-design.sh` despite genuinely explaining root
    cause, because they used "koreň"/"zistenie"/"chýbal" instead of
    "príčina"/"dôvod"/"caused by"/"because the"."""

    # ---- positive: each new synonym is recognized -------------------------
    def test_koren_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Koreň/kontext: worker chýbal v dispatch reťazci, lebo telo "
            "skillu sa nikdy nenačíta pre dispatchnutého subagenta."))

    def test_zdroj_problemu_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Zdroj problému je v tom, že marker sa zapisuje pred overením."))

    def test_zdroj_chyby_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Zdroj chyby: retry súbor sa nikdy neodstráni."))

    def test_zistenie_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Zistenie: retry súbor nikdy nezaniká, lebo TTL sa nikdy "
            "nekontroluje."))

    def test_zistil_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Pri debugovaní som zistil, že hook nikdy nezapíše marker."))

    def test_co_sa_stalo_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Čo sa stalo: hook zbadal starý counter a znova ho použil."))

    def test_chybal_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Skill telo chýbalo v kontexte dispatchnutého workera, takže "
            "krok sa nikdy nespustil."))

    def test_nebolo_is_recognized(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Overenie nebolo nikdy zavolané pred zápisom markera."))

    def test_spo_soben_family_still_matches(self):
        # the already-covered "sp[ôo]soben" family (used to phrase "to je
        # spôsobené") must keep matching -- this addition must not narrow it.
        self.assertTrue(dg._CAUSE_RE.search(
            "To je spôsobené tým, že session id sa nikdy neuložil."))

    def test_english_families_still_match(self):
        self.assertTrue(dg._CAUSE_RE.search(
            "Root cause: the retry loop never reset its backoff counter."))

    # ---- negative controls: ordinary Slovak prose must NOT match --------
    def test_zdrojovy_kod_is_not_mistaken_for_zdroj_problemu(self):
        # bare "zdroj" would false-positive on "source CODE" -- unrelated to
        # a cause claim. The qualifier (problému/chyby) exists for exactly
        # this reason.
        self.assertIsNone(dg._CAUSE_RE.search(
            "Pozrel som sa na zdrojový kód a všetko vyzerá v poriadku."))

    def test_ordinary_status_prose_does_not_match(self):
        self.assertIsNone(dg._CAUSE_RE.search(
            "Aktualizoval som dokumentáciu a pridal testy pre nový "
            "endpoint. Všetko funguje podľa očakávania a je pripravené na "
            "review."))

    def test_ordinary_plan_prose_does_not_match(self):
        self.assertIsNone(dg._CAUSE_RE.search(
            "Ďalší krok je nasadiť zmenu na oba stroje a potvrdiť, že "
            "dashboard zobrazuje novú verziu."))

    def test_ordinary_english_status_prose_does_not_match(self):
        self.assertIsNone(dg._CAUSE_RE.search(
            "Updated the documentation and added tests for the new "
            "endpoint. Everything works as expected and is ready for "
            "review."))

    # ---- end-to-end: the real incident shape now passes the classifier ---
    GOOD_SLOVAK_KOREN = (
        "Koreň/kontext: worker chýbal v dispatch reťazci, lebo telo skillu "
        "sa nikdy nenačíta pre dispatchnutého subagenta -- preto krok "
        "fyzicky nemohol prebehnúť. Prístup: presunúť krok do "
        "vždy-načítaného modulu, aby ho subagent skutočne videl. Zamietnutá "
        "alternatíva: ponechať ho v skille a len pridať odkaz naň -- to by "
        "problém nevyriešilo, lebo telo skillu subagentovi stále nedorazí."
    )

    GOOD_SLOVAK_PRECO = (
        "Prečo toto? Autopilot-worker beží ako samostatný subagent a novo "
        "pridaný krok chýbal presne v jeho vlastnom system prompte -- preto "
        "ho nikdy nevidel, aj keď existoval v skille. Prístup: presunúť "
        "krok do agents/autopilot-worker.md, ktoré je subagentov skutočný "
        "system prompt. Zamietnutá alternatíva: len upozorniť naň v "
        "dokumentácii -- to už raz nefungovalo, lebo dokumentácia nie je "
        "súčasťou dispatchnutého kontextu."
    )

    def test_koren_kontext_heading_now_passes_the_full_classifier(self):
        ok, reason = dg.classify_design_comment(self.GOOD_SLOVAK_KOREN)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_preco_toto_shape_now_passes_the_full_classifier(self):
        ok, reason = dg.classify_design_comment(self.GOOD_SLOVAK_PRECO)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")


# --------------------------------------------------------------------------- #
# issue reference extraction
# --------------------------------------------------------------------------- #

class TestIssueRefs(unittest.TestCase):

    def test_parenthetical_ref(self):
        self.assertEqual(dg.issue_refs("fix(hook): thing (#41) [green]"), [41])

    def test_closes_ref(self):
        self.assertEqual(dg.issue_refs("Closes #41"), [41])

    def test_ref_at_start(self):
        self.assertEqual(dg.issue_refs("#41 done"), [41])

    def test_multiple_distinct_refs_in_order(self):
        self.assertEqual(dg.issue_refs("docs: entry for #137/#139"), [137, 139])

    def test_repeated_ref_deduped(self):
        self.assertEqual(dg.issue_refs("(#80) and again (#80)"), [80])

    def test_no_ref_is_empty(self):
        self.assertEqual(dg.issue_refs("chore: tidy imports"), [])

    def test_markdown_header_hash_not_a_ref(self):
        # "## 3 clean" -- the second '#' is preceded by '#', not a real ref.
        self.assertEqual(dg.issue_refs("## 3 clean"), [])

    def test_hash_glued_to_a_letter_is_not_a_ref(self):
        # "C#7" -- '#' preceded by a word char, not start/space/paren.
        self.assertEqual(dg.issue_refs("uses C#7 syntax"), [])

    def test_none_text_is_empty(self):
        self.assertEqual(dg.issue_refs(None), [])

    def test_prose_issue_n_is_deliberately_not_a_ref(self):
        # #122 -- "issue N" prose (no `#`) is the SANCTIONED way to mention a
        # historical/context ticket in a commit message WITHOUT triggering
        # block-commit-without-design.sh's marker requirement for it (the
        # #137-era playbook convention: reserve bare `#N` for the issue(s) a
        # commit's own `Closes #N` trailer names, describe everything else
        # in prose). ISSUE_REF_RE is `#`-anchored by design (see its own
        # comment) -- this locks that "issue N" staying unmatched is
        # INTENTIONAL, not a coverage gap to widen.
        self.assertEqual(dg.issue_refs("docs: notes for issue 122"), [])

    def test_gh_dash_n_is_deliberately_not_a_ref(self):
        # Same family as test_prose_issue_n_is_deliberately_not_a_ref --
        # ISSUE_REF_RE never anchors on "GH-", so this is not a gap either.
        self.assertEqual(dg.issue_refs("see GH-122 for context"), [])


# --------------------------------------------------------------------------- #
# marker I/O
# --------------------------------------------------------------------------- #

class TestMarkerIO(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-designgate-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self._orig_expanduser = os.path.expanduser
        os.environ["HOME"] = str(self.home)

    def test_marker_missing_by_default(self):
        self.assertFalse(dg.marker_exists("airuleset", 999))

    def test_write_then_exists(self):
        dg.write_marker("airuleset", 999, "https://example.invalid/c/1")
        self.assertTrue(dg.marker_exists("airuleset", 999))

    def test_write_then_read_round_trips_url_and_reason(self):
        dg.write_marker("airuleset", 999, "https://example.invalid/c/1", "ok")
        info = dg.read_marker("airuleset", 999)
        self.assertIsNotNone(info)
        self.assertEqual(info["url"], "https://example.invalid/c/1")
        self.assertEqual(info["reason"], "ok")
        self.assertIsInstance(info["ts"], float)

    def test_read_missing_marker_is_none(self):
        self.assertIsNone(dg.read_marker("airuleset", 12345))

    def test_repo_hash_issue_are_distinct_keys(self):
        dg.write_marker("airuleset", 41, "u1")
        self.assertFalse(dg.marker_exists("airuleset", 42))
        self.assertFalse(dg.marker_exists("other-repo", 41))

    def test_no_repo_key_is_never_a_marker(self):
        self.assertFalse(dg.marker_exists("", 41))
        self.assertFalse(dg.marker_exists(None, 41))

    def test_no_issue_is_never_a_marker(self):
        self.assertFalse(dg.marker_exists("airuleset", None))

    def test_marker_path_is_sanitized(self):
        p = dg.marker_path("owner/name", 41)
        self.assertNotIn("/", os.path.basename(p))


# --------------------------------------------------------------------------- #
# #213 -- multi-kind marker I/O (design/validated/reviewed share the same
# machinery, isolated by `kind`)
# --------------------------------------------------------------------------- #

class TestMultiKindMarkerIO(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-designgate-kinds-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        os.environ["HOME"] = str(self.home)

    def test_default_kind_is_design_backward_compatible(self):
        dg.write_marker("airuleset", 999, "u1")
        self.assertTrue(dg.marker_exists("airuleset", 999))
        self.assertTrue(dg.marker_exists("airuleset", 999, "design"))
        self.assertFalse(dg.marker_exists("airuleset", 999, "validated"))

    def test_validated_kind_is_independent_of_design(self):
        dg.write_marker("airuleset", 999, "u1", kind="validated")
        self.assertFalse(dg.marker_exists("airuleset", 999, "design"))
        self.assertTrue(dg.marker_exists("airuleset", 999, "validated"))

    def test_both_kinds_can_coexist_for_the_same_issue(self):
        dg.write_marker("airuleset", 999, "u1", kind="design")
        dg.write_marker("airuleset", 999, "u2", kind="validated")
        self.assertTrue(dg.marker_exists("airuleset", 999, "design"))
        self.assertTrue(dg.marker_exists("airuleset", 999, "validated"))
        d = dg.read_marker("airuleset", 999, "design")
        v = dg.read_marker("airuleset", 999, "validated")
        self.assertEqual(d["url"], "u1")
        self.assertEqual(v["url"], "u2")

    def test_marker_path_differs_by_kind(self):
        self.assertNotEqual(
            dg.marker_path("airuleset", 41, "design"),
            dg.marker_path("airuleset", 41, "validated"))

    def test_all_kinds_includes_design_and_validated(self):
        self.assertIn("design", dg.ALL_KINDS)
        self.assertIn("validated", dg.ALL_KINDS)

    def test_all_kinds_includes_reviewed(self):
        # #214 -- reviewed joins design/validated in the same marker family.
        self.assertIn("reviewed", dg.ALL_KINDS)

    def test_reviewed_kind_is_independent_of_the_other_two(self):
        dg.write_marker("airuleset", 999, "u1", kind="reviewed")
        self.assertFalse(dg.marker_exists("airuleset", 999, "design"))
        self.assertFalse(dg.marker_exists("airuleset", 999, "validated"))
        self.assertTrue(dg.marker_exists("airuleset", 999, "reviewed"))


# --------------------------------------------------------------------------- #
# #213 -- validation classifier
# --------------------------------------------------------------------------- #

GOOD_VALIDATION = (
    "Reproduced the bug live against current HEAD: ran the failing curl "
    "request against the staging endpoint and confirmed the retry loop "
    "still resets the counter improperly -- the test still fails on this "
    "exact code path, so the issue is still valid and real today."
)

GOOD_VALIDATION_SLOVAK = (
    "Overil som naživo oproti aktuálnemu kódu -- spustil som ten istý "
    "test a potvrdil som, že chyba je stále platná a reprodukuje sa "
    "presne tak, ako píše ticket."
)

GOOD_OBSOLETE_VALIDATION = (
    "Checked the current code and confirmed this is already fixed -- "
    "the retry counter was reset in a merged PR three weeks ago, so the "
    "bug described here is now obsolete and no longer happens."
)


class TestClassifyValidationComment(unittest.TestCase):

    def test_a_real_reproduction_passes(self):
        ok, reason = dg.classify_validation_comment(GOOD_VALIDATION)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_slovak_phrasing_also_passes(self):
        ok, reason = dg.classify_validation_comment(GOOD_VALIDATION_SLOVAK)
        self.assertTrue(ok, reason)

    def test_an_obsolete_finding_also_passes(self):
        ok, reason = dg.classify_validation_comment(GOOD_OBSOLETE_VALIDATION)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_validation_comment("")
        self.assertFalse(ok)
        self.assertIn("short", reason)

    def test_none_body_fails(self):
        ok, reason = dg.classify_validation_comment(None)
        self.assertFalse(ok)

    def test_trivial_chatter_fails_on_length(self):
        for chatter in ("on it", "checking now", "ok", "will look"):
            ok, reason = dg.classify_validation_comment(chatter)
            self.assertFalse(ok, chatter)
            self.assertIn("short", reason)

    def test_long_but_no_action_fails(self):
        body = (
            "This ticket describes a retry loop that resets its backoff "
            "counter incorrectly, which throttles later calls for the "
            "rest of the session. The behaviour is still valid and still "
            "happens today on the current code, and the reported symptom "
            "matches what the test suite currently shows for this path."
        )
        ok, reason = dg.classify_validation_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("validation action", reason)

    def test_long_but_no_evidence_fails(self):
        body = (
            "Ran the failing curl request against the staging endpoint "
            "and reproduced the exact request from the ticket, checked "
            "the response headers and the timing, tried it three more "
            "times against different accounts to be sure it was not a "
            "fluke, and looked at the surrounding logs for context."
        )
        ok, reason = dg.classify_validation_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("validation evidence", reason)

    def test_a_long_purely_narrative_comment_fails(self):
        body = (
            "Spent the last hour poking around the dashboard, clicking "
            "through several menus and trying a few different accounts "
            "to see what would happen, then read through some of the "
            "surrounding code to get a feel for how the module is "
            "organized before deciding what to do about this ticket."
        )
        ok, reason = dg.classify_validation_comment(body)
        self.assertFalse(ok, reason)


# --------------------------------------------------------------------------- #
# #214 -- review classifier
# --------------------------------------------------------------------------- #

GOOD_REVIEW = (
    "Ran /review and requesting-code-review over the diff -- both came "
    "back 0 🔴 0 🟡 0 🔵 after fixing the one missing null check the first "
    "pass found, landed in commit 1234567abcdef on top of the feature."
)

GOOD_REVIEW_SLOVAK = (
    "Spravil som code review (requesting-code-review) diffu -- nasiel "
    "jeden nález (chýbajúca kontrola null), opravené v commite "
    "fedcba7654321, potom uz bolo cisto."
)


class TestClassifyReviewComment(unittest.TestCase):

    def test_a_real_review_pass_passes(self):
        ok, reason = dg.classify_review_comment(GOOD_REVIEW)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_slovak_phrasing_also_passes(self):
        ok, reason = dg.classify_review_comment(GOOD_REVIEW_SLOVAK)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_review_comment("")
        self.assertFalse(ok)
        self.assertIn("short", reason)

    def test_none_body_fails(self):
        ok, reason = dg.classify_review_comment(None)
        self.assertFalse(ok)

    def test_trivial_chatter_fails_on_length(self):
        for chatter in ("on it", "reviewing now", "ok", "will check"):
            ok, reason = dg.classify_review_comment(chatter)
            self.assertFalse(ok, chatter)
            self.assertIn("short", reason)

    def test_long_but_no_review_action_fails(self):
        body = (
            "The change to the retry loop looks correct to me -- the "
            "counter resets on the first success after any failure, "
            "which matches what the ticket asked for, and there is a "
            "test covering the new behaviour so it should hold up fine "
            "in the field without any further changes being needed."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("review action", reason)

    def test_long_but_no_result_fails(self):
        body = (
            "Ran /review over the diff and also went through it with "
            "requesting-code-review to double check the logic once more, "
            "reading every changed line carefully and comparing it "
            "against the original ticket description before deciding "
            "the change was worth landing as it stands right now."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("findings/fix evidence", reason)

    def test_a_long_purely_narrative_review_comment_fails(self):
        body = (
            "Took a look at the diff for a while, scrolled through the "
            "whole file a couple of times and compared it against the "
            "old version, then read the ticket again to remind myself "
            "what it was originally asking for before moving on to the "
            "next item on the list without writing anything down."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertFalse(ok, reason)

    def test_a_bare_decimal_number_is_not_mistaken_for_a_fixing_commit(self):
        # Adversarial-review finding: the bare hex-run alternative matched
        # ANY 7+ digit decimal number too (digits are a subset of hex
        # chars), so an unrelated build/date number satisfied the result
        # half of the classifier with zero real findings/fix evidence.
        body = (
            "I reviewed the current implementation carefully and "
            "confirmed the behavior looks correct. The build number as "
            "of today is 20260801, everything checks out fine overall."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("findings/fix evidence", reason)

    def test_a_hex_looking_english_word_is_not_mistaken_for_a_sha(self):
        # "defaced"/"effaced" are real English words made entirely of a-f
        # letters at >=7 chars -- must not count as a fixing commit sha on
        # their own, with no commit/sha/fix keyword anywhere nearby.
        body = (
            "Reviewed the diff and it honestly looks a bit defaced "
            "somehow in the rendered output, not entirely sure why, will "
            "need to dig into it further before saying anything more."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("findings/fix evidence", reason)

    def test_a_sha_actually_attached_to_a_fix_keyword_still_passes(self):
        body = (
            "Ran /review over the diff and found one real issue -- fixed "
            "it in commit 1234567abcdef, then everything was clean on the "
            "second pass through the same file."
        )
        ok, reason = dg.classify_review_comment(body)
        self.assertTrue(ok, reason)


# --------------------------------------------------------------------------- #
# #206 -- required_refs(): drop already-CLOSED issue refs from the required
# set (a closed ticket is very unlikely to be "the ticket I'm designing for
# right now" -- see hooks/block-commit-without-design.sh for the full story).
# --------------------------------------------------------------------------- #

class TestRequiredRefs(unittest.TestCase):

    def test_closed_ref_is_dropped(self):
        out = dg.required_refs([1734], "/some/cwd", state_of=lambda n, cwd: "CLOSED")
        self.assertEqual(out, [])

    def test_open_ref_is_kept(self):
        out = dg.required_refs([41], "/some/cwd", state_of=lambda n, cwd: "OPEN")
        self.assertEqual(out, [41])

    def test_unmeasurable_ref_is_kept_fail_toward_required(self):
        # never guess an issue is safe to skip -- unknown state -> still
        # required, exactly the pre-#206 unconditional behaviour.
        out = dg.required_refs([41], "/some/cwd", state_of=lambda n, cwd: None)
        self.assertEqual(out, [41])

    def test_mixed_refs_only_open_ones_survive(self):
        closed = {1734, 1766}
        out = dg.required_refs(
            [42, 1734, 1766], "/some/cwd",
            state_of=lambda n, cwd: "CLOSED" if n in closed else "OPEN")
        self.assertEqual(out, [42])

    def test_empty_refs_is_empty(self):
        self.assertEqual(dg.required_refs([], "/some/cwd"), [])

    def test_state_of_receives_the_number_and_cwd(self):
        seen = []

        def spy(n, cwd):
            seen.append((n, cwd))
            return "OPEN"

        dg.required_refs([41, 42], "/repo/dir", state_of=spy)
        self.assertEqual(seen, [(41, "/repo/dir"), (42, "/repo/dir")])

    def test_default_state_of_is_gh_issue_state(self):
        # no state_of passed -> falls back to the real gh-backed resolver,
        # never silently no-ops.
        import inspect
        src = inspect.getsource(dg.required_refs)
        self.assertIn("_gh_issue_state", src)


class TestGhIssueState(unittest.TestCase):
    """`_gh_issue_state` never raises and never guesses -- any failure
    (missing gh, timeout, bad JSON, unexpected value) is None (unmeasurable),
    never a fabricated OPEN or CLOSED."""

    def _fake_run(self, returncode=0, stdout="OPEN\n"):
        import unittest.mock as m
        return m.patch.object(
            dg.subprocess, "run",
            return_value=m.Mock(returncode=returncode, stdout=stdout))

    def test_open_state_parses(self):
        with self._fake_run(stdout="OPEN\n"):
            self.assertEqual(dg._gh_issue_state(41, "/cwd"), "OPEN")

    def test_closed_state_parses(self):
        with self._fake_run(stdout="CLOSED\n"):
            self.assertEqual(dg._gh_issue_state(41, "/cwd"), "CLOSED")

    def test_lowercase_is_normalized(self):
        with self._fake_run(stdout="closed\n"):
            self.assertEqual(dg._gh_issue_state(41, "/cwd"), "CLOSED")

    def test_nonzero_exit_is_unmeasurable(self):
        with self._fake_run(returncode=1, stdout=""):
            self.assertIsNone(dg._gh_issue_state(41, "/cwd"))

    def test_unexpected_output_is_unmeasurable(self):
        with self._fake_run(returncode=0, stdout="garbage\n"):
            self.assertIsNone(dg._gh_issue_state(41, "/cwd"))

    def test_subprocess_exception_is_unmeasurable_never_raises(self):
        import unittest.mock as m
        with m.patch.object(dg.subprocess, "run", side_effect=OSError("no gh")):
            self.assertIsNone(dg._gh_issue_state(41, "/cwd"))

    def test_timeout_is_unmeasurable(self):
        import subprocess as sp
        import unittest.mock as m
        with m.patch.object(dg.subprocess, "run",
                            side_effect=sp.TimeoutExpired("gh", 8)):
            self.assertIsNone(dg._gh_issue_state(41, "/cwd"))

    def test_runs_with_cwd_and_a_timeout(self):
        import unittest.mock as m
        with m.patch.object(dg.subprocess, "run",
                            return_value=m.Mock(returncode=0, stdout="OPEN")) as p:
            dg._gh_issue_state(41, "/some/repo")
            _, kwargs = p.call_args
            self.assertEqual(kwargs.get("cwd"), "/some/repo")
            self.assertIsNotNone(kwargs.get("timeout"))


if __name__ == "__main__":
    unittest.main()
