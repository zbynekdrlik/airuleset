"""#1100 — gates.navody: the per-stream fact file is resolved through the fleet
rename alias (montalu1 -> montalu.md, david1 -> david.md), in the ONE stream-
file reader every stream-fact gate uses.

Root cause (design comment on #1100): `_current_stream()` returns the unspoofable
uid account name (`montalu1`/`david1` after the #537 base-stream rename), and
`_read_stream_file(cwd, stream)` opened exactly `<repo>/.claude/streams/<stream>.md`
— but odoo-erp's two renamed base streams kept their OLD file names (`montalu.md`,
`david.md`), so `montalu1`/`david1` resolved to no file -> `tenant_guide()` ->
`(None, None)` = UNKNOWN -> the navody gate (and the spec gate, which reads the
same file via `specs_for_stream`) failed CLOSED on every acceptance question.

Approach 1 (the decided design): resolve the file through the fleet alias table
INSIDE `_read_stream_file` — the candidate list `[stream] + alias_equivalents(stream)`
read LAZILY from `cli_fleet.STREAM_RENAME_ALIASES` (a leaf module, try/except -> []),
BOTH edge directions, deduplicated, EXACT name first, first-existing-file wins; NO
numeric-suffix heuristic; `_current_stream()` stays the uid; the UNKNOWN block text
names the candidates tried.

This suite locks:

1. ALIAS PRIMITIVE — `_alias_equivalents(stream)` mirrors `cli_quals.
   _stream_rename_equivalents`' one-edge-either-direction semantics (reading
   `cli_fleet` directly, never the `airuleset` facade), and `_stream_file_
   candidates(stream)` = exact-first + alias, deduplicated, NO suffix heuristic.
2. READER — `_read_stream_file` / `tenant_guide` resolve montalu1->montalu.md and
   david1->david.md; exact name wins over the alias; the reverse edge resolves;
   an unaliased missing file (montalu7) stays UNKNOWN.
3. UNKNOWN BLOCK — `evaluate_stop(..., candidates=...)` and `run()` name the
   candidate files in the fail-closed message.
4. SPEC GATE — `gates.spec.specs_for_stream(cwd, "montalu1")` reads montalu.md's
   `specs:` fact for free (it calls `_read_stream_file`).
5. HERMETIC DEGRADE — with the `cli_fleet` import failing, the reader degrades to
   the exact name only (no alias, still fail-closed, never a wrong tenant).

RED-before-GREEN: against the base tree (v0.1.383) `_alias_equivalents` /
`_stream_file_candidates` do not exist, `_read_stream_file` opens only the exact
name, and `evaluate_stop` rejects the `candidates` kwarg.
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import navody  # noqa: E402
from gates import spec    # noqa: E402


def _mapping_reader(mapping):
    """A `read_text(path) -> text|None` keyed on the file BASENAME, mirroring the
    real filesystem: a candidate whose `<name>.md` is absent from the mapping
    reads None (so the reader tries the next candidate)."""
    def _rt(path):
        return mapping.get(os.path.basename(path))
    return _rt


# --------------------------------------------------------------------------- #
# 1. ALIAS PRIMITIVE
# --------------------------------------------------------------------------- #
class TestAliasPrimitive(unittest.TestCase):
    def test_old_to_new_edge(self):
        # montalu -> montalu1 (old base-stream name -> new uid)
        self.assertEqual(navody._alias_equivalents("montalu"), ["montalu1"])
        self.assertEqual(navody._alias_equivalents("david"), ["david1"])

    def test_new_to_old_edge(self):
        # montalu1 -> montalu (new uid -> the file that kept the old name)
        self.assertEqual(navody._alias_equivalents("montalu1"), ["montalu"])
        self.assertEqual(navody._alias_equivalents("david1"), ["david"])

    def test_unaliased_name_has_no_equivalent(self):
        # a numbered sibling stream is in NO rename -> no alias (no suffix heuristic)
        self.assertEqual(navody._alias_equivalents("montalu7"), [])
        self.assertEqual(navody._alias_equivalents("david3"), [])
        self.assertEqual(navody._alias_equivalents("miva1"), [])

    def test_none_and_empty(self):
        self.assertEqual(navody._alias_equivalents(None), [])
        self.assertEqual(navody._alias_equivalents(""), [])

    def test_candidates_exact_first_then_alias(self):
        self.assertEqual(navody._stream_file_candidates("montalu1"),
                         ["montalu1", "montalu"])
        self.assertEqual(navody._stream_file_candidates("montalu"),
                         ["montalu", "montalu1"])
        self.assertEqual(navody._stream_file_candidates("david1"),
                         ["david1", "david"])

    def test_candidates_no_suffix_heuristic(self):
        # montalu7 must NEVER include montalu (a wrong tenant's fact is worse
        # than fail-closed UNKNOWN).
        self.assertEqual(navody._stream_file_candidates("montalu7"), ["montalu7"])

    def test_candidates_none(self):
        self.assertEqual(navody._stream_file_candidates(None), [])
        self.assertEqual(navody._stream_file_candidates(""), [])


# --------------------------------------------------------------------------- #
# 2. READER — tenant_guide via alias
# --------------------------------------------------------------------------- #
class TestReaderAliasResolution(unittest.TestCase):
    def test_montalu1_resolves_url_from_montalu_file(self):
        rt = _mapping_reader(
            {"montalu.md": "navody_url: https://erp.montalu.cloud/p/tok/navody-money.html\n"})
        url, ticket = navody.tenant_guide("/repo", "montalu1", read_text=rt)
        self.assertEqual(url, "https://erp.montalu.cloud/p/tok/navody-money.html")
        self.assertIsNone(ticket)

    def test_david1_resolves_none_ticket_from_david_file(self):
        rt = _mapping_reader({"david.md": "navody_url: NONE — #7560\n"})
        url, ticket = navody.tenant_guide("/repo", "david1", read_text=rt)
        self.assertIsNone(url)
        self.assertEqual(ticket, "#7560")

    def test_exact_name_wins_over_alias(self):
        # both montalu1.md and montalu.md present -> the EXACT file wins.
        rt = _mapping_reader({
            "montalu1.md": "navody_url: https://x/navody-EXACT.html\n",
            "montalu.md": "navody_url: https://x/navody-OLD.html\n",
        })
        url, _ = navody.tenant_guide("/repo", "montalu1", read_text=rt)
        self.assertEqual(url, "https://x/navody-EXACT.html")

    def test_reverse_edge_montalu_finds_montalu1_file(self):
        # a future rename of the FILE: stream 'montalu' resolving montalu1.md.
        rt = _mapping_reader({"montalu1.md": "navody_url: https://x/navody-REV.html\n"})
        url, _ = navody.tenant_guide("/repo", "montalu", read_text=rt)
        self.assertEqual(url, "https://x/navody-REV.html")

    def test_unaliased_missing_file_stays_unknown(self):
        # montalu7 with no montalu7.md -> UNKNOWN even though montalu.md exists
        # (the negative lock: NO suffix heuristic).
        rt = _mapping_reader({"montalu.md": "navody_url: https://x/navody-a.html\n"})
        url, ticket = navody.tenant_guide("/repo", "montalu7", read_text=rt)
        self.assertIsNone(url)
        self.assertIsNone(ticket)


# --------------------------------------------------------------------------- #
# 3. UNKNOWN BLOCK names the candidates
# --------------------------------------------------------------------------- #
class TestUnknownBlockNamesCandidates(unittest.TestCase):
    def test_evaluate_stop_names_both_candidate_files(self):
        cands = navody._stream_file_candidates("montalu1")
        v, reason = navody.evaluate_stop(None, None, "…akceptačná správa…",
                                         candidates=cands)
        self.assertEqual(v, "block")
        self.assertIn("montalu1.md", reason)
        self.assertIn("montalu.md", reason)

    def test_evaluate_stop_without_candidates_keeps_base_text(self):
        # backward compat: no candidates -> the existing #1073 fix text.
        v, reason = navody.evaluate_stop(None, None, "…akceptačná správa…")
        self.assertEqual(v, "block")
        self.assertIn("navody_url", reason)

    def test_run_unknown_block_threads_candidates(self):
        # end-to-end: run() -> _current_stream (patched montalu1) ->
        # _stream_file_candidates -> evaluate_stop(candidates) -> named files.
        rt = _mapping_reader({})  # no file for any candidate -> UNKNOWN
        payload = {"last_assistant_message": "…akceptačná správa…", "cwd": "/repo"}
        err = io.StringIO()
        with mock.patch.object(navody, "_current_stream", return_value="montalu1"):
            with self.assertRaises(SystemExit) as cm:
                with contextlib.redirect_stderr(err):
                    navody.run(payload, read_text=rt)
        self.assertEqual(cm.exception.code, 2)
        reason = err.getvalue()
        self.assertIn("montalu1.md", reason)
        self.assertIn("montalu.md", reason)


# --------------------------------------------------------------------------- #
# 4. SPEC GATE inherits the fix (reads via _read_stream_file)
# --------------------------------------------------------------------------- #
class TestSpecGateAliasResolution(unittest.TestCase):
    def test_specs_for_stream_resolves_alias(self):
        rt = _mapping_reader(
            {"montalu.md": "navody_url: https://x\nspecs: #7801, #7802\n"})
        got = spec.specs_for_stream("/repo", "montalu1", read_text=rt)
        self.assertEqual(got, [7801, 7802])

    def test_specs_for_stream_unaliased_missing_stays_empty(self):
        rt = _mapping_reader({"montalu.md": "specs: #7801\n"})
        self.assertEqual(spec.specs_for_stream("/repo", "montalu7", read_text=rt),
                         [])


# --------------------------------------------------------------------------- #
# 5. HERMETIC DEGRADE — cli_fleet import failing
# --------------------------------------------------------------------------- #
class TestHermeticDegrade(unittest.TestCase):
    def test_degrades_to_exact_name_when_cli_fleet_unavailable(self):
        # sys.modules[name] = None makes `import cli_fleet` raise ImportError.
        with mock.patch.dict(sys.modules, {"cli_fleet": None}):
            self.assertEqual(navody._alias_equivalents("montalu1"), [])
            self.assertEqual(navody._stream_file_candidates("montalu1"),
                             ["montalu1"])
            rt = _mapping_reader(
                {"montalu.md": "navody_url: https://x/navody-a.html\n"})
            url, ticket = navody.tenant_guide("/repo", "montalu1", read_text=rt)
            self.assertIsNone(url)
            self.assertIsNone(ticket)

    def test_degrades_when_alias_table_malformed(self):
        # the WHOLE resolution is guarded: a non-dict STREAM_RENAME_ALIASES (a
        # `.items()`/`in` failure, not an import failure) must ALSO degrade to []
        # / exact-name-only, never raise out of the Stop hook.
        import cli_fleet
        for bad in (None, ["montalu1"], 42):
            with mock.patch.object(cli_fleet, "STREAM_RENAME_ALIASES", bad):
                self.assertEqual(navody._alias_equivalents("montalu1"), [])
                self.assertEqual(navody._stream_file_candidates("montalu1"),
                                 ["montalu1"])

    def test_alias_still_works_after_degrade_context(self):
        # the try/except must not permanently poison the alias (cli_fleet is a
        # real leaf module once the patch is lifted).
        self.assertEqual(navody._alias_equivalents("montalu1"), ["montalu"])


if __name__ == "__main__":
    unittest.main()
