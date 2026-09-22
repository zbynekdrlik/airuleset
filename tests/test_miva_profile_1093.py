"""#1093 — the miva board profile carries its canonical stage set (odoo-erp
#7101 / #7454), and the #1018 Verifikácia handover-note SHAPE check recognises
the miva awaiting-verification stage `Čaká` WITHOUT firing on the everyday
Slovak verb `čaká`.

Root cause (main ed4cda36, v0.1.384): the always-injected CORE
``client-board-tasks.md`` row 13 still said "montalu shape, until the owner
rules otherwise" — written before the miva boards existed — so a miva stream
moved tasks to montalu stage names that do not exist on its boards; and
``stop-check-prose-violations.sh`` ``VERIF_STAGE_RX`` knew only the
montalu/slovnormal stage names, so a miva `presunul som do Čaká` self-report was
never shape-checked. The canonical miva set is
``Nové → Požadujú sa zmeny → V riešení → Čaká → Hotové → Zrušené`` (awaiting
verification = ``Čaká``, question = ``Požadujú sa zmeny``, ``Hotové`` moved by
the OWNER only after client confirmation, ``Zrušené`` never set by a stream).

Each assertion is a STATEMENT lock (against the isolated miva row line / a real
hook drive), never a bare file-wide substring.
"""

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MSG = ROOT / "skills" / "odoo-client-messaging"
CORE = MSG / "client-board-tasks.md"
STAGES = MSG / "client-board-stages.md"
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# The six canonical miva stages, in order (odoo-erp #7101 / #7454).
MIVA_STAGES = ["Nové", "Požadujú sa zmeny", "V riešení", "Čaká", "Hotové",
               "Zrušené"]
MIVA_STAGE_CELL = " → ".join(MIVA_STAGES)


def _strip_fm(t):
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            nl = t.find("\n", end + 1)
            if nl != -1:
                t = t[nl + 1:].lstrip("\n")
    return t.strip()


def _miva_row_line():
    """The single CORE per-board-profile row line for the miva board."""
    for ln in CORE.read_text(encoding="utf-8").splitlines():
        s = ln.lstrip("| ").rstrip()
        if s.startswith("**miva**"):
            return ln
    return None


def _verif_stage_rx():
    """The VERIF_STAGE_RX value out of the live hook (the ONE source of truth)."""
    m = re.search(r"^VERIF_STAGE_RX='(.*)'", HOOK.read_text(encoding="utf-8"),
                  re.M)
    return m.group(1) if m else None


