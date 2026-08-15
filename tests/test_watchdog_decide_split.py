"""#433 item G step 4 — `watchdog/decide.py` split.

The two live-background-task pane readers (`_pane_live_shell_evidence`,
`_pane_live_task_count`) and the whole usage-cap / 5-hour-session-limit /
reset-epoch-parse / stuck-check-decision / state-file cluster (`is_usage_cap`,
`pane_session_limited`, `parse_reset_epoch`, `_reset_epoch_from_scanned_text`,
`parse_reset_epoch_from_error_text`, `session_user_stopped`, `_human_clock`,
`decide`, `declared_wait_until`, `decide_working`, `load_state`, `save_state`)
plus every regex/constant those functions own moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.decide` — two originally non-contiguous
blocks re-exported IN PLACE by ONE positional facade import at the earlier
block's position (the byte-proof: reverse-substituting the C3 `watchdog.`
back-ref prefixes reproduces the __init__ blocks exactly). This is a
BACK-REFERENCE module: the bodies call `__init__`-resident / other-submodule
primitives (`_is_bottom_chrome`, `_above_input_box`, `_iter_jsonl_tail`, the
resident regex `_LIVE_BG_TASK_RX`) and the intra-module co-moved cross-call
`_reset_epoch_from_scanned_text`, ALL written call-time `watchdog.<name>`
(module top: `import watchdog`). Its six def-time defaults (`GRACE_SECONDS`,
`RETRY_INTERVAL_SECONDS`, `MAX_NUDGES`, `BACKOFF_CAP_SECONDS`,
`WORKING_RETRY_INTERVAL_SECONDS`, `MAX_WORKING_NUDGES`) bind `__init__`-resident
constants via `from watchdog import ...` at module top (C4).

NAME-SHADOW (unique to this step): the module is `decide` AND it exports a
function named `decide`. The facade re-export binds the FUNCTION onto the
`watchdog.decide` ATTRIBUTE after the package sets the submodule there, so
`watchdog.decide` resolves to the function (exactly what every `watchdog.decide(...)`
seam wants). The MODULE object is reached only via `sys.modules["watchdog.decide"]`
— which is why the identity assertions below use that, never `watchdog.decide`.

These tests lock the invariants that keep every existing `watchdog.<name>` seam
(run_once jobs 1/4/6 — `decide`/`decide_working`/`save_state`/`parse_reset_epoch*`
/`is_usage_cap`/`pane_session_limited`; `SESSLIMIT_*` read as bare run_once
globals in job 6 — goal / compact / cross_stream / janitor, hooks, and every
monkeypatch in the suite) working unchanged after the move.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402

# NAME-SHADOW: reach the module object ONLY through sys.modules — `watchdog.decide`
# (the attribute) is the FUNCTION after the facade re-export.
decide_mod = sys.modules["watchdog.decide"]  # noqa: E402

# The 14 functions moved into decide.py, in definition order.
MOVED_FUNCS = [
    "_pane_live_shell_evidence",
    "_pane_live_task_count",
    "is_usage_cap",
    "pane_session_limited",
    "parse_reset_epoch",
    "_reset_epoch_from_scanned_text",
    "parse_reset_epoch_from_error_text",
    "session_user_stopped",
    "_human_clock",
    "decide",
    "declared_wait_until",
    "decide_working",
    "load_state",
    "save_state",
]
# The 13 regexes/constants moved with them (all re-exported — C2).
MOVED_CONSTS = [
    "_USAGE_CAP_RX",
    "_TRANSIENT_RX",
    "_SESSION_LIMIT_RX",
    "_RESET_TIME_RX",
    "_RESET_MONTH_NUM",
    "_RESET_TZ_RX",
    "SESSLIMIT_RETRY_S",
    "SESSLIMIT_MAX_TRIES",
    "DATED_RESET_STALE_GRACE_S",
    "DECLARED_WAIT_GRACE_S",
    "DECLARED_WAIT_MAX_S",
    "WORKING_RESPONDED_BACKOFF_SCHEDULE_S",
    "_CLOCK_RX",
]
MOVED_NAMES = MOVED_FUNCS + MOVED_CONSTS  # all 27 names the facade re-exports


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_decide_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly. The NAME-SHADOW means `import watchdog.decide as x` binds the
        # FUNCTION, so the module is reached via sys.modules — a module-local
        # function (no back-ref) proves the submodule loaded and is callable.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.decide, sys; "
             "print(sys.modules['watchdog.decide'].is_usage_cap('usage limit'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "True")
        self.assertEqual(r.stderr.strip(), "")


class NameShadow(unittest.TestCase):
    def test_watchdog_decide_attribute_is_the_function(self):
        # The facade rebinds `watchdog.decide` to the FUNCTION — this is exactly
        # what every `watchdog.decide(...)` caller seam wants.
        self.assertTrue(callable(watchdog.decide))
        self.assertEqual(watchdog.decide.__name__, "decide")
        self.assertIs(watchdog.decide, decide_mod.decide)

    def test_module_object_reachable_via_sys_modules(self):
        self.assertIsNot(watchdog.decide, decide_mod)  # attribute != module
        self.assertTrue(hasattr(decide_mod, "__file__"))
        self.assertTrue(decide_mod.__file__.endswith("watchdog/decide.py"),
                        decide_mod.__file__)


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(decide_mod, name),
                                f"{name} missing from watchdog.decide")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(decide_mod, name),
                              f"watchdog.{name} is not watchdog.decide.{name}")

    def test_moved_functions_report_decide_as_their_module(self):
        for name in MOVED_FUNCS:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.decide")


class DefTimeDefaultsBindResident(unittest.TestCase):
    """C4: `decide`/`decide_working` def-time defaults bind the resident
    `__init__` constants (imported at decide.py's module top). A future edit
    that dropped the module-top `from watchdog import ...` would still import
    (the names are used only as defaults) but bind the WRONG value — these
    assertions pin the binding to the live resident constants."""

    def test_decide_defaults_bind_resident_constants(self):
        import inspect
        p = inspect.signature(watchdog.decide).parameters
        self.assertEqual(p["grace"].default, watchdog.GRACE_SECONDS)
        self.assertEqual(p["interval"].default, watchdog.RETRY_INTERVAL_SECONDS)
        self.assertEqual(p["max_nudges"].default, watchdog.MAX_NUDGES)
        self.assertEqual(p["backoff_cap"].default, watchdog.BACKOFF_CAP_SECONDS)

    def test_decide_working_defaults_bind_resident_and_comoved_constants(self):
        import inspect
        p = inspect.signature(watchdog.decide_working).parameters
        self.assertEqual(p["interval"].default, watchdog.WORKING_RETRY_INTERVAL_SECONDS)
        self.assertEqual(p["max_nudges"].default, watchdog.MAX_WORKING_NUDGES)
        # co-moved into decide.py, re-exported — same tuple object
        self.assertIs(p["backoff_schedule"].default,
                      watchdog.WORKING_RESPONDED_BACKOFF_SCHEDULE_S)


class SesslimitReExportForJob6(unittest.TestCase):
    """`SESSLIMIT_RETRY_S` / `SESSLIMIT_MAX_TRIES` are DEFINED in decide.py but
    CONSUMED by run_once's job 6 as BARE module globals (`if attempts >=
    SESSLIMIT_MAX_TRIES`, `< SESSLIMIT_RETRY_S`). The facade re-export is what
    keeps those bare reads resolvable AND patchable — dropping either from the
    facade would NameError job 6. (The suite's own job-6 tests are the deep
    integration guard; this is the focused unit guard.)"""

    def test_sesslimit_constants_resolve_as_watchdog_globals(self):
        self.assertEqual(watchdog.SESSLIMIT_RETRY_S, 5 * 60)
        self.assertEqual(watchdog.SESSLIMIT_MAX_TRIES, 4)


class BackReferencesResolveAtCallTime(unittest.TestCase):
    """Functional calls through each back-reference whose NameError would
    SURFACE (i.e. not swallowed by a try/except in the body). Any of these
    would fail the instant a `watchdog.` prefix in a moved body were reverted
    to a bare name — the design's #1 hazard, with teeth."""

    def test_pane_live_task_count_reaches_is_bottom_chrome_and_rx(self):
        # `⏵⏵ …` line is trailing chrome (`_is_bottom_chrome`) AND carries the
        # badge (`_LIVE_BG_TASK_RX`); the count sums it.
        self.assertEqual(
            watchdog._pane_live_task_count("⏵⏵ accept edits · 3 shells"), 3)

    def test_pane_live_shell_evidence_reaches_is_bottom_chrome_and_rx(self):
        self.assertTrue(
            watchdog._pane_live_shell_evidence("⏵⏵ accept edits · 3 shells"))
        self.assertFalse(watchdog._pane_live_shell_evidence(""))

    def test_pane_session_limited_reaches_above_input_box(self):
        # No try/except around the `watchdog._above_input_box` call — a bare
        # reversion would NameError here, not silently return.
        self.assertIsInstance(watchdog.pane_session_limited("hello\n❯ "), bool)

    def test_parse_reset_epoch_reaches_scanned_text_core(self):
        # `_reset_epoch_from_scanned_text` is called OUTSIDE parse_reset_epoch's
        # try/except — a bare reversion surfaces. No reset banner -> None.
        self.assertIsNone(watchdog.parse_reset_epoch("hello\n❯ ", 1000))

    def test_parse_reset_epoch_from_error_text_reaches_scanned_text_core(self):
        self.assertIsNone(
            watchdog.parse_reset_epoch_from_error_text("no reset here", 1000))


class BackRefsGoThroughPackageSeam(unittest.TestCase):
    """Patch-observing teeth — the comprehensive #1-hazard defense, and the ONLY
    discriminator for back-refs behind a `try/except Exception` (which would
    swallow a bare-name NameError and silently return the fail-safe). Each test
    patches the PACKAGE seam and asserts the moved caller OBSERVES it; a bare
    intra-body call would use the real primitive and bypass the patch."""

    def test_is_bottom_chrome_seam_observed_by_pane_readers(self):
        # Patch the walk gate to reject everything -> the pane reader never
        # peels a row -> no badge seen. A bare call would use the real
        # `_is_bottom_chrome` (which accepts `⏵⏵ …`) and return True.
        with mock.patch.object(watchdog, "_is_bottom_chrome", lambda s: False):
            self.assertFalse(
                watchdog._pane_live_shell_evidence("⏵⏵ accept edits · 3 shells"))

    def test_live_bg_task_rx_seam_observed_by_pane_readers(self):
        # Patch the resident badge regex to a sentinel the REAL regex never
        # matches; the count must reflect the sentinel.
        with mock.patch.object(watchdog, "_LIVE_BG_TASK_RX",
                               re.compile(r"\d+\s+widgets")):
            self.assertEqual(
                watchdog._pane_live_task_count("⏵⏵ z · 7 widgets"), 7)

    def test_above_input_box_seam_observed_by_pane_session_limited(self):
        # `_above_input_box` is called with NO try/except here; patch it to a
        # region the session-limit regex matches, on a capture that itself has
        # no banner -> True only if the patch is observed.
        with mock.patch.object(watchdog, "_above_input_box",
                               lambda c: "hit your session limit"):
            self.assertTrue(watchdog.pane_session_limited("no banner in this pane"))
        with mock.patch.object(watchdog, "_above_input_box", lambda c: "nothing"):
            self.assertFalse(watchdog.pane_session_limited("no banner in this pane"))

    def test_reset_epoch_scanned_text_seam_observed_by_both_callers(self):
        # The intra-module co-moved cross-call (C3 even between two functions in
        # the SAME module). BOTH callers must observe a patch on the package
        # path — a bare call would bypass it and re-parse for real.
        with mock.patch.object(watchdog, "_reset_epoch_from_scanned_text",
                               lambda scoped, now: 424242):
            self.assertEqual(watchdog.parse_reset_epoch("hello\n❯ ", 1000), 424242)
            self.assertEqual(
                watchdog.parse_reset_epoch_from_error_text("anything", 1000), 424242)

    def test_iter_jsonl_tail_seam_observed_by_session_user_stopped(self):
        # session_user_stopped's whole body is under `try/except Exception`, so
        # a bare `_iter_jsonl_tail` reversion would NameError -> be swallowed ->
        # return the fail-safe False. ONLY this patch-observing test has teeth:
        # a fabricated `/exit` entry must be seen (True) via the package seam.
        exit_entry = {"type": "user",
                      "message": {"content":
                                  "<command-name>/exit</command-name>\nexit"}}
        with mock.patch.object(watchdog, "_iter_jsonl_tail",
                               lambda tpath, max_lines=400: [exit_entry]):
            self.assertTrue(watchdog.session_user_stopped("/nonexistent", since_ts=None))
        # a non-exit entry through the SAME seam must NOT trigger — proves the
        # True above came from reading the patched entries, not a constant.
        normal_entry = {"type": "user", "message": {"content": "just working"}}
        with mock.patch.object(watchdog, "_iter_jsonl_tail",
                               lambda tpath, max_lines=400: [normal_entry]):
            self.assertFalse(watchdog.session_user_stopped("/nonexistent", since_ts=None))


class StateFileFunctionsRoundTrip(unittest.TestCase):
    """`load_state`/`save_state` are module-local (json/os/Path imports co-moved
    into decide.py) — a round-trip proves the move carried their imports."""

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            watchdog.save_state(p, {"k": 1, "nested": {"a": [1, 2, 3]}})
            self.assertEqual(watchdog.load_state(p), {"k": 1, "nested": {"a": [1, 2, 3]}})

    def test_load_state_missing_file_returns_empty(self):
        self.assertEqual(watchdog.load_state("/nonexistent/state.json"), {})


if __name__ == "__main__":
    unittest.main()
