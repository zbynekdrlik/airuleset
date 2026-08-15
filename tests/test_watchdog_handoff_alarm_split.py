"""#433 item G step 13 — `watchdog/handoff_alarm.py` split.

The three cross-stream job-11 stale-hand-off-alarm helpers (`_parse_gh_ts`,
`_normalize_gkreq`, `_stale_handoff_alarm`) were moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.handoff_alarm`, then re-exported IN PLACE
by a positional facade import. This is a BACK-REFERENCE module (convention C3):
`_stale_handoff_alarm` reaches its sibling package-level names at call time
through the package namespace, so every `watchdog.<name>` monkeypatch seam stays
effective. These tests lock the split invariants:

  1. `import watchdog` stays clean in a FRESH subprocess — no circular-import
     breakage from `handoff_alarm.py`'s `import watchdog` back-reference or its
     `from watchdog import GKREQ_REPING_SCHEDULE_S` (bound in `__init__` above
     the facade-import position, proven unpatched).
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.handoff_alarm.<name>` — the C2 re-export contract.
  3. The def-time default `schedule=GKREQ_REPING_SCHEDULE_S` is preserved (C4).
  4. The back-reference SEAMS stay live: patching `watchdog.STALE_HANDOFF_ALARM`,
     `watchdog.GKREQ_STALE_HANDOFF_S`, or `watchdog._gkreq_reping_due` at the
     PACKAGE level still changes `_stale_handoff_alarm`'s behaviour, because the
     moved body reaches those names call-time through `watchdog.` (a regression
     to a frozen `from watchdog import <CONST>` would silently break these —
     the design's #1 monkeypatch-decoupling hazard).
"""

import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.handoff_alarm as handoff_alarm  # noqa: E402

