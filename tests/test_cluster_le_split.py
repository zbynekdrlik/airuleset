"""#433 cluster L-E split lock — cli_fleet.py (the constants-only fleet leaf:
REMOTE_HOSTS + AUTHORITY_PROFILES + AUTHORITY_BY_USER) and cli_remote.py (the
remote-deploy / push / Soniox-provisioning function group).

This was a byte-verbatim module extraction (no behavior change), so instead of
a RED->GREEN regression pair it ships a split-lock test with mutation teeth. It
pins the invariants that keep the extraction correct AND that a future edit
cannot silently break:

  1. `import airuleset` stays clean in a FRESH subprocess, and importing either
     leaf FIRST (before airuleset) is also clean — a circular-import regression
     would blow up exactly here.
  2. The dependency DAG is one-directional: NEITHER leaf has a MODULE-LEVEL
     `import airuleset`, and cli_remote does NOT module-level-import cli_fleet
     (the fleet constants are read via the `airuleset` facade at call time,
     precisely so `patch.object(airuleset, "REMOTE_HOSTS"/"AUTHORITY_BY_USER")`
     tests stay live — the L2 no-op-mock lesson). cli_fleet is a pure leaf
     (imports nothing).
  3. Every moved name is the SAME object at `airuleset.<name>` and at its leaf —
     a dead `patch.object(airuleset, "<name>")` seam is exactly this bug.
  4. The deferred-import couplings are LIVE: patching the resident/promoted
     value on `airuleset` changes the moved function's behavior (a mutant
     reverting a deferred `airuleset.X` read to a frozen/direct copy is caught).
"""

import ast
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_fleet  # noqa: E402
import cli_remote  # noqa: E402

# The hand-maintained move checklist: the reviewer diffs this against the
# facade import blocks in airuleset.py.
FLEET_NAMES = [
    "REMOTE_HOSTS",
    "AUTHORITY_PROFILES",
    "AUTHORITY_BY_USER",
]
REMOTE_NAMES = [
    "REMOTE_DEPLOY_TIMEOUT_S",
    "SONIOX_KEY_SOURCE",
    "_soniox_key_line",
    "provision_subdev_soniox_key",
    "_SSH_AUTH_DENIED_RX",
    "_is_ssh_auth_failure",
    "_SSH_TRANSIENT_RX",
    "SSH_RETRY_MAX_ATTEMPTS",
    "SSH_CONTROL_PERSIST_S",
    "_is_ssh_transient_failure",
    "_ssh_retry_backoff_s",
    "_ssh_control_dir_for_push",
    "_ssh_multiplex_opts",
    "_redacted_ssh_cmd",
    "_HOME_AUDIT_MARKER",
    "_shared_remote_host_ips",
    "_remote_cmd_with_home_audit",
    "_parse_home_audit_output",
    "_parse_home_names",
    "_home_listing_trustworthy",
    "unregistered_home_accounts",
    "_deploy_to_all_remotes",  # #633: cmd_push's extracted deploy loop
    "cmd_push",
]


def _module_level_imports(path):
    """Every module a file imports at MODULE level (top-of-file), as a set of
    dotted roots. Nested (in-body) imports are deliberately excluded."""
    tree = ast.parse((REPO / path).read_text())
    mods = set()
    for node in tree.body:  # module level ONLY
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class TestFreshSubprocessImport(unittest.TestCase):
    def _run(self, code):
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])

    def test_import_airuleset_clean(self):
        self._run("import airuleset")

    def test_import_cli_fleet_standalone(self):
        self._run("import cli_fleet as f; assert f.REMOTE_HOSTS and f.AUTHORITY_BY_USER")

    def test_import_cli_remote_standalone(self):
        self._run("import cli_remote as r; assert r.REMOTE_DEPLOY_TIMEOUT_S")

    def test_import_leaves_first_is_clean(self):
        # importing either leaf BEFORE airuleset must not deadlock or fail on
        # a cycle (cli_remote's airuleset couplings are all deferred).
        self._run("import cli_fleet; import cli_remote; import airuleset")


