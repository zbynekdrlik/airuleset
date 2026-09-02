"""Locks the autopilot authority profiles (issue #16, 2026-07-09).

Incident: David's fork stream (no merge rights) invoked /autopilot and got the
hardcoded full-authority /goal condition ("merged PR to main + main green") —
unsatisfiable, so the run correctly refused to arm. Sub-dev streams must run
/autopilot AS-IS: the authority profile is a property of the USER (streams are
separate linux users), resolved at runtime from AUTHORITY_BY_USER — no per-box
state to lose on a home-dir migration.
"""

import os
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset
# #433 cluster I: `_refuse_unless_empty_is_trustworthy` + `cmd_slice_quals`/
# `cmd_core_quals` moved into the cli_quals_cmd leaf. A test that intercepts the
# SHARED refusal helper must patch it where those commands RESOLVE the bare name
# (the leaf's own globals), not on the airuleset facade attr (K-seam, internals
# #1482).
import cli_quals_cmd

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestAuthorityResolution(TestCase):
    def test_known_stream_users_map_to_their_profiles(self):
        # david1 (was david, airuleset#23; #537 live rename 2026-08-21): same
        # fork-no-merge profile as the base. The OLD unix name's row left the
        # map with the OS account (runbook-537 step 8, live in-place usermod).
        self.assertEqual(airuleset.AUTHORITY_BY_USER["david1"], "fork-no-merge")
        self.assertNotIn("david", airuleset.AUTHORITY_BY_USER)
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "branch-merge")
        # montalu1 (was montalu; #537 live rename 2026-08-19): same
        # branch-merge profile as the base. The OLD unix name's row left the
        # map with the OS account (runbook-537 step 8, live in-place usermod).
        self.assertEqual(airuleset.AUTHORITY_BY_USER["montalu1"], "branch-merge")
        self.assertNotIn("montalu", airuleset.AUTHORITY_BY_USER)
        # simap1 (was simap, airuleset#143; #537 live rename 2026-08-18):
        # phase-1 demo stream that merges NOWHERE — fork-no-merge is the
        # existing lowest profile, already correct. The OLD unix name's row
        # left the map with the OS account (runbook-537 step 8).
        self.assertEqual(airuleset.AUTHORITY_BY_USER["simap1"], "fork-no-merge")
        self.assertNotIn("simap", airuleset.AUTHORITY_BY_USER)
        # miva1 (airuleset#300): PROMOTED to branch-merge (airuleset#821):
        # odoo-erp phase-2 (#3244, 2026-08-14) made it a full write stream "in
        # the montalu mould" — own branch → own PR into develop → hand-off.
        self.assertEqual(airuleset.AUTHORITY_BY_USER["miva1"], "branch-merge")

    def test_montalu_family_streams_map_to_branch_merge(self):
        # airuleset#251: montalu2/3/4 are full parallel montalu streams
        # ("zhodné s dnešným montalu") — same authority as montalu itself.
        for u in ("montalu2", "montalu3", "montalu4"):
            self.assertEqual(airuleset.AUTHORITY_BY_USER[u], "branch-merge", u)

    def test_david_family_streams_map_to_fork_no_merge(self):
        # airuleset#326: david2/david3/david4 are three MORE clones of the
        # david external-developer fork stream — same authority as david.
        for u in ("david2", "david3", "david4"):
            self.assertEqual(airuleset.AUTHORITY_BY_USER[u], "fork-no-merge", u)

    def test_montalu5_8_streams_map_to_branch_merge(self):
        # airuleset#378, odoo-erp#3642: montalu5/6/7/8 are FOUR MORE full
        # parallel montalu streams — same authority as montalu/montalu2/3/4.
        for u in ("montalu5", "montalu6", "montalu7", "montalu8"):
            self.assertEqual(airuleset.AUTHORITY_BY_USER[u], "branch-merge", u)

    def test_resolve_uses_the_map_for_montalu5_8(self):
        for u in ("montalu5", "montalu6", "montalu7", "montalu8"):
            with m.patch.object(airuleset, "_current_user", return_value=u):
                self.assertEqual(airuleset.resolve_authority(), "branch-merge", u)

    def test_resolve_uses_the_map_for_simap(self):
        # #537 live rename: the box now runs as simap1; the old unix name
        # cannot run any process (account deleted), so only the numbered
        # name needs to resolve reduced.
        with m.patch.object(airuleset, "_current_user", return_value="simap1"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_resolve_uses_the_map_for_miva1(self):
        with m.patch.object(airuleset, "_current_user", return_value="miva1"):
            self.assertEqual(airuleset.resolve_authority(), "branch-merge")

    def test_miva1_marker_lowers_the_branch_merge_table(self):
        # airuleset#821: the table flip is the DEFAULT; an explicit HTML-comment
        # marker must still win for miva1 (single source of truth = marker over
        # table). A fork-no-merge marker LOWERS the branch-merge table value —
        # the genuinely-new direction (a marker lowering a mapped branch-merge
        # user; the bogus-marker case is covered by test_bogus_marker_value_ignored).
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="miva1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_resolve_miva1_no_marker_is_branch_merge(self):
        # airuleset#821 REGRESSION: miva1 was PROMOTED 2026-08-14 (#3244 phase 2)
        # to a full write stream "in the montalu mould" — branch-merge authority
        # (push miva1/<topic>, open+merge own PR into develop, then hand-off).
        # odoo-erp states this in PROSE (no HTML-comment marker), so with no
        # marker the per-user table is the effective source and MUST resolve
        # branch-merge — NOT the stale phase-1 fork-no-merge that armed the
        # wrong /goal template on 2026-09-01.
        import tempfile
        d = tempfile.mkdtemp()  # no CLAUDE.md -> table is the effective source
        with m.patch.object(airuleset, "_current_user", return_value="miva1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "branch-merge")

    def test_resolve_uses_the_map_for_david_family(self):
        for u in ("david2", "david3", "david4"):
            with m.patch.object(airuleset, "_current_user", return_value=u):
                self.assertEqual(airuleset.resolve_authority(), "fork-no-merge", u)

    def test_resolve_defaults_to_full_for_unknown_user(self):
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(), "full")

    def test_resolve_uses_the_map_for_stream_users(self):
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_cli_prints_the_profile(self):
        # #349/#463/#533: `m.Mock` auto-creates any unspecified attribute as a
        # truthy Mock, so the `--maintainer-login` / `--self-login` /
        # `--stream-label` early-return branches would silently hijack this test
        # unless pinned False (the established `m.Mock(...)`-args gotcha this
        # repo's own dev rules already document for exactly this shape).
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            with m.patch("builtins.print") as p:
                airuleset.cmd_authority(
                    m.Mock(explain=False, maintainer_login=False,
                           self_login=False, stream_label=False, app_bot_login=False))
        p.assert_any_call("branch-merge")

    def test_cli_explain_logs_the_resolution_source(self):
        # airuleset#821 / #486: --explain is a decision LOG naming which source
        # won (marker / per-user map / unmapped default), distinguishing a 'none'
        # from an INVALID marker (the typo'd-marker misconfig class). It derives
        # from the SAME _authority_decision the resolver uses, so the printed
        # source can never disagree with the resolved profile. Assert all four
        # branches; the seam is _authority_marker_raw (the one shared file read).
        import cli_quals

        def _explain(user, raw_marker):
            with m.patch.object(airuleset, "_current_user", return_value=user):
                with m.patch.object(cli_quals, "_authority_marker_raw",
                                    return_value=raw_marker):
                    with m.patch("builtins.print") as p:
                        airuleset.cmd_authority(
                            m.Mock(explain=True, maintainer_login=False,
                                   self_login=False, stream_label=False,
                                   app_bot_login=False))
            return " ".join(str(c.args[0]) for c in p.call_args_list if c.args)

        # miva1, no marker → per-user map wins and reports branch-merge.
        out = _explain("miva1", None)
        self.assertIn("resolved=branch-merge via per-user map", out)
        self.assertIn("marker=none", out)
        # a VALID marker present → the marker source wins AND is the resolved
        # profile (locks that the named source is never a lie about a map value).
        out = _explain("miva1", "fork-no-merge")
        self.assertIn("resolved=fork-no-merge via project CLAUDE.md marker", out)
        self.assertIn("marker=fork-no-merge", out)
        # an INVALID marker → ignored for resolution (table stands) but surfaced
        # as invalid, the exact 'typo'd marker' class the log exists to diagnose.
        out = _explain("miva1", "branch_merge")
        self.assertIn("resolved=branch-merge via per-user map", out)
        self.assertIn("marker=invalid('branch_merge')", out)
        # an UNMAPPED user → the hardcoded `full` default decided, NOT a map row,
        # and the log says so instead of the misleading "via per-user map".
        out = _explain("nobody-here", None)
        self.assertIn("resolved=full via default (unmapped)", out)

    def test_cli_prints_maintainer_login(self):
        # #349: the close-guard hook's shared-identity fix needs this to tell
        # a genuine self-authored sub-finding apart from maintainer-authored
        # assigned work on a shared-gh-identity reduced-authority box.
        with m.patch("builtins.print") as p:
            airuleset.cmd_authority(
                m.Mock(explain=False, maintainer_login=True,
                       self_login=False, stream_label=False, app_bot_login=False))
        p.assert_any_call(airuleset.MAINTAINER_GH_LOGIN)

    def test_cli_prints_app_bot_login_unconditionally(self):
        # #773: `authority --app-bot-login` prints the shared stream App bot
        # login constant with no network call and no App-token-box detection --
        # the close-guard hook's identity fallback compares a ticket's author
        # against it when --self-login could not resolve the box's own login.
        with m.patch("builtins.print") as p:
            airuleset.cmd_authority(
                m.Mock(explain=False, maintainer_login=False,
                       self_login=False, stream_label=False, app_bot_login=True))
        p.assert_any_call(airuleset.STREAM_APP_BOT_LOGIN)

    def test_cli_prints_stream_label_under_reduced_authority(self):
        # #533: `authority --stream-label` prints `stream:<unix-user>` on a
        # reduced-authority box, for the close-guard hook's acceptance-close
        # ownership check. cmd_authority reads the resolver by its cli_quals
        # global name, so patch it there (the marker-aware end-to-end path is
        # covered by the subprocess tests in test_fork_no_merge_close_guard.py).
        import cli_quals
        with m.patch.object(cli_quals, "resolve_authority",
                            return_value="branch-merge"):
            with m.patch.object(airuleset, "_current_user",
                                return_value="montalu3"):
                with m.patch("builtins.print") as p:
                    airuleset.cmd_authority(
                        m.Mock(explain=False, maintainer_login=False,
                               self_login=False, stream_label=True, app_bot_login=False))
        p.assert_any_call("stream:montalu3")

    def test_cli_stream_label_emits_rename_equivalents(self):
        # #564: on a box whose base stream was RENAMED (montalu -> montalu1),
        # `authority --stream-label` must emit BOTH the current label AND the
        # legacy one, so the close-guard hook still recognizes THIS stream's
        # OWN tickets that still carry the old `stream:montalu` label during the
        # transition. RED before the fix (only `stream:montalu1` is printed).
        import cli_quals
        with m.patch.object(cli_quals, "resolve_authority",
                            return_value="branch-merge"):
            with m.patch.object(airuleset, "_current_user",
                                return_value="montalu1"):
                with m.patch("builtins.print") as p:
                    airuleset.cmd_authority(
                        m.Mock(explain=False, maintainer_login=False,
                               self_login=False, stream_label=True, app_bot_login=False))
        printed = [str(c.args[0]) for c in p.call_args_list if c.args]
        self.assertIn("stream:montalu1", printed, printed)
        self.assertIn("stream:montalu", printed, printed)

    def test_cli_stream_label_empty_under_full_authority(self):
        # #533: on a full-authority box the flag prints NOTHING (the hook's
        # fail-safe then refuses the acceptance exemption).
        import cli_quals
        with m.patch.object(cli_quals, "resolve_authority", return_value="full"):
            with m.patch.object(airuleset, "_current_user",
                                return_value="newlevel"):
                with m.patch("builtins.print") as p:
                    airuleset.cmd_authority(
                        m.Mock(explain=False, maintainer_login=False,
                               self_login=False, stream_label=True, app_bot_login=False))
        for call in p.call_args_list:
            args = call.args
            self.assertFalse(args and str(args[0]).startswith("stream:"),
                             "must not print a stream label under full authority")

    def test_project_marker_overrides_the_user_map(self):
        # cmd_authority's explain text has always PROMISED the marker override; it must
        # actually be honored now (single source of truth for the CLI + the close-guard
        # hook). A project can RAISE david's default (fork-no-merge) to full…
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=full -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "full")

    def test_project_marker_can_lower_authority(self):
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_no_marker_falls_back_to_user_map(self):
        import tempfile
        d = tempfile.mkdtemp()  # no CLAUDE.md
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_bogus_marker_value_ignored(self):
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=superuser -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_bare_prose_mention_does_NOT_change_authority(self):
        # Security (review 2026-07-11): only the HTML-comment marker counts. A prose
        # / doc mention of a profile MUST NOT silently elevate a fork-no-merge stream
        # to full and disable the close guard (the UNSAFE direction).
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "Streams: set airuleset:authority=full to grant full rights.\n")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_last_comment_marker_wins_over_an_example(self):
        # An operative marker placed AFTER a documentation example must not be shadowed.
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "Example: <!-- airuleset:authority=full -->\n"
            "<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")


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


