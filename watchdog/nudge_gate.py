"""Shared per-session / per-pane machine-nudge CADENCE GATE (#797, #1023, #1092).

DOCTRINE (owner 26.10.2026, bundle B9, supersedes the #1023 1 h per-kind floor
and the #913/#1023 3 h total cap — their history is in
`.claude/rules-reference/internals-archive.md`, grep "B9 daily cap"): "vidím
chodiť často nudges do claudes … mali by sa nudges zgrupovať a volať napr. len
raz denne". Every nudge is a whole turn, so every NON-recovery keystroke kind
reaches a pane AT MOST ONCE PER 24 h, and everything due at that moment rides
ONE `nudge:` batch. Two bounds carry it:

  PER-SESSION (`gate_ok` / `batch_eligible`, `state["nudge_cadence"] = {sid:
  {category: last_delivered_ts}}`): a per-kind floor of a day (`_category_floor`)
  plus the cross-kind TOTAL cap of a day (`_total_cap_block`, counting the same
  kind too). While the cap is open, `batch_eligible` names every due GATED
  category so the job-20 riders compose ONE batch (`compose_batch`,
  `mark_batch_sent` stamps every member).

  PER-PANE (`pane_budget_ok` / `mark_pane_attempt`, `state["nudge_pane_attempts"]
  = {pid: [ts]}`): ONE typing attempt per pane per day, consulted BEFORE the first
  keystroke by every delivery primitive that types into an active pane
  (`send_verified`, the stash route `deliver_with_stash`, the goal re-arm in
  `deliver_goal`, the goal-guard direct send) and pre-checked by riders that would
  otherwise read a hold as a failed delivery (report-owed, dying-subagent).
  `pane_budgeted` says which kinds it binds. A pane, not a session: a restarted
  claude in the same pane gets no fresh slot. Every machine delivery threads
  `state` (job 49 task-hygiene since B9), so no budgeted path skips it.

EXEMPT: RECOVERY identities (`RECOVERY_NUDGE_KINDS`: resume/`continue` after an
API error, oauth-resume, compact, the owner's own `/autopilot` goal-arm,
wake-parked, goal-disarm, model-restore) skip both bounds and never count — a
revival of a dead session is not a prompt interruption; a re-arm spends the
day only for a budgeted WORK origin (`deliver_goal`'s `_rearm_budgeted`).
A liveness question is NOT a revival: job 4's stuck-check is the capped
`working-stall` kind. An OWNER-authored delivery (`user_authored`, granted only
in `discord_replies`: the owner's reply, ❓-flag prompt, card reopen) passes
both bounds. The owner's DECLARED schedule (`watch-trigger`, Job 52) is the one
`PRIORITY_CAP_EXEMPT_KINDS` member: it keeps its own hourly floor (its slots ARE
the owner's schedule), never blocks or counts toward the day. The #1257
owner-stop latch (`watchdog/owner_stop.py`) sits ABOVE all of this: a latched
pane gets no machine keystroke of any kind.

The daily batch is NEVER trimmed (its body goes to a nudge file, #1157), so
every due kind rides it; an `infra-priority` wave rides it only while its own
switch is on, its hub receipt posted by the batch callback. A HELD send is
journalled ONCE per (pane|sid, kind) episode (`first_hold`), never per sweep.

`mark_sent` stamps any DELIVERED or typed attempt (confirmed, delivered-
unconfirmed, swallowed — #1023 reopen, #1092 (a)). Fail-safe: a malformed or
future-skewed entry reads as "no prior nudge" → ALLOW (never a permanent mute).
`prune` reaps gone sessions (visited_sids PRIMARY, a TTL SECONDARY, #519/#531).
"""
import os
import time

# The gated job-20 rider categories (the batch members). Jobs 8/11 (bounce /
# gk-request backstops), card, goal-sweep and subagent-stuck stay OUT of the
# batch (own lanes with their own bounds) but are bound by the per-pane day.
# #923 CLASSIFICATION: WORK_DRIVING sections lead the batch, AUDIT sections
# follow. Every new gated category joins exactly ONE of the two sets.
WORK_DRIVING_CATEGORIES = frozenset({
    "release-gap", "queue-arrival",
    "bounce-verdict",   # #1066 lane B: a fresh gk BOUNCE verdict drives work
})

