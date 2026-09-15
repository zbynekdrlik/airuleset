"""#1020 Part 2 item 3 -- gates.audit.count_cli_bypasses reads the per-hook
CLI-token audit logs (the `~/devel/airuleset/audits/*.log` bypass family + the
`/tmp/airuleset-main-exec-bypass-<uid>.log` family) that `--bypasses` was BLIND
to (it counted merged commit messages only). A genuine honored bypass line
counts; a mixed log's NON-bypass line (tier0 `blocked`, main-exec `refused`)
does not.
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import audit  # noqa: E402


def _today():
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class TestCliBypassReader(unittest.TestCase):
    def setUp(self):
        self.audits = tempfile.mkdtemp(prefix="airuleset-cli-bypass-audits-")
        self.tmp = tempfile.mkdtemp(prefix="airuleset-cli-bypass-tmp-")
        self.day = _today()

    def _w(self, name, lines):
        Path(self.audits, name).write_text("\n".join(lines) + "\n")

    def test_pure_bypass_log_line_is_counted(self):
        # secret-scan-bypasses.log: every dated line is a bypass, no token in it.
        self._w("secret-scan-bypasses.log",
                ["%sT10:00:00+02:00  project=repo1  not a real secret" % self.day])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)
        self.assertEqual(c["per_day"][self.day], 1)

    def test_token_bearing_line_counted_and_kinded(self):
        self._w("stream-merge-bypasses.log",
                ["%sT10:00:00+02:00  cwd=/home/x/repo2  inline-bypass  "
                 "# airuleset:stream-merge-ok override" % self.day])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)
        self.assertEqual(c["per_kind"].get("airuleset:stream-merge-ok"), 1)
        # box derived from cwd basename
        self.assertEqual(c["per_box"].get("repo2"), 1)

    def test_tier0_blocked_line_excluded_bypass_counted(self):
        self._w("tier0-build-bypasses.log", [
            "%sT10:00:00+02:00  project=camera-box  blocked  cmd=cargo build" % self.day,
            "%sT10:01:00+02:00  project=camera-box  inline-bypass  "
            "cmd=cargo build # airuleset:build-ok" % self.day,
        ])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        # only the inline-bypass line counts; the `blocked` line does not
        self.assertEqual(c["total"], 1)

    def test_main_exec_refused_excluded_allowed_counted(self):
        Path(self.tmp, "airuleset-main-exec-bypass-1000.log").write_text(
            "%sT10:00:00+02:00 main-exec bypass refused session=s marker=main-exec-ok (no reason, cleared)\n"
            "%sT10:01:00+02:00 main-exec bypass session=s tool=Edit marker=main-exec-ok (allowed) reason=policy text\n"
            % (self.day, self.day))
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)  # the refused line is not a bypass

    def test_old_line_outside_window_excluded(self):
        self._w("secret-scan-bypasses.log",
                ["2020-01-01T10:00:00+02:00  project=old  ancient bypass"])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp, window_days=7)
        self.assertEqual(c["total"], 0)

    def test_missing_dir_is_empty_not_error(self):
        c = audit.count_cli_bypasses(root=self.audits + "-nope", tmp_dir=self.tmp + "-nope")
        self.assertEqual(c["total"], 0)
        self.assertEqual(c["per_day"], {})

    def test_undated_line_ignored(self):
        self._w("secret-scan-bypasses.log",
                ["not a dated line at all", "%sT10:00:00+02:00  project=r  ok" % self.day])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    # review 🟡 -- the three logs that ALSO carry a #1003 AUTO-exemption line
    # must count only the GENUINE human bypass, never the auto-exemption.
    def test_no_design_merge_exempt_excluded_bypass_counted(self):
        self._w("no-design-skips.log", [
            "%sT10:00:00+02:00  merge-commit exempt from design gate: MERGE_HEAD (#1003)" % self.day,
            "%sT10:01:00+02:00  session=s  [no-design: genuine bypass]" % self.day,
        ])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def test_no_test_red_order_exempt_excluded_bypass_counted(self):
        self._w("no-test-skips.log", [
            "%sT10:00:00+02:00  project=r  sha=abc  docs-only fix commit — exempt "
            "from RED-order gate (#1003)" % self.day,
            "%sT10:01:00+02:00  project=r  sha=def  [no-test: genuine]" % self.day,
        ])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def test_test_skip_mergein_exclusion_excluded_bypass_counted(self):
        self._w("test-skip-bypasses.log", [
            "%sT10:00:00+02:00  project=r  merge-in banned line(s) excluded from scan "
            "(already on main) (#1003)" % self.day,
            "%sT10:01:00+02:00  project=r  sha=abc  test-skip-ok genuine" % self.day,
        ])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def test_main_exec_arm_excluded_allowed_counted(self):
        Path(self.tmp, "airuleset-main-exec-bypass-1000.log").write_text(
            "%sT10:00:00+02:00 main-exec bypass-arm session=s cmd=cargo build\n"
            "%sT10:01:00+02:00 main-exec bypass session=s tool=Edit marker=main-exec-ok "
            "(allowed, deferred consume pending post-exec) reason=policy\n"
            % (self.day, self.day))
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)  # only the (allowed ...) line counts

    def test_main_exec_deferred_pair_counts_once(self):
        # review 🟡 #2 -- a normal deferred main-exec bypass writes TWO lines
        # (the `(allowed, deferred ...)` from block-main-implementation.sh + the
        # `(consumed, post-exec)` from post-consume-main-exec-marker.sh). Count once.
        Path(self.tmp, "airuleset-main-exec-bypass-1000.log").write_text(
            "%sT10:00:00+02:00 main-exec bypass session=s tool=Edit marker=main-exec-ok "
            "(allowed, deferred consume pending post-exec) reason=policy\n"
            "%sT10:00:01+02:00 main-exec bypass session=s (consumed, post-exec) reason=policy\n"
            % (self.day, self.day))
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def test_main_exec_pending_write_failed_still_counted(self):
        # the pending-write-FAILED path (line 619) carries `consumed in PreToolUse`
        # -- a GENUINE honored bypass; the precise `consumed, post-exec` marker
        # must NOT skip it.
        Path(self.tmp, "airuleset-main-exec-bypass-1000.log").write_text(
            "%sT10:00:00+02:00 main-exec bypass session=s tool=Edit marker=main-exec-ok "
            "(allowed, pending-write FAILED, consumed in PreToolUse) reason=policy\n"
            % self.day)
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def test_tier0_inline_bypass_with_blocked_in_cmd_is_counted(self):
        # review 🔵 -- a genuine inline-bypass whose cmd text contains the word
        # "blocked" must NOT be false-skipped (the marker anchors on `  blocked  cmd=`).
        self._w("tier0-build-bypasses.log", [
            "%sT10:00:00+02:00  project=r  inline-bypass  cmd=echo blocked && cargo "
            "build # airuleset:build-ok" % self.day,
        ])
        c = audit.count_cli_bypasses(root=self.audits, tmp_dir=self.tmp)
        self.assertEqual(c["total"], 1)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.audits, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBypassSourceColumn(unittest.TestCase):
    """#1020 Part 2 item 3 -- the --bypasses table gains a `source` column
    (commit|cli) merging both readers into ONE view."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import audit_bounce_rule_updates as abr  # noqa: E402
        self.abr = abr

    def test_text_table_has_source_column(self):
        import io
        import contextlib
        commit = {"per_day": {"2026-09-14": 2}, "per_kind": {"airuleset:test-skip-ok": 2}, "total": 2}
        cli = {"per_day": {"2026-09-14": 3, "2026-09-15": 1},
               "per_kind": {"airuleset:secret-ok": 1, "cli:secret-scan-bypasses": 3}, "total": 4}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.abr.print_bypasses_text(commit, cli)
        out = buf.getvalue()
        self.assertIn("day\tsource\tbypass_tokens", out)
        self.assertIn("2026-09-14\tcommit\t2", out)
        self.assertIn("2026-09-14\tcli\t3", out)
        self.assertIn("2026-09-15\tcli\t1", out)
        self.assertIn("total\tcommit\t2", out)
        self.assertIn("total\tcli\t4", out)

    def test_reader_wired_into_module(self):
        # the script imports the gates.audit reader
        self.assertTrue(hasattr(self.abr, "gates_audit"))
        self.assertTrue(hasattr(self.abr.gates_audit, "count_cli_bypasses"))


if __name__ == "__main__":
    unittest.main()
