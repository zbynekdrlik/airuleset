"""BOUNCE-verdict rider (#1066 lane B) — wake an armed REDUCED-authority stream
`/goal` loop the moment a FRESH gk BOUNCE verdict lands on one of ITS OWN
returned tickets, so a returned bounce can never sit unhandled (montalu1 #6474,
30 h; #6413, 10 d).

The faithful sibling of #733's `queue_arrival_recheck`, on the SAME
`goal_lane_sweep` armed-candidate-pane loop (ZERO new pane walk / capture). Two
differences from #733:

  * AUTHORITY — the INVERSE gate: this rider runs on a REDUCED-authority stream
    pane (branch-merge / fork-no-merge). A full/gk box's bounce awareness is
    job 8 + the queue-arrival rider; a returned `prio:bounce` is the reduced
    STREAM's own obligation.
  * SIGNAL SOURCE — ZERO gh: it reads `entry["bounce_unhandled"]`
    (`[{"number": N, "verdict_ts": <epoch>}, ...]`) from the per-cwd
    tickets-status cache lane A (#1066) already computes at footer refresh
    (~120 s), through an injected `bounce_unhandled_fetch(cwd)` seam (None =
    unwired = skipped; the production default reader is `_default_bounce_
    unhandled_fetch`, wired from `_job_goal_lane_sweep`).

The verdict logic is REUSED VERBATIM from queue-arrival by import (never copied):
`_queue_decision` (pure seed -> set-delta -> hold/nudge, `cur=None` fails safe to
skip) and `_advanced_base` (base advance on a CONFIRMED send). The set is over
`(number, verdict_ts)` PAIRS encoded as stable int TOKENS (`_encode_token`), so a
RE-BOUNCE (same number, a NEWER verdict) is a NEW token -> a genuine arrival ->
one nudge, while an unchanged verdict tracks silently. Delivery reuses the shared
`stuck-check: ` machine-nudge prefix, `watchdog.send_verified` (transcript-proof
submit), the `_janitor_mark_watch`/`_janitor_clear_watch` reclaim path, the
per-sweep `handled` set (at most ONE keystroke per pane per sweep), and the
shared per-pane-per-KIND 60-min floor `nudge_gate.gate_ok(state, sid,
"bounce-verdict", now)` (a delta inside the floor is HELD and its members
ACCUMULATE into the next post-floor nudge). `dry_run` mutates nothing.
"""

import watchdog
from watchdog import ops_wait_recheck as _ops_wait_recheck
from watchdog import nudge_gate as _nudge_gate
from watchdog import queue_arrival_recheck as qa
# REUSED VERBATIM (design mandate: reuse `_queue_decision`/`_advanced_base` by
# import, never copy). Binding them as module attributes keeps `bv._queue_decision
# is qa._queue_decision` true (the drift lock).
from watchdog.queue_arrival_recheck import (  # noqa: F401
    _queue_decision, _advanced_base, _budget_left, _persist,
)

# Reuse #733's shared caps/bounds (one source of truth).
NUDGE_MAX_CHARS = qa.NUDGE_MAX_CHARS
MAX_NAMED = qa.MAX_NAMED_ARRIVALS
MAX_SEND_FAILS = qa.MAX_SEND_FAILS
ORPHAN_TTL = qa.QUEUE_ARRIVAL_ORPHAN_TTL_S

# a (number, verdict_ts) pair -> one stable int token. verdict_ts is an epoch
# (~1.79e9, well under the shift), so `number * SHIFT + int(verdict_ts)` is unique
# per pair and orderable; the reverse map recovers (number, ts) for the nudge text.
_TOKEN_SHIFT = 10 ** 11


def _encode_token(number, verdict_ts):
    return int(number) * _TOKEN_SHIFT + int(round(float(verdict_ts)))


def _tokens_and_map(field):
    """`(cur_tokens, {token: (number, verdict_ts)})` from a `bounce_unhandled`
    field. A missing / non-list field -> `(None, {})` so `_queue_decision`
    reads it as UNDETERMINED (skip, no baseline move). A malformed ENTRY
    (non-int number, non-numeric verdict_ts) is skipped, never fatal — a
    still-valid sibling entry is never dropped."""
    if not isinstance(field, list):
        return None, {}
    cur = []
    tok_map = {}
    for e in field:
        if not isinstance(e, dict):
            continue
        n = e.get("number")
        ts = e.get("verdict_ts")
        if not isinstance(n, int) or isinstance(n, bool):
            continue
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        tok = _encode_token(n, ts)
        cur.append(tok)
        tok_map[tok] = (n, ts)
    return cur, tok_map


def _fmt_bounce(arrivals, tok_map):
    """`#6474 #6413` — the arrival ticket numbers, for a decision-log line."""
    parts = []
    for tok in arrivals[:MAX_NAMED]:
        pair = tok_map.get(tok)
        if pair is not None:
            parts.append("#%d" % pair[0])
    extra = len(arrivals) - min(len(arrivals), MAX_NAMED)
    txt = " ".join(parts)
    if extra > 0:
        txt += " (+%d ďalších)" % extra
    return txt


