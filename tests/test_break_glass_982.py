"""Break-glass owner access to the controller (#982).

Tests the three managed deliverables:
  (1) docs/break-glass.md runbook exists and is <=15 lines with key content.
  (2) Managed fail2ban ignoreip — render, provision, status.
  (3) Managed owner key — status checks.
  (4) DNS resolution status for controller names.

Plus data-constant drift locks for OWNER_BREAK_GLASS_IPS and
OWNER_BREAK_GLASS_KEYS in cli_fleet.py.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_disk_guard_root as g  # noqa: E402
import cli_fleet  # noqa: E402


def _patch_box_class(cls):
    """Patch the box-class lookup at the import site inside
    cli_disk_guard_root functions (they import from watchdog.reaper)."""
    return mock.patch("watchdog.reaper.default_box_class",
                      return_value=cls)


# ---------------------------------------------------------------------------
# (1) Runbook
# ---------------------------------------------------------------------------
class TestRunbook(unittest.TestCase):

    def test_break_glass_md_exists(self):
        path = REPO / "docs" / "break-glass.md"
        self.assertTrue(path.exists(), "docs/break-glass.md must exist")

    def test_break_glass_md_is_at_most_35_lines(self):
        path = REPO / "docs" / "break-glass.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 35,
                             "break-glass.md must be <=35 lines")

    def test_break_glass_md_has_key_content(self):
        text = (REPO / "docs" / "break-glass.md").read_text()
        self.assertIn("ar.newlevel.media", text,
                      "must mention the primary DNS name")
        self.assertIn("tmux attach", text, "must mention tmux attach")
        self.assertIn("zbynek-windows", text, "must mention the key name")
        self.assertIn("airuleset@airuleset", text,
                      "must mention MagicDNS fallback")
        self.assertIn("100.101.214.103", text,
                      "must show the tailscale IP")
        self.assertIn("159.69.209.249", text,
                      "must show the public IP (#985)")

    def test_break_glass_md_any_device_section_comes_first(self):
        """#985: the 'from any device' SSH password section must appear
        BEFORE the laptop/tailscale/key section."""
        text = (REPO / "docs" / "break-glass.md").read_text()
        any_device_pos = text.find("any device")
        laptop_pos = text.find("laptop")
        self.assertGreater(any_device_pos, -1,
                           "must have 'any device' section")
        self.assertGreater(laptop_pos, -1,
                           "must have 'laptop' section")
        self.assertLess(any_device_pos, laptop_pos,
                        "'any device' must come BEFORE 'laptop'")

    def test_break_glass_md_has_password_rotation(self):
        """#985: the runbook must document password rotation."""
        text = (REPO / "docs" / "break-glass.md").read_text()
        self.assertIn("chpasswd", text,
                      "must mention chpasswd for rotation")
        self.assertIn("secret show", text,
                      "must mention secret show for re-delivery")

    def test_machine_identities_has_pointer(self):
        text = (REPO / "modules" / "core" / "machine-identities.md").read_text()
        self.assertIn("break-glass", text,
                      "machine-identities must point to the break-glass runbook")

    def test_machine_identities_has_dns_names(self):
        text = (REPO / "modules" / "core" / "machine-identities.md").read_text()
        self.assertIn("ar.newlevel.media", text)
        # The stale "has no MagicDNS name" must be gone.
        self.assertNotIn("has no MagicDNS name", text)


# ---------------------------------------------------------------------------
# (2) Managed fail2ban ignoreip — rendering
# ---------------------------------------------------------------------------
class TestOwnerIgnoreipRender(unittest.TestCase):

    def test_render_has_specific_ips(self):
        content = g.render_owner_ignoreip(ips=("100.118.105.43",))
        self.assertIn("100.118.105.43", content)
        self.assertIn("127.0.0.1/8", content)
        self.assertIn("[DEFAULT]", content)
        self.assertIn("ignoreip", content)
        # MUST NOT carry the whole CGNAT range.
        self.assertNotIn("100.64.0.0/10", content)

    def test_render_uses_fleet_default(self):
        content = g.render_owner_ignoreip()
        for ip in cli_fleet.OWNER_BREAK_GLASS_IPS:
            self.assertIn(ip, content)

    def test_owner_ignoreip_path_is_60(self):
        self.assertTrue(g.OWNER_IGNOREIP_PATH.startswith(
            "/etc/fail2ban/jail.d/60-"))


