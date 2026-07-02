"""Locks the per-model usage window on the statusline (2026-07-02).

The user wanted to see "Fable's 5h limit" at the bottom, since Fable has its own
measurement. Ground truth from a live oauth/usage call: the 5-hour "session" window
is ACCOUNT-WIDE (scope=null) — there is NO per-model 5h. What IS split per model is
the WEEKLY window: `limits[]` carries a `weekly_scoped` entry with
`scope.model.display_name = "Fable"` (the binding one under max-performance). So the
statusline shows Fable's per-model WEEKLY window, not a (non-existent) per-model 5h.

CC's statusLine stdin `rate_limits` only exposes the SHARED five_hour + seven_day, so
the per-model window comes from the api-watchdog's oauth/usage cache (the endpoint
429s hard — the statusline must never poll it). These assertions lock the parse, the
cache write, the check_usage wiring, and the statusline read.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

import watchdog

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# The exact shape a live oauth/usage call returned (2026-07-02).
SAMPLE_USAGE = {
    "limits": [
        {"kind": "session", "group": "session", "percent": 13, "resets_at": "2026-07-02T07:10:00+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 15, "resets_at": "2026-07-03T19:00:00+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 25, "resets_at": "2026-07-03T19:00:00+00:00", "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": True},
    ]
}


class TestUsageWindowsParse(TestCase):
    def test_flattens_shared_and_per_model(self):
        ws = watchdog.usage_windows(SAMPLE_USAGE)
        self.assertEqual(len(ws), 3)
        session = [w for w in ws if w["group"] == "session"][0]
        # The 5h session window is account-wide — model is None (no per-model 5h).
        self.assertIsNone(session["model"])
        self.assertEqual(session["percent"], 13)
        fable = [w for w in ws if w["model"] == "Fable"][0]
        # Fable's separate measurement is WEEKLY, not 5h.
        self.assertEqual(fable["group"], "weekly")
        self.assertEqual(fable["percent"], 25)
        self.assertTrue(fable["is_active"])

    def test_skips_null_percent_and_survives_garbage(self):
        self.assertEqual(watchdog.usage_windows({}), [])
        self.assertEqual(watchdog.usage_windows(None), [])
        self.assertEqual(watchdog.usage_windows({"limits": [{"percent": None}]}), [])


class TestUsageCacheWrite(TestCase):
    def test_writes_ts_and_windows_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.json")
            watchdog.write_usage_cache(SAMPLE_USAGE, 1_700_000_000, path=path)
            got = json.load(open(path))
            self.assertEqual(got["ts"], 1_700_000_000)
            self.assertTrue(any(w["model"] == "Fable" for w in got["windows"]))
            # No leftover temp file.
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_never_raises_on_bad_path(self):
        # A non-writable path must be swallowed, not raised.
        watchdog.write_usage_cache(SAMPLE_USAGE, 1, path="/nonexistent-dir/x/cache.json")


class TestWiring(TestCase):
    def test_check_usage_writes_the_cache(self):
        src = read("watchdog/__init__.py")
        self.assertIn("write_usage_cache(data, now)", src)

    def test_statusline_shim_reads_per_model_cache(self):
        src = read("airuleset.py")
        self.assertIn("airuleset-usage-cache.json", src)
        # Iterates cached windows and skips the shared ones (model is None).
        self.assertIn('model = w.get("model")', src)
        self.assertIn("skip the shared windows", src)


if __name__ == "__main__":
    main()
