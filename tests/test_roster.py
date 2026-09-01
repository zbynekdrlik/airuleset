"""#804 — the durable expected-armed roster (`watchdog/roster.py`)."""
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import roster


class RosterStore(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "goal-roster.json"

    def test_load_missing_is_empty(self):
        self.assertEqual(roster.load_roster(self.path), {})

    def test_upsert_then_load_roundtrip(self):
        r = {}
        roster.upsert(r, "/repo/a", "sid-1", "full", 1000)
        self.assertTrue(roster.save_roster(r, self.path))
        got = roster.load_roster(self.path)
        self.assertEqual(got["/repo/a"]["sid"], "sid-1")
        self.assertEqual(got["/repo/a"]["authority"], "full")
        self.assertEqual(got["/repo/a"]["armed_ts"], 1000)

    def test_armed_ts_preserved_across_refresh_sid_updates(self):
        # A re-observation is NOT a re-arm: armed_ts is the #400-style anchor,
        # preserved; sid/last_seen refresh (session id changes on resurrection).
        r = {}
        roster.upsert(r, "/repo/a", "sid-1", "full", 1000)
        roster.upsert(r, "/repo/a", "sid-2", "full", 2000)
        self.assertEqual(r["/repo/a"]["armed_ts"], 1000)
        self.assertEqual(r["/repo/a"]["sid"], "sid-2")
        self.assertEqual(r["/repo/a"]["last_seen_ts"], 2000)

    def test_drop_removes_and_reports(self):
        r = {}
        roster.upsert(r, "/repo/a", "sid-1", "full", 1000)
        self.assertTrue(roster.drop(r, "/repo/a"))
        self.assertNotIn("/repo/a", r)
        self.assertFalse(roster.drop(r, "/repo/a"))

    def test_upsert_ignores_falsy_cwd_and_bad_roster(self):
        r = {}
        roster.upsert(r, "", "sid", "full", 1)
        self.assertEqual(r, {})
        roster.upsert(None, "/repo/a", "sid", "full", 1)  # no raise

    def test_corrupt_entry_dropped_on_load(self):
        self.path.write_text(json.dumps({"/repo/a": {"sid": "x"}, "/repo/b": "junk"}))
        got = roster.load_roster(self.path)
        self.assertIn("/repo/a", got)
        self.assertNotIn("/repo/b", got)

    def test_non_dict_top_level_is_empty(self):
        self.path.write_text(json.dumps(["not", "a", "dict"]))
        self.assertEqual(roster.load_roster(self.path), {})

    def test_save_unwritable_never_raises(self):
        # A path whose parent cannot be created returns False, never raises.
        bad = Path("/proc/nonexistent-dir-xyz/goal-roster.json")
        self.assertFalse(roster.save_roster({"a": {}}, bad))


class DeadEntries(unittest.TestCase):
    def test_dead_entries_are_the_rostered_cwds_with_no_live_candidate(self):
        r = {}
        roster.upsert(r, "/repo/live", "s1", "full", 1)
        roster.upsert(r, "/repo/dead", "s2", "full", 1)
        dead = roster.dead_entries(r, {"/repo/live"})
        self.assertEqual([cwd for cwd, _ in dead], ["/repo/dead"])

    def test_all_live_is_empty(self):
        r = {}
        roster.upsert(r, "/repo/a", "s1", "full", 1)
        self.assertEqual(roster.dead_entries(r, {"/repo/a"}), [])

    def test_empty_roster_is_empty(self):
        self.assertEqual(roster.dead_entries({}, set()), [])

    def test_dead_entries_never_mutates(self):
        r = {"/repo/dead": {"sid": "s", "armed_ts": 1}}
        roster.dead_entries(r, set())
        self.assertEqual(r, {"/repo/dead": {"sid": "s", "armed_ts": 1}})


class RosterCli(unittest.TestCase):
    """`airuleset.py goal-roster` (list + --drop). The conftest
    `_isolate_goal_roster` fixture points AIRULESET_GOAL_ROSTER_PATH at a
    per-test file, so these never touch the real ~/.claude."""

    def _args(self, drop=""):
        return type("A", (), {"drop": drop})()

    def test_list_empty(self):
        import airuleset
        airuleset.cmd_goal_roster(self._args())  # no raise on empty

    def test_list_then_drop(self):
        import airuleset
        r = {}
        roster.upsert(r, "/repo/x", "sid1", "full", 1000)
        roster.save_roster(r)
        airuleset.cmd_goal_roster(self._args())  # lists (no raise)
        airuleset.cmd_goal_roster(self._args(drop="/repo/x"))
        self.assertEqual(roster.load_roster(), {})

    def test_drop_absent_exits_nonzero(self):
        import airuleset
        with self.assertRaises(SystemExit) as cm:
            airuleset.cmd_goal_roster(self._args(drop="/repo/nope"))
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
