"""#1178 — the queue-arrival rider watches the box's OWN workable set, reaches a
supervisor pane whose /goal ENDED, and never fails silently.

INCIDENT (controller, 2026-09-29): the airuleset supervisor ended its /goal with
stop condition (B) at 01:xx. The owner filed #1176 at 06:29, the montalu4 stream
filed #1177 at 06:45; both were workable in `core-quals --list`, yet the idle
supervisor never learned of them until the owner said so by hand.

What this leaf adds to the #733 rider (`queue_arrival_recheck`), which is at its
size ceiling and keeps the decider / floor / confirmed-baseline semantics
unchanged:

(a) The arrival set is the box's own workable set — the #1067 quals snapshot's
    `i_members` (the SAME `bucketize` "I" bucket `core-quals|slice-quals --list`,
    the footer and the /goal stop-proof count), read by
    `airuleset._watchdog_queue_fetch` as `[{"id", "title"}]` records.
    `review_ids` normalises them (legacy int lists still work), `nudge_text`
    names each new ticket with its title.
(b) `ended_pane_recheck` — a NON-armed FLOW (review / undeclared) pane whose
    goal is DEFINITELY cleared by the STRUCTURED goal_mark (`goal_armed is
    False` from `src == "goal_mark"`: this session HAD a /goal; a pane that never
    had one is an interactive session, not a supervisor) and whose last turn
    ended `✅` is a target too. (An ACHIEVED loop — the 🏁 line sits directly
    above `✅ DONE:` — keeps its mark "set" because CC writes no cleared marker,
    so it already rides the armed path, #1128.)
    The rider seeds and tracks it like any pane; DELIVERY is held
    (`deliver_hold`) unless the pane sits at a bare idle `❯`, its transcript is
    not a live turn (#1110) and no human touched it recently (the #731/#566
    recent-human gate). Base stays OLD on a hold, so the arrival delivers on the
    first eligible sweep. The nudge is ONE `send_verified` machine nudge, turned
    into the #1157 pointer row at the one chokepoint; it never types `/goal`.
(c) `note_fetch` / `status_lines` — an undetermined fetch that persists (>= 10
    min AND >= 3 sweeps) logs ONE `WARN queue-arrival …` line per hour and shows
    in `nudges status` as `queue-arrival: undetermined since <ts> (<reason>)`.
    The reason comes from the reader (`ops_wait_refresh.problem`), so "gh is not
    on PATH" (the controller root cause, fixed in `cmd_watchdog`) is readable.
"""
import json
import os
import time

HEALTH_KEY = "queue_arrival_health"
# how long / how many sweeps an undetermined fetch must persist before it is a
# WARN and a `nudges status` line (a single gh blip is not an incident).
UNDETERMINED_WARN_AFTER_S = 10 * 60
UNDETERMINED_WARN_MIN_SWEEPS = 3
UNDETERMINED_WARN_EVERY_S = 3600
# a health record whose pane stopped reporting is dropped after a day and is
# no longer shown after an hour (the pane/session went away).
HEALTH_SHOW_S = 3600
HEALTH_DROP_S = 24 * 3600
# the own-set fetch is a local snapshot FILE read (no gh), so its outer cache
# only needs to dedupe the panes of one sweep: detection latency stays bounded
# by the snapshot's own 5-min refresh, not snapshot TTL + a 10-min outer TTL.
FETCH_TTL_S = 60
# review F2: the per-sid record tag of an own-set base. A record a pre-#1178
# sweep wrote (it carries `lts`) without it holds the gk hand-off union and is
# RESEEDED once instead of announcing the whole backlog as new.
REC_SRC = "own-i"
# review F5: the #993 dep classify costs >= 1 gh call per arrival and runs every
# sweep while a wave is held; memoise per number and cap the calls per sweep.
CLASSIFY_TTL_S = 30 * 60
CLASSIFY_MAX_PER_SWEEP = 10
CLASSIFY_KEY = "queue_arrival_classify"
# how many new tickets are named WITH their title before the rest are listed
# as bare numbers (the nudge is hard-capped at NUDGE_MAX_CHARS).
MAX_TITLED = 4
TITLE_MAX_CHARS = 60


def review_ids(cur):
    """`(ids, titles)` from the own-set fetch. `cur` is a list of `{"id",
    "title"}` records (production) or of ints (legacy / tests) or None.
    Malformed entries are dropped (never raises); None / not-a-list → `(None,
    {})`, the decider's undetermined signal."""
    if not isinstance(cur, list):
        return None, {}
    ids, titles = [], {}
    for r in cur:
        if isinstance(r, dict):
            try:
                n = int(r["id"])
            except (KeyError, TypeError, ValueError):
                continue
            t = r.get("title")
            titles[n] = t if isinstance(t, str) else ""
        elif isinstance(r, int) and not isinstance(r, bool):
            n = r
        else:
            continue
        ids.append(n)
    return ids, titles


def _short(title):
    t = " ".join((title or "").split())
    return t if len(t) <= TITLE_MAX_CHARS else t[:TITLE_MAX_CHARS - 1] + "…"


