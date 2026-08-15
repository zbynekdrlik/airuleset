"""#433 cluster L-A — split lock for cli_statusline_glue.py.

The statusline-shim RENDERING, marketplace-registration glue, and per-box
skill-subset selector were extracted VERBATIM from airuleset.py into the
pure-stdlib leaf `cli_statusline_glue.py`, re-exported through airuleset's
facade. This test proves the three things a leaf extraction must guarantee:

  1. the leaf imports STANDALONE in a fresh process, with NO airuleset in
     sys.modules (a module-level `import airuleset` would crash CLI mode,
     where airuleset runs as `__main__`);
  2. airuleset re-exports every moved name as the SAME object (facade identity),
     so every resident caller + `airuleset.X` test reference keeps resolving;
  3. the three deferred-`import airuleset` back-refs are LIVE — each moved
     function reads the RESIDENT value through `airuleset.X` at call time, not
     a stale local copy (mutation teeth: patch the resident value, assert the
     function reflects it).

Written as a unittest.TestCase so `python3 -m unittest discover -s tests`
(cmd_push's gate) genuinely collects it.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_statusline_glue as g  # noqa: E402

MOVED_NAMES = (
    "skill_names_for_user",
    "ensure_marketplace_registered",
    "render_caveman_shim",
    "_marketplace_names_for",
    "MARKETPLACE_SOURCES",
    "CAVEMAN_MARKETPLACE_REPO",
    "OFFICIAL_MARKETPLACE_SOURCE",
    "CAVEMAN_SHIM_CONTENT",
)


class TestStandaloneImport(unittest.TestCase):
    def test_leaf_imports_without_pulling_in_airuleset(self):
        # A fresh process: import ONLY the leaf, and prove airuleset never got
        # imported as a side effect (no module-level `import airuleset`).
        code = (
            "import sys; import cli_statusline_glue as g; "
            "assert 'airuleset' not in sys.modules, 'leaf pulled in airuleset at import'; "
            "assert callable(g.render_caveman_shim); "
            "assert g.MARKETPLACE_SOURCES['caveman'] == 'JuliusBrussee/caveman'; "
            "print('OK')"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_leaf_has_no_module_level_import_airuleset(self):
        src = (REPO / "cli_statusline_glue.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.strip() == "import airuleset":
                self.assertTrue(line.startswith("    "),
                                "leaf has a MODULE-LEVEL `import airuleset` — "
                                "crashes CLI mode; must be deferred inside a body")


class TestFacadeReexportIdentity(unittest.TestCase):
    def test_airuleset_reexports_the_same_objects(self):
        for n in MOVED_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(g, n),
                          "airuleset.%s is not the leaf object — facade broken" % n)

    def test_rendering_wiring_boundary_stays_resident(self):
        # #433 L-A moved the shim RENDERING; the shim WIRING (dest path +
        # statusline command, consumed by L-C setup_caveman) stays resident.
        self.assertFalse(hasattr(g, "CAVEMAN_SHIM_DEST"),
                         "CAVEMAN_SHIM_DEST leaked into the leaf — it must stay resident")
        self.assertFalse(hasattr(g, "CAVEMAN_STATUSLINE_COMMAND"),
                         "CAVEMAN_STATUSLINE_COMMAND leaked into the leaf — it must stay resident")
        self.assertTrue(hasattr(airuleset, "CAVEMAN_SHIM_DEST"))
        self.assertEqual(airuleset.CAVEMAN_STATUSLINE_COMMAND,
                         'bash "%s"' % airuleset.CAVEMAN_SHIM_DEST)


class TestDeferredBackRefsAreLive(unittest.TestCase):
    """Mutation teeth: each moved function reads the RESIDENT value via
    `airuleset.X` at call time. Patch the resident value; the function must
    reflect it. A mutant that referenced a stale local copy would not."""

    def test_render_caveman_shim_reads_resident_managed_model(self):
        sentinel = "SENTINEL-MODEL-Zzz9"
        with m.patch.object(airuleset, "MANAGED_MODEL", sentinel):
            out = airuleset.render_caveman_shim()
        self.assertIn(sentinel, out,
                      "render_caveman_shim did not substitute the resident MANAGED_MODEL")
        self.assertNotIn("{{MANAGED_MODEL}}", out)
        # REPO_DIR is the leaf's own copy but must resolve to this same checkout.
        self.assertNotIn("{{REPO_DIR}}", out)

    def test_ensure_marketplace_registered_uses_resident_claude_cli_env(self):
        sentinel_env = {"AIRULESET_TEST_ENV_MARKER": "yes"}
        captured = {}

        def fake_run(*a, **kw):
            captured["env"] = kw.get("env")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch.object(airuleset, "_claude_cli_env", return_value=sentinel_env), \
                m.patch("subprocess.run", side_effect=fake_run):
            ok = airuleset.ensure_marketplace_registered("caveman")
        self.assertTrue(ok)
        self.assertEqual(captured["env"], sentinel_env,
                         "ensure_marketplace_registered did not pass "
                         "airuleset._claude_cli_env() as the subprocess env")

    def test_ensure_marketplace_registered_reads_resident_sources(self):
        # A name absent from MARKETPLACE_SOURCES silently no-ops ("nothing to
        # do") without ever shelling out — proves it reads the moved dict.
        with m.patch("subprocess.run") as run:
            self.assertTrue(airuleset.ensure_marketplace_registered("not-a-marketplace"))
        run.assert_not_called()

    def test_skill_names_for_user_reads_resident_registries(self):
        # Patch the resident skill registries to a sentinel shape; the moved
        # function (deferred import) must derive its result from THEM.
        with m.patch.object(airuleset, "SKILL_NAMES", ["only_one_skill"]), \
                m.patch.object(airuleset, "SKILLS_MAINTAINER_ONLY", set()), \
                m.patch.object(airuleset, "SKILLS_FULL_AUTHORITY_ONLY", set()), \
                m.patch.object(airuleset, "SKILLS_EXTRA_BY_USER", {}):
            self.assertEqual(airuleset.skill_names_for_user("newlevel"), ["only_one_skill"])

    def test_skill_names_for_user_honours_resident_authority_map(self):
        # A non-full-authority user drops the full-authority-only skill; a
        # full-authority (maintainer) user keeps it — driven entirely by the
        # RESIDENT AUTHORITY_BY_USER / MAINTAINER_USERS the function reads.
        with m.patch.object(airuleset, "SKILL_NAMES", ["common", "priv"]), \
                m.patch.object(airuleset, "SKILLS_MAINTAINER_ONLY", set()), \
                m.patch.object(airuleset, "SKILLS_FULL_AUTHORITY_ONLY", {"priv"}), \
                m.patch.object(airuleset, "SKILLS_EXTRA_BY_USER", {}), \
                m.patch.object(airuleset, "MAINTAINER_USERS", {"boss"}), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {"sub": "fork-no-merge"}):
            self.assertEqual(airuleset.skill_names_for_user("sub"), ["common"])
            self.assertEqual(airuleset.skill_names_for_user("boss"), ["common", "priv"])


if __name__ == "__main__":
    unittest.main()
