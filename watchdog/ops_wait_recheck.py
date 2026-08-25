"""Partition-audit re-check nudge (#547 W→I + #552 I→W/U) — the mechanical
counterpart of the prose-only `_partition_workable` labelling contract, in BOTH
directions.

INCIDENT-W (montalu5, 2026-08-18): a session parked 13 tickets into `W`/`ops-wait`
(waiting on Odoo discussion replies + a gk PROD release). The replies arrived and
the release shipped LONG ago, the armed `/goal` loop never looked, the session
stayed blind until the owner asked by hand. Root cause: the `W` re-entry contract
(`statusline-vocabulary.md` W bullet + skills/autopilot) is PROSE-ONLY — the
`/goal` evaluator reads only the transcript, `_watchdog_backlog_fetch` runs only
`--count` (never `--ops-wait`), and NO watchdog job reads the `--ops-wait` members.
So an armed loop parking on `W` has no trigger to ever re-check the external state.

INCIDENT-I (montalu3, 2026-08-18, #552): the OPPOSITE direction of the SAME
label-driven root cause. `_partition_workable` (cli_quals.py) is purely
label-driven — it derives the I/U/W split mechanically, but the LABELS
(`ops-wait` with evidence, `needs-answer`/`needs-decision`, a delivered
`needs-acceptance`) are set by session/supervisor JUDGMENT per the #526/#539
shapes, and NOTHING periodically forces a session to re-audit its `I` list against
those shapes. #547 mechanized only W→I (a parked W ticket whose event landed → the
label should come OFF); the I→W/U direction (an `I` ticket ALREADY meeting a
parking shape → the label should go ON) had no mechanical trigger at all, so 8
tickets rotated in `I` (fix-class waiting on airuleset#533, sent-thread,
deferred-thread) until the owner pushed back — the footer showed an inflated `I`
the owner could not trust. Both are the SAME class as #527's
`U`-without-a-delivered-question (which #539 mechanized with the `no-question!`
tag): a label-driven partition needs a mechanical re-audit trigger.

WHAT THIS DOES (both directions, ONE daily nudge — #552 combined over #547): on a
per-session cadence (~daily), for an armed `/goal` pane whose partition has
anything to audit (`I > 0` OR `--ops-wait` members parked), deliver ONE verified
keystroke that reminds the session to re-audit its WHOLE partition against the
#526/#539 shapes — the I→W/U clause (re-audit each `I` member: fix-class /
sent-thread / deferred-thread → `ops-wait`=W; delivered owner-question →
needs-answer/decision=U; a bare `needs-acceptance` queued for owner approval → U
(#622); only genuinely-dispatchable code work stays I) AND, when W members are
parked, the W→I clause (re-check the external event named in the park comment,
then clear `ops-wait` WITH evidence or confirm the wait). The JUDGMENT stays in
the session (the watchdog cannot judge a #526/#539 shape); only the SCHEDULER is
mechanical, and the supervisor stays the ONLY one that sets/clears any label with
evidence — this job only SURFACES the audit back into the loop's attention
(exactly the U-bucket re-entry shape, cadence instead of a routed Discord answer).
Combining both directions into ONE ping (vs a separate I-nudge) is the #552 design
choice: ONE keystroke/day covering the whole partition audit, not two.

ACCEPTED TRADE-OFF (both #552 adversarial reviews flagged this independently — it is
INTENDED, not a defect): the I direction fires on `I > 0`, which is the DEFAULT
state of any productive armed loop, so this delivers a ~daily "re-audit your I
list" keystroke into EVERY healthy backlog-carrying loop even when nothing is
actually mislabelled — broader than #547's W-parked-only, drain-on-landing trigger.
That is the ONLY possible mechanization of an UN-JUDGEABLE audit (the watchdog
cannot tell a mislabelled `I` member from a correctly-`I` one — that IS the session
judgment it delegates), and it is bounded to ONE consolidated ping/day, gated to
armed loops, and env-tunable via `AIRULESET_OPS_WAIT_RECHECK_CADENCE_S` (floored at
6h). The owner requested exactly this (a daily nudge for `I > 0`, one ping not two —
the ticket's own §3), so the interruption/token cost is the accepted price of never
letting the montalu3 inflated-`I` drift recur silently.

DESIGN (#486 reuse, ZERO new delivery/fetch primitives): this rides
`goal_lane_sweep`'s EXISTING armed-candidate-pane loop (which already resolves
pid/cwd/sid/tpath/loc + the `glance`, reads the structured `state["goal_mark"]`
armed gate, and coordinates keystrokes via the per-sweep `handled` set). It reuses
`watchdog.send_verified` (transcript-proof submit), the
`_janitor_mark_watch`/`_janitor_clear_watch` reclaim of a swallowed nudge, and the
shared `stuck-check: ` own-payload prefix (already in `_JANITOR_OWN_PREFIXES` +
`_MACHINE_PROMPT_PREFIXES`, so a swallowed nudge is reclaimable AND never mistaken
for a human answer). Both signals come from the SAME `_partition_workable`
derivation the footer/stop-proof use — the W members via `ops_wait_fetch` (=
`_watchdog_ops_wait_fetch`, the 1:1 sibling of `_watchdog_backlog_fetch`, read
through the per-repo `_cached_ops_wait` TTL cache), and the `I` COUNT via
`i_count` = the already-computed, already-cached `glance.backlog`
(`_cached_backlog_count`) the loop resolved for its own verdict — never a parallel
query and never a new per-sweep fetch (#367/#181; #547-review cache lesson). An
`i_count` of `None` (an awaiting-user / cheap-verdict pane whose backlog the glance
never consulted) is UNDETERMINED, so the I direction fails safe (no I nudge into a
❓-blocked pane), exactly like a `None` W fetch.

The verdict logic is a PURE `_recheck_decision(rec, i_count, w_members, now,
cadence)` with a THREE-valued spirit (nudge / wait / clear / skip-undetermined);
all I/O (fetch, send, state writes) lives in `goal_ops_wait_recheck` behind the
same injectable seams the sibling jobs use, and `dry_run` mutates nothing (#516).

PHASE 2 (source-aware probes) — EVALUATED (#550) & DEFERRED, not a pending TODO.
An event-driven variant — poll the Odoo Discuss thread / gk release named in a W
ticket's park comment and nudge the MOMENT the reply/release lands, instead of on
cadence — was evaluated in #550 and REJECTED as not cheaply-safe:
  - the external SOURCE (which thread / which release) lives ONLY in the free-form
    park COMMENT, not as a machine-readable field in `--ops-wait`'s structured
    output (reason is only acceptance|ops-wait; the title field is free-form
    prose), so a probe needs a per-ticket `gh issue view --comments` fetch on the
    SWEEP path (the on-demand `--waiting` comment machinery never runs there) + a
    fragile NL parse of prose;
  - the Odoo RO poll needs the stream's prod credential (the watchdog user lacks
    it), is fail2ban-sensitive, and is a network call on the 120s sweep critical
    path (#172/#365 class) — the SESSION already does this poll in-session when
    nudged, which is where that credential/access belongs;
  - the latency gap is already tunable via AIRULESET_OPS_WAIT_RECHECK_CADENCE_S
    (floor OPS_WAIT_RECHECK_MIN_S=6h), so a probe only helps below the hours range
    — not a real need for inherently multi-hour/multi-day waits.
REOPEN only when ALL hold: (T1) a machine-readable park convention exists (a
`waiting-on: odoo-thread:<id>` / `waiting-on: release:<tag>` field sessions
reliably emit); (T2) a measured need for sub-6h re-check latency; (T3) a safe,
budgeted RO-poll / push-signal seam OFF the sweep critical path. None hold today.
Full evidence: issue #550.

HODINOVÝ THREAD-CHECK (#607, owner 2026-08-21) — the owner's contract is that an
armed loop reads its client Discuss threads AT LEAST 1×/hour so a reply never
sits unnoticed. The DUTY is a fleet rule (statusline-vocabulary.md W bullet) and
is NAMED in the `_W_CLAUSE` nudge; the watchdog-side ENFORCEMENT is the SAME as
#570's — the weekend-aware `stale!` freshness result (`cli_quals.
_stale_ops_wait_flagged`, now `working_time`-based), which surfaces WHICH W
tickets have gone >24h WORKING with no stream push (i.e. the hourly-check +
reminder discipline demonstrably failed). Verifying the hourly READ itself at
hourly GRANULARITY is DEFERRED for exactly the PHASE-2 reasons above (the
watchdog cannot read Odoo Discuss — no credential, no machine-readable thread
source, and a per-sweep NL comment-poll is the #172/#365/#550 class it rejects) —
so the daily nudge cadence is unchanged (a 12×/day hourly keystroke would be the
spam #552 already fought). REOPEN the hourly-granularity check only under the
SAME T1/T2/T3 above. Full evidence: issue #607.
"""
import os

