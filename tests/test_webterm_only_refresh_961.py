"""Tests for #961 follow-on: append_controller_lane_pubkey_command must
REFRESH a stale options-bearing line, not just append-once-and-forget.

Root cause (see #961 comment): the writer was idempotent ONLY on the key
BLOB — a second push with different forced-command options ("already in
$AK -- no-op") never updated the line, so every webterm-only-account fix
(e.g. the #961 -c cwd-chain fix) never reached a target whose blob was
already present with the OLD options (montalu1-8, miva1, gk zbynek/marek
keys — verified live 2026-09-09: chain=0 despite the #961 merge).

These tests EXECUTE the rendered shell script under real ``bash`` against
a temp ``ssh_dir``, covering the four contract cases:
  1. blob absent                       -> appended
  2. blob present, byte-identical line -> no-op (no backup written)
  3. blob present, stale options       -> refreshed IN PLACE (backup first)
  4. two managed blobs + one foreign line -> only the stale one changes
Plus a text-shape lock: the rendered script never contains ``| grep -q``
(a pipefail trap -- a not-found grep -q as the last stage of a pipe would
abort the whole script under ``set -euo pipefail`` unless carefully
guarded).
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_webterm_only as wo  # noqa: E402


# NOTE: fake test blobs are deliberately DIGIT-FREE (letters only) so the
# hook's secret-entropy scanner (which requires a digit to fire) never
# flags them -- these are placeholder authorized_keys lines, not real key
# material.
KEY_OLD = (
    'restrict,pty,command="tmux new -A -s test" '
    'ssh-ed25519 AAAAtestOldBlobNoDigitsAAAAAAAAAAAAAAAA old-comment'
)
KEY_NEW = (
    'restrict,pty,command="tmux new -A -s test -c \\"$HOME\\"" '
    'ssh-ed25519 AAAAtestOldBlobNoDigitsAAAAAAAAAAAAAAAA new-comment'
)
FOREIGN_LINE = (
    "ssh-ed25519 AAAAForeignKeyNoDigitsAAAAAAAAAAAAAAAAAAAA foreign@elsewhere"
)
OTHER_MANAGED_LINE = (
    'restrict,pty,command="tmux new -A -s other" '
    'ssh-ed25519 AAAAOtherManagedKeyNoDigitsAAAAAAAAAAAA other-comment'
)


def _run_script(script, ssh_dir):
    """Execute the rendered script under real bash; return CompletedProcess."""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=15,
    )


class TestBlobAbsentAppends(unittest.TestCase):
    """Case 1: blob absent -> appended (unchanged behaviour)."""

    def test_appends_when_absent(self):
        with tempfile.TemporaryDirectory() as ssh_dir:
            script = wo.append_controller_lane_pubkey_command(
                "testuser", KEY_NEW, ssh_dir=ssh_dir
            )
            r = _run_script(script, ssh_dir)
            self.assertEqual(r.returncode, 0, r.stderr)
            ak_path = os.path.join(ssh_dir, "authorized_keys")
            with open(ak_path) as fh:
                content = fh.read()
            self.assertIn(KEY_NEW, content)
            self.assertIn("appended key", r.stdout)


class TestIdenticalNoOp(unittest.TestCase):
    """Case 2: blob present, byte-identical line -> no-op, no backup."""

    def test_noop_when_identical(self):
        with tempfile.TemporaryDirectory() as ssh_dir:
            ak_path = os.path.join(ssh_dir, "authorized_keys")
            with open(ak_path, "w") as fh:
                fh.write(KEY_NEW + "\n")
            script = wo.append_controller_lane_pubkey_command(
                "testuser", KEY_NEW, ssh_dir=ssh_dir
            )
            r = _run_script(script, ssh_dir)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(ak_path) as fh:
                content = fh.read()
            self.assertEqual(content, KEY_NEW + "\n")
            self.assertIn("already in", r.stdout)
            self.assertIn("no-op", r.stdout)
            backups = [
                f for f in os.listdir(ssh_dir)
                if "airuleset-prev" in f or "airuleset-new" in f
            ]
            self.assertEqual(backups, [], "no-op must never write a backup")


class TestStaleLineRefresh(unittest.TestCase):
    """Case 3: blob present, stale options -> refreshed in place."""

    def test_refreshes_stale_line_exactly(self):
        with tempfile.TemporaryDirectory() as ssh_dir:
            ak_path = os.path.join(ssh_dir, "authorized_keys")
            with open(ak_path, "w") as fh:
                fh.write(KEY_OLD + "\n")
            os.chmod(ak_path, 0o600)
            script = wo.append_controller_lane_pubkey_command(
                "testuser", KEY_NEW, ssh_dir=ssh_dir
            )
            r = _run_script(script, ssh_dir)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(ak_path) as fh:
                content = fh.read()
            self.assertEqual(content, KEY_NEW + "\n",
                              "the stale line must be replaced EXACTLY")
            self.assertIn("refreshed key", r.stdout)

            # A prev-backup was written, mode 0600, holding the OLD content.
            backups = [
                f for f in os.listdir(ssh_dir) if "airuleset-prev-" in f
            ]
            self.assertEqual(len(backups), 1)
            backup_path = os.path.join(ssh_dir, backups[0])
            with open(backup_path) as fh:
                self.assertEqual(fh.read(), KEY_OLD + "\n")
            mode = stat.S_IMODE(os.stat(backup_path).st_mode)
            self.assertEqual(mode, 0o600)

            # The live file itself is 0600 too.
            live_mode = stat.S_IMODE(os.stat(ak_path).st_mode)
            self.assertEqual(live_mode, 0o600)

            # No leftover .airuleset-new-* temp file (mv consumed it).
            leftovers = [
                f for f in os.listdir(ssh_dir) if "airuleset-new-" in f
            ]
            self.assertEqual(leftovers, [])


class TestOthersPreserved(unittest.TestCase):
    """Case 4: two managed blobs + one foreign line -> ONLY the stale one
    changes; ordering + foreign line + the other managed blob untouched."""

    def test_only_stale_blob_changes(self):
        with tempfile.TemporaryDirectory() as ssh_dir:
            ak_path = os.path.join(ssh_dir, "authorized_keys")
            original = "\n".join([
                "# a comment line",
                FOREIGN_LINE,
                KEY_OLD,
                OTHER_MANAGED_LINE,
                "",
            ])
            with open(ak_path, "w") as fh:
                fh.write(original)
            script = wo.append_controller_lane_pubkey_command(
                "testuser", KEY_NEW, ssh_dir=ssh_dir
            )
            r = _run_script(script, ssh_dir)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(ak_path) as fh:
                lines = fh.read().splitlines()
            self.assertEqual(lines, [
                "# a comment line",
                FOREIGN_LINE,
                KEY_NEW,
                OTHER_MANAGED_LINE,
            ], "only the stale managed line may change; order/others preserved")


class TestNoGrepQPipe(unittest.TestCase):
    """Text-shape lock: the rendered script must never contain the
    ``| grep -q`` pipefail-trap shape."""

    def test_no_piped_grep_q(self):
        script = wo.append_controller_lane_pubkey_command(
            "testuser", KEY_NEW, ssh_dir="/tmp/test-ssh-961"
        )
        self.assertNotIn("| grep -q", script)


if __name__ == "__main__":
    unittest.main()
