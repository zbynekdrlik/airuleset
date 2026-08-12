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

    # ---- adversarial-review findings (post-#219 fix): the bare "koreň" and
    # "zisten" alternations collided with everyday, unrelated Slovak words,
    # exactly the class of false positive the "zdroj" qualifier was already
    # written to avoid, just not applied to these two.
    def test_korenovy_adresar_root_directory_is_not_mistaken_for_root_cause(self):
        # "koreňový adresár" / "koreňová zložka" = "root directory" / "root
        # folder" -- an everyday IT term, not a cause claim. The adjective
        # form always continues "koreň" + "ov" + gender suffix.
        self.assertIsNone(dg._CAUSE_RE.search(
            "presunul som konfiguračný súbor do koreňového adresára "
            "repozitára"))
        self.assertIsNone(dg._CAUSE_RE.search(
            "súbor je teraz v koreňovej zložke"))

    def test_konzistentne_consistent_is_not_mistaken_for_zistenie(self):
        # "konzistentná"/"nekonzistentný" (consistent/inconsistent) contains
        # "zisten" as a pure accidental SUBSTRING (kon-ZISTEN-tná) with no
        # word boundary in front of it -- nothing to do with "zistenie"
        # (finding).
        self.assertIsNone(dg._CAUSE_RE.search(
            "aby sa chyby hlásili konzistentne naprieč všetkými cestami"))
        self.assertIsNone(dg._CAUSE_RE.search("stav bol nekonzistentný"))

    def test_a_comment_using_only_root_directory_language_fails_the_classifier(self):
        # End-to-end: no real root-cause explanation anywhere, only a
        # filesystem move justified by mentioning "root directory" -- must
        # still fail on "root cause", not be waved through.
        body = (
            "Prístup: presuniem config súbor do koreňového adresára "
            "repozitára, namiesto ponechania v podadresári config/, čo "
            "zjednodušuje cesty vo všetkých skriptoch. Zamietnutá "
            "alternatíva: ponechať ho v podadresári a upraviť každý "
            "skript zvlášť -- to je zbytočná duplicitná práca."
        )
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("root cause", reason)

    def test_a_comment_using_only_consistency_language_fails_the_classifier(self):
        body = (
            "Prístup: pridávam validáciu vstupu na začiatku endpointu "
            "namiesto kontroly až pred zápisom do DB, aby sa chyby "
            "hlásili konzistentne naprieč všetkými cestami. Zamietnutá "
            "alternatíva: kontrola tesne pred zápisom -- to necháva "
            "nekonzistentné chybové hlásenia medzi jednotlivými cestami."
        )
        ok, reason = dg.classify_design_comment(body)
        self.assertFalse(ok, reason)
        self.assertIn("root cause", reason)

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


# --------------------------------------------------------------------------- #
# #310 -- stale scratch-msgfile quarantine. A worker composing
# `cat > path <<EOF ... EOF && git commit -F path` in ONE Bash call has the
# WHOLE compound denied atomically by block-commit-without-design.sh when the
# gate fires -- so a stale file already sitting at `path` from an unrelated
# earlier attempt survives the block untouched, and a LATER bare
# `git commit -F path` retry carries no issue-number TEXT at all (invisible
# to ISSUE_REF_RE), so the gate never blocks it either. See the #310 design
# comment for the full incident.
# --------------------------------------------------------------------------- #

