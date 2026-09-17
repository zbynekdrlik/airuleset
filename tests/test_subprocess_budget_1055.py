"""#1055 P2 — subprocess budget per sweep.

(a) a per-sweep subprocess COUNTER (`reset_subprocess_stats` /
`record_subprocess` / `subprocess_stats` / `run_counted`) fed by every
watchdog runner (`_default_run`, `_gh_out`, `run_counted`), journaled next to
P1's `transcript reads:` line as `subprocess: N calls, Ws, top: label:n,...`.

(b) a per-sweep MEMO namespace (`begin_sweep_memo` / `end_sweep_memo` /
`memoized`) that collapses identical subprocess calls within ONE sweep — one
`tmux list-panes -a` shared by `list_claude_panes` + `_reconcile_candidate_panes`,
one `capture-pane`/`display-message` per pane, one `ps -u` shared by the reaper
jobs. The memo is ACTIVE only between begin/end (run_once), so a direct call
outside a sweep (a unit test, a CLI path) is never memoized — behaviour there is
byte-identical to today.

Behaviour lock: a memo HIT is decision-identical to a fresh read (the world
cannot change mid-sweep), and it issues NO child, so the counter shows exactly
the saving the memos buy. RED on base: none of these names exist.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402
import watchdog.long_turn as lt  # noqa: E402
import watchdog.reaper as reaper  # noqa: E402


class TestSubprocessCounter(unittest.TestCase):
    def setUp(self):
        wd.reset_subprocess_stats()

    def test_reset_zeroes(self):
        wd.record_subprocess("gh", 0.1)
        wd.reset_subprocess_stats()
        s = wd.subprocess_stats()
        self.assertEqual(s["n"], 0)
        self.assertEqual(s["wall"], 0.0)
        self.assertEqual(s["by_label"], {})

    def test_record_counts_wall_and_labels(self):
        wd.record_subprocess("gh", 0.5)
        wd.record_subprocess("gh", 0.25)
        wd.record_subprocess("git", 1.0)
        s = wd.subprocess_stats()
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["wall"], 1.75, places=5)
        self.assertEqual(s["by_label"], {"gh": 2, "git": 1})
        # top sorted by count desc, then label
        self.assertEqual(s["top"][0], ("gh", 2))
        self.assertEqual(s["top"][1], ("git", 1))

    def test_record_bad_wall_is_ignored(self):
        wd.record_subprocess("gh", None)
        wd.record_subprocess("gh", "x")
        s = wd.subprocess_stats()
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["wall"], 0.0)

    def test_run_counted_returns_and_records(self):
        seen = []

        def fake(argv, **kw):
            seen.append((argv, kw))
            return "RESULT"

        out = wd.run_counted(["gh", "issue", "list"], run=fake, timeout=5)
        self.assertEqual(out, "RESULT")
        self.assertEqual(seen[0][0], ["gh", "issue", "list"])
        self.assertEqual(seen[0][1], {"timeout": 5})
        s = wd.subprocess_stats()
        self.assertEqual(s["n"], 1)
        self.assertEqual(s["by_label"], {"gh": 1})

    def test_run_counted_explicit_label(self):
        wd.run_counted(["git", "-C", "/x", "fetch"], label="git", run=lambda a, **k: "")
        self.assertEqual(wd.subprocess_stats()["by_label"], {"git": 1})

    def test_run_counted_records_on_exception(self):
        def boom(argv, **kw):
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            wd.run_counted(["git", "fetch"], run=boom)
        self.assertEqual(wd.subprocess_stats()["n"], 1)

    def test_default_run_records_real_subprocess(self):
        out = wd._default_run(["echo", "hi"])
        self.assertIn("hi", out)
        s = wd.subprocess_stats()
        self.assertEqual(s["n"], 1)
        self.assertEqual(s["top"][0][0], "echo")

    def test_gh_out_records(self):
        import airuleset
        # `gh --version` is a real, side-effect-free gh invocation
        airuleset._gh_out("--version")
        s = wd.subprocess_stats()
        self.assertEqual(s["by_label"].get("gh"), 1)


class TestSweepMemo(unittest.TestCase):
    def tearDown(self):
        wd.end_sweep_memo()

    def test_memo_inactive_by_default(self):
        wd.end_sweep_memo()
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        a = wd.memoized(("k",), compute)
        b = wd.memoized(("k",), compute)
        self.assertEqual((a, b), (1, 2))  # NOT memoized outside a sweep

    def test_memo_active_collapses(self):
        wd.begin_sweep_memo()
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        a = wd.memoized(("k",), compute)
        b = wd.memoized(("k",), compute)
        self.assertEqual((a, b), (1, 1))
        self.assertEqual(len(calls), 1)

    def test_end_clears_memo(self):
        wd.begin_sweep_memo()
        wd.memoized(("k",), lambda: 42)
        wd.end_sweep_memo()
        # a fresh sweep must not serve the previous sweep's value
        wd.begin_sweep_memo()
        self.assertEqual(wd.memoized(("k",), lambda: 99), 99)


class TestPaneInventoryMemo(unittest.TestCase):
    RAW = ("%1\tclaude\t/a\t111\n"
           "%2\tnode\t/b\t222\n"
           "%3\tclaude\t/c\t333\n")

    def tearDown(self):
        wd.end_sweep_memo()

    def test_one_list_panes_shared_within_sweep(self):
        rec = []

        def run(argv):
            rec.append(list(argv))
            return self.RAW

        wd.begin_sweep_memo()
        panes = wd.list_claude_panes(run=run)
        cand = lt._reconcile_candidate_panes(run)
        wd.end_sweep_memo()

        listp = [a for a in rec if a[:3] == ["tmux", "list-panes", "-a"]]
        self.assertEqual(len(listp), 1, "the two readers must share one list-panes")
        # list_claude_panes parses only `claude` foreground panes
        self.assertIn(("%1", "/a"), panes)
        self.assertIn(("%3", "/c"), panes)
        self.assertNotIn(("%2", "/b"), panes)
        # _reconcile widens to claude/node/bun off the SAME raw
        self.assertIn(("%1", "/a", "claude"), cand)
        self.assertIn(("%2", "/b", "node"), cand)

    def test_not_shared_when_memo_inactive(self):
        rec = []

        def run(argv):
            rec.append(list(argv))
            return self.RAW

        wd.list_claude_panes(run=run)
        lt._reconcile_candidate_panes(run)
        listp = [a for a in rec if a[:3] == ["tmux", "list-panes", "-a"]]
        self.assertEqual(len(listp), 2, "direct calls (no sweep) each run their own query")


class TestPaneOwnerMemo(unittest.TestCase):
    def tearDown(self):
        wd.end_sweep_memo()

    def test_pane_owner_memoized_within_sweep(self):
        rec = []

        def run(argv):
            rec.append(list(argv))
            return "zbynek"

        wd.begin_sweep_memo()
        a = wd.pane_owner("%1", run)
        b = wd.pane_owner("%1", run)
        wd.end_sweep_memo()
        self.assertEqual((a, b), ("zbynek", "zbynek"))
        dm = [x for x in rec if len(x) > 1 and x[1] == "display-message"]
        self.assertEqual(len(dm), 1, "owner is static mid-sweep -> one display-message")

    def test_capture_pane_stays_FRESH_even_within_a_sweep(self):
        # #1055 P2: capture_pane is deliberately NOT memoized -- every same-pane
        # recapture in the code is an intentional fresh read (race re-verify /
        # render-settle / parked-wake liveness), so a within-sweep repeat MUST
        # issue a new capture-pane. This is the freshness contract the (b) memo
        # deliberately excludes.
        rec = []
        seq = iter(["FIRST", "SECOND"])

        def run(argv):
            rec.append(list(argv))
            return next(seq)

        wd.begin_sweep_memo()
        a = wd.capture_pane("%1", run)
        b = wd.capture_pane("%1", run)
        wd.end_sweep_memo()
        self.assertEqual((a, b), ("FIRST", "SECOND"))
        caps = [x for x in rec if len(x) > 1 and x[1] == "capture-pane"]
        self.assertEqual(len(caps), 2, "capture must stay fresh, never memoized")


class TestPsSnapshotMemo(unittest.TestCase):
    def tearDown(self):
        wd.end_sweep_memo()

    def test_ps_snapshot_shared_object_within_sweep(self):
        wd.begin_sweep_memo()
        a = reaper.default_ps_fetch()
        b = reaper.default_ps_fetch()
        wd.end_sweep_memo()
        if a is None:
            self.skipTest("ps unavailable on this box")
        self.assertIs(a, b, "the 4 reaper jobs must share ONE ps snapshot per sweep")

    def test_ps_not_shared_outside_sweep(self):
        a = reaper.default_ps_fetch()
        b = reaper.default_ps_fetch()
        if a is None:
            self.skipTest("ps unavailable on this box")
        self.assertIsNot(a, b)


if __name__ == "__main__":
    unittest.main()
