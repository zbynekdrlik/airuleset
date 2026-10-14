"""#1245: dev1 froze a 5th time (14.10.2026). #1224 capped a session only at its
next LAUNCH, so the camera-box, songplayer and restreamer sessions from 9-11.10.
still ran uncapped in the tmux server's unit. songplayer's tool grew to 5.6 GB.

Install now ADOPTS every running uncapped `claude` of this account, with its
descendants, into its own transient scope. The scope has the launcher's caps
and OOMPolicy=continue, and it is created through systemd's StartTransientUnit
with PIDs (busctl). The session is not restarted. A claude in a logind session
scope cannot be moved by the user manager, so it is reported LOUD.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_session_scope as ss  # noqa: E402

DEV1_KB = 7865772
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
        self.cgroup(ANCHOR, "max")
        self.cgroup(CAPPED, "3221819392")
        self.cgroup(LOGIND, "max")

    def cgroup(self, path, mem_max):
        d = self.cg / path.lstrip("/")
        d.mkdir(parents=True, exist_ok=True)
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

    def run(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        if self.on_call:
            self.on_call(len(self.calls))
        if self.fail:
            rc, err = self.fail.pop(0)
            return rc, "", err
        unit = argv[argv.index("StartTransientUnit") + 2]
        i = argv.index("PIDs")
        n = int(argv[i + 2])
        pids = argv[i + 3:i + 3 + n]
        mx = argv[argv.index("MemoryMax") + 2]
        path = USER_SVC + "/app.slice/" + unit
        self.cgroup(path, mx)
        if self.move:
            for p in pids:
                (self.proc / p / "cgroup").write_text("0::%s\n" % path)
        return 0, "o \"/org/freedesktop/systemd1/job/1\"\n", ""

    def adopt(self, host="dev1", env=None):
        return ss.adopt_unscoped_sessions(
            hostname=host, uid=UID, proc_root=str(self.proc),
            cgroup_root=str(self.cg),
            meminfo_text="MemTotal:       %d kB\n" % DEV1_KB,
            run=self.run, env={} if env is None else env, sleep=lambda s: None)


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

    def test_logind_session_scope_is_reported_loud_not_moved(self):
        with tempfile.TemporaryDirectory() as t:
            b = _Box(t)
            b.proc_add(500, "claude", 1, LOGIND, cwd="/home/fohmixer/devel/fohmixer")
            lines = b.adopt()
        self.assertEqual(b.calls, [])
        warn = [ln for ln in lines if "WARN" in ln]
        self.assertTrue(warn and "500" in warn[0] and "session-5.scope" in warn[0]
                        and "#1245" in warn[0], lines)

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
        self.assertIn("cli_session_scope.adopt_unscoped_sessions()", body)


if __name__ == "__main__":
    unittest.main()