class TestStaleMsgfileCandidates(unittest.TestCase):

    def test_a_written_and_committed_path_is_a_candidate(self):
        cmd = "cat > msg.txt <<'EOF'\nfoo (#41)\nEOF\ngit commit -F msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), ["msg.txt"])

    def test_a_committed_only_path_with_no_write_is_not_a_candidate(self):
        # the LEGITIMATE two-step pattern -- msgfile written correctly in an
        # earlier, separate call, this command only consumes it.
        cmd = "git commit -F msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), [])

    def test_a_written_only_path_with_no_commit_is_not_a_candidate(self):
        cmd = "cat > msg.txt <<'EOF'\nfoo\nEOF\n"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), [])

    def test_different_paths_written_and_committed_are_not_a_match(self):
        cmd = "cat > other.txt <<'EOF'\nfoo\nEOF\ngit commit -F msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), [])

    def test_append_redirect_also_counts(self):
        cmd = "cat >> msg.txt <<'EOF'\nfoo\nEOF\ngit commit -F msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), ["msg.txt"])

    def test_double_dash_file_form_also_matches(self):
        cmd = "cat > msg.txt <<'EOF'\nfoo\nEOF\ngit commit --file msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), ["msg.txt"])

    def test_a_relative_dot_prefix_normalizes_to_the_same_target(self):
        cmd = "cat > ./msg.txt <<'EOF'\nfoo\nEOF\ngit commit -F msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), ["msg.txt"])

    def test_no_command_is_empty(self):
        self.assertEqual(dg.stale_msgfile_candidates(""), [])
        self.assertEqual(dg.stale_msgfile_candidates(None), [])

    def test_a_non_commit_git_subcommand_is_never_matched(self):
        cmd = "cat > msg.txt <<'EOF'\nfoo\nEOF\ngit commit-graph write --file msg.txt"
        self.assertEqual(dg.stale_msgfile_candidates(cmd), [])


