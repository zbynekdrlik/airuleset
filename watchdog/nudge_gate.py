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

2. **Nudges arrive too often ("chodia jak besne po sebe").** Every job-20
   keystroke rider (partition-audit / release-gap / queue-arrival / lane-occupancy)
   keeps its OWN ad-hoc timer namespace. The per-sweep `handled` set bounds
   delivery to one keystroke per pane per SWEEP, but sweeps are 60s apart, so a
   kind whose per-JOB floor was below one hour (queue-arrival's 30-min floor) could
   re-fire the SAME kind into the same session inside the hour — the owner's #1023
   regression ("takyto nudge by nemal chodit castejsie nez raz za hodinu!"). This
   gate is where the owner's "raz za hodinu" rule lives, in TWO complementary
   bounds (both owner directives, restored honestly after the #1023 fix-forward
   integration finding of 2026-09-14):

     - PER-KIND FLOOR (#1023, owner 2026-09-14) — a per-pane-per-KIND 60-min
       floor (`NUDGE_MIN_INTERVAL_S`): a SECOND delivery of the SAME kind defers
       until an hour after the last SEND (a keystroke DELIVERED to the pane —
       transcript-confirmed OR delivered-unconfirmed; #1023 reopen).
     - CROSS-KIND TOTAL CAP (#913, owner 2026-09-06, verbatim: "nikdy viac ako
       raz za hodinu!!!! a ani iny nudge do promptu!!!") — at most ONE PRIORITY
       nudge per pane per hour in TOTAL, across ALL kinds (`NUDGE_TOTAL_GAP_S`).
       The #1023 lane DELETED this on the inference "per-kind staging bounds the
       total" — but that is not the owner's word: with two kinds staged on, two
       nudges per hour reach one pane (a kind whose condition isn't met at the
       shared batch delivers later in the hour). So the cap is RESTORED, under a
       NEW name (the old `NUDGE_FAMILY_GAP_S`/`_family_gap` stay deleted).

   RECOVERY identities (`RECOVERY_NUDGE_KINDS` = resume/compact) are EXEMPT from
   BOTH bounds and never count as "another kind delivered" for the cap — a
   revival into a dead/blocked session is not a prompt interruption.

DESIGN — a pure helper over ONE state namespace, no new I/O, no new job:

  state["nudge_cadence"] = {sid: {category: last_delivered_ts}}

persisted in the ONE existing `~/.claude/api-watchdog-state.json` (run_once's
`state`). `gate_ok(state, sid, category, now)` returns True iff a recovery kind,
OR BOTH bounds hold (checked via the shared `_total_cap_block` predicate that
`batch_eligible` also uses — one gate, two sites):

  PER-KIND FLOOR (#1023) — at least `_category_floor(category)` since THIS kind's
  last DELIVERED nudge to this sid. EVERY gated kind carries `_min_interval()`
  (>= 1 h); `u-freshness` the owner's `_u_cadence()` strop and `goal-guard` a 24 h
  floor keep their LONGER intent via `max()`.

  CROSS-KIND TOTAL CAP (#913, restored) — no OTHER priority kind delivered to
  this sid within `_total_gap()` (>= 1 h). Recovery kinds are excluded from the
  scan. The batch path (`batch_eligible`) is the SIBLING gate: it returns [] while
  the cap is closed, so a second batch never leaks a second interruption; when the
  cap is open it composes every floor-eligible kind into ONE keystroke — batching
  is how multiple due kinds SHARE the single hourly interruption.

#923 BATCHING (owner ROZHODNUTÉ): instead of individual delivery, ALL eligible
families compose into ONE combined prompt per 1h slot. `batch_eligible()` returns
the ordered list (WORK_DRIVING first, AUDIT second); `compose_batch()` formats them
into a single `BATCH_PREFIX`-headed message; `mark_batch_sent()` stamps all at once.
The classification (WORK_DRIVING vs AUDIT) determines section ORDER within the batch
and the TRIM ORDER when the batch exceeds max_chars (audit trimmed first).

`mark_sent` is written on any DELIVERED send — a keystroke that reached the pane,
transcript-confirmed OR `delivered-unconfirmed` (#1023 reopen: the owner's "raz
za hodinu" rule is about the nudge REACHING the pane, not the session's reaction).
A genuine SWALLOW (text backed out, nothing delivered) never advances the clock —
the #714 MAX_SEND_FAILS retry bound stays each rider's storm limiter, unchanged. `prune` is the standard #519/#531 orphan reaper shape
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
# #1023-review 🔵8 — goal-sweep / compact / subagent-stuck are ALSO deliberately
# OUT: recovery/arming/compact lanes, not the footer-nudge family, each with its
# OWN bound (goal-sweep = 1-pending-request lifecycle + #731 cap; compact = 30-min
# cooldown + #855 vetoes, and its #848 per-integration supersede would BREAK under
# a 1h floor; subagent-stuck = decide_working interval + max_nudges cap + #491 ACK).
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
# must never turn the reconcile into spam.
U_RECONCILE_CADENCE_S = 3600
U_RECONCILE_CADENCE_MIN_S = 3600

# #1023 (owner directive 2026-09-14) — the GLOBAL per-pane-per-KIND floor: NO
# machine nudge of a given kind reaches a session's prompt more often than once
# per hour. This is the ONE place the "raz za hodinu" rule lives — every gated
# category carries it (`_category_floor` below), so the pre-#1023 per-JOB floors
# (queue-arrival's 30-min `QUEUE_ARRIVAL_NUDGE_FLOOR_S`, and the sibling `_cadence`
# re-check windows that are already >= 1 h) no longer need their own sub-hour
# constants. Env AIRULESET_NUDGE_MIN_INTERVAL_S can only RAISE it (floor-clamped
# at NUDGE_MIN_INTERVAL_MIN_S == the owner's hard 1 h strop, the #504/#543 lesson):
# a units-error / accidental sub-hour value must never re-open the burst this fixes.
#
NUDGE_MIN_INTERVAL_S = 3600
NUDGE_MIN_INTERVAL_MIN_S = 3600

# #1023 fix-forward (integration finding, 2026-09-14) — the CROSS-KIND TOTAL cap
# (#913, owner 2026-09-06, verbatim: "nikdy viac ako raz za hodinu!!!! a ani iny
# nudge do promptu!!!"): at most ONE PRIORITY nudge per pane per hour in TOTAL,
# across ALL kinds. The #1023 lane DELETED this (as `NUDGE_FAMILY_GAP_S`) on the
# inference "per-kind staging bounds the total" — but with two kinds staged on,
# two nudges per hour reach one pane (a kind whose condition is not met at the
# shared batch delivers later in the hour, via the batch path). So the cap is
# RESTORED, under a NEW name (the old `NUDGE_FAMILY_GAP_S`/`_family_gap` stay
# deleted). Env AIRULESET_NUDGE_TOTAL_GAP_S can only RAISE it (floor-clamped at
# NUDGE_TOTAL_GAP_MIN_S == the owner's hard 1 h strop, the #504/#543 lesson):
# a units-error / accidental sub-hour value must never re-open the burst.
NUDGE_TOTAL_GAP_S = 3600
NUDGE_TOTAL_GAP_MIN_S = 3600

# #1023 addendum (owner, 2026-09-14) — RECOVERY revival identities: a nudge that
# REVIVES a dead/blocked session (401/limit resume, /compact) is NOT a prompt
# interruption, so it is exempt from BOTH the per-kind floor and the cross-kind
# total cap, and never counts as "another kind delivered" for the cap. This set
# MUST mirror tmux_io.RECOVERY_NUDGE_KINDS (the canonical staging set) — a
# drift-lock test asserts they are identical. It is duplicated here rather than
# imported so nudge_gate stays a LEAF module: several modules rely on
# `from watchdog import nudge_gate` being import-safe, and tmux_io pulls in the
# whole watchdog package.
RECOVERY_NUDGE_KINDS = frozenset({"resume", "compact"})

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


def _min_interval():
    """The effective per-pane-per-kind floor (#1023), the env override floored at
    NUDGE_MIN_INTERVAL_MIN_S (the owner's hard 1 h strop) so a units error can't
    lower it below one hour (#504/#543)."""
    return max(_env_int("AIRULESET_NUDGE_MIN_INTERVAL_S", NUDGE_MIN_INTERVAL_S),
               NUDGE_MIN_INTERVAL_MIN_S)


def _total_gap():
    """The effective cross-kind TOTAL cap gap (#1023 fix-forward / #913), the env
    override floored at NUDGE_TOTAL_GAP_MIN_S (the owner's hard 1 h strop) so a
    units error can't lower it below one hour (#504/#543). Env can only RAISE."""
    return max(_env_int("AIRULESET_NUDGE_TOTAL_GAP_S", NUDGE_TOTAL_GAP_S),
               NUDGE_TOTAL_GAP_MIN_S)


GOAL_GUARD_FLOOR_S = 24 * 3600


def _category_floor(category):
    """The per-category (== per-kind) floor (#1023): EVERY gated kind carries the
    global `_min_interval()` (>= 1 h) so no kind reaches a session's prompt more
    than once per hour. Kinds with a LONGER intent keep it via `max()`: `goal-guard`
    at least 24 h (#878), `u-freshness` the owner's `_u_cadence()` strop (>= 1 h,
    env-raisable). No kind is exempt (the pre-#1023 default of 0 for most kinds is
    exactly what let queue-arrival re-fire below the hour)."""
    if category == "u-freshness":
        return max(_min_interval(), _u_cadence())
    if category == "goal-guard":
        return max(_min_interval(), GOAL_GUARD_FLOOR_S)
    return _min_interval()


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


def _total_cap_block(sess, exclude_category, now):
    """The cross-kind TOTAL cap (#913, restored #1023 fix-forward): return the
    `(category, ts)` of the MOST-RECENT OTHER priority kind delivered to this
    session within `_total_gap()`, or None when the cap is open. `exclude_category`
    (the kind being decided, or None for the batch path) is skipped so a kind
    never blocks itself via the cap — its OWN repeat is the per-kind floor's job.
    RECOVERY kinds are skipped: a revival is not a prompt interruption and never
    counts toward the cap (they never call `mark_sent` in production either, so
    this is a defensive belt on top of that). A FUTURE-skewed / non-numeric ts is
    ignored by `_gate_ts`, so a corrupt entry can never mute a session via the
    cap (the same fail-safe direction as the per-kind floor)."""
    gap = _total_gap()
    blocker = None
    for cat, raw in sess.items():
        if cat == exclude_category or cat in RECOVERY_NUDGE_KINDS:
            continue
        ts = _gate_ts(raw, now)
        if ts is not None and now - ts < gap and (blocker is None or ts > blocker[1]):
            blocker = (cat, ts)
    return blocker


def gate_ok(state, sid, category, now):
    """True iff a nudge of `category` (== nudge KIND) to `sid` is allowed at `now`.
    A RECOVERY kind (resume/compact) is ALWAYS allowed — a revival into a
    dead/blocked session is not a prompt interruption, exempt from both bounds.
    A PRIORITY kind is allowed iff BOTH hold:
      - PER-KIND FLOOR (#1023): the last SEND of THIS kind (a keystroke DELIVERED
        to the pane — confirmed OR delivered-unconfirmed) is at least
        `_category_floor(category)` old (or absent);
      - CROSS-KIND TOTAL CAP (#913, restored): NO OTHER priority kind was
        delivered to this sid within `_total_gap()` (`_total_cap_block`).
    Used by individual riders; the batch path (`batch_eligible()`) is the sibling
    gate that applies the SAME total cap. Fail-safe ALLOWS on any malformed state
    (never suppress a legit nudge) — including a FUTURE-skewed / corrupt-huge
    numeric ts, which `_gate_ts` ignores so it can never mute a session."""
    if category in RECOVERY_NUDGE_KINDS:
        return True
    sess = _session(state, sid)
    last_cat = _gate_ts(sess.get(category), now)
    if last_cat is not None and now - last_cat < _category_floor(category):
        return False                              # per-kind floor
    if _total_cap_block(sess, category, now) is not None:
        return False                              # cross-kind total cap
    return True


def floor_hold_reason(state, sid, category, now):
    """The honest journal clause for a nudge the gate HELD — distinguishing the
    two bounds (#1023 + #913 fix-forward), matching `gate_ok`'s check order:
      - `"hold:floor (<kind>, <mm> min since last send)"` — THIS kind's own
        per-kind floor. "since last SEND" (not "confirmed", #1023 reopen): the
        mark is stamped whenever a keystroke was DELIVERED to the pane —
        transcript-CONFIRMED *or* `delivered-unconfirmed` (box cleared, submit
        not proven) — because the owner's "raz za hodinu" rule is about the
        nudge REACHING the pane, never about the session's later reaction. A
        clause that said "since last confirmed" while the mark also fires on an
        unconfirmed send is a lie (it misled the reopen's own reconstruction);
      - `"hold:total-cap (<other-kind> delivered <mm> min ago)"` — a DIFFERENT
        priority kind delivered within the total gap.
    Riders render this verbatim (`-> %s`), so the journal token is the gate's, not
    a hardcoded one. Fail-safe `"hold:floor (<kind>, floor not elapsed)"` when no
    ts is readable (never claim a number we cannot compute). A recovery kind is
    never held, so it is never asked for a reason in production."""
    sess = _session(state, sid)
    last = _gate_ts(sess.get(category), now)
    if last is not None and now - last < _category_floor(category):
        return "hold:floor (%s, %d min since last send)" % (
            category, int((now - last) // 60))
    blocker = _total_cap_block(sess, category, now)
    if blocker is not None:
        bcat, bts = blocker
        return "hold:total-cap (%s delivered %d min ago)" % (
            bcat, int((now - bts) // 60))
    return "hold:floor (%s, floor not elapsed)" % category


def mark_sent(state, sid, category, now):
    """Record a DELIVERED nudge of `category` to `sid` at `now` — a keystroke
    that reached the pane. Called on a confirmed OR a delivered-unconfirmed send
    (a genuine swallow, text backed out, must not advance the clock — each
    rider's own MAX_SEND_FAILS bound stays the storm limiter). The recorded per-kind timestamps are what BOTH the per-kind floor
    and the cross-kind total cap (`_total_cap_block`) read. Never raises on a
    pre-existing malformed namespace — it is replaced with a fresh dict for this
    sid rather than crashing the sweep."""
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

    #1023 fix-forward — the batch path is the SIBLING gate of `gate_ok` and
    applies the SAME cross-kind TOTAL cap (#913, restored): if ANY priority kind
    was delivered to this sid within `_total_gap()`, return [] — a new batch is a
    new prompt interruption, forbidden inside the hour. This closes the batch-path
    hole (a kind whose condition was not met at the shared batch would otherwise
    become floor-eligible and deliver a SECOND nudge later in the hour). When the
    cap is open, a category is eligible when its OWN per-kind floor
    (`_category_floor`, >= 1 h) has expired — batching then composes every due
    kind into ONE keystroke, so multiple kinds SHARE the single hourly slot.

    The per-kind switch is enforced upstream: a DISABLED kind's rider logs
    `skip:kind-off` and never contributes to `batch_collect`, so a disabled kind
    is never composed into the batch even though it may be floor-eligible here.

    Returns categories ordered: WORK_DRIVING first, AUDIT second (#923).
    Returns [] when the total cap is closed OR no category's floor has expired.
    Fail-safe: malformed state → [] (the safe direction for batching — no
    batch, the individual riders' own gates still work)."""
    sess = _session(state, sid)
    # Cross-kind total cap: a recent priority delivery blocks the WHOLE batch.
    if _total_cap_block(sess, None, now) is not None:
        return []
    # Collect categories whose per-kind floor has expired.
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
    timestamp, so each member's own per-kind floor (#1023) blocks that kind's
    next delivery for an hour. Delegates to `mark_sent` per category."""
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
