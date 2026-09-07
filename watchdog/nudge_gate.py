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
   nudged this session recently. #913 (owner directive 2026-09-06): the gap was
   raised from 15 min to 1 h — "nikdy viac ako raz za hodinu!!!! a ani iny nudge
   do promptu!!!" — so the cross-family spacing now equals the u-freshness
   per-category strop.

DESIGN — a pure helper over ONE new state namespace, no new I/O, no new job:

  state["nudge_cadence"] = {sid: {category: last_delivered_ts}}

persisted in the ONE existing `~/.claude/api-watchdog-state.json` (run_once's
`state`). `gate_ok(state, sid, category, now)` returns True iff BOTH hold:

  (a) PER-CATEGORY FLOOR — at least `_category_floor(category)` since THIS
      category's last DELIVERED nudge to this sid. `u-freshness` carries the
      owner's `_u_cadence()` strop, `goal-guard` carries a 24 h floor (#878);
      the other categories carry floor 0 (their OWN cadences govern).
  (b) FAMILY SPACING — at least `_family_gap()` since ANY OTHER gated-family
      category's last DELIVERED nudge to this sid. The current category is
      EXCLUDED from this check on purpose: a rider's own back-to-back cadence is
      governed solely by its own last_nudge + (a), so the gate NEVER changes a
      rider's own semantics — it only spaces DISTINCT categories.

#923 BATCHING (owner ROZHODNUTÉ): instead of individual delivery, ALL eligible
families compose into ONE combined prompt per 1h slot. `batch_eligible()` returns
the ordered list (WORK_DRIVING first, AUDIT second); `compose_batch()` formats them
into a single `BATCH_PREFIX`-headed message; `mark_batch_sent()` stamps all at once.
The classification (WORK_DRIVING vs AUDIT) determines section ORDER within the batch
and the TRIM ORDER when the batch exceeds max_chars (audit trimmed first).

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
#
# #923 CLASSIFICATION — families split into two classes:
#
#   WORK_DRIVING — families whose nudge directly DRIVES new work output (spawning
#   workers, starting releases, processing arrivals, reconciling lost lanes).
#   These have PRIORITY within the shared 1h slot.
#
#   AUDIT — families that CHECK state but don't drive new work (footer partition
#   correctness, U badge freshness, foreign goal condition). These DEFER to a
#   due work-driving candidate, so the work motor is never starved.
#
# Every new gated category MUST be added to exactly ONE of these frozensets.
WORK_DRIVING_CATEGORIES = frozenset({
    "lane-occupancy", "release-gap", "queue-arrival", "lane-reconcile",
})

AUDIT_CATEGORIES = frozenset({
    "partition-audit", "u-freshness", "goal-guard",
})

GATED_CATEGORIES = WORK_DRIVING_CATEGORIES | AUDIT_CATEGORIES

# The owner's hard 1×/hour U-reconcile strop. Env AIRULESET_U_RECONCILE_CADENCE_S
# can only RAISE it (floor-clamped at U_RECONCILE_CADENCE_MIN_S == the strop) —
# the #504/#543 floor-clamp lesson: a units-error / accidental sub-hour value
# must never turn the reconcile into spam. This is the ONLY per-category floor.
U_RECONCILE_CADENCE_S = 3600
U_RECONCILE_CADENCE_MIN_S = 3600