class TestQuarantineStaleMsgfile(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-designgate-quarantine-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_an_existing_file_is_renamed_aside(self):
        p = self.tmp / "msg.txt"
        p.write_text("stale\n")
        dest = dg.quarantine_stale_msgfile(str(p))
        self.assertIsNotNone(dest)
        self.assertFalse(p.exists())
        self.assertTrue(Path(dest).exists())
        self.assertEqual(Path(dest).read_text(), "stale\n")

    def test_the_quarantine_path_carries_a_timestamp_suffix(self):
        p = self.tmp / "msg.txt"
        p.write_text("stale\n")
        dest = dg.quarantine_stale_msgfile(str(p), ts=1234.5)
        self.assertEqual(dest, str(p) + ".stale-1234500")

    def test_a_missing_file_is_a_noop(self):
        p = self.tmp / "does-not-exist.txt"
        self.assertIsNone(dg.quarantine_stale_msgfile(str(p)))

    def test_a_directory_is_never_touched(self):
        d = self.tmp / "adir"
        d.mkdir()
        self.assertIsNone(dg.quarantine_stale_msgfile(str(d)))
        self.assertTrue(d.is_dir())

    def test_never_raises_on_a_permission_error(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits")
        p = self.tmp / "msg.txt"
        p.write_text("x\n")
        self.tmp.chmod(0o500)  # read+execute only -> rename inside it fails
        try:
            self.assertIsNone(dg.quarantine_stale_msgfile(str(p)))
        finally:
            self.tmp.chmod(0o700)

    def test_a_symlink_is_renamed_never_followed(self):
        # adversarial-review M12 gap: os.rename on a symlink renames the
        # LINK ITSELF, never the target -- lock this in explicitly.
        target = self.tmp / "important.txt"
        target.write_text("do not touch\n")
        link = self.tmp / "msg.txt"
        link.symlink_to(target)
        dest = dg.quarantine_stale_msgfile(str(link))
        self.assertIsNotNone(dest)
        self.assertFalse(link.exists())
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "do not touch\n")
        self.assertTrue(os.path.islink(dest))
        self.assertEqual(os.readlink(dest), str(target))

    def test_a_collision_on_the_destination_is_never_clobbered(self):
        # adversarial-review finding #2: a same-millisecond collision on
        # the ".stale-<ts>" destination used to silently OVERWRITE
        # whatever was already quarantined there.
        p = self.tmp / "msg.txt"
        p.write_text("second\n")
        existing_dest = str(p) + ".stale-1000"
        Path(existing_dest).write_text("FIRST QUARANTINE -- must survive\n")
        dest = dg.quarantine_stale_msgfile(str(p), ts=1.0)
        self.assertNotEqual(dest, existing_dest)
        self.assertEqual(Path(existing_dest).read_text(),
                          "FIRST QUARANTINE -- must survive\n")
        self.assertEqual(Path(dest).read_text(), "second\n")


class TestIsGitTracked(unittest.TestCase):
    """#310 adversarial-review finding #1 (BLOCKING): stale_msgfile_candidates
    scans `>`/`>>` write targets and `git commit -F` targets INDEPENDENTLY
    over the WHOLE command text -- a `-m "..."` message merely DESCRIBING the
    write-then-consume pattern in PROSE (no real shell write, no real -F
    flag) satisfies both regexes just as well as genuine shell syntax. A
    real tracked project file (e.g. README.md) merely MENTIONED that way
    must never be quarantined -- is_git_tracked() is the discriminator the
    caller applies before ever calling quarantine_stale_msgfile()."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-designgate-tracked-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        self._env = env
        self._git("init", "-q", "-b", "main")

    def _git(self, *args):
        import subprocess
        return subprocess.run(["git", "-C", str(self.repo)] + list(args),
                               check=True, capture_output=True, text=True, env=self._env)

    def test_a_tracked_file_is_tracked(self):
        (self.repo / "README.md").write_text("hello\n")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "initial")
        self.assertTrue(dg.is_git_tracked(str(self.repo / "README.md"), str(self.repo)))

    def test_an_untracked_file_is_not_tracked(self):
        (self.repo / "msg.txt").write_text("scratch\n")
        self.assertFalse(dg.is_git_tracked(str(self.repo / "msg.txt"), str(self.repo)))

    def test_a_committed_then_deleted_file_is_no_longer_tracked(self):
        (self.repo / "gone.txt").write_text("x\n")
        self._git("add", "gone.txt")
        self._git("commit", "-q", "-m", "add then rm")
        self._git("rm", "-q", "gone.txt")
        self._git("commit", "-q", "-m", "rm")
        self.assertFalse(dg.is_git_tracked(str(self.repo / "gone.txt"), str(self.repo)))

    def test_a_nonexistent_path_is_not_tracked(self):
        self.assertFalse(dg.is_git_tracked(str(self.repo / "never-existed.txt"), str(self.repo)))

    def test_unmeasurable_fails_toward_tracked_never_guesses(self):
        # a non-repo cwd (git ls-files errors) must NOT be read as "safe to
        # quarantine" -- fail toward the conservative, non-destructive answer.
        other = Path(tempfile.mkdtemp(prefix="airuleset-designgate-notrepo-"))
        self.addCleanup(shutil.rmtree, other, True)
        (other / "msg.txt").write_text("x\n")
        self.assertTrue(dg.is_git_tracked(str(other / "msg.txt"), str(other)))

    def test_subprocess_exception_is_unmeasurable_fails_toward_tracked(self):
        import unittest.mock as m
        with m.patch.object(dg.subprocess, "run", side_effect=OSError("no git")):
            self.assertTrue(dg.is_git_tracked(str(self.repo / "x"), str(self.repo)))


class TestStaleMsgfileEndToEndTrackedFileNeverQuarantined(TestIsGitTracked):
    """The exact adversarial-review reproduction: a `-m` message merely
    PROSE-describing the write-then-consume pattern must never lose a
    real tracked file, end to end through the real hook."""

    HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-commit-without-design.sh"

    def test_readme_mentioned_in_commit_prose_is_never_quarantined(self):
        import json
        import subprocess
        (self.repo / "README.md").write_text("real project content\n")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "initial")
        self._git("remote", "add", "origin",
                   "https://github.com/zbynekdrlik/airuleset.git")
        cmd = (
            'git commit -m "docs: explain the trap (#41)\n\n'
            'When you write cat > README.md <<EOF then run '
            'git commit -F README.md in one call, the block eats the write."'
        )
        payload = {"tool_input": {"command": cmd}, "session_id": "e2e-310",
                   "cwd": str(self.repo), "agent_type": "autopilot-worker",
                   "agent_id": "aW1"}
        bindir = Path(tempfile.mkdtemp(prefix="airuleset-designgate-e2egh-"))
        self.addCleanup(shutil.rmtree, bindir, True)
        fake_gh = bindir / "gh"
        fake_gh.write_text("#!/usr/bin/env bash\necho OPEN\nexit 0\n")
        fake_gh.chmod(0o755)
        env = dict(self._env)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        home = Path(tempfile.mkdtemp(prefix="airuleset-designgate-e2ehome-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / ".claude").mkdir(parents=True)
        env["HOME"] = str(home)
        r = subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload),
                            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue((self.repo / "README.md").exists(),
                         "a real TRACKED file merely mentioned in commit "
                         "prose must never be quarantined")
        self.assertEqual((self.repo / "README.md").read_text(),
                          "real project content\n")
        siblings = [f for f in self.repo.iterdir()
                    if f.name.startswith("README.md.stale-")]
        self.assertEqual(siblings, [])


class TestEnsureStalePatternExcluded(unittest.TestCase):
    """#310 adversarial-review finding #3: a quarantined `.stale-*` file
    must never get swept into `git add -A` / show up as an untracked
    file -- appended once to the repo's LOCAL (never committed)
    .git/info/exclude, resolved via `git rev-parse --git-common-dir` so
    it lands in the SHARED dir even from inside a worktree checkout."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-designgate-exclude-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        env = dict(os.environ)
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        import subprocess
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                        check=True, capture_output=True, env=env)

    def test_appends_the_pattern_once(self):
        dg.ensure_stale_pattern_excluded(str(self.repo))
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertTrue(exclude.exists())
        self.assertIn("*.stale-*", exclude.read_text())

    def test_calling_it_twice_does_not_duplicate_the_line(self):
        dg.ensure_stale_pattern_excluded(str(self.repo))
        dg.ensure_stale_pattern_excluded(str(self.repo))
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertEqual(exclude.read_text().count("*.stale-*"), 1)

    def test_a_quarantined_file_is_actually_ignored_afterward(self):
        dg.ensure_stale_pattern_excluded(str(self.repo))
        (self.repo / "msg.txt.stale-123").write_text("x\n")
        import subprocess
        env = dict(os.environ)
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(self.repo),
                              capture_output=True, text=True, env=env)
        self.assertNotIn("msg.txt.stale-123", out.stdout)

    def test_a_non_repo_cwd_is_a_silent_noop(self):
        other = Path(tempfile.mkdtemp(prefix="airuleset-designgate-notrepo2-"))
        self.addCleanup(shutil.rmtree, other, True)
        dg.ensure_stale_pattern_excluded(str(other))  # must not raise

    def test_never_raises_on_a_subprocess_failure(self):
        import unittest.mock as m
        with m.patch.object(dg.subprocess, "run", side_effect=OSError("no git")):
            dg.ensure_stale_pattern_excluded(str(self.repo))  # must not raise


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


