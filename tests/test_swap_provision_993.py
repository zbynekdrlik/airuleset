"""#992/#993 item 9 — the managed `swap` install step.

From the #992 controller-OOM audit (4 GB RAM, zero swap → OOM killed the
watchdog + a push Pass B): a managed box with no swap gets a `/swapfile` sized
= RAM (clamped [2 GB, 8 GB]), chmod 600, mkswap, swapon, and an /etc/fstab line.
Idempotent (a box that already has swap is recognised as done), needs `sudo -n`
(a clear skip line otherwise), and `airuleset.py status` shows `swap: <total>/<used>`.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_disk_guard_root as dg  # noqa: E402


class _Rec:
    """A fake `run` recording argv and returning canned rc per matcher."""
    def __init__(self, rules):
        self.rules = rules      # list of (substr, rc, stdout)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        joined = " ".join(argv)
        for sub, rc, out in self.rules:
            if sub in joined:
                return _R(rc, out)
        return _R(0, "")

    def ran(self, substr):
        return any(substr in " ".join(a) for a in self.calls)


class _R:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _meminfo(mem_kb, swap_kb, swap_free_kb=0):
    return ("MemTotal:       %d kB\n"
            "SwapTotal:      %d kB\n"
            "SwapFree:       %d kB\n" % (mem_kb, swap_kb, swap_free_kb))


class TestRenderSwapScript(TestCase):
    def test_render_carries_the_swapfile_lifecycle(self):
        s = dg.render_swap_setup_script(4)
        for token in ("/swapfile", "mkswap", "swapon", "600", "/etc/fstab"):
            self.assertIn(token, s)

    def test_render_is_idempotent_guarded(self):
        # #993-review: assert the actual GUARDS, not mere substrings — the
        # early-exit when swap is active, the fstab-line dedup, the
        # trailing-newline guard, the free-space check, and the blkid guard.
        s = dg.render_swap_setup_script(2)
        self.assertIn("swapon --show=NAME --noheadings", s)
        self.assertIn("-gt 0", s)                       # already-active early exit
        self.assertIn("grep -qE '^/swapfile", s)        # fstab-line dedup
        self.assertIn("tail -c1 /etc/fstab", s)         # trailing-newline guard
        self.assertIn("df --output=avail", s)           # free-space check
        self.assertIn("blkid", s)                       # non-swap-file guard
        self.assertIn("2G", s)                          # size baked in


class TestSwapSizeClamp(TestCase):
    def test_clamps_below_two_up_to_two(self):
        self.assertEqual(dg.swap_size_gb(1 * 1024 * 1024), 2)

    def test_mid_range_equals_ram(self):
        self.assertEqual(dg.swap_size_gb(4 * 1024 * 1024), 4)

    def test_clamps_above_eight_down_to_eight(self):
        self.assertEqual(dg.swap_size_gb(16 * 1024 * 1024), 8)


class TestProvisionSwap(TestCase):
    def test_present_swap_is_a_noop(self):
        rec = _Rec([])
        msg = dg.provision_swap(run=rec, meminfo_text=_meminfo(4194304, 4194304))
        self.assertIn("present", msg.lower())
        self.assertFalse(rec.ran("mkswap"), "must not create swap when present")

    def test_no_sudo_skips_with_clear_line(self):
        rec = _Rec([("sudo -n true", 1, "")])
        msg = dg.provision_swap(run=rec, meminfo_text=_meminfo(4194304, 0))
        self.assertIn("sudo", msg.lower())
        self.assertIn("skip", msg.lower())
        self.assertFalse(rec.ran("mkswap"))

    def test_creates_swap_when_absent_and_sudo_ok(self):
        rec = _Rec([("sudo -n true", 0, "")])
        msg = dg.provision_swap(run=rec, meminfo_text=_meminfo(4194304, 0))
        self.assertIn("applied", msg.lower())
        # ran the create script via sudo -n bash
        self.assertTrue(any("bash" in " ".join(a) and "sudo" in " ".join(a)
                            for a in rec.calls))

    def test_failure_is_reported_non_fatally(self):
        rec = _Rec([("sudo -n true", 0, ""), ("bash", 1, "")])
        msg = dg.provision_swap(run=rec, meminfo_text=_meminfo(4194304, 0))
        self.assertIn("fail", msg.lower())


class TestSwapStatus(TestCase):
    def test_status_line_shows_total_and_used(self):
        line = dg.swap_status(meminfo_text=_meminfo(4194304, 4193404, 4000000))
        self.assertIn("swap", line.lower())
        # total ~4095 MB present, used ~ (total-free)
        self.assertRegex(line, r"\d")

    def test_status_line_zero_swap(self):
        line = dg.swap_status(meminfo_text=_meminfo(4194304, 0, 0))
        self.assertIn("swap", line.lower())
        self.assertIn("0", line)


if __name__ == "__main__":
    main()
