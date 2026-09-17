"""#1055 P1 — `_iter_jsonl_tail` becomes a BOUNDED byte-tail read + per-sweep memo.

Root cause (supervisor cProfile on gk, in the ticket): `_iter_jsonl_tail`
`f.read()`s the WHOLE transcript then `bytes.splitlines()`, called 17× per
sweep across 12 sites on the same few multi-hundred-MB Fable transcripts —
22.4 s cumulative + 1.36 GB MAXRSS every 60 s sweep. The fix: seek from the
end, grow a bounded window (256 KB → 16 MB cap) until `max_lines` complete
lines are found, decode only those; fold `_read_jsonl_byte_tail` onto the same
seek primitive; memoize per sweep on `(path, st_size, st_mtime_ns, max_lines)`
cleared at the top of `run_once`; and journal one summary line per sweep.

RED (fails on base main):
  * BoundedReadIsCheap (always-run) — base pulls the WHOLE file (bytes-read ==
    size, peak >= size) and lacks `transcript_read_stats`;
  * the memo / reset / stats tests — `reset_transcript_cache` /
    `transcript_read_stats` do not exist on base;
  * the `run_once` journal-summary test — base emits no `transcript reads:` line;
  * the 300 MB Measurement — the definitive perf+memory proof, kept but
    env-gated behind AIRULESET_PERF_TESTS (review F2: a 300 MB write on every
    full-suite run churns disk on the pressured fleet this ticket optimizes).

GREEN-and-base behaviour locks (pass on BOTH): the bounded reader returns the
SAME entries as a reference whole-file read (small file identical; large file
last-N identical WITHIN the 16 MB window cap — beyond the cap the newest suffix
that fits is returned, and a single line > cap yields []; real entries are well
under the cap so live behaviour is identical), and the folded
`_read_jsonl_byte_tail` stays byte-identical to its historical seek reader.
"""
import json
import os
import shutil
import sys
import time
import tracemalloc
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.transcripts as transcripts  # noqa: E402


