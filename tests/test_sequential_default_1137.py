"""#1137 — every target runs in the natural, SEQUENTIAL mode by default.

Owner ROZHODNUTÉ 2026-09-30 (verbatim on the issue): "cize seqvencny normal
mod nech je default stav vsetkych targetov" — the main agent works one theme's
tickets with focus, uses subagents where they naturally help, and never
saturates lane slots. A pane runs ``parallel`` ONLY through an explicit
``mode: parallel`` declaration; none is declared.

Locks, per the design comment (Approach 1):
  * the resolver default + a declared window without ``mode`` -> sequential,
    an explicit parallel declaration still -> parallel, no fleet window and
    not the airuleset checkout itself declares parallel;
  * the goal renderer's default / fallback -> the sequential variant, the
    parallel variant renders only on an explicit request, and the gk review
    window arms the SEQUENTIAL review variant (clause (d) in its one-unit form);
  * the refill / queue-arrival nudges skip an UNDECLARED pane and fall back to
    sequential on a resolver error, while an ENDED supervisor pane (#1178,
    proven-empty backlog, idle) still gets its awareness nudge.
"""
import json
import sys
import tempfile
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_concurrency as cc  # noqa: E402
import cli_fleet  # noqa: E402
import goal_registry as gr  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402

GK_HOME = "/home/gatekeeper"


def _project(mode):
    d = tempfile.mkdtemp()
    claude = Path(d) / ".claude"
    claude.mkdir()
    (claude / "lane-resources.json").write_text(json.dumps({"mode": mode}))
    return d


class TestResolverDefault(TestCase):
    def test_default_mode_constants_are_sequential(self):
        self.assertEqual(cc.DEFAULT_MODE, "sequential")
        self.assertEqual(gr.DEFAULT_MODE, "sequential")

    def test_undeclared_pane_resolves_sequential_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cc.resolve_concurrency(d, windows=[], home=GK_HOME),
                             ("sequential", None, "default"))

    def test_declared_window_without_mode_resolves_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            w = [{"name": "x", "cwd": d, "role": None}]
            self.assertEqual(cc.resolve_concurrency(d, windows=w, home=GK_HOME),
                             ("sequential", None, "role"))

    def test_explicit_parallel_declaration_still_resolves_parallel(self):
        d = _project("parallel")
        self.assertEqual(cc.resolve_concurrency(d, windows=[], home=GK_HOME),
                         ("parallel", None, "project"))

    def test_no_fleet_window_declares_parallel(self):
        for entry in cli_fleet.REMOTE_HOSTS:
            for w in entry.get("windows") or []:
                self.assertNotEqual(w.get("mode"), "parallel",
                                    "%s window %s declares parallel"
                                    % (entry.get("name"), w.get("name")))

    def test_gk_review_window_is_sequential(self):
        self.assertEqual(
            cc.resolve_concurrency(GK_HOME + "/devel/odoo/odoo-erp",
                                   windows=cli_fleet.box_windows("gatekeeper"),
                                   home=GK_HOME),
            ("sequential", "review", "role"))

    def test_airuleset_checkout_itself_resolves_sequential(self):
        # the controller is named explicitly in the ruling; its project file
        # no longer declares parallel.
        self.assertEqual(cc.read_project_mode(str(REPO)), "sequential")
        self.assertEqual(cc.resolve_mode(str(REPO), windows=[]), "sequential")