if __name__ == "__main__":
    main()


class TestCompletionReportBranchMergeHandoff(TestCase):
    """#349: the reduced-authority completion-report template used to show a
    Hand-off line ONLY for fork-no-merge — a branch-merge worker had no
    instruction to signal hand-off at all, which is exactly how montalu3's
    three merged tickets sat neither queued nor reviewed."""

    def test_handoff_line_covers_branch_merge_too(self):
        t = read("modules/core/completion-report.md")
        self.assertIn("fork-no-merge AND branch-merge", t)
        self.assertIn("NEVER a self-close", t)

    def test_pr_line_states_ticket_stays_open(self):
        t = read("modules/core/completion-report.md")
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


class TestRunCardRemainingScopedToStream(TestCase):
    """david incident 2026-07-19: the statusline showed 'Issues 2/26' — the
    run-card's remaining was the WHOLE repo's open backlog (26) although
    david's own slice was 5. On a reduced-authority box the card's remaining
    (which feeds the statusline D/T) must count the STREAM's slice — the same
    quals as tickets-status (assignee:@me ∪ author:@me ∪ label:stream:<user>,
    non-skip); full boxes keep the repo-wide count."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
                    repo="kvaskodev/odoo-erp", issue=1408, pr=None,
                    achieved="Fix nasadený a overený", result=None, goal="cieľ", version=None,
                    merge_sha=None, url=None, review="ok", handoff=True,
                    dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def test_reduced_authority_counts_own_slice(self):
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "assignee:@me" in j:
                return '[{"number":1},{"number":2}]'
            if "author:@me" in j:
                return '[{"number":2}]'
            if "label:stream:" in j:
                return "[]"
            return "26"          # the repo-wide count a scoped box must NOT use

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="fork-no-merge"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    with mk.patch("notify.send",
                                  side_effect=lambda body, **k: (
                                      captured.setdefault("b", body),
                                      "sent")[1]):
                        airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(m.group(1), "2", captured["b"])   # slice, NOT 26

    def test_reduced_authority_remaining_excludes_ops_channel(self):
        # #362 review: the reduced-authority "remaining" queries (both the
        # per-qual slice loop and the origin-recovery candidates query, one
        # frame up in cmd_tickets_status) must AND -label:ops-channel onto
        # the same base as -label:autopilot-skip. #4 below carries BOTH
        # assignee:@me and author:@me AND ops-channel -- if the exclusion is
        # ever dropped from the search string the fake reverts to the
        # inflated (assignee/author-only) population, so this fails against
        # a mutant that removes AUTOPILOT_SKIP_EXCL's ops-channel half.
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "-label:ops-channel" in j and "assignee:@me" in j:
                return '[{"number":1}]'
            if "assignee:@me" in j:
                return '[{"number":1},{"number":4}]'
            if "-label:ops-channel" in j and "author:@me" in j:
                return '[{"number":2}]'
            if "author:@me" in j:
                return '[{"number":2},{"number":4}]'
            if "label:stream:" in j:
                return "[]"
            return "26"

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="fork-no-merge"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    with mk.patch("notify.send",
                                  side_effect=lambda body, **k: (
                                      captured.setdefault("b", body),
                                      "sent")[1]):
                        airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "ops-channel ticket #4 leaked into the reduced-authority "
            "'remaining' count -- body: %r" % captured["b"])

    def test_full_authority_remaining_excludes_ops_channel(self):
        # #362 review: the full-authority "remaining" query must AND
        # -label:ops-channel onto EVERY per-qual search string -- #382
        # widened this from a single core-only query to a union of core +
        # needs-gatekeeper + ready-for-review, and the exclusion must
        # survive onto each of the three new queries too, not just the
        # original core one. Without it, a query would read the inflated
        # whole-population figure (5 items); with it, the correctly-scoped
        # 2 -- and since AUTOPILOT_SKIP_EXCL is the shared `base` for every
        # qual, all three per-qual queries below see the exclusion and
        # union to the SAME 2 numbers, never accumulating to 5.
        import unittest.mock as mk

        def gh(*a, **k):
            # Argv-position match for "issue view", never a substring on
            # the joined text -- "label:ready-for-review" itself contains
            # the literal substring "view" ("re-VIEW").
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            j = " ".join(str(x) for x in a)
            if "-label:ops-channel" in j:
                return '[{"number":1},{"number":2}]'
            return '[{"number":1},{"number":2},{"number":3},{"number":4},{"number":5}]'

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: (
                                  captured.setdefault("b", body),
                                  "sent")[1]):
                    airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "the full-authority obligation-scoped 'remaining' count did "
            "not exclude ops-channel -- body: %r" % captured["b"])

    def test_full_authority_remaining_matches_the_obligation_set_not_core_alone(self):
        # #382: #367's own adversarial review (F4) found the card's
        # `remaining` still uses the NARROWER core-only derivation while the
        # footer's `I N` (and the `/goal` stop-proof, `core-quals --count`)
        # already widened to the OBLIGATION set (`_obligation_quals()` --
        # core partition UNION needs-gatekeeper/ready-for-review, regardless
        # of which stream owns the ticket). #10 is a plain core ticket; #20
        # is a STREAM-OWNED ticket that ALSO carries needs-gatekeeper --
        # outside the core partition, but part of the obligation set. The
        # pre-#382 code queried ONLY the core partition (a single `-q
        # length` count) and would report "1", missing #20 entirely; the
        # fix must union in the maintainer-action-labelled tickets too and
        # report "2" -- the SAME number the footer's I N would show for
        # this repo.
        import unittest.mock as mk

        core_excl = airuleset._core_search_excl()

        def gh(*a, **k):
            # Match "issue view" by ARGV POSITION, never by substring on
            # the joined command text -- "label:ready-for-review" itself
            # contains the literal substring "view" ("re-VIEW"), so a bare
            # `"view" in j` check would misclassify that qual's own query
            # as the title/labels lookup and return the wrong shape.
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            j = " ".join(str(x) for x in a)
            if "length" in j:
                # pre-#382 shape: a single core-only "-q length" count --
                # never fired by the fixed code, since it never asks for
                # length any more, but kept here so this fixture would
                # correctly reproduce the OLD (narrower) behaviour too.
                return "1"
            if "label:needs-gatekeeper" in j:
                return '[{"number":20}]'
            if "label:ready-for-review" in j:
                return "[]"
            if core_excl in j:
                return '[{"number":10}]'
            return "[]"

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: (
                                  captured.setdefault("b", body),
                                  "sent")[1]):
                    airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)(?:\s+(\S+))?", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "full-authority 'remaining' still counts only the core "
            "partition (missed the needs-gatekeeper-labelled #20 outside "
            "it) -- body: %r" % captured["b"])
        self.assertIsNone(
            m.group(2),
            "'remaining' now counts the SAME obligation set the footer's "
            "unlabeled I N shows -- a 'core' scope_label next to it would "
            "misrepresent the count as narrower than it actually is -- "
            "body: %r" % captured["b"])

    def test_full_authority_remaining_targets_the_explicit_repo_not_cwd(self):
        # #382 adversarial-review MAJOR: `_union_open_issues(quals, base,
        # repo=repo)` threads `--repo` through as `-R <repo>` on EVERY
        # per-qual query -- dropping that kwarg silently falls back to gh's
        # own cwd-resolved repo, which is WRONG for the run-card: it fires
        # from an arbitrary worker cwd (a worktree, a subdev checkout, a
        # cross-project process-subdev fire) that need not match the
        # `--repo` it was explicitly given. The two sibling tests above
        # only ever assert on the RETURNED counts, so a mutant dropping
        # `repo=repo` from the call is invisible to them (the fake `gh`
        # ignores argv entirely) -- this test asserts on the argv ITSELF:
        # `-R <repo>` must be present on every one of the three per-qual
        # union queries the full-authority branch issues.
        import unittest.mock as mk

        seen_argvs = []

        def gh(*a, **k):
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            seen_argvs.append(a)
            return "[]"

        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: "sent"):
                    airuleset.cmd_notify(self._args())

        union_calls = [a for a in seen_argvs
                      if len(a) > 1 and a[0] == "issue" and a[1] == "list"]
        self.assertEqual(
            len(union_calls), 3,
            "expected exactly 3 per-qual union queries (core, "
            "needs-gatekeeper, ready-for-review) -- got %r" % (union_calls,))
        for a in union_calls:
            self.assertIn(
                "-R", a,
                "a full-authority union query did not target an explicit "
                "repo via -R -- it would silently fall back to gh's own "
                "cwd-resolved repo instead of the --repo the card was "
                "given -- argv: %r" % (a,))
            r_idx = a.index("-R")
            self.assertEqual(
                a[r_idx + 1], "kvaskodev/odoo-erp",
                "the union query's -R value did not match the card's own "
                "--repo -- argv: %r" % (a,))


class TestSliceQualsIsTheOneSliceDefinition(TestCase):
    """#181: montalu@subdev's armed /goal declared the backlog EMPTY (its own
    stop-proof printed 0) while the footer on the SAME box read 'Issues 5' —
    a real FALSE STOP, not a labeling mismatch. Root cause: the footer's
    `_slice_quals()` already branches on the gh LOGIN (shared-account box ->
    the stream LABEL alone, since `@me` there resolves to the maintainer and
    matches nothing assigned) but the /goal proof template hardcoded
    `--assignee @me` with no such branch. `slice-quals` is the fix: ONE
    definition, consumed by the footer AND the /goal proof, so they cannot
    drift apart again."""

    def test_shared_account_box_gets_a_nonzero_count_when_work_is_open(self):
        # Reproduces the live montalu incident exactly: gh login == the
        # maintainer account, an open ticket carries label:stream:montalu and
        # is NOT assigned to anyone. --assignee @me finds nothing (the old,
        # broken proof); slice-quals must still report it.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "assignee:@me" in j:
                return "[]"   # the OLD proof's key — must NEVER be relied on
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z"}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_cmd_slice_quals_actually_calls_the_shared_slice_quals_function(self):
        # I7 (round 2 review): the old test here only asserted
        # _slice_quals()'s OWN output, which is byte-identical before and
        # after the #181 round-1 fix (only cmd_slice_quals/its callers were
        # added around it) -- so it passed UNCHANGED pre-fix and proved
        # nothing about cmd_slice_quals itself. This one mocks
        # _slice_quals() to return a DISTINCTIVE sentinel qual and proves
        # cmd_slice_quals's own gh queries are genuinely built from THAT
        # value -- it fails against a reimplementation that re-derives its
        # own query instead of calling the shared function (the
        # single-source-of-truth claim this mechanism exists to guarantee).
        import contextlib
        import io
        import unittest.mock as mk

        calls = []

        def fake_gh(*a, **k):
            calls.append(a)
            # Non-empty so the C2 shared-account-zero validation path (a
            # DIFFERENT, unrelated check) never engages here — this test is
            # only about WHICH qual the query was built from.
            return '[{"number": 1, "title": "t", "createdAt": "2026-07-01T00:00:00Z"}]'

        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="fork-no-merge"):
            with mk.patch.object(airuleset, "_slice_quals",
                                 return_value=["label:__sentinel_qual__"]) as sq:
                with mk.patch.object(airuleset, "_gh_out", side_effect=fake_gh):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        sq.assert_called()
        self.assertTrue(
            any("label:__sentinel_qual__" in " ".join(str(x) for x in c)
                for c in calls),
            "cmd_slice_quals never queried gh using _slice_quals()'s own "
            "returned qual -- it is not actually the single source of truth")

    def test_own_account_box_still_unions_all_three_quals(self):
        # david/kvaskodev (own-account) must keep the full 3-way union — a
        # naive "just use the label" fix would under-count them.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "assignee:@me" in j:
                return ('[{"number": 1, "title": "a", '
                        '"createdAt": "2026-07-01T00:00:00Z"}]')
            if "author:@me" in j:
                return ('[{"number": 2, "title": "b", '
                        '"createdAt": "2026-07-02T00:00:00Z"}]')
            if "label:stream:david" in j:
                return ('[{"number": 2, "title": "b", '
                        '"createdAt": "2026-07-02T00:00:00Z"}]')   # dup of #2
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "2")   # {1, 2} unioned, not 3

    def test_a_failed_gh_query_never_prints_zero(self):
        # The false-stop's exact shape: a wrong/failed query must NEVER
        # collapse to a printed 0 — that IS the bug this exists to prevent.
        import contextlib
        import io
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", return_value="not json"):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                    self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())


class TestSliceQualsIncludesOwnBounceTickets(TestCase):
    """#307's symmetric check: `core-quals` DROPS `prio:bounce` from the
    full-authority obligation set (it is the SUB-DEV's own work, not this
    box's), but `slice-quals` on the sub-dev's OWN box must still INCLUDE its
    own `prio:bounce` tickets — that is the stream's priority lane (Step 3.1
    seeds from it), and the two sides must stay complementary rather than
    both claiming or both dropping the same ticket.

    No code change was needed for this half: a bounce ticket always ALSO
    carries `stream:<user>` per the cross-stream protocol, and
    `_slice_quals()` never excluded that label — this locks the invariant so
    a future change to `_slice_quals()` cannot silently drop it."""

    def test_own_account_stream_slice_still_finds_its_own_bounce_ticket(self):
        # Own-account stream (david/kvaskodev): assignee ∪ author ∪
        # stream:<user> — a bounce ticket carrying ONLY stream:david (not
        # assigned/authored) must still be found via the stream-label qual.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:david" in j:
                return ('[{"number": 42, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}, '
                        '{"name": "stream:david"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_shared_account_stream_slice_still_finds_its_own_bounce_ticket(self):
        # Shared-account stream (montalu/marek/simap): the slice is
        # `label:stream:<user>` ALONE — a bounce ticket must still surface
        # through that one qual.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 99, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}, '
                        '{"name": "stream:montalu"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")


class TestSliceQualsRefusesRatherThanGuessing(TestCase):
    """Round-2 adversarial review of 49cd3d4..2612400: the #181 round-1 fix
    RELOCATED the false-empty-0 failure instead of removing it (C1, C2 —
    both live-confirmed / reproduced). Also locks I3's finding: the
    footer's gk-partitioned display and slice-quals's raw open count
    deliberately answer DIFFERENT questions, and that difference is safe,
    not a bug to "fix" into numeric equality."""

    def test_full_authority_box_refuses_instead_of_printing_zero(self):
        # C1: live-confirmed on dev1 -- cmd_slice_quals never consulted
        # resolve_authority()/AUTHORITY_BY_USER, so on a full-authority box
        # (no stream at all) it silently built label:stream:<linux-user>,
        # which matches nothing, and printed a clean 0 with 29 real open
        # tickets sitting untouched. It must refuse, never print a count.
        import contextlib
        import io
        import unittest.mock as mk

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="newlevel"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="zbynekdrlik"):
                    with mk.patch.object(airuleset, "_gh_out",
                                         return_value="[]"):
                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            with self.assertRaises(SystemExit) as cm:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_refuses_when_the_label_does_not_exist(self):
        # C2: a forgotten/never-created stream:<user> label makes gh search
        # return [] with exit 0 for a query that can never match anything —
        # a false-empty stop under a different key than round 1's @me.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            return "[]"   # the label itself was never created; every query empty

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_refuses_when_the_cross_check_query_fails(self):
        # C2: the label genuinely exists but nothing carries it — a
        # cross-check must ALSO prove gh's SEARCH path works for this
        # identity/repo before a 0 is trusted. #181 I-1 (round 3) replaced
        # round 2's `involves:@me` probe, which accepted `[]` — the very
        # state it claimed to detect — with a SORT-ONLY search that cannot
        # legitimately be empty; this test now drives that probe failing.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a and a[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return ""   # gh error on the cross-check probe
            return "[]"     # the slice query itself: empty

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_is_trusted_once_validated(self):
        # The genuinely legitimate case must still work: the label exists,
        # nothing carries it, AND gh search is demonstrably healthy for
        # this identity/repo (the cross-check succeeds) — 0 is real here.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a and a[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return '[{"number": 999}]'   # search demonstrably answers
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_a_handed_off_ticket_no_longer_counts_once_handed_off(self):
        # #391 REVERSES this test's own prior premise for the REDUCED-
        # authority path only. I3 (round 2) had reasoned the footer's
        # len(mine)-gk was merely a DISPLAY partition and that slice-quals's
        # raw count must keep counting a handed-off-but-still-OPEN ticket --
        # but the SHIPPED #367 footer never actually computed len(mine)-gk
        # (it computed the FULL len(mine), gk as a subset badge), so that
        # reasoning rested on a formula the footer didn't implement. #391's
        # own explicit ask: a sub-dev's responsibility for a ticket is
        # fulfilled once it is HANDED OFF, not once the gatekeeper has also
        # closed it -- review-watch (the /goal loop staying alive while a
        # hand-off is outstanding) is the STOP-CONDITION's own job, checked
        # separately from this count via the loop's own review-watch clause,
        # never by keeping a handed-off ticket in the raw count. This is
        # scoped ONLY to the reduced-authority `slice-quals` path -- the
        # analogous FULL-authority `core-quals`/`_obligation_quals()` side is
        # UNCHANGED: a handed-off ticket correctly stays in the
        # full-authority box's obligation set until the gatekeeper actually
        # closes it (that box is the one still on the hook for it).
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:david" in j:
                return ('[{"number": 7, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")


class TestSliceQualsExcludesHandedOffLikeTheFooter(TestCase):
    """#391: `slice-quals --count`/`--list` (the reduced-authority `/goal`
    stop-proof) excludes handed-off work IDENTICALLY to `cmd_tickets_status`'s
    own footer -- the #367 consistency guard, applied to the reduced-
    authority reversal (own UNHANDLED work, not the raw slice). Both consume
    the SAME shared derivation, so a future change to one cannot silently
    diverge from the other. Full-authority `core-quals` is untouched --
    covered separately by `TestObligationVsDisplayPartition`/
    `TestCoreQualsCountsTheObligationSet`."""

    def test_count_excludes_a_ready_for_review_ticket(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_count_still_includes_a_genuinely_unhandled_ticket(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": []}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_list_omits_a_handed_off_ticket_too(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "handed off", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "needs-gatekeeper"}]},'
                        '{"number": 6, "title": "still mine", '
                        '"createdAt": "2026-07-02T00:00:00Z", '
                        '"labels": []}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=False, list=True, waiting=False, ops_wait=False, audit=False, extra=None))
        out = buf.getvalue()
        self.assertNotIn("5\t", out)
        self.assertIn("6\t", out)

    def test_a_bounce_ticket_stays_counted_even_though_it_once_was_handed_off(self):
        # #391's own explicit safety test, at the slice-quals layer: a
        # `prio:bounce` ticket (also carrying a stale ready-for-review label)
        # must stay in the UNHANDLED count -- never read as done just
        # because it once carried a hand-off label. Mirrors
        # `test_statusbar.py::test_refresh_a_bounce_ticket_stays_in_
        # unhandled_count_alongside_real_handoffs` for the footer.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}, '
                        '{"name": "prio:bounce"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a returned bounce ticket must stay in the stop-proof's own "
            "count, or the loop reads REVIEW as DONE")

    def test_an_invisible_bounce_stays_unhandled_even_with_a_stale_hand_off_comment(self):
        # #391 CRITICAL-1 (fresh-context adversarial review): mirrors
        # test_statusbar.py's own footer-layer pin. A `prio:bounce` ticket
        # whose comment thread carries ONLY a stale pre-bounce
        # READY-FOR-REVIEW comment (no gatekeeper-shaped comment anywhere
        # -- the sanctioned Discord nudge-lane bounce shape,
        # skills/autopilot/SKILL.md: a bare label + a sub-dev-authored ACK)
        # must stay in the stop-proof's own UNHANDLED count -- the comment
        # fallback must not silently overwrite the label-derived bounce
        # override just because the last (and only) comment signal is True.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "bounced no comment", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}, '
                        '{"name": "prio:bounce"}]}]')
            if "issues/5/timeline" in j:
                return '[{"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"}]'
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a bounce-labeled ticket must not be flipped back to handed "
            "by a stale hand-off comment when no gatekeeper comment is "
            "visible anywhere in the thread")

    def test_a_genuine_re_hand_off_visible_in_comments_still_counts_as_handed(self):
        # The positive control for the test above: a bounce comment IS
        # visible in the thread, followed by a genuine later re-hand-off --
        # this must still resolve to handed. Mirrors test_statusbar.py::
        # test_refresh_recovers_a_genuine_re_hand_off_after_a_bounce_finding
        # for the footer, at the slice-quals layer.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "re-handed", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}]}]')
            if "issues/5/timeline" in j:
                return ('[{"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},'
                        '{"event": "commented", "body": "**GATEKEEPER FINDING:** needs another fix, '
                        'bouncing back."},'
                        '{"event": "commented", "body": "READY-FOR-REVIEW: addressed the finding, '
                        're-pushed."}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_slice_quals_actually_calls_the_shared_handed_derivation(self):
        # MAJOR-2 (fresh-context adversarial review of #391): no test
        # anywhere in this repo referenced `_slice_mine_and_handed` by name
        # before this one -- a re-inlined, drifted derivation inside
        # cmd_slice_quals (e.g. silently dropping the comment-fallback/
        # recovery enrichment) would pass every pre-existing slice-quals
        # test above, since all of them are label-only fixtures. Mirrors
        # #181 I7's own sentinel-mock shape (`test_cmd_slice_quals_
        # actually_calls_the_shared_slice_quals_function`).
        import contextlib
        import io
        import unittest.mock as mk

        sentinel_rows = {
            5: {"number": 5, "title": "handed",
                "createdAt": "2026-07-01T00:00:00Z", "labels": []},
            6: {"number": 6, "title": "unhandled",
                "createdAt": "2026-07-02T00:00:00Z", "labels": []},
        }
        sentinel_handed = {5: True, 6: False}

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(
                        airuleset, "_slice_mine_and_handed",
                        return_value=(sentinel_rows, sentinel_handed,
                                      False)) as sm:
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        sm.assert_called()
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "cmd_slice_quals did not consume _slice_mine_and_handed's own "
            "returned handed map -- it may be re-deriving handed status "
            "itself instead of calling the shared derivation")


class TestRunCardHeartbeatSurvivesStreamCards(TestCase):
    """#181 I6 (round 2 regression): _write_autopilot_progress is the ONLY
    writer of `ts`, which keeps statusbar's 6h AUTOPILOT_RUN_WINDOW_S run
    window alive. #164's fix skipped calling it ENTIRELY for a stream
    ticket's card on a full-authority box — that also skipped the
    heartbeat, so a run carding only stream tickets never activates
    'Issues D/T' at all. Also locks M11: a failed issue lookup must
    default to NOT bumping `done`, never the pre-#164 wrong direction."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                    repo_name=False, newest_card=False,
                    backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
                    repo="o/r", issue=5, pr=None,
                    achieved="Fix nasadený a overený", result=None, goal="cieľ", version=None,
                    merge_sha=None, url=None, review="ok", handoff=False,
                    dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def test_stream_tickets_card_still_writes_the_heartbeat(self):
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return '{"title": "t", "labels": [{"name": "stream:montalu"}]}'
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), False)

    def test_core_tickets_card_still_bumps_done(self):
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return '{"title": "t", "labels": []}'
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), True)

    def test_a_failed_issue_lookup_defaults_to_not_bumping_done(self):
        # M11: a parse/lookup failure used to leave is_core_ticket=True
        # (view.get("labels") is None -> _ticket_is_stream_labeled(None) is
        # False -> `not False` is True), silently restoring the PRE-#164
        # wrong behaviour. It must default the SAFE direction: do not bump
        # `done` when we could not tell.
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return ""     # gh error / empty response
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), False)