def _rx_matches(rx, text):
    """Case-sensitive ERE match under the exact locale the hook uses."""
    p = subprocess.run(
        ["grep", "-qE", rx], input=text,
        capture_output=True, text=True,
        env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"})
    return p.returncode == 0


# ---- REAL #1018 hook drive (same seam as test_verifikacia_shape_1018.py) ---- #

def _run(msg, sid=None):
    sid = sid or ("miva1093-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# read-back evidence ("0 escaped") isolates the ONLY possible blocker to the
# missing Verifikácia sections (the sibling #916/#978 checks are satisfied).
SK_MIVA_MOVED_NO_SECTIONS = (
    "Presunul som úlohu na klientskom project.task boarde do stage Čaká a "
    "odoslal som handover poznámku klientovi na miva PROD. "
    "Read-back: 0 escaped správ, body_is_html verified."
)

SK_MIVA_MOVED_WITH_SECTIONS = (
    "Presunul som úlohu do Čaká a odoslal handover poznámku na project.task "
    "boarde. Read-back: 0 escaped správ.\n"
    "Čo: cenník je nasadený.\n"
    "Kde: Predaj ▸ Cenník — https://erp.example.cloud/odoo/action-123/45\n"
    "Čo skúsiť: otvorte cenník a skontrolujte ceny.\n"
    "stačí 👍"
)

# lowercase `čaká` is the ordinary verb "waits" — even next to a past-tense
# posting verb + an Odoo anchor, the #1018 shape check must NOT fire.
SK_MIVA_LOWERCASE_VERB = (
    "Presunul som úlohu na project.task boarde, teraz úloha čaká na klienta. "
    "Read-back: 0 escaped."
)

# #1093 review (BOTH adversarial reviewers): a CAPITALISED sentence-initial verb
# `Čaká na …` / `Čaká sa …` — with a past-tense move/post verb + an Odoo anchor
# nearby — is ordinary status prose (a move to a DIFFERENT stage, or a plain
# question-post), NOT a move to the awaiting-verification stage. It must NOT be
# gated: the miva stage requires a directional move cue (do / →) which none of
# these carry.
SK_MIVA_CAPITAL_VERB_MOVED_ELSEWHERE = (
    "Presunul som úlohu do stage V riešení na project.task boarde. "
    "Čaká na klienta, kým potvrdí zmeny. Read-back: 0 escaped."
)
SK_MIVA_CAPITAL_VERB_QUESTION = (
    "Odoslal som otázku klientovi na project.task boarde. "
    "Čaká na jeho odpoveď. Read-back: 0 escaped."
)
SK_MIVA_CAPITAL_VERB_REFLEXIVE = (
    "Presunul som úlohu späť na project.task boarde. "
    "Čaká sa na odpoveď klienta. Read-back: 0 escaped."
)


class TestMivaCoreRow(TestCase):
    """The CORE miva row carries the six canonical stages, in order."""

    def test_miva_row_exists(self):
        self.assertIsNotNone(_miva_row_line(),
                             "no `**miva**` per-board-profile row in the CORE")

    def test_miva_row_has_canonical_stage_cell(self):
        row = _miva_row_line()
        self.assertIn(
            MIVA_STAGE_CELL, row,
            f"the CORE miva row must carry the canonical stage cell "
            f"{MIVA_STAGE_CELL!r}; row is: {row!r}")

    def test_miva_row_stages_in_order(self):
        row = _miva_row_line()
        idx = [row.find(s) for s in MIVA_STAGES]
        self.assertTrue(all(i >= 0 for i in idx),
                        f"a canonical miva stage is missing from the row: {row!r}")
        self.assertEqual(idx, sorted(idx),
                         f"the miva stages are out of canonical order: {row!r}")

    def test_miva_row_no_longer_montalu_placeholder(self):
        row = _miva_row_line()
        self.assertNotIn("montalu shape", row,
                         "the miva row still carries the pre-#1093 "
                         "'montalu shape, until the owner rules otherwise' "
                         "placeholder")


class TestMivaStagesCompanion(TestCase):
    """client-board-stages.md names Čaká/Požadujú sa zmeny for miva and no
    longer mis-groups miva with montalu's Verifikácia stage."""

    @classmethod
    def setUpClass(cls):
        cls.text = STAGES.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def _line_with(self, *tokens):
        return [ln for ln in self.lines if all(t in ln for t in tokens)]

    def test_caka_is_mivas_awaiting_verification(self):
        # tied to the miva BULLET's distinctive phrasing so the rule-14 caveat
        # line (which also carries miva+Čaká) cannot lend it false teeth (#498).
        self.assertTrue(
            self._line_with("miva", "awaiting client verification", "Čaká"),
            "the miva bullet must name `Čaká` as its awaiting-verification stage")

    def test_pozaduju_is_mivas_question_stage(self):
        self.assertTrue(
            self._line_with("client-question", "Požadujú sa zmeny"),
            "the miva bullet must name `Požadujú sa zmeny` as its question stage")

    def test_miva_not_grouped_with_verifikacia(self):
        # miva's awaiting-verification stage is Čaká now, never Verifikácia —
        # no physical line may claim both (the pre-#1093 contradiction).
        self.assertFalse(
            self._line_with("miva", "Verifikácia"),
            "a line still groups miva with montalu's `Verifikácia` stage — "
            "the contradiction #1093 removes")

    def test_zrusene_never_set_by_stream(self):
        self.assertTrue(
            self._line_with("Zrušené", "never set by a stream"),
            "the miva bullet must name `Zrušené` (never set by a stream)")

    def test_rule3_enumeration_includes_caka(self):
        # rule 3's awaiting-verification parenthetical lists all three board
        # stage names (the ONLY line carrying all of Verifikácia+Na overenie+Čaká)
        self.assertTrue(
            self._line_with("Verifikácia", "Na overenie", "Čaká"),
            "rule 3 must enumerate Čaká alongside Verifikácia / Na overenie")

    def test_rule14_table_has_board_mapping_caveat(self):
        # the montalu-worded event→phase table carries a caveat mapping to each
        # board's own stages, so a miva stream is not sent to a non-existent one
        self.assertTrue(
            self._line_with("montalu vocabulary"),
            "rule 14's table needs the board-vocabulary mapping caveat (#1093)")

    def test_stage_rules_still_exactly_once_in_stages(self):
        # union lock, focused on the file this lane edits: rules 3/5/6 live in
        # STAGES and nowhere else in the CORE (#1102 partition).
        core = CORE.read_text(encoding="utf-8")
        for header in ("### 3. Handover chatter note",
                       "### 5. Assignee",
                       '### 6. "Done" stage'):
            self.assertEqual(self.text.count(header), 1,
                             f"{header!r} must appear exactly once in STAGES")
            self.assertNotIn(header, core,
                             f"{header!r} leaked back into the CORE (#1102)")


class TestVerifStageRegex(TestCase):
    """VERIF_STAGE_RX recognises the miva stage `Čaká` ONLY after a directional
    move cue (do / → / ->), never the everyday verb `Čaká`/`čaká` — even
    capitalised at the start of a sentence (`Čaká na klienta`); existing stage
    names keep matching. The move cue is what distinguishes a stage move from
    the verb (#1093 review: a bare capital token false-blocked ordinary prose)."""

    @classmethod
    def setUpClass(cls):
        cls.rx = _verif_stage_rx()

    def test_rx_extracted(self):
        self.assertIsNotNone(self.rx, "could not read VERIF_STAGE_RX from hook")

    def test_matches_caka_with_move_cue(self):
        for s in ("do Čaká", "do stage Čaká", "do stavu Čaká", "→ Čaká",
                  "-> Čaká", "do fázy Čaká"):
            self.assertTrue(_rx_matches(self.rx, s),
                            f"VERIF_STAGE_RX must match the miva stage move `{s}`")

    def test_does_not_match_bare_caka_without_cue(self):
        # a bare capitalised token is the sentence-initial verb "Čaká na …" —
        # it must NEVER be gated without a directional move cue (#1093 fix for
        # the fleet-wide false-block on ordinary status prose).
        for s in ("Čaká", "Čaká na klienta", "Čaká sa na odpoveď",
                  "Čaká, kým klient potvrdí"):
            self.assertFalse(_rx_matches(self.rx, s),
                             f"VERIF_STAGE_RX must NOT match the bare verb `{s}`")

    def test_does_not_match_lowercase_verb(self):
        self.assertFalse(_rx_matches(self.rx, "úloha čaká na klienta"),
                         "VERIF_STAGE_RX must NOT match the lowercase verb "
                         "`čaká` (fleet-wide false-block class)")

    def test_cue_is_boundary_anchored(self):
        # a WORD ENDING in "do" ("todo"/"kedo") followed by "Čaká" carries the
        # substring "do Čaká" but is NOT the move cue — the cue is anchored at a
        # word boundary (#1093 review 🔵-1). Real cues still match.
        for s in ("todo Čaká", "kedo Čaká", "budo Čaká"):
            self.assertFalse(_rx_matches(self.rx, s),
                             f"VERIF_STAGE_RX must NOT read the cue inside `{s}`")
        self.assertTrue(_rx_matches(self.rx, "presunul do Čaká"),
                        "a real spaced `do` cue must still match")

    def test_does_not_match_longer_words(self):
        for w in ("do Čakať", "do Čakáreň"):
            self.assertFalse(_rx_matches(self.rx, w),
                             f"VERIF_STAGE_RX must not match `{w}` (word "
                             "boundary, even with a move cue)")

    def test_existing_stage_names_still_match(self):
        for s in ("Verifikácia", "Na overenie"):
            self.assertTrue(_rx_matches(self.rx, s),
                            f"VERIF_STAGE_RX regressed on `{s}`")


class TestHookDrivesCaka(TestCase):
    """Drive the REAL #1018 Stop hook on miva `Čaká` self-reports."""

    def test_caka_move_without_sections_blocked(self):
        self.assertTrue(
            _blocked(_run(SK_MIVA_MOVED_NO_SECTIONS)),
            "a miva `presunul som do Čaká` self-report without the four "
            "handover sections must be BLOCKED (#1018)")

    def test_caka_move_with_sections_not_blocked(self):
        p = _run(SK_MIVA_MOVED_WITH_SECTIONS)
        self.assertFalse(_blocked(p), p.stdout)

    def test_lowercase_caka_not_gated(self):
        self.assertFalse(
            _blocked(_run(SK_MIVA_LOWERCASE_VERB)),
            "a lowercase `čaká` status line must NOT trigger the #1018 shape "
            "check")

    def test_capital_verb_moved_elsewhere_not_gated(self):
        self.assertFalse(
            _blocked(_run(SK_MIVA_CAPITAL_VERB_MOVED_ELSEWHERE)),
            "a task moved to V riešení with a sentence-initial verb `Čaká na "
            "klienta` must NOT be gated (#1093 false-block fix)")

    def test_capital_verb_question_not_gated(self):
        self.assertFalse(
            _blocked(_run(SK_MIVA_CAPITAL_VERB_QUESTION)),
            "a plain question-post ending `Čaká na jeho odpoveď` must NOT be "
            "gated (#1093 false-block fix)")

    def test_capital_verb_reflexive_not_gated(self):
        self.assertFalse(
            _blocked(_run(SK_MIVA_CAPITAL_VERB_REFLEXIVE)),
            "the impersonal reflexive `Čaká sa na odpoveď` must NOT be gated "
            "(#1093 false-block fix)")


class TestCoreSizeGuard(TestCase):
    def test_core_stripped_le_6000(self):
        n = len(_strip_fm(CORE.read_text(encoding="utf-8")))
        self.assertLessEqual(n, 6000,
                             f"CORE is {n} stripped chars (> 6000) — the miva "
                             "row edit must not break the #1102 size lock")


if __name__ == "__main__":
    main()
