"""Since-bounded read of `fleet.jsonl` (#1154 part 2).

`fleet.jsonl` is append-only hourly history that is NEVER trimmed, rotated or
rewritten. claudy reads it as its monthly accounting history. Since part 1
added per-host `sessions[]`, each row is about 6 KB, so a whole-file parse on
every hourly watchdog job grows without bound. `read_rows_since()` reads the
file BACKWARDS in fixed-size blocks and stops at the first complete row older
than `since`. Its result is exactly a full read filtered to `ts >= since`, in
file order (rows are appended in time order, so the first older row ends the
window). Only a JSON object with a parseable `ts` is a row. A malformed line,
a non-dict JSON value, or a dict with no or unparsable `ts` is skipped and
never ends the scan.

Per-caller windows are derived here, next to the reader, so each caller stays
a one-line change and the windows are unit-testable:

- `burn_alert_since` — job 19 (`hourly_burn_alert`) reads the newest row plus
  the last `rel_window` same-scope priors. That window is counted in rows
  relative to the NEWEST row, not the wall clock (a months-old newest row is
  still evaluated). So: newest row ts - (rel_window h + 48 h margin for
  collection gaps). A `rel_window <= 0` means "every prior" there, so it
  returns None (full read).
- `compare_since` — `burn --compare` (`compare_changes`) reads only
  `[change - window, change + window)`, so: earliest dated change - window
  - 1 h. With no dated change the result is empty whatever the rows, so it
  reads nothing. A naive change ts returns None: `compare_changes` compares it
  with aware rows, which is today's path, and full-read behaviour is kept.
- Full history (`since=None`) stays for job 16 (`fleet_budget_alert`) and
  `burn --fleet` (`render_fleet`). Both reach `observed_pct_per_day(rows)`,
  which takes the OLDEST weekly sample across all rows. It also stays for
  job 35 (conformance heartbeat), whose `last_fresh` is unbounded on purpose
  (#543 F3).

A sibling of `burn/__init__.py` (at its size-ratchet ceiling). It must never
import `burn`, because `burn` imports FROM here. Stdlib only. It never raises
on file corruption; a naive `since` is a caller bug and raises ValueError.
"""
import contextlib
import datetime
import json

BLOCK_SIZE = 1 << 16
BURN_ALERT_SINCE_MARGIN = datetime.timedelta(hours=48)
COMPARE_SINCE_MARGIN = datetime.timedelta(hours=1)
_READS_NOTHING = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


def _ts_of(row):
    """Epoch seconds of a row's ISO `ts`, or None (not a dict, no `ts`,
    unparsable). Naive stamps read as local time, like the heartbeat does."""
    if not isinstance(row, dict):
        return None
    try:
        return datetime.datetime.fromisoformat(
            str(row.get("ts")).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_line(raw):
    line = raw.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None


def _iter_rows_backwards(path, block_size=BLOCK_SIZE):
    """Yield `(row, epoch)` for each timestamped JSON-object row, newest
    first. It reads `block_size` bytes at a time from the end of the file. A
    line split across blocks is carried until its start is read. Splitting on
    b"\\n" is UTF-8 safe, because 0x0A never occurs inside a multibyte
    sequence. OSError (missing file, a directory) ends the iteration quietly."""
    try:
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
    except OSError:
        return


def read_rows_since(path, since, block_size=BLOCK_SIZE):
    """Rows of `path` with `ts >= since`, in file order (see module doc)."""
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("read_rows_since needs an aware `since`, got %r" % (since,))
    bound = since.timestamp()
    out = []
    with contextlib.closing(_iter_rows_backwards(path, block_size)) as rows:
        for row, t in rows:
            if t < bound:
                break
            out.append(row)
    out.reverse()
    return out


def burn_alert_since(path, rel_window):
    """Job 19's bound: newest row ts - (rel_window h + margin), or None."""
    if isinstance(rel_window, bool) or not isinstance(rel_window, int) or rel_window <= 0:
        return None
    with contextlib.closing(_iter_rows_backwards(path)) as rows:
        newest = next(rows, None)
    if newest is None:
        return None
    return (datetime.datetime.fromtimestamp(newest[1], datetime.timezone.utc)
            - datetime.timedelta(hours=rel_window) - BURN_ALERT_SINCE_MARGIN)


def compare_since(changes, window_hours):
    """`burn --compare`'s bound: earliest dated change - window - margin."""
    stamps = []
    for c in changes or []:
        try:
            t = datetime.datetime.fromisoformat(str(c.get("ts")).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            continue
        if t.tzinfo is None or t.utcoffset() is None:
            return None
        stamps.append(t)
    if not stamps:
        return _READS_NOTHING
    return min(stamps) - datetime.timedelta(hours=window_hours) - COMPARE_SINCE_MARGIN
