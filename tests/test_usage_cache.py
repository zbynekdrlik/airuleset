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
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

import airuleset
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

    def test_default_path_resolved_at_call_time(self):
        # The default path MUST be resolved from the module global at call time, not
        # bound at def time — else patching it (in tests) can't stop check_usage from
        # clobbering the real ~/.claude cache. This is the regression that leaked once.
        orig = watchdog._USAGE_CACHE_PATH
        with tempfile.TemporaryDirectory() as d:
            patched = os.path.join(d, "cache.json")
            watchdog._USAGE_CACHE_PATH = patched
            try:
                watchdog.write_usage_cache(SAMPLE_USAGE, 42)   # NO explicit path
            finally:
                watchdog._USAGE_CACHE_PATH = orig
            self.assertTrue(os.path.exists(patched), "must write to the patched global path")


class TestStatuslineRendersEndToEnd(TestCase):
    """Run the ACTUAL generated shim with a controlled HOME + stdin. Locks the
    regression that crashed the whole meter: reset() did int(ts), but the cache
    carries ISO-8601 resets_at (CC stdin carries an epoch int) — the ISO string
    raised ValueError and blanked every segment."""

    def _render(self, home, stdin_json):
        shim = os.path.join(home, "shim.sh")
        Path(shim).write_text(airuleset.CAVEMAN_SHIM_CONTENT)
        env = dict(os.environ, HOME=home)
        out = subprocess.run(["bash", shim], input=stdin_json, env=env,
                             capture_output=True, text=True, timeout=20).stdout
        # strip ANSI
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", out)

    def test_fable_segment_renders_with_iso_reset(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            now = time.time()
            iso_future = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                       time.gmtime(now + 40 * 3600))
            cache = {"ts": int(now), "windows": [
                {"kind": "session", "group": "session", "percent": 13, "model": None,
                 "resets_at": iso_future, "is_active": False},
                {"kind": "weekly_scoped", "group": "weekly", "percent": 25, "model": "Fable",
                 "resets_at": iso_future, "is_active": True},
            ]}
            Path(os.path.join(home, ".claude", "airuleset-usage-cache.json")).write_text(
                json.dumps(cache))
            stdin = json.dumps({
                "context_window": {"used_percentage": 32, "context_window_size": 200000},
                "rate_limits": {
                    "five_hour": {"used_percentage": 13, "resets_at": int(now + 9000)},
                    "seven_day": {"used_percentage": 15, "resets_at": int(now + 138000)},
                },
            })
            out = self._render(home, stdin)
            # The shared windows still render (the ISO crash used to blank them too)...
            self.assertIn("5h 13%", out)
            self.assertIn("wk 15%", out)
            self.assertIn("ctx", out)
            # ...and the per-model Fable window now appears.
            self.assertIn("Fable 25%", out)

    def test_stale_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            now = time.time()
            cache = {"ts": int(now - 7 * 3600), "windows": [  # >6h old
                {"group": "weekly", "percent": 25, "model": "Fable",
                 "resets_at": "2099-01-01T00:00:00+00:00", "is_active": True}]}
            Path(os.path.join(home, ".claude", "airuleset-usage-cache.json")).write_text(
                json.dumps(cache))
            stdin = json.dumps({"rate_limits": {
                "five_hour": {"used_percentage": 13, "resets_at": int(now + 9000)}}})
            out = self._render(home, stdin)
            self.assertIn("5h 13%", out)
            self.assertNotIn("Fable", out)


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


