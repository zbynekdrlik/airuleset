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

    def test_close_without_repo_flag_resolves_the_cwd_remote(self):
        cwd = tempfile.mkdtemp(prefix="sw1297-git-")
        subprocess.run(["git", "init", "-q", cwd], check=True)
        subprocess.run(["git", "-C", cwd, "remote", "add", "origin",
                        "https://github.com/o/r.git"], check=True)
        env = dict(os.environ, HOME=self.home, PATH=self.gh + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", str(CLOSE_HOOK)], capture_output=True, text=True, env=env,
                           input=json.dumps({"tool_input": {"command": "gh issue close 5"}}),
                           cwd=cwd)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestCloseTargets(unittest.TestCase):
    """Review round 1: the close parser, per segment (no gh needed)."""

    def _t(self, cmd):
        from gates import closechecklist as cc
        return sorted((repo, num) for repo, num, *_ in cc.close_targets(cmd)[0])

    def test_a_body_edit_patch_is_not_a_close(self):
        self.assertEqual(self._t("gh api repos/o/r/issues/5 -X PATCH -F body=@new.md"), [])
        self.assertEqual(self._t("gh api repos/o/r/issues/5 -X PATCH --input body.json"), [])

    def test_each_close_keeps_its_own_repo(self):
        self.assertEqual(self._t("gh issue close 5 -R a/b && gh issue close 7 -R c/d"),
                         [("a/b", "5"), ("c/d", "7")])

    def test_global_repo_flag_and_issue_url(self):
        self.assertEqual(self._t("gh -R a/b issue close 5"), [("a/b", "5")])
        self.assertEqual(self._t("gh issue close https://github.com/a/b/issues/9"),
                         [("a/b", "9")])

    def test_patch_url_shapes(self):
        for path in ("repos/o/r/issues/5", "https://api.github.com/repos/o/r/issues/5",
                     "'repos/o/r/issues/5'", "--method=PATCH repos/o/r/issues/5"):
            cmd = "gh api %s -X PATCH -f state=closed" % path
            self.assertEqual(self._t(cmd), [("o/r", "5")], cmd)
        self.assertEqual(self._t("gh api repos/{owner}/{repo}/issues/5 -X PATCH -f state=closed"),
                         [(None, "5")])

    def test_patch_close_only_counts_its_own_segment(self):
        self.assertEqual(self._t("gh api repos/o/r/issues/5 -X PATCH -f state=closed && "
                                 "gh api repos/o/r/issues/6/comments"), [("o/r", "5")])
        self.assertEqual(self._t("gh api repos/o/r/issues/5 -X PATCH -f state=closed; "
                                 "echo 'state_reason=not_planned'"), [("o/r", "5")])

    def test_heredoc_body_with_trailing_redirect_is_data(self):
        cmd = "cat <<EOF > notes.md\ngh issue close 9\nEOF\ngit add notes.md"
        self.assertEqual(self._t(cmd), [])

    def _notes(self, cmd):
        from gates import closechecklist as cc
        return cc.close_targets(cmd)[1]

    def test_review2_repo_sources_and_flag_values(self):
        # a cd the gate cannot follow never falls back to the hook cwd's repo
        self.assertEqual(self._t("cd $SOMEWHERE && gh issue close 6"), [])
        self.assertTrue(self._notes("cd $SOMEWHERE && gh issue close 6"))
        self.assertEqual(self._t("GH_REPO=a/b gh issue close 6"), [("a/b", "6")])
        # a flag-looking value inside --comment is not a -R flag
        self.assertEqual(self._t('gh issue close 6 -R o/r -c "-Rework landed"'), [("o/r", "6")])

    def test_review2_wrapped_command_words(self):
        for cmd in ("(gh issue close 5 -R o/r)", "sudo -u x gh issue close 5 -R o/r",
                    "for i in 1; do gh issue close 5 -R o/r; done"):
            self.assertEqual(self._t(cmd), [("o/r", "5")], cmd)

    def test_review2_quoted_heredoc_marker_does_not_swallow_a_close(self):
        cmd = 'echo "use <<EOF syntax"\ngh issue close 5 -R o/r'
        self.assertEqual(self._t(cmd), [("o/r", "5")])

    def test_review2_no_read_starts_that_cannot_finish_in_budget(self):
        from gates import closechecklist as cc
        body = json.dumps({"number": 5, "body": "- [ ] open"})
        verdict, note = cc.evaluate("gh issue close 5 -R o/r", "/", runner=lambda a: (0, body, ""),
                                    budget_s=2.0)
        self.assertEqual(verdict, "allow")
        self.assertIn("time budget", note)

    def test_review2_markdown_edges(self):
        from gates import closechecklist as cc
        self.assertEqual(cc.unchecked_items("- [ ] real\n<!-- unclosed\n- [ ] hidden\n"), ["real"])
        self.assertEqual(cc.unchecked_items("```\n```info\n- [ ] code\n```\n"), [])

    def test_task_lines_in_comments_and_quotes(self):
        from gates import closechecklist as cc
        self.assertEqual(cc.unchecked_items("<!-- - [ ] template hint -->\n> - [ ] quoted\n"),
                         ["quoted"])
        self.assertEqual(cc.unchecked_items("```\n~~~\n- [ ] in code\n```\n- [ ] real\n"),
                         ["real"])


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
        self.assertIn("never a comment on its ticket", text)

    def test_no_surface_still_orders_folding_or_a_live_net_drain(self):
        bad = ("fold the finding onto the existing ticket", "net-drain discipline that gates",
               "rate-gated EXCEPTION", "which of the six criteria it clears",
               "'- [ ]' item / comment on the current ticket", "ONLY when ALL of these are true")
        hits = []
        for top in ("modules", "skills", "agents", "hooks", "gates", "rules"):
            for p in (ROOT / top).rglob("*"):
                if p.is_file() and p.suffix in (".md", ".sh", ".py", ".conf"):
                    text = p.read_text(encoding="utf-8", errors="replace")
                    hits += ["%s: %s" % (p.relative_to(ROOT), b) for b in bad if b in text]
        self.assertEqual(hits, [])

    def test_deep_companion_and_durable_decisions_follow(self):
        deep = self._read("skills/no-dropped-work-deep/DEEP.md")
        self.assertIn("separate deliverable", deep)
        dd = self._read("modules/quality/durable-decisions-to-tickets.md")
        self.assertIn("SAME deliverable", dd)
        self.assertNotIn("never split it into child tickets", dd)


if __name__ == "__main__":
    unittest.main()