# ---------------------------------------------------------------------------
# (2) Managed fail2ban ignoreip — provisioning
# ---------------------------------------------------------------------------
class TestProvisionIgnoreip(unittest.TestCase):

    def test_skipped_on_non_controller(self):
        with _patch_box_class("workstation"):
            result = g.provision_owner_ignoreip()
        self.assertIn("skipped", result)

    def test_skipped_when_no_fail2ban(self):
        import shutil
        with _patch_box_class("controller"), \
             mock.patch.object(shutil, "which", return_value=None):
            result = g.provision_owner_ignoreip()
        self.assertIn("skipped", result)
        self.assertIn("fail2ban", result)

    def test_skipped_when_no_sudo(self):
        def fake_run(argv, **kw):
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
        self.assertIn("skipped", result)
        self.assertIn("sudo", result)

    def test_applies_on_controller_with_sudo(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
        self.assertIn("applied", result)
        self.assertIn(dest, result)
        # Verify the ORDERED sequence: mkdir, tee, chmod, mv, reload.
        verbs = []
        for call in calls:
            for arg in call:
                if arg in ("mkdir", "tee", "chmod", "mv", "fail2ban-client"):
                    verbs.append(arg)
                    break
        self.assertEqual(verbs, ["mkdir", "tee", "chmod", "mv",
                                 "fail2ban-client"])

    def test_failed_mv_returns_failed_and_cleans_tmp(self):
        call_log = []

        def fake_run(argv, **kw):
            call_log.append(list(argv))
            rc = 0
            # Make mv fail
            if "mv" in argv:
                rc = 1

            class R:
                returncode = rc
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
        self.assertIn("FAILED", result)
        self.assertIn("mv", result)
        # Should have attempted cleanup (rm of tmp).
        rm_calls = [c for c in call_log if "rm" in c]
        self.assertTrue(rm_calls, "should clean up tmp on mv failure")

    def test_failed_reload_returns_failed_with_explanation(self):
        def fake_run(argv, **kw):
            rc = 0
            if "fail2ban-client" in argv:
                rc = 1

            class R:
                returncode = rc
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
        self.assertIn("FAILED", result)
        self.assertIn("reload", result)
        self.assertIn("NOT live", result)

    def test_unchanged_file_short_circuits(self):
        """MEDIUM-4 fix: byte-identical file skips write + reload.
        Uses the dest seam for hermeticity (#982 fix-forward)."""
        content = g.render_owner_ignoreip()
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
        self.assertIn("unchanged", result)
        # No calls at all — short-circuited before the sudo probe.
        self.assertEqual(calls, [])

    def test_unchanged_via_dest_seam(self):
        """Unchanged branch exercised through the dest seam, not mocks —
        hermetic on any box regardless of /etc state (#982 fix-forward)."""
        content = g.render_owner_ignoreip()
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        import shutil
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-owner-ignoreip.conf")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            with _patch_box_class("controller"), \
                 mock.patch.object(shutil, "which",
                                   return_value="/usr/bin/fail2ban-client"):
                result = g.provision_owner_ignoreip(run=fake_run, dest=dest)
            self.assertIn("unchanged", result)
            self.assertIn(dest, result)
            # No calls at all — short-circuited before the sudo probe.
            self.assertEqual(calls, [])


