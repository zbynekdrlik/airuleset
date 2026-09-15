"""#1020 Part 2 item 3 -- gates.audit.count_cli_bypasses reads the per-hook
CLI-token audit logs (the `~/devel/airuleset/audits/*.log` bypass family + the
`/tmp/airuleset-main-exec-bypass-<uid>.log` family) that `--bypasses` was BLIND
to (it counted merged commit messages only). A genuine honored bypass line
counts; a mixed log's NON-bypass line (tier0 `blocked`, main-exec `refused`)
does not.
"""
import os
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.audits, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
