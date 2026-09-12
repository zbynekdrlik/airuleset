"""#992/#993 — the `airuleset.py lane-overlap` independence-check helper +
its receipt, and the enforcement in hooks/block-dispatch-over-wdrain.sh.

Before dispatching a lane the supervisor runs `lane-overlap --paths <p1,p2>
--topics <t> --issue <N>` which compares the candidate unit's touched paths +
topic against every LIVE lane (worktree wip refs / goal lane records) and open
PR file list, prints CLEAR/OVERLAP, and writes a per-cwd receipt. The dispatch
hook then BLOCKS an autopilot-worker dispatch whose issue(s) have no fresh
overlap-check receipt (the independence check must have been run for that unit).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_overlap as lo  # noqa: E402

HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"


class TestComputeOverlap(TestCase):
    def test_path_overlap_with_a_live_lane_is_reported(self):
        verdict, overlaps = lo.compute_overlap(
            paths=["airuleset.py", "hooks/x.sh"],
            topics=["autopilot orchestration"],
            live_lanes=[{"ref": "worktree-agent-1",
                         "files": ["airuleset.py"], "topic": "model tiering"}],
            open_prs=[])
        self.assertEqual(verdict, "overlap")
        self.assertTrue(any(o[0] == "lane" for o in overlaps))

    def test_path_overlap_with_an_open_pr_is_reported(self):
        verdict, overlaps = lo.compute_overlap(
            paths=["watchdog/goal.py"], topics=["x"],
            live_lanes=[],
            open_prs=[{"number": 42, "files": ["watchdog/goal.py"],
                       "title": "goal fix"}])
        self.assertEqual(verdict, "overlap")
        self.assertTrue(any(o[0] == "pr" for o in overlaps))

    def test_disjoint_paths_and_topics_are_clear(self):
        verdict, overlaps = lo.compute_overlap(
            paths=["a.py"], topics=["alpha"],
            live_lanes=[{"ref": "w1", "files": ["b.py"], "topic": "beta"}],
            open_prs=[{"number": 1, "files": ["c.py"], "title": "gamma"}])
        self.assertEqual(verdict, "clear")
        self.assertEqual(overlaps, [])

    def test_topic_overlap_is_reported_even_without_a_path_clash(self):
        verdict, overlaps = lo.compute_overlap(
            paths=["a.py"], topics=["autopilot orchestration rework"],
            live_lanes=[{"ref": "w1", "files": ["b.py"],
                         "topic": "autopilot orchestration"}],
            open_prs=[])
        self.assertEqual(verdict, "overlap")
        self.assertTrue(any("topic" in o[0] for o in overlaps))


class TestReceipt(TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lo-home-")

    def test_write_receipt_records_issues_verdict_and_ts(self):
        p = lo.write_receipt(self.home, "cwdkey123", [993, 992],
                             "clear", [])
        got = json.loads(Path(p).read_text())
        self.assertEqual(sorted(got["checked_issues"]), [992, 993])
        self.assertEqual(got["verdict"], "clear")
        self.assertIsInstance(got["ts"], (int, float))

    def test_receipt_path_is_under_lane_overlap_dir(self):
        p = lo.write_receipt(self.home, "abc", [1], "overlap",
                             [["lane", "w1", ["x.py"]]])
        self.assertIn("/lane-overlap/", p)
        self.assertTrue(p.endswith("abc.json"))

    def test_receipt_records_deps_satisfied(self):
        # #993 item 7: the receipt records the dispatched unit's dep state.
        p = lo.write_receipt(self.home, "d1", [993], "clear", [],
                             deps="satisfied")
        got = json.loads(Path(p).read_text())
        self.assertEqual(got["deps"], "satisfied")

    def test_receipt_deps_defaults_satisfied(self):
        p = lo.write_receipt(self.home, "d2", [1], "clear", [])
        got = json.loads(Path(p).read_text())
        self.assertEqual(got["deps"], "satisfied")


class TestResolveIssueDeps(TestCase):
    """#993 item 7 — cli_work_class.resolve_issue_deps: 'satisfied' or the list
    of unsatisfied blocking refs, for the lane-overlap receipt."""

    def _runner(self, bodies, states):
        import json as _j

        def runner(argv, _cwd):
            n = int(argv[argv.index("view") + 1])
            joined = " ".join(argv)
            if "body,comments" in joined:
                b, c = bodies.get(n, ("", []))
                return _j.dumps({"body": b, "comments": [{"body": x} for x in c]})
            if "state" in joined:
                repo = argv[argv.index("-R") + 1] if "-R" in argv else None
                st = states.get((repo, n)) or states.get((None, n))
                return _j.dumps({"state": st}) if st else "{}"
            return "{}"
        return runner

    def test_no_deps_is_satisfied(self):
        import cli_work_class as wc
        r = self._runner({993: ("no deps here", [])}, {})
        self.assertEqual(wc.resolve_issue_deps([993], "o/r", r, "/root"),
                         "satisfied")

    def test_closed_dep_is_satisfied(self):
        import cli_work_class as wc
        r = self._runner({993: ("Depends-on: #10", [])}, {("o/r", 10): "CLOSED"})
        self.assertEqual(wc.resolve_issue_deps([993], "o/r", r, "/root"),
                         "satisfied")

    def test_open_dep_is_unsatisfied(self):
        import cli_work_class as wc
        r = self._runner({993: ("Depends-on: #10", [])}, {("o/r", 10): "OPEN"})
        self.assertEqual(wc.resolve_issue_deps([993], "o/r", r, "/root"),
                         ["#10"])


def _dispatch_payload(prompt, cwd, home):
    return json.dumps({
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "autopilot-worker", "prompt": prompt},
        "cwd": cwd,
    })


class TestHookReceiptGate(TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lo-hook-home-")
        self.cwd = tempfile.mkdtemp(prefix="lo-hook-cwd-")
        import hashlib
        self.cwd_key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]

    def _run(self, prompt):
        env = {**os.environ, "HOME": self.home}
        return subprocess.run(
            ["bash", str(HOOK)],
            input=_dispatch_payload(prompt, self.cwd, self.home),
            capture_output=True, text=True, env=env, timeout=60)

    def _seed_receipt(self, issues, ts=None):
        d = Path(self.home) / ".claude" / "lane-overlap"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"checked_issues": issues, "ts": ts or time.time(),
               "verdict": "clear", "overlaps": []}
        (d / (self.cwd_key + ".json")).write_text(json.dumps(rec))

    def test_dispatch_without_a_receipt_is_blocked(self):
        r = self._run("Work issue #993 in airuleset")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("overlap", r.stderr.lower())

    def test_dispatch_with_a_fresh_covering_receipt_is_allowed(self):
        self._seed_receipt([993])
        r = self._run("Work issue #993 in airuleset")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_receipt_not_covering_the_issue_is_blocked(self):
        self._seed_receipt([111])
        r = self._run("Work issue #993 in airuleset")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_stale_receipt_is_blocked(self):
        self._seed_receipt([993], ts=time.time() - 4000)
        r = self._run("Work issue #993 in airuleset")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_overlap_bypass_token_allows(self):
        r = self._run("OVERLAP-BYPASS: solo infra lane, no live lanes\nWork issue #993")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_batch_requires_all_issues_covered(self):
        self._seed_receipt([41, 43])
        r = self._run("Work issues #41 #43 #47 in camera-box as one bundled PR")
        self.assertEqual(r.returncode, 2, r.stderr)
        self._seed_receipt([41, 43, 47])
        r2 = self._run("Work issues #41 #43 #47 in camera-box as one bundled PR")
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_comma_and_separated_batch_is_fully_covered(self):
        # #993-review: "Work issues #41, #43 and #47" must require ALL three,
        # not just #41 (the span regex used to truncate at the first non-space).
        self._seed_receipt([41, 43])
        r = self._run("Work issues #41, #43 and #47 in camera-box")
        self.assertEqual(r.returncode, 2, r.stderr)
        self._seed_receipt([41, 43, 47])
        r2 = self._run("Work issues #41, #43 and #47 in camera-box")
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_non_autopilot_worker_dispatch_is_untouched(self):
        env = {**os.environ, "HOME": self.home}
        payload = json.dumps({
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "Work issue #993"},
            "cwd": self.cwd})
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
