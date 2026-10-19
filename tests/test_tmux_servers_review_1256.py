"""#1256 review round (2026-10-19) — watchdog side.

1. A target that exits between the scan and the SIGUSR1 must not crash the
   sweep; a target that is no longer a tmux server is never signalled; an
   unknown socket owner (list-sessions failed) means "do nothing".
2. A dry run, or a caller passing no `logs`, never uses up the log-once memo.
3. Under a test run the default `kill` is refused (the #1136/#1245 pattern):
   tests drive the inventory with a fake `run` but the check reads the real
   /proc and would otherwise SIGUSR1 a real server.
4. #318 recovery tries the Claude host first — only among servers bound to the
   path — and the order is proven through `_pane_inventory_raw`, not by source.
5. A requery after a repoint that comes back empty keeps the good inventory.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock as m

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

from watchdog import tmux_io  # noqa: E402
from watchdog import tmux_servers as ts  # noqa: E402
import test_tmux_servers_1256 as base  # noqa: E402  (module import: no re-collection)
import test_tmux_socket_recovery as rec  # noqa: E402


class _Case(base._FakeProcCase):
    def check(self, owner_out, kill, dry_run=False, logs=None, memo=None):
        return ts.duplicate_check(lambda argv: owner_out, logs=logs, dry_run=dry_run,
                                  path=self.PATH, proc_root=self.p.root, kill=kill,
                                  memo_path=memo)


class KillRace(_Case):
    def test_target_gone_before_the_signal_is_logged_not_raised(self):
        self.two_servers(claude_under=(200,))

        def kill(pid, sig):
            raise ProcessLookupError(pid)

        logs = []
        self.assertEqual(self.check("100\n", kill, logs=logs), "gone")
        self.assertTrue(any("gone" in ln for ln in logs), logs)

    def test_target_no_longer_tmux_is_never_signalled(self):
        self.two_servers(claude_under=(200,))
        Path(self.p.root, "200", "comm").write_text("bash\n")   # pid reused
        sent = []
        self.check("100\n", lambda pid, sig: sent.append(pid))
        self.assertEqual(sent, [])

    def test_unknown_owner_does_nothing(self):
        self.two_servers(claude_under=(200,))
        sent = []
        self.assertEqual(self.check("", lambda pid, sig: sent.append(pid)), "unknown")
        self.assertEqual(sent, [])


class MemoOnlyOnARealLoggedPass(_Case):
    def test_dry_run_and_logless_calls_leave_no_memo(self):
        self.two_servers(claude_under=(100, 200))
        memo = os.path.join(self.p.root, "memo")
        self.check("100\n", None, dry_run=True, logs=[], memo=memo)
        self.check("100\n", None, logs=None, memo=memo)
        self.assertFalse(os.path.exists(memo))
        logs = []
        self.check("100\n", None, logs=logs, memo=memo)
        self.assertTrue(logs)


class DefaultKillRefusedUnderTests(_Case):
    def test_no_real_signal_without_an_injected_kill(self):
        self.two_servers(claude_under=(200,))
        logs = []
        with m.patch.object(ts.os, "kill") as real_kill:
            res = ts.duplicate_check(lambda argv: "100\n", logs=logs, path=self.PATH,
                                     proc_root=self.p.root)
        real_kill.assert_not_called()
        self.assertEqual(res, "refused")
        self.assertTrue(any("refused" in ln for ln in logs), logs)


class RecoveryOrder(base._FakeProcCase):
    def test_only_servers_bound_to_the_path_are_prioritised(self):
        self.p.proc(100, "tmux: server")            # hosts claude, NOT bound
        self.p.proc(101, "claude", ppid=100)
        self.p.proc(200, "tmux: server")
        self.p.listen(200, self.PATH)
        self.assertEqual(ts.recovery_order([200, 100], path=self.PATH,
                                           proc_root=self.p.root), [200, 100])

    def test_inventory_signals_in_recovery_order(self):
        tmux = rec._FakeTmux(server_rows=[(2001, os.getuid(), "tmux: server"),
                                          (1371, os.getuid(), "tmux: server")],
                             recovers_on=2001)
        with rec._ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}), \
                m.patch.object(ts, "recovery_order",
                               side_effect=lambda pids, **k: list(reversed(pids))):
            tmux_io._pane_inventory_raw(tmux, logs=[])
        self.assertEqual([int(c[2]) for c in tmux.kill_calls()], [1371, 2001])


class RequeryKeepsAGoodInventory(unittest.TestCase):
    def test_empty_requery_keeps_the_first_read(self):
        outs = iter(["%7\tclaude\t/home/x\t42\n", ""])

        def run(argv):
            return next(outs) if "list-panes" in argv else ""

        with m.patch.object(ts, "duplicate_check", return_value="repointed"):
            out = tmux_io._pane_inventory_raw(run, logs=[])
        self.assertIn("%7", out)


if __name__ == "__main__":
    unittest.main()
