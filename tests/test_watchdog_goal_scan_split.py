"""#433 item G step 10 — `watchdog/goal_scan.py` split.

The goal-marker transcript scan (`scan_goal_markers` + its
`_goal_marker_content`/`_parse_goal_marker` parsers), the `◎ /goal`
footer/header read (`pane_goal_armed`), the installed-SKILL.md resolver
(`goal_templates_path`), and job 9's virgin-arm human-recency gate
(`_goal_autoarm_recent_human_activity`) were moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.goal_scan`, then re-exported IN PLACE by a
positional facade import. `goal_scan.py` is a back-reference module
(`import watchdog`); every cross-module / retained name it reads
(`_human_age_desc`, `_last_human_prompt_ts`, `_is_bottom_chrome`,
`_is_border_rule`, `_trailing_bottom_chrome`, `GOAL_INDICATOR`, and the
goal-format constants `_GOAL_LCS_OPEN`/`_GOAL_LCS_CLOSE`,
`GOAL_ARM_ACTIVE_PREFIX`, `_GOAL_ARM_PROBE`, `_GOAL_HEADER_INDICATOR_RX`,
`GOAL_AUTOARM_RECENT_HUMAN_S`) goes through the package namespace at call time,
so every `patch.object(watchdog, "<name>", ...)` seam still resolves. The one
constant that moved WITH the code is `GOAL_MARK_TAIL_BYTES` — it is
`scan_goal_markers`'s def-time default and its `__init__` home sat BELOW this
module's re-import position, so a `from watchdog import` at goal_scan's module
top would be an import-time forward reference (the step-design rule for a
below-position def-default: move + re-export).

These tests lock the invariants that keep the move safe:

  1. `import watchdog` stays clean in a FRESH subprocess — no circular-import
     breakage from goal_scan.py's `import watchdog` back-reference.
  2. Every moved name (6 functions + 1 constant) is the SAME object at
     `watchdog.<name>` and `watchdog.goal_scan.<name>` — the C2 re-export
     contract (a dead `patch.object(watchdog, "<name>", ...)` seam is exactly
     the drift this catches).
  3. `MOVED_NAMES` is self-validating against goal_scan.py's own AST and the
     `__init__.py` facade — a moved-but-omitted (or stray) name fails loudly
     rather than silently reducing coverage.
  4. `scan_goal_markers`'s `tail_bytes` def-time default IS the moved
     `GOAL_MARK_TAIL_BYTES` (= 4_000_000), preserved by the co-move.
  5. Mutation teeth (the design's #1 hazard: a moved call that BYPASSES a
     patched seam) — each moved body is proven to OBSERVE a `watchdog.<seam>`
     patch, so a future bare/frozen back-reference fails, not passes.
"""

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.goal_scan as goal_scan  # noqa: E402

# Every name moved into goal_scan.py. Kept explicit — a hand-maintained list is
# the point: MovedNamesChecklistIsSelfValidating cross-checks it against the two
# authoritative sources (goal_scan.py's AST + __init__.py's facade import).
MOVED_FUNCTIONS = [
    "_goal_autoarm_recent_human_activity",
    "goal_templates_path",
    "_goal_marker_content",
    "_parse_goal_marker",
    "_newest_marker",
    "scan_goal_markers",
    "seed_goal_marker",
    "pane_goal_armed",
]
MOVED_CONSTANTS = ["GOAL_MARK_TAIL_BYTES", "GOAL_MARK_SEED_CAP_BYTES"]
MOVED_NAMES = MOVED_FUNCTIONS + MOVED_CONSTANTS


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_goal_scan_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (the back-reference `import watchdog` gets a partially-init
        # module, and no `watchdog.<attr>` is touched at import time).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.goal_scan as g; print(g.GOAL_MARK_TAIL_BYTES)"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "4000000")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(goal_scan, name),
                                f"{name} missing from watchdog.goal_scan")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(goal_scan, name),
                              f"watchdog.{name} is not watchdog.goal_scan.{name}")

    def test_goal_scan_lives_in_its_own_module_file(self):
        self.assertTrue(goal_scan.__file__.endswith("watchdog/goal_scan.py"),
                        goal_scan.__file__)


