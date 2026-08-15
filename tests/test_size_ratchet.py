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

    def test_colliding_qualname_keeps_the_largest_not_the_last(self):
        """A property getter/setter pair, an @overload group, or a
        try/except-guarded def all legitimately share one qualname —
        keeping only the LAST one seen would let growth in an EARLIER
        definition go completely unmeasured. max() closes that."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text(
                "class C:\n"
                "    @property\n"
                "    def x(self):\n"
                "        a = 1\n"
                "        b = 2\n"
                "        c = 3\n"
                "        d = 4\n"
                "        return a + b + c + d\n"
                "\n"
                "    @x.setter\n"
                "    def x(self, value):\n"
                "        self._x = value\n",
                encoding="utf-8",
            )
            counts = sr.function_line_counts(p)
            # the getter (6 lines, decorator line excluded from ast lineno)
            # must win over the setter (2 lines) — the LARGER of the two,
            # not whichever was textually last
            self.assertEqual(counts["C.x"], 6)


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


class TestRegenBootstrap(unittest.TestCase):
    """bootstrap=True is the one-time, explicit "seed from today's reality"
    act — the exact opposite bias from ordinary --update: no default-cap
    refusal, everything captured verbatim, however large it already is."""

    def test_bootstrap_seeds_an_oversized_file_verbatim_with_no_refusal(self):
        huge = sr.FILE_DEFAULT_CEILING * 10
        measured = {"files": {"watchdog/__init__.py": huge}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(new_snapshot["files"]["watchdog/__init__.py"], huge)
        self.assertEqual(refusals, [])

    def test_bootstrap_seeds_an_oversized_function_at_or_above_threshold(self):
        huge = sr.FUNC_DEFAULT_CEILING * 5
        measured = {"files": {}, "functions": {"watchdog/__init__.py::run_once": huge}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(
            new_snapshot["functions"]["watchdog/__init__.py::run_once"], huge
        )
        self.assertEqual(refusals, [])

    def test_bootstrap_still_skips_a_function_under_the_track_threshold(self):
        measured = {"files": {}, "functions": {"a.py::tiny": sr.FUNC_TRACK_THRESHOLD - 1}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(new_snapshot["functions"], {})
        self.assertEqual(refusals, [])

    def test_bootstrap_result_passes_check_immediately(self):
        """The whole point: a bootstrapped snapshot must make check() clean
        on the SAME tree it was cut from — a ratchet must never fail on day
        one."""
        measured = {
            "files": {"huge.py": 20000},
            "functions": {"huge.py::big": 5000, "huge.py::small": 10},
        }
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(refusals, [])
        self.assertEqual(sr.check(measured, new_snapshot), [])

    def test_bootstrap_refuses_outright_when_files_are_already_tracked(self):
        """bootstrap is a ONE-TIME act — re-running it against an
        already-seeded snapshot must not silently bless a new oversized
        item just because the flag was passed again. Nothing is written;
        the WHOLE operation refuses, not just the individual new items."""
        measured = {
            "files": {"a.py": 100, "huge_new.py": sr.FILE_DEFAULT_CEILING + 5000},
            "functions": {},
        }
        stored = {"files": {"a.py": 100}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(new_snapshot, {"files": {"a.py": 100}, "functions": {}})
        self.assertEqual(len(refusals), 1)
        self.assertIn("REFUSED", refusals[0])
        self.assertIn("empty/missing snapshot", refusals[0])
        self.assertIn("ONE-TIME", refusals[0])
        self.assertIn("--update", refusals[0])
        # and huge_new.py was NOT silently blessed
        self.assertNotIn("huge_new.py", new_snapshot["files"])

    def test_bootstrap_refuses_outright_when_only_functions_are_tracked(self):
        """The refusal fires on EITHER kind already having entries, not
        only when files are non-empty."""
        measured = {"files": {}, "functions": {"a.py::f": 250}}
        stored = {"files": {}, "functions": {"a.py::f": 250}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(new_snapshot, stored)
        self.assertEqual(len(refusals), 1)

    def test_bootstrap_on_a_genuinely_empty_snapshot_is_not_refused(self):
        """The refusal is specifically about a NON-empty snapshot — an
        empty (or missing) one is exactly what bootstrap exists for."""
        measured = {"files": {"a.py": 500}, "functions": {}}
        stored = {"files": {}, "functions": {}}
        new_snapshot, refusals = sr.regen(measured, stored, bootstrap=True)
        self.assertEqual(refusals, [])
        self.assertEqual(new_snapshot["files"]["a.py"], 500)

    def test_bootstrap_refusal_does_not_mutate_the_stored_argument(self):
        stored = {"files": {"a.py": 100}, "functions": {}}
        before = json.dumps(stored)
        sr.regen({"files": {"a.py": 100}, "functions": {}}, stored, bootstrap=True)
        self.assertEqual(json.dumps(stored), before)


class TestSnapshotIsEmpty(unittest.TestCase):
    def test_true_for_a_genuinely_empty_dict(self):
        self.assertTrue(sr.snapshot_is_empty({"files": {}, "functions": {}}))

    def test_false_with_any_file_entry(self):
        self.assertFalse(sr.snapshot_is_empty({"files": {"a.py": 1}, "functions": {}}))

    def test_false_with_any_function_entry(self):
        self.assertFalse(
            sr.snapshot_is_empty({"files": {}, "functions": {"a.py::f": 1}})
        )


# --- exemption mechanism (empty in production, proven via monkeypatch) -----


class TestExemptions(unittest.TestCase):
    def test_exempt_file_is_skipped_by_check_even_if_over_ceiling(self):
        orig = sr.EXEMPT_FILES
        try:
            sr.EXEMPT_FILES = {"exempt.py"}
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

    def test_exempt_function_is_skipped_by_measure_even_if_oversized(self):
        """The EXEMPT_FUNCTIONS mechanism must actually work end to end
        through measure() — not merely exist as an unread constant."""
        orig = sr.EXEMPT_FUNCTIONS
        try:
            with tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / "x.py").write_text(
                    "def huge():\n" + "    a = 1\n" * 500 + "    return a\n",
                    encoding="utf-8",
                )
                # sanity: without the exemption it's measured, and it's
                # genuinely oversized (so the exemption is meaningfully
                # hiding a real violation, not a no-op on a small function)
                baseline = sr.measure(repo)
                self.assertIn("x.py::huge", baseline["functions"])
                self.assertGreater(
                    baseline["functions"]["x.py::huge"], sr.FUNC_DEFAULT_CEILING
                )

                sr.EXEMPT_FUNCTIONS = {"x.py::huge"}
                exempted = sr.measure(repo)
                self.assertNotIn("x.py::huge", exempted["functions"])
        finally:
            sr.EXEMPT_FUNCTIONS = orig

    def test_production_exempt_sets_are_empty(self):
        """No exemption is currently justified — see the #404 design
        comment. This pins that the shipped set stays empty unless a future
        change adds a genuinely-justified, commented entry."""
        self.assertEqual(sr.EXEMPT_FILES, set())
        self.assertEqual(sr.EXEMPT_FUNCTIONS, set())


# --- invariants between the tunable constants -------------------------------


class TestConstantInvariants(unittest.TestCase):
    def test_track_threshold_never_exceeds_the_func_default_ceiling(self):
        """Locks the module-level assert: if this relationship is ever
        violated, a freshly-bootstrapped function between the two values
        would fail check() immediately, even with nothing having grown
        since bootstrap — day one must never itself be a failure."""
        self.assertLessEqual(sr.FUNC_TRACK_THRESHOLD, sr.FUNC_DEFAULT_CEILING)

    def test_violating_the_invariant_is_caught_at_import_time(self):
        """Mutate a COPY of the module source so the threshold exceeds the
        default, and confirm the module-level assert actually fires — not
        just that the current shipped values happen to satisfy it."""
        src = SCRIPT.read_text(encoding="utf-8")
        needle = "FUNC_TRACK_THRESHOLD = 200"
        self.assertEqual(src.count(needle), 1)
        mutated = src.replace(needle, "FUNC_TRACK_THRESHOLD = 99999", 1)
        self.assertNotEqual(mutated, src)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mutant_size_ratchet.py"
            p.write_text(mutated, encoding="utf-8")
            spec = importlib.util.spec_from_file_location("mutant_size_ratchet", p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["mutant_size_ratchet"] = mod
            with self.assertRaises(AssertionError):
                spec.loader.exec_module(mod)


# --- malformed hand-edited JSON is refused with a clear message, never a ---
# --- raw TypeError deep inside check()/regen() ------------------------------


class TestSnapshotValidation(unittest.TestCase):
    def _load(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            return sr.load_snapshot(p)

    def test_string_ceiling_is_refused_with_a_clear_message(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"files": {"a.py": "1500"}, "functions": {}})
        self.assertIn("a.py", str(ctx.exception))
        self.assertIn("integer", str(ctx.exception))

    def test_null_ceiling_is_refused(self):
        with self.assertRaises(ValueError):
            self._load({"files": {"a.py": None}, "functions": {}})

    def test_negative_ceiling_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"files": {"a.py": -5}, "functions": {}})
        self.assertIn("negative", str(ctx.exception))

    def test_files_as_a_list_is_refused(self):
        with self.assertRaises(ValueError):
            self._load({"files": ["a.py"], "functions": {}})

    def test_bool_ceiling_is_refused(self):
        """bool is an int subclass in Python — must not silently pass as
        a plain integer line count."""
        with self.assertRaises(ValueError):
            self._load({"files": {"a.py": True}, "functions": {}})

    def test_a_well_formed_snapshot_loads_cleanly(self):
        data = self._load({"files": {"a.py": 100}, "functions": {"a.py::f": 50}})
        self.assertEqual(data["files"], {"a.py": 100})
        self.assertEqual(data["functions"], {"a.py::f": 50})

    def test_top_level_non_object_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            with self.assertRaises(ValueError):
                sr.load_snapshot(p)


# --- save_snapshot() writes atomically --------------------------------------


class TestSaveSnapshotAtomicity(unittest.TestCase):
    def test_no_leftover_tmp_file_after_a_successful_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            sr.save_snapshot({"files": {"a.py": 1}, "functions": {}}, p)
            leftovers = [f for f in Path(d).iterdir() if f != p]
            self.assertEqual(leftovers, [])

    def test_a_failed_write_never_corrupts_an_existing_snapshot(self):
        """Simulate a kill/interruption between the tmp write and the
        atomic rename — the ORIGINAL file must survive untouched, and the
        tmp scratch file must not linger."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snap.json"
            original = {"files": {"a.py": 100}, "functions": {}}
            sr.save_snapshot(original, p)
            original_bytes = p.read_bytes()

            import unittest.mock as mock

            with mock.patch("os.replace", side_effect=OSError("simulated kill")):
                with self.assertRaises(OSError):
                    sr.save_snapshot({"files": {"a.py": 1}, "functions": {}}, p)

            # the original file is untouched
            self.assertEqual(p.read_bytes(), original_bytes)
            # and no stray tmp scratch file survives in the directory
            leftovers = [f for f in Path(d).iterdir() if f != p]
            self.assertEqual(leftovers, [])


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

    @classmethod
    def setUpClass(cls):
        # measure() walks ~154 real files via ast — measure it ONCE for the
        # whole class instead of once per test method (each of the three
        # test methods below needs the identical, real, unmutated result).
        cls.measured = sr.measure()
        cls.stored = sr.load_snapshot()

    def test_no_tracked_file_or_function_exceeds_its_recorded_ceiling(self):
        violations = sr.check(self.measured, self.stored)
        self.assertEqual(
            violations,
            [],
            "size ratchet violated:\n" + "\n".join(violations),
        )

    def test_every_current_300plus_line_function_has_an_explicit_ceiling(self):
        """Sanity lock: the day-one snapshot must genuinely have captured
        every current large-function offender, not merely happen to pass
        because none of them exceed the flat default."""
        offenders = [k for k, n in self.measured["functions"].items() if n >= 300]
        untracked = [k for k in offenders if k not in self.stored["functions"]]
        self.assertEqual(
            untracked,
            [],
            "these >=300-line functions have no explicit ceiling entry: "
            + ", ".join(untracked),
        )

    def test_snapshot_has_no_stale_entries_for_deleted_items(self):
        """Every stored key must still resolve to something real in the
        tracked tree — otherwise the snapshot has drifted from --update."""
        stale_files = set(self.stored["files"]) - set(self.measured["files"])
        stale_funcs = set(self.stored["functions"]) - set(self.measured["functions"])
        self.assertEqual(stale_files, set(), f"stale file entries: {stale_files}")
        self.assertEqual(
            stale_funcs, set(), f"stale function entries: {stale_funcs}"
        )