# --------------------------------------------------------------------------- #
# #414 -- SOTA architecture: the design-posted machinery now ALSO requires an
# `Architektúra:` section (structure/topology + framework used OR an
# evidenced why-none-fits) for a "design" marker. Kept as a SEPARATE
# classifier from `classify_design_comment` (never folded in) so that
# function's three OTHER independent consumers (the historical
# `measure_design_compliance.py` Deliverable-1 corpus measurement,
# `replay_design_gate_commit_corpus.py`, and this file's own pre-existing
# ~30 tests) keep their stable, unchanged meaning -- only
# `hooks/post-record-design-comment.sh` combines both, and only for
# kind=="design". This is also what makes "never retro-invalidate an
# already-written marker" trivially true: existing marker FILES are never
# re-classified by anything, and `classify_design_comment` itself never
# changed.
# --------------------------------------------------------------------------- #

GOOD_ARCH_SECTION_EN = (
    "\n\n**Architektúra:** structure/topology -- a single new module function, "
    "no new process or service. Framework: reused the existing design_gate.py "
    "classifier structure directly, no new machinery."
)

GOOD_ARCH_SECTION_SK = (
    "\n\nArchitektúra: štruktúra -- rozšírenie existujúceho modulu, žiadna "
    "nová služba. Framework: znovupoužitý existujúci klasifikátor, žiadny "
    "nový kód netreba."
)