class MovedNamesChecklistIsSelfValidating(unittest.TestCase):
    """MOVED_NAMES is a hand-maintained checklist every other test trusts.
    Cross-check it against the two authoritative sources so a moved-but-omitted
    (or a stray) name is caught, not silently under-covered (step-6 NIT fix)."""

    def _goal_scan_toplevel_defs(self):
        tree = ast.parse((REPO / "watchdog" / "goal_scan.py").read_text())
        funcs, consts = [], []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                funcs.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts.append(t.id)
        return funcs, consts

    def test_moved_functions_match_goal_scan_functiondefs(self):
        funcs, _ = self._goal_scan_toplevel_defs()
        self.assertEqual(sorted(MOVED_FUNCTIONS), sorted(funcs),
                         "MOVED_FUNCTIONS drifted from goal_scan.py's top-level def set")

    def test_moved_constants_match_goal_scan_module_assignments(self):
        _, consts = self._goal_scan_toplevel_defs()
        self.assertEqual(sorted(MOVED_CONSTANTS), sorted(consts),
                         "MOVED_CONSTANTS drifted from goal_scan.py's module-level constant set")

    def test_facade_reexports_exactly_the_moved_names(self):
        tree = ast.parse((REPO / "watchdog" / "__init__.py").read_text())
        imported = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "watchdog.goal_scan":
                imported = [a.name for a in node.names]
        self.assertIsNotNone(
            imported, "no `from watchdog.goal_scan import ...` facade in __init__.py")
        self.assertEqual(sorted(MOVED_NAMES), sorted(imported),
                         "the goal_scan facade re-exports a different set than MOVED_NAMES")


class DefTimeDefault(unittest.TestCase):
    def test_scan_goal_markers_tail_bytes_default_is_the_moved_constant(self):
        # GOAL_MARK_TAIL_BYTES moved WITH scan_goal_markers precisely because it
        # is its def-time default and its __init__ home was below the re-import
        # position. It binds at def time to the module-local constant.
        params = inspect.signature(watchdog.scan_goal_markers).parameters
        self.assertEqual(params["tail_bytes"].default, watchdog.GOAL_MARK_TAIL_BYTES)
        self.assertEqual(params["tail_bytes"].default, 4_000_000)