AUDIT_CATEGORIES = frozenset({
    "partition-audit", "u-freshness", "goal-guard",
})

GATED_CATEGORIES = WORK_DRIVING_CATEGORIES | AUDIT_CATEGORIES

DAY_S = 24 * 3600

# Every bound below is ONE DAY (owner 26.10.2026). Env overrides can only RAISE
# a bound — each is floor-clamped at its `_MIN_S` (the #504/#543 lesson: a units
# error must never re-open the burst).
U_RECONCILE_CADENCE_S = DAY_S        # #797 U-reconcile strop (u-freshness)
U_RECONCILE_CADENCE_MIN_S = DAY_S
NUDGE_MIN_INTERVAL_S = DAY_S         # #1023 per-pane-per-KIND floor
NUDGE_MIN_INTERVAL_MIN_S = DAY_S
NUDGE_TOTAL_GAP_S = DAY_S            # #913/#1023 cross-kind TOTAL cap
NUDGE_TOTAL_GAP_MIN_S = DAY_S

# RECOVERY revival identities (#1023 addendum; #1038/#1181/#1260 goal-arm =
# the owner's own `/autopilot` arm + the owner-authorized stream-migrate
# watcher; #1063 goal-disarm; #1084 the inert `compact`; #1203 model-restore).
# MUST mirror tmux_io.RECOVERY_NUDGE_KINDS (a drift-lock test asserts identity);
# duplicated so nudge_gate stays an import-safe LEAF module.
RECOVERY_NUDGE_KINDS = frozenset(  # #1203 model-restore: watchdog/model_restore.py
    {"resume", "compact", "goal-arm", "wake-parked", "goal-disarm", "model-restore"})

# #1163 — the owner's DECLARED watch-trigger slots (Job 52) are exempt from the
# day: they neither block nor count toward the total cap or the pane budget and
# keep their own hourly floor. Disjoint from RECOVERY_NUDGE_KINDS. #1109's
# `infra-priority` LEFT this set on 26.10.2026: a release-blocking infra hand-off
# now waits for the pane's one daily batch like every other work signal.
PRIORITY_CAP_EXEMPT_KINDS = frozenset({"watch-trigger"})
WATCH_TRIGGER_FLOOR_S = 3600   # == watch_triggers.MIN_SLOT_GAP_S (drift-locked)

# orphan-reaper TTL for a per-sid cadence rec whose session is gone (#519/#531):
# `visited_sids` is PRIMARY (a live pane is never reaped), this only SECONDARY.
NUDGE_CADENCE_ORPHAN_TTL_S = 24 * 3600

# #1092 — the PER-PANE typing-attempt BUDGET (see the module docstring): ONE
# machine typing attempt per pane per rolling day across every budgeted kind,
# stamped AFTER any typing attempt (delivered, unconfirmed OR swallowed).
PANE_ATTEMPT_BUDGET = 1
PANE_ATTEMPT_WINDOW_S = DAY_S


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _u_cadence():
    """The effective U-reconcile floor: the env override, floored at
    U_RECONCILE_CADENCE_MIN_S (a day). Env can only RAISE (#504/#543)."""
    return max(_env_int("AIRULESET_U_RECONCILE_CADENCE_S", U_RECONCILE_CADENCE_S),
               U_RECONCILE_CADENCE_MIN_S)


def _min_interval():
    """The effective per-pane-per-kind floor, floored at NUDGE_MIN_INTERVAL_MIN_S
    (a day). Env can only RAISE (#504/#543)."""
    return max(_env_int("AIRULESET_NUDGE_MIN_INTERVAL_S", NUDGE_MIN_INTERVAL_S),
               NUDGE_MIN_INTERVAL_MIN_S)