# ---------------------------------------------------------------------------
# (2) + (3) Status checks
# ---------------------------------------------------------------------------
class TestIgnoreipStatus(unittest.TestCase):

    def test_non_controller_is_ok(self):
        with _patch_box_class("workstation"):
            ok, msg = g.check_owner_ignoreip_status()
        self.assertTrue(ok)
        self.assertIn("n/a", msg)

    def test_missing_dropin_is_red(self):
        with _patch_box_class("controller"), \
             mock.patch("os.path.isfile", return_value=False):
            ok, msg = g.check_owner_ignoreip_status()
        self.assertFalse(ok)
        self.assertIn("MISSING", msg)

    def test_present_with_all_ips_is_ok(self):
        content = g.render_owner_ignoreip()
        with _patch_box_class("controller"), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch("builtins.open",
                        mock.mock_open(read_data=content)):
            ok, msg = g.check_owner_ignoreip_status()
        self.assertTrue(ok)
        self.assertIn("OK", msg)

    def test_present_but_missing_ip_is_red(self):
        with _patch_box_class("controller"), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch("builtins.open",
                        mock.mock_open(
                            read_data="[DEFAULT]\nignoreip = 127.0.0.1/8\n")):
            ok, msg = g.check_owner_ignoreip_status()
        self.assertFalse(ok)
        self.assertIn("INCOMPLETE", msg)


class TestOwnerKeyStatus(unittest.TestCase):

    def test_non_controller_is_ok(self):
        with _patch_box_class("workstation"):
            ok, msg = g.check_owner_key_status()
        self.assertTrue(ok)
        self.assertIn("n/a", msg)

    def test_missing_ak_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            ak = os.path.join(td, "authorized_keys")
            with _patch_box_class("controller"):
                ok, msg = g.check_owner_key_status(
                    authorized_keys_path=ak)
            self.assertFalse(ok)
            self.assertIn("MISSING", msg)

    def test_ak_without_owner_key_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            ak = os.path.join(td, "authorized_keys")
            with open(ak, "w") as fh:
                fh.write("ssh-ed25519 AAAAFAKEkey00 some-other-key\n")
            with _patch_box_class("controller"):
                ok, msg = g.check_owner_key_status(
                    authorized_keys_path=ak)
            self.assertFalse(ok)
            self.assertIn("MISSING key", msg)
            self.assertIn("zbynek-windows", msg)

    def test_ak_with_owner_key_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            ak = os.path.join(td, "authorized_keys")
            with open(ak, "w") as fh:
                for key in cli_fleet.OWNER_BREAK_GLASS_KEYS:
                    fh.write(key + "\n")
            with _patch_box_class("controller"):
                ok, msg = g.check_owner_key_status(
                    authorized_keys_path=ak)
            self.assertTrue(ok)
            self.assertIn("OK", msg)


