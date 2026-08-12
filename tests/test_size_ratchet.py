"""Tests for the #404 point-1 size ratchet (scripts/size_ratchet.py).

Growth-only cap on file/function line counts for airuleset's own source:
today's sizes are the ceiling, shrinking or stagnation always passes,
growth past a ceiling (or past the flat default for a brand-new item)
fails with an actionable message. See the #404 design comment (issue
thread) and `.claude/rules/airuleset-internals.md`'s ratchet section for
the full rationale.

Written as unittest.TestCase (not bare pytest functions) so this file is
genuinely collected by `cmd_push`'s `python3 -m unittest discover -s
tests` gate — a plain `def test_x():` file with no TestCase is silently
skipped by that discovery mechanism (an existing, out-of-scope repo gap;
see test_rules_ab_experiment.py for the sibling case).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "size_ratchet.py"


def _load():
    spec = importlib.util.spec_from_file_location("size_ratchet", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses (if this module ever grows one) resolve their module from
    # sys.modules during class creation — register defensively, matches the
    # established pattern in tests/test_rules_ab_experiment.py.
    sys.modules["size_ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


sr = _load()


# --- measurement is real, via ast, not regex -------------------------------


class TestFileLineCount(unittest.TestCase):
    def test_counts_real_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text("a\nb\nc\n", encoding="utf-8")
            self.assertEqual(sr.file_line_count(p), 3)

    def test_counts_lines_with_no_trailing_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text("a\nb", encoding="utf-8")
            self.assertEqual(sr.file_line_count(p), 2)


class TestFunctionLineCounts(unittest.TestCase):
    def test_measures_a_top_level_function(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text("def f():\n    a = 1\n    b = 2\n    return a + b\n", encoding="utf-8")
            counts = sr.function_line_counts(p)
            self.assertEqual(counts.get("f"), 4)

    def test_measures_a_method_with_class_qualified_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text(
                "class C:\n"
                "    def m(self):\n"
                "        a = 1\n"
                "        return a\n",
                encoding="utf-8",
            )
            counts = sr.function_line_counts(p)
            self.assertEqual(counts.get("C.m"), 3)

    def test_is_robust_to_a_string_literal_containing_the_word_def(self):
        """A regex-based scanner would be fooled by "def " appearing inside
        a docstring/embedded-script string literal; ast is not."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text(
                'BLOB = """\n'
                'def fake_function():\n'
                '    pass\n'
                'def another_fake():\n'
                '    pass\n'
                '"""\n'
                "\n"
                "def real_one():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            counts = sr.function_line_counts(p)
            # only the ONE real function is measured — the two fake "def"
            # occurrences inside the string literal are not functions at all
            self.assertEqual(counts, {"real_one": 2})

    def test_unrelated_edit_shifting_start_line_does_not_change_the_key(self):
        """The key is the qualname, not path:lineno — an unrelated insertion
        above a function must not look like a new/different function."""
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "x.py"
            p1.write_text("def f():\n    return 1\n", encoding="utf-8")
            p2 = Path(d) / "y.py"
            p2.write_text("# a new comment\n# another one\n\ndef f():\n    return 1\n", encoding="utf-8")
            c1 = sr.function_line_counts(p1)
            c2 = sr.function_line_counts(p2)
            self.assertEqual(set(c1), set(c2))
            self.assertEqual(c1["f"], c2["f"])

    def test_nested_function_gets_a_locals_qualified_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text(
                "def outer():\n"
                "    def inner():\n"
                "        return 1\n"
                "    return inner()\n",
                encoding="utf-8",
            )
            counts = sr.function_line_counts(p)
            self.assertIn("outer.<locals>.inner", counts)


# --- check(): pure comparison, no side effects ------------------------------


