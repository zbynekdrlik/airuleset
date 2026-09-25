"""#1154 part 2 — fleet.jsonl readers read only the window they need.

`burn.load_fleet(path, since=<aware datetime>)` reads the file BACKWARDS in
blocks and stops at the first complete row older than `since`; it must return
exactly what a full read filtered to `ts >= since` returns, in file order.
`since=None` keeps the historical full read. The file itself is never trimmed
(claudy reads it as history).

Each caller passes the window it actually uses — asserted per caller below:
job 19 (`burn_alert_job`) is bounded relative to the NEWEST row, `burn
--compare` to the earliest change mark; job 16 (`fleet_burn_job`), `burn
--fleet` and job 35 (conformance heartbeat) genuinely consume the full history
and keep `since=None`.

Synthetic files in temp dirs only — never the real fleet.jsonl, no network.
"""
import contextlib
import datetime
import io
import json
import os
import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import burn  # noqa: E402
import cli_burn  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import conformance_heartbeat as hb  # noqa: E402

UTC = datetime.timezone.utc
CEST = datetime.timezone(datetime.timedelta(hours=2))
T0 = datetime.datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def _ft():
    from burn import fleet_tail
    return fleet_tail


def _row(i, pad=0, usd=None, tz=UTC, **extra):
    ts = (T0 + datetime.timedelta(hours=i)).astimezone(tz)
    r = {"ts": ts.isoformat(), "total_usd": float(i if usd is None else usd),
         "total_msgs": i, "scope": "agents", "per_host": {}}
    if pad:
        r["pad"] = "x" * pad
    r.update(extra)
    return r


def _write_lines(path, lines, newline="\n", trailing=True):
    data = newline.join(lines) + (newline if trailing else "")
    Path(path).write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def _reference(path, since):
    """The contract: a full read filtered to `ts >= since`, in file order."""
    out = []
    for r in burn._read_jsonl(path):
        if not isinstance(r, dict):
            continue
        t = burn._parse_ts(r.get("ts"))
        if t is None:
            continue
        if t.timestamp() >= since.timestamp():
            out.append(r)
    return out


def _mixed_lines(n=40):
    """Hourly rows with every kind of junk interleaved: a malformed line, a
    blank line, a non-dict JSON value, a row with no ts, a row with an
    unparsable ts, a row far longer than the small test blocks, offsets that
    differ between rows (+00:00 vs +02:00 — the #60 class)."""
    lines = []
    for i in range(n):
        tz = CEST if i % 3 else UTC
        lines.append(json.dumps(_row(i, pad=(700 if i % 7 == 0 else i * 3), tz=tz)))
        if i % 5 == 1:
            lines.append('{"ts": "2026-07-01T0')          # truncated / malformed
        if i % 6 == 2:
            lines.append("")
        if i % 9 == 4:
            lines.append("5")                            # JSON, but not a row
        if i % 8 == 3:
            lines.append(json.dumps({"total_usd": 1.0}))  # no ts at all
        if i % 11 == 5:
            lines.append(json.dumps({"ts": "not-a-date", "total_usd": 2.0}))
    return lines