def _total_gap():
    """The effective cross-kind TOTAL cap gap, floored at NUDGE_TOTAL_GAP_MIN_S
    (a day). Env can only RAISE (#504/#543)."""
    return max(_env_int("AIRULESET_NUDGE_TOTAL_GAP_S", NUDGE_TOTAL_GAP_S),
               NUDGE_TOTAL_GAP_MIN_S)


GOAL_GUARD_FLOOR_S = 24 * 3600


def _category_floor(category):
    """The per-kind floor: a day for every kind (`_min_interval()`), the longer
    of it and `_u_cadence()` / GOAL_GUARD_FLOOR_S for u-freshness / goal-guard
    (env-raisable), and the owner's own hourly slot floor for `watch-trigger`
    (its declared schedule is its bound, #1163)."""
    if category == "watch-trigger":
        return WATCH_TRIGGER_FLOOR_S
    if category == "u-freshness":
        return max(_min_interval(), _u_cadence())
    if category == "goal-guard":
        return max(_min_interval(), GOAL_GUARD_FLOOR_S)
    return _min_interval()


def pane_budgeted(nudge, user_authored=False):
    """True iff a keystroke delivery of `nudge` is bound by the per-pane daily
    budget: a machine nudge identity that is neither a RECOVERY revival nor the
    owner's declared watch-trigger schedule, and not the owner's own Discord reply
    (`user_authored`). None (an un-threaded delivery) is not budgeted here — the
    #1023 kill switch already withholds it."""
    return (nudge is not None and not user_authored
            and nudge not in RECOVERY_NUDGE_KINDS
            and nudge not in PRIORITY_CAP_EXEMPT_KINDS)


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


def _total_cap_block(sess, now):
    """The cross-kind TOTAL cap (#913/#1023, a day since 26.10.2026): the
    `(category, ts)` of the MOST-RECENT budgeted kind delivered to this session
    within `_total_gap()`, or None when the cap is open. The deciding kind's own
    last send counts too. RECOVERY kinds and PRIORITY_CAP_EXEMPT_KINDS
    (watch-trigger) are skipped — they never count. A future-skewed / non-numeric
    ts is ignored by `_gate_ts` (a corrupt entry never mutes a session)."""
    gap = _total_gap()
    blocker = None
    for cat, raw in sess.items():
        if cat in RECOVERY_NUDGE_KINDS or cat in PRIORITY_CAP_EXEMPT_KINDS:
            continue
        ts = _gate_ts(raw, now)
        if ts is not None and now - ts < gap and (blocker is None or ts > blocker[1]):
            blocker = (cat, ts)
    return blocker


def gate_ok(state, sid, category, now):
    """True iff a nudge of `category` (== nudge KIND) to `sid` is allowed at `now`:
    a RECOVERY kind always; else THIS kind's last send is older than its
    `_category_floor` (checked FIRST, `hold:floor`) AND — for every kind but the
    cap-exempt watch-trigger — no budgeted kind reached this sid within the
    day (`_total_cap_block`, `hold:total-cap`). The batch path (`batch_eligible`)
    applies the SAME cap. Fail-safe ALLOWS on malformed / future-skewed state."""
    if category in RECOVERY_NUDGE_KINDS:
        return True
    sess = _session(state, sid)
    last_cat = _gate_ts(sess.get(category), now)
    if last_cat is not None and now - last_cat < _category_floor(category):
        return False                              # per-kind floor
    if category in PRIORITY_CAP_EXEMPT_KINDS:
        return True                               # #1163: its schedule is its bound
    if _total_cap_block(sess, now) is not None:
        return False                              # cross-kind total cap
    return True


