"""watchdog.resource_guard — Job 39: VERIFY-ONLY shared-stream guardrail check (#775).

The standing backstop for the #775 resource guardrails (`cli_resource_guards`).
On a SHARED-STREAM box (subdev), it reads this account's OWN cgroup memory ceiling
(`/sys/fs/cgroup/user.slice/user-<uid>.slice/memory.max`, world-readable — no root
needed) and, if it is still UNLIMITED (`max`), surfaces LOUD that the box runs
without guardrails — one journal line + one deduped gk-request per ~day.

DELIBERATELY VERIFY-ONLY — NO killer logic (#486: no new thousand-line heuristic).
The deterministic intervention is the kernel cgroup-OOM killer that `MemoryMax`
arms; Job 37 (#776 ugrep) and Job 38 (#778 heavy-build) already reap the two
known runaway signatures. This job only checks that the mechanical ceiling is in
place and shouts when it is not.

FAIL-SAFE TOWARD SILENCE (#539 bias — never a false alarm):
  * Off a shared-stream box (dev1/dev2/gk, or an unreadable box-class marker) →
    total no-op, exactly like Job 38's box-class gate.
  * cgroup file missing / unreadable / empty → NOTHING (we cannot prove the guard
    is absent, so we never alarm).
  * A finite `memory.max` (a real ceiling) → NOTHING (the guard is present).
  * Only a definitively UNLIMITED (`max`) ceiling on a shared-stream box alarms,
    and even then at most ~1/day (marker-file dedup).

Never a Discord ping — machine-channel only (#546): the returned journal lines
and a deduped gk-request (the SAME `needs-gatekeeper` hand-off lane the gatekeeper
already drains), never `notify.send()`.
"""
import os
import time

from watchdog.reaper import is_shared_stream_box  # reuse the #778 box-class seam


# This account's own memory ceiling, readable without root on cgroup v2.
CGROUP_MEMORY_MAX_FMT = "/sys/fs/cgroup/user.slice/user-{uid}.slice/memory.max"

# Dedup: at most one alert (journal line + gk-request) per this window. A bit
# under 24h so a roughly-daily cadence never skips a day; the watchdog polls
# every 60s, so without this the alert would spam once per cycle.
DEDUP_INTERVAL_S = 23 * 60 * 60

# The stamp file whose mtime throttles the alert.
DEFAULT_MARKER_PATH = "~/.claude/resource-guard-alert.stamp"

# The gk-request destination — this feature's own tracking ticket, so the
# gatekeeper sees "subdev still runs without guardrails" on the SAME hand-off
# lane it already drains, deduped, never a fresh ticket per day per stream.
TRACKING_ISSUE = 775
TRACKING_REPO = "zbynekdrlik/airuleset"


def _default_cgroup_read(uid):
    """Read this account's cgroup `memory.max` (the stripped first line), or None
    on any error / missing file. Fail-safe: an unreadable ceiling is treated as
    'cannot prove the guard is absent' → the caller stays silent."""
    try:
        with open(CGROUP_MEMORY_MAX_FMT.format(uid=uid)) as fh:
            return fh.readline().strip() or None
    except (OSError, ValueError):
        return None


def _marker_fresh(marker_path, now, interval_s):
    """True iff the dedup stamp exists and its recorded alert time is within
    `interval_s` of `now`. The stamp's CONTENT (the `int(now)` written by
    `_touch_marker`) is the timestamp — NOT the file mtime — so the dedup is
    judged against the SAME clock the caller passes `now` from (real wall clock
    in production, a synthetic timeline in tests; the #727 real-mtime-vs-
    synthetic-now hazard is thereby avoided). Any read/parse error → treat as
    NOT fresh (re-alert): the alert only fires once the cgroup is CONFIRMED
    unlimited, so re-alerting on an unreadable/garbage stamp is the correct
    direction, never a false alarm."""
    try:
        with open(marker_path) as fh:
            stamped = float(fh.readline().strip())
        return (now - stamped) < interval_s
    except (OSError, ValueError):
        return False


def _touch_marker(marker_path, now):
    """Best-effort: write `now` into the stamp file (create dir if needed).
    Returns None on success, or a short error string on failure so the caller can
    LOG it (never a silent swallow — script-failure-policy.md). A write failure is
    non-fatal: the worst case is a duplicate alert next cycle."""
    try:
        d = os.path.dirname(marker_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(marker_path, "w") as fh:
            fh.write("%d\n" % int(now))
        return None
    except OSError as e:
        return "dedup-marker write failed (%r)" % (e,)


def resource_guard_verify(uid=None, cgroup_read=None, box_class_fn=None,
                          gk_request_fn=None, marker_path=None, now=None,
                          dry_run=False, dedup_interval_s=DEDUP_INTERVAL_S):
    """Verify this account's shared-stream resource guardrail is in place (#775).

    Returns the journal log lines (possibly empty). Fires an alert (journal line
    + one gk-request via `gk_request_fn`) ONLY when: this IS a shared-stream box,
    the cgroup `memory.max` is definitively `max` (unlimited), AND the dedup
    marker is stale/absent. Every other case returns [] (silence).

    Seams (all injectable for tests): `uid` (default os.getuid), `cgroup_read`
    (default reads the real cgroup file), `box_class_fn` (default reads the
    box-class marker), `gk_request_fn(uid)` (the real filer; None = journal-only,
    the unwired-seam shape), `marker_path`, `now`, `dry_run` (writes/files
    nothing)."""
    # Box-class gate FIRST — a non-shared-stream box never reads cgroup or alarms.
    if not is_shared_stream_box(box_class_fn):
        return []
    if uid is None:
        uid = os.getuid()
    if cgroup_read is None:
        cgroup_read = _default_cgroup_read
    try:
        mem_max = cgroup_read(uid)
    except Exception:  # noqa: BLE001 — a read error is silence, never a crash
        mem_max = None
    # Unreadable / missing → cannot prove the guard is absent → silence.
    if mem_max is None:
        return []
    # A finite ceiling is set → the guard is present → silence.
    if mem_max != "max":
        return []

    # Definitively UNLIMITED on a shared-stream box → alert (deduped ~1/day).
    now = now if now is not None else time.time()
    marker = os.path.expanduser(marker_path or DEFAULT_MARKER_PATH)
    if _marker_fresh(marker, now, dedup_interval_s):
        return []  # already alerted within the window → deduped silence

    line = ("resource-guard-verify: shared-stream box user-%s.slice memory.max=max "
            "(UNLIMITED) — NO #775 resource guardrails applied; the box is exposed "
            "to a single-stream OOM collapse (authorize the root@subdev operator "
            "key + run `airuleset.py push`)." % uid)
    logs = [line]
    if dry_run:
        return logs  # dry-run: report what it WOULD do, write nothing, file nothing

    marker_err = _touch_marker(marker, now)
    if marker_err:
        logs.append("resource-guard-verify: " + marker_err)
    if gk_request_fn is None:
        logs.append("resource-guard-verify: gk_request_fn not wired — journal only")
        return logs
    try:
        gk_request_fn(uid)
    except Exception as e:  # noqa: BLE001 — best-effort, never a crash
        logs.append("resource-guard-verify: gk-request error: %r" % (e,))
    return logs
