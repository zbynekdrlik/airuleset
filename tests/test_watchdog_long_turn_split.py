"""#433 item G step 11 — `watchdog/long_turn.py` split.

The job-21 LONG-TURN WATCH family (`long_turn_watch` + `pane_turn_elapsed` /
`_human_duration` / `_human_age_desc` + the `LONG_TURN_*` thresholds), job 20's
pane-liveness readers (`_reconcile_candidate_panes`, `_pane_has_bg_agent`) and
the /compact-in-flight + queued-/compact guards (`_pane_compacting`,
`COMPACTING_MARKER`, `_QUEUED_COMPACT_RX`) were moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.long_turn`, then re-exported IN PLACE by a
positional facade import. `long_turn.py` is a back-reference module
(`import watchdog`); its four cross-module references — `_default_run`
(tmux_io.py), `_above_box_scan` (pane_text.py), `_pane_location` (janitor.py)
and `_BG_AGENTS_WAIT_RX` (still `__init__`) — go through the package namespace
at call time (`watchdog.<name>`) so every `patch.object(watchdog, "<name>", …)`
seam still resolves. These tests lock the invariants that keep the move safe:

  1. `import watchdog` stays clean in a FRESH subprocess — no circular-import
     breakage from `long_turn.py`'s `import watchdog` back-reference.
  2. Every moved name (7 functions + 6 constants) is the SAME object at
     `watchdog.<name>` and `watchdog.long_turn.<name>` — the C2 re-export
     contract. A drift to a private copy breaks the identity assertion (a dead
     `patch.object(watchdog, "<name>", …)` seam is exactly this bug).
  3. `MOVED_NAMES` is self-validating against BOTH authoritative sources
     (long_turn.py's own top-level defs/assigns + `__init__.py`'s facade
     ImportFrom), so a moved-but-omitted name cannot silently reduce coverage.
  4. The four back-references are genuinely `watchdog.`-prefixed: patching each
     at the PACKAGE level is OBSERVED by the moved caller. A silent bare-revert
     (the design's #1 hazard) NameErrors or bypasses the patch — either way
     these teeth go red. A functional test alone would NOT catch it for the two
     back-refs whose call sits where a NameError could be swallowed, which is
     why every back-ref gets a patch-observing test (the step-4 lesson).
"""

import ast
import inspect
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.long_turn as long_turn  # noqa: E402

# Every name moved into long_turn.py (7 functions + 6 module-level constants),
# in definition order. Hand-maintained on purpose — it is the checklist the
# reviewer diffs against the facade import block in `__init__.py`; the
# self-validation test below proves it stays in sync with the real sources.
MOVED_NAMES = [
    # functions (definition order)
    "_human_age_desc",
    "_reconcile_candidate_panes",
    "_pane_has_bg_agent",
    "pane_turn_elapsed",
    "_pane_compacting",
    "_human_duration",
    "long_turn_watch",
    # module-level constants (definition order)
    "_AGENT_STRIP_ROW_RX",
    "COMPACTING_MARKER",
    "_QUEUED_COMPACT_RX",
    "_TURN_ELAPSED_RX",
    "LONG_TURN_THRESHOLD_S",
    "LONG_TURN_SAME_TURN_TOLERANCE_S",
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

    def test_import_long_turn_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (the back-reference `import watchdog` gets a partially-init
        # module, and no `watchdog.<attr>` is touched at import time).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.long_turn as lt; print(lt.LONG_TURN_THRESHOLD_S)"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "1800")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(long_turn, name),
                                f"{name} missing from watchdog.long_turn")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(long_turn, name),
                              f"watchdog.{name} is not watchdog.long_turn.{name}")

    def test_long_turn_lives_in_its_own_module_file(self):
        self.assertTrue(long_turn.__file__.endswith("watchdog/long_turn.py"),
                        long_turn.__file__)


