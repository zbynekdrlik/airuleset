"""#1116 — the managed settings merge OWNS the two mouse/altscreen toggles
(dropped from ``~/.claude/settings.json`` ``env`` at install, each removal
reported) and the drift scan gains two new legs: the ``settings.json`` ``env``
and the tmux server's global environment.

Root cause (design comment id 5782368399): ``cli_config`` builds the merged
settings with ``result["env"] = dict(existing_env)`` — preserving EVERY
pre-existing ``env`` key — so a stray ``CLAUDE_CODE_DISABLE_MOUSE`` /
``CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN`` survives every push and, because Claude
Code applies its settings ``env`` inside the process, defeats the v0.1.405
launcher ``unset`` (#1015). And ``cli_bashrc_drift`` read only shell rc files, so
``status`` / install / conformance could not name the two sources that actually
held dev2 back (the incident: "the first restart still carried the toggles").

Design (Approach 1, items 1-3):
 (1) ``cli_config.apply_managed_settings_defaults`` POPs the two drop keys from
     the merged ``env`` (the SAME ``cli_bashrc_drift.MANAGED_ENV_DROP_KEYS`` tuple
     the launcher ``unset`` line is built from) and prints
     ``settings: removed unmanaged env key <k>=<v> (the launcher owns it, #1116)``
     per key; every OTHER env key (managed + foreign) is preserved byte-for-byte.
 (2) ``cli_bashrc_drift`` gains ``scan_env_drift`` with two new legs behind
     injectable seams: a ``settings.json:env`` leg (reads the drop keys from a
     settings.json ``env``) and a ``tmux:global-env`` leg (parses
     ``tmux show-environment -g`` for the drop keys, with the
     ``tmux set-environment -gu <k>`` fix hint; best-effort — no server -> no hit,
     no error). ``bashrc_drift_status_row`` / ``bashrc_drift_install_warning``
     name the source per hit; ``count_bashrc_drift`` sums all legs.
 (3) Drift lock: the launcher ``unset`` line and the merge drop tuple are the
     SAME names (one source of truth).
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

# repo root on the path so the modules import as top-level.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_bashrc_drift as bd  # noqa: E402
import cli_config  # noqa: E402
import cli_claude_scripts  # noqa: E402

_MOUSE = "CLAUDE_CODE_DISABLE_MOUSE"
_ALT = "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"


# --------------------------------------------------------------------------- #
# (0) the single source of truth: MANAGED_ENV_DROP_KEYS
# --------------------------------------------------------------------------- #
class TestManagedEnvDropKeys(unittest.TestCase):
    def test_tuple_is_exactly_the_two_mouse_toggles_in_order(self):
        # order matters: the launcher `unset` line is built via " ".join(...),
        # and test_launcher_mouse_env_1015's frozen _UNSET expects this order.
        self.assertEqual(bd.MANAGED_ENV_DROP_KEYS, (_MOUSE, _ALT))

    def test_tuple_is_an_immutable_tuple(self):
        self.assertIsInstance(bd.MANAGED_ENV_DROP_KEYS, tuple)


# --------------------------------------------------------------------------- #
# (1) the settings merge drops the toggles, reports each, preserves the rest
# --------------------------------------------------------------------------- #
class TestSettingsMergeDropsMouseToggles(unittest.TestCase):
    def _merge(self, settings):
        """Run the merge, returning (merged_dict, printed_stderr). The removal
        line is printed to STDERR (matches the function's playwright diagnostic;
        keeps cmd_diff stdout clean) — so we capture stderr here."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            merged = cli_config.apply_managed_settings_defaults(settings)
        return merged, buf.getvalue()

    def test_both_drop_keys_removed_from_merged_env(self):
        merged, _ = self._merge({"env": {_MOUSE: "1", _ALT: "1"}})
        self.assertNotIn(_MOUSE, merged["env"])
        self.assertNotIn(_ALT, merged["env"])

    def test_foreign_env_key_preserved_byte_for_byte(self):
        merged, _ = self._merge(
            {"env": {_MOUSE: "1", "FOREIGN_KEY": "keep-me-exactly"}})
        self.assertEqual(merged["env"].get("FOREIGN_KEY"), "keep-me-exactly")

    def test_managed_env_keys_still_set_after_the_drop(self):
        import airuleset
        merged, _ = self._merge({"env": {_MOUSE: "1"}})
        self.assertEqual(
            merged["env"].get("CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"),
            airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION)
        self.assertEqual(
            merged["env"].get("CLAUDE_CODE_SUBAGENT_MODEL"),
            airuleset.MODEL_TIERS["opus5"])  # #1119: subagent default = Opus 5.5

    def test_removal_line_printed_per_key_with_value_and_citation(self):
        _, out = self._merge({"env": {_MOUSE: "1", _ALT: "0"}})
        self.assertIn(
            "settings: removed unmanaged env key %s=1 (the launcher owns it, #1116)"
            % _MOUSE, out)
        self.assertIn(
            "settings: removed unmanaged env key %s=0 (the launcher owns it, #1116)"
            % _ALT, out)

    def test_no_removal_line_when_keys_absent(self):
        merged, out = self._merge({"env": {"FOREIGN_KEY": "x"}})
        self.assertNotIn("removed unmanaged env key", out)
        self.assertEqual(merged["env"].get("FOREIGN_KEY"), "x")

    def test_idempotent_second_merge_prints_nothing_new(self):
        # merging the already-merged result must not re-report (keys are gone).
        merged1, _ = self._merge({"env": {_MOUSE: "1"}})
        _, out2 = self._merge(merged1)
        self.assertNotIn("removed unmanaged env key", out2)

    def test_non_dict_env_is_healed_and_no_crash(self):
        # a legacy string/int env must still self-heal (existing #288 behaviour)
        # and the drop-key pop is a harmless no-op on the fresh dict.
        merged, out = self._merge({"env": "not-a-dict"})
        self.assertIsInstance(merged["env"], dict)
        self.assertNotIn(_MOUSE, merged["env"])
        self.assertNotIn("removed unmanaged env key", out)

    def test_merge_drops_exactly_the_managed_tuple_not_other_claude_keys(self):
        # a stray non-drop CLAUDE_CODE_* env key is NOT the merge's concern
        # (only the two toggles the launcher owns); it is preserved.
        merged, _ = self._merge(
            {"env": {_MOUSE: "1", "CLAUDE_CODE_SOMETHING_ELSE": "9"}})
        self.assertNotIn(_MOUSE, merged["env"])
        self.assertEqual(merged["env"].get("CLAUDE_CODE_SOMETHING_ELSE"), "9")


