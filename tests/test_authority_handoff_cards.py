"""authority_handoff_cards tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from unittest import TestCase, main
from authority_testlib import (  # noqa: E402
    airuleset,
    read,
    _renamed_repo_gh,
    _drive,
)


class TestForkNoMergeHandoffCard(TestCase):
    """The fork-no-merge card variant (incident 2026-07-10): the merge-shaped card
    never fired for david's stream, so the user got no per-ticket evaluation. The
    --handoff card shows a 🔎 review status instead of the 📦 deploy line."""

    def test_handoff_card_shows_review_status_not_deploy(self):
        from notify import compose_autopilot_card
        body = compose_autopilot_card(
            repo="kvaskodev/odoo-erp",
            tickets=[{"n": 1408, "title": "t", "goal": "Cieľ X",
                      "achieved": "Hotové, lokálne overené"}],
            version=None, remaining=3, handoff=True)
        self.assertIn("🔎", body)
        self.assertIn("Odovzdané na review", body)
        self.assertIn("odovzdaný na review", body)   # header, not "vyriešené"
        self.assertNotIn("📦", body)                 # no deploy/version line
        self.assertNotIn("zmergnuté", body)
        self.assertIn("🎯 **Cieľ:** Cieľ X", body)

    def test_non_handoff_card_still_shows_deploy_line(self):
        from notify import compose_autopilot_card
        body = compose_autopilot_card(
            repo="o/n", tickets=[{"n": 5, "goal": "g", "achieved": "a"}],
            version="v1.2.3", handoff=False)
        self.assertIn("📦", body)
        self.assertIn("nasadené **v1.2.3**", body)
        self.assertNotIn("Odovzdané na review", body)

    def test_cmd_notify_passes_handoff_through(self):
        import unittest.mock as mk
        args = mk.Mock(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                       record_question=False, edit_question=False, channel_id=False,
                       owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
                       repo="kvaskodev/odoo-erp", issue=1408, pr=None,
                       achieved="Fix nasadený a overený", result=None, goal="cieľ", version=None,
                       merge_sha=None, url=None, review="ok", handoff=True,
                       dedup_key=None, dry_run=False)
        captured = {}

        with mk.patch.object(airuleset, "_gh_out",
                             side_effect=lambda *a, **k: "T" if "view" in a else "3"):
            with mk.patch("notify.send",
                          # `setdefault` RETURNS the (truthy) body, so a
                          # trailing `or "sent"` never fires — the fake used
                          # to hand back the card text as its status. Harmless
                          # until #135 made a non-'sent' status a real failure;
                          # return the status explicitly.
                          side_effect=lambda body, **k: (
                              captured.setdefault("b", body), "sent")[1]):
                airuleset.cmd_notify(args)
        self.assertIn("Odovzdané na review", captured["b"])
        self.assertNotIn("📦", captured["b"])


class TestAutopilotSkillCarriesProfiles(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def test_step1_detects_authority(self):
        t = read(self.SKILL)
        self.assertIn("airuleset.py authority", t)
        self.assertIn("airuleset:authority=", t)  # per-project override marker

    def test_three_goal_templates_exist(self):
        t = read(self.SKILL)
        for marker in ("AUTHORITY: full", "AUTHORITY: branch-merge",
                       "AUTHORITY: fork-no-merge"):
            self.assertIn(marker, t)

    def test_fork_profile_never_opens_prs_and_hands_off(self):
        t = read(self.SKILL)
        self.assertIn("ready-for-review", t)
        self.assertIn("NEVER open or merge a PR", t)

    def test_fork_handoff_is_comment_primary_label_best_effort(self):
        # #17: David's fork-derived GitHub role is `read` — CANNOT add labels
        # (needs triage+). The hand-off signal must work at pure read role:
        # a READY-FOR-REVIEW: comment is PRIMARY; the label is best-effort only,
        # and the /goal proof must NOT hinge on a label search.
        t = read(self.SKILL)
        self.assertIn("READY-FOR-REVIEW:", t)
        self.assertIn("best-effort", t)
        self.assertNotIn('-label:ready-for-review', t)

    def test_worker_handoff_is_comment_primary(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("READY-FOR-REVIEW:", w)
        self.assertIn("best-effort", w)

    def test_branch_merge_profile_stops_at_integration_branch(self):
        t = read(self.SKILL)
        self.assertIn("INTEGRATION branch", t)
        self.assertIn("never staging/main", t.lower())

    def test_reduced_authority_scopes_backlog_to_assigned(self):
        # #181: the SINGLE definition of "my slice" is `slice-quals`, never a
        # hand-rolled --assignee @me (which silently returns 0 on a
        # shared-gh-account stream box — montalu/marek/simap). The skill still
        # NAMES --assignee @me, but only in prose explaining why not to use it.
        t = read(self.SKILL)
        self.assertIn("slice-quals --list", t)
        self.assertIn("slice-quals --count", t)

    def test_branch_merge_dispatch_posts_ready_for_review_too(self):
        # #349 MAJOR-3: the "Authority rides the dispatch" bullet used to
        # describe branch-merge as done once its PR merges into the
        # integration branch — no mention of the hand-off comment at all,
        # which is exactly the montalu3 self-close-and-never-hand-off
        # regression. It must now explicitly say branch-merge posts the SAME
        # READY-FOR-REVIEW comment fork-no-merge uses, and never self-closes.
        # #349 review m4: anchor the window's END to the next stable bullet
        # marker rather than a magic character count — a magic-number window
        # silently truncates (or over-includes unrelated later prose) the
        # moment nearby wording is edited.
        t = read(self.SKILL)
        idx = t.index("Authority rides the dispatch")
        end = t.index("Every dispatch RETURNS IMMEDIATELY", idx)
        bullet = t[idx:end]
        self.assertIn("posts the SAME `READY-FOR-REVIEW:` hand-off comment", bullet)
        self.assertIn("never a self-close", bullet)

    def test_step4_verification_checks_both_reduced_profiles_never_main(self):
        # #349 MAJOR-3: Step 4's own verification sentence must state BOTH
        # reduced-authority done-points (branch-merge needs the PR-merged
        # check AND the comment; fork-no-merge needs only the comment) and
        # explicitly forbid a merge to main for either.
        t = read(self.SKILL)
        idx = t.index("Step 4 verification then checks the PROFILE")
        end = t.index("Every dispatch RETURNS IMMEDIATELY", idx)
        window = t[idx:end]
        self.assertIn("branch-merge: PR merged into integration AND the", window)
        self.assertIn("fork-no-merge: the `READY-FOR-REVIEW:` comment present", window)
        self.assertIn("NEVER a merge to main for either", window)
        self.assertIn("NEVER the ticket closed by the worker itself", window)

    def test_step5_handoff_line_covers_both_profiles(self):
        # #349 MAJOR-3: Step 5's reduced-authority report mapping must state
        # the Hand-off line applies to BOTH profiles (not just fork-no-merge),
        # and that branch-merge's own PR line is ADDITIONAL, ending at the
        # integration branch — never a substitute for the Hand-off line.
        t = read(self.SKILL)
        idx = t.index("Reduced-authority streams (branch-merge / fork-no-merge) carry")
        end = t.index("The heading + audits", idx)
        window = t[idx:end]
        self.assertIn("Hand-off: READY-FOR-REVIEW komentár na #N", window)
        self.assertIn("for BOTH profiles", window)
        self.assertIn("branch-merge posts it too, right after its integration-branch merge", window)
        self.assertIn("a merge alone does NOT", window)
        self.assertIn("additionally for branch-merge", window)

    def test_pr_merge_policy_skill_states_the_hand_off_too(self):
        # #349 round-2-review M1: `skills/pr-merge-policy/SKILL.md`'s own
        # reduced-authority scope bullet described branch-merge as ending at
        # "its PR merged into the project's INTEGRATION branch" with no
        # mention of the hand-off comment at all — a FIFTH place in the repo
        # restating the same wrong-shape omission MAJOR-3 already fixed
        # elsewhere.
        t = read("skills/pr-merge-policy/SKILL.md")
        self.assertIn("THEN posts the same `READY-FOR-REVIEW:` hand-off comment", t)
        self.assertIn("Neither profile ever closes the ticket itself", t)

    def test_authority_profiles_canonical_comment_states_the_hand_off_too(self):
        # #349 round-2-review m1: the canonical AUTHORITY_PROFILES comment
        # (promoted VERBATIM with AUTHORITY_PROFILES/AUTHORITY_BY_USER into the
        # cli_fleet.py constants leaf, #433 L-E) described branch-merge with
        # strictly LESS detail than its fork-no-merge sibling bullet right
        # below it (no hand-off mention at all) — the canonical definition
        # every other doc points back to must not itself omit the invariant.
        t = read("cli_fleet.py")
        idx = t.index("branch-merge  — own PR merged into the project INTEGRATION branch")
        end = t.index("fork-no-merge — fork branch pushed", idx)
        window = t[idx:end]
        self.assertIn("THEN the same ready-for-review hand-off comment", window)
        self.assertIn("never closes the issue itself", window)


class TestWorkerCarriesProfiles(TestCase):
    def test_worker_has_authority_section(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("Authority profile", w)
        self.assertIn("fork-no-merge", w)
        self.assertIn("branch-merge", w)

    def test_merge_policy_notes_reduced_authority(self):
        self.assertIn("airuleset.py authority",
                      read("modules/core/pr-merge-policy.md"))

    def test_worker_forbids_foreign_close_and_fires_handoff_card(self):
        # 2026-07-10 incident + 2026-07-11 gatekeeper refinement: a fork-no-merge
        # worker never closes ASSIGNED/foreign-authored tickets (maintainer closes at
        # review) but MAY close its own self-authored sub-findings with evidence.
        w = read("agents/autopilot-worker.md")
        self.assertIn("NEVER close an ASSIGNED", w)
        self.assertIn("self-authored sub-findings", w)         # the allowed class
        self.assertIn("block-fork-no-merge-issue-close", w)   # names the enforcing hook
        self.assertIn("--handoff", w)                          # fires the fork-no-merge card
        self.assertIn("OBSOLETE:", w)                          # foreign obsolete → comment
        self.assertIn("obsolete_handed_off:", w)               # fork-no-merge FINAL MESSAGE field

    def test_close_guard_hook_is_wired(self):
        import json
        d = json.loads(read("settings/hooks.json"))
        bash = [x for x in d["hooks"]["PreToolUse"] if x.get("matcher") == "Bash"][0]
        cmds = " ".join(h["command"] for h in bash["hooks"])
        self.assertIn("block-fork-no-merge-issue-close.sh", cmds)

    def test_worker_forbids_foreign_close_under_branch_merge_too(self):
        # #349 (2026-08-09 montalu3 regression): the ban + hand-off recipe must
        # apply to branch-merge exactly like fork-no-merge — the mechanical hook
        # was widened to gate any authority != full, and the prose must say so.
        # Normalized (collapsed whitespace) because the prose wraps across
        # markdown line breaks and a literal multi-word needle can straddle one
        # (this repo's own recurring "anchor spans a wrap" test-quality trap).
        w = " ".join(read("agents/autopilot-worker.md").split())
        self.assertIn("NEVER close an ASSIGNED", w)
        self.assertIn("EITHER reduced-authority profile", w)
        self.assertIn("full `/process-subdev` release pipeline", w)
        self.assertIn("authority != `full`", w)

    def test_worker_has_branch_merge_final_message_variant(self):
        # Previously ONLY a "fork-no-merge variant" existed — a branch-merge
        # worker had no evidence-block template telling it to leave the issue
        # OPEN instead of inventing merge-to-main/deploy fields (#349).
        w = read("agents/autopilot-worker.md")
        self.assertIn("branch-merge variant of the FINAL MESSAGE", w)
        self.assertIn("NEVER closed by you", w)
        self.assertIn("ready_for_review:", w)

    def test_step0_obsolete_exception_covers_branch_merge_too(self):
        # #349 adversarial-review MAJOR-2: STEP 0's obsolete-ticket path used
        # to instruct `gh issue close` unconditionally, with the "you may
        # close only self-authored" carve-out labelled "fork-no-merge
        # EXCEPTION" only — a branch-merge reader had no carve-out at all and
        # would follow the DEFAULT (close-obsolete) instruction, directly
        # contradicting the new ban.
        w = read("agents/autopilot-worker.md")
        self.assertIn("REDUCED-AUTHORITY EXCEPTION (fork-no-merge AND branch-merge", w)
        self.assertIn("under EITHER profile", w)

    def test_branch_merge_final_message_uses_the_handed_off_obsolete_shape(self):
        # #349 adversarial-review MAJOR-2: the new branch-merge fence had
        # copied the FULL variant's "OBSOLETE — closed" / obsolete_closed:
        # shape instead of the fork-no-merge variant's "commented, left
        # OPEN" / obsolete_handed_off: shape it should mirror.
        # #349 review m4: anchor the window's END to the next stable section
        # heading rather than a magic character count.
        w = read("agents/autopilot-worker.md")
        idx = w.index("branch-merge variant of the FINAL MESSAGE")
        end = w.index("Worktree-mode variant of the FINAL MESSAGE", idx)
        fence = w[idx:end]
        self.assertIn('"OBSOLETE — commented, left OPEN:', fence)
        self.assertIn("obsolete_handed_off:", fence)
        self.assertNotIn('"OBSOLETE — closed:', fence)
        self.assertNotIn("obsolete_closed:", fence)


class TestCompletionReportBranchMergeHandoff(TestCase):
    """#349: the reduced-authority completion-report template used to show a
    Hand-off line ONLY for fork-no-merge — a branch-merge worker had no
    instruction to signal hand-off at all, which is exactly how montalu3's
    three merged tickets sat neither queued nor reviewed."""

    def test_handoff_line_covers_branch_merge_too(self):
        # #859 batch 4b: deep content in companion
        t = read("modules/core/completion-report.md") + "\n" + read("skills/completion-report-deep/DEEP.md")
        self.assertIn("fork-no-merge AND branch-merge", t)
        self.assertIn("NEVER a self-close", t)

    def test_pr_line_states_ticket_stays_open(self):
        t = read("modules/core/completion-report.md") + "\n" + read("skills/completion-report-deep/DEEP.md")
        self.assertIn("ticket stays OPEN", t)


class TestPerBoxSkillScoping(TestCase):
    """Skill sets are PER BOX (user complaint 2026-07-11: slash commands must be
    relevant to the box, not all-everywhere). Maintainer boxes (newlevel) get all;
    other boxes lose maintainer-only skills; reduced-authority streams also lose
    deploy-ssh (deploys are outside their job). Hidden on-demand skills stay
    everywhere (rule stubs point at them; they never show in the slash list)."""

    def test_maintainer_gets_everything(self):
        self.assertEqual(airuleset.skill_names_for_user("newlevel"),
                         airuleset.SKILL_NAMES)

    def test_gatekeeper_loses_maintainer_only_keeps_deploy(self):
        names = airuleset.skill_names_for_user("gatekeeper")
        for n in airuleset.SKILLS_MAINTAINER_ONLY:
            self.assertNotIn(n, names)
        self.assertIn("deploy-ssh", names)      # full authority — deploys are his job
        self.assertIn("autopilot", names)
        self.assertIn("playbook-review", names)

    def test_subdev_also_loses_deploy_ssh(self):
        for user in ("david1", "marek", "montalu1", "montalu2", "montalu3",
                     "montalu4", "montalu5", "montalu6", "montalu7",
                     "montalu8"):
            names = airuleset.skill_names_for_user(user)
            self.assertNotIn("deploy-ssh", names, user)
            self.assertNotIn("mdreview", names, user)
            self.assertIn("autopilot", names, user)

    def test_hidden_on_demand_skills_stay_everywhere(self):
        # Rule stubs point at these; user-invocable:false keeps them out of the
        # slash list, so they are NOT noise on any box.
        for user in ("newlevel", "gatekeeper", "david"):
            names = airuleset.skill_names_for_user(user)
            for n in ("mutation-testing", "local-builds",
                      "batch-issue-development", "view-image-urls",
                      "version-on-dashboard"):
                self.assertIn(n, names, f"{n} missing for {user}")

    def test_maintainer_only_skills_are_really_hidden_or_maintainer_scoped(self):
        # Every user-invocable skill a non-maintainer box gets must be genuinely
        # cross-box relevant; conversely every skill we scope away must exist.
        from pathlib import Path as P
        for n in airuleset.SKILLS_MAINTAINER_ONLY | airuleset.SKILLS_FULL_AUTHORITY_ONLY:
            self.assertTrue((P(airuleset.REPO_DIR) / "skills" / n).exists(), n)

    def test_per_user_extras_regrant_a_scoped_skill_where_relevant(self):
        # 2026-07-14 incident: aacd29e classified meeting-analysis maintainer-only
        # and the push PRUNED it off montalu — but the user analyzes montalu
        # meeting recordings IN that stream's session. Per-user extras re-grant a
        # scoped-away skill on exactly the boxes where it IS relevant.
        self.assertIn("meeting-analysis",
                      airuleset.skill_names_for_user("montalu1"))
        # airuleset#251: montalu2/3/4 are full parallel montalu streams —
        # same working style, same meeting-recordings-in-session rationale.
        # airuleset#378: montalu5/6/7/8 are FOUR MORE, same rationale.
        for user in ("montalu2", "montalu3", "montalu4", "montalu5",
                     "montalu6", "montalu7", "montalu8"):
            self.assertIn("meeting-analysis",
                          airuleset.skill_names_for_user(user), user)
        for user in ("david", "marek", "gatekeeper"):
            self.assertNotIn("meeting-analysis",
                             airuleset.skill_names_for_user(user), user)
        # extras never leak anything beyond the named skill
        self.assertNotIn("mdreview", airuleset.skill_names_for_user("montalu1"))
        # every extras entry must name real skills (typo guard)
        for user, extras in airuleset.SKILLS_EXTRA_BY_USER.items():
            for n in extras:
                self.assertIn(n, airuleset.SKILL_NAMES, f"{user}:{n}")


class TestTheHandoffArmVerifiesItsOwnMechanism(TestCase):
    """#181 round 4, item 6. The `ready-for-review` arm rests entirely on the
    repo's own hand-off-label workflow — a read-role collaborator gets a 403
    adding the label itself. Measured on zbynekdrlik/odoo-erp 2026-07-30:
    workflow `active`, **23 of its last 30 runs FAILED** (the 5 newest all
    failed, job `label`, startup-shaped), open `ready-for-review` issues **0**.
    So the arm contributes a zero while the only thing that can produce a
    non-zero is failing three runs in four — this ticket's own failure mode by
    a different road, no longer hypothetical.

    A miss must be DETECTABLE at the moment a zero would rest on it. It is NOT
    guessed from comment text: GitHub tokenizes quoted phrases, so
    `"READY-FOR-REVIEW:" in:comments` over-matches, and over-counting the
    obligation set is the never-stops failure."""

    def _drive_enrolled(self, gh, repo="odoo-erp", **flags):
        import unittest.mock as mk
        with mk.patch("notify.repo_name_for", return_value=repo):
            return _drive(airuleset.cmd_core_quals, gh, **flags)

    def test_an_empty_obligation_set_refuses_when_the_labeller_is_failing(self):
        out, err, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, newest_run="failure"))
        self.assertIsNotNone(
            exc,
            "the obligation set is empty and the only mechanism that can put "
            "`ready-for-review` on a hand-off just failed — a hand-off can be "
            "outstanding with no label, so this 0 is not evidence")
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(out.strip(), "")

    def test_a_disabled_labeller_refuses_too(self):
        out, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, workflow_state="disabled_manually"))
        self.assertIsNotNone(exc)
        self.assertEqual(out.strip(), "")

    def test_a_missing_labeller_on_an_enrolled_repo_refuses(self):
        out, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, workflows=[]))
        self.assertIsNotNone(exc)
        self.assertEqual(out.strip(), "")

    def test_every_unsuccessful_newest_run_counts_as_broken(self):
        """The predicate was literally `conclusion == "failure"`, so a
        `startup_failure` / `timed_out` / `cancelled` / `action_required` run
        passed as healthy (adversarial review, round 4). odoo-erp's live
        failures are startup-SHAPED — the failing job records no failing STEP
        — so this is the neighbouring spelling of the very case that motivated
        the guard, and GitHub reports `startup_failure` as its own
        conclusion."""
        for conclusion in ("startup_failure", "timed_out", "cancelled",
                           "action_required"):
            with self.subTest(conclusion=conclusion):
                out, _, exc = self._drive_enrolled(
                    _renamed_repo_gh(healthy=True, newest_run=conclusion))
                self.assertIsNotNone(
                    exc, "a %r newest run passed as a healthy labeller"
                    % conclusion)
                self.assertEqual(out.strip(), "")

    def test_a_labeller_that_has_never_run_is_not_evidence_either(self):
        """An enrolled repo whose hand-off workflow has never produced a run
        cannot have labelled any hand-off, so an empty run list is exactly as
        much evidence as a failing one — none."""
        out, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, newest_run=None))
        self.assertIsNotNone(exc)
        self.assertEqual(out.strip(), "")

    def test_a_run_still_in_progress_is_not_treated_as_broken(self):
        """The companion control: a run in flight has a null conclusion and is
        not evidence of breakage — refusing on it would spin the loop for the
        duration of every labeller run."""
        out, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, newest_run="in_progress"))
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")

    def test_a_healthy_labeller_still_lets_the_zero_through(self):
        out, _, exc = self._drive_enrolled(_renamed_repo_gh(healthy=True))
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")

    def test_an_extra_filtered_empty_result_is_NOT_gated_on_the_labeller(self):
        """Self-found while reviewing the round-4 fix, and confirmed live on
        odoo-erp: gating an `--extra`-filtered query on the hand-off labeller
        is a FALSE REFUSAL.

        `--extra "label:prio:bounce"` asks a filtered question — "any open
        bounce ticket?" — and "none" is an ordinary answer with nothing to do
        with hand-off labels. The hand-off arm is part of the UNFILTERED
        obligation set, i.e. the stop-proof. Gating the seed on it breaks
        Step 3.1 on the one repo the cross-stream flow actually runs on, the
        moment its bounce lane empties. A guard that refuses a legitimate
        answer spins the loop forever — the mirror of the bug this ticket is
        about, not a safer version of it."""
        out, err, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, newest_run="failure"),
            extra="label:prio:bounce")
        self.assertIsNone(
            exc,
            "the bounce seed refused because the hand-off labeller is "
            "unhealthy, which that query does not depend on: %s" % err)
        self.assertEqual(out.strip(), "0")

    def test_the_unfiltered_stop_proof_is_still_gated(self):
        """The companion control — narrowing the gate must not delete it."""
        _, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, newest_run="failure"))
        self.assertIsNotNone(exc)

    def test_a_repo_outside_the_cross_stream_flow_is_never_gated_on_it(self):
        """airuleset itself has no sub-dev streams working it, so nothing here
        depends on a hand-off labeller and a zero must not be blocked by one."""
        out, _, exc = self._drive_enrolled(
            _renamed_repo_gh(healthy=True, workflows=[]), repo="airuleset")
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")


if __name__ == "__main__":
    main()