def _bump_done_of(call_args):
    """The EFFECTIVE `bump_done` a call passed, read from either a keyword or
    a positional argument (#181 M-3).

    The round-2 tests asserted `kwargs.get("bump_done")`, which couples the
    lock to call STYLE rather than behaviour: a correct implementation that
    passes the flag positionally false-fails, and a wrong one that accepts the
    kwarg and ignores it passes. `_write_autopilot_progress(name, remaining,
    bump_done=True)` puts it at positional index 2."""
    args, kwargs = call_args
    if "bump_done" in kwargs:
        return kwargs["bump_done"]
    return args[2] if len(args) > 2 else True


def _fake_gh_by_search(populations):
    """A `_gh_out` stand-in that answers an `--search` query with whichever
    populations' keys occur in the search string, unioned.

    It deliberately serves BOTH query shapes so the same fixture measures the
    round-2 implementation (one query, `--json number -q length`, an integer
    on stdout) and this one (per-qual queries, JSON rows unioned in Python) —
    a fixture that only served one of them would fail pre-fix for a parsing
    reason instead of a behavioural one."""
    import json as _json

    searches = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        searches.append(search)
        nums = set()
        for key, val in populations.items():
            if key in search:
                nums |= set(val)
        if "-q" in args:
            return str(len(nums))
        return _json.dumps(
            [{"number": n, "title": "t%d" % n,
              "createdAt": "2026-07-%02dT00:00:00Z" % (n % 28 + 1)}
             for n in sorted(nums)])

    return gh, searches


