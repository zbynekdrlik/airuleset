"""Behaviour test for watchdog Job 38 — the heavy-build-toolchain OS-process
reaper + the shared-stream box-class concept (#778).

The reaper uses INJECTED ps_fetch/kill_fn/verify_fn/box_class_fn fakes
throughout — it NEVER touches a real process, so it is safe under xdist (the
internals-tests.md isolation lesson: never chmod/kill anything shared across
workers).

Semantics DIFFER from Job 37's shadow-ugrep reaper (#776): heavy build daemons
are BANNED OUTRIGHT on a shared-stream box, so this reaper is KILL-ON-SIGHT
(no age gate, no CPU gate) — but it fires ONLY on a shared-stream box, and
NEVER anywhere else. Fail-open on every ambiguity, exactly like #776.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd                       # noqa: E402
from watchdog.reaper import (               # noqa: E402
    heavy_build_reaper,
    default_box_class,
    is_shared_stream_box,
    _heavy_build_kind,
    SHARED_STREAM,
    GRADLE_DAEMON_CLASS,
    KOTLIN_DAEMON_CLASS,
)

GRADLE_CMD = ("/home/david2/tools/jdk17/bin/java -Xmx3072m -Dfile.encoding=UTF-8 "
              "-cp /home/david2/.gradle/wrapper/gradle-9.3.1/lib/gradle-launcher-9.3.1.jar "
              "-javaagent:agent.jar " + GRADLE_DAEMON_CLASS + " 9.3.1")
KOTLIN_CMD = ("/home/david2/tools/jdk17/bin/java -Xmx700m -cp /opt/kotlin/lib/kotlin.jar "
              + KOTLIN_DAEMON_CLASS + " --daemon-runFilesPath /tmp/kotlin")
AAPT2_CMD = "/home/david2/Android/Sdk/build-tools/34.0.0/aapt2 daemon"
QEMU_CMD = ("/home/david2/Android/Sdk/emulator/qemu/linux-x86_64/qemu-system-x86_64 "
            "-avd Pixel_6 -no-window")

# the four ps columns are (pid, etimes, cputimes, args); the heavy reaper reads
# only pid + args (it reuses the Job 37 ps read shape), so etimes/cputimes are
# arbitrary here — a young, low-CPU build daemon must STILL be killed.
YOUNG, IDLE = 5, 0


def _shared():
    return SHARED_STREAM


def _workstation():
    return "workstation"


def _procs(rows):
    return lambda: rows


class _Recorder:
    def __init__(self):
        self.killed = []

    def __call__(self, pid):
        self.killed.append(int(pid))


class TestHeavyBuildKind(unittest.TestCase):
    def test_gradle_daemon_matches(self):
        self.assertEqual(_heavy_build_kind(GRADLE_CMD), "gradle-daemon")

    def test_kotlin_daemon_matches(self):
        self.assertEqual(_heavy_build_kind(KOTLIN_CMD), "kotlin-daemon")

    def test_aapt2_matches(self):
        self.assertEqual(_heavy_build_kind(AAPT2_CMD), "aapt2")

    def test_qemu_system_matches(self):
        self.assertEqual(_heavy_build_kind(QEMU_CMD), "qemu/emulator")

    def test_signature_is_argv0_anchored_not_substring(self):
        # a process merely QUOTING a signature (argv0 is watch/pgrep/grep/git)
        # never matches — same anchoring discipline as the #776 reaper.
        self.assertIsNone(_heavy_build_kind(
            "watch pgrep -af " + GRADLE_DAEMON_CLASS))
        self.assertIsNone(_heavy_build_kind(
            "grep -F " + KOTLIN_DAEMON_CLASS + " build.log"))
        self.assertIsNone(_heavy_build_kind(
            "git commit -m 'ban " + GRADLE_DAEMON_CLASS + "'"))

    def test_plain_java_process_is_not_a_build_daemon(self):
        # a bare java that is NOT a gradle/kotlin daemon is never killed
        self.assertIsNone(_heavy_build_kind("/usr/bin/java -version"))
        self.assertIsNone(_heavy_build_kind("java -jar myapp.jar"))

    def test_node_is_never_matched(self):
        # node runs Claude Code / MCP servers / webterm — DELIBERATELY excluded
        self.assertIsNone(_heavy_build_kind(
            "node /home/david2/app/node_modules/.bin/webpack --config w.js"))
        self.assertIsNone(_heavy_build_kind("node server.js"))

    def test_empty_and_garbage_are_none(self):
        self.assertIsNone(_heavy_build_kind(""))
        self.assertIsNone(_heavy_build_kind(None))


class TestBoxClass(unittest.TestCase):
    def test_marker_shared_stream_is_shared(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "airuleset-box-class")
            with open(p, "w") as fh:
                fh.write("shared-stream\n")
            self.assertEqual(default_box_class(p), "shared-stream")
            self.assertTrue(is_shared_stream_box(lambda: default_box_class(p)))

    def test_marker_workstation_is_not_shared(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "airuleset-box-class")
            with open(p, "w") as fh:
                fh.write("workstation\n")
            self.assertEqual(default_box_class(p), "workstation")
            self.assertFalse(is_shared_stream_box(lambda: default_box_class(p)))

    def test_missing_marker_fails_open_not_shared(self):
        self.assertIsNone(default_box_class("/nonexistent/box-class-probe"))
        self.assertFalse(
            is_shared_stream_box(lambda: default_box_class("/nonexistent/x")))

    def test_box_class_fn_raising_is_not_shared(self):
        def boom():
            raise RuntimeError("cannot read")
        self.assertFalse(is_shared_stream_box(boom))


class TestHeavyBuildReaper(unittest.TestCase):
    # ---- kill-on-sight ON a shared-stream box --------------------------
    def test_kills_young_gradle_daemon_on_shared_box(self):
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: GRADLE_CMD,
            box_class_fn=_shared)
        self.assertEqual(rec.killed, [111])
        self.assertTrue(any("SIGKILL pid=111" in x and "gradle" in x for x in logs))

    def test_kills_every_signature_on_shared_box(self):
        for cmd in (GRADLE_CMD, KOTLIN_CMD, AAPT2_CMD, QEMU_CMD):
            rec = _Recorder()
            heavy_build_reaper(
                ps_fetch=_procs([(7, YOUNG, IDLE, cmd)]),
                kill_fn=rec, verify_fn=lambda pid: cmd,
                box_class_fn=_shared)
            self.assertEqual(rec.killed, [7], "not killed: %s" % cmd)

    # ---- the OTHER branch: NEVER on a non-shared box -------------------
    def test_never_kills_on_workstation_box(self):
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: GRADLE_CMD,
            box_class_fn=_workstation)
        self.assertEqual(rec.killed, [])
        self.assertEqual(logs, [])

    def test_never_kills_when_box_class_missing(self):
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: GRADLE_CMD,
            box_class_fn=lambda: None)
        self.assertEqual(rec.killed, [])
        self.assertEqual(logs, [])

    # ---- a non-daemon process on a shared box is left alone -----------
    def test_leaves_plain_processes_untouched_on_shared_box(self):
        rec = _Recorder()
        heavy_build_reaper(
            ps_fetch=_procs([(1, 10, 10, "node server.js"),
                             (2, 10, 10, "/usr/bin/java -version"),
                             (3, 10, 10, "python3 -m pytest")]),
            kill_fn=rec, verify_fn=lambda pid: "x", box_class_fn=_shared)
        self.assertEqual(rec.killed, [])

    # ---- fail-safe seams (mirror #776) --------------------------------
    def test_ps_error_kills_nothing(self):
        def boom():
            raise OSError("ps failed")
        rec = _Recorder()
        logs = heavy_build_reaper(ps_fetch=boom, kill_fn=rec,
                                  box_class_fn=_shared)
        self.assertEqual(rec.killed, [])
        self.assertTrue(any("ps error" in x for x in logs))

    def test_ps_returns_none_kills_nothing(self):
        rec = _Recorder()
        logs = heavy_build_reaper(ps_fetch=lambda: None, kill_fn=rec,
                                  box_class_fn=_shared)
        self.assertEqual(rec.killed, [])
        self.assertEqual(logs, [])

    def test_malformed_row_is_skipped(self):
        rec = _Recorder()
        heavy_build_reaper(
            ps_fetch=_procs([("bad", "row"), (9, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: GRADLE_CMD, box_class_fn=_shared)
        self.assertEqual(rec.killed, [9])

    def test_kill_fn_unwired_kills_nothing_and_logs(self):
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=None, verify_fn=lambda pid: GRADLE_CMD, box_class_fn=_shared)
        self.assertTrue(any("kill_fn not wired" in x for x in logs))

    def test_dry_run_kills_nothing(self):
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: GRADLE_CMD, dry_run=True,
            box_class_fn=_shared)
        self.assertEqual(rec.killed, [])
        self.assertTrue(any("DRY-RUN" in x for x in logs))

    def test_toctou_reused_pid_not_killed(self):
        # ps saw a gradle daemon, but /proc now shows an unrelated process
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: "bash -lc 'echo hi'",
            box_class_fn=_shared)
        self.assertEqual(rec.killed, [])
        self.assertTrue(any("reused" in x for x in logs))

    def test_pid_vanished_before_kill_skipped(self):
        rec = _Recorder()
        logs = heavy_build_reaper(
            ps_fetch=_procs([(111, YOUNG, IDLE, GRADLE_CMD)]),
            kill_fn=rec, verify_fn=lambda pid: None, box_class_fn=_shared)
        self.assertEqual(rec.killed, [])
        self.assertTrue(any("vanished" in x for x in logs))


class TestJob38Wiring(unittest.TestCase):
    def test_reaper_is_re_exported_from_watchdog(self):
        self.assertTrue(hasattr(wd, "heavy_build_reaper"))
        self.assertTrue(hasattr(wd, "is_shared_stream_box"))

    def _run_once_with_home(self, home, ps, rec):
        """Drive run_once with a controlled HOME so the box-class marker read
        by the wired heavy_build_reaper is deterministic (not this box's real
        marker). Only the reaper seams are wired; every other job is gated off."""
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            wd.run_once(now=1_000_000.0, run=lambda argv, timeout=8: "",
                        reaper_ps_fetch=ps, reaper_kill_fn=rec)
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_run_once_job38_no_kill_off_shared_box(self):
        # Job 38 reuses the Job-37 reaper_ps_fetch seam; with a workstation
        # (no marker) box-class it kills nothing even with a build daemon live.
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            rec = _Recorder()
            self._run_once_with_home(
                home, lambda: [(111, 5, 0, GRADLE_CMD)], rec)
            self.assertEqual(rec.killed, [])

    def test_run_once_job38_shared_box_no_daemon_no_kill(self):
        # A shared-stream marker flows through run_once → Job 38 runs its full
        # path (box-class gate consulted, ps read) but with no build daemon in
        # the process list nothing is killed. The kill/TOCTOU behaviour itself
        # is covered hermetically in TestHeavyBuildReaper (injected verify_fn).
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            with open(os.path.join(home, ".claude", "airuleset-box-class"),
                      "w") as fh:
                fh.write("shared-stream\n")
            rec = _Recorder()
            self._run_once_with_home(
                home, lambda: [(1, 10, 10, "python3 -m pytest")], rec)
            self.assertEqual(rec.killed, [])


if __name__ == "__main__":
    unittest.main()
