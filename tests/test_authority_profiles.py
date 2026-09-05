"""authority_profiles tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from unittest import TestCase, main
from unittest import mock as m
from authority_testlib import (  # noqa: E402
    airuleset,
)


class TestAuthorityResolution(TestCase):
    def test_known_stream_users_map_to_their_profiles(self):
        # david1 (was david, airuleset#23; #537 live rename 2026-08-21): same
        # fork-no-merge profile as the base. The OLD unix name's row left the
        # map with the OS account (runbook-537 step 8, live in-place usermod).
        self.assertEqual(airuleset.AUTHORITY_BY_USER["david1"], "fork-no-merge")
        self.assertNotIn("david", airuleset.AUTHORITY_BY_USER)
        # marek — DEV STREAM cancelled (#882) but webterm OBSERVER lane
        # survives; fork-no-merge (least-privilege, dominika model #867).
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "fork-no-merge")
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

    def test_resolve_unmapped_user_fails_safe_to_fork_no_merge(self):
        # airuleset#827: an UNMAPPED unix user (a stream account provisioned but
        # forgotten in AUTHORITY_BY_USER, e.g. a future miva2/montalu9) must fail
        # SAFE to the MOST restrictive profile, NEVER fail-OPEN to `full` (which
        # would silently grant merge/deploy/close authority). The pre-#827 code
        # returned "full" here — the security fail-open this ticket fixes.
        import tempfile
        d = tempfile.mkdtemp()  # marker-free cwd: the registries alone decide
        with m.patch.object(airuleset, "_current_user",
                            return_value="miva2-forgotten-stream"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_full_authority_accounts_resolve_full(self):
        # airuleset#827: the fail-safe flip must NOT regress the legitimate
        # full-authority accounts. These four are documented/intended full and
        # deliberately kept OUT of the reduced-stream AUTHORITY_BY_USER table
        # (newlevel = dev1/dev2 + spinbike-vps; gatekeeper = gk box; admin +
        # stepan = forestshop-dev, cli_fleet.py "the owner's own trusted box").
        # They are enumerated in FULL_AUTHORITY_USERS and MUST still resolve full.
        import tempfile
        d = tempfile.mkdtemp()  # marker-free cwd: the registries alone decide
        for u in ("newlevel", "gatekeeper", "admin", "stepan"):
            with m.patch.object(airuleset, "_current_user", return_value=u):
                self.assertEqual(airuleset.resolve_authority(cwd=d), "full", u)

    def test_full_authority_users_are_the_intended_full_accounts(self):
        # airuleset#827: the explicit full-authority allow-list — a hand-maintained
        # frozenset, NOT derived from REMOTE_HOSTS (a derived set would re-open the
        # fail-open bug: a reduced stream forgotten in AUTHORITY_BY_USER would
        # auto-classify full). Mirrors the SSH_ATTACH_EXTRA_USERS idiom (#562/#563)
        # for accounts that must NOT enter the reduced-stream AUTHORITY_BY_USER.
        self.assertEqual(
            set(airuleset.FULL_AUTHORITY_USERS),
            {"newlevel", "gatekeeper", "admin", "stepan"})

    def test_full_authority_users_disjoint_from_stream_table(self):
        # airuleset#827: the two registries must be DISJOINT — a full account is
        # never a reduced stream. Defense-in-depth: _authority_decision checks
        # AUTHORITY_BY_USER FIRST, so even a dual-membership bug resolves to the
        # RESTRICTIVE profile (restrictive wins), but a dual membership would also
        # corrupt the "AUTHORITY_BY_USER membership == is-a-stream" semantics that
        # _own_handoff_label / _ticket_is_stream_labeled rely on.
        self.assertEqual(
            set(airuleset.FULL_AUTHORITY_USERS) & set(airuleset.AUTHORITY_BY_USER),
            set())
        # airuleset#827 (review): AUTHORITY_BY_USER is the REDUCED-stream registry —
        # it must NEVER carry a `full` value. A `full`-valued row would resolve full
        # in _authority_decision ("per-user map" wins) yet be stripped of full-only
        # skills by skill_names_for_user's FULL_AUTHORITY_USERS membership gate — a
        # restrictive-direction inconsistency. A full account belongs in
        # FULL_AUTHORITY_USERS, never here.
        self.assertNotIn("full", set(airuleset.AUTHORITY_BY_USER.values()))

    def test_every_remote_hosts_user_is_classified(self):
        # airuleset#827: the fail-safe flip strands any REMOTE_HOSTS box whose unix
        # user is in NEITHER registry (it would silently degrade to fork-no-merge).
        # Every provisioned box MUST be explicitly classified — reduced stream or
        # full account — so a FUTURE unclassified REMOTE_HOSTS user is a RED test
        # here, forcing an explicit decision instead of a silent grant (approach
        # 2's anti-fail-open enforcement, folded in). dev1 (local `newlevel`)
        # is covered via FULL_AUTHORITY_USERS.
        # Every deploy target must carry a `user` — a user-less entry would be
        # silently exempted from the classification check below.
        self.assertTrue(all(h.get("user") for h in airuleset.REMOTE_HOSTS),
                        "a REMOTE_HOSTS entry carries no `user`")
        classified = (set(airuleset.AUTHORITY_BY_USER)
                      | set(airuleset.FULL_AUTHORITY_USERS))
        unclassified = sorted({h["user"] for h in airuleset.REMOTE_HOSTS
                               if h["user"] not in classified})
        self.assertEqual(
            unclassified, [],
            "REMOTE_HOSTS users in neither AUTHORITY_BY_USER nor "
            "FULL_AUTHORITY_USERS (would fail-safe to fork-no-merge): %r"
            % unclassified)

    def test_resolve_uses_the_map_for_stream_users(self):
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_cli_prints_the_profile(self):
        # #349/#463/#533: `m.Mock` auto-creates any unspecified attribute as a
        # truthy Mock, so the `--maintainer-login` / `--self-login` /
        # `--stream-label` early-return branches would silently hijack this test
        # unless pinned False (the established `m.Mock(...)`-args gotcha this
        # repo's own dev rules already document for exactly this shape).
        # marek removed #882; use montalu1 (branch-merge) instead
        with m.patch.object(airuleset, "_current_user", return_value="montalu1"):
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
        # #828: a VALID marker that LOWERS the table wins AND names the
        # `marker-lowered` source (miva1's branch-merge table capped to
        # fork-no-merge by the marker) — NOT the old raise-capable
        # "project CLAUDE.md marker" source.
        out = _explain("miva1", "fork-no-merge")
        self.assertIn("resolved=fork-no-merge via marker-lowered", out)
        self.assertIn("marker=fork-no-merge", out)
        # an INVALID marker → ignored for resolution (table stands) but surfaced
        # as invalid, the exact 'typo'd marker' class the log exists to diagnose.
        out = _explain("miva1", "branch_merge")
        self.assertIn("resolved=branch-merge via per-user map", out)
        self.assertIn("marker=invalid('branch_merge')", out)
        # an UNMAPPED user → the fail-SAFE fork-no-merge default decided (airuleset
        # #827: no longer the fail-OPEN `full`), NOT a map row, and the log names
        # the source. The map= annotation is self-documenting (carries the remedy).
        out = _explain("nobody-here", None)
        self.assertIn("resolved=fork-no-merge via default (unmapped)", out)
        self.assertIn("unmapped -> fork-no-merge", out)
        # a FULL-AUTHORITY account (the explicit allow-list, airuleset#827) → full
        # via the NAMED 'full-authority account' source, never the unmapped default
        # (which now resolves fork-no-merge). Proves the FULL_AUTHORITY_USERS branch.
        out = _explain("newlevel", None)
        self.assertIn("resolved=full via full-authority account", out)

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
        # #828 (owner decision A — REWRITTEN from the old raise-via-marker
        # semantics): the marker is a CAP, never an override. It may only LOWER
        # authority relative to the per-user table, NEVER raise it. A
        # `fork-no-merge` stream (david1) with an `authority=full` marker in its
        # OWN (stream-editable) CLAUDE.md STAYS `fork-no-merge` — the raise is
        # IGNORED, closing the self-elevation vector. `full` is granted ONLY via
        # the per-user map / full allow-list / ci-runner recognition.
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("<!-- airuleset:authority=full -->\n")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

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


class TestBoxAuthorityFailSafe(TestCase):
    """airuleset#827 — `watchdog._box_authority()` is a PARALLEL authority path
    (box-owner-recipient gate for job 24's delivery-stall watch). It deliberately
    does NOT call `resolve_authority()` (a stray CLAUDE.md marker in the watchdog's
    cwd must never re-open the cross-stream leak) but duplicated the resolver's own
    fail-OPEN `AUTHORITY_BY_USER.get(user, "full")` default — so the #827 flip must
    close it here too, or the security boundary is only half-fixed. These lock the
    marker-free half of `_authority_decision`: reduced-stream map row -> the profile;
    explicit FULL_AUTHORITY_USERS -> full; anything else -> the fail-SAFE
    fork-no-merge (pre-#827 this returned the fail-OPEN full)."""

    def test_box_authority_unmapped_user_fails_safe(self):
        import watchdog as wd
        # RED before the fix: the old `.get(user, "full")` returned "full" for a
        # forgotten/unprovisioned stream account, silently granting the box-owner gate.
        with m.patch.object(airuleset, "_current_user",
                            return_value="miva99-forgotten-stream"):
            self.assertEqual(wd._box_authority(), "fork-no-merge")

    def test_box_authority_full_account_resolves_full(self):
        import watchdog as wd
        # The real full boxes (gk = gatekeeper, dev1/dev2 = newlevel) are in
        # FULL_AUTHORITY_USERS and MUST still resolve full (zero regression).
        for u in ("gatekeeper", "newlevel"):
            with m.patch.object(airuleset, "_current_user", return_value=u):
                self.assertEqual(wd._box_authority(), "full", u)

    def test_box_authority_reduced_stream_uses_the_map(self):
        import watchdog as wd
        # marek removed #882; use montalu1 (branch-merge) instead
        with m.patch.object(airuleset, "_current_user", return_value="montalu1"):
            self.assertEqual(wd._box_authority(), "branch-merge")


if __name__ == "__main__":
    main()
