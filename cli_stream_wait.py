"""cli_stream_wait -- `airuleset.py stream-wait`, the ONE idle waiter a sub-dev
STREAM `/goal` loop keeps live instead of ending on an empty slice (#1128).

Root cause it serves: the reduced-authority (fork-no-merge / branch-merge)
`/goal` templates used to carry a (B) SLICE EMPTY stop, so a stream whose own
`I` was 0 while the gatekeeper held its hand-off ACHIEVED and the session sat
idle (david1-4, 2026-09-23) -- a later gk bounce, client reply or new stream
ticket then waited for the owner. The owner ruling: a stream loop has NO
backlog-empty end. When nothing is dispatchable it keeps exactly one background
`stream-wait` live and ends the turn. The design premise (the #733 gk incident:
an armed loop with a live background waiter sat idle at the prompt) is that
Claude Code defers /goal evaluation while a background task runs, so an idle
stream costs one turn per wake, not one per evaluation.

What it does: poll the stream's OWN slice through the SAME search
`slice-quals` runs (`_slice_quals` quals unioned by `_union_open_issues` over
`AUTOPILOT_SKIP_EXCL` -- never a parallel derivation, #367/#391), fingerprint
each open ticket as (number, updatedAt, sorted label names), and exit 0 on the
FIRST fingerprint change (printing what changed) or after `--max` seconds as a
heartbeat. `updatedAt` moves on every comment, label change and reopen, so a gk
verdict, a bounce, a client reply and a new stream ticket all wake it. The last
fingerprint seen is PERSISTED per repo (`STATE_DIR`), so a change that landed
while no waiter ran (the loop was working, or between its slice check and this
launch) wakes the FIRST poll instead of being absorbed into a fresh baseline;
the price is one extra wake after the loop's own writes.

Failure posture: a gh error, an unresolvable identity or any exception inside
a poll is NEVER a change -- it is counted and retried next poll; a gh-rate
backoff (#1040/#1067, read from the CACHED status only, no gh call) skips the
poll. A full-authority box has no stream slice and is refused (exit 2) before
any wait starts. A waiter killed from outside (a memory-pressure reap, a
compaction) exits silently -- the loop's own liveness checks relaunch it, not
this module. Pure stdlib; the wait loop takes injected
fetch/clock/sleep/backoff/baseline/record seams so tests drive it with fakes.
"""

import json
import os
import sys
import time

DEFAULT_INTERVAL_S = 300
DEFAULT_MAX_S = 3600
# The fingerprint fields -- `_union_open_issues(fields=...)` fetches exactly
# these through the slice search (the ETag snapshot already carries updatedAt).
FIELDS = ("number", "updatedAt", "labels")
STATE_DIR = os.path.expanduser("~/.claude/stream-wait")


class NotStreamBox(Exception):
    """This box resolves to FULL authority -- it has no stream slice to watch."""


def fingerprint(rows):
    """{number: (updatedAt, sorted label names)} for a `{number: row}` map.
    Only the fingerprint fields count; title and other fields never do."""
    fp = {}
    for n, r in (rows or {}).items():
        names = tuple(sorted((lb or {}).get("name") or ""
                             for lb in (r.get("labels") or [])))
        fp[n] = (r.get("updatedAt"), names)
    return fp


def describe_change(old, new):
    """Human lines naming what moved between two fingerprints (never empty
    when they differ): new tickets, tickets that left the slice, and tickets
    whose labels (`+x`/`-y`) or updatedAt (a comment / reopen) changed."""
    lines = []
    for n in sorted(set(new) - set(old)):
        lines.append("#%s new in the slice" % n)
    for n in sorted(set(old) - set(new)):
        lines.append("#%s left the slice (closed or relabelled)" % n)
    for n in sorted(set(old) & set(new)):
        if old[n] == new[n]:
            continue
        added = sorted(set(new[n][1]) - set(old[n][1]))
        removed = sorted(set(old[n][1]) - set(new[n][1]))
        delta = ["+" + x for x in added] + ["-" + x for x in removed]
        lines.append("#%s updated%s" % (n, (" (" + " ".join(delta) + ")")
                                        if delta else " (new activity)"))
    return lines


def state_path(root):
    """The per-repo persisted-fingerprint file (keyed like the footer cache)."""
    import statusbar
    return os.path.join(STATE_DIR, "%s.json" % statusbar.cwd_key(root))


def load_baseline(path):
    """The persisted fingerprint, or None when missing / unreadable (a fresh
    first-poll baseline is then the fail-safe)."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh).get("fp")
        return {int(n): (v[0], tuple(v[1])) for n, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        return None


def save_baseline(path, fp):
    """Persist `fp` atomically; a failed write only costs the next launch its
    persisted baseline (logged, never raised)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(),
                       "fp": {str(n): [u, list(lb)] for n, (u, lb) in fp.items()}},
                      fh)
        os.replace(tmp, path)
    except OSError as exc:
        sys.stderr.write("stream-wait: baseline not saved (%s)\n" % exc)