class TestCoreQualsCountsTheObligationSet(TestCase):
    """#181 round 3, CRITICAL: `_core_search_excl()` is the FOOTER's *display*
    partition ("which population am I showing"); round 2's I4 fix reused it as
    the /goal stop-proof's *obligation* partition ("what must I finish before
    I may stop"). Those are not the same set.

    Live on zbynekdrlik/odoo-erp 2026-07-30: 83 open non-skip, 40 in the core
    partition, 5 open `needs-gatekeeper` of which #2396 and #2377 carry
    `stream:montalu` and are therefore INVISIBLE to a core-only count. The
    gatekeeper closes its 40, the proof prints 0, the loop stops, and those
    tickets stay blocked on the very box that stopped. That is #181 verbatim
    at a new address.

    #307 (2026-08-07) correction: `prio:bounce` is NOT one of
    MAINTAINER_ACTION_LABELS any more — it means the gatekeeper returned the
    ticket to the SUB-DEV, who acts next, not this box. A bare open
    `prio:bounce` (no `needs-gatekeeper`/`ready-for-review` alongside it) is
    the sub-dev's own work and must NOT be counted; a ticket carrying BOTH
    still counts via the `ready-for-review` qual (the hand-off is the live
    signal)."""

    POPULATIONS = {
        "-label:stream:": {1, 2},                 # the core partition
        "label:needs-gatekeeper": {2396, 2377},   # stream-labelled, only I can act
        "label:prio:bounce": {5},                 # #307: sub-dev's ball, never queried
        "label:ready-for-review": {8},            # a hand-off awaiting my review
    }

    def _run(self, populations=None, **flags):
        import contextlib
        import io
        import unittest.mock as mk

        gh, searches = _fake_gh_by_search(
            self.POPULATIONS if populations is None else populations)
        buf = io.StringIO()
        args = dict(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None)
        args.update(flags)
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(mk.Mock(**args))
        return buf.getvalue(), searches

    def test_a_stream_ticket_only_this_box_can_action_is_counted(self):
        out, _ = self._run()
        # {1, 2} ∪ {2396, 2377} ∪ {8} — five tickets; #5 (bare prio:bounce)
        # is deliberately NOT one of them (#307).
        self.assertEqual(out.strip(), "5")

    def test_the_listing_names_the_tickets_only_this_box_can_unblock(self):
        out, _ = self._run(count=False, list=True)
        self.assertIn("2396", out)
        self.assertIn("2377", out)
        self.assertIn("8\t", out)

    def test_a_bare_bounce_ticket_is_not_this_boxs_obligation(self):
        """#307: `prio:bounce` alone means the SUB-DEV acts next, not the
        gatekeeper — a bare bounce ticket (no `needs-gatekeeper`/
        `ready-for-review` alongside it) must not appear in the obligation
        listing OR count."""
        out, _ = self._run(count=False, list=True)
        self.assertNotIn("5\t", out)
        count_out, _ = self._run()
        self.assertNotEqual(count_out.strip(), "6")   # would be 6 if #5 leaked in

    def test_a_ticket_carrying_both_bounce_and_handoff_still_counts(self):
        """A ticket carrying BOTH `prio:bounce` AND `ready-for-review` still
        counts — the hand-off is the live signal (#307)."""
        out, _ = self._run({
            "-label:stream:": set(),
            "label:needs-gatekeeper": set(),
            "label:prio:bounce": {6},
            "label:ready-for-review": {6},
        })
        self.assertEqual(out.strip(), "1")

    def test_prio_bounce_is_never_queried_by_the_plain_obligation_set(self):
        """#307: unlike `needs-gatekeeper`/`ready-for-review`, `prio:bounce`
        is no longer one of MAINTAINER_ACTION_LABELS — the plain (no
        `--extra`) proof must never query it at all."""
        _, searches = self._run()
        joined = " | ".join(searches)
        self.assertNotIn("label:prio:bounce", joined)

    def test_a_stream_ticket_the_subdev_is_working_still_does_not_block_me(self):
        """NOT a revert to the whole-repo count — that is the never-stops
        failure the original ticket explicitly rejected."""
        out, searches = self._run({
            "-label:stream:": {1},
            "label:needs-gatekeeper": set(),
            "label:prio:bounce": set(),
            "label:ready-for-review": set(),
        })
        self.assertEqual(out.strip(), "1")
        # #362: the "bare" shape is now AUTOPILOT_SKIP_EXCL (autopilot-skip
        # + ops-channel), never the plain literal alone — checking against
        # the shared constant keeps this a real (non-vacuous) assertion.
        bare = [s for s in searches
                if s.strip() == airuleset.AUTOPILOT_SKIP_EXCL]
        self.assertEqual(
            bare, [],
            "a bare whole-repo query is back — that is the never-stops "
            "failure #181 rejected, not the obligation set")

    def test_every_maintainer_action_label_is_actually_queried(self):
        _, searches = self._run()
        joined = " | ".join(searches)
        for label in ("needs-gatekeeper", "ready-for-review"):
            self.assertIn("label:" + label, joined)

    def test_extra_filter_also_runs_a_bare_query_for_the_bounce_seed(self):
        """#307: with `prio:bounce` dropped from MAINTAINER_ACTION_LABELS, a
        per-qual AND (base + each obligation qual) can never find a ticket
        that is ONLY bounce-labelled — the common real case the bounce-lane
        SEED (Step 3.1, `core-quals --list --extra "label:prio:bounce"`)
        depends on. `--extra` must ALSO issue the BARE base query (no extra
        AND) so that coverage survives the correction."""
        _, searches = self._run(count=False, list=True, waiting=False,
                                extra="label:prio:bounce")
        self.assertIn(
            airuleset.AUTOPILOT_SKIP_EXCL + " label:prio:bounce", searches)

    def test_whitespace_only_extra_never_leaks_the_bare_whole_repo_query(self):
        """Adversarial review of #307: `extra=" "` is truthy, so an
        unstripped check would still take the bare-extra branch and union in
        a plain AUTOPILOT_SKIP_EXCL query -- the exact whole-repo
        never-stops shape #181 rejected."""
        _, searches = self._run(count=False, list=True, waiting=False, extra="   ")
        bare = [s for s in searches
                if s.strip() == airuleset.AUTOPILOT_SKIP_EXCL]
        self.assertEqual(
            bare, [],
            "a whitespace-only --extra leaked the bare whole-repo query")

    def test_reduced_authority_box_refuses_instead_of_answering(self):
        """I-3: C1's fix applied in the mirror direction. `slice-quals`
        correctly refuses on a full box; `core-quals` answered on ANY box, so
        run on montalu it printed a number that is neither that box's slice
        nor a valid stop-proof for it."""
        import contextlib
        import io
        import unittest.mock as mk

        gh, _ = _fake_gh_by_search(self.POPULATIONS)
        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="branch-merge"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        airuleset.cmd_core_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_failed_gh_query_never_prints_a_number(self):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", return_value="not json"):
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        airuleset.cmd_core_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")


class TestObligationVsDisplayPartition(TestCase):
    """The two partitions answer DIFFERENT questions on purpose (the same
    resolution round 2 reached for I3). Locked so a future round cannot
    "fix" the divergence into numeric equality and re-open #181, nor fold
    the display partition back into the obligation one and re-open #164."""

    def test_the_footers_exclusion_still_hides_every_reduced_stream(self):
        excl = airuleset._core_search_excl()
        for user, profile in airuleset.AUTHORITY_BY_USER.items():
            if profile != "full":
                self.assertIn("-label:stream:" + user, excl)

    def test_a_full_profile_entry_is_not_treated_as_a_sub_dev_stream(self):
        """M-5: `_core_search_excl()` keyed on AUTHORITY_BY_USER's KEYS, so a
        future `full` entry would silently remove a whole population from
        every full-authority count."""
        import unittest.mock as mk

        with mk.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"david": "fork-no-merge", "boss": "full"}):
            excl = airuleset._core_search_excl()
        self.assertIn("-label:stream:david", excl)
        self.assertNotIn("stream:boss", excl)

    def test_core_quals_declares_the_obligation_set_it_counts(self):
        """Driven through the command, so it fails when the COUNT is core-only
        rather than merely when a constant is missing. The two live tickets
        that proved the CRITICAL — odoo-erp #2396/#2377 — carry
        `stream:montalu` AND `needs-gatekeeper`, actionable ONLY by the box
        whose loop would otherwise stop.

        #307: `prio:bounce` is deliberately NOT declared any more — a bare
        bounce ticket is the sub-dev's own work, not this box's obligation."""
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", return_value="[]"):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        mk.Mock(count=False, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        printed = buf.getvalue()
        self.assertIn(airuleset._core_search_excl(), printed)
        for label in ("needs-gatekeeper", "ready-for-review"):
            self.assertIn("label:" + label, printed)
        self.assertNotIn("label:prio:bounce", printed)


