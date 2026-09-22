"""#1061 item 2 -- gates.designdispatch: an autopilot-worker dispatch is REFUSED
unless the newest `Design-by:` comment on every issue is `Design-by: main
<Fable id>` (fail-closed on an unreadable thread).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designdispatch as dd  # noqa: E402

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

# #1046: some classes call dd.* IN-PROCESS, which _logs to
# ~/.claude/design-by-gate.log via expanduser("~"). Point HOME at a fresh empty
# dir for the whole module (module-scoped save+restore — batch-31-safe).
_A1046_ORIG_HOME = None
_A1046_HOME = None


def setUpModule():
    global _A1046_ORIG_HOME, _A1046_HOME
    _A1046_ORIG_HOME = os.environ.get("HOME")
    _A1046_HOME = tempfile.mkdtemp(prefix="a1046-modhome-")
    os.environ["HOME"] = _A1046_HOME


def tearDownModule():
    if _A1046_ORIG_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _A1046_ORIG_HOME
    shutil.rmtree(_A1046_HOME, ignore_errors=True)

FABLE = "claude-fable-5-1"


def _payload(prompt, subagent="autopilot-worker", tool="Agent", cwd="/repo"):
    return json.dumps({
        "tool_name": tool, "cwd": cwd,
        "tool_input": {"subagent_type": subagent, "prompt": prompt}})


def _ev(prompt, comments, **kw):
    """evaluate() with fetch/resolve_slug/fable_id injected. `comments` may be a
    list (one issue) or a dict {issue: [bodies]}."""
    def fetch(slug, number, cwd):
        if isinstance(comments, dict):
            return comments.get(number)
        return comments
    # #1070 review 🔵: inject is_pr so evaluate() stays network-free — the real
    # _is_pull_request would otherwise shell out to `gh api …/issues/<N>` here.
    return dd.evaluate(_payload(prompt, **kw), fetch=fetch,
                       resolve_slug=lambda cwd: "owner/repo",
                       is_pr=lambda n, slug, cwd: (False, None), fable_id=FABLE)


class TestNewestDesignBy(unittest.TestCase):
    def test_none_when_no_design_by(self):
        self.assertIsNone(dd.newest_design_by(["hello", "world"]))

    def test_newest_wins(self):
        bodies = ["Design-by: worker claude-opus-4-8",
                  "some later comment",
                  "Design-by: main claude-fable-5-1"]
        self.assertEqual(dd.newest_design_by(bodies), ("main", "claude-fable-5-1"))

    def test_later_worker_overrides_earlier_main(self):
        bodies = ["Design-by: main claude-fable-5-1",
                  "Design-by: worker claude-opus-4-8"]
        self.assertEqual(dd.newest_design_by(bodies),
                         ("worker", "claude-opus-4-8"))

    def test_bullet_and_bold_tolerant(self):
        self.assertEqual(
            dd.newest_design_by(["- **Design-by:** main claude-fable-5-1[1m]"]),
            ("main", "claude-fable-5-1[1m]"))


class TestIssueNumbers(unittest.TestCase):
    """#1061 review-3: the fleet's real prompts write the ticket WITHOUT `#`
    (the lane-overlap hook refuses `#N` outside its receipt). Parse `#N` AND bare
    `issue N` / `issues N, M` / `issue #N` / `issue-N`, guarding versions/dates,
    scoped to the lead line so a folded body ref never over-checks."""

    def test_bare_issue_word(self):
        self.assertEqual(
            dd.issue_numbers("Work airuleset issue 1061 (owner escalation)"),
            [1061])

    def test_batch_two_bare_issue_words(self):
        self.assertEqual(
            dd.issue_numbers(
                "Work airuleset issue 1056 lane L1 (with issue 1057 items 1, 2, 3, 5)"),
            [1056, 1057])

    def test_hash_form(self):
        self.assertEqual(dd.issue_numbers("Work issue #1048 FIX-FORWARD"), [1048])

    def test_comma_list_after_issues(self):
        self.assertEqual(dd.issue_numbers("Work issues 1056, 1057"), [1056, 1057])

    def test_hyphen_form(self):
        self.assertEqual(dd.issue_numbers("resume issue-1061 lane"), [1061])

    def test_version_and_date_are_not_tickets(self):
        self.assertEqual(
            dd.issue_numbers("bump to 0.1.326 on 2026-09-17, sha 44fc0045"), [])

    def test_no_ticket(self):
        self.assertEqual(dd.issue_numbers("do some infra work"), [])

    def test_folded_ref_on_a_later_line_is_ignored(self):
        # first-line scoping: a folded "issue 1046" on a body line must not
        # be checked as a worked ticket.
        self.assertEqual(
            dd.issue_numbers(
                "Work airuleset issue 1061 (owner escalation)\n"
                "5b. Stale-base wedge (folded from issue 1046, hit 3 lanes)"),
            [1061])

    def test_items_singledigits_not_captured(self):
        self.assertEqual(
            dd.issue_numbers("Work issue 1061 with items 1, 2, 3, 5"), [1061])


class TestEvaluate(unittest.TestCase):
    def test_non_agent_allows(self):
        v, _ = dd.evaluate(_payload("Work issue 1061 in repo", tool="Bash"))
        self.assertEqual(v, "allow")

    def test_non_worker_allows(self):
        v, _ = _ev("Work issue 1061 in repo", ["Design-by: worker x"],
                   subagent="Explore")
        self.assertEqual(v, "allow")

    def test_no_issue_blocks_fail_closed(self):
        # #1061 review-3: an autopilot-worker dispatch naming no ticket is
        # REFUSED (fail-closed), not allowed.
        v, r = _ev("do some infra work", [])
        self.assertEqual(v, "block")
        self.assertIn("names no ticket", r)

    def test_bare_issue_word_main_allows(self):
        # the real fleet prompt shape (no `#`).
        v, _ = _ev("Work airuleset issue 1061 (owner escalation)",
                   ["Design-by: main claude-fable-5-1"])
        self.assertEqual(v, "allow")

    def test_bare_issue_word_worker_blocks(self):
        v, r = _ev("Work airuleset issue 1061 (owner escalation)",
                   ["Design-by: worker claude-opus-4-8"])
        self.assertEqual(v, "block")
        self.assertIn("worker", r)

    def test_main_fable_allows(self):
        v, _ = _ev("Work issue #1061 in repo",
                   ["Design-by: main claude-fable-5-1"])
        self.assertEqual(v, "allow")

    def test_main_fable_1m_tag_allows(self):
        v, _ = _ev("Work issue #1061 in repo",
                   ["Design-by: main claude-fable-5-1[1m]"])
        self.assertEqual(v, "allow")

    def test_worker_design_blocks(self):
        v, r = _ev("Work issue #1061 in repo",
                   ["Design-by: worker claude-opus-4-8"])
        self.assertEqual(v, "block")
        self.assertIn("worker", r)

    def test_wrong_model_blocks(self):
        v, r = _ev("Work issue #1061 in repo",
                   ["Design-by: main claude-opus-4-8"])
        self.assertEqual(v, "block")
        self.assertIn("Fable", r)

    def test_no_design_by_blocks(self):
        v, r = _ev("Work issue #1061 in repo", ["just a normal comment"])
        self.assertEqual(v, "block")
        self.assertIn("no `Design-by:`", r)

    def test_gh_error_blocks_fail_closed(self):
        v, r = _ev("Work issue #1061 in repo", None)  # fetch returns None
        self.assertEqual(v, "block")
        self.assertIn("fail-closed", r)

    def test_unresolvable_slug_blocks(self):
        v = dd.evaluate(_payload("Work issue #1061 in repo"),
                        fetch=lambda s, n, c: ["Design-by: main " + FABLE],
                        resolve_slug=lambda cwd: None, fable_id=FABLE)[0]
        self.assertEqual(v, "block")

    def test_batch_one_missing_blocks(self):
        v, r = _ev("Work issues #1056 #1057 in repo",
                   {1056: ["Design-by: main claude-fable-5-1"],
                    1057: ["no design here"]})
        self.assertEqual(v, "block")
        self.assertIn("#1057", r)

    def test_batch_all_main_allows(self):
        v, _ = _ev("Work issues #1056 #1057 in repo",
                   {1056: ["Design-by: main claude-fable-5-1"],
                    1057: ["Design-by: main claude-fable-5-1"]})
        self.assertEqual(v, "allow")

    def test_bypass_token_allows(self):
        v, r = dd.evaluate(
            _payload("do infra work\nairuleset:design-by-ok infra hotfix"),
            fetch=lambda s, n, c: ["no design"],
            resolve_slug=lambda cwd: "owner/repo", fable_id=FABLE)
        self.assertEqual(v, "allow")
        self.assertIn("bypass", r)


class TestAdapterEndToEnd(unittest.TestCase):
    """Drive the REAL adapter with a fake `gh` on PATH -- exercises -P, PYTHONPATH
    resolution, and the exit-2 stderr contract."""

    HOOK = REPO / "hooks" / "block-dispatch-without-main-design.sh"

    def _fake_gh(self, comments_json):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        gh = Path(d) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "repo" ]; then echo "owner/repo"; exit 0; fi\n'
            'if [ "$1" = "api" ]; then cat <<\'JSON\'\n%s\nJSON\n'
            "exit 0; fi\nexit 0\n" % comments_json)
        gh.chmod(0o755)
        return d

    def _run(self, prompt, comments_json):
        ghdir = self._fake_gh(comments_json)
        env = hermetic_hook_env(self, PATH=ghdir + os.pathsep + os.environ["PATH"])
        # cwd must be a REAL directory -- _resolve_slug runs `gh` with cwd=<the
        # payload cwd>, so a non-existent cwd fails the subprocess (not the gate).
        return subprocess.run(
            ["bash", str(self.HOOK)], input=_payload(prompt, cwd=str(REPO)),
            capture_output=True, text=True, env=env)

    def test_worker_design_blocks_via_adapter(self):
        # one comment per line as `gh api ... -q '.[]'` streams it
        body = json.dumps({"body": "Design-by: worker claude-opus-4-8"})
        r = self._run("Work issue #1061 in repo", body)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("main-authored design", r.stderr)

    def test_main_design_allows_via_adapter(self):
        body = json.dumps({"body": "Design-by: main claude-fable-5-1"})
        r = self._run("Work issue #1061 in repo", body)
        self.assertEqual(r.returncode, 0,
                         "rc=%d stderr=%r" % (r.returncode, r.stderr))


if __name__ == "__main__":
    unittest.main()
