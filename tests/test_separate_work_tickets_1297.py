"""#1297 — separate work gets its own ticket again (reverses the #1259 squeeze).

Owner, verbatim, 2026-11-10: "tvoje tzv zakaz vytvarania novych ticketov
sposobil ze nasilu sa pcha do existujucich ticketov aj veci ktore mali ist o
novych a vysleodk je taky ze sa straca praca".

ROZHODNUTÉ (answer "1"): a NEW ticket for every separate deliverable; a
checklist only for steps of the SAME deliverable. The filing gate drops the
daily cap, the chain-width cap and the net-drain brake; it keeps dedup, no
plan-step tickets and no follow-up-of-a-follow-up chains. Addendum (same day):
the issue DESCRIPTION is the single task list, children are linked both ways
(`Part of #P` / `- [ ] #C`), and a ticket with an unchecked `- [ ]` line can
never be closed.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from test_scope_gate import _fake_gh, _fake_gh_netdrain, body_cmd, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gates.filing import parse, render  # noqa: E402

CLOSE_HOOK = ROOT / "hooks" / "block-close-with-open-checklist.sh"


def _present():
    return "t-1297-present-" + uuid.uuid4().hex[:8]


class TestSeparateWorkFiles(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sw1297-")
        self.cwd = tempfile.mkdtemp(prefix="sw1297-cwd-")

    def test_separate_work_is_an_accepted_reason(self):
        self.assertIn("separate-work", parse.ALLOWED)
        r = run(body_cmd("dstack asks for a TX flow delete", "Requested by o/dstack#9.",
                         scope_gate="separate-work"),
                home=self.home, cwd=self.cwd, session_id=_present())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_planned_work_stays_retired(self):
        self.assertNotIn("planned-work", parse.ALLOWED)
        r = run(body_cmd("step 2", "the second step", scope_gate="planned-work"),
                home=self.home, cwd=self.cwd)
        self.assertIn("-> planned-work-retired", r.stderr)

    def test_a_second_ticket_naming_the_same_parent_passes(self):
        gh = _fake_gh(self.home, {"100": {"title": "the request", "body": "A normal ticket."}})
        for i, topic in enumerate(("alpha defect in the parser", "beta defect in the renderer")):
            r = run(body_cmd("%s (#100 follow-up)" % topic, "Part of #100 (#100 follow-up). %d" % i,
                             scope_gate="separate-work"),
                    home=self.home, cwd=self.cwd, gh_bin=gh, session_id=_present())
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_third_filing_in_a_day_passes(self):
        for i in range(3):
            r = run(body_cmd("distinct %d %s" % (i, uuid.uuid4().hex),
                             "A separate defect %d %s." % (i, uuid.uuid4().hex),
                             scope_gate="cross-cutting"),
                    home=self.home, cwd=self.cwd, session_id=_present())
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_net_drain_no_longer_blocks(self):
        r = run(body_cmd("not draining", "a separate defect found outside the change",
                         scope_gate="separate-work"),
                gh_bin=_fake_gh_netdrain(self.home, created=99, closed=0),
                home=self.home, cwd=self.cwd, session_id=_present())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_follow_up_of_a_follow_up_still_blocks(self):
        gh = _fake_gh(self.home, {"200": {"title": "x (#100 follow-up)",
                                          "body": "Found while fixing #100 (#100 follow-up)."}})
        r = run(body_cmd("y (#200 follow-up)", "Found while fixing #200 (#200 follow-up).", scope_gate="separate-work"),
                home=self.home, cwd=self.cwd, gh_bin=gh, session_id=_present())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("chain-depth-cap", r.stderr)

    def test_no_message_tells_a_session_to_fold_work_into_an_existing_ticket(self):
        for text in (render.BLOCK_MSG, render.WORKER_MSG):
            self.assertNotIn("fold", text.lower())
            self.assertNotIn("belongs ON", text)
        self.assertIn("separate-work", render.BLOCK_MSG)


def _close_gh(tmpdir, issues, fail=False):
    """A fake `gh` answering `gh api repos/<slug>/issues/<n>` from `issues`
    ({"o/r#5": body}); everything else exits 1."""
    bin_dir = Path(tmpdir) / "closebin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, re\n"
        "argv = sys.argv[1:]\n"
        "issues = %r\n"
        "fail = %r\n"
        "if fail: sys.exit(1)\n"
        "if len(argv) >= 2 and argv[0] == 'api':\n"
        "    m = re.match(r'^repos/([^/]+/[^/]+)/issues/(\\d+)$', argv[1])\n"
        "    if m and ('%%s#%%s' %% m.groups()) in issues:\n"
        "        print(json.dumps({'number': int(m.group(2)),\n"
        "                          'body': issues['%%s#%%s' %% m.groups()]}))\n"
        "        sys.exit(0)\n"
        "sys.exit(1)\n" % (issues, fail))
    (bin_dir / "gh").chmod(0o755)
    return str(bin_dir)


def _close(cmd, gh_bin, home):
    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = gh_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(["bash", str(CLOSE_HOOK)],
                          input=json.dumps({"tool_input": {"command": cmd}}),
                          capture_output=True, text=True, env=env,
                          cwd=tempfile.mkdtemp(prefix="sw1297-closecwd-"))


class TestCloseWithOpenChecklist(unittest.TestCase):

    OPEN = "Plan\n- [x] step one — done in abc123\n- [ ] step two\n- [ ] o/other#7\n"
    DONE = "Plan\n- [x] step one — abc123\n- [x] #12 closed\n"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sw1297-close-")
        self.gh = _close_gh(self.home, {"o/r#5": self.OPEN, "o/r#6": self.DONE})

    def test_pure_predicate_counts_every_unchecked_line(self):
        from gates import closechecklist as cc
        self.assertEqual(cc.unchecked_items(self.OPEN), ["step two", "o/other#7"])
        self.assertEqual(cc.unchecked_items(self.DONE), [])
        fenced = "```\n- [ ] an example inside a code block\n```\n"
        self.assertEqual(cc.unchecked_items(fenced), [])
        self.assertEqual(cc.unchecked_items("* [ ] star\n1. [ ] numbered\n"),
                         ["star", "numbered"])

    def test_close_with_an_unchecked_item_is_blocked(self):
        r = _close("gh issue close 5 -R o/r --comment 'done'", self.gh, self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("step two", r.stderr)
        self.assertIn("o/other#7", r.stderr)
        self.assertIn("own ticket", r.stderr)

    def test_close_with_every_item_ticked_passes(self):
        r = _close("gh issue close 6 -R o/r --comment 'done'", self.gh, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_not_planned_close_passes(self):
        r = _close("gh issue close 5 -R o/r --reason 'not planned' -c 'dup'", self.gh, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_api_patch_close_is_checked(self):
        r = _close("gh api repos/o/r/issues/5 -X PATCH -f state=closed", self.gh, self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        r = _close("gh api repos/o/r/issues/5 -X PATCH -f state=closed "
                   "-f state_reason=not_planned", self.gh, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_read_error_fails_open_and_logs(self):
        gh = _close_gh(self.home, {}, fail=True)
        r = _close("gh issue close 5 -R o/r", gh, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = Path(self.home, ".claude", "close-checklist-gate.log").read_text()
        self.assertIn("fail-open", log)

    def test_unrelated_command_is_untouched(self):
        r = _close("gh issue view 5 -R o/r", self.gh, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_is_registered(self):
        text = (ROOT / "settings" / "hooks.json").read_text(encoding="utf-8")
        self.assertIn("hooks/block-close-with-open-checklist.sh", text)


class TestDoctrine(unittest.TestCase):

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_no_dropped_work_orders_a_new_ticket_per_separate_deliverable(self):
        text = self._read("modules/quality/no-dropped-work.md")
        self.assertNotIn("ONE owner request = ONE ticket", text)
        self.assertIn("separate deliverable", text)
        self.assertIn("Part of #", text)
        self.assertIn("Requested by", text)
        self.assertIn("gh issue create -R", text)

    def test_deep_companion_and_durable_decisions_follow(self):
        deep = self._read("skills/no-dropped-work-deep/DEEP.md")
        self.assertIn("separate deliverable", deep)
        dd = self._read("modules/quality/durable-decisions-to-tickets.md")
        self.assertIn("SAME deliverable", dd)
        self.assertNotIn("never split it into child tickets", dd)


if __name__ == "__main__":
    unittest.main()
