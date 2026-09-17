"""gates.audit -- ONE bypass-token audit writer for the gate family (#1020).

Every gate in this family logs a one-line audit entry when a bypass token is
honored (``# airuleset:secret-ok`` on staging, ``# airuleset:test-skip-ok`` /
``[no-test:]`` on a push, ``[no-design:]`` on a commit, ``scope-gate-ok`` on a
filing). Before this module each hook re-implemented the same "mkdir -p the
audits dir, append a timestamped line" mechanism inline. This module holds that
mechanism ONCE; each adapter still composes its OWN line text so the per-hook
log FORMATS are byte-for-byte unchanged (existing log-parsing tools/tests keep
working).

This file provides the WRITER (``append_line`` + ``iso_now`` + ``project_of``)
the migrated adapters need, AND (#1020 Part 2 item 3) the ``--bypasses`` READER
extension: ``count_cli_bypasses`` teaches scripts/audit_bounce_rule_updates.py to
cover these per-hook CLI-token audit logs (the ``audits/*.log`` bypass family +
the ``/tmp/airuleset-main-exec-bypass-<uid>.log`` family), which
``count_bypass_tokens`` (commit messages) is blind to -- so a bypass that never
enters git history (``# airuleset:secret-ok`` on a ``git add``,
``# airuleset:scope-gate-ok`` on a filing) is finally counted.
"""
import datetime
import glob
import os
import re
import subprocess


def audits_dir():
    """The fleet's fixed audit directory (~/devel/airuleset/audits) -- the same
    absolute path every hook in this family already wrote to, deliberately NOT
    relative to a worktree so a dispatched worker's bypass lands in the one
    place the metric reads."""
    return os.path.join(os.path.expanduser("~"), "devel", "airuleset", "audits")


def audit_log_path(name):
    """Absolute path of the audit log file ``name`` (e.g.
    "secret-scan-bypasses.log")."""
    return os.path.join(audits_dir(), name)


