"""#1061 item 5 -- audit_bounce_rule_updates.py --design-by: list OPEN issues
whose newest Design-by comment is not `Design-by: main <Fable id>`.
"""
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import audit_bounce_rule_updates as ab  # noqa: E402

FABLE = "claude-fable-5-1"


def _issues_json(pairs):
    return json.dumps([{"number": n, "title": t} for n, t in pairs])


def _comments_stream(comments):
    """One compact JSON object per line, as `gh api ... -q '.[]'` streams it."""
    return "\n".join(json.dumps(c) for c in comments)


def _make_runner(issues, comments_by_issue):
    """A fake matching airuleset._gh_out(*args, timeout=..., cwd=...)."""
    def fake(*args, timeout=None, cwd=None):
        if "list" in args:
            return _issues_json(issues)
        if "api" in args:
            for a in args:
                m = re.search(r"issues/(\d+)/comments", str(a))
                if m:
                    n = int(m.group(1))
                    return _comments_stream(comments_by_issue.get(n, []))
            return ""
        return ""
    return fake


class TestAuditDesignBy(unittest.TestCase):
    def _run(self, issues, comments_by_issue, since=None):
        runner = _make_runner(issues, comments_by_issue)
        return ab.audit_design_by("owner/repo", since=since,
                                  issues_runner=runner, comments_runner=runner)

    def test_main_fable_is_compliant(self):
        v = self._run([(1, "t1")],
                      {1: [{"body": "Design-by: main " + FABLE,
                            "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual(v, [])

    def test_worker_design_is_violation(self):
        v = self._run([(2, "t2")],
                      {2: [{"body": "Design-by: worker claude-opus-4-8",
                            "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["number"], 2)
        self.assertIn("worker", v[0]["design_by"])

    def test_wrong_model_is_violation(self):
        v = self._run([(3, "t3")],
                      {3: [{"body": "Design-by: main claude-opus-4-8",
                            "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual(len(v), 1)

    def test_no_design_by_is_not_counted(self):
        v = self._run([(4, "t4")],
                      {4: [{"body": "just a comment",
                            "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual(v, [])

    def test_1m_tag_compliant(self):
        v = self._run([(5, "t5")],
                      {5: [{"body": "Design-by: main claude-fable-5-1[1m]",
                            "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual(v, [])

    def test_newest_wins(self):
        v = self._run([(6, "t6")],
                      {6: [{"body": "Design-by: main " + FABLE,
                            "created_at": "2026-09-17T09:00:00Z"},
                           {"body": "Design-by: worker claude-opus-4-8",
                            "created_at": "2026-09-17T11:00:00Z"}]})
        self.assertEqual(len(v), 1)  # newest is worker -> violation

    def test_since_filter_excludes_old(self):
        v = self._run([(7, "t7")],
                      {7: [{"body": "Design-by: worker claude-opus-4-8",
                            "created_at": "2026-09-10T09:00:00Z"}]},
                      since="2026-09-17")
        self.assertEqual(v, [])  # predates the fix date

    def test_since_filter_includes_new(self):
        v = self._run([(8, "t8")],
                      {8: [{"body": "Design-by: worker claude-opus-4-8",
                            "created_at": "2026-09-17T09:00:00Z"}]},
                      since="2026-09-17")
        self.assertEqual(len(v), 1)

    def test_mixed_repo(self):
        v = self._run(
            [(1, "ok"), (2, "bad"), (3, "none")],
            {1: [{"body": "Design-by: main " + FABLE,
                  "created_at": "2026-09-17T10:00:00Z"}],
             2: [{"body": "Design-by: worker x",
                  "created_at": "2026-09-17T10:00:00Z"}],
             3: [{"body": "no design", "created_at": "2026-09-17T10:00:00Z"}]})
        self.assertEqual([x["number"] for x in v], [2])


if __name__ == "__main__":
    unittest.main()
