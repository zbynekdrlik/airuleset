"""Shared per-session / per-category nudge CADENCE GATE (#797).

Two problems this fixes, both reported by the owner:

1. **Footer `U` goes stale.** Since #795 retired the daily question re-ask, the
   footer `U N` is the owner's ONLY question surface, so a phantom `U` (a
   `needs-answer`/`needs-decision` label or question-map entry a session forgot
   to clear after the question was answered/obsoleted) directly LIES to him. The
   new `u_freshness` rider forces an armed session to re-audit its own U members —
   but the owner's hard contract is "raz za hodinu skontroluje… NIE častejšie ako
   raz za hodinu". This gate is where that 1×/hour STROP lives (floor-clamped so
   an env units-error can never lower it).

2. **Nudges arrive in bursts ("chodia jak besne po sebe").** Every job-20
   keystroke rider (partition-audit / release-gap / queue-arrival / lane-occupancy)
   keeps its OWN ad-hoc timer namespace. The per-sweep `handled` set bounds
   delivery to one keystroke per pane per SWEEP, but sweeps are 60s apart — three
   different categories can land into the SAME session minutes after each other
   across sweeps, and no cross-sweep per-session floor exists anywhere. This gate
   adds a FAMILY-SPACING floor (`NUDGE_FAMILY_GAP_S`) that every rider consults, so
   a SECOND category's keystroke defers to a later sweep when a DIFFERENT category
   nudged this session recently.

DESIGN — a pure helper over ONE new state namespace, no new I/O, no new job:

  state["nudge_cadence"] = {sid: {category: last_delivered_ts}}

persisted in the ONE existing `~/.claude/api-watchdog-state.json` (run_once's
`state`). `gate_ok(state, sid, category, now)` returns True iff BOTH hold:

  (a) PER-CATEGORY FLOOR — at least `_category_floor(category)` since THIS
      category's last DELIVERED nudge to this sid. Only `u-freshness` carries a
      non-zero floor (the owner's `_u_cadence()` strop); the four existing
      categories carry floor 0 (their OWN cadences already exceed any floor, so
      the gate is a pure ADDITIONAL no-op floor for them — their steady-state
      semantics are UNCHANGED).
  (b) FAMILY SPACING — at least `_family_gap()` since ANY OTHER gated-family
      category's last DELIVERED nudge to this sid. The current category is
      EXCLUDED from this check on purpose: a rider's own back-to-back cadence is
      governed solely by its own last_nudge + (a), so the gate NEVER changes a
      rider's own semantics — it only spaces DISTINCT categories.

`mark_sent` is written ONLY on a VERIFIED delivered send (a swallowed send never
advances the clock — the #714 MAX_SEND_FAILS retry bound stays each rider's storm
limiter, unchanged). `prune` is the standard #519/#531 orphan reaper shape
(visited_sids PRIMARY, a TTL SECONDARY). Fail-safe: a malformed/corrupt gate entry
reads as "no prior nudge" → `gate_ok` ALLOWS (the safe direction — never SUPPRESS a
legit nudge; u-freshness additionally has its own last_nudge backstop in the rider,
so it can't burst even on a corrupt gate).
"""
import os

# The gated keystroke-rider family. Jobs 8/11 (bounce / gk-request backstops) are
# deliberately OUT — a different lane (idle-pane queue backstops with their own
# staged schedules), not footer/partition nudges into an armed loop.
GATED_CATEGORIES = frozenset({
    "u-freshness", "partition-audit", "release-gap", "queue-arrival",
    "lane-occupancy", "goal-guard",
})

# The owner's hard 1×/hour U-reconcile strop. Env AIRULESET_U_RECONCILE_CADENCE_S
# can only RAISE it (floor-clamped at U_RECONCILE_CADENCE_MIN_S == the strop) —
# the #504/#543 floor-clamp lesson: a units-error / accidental sub-hour value
# must never turn the reconcile into spam. This is the ONLY per-category floor.
U_RECONCILE_CADENCE_S = 3600
U_RECONCILE_CADENCE_MIN_S = 3600

# The cross-category family spacing: consecutive-sweep deliveries of DIFFERENT
# categories to the same session are spaced at least this far apart, so "besne po
# sebe" ends. Env AIRULESET_NUDGE_FAMILY_GAP_S, floored at NUDGE_FAMILY_GAP_MIN_S.
NUDGE_FAMILY_GAP_S = 15 * 60
NUDGE_FAMILY_GAP_MIN_S = 5 * 60

# orphan-reaper TTL for a per-sid cadence rec whose session is gone (mirrors the
# #519/#531 per-sid-leak reaper): the `visited_sids` gate is PRIMARY (a live pane
# is never reaped regardless of age), this is only the SECONDARY safety for a
# budget-deferred pane.
NUDGE_CADENCE_ORPHAN_TTL_S = 24 * 3600


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _u_cadence():
    """The effective U-reconcile floor: the env override, floored at
    U_RECONCILE_CADENCE_MIN_S so a units-error / accidental sub-hour value can
    never lower the owner's hard 1×/hour strop (#504/#543). Env can only RAISE."""
    return max(_env_int("AIRULESET_U_RECONCILE_CADENCE_S", U_RECONCILE_CADENCE_S),
               U_RECONCILE_CADENCE_MIN_S)


def _family_gap():
    """The effective family spacing, floored at NUDGE_FAMILY_GAP_MIN_S so a units
    error can't collapse it back toward a per-sweep re-nudge (#504/#543)."""
    return max(_env_int("AIRULESET_NUDGE_FAMILY_GAP_S", NUDGE_FAMILY_GAP_S),
               NUDGE_FAMILY_GAP_MIN_S)


