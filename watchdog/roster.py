"""Durable EXPECTED-ARMED roster (#804) — the structured-state answer to mode 5
(a stream that MUST be running an armed /goal loop but whose session died and
fell entirely off the census, so nothing ever re-detected it — "sam sa vypne a
uz nezapne").

`~/.claude/goal-roster.json` records, per STREAM (keyed by cwd, so it survives a
session-id change AND a reboot):

    {cwd: {"sid": str, "authority": str, "armed_ts": float, "last_seen_ts": float}}

WRITE side — ONLY from STRUCTURED events (never a pane-render guess):
  * a successful goal-arm delivery (`deliver_goal` "sent")            -> upsert
  * dark-watch reading the persisted `Goal set:` marker for a cwd     -> upsert
An entry is REMOVED only by an EXPLICIT event:
  * a user `/goal clear` (`clear_kind == "user"`, the #170 boundary)  -> drop
  * the owner goal kill-switch (`_owner_disabled("goal")`)            -> drop
  * the CLI `airuleset.py goal-roster --drop <cwd>`                   -> drop
A watchdog GUESS never removes an entry — the whole point is that a silently-dead
session STAYS in the roster so the DEAD-SESSION census keeps surfacing it.

READ side — `goal_lane_sweep`, after its live-candidate pane loop, calls
`dead_entries(roster, live_cwds)`: every rostered cwd with NO live candidate pane
this sweep is a DEAD-SESSION — one `one_glance` verdict line per entry, so a
stream that is EXPECTED to be armed can never drop off the radar silently again.

This module is PURE data + a bounded orphan reaper. It never types a keystroke,
never relaunches anything (that is `resurrect.py`), never raises. Module-import
safety mirrors `compact.py`/`nudge_gate.py`: `watchdog/__init__.py` never imports
it at module level (callers reach it lazily), and it needs no `import watchdog`.
"""
import json
import os
from pathlib import Path


def roster_path(path=None):
    """The durable roster file, resolved at CALL time. Precedence: an explicit
    `path` arg (unit tests); then the `AIRULESET_GOAL_ROSTER_PATH` env seam (the
    conftest/dual-runner isolation convention every sibling store follows, so a
    test that triggers a real save never pollutes the developer's ~/.claude); then
    `~/.claude/goal-roster.json` (Path.home() read fresh, never a frozen import-
    time constant)."""
    if path:
        return Path(path)
    env = os.environ.get("AIRULESET_GOAL_ROSTER_PATH")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "goal-roster.json"


def load_roster(path=None):
    """`{cwd: {...}}` — the durable expected-armed register. `{}` on any error or
    missing file; a non-dict top level or a non-dict entry is dropped defensively
    (the #741 well-formedness lesson: a store reader must match the consumer's
    guard so a corrupt entry can never latch). Never raises."""
    p = roster_path(path)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)}


def save_roster(roster, path=None):
    """Persist `roster` to `~/.claude/goal-roster.json` (best-effort — an
    unwritable ~/.claude never raises, the roster just does not persist this
    sweep, exactly like every other watchdog store). Writes an EMPTY dict on a
    non-dict argument rather than crashing the sweep."""
    p = roster_path(path)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(roster if isinstance(roster, dict) else {}, f)
        return True
    except OSError:
        return False


def upsert(roster, cwd, sid, authority, now):
    """Record/refresh the expected-armed entry for stream `cwd` from a STRUCTURED
    arm event. `armed_ts` is set on FIRST insertion and preserved on later
    refreshes (the #400 anchor discipline — a re-observation is not a re-arm);
    `sid`/`authority`/`last_seen_ts` update every time (the session id is
    refreshed on resurrection). No-op on a falsy cwd. Mutates `roster` in place."""
    if not isinstance(roster, dict) or not cwd:
        return
    entry = roster.get(cwd)
    if not isinstance(entry, dict):
        entry = {"armed_ts": now}
        roster[cwd] = entry
    entry.setdefault("armed_ts", now)
    entry["sid"] = sid
    entry["authority"] = authority
    entry["last_seen_ts"] = now


def drop(roster, cwd):
    """Remove stream `cwd` from the roster (an EXPLICIT deprovision event only —
    user clear / owner-disable / CLI drop). Returns True iff an entry was present.
    Never raises."""
    if not isinstance(roster, dict):
        return False
    return roster.pop(cwd, None) is not None


def dead_entries(roster, live_cwds):
    """Every rostered `(cwd, entry)` whose cwd is NOT among `live_cwds` (the set
    of cwds with a live armed candidate pane THIS sweep) — the DEAD-SESSION set.
    First-seen order (`dict` iteration is insertion-ordered); empty when the
    roster is empty or every stream is live. A pure read — never mutates, never
    raises. `live_cwds` may be any container supporting `in`."""
    if not isinstance(roster, dict):
        return []
    out = []
    for cwd, entry in roster.items():
        if not isinstance(entry, dict):
            continue
        if cwd in live_cwds:
            continue
        out.append((cwd, entry))
    return out
