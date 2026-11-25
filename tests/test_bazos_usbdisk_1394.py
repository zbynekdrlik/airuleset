"""#1394 (owner 25.11.2026): bazos@dev1 handles the USB disks the owner sells.

One root-owned project script, `/usr/local/sbin/bazos-usbdisk`, takes an
action and a disk SERIAL. The #1186 command-scoped sudo grammar gains an
explicit `<path> ARGS` entry: the sudoers rule then omits `""` so the script
gets argv. A plain entry renders exactly as before.
"""
import os
import subprocess
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_account_hardening as hardening
import cli_account_policy as policy

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "root" / "bazos-usbdisk"
ENTRY = "/usr/local/sbin/bazos-usbdisk ARGS"


class Grammar(unittest.TestCase):

    def test_args_entry_is_accepted_for_its_account(self):
        self.assertIsNone(hardening.sudo_script_problem("bazos", ENTRY))

    def test_args_entry_rejects_junk(self):
        for bad in ("/usr/local/sbin/bazos-usbdisk ARGS ARGS",
                    "/usr/local/sbin/bazos-usbdisk *",
                    "/usr/local/sbin/bazos-usbdisk args",
                    "/usr/local/sbin/bazos-usbdisk  ARGS",
                    "/usr/local/sbin/bazos-usbdisk ARGS\n"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(hardening.sudo_script_problem("bazos", bad))

    def test_args_entry_of_another_account_is_foreign(self):
        spec = {"sudo": "commands", "sudo_reason": "x",
                "sudo_commands": [ENTRY]}
        errs = policy.validate_sudo(spec, "baz", declared=("baz", "bazos"))
        self.assertTrue(errs)


class DuplicateForms(unittest.TestCase):

    def test_path_declared_with_and_without_args_is_refused(self):
        spec = {"sudo": "commands", "sudo_reason": "x", "sudo_commands": [
            "/usr/local/sbin/bazos-usbdisk", ENTRY]}
        errs = policy.validate_sudo(spec, "bazos", declared=("bazos",))
        self.assertTrue(any("duplicate" in e for e in errs), errs)


class Render(unittest.TestCase):

    def _render(self, cmds):
        spec = {"sudo": "commands", "sudo_reason": "usb disks",
                "sudo_commands": cmds}
        return hardening.render_sudo_step("bazos", spec)

    def test_args_entry_renders_without_empty_args(self):
        out = self._render([ENTRY])
        self.assertIn("bazos ALL=(root) NOPASSWD: /usr/local/sbin/bazos-usbdisk\n", out)
        self.assertNotIn('bazos-usbdisk ""', out)
        self.assertIn("for P in /usr/local/sbin/bazos-usbdisk; do", out)

    def test_plain_entry_keeps_empty_args(self):
        out = self._render(["/usr/local/sbin/bazos-x"])
        self.assertIn('NOPASSWD: /usr/local/sbin/bazos-x ""', out)


class Declaration(unittest.TestCase):

    def setUp(self):
        self.spec = bootstrap.account_spec("bazos")

    def test_bazos_declares_the_script(self):
        self.assertEqual(self.spec["sudo"], "commands")
        self.assertEqual(self.spec["sudo_commands"], [ENTRY])
        self.assertIn("smartmontools", self.spec["system_packages"])

    def test_declaration_valid(self):
        self.assertEqual(bootstrap.validate_account("bazos", self.spec), [])


class Script(unittest.TestCase):

    def test_parses(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def _run(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                              text=True, env={**os.environ, "BAZOS_USBDISK_DRYRUN": "1"})

    def test_bad_action_and_bad_serial_exit_2(self):
        for args in ((), ("format", "S1234"), ("info", "../sda"),
                     ("info", "S12;rm"), ("wipe", "S1234"),
                     ("wipe", "S1234", "bad/label")):
            with self.subTest(args=args):
                self.assertEqual(self._run(*args).returncode, 2)

    def test_guards_present(self):
        src = SCRIPT.read_text()
        for needle in ('TRAN', '"usb"', 'findmnt -no SOURCE /', 'ro,nosuid,nodev,noexec',
                       '/var/log/bazos-usbdisk.log', 'export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                       'lsblk -sno NAME,TYPE', '/holders', 'mount -t "$t"',
                       'safe_dir', 'DDERR'):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)

    def test_mounted_dir_is_unmounted_before_its_owner_check(self):
        # Live 25.11.: a mounted ntfs/vfat dir shows uid=bazos, so the root:root
        # check must run on the free dir, after umount (and before mount).
        src = SCRIPT.read_text()
        um = src[src.index("\numount)\n"):src.index("\nwipe)\n")]
        self.assertLess(um.index("umount -- "), um.index('safe_dir "$dir"'))
        mo = src[src.index("\nmount)\n"):src.index("\numount)\n")]
        self.assertLess(mo.index("findmnt -rn --mountpoint"), mo.index('safe_dir "$dir"'))


if __name__ == "__main__":
    unittest.main()