class TestDependencyDag(unittest.TestCase):
    def test_cli_fleet_is_a_pure_leaf(self):
        # constants-only, zero imports: no airuleset, no cli_remote, nothing.
        self.assertEqual(_module_level_imports("cli_fleet.py"), set())

    def test_cli_remote_does_not_import_airuleset_at_module_level(self):
        # the REMOTE_HOSTS/AUTHORITY_BY_USER/cmd_install/read_file_safe
        # couplings are deferred to call time; a module-level airuleset import
        # is the import-cycle hazard this split deliberately avoids.
        self.assertNotIn("airuleset", _module_level_imports("cli_remote.py"))

    def test_cli_remote_does_not_import_cli_fleet_at_module_level(self):
        # cli_remote reads the fleet constants via the `airuleset` facade (so
        # patch.object(airuleset, ...) stays live), NEVER cli_fleet directly.
        self.assertNotIn("cli_fleet", _module_level_imports("cli_remote.py"))


class TestFacadeIdentity(unittest.TestCase):
    def test_fleet_names_are_same_object(self):
        for n in FLEET_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(cli_fleet, n),
                          "airuleset.%s is not cli_fleet.%s" % (n, n))

    def test_remote_names_are_same_object(self):
        for n in REMOTE_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(cli_remote, n),
                          "airuleset.%s is not cli_remote.%s" % (n, n))

    def test_no_leftover_definition_in_airuleset_source(self):
        # the promoted constants must no longer be DEFINED in airuleset.py —
        # only re-exported. A stray `REMOTE_HOSTS = [` / `AUTHORITY_BY_USER = {`
        # would mean the move left a second source of truth.
        src = (REPO / "airuleset.py").read_text()
        self.assertNotIn("\nREMOTE_HOSTS = [", src)
        self.assertNotIn("\nAUTHORITY_BY_USER = {", src)
        self.assertNotIn("\nAUTHORITY_PROFILES = (", src)
        self.assertNotIn("\ndef cmd_push(", src)


class TestDeferredCouplingsAreLive(unittest.TestCase):
    """Mutation teeth: patch the resident/promoted value on airuleset and prove
    the moved function reads it at call time. A mutant that reverts a deferred
    `airuleset.X` read to a frozen/direct copy fails these."""

    def test_shared_host_ips_reflects_patched_remote_hosts(self):
        fake = [
            {"name": "a", "host": "10.0.0.1", "user": "u1"},
            {"name": "b", "host": "10.0.0.1", "user": "u2"},  # same IP -> shared
            {"name": "c", "host": "10.0.0.2", "user": "u3"},  # solo
        ]
        with m.patch.object(airuleset, "REMOTE_HOSTS", fake):
            shared = airuleset._shared_remote_host_ips()
        self.assertEqual(shared, {"10.0.0.1"})

    def test_unregistered_home_accounts_reflects_patched_remote_hosts(self):
        fake = [{"name": "a", "host": "1.2.3.4", "user": "known"}]
        with m.patch.object(airuleset, "REMOTE_HOSTS", fake):
            gap = airuleset.unregistered_home_accounts("1.2.3.4", "known\nnewbie\n")
        self.assertEqual(gap, ["newbie"])

    def test_soniox_target_filter_reflects_patched_authority_registry(self):
        # a fake account is a Soniox target ONLY because the PATCHED registry
        # makes it a stream account — proves the deferred AUTHORITY_BY_USER read.
        src = Path(tempfile.mkdtemp()) / ".env"
        src.write_text("SONIOX_API_KEY=ZZ-sentinel\n")
        host = {"name": "faux@x", "host": "9.9.9.9", "user": "faux-acct"}
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", {"faux-acct": "branch-merge"}):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=[host], run=run, source=src)
        self.assertEqual(failed, [])          # succeeded -> it WAS a target
        self.assertTrue(calls, "faux-acct must be contacted (patched registry)")

    def test_soniox_key_line_reflects_patched_read_file_safe(self):
        # _soniox_key_line reads via airuleset.read_file_safe (deferred). A
        # mutant reading the file directly would see the on-disk content, not
        # the patched reader's, and miss the sentinel key.
        src = Path(tempfile.mkdtemp()) / ".env"
        src.write_text("IGNORED=1\n")
        with m.patch.object(airuleset, "read_file_safe",
                            return_value="OTHER=1\nSONIOX_API_KEY=ZZ-sentinel\n"):
            line = airuleset._soniox_key_line(src)
        self.assertEqual(line, "SONIOX_API_KEY=ZZ-sentinel")


if __name__ == "__main__":
    unittest.main()