# ---------------------------------------------------------------------------
# (4) DNS resolution status
# ---------------------------------------------------------------------------
class TestControllerDNS(unittest.TestCase):

    def test_non_controller_returns_empty(self):
        with _patch_box_class("workstation"):
            rows = g.check_controller_dns()
        self.assertEqual(rows, [])

    def test_correct_resolution_is_ok(self):
        """Each name resolves to its per-name expected IP (#985)."""
        def resolve(name):
            return g.CONTROLLER_DNS_EXPECTED.get(name, g.CONTROLLER_TAILSCALE_IP)

        with _patch_box_class("controller"):
            rows = g.check_controller_dns(resolve_fn=resolve)
        for name, ok, msg in rows:
            self.assertTrue(ok, "%s should be OK" % name)
            self.assertIn("OK", msg)
        self.assertEqual(len(rows), len(g.CONTROLLER_DNS_NAMES))

    def test_wrong_ip_is_drift(self):
        def resolve(name):
            return "1.2.3.4"

        with _patch_box_class("controller"):
            rows = g.check_controller_dns(resolve_fn=resolve)
        for name, ok, msg in rows:
            self.assertFalse(ok)
            self.assertIn("DRIFT", msg)

    def test_unresolvable_is_red(self):
        def resolve(name):
            return None

        with _patch_box_class("controller"):
            rows = g.check_controller_dns(resolve_fn=resolve)
        for name, ok, msg in rows:
            self.assertFalse(ok)
            self.assertIn("UNRESOLVABLE", msg)

    def test_multi_address_with_expected_ip_is_ok(self):
        """#985: a multi-homed name is OK when its PER-NAME expected IP
        is in the returned set."""
        def resolve(name):
            expected = g.CONTROLLER_DNS_EXPECTED.get(name, g.CONTROLLER_TAILSCALE_IP)
            return {"1.2.3.4", expected}

        with _patch_box_class("controller"):
            rows = g.check_controller_dns(resolve_fn=resolve)
        for name, ok, msg in rows:
            self.assertTrue(ok, "%s should be OK: %s" % (name, msg))
            self.assertIn("OK", msg)

    def test_multi_address_without_expected_ip_is_drift(self):
        def resolve(name):
            return {"99.99.99.99", "1.2.3.4"}

        with _patch_box_class("controller"):
            rows = g.check_controller_dns(resolve_fn=resolve)
        for name, ok, msg in rows:
            self.assertFalse(ok)
            self.assertIn("DRIFT", msg)

    def test_ar_expects_public_ip(self):
        """#985: ar.newlevel.media expects the PUBLIC IP, not tailscale."""
        self.assertEqual(
            g.CONTROLLER_DNS_EXPECTED["ar.newlevel.media"],
            g.CONTROLLER_PUBLIC_IP)
        self.assertNotEqual(
            g.CONTROLLER_DNS_EXPECTED["ar.newlevel.media"],
            g.CONTROLLER_TAILSCALE_IP)

    def test_magicDNS_expects_tailscale_ip(self):
        """airuleset (MagicDNS) expects the tailscale IP."""
        self.assertEqual(
            g.CONTROLLER_DNS_EXPECTED["airuleset"],
            g.CONTROLLER_TAILSCALE_IP)


# ---------------------------------------------------------------------------
# Data-constant drift locks
# ---------------------------------------------------------------------------
class TestBreakGlassConst(unittest.TestCase):

    def test_ips_is_not_empty(self):
        self.assertTrue(cli_fleet.OWNER_BREAK_GLASS_IPS)

    def test_ips_are_specific_not_cgnat(self):
        for ip in cli_fleet.OWNER_BREAK_GLASS_IPS:
            self.assertNotIn("/10", ip)
            self.assertNotIn("100.64.0.0", ip)

    def test_keys_has_zbynek_windows(self):
        joined = " ".join(cli_fleet.OWNER_BREAK_GLASS_KEYS)
        self.assertIn("zbynek-windows", joined)

    def test_keys_match_owner_pubkeys(self):
        import cli_owner_keys
        for key in cli_fleet.OWNER_BREAK_GLASS_KEYS:
            blob = key.split()[1] if len(key.split()) >= 2 else None
            self.assertIsNotNone(blob)
            found = any(blob in pk for pk in cli_owner_keys.OWNER_PUBKEYS)
            self.assertTrue(found,
                            "break-glass key blob must be in OWNER_PUBKEYS")


# ---------------------------------------------------------------------------
# (5) sshd password login (#985)
# ---------------------------------------------------------------------------
class TestSshdPasswordRender(unittest.TestCase):
    """Render of the sshd password drop-in (#985)."""

    def test_render_has_match_user(self):
        content = g.render_sshd_password_conf()
        self.assertIn("Match User airuleset", content)

    def test_render_has_password_auth_yes(self):
        content = g.render_sshd_password_conf()
        self.assertIn("PasswordAuthentication yes", content)

    def test_render_has_kbd_interactive(self):
        content = g.render_sshd_password_conf()
        self.assertIn("KbdInteractiveAuthentication yes", content)

    def test_render_is_byte_identical_to_live(self):
        """The rendered text must match the live file the supervisor
        already applied (the supervisor read it and posted it as the
        RE-SCOPE comment)."""
        expected = (
            "# airuleset owner directive 2026-09-10: password login for "
            "the airuleset account only (break-glass from any device); "
            "fail2ban sshd jail guards it\n"
            "Match User airuleset\n"
            "    PasswordAuthentication yes\n"
            "    KbdInteractiveAuthentication yes\n"
        )
        self.assertEqual(g.render_sshd_password_conf(), expected)

    def test_render_custom_user(self):
        content = g.render_sshd_password_conf(user="testuser")
        self.assertIn("Match User testuser", content)
        self.assertNotIn("airuleset", content.split("Match User")[1]
                         .split("\n")[0])

    def test_path_is_60_prefix(self):
        self.assertTrue(g.SSHD_PASSWORD_PATH.startswith(
            "/etc/ssh/sshd_config.d/60-"))