# The cross-category family spacing: consecutive-sweep deliveries of DIFFERENT
# categories to the same session are spaced at least this far apart, so "besne po
# sebe" ends. #913 (owner directive 2026-09-06): raised from 15 min to 1 h — no
# watchdog nudge into any session prompt more often than 1x/hour TOTAL.
# Env AIRULESET_NUDGE_FAMILY_GAP_S, floored at NUDGE_FAMILY_GAP_MIN_S.
NUDGE_FAMILY_GAP_S = 60 * 60
NUDGE_FAMILY_GAP_MIN_S = 60 * 60

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
    """The effective family spacing, floored at NUDGE_FAMILY_GAP_MIN_S (1 h since
    #913) so a units error can't lower it below the owner's hard 1x/hour strop
    (#504/#543)."""
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
    Used by individual riders for per-category eligibility; for batched delivery
    (#923 ROZHODNUTÉ) use `batch_eligible()` which collects ALL eligible categories.
    Fail-safe ALLOWS on any malformed state (never suppress a legit nudge) —
    including a FUTURE-skewed / corrupt-huge numeric ts, which `_gate_ts` ignores
    so it can never mute a session indefinitely."""
    sess = _session(state, sid)
    # (a) per-category floor — u-freshness (1h) and goal-guard (24h).
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


# --------------------------------------------------------------------------- #
# #923 BATCHING — compose all eligible families into ONE prompt per 1h slot.
# --------------------------------------------------------------------------- #

# The batch message leads with this prefix — a recognized machine-nudge prefix
# in goal.py `_machine_prefixes` ("nudge:") AND must be added to stash.py
# `_JANITOR_OWN_PREFIXES` for stranded-nudge cleanup.
BATCH_PREFIX = "nudge:"

# The max-chars hard cap for a BATCHED delivery (#923). Individual riders cap
# their own text at 700; the batch adds a prefix + per-section headers, so the
# batch cap must be > 700 to fit at least one full rider section. Set to 1400
# (two full riders). send_verified's _type_literal chunk-typing handles texts
# up to several KB (CC's input box accepts them) — the limit is readability.
BATCH_MAX_CHARS = 1400


def batch_eligible(state, sid, now):
    """Return the list of categories eligible for batched delivery at `now`.

    A category is eligible when BOTH hold:
      (1) The session's family gap is OPEN — no mark_sent of ANY category
          within `_family_gap()` (the 1h total cap, #913).
      (2) The category's own per-category floor has expired.

    Returns categories ordered: WORK_DRIVING first, AUDIT second (#923).
    Returns [] when the gap is closed or no category is eligible.
    Fail-safe: malformed state → [] (the safe direction for batching — no
    batch, the individual riders' own gates still work)."""
    sess = _session(state, sid)
    gap = _family_gap()
    # (1) Is the gap open? Any category sent within the gap → closed.
    for cat, raw in sess.items():
        ts = _gate_ts(raw, now)
        if ts is not None and now - ts < gap:
            return []
    # (2) Collect categories whose per-category floor has expired.
    eligible = []
    for cat in sorted(GATED_CATEGORIES):  # sorted for determinism
        last_cat = _gate_ts(sess.get(cat), now)
        if last_cat is not None and now - last_cat < _category_floor(cat):
            continue
        eligible.append(cat)
    # Order: work-driving first, audit second (each sub-group sorted).
    wd = sorted(c for c in eligible if c in WORK_DRIVING_CATEGORIES)
    au = sorted(c for c in eligible if c in AUDIT_CATEGORIES)
    return wd + au


def compose_batch(items, max_chars=None):
    """Compose a batch message from `[(category, text), ...]`.

    Orders WORK_DRIVING sections first, AUDIT second. Each section is formatted
    as `[category] text`. The message leads with `BATCH_PREFIX` so machine-prefix
    recognition classifies it as a machine nudge.

    When `max_chars` is given and the composed message exceeds it, AUDIT sections
    are trimmed from the end first (the priority taxonomy's trim order), then
    WORK_DRIVING from the end — work-driving is never trimmed while audit
    sections remain.

    Returns `(composed_text, included_categories)` — a 2-tuple so callers know
    which families survived trimming and can mark only those (#923 caller-refactor).
    Empty items returns `('', [])`."""
    if not items:
        return "", []
    wd = [(c, t) for c, t in items if c in WORK_DRIVING_CATEGORIES]
    au = [(c, t) for c, t in items if c in AUDIT_CATEGORIES]

    def _build(sections):
        parts = [BATCH_PREFIX]
        for cat, text in sections:
            parts.append(" [%s] %s" % (cat, text))
        return "".join(parts)

    result = _build(wd + au)
    if max_chars is not None and len(result) > max_chars:
        # Trim audit from the end, then work-driving if still over.
        while au and len(result) > max_chars:
            au.pop()
            result = _build(wd + au)
        while wd and len(result) > max_chars:
            wd.pop()
            result = _build(wd + au)
    included = [c for c, _ in wd + au]
    if not included:
        return "", []   # every section trimmed — nothing to deliver
    return result, included


def mark_batch_sent(state, sid, categories, now):
    """Mark ALL `categories` as sent at `now` in one call.

    Used after a batched delivery: every category in the batch gets the SAME
    timestamp, so the family gap blocks the NEXT batch (not the members of
    THIS one). Delegates to `mark_sent` per category."""
    for cat in categories:
        mark_sent(state, sid, cat, now)


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
