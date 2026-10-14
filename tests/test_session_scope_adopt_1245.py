"""#1245: dev1 froze a 5th time (14.10.2026). #1224 capped a session only at its
next LAUNCH, so the camera-box, songplayer and restreamer sessions from 9-11.10.
still ran uncapped in the tmux server's unit. songplayer's tool grew to 5.6 GB.

Install now ADOPTS every running uncapped `claude` of this account, with its
descendants, into its own transient scope. The scope has the launcher's caps
and OOMPolicy=continue, and it is created through systemd's StartTransientUnit
with PIDs (busctl). The session is not restarted. A claude in a logind session
scope moves too (the user manager moved one out on dev1, live probe).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_session_scope as ss  # noqa: E402

DEV1_KB = 7865772
SMALL_KB = 3911564          # the controller's: the 2 GiB floor applies
UID = 1000
USER_SVC = "/user.slice/user-1000.slice/user@1000.service"
ANCHOR = USER_SVC + "/app.slice/tmux-detach-anchor.service"
CAPPED = USER_SVC + "/app.slice/run-u1.scope"
LOGIND = "/user.slice/user-1000.slice/session-5.scope"


def _launcher_caps_bytes(kb):
    """The launcher's bash arithmetic (`cli_session_scope._HELPER`), in bytes."""
    mx = kb * 40 // 100
    mx = max(mx, 2 * 1024 * 1024)
    return mx * 1024, (mx * 85 // 100) * 1024, (kb * 20 // 100) * 1024


class _Box:
    """A fake /proc + /sys/fs/cgroup, and a fake busctl that does what systemd
    does on StartTransientUnit: it moves the listed pids into the new scope."""

    def __init__(self, base):
        self.proc = Path(base) / "proc"
        self.cg = Path(base) / "cgroup"
        self.proc.mkdir()
        self.cg.mkdir()
        self.calls = []
        self.fail = []          # queued (rc, err) answers for the next calls
        self.move = True        # the fake systemd really moves the pids
        self.on_call = None
        self.lazy = False       # real systemd: the move lands after the call returns
        self.memmax = True      # False: the new scope has no memory controller
        self.pending = None
        self.sleeps = 0
        self.cgroup(ANCHOR, "max")
        self.cgroup(CAPPED, "3221819392")
        self.cgroup(LOGIND, "max")

    def cgroup(self, path, mem_max):
        d = self.cg / path.lstrip("/")
        d.mkdir(parents=True, exist_ok=True)
        if mem_max is not None:
            (d / "memory.max").write_text(mem_max + "\n")

    def proc_add(self, pid, comm, ppid, cgroup, uid=UID, cwd=None):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "stat").write_text("%d (%s) S %d 1 1 0 -1\n" % (pid, comm, ppid))
        (d / "status").write_text("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % (comm, uid, uid, uid, uid))
        (d / "cgroup").write_text("0::%s\n" % cgroup)
        (d / "oom_score_adj").write_text("200\n")
        if cwd:
            os.symlink(cwd, d / "cwd")

    def adj(self, pid):
        return (self.proc / str(pid) / "oom_score_adj").read_text().strip()

    def cgroup_of(self, pid):
        return (self.proc / str(pid) / "cgroup").read_text().strip()[3:]

    _TYPES = {"Description": "s", "PIDs": "au", "MemoryMax": "t", "MemoryHigh": "t",
              "MemorySwapMax": "t", "OOMPolicy": "s"}

    def _parse(self, argv):
        """Parse like busctl does for ``ssa(sv)a(sa(sv))``; None on any mismatch."""
        head = ["busctl", "--user", "call", "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                "StartTransientUnit", "ssa(sv)a(sa(sv))"]
        if argv[:8] != head or len(argv) < 11 or argv[9] != "fail":
            return None
        rest, props = argv[11:], {}
        try:
            for _ in range(int(argv[10])):
                key, typ, rest = rest[0], rest[1], rest[2:]
                if self._TYPES.get(key) != typ:
                    return None
                if typ == "au":
                    n = int(rest[0])
                    props[key], rest = [int(x) for x in rest[1:1 + n]], rest[1 + n:]
                    if len(props[key]) != n:
                        return None
                else:
                    props[key] = int(rest[0]) if typ == "t" else rest[0]
                    rest = rest[1:]
        except (IndexError, ValueError):
            return None
        return (argv[8], props) if rest == ["0"] else None

    def run(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        if self.on_call:
            self.on_call(len(self.calls))
        if self.fail:
            rc, err = self.fail.pop(0)
            return rc, "", err
        parsed = self._parse(argv)
        if parsed is None:
            return 1, "", "Failed to parse message"
        unit, props = parsed
        path = USER_SVC + "/app.slice/" + unit
        self.cgroup(path, str(props["MemoryMax"]) if self.memmax else None)
        self.pending = (props["PIDs"], path)
        if not self.lazy:
            self._land()
        return 0, "o \"/org/freedesktop/systemd1/job/1\"\n", ""

    def _land(self):
        if self.pending and self.move:
            pids, path = self.pending
            for p in pids:
                (self.proc / str(p) / "cgroup").write_text("0::%s\n" % path)
        self.pending = None

    def sleep(self, _s):
        self.sleeps += 1
        if self.lazy and self.sleeps == 2:
            self._land()

    def adopt(self, host="dev1", env=None, mem_kb=DEV1_KB, meminfo=None):
        return ss.adopt_unscoped_sessions(
            hostname=host, uid=UID, proc_root=str(self.proc),
            cgroup_root=str(self.cg),
            meminfo_text=meminfo if meminfo is not None else "MemTotal:       %d kB\n" % mem_kb,
            run=self.run, env={} if env is None else env, sleep=self.sleep)


def _pids(argv):
    i = argv.index("PIDs")
    return sorted(argv[i + 3:i + 3 + int(argv[i + 2])], key=int)


class AdoptUncapped(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.box = _Box(self._t.name)
        b = self.box
        b.proc_add(100, "claude", 50, ANCHOR, cwd="/home/newlevel/devel/songplayer")
        b.proc_add(101, "bash", 100, ANCHOR)
        b.proc_add(102, "python", 101, ANCHOR)
        b.proc_add(200, "python", 1, ANCHOR)          # not under claude
        b.proc_add(50, "tmux: server", 1, ANCHOR)

    def tearDown(self):
        self._t.cleanup()

    def test_uncapped_session_moves_with_its_descendants(self):
        lines = self.box.adopt()
        self.assertEqual(len(self.box.calls), 1, lines)
        argv, _env = self.box.calls[0]
        self.assertEqual(argv[:4], ["busctl", "--user", "call", "org.freedesktop.systemd1"])
        self.assertIn("airuleset-session-100.scope", argv)
        self.assertEqual(_pids(argv), ["100", "101", "102"])
        for p in ("100", "101", "102"):
            self.assertTrue(self.box.cgroup_of(int(p)).endswith("airuleset-session-100.scope"))
        self.assertEqual(self.box.cgroup_of(200), ANCHOR)
        self.assertEqual(self.box.cgroup_of(50), ANCHOR)
        self.assertTrue(any("adopted pid 100" in ln and "songplayer" in ln for ln in lines), lines)

    def test_caps_are_the_launchers(self):
        self.box.adopt()
        argv, _env = self.box.calls[0]
        mx, hi, sw = _launcher_caps_bytes(DEV1_KB)
        self.assertEqual(argv[argv.index("MemoryMax") + 1:argv.index("MemoryMax") + 3], ["t", str(mx)])
        self.assertEqual(argv[argv.index("MemoryHigh") + 1:argv.index("MemoryHigh") + 3], ["t", str(hi)])
        self.assertEqual(argv[argv.index("MemorySwapMax") + 1:argv.index("MemorySwapMax") + 3], ["t", str(sw)])
        self.assertEqual(argv[argv.index("OOMPolicy") + 1:argv.index("OOMPolicy") + 3], ["s", "continue"])
        self.assertEqual(mx, 3221819392)        # dev1's live launcher scope memory.max

    def test_descendant_tools_get_the_tool_oom_adj_claude_does_not(self):
        self.box.adopt()
        self.assertEqual(self.box.adj(101), str(ss.TOOL_OOM_SCORE_ADJ))
        self.assertEqual(self.box.adj(102), str(ss.TOOL_OOM_SCORE_ADJ))
        self.assertEqual(self.box.adj(100), "200")
        self.assertEqual(self.box.adj(200), "200")

    def test_user_bus_env_defaults_when_ssh_has_none(self):
        self.box.adopt(env={})
        _argv, env = self.box.calls[0]
        self.assertEqual(env.get("XDG_RUNTIME_DIR"), "/run/user/1000")
        self.assertEqual(env.get("DBUS_SESSION_BUS_ADDRESS"), "unix:path=/run/user/1000/bus")

    def test_a_nested_claude_moves_with_its_parent_not_into_its_own_scope(self):
        b = self.box
        b.proc_add(103, "claude", 102, ANCHOR)        # a `claude -p` a tool started
        lines = b.adopt()
        self.assertEqual(len(b.calls), 1, lines)
        self.assertEqual(_pids(b.calls[0][0]), ["100", "101", "102", "103"])
        self.assertTrue(b.cgroup_of(103).endswith("airuleset-session-100.scope"))
        self.assertIn("session-scope: 1 adopted, 0 already capped", lines)

    def test_a_child_in_its_own_scope_or_of_another_uid_stays_put(self):
        b = self.box
        b.proc_add(104, "python", 101, CAPPED)        # a systemd-run --scope job
        b.proc_add(105, "sudo", 101, ANCHOR, uid=0)   # setuid: not ours to move
        b.adopt()
        self.assertEqual(_pids(b.calls[0][0]), ["100", "101", "102"])
        self.assertEqual(b.cgroup_of(104), CAPPED)
        self.assertEqual(b.cgroup_of(105), ANCHOR)
        self.assertEqual(b.adj(104), "200")

    def test_the_move_may_land_after_the_call_returns(self):
        self.box.lazy = True
        lines = self.box.adopt()
        self.assertTrue(any("adopted pid 100" in ln for ln in lines), lines)
        self.assertFalse(any("WARN" in ln for ln in lines), lines)

    def test_a_scope_without_a_memory_limit_is_a_defect(self):
        self.box.memmax = False
        lines = self.box.adopt()
        self.assertTrue(any("WARN" in ln and "DEFECT (#1245)" in ln for ln in lines), lines)
        self.assertFalse(any("adopted pid 100" in ln for ln in lines), lines)

    def test_small_box_gets_the_2_gib_floor(self):
        self.box.adopt(mem_kb=SMALL_KB)
        argv = self.box.calls[0][0]
        mx, hi, sw = _launcher_caps_bytes(SMALL_KB)
        self.assertEqual(mx, 2 * 1024 * 1024 * 1024)
        self.assertEqual(argv[argv.index("MemoryMax") + 2], str(mx))
        self.assertEqual(argv[argv.index("MemoryHigh") + 2], str(hi))
        self.assertEqual(argv[argv.index("MemorySwapMax") + 2], str(sw))

    def test_a_present_bus_env_passes_through_unchanged(self):
        given = {"XDG_RUNTIME_DIR": "/run/user/77", "PATH": "/p",
                 "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x/bus"}
        self.box.adopt(env=given)
        self.assertEqual(self.box.calls[0][1], given)

    def test_no_env_given_inherits_the_process_environment(self):
        with mock.patch.dict(os.environ, {"AIRULESET_T1245": "kept"}):
            ss.adopt_unscoped_sessions(
                hostname="dev1", uid=UID, proc_root=str(self.box.proc),
                cgroup_root=str(self.box.cg), meminfo_text="MemTotal: %d kB\n" % DEV1_KB,
                run=self.box.run, sleep=self.box.sleep)
        self.assertEqual(self.box.calls[0][1].get("AIRULESET_T1245"), "kept")

    def test_unreadable_memtotal_is_a_loud_line_and_no_call(self):
        lines = self.box.adopt(meminfo="")
        self.assertEqual(self.box.calls, [])
        self.assertTrue(lines and "WARN" in lines[0] and "DEFECT (#1245)" in lines[0], lines)

    def test_the_fake_rejects_a_malformed_call(self):
        argv = ss._busctl_argv("x.scope", [1], (1, 1, 1))
        self.assertIsNotNone(self.box._parse(argv))
        for bad in (argv[:10] + ["5"] + argv[11:], argv[:-1],
                    [a if a != "au" else "ai" for a in argv]):
            self.assertIsNone(self.box._parse(bad), bad)

    def test_capped_session_is_left_alone(self):
        b = self.box
        b.proc_add(300, "claude", 50, CAPPED)
        b.adopt()
        self.assertEqual(len(b.calls), 1)
        self.assertNotIn("300", _pids(b.calls[0][0]))
        self.assertEqual(b.cgroup_of(300), CAPPED)

    def test_all_capped_means_no_call_and_a_summary(self):
        with tempfile.TemporaryDirectory() as t:
            b = _Box(t)
            b.proc_add(300, "claude", 1, CAPPED)
            lines = b.adopt()
        self.assertEqual(b.calls, [])
        self.assertTrue(any("1 already capped" in ln for ln in lines), lines)

    def test_off_a_declared_host_nothing_happens(self):
        self.assertEqual(self.box.adopt(host="airuleset"), [])
        self.assertEqual(self.box.calls, [])

    def test_foreign_uid_claude_is_never_touched(self):
        with tempfile.TemporaryDirectory() as t:
            b = _Box(t)
            b.proc_add(400, "claude", 1, ANCHOR, uid=1001)
            b.adopt()
        self.assertEqual(b.calls, [])

    def test_logind_session_scope_claude_is_adopted_too(self):
        # Live on dev1 (systemd 255): the user manager moved a sleep out of the
        # ssh login's session-85087.scope into its own scope.
        with tempfile.TemporaryDirectory() as t:
            b = _Box(t)
            b.proc_add(500, "claude", 1, LOGIND, cwd="/home/fohmixer/devel/fohmixer")
            lines = b.adopt()
            moved = b.cgroup_of(500)
        self.assertEqual(len(b.calls), 1, lines)
        self.assertTrue(moved.endswith("airuleset-session-500.scope"), moved)
        self.assertTrue(any("adopted pid 500" in ln and "fohmixer" in ln for ln in lines), lines)

    def test_busctl_failure_is_a_loud_defect_line(self):
        self.box.fail = [(1, "Access denied")]
        lines = self.box.adopt()
        warn = [ln for ln in lines if "WARN" in ln]
        self.assertTrue(warn and "DEFECT (#1245)" in warn[0] and "Access denied" in warn[0], lines)
        self.assertEqual(self.box.cgroup_of(100), ANCHOR)

    def test_a_call_that_did_not_move_the_pid_is_a_defect(self):
        self.box.move = False
        lines = self.box.adopt()
        self.assertTrue(any("WARN" in ln and "DEFECT (#1245)" in ln for ln in lines), lines)
        self.assertFalse(any("adopted pid 100" in ln for ln in lines), lines)

    def test_a_vanished_child_is_retried_once_with_a_fresh_list(self):
        b = self.box
        b.fail = [(1, "No such process")]

        def vanish(n):
            if n == 1:
                for f in (b.proc / "102").iterdir():
                    f.unlink()
                (b.proc / "102").rmdir()
        b.on_call = vanish
        lines = b.adopt()
        self.assertEqual(len(b.calls), 2, lines)
        self.assertEqual(_pids(b.calls[1][0]), ["100", "101"])
        self.assertTrue(any("adopted pid 100" in ln for ln in lines), lines)


class InstallRunsTheAdoption(unittest.TestCase):
    def test_cmd_install_calls_it(self):
        src = (Path(__file__).resolve().parents[1] / "airuleset.py").read_text()
        body = src[src.index("def cmd_install("):]
        body = body[:body.index("\ndef ")]
        call = body.index("cli_session_scope.adopt_unscoped_sessions()")
        self.assertLess(body.index("provision_swap()"), call)
        self.assertIn('print(f"  {line}")', body[call:call + 120])


if __name__ == "__main__":
    unittest.main()
