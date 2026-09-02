"""Owner-daily root-level finding surface — the watchdog side of #841 leg C.

The #834 per-USER Job 40 (`watchdog/disk_guard.py`) drains the calling user's
own home and escalates on the MACHINE channel at >= 90 %. The root/system-level
classes it structurally cannot reach (apt cache, docker images, the system
journal, `/var/log`, other users' `/tmp`, a gh-runner `_work`) are ROTATED +
SURFACED by the root leg (#841 leg A/B): a root-owned daily timer writes their
sizes REPORT-ONLY to a world-readable ``/run/airuleset/disk-guard-root.json``.

This module is the consumer of that report on the per-USER watchdog side. It
needs NO root (it only READS the world-readable JSON), and it NEVER pings: the
owner-daily `❓` is raised by a SESSION reading the finding cache this writes —
`notify` is deliberately NOT imported here (the same doctrine as
`disk_guard.py`). When Job 40 is at CRITICAL pressure and the reported
root-level reclaimable estimate crosses a threshold, it:

  * appends a ``disk-guard: root-level candidates`` line to ``disk-guard.log``
    (the #486 decision-log shape, ONCE per fresh daily report — never per poll);
  * writes ``~/.claude/disk-guard/root-candidates.json`` = ``{ts,
    report_generated_at, estimate_bytes, threshold_bytes, candidates, asked_on}``
    that a session reads to raise ONE `❓` per EPISODE. The `asked_on` date is
    STAMPED once and PRESERVED across every poll (#795 retired re-ask — ask
    ONCE, the footer's red ``disk NN%`` is the persistent surface); it is reset
    only when the episode RESOLVES (the estimate drops below threshold and the
    finding cache is cleared) and later recurs — never a daily re-ask.

Fail-open toward SILENCE for the `❓` (an absent/stale/unparseable report → no
finding, no `❓`), but NOT perfectly silent: a stale report while the disk is
under pressure logs a once-a-day WARN to ``disk-guard.log`` so a DEAD root timer
can never be invisible forever.

stdlib-only at module level; the ONE reuse of ``watchdog.disk_guard``'s
log/path helpers is a DEFERRED import inside the functions, so this module has
no import cycle with the package that hosts it, and it imports NOTHING from
``notify``.
"""
import json
import os
import sys
import time
from pathlib import Path

# The world-readable report the root reporter (#841 leg B) writes.
ROOT_REPORT_PATH = "/run/airuleset/disk-guard-root.json"
# Daily cadence + slack: a report older than this is treated as ABSENT so a
# dead root timer never paints a stale finding (mirrors the footer's stale-cache
# HIDE doctrine). Also the freshness bound the session must not ask past.
ROOT_REPORT_STALE_S = 26 * 3600
# The reclaimable estimate that, at CRITICAL pressure, records a finding.
FINDING_THRESHOLD_BYTES = 500 * 1024 * 1024

ROOT_CANDIDATES_NAME = "root-candidates.json"
STALE_WARN_MARKER_NAME = "root-report-stale-warn"


def _dbg(msg):
    try:
        print("  disk-guard-root: " + msg, file=sys.stderr)
    except Exception:
        pass          # airuleset:script-ok stderr itself failing is unrecoverable


def _guard_dir(home=None):
    # Reuse disk_guard's guard-dir (deferred import — cycle-free). Fall back to
    # the literal path if disk_guard is somehow unavailable (never raise).
    try:
        from watchdog import disk_guard
        return disk_guard._guard_dir(home)
    except Exception:
        home = home or os.path.expanduser("~")
        return Path(home) / ".claude" / "disk-guard"


def _candidates_path(home=None):
    return _guard_dir(home) / ROOT_CANDIDATES_NAME


def _log(home, lines):
    """Append to disk-guard.log via disk_guard's self-bounding helper."""
    if not lines:
        return
    try:
        from watchdog import disk_guard
        disk_guard._append_log(disk_guard._log_path(home), lines)
    except Exception as e:
        _dbg("could not write log: %r" % e)


def _log_line(now, action, path, nbytes, reason):
    try:
        from watchdog import disk_guard
        return disk_guard._log_line(now, action, path, nbytes, reason)
    except Exception:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        return "%s %s %s bytes=%s %s" % (ts, action, path, nbytes, reason or "")


def _human(n):
    try:
        from watchdog import disk_guard
        return disk_guard._human(n)
    except Exception:
        return str(n)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def read_root_report(now, report_path=None, read_fn=None):
    """The root reporter's JSON, or None when absent / unparseable / STALE.

    A report whose ``generated_ts`` is older than :data:`ROOT_REPORT_STALE_S`
    (or in the future by more than a small skew) is treated as ABSENT — a dead
    root timer must never paint a stale finding. ``read_fn`` is injectable for
    tests (defaults to reading :data:`ROOT_REPORT_PATH`)."""
    path = report_path or ROOT_REPORT_PATH
    d = (read_fn or _load_json)(path)
    if not isinstance(d, dict):
        return None
    gts = d.get("generated_ts")
    if not isinstance(gts, (int, float)) or isinstance(gts, bool):
        return None
    age = now - gts
    if age > ROOT_REPORT_STALE_S or age < -3600:
        return None          # stale, or a wildly future ts (clock skew) → absent
    return d