# Every name moved into handoff_alarm.py (3 functions; NO constants — the two
# related constants STALE_HANDOFF_ALARM / _STALE_HANDOFF_EXCLUDE_LABELS stay in
# __init__, and GKREQ_* stay in __init__ too). Kept explicit as the reviewer's
# checklist against the facade import block in __init__.py.
MOVED_NAMES = [
    "_parse_gh_ts",
    "_normalize_gkreq",
    "_stale_handoff_alarm",
]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_handoff_alarm_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (parent-package init runs first, binding GKREQ_REPING_SCHEDULE_S
        # before the from-import in handoff_alarm.py executes).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.handoff_alarm as h; "
             "print(h._parse_gh_ts('2026-08-13T07:31:02Z'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "1786606262")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(handoff_alarm, name),
                                f"{name} missing from watchdog.handoff_alarm")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(
                    getattr(watchdog, name), getattr(handoff_alarm, name),
                    f"watchdog.{name} is not watchdog.handoff_alarm.{name}")

    def test_handoff_alarm_lives_in_its_own_module_file(self):
        self.assertTrue(
            handoff_alarm.__file__.endswith("watchdog/handoff_alarm.py"),
            handoff_alarm.__file__)

    def test_moved_functions_report_the_new_module_qualname(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.handoff_alarm")


class DefTimeDefaultAndConstantHomes(unittest.TestCase):
    def test_schedule_default_preserved_as_reping_schedule(self):
        import inspect
        sig = inspect.signature(watchdog._stale_handoff_alarm)
        self.assertEqual(sig.parameters["schedule"].default,
                         watchdog.GKREQ_REPING_SCHEDULE_S)

    def test_reping_schedule_from_import_is_same_object(self):
        # C4: def-time default imported `from watchdog` at module top.
        self.assertIs(handoff_alarm.GKREQ_REPING_SCHEDULE_S,
                      watchdog.GKREQ_REPING_SCHEDULE_S)

    def test_body_constants_are_NOT_frozen_into_the_submodule(self):
        # The two body-referenced constants stay resident in __init__ and are
        # reached call-time via `watchdog.<CONST>` — they are deliberately NOT
        # from-imported into handoff_alarm (that would freeze a stale value and
        # break the monkeypatch seam). Their absence from the submodule
        # namespace is the structural proof of the call-time choice.
        self.assertFalse(hasattr(handoff_alarm, "STALE_HANDOFF_ALARM"))
        self.assertFalse(hasattr(handoff_alarm, "GKREQ_STALE_HANDOFF_S"))
        self.assertFalse(hasattr(handoff_alarm, "_gkreq_reping_due"))
        # …and they still live in the package namespace for call-time access.
        self.assertTrue(hasattr(watchdog, "STALE_HANDOFF_ALARM"))
        self.assertTrue(hasattr(watchdog, "GKREQ_STALE_HANDOFF_S"))
        self.assertTrue(hasattr(watchdog, "_gkreq_reping_due"))


class BackReferenceSeamsStayLive(unittest.TestCase):
    """C3/C4: the moved body reaches package-level names call-time through
    `watchdog.` — patching them at the package level must still take effect."""

    def _fire(self, handoffs, g=None, now=1_000_000):
        pings = []

        def send_fn(body, **kw):
            pings.append((body, kw))
            return "sent"

        logs = watchdog._stale_handoff_alarm(
            "demo", "/root/demo", handoffs, g if g is not None else {},
            now, send_fn, dry_run=True, persist=lambda: None)
        return pings, logs

    def test_stale_alarm_template_is_read_call_time(self):
        # A stale hand-off (older than the threshold) → material change →
        # immediate alarm. Patching the TEMPLATE at the package level must be
        # reflected in the pinged body (call-time `watchdog.STALE_HANDOFF_ALARM`).
        now = 1_000_000
        handoffs = {7: now - watchdog.GKREQ_STALE_HANDOFF_S - 3600}
        sentinel = "SENTINEL-ALARM %(name)s %(ticks)s"
        with mock.patch.object(watchdog, "STALE_HANDOFF_ALARM", sentinel):
            pings, logs = self._fire(handoffs, now=now)
        self.assertEqual(len(pings), 1, logs)
        self.assertTrue(pings[0][0].startswith("SENTINEL-ALARM demo #7"),
                        pings[0][0])

    def test_threshold_constant_is_read_call_time(self):
        # Patching the THRESHOLD huge at the package level makes a normally-stale
        # hand-off count as fresh → no ping (call-time
        # `watchdog.GKREQ_STALE_HANDOFF_S`).
        now = 1_000_000
        handoffs = {7: now - 6 * 3600 - 3600}   # ~7h old (stale by default)
        with mock.patch.object(watchdog, "GKREQ_STALE_HANDOFF_S", 30 * 24 * 3600):
            pings, logs = self._fire(handoffs, now=now)
        self.assertEqual(pings, [], logs)

    def test_reping_due_helper_is_reached_through_the_package(self):
        # With prev tickets == current stale set (no material change), the else
        # branch calls `watchdog._gkreq_reping_due`. Patching it at the package
        # level must be reached (call-time C3 back-reference).
        now = 1_000_000
        stale_upd = now - watchdog.GKREQ_STALE_HANDOFF_S - 3600
        handoffs = {7: stale_upd}
        g = {"stale_seen": {"demo": {"tickets": [7], "ts": now - 100,
                                     "reping_count": 1}}}
        seen = {}

        def fake_reping_due(prev, now_, schedule):
            seen["called"] = (prev, now_, schedule)
            return True, 42

        with mock.patch.object(watchdog, "_gkreq_reping_due", fake_reping_due):
            pings, logs = self._fire(handoffs, g=g, now=now)
        self.assertIn("called", seen, "watchdog._gkreq_reping_due not reached")
        self.assertEqual(len(pings), 1, logs)
        # the reping_count returned by the patched helper is persisted
        self.assertEqual(g["stale_seen"]["demo"]["reping_count"], 42)


class NormalizeAndParseBehaviour(unittest.TestCase):
    def test_parse_gh_ts_roundtrip_and_garbage(self):
        self.assertEqual(watchdog._parse_gh_ts("2026-08-13T07:31:02Z"),
                         1786606262)
        self.assertIsNone(watchdog._parse_gh_ts("not-a-timestamp"))
        self.assertIsNone(watchdog._parse_gh_ts(None))

    def test_normalize_gkreq_shapes(self):
        self.assertEqual(
            watchdog._normalize_gkreq({"tickets": [1], "handoffs": {7: 100}}),
            ([1], {7: 100}))
        self.assertEqual(watchdog._normalize_gkreq([1, 2]), ([1, 2], None))
        self.assertEqual(watchdog._normalize_gkreq(None), (None, None))
        # mis-shaped halves degrade to the safe value for THAT half
        self.assertEqual(
            watchdog._normalize_gkreq({"tickets": "nope", "handoffs": []}),
            (None, None))


if __name__ == "__main__":
    unittest.main()
