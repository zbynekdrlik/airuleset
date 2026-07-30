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

    def test_slice_quals_key_matches_the_footers_key_for_a_shared_account_box(self):
        # The single-source-of-truth guarantee: slice-quals must resolve the
        # SAME quals _slice_quals() itself returns for THIS box, not a
        # parallel re-implementation that could drift.
        import unittest.mock as mk
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            self.assertEqual(airuleset._slice_quals("montalu"),
                             ["label:stream:montalu"])

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