import watchdog

# #695 -- the DISCUSS-AUDIT clause is odoo-erp-scoped (client Odoo Discuss
# threads are an odoo-erp thing, the same repo scope the #627 close gate holds
# in `block-fork-no-merge-issue-close.sh`). The cwd->repo-name derivation is
# the fleet's ONE existing resolver, `notify.repo_name_for` -- never a second
# parse. Fail-safe: notify unimportable -> resolver None -> the clause simply
# never renders (a missing clause on odoo-erp costs one day's reminder; a
# false clause elsewhere is daily noise instructing an audit with zero hits).
try:
    from notify import repo_name_for as _repo_name_resolver
except Exception:  # pragma: no cover - notify unimportable (partial checkout)
    _repo_name_resolver = None

# env AIRULESET_OPS_WAIT_RECHECK_CADENCE_S — how long a W ticket sits parked (and
# how long between re-nudges) before this job re-surfaces it into the loop. ~22h
# so it fires a little more than daily (robust to sweep timing / never SKIPS a
# day), yet never a per-sweep re-nudge. Both the FIRST-nudge grace (measured from
# first_seen) and the reping cadence use this ONE value.
OPS_WAIT_RECHECK_CADENCE_S = 22 * 3600
# floor for the env override (#504/#543 floor-clamp lesson): a sub-6h value would
# nag an armed loop several times a day about a ticket it JUST parked — a units
# error must never turn the re-check into spam.
OPS_WAIT_RECHECK_MIN_S = 6 * 3600
# orphan-reaper TTL for a per-sid rec whose session is gone (mirrors
# GOAL_MARK_ORPHAN_TTL_S / the #519/#531 per-sid-leak reaper): the `visited_sids`
# gate is PRIMARY (a live pane is never reaped regardless of age), this is only
# the SECONDARY safety for a budget-deferred pane.
OPS_WAIT_RECHECK_ORPHAN_TTL_S = 24 * 3600
# env AIRULESET_OPS_WAIT_FETCH_TTL_S — how long a `--ops-wait` member-list read is
# CACHED per repo (`state["ops_wait_cache"]`, keyed by cwd, shared across every
# armed pane on that repo). This is what stops the fetch from firing every 60s
# sweep per pane: the sibling `_watchdog_backlog_fetch` is likewise cached (10 min,
# `_cached_backlog_count`). 30 min here (a bit longer — the NUDGE cadence is
# ~daily, so the member list never needs to be minute-fresh; a resolved W is
# re-detected within ≤1 TTL). Floored so a units error can't turn it into a
# per-sweep gh union again.
OPS_WAIT_FETCH_TTL_S = 30 * 60
OPS_WAIT_FETCH_TTL_MIN_S = 5 * 60
# a FAILED/unmeasurable fetch (None) is cached only briefly so a transient gh
# hiccup re-checks soon rather than suppressing detection for a whole TTL —
# mirrors BACKLOG_CHECK_FAILURE_TTL_S.
OPS_WAIT_FETCH_FAIL_TTL_S = 60


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _cadence():
    """The effective cadence: the env override, floored at OPS_WAIT_RECHECK_MIN_S
    so a units-error / accidental sub-hour value can never turn the re-check into
    a per-sweep nag (#504/#543)."""
    return max(_env_int("AIRULESET_OPS_WAIT_RECHECK_CADENCE_S",
                        OPS_WAIT_RECHECK_CADENCE_S), OPS_WAIT_RECHECK_MIN_S)


