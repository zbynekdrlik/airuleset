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

    def test_profile_specific_load_bearing_clauses_are_required(self):
        # the coverage guard must protect NON-shared load-bearing clauses too:
        # full's prod-gate (approval-scope.md "never gate on events/prod" hardest
        # rule) + parked, and the reduced review-watch/authority-ends.
        self.assertIn("prod-gate", gr.REQUIRED_BY_PROFILE["full"])
        self.assertIn("parked", gr.REQUIRED_BY_PROFILE["full"])
        for p in ("branch-merge", "fork-no-merge"):
            self.assertIn("review-watch", gr.REQUIRED_BY_PROFILE[p])
            self.assertIn("authority-ends", gr.REQUIRED_BY_PROFILE[p])

    def test_dropping_a_full_only_clause_is_a_red_coverage_result(self):
        # deleting prod-gate from the registry must now be CAUGHT (it wasn't
        # before REQUIRED_BY_PROFILE existed — the #621 review finding).
        saved = list(gr.CLAUSES)
        try:
            gr.CLAUSES[:] = [c for c in gr.CLAUSES if c.id != "prod-gate"]
            self.assertIn("prod-gate", gr.missing_required("full"))
        finally:
            gr.CLAUSES[:] = saved
        self.assertEqual(gr.missing_required("full"), [])


class TestSaturationDirectiveIsReal(TestCase):
    """Acceptance guard (#723 BATCH mode): "the template contains the word
    worktree" is NOT the fix. Lock the actual directive — a BOUNDED parallel
    batch (up to 5 worktree lanes, NO refill while a batch runs) with serial
    integration as branches return."""

    def test_saturation_core_states_the_full_directive(self):
        core = next(c for c in gr.CLAUSES if c.id == "saturation-core").text
        for token in ("BATCH MODE", "up to 5", "PARALLEL", "isolation:worktree",
                      "autopilot-worker", "NO refill"):
            self.assertIn(token, core)

    def test_saturation_delivery_integrates_serially(self):
        for p in gr.PROFILES:
            d = next(c for c in gr.CLAUSES if c.id == "saturation-delivery").text_for(p)
            self.assertIn("SERIALLY", d)
            self.assertIn("as they return", d)


class TestSaturationReconcilesCompactBoundary(TestCase):
    """The owner required the reconciliation to be VISIBLE in the registry, not
    buried in prose (#723 BATCH mode): saturation-core dispatches a bounded
    parallel batch (no refill while it runs); saturation-delivery integrates
    each returned branch serially; compact-boundary fires the compact ONLY at
    the DRAINED batch boundary (whole batch returned + integrated = zero live
    tasks) then dispatches the next batch — never mid-fleet. They cannot
    contradict."""

    def test_the_reconciled_pair_is_registered(self):
        self.assertEqual(gr.SATURATION_RECONCILES_COMPACT,
                         ("saturation-core", "saturation-delivery",
                          "compact-boundary"))

    def test_compact_boundary_fires_at_the_drained_batch_boundary(self):
        for p in gr.PROFILES:
            cb = next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)
            self.assertIn("WHOLE batch has returned", cb)
            self.assertIn("ZERO live tasks", cb)
            self.assertIn("next batch", cb)

    def test_compact_boundary_never_compacts_mid_fleet(self):
        # #723: the whole point — a compact fired while lanes are live breaks
        # task handles / the armed goal (CC #29193). It must say so.
        for p in gr.PROFILES:
            cb = next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)
            self.assertIn("NEVER compact while lanes live", cb)
            self.assertIn("#29193", cb)

    def test_compact_boundary_no_longer_serializes_or_continuously_refills(self):
        # the OLD continuous-mode framing — "compact boundary paces ONE
        # integration per turn / parallel lanes keep building" and the older
        # "do NOT dispatch the next issue in the same turn" — is exactly what
        # suppressed the compact/fleet; both must be gone.
        for p in gr.PROFILES:
            cb = next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)
            self.assertNotIn("do NOT dispatch the next", cb)
            self.assertNotIn("paces ONE", cb)
            self.assertNotIn("keep building", cb)


class TestCompactBoundaryHoldTurn741(TestCase):
    """#741: after `compact-request --self` at a drained boundary the loop HOLDS
    until the compact is delivered — it does NOT dispatch the next batch first.
    The buggy pre-#741 ORDERING claim (the armed goal 'fires the NEXT TURN,
    compacting then dispatching the next batch' — an order nothing enforced) is
    removed from every profile's clause, and the terse HOLD pointer is present."""

    def _cb(self, p):
        return next(c for c in gr.CLAUSES if c.id == "compact-boundary").text_for(p)

    def test_hold_sentence_present_in_every_profile(self):
        for p in gr.PROFILES:
            cb = self._cb(p)
            self.assertIn("HOLD each later goal turn until that compact runs", cb,
                          "%s missing the #741 hold sentence" % p)
            self.assertIn("no next batch first", cb)

    def test_old_ordering_claim_removed_from_every_profile(self):
        # TEETH: a revert to the pre-#741 wording re-introduces exactly these.
        for p in gr.PROFILES:
            cb = self._cb(p)
            self.assertNotIn("compacting then dispatching", cb)
            self.assertNotIn("fires the NEXT TURN", cb)

    def test_old_ordering_gone_from_the_rendered_skill_lines(self):
        # the 3 rendered `/goal` lines in SKILL.md carry the clause verbatim.
        t = skill_text()
        self.assertNotIn("compacting then dispatching", t)

    def test_skill_prose_carries_the_hold_turn_mechanism(self):
        # the full mechanism lives in the NON-capped Step-5 doctrine (the char cap
        # keeps it out of the rendered lines): the status probe, the exact ⏳ WORKING
        # line, and the writer-side latch decision word.
        t = skill_text()
        self.assertIn("compact-request --status", t)
        self.assertIn("čakám na compact hranice várky", t)
        self.assertIn("hold:compact-pending", t)


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

    def test_every_shipped_goal_line_carries_the_batch_directive(self):
        import re
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", skill_text(), re.MULTILINE)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("BATCH MODE", line)
            self.assertIn("isolation:worktree", line)
            self.assertIn("NO refill while a batch runs", line)


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

    def test_render_into_cannot_fix_a_corrupted_prefix_and_drift_shows_it(self):
        # if a /goal line's `STOP CONDITIONS` prefix is corrupted, _SHIPPED_RE
        # skips the block, so render_into is a NO-OP for it and drift() still
        # flags it — the guard `--write` relies on to avoid a false "in sync".
        corrupted = skill_text().replace(
            "/goal STOP CONDITIONS", "/goal STOMP CONDITIONS", 1)
        self.assertNotEqual(corrupted, skill_text())
        residual = gr.drift(gr.render_into(corrupted))
        self.assertTrue(residual, "render_into silently 'fixed' a corrupted block")

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