def iso_now():
    """Local-timezone ISO-8601 seconds timestamp, matching ``date -Iseconds``
    (e.g. ``2026-09-14T20:00:31+02:00``) -- the exact stamp the bash hooks
    produced."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def project_of(cwd=None):
    """The basename of the git top-level for ``cwd`` (process cwd when None),
    or "unknown" -- matching ``basename $(git rev-parse --show-toplevel ||
    echo unknown)``. Never raises."""
    args = ["git"]
    if cwd:
        args += ["-C", cwd]
    args += ["rev-parse", "--show-toplevel"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    top = (r.stdout or "").strip()
    if r.returncode != 0 or not top:
        return "unknown"
    return os.path.basename(top)


def append_line(log_name, line):
    """Append one already-composed audit ``line`` to the audit log ``log_name``,
    best-effort: mkdir -p the audits dir first, never raise (an unwritable
    ~/devel just means the audit entry is lost, never that the gate fails).
    Returns True on success. The caller owns the line's FORMAT so per-hook logs
    stay byte-for-byte identical."""
    path = audit_log_path(log_name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# #1020 Part 2 item 3 -- the --bypasses READER over per-hook CLI-token audit
# logs. Registry of the KNOWN bypass audit logs (basename under audits_dir), each
# with the substring markers that identify a NON-bypass line (an event the log
# also records but that is NOT an honored bypass -- tier0 records `blocked` real
# blocks in the SAME file). An empty marker list means every dated line in that
# log is a bypass (the pure bypass logs). Enumerated EXPLICITLY (not a blanket
# *.log glob) so a non-bypass .log in the same dir is never miscounted -- the
# writers were read to fix the exact filenames + line shapes.
# --------------------------------------------------------------------------- #
CLI_BYPASS_LOGS = (
    ("secret-scan-bypasses.log", ()),
    # no-test-skips.log ALSO carries the #1003 AUTO-exemption `docs-only fix
    # commit — exempt from RED-order gate` (gates/pushtest.py) -- an automatic
    # gate exemption, NOT a human bypass; skip it.
    ("no-test-skips.log", ("from RED-order gate",)),
    # test-skip-bypasses.log ALSO carries the #1003 merge-in AUTO-exclusion
    # `merge-in banned line(s) excluded from scan` (gates/testskips.py); skip it.
    ("test-skip-bypasses.log", ("merge-in banned line(s) excluded from scan",)),
    # no-design-skips.log ALSO carries the #1003 AUTO-exemption `merge-commit
    # exempt from design gate` (gates/commitdesign.py) -- fires on every
    # dev->main merge with issue refs, NOT a human bypass; skip it.
    ("no-design-skips.log", ("merge-commit exempt from design gate",)),
    ("history-rewrite-bypasses.log", ()),
    ("destructive-remote-bypasses.log", ()),
    ("manual-drain-bypasses.log", ()),
    ("prod-write-budget-bypasses.log", ()),
    ("stream-merge-bypasses.log", ()),
    ("subdev-ssh-bypasses.log", ()),
    ("vault-store-reads.log", ()),
    ("script-check-bypasses.log", ()),
    # tier0 logs `blocked` (a real block) in the SAME file as `inline-bypass`/
    # `env-bypass`. The block tag is always immediately followed by the `cmd=`
    # field (`  blocked  cmd=...`), so `  blocked  cmd=` precisely excludes the
    # blocks WITHOUT false-skipping an inline-bypass whose OWN cmd= text happens
    # to contain the word "blocked" (review 🔵).
    ("tier0-build-bypasses.log", ("  blocked  cmd=",)),
)

# The /tmp per-uid main-exec bypass log family (block-main-implementation.sh).
# `main-exec bypass refused ...` is the NON-bypass shape (a marker with no
# reason, cleared); `main-exec bypass session=... (allowed ...)` is the honored
# bypass. #732 relocates it via AIRULESET_MAIN_EXEC_LOG_DIR, but the default
# /tmp location is what an audit run reads.
_MAIN_EXEC_GLOB = "airuleset-main-exec-bypass-*.log"
# The main-exec log has FOUR line shapes across TWO writers; count only the
# honored-bypass event (the `(allowed ...)` line), skip the rest:
#   block-main-implementation.sh:615  `(allowed, deferred consume pending post-exec)`  COUNT
#   block-main-implementation.sh:619  `(allowed, pending-write FAILED, consumed in PreToolUse)`  COUNT
#   block-main-implementation.sh:626  `bypass refused ... (no reason, cleared)`  SKIP
#   block-main-implementation.sh:434  `bypass-arm ...` (preparatory)  SKIP
#   post-consume-main-exec-marker.sh:135  `(consumed, post-exec)`  SKIP (the 2nd
#     line of the deferred pair -- skipping it is what prevents a 2x double-count,
#     review 🟡 #2). The marker is the EXACT `consumed, post-exec` phrase, NOT a
#     broad `(consumed` -- line 619's `consumed in PreToolUse` is a genuine
#     honored bypass and must stay counted.
_MAIN_EXEC_SKIP = ("bypass refused", "bypass-arm", "consumed, post-exec")
_MAIN_EXEC_UID_RE = re.compile(r"airuleset-main-exec-bypass-(\S+?)\.log$")

_BYPASS_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_BYPASS_PROJECT_RE = re.compile(r"\bproject=(\S+)")
_BYPASS_CWD_RE = re.compile(r"\bcwd=(\S+)")
_BYPASS_TOKEN_RE = re.compile(r"airuleset:[a-z-]*-ok")


def _bypass_box_of(line, default):
    """The box/repo a CLI-bypass line belongs to: `project=<name>` if present,
    else the basename of a `cwd=<path>` field, else `default`."""
    m = _BYPASS_PROJECT_RE.search(line)
    if m:
        return m.group(1)
    m = _BYPASS_CWD_RE.search(line)
    if m:
        return os.path.basename(m.group(1).rstrip("/")) or default
    return default


def _iter_bypass_lines(path, skip_markers, default_box, since_day):
    """Yield (day, box, kind) for each genuine honored-bypass line in `path`:
    a leading ISO date (>= since_day) and NONE of `skip_markers` present. `kind`
    is the `airuleset:<x>-ok` token when the line carries one, else a synthetic
    `cli:<logbasename>` label. Never raises (a missing/unreadable log yields
    nothing)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    label = "cli:" + os.path.basename(path).rsplit(".log", 1)[0]
    for line in lines:
        dm = _BYPASS_DATE_RE.match(line)
        if not dm:
            continue
        day = dm.group(1)
        if since_day and day < since_day:
            continue
        if any(mk in line for mk in skip_markers):
            continue
        tok = _BYPASS_TOKEN_RE.search(line)
        kind = tok.group(0) if tok else label
        yield day, _bypass_box_of(line, default_box), kind