def _fetch_ttl():
    """The cache TTL for a real member-list read, floored so an env units error
    can't collapse it back to a per-sweep fetch."""
    return max(_env_int("AIRULESET_OPS_WAIT_FETCH_TTL_S", OPS_WAIT_FETCH_TTL_S),
               OPS_WAIT_FETCH_TTL_MIN_S)


def _cached_member_fetch(cwd, fetch, state, now, cache_key, ttl=None,
                         fail_ttl=None):
    """A per-cwd TTL cache over a member-list `fetch` — the faithful sibling of
    `_cached_backlog_count` (#365), keyed by `cache_key` so ONE implementation
    serves BOTH the parked-W fetch (`ops_wait_cache`, #547) and the I-member
    fetch (`i_members_cache`, #578). This is the load-bearing #547-review fix:
    without it the fetch would spawn a gh union subprocess EVERY 60s sweep for
    EVERY armed pane on the 120s-budgeted sweep's critical path. Bounds it to at
    most one subprocess per repo per TTL, shared across every armed pane there.

    `fetch is None` (not wired) -> None, no cache write (the "wired = on"
    convention). A fetch exception -> None. A `ts` crossing the JSON persistence
    boundary is type-checked (a malformed/legacy entry reads as EXPIRED, never
    raises). None (unmeasurable) is cached only for `fail_ttl` so a transient gh
    hiccup re-checks soon. Returns a `list` or None — never a guessed []. The
    list element TYPE is the fetch's own (ints for ops-wait numbers, dicts for
    I-member records); this cache is element-type-agnostic."""
    if fetch is None:
        return None
    ttl = _fetch_ttl() if ttl is None else ttl
    fail_ttl = OPS_WAIT_FETCH_FAIL_TTL_S if fail_ttl is None else fail_ttl
    cache = state.setdefault(cache_key, {})
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        try:
            age = now - float(entry.get("ts", 0))
        except (TypeError, ValueError):
            age = None
        if age is not None:
            members = entry.get("members")
            entry_ttl = ttl if isinstance(members, list) else fail_ttl
            if age < entry_ttl:
                return members if isinstance(members, list) else None
    try:
        members = fetch(cwd)
    except Exception:
        members = None
    if not (members is None or isinstance(members, list)):
        members = None
    cache[cwd] = {"ts": now, "members": members}
    return members


def _cached_ops_wait(cwd, ops_wait_fetch, state, now, ttl=None, fail_ttl=None):
    """The parked W member NUMBERS for the repo at `cwd`, CACHED per-cwd in
    `state["ops_wait_cache"]` (#547). Thin wrapper over `_cached_member_fetch`."""
    return _cached_member_fetch(cwd, ops_wait_fetch, state, now,
                                "ops_wait_cache", ttl, fail_ttl)


def _cached_i_members(cwd, i_members_fetch, state, now, ttl=None, fail_ttl=None):
    """The WORKABLE (I) member RECORDS (`{number, createdAt, labels}`) for the
    repo at `cwd`, CACHED per-cwd in `state["i_members_cache"]` (#578). Thin
    wrapper over `_cached_member_fetch` — read only inside the ~daily nudge
    branch so the `--audit` subprocess fires at most once per repo per TTL AND
    only when actually nudging, never every sweep (#547 "cache the FETCH")."""
    return _cached_member_fetch(cwd, i_members_fetch, state, now,
                                "i_members_cache", ttl, fail_ttl)