def _warn_once_per_day(home, now):
    """A once-a-day stale-report WARN to disk-guard.log (so a dead root timer is
    never invisible). Deduped via a date-stamped marker file. Returns the log
    lines actually written (empty if already warned today)."""
    today = time.strftime("%Y%m%d", time.gmtime(now))
    marker = _guard_dir(home) / STALE_WARN_MARKER_NAME
    try:
        if marker.exists() and marker.read_text().strip() == today:
            return []
    except OSError:
        pass
    line = _log_line(now, "ROOT-REPORT-STALE", ROOT_REPORT_PATH, 0,
                     "root disk-guard report absent/stale while disk critical — "
                     "the root timer may be dead (see #841)")
    _log(home, [line])
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(today)
    except OSError as e:
        _dbg("stale-warn marker write failed: %r" % e)
    return [line]


def maybe_record_root_finding(status, home, now, report_path=None, read_fn=None,
                              threshold=FINDING_THRESHOLD_BYTES, dry_run=False):
    """Called by Job 40 at CRITICAL pressure. Reads the root report and, when
    the reported reclaimable estimate crosses ``threshold``, writes the
    ``root-candidates.json`` finding a SESSION raises the owner-daily `❓` from
    (never a ping here). Fail-open toward silence, but a stale report while
    critical logs a once-a-day WARN. Returns the log lines (also appended to
    disk-guard.log). ``dry_run`` writes nothing."""
    report = read_root_report(now, report_path=report_path, read_fn=read_fn)
    cache_path = _candidates_path(home)
    existing = _load_json(cache_path)
    if report is None:
        # absent / stale / unparseable — no finding, no `❓` (fail-open silent),
        # but WARN once/day so a dead root timer is never invisible.
        if dry_run:
            return []
        return _warn_once_per_day(home, now)

    est = report.get("estimate_bytes", 0) or 0
    gen_at = report.get("generated_at")
    if not isinstance(est, (int, float)) or isinstance(est, bool) or est < threshold:
        # below the threshold — the root side is fine; clear any stale finding
        # so a resolved episode does not keep a session asking.
        if existing and not dry_run:
            try:
                os.unlink(cache_path)
            except OSError as e:
                _dbg("could not clear resolved finding: %r" % e)
        return []

    # A genuine finding. Preserve `asked_on` across writes (a session set it —
    # the once/day dedup must survive the every-poll refresh), and log the
    # decision line ONLY when it is newly crossed OR a fresh daily report
    # arrived (never every 60 s poll).
    prev_gen = existing.get("report_generated_at") if existing else None
    asked_on = existing.get("asked_on") if existing else None
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    finding = {
        "ts": now,
        "report_generated_at": gen_at,
        "estimate_bytes": int(est),
        "threshold_bytes": int(threshold),
        "candidates": candidates,
        "asked_on": asked_on,
    }
    logs = []
    if not dry_run:
        try:
            p = _candidates_path(home)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(finding), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            _dbg("could not write finding cache: %r" % e)
    if existing is None or prev_gen != gen_at:
        summary = ", ".join("%s=%s" % (c.get("cls"), _human(c.get("bytes", 0)))
                            for c in candidates[:6]) or "(none)"
        line = _log_line(now, "ROOT-CANDIDATES", ROOT_REPORT_PATH, int(est),
                         "disk-guard: root-level candidates >= %s (threshold %s) "
                         "— owner-daily ❓ pending: %s"
                         % (_human(est), _human(threshold), summary))
        logs.append(line)
        if not dry_run:
            _log(home, [line])
    return logs


# --------------------------------------------------------------------------- #
# session-facing reader (the owner-daily ❓ raiser reads THIS, not tickets-status)
# --------------------------------------------------------------------------- #
def read_finding(home=None, now=None):
    """The current root-level finding a SESSION should raise the owner-daily `❓`
    from, or None. Returns None when no finding is recorded, when its underlying
    report is STALE (older than :data:`ROOT_REPORT_STALE_S` — never ask on dead
    root data), or when its estimate is below threshold. The dict carries
    ``asked_on`` so the session dedups (ask once, #795); the footer's red
    ``disk NN%`` is the persistent surface after that."""
    now = time.time() if now is None else now
    d = _load_json(_candidates_path(home))
    if not isinstance(d, dict):
        return None
    ts = d.get("ts")
    est = d.get("estimate_bytes", 0) or 0
    thr = d.get("threshold_bytes", FINDING_THRESHOLD_BYTES) or FINDING_THRESHOLD_BYTES
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    # the finding is only as fresh as the last Job-40 write; a Job 40 that
    # stopped running (dead watchdog) leaves this stale → do not ask.
    if (now - ts) > ROOT_REPORT_STALE_S or (now - ts) < -3600:
        return None
    if not isinstance(est, (int, float)) or est < thr:
        return None
    return d


def mark_asked(home=None, now=None, today=None):
    """Record that the owner-daily `❓` was asked (the once-per-EPISODE dedup a
    session sets after raising the question — #795 no re-ask; preserved until
    the episode resolves). Stores today's date as the stamp. Best-effort;
    returns the date written or None on failure."""
    now = time.time() if now is None else now
    today = today or time.strftime("%Y%m%d", time.gmtime(now))
    p = _candidates_path(home)
    d = _load_json(p)
    if not isinstance(d, dict):
        return None
    d["asked_on"] = today
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, p)
        return today
    except OSError as e:
        _dbg("could not mark asked: %r" % e)
        return None
