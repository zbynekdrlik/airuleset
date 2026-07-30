"""Locks the autopilot authority profiles (issue #16, 2026-07-09).

Incident: David's fork stream (no merge rights) invoked /autopilot and got the
hardcoded full-authority /goal condition ("merged PR to main + main green") —
unsatisfiable, so the run correctly refused to arm. Sub-dev streams must run
/autopilot AS-IS: the authority profile is a property of the USER (streams are
separate linux users), resolved at runtime from AUTHORITY_BY_USER — no per-box
state to lose on a home-dir migration.
"""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestAuthorityResolution(TestCase):
    def test_known_stream_users_map_to_their_profiles(self):
        self.assertEqual(airuleset.AUTHORITY_BY_USER["david"], "fork-no-merge")
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "branch-merge")
        self.assertEqual(airuleset.AUTHORITY_BY_USER["montalu"], "branch-merge")
        # simap (airuleset#143): phase-1 demo stream that merges NOWHERE —
        # fork-no-merge is the existing lowest profile, already correct.
        self.assertEqual(airuleset.AUTHORITY_BY_USER["simap"], "fork-no-merge")

    def test_resolve_uses_the_map_for_simap(self):
        with m.patch.object(airuleset, "_current_user", return_value="simap"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_resolve_defaults_to_full_for_unknown_user(self):
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(), "full")

    def test_resolve_uses_the_map_for_stream_users(self):
        with m.patch.object(airuleset, "_current_user", return_value="david"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_cli_prints_the_profile(self):
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            with m.patch("builtins.print") as p:
                airuleset.cmd_authority(m.Mock(explain=False))
        p.assert_any_call("branch-merge")

    def test_project_marker_overrides_the_user_map(self):
        # cmd_authority's explain text has always PROMISED the marker override; it must
        # actually be honored now (single source of truth for the CLI + the close-guard
        # hook). A project can RAISE david's default (fork-no-merge) to full…
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=full -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="david"):
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
        with m.patch.object(airuleset, "_current_user", return_value="david"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_bogus_marker_value_ignored(self):
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=superuser -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="david"):
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
        with m.patch.object(airuleset, "_current_user", return_value="david"):
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
        args = mk.Mock(run_card=True, autopilot_done=False, mention_prefix=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False,
                       record_question=False, edit_question=False, channel_id=False,
                       owner=False, mirror_owners=False, body=None, run=None,
                       repo="kvaskodev/odoo-erp", issue=1408, pr=None,
                       achieved="hotové", result=None, goal="cieľ", version=None,
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


if __name__ == "__main__":
    main()


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
        for user in ("david", "marek", "montalu"):
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
                      airuleset.skill_names_for_user("montalu"))
        for user in ("david", "marek", "gatekeeper"):
            self.assertNotIn("meeting-analysis",
                             airuleset.skill_names_for_user(user), user)
        # extras never leak anything beyond the named skill
        self.assertNotIn("mdreview", airuleset.skill_names_for_user("montalu"))
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
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, body=None, run=None,
                    repo="kvaskodev/odoo-erp", issue=1408, pr=None,
                    achieved="hotové", result=None, goal="cieľ", version=None,
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
            with mk.patch.object(airuleset, "_current_user", return_value="montalu"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, extra=None))
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
                            mk.Mock(count=True, list=False, extra=None))
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
            with mk.patch.object(airuleset, "_current_user", return_value="david"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "2")   # {1, 2} unioned, not 3

    def test_a_failed_gh_query_never_prints_zero(self):
        # The false-stop's exact shape: a wrong/failed query must NEVER
        # collapse to a printed 0 — that IS the bug this exists to prevent.
        import contextlib
        import io
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu"):
                with mk.patch.object(airuleset, "_gh_out", return_value="not json"):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, extra=None))
                    self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())


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
                                    mk.Mock(count=True, list=False, extra=None))
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
            with mk.patch.object(airuleset, "_current_user", return_value="montalu"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, extra=None))
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
            with mk.patch.object(airuleset, "_current_user", return_value="montalu"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, extra=None))
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
            with mk.patch.object(airuleset, "_current_user", return_value="montalu"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_a_handed_off_ticket_still_counts_until_actually_closed(self):
        # I3 investigated and found NOT a bug (round 2): the footer's
        # len(mine)-gk is a DISPLAY partition; slice-quals's raw count must
        # keep counting a handed-off-but-still-OPEN ticket, or the /goal
        # proof could read 0 while a ticket the gatekeeper has NOT yet
        # closed sits open — silently defeating review-watch.
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
            with mk.patch.object(airuleset, "_current_user", return_value="david"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")


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
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False,
                    repo_name=False, newest_card=False,
                    backfill_digest=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, body=None, run=None,
                    repo="o/r", issue=5, pr=None,
                    achieved="hotové", result=None, goal="cieľ", version=None,
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
    `stream:montalu` and are therefore INVISIBLE to a core-only count — plus
    11 open `prio:bounce`. The gatekeeper closes its 40, the proof prints 0,
    the loop stops, and 13 tickets stay blocked on the very box that stopped.
    That is #181 verbatim at a new address."""

    POPULATIONS = {
        "-label:stream:": {1, 2},                 # the core partition
        "label:needs-gatekeeper": {2396, 2377},   # stream-labelled, only I can act
        "label:prio:bounce": {5},                 # my ball per cross-stream rule 4
        "label:ready-for-review": set(),          # a hand-off awaiting my review
    }

    def _run(self, populations=None, **flags):
        import contextlib
        import io
        import unittest.mock as mk

        gh, searches = _fake_gh_by_search(
            self.POPULATIONS if populations is None else populations)
        buf = io.StringIO()
        args = dict(count=True, list=False, extra=None)
        args.update(flags)
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(mk.Mock(**args))
        return buf.getvalue(), searches

    def test_a_stream_ticket_only_this_box_can_action_is_counted(self):
        out, _ = self._run()
        # {1, 2} ∪ {2396, 2377} ∪ {5} — five, not the core partition's two.
        self.assertEqual(out.strip(), "5")

    def test_the_listing_names_the_tickets_only_this_box_can_unblock(self):
        out, _ = self._run(count=False, list=True)
        self.assertIn("2396", out)
        self.assertIn("2377", out)
        self.assertIn("5\t", out)

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
        bare = [s for s in searches
                if s.strip() == "-label:autopilot-skip"]
        self.assertEqual(
            bare, [],
            "a bare whole-repo query is back — that is the never-stops "
            "failure #181 rejected, not the obligation set")

    def test_every_maintainer_action_label_is_actually_queried(self):
        _, searches = self._run()
        joined = " | ".join(searches)
        for label in ("needs-gatekeeper", "prio:bounce", "ready-for-review"):
            self.assertIn("label:" + label, joined)

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
                            mk.Mock(count=True, list=False, extra=None))
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
                            mk.Mock(count=True, list=False, extra=None))
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
        whose loop would otherwise stop."""
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", return_value="[]"):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        mk.Mock(count=False, list=False, extra=None))
        printed = buf.getvalue()
        self.assertIn(airuleset._core_search_excl(), printed)
        for label in ("needs-gatekeeper", "prio:bounce", "ready-for-review"):
            self.assertIn("label:" + label, printed)


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
                                     return_value="montalu"):
                    with mk.patch.object(
                            airuleset, "_gh_out",
                            return_value='[{"number": 1, "title": "t", '
                                         '"createdAt": "2026-07-01T00:00:00Z"}]'):
                        with contextlib.redirect_stdout(buf):
                            with self.assertRaises(SystemExit) as cm:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_resolved_login_still_picks_the_right_branch(self):
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            self.assertEqual(airuleset._slice_quals("montalu"),
                             ["label:stream:montalu"])
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            self.assertEqual(len(airuleset._slice_quals("david")), 3)


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
                                     return_value="montalu"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(buf):
                            try:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, extra=None))
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
                                     return_value="david"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(io.StringIO()):
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, extra=None))
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
                     workflows=None):
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
    args = dict(count=True, list=False, extra=None)
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
                               authority="fork-no-merge", user="david",
                               login="kvaskodev")
        self._assert_refused(
            out, err, exc,
            "slice-quals printed a false SLICE EMPTY for an own-account "
            "stream — the guard is nested behind the shared-account shape")

    def test_own_account_slice_quals_list_refuses_too(self):
        out, err, exc = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                               authority="fork-no-merge", user="david",
                               login="kvaskodev", count=False, list=True)
        self._assert_refused(out, err, exc, "slice-quals --list went empty")

    def test_both_stop_proofs_refuse_with_the_IDENTICAL_contract(self):
        """The refusal contract must not be two parallel copies — that is the
        shape that let it drift for three rounds."""
        core = _drive(airuleset.cmd_core_quals, _renamed_repo_gh())
        slic = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                      authority="fork-no-merge", user="david", login="kvaskodev")
        for out, err, exc in (core, slic):
            self.assertIsNotNone(exc)
            self.assertNotEqual(exc.code, 0)
            self.assertEqual(out.strip(), "")
            self.assertNotEqual(err.strip(), "")

    # ----- controls: a legitimate zero must still be reportable ------------ #

    def test_a_healthy_index_still_lets_core_quals_report_a_real_zero(self):
        out, _, exc = _drive(airuleset.cmd_core_quals,
                             _renamed_repo_gh(healthy=True))
        self.assertIsNone(exc, "a validated zero must still be reportable")
        self.assertEqual(out.strip(), "0")

    def test_a_repo_with_no_open_issues_at_all_still_reports_zero(self):
        out, _, exc = _drive(airuleset.cmd_core_quals,
                             _renamed_repo_gh(rest="[]"))
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

    def _drive_enrolled(self, gh, repo="odoo-erp"):
        import unittest.mock as mk
        with mk.patch("notify.repo_name_for", return_value=repo):
            return _drive(airuleset.cmd_core_quals, gh)

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

    def test_a_healthy_labeller_still_lets_the_zero_through(self):
        out, _, exc = self._drive_enrolled(_renamed_repo_gh(healthy=True))
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")

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
    `prio:bounce` ticket — which is #2150, `stream:david`. Nothing but a prose
    clause stood between that instruction and the gatekeeper writing code on a
    sub-dev's ticket, and prose in exactly this position is what this ticket
    has been about for four rounds."""

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

    def test_a_stream_owned_row_is_marked_action_only(self):
        gh, _ = _labelled_rows_gh()
        out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                             count=False, list=True)
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("2150\t")]
        self.assertTrue(row, out)
        self.assertIn(
            "action-only", row[0],
            "the oldest open prio:bounce ticket is stream:david's — the seed "
            "instruction points straight at it and the row says nothing")

    def test_a_core_row_is_marked_implement(self):
        gh, _ = _labelled_rows_gh()
        out, _, _ = _drive(airuleset.cmd_core_quals, gh, count=False, list=True)
        row = [ln for ln in out.splitlines() if ln.startswith("11\t")]
        self.assertTrue(row, out)
        self.assertIn("implement", row[0])

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
                             authority="branch-merge", user="montalu",
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
        _drive(airuleset.cmd_core_quals, gh, count=False, list=True,
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
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False,
                    repo_name=False, newest_card=False, backfill_digest=False,
                    record_question=False, edit_question=False,
                    channel_id=False, owner=False, mirror_owners=False,
                    body=None, run=None, repo="kvaskodev/odoo-erp", issue=1408,
                    pr=None, achieved="hotové", result=None, goal="cieľ",
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
