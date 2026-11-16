"""#1343 (owner 16.11.2026) — owner-facing clock times are Europe/Bratislava
local time, never raw UTC.

Incident: devbridge quoted CI times read raw from `gh` (UTC, `…Z`) as
"hotovo okolo 21:00" while it was already 21:35 CET for the owner, who read
it as a lie. `hooks/stop-check-prose-violations.sh` now HARD-blocks a final
message that quotes a raw UTC clock time in prose: an ISO timestamp ending in
`Z`, or an `HH:MM` followed by `UTC`/`GMT`/`Z`.

Kept unblocked (false positives cost the owner a re-loop): fenced code, inline
code, double-quoted mentions, blockquoted log lines, file names / paths that
embed a timestamp, commit hashes, version strings, a bare local time, the word
UTC without a time, and a local time that ALSO states UTC in parentheses.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import (  # noqa: E402
    MODULE_HOOK_HOME, sweep_session_files)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"


def _run(msg, home=None):
    sid = "utc1343-%s" % uuid.uuid4().hex[:10]
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    env = {**os.environ, "HOME": home or MODULE_HOOK_HOME}
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300, env=env)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


def _utc_blocked(p):
    return _blocked(p) and "UTC" in p.stdout and "Europe/Bratislava" in p.stdout


class TestRawUtcTimesAreBlocked(TestCase):

    def _assert_blocks(self, msg):
        p = _run(msg)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertTrue(
            _utc_blocked(p),
            "raw UTC time NOT blocked: %r stdout=%r stderr=%r"
            % (msg, p.stdout[:300], p.stderr.strip()[-300:]))
        return p

    def test_iso_z_timestamp(self):
        self._assert_blocks("CI dobehla o 2026-11-16T20:45:07Z.")

    def test_iso_z_with_space_and_fraction(self):
        self._assert_blocks("Deploy skončil 2026-11-16 20:45:07.123Z, všetko zelené.")

    def test_iso_z_without_seconds(self):
        self._assert_blocks("Run started 2026-11-16T20:45Z and is still going.")

    def test_hour_followed_by_utc(self):
        self._assert_blocks("Hotovo okolo 21:00 UTC.")

    def test_hour_glued_utc(self):
        self._assert_blocks("Release window opens at 06:30UTC tomorrow.")

    def test_hour_followed_by_z(self):
        self._assert_blocks("Last push landed at 20:45Z.")

    def test_hour_followed_by_gmt(self):
        self._assert_blocks("The job ran at 03:15 GMT.")

    def test_seconds_followed_by_utc(self):
        self._assert_blocks("Merged at 20:45:07 UTC on main.")

    def test_range_end_utc(self):
        self._assert_blocks("Okno bolo 20:45-21:00 UTC.")

    def test_utc_only_in_parentheses_without_local_time(self):
        # A parenthesised UTC hour is fine only next to a LOCAL time on the
        # same line — alone in parentheses it is still a raw UTC hour.
        self._assert_blocks("CI dobehla (21:00 UTC).")

    def test_block_message_carries_conversion_hint(self):
        p = self._assert_blocks("Hotovo okolo 21:00 UTC.")
        self.assertIn("TZ=Europe/Bratislava date -d", p.stdout)
        self.assertIn("airuleset:utc-time-ok", p.stdout)


class TestNegativesStayUnblocked(TestCase):

    def _assert_passes(self, msg):
        p = _run(msg)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertFalse(
            _utc_blocked(p),
            "false positive on %r stdout=%r" % (msg, p.stdout[:400]))

    def test_fenced_code_block(self):
        self._assert_passes(
            "Log z behu:\n\n```\n2026-11-16T20:45:07Z build ok\n"
            "21:00 UTC deploy\n```\n\nVšetko prešlo.")

    def test_inline_code(self):
        self._assert_passes("Pole `createdAt` vracia `2026-11-16T20:45:07Z`.")

    def test_double_quoted_mention(self):
        self._assert_passes('Hook teraz blokuje vetu "hotovo okolo 21:00 UTC".')

    def test_blockquoted_log_line(self):
        self._assert_passes("Chyba z logu:\n\n> 2026-11-16T20:45:07Z ERROR timeout\n\nOpravené.")

    def test_local_time_with_utc_in_parentheses(self):
        self._assert_passes("CI dobehla o 21:45 (20:45 UTC).")

    def test_local_time_with_iso_in_parentheses(self):
        self._assert_passes("CI dobehla o 21:45 (2026-11-16T20:45:07Z).")

    def test_file_name_with_timestamp(self):
        self._assert_passes("Výstup je v run-2026-11-16T20:45:07Z.log na disku.")

    def test_path_with_timestamp(self):
        self._assert_passes("Uložené do /var/log/ci/2026-11-16T20:45:07Z/out.json.")

    def test_commit_hash_and_version(self):
        self._assert_passes("Commit c1fa044e nasadený ako v0.1.672 (build 2026.11.16).")

    def test_bare_local_time(self):
        self._assert_passes("Hotovo o 21:45, nasadené.")

    def test_utc_word_without_time(self):
        self._assert_passes("Časy z gh sú v UTC, preto ich prepočítavam na miestny čas.")

    def test_name_starting_with_z_after_time(self):
        self._assert_passes("O 21:45 Zbynek dostal správu.")

    def _home(self):
        td = tempfile.TemporaryDirectory(prefix="utc1343-home-")
        self.addCleanup(td.cleanup)
        return td.name

    def test_bypass_marker_passes_and_is_logged(self):
        home = self._home()
        p = _run("Server loguje v 20:45 UTC (bez lokálneho času).\n"
                 "airuleset:utc-time-ok citujem serverový log", home=home)
        self.assertFalse(_utc_blocked(p), p.stdout[:300])
        # #1343 review F5: the ONE fleet bypass audit dir (gates.audit), so
        # count_cli_bypasses sees it — never a parallel private log.
        log = os.path.join(home, "devel", "airuleset", "audits",
                           "utc-time-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass was not logged")
        with open(log, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("airuleset:utc-time-ok", body)
        self.assertIn("citujem serverový log", body)
        from gates import audit
        counted = audit.count_cli_bypasses(
            root=os.path.dirname(log), tmp_dir=home)
        self.assertEqual(counted["per_kind"].get("airuleset:utc-time-ok"), 1)


class TestReviewFindings(TestCase):
    """#1343 fresh-context review (F1-F7, F10), each reproduced live."""

    def _blocks(self, msg):
        p = _run(msg)
        self.assertTrue(_utc_blocked(p), "NOT blocked: %r" % msg)

    def _passes(self, msg):
        p = _run(msg)
        self.assertFalse(_utc_blocked(p), "false positive: %r" % msg)

    def test_f1_local_offset_is_local_time(self):
        self._passes("Hotovo 21:45 UTC+1.")
        self._passes("Hotovo 21:45 GMT+1, nasadené.")
        self._passes("Leto: 22:45 UTC+02:00.")

    def test_f1_zero_offset_is_still_utc(self):
        self._blocks("Hotovo 20:45 UTC+0.")
        self._blocks("Hotovo 20:45 UTC+00:00.")

    def test_f2_timestamp_in_url_query(self):
        self._passes("Pozri https://example.com/x?since=2026-11-16T20:45:07Z")
        self._passes("Pozri https://example.com/x?since=2026-11-16T20:45:07Z&a=1 tam.")

    def test_f3_lowercase_utc(self):
        self._blocks("Hotovo 20:45 utc.")

    def test_f4_bypass_needs_a_reason(self):
        self._blocks("Hotovo 20:45 UTC.\nairuleset:utc-time-ok")

    def test_f4_backticked_marker_is_a_mention_not_a_bypass(self):
        self._blocks("Hotovo 20:45 UTC. Bypass je `airuleset:utc-time-ok <reason>`.")

    def test_f7_slovak_preposition_z_after_time(self):
        self._passes("Odchod 7:30 Z Košíc, príchod 9:10.")

    def test_f10_square_brackets_like_parentheses(self):
        self._passes("Bratislava 21:45 [20:45 UTC].")

    def test_f6_many_exempt_lines_stay_fast(self):
        msg = "\n".join("- 21:45 (20:45 UTC) item %d" % i for i in range(3000))
        t = time.monotonic()
        p = _run(msg)
        self.assertLess(time.monotonic() - t, 8.0)
        self.assertFalse(_utc_blocked(p))

    def test_f6_many_parentheses_on_one_line_stay_fast(self):
        msg = "21:45 " + "(x)" * 20000 + " (20:45 UTC)"
        t = time.monotonic()
        p = _run(msg)
        self.assertLess(time.monotonic() - t, 8.0)
        self.assertFalse(_utc_blocked(p))

if __name__ == "__main__":
    main()