class ReadRowsSinceEquivalence(unittest.TestCase):
    def test_every_since_matches_a_filtered_full_read_across_block_sizes(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, _mixed_lines())
            sinces = [T0 - datetime.timedelta(hours=5)]
            sinces += [T0 + datetime.timedelta(hours=h, minutes=mm)
                       for h in range(0, 42) for mm in (0, 30)]
            for block in (7, 64, 100, 257, 4096, 1 << 16):
                for s in sinces:
                    got = _ft().read_rows_since(p, s, block_size=block)
                    self.assertEqual(got, _reference(p, s), (block, s))

    def test_load_fleet_since_matches_reference_and_none_keeps_full_read(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, _mixed_lines())
            s = T0 + datetime.timedelta(hours=17)
            self.assertEqual(burn.load_fleet(p, since=s), _reference(p, s))
            self.assertEqual(burn.load_fleet(p, since=None), burn._read_jsonl(p))
            self.assertEqual(burn.load_fleet(p), burn._read_jsonl(p))

    def test_row_spanning_a_block_boundary_is_returned_intact(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            rows = [_row(i, pad=333) for i in range(10)]
            _write_lines(p, [json.dumps(r) for r in rows])
            for block in range(50, 700, 37):
                got = _ft().read_rows_since(p, T0 + datetime.timedelta(hours=3),
                                            block_size=block)
                self.assertEqual(got, rows[3:], block)

    def test_crlf_no_trailing_newline_and_non_utf8_bytes(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            rows = [_row(i) for i in range(6)]
            body = "\r\n".join(json.dumps(r) for r in rows[:3]).encode()
            body += b"\r\n\xff\xfe garbage \x80\r\n"
            body += "\r\n".join(json.dumps(r) for r in rows[3:]).encode()
            p.write_bytes(body)                          # no trailing newline
            for block in (5, 33, 4096):
                for h in range(-1, 7):
                    s = T0 + datetime.timedelta(hours=h)
                    self.assertEqual(_ft().read_rows_since(p, s, block_size=block),
                                     _reference(p, s), (block, h))

    def test_missing_empty_blank_and_directory_are_empty(self):
        with TemporaryDirectory() as d:
            s = T0
            self.assertEqual(burn.load_fleet(Path(d) / "nope.jsonl", since=s), [])
            empty = Path(d) / "empty.jsonl"
            empty.write_bytes(b"")
            self.assertEqual(burn.load_fleet(empty, since=s), [])
            blank = Path(d) / "blank.jsonl"
            blank.write_bytes(b"\n\n  \n")
            self.assertEqual(burn.load_fleet(blank, since=s), [])
            self.assertEqual(burn.load_fleet(Path(d), since=s), [])

    def test_since_after_every_row_and_before_every_row(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            rows = [_row(i) for i in range(5)]
            _write_lines(p, [json.dumps(r) for r in rows])
            self.assertEqual(burn.load_fleet(p, since=T0 + datetime.timedelta(days=9)), [])
            self.assertEqual(burn.load_fleet(p, since=T0 - datetime.timedelta(days=9)), rows)

    def test_naive_since_is_refused(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, [json.dumps(_row(0))])
            with self.assertRaises(ValueError):
                burn.load_fleet(p, since=datetime.datetime(2026, 7, 1))

    def test_file_is_never_modified(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, _mixed_lines())
            before = (p.read_bytes(), p.stat().st_mtime_ns)
            burn.load_fleet(p, since=T0 + datetime.timedelta(hours=20))
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), before)


class _CountingFile:
    def __init__(self, f, counter):
        self._f, self._c = f, counter

    def read(self, *a):
        data = self._f.read(*a)
        self._c.append(len(data))
        return data

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()


class BoundedBytesRead(unittest.TestCase):
    def test_tail_read_of_a_large_file_reads_only_the_window(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            n = 12000
            lines = [json.dumps(_row(i, pad=500)) for i in range(n)]
            _write_lines(p, lines)
            size = p.stat().st_size
            self.assertGreater(size, 6_000_000)
            window_rows = 30
            since = T0 + datetime.timedelta(hours=n - window_rows)
            window_bytes = sum(len(ln) + 1 for ln in lines[-window_rows:])
            counter = []
            real_open = open

            def counting_open(*a, **k):
                return _CountingFile(real_open(*a, **k), counter)
            block = 1 << 16
            with m.patch("burn.fleet_tail.open", counting_open, create=True):
                got = burn.load_fleet(p, since=since)
            self.assertEqual(len(got), window_rows)
            self.assertEqual(got, _reference(p, since))
            read = sum(counter)
            # the window + the one older row that stops the scan + one block
            self.assertLessEqual(read, window_bytes + len(lines[0]) + 1 + block)
            self.assertLess(read, size // 50)


def _spy_load_fleet():
    calls = []
    real = burn.load_fleet

    def spy(path=None, since=None):
        calls.append({"path": path, "since": since})
        return real(path, since=since)
    return calls, spy


class BurnAlertJobWindow(unittest.TestCase):
    """Job 19 reads rows[-1] plus the last `rel_window` same-scope priors
    (COUNT-based, relative to the newest row — the existing tests evaluate a
    months-old newest row with a wall-clock `now`). So its bound is the newest
    row's ts minus (rel_window + 48 h margin)."""

    def _file(self, d, n=200, spike_at=None):
        p = Path(d) / "fleet.jsonl"
        rows = [_row(i, usd=(500.0 if i == spike_at else 10.0),
                     weekly_pct=min(99, i // 4)) for i in range(n)]
        _write_lines(p, [json.dumps(r) for r in rows])
        return p, rows

    def test_since_is_newest_row_minus_rel_window_plus_margin(self):
        with TemporaryDirectory() as d:
            p, rows = self._file(d)
            newest = burn._parse_ts(rows[-1]["ts"])
            want = newest - datetime.timedelta(hours=6) - _ft().BURN_ALERT_SINCE_MARGIN
            self.assertEqual(_ft().BURN_ALERT_SINCE_MARGIN, datetime.timedelta(hours=48))
            self.assertEqual(burn.burn_alert_since(p, 6), want)
            calls, spy = _spy_load_fleet()
            with m.patch.object(burn, "load_fleet", spy):
                wd.burn_alert_job(0.0, {}, lambda *a, **k: "sent", fleet_path=p,
                                  rel_window=6, abs_usd=1e9)
            self.assertEqual([c["since"] for c in calls], [want])

    def test_env_rel_window_is_resolved_before_the_bounded_read(self):
        with TemporaryDirectory() as d:
            p, rows = self._file(d)
            newest = burn._parse_ts(rows[-1]["ts"])
            calls, spy = _spy_load_fleet()
            with m.patch.dict(os.environ, {"AIRULESET_BURN_ALERT_REL_WINDOW": "10"}), \
                    m.patch.object(burn, "load_fleet", spy):
                wd.burn_alert_job(0.0, {}, lambda *a, **k: "sent", fleet_path=p,
                                  abs_usd=1e9)
            self.assertEqual(calls[0]["since"],
                             newest - datetime.timedelta(hours=10 + 48))

    def test_non_positive_rel_window_means_all_priors_so_full_read(self):
        with TemporaryDirectory() as d:
            p, _ = self._file(d)
            self.assertIsNone(burn.burn_alert_since(p, 0))
            self.assertIsNone(burn.burn_alert_since(p, -3))
            self.assertIsNone(burn.burn_alert_since(Path(d) / "missing.jsonl", 6))

    def test_bounded_run_decides_exactly_like_the_full_history(self):
        with TemporaryDirectory() as d:
            p, rows = self._file(d, spike_at=199)
            full = burn.hourly_burn_alert(rows, abs_usd=1e9, rel_window=6)
            self.assertIsNotNone(full)
            sent = []
            state = {}
            wd.burn_alert_job(0.0, state, lambda msg, **k: sent.append(msg) or "sent",
                              fleet_path=p, abs_usd=1e9, rel_window=6)
            self.assertEqual(sent, [full["message"]])
            self.assertEqual(state["burn_alert_hour"], full["hour_bucket"])


class FullHistoryCallers(unittest.TestCase):
    """Job 16 and `burn --fleet` both reach `observed_pct_per_day(rows)`, which
    takes the OLDEST weekly sample across ALL rows; job 35's `_scan` keeps
    `last_fresh` unbounded on purpose (#543 F3). All three keep `since=None`."""

    def test_job16_fleet_burn_reads_full_history(self):
        with TemporaryDirectory() as d:
            fleet = Path(d) / "fleet.jsonl"
            snap = Path(d) / "snapshots.jsonl"
            now = datetime.datetime(2026, 7, 25, 20, 30, tzinfo=UTC).timestamp()
            snap.write_text(json.dumps({"ts": "2026-07-25T19:00:00+00:00", "host": "dev1",
                                        "usd": 1.0, "msgs": 1, "avg_ctx": 1,
                                        "by_model": {}}) + "\n")
            calls, spy = _spy_load_fleet()
            with m.patch.object(burn, "load_fleet", spy):
                wd.fleet_burn_job(now, {}, [], lambda *a, **k: None,
                                  fetch=lambda hs, hb: {}, local_snapshot_path=snap,
                                  fleet_path=fleet, usage_cache={})
            self.assertEqual([c["since"] for c in calls], [None])

    def test_heartbeat_reads_full_history(self):
        calls, spy = _spy_load_fleet()
        with m.patch.object(burn, "load_fleet", spy), \
                m.patch.object(burn, "fleet_path", lambda: Path("/nonexistent/f.jsonl")):
            hb.run_conformance_heartbeat_check(
                1_800_000_000.0, {}, send_fn=None, dry_run=True,
                hosts_fn=lambda: [], feed_mtimes_fn=lambda: (None, None))
        self.assertEqual([c["since"] for c in calls], [None])


def _args(**kw):
    base = {"mark": None, "mark_ts": None, "compare": False, "window": None,
            "fleet": False, "hours": None, "days": None, "host": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


class CliBurnWindows(unittest.TestCase):
    def _changes(self):
        return [{"ts": (T0 + datetime.timedelta(hours=60)).isoformat(), "text": "b"},
                {"ts": (T0 + datetime.timedelta(hours=40)).astimezone(CEST).isoformat(),
                 "text": "a"},
                {"ts": "garbage", "text": "skipped by compare_changes"}]

    def test_compare_since_is_earliest_change_minus_window_minus_margin(self):
        ch = self._changes()
        self.assertEqual(burn.compare_since(ch, 6),
                         T0 + datetime.timedelta(hours=40 - 6) - _ft().COMPARE_SINCE_MARGIN)
        self.assertEqual(_ft().COMPARE_SINCE_MARGIN, datetime.timedelta(hours=1))
        naive = ch + [{"ts": "2026-07-01T05:00:00", "text": "naive"}]
        self.assertIsNone(burn.compare_since(naive, 6))

    def test_no_dated_change_reads_nothing(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, [json.dumps(_row(i)) for i in range(5)])
            s = burn.compare_since([{"ts": "garbage"}], 6)
            self.assertEqual(burn.load_fleet(p, since=s), [])

    def test_bounded_compare_equals_full_compare(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "fleet.jsonl"
            _write_lines(p, [json.dumps(_row(i, usd=i % 13)) for i in range(120)])
            ch = self._changes()
            for w in (1, 6, 24):
                full = burn.compare_changes(burn.fleet_compare_rows(burn.load_fleet(p)), ch, w)
                bounded = burn.compare_changes(burn.fleet_compare_rows(
                    burn.load_fleet(p, since=burn.compare_since(ch, w))), ch, w)
                self.assertEqual(bounded, full, w)

    def test_cmd_burn_compare_passes_compare_since(self):
        ch = self._changes()
        calls = []
        with m.patch.object(burn, "load_changes", lambda: ch), \
                m.patch.object(burn, "load_snapshots", lambda: []), \
                m.patch.object(burn, "load_fleet",
                               lambda path=None, since=None: calls.append(since) or []), \
                contextlib.redirect_stdout(io.StringIO()):
            cli_burn.cmd_burn(_args(compare=True, window=6))
        self.assertEqual(calls, [burn.compare_since(ch, 6)])

    def test_cmd_burn_fleet_reads_full_history(self):
        calls = []
        with m.patch.object(burn, "load_fleet",
                            lambda path=None, since=None: calls.append(since) or []), \
                m.patch.object(burn, "load_usage_cache", lambda: {}), \
                contextlib.redirect_stdout(io.StringIO()):
            cli_burn.cmd_burn(_args(fleet=True, hours=24))
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