class BackRefSeamsAreLive(unittest.TestCase):
    """The design's #1 hazard: a moved call that BYPASSES a patched seam. Each
    test patches a `watchdog.<name>` the moved code reads at call time and proves
    the moved function OBSERVES it — a bare/frozen back-reference fails these."""

    def test_parse_goal_marker_reads_watchdog_lcs_tags(self):
        # Real tags do NOT parse a sentinel-wrapped body...
        self.assertIsNone(watchdog._parse_goal_marker("<SENT>Goal set: X</SENT>"))
        # ...but patching the package-level tag constants makes them parse.
        with mock.patch.object(watchdog, "_GOAL_LCS_OPEN", "<SENT>"), \
             mock.patch.object(watchdog, "_GOAL_LCS_CLOSE", "</SENT>"):
            self.assertEqual(
                watchdog._parse_goal_marker("<SENT>Goal set: X</SENT>"),
                {"state": "set", "payload": "X"})

    def test_parse_goal_marker_reads_watchdog_arm_active_prefix(self):
        with mock.patch.object(watchdog, "GOAL_ARM_ACTIVE_PREFIX", 'SENT: "'):
            self.assertEqual(
                watchdog._parse_goal_marker('SENT: "the payload". go'),
                {"state": "set", "payload": "the payload"})

    def test_scan_goal_markers_byte_prefilter_reads_watchdog_arm_probe(self):
        # A queued-arm entry (content starts with GOAL_ARM_ACTIVE_PREFIX) is
        # found by scan's cheap byte pre-filter, which tests
        # `watchdog._GOAL_ARM_PROBE not in line`. Patch the probe to a token
        # absent from the line -> the pre-filter skips it, so the marker is
        # missed. That absence proves scan reads the package-level probe.
        prefix = watchdog.GOAL_ARM_ACTIVE_PREFIX
        line = json.dumps({"type": "system",
                           "content": prefix + 'do the thing". Please continue.',
                           "timestamp": "2026-08-15T10:00:00Z"})
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(line + "\n")
            path = f.name
        try:
            _off, mark = watchdog.scan_goal_markers(path, off=0)
            self.assertEqual(mark["state"], "set")
            self.assertEqual(mark["payload"], "do the thing")
            with mock.patch.object(watchdog, "_GOAL_ARM_PROBE", b"ZZZ_ABSENT_TOKEN"):
                _off2, mark2 = watchdog.scan_goal_markers(path, off=0)
                self.assertIsNone(mark2)
        finally:
            os.unlink(path)

    def test_goal_autoarm_reads_window_source_and_age_desc(self):
        # No /tmp presence marker for this sid -> only the transcript path runs.
        sid = "no-such-marker-sid-xyzq"
        self.assertFalse(os.path.exists("/tmp/claude-user-active-%s" % sid))
        now = 1_000_000.0
        with mock.patch.object(watchdog, "_last_human_prompt_ts",
                               lambda *a, **k: now - 100), \
             mock.patch.object(watchdog, "_human_age_desc", lambda age: "AGEDESC"):
            # A large window makes a 100 s-old human prompt count as recent, and
            # the message routes through watchdog._human_age_desc.
            with mock.patch.object(watchdog, "GOAL_AUTOARM_RECENT_HUMAN_S", 1000):
                recent, reason = watchdog._goal_autoarm_recent_human_activity(
                    sid, "/no/such/tpath", now)
                self.assertTrue(recent)
                self.assertIn("AGEDESC", reason)
            # A tiny window makes the SAME prompt not recent — proving the moved
            # body reads the package-level window constant, not a frozen copy.
            with mock.patch.object(watchdog, "GOAL_AUTOARM_RECENT_HUMAN_S", 1):
                recent, reason = watchdog._goal_autoarm_recent_human_activity(
                    sid, "/no/such/tpath", now)
                self.assertFalse(recent)

    def test_pane_goal_armed_reads_watchdog_goal_indicator(self):
        cap = "❯ type here\nctx 5% ZZQGOAL"
        self.assertIsNot(watchdog.pane_goal_armed(cap), True)
        with mock.patch.object(watchdog, "GOAL_INDICATOR", "ZZQGOAL"):
            self.assertIs(watchdog.pane_goal_armed(cap), True)