class MovedNamesChecklistIsSelfValidating(unittest.TestCase):
    """MOVED_NAMES is a hand-maintained checklist every other test trusts. Prove
    it equals BOTH authoritative sources, so a name added to (or dropped from)
    the real move cannot silently escape coverage (the step-6 NIT)."""

    def _long_turn_toplevel_names(self):
        src = Path(long_turn.__file__).read_text()
        tree = ast.parse(src)
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.append(t.id)
        return names

    def _facade_imported_names(self):
        src = (REPO / "watchdog" / "__init__.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and node.module == "watchdog.long_turn"):
                return [a.name for a in node.names]
        return []

    def test_checklist_matches_module_source(self):
        self.assertEqual(sorted(MOVED_NAMES),
                         sorted(self._long_turn_toplevel_names()))

    def test_checklist_matches_facade_import(self):
        self.assertEqual(sorted(MOVED_NAMES),
                         sorted(self._facade_imported_names()))


class NoConstantIsADefTimeDefault(unittest.TestCase):
    """The step-11 design keeps every moved constant a BODY read, never a
    def-time default — so all six move + re-export with no `from watchdog
    import`. `long_turn_watch`'s `threshold` therefore defaults to None (it
    reads LONG_TURN_THRESHOLD_S in its body), and a future refactor that turned
    a constant into a signature default would need the from-import this test
    proves is unnecessary."""

    def test_long_turn_watch_threshold_default_is_none(self):
        params = inspect.signature(watchdog.long_turn_watch).parameters
        self.assertIsNone(params["threshold"].default)

    def test_no_moved_constant_appears_as_a_default(self):
        consts = {"COMPACTING_MARKER", "_QUEUED_COMPACT_RX", "_TURN_ELAPSED_RX",
                  "_AGENT_STRIP_ROW_RX", "LONG_TURN_THRESHOLD_S",
                  "LONG_TURN_SAME_TURN_TOLERANCE_S"}
        const_vals = {id(getattr(watchdog, c)) for c in consts}
        for fn_name in ("_human_age_desc", "_reconcile_candidate_panes",
                        "_pane_has_bg_agent", "pane_turn_elapsed",
                        "_pane_compacting", "_human_duration", "long_turn_watch"):
            sig = inspect.signature(getattr(watchdog, fn_name))
            for p in sig.parameters.values():
                if p.default is not inspect.Parameter.empty:
                    self.assertNotIn(id(p.default), const_vals,
                                     f"{fn_name} def-defaults a moved constant")


class BackReferenceSeamsGoThroughThePackage(unittest.TestCase):
    """The four names long_turn.py did NOT co-move are reached call-time as
    `watchdog.<name>`. Patch each at the PACKAGE level and prove the moved
    caller observes it — a silent bare-revert (the design's #1 hazard) either
    NameErrors (name absent from long_turn.py's own globals) or bypasses the
    seam, and either way these assertions go red."""

    def test_default_run_backref_is_observed(self):
        # _reconcile_candidate_panes falls back run=None -> watchdog._default_run.
        out = "%p1\tclaude\t/tmp/a\n%p2\tnode\t/tmp/b\n"
        with mock.patch.object(watchdog, "_default_run", return_value=out) as m:
            res = watchdog._reconcile_candidate_panes(None)
        m.assert_called_once()
        self.assertEqual(res, [("%p1", "/tmp/a", "claude"),
                               ("%p2", "/tmp/b", "node")])

    def test_above_box_scan_backref_is_observed(self):
        # pane_turn_elapsed reads the spinner via watchdog._above_box_scan.
        with mock.patch.object(watchdog, "_above_box_scan",
                               return_value=([], "Baking… (1h 2m 3s · esc)")) as m:
            elapsed = watchdog.pane_turn_elapsed("anything")
        m.assert_called_once()
        self.assertEqual(elapsed, 3600 + 2 * 60 + 3)

    def test_bg_agents_wait_rx_backref_is_observed(self):
        # _pane_has_bg_agent checks watchdog._BG_AGENTS_WAIT_RX.search FIRST.
        # A single line with no agent-strip glyph isolates that path from the
        # co-moved _AGENT_STRIP_ROW_RX scan below it.
        with mock.patch.object(watchdog, "_BG_AGENTS_WAIT_RX", re.compile("ZZZ-sentinel")):
            self.assertTrue(watchdog._pane_has_bg_agent("a line with ZZZ-sentinel here"))
            self.assertFalse(watchdog._pane_has_bg_agent("a plain idle line"))

    def test_pane_location_backref_is_observed(self):
        # long_turn_watch labels a long turn via watchdog._pane_location when
        # project_by_sid has no entry. Drive elapsed >= threshold through the
        # _above_box_scan back-ref, then assert the _pane_location sentinel
        # reaches the log line.
        panes = {"sid1": ("%p9", "captured")}
        with mock.patch.object(watchdog, "_above_box_scan",
                               return_value=([], "Baking… (2h 0m 0s)")), \
                mock.patch.object(watchdog, "_pane_location",
                                  return_value="LOC-SENTINEL") as m:
            logs = watchdog.long_turn_watch(
                now=100000, run=None, state={}, panes_by_sid=panes,
                send_fn=None, dry_run=True, project_by_sid={}, owner_by_sid={})
        m.assert_called()
        self.assertTrue(any("LOC-SENTINEL" in ln for ln in logs),
                        f"_pane_location sentinel not in logs: {logs}")


if __name__ == "__main__":
    unittest.main()