def _bounce_nudge_text(pairs):
    """`stuck-check: BOUNCE #N (gk HH:MM) — ACK + lane within 1 h` (one segment
    per new arrival). Carries the shared `stuck-check: ` own-payload prefix
    (reclaimable, never mistaken for a human answer). Hard-capped at
    NUDGE_MAX_CHARS (a pathological wave truncates on a word boundary)."""
    import datetime
    named = pairs[:MAX_NAMED]
    segs = []
    for n, ts in named:
        when = "?"
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            try:
                when = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
            except (OverflowError, OSError, ValueError):
                when = "?"
        segs.append("BOUNCE #%d (gk %s)" % (n, when))
    body = "; ".join(segs)
    extra = len(pairs) - len(named)
    if extra > 0:
        body += " (+%d ďalších)" % extra
    text = "stuck-check: %s — ACK + lane within 1 h" % body
    if len(text) <= NUDGE_MAX_CHARS:
        return text
    return text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _book_unverified_bounce_send(rec, new_rec, cur_sorted, loc, arrivals_n):
    """#714 bounded retry (the queue-arrival shape, bounce-verdict journal
    text): under MAX_SEND_FAILS keep the base OLD so the SAME verdict re-detects
    next sweep; at MAX_SEND_FAILS back off by ACCEPTING the wave so a
    persistently-swallowing NON-busy pane is not typed every sweep (job 8 stays
    the presence backstop)."""
    prior = rec.get("send_fails") if isinstance(rec, dict) else None
    fails = (prior if isinstance(prior, int) and not isinstance(prior, bool)
             else 0) + 1
    if fails >= MAX_SEND_FAILS:
        new_rec["base"] = cur_sorted
        new_rec["send_fails"] = 0
        return ("bounce-verdict %s -> submit-unverified x%d — backing off, "
                "accepting the %d-verdict wave (bounded retry #714)"
                % (loc, fails, arrivals_n))
    new_rec["send_fails"] = fails
    return ("bounce-verdict %s -> submit-unverified (attempt %d/%d, retry next "
            "sweep, %d new)" % (loc, fails, MAX_SEND_FAILS, arrivals_n))


