"""Ancestor-guard tests for lib_hook_block_log.sh (#988 fix-forward).

The PYTEST_CURRENT_TEST env guard does NOT fire when hook tests spawn hooks
with custom env dicts that omit it. The /proc ancestor walk is the structural
fix: walk the parent chain from $PPID and suppress writes when any ancestor's
argv contains 'pytest'.

Test plan:
  RED — today's lib has no ancestor check, so:
    (a) a hook spawned with a CLEAN env={} from inside pytest writes to the
        default log (should be suppressed but isn't)
    (b) status reader counts legacy rows as valid (should skip them)
  GREEN — after the fix:
    (a) the ancestor walk detects pytest in the parent chain and suppresses
    (b) status reader skips legacy rows and reports them
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
    """A hook spawned from INSIDE pytest (even with a clean env dict that
    does NOT carry PYTEST_CURRENT_TEST) must NOT write to the default log.
    The /proc ancestor walk detects pytest in the parent chain."""

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
                "pytest — the ancestor walk should detect pytest in the "
                "parent chain. Log content: %s"
                % (default_log.read_text() if default_log.exists() else "<none>"),
            )


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