class TestSliceQualsRefusesAnUnresolvableIdentity(TestCase):
    """#181 I-2, live-reproduced on david@subdev: `_gh_login`'s bare
    `except: return ""` made "gh api user failed" indistinguishable from
    "not the maintainer", so a broken-gh box silently got the own-account
    3-qual union — C2's shared-account validation skipped entirely, and on
    odoo-erp `author:@me` re-opens the 2026-07-20 foreign-stream leak the
    branch exists to prevent."""

    def _failing_gh_api_user(self):
        """A `subprocess.run` stand-in that reproduces the real failure:
        `gh api user` exits 4 ("please run gh auth login")."""
        import subprocess
        import unittest.mock as mk

        real = subprocess.run

        def run(argv, *a, **k):
            if list(argv[:3]) == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(
                    argv, 4, "",
                    "To get started with GitHub CLI, please run: gh auth login")
            return real(argv, *a, **k)

        return mk.patch("subprocess.run", side_effect=run)

    def test_slice_quals_never_returns_a_guessed_qual_set(self):
        with self._failing_gh_api_user():
            try:
                quals = airuleset._slice_quals("montalu")
            except Exception as exc:      # noqa: BLE001 - the type is asserted
                self.assertIsInstance(exc, airuleset.SliceUnresolved)
                return
        self.fail(
            "_slice_quals guessed %r from an identity it could not resolve — "
            "on a shared-account box that means C2's validation is skipped, "
            "and on odoo-erp `author:@me` is the whole maintainer backlog "
            "(the 2026-07-20 foreign-stream leak)" % (quals,))

    def test_the_cli_refuses_rather_than_printing_a_count(self):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with self._failing_gh_api_user():
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="branch-merge"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="montalu1"):
                    with mk.patch.object(
                            airuleset, "_gh_out",
                            return_value='[{"number": 1, "title": "t", '
                                         '"createdAt": "2026-07-01T00:00:00Z"}]'):
                        with contextlib.redirect_stdout(buf):
                            with self.assertRaises(SystemExit) as cm:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_resolved_login_still_picks_the_right_branch(self):
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            # #537: montalu is a base-stream rename target, so its shared-account
            # slice now carries BOTH the old and the new stream label (the
            # transition alias). The BRANCH selection (shared-account -> label
            # only) is unchanged; only the label set expanded.
            self.assertEqual(airuleset._slice_quals("montalu"),
                             ["label:stream:montalu", "label:stream:montalu1"])
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            # #537: david's own-account slice keeps assignee/author + BOTH the
            # old and new stream label (david + david1) -> 4 quals, not 3.
            self.assertEqual(len(airuleset._slice_quals("david")), 4)

    def test_montalu5_8_slice_quals_derive_generically_no_new_map_needed(self):
        # airuleset#378: statusline/`/goal` stop-proof scoping for
        # montalu5..montalu8 needs NO dedicated map -- `_slice_quals()`
        # already derives a shared-account stream's slice generically from
        # ANY user string once `_gh_login()` resolves to the maintainer
        # login (the exact branch montalu/montalu2/3/4 already take). This
        # PINS that genericity for the new accounts, per the ticket's own
        # explicit "derived automatically ... just PIN it with a test"
        # instruction.
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            for user in ("montalu5", "montalu6", "montalu7", "montalu8"):
                self.assertEqual(airuleset._slice_quals(user),
                                 ["label:stream:%s" % user], user)


class TestSliceQualsHandlesAppTokenBoxes(TestCase):
    """#356: david2/david3/david4 (odoo-erp#3282, spun up by airuleset#326)
    authenticate `gh` via a GitHub App INSTALLATION token
    (odoo-erp#3281's `gh-app-stream-tokens` mechanism), never a PAT. An
    App installation token carries NO user identity at all, so
    `gh api user` 403s ("Resource not accessible by integration") on
    EVERY call, structurally — not intermittently. `_gh_login()`
    correctly returns `None` on that (#181 I-2's own established
    contract, unchanged here), which used to make `_slice_quals()`
    raise `SliceUnresolved` unconditionally, so `slice-quals --count`
    could never reach 0 and the fork-no-merge/branch-merge `/goal`
    stop-proof condition (B) could never legitimately terminate on
    these boxes.

    Detection is a LOCAL, static fact — the App-token directory's
    presence (`~/.config/gh-app-tokens/`, the exact path
    `scripts/gh-app/gh-app-token.sh`/`push-stream-tokens.sh` read from
    and create in the real, deployed `zbynekdrlik/odoo-erp` mechanism)
    — never the network call itself. That is what makes it safe: it
    removes the identity ambiguity structurally instead of trying to
    classify a transient/generic gh failure (see the rejected
    alternative on the ticket's own design comment)."""

    def _app_token_dir(self, tmp):
        d = Path(tmp) / "gh-app-tokens"
        d.mkdir(parents=True)
        return d

    def _failing_gh_api_user(self):
        # Mirrors TestSliceQualsRefusesAnUnresolvableIdentity's own helper
        # ABOVE — a real 403 is just one more non-zero exit, and
        # `_gh_login` already treats ANY non-zero exit identically
        # (#181 I-2). Reusing the SAME shape here is deliberate: it proves
        # the fix works for the real failure mode, not a hand-picked one.
        import subprocess
        import unittest.mock as mk

        real = subprocess.run

        def run(argv, *a, **k):
            if list(argv[:3]) == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(
                    argv, 1, "",
                    "gh: HTTP 403: Resource not accessible by integration "
                    "(https://api.github.com/user)")
            return real(argv, *a, **k)

        return mk.patch("subprocess.run", side_effect=run)

    def test_an_app_token_box_gets_the_label_only_slice_never_slice_unresolved(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with self._failing_gh_api_user():
                    quals = airuleset._slice_quals("david2")
        self.assertEqual(quals, ["label:stream:david2"])

    def test_an_app_token_box_never_calls_gh_login_at_all(self):
        # The whole point is removing the network dependency, not just
        # tolerating its failure — assert the call never happens.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with mk.patch.object(airuleset, "_gh_login") as spy:
                    quals = airuleset._slice_quals("david3")
        spy.assert_not_called()
        self.assertEqual(quals, ["label:stream:david3"])

    def test_a_pat_box_with_no_app_token_dir_is_completely_unaffected(self):
        # False-positive control: without the App-token dir, both of
        # _slice_quals()'s EXISTING branches behave byte-identically to
        # before this fix.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gh-app-tokens"      # deliberately never created
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(missing)}):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="zbynekdrlik"):
                    # #537: the base-stream rename alias expands montalu's
                    # shared-account slice to both labels; the App-token
                    # branch selection this test guards is still unaffected.
                    self.assertEqual(airuleset._slice_quals("montalu"),
                                     ["label:stream:montalu", "label:stream:montalu1"])
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    self.assertEqual(len(airuleset._slice_quals("david")), 4)

    def test_the_default_token_dir_matches_the_real_deployed_path(self):
        # Locks the ACTUAL path the real scripts/gh-app/*.sh in
        # zbynekdrlik/odoo-erp read from and create -- not an invented
        # convention.
        import unittest.mock as mk

        with mk.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GH_APP_TOKEN_DIR", None)
            got = airuleset._gh_app_token_dir()
        self.assertEqual(got, Path.home() / ".config" / "gh-app-tokens")

    def test_a_plain_file_at_that_path_is_not_treated_as_the_token_dir(self):
        # A stray FILE (not a directory) at the path must not be misread as
        # "provisioned" -- `.is_dir()`, never a bare `.exists()`.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            stray = Path(tmp) / "gh-app-tokens"
            stray.write_text("not a directory")
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(stray)}):
                with self._failing_gh_api_user():
                    with self.assertRaises(airuleset.SliceUnresolved):
                        airuleset._slice_quals("david4")

    def test_a_network_failure_counting_the_slice_still_refuses_never_a_false_zero(self):
        # Fail-SAFE, not fail-OPEN: once the identity ambiguity is resolved
        # LOCALLY (never calls _gh_login at all -- asserted below), a
        # genuine gh failure while actually COUNTING the slice must still
        # refuse (non-zero exit, empty stdout) -- never a false
        # "0 = slice empty".
        import contextlib
        import io
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            buf = io.StringIO()
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with mk.patch.object(airuleset, "resolve_authority",
                                     return_value="fork-no-merge"):
                    with mk.patch.object(airuleset, "_current_user",
                                         return_value="david2"):
                        with mk.patch.object(airuleset, "_gh_login") as spy:
                            with mk.patch.object(airuleset, "_gh_out",
                                                 return_value=""):
                                with contextlib.redirect_stdout(buf):
                                    with self.assertRaises(SystemExit) as cm:
                                        airuleset.cmd_slice_quals(
                                            mk.Mock(count=True, list=False, waiting=False,
                                                   ops_wait=False, audit=False, extra=None))
        spy.assert_not_called()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")


