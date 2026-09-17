"""#1061 item 2 -- gates.designdispatch: an autopilot-worker dispatch is REFUSED
unless the newest `Design-by:` comment on every issue is `Design-by: main
<Fable id>` (fail-closed on an unreadable thread).
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designdispatch as dd  # noqa: E402

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
    return dd.evaluate(_payload(prompt, **kw), fetch=fetch,
                       resolve_slug=lambda cwd: "owner/repo", fable_id=FABLE)


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


class TestEvaluate(unittest.TestCase):
    def test_non_agent_allows(self):
        v, _ = dd.evaluate(_payload("Work issue #1 in repo", tool="Bash"))
        self.assertEqual(v, "allow")

    def test_non_worker_allows(self):
        v, _ = _ev("Work issue #1 in repo", ["Design-by: worker x"],
                   subagent="Explore")
        self.assertEqual(v, "allow")

    def test_no_issue_allows(self):
        v, _ = _ev("do some infra work", [])
        self.assertEqual(v, "allow")

    def test_main_fable_allows(self):
        v, _ = _ev("Work issue #1 in repo",
                   ["Design-by: main claude-fable-5-1"])
        self.assertEqual(v, "allow")

    def test_main_fable_1m_tag_allows(self):
        v, _ = _ev("Work issue #1 in repo",
                   ["Design-by: main claude-fable-5-1[1m]"])
        self.assertEqual(v, "allow")

    def test_worker_design_blocks(self):
        v, r = _ev("Work issue #1 in repo",
                   ["Design-by: worker claude-opus-4-8"])
        self.assertEqual(v, "block")
        self.assertIn("worker", r)

    def test_wrong_model_blocks(self):
        v, r = _ev("Work issue #1 in repo",
                   ["Design-by: main claude-opus-4-8"])
        self.assertEqual(v, "block")
        self.assertIn("Fable", r)

    def test_no_design_by_blocks(self):
        v, r = _ev("Work issue #1 in repo", ["just a normal comment"])
        self.assertEqual(v, "block")
        self.assertIn("no `Design-by:`", r)

    def test_gh_error_blocks_fail_closed(self):
        v, r = _ev("Work issue #1 in repo", None)  # fetch returns None
        self.assertEqual(v, "block")
        self.assertIn("fail-closed", r)

    def test_unresolvable_slug_blocks(self):
        v = dd.evaluate(_payload("Work issue #1 in repo"),
                        fetch=lambda s, n, c: ["Design-by: main " + FABLE],
                        resolve_slug=lambda cwd: None, fable_id=FABLE)[0]
        self.assertEqual(v, "block")

    def test_batch_one_missing_blocks(self):
        v, r = _ev("Work issues #1 #2 in repo",
                   {1: ["Design-by: main claude-fable-5-1"],
                    2: ["no design here"]})
        self.assertEqual(v, "block")
        self.assertIn("#2", r)

    def test_batch_all_main_allows(self):
        v, _ = _ev("Work issues #1 #2 in repo",
                   {1: ["Design-by: main claude-fable-5-1"],
                    2: ["Design-by: main claude-fable-5-1"]})
        self.assertEqual(v, "allow")

    def test_bypass_token_allows(self):
        v, r = dd.evaluate(
            _payload("Work issue #1 in repo\nairuleset:design-by-ok infra hotfix"),
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
        env = dict(os.environ, PATH=ghdir + os.pathsep + os.environ["PATH"])
        # cwd must be a REAL directory -- _resolve_slug runs `gh` with cwd=<the
        # payload cwd>, so a non-existent cwd fails the subprocess (not the gate).
        return subprocess.run(
            ["bash", str(self.HOOK)], input=_payload(prompt, cwd=str(REPO)),
            capture_output=True, text=True, env=env)

    def test_worker_design_blocks_via_adapter(self):
        # one comment per line as `gh api ... -q '.[]'` streams it
        body = json.dumps({"body": "Design-by: worker claude-opus-4-8"})
        r = self._run("Work issue #1 in repo", body)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("main-authored design", r.stderr)

    def test_main_design_allows_via_adapter(self):
        body = json.dumps({"body": "Design-by: main claude-fable-5-1"})
        r = self._run("Work issue #1 in repo", body)
        self.assertEqual(r.returncode, 0,
                         "rc=%d stderr=%r" % (r.returncode, r.stderr))


if __name__ == "__main__":
    unittest.main()