def _named(arrivals, titles, max_named):
    shown = []
    for i, n in enumerate(arrivals[:max_named]):
        t = _short((titles or {}).get(n)) if i < MAX_TITLED else ""
        shown.append("#%d (%s)" % (n, t) if t else "#%d" % n)
    extra = len(arrivals) - len(shown)
    return ", ".join(shown) + (" (+%d ďalších)" % extra if extra > 0 else "")


def nudge_text(arrivals, cur_count, titles=None, max_chars=700, max_named=12):
    """The queue-arrival keystroke: the shared `stuck-check: ` own-payload
    prefix, then ONE headline sentence naming the new tickets (the #1157
    pointer row shows it), then the how-to. Never starts with `/` (never a slash
    command). Capped at `max_chars` on a word boundary."""
    text = (
        "stuck-check: do tvojho I pribudli tickety (nové alebo vrátené): %s — "
        "spracuj ich; ak tvoj /goal "
        "už skončil, spusti /autopilot. Spolu %d v I (tvoj workable backlog, "
        "ten istý set ako core-quals/slice-quals --list a /goal stop-proof). "
        "Poradie riadi priorita dohodnutá v tejto session (architektúra > "
        "architecture-rework > prio:bounce > backlog, #993), nie tento nudge. "
        "NEdispatchni dep-wait jednotku (otvorené Depends-on). Ak už na nich "
        "robíš, potvrď." % (_named(arrivals, titles, max_named), cur_count))
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1].rsplit(" ", 1)[0] + "…"


def current_rec(rec, role):
    """The persisted per-sid record the decider may trust: a malformed one is
    {} (seed), and — review F2 — a review-role record a pre-#1178 sweep wrote
    (`lts` present, no `src` tag: its base is the gk hand-off union) is {} too,
    so the first own-set read SEEDS. The caller stamps `src` on every record it
    persists for the review role."""
    if not isinstance(rec, dict):
        return {}
    if role != "infra" and "lts" in rec and rec.get("src") != REC_SRC:
        return {}
    return rec


def memo_classify(state, classify_fn, now, cap=CLASSIFY_MAX_PER_SWEEP, scope=""):
    """Wrap the #993 per-arrival classify (review F5): a class read within
    CLASSIFY_TTL_S is reused from `state[CLASSIFY_KEY]`, at most `cap` fresh
    reads happen per wrapper (one wrapper per rider call), and a number past the
    cap reads "dep-wait" — HELD and retried next sweep, never a guessed
    dispatch. Keys are `<scope>#<number>` (scope = the cwd: two repos on one
    box never share a class). None stays None (unwired = all dispatchable)."""
    if classify_fn is None:
        return None
    memo = state.setdefault(CLASSIFY_KEY, {}) if isinstance(state, dict) else {}
    for k in [k for k, v in memo.items()
              if not (isinstance(v, list) and len(v) == 2
                      and now - _num(v[1]) < CLASSIFY_TTL_S)]:
        memo.pop(k, None)
    left = [cap]

    def _fn(number):
        key = "%s#%s" % (scope, number)
        hit = memo.get(key)
        if hit:
            return hit[0]
        if left[0] <= 0:
            return "dep-wait"
        left[0] -= 1
        cls = classify_fn(number)
        memo[key] = [cls, now]
        return cls
    return _fn


def infra_authority_skip(cwd):
    """The #1029 INFRA queue is a gk concern: None on a full-authority box, else
    the skip reason (`not-full-authority (<a>)` / `authority-unresolved`). Only
    the infra role keeps this gate since #1178 dropped it for the own set."""
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception as e:  # noqa: BLE001 — unresolvable fails safe to skip
        return "authority-unresolved (%r)" % (e,)
    return None if authority == "full" else "not-full-authority (%s)" % authority


# --- (c) undetermined health ------------------------------------------------

GENERIC_REASON = "fetch returned None (cached failure or unresolvable authority)"


def undetermined_reason(cwd):
    """Why the own-set read for `cwd` came back None THIS process, or None when
    this process has no specific reason (a sweep served the cached None);
    `note_fetch` then keeps the earlier specific reason (review F8)."""
    try:
        from watchdog import ops_wait_refresh
        return ops_wait_refresh.problem(cwd)
    except Exception:  # noqa: BLE001 — a reason is display only
        return None


