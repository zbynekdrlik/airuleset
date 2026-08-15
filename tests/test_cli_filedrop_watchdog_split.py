"""#433 cluster L-B split lock: File-Drop + api-watchdog systemd legs were
extracted VERBATIM from airuleset.py into cli_filedrop_watchdog.py behind a
single facade re-export. This locks the extraction so a later edit/merge cannot
silently break it:

  * every moved name re-exports IDENTICALLY (airuleset.X is leaf.X), incl.
    SUBCOMMANDS["share"]/["filedrop"] resolving to the leaf functions;
  * the leaf is SELF-CONTAINED (no module-level `import airuleset`) and imports
    standalone in a fresh process -- the facade imports it DURING airuleset.py's
    own init, so a module-level back-import would be a circular-import hazard
    (internals note 1483);
  * the moved functions resolve their helper BACK-REFS via the LEAF's own globals
    (mutation teeth: patching the leaf lands, patching the facade is a no-op) --
    so a future edit that re-routes a back-ref through `airuleset.X` flips these;
  * the resident callers that STAY in airuleset.py (_current_remote_host_entry,
    _validate_filedrop) keep resolving the moved names through the facade.
"""
import ast
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path

import airuleset
import cli_filedrop_watchdog as fw

REPO = Path(__file__).resolve().parent.parent

# The 31 names the facade re-exports (8 filedrop-package aliases + 2 File-Drop
# service-unit paths + 4 api-watchdog unit paths + 17 install/CLI functions).
MOVED = [
    "FILEDROP_PORT", "FILEDROP_DEFAULT_PORT", "FILEDROP_PORT_FILE",
    "filedrop_persisted_port", "filedrop_host_ip", "filedrop_bind_ips",
    "filedrop_url", "FILEDROP_DIR",
    "FILEDROP_SERVICE_TEMPLATE", "FILEDROP_SERVICE_DEST",
    "WATCHDOG_SERVICE_TEMPLATE", "WATCHDOG_TIMER_TEMPLATE",
    "WATCHDOG_SERVICE_DEST", "WATCHDOG_TIMER_DEST",
    "_xdg_runtime_env", "_run_systemctl", "_whoami", "_render_filedrop_unit",
    "_choose_filedrop_port", "_filedrop_is_live", "_wait_filedrop_live",
    "_restart_filedrop_service", "setup_filedrop_service", "maybe_setup_filedrop",
    "_filedrop_serve", "cmd_share", "_filedrop_status", "cmd_filedrop",
    "watchdog_disable_marker", "setup_watchdog_service", "maybe_setup_watchdog",
]
MOVED_FUNCS = [
    "_xdg_runtime_env", "_run_systemctl", "_whoami", "_render_filedrop_unit",
    "_choose_filedrop_port", "_filedrop_is_live", "_wait_filedrop_live",
    "_restart_filedrop_service", "setup_filedrop_service", "maybe_setup_filedrop",
    "_filedrop_serve", "cmd_share", "_filedrop_status", "cmd_filedrop",
    "watchdog_disable_marker", "setup_watchdog_service", "maybe_setup_watchdog",
]
MOVED_CONSTS = [
    "FILEDROP_SERVICE_TEMPLATE", "FILEDROP_SERVICE_DEST",
    "WATCHDOG_SERVICE_TEMPLATE", "WATCHDOG_TIMER_TEMPLATE",
    "WATCHDOG_SERVICE_DEST", "WATCHDOG_TIMER_DEST",
]


class TestFacadeReExportIdentity(unittest.TestCase):
    def test_all_moved_names_reexport_identically(self):
        for n in MOVED:
            self.assertTrue(hasattr(fw, n), "leaf missing %s" % n)
            self.assertTrue(hasattr(airuleset, n), "facade missing %s" % n)
            self.assertIs(getattr(airuleset, n), getattr(fw, n),
                          "airuleset.%s is not the leaf object -- facade re-export broken" % n)

    def test_subcommands_wire_to_the_leaf_functions(self):
        self.assertIs(airuleset.SUBCOMMANDS["filedrop"], fw.cmd_filedrop)
        self.assertIs(airuleset.SUBCOMMANDS["share"], fw.cmd_share)


class TestMovedNamesNoLongerDefinedInAiruleset(unittest.TestCase):
    """The moved defs must exist ONLY in the leaf now -- a stray re-definition in
    airuleset.py (e.g. a bad merge) would shadow the facade re-export."""

    def _airuleset_toplevel(self):
        tree = ast.parse((REPO / "airuleset.py").read_text())
        funcs, consts = set(), set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                funcs.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts.add(t.id)
        return funcs, consts

    def test_no_moved_function_is_redefined_in_airuleset(self):
        funcs, _ = self._airuleset_toplevel()
        clash = sorted(set(MOVED_FUNCS) & funcs)
        self.assertEqual(clash, [], "moved functions still defined in airuleset.py: %s" % clash)

    def test_no_moved_const_is_reassigned_in_airuleset(self):
        _, consts = self._airuleset_toplevel()
        clash = sorted(set(MOVED_CONSTS) & consts)
        self.assertEqual(clash, [], "moved consts still assigned in airuleset.py: %s" % clash)


