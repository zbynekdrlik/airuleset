"""#1235 — every managed Linux box must have swap (owner, 14.10.2026).

The crashed box (stream-pp) was provisioned by a project session, out of the
fleet's reach. A path-scoped user rule reaches every project session the
moment it touches a provisioning artifact, and the fleet's own swap audit
stops reading a missing swap as normal.
"""
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_disk_guard_root as g   # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_situational_injection import injected, run as run_hook   # noqa: E402

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
                       "FAILS", "defect", "never", "#1235", "2-8 GB", "container"):
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
        self.assertIn("WARN", line)
        self.assertIn("MISSING", line)
        self.assertIn("#1235", line)

    def test_a_skipped_create_is_never_reported_as_applied(self):
        # review A of 1235: the script skipped on low disk with rc 0, and
        # install printed "swap: applied" on a box with no swap
        def run(argv, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")
        line = g.provision_swap(run=run, meminfo_text=NO_SWAP,
                                meminfo_after_text=NO_SWAP)
        self.assertNotIn("applied", line)
        self.assertIn("MISSING", line)
        self.assertIn("DEFECT", line)

    def test_free_space_is_checked_only_when_a_file_must_be_created(self):
        # review B: an inactive full-size /swapfile needs only swapon
        script = g.render_swap_setup_script(8)
        self.assertGreater(script.index("insufficient free space"),
                           script.index("if [ ! -f %s ]" % g.SWAPFILE_PATH))

    def test_the_low_disk_skip_exits_non_zero(self):
        script = g.render_swap_setup_script(8)
        skip = script.split("insufficient free space", 1)[1].split("\n", 1)[0]
        self.assertIn("exit 1", skip)


class TestSwapTriggers(unittest.TestCase):
    """review B of 1235: a path rule loads only on a Read in the main session;
    the trigger rows reach Write/Edit and Bash, workers included."""

    BODY = "Every Linux box MUST have active swap"

    def _fires(self, tool, tool_input):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = injected(run_hook(tool_input, tool_name=tool,
                                    session_id="swap-" + uuid.uuid4().hex, tmpdir=tmp))
        return bool(ctx) and self.BODY in ctx

    def test_bash_shapes_that_find_or_build_swap(self):
        for cmd in ("swapon --show", "sudo mkswap /swapfile",
                    "ssh root@box 'free -h'", "dmesg | grep -i oom-kill",
                    "journalctl -k | grep 'Out of memory'",
                    "hcloud server create --name box --type cx33",
                    # review B of the fixes: case and recall
                    "dmesg -T | grep -i oom", "cat /proc/swaps", "swapoff -a",
                    'journalctl -k | grep -i "out of memory"',
                    "sudo fallocate -l 4G /swapfile", "ssh box free"):
            self.assertTrue(self._fires("Bash", {"command": cmd}), cmd)

    def test_writing_a_provisioning_file(self):
        for path in ("/home/u/devel/p/scripts/provision-box.sh",
                     "/home/u/devel/p/deploy/user-data",
                     "/home/u/devel/p/cloud-config.yaml",
                     "/home/u/devel/p/ansible/roles/base/tasks/main.yml",
                     "/home/u/devel/p/scripts/avsync-dev2-install.sh"):
            self.assertTrue(self._fires("Write", {"file_path": path, "content": "x"}),
                            path)

    def test_unrelated_actions_stay_quiet(self):
        self.assertFalse(self._fires("Bash", {"command": "ls -la"}))
        self.assertFalse(self._fires("Write", {"file_path": "/h/p/src/main.rs",
                                               "content": "fn main() {}"}))


if __name__ == "__main__":
    unittest.main()
