"""Durable EXPECTED-ARMED roster (#804) — the structured-state answer to mode 5
(a stream that MUST be running an armed /goal loop but whose session died and
fell entirely off the census, so nothing ever re-detected it — "sam sa vypne a
uz nezapne").

`~/.claude/goal-roster.json` records, per STREAM (keyed by cwd, so it survives a
session-id change AND a reboot):

    {cwd: {"sid": str, "authority": str, "armed_ts": float, "last_seen_ts": float}}

`upsert`/`drop` write only the four fields above. `goal_lane_sweep` ADDS,
per entry, its own census/resurrect bookkeeping on the SAME dict — `census_ts`
(the DEAD-SESSION line's flood-latch) and the mode-5 resurrect anchors `rgts`
(last relaunch-attempt time), `rfails` (consecutive failed attempts, the #805
--continue->fresh escalation) and `ratt` (did the last due-cycle fire a
relaunch) — all cleared when the cwd is live again (a successful resurrect
resets). This module stores/loads them transparently; it never authors them.

LIFECYCLE (as SHIPPED — all driven from `goal_lane_sweep`, no cross-job hooks):
  * WRITE (upsert): a candidate pane whose STRUCTURED one-glance verdict is
    genuinely ARMED this sweep upserts its `{cwd, sid, authority, armed_ts,
    last_seen_ts}` (never a pane-render guess). armed_ts is set once and
    preserved; sid/authority/last_seen refresh (a resurrected session's id
    changes).
  * REMOVE (drop): a DEFINITE goal-clear observed in the sweep (`glance.goal_armed
    is False` -- the stream is no longer expected-armed, whether a user `/goal
    clear` or a self-unarm) drops the entry; so does the explicit CLI
    `airuleset.py goal-roster --drop <cwd>`. A transient armed-UNKNOWN (`None`)
    never drops. When the owner goal kill-switch is set the sweep returns EARLY
    and never touches the roster (entries ride inertly, no census runs) -- they
    re-populate when it is re-enabled. A watchdog GUESS never removes an entry:
    a session that dies WHILE armed leaves its entry, so the DEAD-SESSION census
    keeps surfacing it.

READ side — `goal_lane_sweep`, after its live-candidate pane loop (and only when
that loop was NOT cut short by the sweep budget), calls `dead_entries(roster,
live_cwds)`: every rostered cwd with NO live candidate pane this sweep is a
DEAD-SESSION -- one `one_glance` verdict line per entry (cadenced), so a stream
that is EXPECTED to be armed can never drop off the radar silently again.

This module is PURE data (load/save/upsert/drop/dead_entries) -- the drop
DECISIONS live in `goal_lane_sweep`, not here. It never types a keystroke and
never relaunches anything ITSELF; the mode-5 relaunch ACTION lives in the sibling
`resurrect.py`, driven from the census (it is no longer "a future module"). It
never raises. Module-import safety mirrors `compact.py`/`nudge_gate.py`:
`watchdog/__init__.py` never imports it at module level (callers reach it lazily),
and it needs no `import watchdog`.
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
    """Persist `roster` ATOMICALLY (tmp + os.replace, so a kill mid-write never
    leaves a torn file that `load_roster` would read as `{}` and silently lose
    every DEAD entry). Best-effort — an unwritable ~/.claude / a serialization
    fault never raises, the roster just does not persist this sweep, exactly like
    every other watchdog store. Writes an EMPTY dict on a non-dict argument
    rather than crashing. Returns True iff the write landed."""
    p = roster_path(path)
    tmp = str(p) + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(roster if isinstance(roster, dict) else {}, f)
        os.replace(tmp, p)
        return True
    except (OSError, ValueError, TypeError):
        # A stale `.tmp` after a failed write is harmless (the next successful
        # save's os.replace overwrites the REAL file, never the tmp); remove it
        # best-effort. The caller learns of the failure from the returned False.
        # airuleset:script-ok best-effort tmp cleanup, outcome surfaced via return
        try:
            os.remove(tmp)
        except OSError:
            pass
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