def _fmt_age(seconds):
    """Human age of a parked W ticket for the nudge text — hours under 48h, days
    beyond (so a fresh-ish park reads honestly rather than rounding to `0d`). A
    negative age (a future `first_seen` under clock skew) clamps to 0 so the text
    never reads `~-1h` (#547 review 🔵)."""
    seconds = max(0, seconds)
    if seconds < 48 * 3600:
        return "~%dh" % int(seconds // 3600)
    return "~%dd" % int(seconds // 86400)


def _member_numbers(members):
    """The issue NUMBERS of a parked-W member list, tolerant of BOTH the #570
    structured shape (`[{"number": int, "stale": bool}, ...]`, from
    `_watchdog_ops_wait_fetch`) AND a legacy bare `int` (an older fetch, or an
    existing int-based test) — so the decider/sig/nudge machinery is unchanged
    for the int case and enriched for the dict case. A malformed element is
    dropped (never raises)."""
    out = []
    for m in members or []:
        if isinstance(m, bool):
            continue
        if isinstance(m, int):
            out.append(m)
        elif isinstance(m, dict) and isinstance(m.get("number"), int):
            out.append(m["number"])
    return out


def _stale_numbers(members):
    """The subset of `_member_numbers` flagged `stale!` — only the #570
    structured shape carries staleness, so a legacy int list yields an EMPTY
    set (no stale sub-clause, the safe/unchanged direction)."""
    return [m["number"] for m in (members or [])
            if isinstance(m, dict) and m.get("stale")
            and isinstance(m.get("number"), int)]


def _gk_handoff_numbers(members):
    """The subset of `_member_numbers` flagged `gk_handoff!` (#636) — a parked W
    ticket that ALSO carries a gk hand-off label (the post-release-limbo
    contradiction). Only the structured shape carries the flag, so a legacy int
    list yields an EMPTY list (no gk-handoff sub-clause — the safe/unchanged
    direction, exactly like `_stale_numbers`)."""
    return [m["number"] for m in (members or [])
            if isinstance(m, dict) and m.get("gk_handoff")
            and isinstance(m.get("number"), int)]


def _sig(members):
    """A stable signature of the parked W set (sorted numbers, comma-joined) —
    stored so a reader can see WHICH tickets a rec is tracking, and so a future
    set-change refinement has a hook. Numeric sort so the sig is order-stable."""
    return ",".join(str(n) for n in sorted(_member_numbers(members)))


def _members_line(members):
    """`#A #B #C` for the nudge text, oldest-number-first for stable reading."""
    return " ".join("#%d" % n for n in sorted(_member_numbers(members)))


def _members_line_aged(members, w_seen, now):
    """`#A (~ageA) #B (~ageB)` for the W clause (#594) — each member aged from
    ITS OWN `w_seen` entry (when the watchdog first observed it in W, ≈ the
    `ops-wait` label-add within one fetch TTL), never a single stale partition-
    level anchor. Oldest-number-first for stable reading. A member absent from
    `w_seen`, or an unusable map (a state/fetch edge), ages `?` — never a
    fabricated age (#539 fail-safe bias)."""
    seen = w_seen if isinstance(w_seen, dict) else {}
    parts = []
    for n in sorted(_member_numbers(members)):
        ts = seen.get(str(n))
        age = _fmt_age(now - ts) if isinstance(ts, (int, float)) else "?"
        parts.append("#%d (%s)" % (n, age))
    return " ".join(parts)


def _partition_sig(i_count, w_members):
    """A stable signature of the whole partition-audit set — the I count and the
    sorted W numbers — stored for observability (a reader sees what a rec is
    tracking) and as a hook for a future set-change refinement. `i:?`/`w:?` name
    an UNDETERMINED (None) half honestly, never a misleading `0`/empty."""
    i_part = "i:%d" % i_count if isinstance(i_count, int) else "i:?"
    w_part = "w:%s" % _sig(w_members) if isinstance(w_members, list) else "w:?"
    return "%s|%s" % (i_part, w_part)


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state) + i_count (the cached I workable count, or None)
# + w_members (the fetched W numbers, or None) -> (action, new_rec, reason).
# The partition has "something to audit" when I>0 OR W is non-empty. action:
#   "skip"  -- nothing positive to audit AND at least one half UNDETERMINED
#              (fetch failed/refused, or i_count None on a ❓-blocked pane) ->
#              NEVER a nudge, NEVER a state change (the safe direction, #535
#              undetermined→silent; never clear a rec that might still be parked);
#   "clear" -- BOTH halves determined-empty (I==0 AND W==[]) -> partition drained,
#              pop the sid's rec (episode end);
#   "wait"  -- something to audit but still inside the grace / reping window ->
#              persist (seed first_seen/w_seen, refresh sig), no nudge;
#   "nudge" -- something to audit past the cadence -> the caller ATTEMPTS a
#              verified send and advances last_nudge only on a CONFIRMED submit.

def _recheck_decision(rec, i_count, w_members, now, cadence):
    """Pure verdict for ONE session's partition audit (#552 generalises #547's
    W-only decider to BOTH directions). `rec` is the persisted per-sid dict (or
    None/malformed for a fresh session). `i_count` is the cached `I` workable
    count (int), or None when UNDETERMINED (a ❓-blocked / cheap-verdict pane whose
    backlog the glance never consulted). `w_members` is the fetched `--ops-wait`
    numbers (list, possibly empty), or None when UNDETERMINED (a gh error/refusal).

    The partition has something to audit when I>0 OR W is non-empty. When NEITHER
    half is positive, the verdict splits on determinability: BOTH halves
    determined-empty (`i_count==0` AND `w_members==[]`) is a genuinely drained
    partition → `clear` (pop the rec); otherwise at least one half is UNDETERMINED
    → `skip` (leave the rec unchanged, no nudge) — never a false re-surface, and
    never a clear of a rec whose W might still be parked. This preserves #547's
    `skip:undetermined` exactly (W=None with I=0/undetermined still skips).

    A non-empty partition nudges only when `now - (last_nudge or first_seen) >=
    cadence`: `first_seen` gives the initial grace (never nag a session about a
    partition it JUST arrived at — after the owner's manual re-audit, a freshly
    seen pane sits in grace and stays silent, the montalu3 fixture), and
    `last_nudge` becomes the reping anchor afterwards. `last_nudge` is PRESERVED
    unchanged here (a "nudge" verdict is an INTENT; the caller sets last_nudge=now
    only after a transcript-confirmed submit, so a swallowed send retries next
    sweep rather than silently skipping a whole cadence). `first_seen` is seeded to
    `now` on first sight — so a long-pre-existing partition (present before this
    job existed) is first nudged one cadence after deploy, the safe cold-start.

    `w_seen` is a PER-TICKET first-seen map ({str(number): ts}) for the nudge
    text's truthful, per-member W-park age (#594): each current W member keeps
    its EXISTING anchor and a member NEWLY appearing in W is seeded to `now`
    (≈ its `ops-wait` label-add within one fetch TTL); a member that leaves W is
    DROPPED (only the current set survives). This replaces the single
    partition-level `w_first_seen` anchor whose bug it fixes — that one W-wide
    anchor was seeded when W FIRST became non-empty and preserved across sweeps
    regardless of WHICH tickets were in W, so a ticket added to an already-
    non-empty W set inherited a stale (up to days-old) age. Per-ticket, a ticket
    parked TODAY reads "~0h" even alongside a member parked days ago. `w_seen` is
    None (dropped) when W is empty. Cold-start caveat (unchanged from `first_seen`):
    a member present before this job first saw the session is seeded on first
    SIGHT, so its age is under-reported until it re-enters W — the SAFE direction
    (never the over-report the incident was about), and the exact GitHub
    label-add EVENT timestamp is deliberately NOT fetched (a per-ticket timeline
    query the repo twice rejected on this path, #507/#550)."""
    i_pos = isinstance(i_count, int) and i_count > 0
    w_pos = isinstance(w_members, list) and bool(w_members)
    if not (i_pos or w_pos):
        i_empty = isinstance(i_count, int) and i_count <= 0
        w_empty = isinstance(w_members, list) and not w_members
        if i_empty and w_empty:
            return ("clear", None, "drained")
        return ("skip", rec, "undetermined")
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)):
        last_nudge = None
    if w_pos:
        prior_seen = rec.get("w_seen") if isinstance(rec, dict) else None
        prior_seen = prior_seen if isinstance(prior_seen, dict) else {}
        w_seen = {}
        for n in _member_numbers(w_members):
            key = str(n)
            ts = prior_seen.get(key)
            w_seen[key] = ts if isinstance(ts, (int, float)) else now
    else:
        w_seen = None
    new_rec = {"first_seen": first_seen, "last_nudge": last_nudge,
               "w_seen": w_seen,
               "sig": _partition_sig(i_count, w_members)}
    anchor = last_nudge if last_nudge is not None else first_seen
    if now - anchor >= cadence:
        return ("nudge", new_rec, "due")
    return ("wait", new_rec, "grace")


