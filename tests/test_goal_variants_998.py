"""#998 item 3 — goal renderer per (authority, role) + no-turn-cap lock.

`goal_registry.render_goal_line` appends the infra-role clause and swaps the
(B) block for the review / quality roles; NO variant carries a turn cap.
`watchdog.goal.goal_template_for` resolves the pane's role from cwd and uses the
SAME renderer for role variants (SKILL.md read for the no-role line). #1137
(owner 25.10.2026) removed the parallel/sequential mode axis: every variant
carries the one bundling sentence instead of a mode clause.
"""
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import goal_registry as gr  # noqa: E402
from watchdog import goal  # noqa: E402


BUNDLE = "one branch, one PR and one CI cycle"


class TestRenderGoalLine(TestCase):
    def test_default_variant_equals_the_shipped_skill_line(self):
        # #998-review: NOT tautological (render delegates to render_goal_line, so
        # comparing the two proves nothing) — assert the no-role variant
        # byte-equals the line SHIPPED in SKILL.md, the real drift target.
        with open(gr.skill_path(), encoding="utf-8") as fh:
            shipped = gr.shipped_lines(fh.read())
        self.assertEqual(set(shipped), set(gr.PROFILES))
        for p in gr.PROFILES:
            self.assertEqual(gr.render_goal_line(p, None), shipped[p])
            self.assertEqual(gr.render_goal_line(p), shipped[p])
            self.assertIn(BUNDLE, shipped[p])
            self.assertNotIn("ONE unit at a time", shipped[p])
            self.assertNotIn("CONTINUOUS REFILL", shipped[p])

    def test_infra_role_appends_scope_clause(self):
        line = gr.render_goal_line("full", "infra")
        self.assertIn("INFRA ROLE", line)
        self.assertIn("only tickets labelled", line)
        self.assertIn("box-maintenance steps stop", line)
        # the infra clause follows the bundle clause
        self.assertLess(line.index(BUNDLE), line.index("INFRA ROLE"))

    def test_no_variant_carries_a_turn_cap(self):
        for authority, role in gr.variant_specs():
            line = gr.render_goal_line(authority, role)
            self.assertIsNone(gr._TURN_CAP_RE.search(line),
                              "%s/%s has a turn cap" % (authority, role))

    def test_every_variant_under_budget(self):
        for authority, role in gr.variant_specs():
            line = gr.render_goal_line(authority, role)
            self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP)

    def test_variant_check_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_unknown_args_raise(self):
        with self.assertRaises(ValueError):
            gr.render_goal_line("full", "boss")
        with self.assertRaises(ValueError):
            gr.render_goal_line("nope", None)


class TestGoalTemplateFor(TestCase):
    def test_default_delegates_to_skill_md(self):
        with tempfile.TemporaryDirectory() as d:
            got = goal.goal_template_for("full", d)
            self.assertEqual(got, goal.goal_template_for_authority("full"))

    def test_explicit_role_override(self):
        got = goal.goal_template_for("full", "/x", role="infra")
        self.assertIn("INFRA ROLE", got)