class TestClassifyArchitectureSection(unittest.TestCase):

    def test_full_english_section_passes(self):
        ok, reason = dg.classify_architecture_section(
            GOOD_SCOPED + GOOD_ARCH_SECTION_EN)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_full_slovak_section_passes(self):
        ok, reason = dg.classify_architecture_section(
            GOOD_SLOVAK + GOOD_ARCH_SECTION_SK)
        self.assertTrue(ok, reason)

    def test_missing_header_entirely_fails(self):
        ok, reason = dg.classify_architecture_section(GOOD_SCOPED)
        self.assertFalse(ok)
        self.assertIn("Architekt", reason)

    def test_header_present_but_no_structure_keyword_fails(self):
        body = (
            GOOD_SCOPED + "\n\n**Architektúra:** we will use the existing "
            "framework here, nothing else to say about it."
        )
        ok, reason = dg.classify_architecture_section(body)
        self.assertFalse(ok, reason)
        self.assertIn("structure", reason.lower())

    def test_header_present_but_no_framework_or_whynot_fails(self):
        body = (
            GOOD_SCOPED + "\n\n**Architektúra:** the structure/topology here "
            "is a single function, that's all."
        )
        ok, reason = dg.classify_architecture_section(body)
        self.assertFalse(ok, reason)
        self.assertIn("framework", reason.lower())

    def test_why_none_fits_language_counts_instead_of_framework_name(self):
        body = (
            GOOD_SCOPED + "\n\n**Architektúra:** structure -- a single "
            "function extension. No framework fits this scope; investigated "
            "the existing solution and it already covers this need."
        )
        ok, reason = dg.classify_architecture_section(body)
        self.assertTrue(ok, reason)

    def test_markdown_heading_form_is_recognized(self):
        body = GOOD_SCOPED + "\n\n## Architektúra\nstructure: existing module. framework: none new."
        ok, reason = dg.classify_architecture_section(body)
        self.assertTrue(ok, reason)

    def test_english_architecture_spelling_is_recognized(self):
        body = GOOD_SCOPED + "\n\nArchitecture: structure -- one module. Framework: none new."
        ok, reason = dg.classify_architecture_section(body)
        self.assertTrue(ok, reason)

    def test_diacritic_free_slovak_spelling_is_recognized(self):
        body = GOOD_SCOPED + "\n\nArchitektura: struktura -- existujuci modul. Framework: ziadny novy."
        ok, reason = dg.classify_architecture_section(body)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_architecture_section("")
        self.assertFalse(ok)

    def test_none_body_fails(self):
        ok, reason = dg.classify_architecture_section(None)
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
# #414 -- Triage: line + (for a non-trivial ticket) 2-3 considered approaches
# with trade-offs, restoring the interactive-brainstorming-era design depth
# the owner reported losing once autopilot took over ("per-ticket tunel --
# nikto nedržal celok"). Also a SEPARATE classifier, same reasoning as above.
# --------------------------------------------------------------------------- #

TRIVIAL_TRIAGE_BODY = GOOD_SCOPED + "\n\nTriage: trivial"

