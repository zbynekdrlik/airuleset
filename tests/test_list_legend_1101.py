"""#1101 — `core-quals`/`slice-quals --list` lead with ONE `#`-prefixed legend
line explaining the obligation column (implement vs action-only) and the
label-move routing, so a session reading the number can never disown its own
`action-only` hand-offs as "gk-infra / stream / not mine". `--count` is
UNCHANGED (no header). The #754 non-member-line contract holds: the legend line
starts with `#`, so every consumer that skips `#` lines reads the data rows
unchanged, and `len(data rows) == --count`.

Also locks the DEEP-1 companion doctrine paragraph (the always-on module stays
unchanged; the reading-`--list` legend lives in the deep companion).
"""

import contextlib
import io
import json as _json
import unittest.mock as mk
from pathlib import Path
from unittest import TestCase, main

from authority_testlib import (  # noqa: E402
    airuleset,
    _drive,
    _labelled_rows_gh,
)

ROOT = Path(__file__).resolve().parents[1]
DEEP1 = ROOT / "skills" / "statusline-vocabulary-deep" / "DEEP-1.md"


def _lines(out):
    return [ln for ln in out.splitlines() if ln.strip()]


def _header_lines(out):
    return [ln for ln in _lines(out) if ln.lstrip().startswith("#")]


def _data_lines(out):
    return [ln for ln in _lines(out) if not ln.lstrip().startswith("#")]


class _ListHeaderContract:
    """Shared contract, run against both cmd_core_quals and cmd_slice_quals."""

    def _run_list(self):
        raise NotImplementedError

    def _run_count(self):
        raise NotImplementedError

    def test_list_leads_with_exactly_one_hash_legend_line(self):
        out, err, exc = self._run_list()
        self.assertIsNone(exc, "expected a healthy --list, got exit: %r / %r"
                          % (exc, err))
        lines = _lines(out)
        self.assertTrue(lines, "no --list output at all: %r" % out)
        self.assertTrue(lines[0].lstrip().startswith("#"),
                        "the FIRST non-empty --list line must be the `#` legend "
                        "header; got %r" % lines[0])
        self.assertEqual(len(_header_lines(out)), 1,
                         "exactly ONE `#`-prefixed legend line is allowed; got "
                         "%r" % _header_lines(out))

    def test_legend_explains_the_obligation_column_and_label_routing(self):
        out, _err, _exc = self._run_list()
        header = _header_lines(out)[0]
        for tok in ("implement", "action-only"):
            self.assertIn(tok, header,
                          "legend must name the obligation column value %r" % tok)
        for tok in ("review", "merge", "release"):
            self.assertIn(tok, header.lower(),
                          "legend must state action-only = review/merge/release")
        for tok in ("infra", "prio:bounce", "needs-answer", "ops-wait"):
            self.assertIn(tok, header,
                          "legend must carry the label-move routing token %r"
                          % tok)

    def test_data_rows_skip_the_hash_and_match_count(self):
        list_out, _e1, _x1 = self._run_list()
        count_out, _e2, _x2 = self._run_count()
        n = int(count_out.strip())
        self.assertEqual(
            len(_data_lines(list_out)), n,
            "after skipping the `#` legend, len(--list data rows) must equal "
            "--count (%d); data=%r" % (n, _data_lines(list_out)))
        # every data row still parses field 0 as an int (the #754 contract)
        for ln in _data_lines(list_out):
            int(ln.split("\t", 1)[0])

    def test_count_output_has_no_hash_header(self):
        count_out, _err, _exc = self._run_count()
        self.assertEqual(_header_lines(count_out), [],
                         "--count must be UNCHANGED — no `#` legend line")


class TestCoreQualsListHeader(_ListHeaderContract, TestCase):
    """Full-authority obligation set via the shared _drive/_labelled_rows_gh
    harness (a core row + a foreign-stream hand-off)."""

    def _run_list(self):
        gh, _seen = _labelled_rows_gh()
        return _drive(airuleset.cmd_core_quals, gh, count=False, list=True)

    def _run_count(self):
        gh, _seen = _labelled_rows_gh()
        return _drive(airuleset.cmd_core_quals, gh, count=True, list=False)


class TestSliceQualsListHeader(_ListHeaderContract, TestCase):
    """Reduced-authority slice, mirroring the proven-hermetic pattern from
    test_authority_slice_quals (login=zbynekdrlik, user=montalu1 →
    AUTHORITY_BY_USER reduced; rows for `label:stream:montalu`)."""

    def _slice_run(self, **flags):
        args = dict(count=False, list=False, waiting=False, ops_wait=False,
                    audit=False, dep_wait=False, count_dispatchable=False,
                    extra=None)
        args.update(flags)

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return _json.dumps([
                    {"number": 31, "title": "own work a",
                     "createdAt": "2026-07-01T00:00:00Z", "labels": []},
                    {"number": 32, "title": "own work b",
                     "createdAt": "2026-07-02T00:00:00Z", "labels": []}])
            return "[]"

        out, err, exc = io.StringIO(), io.StringIO(), None
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(out):
                        with contextlib.redirect_stderr(err):
                            try:
                                airuleset.cmd_slice_quals(mk.Mock(**args))
                            except SystemExit as e:
                                exc = e
        return out.getvalue(), err.getvalue(), exc

    def _run_list(self):
        return self._slice_run(list=True)

    def _run_count(self):
        return self._slice_run(count=True)


class TestDeep1CarriesTheReadingListDoctrine(TestCase):
    """The doctrine home for reading `--list` is the DEEP-1 companion (the
    always-on module stays unchanged — context-baseline is down-only)."""

    def test_deep1_has_a_reading_list_legend_paragraph(self):
        text = DEEP1.read_text(encoding="utf-8")
        self.assertIn("--list", text)
        self.assertIn("action-only", text)
        for tok in ("review", "merge", "release"):
            self.assertIn(tok, text.lower())
        # the label-move routing, the actual fix for "not mine"
        for tok in ("infra", "prio:bounce", "needs-answer", "ops-wait"):
            self.assertIn(tok, text)
        # names the exact regression: an action-only hand-off is still YOUR I
        self.assertRegex(text.lower(),
                         r"action-only.{0,400}(oblig|povinnos|your i|tvoj)")

    def test_always_on_module_unchanged_marker(self):
        # statusline-vocabulary.md must NOT gain the legend (down-only ratchet).
        mod = (ROOT / "modules" / "core" / "statusline-vocabulary.md").read_text(
            encoding="utf-8")
        self.assertNotIn("route by LABEL, never by explanation", mod,
                         "the reading-`--list` legend belongs in DEEP-1, not "
                         "the always-on module")


if __name__ == "__main__":
    main()
