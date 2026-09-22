"""#1074 fix-forward (doc-only) — the `/autopilot` SKILL.md PROSE must know the
THIRD gk role `quality`, not just `review`/`infra`.

Live evidence 22.9.2026 (gk-quality window, owner watching): the session read
`skills/autopilot/SKILL.md` Step 3 routing (`--role infra`/`--role review` only)
and the three by-authority `/goal STOP CONDITIONS` blocks, found no `quality`,
and rightly REFUSED to arm — while the ARM path IS role-aware
(`resolve_concurrency(.../odoo-erp-quality)` -> `("sequential","quality","role")`,
`render_goal_line("full","sequential","quality")` = the QUALITY CONTRACT loop).
The doctrine (prose) must say what the code already does. Owner verbatim on the
confusion: "to co mi odpovedal na to aka je jeho uloha je uplna blbost".

These are STATEMENT-level content locks (never an H1 substring), whitespace-
normalized so a markdown line-wrap can never break a naive `assertIn`
(the #498/#500 window-teeth discipline). Nothing here touches the three rendered
`/goal STOP CONDITIONS` lines — those stay by AUTHORITY (the role variant is
composed at ARM time by `goal_registry.render_goal_line`), and (d) proves they
are byte-identical to the committed `main` ref and that `goal-inventory --check`
stays clean.
"""

import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "autopilot" / "SKILL.md"


def _norm(s):
    """Collapse every run of whitespace (newlines included) to a single space
    and strip, so a physical markdown line-wrap in the middle of a locked
    sentence never defeats `assertIn`."""
    return " ".join(s.split())


def _text():
    return SKILL.read_text(encoding="utf-8")


def _stop_condition_lines(blob):
    return [ln for ln in blob.split("\n") if ln.startswith("/goal STOP CONDITIONS")]


# --------------------------------------------------------------------------- #
# (a) Step 3 — the TERNARY slice + the one-sentence rule
# --------------------------------------------------------------------------- #
class TestStep3TernarySlice(TestCase):
    def setUp(self):
        self.w = _norm(_text())

    def test_names_the_ternary_slice(self):
        # the slice is now review | infra | quality, not the binary infra/review
        self.assertIn("`--role review | infra | quality`", self.w)

    def test_quality_slice_routes_the_gk_quality_label(self):
        self.assertIn("`--role quality` the quality rows", self.w)

    def test_the_declared_window_rule_sentence(self):
        self.assertIn("A DECLARED window's role decides its slice", self.w)
        self.assertIn(
            "`core-quals --role <role> --count` counts exactly that window's rows",
            self.w,
        )
        # review/quality arm the roled count as their (B) proof
        self.assertIn(
            "the review and quality windows arm that roled count "
            "as their `/goal` (B) stop-proof",
            self.w,
        )

    def test_infra_window_b_proof_is_not_overclaimed_as_roled(self):
        # #1074 correctness (adversarial review 1): render_goal_line(full,
        # sequential, infra) arms the UNROLED `core-quals --count`, only
        # review/quality get the roled count. The prose must NOT claim the
        # roled count is the infra window's stop-proof.
        self.assertIn(
            "the gk-infra window still arms the unroled `core-quals --count`",
            self.w,
        )

    def test_the_three_slices_are_disjoint_and_total(self):
        self.assertIn("U(review) + U(infra) + U(quality) == U(all)", self.w)

    def test_the_stale_binary_wording_is_gone(self):
        # the old "`--role review` slices the non-infra rows" is inaccurate once
        # quality is a third partition (review keeps NEITHER infra NOR quality)
        self.assertNotIn("slices the non-infra rows", self.w)


# --------------------------------------------------------------------------- #
# (b) issue #998 arm paragraph — a SEQUENTIAL/ROLE pane never arms a default block
# --------------------------------------------------------------------------- #
class TestArmParagraphResolvedVariant(TestCase):
    def setUp(self):
        self.w = _norm(_text())

    def test_default_blocks_are_not_what_gets_armed(self):
        self.assertIn("the DEFAULT blocks below are NOT what gets armed", self.w)

    def test_goal_arm_self_arms_the_resolved_variant(self):
        self.assertIn("`goal-arm --self` arms the resolved variant", self.w)

    def test_do_not_tell_the_owner_to_paste_a_default_block(self):
        self.assertIn(
            "do NOT tell the owner to paste a default block there", self.w)

    def test_print_the_resolved_line_instead(self):
        self.assertIn("this pane resolves to `<mode>/<role>`", self.w)
        self.assertIn("the roster/`status` `goal:` row confirms it", self.w)


# --------------------------------------------------------------------------- #
# (c) the gk-quality window sits next to gk review + gk-infra in the examples
# --------------------------------------------------------------------------- #
class TestDeclaredWindowExample(TestCase):
    def test_gk_quality_named_in_the_1038_declared_window_example(self):
        # #1038 example was "gk review, gk-infra, d3 today" -> add gk-quality
        self.assertIn("gk review, gk-infra, gk-quality", _norm(_text()))


# --------------------------------------------------------------------------- #
# (d) the three /goal STOP CONDITIONS lines are UNTOUCHED + goal-inventory clean
# --------------------------------------------------------------------------- #
class TestStopConditionsUntouched(TestCase):
    def test_exactly_three_stop_condition_lines(self):
        self.assertEqual(len(_stop_condition_lines(_text())), 3)

    def test_stop_condition_lines_byte_identical_to_main(self):
        cur = _stop_condition_lines(_text())
        main_blob = None
        for ref in ("main", "origin/main"):
            r = subprocess.run(
                ["git", "show", f"{ref}:skills/autopilot/SKILL.md"],
                cwd=str(REPO), capture_output=True, text=True)
            if r.returncode == 0:
                main_blob = r.stdout
                break
        if main_blob is None:
            self.skipTest("neither main nor origin/main resolves in this checkout"
                          " -- goal-inventory --check is the standing byte lock")
        self.assertEqual(
            cur, _stop_condition_lines(main_blob),
            "the by-authority /goal STOP CONDITIONS lines MUST stay byte-identical "
            "to main -- the role variant is composed at ARM time, never in prose")

    def test_goal_inventory_check_stays_clean(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "goal-inventory", "--check"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("variants locked", r.stdout + r.stderr)


if __name__ == "__main__":
    main()