# The GENERIC I→W/U re-audit clause (#552): the FALLBACK when the per-member
# audit list cannot be fetched (fetch failed / not wired). Instructs the loop to
# re-audit its `I` list against the #526/#539 parking shapes. The word
# "re-audituj" is the stable token the wiring test keys on.
_I_CLAUSE = (
    "I→W/U: re-audituj každý `I` tiket proti #526/#539 tvarom — fix-class čakajúci "
    "na externú udalosť → `ops-wait` s dôkazom (W); odoslaný acceptance thread → "
    "`ops-wait` (W); deferred-thread na pomenovanú udalosť → `ops-wait` (W); "
    "doručená živá owner-otázka → needs-answer/needs-decision (U); owner fyzický/"
    "manuálny krok pri rigu → `needs-owner-action` (U, #601 — owner nie je tretia "
    "strana); bare `needs-acceptance` queued na owner-schválenie → `U` (#622 — "
    "nikdy `I`); HOTOVÝ tiket (merged/released/akceptovaný) čakajúci UŽ LEN na "
    "gatekeepera aby ho ZAVREL → `GATEKEEPER-ACTION:` (`needs-gatekeeper`) → gk N, "
    "NIE `ops-wait` (gatekeeper nie je tretia strana, #636); len reálne "
    "dispatchovateľná kódová práca ostáva `I`.")

# #578 — the NAMED per-I-member audit. The gk `I 16` incident showed the generic
# clause above is too weak: the session COULD enumerate its I members and STILL
# left them mislabelled (a 7-ticket release-tail bare, a `ready-for-review` review
# lane mis-called "stream-owned"). So when the member list IS available
# (`_watchdog_i_members_fetch` via `--audit`), the nudge NAMES each member with
# age + labels + a SHAPE-specific instruction, making "I know but I don't label"
# impossible to sustain silently. Judgment stays in the session (no
# auto-labelling); the supervisor still sets/clears every label with evidence.
_I_MEMBER_MAX = 12   # bound the keystroke — name the oldest N, summarise the rest
# labels that make a member DEFINITIVELY a gk review lane (the #4497 shape): only
# this box can review/merge/close it, so it is DISPATCHED, never parked.
_REVIEW_LANE_LABELS = ("ready-for-review", "needs-gatekeeper")


def _member_age_s(created_at, now):
    """Age in seconds of an ISO-8601 `createdAt` (`2026-08-03T00:00:00Z`), or
    None when unparsable/blank. Tolerant of the trailing `Z` (older Pythons'
    `fromisoformat` needs `+00:00`); a clock-skew future createdAt returns a
    negative age which `_fmt_age` clamps to 0."""
    if not isinstance(created_at, str) or not created_at.strip():
        return None
    import datetime
    s = created_at.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    try:
        return now - dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _i_member_line(rec, now):
    """One named audit line: `#N (~age) [labels] → <short shape tag>`. The FULL
    shape instructions live once in the clause HEADER (`_i_clause_named`); the
    per-member tag is a short pointer so the keystroke stays bounded even at 12
    members. A `ready-for-review`/`needs-gatekeeper` member is a review lane
    (dispatch); any other is a bare/other member (ops-wait if gated)."""
    labels = [lb for lb in (rec.get("labels") or []) if isinstance(lb, str)]
    age_s = _member_age_s(rec.get("createdAt"), now)
    age = _fmt_age(age_s) if age_s is not None else "?"
    lbl = ",".join(labels) if labels else "bez labelov"
    tag = ("review lane → DISPATCHNI"
           if any(lb in _REVIEW_LANE_LABELS for lb in labels)
           else "bare/other → ops-wait ak gated")
    return "#%s (%s) [%s] %s" % (rec.get("number"), age, lbl, tag)


def _i_clause_named(i_members, now):
    """The named I→W/U audit clause — the two shape instructions once in the
    header, then each valid member spelled out (oldest first) with a short tag,
    capped at `_I_MEMBER_MAX` with a `(+K ďalších)` tail so the keystroke stays
    bounded. `; `-joined (never a raw newline — a newline in a typed keystroke can
    submit prematurely)."""
    valid = [r for r in (i_members or [])
             if isinstance(r, dict) and isinstance(r.get("number"), int)]
    ordered = sorted(valid, key=lambda r: r.get("createdAt") or "")
    shown = ordered[:_I_MEMBER_MAX]
    body = "; ".join(_i_member_line(r, now) for r in shown)
    tail = len(ordered) - len(shown)
    tail_str = ("; (+%d ďalších `I` tiketov — re-audituj aj tie)" % tail
                if tail > 0 else "")
    return ("I→W/U (menovite): re-audituj tieto `I` tikety proti #526/#539 — "
            "`ready-for-review`/`needs-gatekeeper` člen JE tvoja review lane, "
            "DISPATCHNI ju (neparkuj); bare/umbrella člen gated na release/"
            "checklist/iný ticket → olabeluj `ops-wait` s pomenovaným eventom "
            "(supervisor, s dôkazom); label mení supervisor s dôkazom, nikdy "
            "automaticky. Členovia: %s%s; bare `needs-acceptance` queued na "
            "owner-schválenie → `U` (#622); len reálne dispatchovateľná kódová "
            "práca ostáva `I`." % (body, tail_str))

