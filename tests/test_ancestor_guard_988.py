"""Ancestor-guard tests for lib_hook_block_log.sh (#988 fix-forward).

The PYTEST_CURRENT_TEST env guard does NOT fire when hook tests spawn hooks
with custom env dicts that omit it. The /proc ancestor walk is the structural
fix: walk the parent chain from $PPID and suppress writes when any ancestor's
argv contains 'pytest' OR 'unittest' (the push gate runs unittest discover).

Test plan:
  RED — today's lib matches only pytest, not unittest:
    (a) a hook spawned under unittest with a fake /proc tree showing a
        'python3 -m unittest discover' ancestor writes to the default log
        (should be suppressed but isn't)
  GREEN — after the fix:
    (a) the ancestor walk detects both pytest and unittest in the parent chain
    (b) a non-test ancestor (bash) correctly allows the write
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

REPO = Path(airuleset.__file__).resolve().parent
LIB = REPO / "hooks" / "lib_hook_block_log.sh"
HOOK_BMI = REPO / "hooks" / "block-main-implementation.sh"


class AncestorGuardSuppression(unittest.TestCase):
    """A hook spawned from INSIDE a test runner (pytest or unittest), even
    with a clean env dict that does NOT carry PYTEST_CURRENT_TEST, must NOT
    write to the default log. The /proc ancestor walk detects the runner."""

    def test_clean_env_no_default_log(self):
        """Spawn the lib's log_hook_block with a CLEAN env (no
        PYTEST_CURRENT_TEST, no AIRULESET_HOOK_BLOCK_LOG) from inside
        pytest. The default log must NOT be written."""
        with TemporaryDirectory() as d:
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            (fake_home / ".claude").mkdir()
            default_log = fake_home / ".claude" / "hook-blocks.log"

            # CLEAN env: only PATH + HOME, no PYTEST_CURRENT_TEST
            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(fake_home),
            }

            subprocess.run(
                ["bash", "-c",
                 'source "%s" && log_hook_block ancestor-test "test-cmd"' % LIB],
                env=clean_env, capture_output=True, text=True,
            )
            self.assertFalse(
                default_log.exists(),
                "Default log must NOT be written when invoked from inside "
                "a test runner (pytest or unittest) — the ancestor walk "
                "should detect the runner in the parent chain. Log content: %s"
                % (default_log.read_text() if default_log.exists() else "<none>"),
            )


class UnittestAncestorSuppression(unittest.TestCase):
    """A hook invoked from a process whose /proc ancestor chain contains
    'python3 -m unittest discover' must NOT write to the default log.
    Uses the AIRULESET_PROC_ROOT seam to build a fake /proc tree."""

    def test_unittest_discover_ancestor_suppresses(self):
        """Fake /proc tree: bash($$) -> python3 -m unittest discover.
        The guard must detect 'unittest' in the parent chain and suppress."""
        with TemporaryDirectory() as d:
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            (fake_home / ".claude").mkdir()
            default_log = fake_home / ".claude" / "hook-blocks.log"

            fake_proc = Path(d) / "proc"
            fake_proc.mkdir()

            # PID 100 = python3 -m unittest discover (the test runner ancestor)
            pid100 = fake_proc / "100"
            pid100.mkdir()
            (pid100 / "cmdline").write_bytes(
                b"python3\x00-m\x00unittest\x00discover\x00-s\x00tests\x00"
            )
            (pid100 / "status").write_text("PPid:\t1\n")

            # PID 1 = init (root, terminates the walk)
            pid1 = fake_proc / "1"
            pid1.mkdir()
            (pid1 / "cmdline").write_bytes(b"init\x00")
            (pid1 / "status").write_text("PPid:\t0\n")

            # The hook script will read its own $$ PID. We use setsid so the
            # bash process gets its own PID, then override /proc so the walk
            # starts from fake entries. We need to make the bash process's
            # PPID point into our fake tree. Since setsid creates a new session
            # leader, its PPID is the calling process. We make a fake entry
            # for $$ that points to PID 100.
            #
            # Strategy: the script runs as bash -c '...'; we create the
            # fake /proc/$$/status pointing to PID 100 INSIDE the script.
            script = (
                'MY_PID=$$; '
                'mkdir -p "%s/$MY_PID"; '
                'echo "PPid:\\t100" > "%s/$MY_PID/status"; '
                'printf "bash\\x00-c\\x00test\\x00" > "%s/$MY_PID/cmdline"; '
                'source "%s" && log_hook_block unittest-ancestor-test "test-cmd"'
            ) % (fake_proc, fake_proc, fake_proc, LIB)

            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(fake_home),
                "AIRULESET_PROC_ROOT": str(fake_proc),
            }

            subprocess.run(
                ["setsid", "bash", "-c", script],
                env=clean_env, capture_output=True, text=True,
            )
            self.assertFalse(
                default_log.exists(),
                "Default log must NOT be written when a test runner "
                "(pytest or unittest) is in the ancestor chain. "
                "Log content: %s"
                % (default_log.read_text() if default_log.exists() else "<none>"),
            )

    def test_bash_ancestor_writes(self):
        """Fake /proc tree: bash($$) -> bash (not a test runner).
        The guard must NOT suppress — the log must be written."""
        with TemporaryDirectory() as d:
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            (fake_home / ".claude").mkdir()
            default_log = fake_home / ".claude" / "hook-blocks.log"

            fake_proc = Path(d) / "proc"
            fake_proc.mkdir()

            # PID 100 = plain bash (NOT a test runner)
            pid100 = fake_proc / "100"
            pid100.mkdir()
            (pid100 / "cmdline").write_bytes(b"bash\x00--login\x00")
            (pid100 / "status").write_text("PPid:\t1\n")

            # PID 1 = init
            pid1 = fake_proc / "1"
            pid1.mkdir()
            (pid1 / "cmdline").write_bytes(b"init\x00")
            (pid1 / "status").write_text("PPid:\t0\n")

            script = (
                'MY_PID=$$; '
                'mkdir -p "%s/$MY_PID"; '
                'echo "PPid:\\t100" > "%s/$MY_PID/status"; '
                'printf "bash\\x00-c\\x00test\\x00" > "%s/$MY_PID/cmdline"; '
                'source "%s" && log_hook_block bash-ancestor-test "test-cmd"'
            ) % (fake_proc, fake_proc, fake_proc, LIB)

            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(fake_home),
                "AIRULESET_PROC_ROOT": str(fake_proc),
            }

            r = subprocess.run(
                ["setsid", "bash", "-c", script],
                env=clean_env, capture_output=True, text=True,
            )
            self.assertTrue(
                default_log.exists(),
                "Log MUST be written when no test runner ancestor is found. "
                "rc=%d stderr=%s" % (r.returncode, r.stderr),
            )
            content = default_log.read_text()
            self.assertIn("bash-ancestor-test", content)


class NonPytestParentWrites(unittest.TestCase):
    """When the lib is invoked from a NON-pytest parent (no pytest in the
    ancestor chain), it MUST write to the log normally. Uses a fake /proc
    root (AIRULESET_PROC_ROOT seam) to ensure no pytest ancestor is visible."""

    def test_non_pytest_parent_writes(self):
        """A bash process with a fake /proc root showing no pytest ancestor
        must write to the log."""
        with TemporaryDirectory() as d:
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            (fake_home / ".claude").mkdir()
            default_log = fake_home / ".claude" / "hook-blocks.log"

            # Build a fake /proc tree with a single PID=1 entry (init, no pytest)
            fake_proc = Path(d) / "proc"
            fake_proc.mkdir()
            pid1_dir = fake_proc / "1"
            pid1_dir.mkdir()
            (pid1_dir / "cmdline").write_bytes(b"init\x00")
            (pid1_dir / "status").write_text("PPid:\t0\n")

            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(fake_home),
                "AIRULESET_PROC_ROOT": str(fake_proc),
            }

            r = subprocess.run(
                ["setsid", "bash", "-c",
                 'source "%s" && log_hook_block non-pytest-test "test-cmd"' % LIB],
                env=clean_env, capture_output=True, text=True,
            )
            self.assertTrue(
                default_log.exists(),
                "Log MUST be written when no pytest ancestor is found. "
                "rc=%d stderr=%s" % (r.returncode, r.stderr),
            )
            content = default_log.read_text()
            self.assertIn("non-pytest-test", content)
            self.assertIn("\t", content,
                          "Row must be tab-separated")


class StatusReaderLegacyRows(unittest.TestCase):
    """The status reader must count ONLY well-formed 3-column tab-separated
    rows and report legacy rows as skipped."""

    def test_legacy_rows_skipped(self):
        """Legacy space-separated rows must be skipped, well-formed rows
        counted. Output must include 'legacy rows skipped: N'."""
        from io import StringIO
        import contextlib
        from datetime import datetime, timezone

        with TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            legacy_ts = "2026-09-10T17:25:39+02:00"

            lines = [
                # 3 legacy space-separated rows
                "%s block-main-implementation some-unknown-command --flag\n" % legacy_ts,
                "%s block-main-implementation grep -rn TODO .\n" % legacy_ts,
                "%s block-main-implementation /x/app.py\n" % legacy_ts,
                # 2 well-formed tab-separated rows
                "%s\tblock-main-implementation\tsome cmd\n" % now,
                "%s\tblock-ci-poll-repeat\tgh run view\n" % now,
            ]
            claude_dir = Path(d) / ".claude"
            claude_dir.mkdir()
            log_file = claude_dir / "hook-blocks.log"
            log_file.write_text("".join(lines))

            import unittest.mock as m
            buf = StringIO()
            with m.patch("airuleset.Path.home", return_value=Path(d)):
                with contextlib.redirect_stdout(buf):
                    airuleset._print_hook_blocks_count()
            output = buf.getvalue()
            # Must count only 2 well-formed rows
            self.assertIn("hook blocks (24 h): 2", output,
                          "Only well-formed rows should be counted: " + output)
            # Must report legacy rows
            self.assertIn("legacy rows skipped: 3", output,
                          "Legacy rows should be reported: " + output)


if __name__ == "__main__":
    unittest.main()
