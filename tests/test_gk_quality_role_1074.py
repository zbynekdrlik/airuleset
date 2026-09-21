"""#1074 — the third gk session `gk-quality`: a declared SEQUENTIAL window with
its own role, obligation set and /goal loop, provisioned like the INFRA window.

One new role token end-to-end (Approach 1, main-authored design):
 (a) fleet — `WINDOW_ROLES` accepts `quality`; gk declares THREE windows, the
     third `gk-quality` at `~/devel/odoo/odoo-erp-quality`, sequential; the
     data-driven `resolve_concurrency` resolves it to `("sequential","quality",
     "role")` while the review window stays parallel and gk-infra is unchanged.
 (b) work class + partition — `work_class` maps the `gk-quality` label to the
     new `quality` class (precedence: gk-quality -> quality; infra/
     architecture-rework -> infra; else independent; None -> infra unchanged);
     `_apply_role_filter` partitions review/infra/quality DISJOINTLY — the
     #1065 invariant extended: U(review)+U(infra)+U(quality) == U(all); the
     footer status row names the quality role.
 (c) goal loop — `goal_registry.ROLES` includes `quality`; the
     `(full, sequential, quality)` variant renders the charter clauses within
     the 4000 cap with >= 150 headroom, carries no turn cap, and is the 10th
     locked `goal-inventory --check` variant.
 (d) provisioning — the tmux create body renders the third gk window
     (data-driven from the declaration, no hardcoded install step).
 (e) doctrine — the quality role + `gk-quality` label are documented in
     `skills/statusline-vocabulary-deep/DEEP-1.md`.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import airuleset  # noqa: E402
import cli_concurrency  # noqa: E402
import cli_fleet  # noqa: E402
import cli_quals_cmd  # noqa: E402
import cli_tmux_provisioning as prov  # noqa: E402
import cli_work_class as wc  # noqa: E402
import goal_registry as gr  # noqa: E402

GK_HOME = "/home/gatekeeper"
ODOO = "zbynekdrlik/odoo-erp"


# --------------------------------------------------------------------------- #
# (a) FLEET — the third declared window + the data-driven resolver
# --------------------------------------------------------------------------- #
class TestGkDeclaresThreeWindows(TestCase):
    def _gk(self):
        return cli_fleet.box_windows("gatekeeper")

    def test_window_roles_accepts_quality(self):
        self.assertIn("quality", cli_fleet.WINDOW_ROLES)

    def test_validate_windows_accepts_role_quality(self):
        w = [{"name": "gk-quality", "cwd": "devel/odoo/odoo-erp-quality",
              "role": "quality", "mode": "sequential"}]
        self.assertEqual(cli_fleet.validate_windows(w), [])

    def test_gk_declares_review_infra_and_quality(self):
        names = [x["name"] for x in self._gk()]
        self.assertEqual(names, ["gk", "gk-infra", "gk-quality"])

    def test_the_quality_window_shape(self):
        q = [x for x in self._gk() if x["name"] == "gk-quality"][0]
        self.assertEqual(q["cwd"], "~/devel/odoo/odoo-erp-quality")
        self.assertEqual(q["role"], "quality")
        self.assertEqual(q["mode"], "sequential")

    def test_gk_declaration_validates(self):
        self.assertEqual(cli_fleet.validate_windows(self._gk()), [])

    def test_quality_window_resolves_sequential_quality_by_cwd(self):
        mode, role, src = cli_concurrency.resolve_concurrency(
            GK_HOME + "/devel/odoo/odoo-erp-quality",
            windows=self._gk(), home=GK_HOME)
        self.assertEqual((mode, role, src), ("sequential", "quality", "role"))

    def test_review_window_still_parallel(self):
        mode, role, src = cli_concurrency.resolve_concurrency(
            GK_HOME + "/devel/odoo/odoo-erp",
            windows=self._gk(), home=GK_HOME)
        self.assertEqual((mode, role, src), ("parallel", "review", "role"))

    def test_infra_window_unchanged(self):
        mode, role, src = cli_concurrency.resolve_concurrency(
            GK_HOME + "/devel/odoo/odoo-erp-infra",
            windows=self._gk(), home=GK_HOME)
        self.assertEqual((mode, role, src), ("sequential", "infra", "role"))

    def test_quality_and_infra_siblings_never_cross_match(self):
        # sibling dirs (odoo-erp / odoo-erp-infra / odoo-erp-quality) each pin
        # to their OWN window thanks to the os.sep containment boundary.
        for tail, want in (("odoo-erp", "review"),
                           ("odoo-erp-infra", "infra"),
                           ("odoo-erp-quality", "quality")):
            r = cli_concurrency.resolve_role(
                GK_HOME + "/devel/odoo/" + tail,
                windows=self._gk(), home=GK_HOME)
            self.assertEqual(r, want, "%s -> %r != %r" % (tail, r, want))


# --------------------------------------------------------------------------- #
# (b) WORK CLASS + the disjoint role partition
# --------------------------------------------------------------------------- #
def _labels(*names):
    return [{"name": n} for n in names]


class TestWorkClassQuality(TestCase):
    def test_gk_quality_label_is_quality_class(self):
        self.assertEqual(wc.work_class(ODOO, _labels("gk-quality")), "quality")

    def test_quality_precedence_over_infra(self):
        # gk-quality WINS over a co-present infra label (a quality ticket that
        # also happens to touch infra belongs to the quality window).
        self.assertEqual(
            wc.work_class(ODOO, _labels("gk-quality", "infra")), "quality")

    def test_infra_still_infra(self):
        self.assertEqual(wc.work_class(ODOO, _labels("infra")), "infra")

    def test_architecture_rework_still_infra(self):
        self.assertEqual(
            wc.work_class(ODOO, _labels("architecture-rework")), "infra")

    def test_unlabelled_is_independent(self):
        self.assertEqual(wc.work_class(ODOO, []), "independent")

    def test_none_labels_fail_safe_infra(self):
        self.assertEqual(wc.work_class(ODOO, None), "infra")

    def test_airuleset_repo_is_infra_even_with_quality_label(self):
        # the whole fleet-harness repo is infra regardless of any label.
        self.assertEqual(
            wc.work_class("zbynekdrlik/airuleset", _labels("gk-quality")),
            "infra")


class TestApplyRoleFilterPartition(TestCase):
    """_apply_role_filter is the ROUTING resolver — tested at the function level
    (no argparse), passing the slug directly so no gh call is made."""

    def _rows(self):
        return {
            1: {"labels": _labels()},                    # independent
            2: {"labels": _labels("infra")},             # infra
            3: {"labels": _labels("gk-quality")},        # quality
            4: {"labels": _labels("architecture-rework")},  # infra
            5: {"labels": _labels("gk-quality", "infra")},  # quality (precedence)
        }

    def _keys(self, role):
        return set(cli_quals_cmd._apply_role_filter(
            self._rows(), "/root", role, slug=ODOO).keys())

    def test_quality_keeps_only_quality_rows(self):
        self.assertEqual(self._keys("quality"), {3, 5})

    def test_infra_keeps_only_infra_rows(self):
        self.assertEqual(self._keys("infra"), {2, 4})

    def test_review_excludes_quality_and_infra(self):
        self.assertEqual(self._keys("review"), {1})

    def test_disjoint_union_covers_every_row(self):
        rev, inf, qual = (self._keys("review"), self._keys("infra"),
                          self._keys("quality"))
        self.assertEqual(rev | inf | qual, set(self._rows().keys()))
        # pairwise disjoint — every row in EXACTLY one window.
        self.assertEqual(rev & inf, set())
        self.assertEqual(rev & qual, set())
        self.assertEqual(inf & qual, set())

    def test_no_role_is_a_noop(self):
        self.assertEqual(
            cli_quals_cmd._apply_role_filter(self._rows(), "/root", None),
            self._rows())


class TestFooterConcurrencyRow(TestCase):
    def _gk(self):
        return cli_fleet.box_windows("gatekeeper")

    def test_goal_variant_label_quality(self):
        # #1038 convention is <role>/<mode>, so the quality window's variant.
        self.assertEqual(
            cli_concurrency.goal_variant_label("sequential", "quality"),
            "quality/sequential")

    def test_status_row_names_the_quality_role(self):
        row = cli_concurrency.concurrency_status_row(
            GK_HOME + "/devel/odoo/odoo-erp-quality",
            windows=self._gk(), home=GK_HOME)
        self.assertIn("concurrency: sequential", row)
        self.assertIn("role=quality", row)


# --------------------------------------------------------------------------- #
# (c) GOAL LOOP — the (full, sequential, quality) variant
# --------------------------------------------------------------------------- #
class TestQualityGoalVariant(TestCase):
    def _q(self):
        return gr.render_goal_line("full", "sequential", "quality")

    def test_roles_includes_quality(self):
        self.assertIn("quality", gr.ROLES)

    def test_renders_and_is_sequential(self):
        line = self._q()
        self.assertIn("ONE unit at a time", line)
        self.assertNotIn("CONTINUOUS REFILL", line)

    def test_carries_the_charter_clauses(self):
        line = self._q()
        # own the lenses + the hand-off gate promotions mechanically
        self.assertIn("QUALITY ROLE", line)
        self.assertIn("subdev_handoff_gate.py", line)
        # root-cause every bounce / release-break into a guard; rounds trend down
        self.assertIn("audit_bounce_rule_updates.py --rounds", line)
        # fresh-prod-copy + E2E evidence enforced at hand-off
        self.assertIn("E2E", line)
        # a read-only prod fact source for the streams
        self.assertIn("prod", line.lower())
        # obligation proof = the quality-scoped count
        self.assertIn("core-quals --role quality --count", line)

    def test_a_reintroduced_solved_problem_is_a_rule_defect(self):
        self.assertIn("rule defect", self._q().lower())

    def test_no_turn_cap(self):
        self.assertIsNone(gr._TURN_CAP_RE.search(self._q()))

    def test_arms_under_the_cap_with_headroom(self):
        line = self._q()
        self.assertLessEqual(
            len(line), gr.GOAL_ARM_CHAR_CAP - 150,
            "quality variant headroom < 150 (len=%d)" % len(line))

    def test_quality_is_gk_full_only(self):
        # like review, the quality charter hardcodes full-authority gk semantics.
        for auth in ("branch-merge", "fork-no-merge"):
            with self.assertRaises(ValueError):
                gr.render_goal_line(auth, "sequential", "quality")

    def test_variant_check_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_ten_locked_variants_including_quality(self):
        specs = gr.variant_specs()
        self.assertIn(("full", "sequential", "quality"), specs)
        self.assertEqual(len(specs), 10)


# --------------------------------------------------------------------------- #
# (d) PROVISIONING — the tmux create body renders the third window
# --------------------------------------------------------------------------- #
class TestTmuxCreateBodyRendersThreeWindows(TestCase):
    def test_create_body_renders_the_quality_window(self):
        body = prov._managed_windows_create_body(
            cli_fleet.box_windows("gatekeeper"))
        # the non-primary windows are gk-infra AND gk-quality.
        self.assertIn("gk-infra", body)
        self.assertIn("gk-quality", body)
        self.assertIn("odoo-erp-quality", body)
        # two create blocks (one per non-primary window).
        self.assertEqual(body.count("new-window"), 2)


# --------------------------------------------------------------------------- #
# (e) DOCTRINE — the quality role is documented in DEEP-1
# --------------------------------------------------------------------------- #
class TestDeep1Doctrine(TestCase):
    def test_deep1_names_the_quality_role_and_label(self):
        p = _ROOT / "skills" / "statusline-vocabulary-deep" / "DEEP-1.md"
        text = p.read_text(encoding="utf-8")
        self.assertIn("gk-quality", text)
        self.assertIn("quality", text)
        self.assertIn("1074", text)


if __name__ == "__main__":
    main()