class TestFableGate(TestCase):
    """Budget gate for AUTOMATIC Fable escalation (model-tiering 2026-07-03).

    The user wants hard tasks escalated to Fable AUTOMATICALLY — but the
    2026-07-01 Fable-everywhere incident (weekly limits tripped mid-work, work
    stopped) must never repeat, so the gate is fail-safe CLOSED on any doubt and
    CLOSED once either weekly window (Fable-scoped or shared) reaches the
    threshold."""

    NOW = 1_700_000_000

    def _cache(self, d, windows, ts=None):
        path = os.path.join(d, "cache.json")
        with open(path, "w") as f:
            json.dump({"ts": ts if ts is not None else self.NOW,
                       "windows": windows}, f)
        return path

    def _win(self, percent, model=None, group="weekly"):
        return {"kind": "weekly_scoped" if model else "weekly_all",
                "group": group, "percent": percent, "model": model,
                "resets_at": "2026-07-06T10:00:00+00:00", "is_active": bool(model)}

    def test_open_when_both_windows_have_headroom(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(44), self._win(52, model="Fable")])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertTrue(ok)
            self.assertIn("fable=52%", reason)
            self.assertIn("weekly=44%", reason)

    def test_closed_when_fable_window_at_threshold(self):
        # >= is CLOSED (boundary included — 80% used with an 80 gate = no headroom).
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(10), self._win(80, model="Fable")])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertFalse(ok)
            self.assertIn("fable window at 80%", reason)

    def test_closed_when_shared_weekly_high_even_if_fable_low(self):
        # Fable burn counts against the shared weekly too — a nearly-spent shared
        # window closes the gate regardless of Fable's own headroom.
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(92), self._win(10, model="Fable")])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertFalse(ok)
            self.assertIn("weekly window at 92%", reason)

    def test_missing_cache_is_fail_safe_closed(self):
        ok, reason = watchdog.fable_gate(now=self.NOW, path="/nonexistent/cache.json")
        self.assertFalse(ok)
        self.assertIn("no usage cache", reason)

    def test_stale_cache_is_fail_safe_closed(self):
        # Older than the 6h staleness bound → the numbers are unknown → CLOSED.
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(1), self._win(1, model="Fable")],
                            ts=self.NOW - 7 * 3600)
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p)
            self.assertFalse(ok)
            self.assertIn("stale", reason)

    def test_no_weekly_windows_is_fail_safe_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(13, group="session")])
            # a session-only cache has no weekly signal at all → CLOSED
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p)
            self.assertFalse(ok)
            self.assertIn("no weekly window", reason)

    def test_no_fable_window_gates_on_shared_weekly_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(30)])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertTrue(ok)
            self.assertIn("weekly=30%", reason)

    def test_session_window_does_not_gate(self):
        # The 5h session window resets within hours — it must NOT close the gate
        # (that would block escalation exactly when the user works most; the
        # incident being prevented was the WEEKLY trip).
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(99, group="session"),
                                self._win(40), self._win(50, model="Fable")])
            ok, _ = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertTrue(ok)

    def test_env_threshold_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(40), self._win(50, model="Fable")])
            os.environ["AIRULESET_FABLE_GATE_PCT"] = "45"
            try:
                ok, _ = watchdog.fable_gate(now=self.NOW, path=p)
                self.assertFalse(ok)          # fable 50 >= env gate 45
            finally:
                del os.environ["AIRULESET_FABLE_GATE_PCT"]

    # --- review findings F1/F2/F3 (adversarial pass, 2026-07-03) — each was a
    # verified FAIL-OPEN or contract violation before the fix. -----------------

    def test_future_ts_is_fail_safe_closed(self):
        # F1: a FUTURE ts (writer clock skew / corrupt write) makes age negative;
        # a plain `age > MAX` check calls that "fresh" FOREVER — the gate would
        # stay OPEN on frozen numbers even after the watchdog died. Any age
        # outside [0, MAX] is unknown → CLOSED.
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [self._win(10), self._win(10, model="Fable")],
                            ts=self.NOW + 1_000_000_000)
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p)
            self.assertFalse(ok)
            self.assertIn("stale", reason)

    def test_multiple_fable_windows_take_the_max_and_only_weekly(self):
        # F2: selection was last-wins with no group filter — a fable-scoped
        # SESSION window at 10% could mask a fable WEEKLY at 95% (order-dependent
        # fail-open). Only weekly windows gate, and the MAX percent decides.
        with tempfile.TemporaryDirectory() as d:
            p = self._cache(d, [
                self._win(95, model="Fable"),                       # weekly, binding
                self._win(10, model="Fable 5", group="session"),    # must not mask
                self._win(40),
            ])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p, threshold=80)
            self.assertFalse(ok)
            self.assertIn("fable window at 95%", reason)

    def test_corrupt_cache_shapes_are_closed_not_raised(self):
        # F3: valid-JSON-but-wrong-shape must return (False, reason), never raise —
        # a caller unpacking (ok, reason) crashing IS a gate failure.
        cases = [
            "[1, 2, 3]",                                            # list top-level
            '{"ts": "abc", "windows": []}',                         # string ts
            '{"ts": %d, "windows": "garbage"}' % self.NOW,          # string windows
            '{"ts": %d, "windows": [{"group": "weekly", "model": null,'
            ' "percent": "95"}]}' % self.NOW,                       # string percent
        ]
        with tempfile.TemporaryDirectory() as d:
            for i, raw in enumerate(cases):
                p = os.path.join(d, "c%d.json" % i)
                Path(p).write_text(raw)
                ok, reason = watchdog.fable_gate(now=self.NOW, path=p)
                self.assertFalse(ok, "case %d must be CLOSED: %s" % (i, raw))

    def test_bool_percent_is_unknown_not_one_percent(self):
        # F3 sub-case: JSON `true` percent parsed as int 1 would read "1% used" →
        # OPEN on a corrupt value. bool is not a percent — skip the window; with
        # no other weekly window left the gate is CLOSED.
        with tempfile.TemporaryDirectory() as d:
            win = self._win(0, model="Fable")
            win["percent"] = True
            p = self._cache(d, [win])
            ok, reason = watchdog.fable_gate(now=self.NOW, path=p)
            self.assertFalse(ok)
            self.assertIn("no weekly window", reason)

    def test_cli_registration(self):
        self.assertIn("fable-gate", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["fable-gate"], airuleset.cmd_fable_gate)


if __name__ == "__main__":
    main()
