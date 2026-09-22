"""#1116 slice 2 — install removes the legacy `CLAUDE_CODE_DISABLE_CRON=1`
export from ~/.bashrc.

Root cause (design comment, live read 22.9.2026): a pre-marker April-2026
install (writer 71804eab, writer removed in 75a3932d with NO remover) wrote two
un-marked lines at the top of ~/.bashrc —

    # Claude Code: disable /loop and CronCreate (managed by airuleset)
    export CLAUDE_CODE_DISABLE_CRON=1

They disable /loop + CronCreate (contradicting claude-code-tooling.md) and show
as a permanent `cli_bashrc_drift` row. `remove_legacy_disable_cron_export`
deletes ONLY those two lines (or a lone matching export), backs up once, is
idempotent, and never touches user exports or managed marker blocks. The drift
scan (cli_bashrc_drift) stays unchanged and is the live proof.

These tests drive the applier against a fixture bashrc in a temp dir (path
injected via `bashrc_path=`) — no real ~/.bashrc, no ssh, no install run.
"""
import inspect
import tempfile
import unittest
from pathlib import Path

import cli_bashrc_appliers as appliers


LEGACY_COMMENT = "# Claude Code: disable /loop and CronCreate (managed by airuleset)"
LEGACY_EXPORT = "export CLAUDE_CODE_DISABLE_CRON=1"
BACKUP_SUFFIX = ".airuleset-1116.bak"


