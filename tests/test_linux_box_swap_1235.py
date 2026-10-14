"""#1235 — every managed Linux box must have swap (owner, 14.10.2026).

The crashed box (stream-pp) was provisioned by a project session, out of the
fleet's reach. A path-scoped user rule reaches every project session the
moment it touches a provisioning artifact, and the fleet's own swap audit
stops reading a missing swap as normal.
"""
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_disk_guard_root as g   # noqa: E402

RULE = ROOT / "rules" / "linux-box-swap.md"
NO_SWAP = "MemTotal: 14000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"


def _paths(text):
    head = text.split("---")[1]
    return re.findall(r'^\s*-\s*"([^"]+)"', head, re.M)


def _glob_rx(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


class TestSwapRule(unittest.TestCase):

    def test_rule_is_installed_for_every_account(self):
        profile = (ROOT / "profiles" / "universal.profile").read_text()
        self.assertIn("rules/linux-box-swap.md", profile.split())
        self.assertTrue(RULE.is_file())

    def test_rule_loads_on_real_provisioning_artifacts(self):
        rxs = [_glob_rx(p) for p in _paths(RULE.read_text())]
        for path in (".claude/skills/provision/SKILL.md",            # camera-box
                     ".claude/rules/strih-linux-provisioning.md",    # camera-box
                     "scripts/avsync-watchdog-install.sh",           # camera-box
                     "scripts/setup-environments.sh",                # spinbike
                     "provision.sh", "deploy/cloud-init.yaml",
                     "ansible/roles/base/tasks/main.yml"):
            self.assertTrue(any(rx.match(path) for rx in rxs), path)
        for path in ("src/main.rs", "README.md", "tests/test_x.py"):
            self.assertFalse(any(rx.match(path) for rx in rxs), path)

    def test_rule_says_what_the_owner_ruled(self):
        body = " ".join(RULE.read_text().split())
        for phrase in ("MUST have active swap", "/etc/fstab", "swapon --show",
                       "FAILS", "defect", "never", "#1235"):
            self.assertIn(phrase, body)


class TestFleetSwapIsLoud(unittest.TestCase):

    def test_status_marks_a_missing_swap_as_a_defect(self):
        line = g.swap_status(meminfo_text=NO_SWAP)
        self.assertIn("DEFECT", line)
        self.assertIn("#1235", line)

    def test_status_with_swap_is_unchanged(self):
        line = g.swap_status(meminfo_text="MemTotal: 1 kB\nSwapTotal: 4194300 kB\n"
                                          "SwapFree: 4194300 kB\n")
        self.assertTrue(line.startswith("swap: "))
        self.assertNotIn("DEFECT", line)

    def test_install_without_swap_and_without_sudo_is_loud(self):
        def run(argv, **kw):
            return mock.Mock(returncode=1, stdout="", stderr="a password is required")
        line = g.provision_swap(run=run, meminfo_text=NO_SWAP)
        self.assertIn("⚠", line)
        self.assertIn("MISSING", line)
        self.assertIn("#1235", line)


if __name__ == "__main__":
    unittest.main()
