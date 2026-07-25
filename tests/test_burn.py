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


if __name__ == "__main__":
    unittest.main()