def floor_hold_reason(state, sid, category, now):
    """The honest journal clause for a nudge the gate HELD, in `gate_ok`'s order:
    `hold:floor (<kind>, <mm> min since last send)` for THIS kind's own floor, or
    `hold:total-cap (<kind> delivered <mm> min ago)` for the most recent budgeted
    kind in the day (possibly THIS kind). Riders render it verbatim. Fail-safe
    `hold:floor (<kind>, floor not elapsed)` when no ts is readable."""
    sess = _session(state, sid)
    last = _gate_ts(sess.get(category), now)
    if last is not None and now - last < _category_floor(category):
        return "hold:floor (%s, %d min since last send)" % (
            category, int((now - last) // 60))
    if category in PRIORITY_CAP_EXEMPT_KINDS:
        return "hold:floor (%s, floor not elapsed)" % category
    blocker = _total_cap_block(sess, now)
    if blocker is not None:
        bcat, bts = blocker
        return "hold:total-cap (%s delivered %d min ago)" % (
            bcat, int((now - bts) // 60))
    return "hold:floor (%s, floor not elapsed)" % category


def mark_sent(state, sid, category, now):
    """Record a nudge of `category` to `sid` at `now` that reached the pane:
    confirmed, delivered-unconfirmed, or (#1092 (a)) a GENUINE SWALLOW — the text
    reached the pane, so the SAME kind cannot re-fire on the next sweep. Both
    the per-kind floor and the total cap read these stamps. A malformed
    namespace is replaced for this sid, never a crash."""
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
# #923 BATCHING — compose all eligible families into ONE prompt per daily slot.
# --------------------------------------------------------------------------- #

# The batch message leads with this prefix — a recognized machine-nudge prefix
# in goal.py `_machine_prefixes` ("nudge:") AND must be added to stash.py
# `_JANITOR_OWN_PREFIXES` for stranded-nudge cleanup.
BATCH_PREFIX = "nudge:"

# B9 fix (owner 26.10.2026): the daily batch is NEVER trimmed — since #1157 its
# body goes to a nudge file and ONE pointer row is typed, so every due kind rides
# the pane's one daily batch. 1400 was the pre-#1157 TYPED-batch cap; it stays
# only as the bound `send_outcome._presend_own` uses to recognise such a typed
# batch still sitting in a box.
BATCH_MAX_CHARS = 1400


def batch_eligible(state, sid, now):
    """Return the list of categories eligible for batched delivery at `now`.

    The SIBLING gate of `gate_ok`, applying the SAME total cap: any budgeted
    kind delivered to this sid within the day → [] (a second batch would be a
    second interruption). When the cap is open every category whose own floor
    expired is eligible, so every due kind SHARES the pane's one daily slot.

    The per-kind switch is enforced upstream: a DISABLED kind's rider logs
    `skip:kind-off` and never contributes to `batch_collect`, so a disabled kind
    is never composed into the batch even though it may be floor-eligible here.

    Returns categories ordered: WORK_DRIVING first, AUDIT second (#923).
    Returns [] when the total cap is closed OR no category's floor has expired.
    Fail-safe: malformed state → [] (the safe direction for batching — no
    batch, the individual riders' own gates still work)."""
    sess = _session(state, sid)
    # Cross-kind total cap: a recent priority delivery blocks the WHOLE batch.
    if _total_cap_block(sess, now) is not None:
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


def compose_batch(items):
    """Compose ONE batch message from `[(category, text), ...]`: WORK_DRIVING
    sections first, AUDIT second, each `[category] text`, led by `BATCH_PREFIX`
    so machine-prefix recognition classifies it as a machine nudge. Nothing is
    trimmed (the body goes to a nudge file, #1157). Returns `(composed_text,
    included_categories)` — every known gated category in `items`, in order;
    `('', [])` when none."""
    sections = ([(c, t) for c, t in items if c in WORK_DRIVING_CATEGORIES]
                + [(c, t) for c, t in items if c in AUDIT_CATEGORIES])
    if not sections:
        return "", []
    return (BATCH_PREFIX + "".join(" [%s] %s" % s for s in sections),
            [c for c, _t in sections])


def mark_batch_sent(state, sid, categories, now):
    """Mark ALL `categories` as sent at `now` in one call.

    Used after a batched delivery: every member gets the SAME timestamp, so each
    member's own floor and the total cap hold for a day."""
    for cat in categories:
        mark_sent(state, sid, cat, now)


# --------------------------------------------------------------------------- #
# #1092 — the per-pane typing-attempt BUDGET (see PANE_ATTEMPT_BUDGET above).
# --------------------------------------------------------------------------- #
def _pane_attempts(state, pid, now):
    """The typing-attempt timestamps for `pid` WITHIN the rolling window, at
    `now`. A future-skewed / non-numeric ts is dropped by `_gate_ts` (the same
    fail-safe direction as the cadence gate — a corrupt entry never suppresses).
    Returns [] on any malformed state (fail-safe: 'no prior attempt' → ALLOW)."""
    if not isinstance(state, dict):
        return []
    pa = state.get("nudge_pane_attempts")
    if not isinstance(pa, dict):
        return []
    lst = pa.get(pid)
    if not isinstance(lst, list):
        return []
    win = PANE_ATTEMPT_WINDOW_S
    return [t for t in lst
            if _gate_ts(t, now) is not None and now - t < win]


def pane_budget_ok(state, pid, now):
    """True iff FEWER than `PANE_ATTEMPT_BUDGET` typing attempts have been made
    into `pid` within the rolling `PANE_ATTEMPT_WINDOW_S`. The keystroke brake
    every active-pane delivery path consults BEFORE typing; a 2nd attempt in the
    day is refused. Fail-safe ALLOWS on malformed state (never suppress a legit
    delivery by accident — mirrors `gate_ok`)."""
    return len(_pane_attempts(state, pid, now)) < PANE_ATTEMPT_BUDGET


def pane_budget_hold_reason(state, pid, now):
    """The honest journal clause for a typing attempt the pane budget REFUSED:
    `hold:pane-budget (<n> attempts since HH:MM)` — the count of in-window
    attempts + the OLDEST one's local clock time (what the owner reads in the
    journal). Fail-safe `hold:pane-budget (budget exhausted)` when no ts is
    readable (never claim a time we cannot compute)."""
    attempts = sorted(_pane_attempts(state, pid, now))
    if not attempts:
        return "hold:pane-budget (budget exhausted)"
    hhmm = time.strftime("%H:%M", time.localtime(attempts[0]))
    return "hold:pane-budget (%d attempts since %s)" % (len(attempts), hhmm)


# B9 fix (review 15): a HELD nudge is journalled ONCE per (pane|sid, kind)
# episode — the stamp that blocks it — never on every 60-s sweep (~2.9k lines
# per pane per day). `{"<key>|<kind>": [since, logged_at]}`, aged by `prune`.
HOLD_LOG_KEY = "nudge_hold_logged"
HOLD_LOG_TTL_S = 7 * DAY_S


def first_hold(state, key, kind, since, now):
    """True the first time a hold of `kind` at `key` (a pane id or a sid) is seen
    for the blocking stamp `since` — the caller journals then — and False for the
    rest of that episode. No `since` / no `state` always journals (a hold is
    never silent by accident)."""
    if not isinstance(state, dict) or since is None:
        return True
    rec = state.get(HOLD_LOG_KEY)
    if not isinstance(rec, dict):
        rec = state[HOLD_LOG_KEY] = {}
    k = "%s|%s" % (key, kind)
    prev = rec.get(k)
    if isinstance(prev, list) and prev[:1] == [since]:
        return False
    rec[k] = [since, now]
    return True


def first_pane_hold(state, pid, kind, now):
    """`first_hold` for a per-pane budget hold: the episode is the day's oldest
    typing attempt into the pane."""
    attempts = sorted(_pane_attempts(state, pid, now))
    return first_hold(state, pid, kind, attempts[0] if attempts else None, now)


def first_gate_hold(state, sid, category, now):
    """`first_hold` for a `gate_ok` hold: the episode is the stamp that blocks
    it (this kind's own floor first, else the total cap's)."""
    sess = _session(state, sid)
    last = _gate_ts(sess.get(category), now)
    if last is None or now - last >= _category_floor(category):
        blocker = _total_cap_block(sess, now)
        last = blocker[1] if blocker else None
    return first_hold(state, sid, category, last, now)


def mark_pane_attempt(state, pid, now):
    """Record a typing attempt into `pid` at `now`. Called AFTER any keystroke
    delivery attempt regardless of outcome (delivered, delivered-unconfirmed, OR
    swallowed — each is a typing attempt into the pane). Prunes the pid's own list
    to the rolling window on write so the namespace stays bounded. A no-op on a
    malformed `state` or a falsy `pid` (mirrors `mark_sent`)."""
    if not isinstance(state, dict) or not pid:
        return
    pa = state.get("nudge_pane_attempts")
    if not isinstance(pa, dict):
        pa = {}
        state["nudge_pane_attempts"] = pa
    prior = pa.get(pid)
    # keep in-window AND future-skewed (the #1092 F1 direction): a prior attempt
    # another delivery path stamped at a slightly-later clock (`time.time()` vs
    # this sweep's `now`) must NOT be dropped here either, or a same-sweep re-arm
    # mark would wipe the nudge's just-stamped attempt.
    kept = ([t for t in prior
             if isinstance(t, (int, float)) and not isinstance(t, bool)
             and (t > now or now - t < PANE_ATTEMPT_WINDOW_S)]
            if isinstance(prior, list) else [])
    kept.append(now)
    pa[pid] = kept


def _pane_attempt_recent(lst, now):
    """#1092 blocker fix — True iff `lst` holds a ts the PRUNE must treat as still
    live: in the rolling window OR FUTURE-skewed. This mirrors `_stale_entry`'s
    #519 "a future ts counts as FRESH" direction — the OPPOSITE of the gate READ
    (`_pane_attempts`/`_gate_ts`, which DROPS a future ts so a corrupt entry can
    never BLOCK a send). The two directions are each the SAFE one for their job:
    the read errs toward ALLOW, the prune errs toward KEEP. Without this the
    end-of-sweep prune (run at the sweep `now`) reaped the attempt `send_verified`
    had just stamped a few seconds LATER (`time.time()` > `now`), so the budget
    never accumulated across sweeps and the belt was inert against the batch
    storm (Review 2 F1). Type-checked like `_gate_ts`."""
    if not isinstance(lst, list):
        return False
    for t in lst:
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            if t > now or now - t < PANE_ATTEMPT_WINDOW_S:
                return True
    return False


def _prune_pane_attempts(state, now):
    """#1092 — reap `nudge_pane_attempts` entries for panes with NO recent attempt
    left (a gone/idle pane). Keyed on pid (not sid), so it cannot use the
    `visited_sids` gate the cadence prune uses; the window is the bound. Reaps a
    pid ONLY when it has no ts in the window AND none future-skewed
    (`_pane_attempt_recent`) — so a just-stamped attempt whose clock ran slightly
    ahead of this sweep's `now` (a `time.time()`-stamping caller) is KEPT, not
    wiped (the Review 2 F1 blocker: the belt must survive to the NEXT sweep).
    Never raises."""
    if not isinstance(state, dict):
        return
    pa = state.get("nudge_pane_attempts")
    if not isinstance(pa, dict):
        return
    for pid in [p for p in list(pa.keys())
                if not _pane_attempt_recent(pa.get(p), now)]:
        pa.pop(pid, None)


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
    _prune_pane_attempts(state, now)   # #1092 — pid-keyed pane-budget namespace
    held = state.get(HOLD_LOG_KEY)
    if isinstance(held, dict):        # B9 fix: aged hold-journal episodes
        for k in [k for k, v in held.items() if not isinstance(v, list)
                  or len(v) < 2 or _ts(v[1]) is None or now - v[1] > HOLD_LOG_TTL_S]:
            held.pop(k, None)
        if not held:
            state.pop(HOLD_LOG_KEY, None)
    cad = state.get("nudge_cadence")
    if not isinstance(cad, dict):
        return
    for sid in [k for k, v in list(cad.items())
                if k not in visited_sids and _stale_entry(v, now, ttl_s)]:
        cad.pop(sid, None)