# The W→I re-check clause (#547, preserved as a subset of the combined nudge):
# names the parked W numbers each with ITS OWN park age (#594 — per-ticket, not a
# single stale partition anchor), instructs re-checking the external event the
# park comment records.
_W_CLAUSE = (
    "W→I: parknuté `ops-wait` tikety %s — klientske Discuss vlákna čítaj ASPOŇ "
    "1×/hodinu (či neprišla odpoveď, #607) a over externý stav ktorý si "
    "pri parkovaní zapísal do komentára (odpoveď vo vlákne / vyšlý release = verzia "
    "ŽIVÁ na cieli: deploy-set zelený + priame čítanie verzie, NIE run-terminal — "
    "#588): ak už dorazil, zlož `ops-wait` s dôkazom a vráť tiket do práce; ak sa "
    "stále čaká, potvrď to. MIS-SHAPE (#601): ak je blocker OWNER (fyzický krok "
    "pri rigu / jeho prítomnosť / manuálna akcia), NIE JE to W — prelabeluj "
    "`needs-owner-action` a tiket sa presunie do U (owner nie je tretia strana). "
    "MIS-SHAPE (#607): ak re-entry event je ROZHODNUTIE/ODPOVEĎ OWNERA (nie tretej "
    "strany, nie fyzický krok), tiež NIE JE to W — „na U sa vždy pýtam“: prelabeluj "
    "`needs-answer`/`needs-decision` a DORUČ otázku (❓ ping), tiket sa presunie do U. "
    "MIS-SHAPE (#636): ak je blocker GATEKEEPER (review / release / ZAVRIEŤ po "
    "release+akceptácii), NIE JE to W — gatekeeper NIE JE tretia strana, je to "
    "menovaný fleet aktér s vlastnou hand-off lane: podaj `GATEKEEPER-ACTION:` "
    "(auto-label `needs-gatekeeper`) A ZLOŽ `ops-wait` — tiket sa presunie do gk N "
    "(stream) / do I gk-boxu, nie visí vo W ktoré nikto netlačí.")

# The #570 stale sub-clause — appended to the W clause when any parked W member
# has gone COLD (no fresh ≤24h stream-push evidence: `stale!` in `--ops-wait`,
# parsed by `_watchdog_ops_wait_fetch` into `member["stale"]`). NAMES the stale
# members and states the doctrine action: re-verify the blocker by RE-READING
# the referenced ticket + remind the third party TODAY with a ticket comment
# (that comment IS the freshness evidence — #570 bod 3 "tlač dopredu každý deň").
_W_STALE_CLAUSE = (
    "STALE (>24h pracovných dní bez odpovede, víkend sa nepočíta — #607) %s: "
    "kontrakt už nie je len tag — MUSÍ ísť DNES do klientskeho Discuss vlákna "
    "VECNÁ pripomienka (mention + partner_ids + konkrétna vec čo čakáš, nie "
    "prázdny ack), over blocker RE-ČÍTANÍM referencovaného tiketu a na tikete "
    "komentárom zaznač že pripomienka odišla (ten komentár JE evidencia).")

# The #636 gk-handoff sub-clause — appended to the W clause when any parked W
# member ALSO carries a gk hand-off label (`gk-handoff!` in `--ops-wait`, parsed
# into `member["gk_handoff"]`). NAMES the contradictory members and states the
# doctrine action: this is post-release limbo — the ticket is a gk hand-off the
# stale `ops-wait` HIDES from gk N (stream) / the gk box's I, so DROP the
# `ops-wait` (the gatekeeper is not a third party, #601 parallel); if it is not
# yet in the gk queue, also add `needs-gatekeeper` (GATEKEEPER-ACTION: close #N).
_W_GK_HANDOFF_CLAUSE = (
    "GK-HANDOFF (post-release limbo, #636) %s: tento W tiket NESIE aj "
    "needs-gatekeeper/ready-for-review — je to gk hand-off, NIE tretia strana. "
    "ZLOŽ `ops-wait` (a ak ešte nie je v gk fronte, podaj `GATEKEEPER-ACTION:` → "
    "`needs-gatekeeper`), aby sa presunul do gk N / do I gk-boxu a gatekeeper ho "
    "zavrel — nesmie visieť vo W, ktoré nikto netlačí. Label mení supervisor.")


def _discuss_audit_scope(cwd):
    """True iff the repo at `cwd` is odoo-erp -- the ONLY repo whose tickets
    bind client Odoo Discuss threads (#695; the same repo scope the #627 close
    gate holds). Resolved via `notify.repo_name_for` (the fleet's one
    cwd->repo-name derivation); ANY failure -- notify unimportable, no git
    remote, a resolver error -- reads False, the fail-safe direction for a
    nudge clause. Called only in the ~daily nudge branch, so the git read
    never lands on the per-sweep hot path."""
    if _repo_name_resolver is None:
        return False
    try:
        return (_repo_name_resolver(cwd) or "").strip().lower() == "odoo-erp"
    except Exception:
        return False


