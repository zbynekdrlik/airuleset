"""Per-DISPATCH floor vs in-dispatch growth (#131).

#130 built the MAIN-vs-SUBAGENT meter but stores only one folded row per
transcript: `scan_split()`'s per-file loop (burn/__init__.py:1187-1227) sums
every usage line into a single accumulator and discards per-line ordering, and
`ctx_per_turn()` (burn/__init__.py:1054) is one mean over the whole window. So
the question #131 asks — how much of a subagent turn is the fixed prefix it
starts with, and how much is growth it accumulated — has no representation in
what #130 stores.

Two things these tests lock, both of which the earlier hand-count got wrong:

* **A transcript LINE is not a turn.** Claude Code writes one assistant API
  response as several lines (one per content block), each carrying a COPY of
  the same request-level `usage`. Measured on a real transcript: 236 usage
  lines, 111 distinct `requestId`s, one request repeated across 9 lines each
  restating the same `cache_read_input_tokens: 118,307`. Counting lines
  inflates turns ~2x and multiply-counts the same input tokens.
* **A FILE is not an in-window dispatch.** A dispatch that started before the
  window has no floor inside it — its first in-window request is already
  carrying accumulated growth. Such straddlers are excluded and counted, never
  averaged in.

The instrument is additive: `scan_split()` and `scan()` are untouched, because
they feed the standing #130 meter and `hourly_burn_alert()`'s live baselines.
"""
import argparse
import contextlib
import datetime
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import burn


UTC = datetime.timezone.utc


def _line(ts, rid, i=0, cw=0, cr=0, o=0, cwd="/home/newlevel/devel/demo"):
    """One transcript line carrying a request-level usage snapshot."""
    return json.dumps({
        "timestamp": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "requestId": rid,
        "isSidechain": True,
        "cwd": cwd,
        "type": "assistant",
        "message": {"id": "msg_" + rid, "model": "claude-sonnet-5",
                    "usage": {"input_tokens": i,
                              "cache_creation_input_tokens": cw,
                              "cache_read_input_tokens": cr,
                              "output_tokens": o}},
    })


def _prompt_line(ts, text, cwd="/home/newlevel/devel/demo"):
    return json.dumps({
        "timestamp": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "type": "user", "isSidechain": True, "cwd": cwd,
        "message": {"role": "user", "content": text},
    })


def _attachment_line(ts, atype, payload):
    d = {"timestamp": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
         "type": "attachment", "attachment": dict(payload, type=atype)}
    return json.dumps(d)


def _write_sub(root, project, session, agent, lines):
    d = Path(root) / project / session / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (agent + ".jsonl")
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_parent(root, project, session, lines):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / (session + ".jsonl")
    p.write_text("\n".join(lines) + "\n")
    return p