GOAL_GUARD_FLOOR_S = 24 * 3600


def _category_floor(category):
    """The per-category floor: the owner's `_u_cadence()` strop for `u-freshness`,
    24h for `goal-guard` (#878 — at most 1 nudge/24h per session), 0 for every
    other gated category (their own cadences govern)."""
    if category == "u-freshness":
        return _u_cadence()
    if category == "goal-guard":
        return GOAL_GUARD_FLOOR_S
    return 0


def _session(state, sid):
    """The `{category: ts}` map for `sid`, or an empty dict when absent/malformed
    (the fail-safe read: a corrupt entry reads as 'no prior nudge')."""
    if not isinstance(state, dict):
        return {}
    cad = state.get("nudge_cadence")
    if not isinstance(cad, dict):
        return {}
    sess = cad.get(sid)
    return sess if isinstance(sess, dict) else {}


def _ts(v):
    """A numeric timestamp or None (a bool is not a timestamp — the JSON-boundary
    guard the sibling riders use)."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _gate_ts(v, now):
    """A numeric timestamp that is NOT in the future, else None. A ts strictly
    greater than `now` cannot be a real 'last delivered' time (you can't have
    nudged in the future) — it is a corrupt/huge value or cross-clock skew, so it
    is IGNORED (treated as no-prior-nudge → the gate ALLOWS). This is the module's
    own fail-safe promise (docstring: 'a malformed/corrupt gate entry reads as no
    prior nudge → gate_ok ALLOWS … never SUPPRESS a legit nudge') applied to the
    NUMERIC-skew class, not only the non-numeric one. It genuinely self-heals: the
    corrupt entry is ignored, the gate allows, and the next real `mark_sent`
    overwrites it with `now`. NOT a clamp-to-`now` (which would make `now - ts ==
    0 < floor` → defer FOREVER, since a re-read future ts re-clamps to `now` every
    call — a permanent mute of u-freshness, the owner's ONLY question surface, the
    exact worst direction). `_stale_entry` keeps a future ts as fresh (its own safe
    direction, #519), so this stays local to the gate decision. Within the
    single-box watchdog `now` is monotone and a real ts is always ≤ `now`, so a
    future ts never arises from normal operation — ignoring it only affects the
    genuinely-corrupt case, where ALLOW is correct (a duplicate nudge is far less
    harmful than a permanent mute; #752 treats future-skew as a real class)."""
    ts = _ts(v)
    return None if ts is None or ts > now else ts


def gate_ok(state, sid, category, now):
    """True iff a nudge of `category` to `sid` is allowed at `now` — see the
    module docstring for (a) the per-category floor and (b) the family spacing.
    Fail-safe ALLOWS on any malformed state (never suppress a legit nudge) —
    including a FUTURE-skewed / corrupt-huge numeric ts, which `_gate_ts` ignores
    so it can never mute a session indefinitely."""
    sess = _session(state, sid)
    # (a) per-category floor — only u-freshness carries a non-zero one.
    last_cat = _gate_ts(sess.get(category), now)
    if last_cat is not None and now - last_cat < _category_floor(category):
        return False
    # (b) family spacing — any OTHER gated-family category within the gap defers.
    gap = _family_gap()
    for cat, raw in sess.items():
        if cat == category:
            continue
        ts = _gate_ts(raw, now)
        if ts is not None and now - ts < gap:
            return False
    return True


def mark_sent(state, sid, category, now):
    """Record a VERIFIED delivered nudge of `category` to `sid` at `now`. Called
    ONLY on a confirmed/delivered-unconfirmed send (a swallowed send must not
    advance the clock — each rider's own MAX_SEND_FAILS bound stays the storm
    limiter). Never raises on a pre-existing malformed namespace — it is replaced
    with a fresh dict for this sid rather than crashing the sweep."""
    if not isinstance(state, dict):
        return
    cad = state.get("nudge_cadence")
    if not isinstance(cad, dict):
        cad = {}
        state["nudge_cadence"] = cad
    sess = cad.get(sid)
    if not isinstance(sess, dict):
        sess = {}
        cad[sid] = sess
    sess[category] = now


def _stale_entry(v, now, ttl_s):
    """True iff a per-sid cadence entry is reapable by AGE (the secondary gate):
    malformed, OR every recorded ts older than `ttl_s`. A future ts (clock skew)
    counts as fresh (the safe direction, #519)."""
    if not isinstance(v, dict) or not v:
        return True
    for raw in v.values():
        ts = _ts(raw)
        if ts is not None and (now - ts) < ttl_s:
            return False
    return True


def prune(state, visited_sids, now, ttl_s=NUDGE_CADENCE_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["nudge_cadence"]` (keyed on
    `sid = tpath.stem`). Reap ONLY when BOTH: (1) the sid was NOT a live candidate
    pane THIS sweep (`visited_sids` — session gone/superseded), AND (2) its entry
    is stale by `_stale_entry`. The visited gate is PRIMARY: a live pane is never
    reaped regardless of age. Never raises. Faithful mirror of the sibling riders'
    orphan reapers."""
    if not isinstance(state, dict):
        return
    cad = state.get("nudge_cadence")
    if not isinstance(cad, dict):
        return
    for sid in [k for k, v in list(cad.items())
                if k not in visited_sids and _stale_entry(v, now, ttl_s)]:
        cad.pop(sid, None)