# --------------------------------------------------------------------------- #
# (2a) the settings.json:env scan leg
# --------------------------------------------------------------------------- #
class TestSettingsEnvScanLeg(unittest.TestCase):
    def _settings(self, td, env):
        import json
        p = Path(td) / "settings.json"
        p.write_text(json.dumps({"env": env}))
        return p

    def test_drop_keys_in_settings_env_are_reported_as_settings_source(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._settings(td, {_MOUSE: "1", _ALT: "1", "FOREIGN": "x"})
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(sp), tmux_env_reader=None)
            sources = [h.source for h in hits]
            self.assertEqual(sources.count("settings.json:env"), 2, hits)
            details = " ".join(h.detail for h in hits)
            self.assertIn(_MOUSE, details)
            self.assertIn(_ALT, details)
            # a foreign (non-drop) env key is NOT reported by this leg.
            self.assertNotIn("FOREIGN", details)

    def test_settings_env_detail_carries_key_and_value(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._settings(td, {_MOUSE: "1"})
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(sp), tmux_env_reader=None)
            self.assertEqual(len(hits), 1, hits)
            self.assertEqual(hits[0].source, "settings.json:env")
            self.assertIn("%s=1" % _MOUSE, hits[0].detail)

    def test_missing_settings_file_no_hit_no_error(self):
        hits = bd.scan_env_drift(
            paths=[], settings_path="/nonexistent/definitely/settings.json",
            tmux_env_reader=None)
        self.assertEqual(hits, [])

    def test_malformed_settings_json_no_hit_no_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text("{ this is not json ")
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(p), tmux_env_reader=None)
            self.assertEqual(hits, [])

    def test_invalid_utf8_settings_no_raise(self):
        # adversarial-review finding: an invalid-UTF-8 settings.json makes
        # read_text(encoding="utf-8") raise UnicodeDecodeError (a ValueError, NOT
        # OSError). The leg's "never raise on malformed" contract requires it to
        # degrade to no error — read uses errors="replace" so json.loads then
        # fails into the caught branch. A file whose bytes ARE a drop key with a
        # garbled value still yields the hit (the key name is intact).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_bytes(b'{"env": {"NOT_A_DROP_KEY": "\xff\xfe garbage"}}')
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(p), tmux_env_reader=None)
            self.assertEqual(hits, [])  # no drop key present, and NO raise

    def test_settings_without_env_object_no_hit(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text('{"model": "x"}')
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(p), tmux_env_reader=None)
            self.assertEqual(hits, [])

    def test_settings_env_not_a_dict_no_hit_no_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text('{"env": "oops-a-string"}')
            hits = bd.scan_env_drift(
                paths=[], settings_path=str(p), tmux_env_reader=None)
            self.assertEqual(hits, [])

    def test_settings_leg_skipped_when_settings_path_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._settings(td, {_MOUSE: "1"})
            # settings_path=None means "do not scan settings" (targeted mode).
            hits = bd.scan_env_drift(
                paths=[], settings_path=None, tmux_env_reader=None)
            self.assertEqual(hits, [])
            # sanity: the file DOES hold a drop key (so None truly skipped it).
            self.assertTrue(sp.exists())

    def test_targeted_scan_with_default_kwargs_is_bashrc_only(self):
        # adversarial-review finding (both reviewers): the SINGLE entry point
        # scan_env_drift must NOT read the real box settings.json / spawn tmux on
        # an EXPLICIT-paths call when the source kwargs are left at their default.
        # (A bashrc-only call is `paths=[...]` with no source kwargs.) Only a
        # DEFAULT box scan (paths=None) touches the real sources.
        #
        # Mutation teeth: patch the DEFAULT tmux reader to a DIRTY one, so that a
        # mutant which flips the box-scan gate (and thus runs the default legs on
        # a targeted call) is caught DETERMINISTICALLY — not only when the real
        # box happens to be dirty.
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as td:
            br = Path(td) / ".bashrc"
            br.write_text("export %s=1\n" % _MOUSE)
            with m.patch.object(bd, "_default_tmux_env_reader",
                                lambda: "%s=1\n" % _ALT):
                hits = bd.scan_env_drift(paths=[str(br)])  # default source kwargs
            self.assertTrue(hits)
            self.assertTrue(all(h.source.startswith("bashrc:") for h in hits),
                            "a targeted scan_env_drift must stay bashrc-only "
                            "(it must NOT invoke the default tmux/settings legs)")