# --------------------------------------------------------------------------- #
# Reference readers = the HISTORICAL (base-main) behaviour, verbatim. The
# bounded reader must reproduce these exactly for the last N lines.
# --------------------------------------------------------------------------- #
def _ref_iter_tail(path, max_lines=60):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for ln in raw.splitlines()[-max_lines:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _ref_byte_tail(path, tail_bytes, max_entries):
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-int(tail_bytes), 2)
            except OSError:
                f.seek(0)
            raw = f.read()
    except (OSError, ValueError, TypeError):
        return []
    out = []
    for ln in raw.splitlines()[-max_entries:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _line(i, pad=0):
    return json.dumps({"type": "assistant", "i": i,
                       "message": {"role": "assistant",
                                   "content": [{"type": "text",
                                                "text": "x" * pad}]}})


def _write_lines(path, lines):
    Path(path).write_text("\n".join(lines) + "\n")


def _reset():
    # getattr-guarded so the perf tests measure the REAL base cost (whole-file
    # read) on base main instead of erroring on the missing symbol.
    getattr(transcripts, "reset_transcript_cache", lambda: None)()


class BehaviourLock(unittest.TestCase):
    """Passes on base AND green — the correctness contract."""

    def setUp(self):
        _reset()

    def test_small_file_identical_to_whole_file_reference(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "s.jsonl")
            _write_lines(p, [_line(i) for i in range(40)])
            for ml in (1, 5, 60, 200, 1000):
                _reset()
                self.assertEqual(transcripts._iter_jsonl_tail(p, ml),
                                 _ref_iter_tail(p, ml), "max_lines=%d" % ml)

    def test_large_file_last_n_identical_to_reference(self):
        # >256 KB with ~2 KB lines forces the seek + window-doubling path.
        with TemporaryDirectory() as d:
            p = str(Path(d) / "big.jsonl")
            _write_lines(p, [_line(i, pad=2000) for i in range(2000)])
            self.assertGreater(os.path.getsize(p), 300 * 1024)
            for ml in (1, 60, 200, 500):
                _reset()
                got = transcripts._iter_jsonl_tail(p, ml)
                self.assertEqual(got, _ref_iter_tail(p, ml), "max_lines=%d" % ml)
            # the very last line is the newest entry, in oldest->newest order
            _reset()
            got = transcripts._iter_jsonl_tail(p, 3)
            self.assertEqual([e["i"] for e in got], [1997, 1998, 1999])

    def test_last_line_no_trailing_newline(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "nonl.jsonl")
            Path(p).write_bytes(("\n".join(_line(i) for i in range(30))).encode())
            _reset()
            self.assertEqual(transcripts._iter_jsonl_tail(p, 5),
                             _ref_iter_tail(p, 5))

    def test_invalid_lines_are_skipped_like_reference(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "mix.jsonl")
            _write_lines(p, [_line(0), "not json", _line(1), "{bad", _line(2)])
            _reset()
            self.assertEqual(transcripts._iter_jsonl_tail(p, 10),
                             _ref_iter_tail(p, 10))

    def test_missing_file_returns_empty(self):
        _reset()
        self.assertEqual(transcripts._iter_jsonl_tail("/no/such/file.jsonl"), [])

    def test_empty_file_returns_empty(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "empty.jsonl")
            Path(p).write_bytes(b"")
            _reset()
            self.assertEqual(transcripts._iter_jsonl_tail(p, 10), [])

    def test_byte_tail_fold_is_identical_to_reference(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "b.jsonl")
            _write_lines(p, [_line(i, pad=1000) for i in range(1000)])
            size = os.path.getsize(p)
            for tail, me in [(2000, 200), (500, 50), (size, 100),
                             (size * 2, 100), (10 * size, 5), (0, 10)]:
                self.assertEqual(
                    transcripts._read_jsonl_byte_tail(p, tail, me),
                    _ref_byte_tail(p, tail, me),
                    "tail=%d max_entries=%d" % (tail, me))

    def test_byte_tail_bad_type_returns_empty(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "b2.jsonl")
            _write_lines(p, [_line(0)])
            self.assertEqual(transcripts._read_jsonl_byte_tail(p, "nope", 10), [])
            self.assertEqual(transcripts._read_jsonl_byte_tail("/no/file", 100, 10), [])


class MemoSemantics(unittest.TestCase):
    """RED on base — reset_transcript_cache / transcript_read_stats are new."""

    def test_reset_and_stats_exist_and_reset_zeroes(self):
        transcripts.reset_transcript_cache()
        st = transcripts.transcript_read_stats()
        self.assertEqual((st["files"], st["bytes"], st["hits"]), (0, 0, 0))

    def test_second_read_is_a_memo_hit(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "m.jsonl")
            _write_lines(p, [_line(i) for i in range(50)])
            transcripts.reset_transcript_cache()
            a = transcripts._iter_jsonl_tail(p, 60)
            s1 = transcripts.transcript_read_stats()
            b = transcripts._iter_jsonl_tail(p, 60)
            s2 = transcripts.transcript_read_stats()
            self.assertEqual(a, b)
            self.assertEqual(s1["files"], 1)
            self.assertEqual(s1["hits"], 0)
            self.assertEqual(s2["files"], 1, "second call must NOT re-read")
            self.assertEqual(s2["hits"], 1, "second call must be a memo hit")
            self.assertGreater(s1["bytes"], 0)

    def test_distinct_max_lines_are_distinct_keys(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "k.jsonl")
            _write_lines(p, [_line(i) for i in range(50)])
            transcripts.reset_transcript_cache()
            transcripts._iter_jsonl_tail(p, 60)
            transcripts._iter_jsonl_tail(p, 200)
            self.assertEqual(transcripts.transcript_read_stats()["files"], 2)

    def test_stale_key_rereads_after_file_grows(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "g.jsonl")
            _write_lines(p, [_line(i) for i in range(10)])
            transcripts.reset_transcript_cache()
            first = transcripts._iter_jsonl_tail(p, 3)
            self.assertEqual([e["i"] for e in first], [7, 8, 9])
            # append -> size + mtime change -> new key -> must re-read fresh
            with open(p, "a") as f:
                f.write(_line(10) + "\n")
            os.utime(p, (time.time() + 5, time.time() + 5))
            second = transcripts._iter_jsonl_tail(p, 3)
            self.assertEqual([e["i"] for e in second], [8, 9, 10],
                             "a changed file must re-read, never serve a stale memo")
            self.assertEqual(transcripts.transcript_read_stats()["files"], 2)

    def test_reset_clears_the_memo(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "c.jsonl")
            _write_lines(p, [_line(i) for i in range(20)])
            transcripts.reset_transcript_cache()
            transcripts._iter_jsonl_tail(p, 60)
            transcripts.reset_transcript_cache()
            transcripts._iter_jsonl_tail(p, 60)
            # after reset, the second read is a fresh read, not a hit
            self.assertEqual(transcripts.transcript_read_stats()["hits"], 0)
            self.assertEqual(transcripts.transcript_read_stats()["files"], 1)

    def test_window_cap_returns_bounded_result_without_whole_read(self):
        # Shrink the cap so a large max_lines cannot be fully satisfied; the
        # reader must return what fits in the cap window (never error, never
        # read the whole file).
        with TemporaryDirectory() as d:
            p = str(Path(d) / "cap.jsonl")
            _write_lines(p, [_line(i, pad=500) for i in range(5000)])
            transcripts.reset_transcript_cache()
            with mock.patch.object(transcripts, "_TAIL_WINDOW_CAP", 4096), \
                 mock.patch.object(transcripts, "_TAIL_WINDOW_START", 4096):
                got = transcripts._iter_jsonl_tail(p, 100000)
            self.assertTrue(got, "cap window must still yield the newest entries")
            # entries are a contiguous newest suffix ending at the last line
            self.assertEqual(got[-1]["i"], 4999)
            self.assertLess(len(got), 5000, "must not have read the whole file")
            st = transcripts.transcript_read_stats()
            self.assertLess(st["bytes"], os.path.getsize(p),
                            "capped read must be far smaller than the file")


@unittest.skipUnless(
    os.environ.get("AIRULESET_PERF_TESTS"),
    "heavy 300 MB perf/memory measurement — set AIRULESET_PERF_TESTS=1 to run "
    "(env-gated per #1055 review F2: writing 300 MB on every full-suite run "
    "churns disk on the CPU/disk-pressured fleet this ticket optimizes; the "
    "always-run BoundedReadIsCheap proof covers the mechanism in CI, and the "
    "before/after cProfile on the controller's real 550 MB transcript is the "
    "live evidence).")
class Measurement(unittest.TestCase):
    """RED on base (opt-in) — the whole-file read is slow + memory-heavy on
    300 MB (first read ~2.3 s + >300 MB peak on base main vs < 300 ms / < 32 MB
    bounded); the definitive perf+memory proof, kept but env-gated."""

    @classmethod
    def setUpClass(cls):
        cls.dir = mkdtemp(prefix="t1055-")
        cls.path = str(Path(cls.dir) / "huge.jsonl")
        target = 300 * 1024 * 1024
        chunk = ("\n".join(_line(i, pad=400) for i in range(1000)) + "\n").encode()
        written = 0
        with open(cls.path, "wb") as f:
            while written < target:
                f.write(chunk)
                written += len(chunk)
        cls.size = os.path.getsize(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_first_read_is_bounded_fast_and_low_memory(self):
        self.assertGreater(self.size, 250 * 1024 * 1024)
        _reset()
        tracemalloc.start()
        t0 = time.perf_counter()
        entries = transcripts._iter_jsonl_tail(self.path, 200)
        dt = time.perf_counter() - t0
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(len(entries), 200)
        self.assertLess(dt, 0.300, "first bounded read took %.3fs on a 300 MB file" % dt)
        self.assertLess(peak, 32 * 1024 * 1024,
                        "peak %d bytes — a whole-file read of 300 MB" % peak)

    def test_second_read_hits_memo_and_is_fast(self):
        _reset()
        transcripts._iter_jsonl_tail(self.path, 200)   # populate
        t0 = time.perf_counter()
        entries = transcripts._iter_jsonl_tail(self.path, 200)
        dt = time.perf_counter() - t0
        self.assertEqual(len(entries), 200)
        self.assertLess(dt, 0.050, "second (memoized) read took %.3fs" % dt)


class BoundedReadIsCheap(unittest.TestCase):
    """Always-run mechanism proof (RED on base: `transcript_read_stats` is new;
    and on base the whole-file read pulls the ENTIRE file so bytes-read == size).
    A ~6 MB transcript read with max_lines=200 must pull only a bounded window,
    far less than the file — this is the cheap CI-safe stand-in for the env-gated
    300 MB Measurement (review F2), while staying decisive against base main."""

    def test_bounded_read_pulls_far_fewer_bytes_than_the_file(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "mid.jsonl")
            _write_lines(p, [_line(i, pad=2000) for i in range(3000)])   # ~6 MB
            size = os.path.getsize(p)
            self.assertGreater(size, 5 * 1024 * 1024)
            transcripts.reset_transcript_cache()
            entries = transcripts._iter_jsonl_tail(p, 200)
            self.assertEqual(len(entries), 200)
            self.assertEqual([e["i"] for e in entries[-3:]], [2997, 2998, 2999])
            read = transcripts.transcript_read_stats()["bytes"]
            self.assertGreater(read, 0)
            self.assertLess(read, size // 4,
                            "bounded read pulled %d of %d bytes — not bounded" % (read, size))

    def test_bounded_read_peak_memory_is_a_fraction_of_the_file(self):
        # A cheap tracemalloc bound: reading the tail of a ~6 MB file must peak
        # well under the file size (base main's whole-file read peaks >= the
        # file). Generous bound (2 MB) vs a 6 MB file keeps it non-flaky.
        with TemporaryDirectory() as d:
            p = str(Path(d) / "mem.jsonl")
            _write_lines(p, [_line(i, pad=2000) for i in range(3000)])   # ~6 MB
            _reset()
            tracemalloc.start()
            entries = transcripts._iter_jsonl_tail(p, 200)
            _cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(len(entries), 200)
            self.assertLess(peak, 2 * 1024 * 1024,
                            "peak %d bytes reading the tail of a 6 MB file" % peak)


class RunOnceJournalSummary(unittest.TestCase):
    """RED on base — run_once emits no `transcript reads:` line."""

    def _drive(self):
        with TemporaryDirectory() as d:
            with mock.patch.object(watchdog, "list_claude_panes", lambda *a, **k: []):
                return list(watchdog.run_once(
                    now=1000.0, dry_run=True,
                    run=lambda *a, **k: "",
                    send_fn=lambda *a, **k: None,
                    projects_dir=Path(d) / "proj",
                    state_path=str(Path(d) / "state.json"),
                ))

    def test_run_once_appends_transcript_reads_summary(self):
        logs = self._drive()
        self.assertTrue(
            any(ln.startswith("transcript reads:") for ln in logs),
            "run_once must journal a per-sweep transcript-read summary line")

    def test_summary_has_files_bytes_and_memo_hits(self):
        logs = self._drive()
        line = next((ln for ln in logs if ln.startswith("transcript reads:")), "")
        self.assertRegex(line, r"transcript reads: \d+ files, \d+ .*read, \d+ memo hits")


if __name__ == "__main__":
    unittest.main()
