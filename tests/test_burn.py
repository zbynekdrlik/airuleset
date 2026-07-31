"""Token-spend attribution (`airuleset.py burn`) — ported from the scratch
analyzer that measured the 2026-07 burn behind the whole cost-fix package:
~$13,600 across all 6 managed boxes over 8 days, 76% Fable 5 (running as the
MAIN session model, not the advisor the rules mandate), 92% of that spend in
input context (cache read + cache write) vs 8% output. stdlib only, no
network — walks local `~/.claude/projects/*/*.jsonl` transcripts and prices
per-Mtok. `--host` collects a remote box over ssh by invoking that box's OWN
already-deployed `airuleset.py burn --json` (never scp).
"""
import argparse
import contextlib
import datetime
import io
import json
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import burn


def _line(model, i=0, cw=0, cr=0, o=0, ts=None, sidechain=False):
    return json.dumps({
        "timestamp": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "isSidechain": sidechain,
        "message": {"model": model, "usage": {
            "input_tokens": i, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": o}},
    })


def _write(root, project, session, lines):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    (d / (session + ".jsonl")).write_text("\n".join(lines) + "\n")


class TestTier(unittest.TestCase):
    def test_matches_known_models(self):
        self.assertEqual(burn.tier("claude-fable-5"), "fable")
        self.assertEqual(burn.tier("claude-opus-5[1m]"), "opus")
        self.assertEqual(burn.tier("claude-sonnet-5"), "sonnet")
        self.assertEqual(burn.tier("claude-haiku-4-5"), "haiku")
        self.assertEqual(burn.tier("gpt-4"), "other")
        self.assertEqual(burn.tier(None), "other")

    def test_price_table_matches_model_awareness_doc(self):
        # (input, cache_write, cache_read, output) per Mtok — Opus-5-era.
        self.assertEqual(burn.PRICE["opus"], (5.0, 6.25, 0.5, 25.0))
        self.assertEqual(burn.PRICE["fable"], (10.0, 12.5, 1.0, 50.0))
        self.assertEqual(burn.PRICE["sonnet"], (2.0, 2.5, 0.2, 10.0))
        self.assertEqual(burn.PRICE["haiku"], (1.0, 1.25, 0.1, 5.0))


class TestScan(unittest.TestCase):
    def test_aggregates_by_model_day_project_and_prices_correctly(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            _write(tmp, "proj-a", "s1", [
                _line("claude-opus-5", i=0, cw=0, cr=2000000, o=0,
                      ts=now.isoformat()),
                _line("claude-fable-5", i=0, cw=0, cr=500000, o=0,
                      ts=now.isoformat(), sidechain=True),
            ])
            report = burn.scan(tmp, days=7, now=now)
            self.assertEqual(report["files_scanned"], 1)
            self.assertEqual(report["usage_lines"], 2)
            self.assertIn("claude-opus-5", report["by_model"])
            self.assertIn("claude-fable-5", report["by_model"])
            # opus cache_read $0.5/Mtok * 2,000,000 = $1.00 exactly
            self.assertEqual(report["by_model"]["claude-opus-5"]["usd"], 1.0)
            # fable cache_read $1.0/Mtok * 500,000 = $0.50 exactly
            self.assertEqual(report["by_model"]["claude-fable-5"]["usd"], 0.5)
            self.assertIn("proj-a", report["by_project"])
            self.assertIn("main|opus", report["main_vs_sidechain"])
            self.assertIn("sidechain|fable", report["main_vs_sidechain"])

    def test_ignores_files_older_than_the_day_cutoff(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            _write(tmp, "proj-b", "s1", [
                json.dumps({"type": "system"}),
                _line("claude-sonnet-5", i=5, cr=10, o=5, ts=now.isoformat()),
            ])
            old = os.path.join(tmp, "proj-b", "s1.jsonl")
            old_ts = (now - datetime.timedelta(days=30)).timestamp()
            os.utime(old, (old_ts, old_ts))
            report = burn.scan(tmp, days=7, now=now)
            self.assertEqual(report["files_scanned"], 0)  # mtime past cutoff

    def test_ignores_lines_without_usage(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            _write(tmp, "proj-c", "s1", [
                json.dumps({"type": "system"}),
                json.dumps({"type": "user", "message": {"role": "user"}}),
                _line("claude-sonnet-5", i=5, cr=10, o=5, ts=now.isoformat()),
            ])
            report = burn.scan(tmp, days=7, now=now)
            self.assertEqual(report["usage_lines"], 1)

    def test_a_future_dated_line_is_dropped_like_scan_split_already_drops_it(self):
        # F10: scan()'s request loop only checked `t < cutoff`, never
        # `t > now` -- scan_split() (line ~1364) already drops future-dated
        # lines (a clock-skew / malformed-timestamp artifact). Bring scan()
        # to the same shape.
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            future_ts = (now + datetime.timedelta(days=1)).isoformat()
            _write(tmp, "proj-future", "s1", [
                _line("claude-sonnet-5", i=5, cr=10, o=5, ts=future_ts),
            ])
            report = burn.scan(tmp, days=7, now=now)
            self.assertEqual(report["usage_lines"], 0)
            self.assertEqual(report["by_model"], {})


class TestLocalReport(unittest.TestCase):
    def test_adds_host_and_user(self):
        with TemporaryDirectory() as tmp:
            report = burn.local_report(days=1, root=tmp)
            self.assertIn("host", report)
            self.assertIn("user", report)
            self.assertEqual(report["files_scanned"], 0)


class TestMergeReports(unittest.TestCase):
    ROW_A = {"in": 1, "cache_w": 0, "cache_r": 1, "out": 1, "usd": 1.5, "msgs": 3}
    ROW_B = {"in": 1, "cache_w": 0, "cache_r": 1, "out": 1, "usd": 0.5, "msgs": 2}

    def test_merges_two_hosts_into_by_host_and_by_project(self):
        r1 = {"host": "dev1", "files_scanned": 2, "usage_lines": 5,
              "by_model": {"claude-opus-5": dict(self.ROW_A)},
              "by_day": {"2026-07-20": dict(self.ROW_A)},
              "by_project": {"proj-a": dict(self.ROW_A)}}
        r2 = {"host": "dev2", "files_scanned": 1, "usage_lines": 2,
              "by_model": {"claude-opus-5": dict(self.ROW_B)},
              "by_day": {"2026-07-20": dict(self.ROW_B)},
              "by_project": {"proj-a": dict(self.ROW_B)}}
        combined = burn.merge_reports([r1, r2])
        self.assertEqual(combined["by_model"]["claude-opus-5"]["usd"], 2.0)
        self.assertEqual(combined["by_host"]["dev1"]["usd"], 1.5)
        self.assertEqual(combined["by_host"]["dev2"]["usd"], 0.5)
        self.assertIn("dev1:proj-a", combined["by_project"])
        self.assertIn("dev2:proj-a", combined["by_project"])
        self.assertEqual(combined["files_scanned"], 3)

    def test_single_report_still_produces_by_host(self):
        r1 = {"host": "dev1", "files_scanned": 1, "usage_lines": 1,
              "by_model": {"claude-sonnet-5": dict(self.ROW_A)},
              "by_day": {"2026-07-20": dict(self.ROW_A)},
              "by_project": {"proj-a": dict(self.ROW_A)}}
        combined = burn.merge_reports([r1])
        self.assertEqual(combined["by_host"]["dev1"]["usd"], 1.5)


class TestRenderHuman(unittest.TestCase):
    def test_includes_total_by_model_and_avg_context(self):
        combined = burn.merge_reports([{
            "host": "dev1", "files_scanned": 1, "usage_lines": 10,
            "by_model": {"claude-opus-5": {"in": 0, "cache_w": 0,
                                            "cache_r": 1000000, "out": 0,
                                            "usd": 5.0, "msgs": 10}},
            "by_day": {"2026-07-20": {"in": 0, "cache_w": 0,
                                       "cache_r": 1000000, "out": 0,
                                       "usd": 5.0, "msgs": 10}},
            "by_project": {"proj-a": {"in": 0, "cache_w": 0,
                                       "cache_r": 1000000, "out": 0,
                                       "usd": 5.0, "msgs": 10}},
        }])
        out = burn.render_human(combined, days=7)
        self.assertIn("$5.00", out)
        self.assertIn("claude-opus-5", out)
        self.assertIn("avg context/msg", out)
        self.assertIn("100,000", out)   # 1,000,000 cache tokens / 10 msgs

    def test_by_host_section_only_shown_for_multiple_hosts(self):
        one_host = burn.merge_reports([{
            "host": "dev1", "files_scanned": 0, "usage_lines": 0,
            "by_model": {}, "by_day": {}, "by_project": {}}])
        self.assertNotIn("by host:", burn.render_human(one_host, days=7))
        two_hosts = burn.merge_reports([
            {"host": "dev1", "files_scanned": 0, "usage_lines": 0,
             "by_model": {"m": {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0,
                                 "usd": 1.0, "msgs": 1}},
             "by_day": {}, "by_project": {}},
            {"host": "dev2", "files_scanned": 0, "usage_lines": 0,
             "by_model": {"m": {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0,
                                 "usd": 1.0, "msgs": 1}},
             "by_day": {}, "by_project": {}},
        ])
        self.assertIn("by host:", burn.render_human(two_hosts, days=7))


class TestCmdBurnRegistration(unittest.TestCase):
    def test_registered_in_subcommands(self):
        self.assertIn("burn", airuleset.SUBCOMMANDS)
        self.assertTrue(callable(airuleset.SUBCOMMANDS["burn"]))

    def test_remote_cmd_uses_identity_when_present(self):
        remote = {"name": "gatekeeper", "host": "1.2.3.4", "user": "gatekeeper",
                  "repo_path": "~/devel/airuleset",
                  "identity": "~/.secrets/gatekeeper_access_ed25519"}
        cmd = airuleset._burn_remote_cmd(remote, days=7)
        self.assertIn("-i", cmd)
        self.assertNotIn("sshpass", cmd)
        self.assertIn("gatekeeper@1.2.3.4", cmd)
        self.assertIn("airuleset.py burn --json --days 7", " ".join(cmd))

    def test_remote_cmd_uses_sshpass_without_identity(self):
        remote = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                  "repo_path": "~/devel/airuleset"}
        cmd = airuleset._burn_remote_cmd(remote, days=3)
        self.assertIn("sshpass", cmd)
        self.assertIn("newlevel@5.6.7.8", cmd)
        self.assertIn("airuleset.py burn --json --days 3", " ".join(cmd))

    def test_unknown_host_name_exits_nonzero(self):
        with self.assertRaises(SystemExit) as ctx:
            airuleset.cmd_burn(argparse.Namespace(days=7, json=True,
                                                   host="not-a-real-box"))
        self.assertNotEqual(ctx.exception.code, 0)


class TestCmdBurnEndToEnd(unittest.TestCase):
    def _with_home(self, fn):
        with TemporaryDirectory() as home:
            projects = Path(home) / ".claude" / "projects"
            _write(str(projects), "proj-x", "s1", [
                _line("claude-sonnet-5", i=10, cr=1000, o=20)])
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                return fn()
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_prints_human_table_by_default(self):
        def run():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(days=7, json=False, host=None))
            return buf.getvalue()
        out = self._with_home(run)
        self.assertIn("total:", out)
        self.assertIn("claude-sonnet-5", out)

    def test_json_flag_prints_valid_json(self):
        def run():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(days=7, json=True, host=None))
            return buf.getvalue()
        out = self._with_home(run)
        data = json.loads(out)
        self.assertIn("by_model", data)
        self.assertIn("claude-sonnet-5", data["by_model"])


# --------------------------------------------------------------------------- #
# #37 follow-up: hourly burn snapshots + `burn --compare` — the AUTOMATIC
# before/after feedback loop the user asked for ("meraj hodinovo a povedz mi
# ci sa to zlepsilo alebo zhorsilo, sam"). scan() gains an HOUR-granularity
# bucket (reusing its existing per-line parser — no second parser),
# hourly_snapshot() picks one hour's row out of it, and compare_changes()/
# render_compare() are the arithmetic + Slovak report behind --compare.
# --------------------------------------------------------------------------- #


class TestScanHourlyBuckets(unittest.TestCase):
    def test_by_hour_and_by_hour_model_buckets(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            hour_start = now.astimezone().replace(minute=0, second=0, microsecond=0)
            _write(tmp, "proj-h", "s1", [
                _line("claude-opus-5", cr=1000000, ts=hour_start.isoformat()),
            ])
            report = burn.scan(tmp, days=1, now=now)
            hour_key = hour_start.strftime("%Y-%m-%dT%H:00")
            self.assertIn(hour_key, report["by_hour"])
            self.assertEqual(report["by_hour"][hour_key]["usd"], 0.5)
            self.assertIn(hour_key + "|claude-opus-5", report["by_hour_model"])
            self.assertEqual(
                report["by_hour_model"][hour_key + "|claude-opus-5"]["usd"], 0.5)

    def test_separate_hours_do_not_bleed_into_each_other(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            h1 = now.astimezone().replace(minute=0, second=0, microsecond=0)
            h0 = h1 - datetime.timedelta(hours=1)
            _write(tmp, "proj-h2", "s1", [
                _line("claude-opus-5", cr=1000000, ts=h0.isoformat()),
                _line("claude-opus-5", cr=2000000, ts=h1.isoformat()),
            ])
            report = burn.scan(tmp, days=1, now=now)
            k0 = h0.strftime("%Y-%m-%dT%H:00")
            k1 = h1.strftime("%Y-%m-%dT%H:00")
            self.assertEqual(report["by_hour"][k0]["usd"], 0.5)
            self.assertEqual(report["by_hour"][k1]["usd"], 1.0)


class TestHourlySnapshot(unittest.TestCase):
    def test_covers_previous_full_hour_only(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 25, 14, 5, 0, tzinfo=datetime.timezone.utc)
            prev_hour_start = (now - datetime.timedelta(hours=1)).astimezone().replace(
                minute=0, second=0, microsecond=0)
            cur_hour_start = now.astimezone().replace(minute=0, second=0, microsecond=0)
            _write(tmp, "proj-s", "s1", [
                _line("claude-opus-5", cr=1000000, ts=prev_hour_start.isoformat()),
                _line("claude-sonnet-5", cr=1000000, ts=cur_hour_start.isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="dev1", user="zbynek")
            self.assertEqual(row["host"], "dev1")
            self.assertEqual(row["user"], "zbynek")
            self.assertEqual(row["window_h"], 1)
            self.assertEqual(row["usd"], 0.5)
            self.assertEqual(row["msgs"], 1)
            self.assertIn("claude-opus-5", row["by_model"])
            self.assertNotIn("claude-sonnet-5", row["by_model"])

    def test_avg_ctx_is_cache_tokens_per_message(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
            prev_hour_start = (now - datetime.timedelta(hours=1)).astimezone().replace(
                minute=0, second=0, microsecond=0)
            _write(tmp, "proj-s2", "s1", [
                _line("claude-opus-5", cw=200000, cr=300000,
                      ts=prev_hour_start.isoformat()),
                _line("claude-opus-5", cw=0, cr=500000,
                      ts=(prev_hour_start + datetime.timedelta(minutes=5)).isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="dev1", user="z")
            self.assertEqual(row["msgs"], 2)
            self.assertEqual(row["avg_ctx"], 500000)

    def test_empty_hour_yields_zeroed_row(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            row = burn.hourly_snapshot(now, root=tmp, host="dev1", user="z")
            self.assertEqual(row["usd"], 0.0)
            self.assertEqual(row["msgs"], 0)
            self.assertEqual(row["avg_ctx"], 0)
            self.assertEqual(row["by_model"], {})

    def test_row_carries_the_agents_scope_tag(self):
        # #149: every row is stamped "scope": "agents" — the post-subagent-
        # reconciliation marker _window_stats()/compare_changes() and
        # hourly_burn_alert() use to keep pre- and post-#149 history from
        # ever being compared as one continuous series.
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            row = burn.hourly_snapshot(now, root=tmp, host="dev1", user="z")
            self.assertEqual(row["scope"], "agents")


class TestDispatchRatioMeasurement80(unittest.TestCase):
    """#80: the acceptance metric ("main Bash : Agent under 5:1, measured
    from the transcript, not from an impression") was hand-derived with a
    throwaway script. The hourly snapshot already walks every assistant
    entry and already knows `isSidechain` — counting the MAIN agent's
    `Bash` vs `Agent`/`Task` tool_use blocks in the same pass makes the
    ratio readable straight out of snapshots.jsonl / fleet.jsonl, with no
    second transcript parser."""

    def _tool_line(self, model, tools, ts=None, sidechain=False):
        content = [{"type": "tool_use", "id": "t%d" % i, "name": n, "input": {}}
                   for i, n in enumerate(tools)]
        return json.dumps({
            "timestamp": ts or datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "isSidechain": sidechain,
            "type": "assistant",
            "message": {"model": model, "content": content, "usage": {
                "input_tokens": 0, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1000, "output_tokens": 0}},
        })

    def _prev_hour(self, now):
        return (now - datetime.timedelta(hours=1)).astimezone().replace(
            minute=0, second=0, microsecond=0)

    def test_snapshot_counts_main_bash_and_dispatches(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 26, 15, 5, 0,
                                    tzinfo=datetime.timezone.utc)
            h = self._prev_hour(now)
            _write(tmp, "proj-r", "s1", [
                self._tool_line("claude-opus-5", ["Bash"], ts=h.isoformat()),
                self._tool_line("claude-opus-5", ["Bash"], ts=h.isoformat()),
                self._tool_line("claude-opus-5", ["Bash"], ts=h.isoformat()),
                self._tool_line("claude-opus-5", ["Agent"], ts=h.isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="gk", user="g")
            self.assertEqual(row["main_bash"], 3)
            self.assertEqual(row["main_agent"], 1)

    def test_sidechain_tool_calls_are_not_main_agent_calls(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 26, 15, 5, 0,
                                    tzinfo=datetime.timezone.utc)
            h = self._prev_hour(now)
            _write(tmp, "proj-r2", "s1", [
                self._tool_line("claude-sonnet-5", ["Bash", "Bash"],
                                ts=h.isoformat(), sidechain=True),
                self._tool_line("claude-opus-5", ["Bash"], ts=h.isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="gk", user="g")
            self.assertEqual(row["main_bash"], 1,
                             "a worker's own Bash is the wanted shape")

    def test_task_counts_as_a_dispatch_too(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 26, 15, 5, 0,
                                    tzinfo=datetime.timezone.utc)
            h = self._prev_hour(now)
            _write(tmp, "proj-r3", "s1", [
                self._tool_line("claude-opus-5", ["Task"], ts=h.isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="gk", user="g")
            self.assertEqual(row["main_agent"], 1)

    def test_other_hours_do_not_bleed_in(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime(2026, 7, 26, 15, 5, 0,
                                    tzinfo=datetime.timezone.utc)
            h = self._prev_hour(now)
            cur = now.astimezone().replace(minute=0, second=0, microsecond=0)
            _write(tmp, "proj-r4", "s1", [
                self._tool_line("claude-opus-5", ["Bash"], ts=h.isoformat()),
                self._tool_line("claude-opus-5", ["Bash", "Bash"],
                                ts=cur.isoformat()),
            ])
            row = burn.hourly_snapshot(now, root=tmp, host="gk", user="g")
            self.assertEqual(row["main_bash"], 1)

    def test_empty_hour_reports_zeroes(self):
        with TemporaryDirectory() as tmp:
            now = datetime.datetime.now(datetime.timezone.utc)
            row = burn.hourly_snapshot(now, root=tmp, host="gk", user="g")
            self.assertEqual(row["main_bash"], 0)
            self.assertEqual(row["main_agent"], 0)

    def test_fleet_row_sums_the_ratio_inputs(self):
        row = burn.merge_fleet_row("2026-07-26T15:00:00+00:00", {
            "gk": {"usd": 30.0, "msgs": 176, "avg_ctx": 256000,
                   "main_bash": 91, "main_agent": 2},
            "dev1": {"usd": 3.0, "msgs": 20, "avg_ctx": 100000,
                     "main_bash": 9, "main_agent": 3},
        })
        self.assertEqual(row["total_main_bash"], 100)
        self.assertEqual(row["total_main_agent"], 5)
        self.assertEqual(row["per_host"]["gk"]["main_bash"], 91)
        self.assertEqual(row["per_host"]["gk"]["main_agent"], 2)

    def test_fleet_row_tolerates_hosts_without_the_new_fields(self):
        # an older box that has not been pushed yet sends a row with no
        # main_bash/main_agent — it must count as zero, never crash.
        row = burn.merge_fleet_row("t", {
            "old": {"usd": 1.0, "msgs": 5, "avg_ctx": 1000},
            "err": {"error": "ssh timeout"},
        })
        self.assertEqual(row["total_main_bash"], 0)
        self.assertEqual(row["total_main_agent"], 0)


class TestChangesAndSnapshotsIO(unittest.TestCase):
    def test_mark_change_appends_json_line(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "changes.jsonl"
            burn.mark_change(
                "krok 1: test", path=path, host="dev1",
                now=datetime.datetime(2026, 7, 25, 10, 0, tzinfo=datetime.timezone.utc))
            rows = burn.load_changes(path=path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["text"], "krok 1: test")
            self.assertEqual(rows[0]["host"], "dev1")
            self.assertEqual(rows[0]["ts"], "2026-07-25T10:00:00+00:00")

    def test_mark_change_appends_not_overwrites(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "changes.jsonl"
            burn.mark_change("a", path=path)
            burn.mark_change("b", path=path)
            rows = burn.load_changes(path=path)
            self.assertEqual([r["text"] for r in rows], ["a", "b"])

    def test_load_snapshots_skips_malformed_lines(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.jsonl"
            path.write_text('{"ts": "x"}\nNOT JSON\n{"ts": "y"}\n')
            rows = burn.load_snapshots(path=path)
            self.assertEqual(len(rows), 2)

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(burn.load_snapshots(path="/nonexistent/path.jsonl"), [])
        self.assertEqual(burn.load_changes(path="/nonexistent/path.jsonl"), [])


class TestCompareChanges(unittest.TestCase):
    # #149: every fixture row defaults to "scope": "agents" (the shape a
    # real post-#149 hourly_snapshot() row now carries), since
    # _window_stats() ignores any row that lacks it — pass scope=None to
    # build a pre-#149-shaped row for the tests that specifically exercise
    # that exclusion.
    def _snap(self, ts, usd, avg_ctx, msgs, scope="agents"):
        row = {"ts": ts, "host": "dev1", "user": "z", "window_h": 1,
               "usd": usd, "msgs": msgs, "avg_ctx": avg_ctx, "by_model": {}}
        if scope is not None:
            row["scope"] = scope
        return row

    def test_computes_before_after_means_and_deltas(self):
        change_ts = datetime.datetime(2026, 7, 25, 12, 0, tzinfo=datetime.timezone.utc)
        snaps = [
            self._snap("2026-07-25T10:00:00+00:00", 1.0, 200000, 10),
            self._snap("2026-07-25T11:00:00+00:00", 1.0, 200000, 10),
            self._snap("2026-07-25T12:00:00+00:00", 0.5, 100000, 12),
            self._snap("2026-07-25T13:00:00+00:00", 0.5, 100000, 12),
        ]
        changes = [{"ts": change_ts.isoformat(), "host": "dev1", "text": "krok 1"}]
        results = burn.compare_changes(snaps, changes, window_hours=2)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["before"]["n"], 2)
        self.assertEqual(r["before"]["usd_h"], 1.0)
        self.assertEqual(r["after"]["n"], 2)
        self.assertEqual(r["after"]["usd_h"], 0.5)
        self.assertEqual(r["after"]["avg_ctx"], 100000)

    def test_no_data_in_window_reports_zero_n(self):
        change_ts = datetime.datetime(2026, 7, 25, 12, 0, tzinfo=datetime.timezone.utc)
        changes = [{"ts": change_ts.isoformat(), "host": "dev1", "text": "krok X"}]
        results = burn.compare_changes([], changes, window_hours=6)
        self.assertEqual(results[0]["before"]["n"], 0)
        self.assertEqual(results[0]["after"]["n"], 0)

    def test_changes_sorted_chronologically(self):
        c1 = {"ts": "2026-07-25T12:00:00+00:00", "host": "dev1", "text": "second"}
        c2 = {"ts": "2026-07-25T08:00:00+00:00", "host": "dev1", "text": "first"}
        results = burn.compare_changes([], [c1, c2], window_hours=1)
        self.assertEqual([r["text"] for r in results], ["first", "second"])

    def test_rows_without_the_scope_tag_are_excluded_not_mixed_in(self):
        # #149: a snapshot row from before the scope tag existed (the
        # main-only era) must never silently participate in a comparison —
        # a window containing ONLY such rows reports the SAME empty shape
        # (n=0) as a window with no rows at all, never a false "before" or
        # "after" figure built from pre-#149 data.
        change_ts = datetime.datetime(2026, 7, 25, 12, 0, tzinfo=datetime.timezone.utc)
        snaps = [
            self._snap("2026-07-25T10:00:00+00:00", 1.0, 200000, 10, scope=None),
            self._snap("2026-07-25T13:00:00+00:00", 0.5, 100000, 12, scope=None),
        ]
        changes = [{"ts": change_ts.isoformat(), "host": "dev1", "text": "krok 1"}]
        results = burn.compare_changes(snaps, changes, window_hours=2)
        self.assertEqual(results[0]["before"]["n"], 0)
        self.assertEqual(results[0]["after"]["n"], 0)

    def test_bad_ts_change_is_skipped_not_crashed(self):
        results = burn.compare_changes([], [{"ts": "not-a-date", "text": "bad"}],
                                       window_hours=1)
        self.assertEqual(results, [])


class TestRenderCompare(unittest.TestCase):
    def test_no_changes_renders_hint(self):
        out = burn.render_compare([], window_hours=6)
        self.assertIn("--mark", out)

    def test_includes_change_text_and_direction(self):
        results = [{
            "ts": "2026-07-25T12:00:00+00:00", "host": "dev1",
            "text": "krok 1: opus default",
            "before": {"n": 3, "usd_h": 1.0, "avg_ctx": 200000, "msgs_h": 10},
            "after": {"n": 3, "usd_h": 0.4, "avg_ctx": 90000, "msgs_h": 11},
        }]
        out = burn.render_compare(results, window_hours=6)
        self.assertIn("krok 1: opus default", out)
        self.assertIn("lepšie", out)

    def test_worse_direction_is_labeled(self):
        results = [{
            "ts": "2026-07-25T12:00:00+00:00", "host": "dev1", "text": "krok X",
            "before": {"n": 1, "usd_h": 0.4, "avg_ctx": 90000, "msgs_h": 11},
            "after": {"n": 1, "usd_h": 1.0, "avg_ctx": 200000, "msgs_h": 10},
        }]
        out = burn.render_compare(results, window_hours=6)
        self.assertIn("horšie", out)

    def test_missing_window_data_does_not_crash(self):
        results = [{
            "ts": "2026-07-25T12:00:00+00:00", "host": "dev1", "text": "krok Y",
            "before": {"n": 0, "usd_h": None, "avg_ctx": None, "msgs_h": None},
            "after": {"n": 0, "usd_h": None, "avg_ctx": None, "msgs_h": None},
        }]
        out = burn.render_compare(results, window_hours=6)
        self.assertIn("krok Y", out)


class TestCmdBurnMarkAndCompare(unittest.TestCase):
    def _with_home(self, fn):
        with TemporaryDirectory() as home:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                return fn()
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_mark_writes_to_changes_jsonl_under_home(self):
        def run():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark="krok 1 test",
                    compare=False, window=None, mark_ts=None))
            path = Path(os.environ["HOME"]) / ".claude" / "burn-history" / "changes.jsonl"
            rows = burn.load_changes(path=path)
            return buf.getvalue(), rows
        out, rows = self._with_home(run)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "krok 1 test")
        self.assertIn("krok 1 test", out)

    def test_mark_ts_backdates_the_change(self):
        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark="krok 1b",
                    compare=False, window=None,
                    mark_ts="2026-07-25T13:15:14+02:00"))
            path = Path(os.environ["HOME"]) / ".claude" / "burn-history" / "changes.jsonl"
            return burn.load_changes(path=path)
        rows = self._with_home(run)
        self.assertEqual(rows[0]["ts"], "2026-07-25T13:15:14+02:00")

    def test_compare_with_no_data_prints_hint(self):
        def run():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark=None, compare=True,
                    window=6, mark_ts=None))
            return buf.getvalue()
        out = self._with_home(run)
        self.assertIn("--mark", out)


# --------------------------------------------------------------------------- #
# #55 — FLEET monitoring: fleet.jsonl merge, weekly-budget sustainability,
# trend, the budget-exceeded alert trigger, `burn --fleet`, and the
# `--compare` fleet extension.
# --------------------------------------------------------------------------- #

def _host_row(usd, msgs, avg_ctx, by_model=None):
    return {"usd": usd, "msgs": msgs, "avg_ctx": avg_ctx, "by_model": by_model or {}}


class TestFleetMerge(unittest.TestCase):
    def test_totals_and_weighted_avg_ctx(self):
        row = burn.merge_fleet_row("2026-07-25T17:00:00+00:00", {
            "dev1": _host_row(1.0, 10, 100000),
            "dev2": _host_row(3.0, 30, 300000),
        })
        self.assertEqual(row["total_usd"], 4.0)
        self.assertEqual(row["total_msgs"], 40)
        # weighted: (100000*10 + 300000*30) / 40 = 250000
        self.assertEqual(row["weighted_avg_ctx"], 250000)
        self.assertEqual(row["per_host"]["dev1"]["usd"], 1.0)

    def test_missing_host_becomes_error_and_is_excluded_from_totals(self):
        row = burn.merge_fleet_row("2026-07-25T17:00:00+00:00", {
            "dev1": _host_row(1.0, 10, 100000),
            "gatekeeper": {"error": "ssh timeout"},
            "david@subdev": None,
        })
        self.assertEqual(row["per_host"]["gatekeeper"], {"error": "ssh timeout"})
        self.assertEqual(row["per_host"]["david@subdev"], {"error": "no data"})
        self.assertEqual(row["total_usd"], 1.0)
        self.assertEqual(row["total_msgs"], 10)

    def test_all_hosts_failing_yields_zeroed_totals_not_a_crash(self):
        row = burn.merge_fleet_row("t", {"a": {"error": "x"}, "b": {"error": "y"}})
        self.assertEqual(row["total_usd"], 0.0)
        self.assertEqual(row["total_msgs"], 0)
        self.assertEqual(row["weighted_avg_ctx"], 0)

    def test_non_dict_row_is_treated_as_a_malformed_error_not_a_crash(self):
        row = burn.merge_fleet_row("t", {"weird": 42, "also-weird": ["x"]})
        self.assertEqual(row["per_host"]["weird"], {"error": "malformed row"})
        self.assertEqual(row["per_host"]["also-weird"], {"error": "malformed row"})
        self.assertEqual(row["total_usd"], 0.0)

    def test_carries_weekly_pct_and_resets_at_when_given(self):
        row = burn.merge_fleet_row("t", {}, weekly_pct=42, resets_at="2026-08-01T00:00:00+00:00")
        self.assertEqual(row["weekly_pct"], 42)
        self.assertEqual(row["resets_at"], "2026-08-01T00:00:00+00:00")

    def test_no_weekly_pct_key_when_not_given(self):
        row = burn.merge_fleet_row("t", {})
        self.assertNotIn("weekly_pct", row)
        self.assertNotIn("resets_at", row)

    def test_stale_flag_survives_the_merge_into_per_host(self):
        # #60 — a host whose _fleet_remote_row detected a wrong-hour tail
        # line comes in as {"error": ..., "stale": True}; merge must NOT
        # drop the "stale" marker (it's how render_fleet tells "no sample
        # yet" apart from a hard ssh/JSON failure).
        row = burn.merge_fleet_row("t", {
            "dev1": _host_row(1.0, 10, 100000),
            "gk": {"error": "no sample for hour 123 (latest 2026-07-25T16:00:00+00:00)",
                  "stale": True},
        })
        self.assertEqual(row["per_host"]["gk"],
                         {"error": "no sample for hour 123 (latest 2026-07-25T16:00:00+00:00)",
                          "stale": True})
        self.assertEqual(row["total_usd"], 1.0)

    def test_non_stale_error_does_not_gain_a_stale_key(self):
        row = burn.merge_fleet_row("t", {"gk": {"error": "ssh timeout"}})
        self.assertEqual(row["per_host"]["gk"], {"error": "ssh timeout"})
        self.assertNotIn("stale", row["per_host"]["gk"])

    def test_scope_agents_is_carried_through_when_a_host_row_has_it(self):
        # #149/F6: a SINGLE contributing host row tagged "scope": "agents"
        # makes the MERGED fleet row carry it too -- unanimous is trivially
        # satisfied when there is only one contributor.
        row = dict(_host_row(1.0, 10, 100000))
        row["scope"] = "agents"
        merged = burn.merge_fleet_row("t", {"dev1": row})
        self.assertEqual(merged.get("scope"), "agents")

    def test_scope_agents_requires_every_contributing_host_to_carry_it(self):
        # F6: merge_fleet_row() used to tag scope:"agents" if ANY host row
        # carried it -- a mixed old+new fleet hour (one host already
        # reconciled, one not) got tagged anyway, poisoning the REL/weekly-
        # step baseline with a hour that still includes old-scope spend.
        # Requiring UNANIMITY across every contributing (non-error) host is
        # the fix: two hosts, both tagged, DOES tag the merged row.
        row1 = dict(_host_row(1.0, 10, 100000))
        row1["scope"] = "agents"
        row2 = dict(_host_row(2.0, 20, 100000))
        row2["scope"] = "agents"
        merged = burn.merge_fleet_row("t", {"dev1": row1, "dev2": row2})
        self.assertEqual(merged.get("scope"), "agents")

    def test_mixed_scope_fleet_is_not_tagged(self):
        # F6 negative case: dev1 already reconciled (scope="agents"), dev2
        # not yet pushed (no scope key at all) -- the merged row must NOT
        # be tagged, because it is not genuinely a post-#149 hour for every
        # contributor.
        row1 = dict(_host_row(1.0, 10, 100000))
        row1["scope"] = "agents"
        row2 = _host_row(2.0, 20, 100000)   # no scope key -- old host
        merged = burn.merge_fleet_row("t", {"dev1": row1, "dev2": row2})
        self.assertNotIn("scope", merged)

    def test_no_scope_key_when_no_host_row_has_it(self):
        # every existing host row shape (pre-#149, or an old box not yet
        # pushed) — the merged row must not gain a scope it never earned.
        merged = burn.merge_fleet_row("t", {"dev1": _host_row(1.0, 10, 100000)})
        self.assertNotIn("scope", merged)

    def test_error_only_hosts_never_gain_a_scope_tag(self):
        # F6: a fleet cycle where every host errored has ZERO contributors --
        # "unanimous" over an empty set must not be misread as "tagged".
        merged = burn.merge_fleet_row("t", {"gk": {"error": "ssh timeout"}})
        self.assertNotIn("scope", merged)


class TestFleetPathAndIO(unittest.TestCase):
    def test_load_fleet_missing_file_is_empty(self):
        self.assertEqual(burn.load_fleet(path="/nonexistent/fleet.jsonl"), [])

    def test_fleet_path_under_burn_history_dir(self):
        self.assertEqual(burn.fleet_path(), burn.burn_history_dir() / "fleet.jsonl")

    def test_usage_cache_path_under_dot_claude(self):
        self.assertEqual(burn.usage_cache_path(),
                         Path.home() / ".claude" / "airuleset-usage-cache.json")

    def test_load_usage_cache_missing_file_is_none(self):
        self.assertIsNone(burn.load_usage_cache(path="/nonexistent/cache.json"))


class TestSharedWeeklyWindow(unittest.TestCase):
    def test_picks_the_model_less_weekly_window(self):
        cache = {"windows": [
            {"group": "weekly", "percent": 15, "model": None, "resets_at": "2026-08-01T00:00:00+00:00"},
            {"group": "weekly", "percent": 25, "model": "Fable", "resets_at": "2026-08-01T00:00:00+00:00"},
            {"group": "session", "percent": 90, "model": None, "resets_at": "x"},
        ]}
        self.assertEqual(burn.shared_weekly_window(cache), (15, "2026-08-01T00:00:00+00:00"))

    def test_no_weekly_window_is_none(self):
        self.assertIsNone(burn.shared_weekly_window({"windows": []}))
        self.assertIsNone(burn.shared_weekly_window(None))


class TestWeeklyBudget(unittest.TestCase):
    def test_budget_pct_per_day_spread_over_remaining_days(self):
        now = datetime.datetime(2026, 7, 25, 0, 0, tzinfo=datetime.timezone.utc)
        cache = {"windows": [{"group": "weekly", "percent": 30, "model": None,
                              "resets_at": "2026-07-29T00:00:00+00:00"}]}
        b = burn.weekly_budget(cache, now=now)
        self.assertEqual(b["weekly_pct"], 30)
        self.assertEqual(b["remaining_days"], 4.0)
        self.assertEqual(b["budget_pct_per_day"], 17.5)   # (100-30)/4

    def test_no_cache_is_none(self):
        self.assertIsNone(burn.weekly_budget(None))

    def test_unparsable_resets_at_still_returns_percent(self):
        cache = {"windows": [{"group": "weekly", "percent": 10, "model": None,
                              "resets_at": "not-a-date"}]}
        b = burn.weekly_budget(cache)
        self.assertEqual(b["weekly_pct"], 10)
        self.assertIsNone(b["remaining_days"])
        self.assertIsNone(b["budget_pct_per_day"])


class TestFleetTrend(unittest.TestCase):
    def _row(self, ts, total_usd, per_host):
        return {"ts": ts, "total_usd": total_usd, "per_host": per_host}

    def test_needs_at_least_two_rows(self):
        self.assertIsNone(burn.fleet_trend([]))
        self.assertIsNone(burn.fleet_trend([self._row("t0", 1.0, {})]))

    def test_latest_vs_mean_of_previous(self):
        rows = [
            self._row("t0", 2.0, {"dev1": _host_row(2.0, 5, 1000)}),
            self._row("t1", 4.0, {"dev1": _host_row(4.0, 5, 1000)}),
            self._row("t2", 1.0, {"dev1": _host_row(1.0, 5, 1000)}),
        ]
        trend = burn.fleet_trend(rows, n_prev=3)
        self.assertEqual(trend["total"]["prev_mean"], 3.0)
        self.assertEqual(trend["total"]["latest"], 1.0)
        self.assertIn("lepšie", trend["total"]["delta"])
        self.assertEqual(trend["by_host"]["dev1"]["prev_mean"], 3.0)
        self.assertEqual(trend["by_host"]["dev1"]["latest"], 1.0)

    def test_a_host_with_an_error_row_is_excluded_from_its_own_trend(self):
        rows = [
            self._row("t0", 1.0, {"dev1": _host_row(1.0, 1, 100)}),
            self._row("t1", 1.0, {"dev1": {"error": "down"}}),
        ]
        trend = burn.fleet_trend(rows)
        self.assertIsNone(trend["by_host"]["dev1"]["latest"])
        # #60 — the host set changed (dev1 valid -> dev1 error), so the
        # TOTAL must refuse to compare, even though by_host still degrades
        # gracefully per-host.
        self.assertFalse(trend["total"]["comparable"])

    def test_total_comparable_when_host_set_matches_across_hours(self):
        rows = [
            self._row("t0", 5.0, {"dev1": _host_row(2.0, 5, 1000), "dev2": _host_row(3.0, 5, 1000)}),
            self._row("t1", 4.0, {"dev1": _host_row(1.0, 5, 1000), "dev2": _host_row(3.0, 5, 1000)}),
        ]
        trend = burn.fleet_trend(rows)
        self.assertTrue(trend["total"]["comparable"])
        self.assertEqual(trend["total"]["prev_mean"], 5.0)
        self.assertEqual(trend["total"]["latest"], 4.0)

    def test_total_not_comparable_when_latest_host_set_differs_from_all_prev(self):
        # #60 core regression scenario: 2 prior hours both had dev1+dev2
        # valid; the latest hour has dev2 gone stale -- the total must NOT
        # be compared against those 2 hours (that produced the live false
        # "-39.8% (lepšie)" trend).
        rows = [
            self._row("t0", 5.0, {"dev1": _host_row(2.0, 5, 1000), "dev2": _host_row(3.0, 5, 1000)}),
            self._row("t1", 5.0, {"dev1": _host_row(2.0, 5, 1000), "dev2": _host_row(3.0, 5, 1000)}),
            self._row("t2", 2.0, {"dev1": _host_row(2.0, 5, 1000),
                                  "dev2": {"error": "no sample for hour X", "stale": True}}),
        ]
        trend = burn.fleet_trend(rows, n_prev=3)
        self.assertFalse(trend["total"]["comparable"])
        self.assertIn("neporovnat", trend["total"]["reason"])
        self.assertIsNone(trend["total"]["prev_mean"])
        # by_host for dev1 (present + valid every hour) still works normally
        self.assertEqual(trend["by_host"]["dev1"]["latest"], 2.0)

    def test_total_not_comparable_across_a_scope_boundary(self):
        # F2: fleet_trend() compared total_usd across the #149 scope
        # boundary -- a tagged latest hour against an untagged prev hour
        # (SAME host set) live printed a false "-21.5% (lepšie)". comparable_
        # prev must additionally require the SAME scope as latest, not just
        # the same host set.
        prev = self._row("t0", 5.0, {"dev1": _host_row(5.0, 5, 1000)})
        # prev carries no "scope" key at all -- pre-#149 shaped.
        latest = self._row("t1", 100.0, {"dev1": _host_row(100.0, 5, 1000)})
        latest["scope"] = "agents"
        trend = burn.fleet_trend([prev, latest])
        self.assertFalse(trend["total"]["comparable"])
        self.assertIsNone(trend["total"]["prev_mean"])

    def test_total_comparable_when_scope_matches_across_hours(self):
        # F2, positive case: SAME scope on both sides (same host set too)
        # still compares normally -- the fix narrows the comparison, it
        # doesn't disable it.
        prev = self._row("t0", 5.0, {"dev1": _host_row(5.0, 5, 1000)})
        prev["scope"] = "agents"
        latest = self._row("t1", 100.0, {"dev1": _host_row(100.0, 5, 1000)})
        latest["scope"] = "agents"
        trend = burn.fleet_trend([prev, latest])
        self.assertTrue(trend["total"]["comparable"])
        self.assertEqual(trend["total"]["prev_mean"], 5.0)
        self.assertEqual(trend["total"]["latest"], 100.0)
        self.assertEqual(trend["by_host"]["dev1"]["prev_mean"], 5.0)


class TestObservedPctPerDay(unittest.TestCase):
    def test_needs_two_samples(self):
        self.assertIsNone(burn.observed_pct_per_day([]))
        self.assertIsNone(burn.observed_pct_per_day([{"ts": "2026-07-25T10:00:00+00:00", "weekly_pct": 10}]))

    def test_rate_from_oldest_and_newest_sample(self):
        rows = [
            {"ts": "2026-07-25T10:00:00+00:00", "weekly_pct": 10},
            {"ts": "2026-07-25T22:00:00+00:00", "weekly_pct": 22},  # 12h later, +12pct
        ]
        # 12 pct over 12h -> 24 pct/day
        self.assertEqual(burn.observed_pct_per_day(rows), 24.0)

    def test_a_sample_with_unparsable_ts_is_skipped_not_a_crash(self):
        rows = [
            {"ts": "not-a-date", "weekly_pct": 5},
            {"ts": "2026-07-25T10:00:00+00:00", "weekly_pct": 10},
            {"ts": "2026-07-25T22:00:00+00:00", "weekly_pct": 22},
        ]
        self.assertEqual(burn.observed_pct_per_day(rows), 24.0)

    def test_rows_without_weekly_pct_are_ignored(self):
        rows = [{"ts": "2026-07-25T10:00:00+00:00"}, {"ts": "2026-07-25T11:00:00+00:00"}]
        self.assertIsNone(burn.observed_pct_per_day(rows))


class TestFleetSustainability(unittest.TestCase):
    def test_no_cache_reports_missing_cache(self):
        s = burn.fleet_sustainability([], None)
        self.assertIsNone(s["budget"])
        self.assertIn("cache", s["verdict"])

    def test_not_enough_samples_reports_that(self):
        cache = {"windows": [{"group": "weekly", "percent": 10, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}
        s = burn.fleet_sustainability([], cache,
                                      now=datetime.datetime(2026, 7, 25, tzinfo=datetime.timezone.utc))
        self.assertIsNone(s["observed_pct_per_day"])
        self.assertIn("vzoriek", s["verdict"])

    def test_pace_within_budget_is_sedi(self):
        now = datetime.datetime(2026, 7, 25, 0, 0, tzinfo=datetime.timezone.utc)
        cache = {"windows": [{"group": "weekly", "percent": 10, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}  # 7 days left, budget 90/7=12.86%/day
        rows = [
            {"ts": "2026-07-24T00:00:00+00:00", "weekly_pct": 8},
            {"ts": "2026-07-25T00:00:00+00:00", "weekly_pct": 10},  # +2%/day
        ]
        s = burn.fleet_sustainability(rows, cache, now=now)
        self.assertEqual(s["verdict"], "sedi")

    def test_pace_over_budget_is_prekracuje(self):
        now = datetime.datetime(2026, 7, 25, 0, 0, tzinfo=datetime.timezone.utc)
        cache = {"windows": [{"group": "weekly", "percent": 90, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}  # budget 10/7=1.43%/day
        rows = [
            {"ts": "2026-07-24T00:00:00+00:00", "weekly_pct": 80},
            {"ts": "2026-07-25T00:00:00+00:00", "weekly_pct": 90},  # +10%/day
        ]
        s = burn.fleet_sustainability(rows, cache, now=now)
        self.assertEqual(s["verdict"], "prekracuje rozpocet")


class TestFleetBudgetAlert(unittest.TestCase):
    def test_no_alert_when_within_budget(self):
        now = datetime.datetime(2026, 7, 25, 0, 0, tzinfo=datetime.timezone.utc)
        cache = {"windows": [{"group": "weekly", "percent": 10, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}
        rows = [
            {"ts": "2026-07-24T00:00:00+00:00", "weekly_pct": 8, "per_host": {}},
            {"ts": "2026-07-25T00:00:00+00:00", "weekly_pct": 10, "per_host": {}},
        ]
        self.assertIsNone(burn.fleet_budget_alert(rows, cache, now=now))

    def test_alert_names_top_host_and_model_when_over_budget(self):
        now = datetime.datetime(2026, 7, 25, 0, 0, tzinfo=datetime.timezone.utc)
        cache = {"windows": [{"group": "weekly", "percent": 90, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}
        rows = [
            {"ts": "2026-07-24T00:00:00+00:00", "weekly_pct": 80, "per_host": {}},
            {"ts": "2026-07-25T00:00:00+00:00", "weekly_pct": 90, "per_host": {
                "dev1": _host_row(1.0, 5, 1000, {"claude-sonnet-5": 1.0}),
                "dev2": _host_row(9.0, 5, 1000, {"claude-fable-5": 9.0}),
            }},
        ]
        alert = burn.fleet_budget_alert(rows, cache, now=now)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["top_host"], "dev2")
        self.assertEqual(alert["top_model"], "claude-fable-5")
        self.assertIn("dev2", alert["message"])
        self.assertIn("claude-fable-5", alert["message"])


class TestHourlyBurnAlert(unittest.TestCase):
    """#81 -- `burn.hourly_burn_alert` / `burn.render_burn_alert`: the pure
    comparison + Slovak message-render behind job 19's hourly Discord ping."""

    # #149: every fixture row defaults to "scope": "agents" (the shape a
    # real post-#149 merge_fleet_row() now carries, best-effort) -- the
    # REL-median and weekly-step checks only consider same-scope PRIOR rows,
    # so leaving every row in a test at the same default scope keeps this
    # whole class behaving exactly as it did pre-#149. Pass scope=None to
    # build a pre-#149-shaped row for the test that specifically exercises
    # that exclusion.
    def _row(self, ts, total_usd, total_msgs=0, weekly_pct=None, per_host=None,
            scope="agents"):
        row = {"ts": ts, "total_usd": total_usd, "total_msgs": total_msgs}
        if weekly_pct is not None:
            row["weekly_pct"] = weekly_pct
        if per_host is not None:
            row["per_host"] = per_host
        if scope is not None:
            row["scope"] = scope
        return row

    def test_empty_rows_is_none(self):
        self.assertIsNone(burn.hourly_burn_alert([]))

    def test_quiet_hour_below_every_threshold_is_none(self):
        rows = [self._row("2026-07-26T10:00:00+00:00", 2.0, weekly_pct=50),
               self._row("2026-07-26T11:00:00+00:00", 2.5, weekly_pct=51)]
        self.assertIsNone(burn.hourly_burn_alert(rows))

    def test_absolute_threshold_triggers(self):
        rows = [self._row("2026-07-26T14:00:00+00:00", 64.88, total_msgs=337)]
        alert = burn.hourly_burn_alert(rows, abs_usd=20.0)
        self.assertIsNotNone(alert)
        self.assertTrue(any("absolutn" in r for r in alert["reasons"]), alert)
        self.assertIn("64.88", alert["message"])

    def test_below_absolute_threshold_does_not_trigger_alone(self):
        rows = [self._row("2026-07-26T14:00:00+00:00", 19.99)]
        self.assertIsNone(burn.hourly_burn_alert(rows, abs_usd=20.0))

    def test_relative_multiple_of_median_triggers(self):
        # median of the last 6 hours is $2.0 -- 10x that easily crosses a
        # 3x multiplier, even though $6 is well under the absolute default.
        prev = [self._row("2026-07-26T%02d:00:00+00:00" % h, 2.0)
               for h in range(4, 10)]
        rows = prev + [self._row("2026-07-26T10:00:00+00:00", 6.5)]
        alert = burn.hourly_burn_alert(rows, abs_usd=1000.0, rel_mult=3.0,
                                       rel_window=6)
        self.assertIsNotNone(alert)
        self.assertTrue(any("median" in r for r in alert["reasons"]), alert)

    def test_below_relative_multiple_does_not_trigger_alone(self):
        prev = [self._row("2026-07-26T%02d:00:00+00:00" % h, 2.0)
               for h in range(4, 10)]
        rows = prev + [self._row("2026-07-26T10:00:00+00:00", 5.0)]
        self.assertIsNone(burn.hourly_burn_alert(rows, abs_usd=1000.0,
                                                 rel_mult=3.0, rel_window=6))

    def test_no_prior_hours_never_crashes_the_relative_check(self):
        rows = [self._row("2026-07-26T14:00:00+00:00", 5.0)]
        # nothing to compute a median from -- must not raise, and must not
        # spuriously trigger the relative check.
        self.assertIsNone(burn.hourly_burn_alert(rows, abs_usd=1000.0,
                                                 rel_mult=3.0))

    def test_relative_check_ignores_prior_rows_without_the_scope_tag(self):
        # #149: 6 tiny pre-#149-shaped rows (no scope tag) would give a
        # median of $0.10 -- comparing the current (always post-#149) hour
        # against that would fire the relative check on the scope
        # DISCONTINUITY itself, not on a real spike. They must be ignored
        # outright, exactly as if no prior hours existed at all.
        old_prev = [self._row("2026-07-26T%02d:00:00+00:00" % h, 0.10, scope=None)
                   for h in range(4, 10)]
        rows = old_prev + [self._row("2026-07-26T10:00:00+00:00", 6.5)]
        alert = burn.hourly_burn_alert(rows, abs_usd=1000.0, rel_mult=3.0,
                                       rel_window=6)
        self.assertIsNone(alert, alert)

        # The SAME shape, but every prior row also carries the scope tag,
        # DOES fire the relative check -- proving the exclusion above is
        # about the tag, not about the values.
        scoped_prev = [self._row("2026-07-26T%02d:00:00+00:00" % h, 0.10)
                      for h in range(4, 10)]
        rows2 = scoped_prev + [self._row("2026-07-26T10:00:00+00:00", 6.5)]
        alert2 = burn.hourly_burn_alert(rows2, abs_usd=1000.0, rel_mult=3.0,
                                        rel_window=6)
        self.assertIsNotNone(alert2)
        self.assertTrue(any("median" in r for r in alert2["reasons"]), alert2)

    def test_relative_check_only_compares_priors_matching_the_latest_rows_own_scope(self):
        # F3: hourly_burn_alert() filtered PRIOR rows to scope=="agents" but
        # never checked the LATEST row's own scope. Fixture: tagged priors
        # at $1 + an UNTAGGED latest at $50 -- crossing the scope boundary
        # must not fire the median check (comparing a new-scope $50 hour
        # against an old-scope $1 median is meaningless, not a real spike).
        prev = [self._row("2026-07-26T%02d:00:00+00:00" % h, 1.0)
               for h in range(4, 10)]              # default scope="agents"
        latest = self._row("2026-07-26T10:00:00+00:00", 50.0, scope=None)
        alert = burn.hourly_burn_alert(prev + [latest], abs_usd=1000.0,
                                       rel_mult=3.0, rel_window=6)
        self.assertIsNone(alert, alert)

        # Same shape, but the priors are ALSO untagged (old-vs-old) -- must
        # still compare and fire, since both sides share the SAME (missing)
        # scope -- old-vs-old stays consistent mid-rollout too.
        prev2 = [self._row("2026-07-26T%02d:00:00+00:00" % h, 1.0, scope=None)
                for h in range(4, 10)]
        latest2 = self._row("2026-07-26T10:00:00+00:00", 50.0, scope=None)
        alert2 = burn.hourly_burn_alert(prev2 + [latest2], abs_usd=1000.0,
                                        rel_mult=3.0, rel_window=6)
        self.assertIsNotNone(alert2)
        self.assertTrue(any("median" in r for r in alert2["reasons"]), alert2)

    def test_relative_check_requires_a_warmup_of_same_scope_priors(self):
        # F4: with only 1 or 2 same-scope prior hours the median is not a
        # reliable baseline -- REL must not fire regardless of how far
        # above it the latest hour sits (fixture: a single $2 prior would
        # let a $7 hour read as "3x the median"). Needs
        # max(3, rel_window // 2) same-scope priors before it may fire.
        latest = self._row("2026-07-26T10:00:00+00:00", 7.0)
        one_prior = [self._row("2026-07-26T09:00:00+00:00", 2.0)]
        alert = burn.hourly_burn_alert(one_prior + [latest], abs_usd=1000.0,
                                       rel_mult=3.0, rel_window=6)
        self.assertIsNone(alert, alert)

        two_priors = [self._row("2026-07-26T%02d:00:00+00:00" % h, 2.0)
                     for h in (8, 9)]
        alert2 = burn.hourly_burn_alert(two_priors + [latest], abs_usd=1000.0,
                                        rel_mult=3.0, rel_window=6)
        self.assertIsNone(alert2, alert2)

        three_priors = [self._row("2026-07-26T%02d:00:00+00:00" % h, 2.0)
                       for h in (7, 8, 9)]
        alert3 = burn.hourly_burn_alert(three_priors + [latest], abs_usd=1000.0,
                                        rel_mult=3.0, rel_window=6)
        self.assertIsNotNone(alert3)
        self.assertTrue(any("median" in r for r in alert3["reasons"]), alert3)

    def test_weekly_step_crossing_triggers(self):
        rows = [self._row("2026-07-26T13:00:00+00:00", 1.0, weekly_pct=77),
               self._row("2026-07-26T14:00:00+00:00", 1.0, weekly_pct=80)]
        alert = burn.hourly_burn_alert(rows, abs_usd=1000.0, rel_mult=1000.0,
                                       weekly_step_pct=5)
        self.assertIsNotNone(alert)
        self.assertTrue(any("tyzdenny" in r for r in alert["reasons"]), alert)
        self.assertIn("77", alert["message"])
        self.assertIn("80", alert["message"])

    def test_weekly_percent_within_the_same_step_does_not_trigger(self):
        rows = [self._row("2026-07-26T13:00:00+00:00", 1.0, weekly_pct=77),
               self._row("2026-07-26T14:00:00+00:00", 1.0, weekly_pct=78)]
        self.assertIsNone(burn.hourly_burn_alert(rows, abs_usd=1000.0,
                                                 rel_mult=1000.0, weekly_step_pct=5))

    def test_weekly_window_reset_drop_is_never_a_crossing(self):
        # a weekly reset drops the % back down -- must never misread the
        # DROP itself as "crossed a step".
        rows = [self._row("2026-07-26T13:00:00+00:00", 1.0, weekly_pct=97),
               self._row("2026-07-26T14:00:00+00:00", 1.0, weekly_pct=2)]
        self.assertIsNone(burn.hourly_burn_alert(rows, abs_usd=1000.0,
                                                 rel_mult=1000.0, weekly_step_pct=5))

    def test_multiple_thresholds_firing_produce_one_combined_message(self):
        rows = [self._row("2026-07-26T13:00:00+00:00", 1.0, weekly_pct=77),
               self._row("2026-07-26T14:00:00+00:00", 100.0, weekly_pct=80)]
        alert = burn.hourly_burn_alert(rows, abs_usd=20.0, rel_mult=3.0,
                                       weekly_step_pct=5)
        self.assertIsNotNone(alert)
        # every threshold genuinely fires here (abs, relative-to-median, AND
        # the weekly step) -- the point is ONE combined message regardless.
        self.assertGreaterEqual(len(alert["reasons"]), 2)
        self.assertEqual(alert["message"].count("Spotreba"), 1)

    def test_message_names_top_two_hosts_and_previous_hours(self):
        per_host = {
            "gatekeeper": _host_row(31.57, 176, 257000),
            "dev1": _host_row(29.68, 149, 211000),
            "montalu": _host_row(3.63, 12, 90000),
        }
        rows = [self._row("2026-07-26T11:00:00+00:00", 17.60),
               self._row("2026-07-26T12:00:00+00:00", 15.04),
               self._row("2026-07-26T13:00:00+00:00", 8.86),
               self._row("2026-07-26T14:00:00+00:00", 64.88, total_msgs=337,
                        per_host=per_host)]
        alert = burn.hourly_burn_alert(rows, abs_usd=20.0)
        self.assertIsNotNone(alert)
        msg = alert["message"]
        self.assertIn("14:00", msg)
        self.assertIn("64.88", msg)
        self.assertIn("337", msg)
        self.assertIn("gatekeeper", msg)
        self.assertIn("dev1", msg)
        self.assertNotIn("montalu", msg)   # only the top 2 hosts
        self.assertIn("17.60", msg)
        self.assertIn("15.04", msg)
        self.assertIn("8.86", msg)

    def test_a_host_with_an_error_entry_is_excluded_from_top_hosts(self):
        per_host = {"gatekeeper": _host_row(31.57, 176, 257000),
                   "dev2": {"error": "ssh timeout"}}
        rows = [self._row("2026-07-26T14:00:00+00:00", 31.57, per_host=per_host)]
        alert = burn.hourly_burn_alert(rows, abs_usd=20.0)
        self.assertIsNotNone(alert)
        self.assertIn("gatekeeper", alert["message"])
        self.assertNotIn("dev2", alert["message"])

    def test_hour_bucket_is_the_evaluated_rows_own(self):
        ts = "2026-07-26T14:00:00+00:00"
        rows = [self._row(ts, 64.88)]
        alert = burn.hourly_burn_alert(rows, abs_usd=20.0)
        self.assertEqual(alert["hour_bucket"], burn.hour_bucket_of_ts(ts))


class TestBurnAlertAbsRecalibration(unittest.TestCase):
    def test_abs_threshold_recalibrated_against_measured_fleet_p95(self):
        # F5: BURN_ALERT_ABS_USD=30.0 was calibrated against dev1's own
        # hourly buckets, but job 19 feeds hourly_burn_alert() job 16's
        # FLEET-WIDE merged total_usd -- against that population $30 fired
        # on 264/317 (83%) of real ~/.claude/burn-history/fleet.jsonl
        # old-scope hourly rows (2026-07-25..2026-07-31). Recalibrated:
        # p95 total_usd of those 317 rows is $151.27; scaled by the
        # measured 1.39x old->new ratio (#149) that's ~=$210.27, rounded to
        # a clean $210.
        self.assertEqual(burn.BURN_ALERT_ABS_USD, 210.0)


class TestWeeklyStepCrossed(unittest.TestCase):
    def test_crossing_upward_is_true(self):
        self.assertTrue(burn._weekly_step_crossed(77, 80, 5))

    def test_within_the_same_step_is_false(self):
        self.assertFalse(burn._weekly_step_crossed(77, 78, 5))

    def test_a_drop_is_never_a_crossing(self):
        self.assertFalse(burn._weekly_step_crossed(97, 2, 5))

    def test_equal_values_are_never_a_crossing(self):
        self.assertFalse(burn._weekly_step_crossed(80, 80, 5))

    def test_none_on_either_side_is_false(self):
        self.assertFalse(burn._weekly_step_crossed(None, 80, 5))
        self.assertFalse(burn._weekly_step_crossed(77, None, 5))
        self.assertFalse(burn._weekly_step_crossed(None, None, 5))


class TestMedianHelper(unittest.TestCase):
    def test_odd_count(self):
        self.assertEqual(burn._median([3.0, 1.0, 2.0]), 2.0)

    def test_even_count_averages_the_middle_two(self):
        self.assertEqual(burn._median([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_empty_is_none(self):
        self.assertIsNone(burn._median([]))

    def test_single_value(self):
        self.assertEqual(burn._median([5.0]), 5.0)


class TestFleetCompareRows(unittest.TestCase):
    def test_normalizes_to_snapshot_shape(self):
        rows = [{"ts": "t0", "total_usd": 3.0, "weighted_avg_ctx": 500, "total_msgs": 7}]
        got = burn.fleet_compare_rows(rows)
        self.assertEqual(got, [{"ts": "t0", "usd": 3.0, "avg_ctx": 500, "msgs": 7}])

    def test_scope_is_carried_through(self):
        # F1: fleet_compare_rows() used to rebuild rows without "scope" --
        # _window_stats() (via compare_changes()) filters on
        # r.get("scope") == "agents", so every normalized row got silently
        # dropped and `burn --compare`'s fleet half never had any data.
        rows = [{"ts": "t0", "total_usd": 3.0, "weighted_avg_ctx": 500,
                "total_msgs": 7, "scope": "agents"}]
        got = burn.fleet_compare_rows(rows)
        self.assertEqual(got[0]["scope"], "agents")

    def test_scope_tag_end_to_end_through_compare_changes(self):
        # F1: the real regression -- fleet_compare_rows() feeding
        # compare_changes() with genuinely scope-tagged fleet rows must
        # yield NON-EMPTY before/after stats, not "ziadne data" forever.
        fleet_rows = [
            {"ts": "2026-07-31T09:00:00+00:00", "total_usd": 4.0,
             "weighted_avg_ctx": 900, "total_msgs": 9, "scope": "agents"},
            {"ts": "2026-07-31T10:00:00+00:00", "total_usd": 5.0,
             "weighted_avg_ctx": 1000, "total_msgs": 10, "scope": "agents"},
        ]
        snap_rows = burn.fleet_compare_rows(fleet_rows)
        changes = [{"ts": "2026-07-31T10:30:00+00:00", "host": "dev1",
                   "text": "zmena"}]
        results = burn.compare_changes(snap_rows, changes, window_hours=6)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0]["before"]["n"], 0, results[0])


class TestRenderFleet(unittest.TestCase):
    def test_empty_rows_prints_hint(self):
        out = burn.render_fleet([])
        self.assertIn("--fleet", out)

    def test_renders_table_and_hosts(self):
        rows = [{"ts": "2026-07-25T17:00:00+00:00", "total_usd": 4.0, "total_msgs": 40,
                "weighted_avg_ctx": 250000, "per_host": {
                    "dev1": _host_row(1.0, 10, 100000),
                    "dev2": {"error": "ssh timeout"},
                }}]
        out = burn.render_fleet(rows)
        self.assertIn("dev1=$1.00", out)
        self.assertIn("dev2=ERR", out)
        self.assertIn("total=$4.00", out)

    def test_includes_sustainability_verdict(self):
        rows = [{"ts": "2026-07-25T17:00:00+00:00", "total_usd": 1.0, "total_msgs": 1,
                "weighted_avg_ctx": 1000, "per_host": {}}]
        out = burn.render_fleet(rows, cache=None)
        self.assertIn("verdikt:", out)
        self.assertIn("cache", out)

    def test_stale_host_renders_as_dash_not_err_or_dollar(self):
        # #60 — a stale (wrong-hour) host must render distinctly from BOTH a
        # hard collection error (ERR) and a genuine $0.00 sample.
        rows = [{"ts": "2026-07-25T17:00:00+00:00", "total_usd": 1.0, "total_msgs": 10,
                "weighted_avg_ctx": 100000, "per_host": {
                    "dev1": _host_row(1.0, 10, 100000),
                    "gk": {"error": "no sample for hour 123 (latest ...)", "stale": True},
                }}]
        out = burn.render_fleet(rows)
        self.assertIn("gk=—", out)
        self.assertNotIn("gk=ERR", out)
        self.assertNotIn("gk=$0.00", out)

    def test_row_note_shows_how_many_hosts_have_a_sample(self):
        rows = [{"ts": "2026-07-25T17:00:00+00:00", "total_usd": 1.0, "total_msgs": 10,
                "weighted_avg_ctx": 100000, "per_host": {
                    "dev1": _host_row(1.0, 10, 100000),
                    "gk": {"error": "no sample for hour 123", "stale": True},
                    "montalu": {"error": "ssh timeout"},
                }}]
        out = burn.render_fleet(rows)
        self.assertIn("1/3", out)
        self.assertIn("hostov ma vzorku", out)

    def test_trend_not_comparable_prints_reason_not_percent(self):
        rows = [
            {"ts": "t0", "total_usd": 5.0, "total_msgs": 10, "weighted_avg_ctx": 1000,
             "per_host": {"dev1": _host_row(2.0, 5, 1000), "dev2": _host_row(3.0, 5, 1000)}},
            {"ts": "t1", "total_usd": 2.0, "total_msgs": 5, "weighted_avg_ctx": 1000,
             "per_host": {"dev1": _host_row(2.0, 5, 1000),
                          "dev2": {"error": "stale", "stale": True}}},
        ]
        out = burn.render_fleet(rows)
        self.assertIn("neporovnat", out)


class TestCmdBurnFleet(unittest.TestCase):
    def _with_home(self, fn):
        with TemporaryDirectory() as home:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                return fn()
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_fleet_with_no_data_prints_hint(self):
        def run():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark=None, compare=False,
                    window=None, mark_ts=None, fleet=True, hours=24))
            return buf.getvalue()
        out = self._with_home(run)
        self.assertIn("--fleet", out)

    def test_fleet_renders_written_rows(self):
        def run():
            path = Path(os.environ["HOME"]) / ".claude" / "burn-history" / "fleet.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "ts": "2026-07-25T17:00:00+00:00", "total_usd": 2.0, "total_msgs": 5,
                "weighted_avg_ctx": 1000, "per_host": {"dev1": _host_row(2.0, 5, 1000)},
            }) + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark=None, compare=False,
                    window=None, mark_ts=None, fleet=True, hours=24))
            return buf.getvalue()
        out = self._with_home(run)
        self.assertIn("dev1=$2.00", out)

    def test_compare_includes_fleet_block_when_fleet_data_exists(self):
        def run():
            home = Path(os.environ["HOME"])
            change_ts = "2026-07-25T12:00:00+00:00"
            burn.mark_change("krok 1", now=datetime.datetime.fromisoformat(change_ts))
            fleet_p = home / ".claude" / "burn-history" / "fleet.jsonl"
            fleet_p.parent.mkdir(parents=True, exist_ok=True)
            with open(fleet_p, "a") as f:
                for ts, usd in [("2026-07-25T10:00:00+00:00", 2.0),
                               ("2026-07-25T13:00:00+00:00", 1.0)]:
                    f.write(json.dumps({"ts": ts, "total_usd": usd, "total_msgs": 5,
                                        "weighted_avg_ctx": 1000, "per_host": {}}) + "\n")
            snap_p = home / ".claude" / "burn-history" / "snapshots.jsonl"
            with open(snap_p, "a") as f:
                for ts, usd in [("2026-07-25T10:00:00+00:00", 2.0),
                               ("2026-07-25T13:00:00+00:00", 1.0)]:
                    f.write(json.dumps({"ts": ts, "host": "dev1", "user": "z", "window_h": 1,
                                        "usd": usd, "msgs": 5, "avg_ctx": 1000, "by_model": {}}) + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_burn(argparse.Namespace(
                    days=7, json=False, host=None, mark=None, compare=True,
                    window=6, mark_ts=None))
            return buf.getvalue()
        out = self._with_home(run)
        self.assertIn("Sada (cela monitorovana sada):", out)


# --------------------------------------------------------------------------- #
# #55 — the fleet job's REMOTE collector (watchdog job 16's `fleet_fetch`):
# tails each REMOTE_HOSTS box's OWN already-written snapshots.jsonl over ssh
# (never re-scans transcripts remotely — cheap, hourly-safe). Mirrors
# _burn_remote_cmd/_burn_remote's own identity/sshpass split + fail-safe
# contract exactly (never invent a new ssh shape).
# --------------------------------------------------------------------------- #

class TestFleetRemoteCmd(unittest.TestCase):
    def test_uses_identity_when_present(self):
        remote = {"name": "gatekeeper", "host": "1.2.3.4", "user": "gatekeeper",
                  "repo_path": "~/devel/airuleset",
                  "identity": "~/.secrets/gatekeeper_access_ed25519"}
        cmd = airuleset._fleet_remote_cmd(remote)
        self.assertIn("-i", cmd)
        self.assertNotIn("sshpass", cmd)
        self.assertIn("gatekeeper@1.2.3.4", cmd)
        self.assertIn("tail -n 1 ~/.claude/burn-history/snapshots.jsonl", " ".join(cmd))

    def test_uses_sshpass_without_identity(self):
        remote = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                  "repo_path": "~/devel/airuleset"}
        cmd = airuleset._fleet_remote_cmd(remote)
        self.assertIn("sshpass", cmd)
        self.assertIn("newlevel@5.6.7.8", cmd)


class TestHourBucketOfTs(unittest.TestCase):
    def test_same_utc_instant_different_offsets_bucket_equal(self):
        # #60 — gk writes +00:00, dev1 +02:00; 18:00+02:00 IS 16:00 UTC.
        a = airuleset._hour_bucket_of_ts("2026-07-25T16:00:00+00:00")
        b = airuleset._hour_bucket_of_ts("2026-07-25T18:00:00+02:00")
        self.assertEqual(a, b)

    def test_same_hour_digits_different_offset_is_a_different_bucket(self):
        # same raw "17:00" digits, but +00:00 vs +02:00 are different UTC
        # instants -- a naive string compare would wrongly treat these equal.
        a = airuleset._hour_bucket_of_ts("2026-07-25T17:00:00+00:00")
        b = airuleset._hour_bucket_of_ts("2026-07-25T17:00:00+02:00")
        self.assertNotEqual(a, b)

    def test_unparsable_or_missing_is_none(self):
        self.assertIsNone(airuleset._hour_bucket_of_ts("not-a-date"))
        self.assertIsNone(airuleset._hour_bucket_of_ts(None))


class TestFleetRemoteRow(unittest.TestCase):
    REMOTE = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
              "repo_path": "~/devel/airuleset"}

    def test_parses_the_tailed_line_when_hour_matches(self):
        ts = "2026-07-25T17:00:00+00:00"
        row = {"ts": ts, "host": "dev2", "usd": 1.5,
              "msgs": 3, "avg_ctx": 2000, "by_model": {}}
        want = airuleset._hour_bucket_of_ts(ts)
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n",
                                        stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, want)
        self.assertEqual(got["usd"], 1.5)
        self.assertNotIn("error", got)

    def test_mismatched_hour_becomes_stale_error_not_silent_fallback(self):
        # #60 core regression: the remote's LAST line is real JSON, but from
        # an EARLIER hour (its own job 13 hasn't written this hour's row
        # yet) -- must error, NEVER silently return the stale row's data.
        row = {"ts": "2026-07-25T16:00:00+00:00", "host": "dev2", "usd": 999.0,
              "msgs": 1, "avg_ctx": 1, "by_model": {}}
        want = airuleset._hour_bucket_of_ts("2026-07-25T17:00:00+00:00")
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n",
                                        stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, want)
        self.assertIn("error", got)
        self.assertTrue(got.get("stale"))
        self.assertNotIn("usd", got)

    def test_timezone_offset_is_converted_to_utc_before_comparing(self):
        # gk writes +00:00, dev1 +02:00 (live #60 evidence) -- 18:00+02:00 IS
        # 16:00 UTC, so it must MATCH an hour bucket requested for 16:00 UTC
        # even though the raw hour-of-day digits ("18" vs "16") differ.
        row = {"ts": "2026-07-25T18:00:00+02:00", "host": "dev1", "usd": 2.0,
              "msgs": 1, "avg_ctx": 1, "by_model": {}}
        want = airuleset._hour_bucket_of_ts("2026-07-25T16:00:00+00:00")
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n",
                                        stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, want)
        self.assertEqual(got["usd"], 2.0)
        self.assertNotIn("error", got)

    def test_timezone_offset_that_lands_in_different_utc_hour_still_errors(self):
        # same raw hour-of-day digits ("17") but a DIFFERENT offset -> a
        # DIFFERENT UTC instant -- must NOT match.
        row = {"ts": "2026-07-25T17:00:00+02:00", "host": "dev1", "usd": 3.0,
              "msgs": 1, "avg_ctx": 1, "by_model": {}}
        want = airuleset._hour_bucket_of_ts("2026-07-25T17:00:00+00:00")
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n",
                                        stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, want)
        self.assertIn("error", got)
        self.assertTrue(got.get("stale"))

    def test_missing_ts_becomes_stale_error(self):
        row = {"host": "dev2", "usd": 1.0, "msgs": 1, "avg_ctx": 1}
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n",
                                        stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, 123456)
        self.assertIn("error", got)
        self.assertTrue(got.get("stale"))

    def test_nonzero_exit_becomes_error(self):
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=255, stdout="", stderr="Connection refused")):
            got = airuleset._fleet_remote_row(self.REMOTE, 0)
        self.assertIn("error", got)
        self.assertIn("Connection refused", got["error"])
        self.assertNotIn("stale", got)

    def test_timeout_becomes_error_not_raise(self):
        import subprocess as sp

        def raise_timeout(*a, **k):
            raise sp.TimeoutExpired(cmd="ssh", timeout=15)
        with m.patch("subprocess.run", side_effect=raise_timeout):
            got = airuleset._fleet_remote_row(self.REMOTE, 0)
        self.assertIn("error", got)

    def test_empty_output_becomes_error(self):
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout="", stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, 0)
        self.assertIn("error", got)

    def test_invalid_json_becomes_error(self):
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout="NOT JSON\n", stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, 0)
        self.assertIn("error", got)

    def test_non_dict_json_becomes_error(self):
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout="42\n", stderr="")):
            got = airuleset._fleet_remote_row(self.REMOTE, 0)
        self.assertIn("error", got)


class TestWatchdogFleetFetch(unittest.TestCase):
    def test_one_row_per_host_a_bad_host_never_drops_the_rest(self):
        hosts = [
            {"name": "dev2", "host": "5.6.7.8", "user": "newlevel", "repo_path": "~"},
            {"name": "gatekeeper", "host": "1.2.3.4", "user": "gatekeeper", "repo_path": "~"},
        ]
        want = airuleset._hour_bucket_of_ts("2026-07-25T17:00:00+00:00")

        def fake_run(cmd, **kwargs):
            if "5.6.7.8" in " ".join(cmd):
                return m.Mock(returncode=0,
                             stdout=json.dumps({"ts": "2026-07-25T17:00:00+00:00",
                                               "usd": 1.0, "msgs": 1, "avg_ctx": 100}) + "\n",
                             stderr="")
            return m.Mock(returncode=255, stdout="", stderr="Connection refused")
        with m.patch("subprocess.run", side_effect=fake_run):
            got = airuleset._watchdog_fleet_fetch(hosts, want)
        self.assertEqual(got["dev2"]["usd"], 1.0)
        self.assertIn("error", got["gatekeeper"])

    def test_defaults_to_remote_hosts(self):
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=255, stdout="", stderr="down")):
            got = airuleset._watchdog_fleet_fetch()
        self.assertEqual(set(got.keys()), {h["name"] for h in airuleset.REMOTE_HOSTS})

    def test_want_hour_bucket_defaults_to_current_utc_hour_when_not_given(self):
        # exercise the "no want_hour_bucket given" default path directly:
        # a row stamped at exactly "now" must round-trip as fresh, proving
        # the default is a real, current UTC hour bucket (not e.g. always 0).
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        row = {"ts": now_iso, "usd": 1.0, "msgs": 1, "avg_ctx": 1}
        with m.patch("subprocess.run",
                    return_value=m.Mock(returncode=0, stdout=json.dumps(row) + "\n", stderr="")):
            got = airuleset._watchdog_fleet_fetch(
                [{"name": "dev2", "host": "x", "user": "y"}])
        self.assertNotIn("error", got["dev2"])


if __name__ == "__main__":
    unittest.main()
