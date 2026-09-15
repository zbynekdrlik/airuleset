"""Central dead-box heartbeat-missing detector (#543) — watchdog job 35, dev1-only.

The per-box conformance self-check (#535, ``watchdog/conformance.py``, job 34)
has ONE structural gap it cannot cover: a DEAD box's self-check sends NOTHING.
If a box's watchdog stops running entirely (the timer dies, the box is off,
systemd is broken), there is no per-box process left to notice or report it —
silence looks like health. That is exactly the failure class #535 set out to
make visible, deferred (by its own docstring) to this central follow-up.

This job is the CENTRAL detector: dev1 (the always-on deploy source / fleet
coordinator) knows which boxes SHOULD be reporting and LOUD-pings the owner when
one goes silent past a threshold.

HEARTBEAT SOURCE (investigate-existing-first / #486 reuse — the ticket's own
suggested carrier). Every managed box already writes an hourly burn snapshot
(job 13); dev1 already collects each box's latest snapshot into
``~/.claude/burn-history/fleet.jsonl`` hourly (job 16, ``fleet_burn_job`` via
``_watchdog_fleet_fetch``, which filters ``"pending"`` rename targets out via
``_deployable_hosts`` — #537). A box present as a FRESH (no ``"error"`` key)
per-host entry in a fleet row WAS alive that hour; a dead/unreachable box shows
as ``{"error": ...}`` (with an optional ``"stale": True`` co-flag on a wrong-hour
row — ``merge_fleet_row`` only ever sets ``stale`` INSIDE the error branch, never
bare), or is absent. So each fleet row's own
``ts`` is a per-box liveness heartbeat, already centrally collected — NO new
producer, NO new ssh (fail2ban-safe). This detector detects WATCHDOG liveness
(via the burn snapshot), which is precisely the ticket's gap (watchdog dead =
no snapshot = error in the fleet), not a job-34-specific breakage.

FAIL-SAFE (the dispatch's hard constraint — an unreachable / broken central read
must NOT false-alarm every box). If the COLLECTION itself is stale (dev1's job 16
degraded → ``fleet.jsonl`` stops growing → every box's last-fresh ages out), the
per-box verdict would false-alarm the whole fleet. So the per-box check is
TRUSTED only when the newest fleet row is recent; a stale collection pings ONCE
about the COLLECTOR and SKIPS the per-box check — never N false dead-box pings.

DESIGN (#486 / mirrors #535's conformance module): tens of lines of STRUCTURED
comparisons. Each verdict is a PURE ``classify_<x>(facts) -> (name, ok, detail)``
with a THREE-valued ``ok``:

  * ``True``  — ALIVE / collection fresh (conformant);
  * ``False`` — genuine DEAD box / stalled collector (alarm);
  * ``None``  — UNDETERMINED (no fleet data, a brand-new box not yet fetched) →
                logged, NEVER an alarm.

``run_conformance_heartbeat_check`` owns ALL I/O behind injectable seams
(``fleet_rows_fn`` default ``burn.load_fleet``, ``hosts_fn`` default
``airuleset._deployable_hosts``, ``send_fn``, ``persist``), so the safety-critical
"never a false alarm" invariant lives in trivially-auditable pure functions.
"""
import datetime
import os

import watchdog

CONFORMANCE_HB_CHECK_S = 6 * 3600          # env AIRULESET_CONFORMANCE_HB_CHECK_S —
                                            # how often the central check runs; a
                                            # dead box past 36h is never urgent to
                                            # the minute, and the file read is local
CONFORMANCE_HB_CHECK_MIN_S = 3600          # floor for the env override (#504/#172):
                                            # a sub-hour value would run every sweep
CONFORMANCE_HB_STALE_S = 36 * 3600         # env AIRULESET_CONFORMANCE_HB_STALE_S — a
                                            # box silent (no fresh snapshot collected)
                                            # this long = dead. Generous: the snapshot
                                            # is hourly, so a healthy box is fresh
                                            # within ~1h; 36h survives a reboot / a
                                            # network blip / a brief job-16 hiccup
CONFORMANCE_HB_STALE_MIN_S = 6 * 3600      # floor: a sub-6h threshold would alarm on
                                            # an ordinary brief outage
