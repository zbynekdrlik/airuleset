"""authority_marker_cap_828 tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from unittest import TestCase, main
from unittest import mock as m
from authority_testlib import (  # noqa: E402
    airuleset,
)


class TestMarkerIsLowerOnly828(TestCase):
    """airuleset#828 (owner decision A): the project CLAUDE.md
    `<!-- airuleset:authority=<profile> -->` marker is a CAP, not an override.
    It may only LOWER authority relative to the per-user table /
    FULL_AUTHORITY_USERS result (more-restrictive-of on the lattice
    full > branch-merge > fork-no-merge), NEVER raise it -- closing the
    self-elevation vector (a reduced stream editing its OWN CLAUDE.md could grant
    itself `full` and disable the issue-close guard). `full` is granted ONLY via
    the per-user map / full allow-list / ci-runner recognition. These are RED on
    the pre-#828 raise-capable resolver and GREEN once the marker caps the base.
    """

    def _write_marker(self, profile):
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "<!-- airuleset:authority=%s -->\n" % profile)
        return d

    # -- the lattice helper ------------------------------------------------ #
    def test_more_restrictive_picks_the_lower_profile(self):
        import cli_quals
        mr = cli_quals._more_restrictive
        # full > branch-merge > fork-no-merge; more-restrictive == the LOWER.
        self.assertEqual(mr("full", "fork-no-merge"), "fork-no-merge")
        self.assertEqual(mr("fork-no-merge", "full"), "fork-no-merge")
        self.assertEqual(mr("full", "branch-merge"), "branch-merge")
        self.assertEqual(mr("branch-merge", "full"), "branch-merge")
        self.assertEqual(mr("branch-merge", "fork-no-merge"), "fork-no-merge")
        self.assertEqual(mr("full", "full"), "full")
        self.assertEqual(mr("branch-merge", "branch-merge"), "branch-merge")

    # -- a RAISE marker is IGNORED ----------------------------------------- #
    def test_full_marker_on_fork_stream_is_ignored(self):
        # a fork-no-merge stream editing its own CLAUDE.md to authority=full
        # STAYS fork-no-merge (the self-elevation vector, closed).
        d = self._write_marker("full")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_branch_merge_marker_on_fork_stream_is_ignored(self):
        # branch-merge would still RAISE fork-no-merge -> ignored.
        d = self._write_marker("branch-merge")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_full_marker_on_branch_merge_stream_is_ignored(self):
        # marek is branch-merge in the table; a full marker would RAISE -> ignored.
        d = self._write_marker("full")
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "branch-merge")

    def test_full_marker_on_full_account_stays_full(self):
        # a full account + a full marker: equal to base, no raise, no lower.
        d = self._write_marker("full")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "full")

    # -- a LOWER marker APPLIES -------------------------------------------- #
    def test_branch_merge_marker_lowers_a_full_account(self):
        # owner's RED->GREEN case: a full user + authority=branch-merge marker
        # becomes branch-merge.
        d = self._write_marker("branch-merge")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "branch-merge")

    def test_fork_marker_lowers_a_full_account(self):
        # owner's more-restrictive case: marker=fork-no-merge + table=full ->
        # fork-no-merge.
        d = self._write_marker("fork-no-merge")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    def test_fork_marker_lowers_a_branch_merge_stream(self):
        # marek (branch-merge) + fork-no-merge marker -> fork-no-merge.
        d = self._write_marker("fork-no-merge")
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    # -- no marker -> the table -------------------------------------------- #
    def test_no_marker_resolves_from_the_table(self):
        import tempfile
        d = tempfile.mkdtemp()  # no CLAUDE.md
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "branch-merge")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            self.assertEqual(airuleset.resolve_authority(cwd=d), "fork-no-merge")

    # -- no current account loses rights (all full identities in the table) - #
    def test_full_accounts_still_resolve_full_without_a_marker(self):
        import tempfile
        d = tempfile.mkdtemp()  # marker-free
        for u in airuleset.FULL_AUTHORITY_USERS:
            with m.patch.object(airuleset, "_current_user", return_value=u):
                self.assertEqual(airuleset.resolve_authority(cwd=d), "full", u)

    # -- _authority_base is the marker-FREE base --------------------------- #
    def test_authority_base_is_marker_free(self):
        import cli_quals
        self.assertEqual(cli_quals._authority_base("marek"),
                         ("branch-merge", "per-user map"))
        self.assertEqual(cli_quals._authority_base("david1"),
                         ("fork-no-merge", "per-user map"))
        self.assertEqual(cli_quals._authority_base("newlevel"),
                         ("full", "full-authority account"))
        self.assertEqual(cli_quals._authority_base("nobody-here"),
                         ("fork-no-merge", "default (unmapped)"))

    # -- _authority_decision provenance ------------------------------------ #
    def test_decision_names_marker_lowered_source(self):
        import cli_quals
        d = self._write_marker("fork-no-merge")
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            profile, source, raw = cli_quals._authority_decision(cwd=d)
        self.assertEqual(profile, "fork-no-merge")
        self.assertEqual(source, "marker-lowered")
        self.assertEqual(raw, "fork-no-merge")

    def test_decision_ignores_raise_and_keeps_base_source(self):
        import cli_quals
        d = self._write_marker("full")
        with m.patch.object(airuleset, "_current_user", return_value="david1"):
            profile, source, raw = cli_quals._authority_decision(cwd=d)
        self.assertEqual(profile, "fork-no-merge")
        self.assertEqual(source, "per-user map")   # base source, NOT the marker
        self.assertEqual(raw, "full")              # raw marker still surfaced

    # -- cmd_authority --explain ------------------------------------------- #
    def _explain(self, user, marker_profile):
        # Patch the ONE shared marker-read seam (as the #839 explain tests do)
        # so cmd_authority()'s cwd-relative CLAUDE.md read is deterministic.
        import cli_quals
        import io
        import contextlib
        buf = io.StringIO()
        with m.patch.object(airuleset, "_current_user", return_value=user):
            with m.patch.object(cli_quals, "_authority_marker_raw",
                                return_value=marker_profile):
                with contextlib.redirect_stdout(buf):
                    cli_quals.cmd_authority(m.Mock(
                        maintainer_login=False, self_login=False,
                        app_bot_login=False, stream_label=False, explain=True))
        return buf.getvalue()

    def test_explain_names_marker_lowered(self):
        out = self._explain("newlevel", "branch-merge")
        self.assertIn("resolved=branch-merge via marker-lowered", out)

    def test_explain_names_ignored_would_raise(self):
        out = self._explain("david1", "full")
        self.assertIn("resolved=fork-no-merge via per-user map", out)
        self.assertIn("marker=full ignored (would raise fork-no-merge)", out)

    def test_explain_marks_a_redundant_marker_as_equal_base(self):
        # a marker EQUAL to the base is neither lowered nor a raise — the base
        # source stands and the annotation flags it redundant.
        out = self._explain("marek", "branch-merge")
        self.assertIn("resolved=branch-merge via per-user map", out)
        self.assertIn("marker=branch-merge (== base branch-merge)", out)


if __name__ == "__main__":
    main()