def count_cli_bypasses(root=None, tmp_dir="/tmp", window_days=7, now=None):
    """Count honored CLI-token bypasses across the per-hook audit logs, per day,
    kind and box, within the last `window_days` LOCAL days. Returns
    ``{"per_day": {YYYY-MM-DD: n}, "per_kind": {kind: n}, "per_box": {box: n},
    "total": n}`` -- days/kinds/boxes with zero hits are absent. `root` defaults
    to the fleet audits dir; `tmp_dir` holds the main-exec log family. Never
    raises -- a missing dir/log is simply zero (this is an observability view,
    never a gate)."""
    root = root or audits_dir()
    _now = now or datetime.datetime.now().astimezone()
    since_day = (_now - datetime.timedelta(days=window_days)).strftime("%Y-%m-%d")
    per_day, per_kind, per_box, total = {}, {}, {}, 0

    sources = [(os.path.join(root, name), skip, name.rsplit(".log", 1)[0])
               for name, skip in CLI_BYPASS_LOGS]
    for mp in sorted(glob.glob(os.path.join(tmp_dir, _MAIN_EXEC_GLOB))):
        um = _MAIN_EXEC_UID_RE.search(mp)
        box = "main-exec-" + um.group(1) if um else "main-exec"
        sources.append((mp, _MAIN_EXEC_SKIP, box))

    for path, skip, default_box in sources:
        for day, box, kind in _iter_bypass_lines(path, skip, default_box, since_day):
            per_day[day] = per_day.get(day, 0) + 1
            per_kind[kind] = per_kind.get(kind, 0) + 1
            per_box[box] = per_box.get(box, 0) + 1
            total += 1
    return {"per_day": per_day, "per_kind": per_kind, "per_box": per_box,
            "total": total}


# --------------------------------------------------------------------------- #
# #1049 -- the self-service-gate BLOCK reader. Sibling of count_cli_bypasses,
# but over the block-recurrence Prevencia log (~/.claude/selfservice-gate.log,
# written by gates.selfservice) rather than the honored-bypass audit logs.
# scripts/audit_bounce_rule_updates.py exposes it as `--selfservice-blocks` so a
# stream that keeps escalating a self-serviceable PROD read is counted per
# stream, per reason-code, per day (the #957 24h-recurrence discipline). A PASS/
# NOTFILED line is NOT a recurrence and is skipped; only genuine BLOCKs count.
# --------------------------------------------------------------------------- #
_SS_STREAM_RE = re.compile(r"\bstream=(\S+)")
_SS_REASON_RE = re.compile(r"\breason=(\S+)")


def selfservice_log_path():
    """The Prevencia log location (~/.claude/selfservice-gate.log) -- the same
    path gates.selfservice._log_path writes to."""
    return os.path.join(os.path.expanduser("~"), ".claude", "selfservice-gate.log")


def count_selfservice_blocks(path=None, window_days=7, now=None):
    """Count self-service-gate BLOCKs per stream, reason-code and day within the
    last `window_days` LOCAL days. Returns ``{"per_stream": {...}, "per_reason":
    {...}, "per_day": {...}, "total": n}`` -- zero-hit keys absent. Never raises
    (observability view, never a gate): a missing/unreadable log is simply zero.
    Only `verdict=BLOCK` lines count; PASS/NOTFILED are not recurrences."""
    path = path or selfservice_log_path()
    _now = now or datetime.datetime.now().astimezone()
    since_day = (_now - datetime.timedelta(days=window_days)).strftime("%Y-%m-%d")
    per_stream, per_reason, per_day, total = {}, {}, {}, 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        # no log yet -> zero recurrences
        return {"per_stream": {}, "per_reason": {}, "per_day": {}, "total": 0}
    for line in lines:
        dm = _BYPASS_DATE_RE.match(line)
        if not dm:
            continue
        day = dm.group(1)
        if day < since_day:
            continue
        if "verdict=BLOCK" not in line:
            continue
        sm = _SS_STREAM_RE.search(line)
        rm = _SS_REASON_RE.search(line)
        stream = sm.group(1) if sm else "unknown"
        reason = rm.group(1) if rm else "unknown"
        per_stream[stream] = per_stream.get(stream, 0) + 1
        per_reason[reason] = per_reason.get(reason, 0) + 1
        per_day[day] = per_day.get(day, 0) + 1
        total += 1
    return {"per_stream": per_stream, "per_reason": per_reason,
            "per_day": per_day, "total": total}


