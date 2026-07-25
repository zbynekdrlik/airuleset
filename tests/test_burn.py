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
    def _snap(self, ts, usd, avg_ctx, msgs):
        return {"ts": ts, "host": "dev1", "user": "z", "window_h": 1,
                "usd": usd, "msgs": msgs, "avg_ctx": avg_ctx, "by_model": {}}

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

    def test_carries_weekly_pct_and_resets_at_when_given(self):
        row = burn.merge_fleet_row("t", {}, weekly_pct=42, resets_at="2026-08-01T00:00:00+00:00")
        self.assertEqual(row["weekly_pct"], 42)
        self.assertEqual(row["resets_at"], "2026-08-01T00:00:00+00:00")

    def test_no_weekly_pct_key_when_not_given(self):
        row = burn.merge_fleet_row("t", {})
        self.assertNotIn("weekly_pct", row)
        self.assertNotIn("resets_at", row)


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


class TestFleetCompareRows(unittest.TestCase):
    def test_normalizes_to_snapshot_shape(self):
        rows = [{"ts": "t0", "total_usd": 3.0, "weighted_avg_ctx": 500, "total_msgs": 7}]
        got = burn.fleet_compare_rows(rows)
        self.assertEqual(got, [{"ts": "t0", "usd": 3.0, "avg_ctx": 500, "msgs": 7}])


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


if __name__ == "__main__":
    unittest.main()