# #695 -- the DISCUSS-AUDIT clause: a CLOSED thread-bound ticket (the manual
# `Discuss-thread:` mark OR the #657-mandated `discuss.channel_<N>` deep URL)
# with no `Discuss-closed:`/`Discuss-defer:` disposition means a client thread
# is rotting with no closing note (montalu5: 4 tickets, threads rotted for
# days). The watchdog structurally CANNOT do this audit itself -- it has no
# Discuss credential and per-ticket gh reads of CLOSED tickets on the sweep
# path are the #507/#550-rejected shape -- so, exactly like the #607 hourly
# thread-check, the DUTY is named in the daily nudge with the exact command,
# and the session's own judgment + gh does the read. Doctrine-only: no count
# changes, no new fetch, the supervisor still posts/records everything itself.
_DISCUSS_ORPHAN_CLAUSE = (
    "DISCUSS-AUDIT (#695): ZAVRETÝ thread-bound tiket (nesie `Discuss-thread:` "
    "značku alebo discuss.channel_<N> deep URL) bez `Discuss-closed:`/"
    "`Discuss-defer:` = klientske vlákno hnije bez záverečnej správy. Spusti "
    "audit: `gh issue list -s closed -S \"Discuss-thread OR discuss.channel_\" "
    "-L 30`, over dispozíciu každého; kde chýba, POŠLI záverečnú správu do "
    "vlákna (handover-compose.md) a zapíš `Discuss-closed: msg <id>` na tiket.")


def _nudge_text(i_count, w_members, now, w_seen, i_members=None,
                discuss_audit=False):
    """The partition-audit keystroke injected into the armed loop. Carries the
    shared `stuck-check: ` prefix (own-payload recognition + machine-prompt
    exclusion — see the module docstring) and composes ONE ping from whichever
    direction(s) apply: the I→W/U re-audit clause when I>0, the W→I re-check clause
    (naming the parked W numbers each with ITS OWN per-ticket park age from
    `w_seen`, #594) when W is non-empty. When ONLY W applies (I==0), the text
    degrades to #547's W-only nudge; when both apply, both clauses ride one
    keystroke. Two W SUB-clauses are appended when their flags fire: the #636
    `gk-handoff!` sub-clause (a W member ALSO carrying needs-gatekeeper/
    ready-for-review — the post-release-limbo contradiction; drop ops-wait) and
    the #570 `stale!` sub-clause. The label change is always the SUPERVISOR's with
    evidence, never an auto-unlabel by the watchdog.

    `w_seen` (#594): the PER-TICKET first-seen map ({str(number): ts}) that ages
    each W member from its OWN W entry, never a single stale partition-level
    anchor. A member missing from the map, or an unusable map, ages `?` (never a
    fabricated age).

    `i_members` (#578): the fetched WORKABLE (I) member records (`{number,
    createdAt, labels}`, via `_cached_i_members`). When present + non-empty, the
    I clause NAMES each member with age + labels + a shape-specific instruction
    (`_i_clause_named`); when None (fetch failed / not wired) or empty it degrades
    to the generic `_I_CLAUSE` — never a crash, never a bare count.

    `discuss_audit` (#695): True appends the odoo-erp-scoped DISCUSS-AUDIT
    clause (closed thread-bound tickets without a closing-note disposition) —
    the caller resolves it via `_discuss_audit_scope(cwd)` in the nudge branch."""
    i_pos = isinstance(i_count, int) and i_count > 0
    w_pos = isinstance(w_members, list) and bool(w_members)
    clauses = []
    if i_pos:
        valid = [r for r in (i_members or [])
                 if isinstance(r, dict) and isinstance(r.get("number"), int)]
        clauses.append(_i_clause_named(valid, now) if valid else _I_CLAUSE)
    if w_pos:
        clauses.append(_W_CLAUSE % _members_line_aged(w_members, w_seen, now))
        gk_handoff = _gk_handoff_numbers(w_members)
        if gk_handoff:
            clauses.append(_W_GK_HANDOFF_CLAUSE
                           % " ".join("#%d" % n for n in sorted(gk_handoff)))
        stale = _stale_numbers(w_members)
        if stale:
            clauses.append(_W_STALE_CLAUSE
                           % " ".join("#%d" % n for n in sorted(stale)))
    if discuss_audit:
        clauses.append(_DISCUSS_ORPHAN_CLAUSE)
    return (
        "stuck-check: partition-audit — over či `I`/`W` labely tvojho `/goal` "
        "partition sedia s doktrínou #526/#539. %s Label mení supervisor s "
        "dôkazom, nikdy automaticky." % " ".join(clauses))


# --- ORPHAN REAPER ---------------------------------------------------------