class TestOneRequestIsOneTurn(unittest.TestCase):
    """The content-block duplication that made every earlier count wrong."""

    def test_repeated_usage_lines_of_one_request_are_one_turn(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            # One API request written as three lines (thinking + two tool_use
            # blocks), each restating the same request-level usage.
            _write_sub(root, "proj", "s1", "agent-a1", [
                _line(now, "req_1", cw=1000, o=3),
                _line(now, "req_1", cw=1000, o=3),
                _line(now, "req_1", cw=1000, o=440),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["floor"], 1000)
        self.assertEqual(row["total_ctx"], 1000)
        # The final block carries the response's real output total.
        self.assertEqual(row["out"], 440)

    def test_message_id_is_the_fallback_when_requestid_is_absent(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            a = json.loads(_line(now, "req_1", cw=500))
            b = json.loads(_line(now, "req_1", cw=500))
            for e in (a, b):
                e.pop("requestId")
            _write_sub(root, "proj", "s1", "agent-a1",
                       [json.dumps(a), json.dumps(b)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual(data["dispatches"][0]["turns"], 1)


class TestSyntheticEntriesAreNotTurns(unittest.TestCase):
    """Claude Code writes a `<synthetic>` placeholder entry (interrupt / error)
    carrying a usage block whose four counters are all zero and a UUID where a
    requestId would be. It is not an API request. Counted as one, it becomes a
    dispatch's LAST request and reports a context of 0 — which showed up live
    as a growth of -117,959 on 1 of 301 real dispatches.
    """

    def _synthetic(self, ts):
        return json.dumps({
            "timestamp": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "requestId": "a2beb015-5f2f-4b96-a3d8-143b1be75b08",
            "type": "assistant",
            "message": {"model": "<synthetic>", "usage": {
                "input_tokens": 0, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": 0}},
        })

    def test_a_trailing_synthetic_entry_does_not_become_the_last_turn(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                _line(now, "req_1", cw=100000),
                _line(now, "req_2", cw=800, cr=100000),
                self._synthetic(now),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["turns"], 2)
        self.assertEqual(row["last"], 100800)
        self.assertEqual(row["growth"], 800)

    def test_a_leading_synthetic_entry_does_not_become_the_floor(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                self._synthetic(now),
                _line(now, "req_1", cw=100000),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["floor"], 100000)

    def test_a_dispatch_of_only_synthetic_entries_is_not_a_dispatch(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [self._synthetic(now)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual(data["dispatches"], [])

    def test_a_real_request_reporting_zero_context_is_still_dropped(self):
        """The discriminator is the zero USAGE, not the model string — a
        placeholder written without the `<synthetic>` marker must not be
        allowed to report a 0-token context either."""
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                _line(now, "req_1", cw=50000),
                _line(now, "req_2", i=0, cw=0, cr=0, o=0),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual(data["dispatches"][0]["turns"], 1)


class TestFloorAndGrowth(unittest.TestCase):
    def test_floor_is_the_first_request_and_last_is_the_last(self):
        now = datetime.datetime.now(UTC)

        def t(m):
            return now - datetime.timedelta(minutes=m)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                _line(t(30), "req_1", cw=100000, cr=0),
                _line(t(20), "req_2", cw=5000, cr=100000),
                _line(t(10), "req_3", cw=8000, cr=105000),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["turns"], 3)
        self.assertEqual(row["floor"], 100000)
        self.assertEqual(row["last"], 113000)
        self.assertEqual(row["growth"], 13000)
        self.assertEqual(row["total_ctx"], 100000 + 105000 + 113000)

    def test_a_single_turn_dispatch_has_zero_growth(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=42000)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["growth"], 0)
        self.assertEqual(row["floor"], row["last"])


class TestOnlyDispatchesThatSTARTEDInWindowCount(unittest.TestCase):
    """A file touched in the window is not a dispatch begun in the window."""

    def test_straddling_dispatch_is_excluded_and_counted_separately(self):
        now = datetime.datetime.now(UTC)
        before = now - datetime.timedelta(hours=20)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-old", [
                _line(before, "req_1", cw=90000),
                _line(now - datetime.timedelta(minutes=5), "req_2",
                      cw=1000, cr=90000),
            ])
            _write_sub(root, "proj", "s1", "agent-new", [
                _line(now - datetime.timedelta(minutes=9), "req_1", cw=70000),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual(len(data["dispatches"]), 1)
        self.assertEqual(data["dispatches"][0]["floor"], 70000)
        self.assertEqual(data["straddling"], 1)

    def test_main_transcripts_are_not_dispatches(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_parent(root, "proj", "s1", [_line(now, "req_1", cw=1234)])
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_2", cw=4321)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual([d["floor"] for d in data["dispatches"]], [4321])


class TestDistributionNotAMean(unittest.TestCase):
    """#131 rejects a single mean explicitly — a mean already exists."""

    def test_percentiles_and_n_are_reported(self):
        d = burn.distribution([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(d["n"], 10)
        self.assertEqual(d["min"], 1)
        self.assertEqual(d["max"], 10)
        self.assertEqual(d["median"], 5)
        self.assertEqual(d["p25"], 3)
        self.assertEqual(d["p75"], 8)
        self.assertEqual(d["p90"], 9)
        # 55/10 = 5.5 -> 6. The mean is deliberately reported ALONGSIDE the
        # quantiles, not instead of them: on the real data it sits between p75
        # and p90, which is exactly why #131 rejects reporting it alone.
        self.assertEqual(d["mean"], 6)

    def test_empty_is_none_not_a_fabricated_zero(self):
        self.assertIsNone(burn.distribution([]))

    def test_a_single_value_is_its_own_every_quantile(self):
        d = burn.distribution([7])
        self.assertEqual((d["n"], d["min"], d["median"], d["max"]), (1, 7, 7, 7))


class TestFloorVersusGrowthAccounting(unittest.TestCase):
    def test_context_tokens_split_floor_times_turns_from_growth(self):
        rows = [{"floor": 100, "turns": 3, "total_ctx": 100 + 150 + 200,
                 "units": 0.0}]
        acc = burn.floor_growth_totals(rows)
        self.assertEqual(acc["context"]["floor"], 300)
        self.assertEqual(acc["context"]["growth"], 150)
        self.assertEqual(acc["context"]["total"], 450)
        self.assertAlmostEqual(acc["context"]["floor_share"], 300 / 450)

    def test_cost_model_prices_the_floor_written_once_then_reread(self):
        """The floor is cache-WRITTEN on turn 1 and cache-READ afterwards, so
        its COST share is far below its TOKEN share. Reporting only the token
        share would overstate the lever."""
        rows = [{"floor": 1000, "turns": 11, "total_ctx": 99999, "units": 0.0}]
        acc = burn.floor_growth_totals(rows)
        w = burn.COST_UNIT_WEIGHTS
        self.assertAlmostEqual(acc["cost"]["floor_write_units"],
                               1000 * w["cache_w"])
        self.assertAlmostEqual(acc["cost"]["floor_read_units"],
                               1000 * 10 * w["cache_r"])

    def test_no_dispatches_yields_no_fabricated_shares(self):
        acc = burn.floor_growth_totals([])
        self.assertIsNone(acc["context"]["floor_share"])


class TestAgentTypeJoin(unittest.TestCase):
    """The floor is bimodal by agent TYPE, so the type must be recoverable."""

    def _parent_lines(self, now, tool_use_id, agent_id, subagent_type):
        use = {"timestamp": now.isoformat().replace("+00:00", "Z"),
               "type": "assistant",
               "message": {"content": [
                   {"type": "tool_use", "id": tool_use_id, "name": "Agent",
                    "input": {"subagent_type": subagent_type,
                              "prompt": "do the thing"}}]}}
        res = {"timestamp": now.isoformat().replace("+00:00", "Z"),
               "type": "user",
               "message": {"content": [
                   {"type": "tool_result", "tool_use_id": tool_use_id,
                    "content": [{"type": "text",
                                 "text": "agentId: %s (internal)" % agent_id}]}]}}
        return [json.dumps(use), json.dumps(res)]

    def test_type_is_joined_through_the_tool_result_that_names_the_agent(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_parent(root, "proj", "s1",
                          self._parent_lines(now, "toolu_1", "a1", "Explore"))
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=20000)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertEqual(data["dispatches"][0]["agent_type"], "Explore")

    def test_unjoinable_dispatch_reports_none_never_a_guess(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=20000)])
            data = burn.scan_dispatches(root, hours=12, now=now)
        self.assertIsNone(data["dispatches"][0]["agent_type"])


class TestFloorComponentsAreMeasuredNotGuessed(unittest.TestCase):
    def test_import_closure_counts_every_imported_file_once(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.md").write_text("AAAA\n@%s/b.md\n@%s/c.md\n"
                                       % (root, root))
            (root / "b.md").write_text("BB\n@%s/c.md\n" % root)
            (root / "c.md").write_text("CCC\n")
            total = burn.import_closure_chars(str(root / "a.md"))
            expected = len((root / "a.md").read_text()) \
                + len((root / "b.md").read_text()) \
                + len((root / "c.md").read_text())
        # c.md is imported by BOTH a.md and b.md and must still count once.
        self.assertEqual(total, expected)

    def test_a_cycle_terminates(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.md").write_text("A\n@%s/b.md\n" % root)
            (root / "b.md").write_text("B\n@%s/a.md\n" % root)
            self.assertEqual(burn.import_closure_chars(str(root / "a.md")),
                             len("A\n@%s/b.md\n" % root)
                             + len("B\n@%s/a.md\n" % root))

    def test_missing_file_is_zero_not_an_error(self):
        self.assertEqual(burn.import_closure_chars("/nonexistent/x.md"), 0)

    def test_transcript_carried_components_are_read_exactly(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                _prompt_line(now, "x" * 300),
                _attachment_line(now, "skill_listing",
                                 {"content": "y" * 900, "skillCount": 4}),
                _attachment_line(now, "deferred_tools_delta",
                                 {"addedNames": ["A", "B", "C"]}),
                _line(now, "req_1", cw=50000),
            ])
            data = burn.scan_dispatches(root, hours=12, now=now)
        row = data["dispatches"][0]
        self.assertEqual(row["prompt_chars"], 300)
        self.assertEqual(row["skill_listing_chars"], 900)
        self.assertEqual(row["deferred_tool_names"], 3)

    def test_attribution_names_the_residual_instead_of_hiding_it(self):
        parts = burn.floor_attribution(
            floor=100000,
            chars={"always_on_ruleset": 210000, "project_claude_md": 4700,
                   "agent_definition": 0, "skill_listing": 23000,
                   "dispatch_prompt": 4000},
            chars_per_token=3.0)
        named = parts["parts"]
        self.assertEqual(named["always_on_ruleset"], 70000)
        self.assertEqual(
            parts["residual"],
            100000 - sum(named.values()))
        self.assertIn("tool schema", parts["residual_is"].lower())

    def test_attribution_never_reports_a_negative_residual_silently(self):
        parts = burn.floor_attribution(
            floor=1000,
            chars={"always_on_ruleset": 210000}, chars_per_token=3.0)
        self.assertLess(parts["residual"], 0)
        self.assertTrue(parts["over_attributed"])


class TestRendering(unittest.TestCase):
    def _report(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1", [
                _line(now - datetime.timedelta(minutes=8), "req_1", cw=100000),
                _line(now - datetime.timedelta(minutes=4), "req_2",
                      cw=9000, cr=100000),
            ])
            return burn.scan_dispatches(root, hours=12, now=now)

    def test_render_states_the_window_distributions_and_straddlers(self):
        txt = burn.render_floor(self._report(), hours=12)
        low = txt.lower()
        self.assertIn("floor", low)
        self.assertIn("growth", low)
        self.assertIn("median", low)
        self.assertIn("p90", low)
        self.assertIn("straddl", low)

    def test_render_states_that_the_unit_is_relative_not_a_price(self):
        txt = burn.render_floor(self._report(), hours=12)
        self.assertIn("NOT a price", txt)
        self.assertNotIn("$", txt)


class TestCliSurface(unittest.TestCase):
    def _run(self, **kw):
        args = argparse.Namespace(hours=12, json=False, host=None,
                                  tickets=False, root=None, floor=False)
        for k, v in kw.items():
            setattr(args, k, v)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_delegation(args)
        return buf.getvalue()

    def test_floor_flag_prints_the_per_dispatch_report(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=100000),
                        _line(now, "req_2", cw=5000, cr=100000)])
            out = self._run(root=root, floor=True)
        self.assertIn("FLOOR", out.upper())
        self.assertIn("dispatch", out.lower())

    def test_without_the_flag_the_standing_130_meter_is_unchanged(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=100000)])
            out = self._run(root=root, floor=False)
        self.assertIn("MAIN vs SUBAGENT", out)
        self.assertNotIn("FLOOR", out.upper())

    def test_floor_json_is_machine_readable(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=100000)])
            out = self._run(root=root, floor=True, json=True)
        data = json.loads(out)
        self.assertEqual(data["dispatches"][0]["floor"], 100000)
        self.assertIn("turns", data["distributions"])

    def test_the_floor_flag_is_wired_through_the_real_parser(self):
        now = datetime.datetime.now(UTC)
        with TemporaryDirectory() as root:
            _write_sub(root, "proj", "s1", "agent-a1",
                       [_line(now, "req_1", cw=100000)])
            argv = ["airuleset", "delegation", "--floor", "--json",
                    "--root", root]
            buf = io.StringIO()
            old = sys.argv
            sys.argv = argv
            try:
                with contextlib.redirect_stdout(buf):
                    airuleset.main()
            finally:
                sys.argv = old
        self.assertEqual(json.loads(buf.getvalue())["dispatches"][0]["floor"],
                         100000)


if __name__ == "__main__":
    unittest.main()