def check_stream_box():
    """Raise NotStreamBox on a FULL-authority box (the same refusal
    `slice-quals` makes, #181 C1); return the repo root otherwise."""
    import airuleset
    root = airuleset._repo_root() or None
    if airuleset.resolve_authority(cwd=root) == "full":
        raise NotStreamBox("this box resolves to FULL authority -- there is "
                           "no stream slice to wait on")
    return root


def fetch_slice(root=None, quals=None):
    """(rows, None) for this stream's open, non-skip slice, or (None, err) on
    ANY gh failure / unresolvable identity -- never an empty slice on error."""
    import airuleset
    if root is None:
        root = check_stream_box()
    if quals is None:
        try:
            quals = airuleset._slice_quals(airuleset._current_user(), cwd=root)
        except airuleset.SliceUnresolved as exc:
            return None, str(exc)
    rows, failed = airuleset._union_open_issues(
        quals, airuleset.AUTOPILOT_SKIP_EXCL, cwd=root, fields=FIELDS)
    if failed:
        return None, "a slice query failed (gh error)"
    return rows, None


def make_fetch(root):
    """A poll function that resolves the slice quals ONCE (on the first
    success) -- the own-account quals cost a `gh api user` call each time."""
    import airuleset
    cache = {}

    def fetch():
        if "quals" not in cache:
            try:
                cache["quals"] = airuleset._slice_quals(
                    airuleset._current_user(), cwd=root)
            except airuleset.SliceUnresolved as exc:
                return None, str(exc)
        return fetch_slice(root, cache["quals"])
    return fetch


def _default_backoff():
    """The cached gh-rate backoff (seconds) -- the probe never spends a gh
    call itself (the ops_wait_refresh pattern). Fail-open -> 0."""
    try:
        import cli_gh_rate
        return cli_gh_rate.current_gh_backoff(status=cli_gh_rate._load_cache())
    except Exception as exc:  # noqa: BLE001 -- a probe failure never blocks a poll
        sys.stderr.write("stream-wait: gh-rate probe failed (%s)\n"
                         % type(exc).__name__)
        return 0


def _poll(fetch):
    """One fetch; ANY exception is a poll error (never kills the waiter --
    an instantly-dead waiter would wake the loop into a relaunch spin)."""
    try:
        return fetch()
    except Exception as exc:  # noqa: BLE001 -- the no-change fail-safe
        return None, "%s: %s" % (type(exc).__name__, exc)


def wait_for_change(fetch, interval, max_s, now_fn=time.monotonic,
                    sleep_fn=time.sleep, backoff_fn=_default_backoff,
                    baseline=None, record_fn=None):
    """Poll `fetch` every `interval` s until its fingerprint differs from
    `baseline` (the persisted one; else the first successful poll), or `max_s`
    s elapse. `record_fn(fp)` sees every successful poll. Returns
    `(verdict, lines, stats)`: verdict "changed" (lines = what moved) or
    "heartbeat" (lines = []); stats counts polls / errors / rate holds.
    Never sleeps past the deadline; an error or a rate hold is no change."""
    if interval <= 0 or max_s <= 0:
        raise ValueError("stream-wait: --interval and --max must be > 0")
    deadline = now_fn() + max_s
    stats = {"polls": 0, "errors": 0, "holds": 0}
    while True:
        if backoff_fn() > 0:
            stats["holds"] += 1
        else:
            stats["polls"] += 1
            rows, err = _poll(fetch)
            if err or rows is None:
                stats["errors"] += 1
                sys.stderr.write("stream-wait: poll error, no change (%s)\n"
                                 % (err or "no rows"))
            else:
                fp = fingerprint(rows)
                if record_fn is not None:
                    record_fn(fp)
                if baseline is None:
                    baseline = fp
                elif fp != baseline:
                    return "changed", describe_change(baseline, fp), stats
        remaining = deadline - now_fn()
        if remaining <= 0:
            return "heartbeat", [], stats
        sleep_fn(min(interval, remaining))


def run(args):
    """The `stream-wait` command body -> exit code (0 changed/heartbeat,
    2 refused: a full-authority box or invalid bounds)."""
    interval = getattr(args, "interval", DEFAULT_INTERVAL_S)
    max_s = getattr(args, "max", DEFAULT_MAX_S)
    try:
        root = check_stream_box()
        path = state_path(root or os.getcwd())
        verdict, lines, stats = wait_for_change(
            make_fetch(root), interval, max_s, baseline=load_baseline(path),
            record_fn=lambda fp: save_baseline(path, fp))
    except (NotStreamBox, ValueError) as exc:
        print("stream-wait: %s" % exc, file=sys.stderr)
        return 2
    tally = "%d polls, %d gh errors, %d rate holds" % (
        stats["polls"], stats["errors"], stats["holds"])
    if verdict == "changed":
        print("stream-wait: CHANGED (%s) -- re-check `slice-quals` and work "
              "what arrived:" % tally)
        for ln in lines:
            print("  " + ln)
    else:
        print("stream-wait: HEARTBEAT -- no change in %ss (%s); relaunch "
              "stream-wait if nothing is dispatchable" % (max_s, tally))
    return 0