CONFORMANCE_HB_REPING_S = 3 * 24 * 3600    # env AIRULESET_CONFORMANCE_HB_REPING_S —
                                            # re-remind cadence for an UNCHANGED dead
                                            # box: >1 day (never a daily re-spam) but
                                            # finite (never permanently silent, #134)
CONFORMANCE_HB_REPING_MIN_S = 24 * 3600    # floor
CONFORMANCE_HB_COLLECTION_STALE_S = 6 * 3600   # env
                                            # AIRULESET_CONFORMANCE_HB_COLLECTION_STALE_S
                                            # — if the NEWEST fleet row is older than
                                            # this, the COLLECTION itself stalled (job
                                            # 16 hourly + HH:05 delay → a healthy
                                            # collector has a row within ~1-2h). Skip
                                            # the per-box check, ping ONCE about the
                                            # collector. Kept well UNDER the per-box
                                            # stale threshold so a genuine collector
                                            # outage is caught long before it could
                                            # false-alarm every box. HARD-CLAMPED
                                            # below `stale` at use (#543 review F2):
                                            # a value >= stale would judge a stalled
                                            # collector "fresh" and re-open the
                                            # whole-fleet false alarm via the per-box
                                            # path — the invariant, not just a doc.
CONFORMANCE_HB_COLLECTION_STALE_MIN_S = 3600  # floor for the env override (#504): a
                                            # units-error sub-hour value would flag a
                                            # HEALTHY collector as stalled and silently
                                            # disable per-box dead-box detection
CONFORMANCE_HB_LOOKBACK_S = 4 * 24 * 3600  # how far back a host must APPEAR (fresh or
                                            # error) to be a KNOWN box vs a brand-new
                                            # one not yet fetched (the grace gate); a
                                            # host absent from every row this recent is
                                            # UNDETERMINED, never a false alarm. NOTE
                                            # (#543 review F3): the last-fresh instant
                                            # itself is scanned UNBOUNDED (all rows, see
                                            # `_scan`) so a stably-dead box's dedup sig
                                            # never flips just because its last-fresh
                                            # evidence slid out of this window.

_COLLECTION_KEY = "__collection__"          # reserved dedup key: collector-stalled ping
_ALLFLEET_KEY = "__allfleet__"              # reserved dedup key: whole-fleet-down ping
                                            # (#543 review F1 aggregate — see below)
_FEEDLAG_KEY = "__feedlag__"                # reserved dedup key: consumer-feed-lag ping
                                            # (#1019 — the feed claudy CONSUMES is
                                            # stale vs the producer)

# --- #1019 CONSUMER-FEED-LAG constants -------------------------------------
# #971 moved the fleet PRODUCER to /var/lib/airuleset/fleet.jsonl but added no
# check that the file claudy actually READS advances with it; a frozen consumer
# went unnoticed for 3.5 days. This guard section watches that gap on its OWN
# hourly cadence (the ticket's "malo zaznieť do hodiny"), independent of the 6h
# dead-box scan — no new job/timer, it rides the same job-35 send/persist/dedup
# seams.
FEED_LAG_CHECK_S = 3600                     # env AIRULESET_FLEET_LAG_CHECK_S — how
                                            # often the feed-age check runs; hourly
                                            # matches the producer's own hourly write
FEED_LAG_CHECK_MIN_S = 300                  # floor for the env override (#504): a
                                            # sub-5min value would run every sweep
FEED_LAG_STALE_S = 2 * 3600                 # env AIRULESET_FLEET_LAG_STALE_S — the
                                            # ticket's threshold: consumer more than
                                            # this behind the producer = alarm
FEED_LAG_STALE_MIN_S = 3600                # floor: a sub-1h threshold would alarm on
                                            # ordinary hourly-write jitter (a healthy
                                            # non-symlink hourly sync lags up to ~1h)


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _row_ts_epoch(row):
    """Epoch seconds of a fleet row's ISO ``ts``, or ``None`` when missing /
    unparsable (an UNDETERMINED row — never trusted as a heartbeat)."""
    if not isinstance(row, dict):
        return None
    ts = row.get("ts")
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def _is_fresh(entry):
    """A per-host fleet entry is FRESH (box alive that hour) iff it is a dict
    with NO ``"error"`` key. ``merge_fleet_row`` records a dead/stale/missing
    host as ``{"error": ...}`` (optionally ``"stale": True``); anything else
    (a real snapshot row dict) is fresh. A non-dict is never fresh."""
    return isinstance(entry, dict) and "error" not in entry


