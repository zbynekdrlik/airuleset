"""#621 — the `/goal` condition is a COMPOSED STRUCTURE, not three prose blobs.

Owner directive 2026-08-22: a `/goal` condition maintained as three independent
prose blocks cannot be reasoned about — nobody could say which goal carried
which clause, which is exactly why the saturation directive was ABSENT from all
three for months with nothing to report it. `goal_registry.py` composes each
profile's goal from named clauses; these tests LOCK clause COVERAGE (a missing
clause = red test), a COMPUTED budget (never a hand-estimate — #617 lost days on
one), the registry↔runtime-cap parity, the saturation↔compact-boundary
reconciliation, and the drift between the registry and the `/goal` lines shipped
in `skills/autopilot/SKILL.md` (which `watchdog/goal.py` reads at arm time).
"""

import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import goal_registry as gr  # noqa: E402


def skill_text():
    return (ROOT / gr.SKILL_REL).read_text(encoding="utf-8")


class TestRegistryShape(TestCase):
    def test_three_profiles(self):
        self.assertEqual(gr.PROFILES, ("full", "branch-merge", "fork-no-merge"))

    def test_each_render_is_one_pasteable_goal_line(self):
        for p in gr.PROFILES:
            line = gr.render(p)
            self.assertTrue(line.startswith("/goal "), p)
            self.assertNotIn("\n", line, "%s render must be ONE physical line" % p)

    def test_render_is_deterministic(self):
        for p in gr.PROFILES:
            self.assertEqual(gr.render(p), gr.render(p))

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            gr.render("gatekeeper")


class TestComputedBudget(TestCase):
    """#617 lost days on a hand-estimated length. The budget is COMPUTED from
    the rendered text and asserted here; an over-cap edit is a red test at build
    time, never a silent runtime refusal."""

    def test_every_profile_is_within_the_computed_cap(self):
        over = [(p, gr.length(p)) for p in gr.PROFILES if gr.over_budget(p)]
        self.assertEqual(
            over, [],
            "profiles over the %d-char /goal cap: %r" % (gr.GOAL_ARM_CHAR_CAP, over))

    def test_headroom_is_cap_minus_length(self):
        for p in gr.PROFILES:
            self.assertEqual(gr.headroom(p), gr.GOAL_ARM_CHAR_CAP - gr.length(p))

    def test_registry_cap_equals_the_runtime_gate(self):
        """The registry's budget assumption MUST equal the real gate
        watchdog/goal.py enforces, or a template could pass here and be refused
        at arm time (or vice-versa)."""
        import watchdog.goal as wg
        self.assertEqual(gr.GOAL_ARM_CHAR_CAP, wg.GOAL_ARM_CHAR_CAP)


class TestClauseCoverage(TestCase):
    """The only thing that prevents a repeat of #621: a missing clause KIND is a
    red test, not an invisible gap in an opaque blob."""

    def test_every_profile_covers_all_required_clauses(self):
        for p in gr.PROFILES:
            self.assertEqual(gr.missing_required(p), [],
                             "%s is missing required clauses" % p)

    def test_every_profile_carries_the_saturation_clause(self):
        # the #621 fix: the directive that dispatches a parallel worktree fleet
        for p in gr.PROFILES:
            ids = gr.clause_ids(p)
            self.assertIn("saturation-core", ids, p)
            self.assertIn("saturation-delivery", ids, p)

    def test_saturation_core_is_ONE_shared_definition(self):
        # owner: "jedna definícia, jedno miesto pravdy" — the reusable saturation
        # directive is a single shared string carried by all three profiles.
        c = next(c for c in gr.CLAUSES if c.id == "saturation-core")
        self.assertIsInstance(c.text, str, "saturation-core must be one shared text")
        self.assertEqual(set(c.profiles), set(gr.PROFILES))

    def test_required_set_includes_saturation_and_compact_boundary(self):
        self.assertIn("saturation-core", gr.REQUIRED_CLAUSES)
        self.assertIn("saturation-delivery", gr.REQUIRED_CLAUSES)
        self.assertIn("compact-boundary", gr.REQUIRED_CLAUSES)

    def test_clause_ids_are_unique_within_a_profile(self):
        for p in gr.PROFILES:
            ids = gr.clause_ids(p)
            self.assertEqual(len(ids), len(set(ids)), p)


class TestSaturationDirectiveIsReal(TestCase):
    """Acceptance guard: "the template contains the word worktree" is NOT the
    fix. Lock the actual directive — parallel worktree lanes to saturation,
    resource-signal backoff, serial one-per-turn integration."""

    def test_saturation_core_states_the_full_directive(self):
        core = next(c for c in gr.CLAUSES if c.id == "saturation-core").text
        for token in ("SATURATE", "PARALLEL", "isolation:worktree",
                      "autopilot-worker", "saturation", "resource signal"):
            self.assertIn(token, core)

    def test_saturation_delivery_integrates_serially(self):
        for p in gr.PROFILES:
            d = next(c for c in gr.CLAUSES if c.id == "saturation-delivery").text_for(p)
            self.assertIn("SERIALLY", d)
            self.assertIn("ONE per turn", d)


