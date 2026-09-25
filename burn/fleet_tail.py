"""Since-bounded read of `fleet.jsonl` (#1154 part 2).

`fleet.jsonl` is append-only hourly history. It is NEVER trimmed, rotated or
rewritten, because claudy reads it as monthly accounting history. Since part 1
added per-host `sessions[]`, a row is about 6 KB, so parsing the whole file in
every hourly watchdog job grows without bound.

`read_rows_since()` reads the file BACKWARDS in fixed-size blocks. It stops at
the first complete row older than `since` and returns what a full read
filtered to `ts >= since` returns, in file order. It relies on one writer
appending rows in time order. A "row" is a JSON object with a parseable `ts`.
Malformed lines, non-dict JSON values and dicts with no or an unparsable `ts`
are skipped and never stop the scan. An OSError at any point returns `[]`, the
same as a full read would.

Each caller's window is derived here, next to the reader. That keeps each
caller a one-line change and makes the windows unit-testable:

- `burn_alert_since` — job 19 (`hourly_burn_alert`) reads `rows[-1]`, the last
  `rel_window` priors (for the message) and the last `rel_window` priors that
  share the newest row's `scope` (for the REL median and the weekly step).
  These are COUNTS relative to the newest row, not a wall-clock window, and a
  months-old newest row is still evaluated. A tail scan returns the ts of the
  oldest row those counts reach. The result is exact, with no time margin, so
  a collection gap changes nothing. It returns None (full read) when
  `rel_window <= 0` ("every prior" there) or when the file holds too few rows.
  Documented change: a trailing line with no `ts` used to become `rows[-1]`,
  which silently skipped the hour. Now the real newest row is evaluated.
- `compare_since` — `burn --compare` (`compare_changes`) only reads
  `[change - window, change + window)`. The bound is the earliest dated
  change - window - 1 h, clamped to the newest row so that `if fleet_rows:`
  still prints the "Sada" block exactly as a full read did. With no dated
  change the report is empty whatever the rows, so nothing is read. A naive
  change ts returns None: `compare_changes` already compares it with aware
  rows, so the full-read path is kept.
- Full history (`since=None`) is kept by job 16 (`fleet_budget_alert`) and
  `burn --fleet` (`render_fleet`), because both reach
  `observed_pct_per_day(rows)`, which takes the OLDEST weekly sample across
  ALL rows. It is also kept by job 35 (conformance heartbeat), whose
  `last_fresh` is unbounded on purpose (#543 F3).

This is a sibling of `burn/__init__.py`, which is at its size-ratchet ceiling.
It must never import `burn`, because `burn` imports FROM here. It is stdlib
only. A naive `since` is a caller bug and raises ValueError.
"""
import datetime
import json

BLOCK_SIZE = 1 << 16
COMPARE_SINCE_MARGIN = datetime.timedelta(hours=1)
_READS_NOTHING = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


def _parse_ts(s):
    """ISO-8601 (`Z` accepted) -> datetime, or None. Canonical home (#1154);
    `burn._parse_ts` re-exports it."""
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _ts_of(row):
    """Epoch seconds of a row's `ts`, or None. A naive stamp reads as local
    time, as the heartbeat does."""
    t = _parse_ts(row.get("ts")) if isinstance(row, dict) else None
    try:
        return t.timestamp() if t is not None else None
    except (OverflowError, ValueError):
        return None


def _parse_line(raw):
    line = raw.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None


def _rows_backwards(path, block_size=BLOCK_SIZE):
    """Yield `(row, epoch)` for each timestamped JSON-object row, newest
    first. It reads `block_size` bytes at a time from the end of the file. A
    line split across blocks is carried until its start has been read.
    Splitting on b"\\n" is UTF-8 safe. It raises OSError; callers map that to
    their full-read equivalent."""
    with open(path, "rb") as f:
        pos = f.seek(0, 2)
        carry = b""
        while pos > 0:
            step = min(block_size, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + carry).split(b"\n")
            carry = lines[0] if pos > 0 else b""
            for raw in reversed(lines if pos == 0 else lines[1:]):
                row = _parse_line(raw)
                t = _ts_of(row)
                if t is not None:
                    yield row, t


def _scan(path, stop, block_size=BLOCK_SIZE):
    """Rows (newest first) up to and including the one `stop(row, t)`
    returns True for. None on any OSError."""
    out = []
    rows = _rows_backwards(path, block_size)
    try:
        for row, t in rows:
            out.append((row, t))
            if stop(row, t):
                break
    except OSError:
        return None
    finally:
        rows.close()
    return out


def read_rows_since(path, since, block_size=BLOCK_SIZE):
    """Rows of `path` with `ts >= since`, in file order (see module doc)."""
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("read_rows_since needs an aware `since`, got %r" % (since,))
    bound = since.timestamp()
    got = _scan(path, lambda _row, t: t < bound, block_size) or []
    return [row for row, t in reversed(got) if t >= bound]


def burn_alert_since(path, rel_window):
    """Job 19's exact bound (see module doc), or None for a full read."""
    if isinstance(rel_window, bool) or not isinstance(rel_window, int) or rel_window <= 0:
        return None
    seen = {"n": -1, "same": 0, "scope": None}

    def enough(row, _t):
        seen["n"] += 1
        if seen["n"] == 0:
            seen["scope"] = row.get("scope")
            return False
        seen["same"] += row.get("scope") == seen["scope"]
        return seen["n"] >= rel_window and seen["same"] >= rel_window

    got = _scan(path, enough)
    if not got or seen["n"] < rel_window or seen["same"] < rel_window:
        return None
    return datetime.datetime.fromtimestamp(got[-1][1], datetime.timezone.utc)


def compare_since(changes, window_hours, path=None):
    """`burn --compare`'s bound (see module doc)."""
    stamps = []
    for c in changes or []:
        t = _parse_ts(c.get("ts")) if isinstance(c, dict) else None
        if t is None:
            continue
        if t.tzinfo is None or t.utcoffset() is None:
            return None
        stamps.append(t)
    if not stamps:
        return _READS_NOTHING
    since = min(stamps) - datetime.timedelta(hours=window_hours) - COMPARE_SINCE_MARGIN
    newest = _scan(path, lambda _row, _t: True) if path is not None else None
    if newest:
        since = min(since, datetime.datetime.fromtimestamp(newest[0][1], datetime.timezone.utc))
    return since
