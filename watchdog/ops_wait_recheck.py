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

BUSY-PANE GATE + COMPACT TRIGGER (#714, owner 2026-08-26, david2@subdev). Two
coupled defects made the nudge TYPE into the prompt and NEVER submit — the text
parked orphaned ("neda sa tam teraz pracovat"). (1) NO BUSY-PANE GATE: the
orchestrator delivered via `send_verified` with no check for CC's "Waiting for N
background agents to finish" state — a submit into that transient mid-turn state
is swallowed and the text parks (`pane_at_idle_prompt` misses it — the box shows
a free bare `❯`, the Waiting spinner is a row ABOVE it). FIX = `_pane_busy_waiting`
(the caller's `captured`, `watchdog._BG_AGENTS_WAIT_RX`, NARROWED to the Waiting
line only — NOT the `◯` strip rows an armed loop always carries, #605): defer
WITHOUT a keystroke, retry a later sweep. (2) UNBOUNDED NUDGE: `_nudge_text`
enumerated every W member with per-member ages and stacked full-doctrine
sub-clauses (thousands of chars), which collapses into a `[Pasted text …]`
placeholder the send/undo/janitor machinery cannot recover (the `stuck-check:`
prefix is swallowed inside it). FIX = a compact TRIGGER (I=%d/W=%d COUNTS + the
commands `slice-quals --audit`/`--ops-wait` + compact flag counts with doctrine
pointers), hard-capped at NUDGE_MAX_CHARS; the members live in the command
OUTPUT, the doctrine in the session's modules — the #578 named I-member audit +
its watchdog fetch were REMOVED (the session runs `--audit` itself). (3) BOUNDED
RETRY (MAX_SEND_FAILS): a persistently-swallowing NON-busy pane backs off a full
cadence instead of typing every 60s sweep forever.

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
is NAMED in the `_W_TRIGGER` nudge (#714 compact); the watchdog-side ENFORCEMENT is the SAME as
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

RELEASE-LANDED ESCALATION (#698, owner hard-fail 2026-08-25 — ~25 release-gated
W tickets hung parked long after their release landed). The W clause's release
re-check used to be WORDING only (the #588 deployed-state doctrine is NAMED in
the W trigger, but no code path ever READ any release state), so a session skims
the same ~daily reminder and a landed release never re-enters mechanically.
Now, in the nudge branch only: when a parked W member's TITLE names a
release/version/stage (`_release_shaped_numbers` — the `--ops-wait` fetch now
carries `member["title"]`, field 4 it already printed, zero new gh calls), the
repo's OWN release-train state is read through `release_gap.
_cached_release_state` (the SHARED #616 per-repo TTL cache in
`state["release_state_cache"]` — one gh fetch per repo per TTL across BOTH
job-20 consumers, never a parallel query) and IFF the train is PROVEN drained
(`train` True — the staging branch verified to exist, so a 2-branch repo with a
stray `develop` never counts — AND ahead == 0 AND no release in flight) the W
nudge gains a compact `RELEASE LANDOL` flag (#714 — a COUNT, not a member
enumeration): "release LANDOL — verify per the #588 deployed-state doctrine and
clear `ops-wait` WITH evidence TODAY"; above RELEASE_LANDED_OWNER_ASK_N members it additionally instructs the
SESSION to summarise to the owner via the standard ❓ channel (never a new alarm
class, #546/#688 — the session pings, the watchdog never does). HONEST
BOUNDARY: 2-branch repos (`train` False), fork-no-merge boxes (origin = the
FORK, whose frozen branches could read drained forever — authority-guarded in
the orchestrator), and a member whose release reference lives only in a
COMMENT (the #550 T1 machine-readable-park reopen trigger) stay on the generic
clause; a CROSS-REPO release wait cannot be verified here — a release-shaped
member IS still named when the LOCAL train drains, and that case is carried by
the clause's own "iný/cudzí release" honesty branch, not by suppression; a
repo with a PERPETUAL integration gap (continuous merges) is escalated only in
its drained windows — the deliberate cost of never claiming an unproven
"landed" (per-event refinements are a tracked follow-up). An undetermined/
unproven read SUPPRESSES the escalation, never invents it. The supervisor
stays the ONLY one clearing
`ops-wait` with evidence; this changes the nudge WORDING only — cadence, counts
and labels are untouched (the #547/#552/#570/#636 pattern).
"""
import os
import re

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

# #714 — the nudge is a TRIGGER, not a textbook. Hard cap on the keystroke so it
# never grows into the multi-KB wall the incident produced (full doctrine + a
# named list of 53 W tickets), which (a) collapses into a `[Pasted text …]`
# placeholder the send/undo/janitor machinery cannot recover — the `stuck-check:`
# own-payload prefix is swallowed INSIDE the placeholder, so the janitor never
# reclaims the orphan — and (b) is unreadable. The doctrine lives in the
# session's own modules (statusline-vocabulary.md); the W/I member lists live in
# the `slice-quals --ops-wait` / `--audit` OUTPUT (tagged per member) — so the
# nudge carries COUNTS + the commands + doctrine-ticket pointers, never the
# enumeration. The mandatory I/W trigger core is a fixed template + two small
# numbers, always well under this cap; optional flag/discuss detail is appended
# greedily only while it fits.
NUDGE_MAX_CHARS = 700

# #714 -- BOUNDED RETRY. A genuine swallow (typed, submit unconfirmed, box NOT
# cleared) leaves `last_nudge` unadvanced so the nudge retries next sweep -- the
# #594 dedup covers only the delivered-unconfirmed race, NOT a persistent
# swallow, so a NON-busy pane that keeps swallowing would be TYPED into every 60s
# sweep forever (the reported retry storm — "neda sa tam teraz pracovat"). After
# this many CONSECUTIVE undelivered sends, back off a full cadence instead of
# retrying every sweep, bounding the storm to ≤MAX_SEND_FAILS keystrokes per
# cadence. Reset to 0 on any delivered send. (The busy-pane gate below is the
# PRIMARY fix — it defers WITHOUT typing, so a Waiting-state pane never even
# reaches a send and never contributes to this counter.)
MAX_SEND_FAILS = 3


def _pane_busy_waiting(captured):
    """True iff the pane shows CC's "Waiting for N background agents to finish"
    spinner (#714 — the david2@subdev incident state): the supervisor turn has
    ENDED and is blocked waiting for a background worker to complete before
    re-invocation, so a submitted Enter is swallowed/queued and the nudge parks
    orphaned in the input box (`pane_at_idle_prompt` does not catch it — the box
    still shows a free bare `❯`, the Waiting spinner is a row ABOVE it).

    Reuses `watchdog._BG_AGENTS_WAIT_RX` — the SAME signal job-20 goal re-arm
    already gates on via `_pane_has_bg_agent` — but NARROWED to the Waiting line
    only, NOT the agent-strip `◯` worker rows: an armed autopilot loop ALWAYS
    carries those, so gating on them would DEFER the nudge on every sweep and
    starve it forever (the #611 dead-letter class). The Waiting state is
    transient, so the nudge simply defers to a later sweep, when the pane is
    genuinely idle at `❯` and the submit lands. Fail-safe False on empty/None."""
    return bool(captured) and bool(watchdog._BG_AGENTS_WAIT_RX.search(captured))


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


def _release_recheck_numbers(members):
    """The subset of `_member_numbers` flagged `recheck!` (#699) — a RELEASE-
    parked W ticket OVERDUE for its hourly deployed-state re-check (no fresh <=1h
    working-time OWN re-check evidence). Only the structured shape carries the
    flag, so a legacy int list yields an EMPTY list (no recheck sub-clause — the
    safe/unchanged direction, exactly like `_stale_numbers`/`_gk_handoff_numbers`)."""
    return [m["number"] for m in (members or [])
            if isinstance(m, dict) and m.get("release_recheck")
            and isinstance(m.get("number"), int)]


def _acceptance_numbers(members):
    """The subset of `_member_numbers` whose base reason is `acceptance` (#753
    (b)) — a client-thread-parked W member the session must re-audit for a newer
    client reply/reaction. Only the #753 structured shape carries the flag, so a
    legacy int list yields an EMPTY list (no UNPARK-AUDIT sub-clause — the
    safe/unchanged direction, exactly like `_stale_numbers`)."""
    return [m["number"] for m in (members or [])
            if isinstance(m, dict) and m.get("acceptance")
            and isinstance(m.get("number"), int)]


# #698 — above this many release-landed-flagged W members on one box, the
# escalated sub-clause additionally instructs the session to summarise the
# state to the owner via the standard ❓ channel (the ticket's own >N=5; never
# a new alarm class — the session pings, the watchdog never does, #546/#688).
RELEASE_LANDED_OWNER_ASK_N = 5

# #754 — the aggregate W-drain escalation threshold. When the parked-W set
# exceeds this size, `_flag_items` prepends a W-OVERFLOW clause instructing the
# session to run a W-drain pass (zavri / odparkuj / cituj blocker) BEFORE
# dispatching new I work, and to summarise a consolidation proposal to the owner
# ❓ if the bucket cannot be drained. The goal state of the loop is I0 ∧ U0 ∧ W0
# — W is a DEBT bucket with a strop, not a terminal ticket state. LOCKED equal to
# the canonical `cli_quals.OPS_WAIT_WDRAIN_THRESHOLD` by
# tests/test_wdrain_lane_754.py (kept local here to preserve this module's
# hermeticity, the same convention as RELEASE_LANDED_OWNER_ASK_N), so the CLI
# `--ops-wait` OVER-THRESHOLD marker and this escalation fire at the SAME |W|.
WDRAIN_ESCALATE_N = 8

# #698 — a release-SHAPED title: a release keyword (release/vydanie/
# nasadenie/deploy), a `stage-N` token, or a v-PREFIXED version (`v2.181`).
# The ticket-body heuristic scoped to the TITLE (the only member text the
# fetch carries — a comment-only reference is the documented #550 T1
# boundary), NARROWED by adversarial review: a bare `\d+\.\d+` arm matched
# dates ("19.8."), amounts ("2.5"), and IPs — a false-positive class far
# wider than the accepted keyword one — and every live true positive carries
# a keyword or v-prefix anyway, so the bare-decimal arm is dropped (a bare
# "čaká na 2.181" title is the documented FN boundary -> generic clause). A
# remaining keyword false positive is SAFE (the escalated clause demands
# evidence and offers the "wait still holds — write why" branch); a false
# negative degrades to the generic clause (pre-#698 behavior).
_RELEASE_SHAPED_RX = re.compile(
    r"(?i)(?:\breleas|\bvydan|\bnasaden|\bdeploy|\bstage-\d+\b|"
    r"\bv\d+\.\d+(?:\.\d+)*\b)")


def _release_shaped_numbers(members):
    """The subset of `_member_numbers` whose TITLE names a release/version/
    stage token (#698) — only the dict shape carries a title, so a legacy int
    list (or a member without one) yields an EMPTY list: no escalation, the
    safe/unchanged direction, exactly like `_stale_numbers`. A malformed
    element is dropped (never raises); a bool "number" (an `int` subclass)
    is excluded, mirroring `_release_train_drained`'s own bool guard."""
    return [m["number"] for m in (members or [])
            if isinstance(m, dict)
            and isinstance(m.get("number"), int)
            and not isinstance(m.get("number"), bool)
            and isinstance(m.get("title"), str)
            and _RELEASE_SHAPED_RX.search(m["title"])]


def _release_train_drained(rstate):
    """#698 — True IFF the repo-level release-train state PROVES a real,
    fully-drained 3-branch train: `train` True (the staging branch was
    verified to exist by `_watchdog_release_state_fetch`), `ahead` == 0 (the
    integration branch is not ahead of prod) and `in_flight` False (no open
    release PR, no running deploy). Anything else — None/undetermined, a
    missing or False `train` (a 2-branch repo, or a legacy rstate), a live
    gap, a moving train, a bool `ahead` — is False: the escalated "release
    LANDOL" claim never rides an unproven state."""
    if not isinstance(rstate, dict):
        return False
    ahead = rstate.get("ahead")
    return (rstate.get("train") is True
            and isinstance(ahead, int) and not isinstance(ahead, bool)
            and ahead == 0 and rstate.get("in_flight") is False)


def _sig(members):
    """A stable signature of the parked W set (sorted numbers, comma-joined) —
    stored so a reader can see WHICH tickets a rec is tracking, and so a future
    set-change refinement has a hook. Numeric sort so the sig is order-stable."""
    return ",".join(str(n) for n in sorted(_member_numbers(members)))


def _members_line(members):
    """`#A #B #C` for the nudge text, oldest-number-first for stable reading."""
    return " ".join("#%d" % n for n in sorted(_member_numbers(members)))


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


# #714 -- the compact partition-audit TRIGGER text. The nudge NAMES doctrine
# tickets + the commands the session runs itself (`slice-quals --audit` /
# `--ops-wait`), carries only COUNTS, and NEVER enumerates members or restates
# the full doctrine -- the doctrine lives in the session's own modules
# (statusline-vocabulary.md) and the tagged member lists in the command output.
_NUDGE_HEAD = ("stuck-check: partition-audit -- over `I`/`W` labely `/goal` "
               "partition proti doktríne #526/#539. ")
_NUDGE_TAIL = " Label mení supervisor s dôkazom, nikdy automaticky."

# The I→W/U trigger (#552/#578): re-audit the whole I list against the parking
# shapes by running `slice-quals --audit` (it enumerates each member with labels
# + a shape hint -- the #578 named audit moved SESSION-side, out of the keystroke
# payload, #714). "re-audituj" is the stable token the wiring test keys on.
# `%d` = the I count.
_I_TRIGGER = (
    "I=%d: spusti `slice-quals --audit`, re-audituj tvary (gated → ops-wait W; "
    "owner-otázka/krok → needs-owner-action U #601/#607; gk-close → "
    "needs-gatekeeper #636; acceptance → U #622); len dispatchovateľná ostáva I.")

# The W→I trigger (#547/#588/#607): re-check the parked external events. COUNT
# only -- the members + their stale!/recheck!/gk-handoff! tags are in the
# `slice-quals --ops-wait` OUTPUT, never the keystroke. `%d` = the W count.
_W_TRIGGER = (
    "W=%d parknutých: spusti `slice-quals --ops-wait`, Discuss 1×/hod (#607), "
    "over blockery (release = #588 deployed-state, nie run-terminal), zlož "
    "`ops-wait` s dôkazom; mis-shape → owner needs-owner-action U #601 / gk #636.")


def _flag_items(w_members, release_landed):
    """Self-contained compact flag sentences for the fired W sub-categories
    (#714) -- each carries its identifying token + doctrine ticket # + a COUNT,
    NEVER a member enumeration (the session gets WHICH members, tagged, from
    `slice-quals --ops-wait`). Ordered most-urgent first. The #698 release-landed
    escalation keeps its >N owner-ask tail. Returns a list of standalone
    sentences; the caller appends as many as fit under NUDGE_MAX_CHARS."""
    items = []
    # #754 — the AGGREGATE W-OVERFLOW clause, FIRST so it survives the greedy
    # NUDGE_MAX_CHARS cap. The goal state is I0 ∧ U0 ∧ W0: W is a DEBT bucket, and
    # an over-threshold |W| is a strop breach the loop must act on BEFORE new I
    # work — never a passive parking lot (live: odoo-erp montalu3 grew to W 34
    # while the armed loop kept dispatching I lanes).
    w_total = len(_member_numbers(w_members))
    if w_total > WDRAIN_ESCALATE_N:
        # Compact (kept short so it survives the greedy NUDGE_MAX_CHARS cap even
        # when I>0 and every per-category flag also fires — the aggregate drain
        # signal must never be the item that gets dropped).
        items.append(
            "W-OVERFLOW %d>%d (#754 -- W-drain PRED I: "
            "zavri/odparkuj/cituj; nedá sa → zhrň ownerovi ❓)."
            % (w_total, WDRAIN_ESCALATE_N))
    stale = len(_stale_numbers(w_members))
    if stale:
        items.append("STALE %d (#607 -- pošli vecnú pripomienku "
                     "DNES)." % stale)
    recheck = len(_release_recheck_numbers(w_members))
    if recheck:
        items.append("RELEASE-RECHECK %d (#699 -- deployed-state 1×/hod)."
                     % recheck)
    gk = len(_gk_handoff_numbers(w_members))
    if gk:
        items.append("gk-handoff %d (#636 -- zlož ops-wait → "
                     "needs-gatekeeper)." % gk)
    landed = [n for n in (release_landed or [])
              if isinstance(n, int) and not isinstance(n, bool)]
    if landed:
        it = ("RELEASE LANDOL %d (#698 -- over #588 + zlož ops-wait DNES)"
              % len(landed))
        if len(landed) > RELEASE_LANDED_OWNER_ASK_N:
            it += ", nad %d zhrň ownerovi ❓" % RELEASE_LANDED_OWNER_ASK_N
        items.append(it + ".")
    return items


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


# #695 -- the DISCUSS-AUDIT trigger (odoo-erp only): a CLOSED thread-bound ticket
# without a `Discuss-closed:`/`Discuss-defer:` disposition means a client thread
# is rotting with no closing note (montalu5). The watchdog cannot read Discuss
# (#550), so the DUTY is named with the exact command; the session's own gh does
# the read. Doctrine-only, no count change.
_DISCUSS_TRIGGER = (
    "DISCUSS-AUDIT (#695): zavri tikety bez `Discuss-closed:` -- "
    "`gh issue list -s closed -S \"discuss.channel_\" -L 30`.")


# #753 (b) -- the UNPARK-AUDIT clause (odoo-erp only, the SAME scope as
# DISCUSS-AUDIT): a client-thread-parked (`acceptance`) W member may have a newer
# client reply/reaction than our last push. The watchdog cannot read Discuss
# (#550/#695), so the DUTY is session-delegated: re-read the cited threads and,
# on a newer client reply/reaction, clear `ops-wait` WITH a citation. A COUNT
# (the members are in `slice-quals --ops-wait`, reason `acceptance`), never a
# member enumeration (#714). `%d` = the acceptance-parked W count.
_UNPARK_AUDIT_TRIGGER = (
    "UNPARK-AUDIT %d (#753): re-read cited Discuss threads of acceptance-W "
    "members (aj reakcie #745) -- novšia klientska odpoveď po našom pushi ⇒ "
    "zlož `ops-wait` s citáciou.")


def _nudge_text(i_count, w_members, now=None, w_seen=None, *,
                release_landed=None, discuss_audit=False, unpark_audit_n=0):
    """The compact partition-audit TRIGGER keystroke (#714 -- replaced the
    per-member enumeration + full-doctrine wall that parked orphaned in the
    incident). Carries the `stuck-check: ` prefix (janitor own-payload
    recognition + machine-prompt exclusion, see the module docstring), the
    I->W/U and W->I triggers as COUNTS + the commands the session runs itself
    (`slice-quals --audit` / `--ops-wait`), and compact flag sentences for the
    fired W sub-categories (#570/#636/#698/#699/#695).

    HARD-CAPPED at NUDGE_MAX_CHARS: the mandatory I/W trigger core (a fixed
    template + two small numbers) always fits; the optional flag/discuss detail
    is appended GREEDILY, keeping only the items that fit, so an incident-scale
    pathological partition (all flags on 53 members) never breaches the cap --
    the dropped detail stays recoverable via the commands. The W/I MEMBERS
    themselves live in the command output, NEVER the keystroke; the doctrine
    lives in the session's own modules.

    `w_seen` is accepted and IGNORED (the compact nudge no longer renders
    per-member ages -- the actionable freshness signal is the `stale!` tag in
    `slice-quals --ops-wait`, #714). `release_landed` (#698): the release-shaped
    W numbers whose repo's release train the caller PROVED drained -- non-empty
    fires the RELEASE-LANDOL flag; None/empty is byte-identical to no flag.
    `discuss_audit` (#695): True appends the odoo-erp-scoped DISCUSS-AUDIT
    trigger. `unpark_audit_n` (#753 (b)): a POSITIVE count appends the
    odoo-erp-scoped UNPARK-AUDIT trigger (re-read the acceptance-parked members'
    cited Discuss threads for a newer client reply/reaction); 0/non-int → no
    clause (byte-identical to the pre-#753 nudge)."""
    i_pos = isinstance(i_count, int) and i_count > 0
    w_list = w_members if isinstance(w_members, list) else []
    w_count = len(_member_numbers(w_list))
    core = []
    if i_pos:
        core.append(_I_TRIGGER % i_count)
    if w_count:
        core.append(_W_TRIGGER % w_count)
    core_body = " ".join(core)
    # optional detail (self-contained flag/audit sentences), appended greedily
    # while under the cap -- the members stay in the command output, not here.
    optional = []
    if w_count:
        optional.extend(_flag_items(w_list, release_landed))
    if discuss_audit:
        optional.append(_DISCUSS_TRIGGER)
    # #753 (b): the acceptance-unpark audit — only when there ARE acceptance-parked
    # W members (a positive count), so a repo with none never carries the clause.
    if isinstance(unpark_audit_n, int) and not isinstance(unpark_audit_n, bool) \
            and unpark_audit_n > 0:
        optional.append(_UNPARK_AUDIT_TRIGGER % unpark_audit_n)
    detail = []
    for item in optional:
        cand = (_NUDGE_HEAD + core_body + " "
                + " ".join(detail + [item]) + _NUDGE_TAIL)
        if len(cand) <= NUDGE_MAX_CHARS:
            detail.append(item)
    body = core_body + ((" " + " ".join(detail)) if detail else "")
    text = _NUDGE_HEAD + body + _NUDGE_TAIL
    if len(text) <= NUDGE_MAX_CHARS:
        return text
    # Defensive (unreachable with the fixed template + the greedy append above):
    # truncate at a word boundary, preserving the `stuck-check:` prefix.
    return text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


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


def _book_unverified_send(rec, new_rec, loc, sig, now):
    """#714 bounded retry: book ONE undelivered send onto the persisted rec
    (`new_rec` IS `wrecs[sid]`, so mutation persists). Under MAX_SEND_FAILS it
    increments the consecutive-failure counter and retries next sweep; at
    MAX_SEND_FAILS it BACKS OFF one full cadence (advance last_nudge, reset the
    counter) so a persistently-swallowing NON-busy pane is not typed into every
    60s sweep forever. The counter crosses the JSON persistence boundary, so a
    corrupt/legacy non-int reads as 0 and never raises (the module's own
    persisted-state discipline, cf. `_cached_member_fetch`'s `ts` guard).
    Returns the decision log line."""
    prior = rec.get("send_fails")
    fails = (prior if isinstance(prior, int) and not isinstance(prior, bool)
             else 0) + 1
    if fails >= MAX_SEND_FAILS:
        new_rec["last_nudge"] = now
        new_rec["send_fails"] = 0
        return ("ops-wait-recheck %s -> submit-unverified x%d — backing off one "
                "cadence (bounded retry #714, partition %s)" % (loc, fails, sig))
    new_rec["send_fails"] = fails
    return ("ops-wait-recheck %s -> submit-unverified (attempt %d/%d, retry next "
            "sweep, partition %s)" % (loc, fails, MAX_SEND_FAILS, sig))


# --- ORCHESTRATOR ----------------------------------------------------------

def goal_ops_wait_recheck(now, run, wrecs, sid, cwd, pid, tpath, loc,
                          dry_run, handled, ops_wait_fetch, state,
                          sleep_fn=None, cadence=None, i_count=None,
                          release_state_fetch=None, captured=None):
    """Audit ONE armed candidate pane's partition (I→W/U + W→I) and, on cadence,
    deliver ONE verified re-audit nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `wrecs[sid]` (persisted
    by the shared `state`); returns a list of decision log lines (#486 — every
    verdict logged, never a silent skip). `dry_run` mutates no persistent state
    and sends nothing.

    `ops_wait_fetch(cwd)` is the injected W seam (network call kept out of run_once
    unit tests, exactly like `backlog_fetch`): returns the parked W member
    dicts (`{number, stale, gk_handoff, title}` — legacy bare ints accepted),
    or None when unmeasurable — None fails safe to `skip`. It is
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

    `captured` (#714): the pane capture the caller already read for the lane
    nudge (ZERO new capture) — the BUSY-PANE GATE. When it shows CC's "Waiting
    for N background agents to finish" state (`_pane_busy_waiting`), the nudge is
    DEFERRED (no keystroke, last_nudge unadvanced, `handled` unclaimed) so it
    retries a later sweep: a submit into that transient mid-turn state is
    swallowed and parks the text orphaned in the input box (the incident). None
    (unwired / older caller) skips the gate — the send's own bare/collapsed
    checks still apply. The compact nudge (#714) is small enough that a genuine
    swallow is cleanly undone by `send_verified` and the `stuck-check:` prefix
    stays visible for the janitor reclaim (it no longer collapses into a
    `[Pasted text]` placeholder the way the old multi-KB wall did).

    `release_state_fetch(cwd)` (#698): the SAME injected seam the #616
    release-gap rider uses, read here ONLY inside the nudge branch and ONLY
    when a release-SHAPED W member exists, through `release_gap.
    _cached_release_state` (the SHARED `state["release_state_cache"]` per-repo
    TTL cache — one gh fetch per repo per TTL across BOTH job-20 consumers). A
    PROVEN drained train fires the compact `RELEASE LANDOL` flag (#714 — a
    COUNT, via `release_landed`); None / not wired / not drained keeps the
    pre-#698 generic wording — the escalation fails safe, never invents a
    "landed" claim, and never touches a label (supervisor-only, with
    evidence)."""
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
    # #780 WRITER-SIDE LATCH (#741): a pending /compact for this session HOLDS the
    # partition-audit nudge — never push work into the armed loop while a
    # drained-boundary compact waits for its quiet window. Same shape as the
    # goal-family writers (goal.py:1792) and the busy-pane gate below: defer
    # WITHOUT a keystroke (last_nudge unadvanced, `handled` unclaimed) so it
    # retries a later sweep once the compact delivers. Lazy import (compact imports
    # watchdog — avoids any import-order cycle); fail-safe False on any error.
    from watchdog import compact as _compact
    if _compact.has_pending_request(sid):
        logs.append("ops-wait-recheck %s -> hold:compact-pending (pending "
                    "/compact; no nudge until it delivers)" % loc)
        return logs
    if handled is not None and sid in handled:
        logs.append("ops-wait-recheck %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep)" % loc)
        return logs
    # #714 BUSY-PANE GATE (the primary fix): NEVER type into a pane showing CC's
    # "Waiting for N background agents to finish" state — the submit is swallowed
    # and the text parks ORPHANED in the input box (the david2 incident). Defer
    # WITHOUT a keystroke (no type-and-fail loop, no send_fails increment); the
    # transient Waiting state clears between turns and a later sweep delivers into
    # the genuinely-idle `❯`. last_nudge stays unadvanced (the persisted rec above
    # keeps first_seen/w_seen/sig), the pane is NOT claimed in `handled`.
    if _pane_busy_waiting(captured):
        logs.append("ops-wait-recheck %s -> skip:busy-bg-agent (pane waiting on a "
                    "background agent — deferred, retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("ops-wait-recheck %s -> WOULD-NUDGE partition %s" % (loc, sig))
        return logs

    # #698: the release-landed escalation — read the repo's release-train state
    # ONLY here in the nudge branch, ONLY when a release-shaped W member exists
    # (title names a release/version/stage), through the SHARED #616 per-repo
    # TTL cache (`state["release_state_cache"]` — one gh fetch per repo per
    # TTL across BOTH job-20 consumers, never a parallel query). Undetermined
    # (None) / not a PROVEN drained train / seam not wired -> `landed` None ->
    # the generic W clause (pre-#698 wording): the escalated "release LANDOL"
    # claim only ever rides a proven drained train. AUTHORITY guard (#698
    # review): the fetch resolves the repo from the checkout's ORIGIN slug —
    # on a fork-no-merge box origin is the FORK, whose frozen develop/staging/
    # main can read "train drained" FOREVER, a persistent false claim no
    # upstream release can correct. So the escalation runs only for full /
    # branch-merge authority (origin = the canonical repo there); fork-no-merge
    # and an unresolvable authority fail safe to the generic wording.
    rel_shaped = _release_shaped_numbers(members)
    rstate = None
    if rel_shaped and release_state_fetch is not None:
        try:
            import airuleset
            authority = airuleset.resolve_authority(cwd)
        except Exception:
            authority = None
        if authority in ("full", "branch-merge"):
            from watchdog import release_gap
            try:
                rstate = release_gap._cached_release_state(
                    cwd, release_state_fetch, state, now)
            except Exception:
                rstate = None
    landed = rel_shaped if _release_train_drained(rstate) else None
    # #695: the DISCUSS-AUDIT clause scope is resolved HERE, in the nudge
    # branch only (a per-cwd git-remote read at most ~once a day per pane),
    # never on the per-sweep hot path.
    # #753 (b): the same odoo-erp scope gates the UNPARK-AUDIT clause; its count
    # is the acceptance-parked W members from the ALREADY-fetched rows (zero new
    # gh calls), so the session re-audits their cited Discuss threads.
    _dscope = _discuss_audit_scope(cwd)
    text = _nudge_text(i_count, members, now, release_landed=landed,
                       discuss_audit=_dscope,
                       unpark_audit_n=(len(_acceptance_numbers(members))
                                       if _dscope else 0))
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
        # #714 BOUNDED RETRY: a genuine swallow leaves last_nudge unadvanced so it
        # retries next sweep — but a PERSISTENTLY-swallowing NON-busy pane would
        # then be typed into every 60s sweep forever (the retry storm; the #594
        # dedup covers only the delivered-unconfirmed race, not a persistent
        # swallow). `_book_unverified_send` counts the failure on the persisted
        # rec (new_rec IS wrecs[sid]) and backs off a full cadence after
        # MAX_SEND_FAILS. send_verified already backed our text OUT of the box on
        # a genuine swallow, so nothing parks, and the pane is NOT claimed.
        logs.append(_book_unverified_send(rec, new_rec, loc, sig, now))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    new_rec["send_fails"] = 0
    wrecs[sid] = new_rec
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("ops-wait-recheck nudge %s -> partition %s (tracked %s)%s"
                % (loc, sig, _fmt_age(now - new_rec["first_seen"]), note))
    return logs
