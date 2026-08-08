"""Locks item 8 of #95: the "Pre-answered questions" table in
ask-before-assuming.md was trimmed from 26 rows to 9 (+ one pointer line).

Every dropped row's justification was "a real hook already hard-blocks this
question" — proven empirically (real hook invocation, real STDIN payload,
never a re-implementation of the hook's own regex) BEFORE the row was
removed, but ONLY ever against an ENGLISH fixture.

#316 (2026-08-08) audited all 17 dropped rows against a realistic SLOVAK
rendering of the SAME banned intent — this repo's own questions are always
Slovak (`user-questions-slovak.md`), so an English-only coverage proof was a
felt difference waiting to happen (montalu2's `schvaľuješ zapísaný design
spec?` sailed through untouched). Only 3 of the 17 dropped rows genuinely
held in BOTH languages at the time — but ONLY because their Slovak
rendering happened to retain the literal English jargon (`admin-merge`,
`subagent-driven-development`) the existing regex keys on. The other 14
were reverted back into the table, in their original relative position and
their original text unchanged (ONE row — "review the spec/plan before
hand-off" — also gained a deliberate new item-3 reinforcement sentence in
this same PR; every other reverted row's text is byte-for-byte its
pre-`a89024a` original).

#319 (2026-08-08) found that same "held in both languages" claim was itself
loanword-contingent for those 3 rows: a genuinely NATURAL Slovak rendering
of the SAME banned intent, with the loanword replaced by ordinary Slovak
words, was NOT blocked by any of the three hooks — failing #316's own
audit criterion exactly like the other 14 rows had. Fixed with two new,
narrow Slovak detectors in `hooks/stop-check-prose-violations.sh`
(dispatch-now-or-hold, and merge-despite/quality-bypass), mirroring #316's
own established pattern shape verbatim. All 17 originally-dropped rows are
now hook-covered in genuinely natural Slovak, not merely in English or by
loanword coincidence — the loanword-retaining Slovak fixtures below still
document a true, residual fact (they were ALREADY blocked before #319, via
a different — coincidental — match), but that is no longer the reason the
3 rows stay out of the table.

Full row-by-row mapping (hook file + exact regex fragment per row):
https://github.com/zbynekdrlik/airuleset/issues/95 (design comment).
Slovak audit (per-row pass/fail): https://github.com/zbynekdrlik/airuleset/issues/316
Genuinely-natural-Slovak coverage for the remaining 3 rows (fixtures +
false-positive controls): https://github.com/zbynekdrlik/airuleset/issues/319
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from _hook_state_cleanup import sweep_session_files  # noqa: E402

HOOKS = airuleset.REPO_DIR / "hooks"


def _run_pre_ask(text):
    """PreToolUse(AskUserQuestion) hook — exit 2 means the tool call itself
    is blocked before the question ever reaches the user. No session id in
    this payload at all, so no per-session counter file to sweep."""
    payload = json.dumps({"tool_input": {"question": text}})
    p = subprocess.run(
        ["bash", str(HOOKS / "pre-ask-auto-answer.sh")],
        input=payload, capture_output=True, text=True,
    )
    return p.returncode == 2


def _run_stop_prose(text):
    """Stop hook — {"decision":"block"} on stdout means the same question
    written as prose (not the AskUserQuestion tool) is also hard-blocked.

    #202: the hook only clears its own retry-counter file on a CLEAN stop,
    never on a block — and almost every call here IS a block, by design (the
    whole point of this file). Swept immediately: the sid is unique per call
    and nothing later needs the counter once the verdict is read."""
    sid = f"test-dropped-row-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOKS / "stop-check-prose-violations.sh")],
        input=payload, capture_output=True, text=True,
    )
    sweep_session_files(sid)
    return '"decision"' in p.stdout and '"block"' in p.stdout


def _run_stop_untracked_work(text):
    """Stop hook — {"decision":"block"} means "asking permission to file
    issues" is hard-blocked. Fresh session id per call: the hook caps at
    3 blocks/session, so a shared session id across calls would silently
    stop blocking after the 3rd and look like a coverage regression.

    #202: same leftover-counter shape as `_run_stop_prose` above — swept
    immediately for the same reason."""
    sid = f"test-dropped-row-ndw-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOKS / "stop-check-untracked-work.sh")],
        input=payload, capture_output=True, text=True,
    )
    sweep_session_files(sid)
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestPreAskAutoAnswerCoversDroppedRows(TestCase):
    """The 3 rows that GENUINELY stayed dropped — hooks/pre-ask-auto-answer.sh
    hard-blocks the AskUserQuestion tool call itself (exit code 2)."""

    def test_dispatch_now_or_hold_for_review_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Plan committed locally as abc123. Dispatch all tasks via "
            "subagent-driven-development now, or hold for your review "
            "of the plan first?"))

    def test_admin_merge_shortcut_menu_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Realistic options: admin-merge / close PR / stop runner"))

    def test_merge_despite_failing_check_blocked(self):
        self.assertTrue(_run_pre_ask("Should I merge despite the failing check?"))


class TestStopCheckProseCoversDroppedRows(TestCase):
    """The same 2 of the 3 surviving rows that ALSO hard-block as prose
    (a Stop-gate, not the AskUserQuestion tool)."""

    def test_admin_merge_realistic_options_prose_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Realistic options: admin-merge / close PR / stop runner"))

    def test_merge_despite_prose_blocked(self):
        self.assertTrue(_run_stop_prose("Should I merge despite the failing check?"))


class TestSurvivingRowsAlsoCoverSlovak(TestCase):
    """#316's own audit evidence, made durable: a realistic Slovak rendering
    of each of the 3 surviving rows' SAME banned intent, run against the
    real hook. Each one blocks via a genuine hook match — but at #316's own
    audit time this was COINCIDENTAL: the fixture below retains the literal
    English jargon term the pre-#319 regex keyed on (`subagent-driven-
    development`, `admin-merge`), not a genuine Slovak-language match.
    #319 found that a NATURAL Slovak rendering of the SAME intent, with the
    loanword replaced by ordinary Slovak words, was NOT blocked by ANY
    hook at that point — so retaining the loanword was never sufficient
    justification to leave the row out of the table (see
    TestGenuineSlovakCoverageForRemainingThreeRows below for the fix: two
    new, genuinely Slovak-language detectors that no longer need the
    loanword at all). This class stays as a residual regression lock —
    the loanword-retaining fixtures below are still real, additional
    coverage on top of the new detectors, not the row's only protection."""

    def test_dispatch_now_or_hold_slovak_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Plán je commitnutý ako abc123. Mám hneď spustiť subagentov "
            "cez subagent-driven-development, alebo počkať na tvoju "
            "kontrolu plánu?"))

    def test_admin_merge_slovak_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Ako naložiť s týmto gated PR? Reálne možnosti: admin-merge "
            "/ zavrieť PR / zastaviť runner."))
        self.assertTrue(_run_stop_prose(
            "Ako naložiť s týmto gated PR? Reálne možnosti: admin-merge "
            "/ zavrieť PR / zastaviť runner."))

    def test_merge_despite_slovak_blocked(self):
        msg = "Mám to zmergovať napriek zlyhávajúcej kontrole? Chceš aby som spravil admin-merge?"
        self.assertTrue(_run_pre_ask(msg))
        self.assertTrue(_run_stop_prose(msg))


class TestGenuineSlovakCoverageForRemainingThreeRows(TestCase):
    """#319: a NATURAL Slovak rendering of rows 7-9's banned intent — the
    loanword (`subagent-driven-development`, `admin-merge`) replaced by
    ordinary Slovak words — is now hard-blocked by two new, narrow
    detectors in `hooks/stop-check-prose-violations.sh`:

    1. Slovak dispatch-now-or-hold (row 7): a Slovak "start" verb
       (spustiť/rozbehnúť/začať) near a "now" word (hneď/teraz/ihneď),
       "alebo"/"či", a Slovak "wait" verb (počkať/počkaj/čakať), and a
       plán/kontrola/review word nearby.
    2. Slovak merge-despite / quality-bypass (rows 8+9): a Slovak "merge"
       verb (zlúčiť/zmergovať/zmerguj) near "napriek" (despite).

    Both mirror #316's own established pattern shape verbatim: bounded
    `.{0,N}` windows, `\\b` anchors on plain diacritic alternation (never
    embedded bracket classes), `LC_ALL=C.UTF-8` forced on the one grep
    call that needs it (see TestNewSlovakDetectorsSurviveABareLocale
    below — without the forcing, `\\b` next to a diacritic is itself
    locale-dependent under a bare C/POSIX locale)."""

    DISPATCH_NOW_OR_HOLD_SK = (
        "Plán je uložený. Mám hneď rozbehnúť prácu na úlohách, "
        "alebo počkať, kým si pozrieš plán?")
    ADMIN_MERGE_REALISTIC_OPTIONS_SK = (
        "Ako naložiť s týmto zablokovaným PR? Možnosti sú: obísť "
        "kontrolu vetvy a zlúčiť to napriek zlyhaniu, zavrieť PR, "
        "alebo zastaviť runner.")
    MERGE_DESPITE_SK = "Mám to zlúčiť napriek zlyhávajúcej kontrole?"

    def test_dispatch_now_or_hold_natural_slovak_blocked(self):
        self.assertTrue(
            _run_stop_prose(self.DISPATCH_NOW_OR_HOLD_SK),
            "#319: natural Slovak dispatch-now-or-hold (no loanword) "
            "must be blocked by stop-check-prose-violations.sh")

    def test_admin_merge_realistic_options_natural_slovak_blocked(self):
        self.assertTrue(
            _run_stop_prose(self.ADMIN_MERGE_REALISTIC_OPTIONS_SK),
            "#319: natural Slovak admin-merge/realistic-options shortcut "
            "menu (no loanword) must be blocked by "
            "stop-check-prose-violations.sh")

    def test_merge_despite_natural_slovak_blocked(self):
        self.assertTrue(
            _run_stop_prose(self.MERGE_DESPITE_SK),
            "#319: natural Slovak merge-despite (no loanword) must be "
            "blocked by stop-check-prose-violations.sh")


class TestNewSlovakDetectorsDoNotOverfire(TestCase):
    """#319 false-positive discipline: an unrelated, genuinely legitimate
    Slovak sentence that happens to use the SAME individual words the new
    detectors key on (spustiť/hneď/počkať, zlúčiť/napriek) in a DIFFERENT
    context — or a real design-fork question with bullet options — must
    stay welcome. Mirrors the false-positive controls posted on the
    ticket's own design comment."""

    NEGATIVE_PHRASES = [
        # unrelated "started tests, will wait for results" — no "or",
        # no plan/review word nearby.
        "Spustil som testy a počkám na výsledky.",
        # "napriek" present but no merge-verb nearby — an unrelated
        # deploy-status statement, not a merge-despite-failure question.
        "Nasadenie prebehlo úspešne napriek tomu, že testy boli pomalé.",
        # same 4-token dispatch shape, but hardware/live-timing, not
        # subagent/plan dispatch — no plán/kontrola/review word nearby.
        "Mám hneď reštartovať OBS, alebo počkať do konca prenosu?",
        # an already-legitimate (English-pattern-adjacent) Slovak
        # question that must not newly trip either detector.
        "Vyzerá tento návrh dobre? Ak áno, zapíšem spec do "
        "docs/.../spec.md a commitnem.",
        # a genuine design-fork question with real bullet options.
        "Ktorý návrh preferuješ: A alebo B, s dôsledkami pre výkon "
        "a údržbu?\n\n"
        "• Návrh A — jednoduchší, pomalší\n"
        "• Návrh B — komplexnejší, rýchlejší",
    ]

    def test_negative_phrases_not_blocked_by_stop_prose(self):
        for phrase in self.NEGATIVE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(
                    _run_stop_prose(phrase), f"stop-prose blocked: {phrase!r}")


class TestNewSlovakDetectorsSurviveABareLocale(TestCase):
    """#316-review's own CRITICAL finding (reproduced again here, live,
    for the two NEW detectors this ticket adds): `\\b` immediately
    adjacent to a diacritic is locale-dependent under a bare C/POSIX
    locale (no LANG/LC_ALL set on the box) — reproduced directly: without
    forcing LC_ALL=C.UTF-8, the SAME positive fixtures above silently miss
    under `LC_ALL=C LANG=C`. The fix forces LC_ALL=C.UTF-8 on just the
    grep calls that need it."""

    def _run_stop_prose_bare_locale(self, text):
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        sid = f"test-319-locale-{uuid.uuid4().hex[:10]}"
        payload = json.dumps({"session_id": sid, "last_assistant_message": text})
        p = subprocess.run(
            ["bash", str(HOOKS / "stop-check-prose-violations.sh")],
            input=payload, capture_output=True, text=True, env=env,
        )
        sweep_session_files(sid)
        return '"decision"' in p.stdout and '"block"' in p.stdout

    def test_dispatch_now_or_hold_blocked_under_bare_c_locale(self):
        self.assertTrue(
            self._run_stop_prose_bare_locale(
                "Plán je uložený. Mám hneď rozbehnúť prácu na úlohách, "
                "alebo počkať, kým si pozrieš plán?"),
            "the dispatch-now-or-hold detector went inert under a bare "
            "C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or broken")

    def test_merge_despite_blocked_under_bare_c_locale(self):
        self.assertTrue(
            self._run_stop_prose_bare_locale(
                "Mám to zlúčiť napriek zlyhávajúcej kontrole?"),
            "the merge-despite detector went inert under a bare C/POSIX "
            "locale — LC_ALL=C.UTF-8 forcing is missing or broken")


class TestRevertedRowsNoLongerClaimedAsHookCovered(TestCase):
    """The 14 rows reverted back into the table by #316 — their Slovak
    rendering is NOT blocked by any of the 3 hooks. This is not a bug: it
    is exactly WHY they were reverted (prose-only guidance now, no hook
    claim behind them). Locked here so a future re-trim of the table
    cannot silently drop them again without re-proving Slovak coverage."""

    REVERTED_SLOVAK_PHRASES = [
        # row 1 — subagent-vs-inline
        "Mám to spraviť cez subagenta alebo sekvenčne/inline? "
        "Dve možnosti vykonania.",
        # row 2 — visual companion
        "Chceš vizuálneho spoločníka / mockup pre túto layoutovú otázku?",
        # row 3 — say go
        "Pripravený spustiť — povedz go.",
        # row 4 — ready for next step
        "Ak je to dobré, povedz a ja to spustím. Pripravený na ďalší krok?",
        # row 5 — review-the-spec-before-handoff
        "Prosím skontroluj spec/plán a daj mi vedieť, ak chceš zmeny, "
        "predtým než to odovzdám writing-plans.",
        # row 6 — does this design look right
        "Vyzerá tento návrh dobre? Ak áno, zapíšem spec do "
        "docs/.../spec.md a commitnem.",
        # row 10 — investigate or merge despite
        "Mám preskúmať problém s codecov, alebo to radšej zmergovať "
        "napriek tomu?",
        # row 11 — functionally ready but unstable
        "PR je funkčne pripravený ale UNSTABLE — rozhodneš o mergnutí?",
        # row 12 — should I merge / approve merge
        "PR je zelený — mám ho zmergovať? Alebo počkať?",
        # row 13 — follow-up cleanup or do it now
        "Mám tento cleanup zapísať ako follow-up issue, alebo to "
        "spraviť teraz?",
        # row 14 — give the word, create the issues
        "Daj mi znamenie a vytvorím tie issues. Si pripravený "
        "schváliť ten backlog?",
        # row 15 — can you test it on your end
        "Môžeš mi to otestovať na svojej strane? Daj mi vedieť, "
        "či to funguje.",
        # row 16 — fix locally before next user test
        "Opravím to lokálne pred ďalším user testom. Prestávam ťa "
        "používať ako testera.",
        # row 17 — tool missing, could you try on staging
        "Mohol by si to vyskúšať na staging prostredí? Chýba mi na "
        "to nástroj.",
    ]

    def test_reverted_slovak_phrases_not_blocked_by_any_hook(self):
        for phrase in self.REVERTED_SLOVAK_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(_run_pre_ask(phrase), f"pre-ask blocked: {phrase!r}")
                self.assertFalse(_run_stop_prose(phrase), f"stop-prose blocked: {phrase!r}")
                self.assertFalse(
                    _run_stop_untracked_work(phrase),
                    f"stop-untracked-work blocked: {phrase!r}")


class TestKeptRowsRemainUncoveredByHooks(TestCase):
    """The 9 rows that STAYED KEPT since a89024a are the ones with NO
    matching hook pattern — this is the negative control proving the drop
    decisions above weren't just "every row blocks something": these
    representative phrases must NOT be hard-blocked by any of the three
    hooks."""

    KEPT_PHRASES = [
        "Where should I place the diagnostic QR code?",
        # NOTE: deliberately avoids the standalone phrase "which approach?" —
        # that wording alone trips pre-ask-auto-answer.sh's UNRELATED
        # process-pause pattern (`which (approach|execution|...)\?`), a
        # coincidental match on wording, not real coverage of this row's
        # actual intent (self-invented technical obstacles). This is
        # exactly the false-positive rejected-alternative-2 in #95's
        # design comment — the negative control must not reintroduce it.
        "Feature X I designed hit a technical wall — investigate the "
        "cause, ship an estimate, or show n/a?",
        "Should I continue with phase N?",
        "Should I monitor CI?",
        "Want me to verify with Playwright?",
        "Ready for issue #N+1?",
        "Should I bundle these issues or do separate PRs?",
        "Rollout plan: PR1 schema, PR2 module, PR3 route, PR4 enable",
        "Should I just say UNVERIFIED and let user test?",
    ]

    def test_kept_phrases_not_blocked_by_any_hook(self):
        for phrase in self.KEPT_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(_run_pre_ask(phrase), f"pre-ask blocked: {phrase!r}")
                self.assertFalse(_run_stop_prose(phrase), f"stop-prose blocked: {phrase!r}")
                self.assertFalse(
                    _run_stop_untracked_work(phrase),
                    f"stop-untracked-work blocked: {phrase!r}")


class TestNoLeftoverCounterFiles(TestCase):
    """#202: this file's blocking probes each mint a fresh session id and
    drive a REAL hook that writes a retry-counter file for a session that
    just blocked — by far the largest generator of the ~2418 leaked
    `/tmp/airuleset-*-block-*` files measured on this box in one real day
    (2026-07-30), since every one of the "must block" assertions above
    leaves its own counter behind (both hooks only clear it on a CLEAN
    stop). A fixed uuid makes the exact counter path predictable so its
    presence/absence can be asserted directly."""

    def test_stop_prose_probe_leaves_no_counter_file_behind(self):
        fixed = uuid.UUID("12345678-1234-5678-1234-567812345678")
        with m.patch("uuid.uuid4", return_value=fixed):
            self.assertTrue(
                _run_stop_prose("Should I merge despite the failing check?"))
        counter = Path(
            "/tmp/airuleset-stop-block-test-dropped-row-%s" % fixed.hex[:10])
        self.addCleanup(lambda: counter.unlink(missing_ok=True))
        self.assertFalse(
            counter.exists(),
            "#202: the blocking probe left a retry-counter file in /tmp: %s"
            % counter)

    def test_untracked_work_probe_leaves_no_counter_file_behind(self):
        fixed = uuid.UUID("87654321-4321-8765-4321-876543218765")
        with m.patch("uuid.uuid4", return_value=fixed):
            self.assertTrue(
                _run_stop_untracked_work(
                    "Give the word and I'll create the issues"))
        counter = Path(
            "/tmp/airuleset-untracked-work-block-test-dropped-row-ndw-%s"
            % fixed.hex[:10])
        self.addCleanup(lambda: counter.unlink(missing_ok=True))
        self.assertFalse(
            counter.exists(),
            "#202: the blocking probe left a retry-counter file in /tmp: %s"
            % counter)

    def test_probes_still_mint_a_fresh_session_id_per_call(self):
        # The other half of #202's acceptance criteria: cleanup must not
        # come at the cost of the fresh-id-per-probe property (a shared
        # session id would silently stop blocking after the hook's retry
        # cap, per the existing docstring on _run_stop_untracked_work).
        sids_seen = []
        orig_uuid4 = uuid.uuid4

        def _recording_uuid4():
            val = orig_uuid4()
            sids_seen.append(val)
            return val

        with m.patch("uuid.uuid4", side_effect=_recording_uuid4):
            _run_stop_prose("Should I merge despite the failing check?")
            _run_stop_prose("Should I merge despite the failing check?")
        self.assertEqual(len(sids_seen), 2)
        self.assertNotEqual(sids_seen[0], sids_seen[1])


if __name__ == "__main__":
    main()