class TestSshdPasswordProvision(unittest.TestCase):
    """Provisioning of the sshd password drop-in (#985)."""

    def test_skipped_on_non_controller(self):
        with _patch_box_class("workstation"):
            result = g.provision_sshd_password()
        self.assertIn("skipped", result)

    def test_unchanged_short_circuits(self):
        """Byte-identical file skips write + reload."""
        content = g.render_sshd_password_conf()
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-password.conf")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            with _patch_box_class("controller"):
                result = g.provision_sshd_password(run=fake_run, dest=dest)
        self.assertIn("unchanged", result)
        self.assertEqual(calls, [])

    def test_applies_on_controller_with_sudo(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-password.conf")
            with _patch_box_class("controller"):
                result = g.provision_sshd_password(run=fake_run, dest=dest)
        self.assertIn("applied", result)
        # Must have called sshd -t before mv.
        verbs = []
        for call in calls:
            for arg in call:
                if arg in ("mkdir", "tee", "chmod", "sshd", "mv",
                           "systemctl"):
                    verbs.append(arg)
                    break
        self.assertEqual(verbs, ["mkdir", "tee", "chmod", "sshd", "mv",
                                 "systemctl"])

    def test_sshd_t_failure_blocks_apply(self):
        """If sshd -t fails, the drop-in must NOT be moved into place."""
        calls = []

        def fake_run(argv, **kw):
            calls.append(list(argv))
            rc = 0
            stderr = ""
            if "sshd" in argv:
                rc = 1
                stderr = "bad config"
            class R:
                returncode = rc
                stdout = ""
            R.stderr = stderr
            return R()

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-password.conf")
            with _patch_box_class("controller"):
                result = g.provision_sshd_password(run=fake_run, dest=dest)
        self.assertIn("FAILED", result)
        self.assertIn("sshd -t", result)
        # mv must NOT have been called.
        mv_calls = [c for c in calls if "mv" in c]
        self.assertEqual(mv_calls, [])


class TestSshdPasswordStatus(unittest.TestCase):
    """Status check for the sshd password drop-in (#985)."""

    def test_non_controller_is_na(self):
        with _patch_box_class("workstation"):
            ok, msg = g.check_sshd_password_status()
        self.assertTrue(ok)
        self.assertIn("n/a", msg)

    def test_missing_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "nonexistent.conf")
            with _patch_box_class("controller"):
                ok, msg = g.check_sshd_password_status(dest=dest)
        self.assertFalse(ok)
        self.assertIn("MISSING", msg)

    def test_matching_content_is_ok(self):
        content = g.render_sshd_password_conf()
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-password.conf")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            with _patch_box_class("controller"):
                ok, msg = g.check_sshd_password_status(dest=dest)
        self.assertTrue(ok)
        self.assertIn("OK", msg)
        self.assertIn("sshd password login", msg)

    def test_different_content_is_drift(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "60-airuleset-password.conf")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write("Match User nobody\n    PasswordAuthentication no\n")
            with _patch_box_class("controller"):
                ok, msg = g.check_sshd_password_status(dest=dest)
        self.assertFalse(ok)
        self.assertIn("DRIFT", msg)


if __name__ == "__main__":
    unittest.main()