class TestGoalRendererDefault(TestCase):
    def test_default_render_is_the_sequential_variant(self):
        for p in gr.PROFILES:
            self.assertEqual(gr.render(p), gr.render_goal_line(p, "sequential"))
            self.assertEqual(gr.render_goal_line(p), gr.render(p))
            self.assertIn("ONE unit at a time", gr.render(p))
            self.assertNotIn("CONTINUOUS REFILL", gr.render(p))

    def test_parallel_variant_renders_only_on_explicit_request(self):
        for p in gr.PROFILES:
            line = gr.render_goal_line(p, "parallel", None)
            self.assertIn("CONTINUOUS REFILL", line)
            self.assertNotEqual(line, gr.render(p))

    def test_parallel_project_uses_the_variant_renderer(self):
        d = _project("parallel")
        got = goal.goal_template_for("full", d, path=str(Path(d) / "no-skill.md"))
        self.assertEqual(got, gr.render_goal_line("full", "parallel"))

    def test_sequential_pane_serves_the_shipped_default_line(self):
        d = _project("sequential")
        got = goal.goal_template_for("full", d, path=gr.skill_path())
        self.assertEqual(got, gr.render_goal_line("full", "sequential"))

    def test_resolver_error_falls_back_to_the_sequential_default(self):
        logs = []
        with m.patch.object(cc, "resolve_concurrency",
                            side_effect=RuntimeError("boom")):
            got = goal.goal_template_for("full", "/x", path=gr.skill_path(),
                                         logs=logs)
        self.assertEqual(got, gr.render_goal_line("full", "sequential"))
        self.assertTrue(any("falling back to default (sequential)" in ln
                            for ln in logs), logs)


class TestNaturalDoctrine(TestCase):
    """The default clause and the always-on doctrine state the natural mode:
    the main may implement a unit itself (no forced worker per unit), and
    parallel lanes are an explicit declaration only."""

    def test_default_clause_does_not_force_a_worker_per_unit(self):
        clause = gr._SEQUENTIAL_SATURATION
        self.assertIn("one theme with focus", clause)
        self.assertIn("implement it yourself or with one worker", clause)
        self.assertNotIn("ONE unit at a time: dispatch", clause)
        for p in gr.PROFILES:
            self.assertIn(clause, gr.render(p))

    def test_skill_body_unit_is_not_forced_through_a_worker(self):
        body = (REPO / gr.SKILL_REL).read_text(encoding="utf-8")
        self.assertIn(gr._SEQUENTIAL_SATURATION, body)
        self.assertNotIn("One `autopilot-worker` unit is dispatched", body)
        # review round 2: no stale quote of the old dispatch-per-unit clause.
        self.assertNotIn("ONE unit at a time: dispatch", body)
        self.assertNotIn("Main session stays thin** — it holds only", body)
        self.assertIn("the main may implement a unit itself, #1137", body)

    def test_always_on_autopilot_pointer_does_not_force_a_worker(self):
        tooling = (REPO / "modules/core/claude-code-tooling.md").read_text(
            encoding="utf-8")
        self.assertNotIn("dispatches each issue to an **in-session background", tooling)
        self.assertIn("works each issue itself or, where that naturally helps, "
                      "through an **in-session background", tooling)
        self.assertNotIn("no push)", tooling)

    def test_always_on_modules_state_the_sequential_default(self):
        tooling = [ln for ln in (REPO / "modules/core/claude-code-tooling.md")
                   .read_text(encoding="utf-8").splitlines()
                   if "Parallelism is the working model" in ln][0]
        self.assertIn("DEFAULT is `sequential` on every box (#1137", tooling)
        self.assertIn("declares `mode: parallel`", tooling)
        branch = (REPO / "modules" / "git" / "two-branch-workflow.md").read_text(
            encoding="utf-8")
        self.assertNotIn("parallel DISPATCH via `isolation: \"worktree\"` is "
                         "the `autopilot` skill's DEFAULT", branch)
        self.assertIn("the `autopilot` skill's DEFAULT is SEQUENTIAL", branch)