def _prune_bounce_verdict_orphans(brecs, visited_sids, now, ttl_s=ORPHAN_TTL):
    """#531 orphan reaper for `state["bounce_verdict"]` (keyed on sid). Reap ONLY
    when the sid was NOT a live candidate pane this sweep AND (malformed OR `lts`
    older than ttl). Faithful mirror of `_prune_queue_arrival_orphans`."""
    if not isinstance(brecs, dict):
        return
    for sid in [k for k, v in list(brecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        brecs.pop(sid, None)


def _default_bounce_unhandled_fetch(cwd):
    """Production `bounce_unhandled` reader — the #459 `obligation_count`
    pattern: read the per-cwd tickets-status cache
    (`statusbar.cache_dir()/cwd_key(cwd)+".json"`), ZERO gh (lane A refreshes it
    ~every 120 s at footer render). Returns the `bounce_unhandled` field value
    (a list, or None when absent/None). FULLY guarded — any failure (statusbar
    unimportable at the watchdog's runtime path, a corrupt cache) degrades to
    None, which the rider reads as UNDETERMINED -> stay silent (fail toward
    no-nudge)."""
    try:
        import json
        import statusbar
        path = statusbar.cache_dir() / (statusbar.cwd_key(cwd) + ".json")
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
        return d.get("bounce_unhandled") if isinstance(d, dict) else None
    except Exception:
        return None


def goal_bounce_verdict_recheck(now, run, brecs, sid, cwd, pid, tpath, loc,
                                dry_run, handled, bounce_unhandled_fetch, state,
                                sleep_fn=None, captured=None, persist=None,
                                budget_left_fn=None):
    """Audit ONE armed REDUCED-authority pane's `bounce_unhandled` cache and, on
    a NEW (number, verdict_ts) pair, deliver ONE verified BOUNCE-verdict nudge
    into that session. Called from `goal.goal_lane_sweep`'s armed-pane loop with
    the already-resolved pane context (ZERO new pane walk). Mutates
    `brecs[sid]`; returns decision-log lines (#486 — every verdict logged, never
    a silent skip). `dry_run` mutates no persistent state and sends nothing."""
    logs = []
    # REDUCED-authority gate (the INVERSE of queue-arrival's full-only gate):
    # only a reduced STREAM box acts on its own returned bounce; a full/gk box's
    # bounce awareness is job 8 + queue-arrival. Cheap, BEFORE any read. An
    # unresolvable authority fails safe to skip (never a false nudge).
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception as e:  # noqa: BLE001
        logs.append("bounce-verdict %s -> skip:authority-unresolved (%r)"
                    % (loc, e))
        return logs
    if authority == "full":
        logs.append("bounce-verdict %s -> skip:full-authority (%s)"
                    % (loc, authority))
        return logs
    if bounce_unhandled_fetch is None:
        logs.append("bounce-verdict %s -> skip:unwired (no bounce_unhandled_fetch)"
                    % loc)
        return logs
    # LOCAL cache read, ZERO gh; a read error reads as None -> skip.
    try:
        field = bounce_unhandled_fetch(cwd)
    except Exception as e:  # noqa: BLE001
        logs.append("bounce-verdict %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs
    cur, tok_map = _tokens_and_map(field)   # None when field missing/non-list

    rec = brecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason, arrivals = _queue_decision(rec, cur, now)

    if action == "skip":
        logs.append("bounce-verdict %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action in ("seed", "track", "hold"):
        if not dry_run:
            new_rec["lts"] = now
            brecs[sid] = new_rec
        logs.append("bounce-verdict %s -> %s (%s — %d unhandled, baseline %s)"
                    % (loc, action, reason, len(cur or []),
                       "seeded" if action == "seed" else "advanced"))
        return logs

    # action == "nudge": persist the refreshed rec (base OLD until a CONFIRMED send).
    cur_sorted = sorted({int(x) for x in cur})
    if not dry_run:
        new_rec["lts"] = now
        prior_fails = rec.get("send_fails")
        if isinstance(prior_fails, int) and not isinstance(prior_fails, bool):
            new_rec["send_fails"] = prior_fails   # carry across a deferral
        brecs[sid] = new_rec

    if not watchdog.nudges_enabled("bounce-verdict"):   # per-kind switch (default OFF)
        logs.append("bounce-verdict %s -> skip:kind-off (%d new)"
                    % (loc, len(arrivals)))
        return logs
    from watchdog import compact as _compact
    if _compact.pending_compact_hold(sid, now):
        logs.append("bounce-verdict %s -> hold:compact-pending (%d new)"
                    % (loc, len(arrivals)))
        return logs
    if handled is not None and sid in handled:
        logs.append("bounce-verdict %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep, %d new)"
                    % (loc, len(arrivals)))
        return logs
    if _ops_wait_recheck._pane_busy_waiting(captured):
        logs.append("bounce-verdict %s -> hold:busy (waiting on background "
                    "agents — deferred to next idle tick, %d new)"
                    % (loc, len(arrivals)))
        return logs
    if not _nudge_gate.gate_ok(state, sid, "bounce-verdict", now):
        logs.append("bounce-verdict %s -> %s; retry next sweep, %d new"
                    % (loc, _nudge_gate.floor_hold_reason(
                        state, sid, "bounce-verdict", now), len(arrivals)))
        return logs
    if dry_run:
        logs.append("bounce-verdict %s -> WOULD-NUDGE (%d new: %s)"
                    % (loc, len(arrivals), _fmt_bounce(arrivals, tok_map)))
        return logs

    pairs = [tok_map[a] for a in arrivals if a in tok_map]
    text = _bounce_nudge_text(pairs)
    watchdog._janitor_mark_watch(state, pid, now)
    send_out = {}
    _skip_confirm = False
    _left = _budget_left(budget_left_fn)
    if _left is not None and _left < qa.QUEUE_ARRIVAL_CONFIRM_MIN_BUDGET_S:
        _skip_confirm = True
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out, nudge="bounce-verdict",
                                state=state, skip_confirm=_skip_confirm, now=now)
    if not ok:
        if send_out.get("delivered_unconfirmed"):
            # NON-terminal for the baseline (janitor watch LEFT SET) but stamp
            # the per-kind floor (the text reached the pane) so the re-confirm
            # defers a full hour.
            _nudge_gate.mark_sent(state, sid, "bounce-verdict", now)
            _persist(persist, logs)
            logs.append("bounce-verdict %s -> delivered-unconfirmed (baseline "
                        "unchanged, floor stamped, undo via janitor, %d new)"
                        % (loc, len(arrivals)))
            return logs
        if send_out.get("pane_budget_held"):
            logs.append("bounce-verdict %s -> held (pane-budget, not typed; "
                        "retry next sweep, %d new)" % (loc, len(arrivals)))
            return logs
        logs.append(_book_unverified_bounce_send(rec, new_rec, cur_sorted, loc,
                                                 len(arrivals)))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["base"] = _advanced_base(new_rec["base"], cur_sorted, arrivals)
    new_rec["send_fails"] = 0
    brecs[sid] = new_rec
    _nudge_gate.mark_sent(state, sid, "bounce-verdict", now)
    _persist(persist, logs)
    if handled is not None:
        handled.add(sid)
    logs.append("bounce-verdict nudge %s -> %d new (%s), baseline advanced to %d"
                % (loc, len(arrivals), _fmt_bounce(arrivals, tok_map),
                   len(cur_sorted)))
    return logs