# --------------------------------------------------------------------------- #
# #1056 L2 (i) -- the --label-flips reader: blind label flips per stream per
# day, target 0. Two sources, both "a returned bounce was re-flagged with no
# real response": (1) the L1 blind-label-flip GATE log
# (~/.claude/labeledit-gate.log, written by gates.labeledit._write_log as
# "<ISO> <stream> <verdict> <reason> <ticket>" -- a BLOCK = a flip attempt
# blocked at the stream box), and (2) the (h) gk-side REVERT automat's notes
# (audits/labeledit-reverts.log, written by cross_stream._apply_bounce_flip_
# revert as "<ISO> stream=<s> repo=<r> ticket=<N> verdict=<id> ids=<...>" -- a
# flip that got through and was reverted). A rising per-stream count = a stream
# still re-flagging returned bounces; trend it to 0 (the #957 discipline).
# --------------------------------------------------------------------------- #
def labeledit_gate_log_path():
    """The blind-label-flip gate log (~/.claude/labeledit-gate.log) -- the same
    path gates.labeledit._log_path writes to."""
    return os.path.join(os.path.expanduser("~"), ".claude", "labeledit-gate.log")


def _read_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def count_label_flips(gate_log_path=None, revert_log_path=None, window_days=7,
                      now=None):
    """Count blind label flips per stream, day and source within the last
    `window_days` LOCAL days. Returns ``{"per_stream": {...}, "per_day": {...},
    "per_source": {"gate-block": n, "revert": n}, "total": n}`` -- zero-hit keys
    absent. Never raises (observability view, never a gate): a missing/
    unreadable log is simply zero. `gate_log_path` defaults to the box-local
    blind-label-flip gate log; `revert_log_path` to the fleet audits dir's
    revert-notes log."""
    gate_log_path = gate_log_path or labeledit_gate_log_path()
    revert_log_path = revert_log_path or audit_log_path(
        "labeledit-reverts.log")
    _now = now or datetime.datetime.now().astimezone()
    since_day = (_now - datetime.timedelta(days=window_days)).strftime(
        "%Y-%m-%d")
    per_stream, per_day, per_source, total = {}, {}, {}, 0

    def _tally(day, stream, source):
        per_stream[stream] = per_stream.get(stream, 0) + 1
        per_day[day] = per_day.get(day, 0) + 1
        per_source[source] = per_source.get(source, 0) + 1

    # Source 1: gate BLOCK lines (space-separated fields, no `key=` form).
    for line in _read_lines(gate_log_path):
        dm = _BYPASS_DATE_RE.match(line)
        if not dm or dm.group(1) < since_day:
            continue
        parts = line.split()
        # <ISO> <stream> <verdict> <reason> <ticket>
        if len(parts) < 3 or parts[2] != "BLOCK":
            continue
        _tally(dm.group(1), parts[1] or "unknown", "gate-block")
        total += 1

    # Source 2: revert-note lines (`stream=<s>` field).
    for line in _read_lines(revert_log_path):
        dm = _BYPASS_DATE_RE.match(line)
        if not dm or dm.group(1) < since_day:
            continue
        sm = _SS_STREAM_RE.search(line)
        _tally(dm.group(1), sm.group(1) if sm else "unknown", "revert")
        total += 1

    return {"per_stream": per_stream, "per_day": per_day,
            "per_source": per_source, "total": total}