class TestSaturationReconcilesCompactBoundary(TestCase):
    """The owner required the reconciliation to be VISIBLE in the registry, not
    buried in prose: saturation dispatches parallel lanes + integrates serially
    one-per-turn; compact-boundary paces exactly ONE integration/hand-off per
    turn and states the parallel lanes keep building. They cannot contradict."""

    def test_the_reconciled_pair_is_registered(self):
        self.assertEqual(gr.SATURATION_RECONCILES_COMPACT,
                         ("saturation-core", "compact-boundary"))

    def test_compact_boundary_paces_one_per_turn_and_keeps_lanes(self):
        for p in gr.PROFILES:
            cb = next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)
            self.assertIn("compact boundary paces ONE", cb)
            self.assertIn("NOT the parallel lanes", cb)
            self.assertIn("keep building", cb)

    def test_compact_boundary_no_longer_serializes_the_loop(self):
        # the old serializing tail — "do NOT dispatch the next issue in the same
        # turn" — is exactly what suppressed the fleet; it must be gone.
        for p in gr.PROFILES:
            cb = next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)
            self.assertNotIn("do NOT dispatch the next", cb)


class TestLoadBearingInvariantsSurviveTheRefactor(TestCase):
    """Belt-and-suspenders: the composed render must still carry every
    load-bearing token the shipped `/goal` line always had (the same invariants
    tests/test_goal_backlog_proof.py locks on the shipped file), so a clause
    edit can never silently drop one."""

    SHARED = ("🏁 BACKLOG EMPTY:", "pasted OUTPUT", "HOW TO TELL A REAL COMPLETION",
              "TO PRODUCE THE PROOF", "`✅ DONE:` NEVER satisfies (B)",
              "CONTINUE", "no third answer", "❓ NEEDS YOU")

    def test_shared_tokens_present_in_every_profile(self):
        for p in gr.PROFILES:
            r = gr.render(p)
            for tok in self.SHARED:
                self.assertIn(tok, r, "%s missing %r" % (p, tok))

    def test_full_profile_specific_invariants(self):
        r = gr.render("full")
        for tok in ("core-quals --count", "printing exactly `0`",
                    "printing exactly `success`", "OBLIGED to action",
                    "needs-gatekeeper", "NOT mine to implement",
                    "Bounce lane: open tickets labeled prio:bounce"):
            self.assertIn(tok, r)
        # obligation clause must not carry prio:bounce (#307)
        oc = r[r.index("BACKLOG EMPTY"):r.index("is resolved")]
        self.assertNotIn("prio:bounce", oc)

    def test_reduced_profile_specific_invariants(self):
        for p in ("branch-merge", "fork-no-merge"):
            r = gr.render(p)
            self.assertIn("slice-quals --count", r)
            self.assertIn("RELEASED", r)
            self.assertNotIn("--assignee @me", r)

    def test_full_and_branch_merge_still_stop_after_two_real_attempts(self):
        self.assertIn("two real attempts", gr.render("full"))
        self.assertIn("two real attempts", gr.render("branch-merge"))


class TestShippedSkillMatchesRegistry(TestCase):
    """`watchdog/goal.py` reads the `/goal` lines from SKILL.md at arm time, so
    the shipped lines MUST equal render(registry). These two tests are RED until
    the shipped lines are re-rendered (`airuleset.py goal-inventory --write`) —
    they are the #621 fix's own regression test."""

    def test_shipped_lines_match_the_registry(self):
        d = gr.drift(skill_text())
        self.assertEqual([p for p, _, _ in d], [],
                         "SKILL.md /goal lines drifted from the registry: %r"
                         % [p for p, _, _ in d])

    def test_every_shipped_goal_line_carries_the_saturation_directive(self):
        import re
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", skill_text(), re.MULTILINE)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("SATURATE", line)
            self.assertIn("isolation:worktree", line)


class TestRenderIntoIsSurgical(TestCase):
    def test_render_into_only_touches_the_goal_lines(self):
        # Start from a DRIFTED copy (one full-profile token mangled, prefix
        # intact so the block still matches) so this proves surgery regardless
        # of whether the shipped file is currently in sync with the registry.
        drifted = skill_text().replace(
            gr.render("full"), "/goal STOP CONDITIONS drifted stub for the test", 1)
        self.assertNotEqual(drifted, skill_text(), "could not build a drifted fixture")
        new = gr.render_into(drifted)
        old_lines, new_lines = drifted.split("\n"), new.split("\n")
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [i for i in range(len(old_lines)) if old_lines[i] != new_lines[i]]
        self.assertTrue(changed, "render_into changed nothing")
        for i in changed:
            self.assertTrue(new_lines[i].startswith("/goal "),
                            "render_into touched a NON-/goal line: %r" % old_lines[i])
        self.assertEqual(gr.shipped_lines(new)["full"], gr.render("full"))

    def test_render_into_is_idempotent(self):
        once = gr.render_into(skill_text())
        twice = gr.render_into(once)
        self.assertEqual(once, twice)

    def test_render_into_makes_shipped_equal_render(self):
        rendered = gr.render_into(skill_text())
        shipped = gr.shipped_lines(rendered)
        for p in gr.PROFILES:
            self.assertEqual(shipped[p], gr.render(p))


class TestGoalInventoryCLI(TestCase):
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "goal-inventory", *argv],
            capture_output=True, text=True)

    def test_inventory_lists_every_profile_with_length_and_headroom(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in gr.PROFILES:
            self.assertIn(p, r.stdout)
        self.assertIn("headroom", r.stdout.lower())

    def test_inventory_json_is_machine_readable(self):
        import json
        r = self._run("--profile", "full", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["profile"], "full")
        self.assertEqual(data["length"], gr.length("full"))
        self.assertEqual(data["missing_required"], [])

    def test_check_exit_code_reflects_drift(self):
        """--check exits 0 iff SKILL.md matches the registry (RED until the
        shipped lines are re-rendered)."""
        r = self._run("--check")
        expected = 0 if not gr.drift(skill_text()) else 1
        self.assertEqual(r.returncode, expected, r.stdout + r.stderr)


if __name__ == "__main__":
    main()