class SeedGoalMarker(unittest.TestCase):
    """#517 -- the first-sight bounded reverse-block-scan seed. All tests use
    small explicit tail/cap/block so they are fast and can exercise the deep,
    straddle, past-cap and BOF paths without a real >4 MB file."""

    def _marker_line(self, state="set", ts=500):
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(ts, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        wrapped = "<local-command-stdout>Goal %s: /goal x</local-command-stdout>" % state
        return json.dumps({"type": "system", "subtype": "local_command",
                           "timestamp": iso, "content": wrapped})

    def _tmpfile(self):
        fd, name = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(name) and os.unlink(name))
        return name

    def _write(self, before="", marker=None, after=""):
        """A file = `before` + (marker line + \\n) + `after`."""
        p = self._tmpfile()
        with open(p, "w", encoding="utf-8") as f:
            if before:
                f.write(before)
            if marker is not None:
                f.write(marker + "\n")
            if after:
                f.write(after)
        return p

    def test_marker_within_the_tail_is_found_like_scan(self):
        # Marker in the tail window -> found, and new_off matches
        # scan_goal_markers(off=None) exactly (incremental resume unchanged).
        p = self._write(marker=self._marker_line("set"), after="padding\n" * 3)
        seed_off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=10000, cap_bytes=100000, block=10000)
        self.assertEqual(status, "found")
        self.assertEqual(mark.get("state"), "set")
        scan_off, scan_mark = watchdog.scan_goal_markers(
            p, off=None, tail_bytes=10000)
        self.assertEqual(seed_off, scan_off)
        self.assertEqual(mark.get("state"), scan_mark.get("state"))

    def test_new_off_matches_scan_when_start_gt_zero(self):
        # #517-review: lock the byte-identical new_off for the start>0 case (file
        # bigger than tail_bytes, so the first-line bootstrap-drop is active),
        # not just the small-file start==0 case above. Both derive new_off from
        # rfind(b"\n") over the identical [size-tail:size] window, so they must
        # agree exactly -- incremental resume is unchanged for a large file too.
        p = self._write(before="head-line\n" * 3,
                        marker=self._marker_line("set"),
                        after="tail-pad\n" * 30)
        seed_off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=80, cap_bytes=100000, block=80)   # start = size-80 > 0
        scan_off, _scan_mark = watchdog.scan_goal_markers(
            p, off=None, tail_bytes=80)
        self.assertEqual(seed_off, scan_off)
        # the marker is beyond the 80-byte tail -> seed finds it via reverse scan
        self.assertEqual(status, "found")
        self.assertEqual(mark.get("state"), "set")

    def test_marker_beyond_tail_within_cap_is_found_by_reverse_scan(self):
        # Marker before a tiny tail but within the cap -> the reverse scan finds
        # it. MUTATION-LOCK: without the reverse scan this reads not-armed.
        p = self._write(marker=self._marker_line("set"), after="padding-x\n" * 60)
        seed_off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=40, cap_bytes=100000, block=40)
        self.assertEqual(status, "found", (status, mark))
        self.assertEqual(mark.get("state"), "set")

    def test_cleared_marker_beyond_tail_is_found_not_unknown(self):
        # A Goal cleared: marker deeper than the tail resolves DEFINITIVELY
        # not-armed (state "cleared"), never the ambiguous unknown-past-cap.
        p = self._write(marker=self._marker_line("cleared"),
                        after="padding-x\n" * 60)
        _off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=40, cap_bytes=100000, block=40)
        self.assertEqual(status, "found")
        self.assertEqual(mark.get("state"), "cleared")

    def test_marker_straddling_block_boundaries_is_reassembled(self):
        # The one marker line (~130 B) spans several 40-byte blocks -> the carry
        # must reassemble it. MUTATION-LOCK for the straddle handling.
        p = self._write(marker=self._marker_line("set"), after="pad\n" * 5)
        _off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=40, cap_bytes=100000, block=40)
        self.assertEqual(status, "found", (status, mark))
        self.assertEqual(mark.get("state"), "set")

    def test_marker_deeper_than_cap_is_unknown_never_armed(self):
        # The arm is deeper than the cap -> honest unknown-past-cap, and NEVER a
        # fabricated armed marker (mark is None so resolve_goal_armed can't read
        # armed=True from it).
        p = self._write(marker=self._marker_line("set"),
                        after="padding-x\n" * 200)
        off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=50, cap_bytes=100, block=50)
        self.assertEqual(status, "unknown-past-cap", (status, mark))
        self.assertIsNone(mark)

    def test_no_marker_at_all_reaches_bof_not_unknown(self):
        # A never-armed file scanned to BOF -> none-bof (definitively not armed),
        # NOT unknown-past-cap (which would spam a first-sight observability log).
        p = self._write(before="line-a\n" * 5, after="line-b\n" * 5)
        off, mark, status = watchdog.seed_goal_marker(
            p, tail_bytes=40, cap_bytes=100000, block=40)
        self.assertEqual(status, "none-bof", (status, mark))
        self.assertIsNone(mark)

    def test_unreadable_path_is_fail_safe(self):
        off, mark, status = watchdog.seed_goal_marker("/no/such/seed/path")
        self.assertEqual((off, mark, status), (0, None, "none-bof"))


if __name__ == "__main__":
    unittest.main()
