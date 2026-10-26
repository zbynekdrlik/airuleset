"""Bundle B9 fix round, fourth adversarial review of the issue 1257 owner-stop
latch: the shared Claude-process predicate under a SHELL parent (the usual
npm launch shape), a process name that is not UTF-8, and the review-4 edges.
Real processes; /proc is read for real, never mocked (an OS fault is the
only seam patched).
"""
import os
import subprocess
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import owner_stop  # noqa: E402

CLI = "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"


def _spawn(testcase, argv):
    proc = subprocess.Popen(argv)
    testcase.addCleanup(proc.wait)
    testcase.addCleanup(proc.kill)
    return proc


def _children(pid, deadline=10.0):
    """The child pids of `pid` once it has forked one (a shell `cmd; :`)."""
    end = time.time() + deadline
    while time.time() < end:
        kids = [p for p in os.listdir("/proc") if p.isdigit() and wd._proc_read(
            "/proc/%s/stat" % p).rsplit(") ", 1)[-1].split()[1:2] == [str(pid)]]
        if kids:
            return kids
        time.sleep(0.05)
    raise AssertionError("no child of %s" % pid)


class _Procs(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.node = Path(d.name) / "node"
        self.node.symlink_to(sys.executable)

    def _shell(self, *argv):
        """`sh -c '<argv>; :'` — the shell stays the parent (no exec), the way a
        tmux pane's shell forks the Claude it runs."""
        sh = _spawn(self, ["sh", "-c", " ".join("'%s'" % a for a in argv) + "; :"])
        _children(sh.pid)
        return str(sh.pid)

    def _npm_under_shell(self):
        return self._shell(str(self.node), "-c", "import time; time.sleep(60)", CLI)


class TheNpmShapeUnderAShell(_Procs):
    """Review 5, finding 1: every npm test used the node process itself as the
    pane_pid, so the walk under a shell parent — the usual launch, which
    `_hosts_claude` and `list_claude_panes` depend on — had no test."""

    def test_the_npm_claude_under_a_shell_is_found(self):
        sh = self._npm_under_shell()
        child = wd._pane_claude_pid(sh)
        self.assertIsNotNone(child)
        self.assertNotEqual(child, sh)
        self.assertTrue(owner_stop._hosts_claude(sh))

    def test_a_plain_node_under_a_shell_is_no_claude(self):
        sh = self._shell(str(self.node), "-c", "import time; time.sleep(60)",
                         "/srv/app/server.js")
        self.assertIsNone(wd._pane_claude_pid(sh))
        self.assertFalse(owner_stop._hosts_claude(sh))

    def test_list_claude_panes_sees_an_npm_claude_under_a_shell(self):
        npm, plain = self._npm_under_shell(), self._shell(
            str(self.node), "-c", "import time; time.sleep(60)", "/srv/app/x.js")
        inv = "%%S\tsh\t/x\t%s\n%%T\tsh\t/y\t%s\n" % (npm, plain)
        panes = wd.list_claude_panes(lambda argv, timeout=8: inv
                                     if "list-panes" in argv else "")
        self.assertEqual([p for p, _c in panes], ["%S"])


class ABadlyNamedProcessAnywhere(_Procs):
    """Review 5, finding 2: the decode fix had no test that fails without it;
    and the rename was awaited with a fixed sleep (a race under load)."""

    def test_the_hosted_walk_survives_a_non_utf8_process_name(self):
        bad = _spawn(self, [sys.executable, "-c",
                            "import ctypes, time; ctypes.CDLL(None).prctl("
                            "15, b'bad\\xff\\xfe', 0, 0, 0); time.sleep(60)"])
        end = time.time() + 10
        while b"\xff" not in Path("/proc/%d/comm" % bad.pid).read_bytes():
            self.assertLess(time.time(), end, "the child never renamed itself")
            time.sleep(0.05)
        self.assertIsNotNone(wd._pane_claude_pid(self._npm_under_shell()))


class AnUnreadableProcFailsClosed(_Procs):
    """Review 5, finding 3: `_hosts_claude` promised "an unreadable /proc reads
    as a Claude", but the shared walk turns a listing fault (e.g. EMFILE) into
    "no Claude", so an npm Claude next to a bare shell read as a dev server
    and its working session could be latched as an exit."""

    def test_a_proc_listing_fault_reads_as_a_claude(self):
        sh = self._shell("sleep", "60")
        real = os.listdir

        def listdir(path="."):
            if str(path) == "/proc":
                raise OSError(24, "Too many open files")
            return real(path)
        with m.patch("os.listdir", side_effect=listdir):
            self.assertTrue(owner_stop._hosts_claude(sh))


def _lane(testcase, goal_mod, cwd, before):
    """A `goal_lane_sweep` stand-in that runs `before(now, kw)` (set up the
    sessions, run `owner_stop.refresh`) ahead of each real sweep."""
    orig = goal_mod.goal_lane_sweep

    def sweep(now, **kw):
        before(now, kw)
        return orig(now, **kw)
    testcase.addCleanup(owner_stop.reset)
    return m.patch.object(goal_mod, "goal_lane_sweep", sweep)


class StallEpisodesUnderTheLatch(unittest.TestCase):
    """Review 5, finding 5: the paused-loop alert gate (review 4) froze the
    stuck / gk-stall episodes instead of ending them — a stale streak could
    alert at once on release and a leftover `soa` keeps the full sweep
    cadence — and it also silenced the alerts of a pane held only by a
    SIBLING session's latch (`via`), which the owner never stopped."""

    def setUp(self):
        import test_oauth_stuck_alert_662 as sa
        self.sa = sa
        self.h = sa.SweepStuckOwnerAlert("run")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def _dir(self, kw):
        return Path(kw["projects_dir"]) / wd.encode_project_dir(self.h.CWD)

    def test_an_own_pause_ends_the_stall_episodes(self):
        def before(now, kw):
            t = self._dir(kw) / "sess-662.jsonl"
            if "pauza" not in t.read_text(encoding="utf-8"):
                aged = os.path.getmtime(t)
                with open(t, "a", encoding="utf-8") as fh:
                    fh.write('{"type": "user", "timestamp": "2026-01-01T00:00:00Z",'
                             ' "message": {"role": "user", "content": "pauza"}}\n')
                os.utime(t, (aged, aged))
            owner_stop.refresh(kw["state"], [("%9", self.h.CWD)],
                               kw["projects_dir"], now)
        state = {"goal_lane": {"sess-662": {"soa": 5, "soa_ts": 1, "gks_ts": 1}}}
        with _lane(self, self.sa.goal, self.h.CWD, before):
            sends, state, sid = self.h._run(self.h._stuck(2), state=state)
        rec = state["goal_lane"][sid]
        self.assertEqual([k for k in ("soa", "soa_ts", "gks_ts") if k in rec], [], rec)
        self.assertEqual(self.h._alerts(sends), [])

    def test_a_siblings_latch_never_silences_the_stuck_alert(self):
        def before(now, kw):
            sib = self._dir(kw) / "sess-sibling.jsonl"
            if not sib.exists():
                sib.write_text('{"type": "user", "timestamp": "2026-01-01T00:00:00Z",'
                               ' "message": {"role": "user", "content": "pauza"}}\n')
                os.utime(sib, (now - 7200, now - 7200))
            with m.patch.object(wd, "_pane_claude_start_epoch",
                                side_effect=lambda pid, run=None: None):
                owner_stop.refresh(kw["state"], [("%9", self.h.CWD), ("%8", self.h.CWD)],
                                   kw["projects_dir"], now,
                                   run=lambda argv, timeout=8: "")
            self.assertEqual((owner_stop.pane_latch("%9") or {}).get("via"),
                             "sess-sibling")
        with _lane(self, self.sa.goal, self.h.CWD, before):
            sends, _s, _sid = self.h._run(
                self.h._stuck(self.sa.goal.GOAL_LANE_STUCK_ALERT_STREAK + 3))
        self.assertEqual(len(self.h._alerts(sends)), 1, sends)


class AnUnclaimedSessionJournalsQuietly(unittest.TestCase):
    """Review 5, finding 6: an unresolved pane whose newest session's own
    transcript holds a stop journalled SET twice per episode (the pane hold
    AND the session record), and a later clear said "the owner typed a new
    prompt" for a session no pane owns. The pane line is the one line."""
    CWD = "/home/newlevel/devel/quietunclaimed"

    def test_one_set_line_per_episode(self):
        import json
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.addCleanup(owner_stop.reset)
        proj = Path(d.name)
        sd = proj / wd.encode_project_dir(self.CWD)
        sd.mkdir(parents=True)
        now = 1_800_000_000

        def row(role, text, ts):
            iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))
            if role == "user":
                return {"type": "user", "timestamp": iso,
                        "message": {"role": "user", "content": text}}
            return {"type": "assistant", "timestamp": iso,
                    "message": {"content": [{"type": "text", "text": text}]}}
        for sid, rows, mt in (("SA", [row("user", "sprav ticket 5", now - 900),
                                      row("assistant", "robím", now - 800)], now - 800),
                              ("SB", [row("user", "sprav ticket 6", now - 600),
                                      row("user", "pauza", now - 550)], now - 600)):
            f = sd / (sid + ".jsonl")
            f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            os.utime(f, (mt, mt))
        logs, state = [], {}
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: {"%A": now - 950}.get(pid)):
            for i in range(2):
                logs += owner_stop.refresh(state, [("%A", self.CWD), ("%B", self.CWD)],
                                           proj, now + 60 * i,
                                           run=lambda argv, timeout=8: "")
        self.assertEqual(sum("SET" in ln for ln in logs), 1, logs)
        self.assertEqual((owner_stop.pane_latch("%B") or {}).get("fact"), "pause")


if __name__ == "__main__":
    unittest.main()