def _prune_ops_wait_orphans(wrecs, visited_sids, now,
                            ttl_s=OPS_WAIT_RECHECK_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["ops_wait_recheck"]` (keyed
    on `sid = tpath.stem`). A rec is normally popped at episode end (W set goes
    empty), but a session that DIES while W is still parked would leak its rec
    forever. Reap ONLY when BOTH: (1) the sid was NOT a live candidate pane THIS
    sweep (`visited_sids` — session gone/superseded), AND (2) it is malformed OR
    its `lts` (write-time age anchor) is older than `ttl_s`. The visited gate is
    PRIMARY: a live pane (its loop body reached `sid = tpath.stem`) is never
    reaped regardless of `lts` staleness. A FUTURE `lts` (clock skew) is kept (the
    safe direction, #519). Never a per-episode pop here; never raises. Faithful
    mirror of `goal._prune_goal_mark_orphans` / `_prune_goal_lane_orphans`."""
    if not isinstance(wrecs, dict):
        return
    for sid in [k for k, v in list(wrecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        wrecs.pop(sid, None)


# --- ORCHESTRATOR ----------------------------------------------------------

def goal_ops_wait_recheck(now, run, wrecs, sid, cwd, pid, tpath, loc,
                          dry_run, handled, ops_wait_fetch, state,
                          sleep_fn=None, cadence=None, i_count=None,
                          i_members_fetch=None):
    """Audit ONE armed candidate pane's partition (I→W/U + W→I) and, on cadence,
    deliver ONE verified re-audit nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `wrecs[sid]` (persisted
    by the shared `state`); returns a list of decision log lines (#486 — every
    verdict logged, never a silent skip). `dry_run` mutates no persistent state
    and sends nothing.

    `ops_wait_fetch(cwd)` is the injected W seam (network call kept out of run_once
    unit tests, exactly like `backlog_fetch`): returns the parked W numbers
    (`list[int]`), or None when unmeasurable — None fails safe to `skip`. It is
    read through `_cached_ops_wait` (per-repo TTL cache) so the gh subprocess
    fires at most once per repo per TTL, never every sweep per pane (#547 review).

    `i_count` is the `I` workable count — passed by the caller as the ALREADY
    resolved + cached `glance.backlog` (`_cached_backlog_count`), so this job adds
    ZERO new fetch (#552; #547-review cache lesson). `None` (an awaiting-user /
    cheap-verdict pane whose backlog the glance never consulted) is UNDETERMINED
    and fails the I direction safe — no I nudge into a ❓-blocked pane.

    Keystroke coordination reuses the sibling machinery verbatim: `send_verified`
    (transcript-proof submit; a swallowed Enter is NOT booked, its text restored),
    `_janitor_mark_watch`/`_janitor_clear_watch` (a residual stuck send stays
    reclaimable via the shared `stuck-check: ` prefix), and the per-sweep
    `handled` set (at most ONE keystroke per pane per sweep across the keystroke
    jobs — this job runs AFTER the lane nudge in the loop, so a pane the lane
    nudge already typed is deferred to next sweep, and a nudge WE send claims the
    sid so any keystroke job later in the SAME sweep skips it).

    `i_members_fetch(cwd)` (#578): the injected I-member seam — the WORKABLE
    member records (`{number, createdAt, labels}`) for the NAMED audit clause,
    read through `_cached_i_members` (per-repo TTL cache) and ONLY inside the
    nudge branch, so the `--audit` subprocess fires at most once per repo per TTL
    AND only when actually nudging (~daily), never every sweep. None (not wired /
    fetch failed) degrades the nudge to the generic `_I_CLAUSE` — never a crash."""
    logs = []
    cadence = cadence or _cadence()
    # CACHED per-repo (#547 review): the fetch fires at most once per repo per
    # OPS_WAIT_FETCH_TTL_S, NOT every sweep per pane — the sibling of
    # `_cached_backlog_count`. A cache/fetch error reads as None -> skip.
    try:
        members = _cached_ops_wait(cwd, ops_wait_fetch, state, now)
    except Exception as e:
        logs.append("ops-wait-recheck %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    rec = wrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason = _recheck_decision(rec, i_count, members, now,
                                                cadence)

    if action == "skip":
        logs.append("ops-wait-recheck %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action == "clear":
        if not dry_run:
            wrecs.pop(sid, None)
        logs.append("ops-wait-recheck %s -> clear (partition drained — I==0 AND "
                    "W==[])" % loc)
        return logs

    # action in ("wait", "nudge"): persist the seeded/refreshed rec (first_seen,
    # w_seen, sig, lts age-anchor for the reaper). last_nudge is only
    # advanced on a CONFIRMED send below.
    if not dry_run:
        new_rec["lts"] = now
        wrecs[sid] = new_rec

    sig = new_rec["sig"]
    if action == "wait":
        anchor = new_rec["last_nudge"] or new_rec["first_seen"]
        logs.append("ops-wait-recheck %s -> wait (partition %s, %s since anchor "
                    "< cadence)" % (loc, sig, _fmt_age(now - anchor)))
        return logs

    # action == "nudge"
    if handled is not None and sid in handled:
        logs.append("ops-wait-recheck %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("ops-wait-recheck %s -> WOULD-NUDGE partition %s" % (loc, sig))
        return logs

    # #578: fetch the WORKABLE I members (cached, once per repo per TTL) ONLY here
    # in the nudge branch, so the `--audit` subprocess never fires on a wait/skip
    # sweep. AND only when the I direction is actually active (`i_count > 0`) — a
    # W-only nudge (i_count 0/None) would not render the I clause, so the fetch
    # would be wasted (#578 review 🔵). None (not wired / fetch failed / W-only)
    # degrades to the generic clause.
    i_members = (_cached_i_members(cwd, i_members_fetch, state, now)
                 if isinstance(i_count, int) and i_count > 0 else None)
    # #695: the DISCUSS-AUDIT clause scope is resolved HERE, in the nudge
    # branch only (a per-cwd git-remote read at most ~once a day per pane),
    # never on the per-sweep hot path.
    text = _nudge_text(i_count, members, now, new_rec["w_seen"],
                       i_members=i_members,
                       discuss_audit=_discuss_audit_scope(cwd))
    # Mark janitor provenance BEFORE the send (mirrors the lane nudge): a residual
    # stuck send stays reclaimable, cleared only on a delivered submit.
    watchdog._janitor_mark_watch(state, pid, now)
    # #594: read BOTH the confirmed-submit bool AND the delivered-unconfirmed
    # signal. Injecting into an actively-cycling armed loop, the Enter submits
    # (box clears — delivered/queued) but the transcript confirmation races, so a
    # bool-only read leaves `last_nudge` unadvanced and the nudge re-fires +
    # re-delivers EVERY sweep (the reported 6×/35min). A DELIVERED submit
    # (confirmed OR box-bare-unconfirmed) advances the dedup; only a GENUINE
    # swallow / abort (neither) retries — send_verified distinguishes them
    # (`delivered_unconfirmed` is set only in the box-cleared branch, never on
    # the undone-swallow path).
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out)
    delivered = ok or bool(send_out.get("delivered_unconfirmed"))
    if not delivered:
        # Genuinely unverified (swallowed / aborted / unreadable) — transient,
        # retried next sweep. Do NOT advance last_nudge (else a swallowed send
        # silently skips a whole cadence), do NOT claim the pane in `handled`.
        logs.append("ops-wait-recheck %s -> submit-unverified (partition %s, "
                    "retry next sweep)" % (loc, sig))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    wrecs[sid] = new_rec
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("ops-wait-recheck nudge %s -> partition %s (tracked %s)%s"
                % (loc, sig, _fmt_age(now - new_rec["first_seen"]), note))
    return logs