class TestSearchIndexCrossCheckAssertsNonEmpty(TestCase):
    """#181 I-1: round 2's `involves:@me` cross-check only required the
    response to PARSE — and `[]` parses. "Search returns nothing everywhere"
    IS `[]`, i.e. the exact state it claimed to detect was the state it
    accepted (reviewer executed it: every query [] -> rc 0, stdout `0`)."""

    def _run(self, gh):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        exc = None
        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="branch-merge"):
            with mk.patch.object(airuleset, "_gh_login",
                                 return_value="zbynekdrlik"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="montalu1"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(buf):
                            try:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                            except SystemExit as e:
                                exc = e
        return buf.getvalue(), exc

    def test_an_empty_search_index_refuses_instead_of_reporting_zero(self):
        """Every SEARCH query empty while the repo demonstrably has open
        issues: the search index is not answering, so 0 is not evidence."""
        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "--search" in args:
                return "[]"          # search sees nothing, anywhere
            return '[{"number": 4242}]'   # the REST listing path does

        out, exc = self._run(gh)
        self.assertIsNotNone(exc, "an unanswering search index printed a count")
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(out.strip(), "")

    def test_a_healthy_search_index_still_trusts_a_real_zero(self):
        def gh(*a, **k):
            args = [str(x) for x in a]
            j = " ".join(args)
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return '[{"number": 4242}]'   # search demonstrably answers
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNone(exc, "a validated zero must still be reportable")
        self.assertEqual(out.strip(), "0")

    def test_a_repo_with_no_open_issues_at_all_still_trusts_zero(self):
        """The sort-only probe is legitimately empty here, and the REST path
        confirms it — an empty slice on an empty repo is trivially correct."""
        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")

    def test_a_failing_cross_check_probe_refuses(self):
        def gh(*a, **k):
            args = [str(x) for x in a]
            j = " ".join(args)
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return ""            # gh error on the probe itself
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(out.strip(), "")


class TestSliceQualsResolvesAgainstTheRepoRoot(TestCase):
    """#181 I-5: `cmd_slice_quals` resolved authority against the PROCESS cwd
    (`resolve_authority()` reads `Path.cwd()/CLAUDE.md`) while the footer
    resolves against the repo ROOT — so a project marker was invisible to one
    of the two consumers of "THE one definition", and they could disagree
    about which profile the box is even running."""

    def _fake_gh(self, bindir):
        import os as _os
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"api user"*) echo "kvaskodev";;\n'
            '  *"repo view"*) echo "kvaskodev/demo";;\n'
            '  *assignee:@me*) echo \'[{"number":1,"title":"t",'
            '"createdAt":"2026-07-01T00:00:00Z"}]\';;\n'
            '  *) echo "[]";;\n'
            "esac\n")
        gh.chmod(0o755)
        _os.environ  # noqa: B018 - keep the import used and explicit
        return gh

    def test_a_project_marker_at_the_root_is_honoured_from_a_subdirectory(self):
        import os
        import subprocess
        import sys as _sys
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=branch-merge -->\n")
            sub = Path(repo, "addons", "deep")
            sub.mkdir(parents=True)
            self._fake_gh(bindir)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
            env.update(HOME=home, PATH="%s:%s" % (bindir, os.environ["PATH"]))
            r = subprocess.run(
                [_sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals", "--count"],
                cwd=str(sub), capture_output=True, text=True, env=env)
            self.assertEqual(
                r.returncode, 0,
                "the root marker was invisible from a subdirectory: %s"
                % r.stderr)
            self.assertEqual(r.stdout.strip(), "1")


class TestGhCallsCarryTheCredentialsFileToken(TestCase):
    """#181 I-6, CONFIRMED live on david@subdev 2026-07-30: `_gh_out` did not
    use `_gh_env()`, unlike `cmd_tickets_status`. That box has no GH_TOKEN in
    its shell env and authenticates per-command from ~/.git-credentials, so
    bare `gh` exits 4 and `slice-quals --count` printed "a gh query failed"
    and exited 1 — which means the fork-no-merge template's condition (B) can
    never hold there and that loop can never legitimately finish."""

    def test_slice_quals_works_on_a_box_whose_gh_is_only_credentials_file_authed(self):
        import os
        import subprocess
        import sys as _sys
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            Path(home, ".git-credentials").write_text(
                "https://kvaskodev:ghp_fake_token_for_this_test@github.com\n")
            gh = Path(bindir) / "gh"
            # Exactly david's box: unauthenticated without a token in the env.
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ -z "${GH_TOKEN:-}" ]; then\n'
                '  echo "To get started with GitHub CLI, please run: '
                'gh auth login" >&2\n'
                "  exit 4\n"
                "fi\n"
                'case "$*" in\n'
                '  *"api user"*) echo "kvaskodev";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"title":"t",'
                '"createdAt":"2026-07-01T00:00:00Z"}]\';;\n'
                '  *) echo "[]";;\n'
                "esac\n")
            gh.chmod(0o755)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
            env.update(HOME=home, PATH="%s:%s" % (bindir, os.environ["PATH"]))
            r = subprocess.run(
                [_sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals", "--count"],
                cwd=repo, capture_output=True, text=True, env=env)
            self.assertEqual(
                r.returncode, 0,
                "condition (B) is unsatisfiable on a credentials-file box: %s"
                % r.stderr)
            self.assertEqual(r.stdout.strip(), "1")


class TestGhEnvPrefersFreshMintOverGitCredentialsCorpse(TestCase):
    """#401: on an App-token box (odoo-erp#3281's gh-app-stream-tokens
    mechanism -- david2-4, marek, montalu/2/3/4) ~/.git-credentials can
    independently hold a ONE-SHOT snapshot of a 60-minute App installation
    token, written once by whatever process last wrote it and NEVER
    refreshed -- while the box's real live-refresh path
    (~/.config/gh-app-tokens/, refreshed every 45 min by a gatekeeper timer)
    sits right next to it. `_gh_env()` used to always prefer the static
    .git-credentials snapshot -- once that corpse token expired, EVERY gh
    call this file makes failed 401 forever, even though a live token sat
    right next to it (live-diagnosed on montalu3@subdev, 2026-08-12: the
    .git-credentials line was ~11.5h stale -- App tokens live 60 min --
    while ~/.config/gh-app-tokens/primary was ~31 min old and fully live).
    Fix: an App-token box (directory-presence detected via
    `_is_gh_app_token_box()`, #356's existing, local/static signal
    `_slice_quals()` already uses -- no network call) reads the fresh
    per-call token file instead of the git-credentials snapshot."""

    def test_app_token_box_uses_the_fresh_token_file_not_git_credentials(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\n")
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "fresh-app-token")

    def test_app_token_box_with_no_delivered_token_yet_falls_through_to_a_real_pat(self):
        # Adversarial-review MAJOR-1: `_is_gh_app_token_box()`'s own
        # docstring already documents a known residual -- a STRAY App-token
        # dir can exist on a genuine own-account PAT box (a misdirected
        # delivery, or an App->PAT migration leftover). An unconditional
        # early-return here (no fall-through) would silently kill that
        # box's real, working PAT auth the moment such a stray dir shows
        # up -- a new, mirror-image regression of #401 itself. The box's
        # own timer not having delivered a token YET must fall through to
        # the same .git-credentials logic every other box uses.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()          # provisioned, but no "primary" file yet
            Path(home, ".git-credentials").write_text(
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_app_token_box_with_only_a_corpse_credential_still_leaves_gh_token_unset(self):
        # The fall-through above must NEVER resurrect the #401 corpse: when
        # the ONLY thing .git-credentials holds is an x-access-token line
        # (the App mechanism's own fixed placeholder username), the second
        # belt still refuses it even though the App branch fell through to
        # reach it. This is the precise, un-masked version of the old
        # (adversarial-review MAJOR-2) test -- that one used an
        # x-access-token fixture too, which made it pass for EITHER "no
        # fall-through" or "fall through but still refuse", so it never
        # actually distinguished the two designs from each other.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()          # provisioned, but no "primary" file yet
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertNotIn("GH_TOKEN", env)

    def test_an_earlier_corpse_line_never_hides_a_genuine_pat_recorded_later(self):
        # Adversarial-review MINOR-2: the old `re.search` took the FIRST
        # github.com match only, so an x-access-token line appearing BEFORE
        # a real PAT line in .git-credentials would refuse the whole file
        # even though a genuine, usable credential sat right below it --
        # reachable on a box mid-migration where both lines coexist. The
        # scan must try every match, not just the first.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n"
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_a_malformed_token_file_degrades_to_no_token_never_crashes(self):
        # Adversarial-review MINOR-4/5: `primary` may be caught mid-write by
        # its own 45-min refresh timer, or otherwise corrupt -- a
        # UnicodeDecodeError (a ValueError subclass, NOT caught by a bare
        # `except OSError`) must degrade to "no token found", never
        # propagate and crash the caller. A multi-line file (e.g. a stray
        # trailing metadata line) must also never smuggle a 2nd line into
        # the token value -- only the first line is ever used.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_bytes(b"\xff\xfe not valid utf-8")
            Path(home, ".git-credentials").write_text(
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()      # must not raise
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_multi_line_token_file_only_ever_uses_the_first_line(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\nexpires=soon\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "fresh-app-token")

    def test_a_spoofed_subdomain_host_is_never_matched(self):
        # Adversarial-review THEORETICAL-3: the host match had no trailing
        # anchor, so a credentials line for a LOOKALIKE host
        # (github.com.evil.example) would match "github.com" as a mere
        # substring and hand that unrelated token to gh, which would then
        # send it to the real github.com in an Authorization header.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://someone:gitlab-secret@github.com.evil.example\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertFalse(env.get("GH_TOKEN"))

    def test_real_env_token_still_wins_on_an_app_token_box(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "explicit-real-token"}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "explicit-real-token")

    def test_x_access_token_credential_is_never_authoritative_even_without_app_dir(self):
        # A stray x-access-token: line in .git-credentials structurally can
        # only be an App installation-token corpse (it's the FIXED username
        # GitHub requires for one) -- never a valid durable PAT, so it must
        # never be fed to gh even on a box _is_gh_app_token_box() doesn't
        # currently recognize (dir missing/relocated).
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertFalse(env.get("GH_TOKEN"))

    def test_pat_box_git_credentials_fallback_is_completely_unaffected(self):
        # False-positive control: a genuine PAT box (david-style, no
        # App-token dir, non-x-access-token username) must behave BYTE-
        # IDENTICAL to before this fix.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://kvaskodev:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")


class TestSliceQualsDoesNotSilentlyCapItsOwnCount(TestCase):
    """M-2: `slice-quals` still asked for `-L 200` while `core-quals` asked
    for 1000. A single population can only be UNDER-counted by a clamp, never
    zeroed — but the documented "0 = slice empty" contract must not silently
    cap either."""

    def test_the_slice_queries_ask_for_the_same_limit_as_core_quals(self):
        import contextlib
        import io
        import unittest.mock as mk

        seen = []

        def gh(*a, **k):
            seen.append([str(x) for x in a])
            return ('[{"number": 1, "title": "t", '
                    '"createdAt": "2026-07-01T00:00:00Z"}]')

        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="fork-no-merge"):
            with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="david1"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(io.StringIO()):
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        issue_queries = [q for q in seen if q and q[0] == "issue"]
        self.assertTrue(issue_queries)
        for q in issue_queries:
            self.assertIn("1000", q)
            self.assertNotIn("200", q)


class TestHeartbeatRefreshesALiveRunNeverOpensOne(TestCase):
    """#164 M-1 (round 3): a heartbeat-only write with no prior progress file
    created `{"done": 0}` + a fresh `ts`, and statusbar then rendered
    `Issues 0/40` for a full 6h window — an assertion that a run is active and
    has achieved nothing, replacing the correct `Issues 40 core`. Round 2
    fixed a heartbeat that stopped too early with one that started too
    eagerly."""

    def setUp(self):
        import unittest.mock as mk
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        p = mk.patch("statusbar.progress_dir", return_value=self.dir)
        p.start()
        self.addCleanup(p.stop)

    def _read(self, name="demo"):
        import json as _json
        return _json.loads((self.dir / (name + ".json")).read_text())

    def _seed(self, done, age_s, name="demo"):
        import json as _json
        import time
        (self.dir / (name + ".json")).write_text(_json.dumps(
            {"done": done, "remaining": 7, "ts": int(time.time()) - age_s}))

    def test_a_heartbeat_with_no_live_run_writes_nothing(self):
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertFalse(
            (self.dir / "demo.json").exists(),
            "a stream-only card opened a run window at 0/40 — the footer now "
            "claims an active run that has achieved nothing")

    def test_a_heartbeat_after_the_window_expired_does_not_reopen_it(self):
        import statusbar
        self._seed(done=4, age_s=statusbar.AUTOPILOT_RUN_WINDOW_S + 60)
        before = self._read()
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertEqual(
            self._read(), before,
            "an expired run window was reopened at done=0 by a heartbeat")

    def test_a_heartbeat_inside_the_window_advances_ts_and_leaves_done(self):
        """#164 I-4: the invariant round 2 shipped with NO behavioural test —
        replacing `done = base_done + 1 if bump_done else base_done` with
        `done = base_done + 1` left 104 tests passing."""
        self._seed(done=4, age_s=300)
        before = self._read()
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        after = self._read()
        self.assertEqual(after["done"], 4, "bump_done=False incremented done")
        self.assertGreater(after["ts"], before["ts"],
                           "the heartbeat did not advance ts")

    def test_a_core_ticket_card_still_increments_done(self):
        self._seed(done=4, age_s=300)
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        self.assertEqual(self._read()["done"], 5)

    def test_a_first_core_ticket_card_opens_the_run_window(self):
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        self.assertEqual(self._read()["done"], 1)


# --------------------------------------------------------------------------- #
# Round 4. Every earlier round shipped the SAME class: a stop-proof that fails
# OPEN, printing a wrong `0` that silently ends an autonomous /goal loop, with
# genuine command output attached. The guard rounds 1-3 built is correct; it
# was wired into ONE of the paths that need it.
# --------------------------------------------------------------------------- #


def _renamed_repo_gh(rest='[{"number": 4242}]', healthy=False,
                     workflow_state="active", newest_run="success",
                     workflows=None, _NORUN=object()):
    """A `_gh_out` stand-in reproducing the LIVE renamed-repo state.

    Measured 2026-07-30 against a checkout whose `origin` still points at the
    pre-rename `zbynekdrlik/odoo-slovnormal`: GitHub's issue SEARCH index does
    not follow a repo rename, the REST/repository-listing path does. REST 110,
    every `--search` 0, and `core-quals --count` printed `0` with rc 0.
    """
    import json as _json

    def gh(*a, **k):
        args = [str(x) for x in a]
        joined = " ".join(args)
        if args and args[0] == "label":
            return '[{"name": "stream:david"}]'
        if args and args[0] == "workflow":
            if workflows is not None:
                return _json.dumps(workflows)
            return _json.dumps([
                {"name": "Sub-dev Handoff Label", "state": workflow_state,
                 "path": ".github/workflows/subdev-handoff-label.yml"}])
        if args and args[0] == "run":
            if newest_run is None:
                return "[]"                     # the workflow has never run
            if newest_run == "in_progress":
                return _json.dumps([{"conclusion": None,
                                     "status": "in_progress"}])
            return _json.dumps([{"conclusion": newest_run,
                                 "status": "completed"}])
        if "sort:created-desc" in joined:
            return '[{"number": 999}]' if healthy else "[]"
        if "--search" in args:
            return "[]"
        return rest

    return gh


def _drive(cmd, gh, authority="full", user="newlevel", login="zbynekdrlik",
           **flags):
    """Run a real stop-proof command and capture stdout / stderr / exit."""
    import contextlib
    import io
    import unittest.mock as mk

    out, err, exc = io.StringIO(), io.StringIO(), None
    args = dict(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None)
    args.update(flags)
    with mk.patch.object(airuleset, "resolve_authority", return_value=authority):
        with mk.patch.object(airuleset, "_current_user", return_value=user):
            with mk.patch.object(airuleset, "_gh_login", return_value=login):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(out):
                        with contextlib.redirect_stderr(err):
                            try:
                                cmd(mk.Mock(**args))
                            except SystemExit as e:
                                exc = e
    return out.getvalue(), err.getvalue(), exc


class TestEveryStopProofRefusesAnUnansweringSearchIndex(TestCase):
    """#181 round 4, CRITICAL. `_search_index_healthy()` had exactly ONE call
    site: inside `cmd_slice_quals`, nested behind
    `len(quals) == 1 and quals[0].startswith("label:")` — the SHARED-account
    shape. So it was not a guard on "an empty result out of the search index",
    it was an extra validation for one caller's zero, and two live paths walked
    straight past it.

    Both reproduced on dev1, 2026-07-30, against the shipped code:

      gh issue list --state open -L 1000 --json number --jq length        110
      gh issue list --state open --search "sort:created-desc" -L 1000       0
      python3 airuleset.py core-quals --count                              0  (rc 0)

      _slice_quals("david") -> ['assignee:@me','author:@me','label:stream:david']
      cmd_slice_quals(count=True), every search [] -> stdout '0', no SystemExit

    The rename is only the cheapest trigger; any state where search answers
    empty while REST does not reaches the same line."""

    def _assert_refused(self, out, err, exc, why):
        self.assertIsNotNone(exc, why)
        self.assertNotEqual(exc.code, 0, why)
        self.assertEqual(out.strip(), "", "a refusal must print NOTHING to stdout")
        self.assertNotEqual(err.strip(), "", "a refusal must say why on stderr")

    def test_core_quals_count_refuses_when_search_is_dead_but_rest_is_not(self):
        out, err, exc = _drive(airuleset.cmd_core_quals, _renamed_repo_gh())
        self._assert_refused(
            out, err, exc,
            "core-quals printed a stop-proof `0` while the repository listing "
            "path shows open issues — the gatekeeper pastes that 0, writes "
            "BACKLOG EMPTY and stops with the whole backlog outstanding")

    def test_core_quals_list_refuses_too(self):
        """`--list` is the mandated backlog SELECTION source, so an
        unanswering index empties the worker's work queue just as silently as
        it zeroes the count."""
        out, err, exc = _drive(airuleset.cmd_core_quals, _renamed_repo_gh(),
                               count=False, list=True)
        self._assert_refused(out, err, exc,
                             "core-quals --list returned an empty backlog")

    def test_own_account_slice_quals_refuses_when_search_is_dead(self):
        """The hole is open in the command round 3 fixed, too: an own-account
        stream has THREE quals, so `len(quals) == 1` is False and the guard is
        skipped entirely."""
        out, err, exc = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                               authority="fork-no-merge", user="david1",
                               login="kvaskodev")
        self._assert_refused(
            out, err, exc,
            "slice-quals printed a false SLICE EMPTY for an own-account "
            "stream — the guard is nested behind the shared-account shape")

    def test_own_account_slice_quals_list_refuses_too(self):
        out, err, exc = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                               authority="fork-no-merge", user="david1",
                               login="kvaskodev", count=False, list=True)
        self._assert_refused(out, err, exc, "slice-quals --list went empty")

    def test_both_stop_proofs_refuse_with_the_IDENTICAL_contract(self):
        """The refusal contract must not be two parallel copies — that is the
        shape that let it drift for three rounds."""
        core = _drive(airuleset.cmd_core_quals, _renamed_repo_gh())
        slic = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                      authority="fork-no-merge", user="david1", login="kvaskodev")
        for out, err, exc in (core, slic):
            self.assertIsNotNone(exc)
            self.assertNotEqual(exc.code, 0)
            self.assertEqual(out.strip(), "")
            self.assertNotEqual(err.strip(), "")

    def test_both_commands_route_through_the_SAME_helper(self):
        """The assertion above is satisfied by two parallel copies, which is
        exactly the shape this round set out to remove — so it cannot be the
        only thing standing behind the word "identical" (adversarial review,
        round 4). This drives both real commands and records who asked."""
        import unittest.mock as mk

        callers = []

        def recorder(cmd, quals, cwd=None):
            callers.append(cmd)

        with mk.patch.object(cli_quals_cmd, "_refuse_unless_empty_is_trustworthy",
                             side_effect=recorder):
            _drive(airuleset.cmd_core_quals, _renamed_repo_gh(healthy=True))
            _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(healthy=True),
                   authority="fork-no-merge", user="david1", login="kvaskodev")
        self.assertEqual(
            sorted(callers), ["core-quals", "slice-quals"],
            "one of the two stop-proofs does not go through the shared "
            "refusal helper: %r" % (callers,))

    # ----- controls: a legitimate zero must still be reportable ------------ #

    def _drive_unenrolled(self, gh):
        """Pin the repo identity: these controls reach the hand-off gate, and
        leaving them on the REAL `notify.repo_name_for` made them pass only
        because this repo happens not to be enrolled in the cross-stream flow
        — they would flip the day it was (adversarial review, round 4). A
        control whose result depends on where it is run is not a control."""
        import unittest.mock as mk
        with mk.patch("notify.repo_name_for", return_value="not-enrolled"):
            return _drive(airuleset.cmd_core_quals, gh)

    def test_a_healthy_index_still_lets_core_quals_report_a_real_zero(self):
        out, _, exc = self._drive_unenrolled(_renamed_repo_gh(healthy=True))
        self.assertIsNone(exc, "a validated zero must still be reportable")
        self.assertEqual(out.strip(), "0")

    def test_a_repo_with_no_open_issues_at_all_still_reports_zero(self):
        out, _, exc = self._drive_unenrolled(_renamed_repo_gh(rest="[]"))
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")


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


