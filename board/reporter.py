"""Autopilot Board reporter client — fire-and-forget, never blocks, exits 0.

A worker calls these helpers (via `airuleset.py report`) to mint a run_id once,
emit monotonic-seq events, and POST them to the board. Network is best-effort:
a hard REPORT_TIMEOUT, a circuit breaker, and a flock-guarded offline queue keep
the worker's own progress from ever stalling on the board being down.

stdlib only.
"""
import os
import re
import time
import uuid
import json
import fcntl
import urllib.request

from board import (REPORT_TIMEOUT, CIRCUIT_BREAKER_S, FLUSH_CAP, QUEUE_TTL_S,
                   QUEUE_MAX_BYTES, board_url)

STATE_DIR = os.path.expanduser("~/.claude")
BOARD_URL = board_url()


def _p(name):
    return os.path.join(STATE_DIR, name)


def _safe_prefix(repo):
    """`owner/name` -> `owner_name` for the run_id prefix only.

    The raw repo string is still sent verbatim in the event payload; only the
    id prefix is sanitised so the run_id stays a single filesystem-safe token.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", repo)


def _load(f):
    try:
        with open(f) as h:
            return json.load(h)
    except Exception:
        return {}


def _ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def _save(f, d):
    _ensure_state_dir()
    with open(f, "w") as h:
        json.dump(d, h)


def start_run(repo, issue, title, is_bug_fix=False, has_deploy=False,
              merge_mode="auto"):
    """Mint a run_id once, persist the repo#issue -> run_id mapping, seed seq=0,
    and emit the opening 'validating' event. Returns the run_id."""
    rid = f"{_safe_prefix(repo)}-{issue}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}"
    runs = _load(_p("autopilot-board-runs.json"))
    runs[f"{repo}#{issue}"] = rid
    _save(_p("autopilot-board-runs.json"), runs)
    _save(_p(f"autopilot-board-seq-{rid}.json"), {"seq": 0})
    report(rid, phase="validating", repo=repo, issue=issue, title=title,
           is_bug_fix=is_bug_fix, has_deploy=has_deploy, merge_mode=merge_mode,
           _start=True)
    return rid


def current_run(repo, issue):
    """Return the persisted run_id for repo#issue (or None) — lets a later
    invocation reuse the same run rather than minting a fresh one."""
    return _load(_p("autopilot-board-runs.json")).get(f"{repo}#{issue}")


def next_seq(rid):
    """Monotonic per-run sequence number, persisted across invocations."""
    f = _p(f"autopilot-board-seq-{rid}.json")
    d = _load(f)
    d["seq"] = d.get("seq", 0) + 1
    _save(f, d)
    return d["seq"]


# --- secret scrub --------------------------------------------------------
# Matched on the value, not the key — anything that smells like a credential is
# redacted before it can land on the board (where it would render in HTML).
_SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]+"
    r"|github_pat_[A-Za-z0-9_]+"
    r"|AKIA[0-9A-Z]+"
    r"|xox[a-z]-[A-Za-z0-9-]+"
    r"|-----BEGIN[^\n]*"
    r"|Bearer\s+[A-Za-z0-9._-]+)")


def scrub(s):
    """Redact obvious secrets, collapse to a single line, cap length. Applied to
    every free-text field (goal/approach/result/note/title) before it leaves the
    worker."""
    if not s:
        return s
    return _SECRET_RE.sub("[redacted]", str(s))[:2000].replace("\n", " ").strip()


# --- network + circuit breaker -------------------------------------------
def _token():
    try:
        with open(_p("autopilot-board.token")) as h:
            return h.read().strip()
    except Exception:
        return ""


def _down_recently():
    """True while the circuit breaker is open — a recent failure stamp means
    skip the network for CIRCUIT_BREAKER_S and only queue."""
    try:
        ts = float(open(_p("autopilot-board-down")).read().strip())
        return (time.time() - ts) < CIRCUIT_BREAKER_S
    except Exception:
        return False


def _mark_down():
    try:
        with open(_p("autopilot-board-down"), "w") as h:
            h.write(str(time.time()))
    except OSError:
        pass


def _clear_down():
    try:
        os.remove(_p("autopilot-board-down"))
    except OSError:
        pass


def _post_one(body):
    """POST a single event to the board. Returns True on HTTP 2xx, False on any
    error (timeout, connection refused, non-2xx, bad URL, non-serialisable body).
    Never raises."""
    try:
        req = urllib.request.Request(
            BOARD_URL.rstrip("/") + "/report",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     "X-Board-Token": _token()})
        with urllib.request.urlopen(req, timeout=REPORT_TIMEOUT) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# --- emit + queue --------------------------------------------------------
def report(rid, phase=None, _start=False, reviews=None, **fields):
    """Build one event for `rid`, durably queue it, then best-effort flush.
    Fire-and-forget: never blocks past REPORT_TIMEOUT, never raises."""
    try:
        for k in ("goal", "approach", "result", "note", "title"):
            if k in fields:
                fields[k] = scrub(fields[k])
        ev = {"run_id": rid, "event_id": uuid.uuid4().hex, "seq": next_seq(rid),
              "phase": phase, "event_ts": time.time(),
              "machine": os.uname().nodename}
        ev.update({k: v for k, v in fields.items() if v is not None})
        if reviews:
            ev["reviews"] = reviews     # [(check, state), ...]
        _enqueue_and_flush(ev)
    except Exception:
        pass


def _enqueue_and_flush(ev):
    # append first (durability), then enforce size cap, then try to flush under lock
    _ensure_state_dir()
    qf = _p("autopilot-board-queue.jsonl")
    with open(qf, "a") as h:
        h.write(json.dumps(ev) + "\n")
    # enforce QUEUE_MAX_BYTES: trim oldest lines under flock so we don't race
    _trim_queue(qf)
    if _down_recently():
        return  # circuit breaker open — skip the network, leave it queued
    flush_queue()


def _trim_queue(qf):
    """If the queue file exceeds QUEUE_MAX_BYTES, drop oldest lines (keep newest)."""
    try:
        if os.path.getsize(qf) <= QUEUE_MAX_BYTES:
            return
        h = open(qf, "r+")
        try:
            fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            h.close()
            return
        try:
            data = h.read()
            if len(data.encode()) <= QUEUE_MAX_BYTES:
                return
            lines = data.splitlines(keepends=True)
            # drop oldest lines until we're under the cap
            while lines and len("".join(lines).encode()) > QUEUE_MAX_BYTES:
                lines.pop(0)
            h.seek(0)
            h.truncate()
            h.writelines(lines)
        finally:
            fcntl.flock(h, fcntl.LOCK_UN)
            h.close()
    except Exception:
        pass


def flush_queue():
    """Drain the offline queue to the board under an exclusive non-blocking lock.

    - Lock held by another reporter → return immediately (we already appended).
    - Remove a line only on HTTP 2xx; on the first failure stop, keep the
      remainder, open the circuit breaker, and return (caller exits 0).
    - At most FLUSH_CAP sends per invocation; events older than QUEUE_TTL_S are
      dropped; a poison (bad-JSON) line is skipped and dropped, never crashes.
    """
    qf = _p("autopilot-board-queue.jsonl")
    if not os.path.exists(qf):
        return
    try:
        h = open(qf, "r+")
        try:
            fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            h.close()
            return  # another reporter owns the queue — skip the flush
    except OSError:
        return  # couldn't open the file at all
    try:
        lines = h.readlines()
        remaining = []
        sent = 0
        now = time.time()
        for i, ln in enumerate(lines):
            if sent >= FLUSH_CAP:
                remaining.append(ln)
                continue
            try:
                body = json.loads(ln)
            except Exception:
                continue  # poison line: skip + drop
            if now - body.get("event_ts", now) > QUEUE_TTL_S:
                continue  # TTL drop
            if _post_one(body):
                sent += 1
                _clear_down()
            else:
                _mark_down()
                remaining.append(ln)
                remaining.extend(lines[i + 1:])
                break
        h.seek(0)
        h.truncate()
        h.writelines(remaining)
    finally:
        fcntl.flock(h, fcntl.LOCK_UN)
        h.close()
