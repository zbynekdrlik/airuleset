"""authority_cli_829 tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m
from authority_testlib import (  # noqa: E402
    airuleset,
)


class TestAuthCliRepoRoot829(TestCase):
    """#829: `cmd_authority` (plain `authority` and `--explain`) must resolve the
    project CLAUDE.md marker against the REPO ROOT (`airuleset._repo_root() or
    None`), IDENTICALLY to the in-process consumers (the run-card, the
    footer/slice gates, and the close-guard hook, which all anchor via
    `_repo_root()` -- the #181 I-5 / run-card precedent). Pre-#829 it anchored at
    the bare process cwd, so invoked from a SUBDIRECTORY of a marker-carrying
    project it read `<subdir>/CLAUDE.md` (no marker), the map/allow-list won, and
    `--explain` (the #821 stale-mapping diagnostic) mis-named the winning source
    and printed `marker=none` while the consumers honored the repo-root marker.
    `--explain` also now names the ACTUAL marker PATH it read (or `marker=none`).
    Uses a marker that LOWERS a full account to fork-no-merge, so the fixture is
    robust to #828's "marker may only LOWER authority" (lowering is always
    honored) and never depends on the marker-precedence half."""

    _FLAGS = dict(maintainer_login=False, self_login=False,
                  stream_label=False, app_bot_login=False)

    def _printed(self, explain=False):
        with m.patch("builtins.print") as p:
            airuleset.cmd_authority(m.Mock(explain=explain, **self._FLAGS))
        return [str(c.args[0]) for c in p.call_args_list if c.args]

    def _mkrepo(self, marker=None):
        # A throwaway "repo root" dir (auto-cleaned), optionally carrying a
        # CLAUDE.md with `marker`. addCleanup so the suite never leaks tmpdirs.
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if marker is not None:
            (Path(d) / "CLAUDE.md").write_text(marker)
        return d

    def test_cli_reads_the_marker_at_the_repo_root(self):
        # Seam (fully deterministic, mirrors the run-card repo-root tests): the
        # CLI reads the authority marker anchored at airuleset._repo_root(),
        # never the bare process cwd. RED: pre-#829 the marker read cwd is None
        # (Path.cwd()), not the repo root.
        import cli_quals
        seen = {}

        def fake_marker(cwd=None):
            seen["marker_cwd"] = cwd
            return None

        with m.patch.object(airuleset, "_repo_root", return_value="/repo/root"), \
                m.patch.object(cli_quals, "_authority_marker_raw",
                               side_effect=fake_marker), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self._printed(explain=False)
        self.assertEqual(
            seen.get("marker_cwd"), "/repo/root",
            "the CLI must read the authority marker at the repo root, not the "
            "process cwd -- got %r" % (seen.get("marker_cwd"),))

    def test_plain_profile_honors_a_repo_root_marker_from_a_subdir(self):
        # A repo-root marker LOWERING newlevel (full) -> fork-no-merge is honored
        # by the CLI even when the process cwd (a subdir) carries no CLAUDE.md.
        # RED: prints `full` (the ambient/subdir cwd has no marker).
        d = self._mkrepo("<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            printed = self._printed(explain=False)
        self.assertIn("fork-no-merge", printed, printed)
        self.assertNotIn("full", printed, printed)

    def test_plain_profile_matches_the_in_process_consumers(self):
        # The unification claim: the CLI's profile == what every in-process
        # consumer resolves via resolve_authority(cwd=_repo_root() or None).
        d = self._mkrepo("<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            printed = self._printed(explain=False)
            consumer = airuleset.resolve_authority(
                cwd=airuleset._repo_root() or None)
        self.assertIn(consumer, printed, (consumer, printed))
        self.assertEqual(consumer, "fork-no-merge")

    def test_explain_names_the_actual_marker_path(self):
        # #829: --explain names the CLAUDE.md PATH the winning marker was read
        # from (repo-root-anchored). RED: pre-#829 it says `marker=none` and
        # `via full-authority account`, naming no path.
        d = self._mkrepo("<!-- airuleset:authority=fork-no-merge -->\n")
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            out = " ".join(self._printed(explain=True))
        self.assertIn("resolved=fork-no-merge via marker-lowered", out)  # #828: a lowering marker reports source marker-lowered
        self.assertIn("(read from %s)" % (Path(d) / "CLAUDE.md"), out)

    def test_explain_invalid_marker_names_path(self):
        # #829: an INVALID marker (matches `[a-z-]+` but is not a profile) is
        # ignored for resolution (the map/allow-list stands) but --explain
        # surfaces it as `invalid('<tok>')` AND names the PATH it was read from
        # (the #821 typo'd-marker diagnostic, now with the file location).
        d = self._mkrepo("<!-- airuleset:authority=superuser -->\n")
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            out = " ".join(self._printed(explain=True))
        self.assertIn("marker=invalid('superuser')", out)
        self.assertIn("(read from %s)" % (Path(d) / "CLAUDE.md"), out)
        self.assertIn("resolved=full via full-authority account", out)

    def test_explain_marker_none_carries_no_path(self):
        # No marker at the repo root -> `marker=none`, and NO path is claimed
        # (nothing was read). miva1 resolves via the per-user map (branch-merge).
        d = self._mkrepo()  # no CLAUDE.md
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="miva1"):
            out = " ".join(self._printed(explain=True))
        self.assertIn("marker=none", out)
        # No "(read from <path>)" clause when there is no marker (robust to the
        # boilerplate tail ever mentioning CLAUDE.md; keys on the path phrase).
        self.assertNotIn("read from", out,
                         "marker=none must not claim a marker PATH")
        self.assertIn("resolved=branch-merge via per-user map", out)

    def test_outside_any_repo_falls_back_cleanly(self):
        # _repo_root()=="" (outside any git repo) -> `_repo_root() or None` is
        # None, matching the consumers exactly. Deterministic: patch the marker
        # read to record the cwd it was handed AND return no marker, so the test
        # never depends on the ambient process cwd's own CLAUDE.md. Locks that
        # `"" or None` passes None (not "") and the CLI still resolves + prints.
        import cli_quals
        seen = {}

        def fake_marker(cwd=None):
            seen.setdefault("cwds", []).append(cwd)
            return None

        with m.patch.object(airuleset, "_repo_root", return_value=""), \
                m.patch.object(cli_quals, "_authority_marker_raw",
                               side_effect=fake_marker), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            plain = self._printed(explain=False)
            out = " ".join(self._printed(explain=True))
        self.assertTrue(seen.get("cwds"), "marker read never happened")
        self.assertTrue(all(c is None for c in seen["cwds"]),
                        "outside a repo the CLI must pass cwd=None (not ''), "
                        "matching the consumers -- got %r" % (seen["cwds"],))
        self.assertIn("full", plain, plain)
        self.assertIn("marker=none", out)
        self.assertIn("resolved=full via full-authority account", out)

    def test_stream_label_anchors_at_repo_root(self):
        # #829: the `--stream-label` arm (also shelled out by the close-guard
        # hook, from the SAME session cwd) must honor a repo-root marker from a
        # subdir too, or it disagrees with the plain profile. A repo-root marker
        # LOWERING newlevel (full) -> fork-no-merge makes the arm print a
        # `stream:` label; RED pre-fix: the bare-cwd `resolve_authority()`
        # resolves `full` -> prints NOTHING.
        d = self._mkrepo("<!-- airuleset:authority=fork-no-merge -->\n")
        flags = dict(self._FLAGS)
        flags["stream_label"] = True
        with m.patch.object(airuleset, "_repo_root", return_value=d), \
                m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            with m.patch("builtins.print") as p:
                airuleset.cmd_authority(m.Mock(explain=False, **flags))
        printed = [str(c.args[0]) for c in p.call_args_list if c.args]
        self.assertTrue(
            any(s.startswith("stream:") for s in printed),
            "the --stream-label arm must honor the repo-root marker (reduced "
            "authority) from a subdir -- got %r" % (printed,))


if __name__ == "__main__":
    main()
