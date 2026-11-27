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
from _bazos_usbdisk_sandbox import Sandbox

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


class NewActionsArgv(Script):

    def test_new_actions_refuse_bad_argv_with_2(self):
        for args in (("smart",), ("smart", "../sda"), ("smart", "S1234", "x"),
                     ("readtest",), ("readtest", "S12;rm"), ("readtest", "S1234", "x")):
            with self.subTest(args=args):
                self.assertEqual(self._run(*args).returncode, 2)

    def _section(self, name):
        src = SCRIPT.read_text()
        start = src.index("\n%s)\n" % name)
        return src[start:src.index("\n    ;;\n", start)]

    def test_new_actions_resolve_the_disk_first(self):
        for name in ("smart", "readtest"):
            with self.subTest(name=name):
                sec = self._section(name)
                self.assertIn('D=$(disk_for "$SERIAL")', sec.split("\n")[2])

    def test_readtest_only_reads(self):
        sec = self._section("readtest")
        for needle in ("if=/dev/$D", "of=/dev/null", "iflag=direct"):
            self.assertIn(needle, sec)
        # Live 27.11.: noerror retried a dead region until dd hung in D state.
        self.assertNotIn("noerror", sec)
        self.assertNotIn("of=/dev/$", sec)
        self.assertNotIn('of="/dev/$', sec)


class Behaviour(unittest.TestCase):

    def setUp(self):
        self.sb = Sandbox(self)

    def test_smart_walks_the_d_chain_to_jmicron(self):
        self.sb.set(smart_rc={"auto": 1, "sat": 2, "sntasmedia": 4})
        r = self.sb.run("smart", "S1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SMART via sntjmicron rc=0", r.stdout)
        self.assertNotIn("SMART via sat", r.stdout)
        calls = self.sb.calls("smartctl")
        self.assertEqual(calls[0], ["-a", "/dev/sda"])
        self.assertEqual(calls[-1], ["-a", "-d", "sntjmicron", "/dev/sda"])
        self.assertIn("smart S1234 /dev/sda", self.sb.log_text())

    def test_smart_health_bits_still_count_as_read(self):
        self.sb.set(smart_rc={"auto": 8})
        r = self.sb.run("smart", "S1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sb.calls("smartctl")), 1)

    def test_smart_fails_loud_when_no_type_reads(self):
        self.sb.set(smart_rc={"auto": 2, "sat": 2, "sntasmedia": 2, "sntjmicron": 2})
        r = self.sb.run("smart", "S1234")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.sb.calls("smartctl")), 4)

    def test_root_and_unknown_serials_touch_no_device(self):
        for action in ("smart", "readtest"):
            for serial in ("ROOTSER1", "NOPE1234"):
                with self.subTest(action=action, serial=serial):
                    r = self.sb.run(action, serial)
                    self.assertEqual(r.returncode, 1)
                    for tool in ("smartctl", "dd", "blockdev"):
                        self.assertEqual(self.sb.calls(tool), [])

    def test_readtest_clean_disk(self):
        r = self.sb.run("readtest", "S1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        dd = self.sb.calls("dd")
        self.assertEqual(len(dd), 1)
        for arg in ("if=/dev/sda", "of=/dev/null", "bs=4M", "iflag=direct"):
            self.assertIn(arg, dd[0])
        self.assertFalse([a for a in dd[0] if "noerror" in a], dd[0])
        self.assertIn("bytes=16777216", r.stdout)
        self.assertIn("failed_at=none", r.stdout)
        self.assertIn("kernel_io_errors=0", r.stdout)
        self.assertIn("result=OK", r.stdout)
        self.assertIn("MB/s=", r.stdout)
        self.assertIn("bytes copied", r.stderr)
        self.assertIn("result=OK", self.sb.log_text())

    def test_readtest_stops_at_the_first_read_error(self):
        self.sb.set(dd_fail_at=8388608,
                    kernel="blk_update_request: I/O error, dev sda, sector 16384 op 0x0\n")
        r = self.sb.run("readtest", "S1234")
        self.assertEqual(r.returncode, 1)
        dd = self.sb.calls("dd")
        self.assertEqual(len(dd), 1)
        self.assertFalse([a for a in dd[0] if "noerror" in a], dd[0])
        self.assertIn("failed_at=8388608", r.stdout)
        self.assertIn("bytes=8388608", r.stdout)
        self.assertIn("kernel_io_errors=1", r.stdout)
        self.assertIn("dd_rc=1", r.stdout)
        self.assertIn("result=ERRORS", r.stdout)
        self.assertIn("result=ERRORS", self.sb.log_text())

    def test_readtest_short_read_is_incomplete(self):
        self.sb.set(dd_bytes=4194304)
        r = self.sb.run("readtest", "S1234")
        self.assertEqual(r.returncode, 1)
        self.assertIn("result=INCOMPLETE", r.stdout)

    def test_readtest_refuses_rw_mount(self):
        self.sb.set(mounts={"sda2": "rw,nosuid,nodev"})
        r = self.sb.run("readtest", "S1234")
        self.assertEqual(r.returncode, 1)
        self.assertIn("read-write", r.stderr)
        self.assertEqual(self.sb.calls("dd"), [])

    def test_readtest_allows_ro_mount(self):
        self.sb.set(mounts={"sda1": "ro,nosuid,nodev,noexec"})
        r = self.sb.run("readtest", "S1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sb.calls("dd")), 1)

    def test_list_and_info_show_blkid(self):
        for args in (("list",), ("info", "S1234")):
            with self.subTest(args=args):
                r = self.sb.run(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('/dev/sda1: PARTLABEL="LDM metadata partition"', r.stdout)
                self.assertIn("/dev/sda2:", r.stdout)
        probed = {c[-1] for c in self.sb.calls("blkid")}
        self.assertEqual(probed, {"/dev/sda", "/dev/sda1", "/dev/sda2"})
        for c in self.sb.calls("blkid"):
            self.assertEqual(c[:-1], ["-c", "/dev/null", "-s", "TYPE", "-s", "PARTLABEL", "-s", "LABEL"])


if __name__ == "__main__":
    unittest.main()