# --------------------------------------------------------------------------- #
# #1000 — the review-ROLE variant. The gk review window's /goal is the
# RENDERED review variant (no hand-written FLOW text): early advisory review of
# every open stream PR + 1 h hand-off SLA + release-train-never-idles +
# per-cycle status, NO turn cap. The
# owner directive ("uz mam dost tvojich patchworkov") is ONE renderer, no
# second text source; the 4000 arm cap makes appending the clauses to the
# full template impossible (5081 > 4000), so the review role REPLACES the
# generic (B) proof + obligation block (which clause (h) supersedes) with the
# review block. Every OTHER clause stays byte-identical.
# --------------------------------------------------------------------------- #
class TestReviewRoleVariant1000(TestCase):
    def _review(self):
        return gr.render_goal_line("full", "review")

    def test_review_variant_contains_each_clause_a_to_h(self):
        line = self._review()
        # (a) infra only filed here
        self.assertIn("infra tickety (label infra) sa TU NEpracujú, iba zakladajú", line)
        # (b) subdevs never wait on gk — early advisory review within 1 h + the
        # exact advisory comment literal
        self.assertIn("SUBDEVS NIKDY NEČAKAJÚ NA GK", line)
        self.assertIn("skoré advisory review do 1 h od posledného pushu", line)
        self.assertIn("gk skoré review (advisory, pred hand-offom)", line)
        # (c) hand-off verdict/action within 1 h, montalu first
        self.assertIn("gk verdikt alebo akčný komentár do 1 h", line)
        self.assertIn("stream:montalu prvé", line)
        # (d) CI waits never block the window (#1137: no lane saturation)
        self.assertIn("čakanie na CI nikdy neblokuje ďalšiu prácu", line)
        self.assertNotIn("lanes plním", line)
        # (e) merged PR into develop; red/DIRTY gk PR -> fix lane same cycle
        self.assertIn("merged PR do develop", line)
        self.assertIn("červený/DIRTY gk PR dostane fix lane v tom istom cykle", line)
        # (f) release train never idles; every STOP reported on odoo-erp#6883
        self.assertIn("release train nikdy nestojí", line)
        self.assertIn("odoo-erp#6883", line)
        # (g) per-cycle status print — the exact command (#1137: no lanes N/cap)
        self.assertIn(
            "python3 ~/devel/airuleset/airuleset.py core-quals --role review --count",
            line)
        self.assertNotIn("lanes N/cap", line)
        # (h) review-specific DONE + no turn limit
        self.assertIn("montalu/slovnormal/miva == main", line)
        self.assertIn("bez akéhokoľvek turn limitu", line)

    def test_review_variant_is_the_gk_review_B_condition(self):
        # (h) IS the review window's (B): it labels itself as (B) for the gk
        # review window so the header's "(A) ... (B) ..." structure holds.
        line = self._review()
        self.assertIn("gk REVIEW okno", line)
        self.assertIn("(A) BLOCKED ON MY ANSWER", line)  # kept byte-identical

    def test_review_variant_carries_no_turn_cap(self):
        line = self._review()
        self.assertNotIn("stop po", line.lower())
        self.assertNotIn("stop after", line.lower())
        self.assertIsNone(gr._TURN_CAP_RE.search(line))

    def test_review_variant_arms_under_the_cap_with_headroom(self):
        # The review variant MUST actually arm in the gk window: raw <= cap,
        # and (the #384/#730 aim) headroom >= 150 so the CC ~173-char stored
        # wrapper cannot push it over 4000.
        line = self._review()
        self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP - 150,
                             "review variant headroom < 150 (len=%d)" % len(line))

    def test_review_variant_replaces_the_generic_B_machinery(self):
        # The generic (B) BACKLOG-EMPTY proof + obligation clauses are GONE
        # (clause (h) supersedes them for the review window) — proving the
        # substitution, not an append.
        line = self._review()
        for cid in ("obligation", "proof", "produce-proof", "stream-note"):
            txt = next(c for c in gr.CLAUSES if c.id == cid).text_for("full")
            self.assertNotIn(txt, line,
                             "review variant still carries generic (B) clause %s" % cid)
        # the generic proof command must be gone too (the review status uses
        # `core-quals --role review --count`, not the bare `core-quals --count`)
        self.assertNotIn("core-quals --count`", line)

    def test_review_variant_keeps_other_clauses_present_verbatim(self):
        # every NON-(B) clause the full no-role template carries appears
        # VERBATIM in the review render — this catches a DROPPED clause; a
        # REWORD (which moves both the live clause text and the render together)
        # is caught instead by the variant sha snapshot + goal-inventory
        # --check drift lock (#1000 F6: honest about what this test proves).
        line = self._review()
        for cid in ("header", "stop-a", "irreversible", "work-intro",
                    "bundle", "prod-gate",
                    "ask", "parked", "night", "bounce", "verify-sources",
                    "compact-boundary"):
            txt = next(c for c in gr.CLAUSES if c.id == cid).text_for("full")
            self.assertIn(txt, line, "review variant dropped/reworded %s" % cid)

    # Golden sha256[:24] of the 6 non-review variants (no role + infra per
    # authority) — the review role must NOT alter any of them. 24 hex chars =
    # 96 bits (kept < 32 so the entropy scanner never mistakes it for a
    # secret). Regenerate ONLY on a DELIBERATE clause edit (a shared clause
    # change moves every hash). History: #1084, #1066 lane B, #1128 and
    # #1137 (owner ROZHODNUTÉ 2026-09-30) re-goldened the mode-keyed table;
    # #1137 (owner 25.10.2026) removed the mode axis and replaced the mode
    # clauses with the bundling sentence — every variant changed. The #1137
    # review round pointed the two stream report clauses at the DEEP companion.
    _GOLDEN = {
        "full/None": "50d0bc6877013e6cd04e4b23",
        "full/infra": "80d411270fb48c2d3634ac89",
        "branch-merge/None": "a00596f8e7f82dff5e746f35",
        "branch-merge/infra": "757aab945455df84488c5509",
        "fork-no-merge/None": "232880b1afc95a7ec739ca48",
        "fork-no-merge/infra": "f3895c2c19171366d27ce959",
    }

    def test_nonreview_variants_byte_identical_snapshot(self):
        import hashlib
        for key, want in self._GOLDEN.items():
            a, r = key.split("/")
            role = None if r == "None" else r
            got = hashlib.sha256(
                gr.render_goal_line(a, role).encode()).hexdigest()[:24]
            self.assertEqual(got, want, "%s changed (not byte-identical)" % key)

    def test_review_markers_never_leak_into_nonreview_variants(self):
        for a in gr.PROFILES:
            for tok in ("REVIEW ROLE", "SUBDEVS NIKDY NEČAKAJÚ NA GK",
                        "skoré advisory review"):
                self.assertNotIn(tok, gr.render_goal_line(a, None),
                                 "%s leaked review marker %r" % (a, tok))
            self.assertNotIn("REVIEW ROLE", gr.render_goal_line(a, "infra"))

    def test_review_role_is_gk_full_only(self):
        # #1000 F5 — the review block hardcodes full-authority gk semantics, so
        # a reduced-authority review render is refused (never silently emits gk
        # clauses into a branch-merge/fork-no-merge goal).
        for a in ("branch-merge", "fork-no-merge"):
            with self.assertRaises(ValueError):
                gr.render_goal_line(a, "review")

    def test_variant_check_locks_the_review_variant_budget(self):
        # #1000 F1 — goal-inventory --check / variant_check guards the review
        # variant's budget + no-turn-cap (#1137: it is in variant_specs now).
        self.assertIn(("full", "review"), gr.variant_specs())
        self.assertEqual(gr.variant_check(), [])
        line = gr.render_goal_line("full", "review")
        self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP)

    def test_goal_template_for_review_window_uses_the_variant(self):
        # a pane resolved to role review arms the RENDERED review variant.
        got = goal.goal_template_for("full", "/x", role="review")
        self.assertIsNotNone(got)
        self.assertIn("SUBDEVS NIKDY NEČAKAJÚ NA GK", got)
        self.assertIn("core-quals --role review --count", got)


if __name__ == "__main__":
    main()
