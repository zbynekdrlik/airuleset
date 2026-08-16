"""#433 cluster L-D — split lock for cli_config.py.

The config-authoring functions (parse_profile / categorize_entries /
symlink_global_rules / generate_claude_md / preserve_external_blocks /
load_hooks_json / merge_hooks_into_settings / apply_managed_settings_defaults)
and the validate/diff functions (read_file_safe / unified_diff /
_validate_filedrop / _validate_tmux_cutover / _validate_watchdog / cmd_validate
/ cmd_diff) were extracted VERBATIM from airuleset.py into the leaf
`cli_config.py`, re-exported through airuleset's facade. The config CONSTANTS
(MANAGED_* / EXTERNAL_BLOCK_MARKERS / HOOKS_JSON / UNIVERSAL_PROFILE /
SKILL_NAMES / AGENT_NAMES / CLAUDE_DIR-derived paths) stay RESIDENT; the leaf
reads them via a per-body deferred `import airuleset`. REPO_DIR is a 1-line
leaf-dup (identical value, not test-patched).

This test proves the four things this leaf extraction must guarantee:

  1. the leaf imports STANDALONE in a fresh process, with NO airuleset in
     sys.modules (a module-level `import airuleset` would crash CLI mode,
     where airuleset runs as `__main__`);
  2. airuleset re-exports every moved name as the SAME object (facade identity),
     SUBCOMMANDS binds the re-exports, and the config CONSTANTS did NOT leak
     into the leaf (they stay resident);
  3. the deferred-`import airuleset` back-refs are LIVE — each moved function
     reads the RESIDENT value through `airuleset.X` at call time, not a stale
     local copy (mutation teeth: patch the resident value, assert the function
     reflects it);
  4. REPO_DIR is a value-identical leaf-dup, and the validate helpers' deferred
     back-refs (FILEDROP_SERVICE_TEMPLATE / TMUX_CUTOVER_*) resolve.

Written as a unittest.TestCase so `python3 -m unittest discover -s tests`
(cmd_push's gate) genuinely collects it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_config as c  # noqa: E402

MOVED_NAMES = (
    "parse_profile",
    "categorize_entries",
    "symlink_global_rules",
    "generate_claude_md",
    "preserve_external_blocks",
    "load_hooks_json",
    "merge_hooks_into_settings",
    "apply_managed_settings_defaults",
    "read_file_safe",
    "unified_diff",
    "_validate_filedrop",
    "_validate_tmux_cutover",
    "_validate_watchdog",
    "cmd_validate",
    "cmd_diff",
)

# Config CONSTANTS that MUST stay resident in airuleset.py (test-patchability +
# single-source-of-truth) and must NOT be defined on the leaf.
RESIDENT_ONLY_CONSTANTS = (
    "MANAGED_MARKER", "MANAGED_HEADER", "EXTERNAL_BLOCK_MARKERS",
    "MANAGED_EFFORT_LEVEL", "MANAGED_TUI", "MANAGED_MODEL",
    "MANAGED_MAX_SUBAGENTS_PER_SESSION", "MANAGED_CLEANUP_PERIOD_DAYS",
    "HOOKS_JSON", "UNIVERSAL_PROFILE", "SKILL_NAMES", "AGENT_NAMES",
    "CLAUDE_MD", "SETTINGS_JSON", "SKILLS_DIR", "RULES_DIR",
)


class TestStandaloneImport(unittest.TestCase):
    def test_leaf_imports_without_pulling_in_airuleset(self):
        code = (
            "import sys; import cli_config as c; "
            "assert 'airuleset' not in sys.modules, 'leaf pulled in airuleset at import'; "
            "assert callable(c.generate_claude_md); "
            "assert callable(c.cmd_validate); "
            "print('OK')"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_leaf_has_no_module_level_import_airuleset(self):
        src = (REPO / "cli_config.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.strip() == "import airuleset":
                self.assertTrue(line.startswith("    "),
                                "leaf has a MODULE-LEVEL `import airuleset` — "
                                "crashes CLI mode; must be deferred inside a body")


class TestFacadeReexportIdentity(unittest.TestCase):
    def test_airuleset_reexports_the_same_objects(self):
        for n in MOVED_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(c, n),
                          "airuleset.%s is not the leaf object — facade broken" % n)

    def test_subcommands_bind_the_reexports(self):
        self.assertIs(airuleset.SUBCOMMANDS["validate"], c.cmd_validate)
        self.assertIs(airuleset.SUBCOMMANDS["diff"], c.cmd_diff)

    def test_config_constants_stay_resident_not_on_leaf(self):
        for name in RESIDENT_ONLY_CONSTANTS:
            self.assertTrue(hasattr(airuleset, name),
                            "config constant %s must stay resident" % name)
            self.assertFalse(hasattr(c, name),
                             "config constant %s leaked into the leaf — it must "
                             "stay resident (test-patchability / source of truth)" % name)

    def test_read_file_safe_shared_symbol_reexported_for_L_E(self):
        # read_file_safe is shared with L-E (provision_subdev_soniox_key calls
        # it); L-D owns the def and re-exports it through the facade so L-E's
        # airuleset.read_file_safe resolves regardless of merge order.
        self.assertIs(airuleset.read_file_safe, c.read_file_safe)


class TestLeafDupRepoDir(unittest.TestCase):
    def test_repo_dir_is_value_identical_leaf_dup(self):
        # REPO_DIR is a 1-line leaf-dup (Path(__file__).resolve().parent) — the
        # leaf is a sibling in the same repo dir, so it must equal airuleset's.
        self.assertEqual(c.REPO_DIR, airuleset.REPO_DIR)


class TestDeferredBackRefsAreLive(unittest.TestCase):
    """Mutation teeth: each moved function reads the RESIDENT value via
    `airuleset.X` at call time. Patch the resident value; the function must
    reflect it. A mutant that referenced a stale local copy would not."""

    def test_apply_managed_settings_reads_resident_model_and_effort(self):
        with m.patch.object(airuleset, "MANAGED_MODEL", "SENTINEL-MODEL-Zz9"), \
                m.patch.object(airuleset, "MANAGED_EFFORT_LEVEL", "SENTINEL-EFFORT-Qq7"):
            out = airuleset.apply_managed_settings_defaults({})
        self.assertEqual(out["model"], "SENTINEL-MODEL-Zz9",
                         "apply_managed_settings_defaults did not read resident MANAGED_MODEL")
        self.assertEqual(out["effortLevel"], "SENTINEL-EFFORT-Qq7",
                         "apply_managed_settings_defaults did not read resident MANAGED_EFFORT_LEVEL")

    def test_generate_claude_md_reads_resident_markers(self):
        with m.patch.object(airuleset, "MANAGED_MARKER", "SENTINEL-MARKER-Aa1"), \
                m.patch.object(airuleset, "MANAGED_HEADER", "SENTINEL-HEADER-Bb2"):
            out = airuleset.generate_claude_md([])
        self.assertIn("SENTINEL-MARKER-Aa1", out,
                      "generate_claude_md did not read resident MANAGED_MARKER")
        self.assertIn("SENTINEL-HEADER-Bb2", out,
                      "generate_claude_md did not read resident MANAGED_HEADER")

    def test_preserve_external_blocks_reads_resident_markers(self):
        with m.patch.object(airuleset, "EXTERNAL_BLOCK_MARKERS",
                            [("<SENT-START>", "<SENT-END>")]):
            out = airuleset.preserve_external_blocks(
                "keep <SENT-START>payload<SENT-END> tail", "fresh content")
        self.assertIn("<SENT-START>payload<SENT-END>", out,
                      "preserve_external_blocks did not read resident EXTERNAL_BLOCK_MARKERS")

    def test_load_hooks_json_reads_resident_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hooks.json"
            p.write_text(json.dumps({"hooks": {"Sentinel": []}}))
            with m.patch.object(airuleset, "HOOKS_JSON", p):
                out = airuleset.load_hooks_json()
        self.assertEqual(out, {"hooks": {"Sentinel": []}},
                         "load_hooks_json did not read resident HOOKS_JSON path")

    def test_validate_tmux_cutover_reads_resident_script_content(self):
        # A resident TMUX_CUTOVER_SCRIPT_CONTENT that references the packaged
        # /usr/bin/tmux must be flagged as an error — proving the moved
        # _validate_tmux_cutover reads the resident (deferred) constant.
        with m.patch.object(airuleset, "TMUX_CUTOVER_SCRIPT_CONTENT",
                            "#!/bin/sh\nexec /usr/bin/tmux\n"):
            errs = airuleset._validate_tmux_cutover()
        self.assertTrue(any("/usr/bin/tmux" in e for e in errs),
                        "_validate_tmux_cutover did not read resident "
                        "TMUX_CUTOVER_SCRIPT_CONTENT")


class TestValidateHelpersResolve(unittest.TestCase):
    """On this real, valid repo checkout the three validate helpers return NO
    errors — proving every deferred back-ref (FILEDROP_SERVICE_TEMPLATE,
    TMUX_CUTOVER_*, leaf-dup REPO_DIR) resolves without NameError."""

    def test_validate_filedrop_clean(self):
        self.assertEqual(airuleset._validate_filedrop(), [])

    def test_validate_watchdog_clean(self):
        self.assertEqual(airuleset._validate_watchdog(), [])

    def test_validate_tmux_cutover_clean(self):
        self.assertEqual(airuleset._validate_tmux_cutover(), [])


if __name__ == "__main__":
    unittest.main()