class TestCliBootstrapFlagRequiresUpdate(unittest.TestCase):
    def test_bootstrap_with_check_instead_of_update_is_rejected(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--bootstrap"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--bootstrap only makes sense together with --update", r.stderr)

    def test_bootstrap_with_no_mode_at_all_is_rejected_by_argparse(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--bootstrap"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        self.assertNotEqual(r.returncode, 0)

    def test_update_bootstrap_against_the_real_already_seeded_snapshot_is_refused(self):
        """The REAL, committed tests/size_ratchet.json is non-empty (154
        files tracked) — re-running --update --bootstrap against it must
        refuse outright and NEVER touch the file on disk."""
        import subprocess

        before = (REPO / "tests" / "size_ratchet.json").read_bytes()
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--update", "--bootstrap"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        after = (REPO / "tests" / "size_ratchet.json").read_bytes()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("ONE-TIME", r.stdout)
        self.assertEqual(before, after, "the real snapshot must be untouched")


class TestRuleBytesCap(unittest.TestCase):
    """#482: a byte-size cap on every path-scoped ``.claude/rules/*.md`` so the
    former 973 KB monolith can never regrow into a session-killing injection.
    BYTE metric (not lines) on purpose: the monolith was only 1575 lines but
    973 KB (~616 B/line), so a line cap would never have caught it. Distinct
    semantic from the .py ratchet above: a flat CAP (files may grow with
    playbook lessons up to the cap, then must archive), not freeze-at-current."""

    def test_default_cap_is_50kb(self):
        self.assertEqual(sr.RULE_BYTES_DEFAULT_CEILING, 51200)

    def test_tracked_rule_files_are_pathscoped_md_only(self):
        files = sr.tracked_rule_files()
        self.assertTrue(files, "expected the split per-area rule files")
        for rel in files:
            self.assertTrue(rel.startswith(".claude/rules/"), rel)
            self.assertTrue(rel.endswith(".md"), rel)
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertTrue(text.lstrip().startswith("---"), f"{rel}: no frontmatter")
            head = text.split("---")[1]
            self.assertIn("paths:", head, f"{rel}: frontmatter has no paths:")

    def test_the_ondemand_archive_is_not_tracked(self):
        files = sr.tracked_rule_files()
        for rel in files:
            self.assertNotIn("rules-reference", rel)

    def test_measure_includes_rule_bytes(self):
        m = sr.measure()
        self.assertIn("rule_bytes", m)
        self.assertTrue(m["rule_bytes"])
        for n in m["rule_bytes"].values():
            self.assertIsInstance(n, int)

    def test_rule_over_cap_fails_with_byte_wording(self):
        measured = {"files": {}, "functions": {},
                    "rule_bytes": {".claude/rules/internals-x.md": sr.RULE_BYTES_DEFAULT_CEILING + 1}}
        stored = {"files": {}, "functions": {}, "rule_bytes": {}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("internals-x.md", v[0])
        self.assertIn("bytes", v[0].lower())

    def test_rule_under_cap_passes(self):
        measured = {"files": {}, "functions": {},
                    "rule_bytes": {".claude/rules/internals-x.md": 40000}}
        stored = {"files": {}, "functions": {}, "rule_bytes": {}}
        self.assertEqual(sr.check(measured, stored), [])

    def test_rule_respects_a_tighter_stored_ceiling(self):
        measured = {"files": {}, "functions": {},
                    "rule_bytes": {".claude/rules/internals-x.md": 45000}}
        stored = {"files": {}, "functions": {},
                  "rule_bytes": {".claude/rules/internals-x.md": 40000}}
        v = sr.check(measured, stored)
        self.assertEqual(len(v), 1)
        self.assertIn("40000", v[0])

    def test_every_real_pathscoped_rule_file_is_under_the_cap(self):
        m = sr.measure()
        over = {k: n for k, n in m["rule_bytes"].items()
                if n > sr.RULE_BYTES_DEFAULT_CEILING}
        self.assertEqual(over, {}, f"path-scoped rule files over the 50KB cap: {over}")


if __name__ == "__main__":
    unittest.main()