# --------------------------------------------------------------------------- #
# (2b) the tmux:global-env scan leg
# --------------------------------------------------------------------------- #
class TestTmuxEnvScanLeg(unittest.TestCase):
    def test_drop_keys_in_tmux_env_are_reported_with_fix_hint(self):
        tmux_out = (
            "PATH=/usr/bin\n"
            "%s=1\n"
            "SOMETHING=else\n"
            "%s=1\n" % (_MOUSE, _ALT))
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=lambda: tmux_out)
        sources = [h.source for h in hits]
        self.assertEqual(sources.count("tmux:global-env"), 2, hits)
        # the fix hint names `tmux set-environment -gu <k>` for each.
        for k in (_MOUSE, _ALT):
            h = next(h for h in hits if k in h.detail)
            self.assertIn("tmux set-environment -gu %s" % k, h.fix)

    def test_tmux_removed_marker_line_is_not_a_hit(self):
        # `tmux show-environment -g` prints `-VAR` for a var REMOVED from the
        # global environment — that is the CLEAN state, never drift.
        tmux_out = "-%s\n-%s\n" % (_MOUSE, _ALT)
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=lambda: tmux_out)
        self.assertEqual(hits, [])

    def test_no_tmux_server_reader_returns_empty_no_hit_no_error(self):
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=lambda: "")
        self.assertEqual(hits, [])

    def test_no_tmux_server_reader_raises_no_hit_no_error(self):
        def _boom():
            raise RuntimeError("no server running")
        # a raising reader (e.g. the real subprocess failing) degrades to
        # no hit, never propagates.
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=_boom)
        self.assertEqual(hits, [])

    def test_tmux_leg_skipped_when_reader_is_none(self):
        # reader None -> the leg is not run at all (targeted mode).
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=None)
        self.assertEqual(hits, [])

    def test_non_drop_tmux_var_not_reported(self):
        tmux_out = "CLAUDE_CODE_SOMETHING=1\nFOO=bar\n"
        hits = bd.scan_env_drift(
            paths=[], settings_path=None, tmux_env_reader=lambda: tmux_out)
        self.assertEqual(hits, [])

    def test_default_tmux_reader_is_best_effort_when_tmux_absent(self):
        # the real default reader must never raise even if `tmux` is missing.
        # Force a missing binary via an empty PATH; expect "" (empty), no error.
        old = os.environ.get("PATH")
        try:
            os.environ["PATH"] = ""
            out = bd._default_tmux_env_reader()
        finally:
            if old is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old
        self.assertEqual(out, "")