class TestLegacyDisableCronRemover(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.bashrc = self.tmp / ".bashrc"

    def tearDown(self):
        self._td.cleanup()

    def _write(self, text):
        self.bashrc.write_text(text)

    def _read(self):
        return self.bashrc.read_text()

    # ------------------------------------------------------------------ #
    def test_removes_exactly_the_legacy_pair(self):
        """The legacy comment + the export directly after it are removed; every
        other line — including a user export — survives byte-for-byte."""
        original = (
            f"{LEGACY_COMMENT}\n"
            f"{LEGACY_EXPORT}\n"
            "export CLAUDE_CODE_FOO=1\n"
            "alias ll='ls -la'\n"
        )
        self._write(original)
        changed = appliers.remove_legacy_disable_cron_export(
            bashrc_path=self.bashrc)
        self.assertTrue(changed)
        out = self._read()
        self.assertNotIn(LEGACY_COMMENT, out)
        self.assertNotIn(LEGACY_EXPORT, out)
        # everything else intact
        self.assertIn("export CLAUDE_CODE_FOO=1", out)
        self.assertIn("alias ll='ls -la'", out)
        self.assertEqual(
            out,
            "export CLAUDE_CODE_FOO=1\n"
            "alias ll='ls -la'\n",
        )

    def test_legacy_pair_not_at_top(self):
        """The pair is removed wherever it sits, surrounding lines untouched."""
        original = (
            "# my rc\n"
            "export PATH=$PATH:/x\n"
            f"{LEGACY_COMMENT}\n"
            f"{LEGACY_EXPORT}\n"
            "echo done\n"
        )
        self._write(original)
        self.assertTrue(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(
            self._read(),
            "# my rc\n"
            "export PATH=$PATH:/x\n"
            "echo done\n",
        )

    def test_lone_export_removed(self):
        """A lone matching export line (no preceding legacy comment) is removed."""
        self._write(
            "export PATH=$PATH:/x\n"
            f"{LEGACY_EXPORT}\n"
            "echo hi\n"
        )
        self.assertTrue(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(
            self._read(),
            "export PATH=$PATH:/x\n"
            "echo hi\n",
        )

    def test_user_export_untouched(self):
        """A user `export CLAUDE_CODE_FOO=1` is never touched (no-op → False)."""
        original = "export CLAUDE_CODE_FOO=1\necho hi\n"
        self._write(original)
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), original)

    def test_disable_cron_zero_untouched(self):
        """A DIFFERENTLY-valued `export CLAUDE_CODE_DISABLE_CRON=0` stays: the
        remover matches `=1` exactly."""
        original = "export CLAUDE_CODE_DISABLE_CRON=0\necho hi\n"
        self._write(original)
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), original)

    def test_inside_managed_marker_block_untouched(self):
        """The exact export sitting INSIDE a `# >>> airuleset …` managed block is
        never removed (the remover only acts outside balanced marker blocks)."""
        original = (
            "# >>> airuleset: some block >>>\n"
            f"{LEGACY_EXPORT}\n"
            "# <<< airuleset: some block <<<\n"
            "echo hi\n"
        )
        self._write(original)
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), original)

    def test_comment_and_export_inside_marker_block_untouched(self):
        """Both legacy lines inside a managed block are left intact."""
        original = (
            "# >>> airuleset: x >>>\n"
            f"{LEGACY_COMMENT}\n"
            f"{LEGACY_EXPORT}\n"
            "# <<< airuleset: x <<<\n"
        )
        self._write(original)
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), original)

    def test_idempotent_second_run(self):
        """A second run is a no-op (returns False, file unchanged)."""
        self._write(
            f"{LEGACY_COMMENT}\n{LEGACY_EXPORT}\nexport CLAUDE_CODE_FOO=1\n")
        self.assertTrue(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        after_first = self._read()
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), after_first)

    def test_backup_written_once(self):
        """The backup ~/.bashrc.airuleset-1116.bak holds the PRE-edit content and
        is written exactly once (a later run never overwrites it)."""
        original = f"{LEGACY_COMMENT}\n{LEGACY_EXPORT}\nkeep\n"
        self._write(original)
        backup = self.bashrc.with_name(self.bashrc.name + BACKUP_SUFFIX)
        self.assertFalse(backup.exists())
        appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc)
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(), original)
        # a no-op second run must not create/overwrite the backup
        backup.write_text("SENTINEL")
        appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc)
        self.assertEqual(backup.read_text(), "SENTINEL")

    def test_no_backup_when_nothing_removed(self):
        """No legacy line → no edit → no backup created."""
        self._write("export CLAUDE_CODE_FOO=1\n")
        appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc)
        backup = self.bashrc.with_name(self.bashrc.name + BACKUP_SUFFIX)
        self.assertFalse(backup.exists())

    def test_missing_bashrc_is_noop(self):
        """A missing ~/.bashrc is a valid state — returns False, no crash."""
        self.assertFalse(self.bashrc.exists())
        self.assertFalse(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))

    def test_trailing_whitespace_on_export_tolerated(self):
        """Trailing whitespace on the export line does not save it."""
        self._write(f"{LEGACY_COMMENT}\n{LEGACY_EXPORT}   \nkeep\n")
        self.assertTrue(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), "keep\n")

    def test_preserves_no_trailing_newline(self):
        """A file with no trailing newline keeps that shape after removal."""
        self._write(f"keep\n{LEGACY_COMMENT}\n{LEGACY_EXPORT}")
        self.assertTrue(
            appliers.remove_legacy_disable_cron_export(bashrc_path=self.bashrc))
        self.assertEqual(self._read(), "keep\n")


class TestCmdInstallWiresRemover(unittest.TestCase):
    def test_cmd_install_calls_remover(self):
        """cmd_install must call remove_legacy_disable_cron_export (source-lock —
        the same shape used for the other bashrc appliers)."""
        import airuleset
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("remove_legacy_disable_cron_export(", src)

    def test_remover_is_importable_from_airuleset_facade(self):
        """The name is re-exported on airuleset (facade), like the sibling
        appliers, so `airuleset.remove_legacy_disable_cron_export` resolves."""
        import airuleset
        self.assertTrue(
            hasattr(airuleset, "remove_legacy_disable_cron_export"))


if __name__ == "__main__":
    unittest.main()
