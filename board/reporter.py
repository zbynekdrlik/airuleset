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

STATE_DIR = os.path.expanduser("~/.claude")


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


def _save(f, d):
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


def report(*a, **k):  # temporary stub — replaced by the real body in Task 8
    pass