def _num(v, default=0):
    """A persisted number, or `default` for a legacy/corrupt value."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _persistent(rec, now):
    return (now - _num(rec.get("since"), now) >= UNDETERMINED_WARN_AFTER_S
            and _num(rec.get("n")) >= UNDETERMINED_WARN_MIN_SWEEPS)


def _clock(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def note_fetch(state, key, determined, now, loc, reason=None, dry_run=False):
    """Track the rider's fetch health per `key` (the cwd; `<cwd> (infra)` for
    the infra queue). A determined fetch drops the record. An undetermined one
    starts/extends it and returns ONE `WARN queue-arrival …` line at most once
    per UNDETERMINED_WARN_EVERY_S once it is persistent, else []. `dry_run`
    mutates nothing. Never raises on a malformed record."""
    if not isinstance(state, dict):
        return []
    old = state.get(HEALTH_KEY)
    recs = {k: v for k, v in (old.items() if isinstance(old, dict) else ())
            if isinstance(v, dict) and now - _num(v.get("last")) <= HEALTH_DROP_S}
    if determined:
        recs.pop(key, None)
        rec, out = None, []
    else:
        rec = dict(recs.get(key) or {})
        rec.update(since=_num(rec.get("since"), now), n=_num(rec.get("n")) + 1,
                   last=now, loc=loc)
        if reason or not rec.get("reason"):
            rec["reason"] = reason or GENERIC_REASON
        out = []
        warned = rec.get("warned")
        if _persistent(rec, now) and (not isinstance(warned, (int, float))
                                      or now - warned >= UNDETERMINED_WARN_EVERY_S):
            rec["warned"] = now
            out.append("WARN queue-arrival %s -> undetermined since %s (%s) — "
                       "arrival detection is blind until the fetch recovers"
                       % (loc, _clock(rec["since"]), rec.get("reason", "?")))
        recs[key] = rec
    if not dry_run and (recs or old is not None):
        state[HEALTH_KEY] = recs
    return out


def status_lines(home=None, now=None, state=None):
    """`nudges status` lines for every persistent, recently-seen undetermined
    fetch, read from the watchdog state file (or `state`). [] when healthy."""
    now = time.time() if now is None else now
    if state is None:
        path = os.path.join(home or os.path.expanduser("~"), ".claude",
                            "api-watchdog-state.json")
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return []
    recs = state.get(HEALTH_KEY) if isinstance(state, dict) else None
    out = []
    for key, rec in sorted((recs or {}).items()):
        last = _num(rec.get("last")) if isinstance(rec, dict) else 0
        if (isinstance(rec, dict) and _persistent(rec, last)
                and now - last <= HEALTH_SHOW_S):
            out.append("queue-arrival: undetermined since %s (%s) — %s"
                       % (_clock(_num(rec.get("since"))), rec.get("reason", "?"),
                          rec.get("loc", key)))
    return out


# --- (b) an idle supervisor pane whose goal ENDED -------------------------

def ended_candidate(glance):
    """True when a NON-armed pane may be watched for arrivals: its goal is
    DEFINITELY cleared by the STRUCTURED goal_mark (`goal_armed is False` with
    `src == "goal_mark"` — review F1: the heartbeat's False alone means only "no
    goal marker in the tail", i.e. an interactive session that never had a
    /goal; the armed-unknown None fails closed, #486 G6), its last turn ended
    `✅` (marker "done"), and the `queue-arrival` kind is ON on this box
    (checked BEFORE any fetch, so a box whose profile keeps the kind off never
    spawns a refresher for a goal-less pane)."""
    import watchdog
    return (getattr(glance, "goal_armed", None) is False
            and getattr(glance, "src", None) == "goal_mark"   # review F1
            and getattr(glance, "marker", None) == "done"
            and watchdog.nudges_enabled("queue-arrival"))


def ended_hold_reason(captured, sid, cwd, tpath, pid, run, now):
    """None when the ended supervisor pane may take the nudge NOW, else the
    hold reason. Ordered cheapest first; every gate fails toward HOLD."""
    from watchdog.pane_classify import pane_at_idle_prompt
    from watchdog import goal_turn_liveness as _live
    from watchdog import goal as _goal
    if not pane_at_idle_prompt(captured or ""):
        return "not-idle-prompt"
    if _live.turn_live(_live.transcript_age_s(tpath, now)):
        return "busy-transcript"
    if _goal._recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run):
        return "human-active"
    return None


def ended_pane_recheck(glance, now, run, qrecs, sid, cwd, pid, tpath, loc,
                       dry_run, handled, queue_fetch, state, **kw):
    """Run the #733 rider on an ENDED supervisor pane (see `ended_candidate`),
    holding delivery on `ended_hold_reason`. Returns the rider's log lines, or
    [] when the pane is not a candidate (no fetch, no state)."""
    if queue_fetch is None or not ended_candidate(glance):
        return []
    role_fn = kw.get("resolve_role_fn")
    try:   # review F1: only the FLOW supervisor is told to run /autopilot
        role = role_fn(cwd) if role_fn is not None else None
    except Exception:  # noqa: BLE001 — a resolver fault never guesses
        return []
    if role not in (None, "review"):
        return []
    from watchdog import queue_arrival_recheck as _qa
    captured = kw.get("captured")
    return _qa.goal_queue_arrival_recheck(
        now, run, qrecs, sid, cwd, pid, tpath, loc, dry_run, handled,
        queue_fetch=queue_fetch, state=state,
        deliver_hold=lambda: ended_hold_reason(captured, sid, cwd, tpath, pid,
                                               run, now),
        **kw)