class TestCheck(unittest.TestCase):
    def test_equal_to_ceiling_passes(self):
        measured = {"files": {"a.py": 100}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        self.assertEqual(sr.check(measured, stored), [])

    def test_under_ceiling_passes(self):
        measured = {"files": {"a.py": 90}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        self.assertEqual(sr.check(measured, stored), [])

    def test_over_ceiling_fails_with_actionable_message(self):
        measured = {"files": {"a.py": 150}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("a.py", v[0])
        self.assertIn("100", v[0])
        self.assertIn("150", v[0])
        self.assertIn("Split this file into smaller", v[0])

    def test_new_file_within_default_cap_passes_silently(self):
        measured = {"files": {"a.py": 50}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        self.assertEqual(sr.check(measured, stored), [])

    def test_new_file_exceeding_default_cap_fails(self):
        measured = {"files": {"a.py": sr.FILE_DEFAULT_CEILING + 1}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("new file", v[0])
        self.assertIn(str(sr.FILE_DEFAULT_CEILING), v[0])

    def test_new_function_exceeding_default_cap_fails_with_split_wording(self):
        measured = {"files": {}, "functions": {"a.py::f": sr.FUNC_DEFAULT_CEILING + 1}}
        stored = {"files": {}, "functions": {}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("new function", v[0])
        self.assertIn("f()", v[0])
        self.assertIn("helper functions", v[0])

    def test_function_over_its_own_ceiling_fails(self):
        measured = {"files": {}, "functions": {"a.py::big": 400}}
        stored = {"files": {}, "functions": {"a.py::big": 350}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("big()", v[0])
        self.assertIn("350", v[0])
        self.assertIn("400", v[0])

    def test_shrinking_below_ceiling_never_fails(self):
        measured = {"files": {}, "functions": {"a.py::big": 100}}
        stored = {"files": {}, "functions": {"a.py::big": 350}}
        self.assertEqual(sr.check(measured, stored), [])

    def test_check_never_mutates_its_arguments(self):
        measured = {"files": {"a.py": 150}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        before_m, before_s = json.dumps(measured), json.dumps(stored)
        sr.check(measured, stored)
        self.assertEqual(json.dumps(measured), before_m)
        self.assertEqual(json.dumps(stored), before_s)


# --- regen(): auto-tighten, never loosen ------------------------------------


class TestRegen(unittest.TestCase):
    def test_shrinking_item_tightens_its_ceiling(self):
        measured = {"files": {"a.py": 80}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["files"]["a.py"], 80)
        self.assertEqual(refusals, [])

    def test_never_loosens_even_when_current_exceeds_stored(self):
        """A currently-violating item (grown past its ceiling) must NOT have
        its ceiling silently raised to match — check() must still fail for
        it afterwards. regen only ever tightens or leaves unchanged."""
        measured = {"files": {"a.py": 150}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["files"]["a.py"], 100)
        self.assertEqual(refusals, [])
        # and the resulting snapshot still flags the violation
        v = sr.check(measured, new_snapshot)
        self.assertTrue(v, "regen must not have silently loosened the ceiling")

    def test_stagnant_item_is_left_unchanged(self):
        measured = {"files": {"a.py": 100}, "functions": {}}
        stored = {"files": {"a.py": 100}, "functions": {}}
        new_snapshot, _ = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["files"]["a.py"], 100)

    def test_new_file_within_default_cap_is_added_at_its_current_size(self):
        measured = {"files": {"new.py": 42}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["files"]["new.py"], 42)
        self.assertEqual(refusals, [])

    def test_new_file_exceeding_default_cap_is_refused_not_added(self):
        big = sr.FILE_DEFAULT_CEILING + 500
        measured = {"files": {"huge.py": big}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertNotIn("huge.py", new_snapshot["files"])
        self.assertEqual(len(refusals), 1)
        self.assertIn("huge.py", refusals[0])
        self.assertIn(str(sr.FILE_DEFAULT_CEILING), refusals[0])

    def test_small_new_function_under_track_threshold_is_not_added(self):
        measured = {"files": {}, "functions": {"a.py::tiny": sr.FUNC_TRACK_THRESHOLD - 1}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["functions"], {})
        self.assertEqual(refusals, [])

    def test_function_crossing_track_threshold_is_added(self):
        measured = {"files": {}, "functions": {"a.py::mid": sr.FUNC_TRACK_THRESHOLD + 5}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["functions"]["a.py::mid"], sr.FUNC_TRACK_THRESHOLD + 5)
        self.assertEqual(refusals, [])

    def test_new_function_exceeding_default_cap_is_refused(self):
        measured = {"files": {}, "functions": {"a.py::huge": sr.FUNC_DEFAULT_CEILING + 50}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertNotIn("a.py::huge", new_snapshot["functions"])
        self.assertEqual(len(refusals), 1)

    def test_deleted_item_is_pruned_from_the_regenerated_snapshot(self):
        measured = {"files": {}, "functions": {}}
        stored = {"files": {"gone.py": 500}, "functions": {"gone.py::f": 250}}
        new_snapshot, refusals = sr.regen(measured, stored)
        self.assertEqual(new_snapshot["files"], {})
        self.assertEqual(new_snapshot["functions"], {})
        self.assertEqual(refusals, [])


# --- exemption mechanism (empty in production, proven via monkeypatch) -----


class TestExemptions(unittest.TestCase):
    def test_exempt_file_is_skipped_by_check_even_if_over_ceiling(self):
        orig = sr.EXEMPT_FILES
        try:
            sr.EXEMPT_FILES = {"exempt.py"}
            measured = {"files": {"exempt.py": 99999}, "functions": {}}
            stored = {"files": {}, "functions": {}}
            # check() itself doesn't consult EXEMPT_FILES directly (that's a
            # measurement-time concern in tracked_files()); prove the real
            # exclusion point instead: tracked_files() never returns an
            # exempt path even if it physically exists on disk.
            with tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / "exempt.py").write_text("x = 1\n" * 99999, encoding="utf-8")
                self.assertNotIn("exempt.py", sr.tracked_files(repo))
        finally:
            sr.EXEMPT_FILES = orig

    def test_production_exempt_sets_are_empty(self):
        """No exemption is currently justified — see the #404 design
        comment. This pins that the shipped set stays empty unless a future
        change adds a genuinely-justified, commented entry."""
        self.assertEqual(sr.EXEMPT_FILES, set())
        self.assertEqual(sr.EXEMPT_FUNCTIONS, set())


# --- snapshot I/O ------------------------------------------------------------


class TestSnapshotIO(unittest.TestCase):
    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            data = {"files": {"b.py": 2, "a.py": 1}, "functions": {"a.py::f": 10}}
            sr.save_snapshot(data, p)
            loaded = sr.load_snapshot(p)
            self.assertEqual(loaded["files"], {"a.py": 1, "b.py": 2})
            self.assertEqual(loaded["functions"], {"a.py::f": 10})

    def test_saved_keys_are_sorted_for_merge_friendly_diffs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            sr.save_snapshot({"files": {"z.py": 1, "a.py": 2}, "functions": {}}, p)
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            self.assertEqual(list(data["files"].keys()), ["a.py", "z.py"])

    def test_missing_snapshot_file_loads_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "does-not-exist.json"
            loaded = sr.load_snapshot(p)
            self.assertEqual(loaded, {"files": {}, "functions": {}})


# --- tracked_files() scope matches the ticket's own "at minimum" list ------


class TestTrackedScope(unittest.TestCase):
    def test_tracked_files_includes_the_ticket_named_areas(self):
        files = sr.tracked_files()
        self.assertIn("airuleset.py", files)
        self.assertIn("watchdog/__init__.py", files)
        self.assertIn("notify/__init__.py", files)
        self.assertIn("burn/__init__.py", files)
        self.assertTrue(any(f.startswith("filedrop/") for f in files))
        self.assertIn("tests/test_airuleset.py", files)
        self.assertIn("tests/test_size_ratchet.py", files)

    def test_tracked_files_excludes_hooks_and_scripts(self):
        files = sr.tracked_files()
        self.assertFalse(any(f.startswith("hooks/") for f in files))
        self.assertFalse(any(f.startswith("scripts/") for f in files))

    def test_tests_glob_is_flat_not_recursive(self):
        """tests/*.py per the ticket's literal wording — no subdirectory
        walk (tests/ has none today, but the scope should stay explicit)."""
        files = sr.tracked_files()
        for f in files:
            if f.startswith("tests/"):
                self.assertNotIn("/", f[len("tests/") :])


# --- the actual gate: today's real repo against the shipped snapshot -------


class TestCurrentTreePassesTheShippedSnapshot(unittest.TestCase):
    """This IS the ratchet: the real assertion the suite (and cmd_push's
    fail-closed gate) runs on every commit. It must pass on day one — a
    growth-only cap over sizes measured on the day the snapshot was cut can
    never itself be a day-one failure."""

    def test_no_tracked_file_or_function_exceeds_its_recorded_ceiling(self):
        measured = sr.measure()
        stored = sr.load_snapshot()
        violations = sr.check(measured, stored)
        self.assertEqual(
            violations,
            [],
            "size ratchet violated:\n" + "\n".join(violations),
        )

    def test_every_current_300plus_line_function_has_an_explicit_ceiling(self):
        """Sanity lock: the day-one snapshot must genuinely have captured
        every current large-function offender, not merely happen to pass
        because none of them exceed the flat default."""
        measured = sr.measure()
        stored = sr.load_snapshot()
        offenders = [k for k, n in measured["functions"].items() if n >= 300]
        untracked = [k for k in offenders if k not in stored["functions"]]
        self.assertEqual(
            untracked,
            [],
            "these >=300-line functions have no explicit ceiling entry: "
            + ", ".join(untracked),
        )

    def test_snapshot_has_no_stale_entries_for_deleted_items(self):
        """Every stored key must still resolve to something real in the
        tracked tree — otherwise the snapshot has drifted from --update."""
        measured = sr.measure()
        stored = sr.load_snapshot()
        stale_files = set(stored["files"]) - set(measured["files"])
        stale_funcs = set(stored["functions"]) - set(measured["functions"])
        self.assertEqual(stale_files, set(), f"stale file entries: {stale_files}")
        self.assertEqual(
            stale_funcs, set(), f"stale function entries: {stale_funcs}"
        )


if __name__ == "__main__":
    unittest.main()
