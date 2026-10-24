"""#1258/#1259 review round 1 — the gaps two adversarial reviews found.

- `user-request` was the new split door: a child naming a parent is now
  width-capped whatever its criterion, and an unattended `user-request` is
  daily-capped.
- the untracked-work Stop hook blocked "the rest stays in the #N checklist" and
  ordered one `gh issue create` per part.
- a no-board account still got the client-board doctrine injected, and an
  explicit config path bypassed the no-board guard.
- the install wiring was untested and shared a try block with the box class.
- the frontmatter checker missed ` #` (YAML cuts the value) and reserved first
  characters; agents/autopilot-worker.md was silently cut to 391 chars.
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402
from test_scope_gate import _fake_gh, body_cmd, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import airuleset  # noqa: E402
import cli_config  # noqa: E402
import cli_odoo_board  # noqa: E402
import cli_odoo_ro  # noqa: E402
from gates.filing.caps import DAILY_CAP  # noqa: E402

HOOKS = ROOT / "hooks"


def _away_sid(testcase):
    sid = "t-rf1259-" + uuid.uuid4().hex[:10]
    mark = Path("/tmp/claude-user-active-%s" % sid)
    mark.write_text("")
    old = time.time() - 1000
    os.utime(mark, (old, old))
    testcase.addCleanup(lambda: mark.unlink(missing_ok=True))
    return sid


class TestUserRequestIsNoSplitDoor(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rf1259-")
        self.cwd = tempfile.mkdtemp(prefix="rf1259-cwd-")

    def test_retired_planned_work_names_its_own_reason(self):
        r = run(body_cmd("step 2", "the second step", scope_gate="planned-work"),
                home=self.home, cwd=self.cwd)
        self.assertIn("-> planned-work-retired", r.stderr)

    def test_a_user_request_child_of_a_parent_is_width_capped(self):
        gh = _fake_gh(self.home, {"100": {"title": "the request", "body": "A normal ticket."}})
        r = run(body_cmd("part one (#100 follow-up)", "Part of #100 (#100 follow-up).",
                         scope_gate="cross-cutting"), home=self.home, cwd=self.cwd, gh_bin=gh)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(body_cmd("part two (#100 follow-up)", "Another part of #100 (#100 follow-up).",
                         scope_gate="user-request"), home=self.home, cwd=self.cwd, gh_bin=gh)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> chain-width-cap", r.stderr)

    def test_an_away_user_request_is_daily_capped(self):
        quote = "Owner %s, verbatim: \"urob to\"." % datetime.date.today().strftime("%d.%m.%Y")
        for i in range(DAILY_CAP):
            r = run(body_cmd("asked %d" % i, "%s Distinct topic %d %s." % (quote, i, uuid.uuid4().hex),
                             scope_gate="user-request"),
                    home=self.home, cwd=self.cwd, session_id=_away_sid(self))
            self.assertEqual(r.returncode, 0, r.stderr)
        r = run(body_cmd("asked past", "%s One more topic %s." % (quote, uuid.uuid4().hex),
                         scope_gate="user-request"),
                home=self.home, cwd=self.cwd, session_id=_away_sid(self))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> daily-cap", r.stderr)

    def test_a_present_owner_request_is_not_daily_capped(self):
        for i in range(DAILY_CAP + 1):
            r = run(body_cmd("owner asked %d" % i, "Distinct topic %d %s." % (i, uuid.uuid4().hex),
                             scope_gate="user-request"),
                    home=self.home, cwd=self.cwd, session_id="t-rf-present-" + uuid.uuid4().hex[:8])
            self.assertEqual(r.returncode, 0, r.stderr)


class TestUntrackedWorkHookAcceptsTheChecklist(unittest.TestCase):

    def _blocked(self, text):
        sid = "t-rf-ndw-" + uuid.uuid4().hex[:10]
        for f in ("/tmp/airuleset-untracked-work-block-" + sid,):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        p = subprocess.run(["bash", str(HOOKS / "stop-check-untracked-work.sh")],
                           input=json.dumps({"session_id": sid, "last_assistant_message": text}),
                           capture_output=True, text=True, env=hermetic_hook_env(self))
        return '"block"' in p.stdout, p.stdout

    def test_leftover_recorded_in_the_ticket_checklist_passes(self):
        blocked, out = self._blocked(
            "Done with step 1. The remaining steps stay as - [ ] items in the #42 checklist.")
        self.assertFalse(blocked, out)

    def test_leftover_with_no_record_still_blocks_and_says_record_it(self):
        blocked, out = self._blocked("Done with step 1. The rest can wait for later.")
        self.assertTrue(blocked, out)
        self.assertIn("Recorded on #N", out)
        self.assertNotIn("for each unfinished part, gh issue create", out)


class TestNoBoardAccount(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rf1258-")
        self.claude = Path(self.home, ".claude")
        self.claude.mkdir()

    def test_an_explicit_config_path_is_refused_when_the_board_is_off(self):
        cfg = self.claude / cli_odoo_ro.CONFIG_BASENAME
        cfg.write_text(json.dumps({"url": "https://erp.slovnormal.sk"}), encoding="utf-8")
        (self.claude / cli_odoo_board.BOARD_MARKER).write_text("off\n", encoding="utf-8")
        self.assertIsNone(cli_odoo_ro.load_config(str(cfg), home=self.home))

    def test_writing_off_clears_a_stale_task_hygiene_status(self):
        st = self.claude / "task-hygiene" / "status.json"
        st.parent.mkdir()
        st.write_text("{}", encoding="utf-8")
        cli_odoo_board.write_board_marker(self.claude, "david2")
        self.assertFalse(st.exists())

    def test_install_step_writes_and_clears_the_marker(self):
        marker = self.claude / cli_odoo_board.BOARD_MARKER
        with mock.patch("airuleset._current_user", return_value="david3"), \
             mock.patch.object(airuleset, "CLAUDE_DIR", self.claude):
            airuleset._write_box_class_marker()
        self.assertEqual(marker.read_text(encoding="utf-8"), "off\n")
        with mock.patch("airuleset._current_user", return_value="montalu1"), \
             mock.patch.object(airuleset, "CLAUDE_DIR", self.claude):
            airuleset._write_box_class_marker()
        self.assertFalse(marker.exists())

    def test_the_marker_survives_a_box_class_failure(self):
        with mock.patch("airuleset._current_user", return_value="david4"), \
             mock.patch.object(airuleset, "CLAUDE_DIR", self.claude), \
             mock.patch("cli_box_class.box_class_for_user", side_effect=RuntimeError("boom")):
            airuleset._write_box_class_marker()
        self.assertTrue((self.claude / cli_odoo_board.BOARD_MARKER).exists())

    def _prompt_injection(self, marker):
        env = hermetic_hook_env(self, TMPDIR=tempfile.mkdtemp(prefix="rf1258-tmp-"))
        if marker:
            d = Path(env["HOME"], ".claude")
            d.mkdir(parents=True, exist_ok=True)
            (d / cli_odoo_board.BOARD_MARKER).write_text("off\n", encoding="utf-8")
        payload = json.dumps({"session_id": "rf1258-" + uuid.uuid4().hex[:8],
                              "hook_event_name": "UserPromptSubmit",
                              "prompt": "❓ odpovedz klientovi na úlohu o termináli"})
        r = subprocess.run(["bash", str(HOOKS / "inject-situational-rule.sh")], input=payload,
                           capture_output=True, text=True, env=env)
        return r.stdout

    def test_board_doctrine_is_injected_on_a_board_account(self):
        self.assertIn("odoo-client-board-questions", self._prompt_injection(marker=False))

    def test_board_doctrine_is_not_injected_when_the_board_is_off(self):
        self.assertNotIn("odoo-client-board", self._prompt_injection(marker=True))


class TestFrontmatterShapes(unittest.TestCase):

    def _errs(self, line):
        return cli_config.frontmatter_errors("---\n%s\n---\n" % line)

    def test_a_space_hash_cuts_the_value(self):
        self.assertTrue(self._errs("description: works on #N tickets"))

    def test_reserved_first_characters(self):
        for v in ("@x", "`x`", "%x", "*x", "!foo"):
            self.assertTrue(self._errs("k: " + v), v)
        self.assertEqual(self._errs("k: !!str a"), [])

    def test_crlf_and_bom_frontmatter_is_checked(self):
        self.assertTrue(cli_config.frontmatter_errors("﻿---\r\nk: a: b\r\n---"))

    def test_unclosed_quote(self):
        self.assertTrue(self._errs('k: "abc'))

    def test_autopilot_worker_description_is_whole(self):
        text = (ROOT / "agents" / "autopilot-worker.md").read_text(encoding="utf-8")
        self.assertEqual(cli_config.frontmatter_errors(text), [])
        self.assertTrue(text.split("\n")[2].startswith("description: '"))


if __name__ == "__main__":
    unittest.main()