NONTRIVIAL_GOOD_BODY = (
    "Triage: non-trivial -- this introduces a new long-lived daemon.\n\n"
    "Root cause: no existing process watches this queue, so items rot "
    "silently until someone happens to look. Approach 1: a new systemd timer "
    "polling the queue every 60s, reusing the existing watchdog job runner "
    "-- trade-off: adds one more job to an already-large run_once(), but "
    "zero new infrastructure. Approach 2: a dedicated long-lived daemon "
    "process -- trade-off: cleaner separation, but a whole new supervised "
    "process to deploy/monitor/restart, more moving parts for a queue this "
    "small. Chosen approach: Approach 1, the polling timer -- the queue "
    "volume does not justify a dedicated process yet. Rejected alternative: "
    "Approach 2, the dedicated daemon -- too much operational overhead for "
    "the current volume."
)


class TestClassifyTriageAndApproaches(unittest.TestCase):

    def test_trivial_triage_passes_without_multiple_approaches(self):
        ok, reason = dg.classify_triage_and_approaches(TRIVIAL_TRIAGE_BODY)
        self.assertTrue(ok, reason)

    def test_nontrivial_with_two_approaches_and_tradeoffs_passes(self):
        ok, reason = dg.classify_triage_and_approaches(NONTRIVIAL_GOOD_BODY)
        self.assertTrue(ok, reason)

    def test_missing_triage_line_fails(self):
        ok, reason = dg.classify_triage_and_approaches(GOOD_SCOPED)
        self.assertFalse(ok)
        self.assertIn("Triage", reason)

    def test_triage_value_naming_neither_class_fails(self):
        body = GOOD_SCOPED + "\n\nTriage: unsure"
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertFalse(ok, reason)

    def test_nontrivial_netrivialne_is_recognized_not_confused_with_trivial(self):
        # "netriviálne" CONTAINS the substring "trivi" -- must classify as
        # non-trivial, never fall through to the trivial (single-paragraph)
        # branch just because "trivi" matched somewhere.
        body = GOOD_SCOPED + "\n\nTriage: netriviálne"
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertFalse(ok, reason)  # only one approach in GOOD_SCOPED -> fails on approach count
        self.assertNotIn("Triage", reason)  # the Triage: line itself WAS found and classified

    def test_nontrivial_with_only_one_approach_fails(self):
        body = (
            "Triage: non-trivial -- new component.\n\n" + GOOD_SCOPED +
            " Trade-off: considered vs the alternative above."
        )
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertFalse(ok, reason)
        self.assertIn("approach", reason.lower())

    def test_nontrivial_with_two_approaches_but_no_tradeoff_language_fails(self):
        body = (
            "Triage: non-trivial -- new component.\n\n"
            "Approach 1: do it with a dedicated systemd timer polling the "
            "queue every 60 seconds, reusing the existing watchdog job "
            "runner infrastructure that already exists on every managed "
            "box. Approach 2: do it with a brand new standalone daemon "
            "process that stays resident and watches the queue directly "
            "without any polling interval at all. Chosen: Approach 1, "
            "because it is simpler and I looked carefully at both options "
            "and honestly they both work fine either way in the end for "
            "this particular use case as far as I can tell right now."
        )
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertFalse(ok, reason)
        self.assertIn("trade", reason.lower())

    def test_nontrivial_short_body_fails_on_length_even_with_triage_line(self):
        body = "Triage: non-trivial\n\nApproach 1: x. Approach 2: y. Trade-off: z."
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertFalse(ok, reason)

    def test_slovak_pristup_markers_are_recognized(self):
        body = (
            "Triage: netriviálne -- nová dlhožijúca komponenta.\n\n"
            "Koreň/kontext: fronta nemá žiadny proces, ktorý by ju "
            "sledoval, položky ticho hnijú. Prístup 1: nový systemd timer, "
            "ktorý pollne frontu každých 60s, znovupoužije existujúci "
            "watchdog runner -- kompromis: pridáva ďalší job do už veľkého "
            "run_once(), ale žiadna nová infraštruktúra. Prístup 2: "
            "samostatný dlhožijúci proces -- kompromis: čistejšie "
            "oddelenie, ale nová supervidovaná služba na nasadenie a "
            "monitorovanie, viac pohyblivých častí pre takto malú frontu. "
            "Zvolený prístup: Prístup 1, polling timer -- objem fronty "
            "zatiaľ neopodstatňuje samostatný proces. Zamietnutá "
            "alternatíva: Prístup 2, samostatný proces -- zbytočná "
            "prevádzková réžia pre súčasný objem."
        )
        ok, reason = dg.classify_triage_and_approaches(body)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_triage_and_approaches("")
        self.assertFalse(ok)

    def test_none_body_fails(self):
        ok, reason = dg.classify_triage_and_approaches(None)
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
# #414 -- reject-reason I/O: purely diagnostic, never gates anything itself.
# Lets block-commit-without-design.sh surface WHY the worker's last posted
# comment did not classify, instead of the bare "no design comment posted
# yet". Same file-per-key convention as marker I/O, a SIBLING directory
# (never mixed into design-posted/) so a reject can never be mistaken for a
# delivered marker.
# --------------------------------------------------------------------------- #