class TestLeafIsSelfContained(unittest.TestCase):
    def test_no_module_level_import_airuleset(self):
        tree = ast.parse((REPO / "cli_filedrop_watchdog.py").read_text())
        for node in tree.body:  # MODULE level only -- a lazy in-body import is fine
            if isinstance(node, ast.Import):
                self.assertNotIn("airuleset", [a.name for a in node.names],
                                 "leaf has a module-level `import airuleset` (circular-import hazard)")
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "airuleset",
                                    "leaf has a module-level `from airuleset import ...`")

    def test_leaf_imports_standalone_in_a_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import cli_filedrop_watchdog as m; "
             "assert m.setup_watchdog_service and m.cmd_filedrop and m.setup_filedrop_service; "
             "assert m.FILEDROP_PORT is not None and m.WATCHDOG_SERVICE_DEST is not None"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_airuleset_and_leaf_reexport_identity_in_a_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import airuleset, cli_filedrop_watchdog as fw; "
             "assert airuleset.setup_filedrop_service is fw.setup_filedrop_service; "
             "assert airuleset.SUBCOMMANDS['filedrop'] is fw.cmd_filedrop; "
             "print('OK')"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


class TestBackRefSeamTeeth(unittest.TestCase):
    """Moved functions resolve helper back-refs via the LEAF's globals, not
    airuleset's. Each teeth-pair proves it: the leaf patch LANDS, the facade
    patch is a silent NO-OP (so a facade-targeted test would wrongly pass)."""

    def test_render_unit_resolves_bind_ips_via_leaf_not_facade(self):
        with m.patch.object(fw, "filedrop_bind_ips", return_value=["9.9.9.9"]):
            unit = fw._render_filedrop_unit(8788)
        self.assertIn("9.9.9.9", unit,
                      "leaf-patched filedrop_bind_ips did not land -> back-ref not via leaf")
        with m.patch.object(airuleset, "filedrop_bind_ips", return_value=["9.9.9.9"]):
            unit2 = fw._render_filedrop_unit(8788)
        self.assertNotIn("9.9.9.9", unit2,
                         "facade-patched filedrop_bind_ips landed -> a facade patch would silently pass")

    def test_choose_port_resolves_persisted_helper_via_leaf_not_facade(self):
        leaf_mock = m.Mock(return_value=8791)
        with m.patch.object(fw, "filedrop_persisted_port", leaf_mock), \
             m.patch.object(fw, "_run_systemctl", lambda a: (3, "inactive", "")):
            self.assertEqual(fw._choose_filedrop_port("127.0.0.1"), 8791)
        self.assertTrue(leaf_mock.called,
                        "leaf-patched filedrop_persisted_port was not consulted -> back-ref not via leaf")
        facade_mock = m.Mock(return_value=8791)
        with m.patch.object(airuleset, "filedrop_persisted_port", facade_mock), \
             m.patch.object(fw, "_run_systemctl", lambda a: (3, "inactive", "")):
            fw._choose_filedrop_port("127.0.0.1")
        self.assertFalse(facade_mock.called,
                         "facade-patched filedrop_persisted_port was consulted -> a facade patch would silently pass")

    def test_setup_watchdog_drives_the_leaf_run_systemctl(self):
        calls = []
        with m.patch.object(fw, "_run_systemctl",
                            side_effect=lambda a: (calls.append(list(a)), (0, "", ""))[1]), \
             m.patch.object(fw, "watchdog_disable_marker", return_value=Path("/nonexistent")), \
             m.patch.object(Path, "exists", return_value=True), \
             m.patch.object(Path, "write_text"), \
             m.patch.object(Path, "mkdir"), \
             m.patch.object(Path, "read_text", return_value=""), \
             m.patch("subprocess.run"):
            fw.setup_watchdog_service()
        self.assertTrue(any("daemon-reload" in c for c in calls),
                        "setup_watchdog_service did not drive the LEAF's _run_systemctl")

    def test_resident_caller_resolves_whoami_via_facade(self):
        # _current_remote_host_entry STAYS in airuleset.py; it must resolve the
        # MOVED _whoami through the facade re-export (patching airuleset._whoami lands).
        fake = m.Mock(return_value="nobody-xyz-not-a-host")
        with m.patch.object(airuleset, "_whoami", fake):
            airuleset._current_remote_host_entry()
        self.assertTrue(fake.called,
                        "_current_remote_host_entry (resident) did not resolve the moved _whoami via the facade")


if __name__ == "__main__":
    unittest.main()
