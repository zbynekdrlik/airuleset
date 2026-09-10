"""#973: system_packages support in the bootstrap renderer.

Tests that:
(a) claudy's rendered script contains the apt step with all 12 packages
    and the dpkg-query idempotency guard.
(b) An account without system_packages renders NO apt-get at all.
(c) bash -n on the rendered script passes.
(d) The rendered script retains set -euo pipefail (idempotent shape).
"""
import subprocess
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_bootstrap as bootstrap  # noqa: E402


# The exact 12 packages that must appear in the claudy bootstrap (#973).
EXPECTED_PACKAGES = [
    "libnss3", "libnspr4", "libatk1.0-0t64", "libatk-bridge2.0-0t64",
    "libatspi2.0-0t64", "libgbm1", "libasound2t64", "libxcomposite1",
    "libxdamage1", "libxext6", "libxfixes3", "libxrandr2",
]


class TestSystemPackagesClaudy(unittest.TestCase):
    """(a) claudy render contains the apt step with all 12 packages."""

    def setUp(self):
        self.script = bootstrap.render_root_bootstrap("claudy")

    def test_all_12_packages_present(self):
        for pkg in EXPECTED_PACKAGES:
            self.assertIn(pkg, self.script,
                          "package %r missing from rendered script" % pkg)

    def test_exactly_12_packages_declared(self):
        self.assertEqual(len(bootstrap.SERVICE_ACCOUNTS["claudy"]["system_packages"]),
                         12)

    def test_dpkg_query_guard(self):
        self.assertIn("dpkg-query -W", self.script)
        self.assertIn("install ok installed", self.script)

    def test_apt_get_install(self):
        self.assertIn("DEBIAN_FRONTEND=noninteractive", self.script)
        self.assertIn("apt-get install -y -q", self.script)
        # Fable review YELLOW: must carry the repo's DPkg lock timeout idiom
        self.assertIn("DPkg::Lock::Timeout", self.script)

    def test_step_8_heading(self):
        self.assertIn("# 8. System packages (idempotent)", self.script)

    def test_readback_system_packages(self):
        self.assertIn("system packages:", self.script)

    def test_need_install_array(self):
        """The script collects missing packages in a NEED_INSTALL array."""
        self.assertIn("NEED_INSTALL=()", self.script)
        self.assertIn("NEED_INSTALL+=(", self.script)

    def test_idempotent_skip_message(self):
        """When all packages present, the script reports 'nothing to do'."""
        self.assertIn("all 12 system packages already installed", self.script)


class TestNoSystemPackages(unittest.TestCase):
    """(b) An account without system_packages renders no apt-get."""

    def test_account_without_packages_no_apt(self):
        """A hypothetical account with no system_packages must produce a
        script with NO apt-get, NO dpkg-query, NO step 8."""
        saved = bootstrap.SERVICE_ACCOUNTS.copy()
        try:
            bootstrap.SERVICE_ACCOUNTS["_test_bare"] = {
                "webterm_sessions": {},
            }
            script = bootstrap.render_root_bootstrap("_test_bare")
            self.assertNotIn("apt-get", script)
            self.assertNotIn("dpkg-query", script)
            self.assertNotIn("# 8. System packages", script)
            self.assertNotIn("system packages:", script)
        finally:
            bootstrap.SERVICE_ACCOUNTS.clear()
            bootstrap.SERVICE_ACCOUNTS.update(saved)

    def test_empty_packages_list_no_apt(self):
        """An account with system_packages=[] also produces no apt step."""
        saved = bootstrap.SERVICE_ACCOUNTS.copy()
        try:
            bootstrap.SERVICE_ACCOUNTS["_test_empty"] = {
                "webterm_sessions": {},
                "system_packages": [],
            }
            script = bootstrap.render_root_bootstrap("_test_empty")
            self.assertNotIn("apt-get", script)
            self.assertNotIn("dpkg-query", script)
        finally:
            bootstrap.SERVICE_ACCOUNTS.clear()
            bootstrap.SERVICE_ACCOUNTS.update(saved)


class TestBashSyntax(unittest.TestCase):
    """(c) bash -n on the rendered script passes."""

    def test_claudy_bash_n(self):
        script = bootstrap.render_root_bootstrap("claudy")
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, "bash -n failed: %s" % r.stderr)

    def test_bare_account_bash_n(self):
        """A no-packages account also passes bash -n."""
        saved = bootstrap.SERVICE_ACCOUNTS.copy()
        try:
            bootstrap.SERVICE_ACCOUNTS["_test_bare"] = {
                "webterm_sessions": {},
            }
            script = bootstrap.render_root_bootstrap("_test_bare")
            r = subprocess.run(["bash", "-n"], input=script,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             "bash -n failed (bare): %s" % r.stderr)
        finally:
            bootstrap.SERVICE_ACCOUNTS.clear()
            bootstrap.SERVICE_ACCOUNTS.update(saved)


class TestIdempotentShape(unittest.TestCase):
    """(d) The rendered script is still idempotent-shaped."""

    def test_set_euo_pipefail(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("set -euo pipefail", script)

    def test_useradd_guard(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("if id", script)

    def test_shebang(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertTrue(script.startswith("#!/usr/bin/env bash"))


class TestHelperFunctions(unittest.TestCase):
    """Direct tests for the new helper functions."""

    def test_render_system_packages_step_none(self):
        self.assertEqual(bootstrap._render_system_packages_step(None), "")

    def test_render_system_packages_step_empty(self):
        self.assertEqual(bootstrap._render_system_packages_step([]), "")

    def test_render_system_packages_readback_none(self):
        self.assertEqual(bootstrap._render_system_packages_readback(None), "")

    def test_render_system_packages_readback_empty(self):
        self.assertEqual(bootstrap._render_system_packages_readback([]), "")

    def test_render_system_packages_step_single(self):
        step = bootstrap._render_system_packages_step(["libfoo"])
        self.assertIn("libfoo", step)
        self.assertIn("dpkg-query", step)
        self.assertIn("apt-get install", step)
        self.assertIn("all 1 system packages", step)

    def test_render_system_packages_readback_single(self):
        rb = bootstrap._render_system_packages_readback(["libfoo"])
        self.assertIn("libfoo", rb)
        self.assertIn("system packages:", rb)


class TestPackageNameValidation(unittest.TestCase):
    """Fable review BLUE: package names validated at render time."""

    def test_valid_names_pass(self):
        # Should not raise
        bootstrap._validate_package_names(["libnss3", "libatk1.0-0t64"])

    def test_invalid_name_with_space_raises(self):
        with self.assertRaises(ValueError):
            bootstrap._validate_package_names(["lib foo"])

    def test_invalid_name_with_quote_raises(self):
        with self.assertRaises(ValueError):
            bootstrap._validate_package_names(["lib'foo"])

    def test_invalid_name_with_semicolon_raises(self):
        with self.assertRaises(ValueError):
            bootstrap._validate_package_names(["libfoo;rm -rf"])

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            bootstrap._validate_package_names([""])

    def test_single_char_raises(self):
        with self.assertRaises(ValueError):
            bootstrap._validate_package_names(["x"])

    def test_all_claudy_packages_valid(self):
        # Should not raise
        bootstrap._validate_package_names(
            bootstrap.SERVICE_ACCOUNTS["claudy"]["system_packages"])


if __name__ == "__main__":
    unittest.main()