# --------------------------------------------------------------------------- #
# (2c) the three consumers render/sum all legs, and name the source per hit
# --------------------------------------------------------------------------- #
class TestConsumersAcrossAllLegs(unittest.TestCase):
    def _bashrc(self, td):
        p = Path(td) / ".bashrc"
        p.write_text("export %s=1\n" % _MOUSE)
        return p

    def _settings(self, td):
        import json
        p = Path(td) / "settings.json"
        p.write_text(json.dumps({"env": {_ALT: "1"}}))
        return p

    def test_status_row_names_each_source(self):
        with tempfile.TemporaryDirectory() as td:
            br = self._bashrc(td)
            sp = self._settings(td)
            tmux_out = "%s=1\n" % _MOUSE
            hits = bd.scan_env_drift(
                paths=[str(br)], settings_path=str(sp),
                tmux_env_reader=lambda: tmux_out)
            self.assertEqual(len(hits), 3, hits)
            row = bd.bashrc_drift_status_row(
                paths=[str(br)], settings_path=str(sp),
                tmux_env_reader=lambda: tmux_out)
            self.assertIn("bashrc-drift:", row)
            self.assertIn("bashrc:%s:1" % br, row)
            self.assertIn("settings.json:env", row)
            self.assertIn("tmux:global-env", row)
            self.assertIn("tmux set-environment -gu %s" % _MOUSE, row)

    def test_install_warning_names_each_source(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._settings(td)
            warn = bd.bashrc_drift_install_warning(
                paths=[], settings_path=str(sp), tmux_env_reader=None)
            self.assertIsNotNone(warn)
            self.assertIn("WARNING", warn)
            self.assertIn("settings.json:env", warn)

    def test_install_warning_none_when_clean(self):
        warn = bd.bashrc_drift_install_warning(
            paths=[], settings_path="/nonexistent/x.json", tmux_env_reader=None)
        self.assertIsNone(warn)

    def test_count_sums_all_legs(self):
        with tempfile.TemporaryDirectory() as td:
            br = self._bashrc(td)          # 1 bashrc hit
            sp = self._settings(td)        # 1 settings hit
            tmux_out = "%s=1\n%s=1\n" % (_MOUSE, _ALT)  # 2 tmux hits
            n = bd.count_bashrc_drift(
                paths=[str(br)], settings_path=str(sp),
                tmux_env_reader=lambda: tmux_out)
            self.assertEqual(n, 4)


# --------------------------------------------------------------------------- #
# (3) drift lock: the launcher `unset` line and the merge drop tuple are one
# --------------------------------------------------------------------------- #
class TestLauncherAndMergeShareTheTuple(unittest.TestCase):
    def test_main_launcher_unset_line_is_built_from_the_tuple(self):
        content = cli_claude_scripts.render_claude_launch_script()
        expected = "unset " + " ".join(bd.MANAGED_ENV_DROP_KEYS)
        self.assertIn(expected, content)
        self.assertNotIn("{{MANAGED_ENV_UNSET}}", content)

    def test_impl_launcher_unset_line_is_built_from_the_tuple(self):
        content = cli_claude_scripts.render_claude_impl_launch_script()
        expected = "unset " + " ".join(bd.MANAGED_ENV_DROP_KEYS)
        self.assertIn(expected, content)
        self.assertNotIn("{{MANAGED_ENV_UNSET}}", content)

    def test_render_helper_matches_the_tuple_exactly(self):
        # the launcher unset line has EXACTLY the tuple's names, in order —
        # a mutant that reorders or drops a name is caught here.
        self.assertEqual(
            cli_claude_scripts._managed_env_unset_line(),
            "unset %s %s" % (_MOUSE, _ALT))

    def test_no_raw_unset_literal_in_the_launcher_constants(self):
        # single source of truth: the raw constants must NOT hardcode the unset
        # line anymore — it is a {{placeholder}} substituted from the tuple.
        raw_main = cli_claude_scripts.CLAUDE_LAUNCH_SCRIPT_CONTENT
        raw_impl = cli_claude_scripts.CLAUDE_IMPL_LAUNCH_SCRIPT_CONTENT
        literal = "unset %s %s" % (_MOUSE, _ALT)
        self.assertNotIn(literal, raw_main,
                         "the main launcher must build the unset from the tuple")
        self.assertNotIn(literal, raw_impl,
                         "the impl launcher must build the unset from the tuple")
        self.assertIn("{{MANAGED_ENV_UNSET}}", raw_main)
        self.assertIn("{{MANAGED_ENV_UNSET}}", raw_impl)

    def test_merge_pops_exactly_the_same_tuple(self):
        # every drop key removed by the merge is a member of MANAGED_ENV_DROP_KEYS
        # (proves the merge and the launcher name the identical set).
        env = {k: "1" for k in bd.MANAGED_ENV_DROP_KEYS}
        env["FOREIGN"] = "x"
        merged = cli_config.apply_managed_settings_defaults({"env": dict(env)})
        for k in bd.MANAGED_ENV_DROP_KEYS:
            self.assertNotIn(k, merged["env"])
        self.assertEqual(merged["env"].get("FOREIGN"), "x")


if __name__ == "__main__":
    unittest.main()
