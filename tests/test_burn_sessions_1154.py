"""#1154 — per-session rows and a canonical weekly window in the hourly fleet
burn snapshot (for claudy's per-group accounting on shared machines).

`scan()` gains a `by_hour_session` breakdown keyed `hour|session_id` (the
same per-line pass, no second parser); `hourly_snapshot()` emits
`sessions: [{project, session_id, usd, msgs, by_model}]` for the previous
full hour plus `weekly_window: [{scope, kind, pct, resets_at, read_ts,
stale}]` from the box's usage cache; `merge_fleet_row()` passes both through
additively. Every test uses synthetic transcripts / caches in temp dirs —
never this box's real `~/.claude/projects` or usage cache, never ssh/network.
"""
import datetime
import json
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import burn  # noqa: E402

NOW = datetime.datetime(2026, 7, 25, 14, 5, 0, tzinfo=datetime.timezone.utc)
PREV = (NOW - datetime.timedelta(hours=1)).astimezone().replace(
    minute=0, second=0, microsecond=0)
PREV_KEY = PREV.strftime("%Y-%m-%dT%H:00")


def _line(model, cr=0, o=0, ts=None, cwd=None, rid=None, sidechain=False):
    e = {"timestamp": (ts or PREV).isoformat(), "isSidechain": sidechain,
         "message": {"model": model, "usage": {
             "input_tokens": 0, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": cr, "output_tokens": o}}}
    if cwd is not None:
        e["cwd"] = cwd
    if rid is not None:
        e["requestId"] = rid
    return json.dumps(e)


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _snapshot(root, cache_path=None, now=NOW):
    return burn.hourly_snapshot(now, root=root, host="dev1", user="z",
                                usage_cache_path=cache_path
                                or os.path.join(root, "no-cache.json"))


class TestScanBySessionBreakdown(unittest.TestCase):
    def test_by_hour_session_is_keyed_hour_pipe_session_id(self):
        with TemporaryDirectory() as tmp:
            _write(Path(tmp) / "-home-z-devel-a" / "sid-a.jsonl",
                   [_line("claude-opus-5", cr=1000000, cwd="/home/z/devel/a")])
            rep = burn.scan(tmp, days=2, now=NOW)
            row = rep["by_hour_session"][PREV_KEY + "|sid-a"]
            self.assertEqual(row["usd"], 0.5)
            self.assertEqual(row["msgs"], 1)
            self.assertEqual(row["project"], "/home/z/devel/a")
            self.assertEqual(row["by_model"], {"claude-opus-5": 0.5})


class TestSnapshotSessions(unittest.TestCase):
    def test_session_sum_equals_host_usd_and_is_sorted_by_usd_desc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "-home-z-devel-claudy" / "s-small.jsonl", [
                _line("claude-opus-5", cr=1000000, cwd="/home/z/devel/claudy"),
                _line("claude-sonnet-5", cr=333333, cwd="/home/z/devel/claudy",
                      ts=PREV + datetime.timedelta(minutes=7)),
            ])
            _write(root / "-home-z-devel-odoo" / "s-big.jsonl", [
                _line("claude-fable-5-1", cr=3000000, o=12345,
                      cwd="/home/z/devel/odoo"),
            ])
            row = _snapshot(tmp)
            sessions = row["sessions"]
            self.assertEqual([s["session_id"] for s in sessions], ["s-big", "s-small"])
            self.assertEqual([s["project"] for s in sessions],
                             ["/home/z/devel/odoo", "/home/z/devel/claudy"])
            self.assertEqual(sorted(sessions[0]), sorted(
                ["project", "session_id", "usd", "msgs", "by_model"]))
            self.assertEqual(sum(s["msgs"] for s in sessions), row["msgs"])
            self.assertAlmostEqual(sum(s["usd"] for s in sessions), row["usd"],
                                   delta=0.01)
            self.assertGreater(row["usd"], 0)
            small = sessions[1]
            self.assertEqual(set(small["by_model"]), {"claude-opus-5", "claude-sonnet-5"})
            self.assertAlmostEqual(sum(small["by_model"].values()), small["usd"],
                                   places=3)

    def test_subagent_transcripts_fold_into_their_parent_session(self):
        with TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "-home-z-devel-a"
            _write(pdir / "parent.jsonl", [
                _line("claude-opus-5", cr=1000000, cwd="/home/z/devel/a")])
            _write(pdir / "parent" / "subagents" / "agent-x.jsonl", [
                _line("claude-sonnet-5", cr=1000000, sidechain=True,
                      cwd="/home/z/devel/a/.claude/worktrees/agent-x")])
            _write(pdir / "parent" / "subagents" / "agent-y.jsonl", [
                _line("claude-opus-5", cr=2000000, sidechain=True,
                      cwd="/home/z/devel/a")])
            row = _snapshot(tmp)
            self.assertEqual(len(row["sessions"]), 1)
            s = row["sessions"][0]
            self.assertEqual(s["session_id"], "parent")
            # the parent's own launch cwd wins over a worktree lane's cwd
            self.assertEqual(s["project"], "/home/z/devel/a")
            self.assertEqual(s["msgs"], 3)
            self.assertAlmostEqual(s["usd"], 0.5 + 0.2 + 1.0, places=4)
            self.assertAlmostEqual(s["usd"], row["usd"], delta=0.01)

    def test_project_falls_back_to_the_slug_when_no_line_carries_a_cwd(self):
        with TemporaryDirectory() as tmp:
            _write(Path(tmp) / "-home-z-devel-nocwd" / "s1.jsonl",
                   [_line("claude-opus-5", cr=1000000)])
            row = _snapshot(tmp)
            self.assertEqual(row["sessions"][0]["project"], "-home-z-devel-nocwd")

    def test_orphan_subagent_cwd_is_used_when_the_parent_has_none(self):
        with TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "-home-z-devel-b"
            _write(pdir / "p.jsonl", [_line("claude-opus-5", cr=1000000)])
            _write(pdir / "p" / "subagents" / "agent-1.jsonl", [
                _line("claude-opus-5", cr=1000000, sidechain=True,
                      cwd="/home/z/devel/b")])
            row = _snapshot(tmp)
            self.assertEqual(row["sessions"][0]["project"], "/home/z/devel/b")

    def test_a_one_session_box_yields_a_one_entry_list(self):
        with TemporaryDirectory() as tmp:
            _write(Path(tmp) / "-home-z-x" / "only.jsonl", [
                _line("claude-opus-5", cr=1000000, cwd="/home/z/x", rid="r1"),
                _line("claude-opus-5", cr=1000000, cwd="/home/z/x", rid="r2",
                      ts=PREV + datetime.timedelta(minutes=30)),
            ])
            row = _snapshot(tmp)
            self.assertEqual(len(row["sessions"]), 1)
            self.assertEqual(row["sessions"][0]["msgs"], 2)
            self.assertAlmostEqual(row["sessions"][0]["usd"], row["usd"], delta=0.01)

    def test_other_hours_and_empty_boxes_contribute_no_sessions(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(_snapshot(tmp)["sessions"], [])
            _write(Path(tmp) / "-p" / "s.jsonl", [
                _line("claude-opus-5", cr=1000000,
                      ts=PREV - datetime.timedelta(hours=1))])
            self.assertEqual(_snapshot(tmp)["sessions"], [])


def _cache(path, ts, windows):
    with open(path, "w") as f:
        json.dump({"ts": ts, "account_email": "z@example.com", "windows": windows}, f)


WINDOWS = [
    {"kind": "five_hour", "group": "session", "percent": 12, "model": None,
     "resets_at": "2026-07-25T18:00:00+00:00", "is_active": True},
    {"kind": "weekly_scoped", "group": "weekly", "percent": 61, "model": "Fable",
     "resets_at": "2026-07-29T08:00:00+00:00", "is_active": True},
    {"kind": "weekly", "group": "weekly", "percent": 40, "model": None,
     "resets_at": "2026-07-28T10:00:00+00:00", "is_active": True},
]


class TestSnapshotWeeklyWindow(unittest.TestCase):
    def test_every_weekly_window_is_emitted_with_its_scope_all_models_first(self):
        with TemporaryDirectory() as tmp:
            cp = os.path.join(tmp, "cache.json")
            read_ts = int(NOW.timestamp()) - 600
            _cache(cp, read_ts, WINDOWS)
            ww = _snapshot(tmp, cp)["weekly_window"]
            self.assertEqual(ww, [
                {"scope": "all-models", "kind": "weekly", "pct": 40,
                 "resets_at": "2026-07-28T10:00:00+00:00", "read_ts": read_ts,
                 "stale": False},
                {"scope": "Fable", "kind": "weekly_scoped", "pct": 61,
                 "resets_at": "2026-07-29T08:00:00+00:00", "read_ts": read_ts,
                 "stale": False},
            ])

    def test_a_cache_older_than_its_ttl_is_marked_stale(self):
        with TemporaryDirectory() as tmp:
            cp = os.path.join(tmp, "cache.json")
            old = int(NOW.timestamp()) - burn.FLEET_WEEKLY_CANDIDATE_MAX_AGE - 60
            _cache(cp, old, WINDOWS)
            ww = _snapshot(tmp, cp)["weekly_window"]
            self.assertEqual(len(ww), 2)
            self.assertTrue(all(w["stale"] is True for w in ww))
            self.assertTrue(all(w["read_ts"] == old for w in ww))

    def test_a_cache_without_a_write_time_is_stale_never_current(self):
        with TemporaryDirectory() as tmp:
            cp = os.path.join(tmp, "cache.json")
            with open(cp, "w") as f:
                json.dump({"windows": WINDOWS}, f)
            ww = _snapshot(tmp, cp)["weekly_window"]
            self.assertTrue(ww and all(w["stale"] and w["read_ts"] is None for w in ww))

    def test_missing_or_malformed_cache_yields_an_empty_list(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(_snapshot(tmp)["weekly_window"], [])
            cp = os.path.join(tmp, "bad.json")
            with open(cp, "w") as f:
                json.dump({"ts": 1, "windows": ["x", {"group": "weekly",
                                                      "percent": True}]}, f)
            self.assertEqual(_snapshot(tmp, cp)["weekly_window"], [])

    def test_existing_fields_are_unchanged(self):
        with TemporaryDirectory() as tmp:
            cp = os.path.join(tmp, "cache.json")
            _cache(cp, int(NOW.timestamp()), WINDOWS)
            row = _snapshot(tmp, cp)
            self.assertEqual(row["account_email"], "z@example.com")
            for k in ("ts", "host", "user", "window_h", "scope", "usd", "msgs",
                      "avg_ctx", "by_model", "main_bash", "main_agent"):
                self.assertIn(k, row)


class TestFleetPassThrough(unittest.TestCase):
    SESSIONS = [{"project": "/p", "session_id": "s", "usd": 1.0, "msgs": 2,
                 "by_model": {"claude-opus-5": 1.0}}]
    WW = [{"scope": "all-models", "kind": "weekly", "pct": 40, "resets_at": "r",
           "read_ts": 1, "stale": False}]

    def test_new_fields_pass_through_into_per_host_unchanged(self):
        row = burn.merge_fleet_row("t", {"dev1": {
            "usd": 1.0, "msgs": 2, "avg_ctx": 1, "by_model": {},
            "sessions": self.SESSIONS, "weekly_window": self.WW}})
        self.assertEqual(row["per_host"]["dev1"]["sessions"], self.SESSIONS)
        self.assertEqual(row["per_host"]["dev1"]["weekly_window"], self.WW)
        self.assertEqual(row["total_usd"], 1.0)

    def test_an_old_shape_row_still_merges_without_the_new_keys(self):
        row = burn.merge_fleet_row("t", {
            "old": {"usd": 2.0, "msgs": 3, "avg_ctx": 5, "by_model": {}},
            "new": {"usd": 1.0, "msgs": 2, "avg_ctx": 1, "by_model": {},
                    "sessions": self.SESSIONS, "weekly_window": self.WW},
            "down": {"error": "ssh timeout"}})
        self.assertNotIn("sessions", row["per_host"]["old"])
        self.assertNotIn("weekly_window", row["per_host"]["old"])
        self.assertEqual(row["per_host"]["down"], {"error": "ssh timeout"})
        self.assertEqual(row["total_usd"], 3.0)
        self.assertEqual(row["total_msgs"], 5)

    def test_remote_row_carries_the_new_fields_over_the_one_ssh_round_trip(self):
        ts = "2026-07-25T17:00:00+00:00"
        snap = {"ts": ts, "host": "dev2", "usd": 1.0, "msgs": 2, "avg_ctx": 1,
                "by_model": {}, "account_email": "z@example.com",
                "sessions": self.SESSIONS, "weekly_window": self.WW}
        cache = {"ts": 1, "account_email": "z@example.com", "windows": [
            {"group": "weekly", "percent": 42, "model": None, "resets_at": "r"}]}
        stdout = (json.dumps(snap) + "\n" + airuleset._FLEET_CACHE_MARKER
                  + "\n" + json.dumps(cache))
        remote = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                  "repo_path": "~/devel/airuleset"}
        with m.patch("subprocess.run", return_value=m.Mock(
                returncode=0, stdout=stdout, stderr="")) as run:
            got = airuleset._fleet_remote_row(remote, airuleset._hour_bucket_of_ts(ts))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(got["sessions"], self.SESSIONS)
        self.assertEqual(got["weekly_window"], self.WW)
        merged = burn.merge_fleet_row(ts, {"dev2": got})
        self.assertEqual(merged["per_host"]["dev2"]["sessions"], self.SESSIONS)
        self.assertEqual(merged["per_host"]["dev2"]["weekly_window"], self.WW)


if __name__ == "__main__":
    unittest.main()