class TestSequentialReviewVariant(TestCase):
    """The gk review window arms the SEQUENTIAL review variant: the one-unit
    clause instead of the refill clause, and review clause (d) in its one-unit
    form (never "lanes plním … vždy keď existuje workable")."""

    def _seq(self):
        return gr.render_goal_line("full", "sequential", "review")

    def test_one_unit_not_refill(self):
        self.assertIn("ONE unit at a time", self._seq())
        self.assertNotIn("CONTINUOUS REFILL", self._seq())

    def test_clause_d_is_the_one_unit_form(self):
        line = self._seq()
        self.assertNotIn("lanes plním DISPATCHOVATEĽNÝMI jednotkami", line)
        self.assertIn("jedna DISPATCHOVATEĽNÁ jednotka naraz", line)
        self.assertIn("sloty nikdy nenasycujem", line)
        self.assertIn("čakanie na CI nikdy nedrží slot", line)

    def test_every_other_review_clause_is_kept(self):
        line = self._seq()
        for tok in ("SUBDEVS NIKDY NEČAKAJÚ NA GK",
                    "gk verdikt alebo akčný komentár do 1 h",
                    "release train nikdy nestojí",
                    "core-quals --role review --count",
                    "bez akéhokoľvek turn limitu"):
            self.assertIn(tok, line)

    def test_arms_under_the_cap_with_headroom(self):
        self.assertLessEqual(len(self._seq()), gr.GOAL_ARM_CHAR_CAP - 150)

    def test_janitor_and_check_cover_both_review_modes(self):
        variants = gr.all_goal_line_variants()
        self.assertIn(self._seq(), variants)
        self.assertIn(gr.render_goal_line("full", "parallel", "review"), variants)
        self.assertEqual(gr.variant_check(), [])

    def test_gk_review_window_arms_the_sequential_review_variant(self):
        mode, role, _src = cc.resolve_concurrency(
            GK_HOME + "/devel/odoo/odoo-erp",
            windows=cli_fleet.box_windows("gatekeeper"), home=GK_HOME)
        got = goal.goal_template_for("full", "/x", role=role, mode=mode)
        self.assertEqual(got, self._seq())


def _lane_nudge(cwd):
    with m.patch("airuleset.resolve_authority", return_value="full"):
        return goal.goal_lane_occupancy_nudge(
            100000, lambda *a, **k: None, {}, "sid", cwd, "111", "captured",
            "/tmp/t.jsonl", 100000, "loc", None, False, set(), "/tmp/proj",
            backlog_fetch=lambda cwd: 5, state={}, sleep_fn=lambda s: None)


def _arrival(cwd, called, **kw):
    with m.patch("airuleset.resolve_authority", return_value="full"):
        return qa.goal_queue_arrival_recheck(
            100000, lambda *a, **k: None, {}, "sid", cwd, "%9", "/tmp/t.jsonl",
            "sess:0", False, set(),
            queue_fetch=lambda c: called.append(c) or [1], state={},
            sleep_fn=lambda *a, **k: None, **kw)


class TestNudgesOnTheDefault(TestCase):
    def test_undeclared_pane_skips_the_refill_nudge(self):
        with tempfile.TemporaryDirectory() as cwd:
            logs, owns = _lane_nudge(cwd)
        self.assertFalse(owns)
        self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)

    def test_undeclared_pane_skips_the_armed_queue_arrival(self):
        called = []
        with tempfile.TemporaryDirectory() as cwd:
            logs = _arrival(cwd, called)
        self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)
        self.assertEqual(called, [])

    def test_resolver_error_falls_back_to_sequential_for_both(self):
        called = []
        with m.patch.object(cc, "resolve_mode", side_effect=RuntimeError("boom")):
            lane_logs, owns = _lane_nudge("/x")
            qa_logs = _arrival("/x", called)
        self.assertFalse(owns)
        for logs in (lane_logs, qa_logs):
            self.assertTrue(any("treating as sequential" in ln for ln in logs), logs)
            self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)
        self.assertEqual(called, [])

    def test_ended_supervisor_pane_still_gets_the_awareness_fetch(self):
        # #1178: an ENDED supervisor (proven-empty backlog, idle — the
        # `deliver_hold` path) is told about new tickets even on a sequential
        # pane; it is "next", never a refill.
        called = []
        cwd = _project("sequential")
        logs = _arrival(cwd, called, deliver_hold=lambda: "not-idle-prompt")
        self.assertFalse(any("skip:sequential-mode" in ln for ln in logs), logs)
        self.assertEqual(called, [cwd])


if __name__ == "__main__":
    main()