def _labelled_rows_gh(healthy=True):
    """A `_gh_out` stand-in whose issue rows carry real `labels` values."""
    import json as _json

    populations = {
        "-label:stream:": [
            {"number": 11, "title": "core work", "labels": [],
             "createdAt": "2026-07-01T00:00:00Z"}],
        "label:needs-gatekeeper": [],
        "label:prio:bounce": [
            {"number": 2150, "title": "bounced stream ticket",
             "createdAt": "2026-06-01T00:00:00Z",
             "labels": [{"name": "prio:bounce"}, {"name": "stream:david"}]}],
        "label:ready-for-review": [],
    }
    seen = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        joined = " ".join(args)
        seen.append(args)
        if args and args[0] == "label":
            return '[{"name": "stream:montalu"}]'
        if args and args[0] == "workflow":
            return "[]"
        if args and args[0] == "run":
            return "[]"
        if "sort:created-desc" in joined:
            return '[{"number": 999}]' if healthy else "[]"
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        rows = []
        for key, val in populations.items():
            if key in search:
                rows.extend(val)
        return _json.dumps(rows)

    return gh, seen


class TestTheSelectionSourceCarriesTheOwnershipDiscriminator(TestCase):
    """#181 round 4, HIGH. `core-quals --list` is the mandated backlog
    SELECTION source and emitted no not-mine-to-implement discriminator:
    `_union_open_issues` asked for `number,title,createdAt` — labels were never
    fetched at all — and `_print_issue_rows` printed number/createdAt/title.

    On odoo-erp the obligation set is 55 rows, and the FULL template's own
    bounce-lane instruction seeds every new batch from the OLDEST open
    `prio:bounce` ticket via `core-quals --list --extra "label:prio:bounce"` —
    which is #2150, `stream:david`. Nothing but a prose clause stood between
    that instruction and the gatekeeper writing code on a sub-dev's ticket,
    and prose in exactly this position is what this ticket has been about for
    four rounds.

    #307 (2026-08-07): `prio:bounce` is no longer part of the PLAIN obligation
    set (a bare bounce ticket is the sub-dev's own work) — #2150 now surfaces
    ONLY through the `--extra "label:prio:bounce"` bounce-lane seed path,
    exactly how the FULL template actually invokes it, still marked
    `action-only` there."""

    def test_the_union_queries_actually_fetch_the_labels(self):
        gh, seen = _labelled_rows_gh()
        _drive(airuleset.cmd_core_quals, gh, count=False, list=True)
        json_fields = [a[a.index("--json") + 1] for a in seen
                       if "--json" in a and a and a[0] == "issue"]
        self.assertTrue(json_fields)
        self.assertTrue(
            any("labels" in f for f in json_fields),
            "labels are never fetched, so no row can carry a discriminator: %r"
            % (json_fields,))

    def test_a_bounce_stream_row_is_marked_action_only_via_the_seed(self):
        # The REAL invocation (SKILL.md Step 3.1) always passes `--extra
        # "label:prio:bounce"` for the seed — never a bare `--list`.
        gh, _ = _labelled_rows_gh()
        out, _, exc = _drive(airuleset.cmd_core_quals, gh, count=False,
                             list=True, extra="label:prio:bounce")
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("2150\t")]
        self.assertTrue(row, out)
        self.assertIn(
            "action-only", row[0],
            "the oldest open prio:bounce ticket is stream:david's — the seed "
            "instruction points straight at it and the row says nothing")

    def test_a_bare_bounce_ticket_is_absent_from_the_plain_obligation_list(self):
        """#307: WITHOUT `--extra`, #2150 (bare prio:bounce, no
        needs-gatekeeper/ready-for-review) must NOT appear at all — it is the
        sub-dev's own work, not this box's obligation."""
        gh, _ = _labelled_rows_gh()
        out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                             count=False, list=True)
        self.assertIsNone(exc)
        self.assertNotIn("2150\t", out)

    def test_a_core_row_is_marked_implement(self):
        gh, _ = _labelled_rows_gh()
        out, _, _ = _drive(airuleset.cmd_core_quals, gh, count=False, list=True)
        row = [ln for ln in out.splitlines() if ln.startswith("11\t")]
        self.assertTrue(row, out)
        self.assertIn("implement", row[0])

    def test_a_row_whose_ownership_cannot_be_read_defaults_to_action_only(self):
        """M11's lesson, applied to the new column. `labels` ABSENT from a row
        is undeterminable, not "unlabelled" — and the two failure directions
        are not symmetric: a sub-dev's ticket printed `implement` invites the
        gatekeeper to write code on a foreign stream's ticket (a silent
        authority violation, the exact harm this column exists to prevent),
        while a core ticket printed `action-only` merely stalls visibly. So an
        unreadable row takes the conservative side. A row with `labels: []` is
        a genuinely unlabelled core ticket and must still read `implement`."""
        import json as _json

        def gh(*a, **k):
            args = [str(x) for x in a]
            if "sort:created-desc" in " ".join(args):
                return '[{"number": 999}]'
            if "--search" in args:
                return _json.dumps([
                    {"number": 31, "title": "ownership unreadable",
                     "createdAt": "2026-07-01T00:00:00Z"}])
            return "[]"

        out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                             count=False, list=True)
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("31\t")]
        self.assertTrue(row, out)
        self.assertIn(
            "action-only", row[0],
            "a row whose labels could not be read was advertised as ordinary "
            "work — that is the dangerous direction")

    def test_labels_present_but_malformed_is_also_undeterminable(self):
        """`"labels" in row` is not enough: a `labels` value that is not a list
        of dicts (bare strings, an explicit null) is unreadable ownership, not
        an absence of ownership, and printing `implement` for it is the
        dangerous direction (adversarial review, round 4)."""
        import json as _json

        for value in (["stream:david"], None, "stream:david", [None]):
            with self.subTest(labels=value):
                def gh(*a, _v=value, **k):
                    args = [str(x) for x in a]
                    if "sort:created-desc" in " ".join(args):
                        return '[{"number": 999}]'
                    if "--search" in args:
                        return _json.dumps([
                            {"number": 41, "title": "malformed ownership",
                             "createdAt": "2026-07-01T00:00:00Z",
                             "labels": _v}])
                    return "[]"

                out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                                     count=False, list=True)
                self.assertIsNone(exc)
                row = [ln for ln in out.splitlines() if ln.startswith("41\t")]
                self.assertTrue(row, out)
                self.assertIn("action-only", row[0])

    def test_my_OWN_streams_ticket_is_mine_to_implement(self):
        """A reduced-authority box's own `stream:<me>` tickets must not come
        back marked action-only — the discriminator is relative to THIS box."""
        import json as _json

        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "--search" in args:
                return _json.dumps([
                    {"number": 7, "title": "mine",
                     "createdAt": "2026-07-01T00:00:00Z",
                     "labels": [{"name": "stream:montalu"}]}])
            return "[]"

        out, _, exc = _drive(airuleset.cmd_slice_quals, gh,
                             authority="branch-merge", user="montalu1",
                             login="zbynekdrlik", count=False, list=True)
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("7\t")]
        self.assertTrue(row, out)
        self.assertIn("implement", row[0])
        self.assertNotIn("action-only", row[0])

    def test_core_quals_can_scope_the_bounce_seed_like_slice_quals_does(self):
        """The full-authority bounce seed went through a raw `gh issue list`,
        so the single highest-priority selection path had neither the guard nor
        the discriminator."""
        gh, seen = _labelled_rows_gh()
        _drive(airuleset.cmd_core_quals, gh, count=False, list=True, waiting=False,
               extra="label:prio:bounce")
        searches = [a[a.index("--search") + 1] for a in seen
                    if "--search" in a and a and a[0] == "issue"]
        searches = [s for s in searches if "sort:created-desc" not in s]
        self.assertTrue(searches)
        for s in searches:
            self.assertIn("label:prio:bounce", s)