class TestRejectReasonIO(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-designgate-reject-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        os.environ["HOME"] = str(self.home)

    def test_no_reject_reason_by_default(self):
        self.assertIsNone(dg.read_reject_reason("airuleset", 999))

    def test_write_then_read_round_trips(self):
        dg.write_reject_reason("airuleset", 999, "missing: Architektúra: section")
        self.assertEqual(dg.read_reject_reason("airuleset", 999),
                         "missing: Architektúra: section")

    def test_later_write_overwrites_the_earlier_reason(self):
        dg.write_reject_reason("airuleset", 999, "first reason")
        dg.write_reject_reason("airuleset", 999, "second reason")
        self.assertEqual(dg.read_reject_reason("airuleset", 999), "second reason")

    def test_distinct_kinds_are_isolated(self):
        dg.write_reject_reason("airuleset", 999, "design reason", kind="design")
        dg.write_reject_reason("airuleset", 999, "validated reason", kind="validated")
        self.assertEqual(dg.read_reject_reason("airuleset", 999, "design"), "design reason")
        self.assertEqual(dg.read_reject_reason("airuleset", 999, "validated"), "validated reason")

    def test_no_repo_key_never_writes(self):
        self.assertFalse(dg.write_reject_reason("", 999, "x"))
        self.assertFalse(dg.write_reject_reason(None, 999, "x"))

    def test_reject_reason_never_collides_with_a_real_marker(self):
        # writing a reject reason must NEVER cause marker_exists() to see a
        # marker where none was ever granted.
        dg.write_reject_reason("airuleset", 999, "missing: Architektúra: section")
        self.assertFalse(dg.marker_exists("airuleset", 999, "design"))

    def test_an_existing_marker_written_before_this_change_is_never_touched(self):
        # #414's own explicit requirement: never retro-invalidate an
        # already-posted design marker. A marker written directly (as if by
        # an OLDER version of post-record-design-comment.sh, before the
        # Architektúra:/Triage: checks existed) must stay valid regardless
        # of anything the new classifiers would now require.
        dg.write_marker("airuleset", 41, "https://example.invalid/old-comment", "ok", kind="design")
        self.assertTrue(dg.marker_exists("airuleset", 41, "design"))
        # the new classifiers exist and would reject a bare "ok" body, but
        # nothing ever re-runs them against an already-written marker.
        ok, _ = dg.classify_architecture_section("ok")
        self.assertFalse(ok)
        self.assertTrue(dg.marker_exists("airuleset", 41, "design"))


if __name__ == "__main__":
    unittest.main()