# --- PURE DECIDERS ---------------------------------------------------------
# facts in -> (name, ok, detail); ok True=alive/fresh / False=dead/stalled /
# None=undetermined (never an alarm).

def classify_collection(latest_row_ts, now, stale_s):
    """Is the CENTRAL fleet collection itself alive? ``latest_row_ts`` is the
    epoch of the newest fleet row (or ``None`` = no fleet data collected yet).
    Fresh collection → per-box checks are trustworthy. A stale collection
    (dev1's job 16 degraded) must NOT let every box false-alarm — it is its OWN
    single signal. No data → UNDETERMINED (a fresh dev1, never collected)."""
    if latest_row_ts is None:
        return ("collection", None, "žiadne fleet dáta — kontrola preskočená")
    age = now - latest_row_ts
    if age <= stale_s:
        return ("collection", True, "fleet zber čerstvý (posledný záznam pred ~%dh)"
                % round(age / 3600))
    return ("collection", False,
            "fleet zber na dev1 (job 16) zastal — posledný fleet záznam pred ~%dh; "
            "centrálna kontrola mŕtvych boxov je pozastavená, kým sa zber neobnoví. "
            "Skontroluj api-watchdog na dev1." % round(age / 3600))


def classify_box(host, last_fresh_ts, present, now, stale_s):
    """Is box ``host`` alive? ``last_fresh_ts`` = epoch of the newest fleet row
    where it was FRESH (or ``None`` = never fresh in the scan window).
    ``present`` = it appeared at all (fresh or error) in the window.

      * not present            → UNDETERMINED (brand-new box not yet fetched —
                                  NEVER a false alarm);
      * present, never fresh    → DEAD (its watchdog has never produced a fresh
                                  snapshot in the whole history);
      * present, last fresh > stale → DEAD (silent past the threshold);
      * else                    → ALIVE.

    OPERATOR NOTE (#543 review F4): a deploy target registered in ``REMOTE_HOSTS``
    but whose account/box is not live yet MUST carry ``"pending": True`` (#537) —
    ``_deployable_hosts`` then filters it out, so it is never fetched and never
    ``present``; without that flag it would be present-only-as-error → an
    immediate false dead-box ping on its first sweep.
    """
    if not present:
        return ("host", None,
                "box `%s` sa v čerstvých fleet dátach nevyskytuje — nový/nezberaný, "
                "žiaden alarm" % host)
    if last_fresh_ts is None:
        return ("host", False,
                "box `%s` neodpovedá — jeho watchdog nikdy nenahlásil čerstvý signál "
                "v sledovanom okne. Over či box beží a či beží api-watchdog.timer."
                % host)
    age = now - last_fresh_ts
    if age > stale_s:
        return ("host", False,
                "box `%s` neodpovedá — jeho watchdog prestal hlásiť (posledný signál "
                "pred ~%dh). Over či box beží a či beží api-watchdog.timer; na dev1 "
                "skús deploy/reštart." % (host, round(age / 3600)))
    return ("host", True,
            "box `%s` žije (posledný signál pred ~%dh)" % (host, round(age / 3600)))


def classify_feed_lag(producer_mtime, consumer_mtime, now, lag_s):
    """#1019 — is the fleet feed claudy CONSUMES fresh vs the PRODUCER? Both are
    epoch-seconds mtimes (``None`` = the file was unreadable). ``now`` is unused
    for the verdict itself (the lag is producer-vs-consumer, not vs wall-clock)
    but kept in the signature for symmetry with the other deciders + future use.
    Returns ``(name, ok, detail)`` with the same THREE-valued ``ok`` contract:

      * ``True``  — consumer within ``lag_s`` of the producer (or AHEAD) → fresh;
      * ``False`` — consumer more than ``lag_s`` behind the producer → ALARM;
      * ``None``  — either mtime unreadable → UNMEASURABLE, LOGGED, never an alarm
                    (the dispatch's hard constraint: an unreadable path must not
                    false-alarm — a healthy symlink read via sudo, a missing
                    producer on a fresh box, both land here safely).
    """
    if producer_mtime is None:
        return ("feed", None,
                "producentský feed /var/lib/airuleset/fleet.jsonl nečitateľný — "
                "kontrola veku preskočená (žiaden alarm)")
    if consumer_mtime is None:
        return ("feed", None,
                "konzumovaný feed (claudy) nečitateľný — nemerateľné, žiaden alarm")
    lag = producer_mtime - consumer_mtime
    if lag <= lag_s:
        return ("feed", True,
                "konzumovaný feed čerstvý (za producentom ~%dm)"
                % round(max(lag, 0) / 60))
    return ("feed", False,
            "konzumovaný fleet feed (claudy) zaostáva za producentom o ~%dh — "
            "claudy číta STARÉ fleet dáta (dashboard/detaily zamrznuté). Over "
            "symlink ~claudy/.claude/burn-history/fleet.jsonl → "
            "/var/lib/airuleset/fleet.jsonl (alebo CLAUDY_FLEET)." % round(lag / 3600))