class TestRunCardResolvesIdentityAgainstTheRepoRoot(TestCase):
    """#181 I-5 residual (item 4): `_notify_run_card` was the fourth call site
    and the only one still resolving identity against the PROCESS cwd — both
    `resolve_authority()` and `_slice_quals()` were called with no cwd, so the
    run-card and the footer could resolve the same box differently whenever a
    session ran from a subdirectory. "One definition, resolved per box" is not
    true until all four agree."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                    repo_name=False, newest_card=False, backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False,
                    channel_id=False, owner=False, mirror_owners=False, question_ping_off=False,
                    body=None, run=None, repo="kvaskodev/odoo-erp", issue=1408,
                    pr=None, achieved="Fix nasadený a overený", result=None, goal="cieľ",
                    version=None, merge_sha=None, url=None, review="ok",
                    handoff=True, dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def _capture(self):
        import unittest.mock as mk

        seen = {}

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "--search" in [str(x) for x in a]:
                return "[]"
            return "26"

        def auth(cwd=None):
            seen["authority_cwd"] = cwd
            return "fork-no-merge"

        def quals(user, cwd=None):
            seen["slice_cwd"] = cwd
            return ["label:stream:david"]

        with mk.patch.object(airuleset, "_repo_root", return_value="/repo/root"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch.object(airuleset, "resolve_authority",
                                     side_effect=auth):
                    with mk.patch.object(airuleset, "_slice_quals",
                                         side_effect=quals):
                        with mk.patch("notify.send", return_value="sent"):
                            airuleset.cmd_notify(self._args())
        return seen

    def test_the_card_resolves_the_slice_against_the_repo_root(self):
        seen = self._capture()
        self.assertEqual(
            seen.get("slice_cwd"), "/repo/root",
            "the run-card still resolves 'my slice' against the process cwd "
            "while the footer resolves it against the repo root")

    def test_the_card_resolves_authority_against_the_repo_root_too(self):
        seen = self._capture()
        self.assertEqual(
            seen.get("authority_cwd"), "/repo/root",
            "a project CLAUDE.md authority marker is invisible to the card "
            "whenever the session cwd is a subdirectory")


class TestHeartbeatOnAReviewOnlyRun(TestCase):
    """#164 M-1 residual (item 5). The GUARD is right; its stated reason was
    false. The docstring claimed the run a heartbeat exists to keep alive
    "always has an in-window file already" — which does not hold for a
    gatekeeper run whose cards are ALL sub-dev stream tickets, a shape the
    obligation-set change makes more common.

    Opening the window there is NOT the fix and this round does not do it:
    `done` and `remaining` are both core-scoped by deliberate design (#164), so
    a review-only run would open at `0/N` and hold it for a full 6h window —
    M-1 verbatim. Making D/T meaningful for such a run means scoping
    `remaining` to the OBLIGATION set while the idle render stays core-scoped,
    i.e. one label over two populations depending on run state, which is #164's
    own title. So the behaviour stays and the false sentence goes."""

    def setUp(self):
        import unittest.mock as mk
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        p = mk.patch("statusbar.progress_dir", return_value=self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_a_whole_review_only_run_still_opens_no_window(self):
        for _ in range(5):
            airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertFalse(
            (self.dir / "demo.json").exists(),
            "a review-only run opened a progress window at 0/40 — the footer "
            "would assert an active run that has achieved nothing")

    def test_the_first_core_card_of_that_run_opens_it_immediately(self):
        for _ in range(5):
            airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        import json as _json
        self.assertEqual(
            _json.loads((self.dir / "demo.json").read_text())["done"], 1)

    def test_the_docstring_no_longer_claims_the_file_is_always_there(self):
        # Whitespace-normalised: the docstring is hard-wrapped, so a raw
        # assertNotIn would pass vacuously on the newline and lock nothing.
        doc = " ".join((airuleset._write_autopilot_progress.__doc__ or "").split())
        self.assertNotIn(
            "always has an in-window file already", doc,
            "the single source of truth for this invariant states a reason "
            "that is false for a review-only gatekeeper run")

    def test_the_docstring_states_the_real_reason_and_the_consequence(self):
        doc = " ".join((airuleset._write_autopilot_progress.__doc__ or "").split())
        self.assertIn("review-only", doc)
        self.assertIn("no evidence of CORE progress", doc)


def _fake_gh_search_filtered(items):
    """A `_gh_out` stand-in that evaluates a `gh issue list --search "..."`
    query against `items` (a list of `{"number", "labels": set(...)}`) with
    REAL exclusion semantics -- `-label:X` genuinely removes an item
    carrying X, `label:A,B` matches an item carrying A OR B (gh's own
    documented comma-OR-within-one-qualifier behaviour, #181 M8/M-6) --
    unlike `_fake_gh_by_search`'s pure substring-INCLUSION model above,
    which cannot prove that an EXCLUSION term added to the built query
    actually removes a ticket (#362): that fake would report a ticket as
    still present just because SOME population key's substring occurs in
    the search string, with no regard for whether a `-label:` term should
    have removed it. `assignee:`/`author:`/`sort:` tokens are accepted but
    ignored -- this fixture is only ever driven with label-only quals."""
    searches = []

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        searches.append(search)
        want_length = "-q" in args
        matched = []
        for it in items:
            labels = it.get("labels") or set()
            ok = True
            for tok in search.split():
                if tok.startswith("-label:"):
                    names = tok[len("-label:"):].split(",")
                    if any(n in labels for n in names):
                        ok = False
                        break
                elif tok.startswith("label:"):
                    names = tok[len("label:"):].split(",")
                    if not any(n in labels for n in names):
                        ok = False
                        break
            if ok:
                matched.append(it)
        if want_length:
            return str(len(matched))
        import json as _json
        return _json.dumps([
            {"number": it["number"],
             "title": it.get("title", "t%d" % it["number"]),
             "createdAt": it.get("createdAt", "2026-01-01T00:00:00Z"),
             "labels": [{"name": n} for n in sorted(it.get("labels") or [])]}
            for it in matched])

    return gh, searches


class TestQualsExcludePermanentOpsChannelTickets(TestCase):
    """#362: a stream self-declares a ticket PERMANENT via the `ops-channel`
    label (odoo-erp #1861 -- "[TRVALY OPS KANAL -- NEZATVARAT] erp-test-*
    teardown/recreate/refresh", and #3037 -- a snapshot-retention alert log)
    so it deliberately never auto-closes. Before this fix `core-quals`/
    `slice-quals` treated it as ordinary workable backlog -- the /goal
    stop-proof's `--count 0` was UNREACHABLE while it stayed open, and the
    /autopilot loop dispatched a full worker onto it that found "nothing to
    do -- permanent log ticket" (~220k wasted subagent tokens)."""

    def test_core_quals_count_excludes_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        items = [
            {"number": 1, "labels": set()},              # ordinary core work
            {"number": 2, "labels": {"ops-channel"}},     # permanent channel
        ]
        gh, searches = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority", return_value="full"):
            with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        m.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a permanent ops-channel ticket is still counted as workable "
            "backlog -- searches issued: %r" % searches)

    def test_core_quals_list_omits_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        items = [
            {"number": 1, "labels": set()},
            {"number": 2, "labels": {"ops-channel"}},
        ]
        gh, _ = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority", return_value="full"):
            with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        m.Mock(count=False, list=True, waiting=False, ops_wait=False, audit=False, extra=None))
        out = buf.getvalue()
        self.assertIn("1\t", out)
        self.assertNotIn("2\t", out)

    def test_slice_quals_count_excludes_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        # The SHARED-account, single-label slice shape (montalu/marek/simap):
        # _slice_quals() mocked directly so this test is about cmd_slice_quals'
        # own query-building, not about resolving gh identity.
        items = [
            {"number": 1, "labels": {"stream:montalu"}},
            {"number": 2, "labels": {"stream:montalu", "ops-channel"}},
        ]
        gh, searches = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="branch-merge"):
            with m.patch.object(airuleset, "_slice_quals",
                                return_value=["label:stream:montalu"]):
                with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            m.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a permanent ops-channel ticket is still in this stream's own "
            "slice -- searches issued: %r" % searches)