def _sig_for_feed(consumer_mtime):
    """Dedup signature for a feed-lag episode. Keyed on the FROZEN consumer mtime:
    while the consumer stays stuck the sig is stable (no re-ping until ``reping``);
    when it advances and later freezes at a NEW instant that is a NEW episode
    (re-ping immediately). Mirrors ``_sig_for_box``. ``None`` never reaches here
    (an unreadable consumer is UNMEASURABLE, handled before the ping)."""
    return "feedlag:%d" % int(consumer_mtime)


def _default_feed_mtimes():
    """Default I/O seam for the feed-lag check: ``(producer_mtime, consumer_mtime)``
    in epoch seconds, each ``None`` on any read failure. PRODUCER
    (/var/lib/airuleset/fleet.jsonl) is owned by this account → direct ``os.stat``.
    CONSUMER (claudy's home feed) is under a foreign home not traversable by this
    account, so it is read via ``sudo -n stat -L -c %Y`` — the ``-L`` DEREFERENCES
    the symlink (GNU stat does NOT follow links by default; a bare ``stat`` returns
    the LINK's own mtime = when the symlink was created, a false lag) → the mtime
    of the DATA claudy actually reads (a healthy symlink to the producer gives the
    SAME mtime → lag ~0 → no alarm; a broken symlink → stat fails → None →
    UNMEASURABLE). ``os.stat`` on the producer likewise follows any symlink.
    Read-only, best-effort: the guard already relies on ``sudo -n`` on the
    controller (disk-guard root leg, ``_provision_shared_fleet_dir``); any
    failure → ``None`` → UNMEASURABLE (never a false alarm)."""
    import airuleset
    import subprocess
    producer = None
    try:
        producer = os.stat(airuleset.SHARED_FLEET_DIR / "fleet.jsonl").st_mtime
    except OSError:
        producer = None
    consumer = None
    try:
        consumer_path = str(airuleset._claudy_home_feed_path())
        r = subprocess.run(["sudo", "-n", "stat", "-L", "-c", "%Y", consumer_path],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            consumer = float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        consumer = None
    return (producer, consumer)


def _check_feed_lag(now, state, seen, send_fn, dry_run, persist, logs,
                    feed_mtimes_fn, interval, threshold, reping):
    """#1019 — the consumer-feed-lag section of job 35, on its OWN hourly cadence
    (``feed_lag_last_check``), placed BEFORE the 6h dead-box gate so a stale
    consumer is caught within the hour rather than after up to 6h. Reuses the
    job's ``seen`` dedup dict (key ``_FEEDLAG_KEY``), ``_ping``, ``send_fn`` and
    ``persist`` — no new store. Best-effort; an unmeasurable read LOGS and never
    alarms."""
    if not watchdog._sweep_due(state, "feed_lag_last_check", now, interval):
        return
    if not dry_run:
        # #172 kill-safety: stamp + persist the cadence marker BEFORE the read.
        state["feed_lag_last_check"] = now
        persist()
    try:
        producer_mtime, consumer_mtime = feed_mtimes_fn()
    except Exception as e:
        logs.append("conformance-hb [feed] mtime read zlyhal (%r) — preskočené"
                    % (e,))
        return
    name, ok, detail = classify_feed_lag(producer_mtime, consumer_mtime, now,
                                         threshold)
    logs.append("conformance-hb [feed] %s -- %s"
                % ({True: "OK", False: "LAG", None: "unknown"}[ok], detail))
    if ok is None:
        return                                    # unmeasurable — never an alarm
    if ok is True:
        if _FEEDLAG_KEY in seen and not dry_run:
            seen.pop(_FEEDLAG_KEY, None)          # caught up — episode cleared
        return
    _ping(send_fn, seen, _FEEDLAG_KEY, _sig_for_feed(consumer_mtime), detail,
          now, reping, dry_run, persist, logs, "feed",
          "🔴 **fleet feed zastal (konzument)**")


def _sig_for_box(last_fresh_ts):
    """Dedup signature for a dead-box episode. Keyed on the last-fresh instant:
    a box that recovers (fresh advances) then dies AGAIN gets a NEW sig, so the
    re-divergence re-pings immediately rather than being swallowed by ``reping``
    (mirrors #535's ``_sig_for``). ``None`` (never fresh) is its own stable sig."""
    return "hb:%s" % ("never" if last_fresh_ts is None else int(last_fresh_ts))


# --- ORCHESTRATOR ----------------------------------------------------------

def _scan(rows, now, lookback_s):
    """One pass over the fleet rows → ``(latest_row_ts, {host: last_fresh_ts},
    {present hosts})``. THREE different scopes, deliberately (#543 review F3):
      * ``latest``     — newest row ts across ALL rows (feeds classify_collection);
      * ``last_fresh`` — newest row-ts where the host was FRESH, across ALL rows
        (UNBOUNDED): a stably-dead box keeps its true last-fresh instant so its
        dedup sig never flips just because the evidence slid out of ``lookback_s``;
      * ``present``    — every host seen (fresh OR error) WITHIN ``lookback_s`` of
        ``now``: the brand-new-vs-known gate, so a box absent from every recent row
        is UNDETERMINED (grace), and a box REMOVED from the fleet long ago stops
        being ``present`` and never alarms.
    Rows with an unparsable ts are skipped."""
    latest = None
    last_fresh = {}
    present = set()
    for row in rows:
        rts = _row_ts_epoch(row)
        if rts is None:
            continue
        if latest is None or rts > latest:
            latest = rts
        per_host = row.get("per_host") if isinstance(row, dict) else None
        if not isinstance(per_host, dict):
            continue
        recent = (now - rts <= lookback_s)
        for name, entry in per_host.items():
            if recent:
                present.add(name)                 # presence is lookback-bounded
            if _is_fresh(entry):                  # last-fresh is UNBOUNDED
                if name not in last_fresh or rts > last_fresh[name]:
                    last_fresh[name] = rts
    return latest, last_fresh, present


def _ping(send_fn, seen, key, sig, detail, now, reping, dry_run, persist,
          logs, label, header):
    """Deduped LOUD ping for one dead-box / stalled-collector episode. A ping
    fires only when the episode is NEW (changed sig) or ``reping`` has elapsed
    since the last ping; the dedup memory is persisted BEFORE the send (#172-F3).
    ``header`` is the message's leading emoji + bold tag (a dead box vs a stalled
    collector are DIFFERENT alarms — the header says which). Returns True iff a
    ping was actually attempted."""
    prev = seen.get(key) or {}
    same = (prev.get("sig") == sig)
    pinged = prev.get("pinged_ts")
    if send_fn is None or dry_run or (
            same and pinged is not None and (now - float(pinged)) < reping):
        if dry_run:
            logs.append("conformance-hb [%s] WOULD-PING -- %s" % (label, detail))
        return False
    seen[key] = {"sig": sig, "pinged_ts": now}
    if not dry_run:
        persist()                          # dedup memory BEFORE the ping (#172-F3)
    status = send_fn(
        "%s -- %s" % (header, detail),
        # FRESH per real decision INSTANT, never a coarser bucket (#535 review
        # MAJOR-2): the per-key `seen` dedup above is the authoritative gate; a
        # bucketed sig-independent dedup_key would swallow exactly the changed-sig
        # / re-divergence pings it allows. `int(now)` is unique per genuine
        # decision (cadence-gated + per-key seen = never two in one second).
        dedup_key="conformance-hb:%s:%d" % (key, int(now)),
        dry_run=dry_run)
    logs.append("conformance-hb [%s] PING -> %s" % (label, status))
    return True


def run_conformance_heartbeat_check(now, state, send_fn=None, dry_run=False,
                                    fleet_rows_fn=None, hosts_fn=None,
                                    interval=None, stale=None, reping=None,
                                    collection_stale=None, lookback=None,
                                    persist=None, feed_mtimes_fn=None,
                                    feed_lag_interval=None,
                                    feed_lag_threshold=None):
    """Job 35: the central dead-box sweep, controller-only (gated in ``run_once``
    on ``conformance_hb_enabled``). Cadence-gated on its own state key
    ``conformance_hb_last_check`` (``_sweep_due``); the marker is stamped +
    persisted BEFORE any read (#172 kill-safe). Best-effort — every verdict
    fails safe to UNDETERMINED, never a raise, never a false alarm. Returns a
    decision log line per box (#486). ``dry_run`` mutates no persistent state
    and sends nothing (peek pattern).

    #1019: ALSO runs a CONSUMER-FEED-LAG check (``_check_feed_lag``) on its OWN
    hourly cadence (``feed_lag_last_check``), BEFORE the 6h dead-box gate, so a
    frozen consumer feed is caught within the hour. ``feed_mtimes_fn`` is the
    injectable I/O seam (default ``_default_feed_mtimes``); ``feed_lag_interval``
    / ``feed_lag_threshold`` default from the env + floors below."""
    persist = persist or (lambda: None)
    if fleet_rows_fn is None:
        import burn
        fleet_rows_fn = burn.load_fleet
    if hosts_fn is None:
        import airuleset            # facade re-export of _deployable_hosts (#537)
        hosts_fn = airuleset._deployable_hosts
    if interval is None:
        interval = max(_env_int("AIRULESET_CONFORMANCE_HB_CHECK_S",
                                CONFORMANCE_HB_CHECK_S), CONFORMANCE_HB_CHECK_MIN_S)
    if stale is None:
        stale = max(_env_int("AIRULESET_CONFORMANCE_HB_STALE_S",
                             CONFORMANCE_HB_STALE_S), CONFORMANCE_HB_STALE_MIN_S)
    if reping is None:
        reping = max(_env_int("AIRULESET_CONFORMANCE_HB_REPING_S",
                              CONFORMANCE_HB_REPING_S), CONFORMANCE_HB_REPING_MIN_S)
    if collection_stale is None:
        # #543 review F2: floor AND hard-clamp BELOW `stale`. The whole-fleet
        # fail-safe only holds while collection_stale < stale — a value >= stale
        # would judge a genuinely stalled collector "fresh" and re-open the
        # per-box false alarm; a units-error sub-hour value would flag a healthy
        # collector as stalled and disable dead-box detection.
        collection_stale = max(
            min(_env_int("AIRULESET_CONFORMANCE_HB_COLLECTION_STALE_S",
                         CONFORMANCE_HB_COLLECTION_STALE_S), stale - 1),
            CONFORMANCE_HB_COLLECTION_STALE_MIN_S)
    if lookback is None:
        lookback = CONFORMANCE_HB_LOOKBACK_S
    if feed_mtimes_fn is None:
        feed_mtimes_fn = _default_feed_mtimes
    if feed_lag_interval is None:
        feed_lag_interval = max(_env_int("AIRULESET_FLEET_LAG_CHECK_S",
                                         FEED_LAG_CHECK_S), FEED_LAG_CHECK_MIN_S)
    if feed_lag_threshold is None:
        feed_lag_threshold = max(_env_int("AIRULESET_FLEET_LAG_STALE_S",
                                          FEED_LAG_STALE_S), FEED_LAG_STALE_MIN_S)

    logs = []
    # The dedup memory is SHARED by the feed-lag section (below) and the dead-box
    # scan; load it ONCE here (moved earlier — #1019) so both use the same dict.
    seen = dict(state.get("conformance_heartbeat") or {})
    if not dry_run:
        state["conformance_heartbeat"] = seen     # same dict from here on (#172-F3)

    # --- #1019 consumer-feed-lag check: OWN hourly cadence, BEFORE the 6h gate ---
    _check_feed_lag(now, state, seen, send_fn, dry_run, persist, logs,
                    feed_mtimes_fn, feed_lag_interval, feed_lag_threshold, reping)

    # --- dead-box scan: 6h cadence ---
    if not watchdog._sweep_due(state, "conformance_hb_last_check", now, interval):
        return logs
    if not dry_run:
        # #172: stamp + persist the cadence marker BEFORE the read so a systemd
        # TimeoutStartSec kill can never re-run the identical sweep forever.
        state["conformance_hb_last_check"] = now
        persist()

    try:
        rows = list(fleet_rows_fn() or [])
    except Exception as e:
        logs.append("conformance-hb fleet read zlyhal (%r) — preskočené" % (e,))
        return logs
    if not rows:
        logs.append("conformance-hb žiadne fleet dáta — preskočené")
        return logs

    latest_ts, last_fresh, present = _scan(rows, now, lookback)

    # --- collection health first (the fail-safe gate) ---
    cname, cok, cdetail = classify_collection(latest_ts, now, collection_stale)
    logs.append("conformance-hb [%s] %s -- %s"
                % (cname, {True: "OK", False: "STALLED", None: "unknown"}[cok],
                   cdetail))
    if cok is True and _COLLECTION_KEY in seen and not dry_run:
        seen.pop(_COLLECTION_KEY, None)           # collector recovered
    if cok is None:
        return logs                                # no data — nothing to judge
    if cok is False:
        # collector stalled — ONE ping about it, SKIP the per-box check so a
        # broken collector never false-alarms every box (the fail-safe).
        _ping(send_fn, seen, _COLLECTION_KEY,
              "collstale:%d" % int(collection_stale), cdetail, now, reping,
              dry_run, persist, logs, "collection",
              "⚠️ **fleet zber zastal**")
        return logs

    # --- per-box liveness (collection is fresh, so verdicts are trustworthy) ---
    try:
        hosts = hosts_fn() or []
    except Exception as e:
        logs.append("conformance-hb hosts read zlyhal (%r) — preskočené" % (e,))
        return logs
    # Pass 1: classify every deployable box. `checked` = every present+judged box
    # (ok is not None — ALIVE and DEAD both); `dead` = the DRIFT subset only. A box
    # alive → pop its dedup (a re-death re-pings). A brand-new/absent box → grace
    # (None), neither checked nor pinged.
    checked = 0        # count of present+judged boxes (alive + dead)
    dead = []          # (name, detail, sig) for the DEAD subset
    for h in hosts:
        name = h.get("name") if isinstance(h, dict) else None
        if not name or name in (_COLLECTION_KEY, _ALLFLEET_KEY):
            continue          # skip a host colliding with a reserved dedup key
        lf = last_fresh.get(name)
        hname, ok, detail = classify_box(name, lf, name in present, now, stale)
        logs.append("conformance-hb [%s] %s -- %s"
                    % (name, {True: "OK", False: "DEAD", None: "unknown"}[ok],
                       detail))
        if ok is None:
            continue                              # brand-new / not fetched — grace
        checked += 1
        if ok is True:
            if name in seen and not dry_run:
                seen.pop(name, None)              # alive — re-death re-pings
            continue
        dead.append((name, detail, _sig_for_box(lf)))
    # Pass 2 — #543 review F1 (the collection-freshness fail-safe's blind spot):
    # a SIMULTANEOUS total-fleet death is overwhelmingly a dev1-SIDE failure (job
    # 16 keeps writing FRESH-ts rows, so classify_collection passes, but dev1 has
    # lost the tailnet so EVERY remote is `{"error":...}` — content, not ts, is the
    # real signal), NOT N genuine box deaths. Suppress the N per-box pings and emit
    # ONE aggregate — the whole point is to never false-alarm the whole fleet.
    if checked > 1 and len(dead) == checked:
        _ping(send_fn, seen, _ALLFLEET_KEY, "allfleet:%d" % checked,
              "celý fleet (%d boxov) naraz neodpovedá — takmer isto výpadok siete "
              "alebo fleet-zberu na dev1 (job 16 stále píše čerstvé riadky, ale "
              "každý box je v chybe), nie %d skutočných úmrtí. Skontroluj sieť / "
              "tailscale na dev1 a či boxy žijú." % (checked, checked),
              now, reping, dry_run, persist, logs, "allfleet",
              "🔴 **fleet nedostupný**")
        return logs
    # Not all-dead → the fleet is (at least partially) reachable, so re-arm the
    # aggregate latch (a future total death re-pings immediately) and fire the
    # genuine per-box dead pings.
    if _ALLFLEET_KEY in seen and not dry_run:
        seen.pop(_ALLFLEET_KEY, None)
    for name, detail, sig in dead:
        _ping(send_fn, seen, name, sig, detail, now, reping,
              dry_run, persist, logs, name, "🔴 **dead-box**")
    return logs
