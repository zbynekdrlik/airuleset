"""Stale hand-off alarm helpers for cross-stream job 11 (#399).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 13 of the
definitive module split (issue #433). These three back-reference helpers
belong to the cross-stream review-queue domain but are NOT folded into
``cross_stream.py`` (convention C7 — that module is already near the cap and
must not grow):

  ``_parse_gh_ts``        -- epoch seconds for a GitHub ISO-8601 UTC stamp.
  ``_normalize_gkreq``    -- (tickets, handoffs) from a job-11 fetch result,
                             accepting both the #399 dict shape and the legacy
                             bare-list contract.
  ``_stale_handoff_alarm`` -- the stale ready-for-review / needs-gatekeeper
                             hand-off alarm step of job 11 (#399, 6h owner ping).
  ``_stale_handoff_push``  -- the 24h-push kontrakt (#607): a durable "does the
                             gk even know?" comment on a >24h-working stale
                             hand-off, weekend-aware, per-ticket ~daily dedup;
                             returns the freshly-pushed numbers for the caller's
                             gk-session nudge. Reaches `watchdog.GK_STALE_PUSH_S`
                             / `watchdog._gkreq_reping_due` call-time (C3).

Every name here is re-exported into the ``watchdog`` namespace by the
positional facade import in ``__init__.py``, so all existing ``watchdog.<name>``
seams (``cross_stream.gk_request_backstop`` reaches all three as
``watchdog._parse_gh_ts`` / ``watchdog._normalize_gkreq`` /
``watchdog._stale_handoff_alarm``, plus every test that drives that path) keep
resolving unchanged.

Direction is back-reference (convention C3): ``_stale_handoff_alarm`` reaches
its sibling package-level names at call time through the package namespace --
``watchdog._gkreq_reping_due`` (lives in ``cross_stream.py``, re-exported),
``watchdog.GKREQ_STALE_HANDOFF_S`` and ``watchdog.STALE_HANDOFF_ALARM`` (both
constants still resident in ``__init__.py``) -- so any ``watchdog.<name>``
monkeypatch a test applies stays effective. The one def-time need,
``schedule=GKREQ_REPING_SCHEDULE_S``, binds at def time and so must be a real
name: it is imported ``from watchdog`` at module top (convention C4), legal
because ``GKREQ_REPING_SCHEDULE_S`` is bound in ``__init__.py`` above the
facade-import position and a grep proved it unpatched -- never the banned
``from watchdog import <function-below-its-position>`` shape.
"""

import time

import watchdog
from watchdog import GKREQ_REPING_SCHEDULE_S


def _parse_gh_ts(s):
    """Epoch seconds for a GitHub ISO-8601 UTC stamp ('2026-08-13T07:31:02Z',
    the shape `gh --json updatedAt` always returns); None on anything
    unparsable — an unmeasurable timestamp must never feed the stale alarm
    (#399: never alarm on a guess)."""
    import calendar
    try:
        return int(calendar.timegm(
            time.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return None


def _normalize_gkreq(fetched):
    """`(tickets, handoffs)` from a job-11 fetch result, accepting BOTH the
    #399 dict shape and the legacy bare-list contract (the same
    normalize-the-widened-callback shape `_normalize_closed` already
    established for job 24/25's `closed_fetch`):

      dict  → (its "tickets" list, its "handoffs" dict) — either half
              absent/mis-shaped degrades to the safe value for THAT half
              (tickets None = fetch-failed semantics for the nudge flow;
              handoffs None = stale scan unmeasurable, never alarm)
      list/tuple → (the list, None) — legacy callers/fixtures: stale scan off
      None / anything else → (None, None)
    """
    if isinstance(fetched, dict):
        tickets = fetched.get("tickets")
        if not isinstance(tickets, list):
            tickets = None
        handoffs = fetched.get("handoffs")
        if not isinstance(handoffs, dict):
            handoffs = None
        return tickets, handoffs
    if isinstance(fetched, (list, tuple)):
        return list(fetched), None
    return None, None


def _stale_handoff_alarm(name, root, handoffs, g, now, send_fn, dry_run,
                         persist, schedule=GKREQ_REPING_SCHEDULE_S):
    """#399 — the stale hand-off alarm step of job 11: given the fetched
    `handoffs` map ({num: updated_epoch}) for ONE target, Discord-ping ONCE
    (staged 24h/3d/7d re-pings via the SAME `_gkreq_reping_due` backoff as
    the sibling no-pane ping, under its own `g["stale_seen"]` dedup
    namespace — independent episodes never share bookkeeping) when any
    hand-off has sat untouched for `GKREQ_STALE_HANDOFF_S`+. Detection-only:
    never a keystroke, never gated on pane state — a rotting queue is wrong
    regardless of whether a session happens to be running, and keying the
    alarm set on pane presence would make it flap with a transient
    pane-read blip (the sibling's own single-blip lesson). Returns log
    lines; mutates `g["stale_seen"]` and calls `persist()` before the ping
    (dedup memory survives a mid-sweep kill)."""
    logs = []
    stale_seen = g.get("stale_seen")
    if not isinstance(stale_seen, dict):
        stale_seen = {}
    g["stale_seen"] = stale_seen
    ages = {}
    for n, upd in handoffs.items():
        if isinstance(upd, bool) or not isinstance(upd, (int, float)):
            continue                  # unmeasurable → never alarm on a guess
        age = now - upd
        if age < watchdog.GKREQ_STALE_HANDOFF_S:
            continue                  # fresh (or future-dated) → normal flow
        try:
            ages[int(n)] = age
        except (TypeError, ValueError):
            continue
    stale = sorted(ages)
    if not stale:
        stale_seen.pop(name, None)     # clean → forget (mirrors `seen`)
        return logs
    prev = stale_seen.get(name)
    if not isinstance(prev, dict):
        prev = {}
    if prev.get("tickets") != stale:
        due, count = True, 1           # material change → immediate alarm
    else:
        due, count = watchdog._gkreq_reping_due(prev, now, schedule)
    if not due:
        return logs                    # staged schedule not cleared yet
    tick_str = " ".join("#%d" % n for n in stale)
    body = watchdog.STALE_HANDOFF_ALARM % {
        "name": name, "n": len(stale), "ticks": tick_str,
        "hours": int(max(ages.values()) // 3600), "root": root}
    stale_seen[name] = {"tickets": stale, "ts": int(now),
                        "reping_count": count}
    persist()                          # dedup memory BEFORE the ping
    # Dedup key fresh per DECISION INSTANT, never per content — this
    # function's own schedule/material-change logic is the sole authority
    # on whether a send is due (the sibling's notify-TTL lesson), and the
    # repo's own project thread gets the ping (stream-qualified label).
    from notify import stream_qualified
    result = send_fn(body, dedup_key="gkstale:%s:%d" % (name, int(now)),
                     dry_run=dry_run, project=stream_qualified(name))
    logs.append("gkstale-ping %s %s (send=%r)" % (name, tick_str, result))
    return logs


def _stale_handoff_push(name, root, handoffs, g, now, comment_fn,
                        dry_run=False, persist=None,
                        threshold_s=None, schedule=GKREQ_REPING_SCHEDULE_S):
    """#607 — the 24h-push kontrakt: given the fetched `handoffs` map ({num:
    updated_epoch}) for ONE target, post a DURABLE "gk, vieš o tom?" comment on
    every hand-off untouched for >`threshold_s` (default `GK_STALE_PUSH_S`, 24h)
    of WORKING time — Sat/Sun in Europe/Bratislava EXCLUDED
    (`working_time.working_seconds_between`, the SAME shared helper the CLI
    `stale!` tag uses, so the two never drift). Returns `(logs, pushed)` — the
    freshly-commented ticket numbers, which the caller uses to fire ONE gk-session
    nudge (the durable comment is the primary record and fires even with no
    session running — exactly the "does the gk even know?" case).

    Per-TICKET dedup in `g["stale_push_seen"][name]` ({str(num): {ts,
    reping_count}}) via the SAME staged 24h/3d/7d `_gkreq_reping_due` backoff the
    sibling `_stale_handoff_alarm` uses — so one stale ticket is not re-commented
    every 30-min sweep (the dispatch's "~daily cadence per ticket"). Fail-safe
    biases to SILENCE (#539/#570): an unmeasurable `updated` (None / non-numeric)
    is skipped; a `comment_fn` that returns falsy / raises does NOT advance the
    dedup, so a failed post retries next sweep (the #551 comment-latch tri-state
    — latch on the durable comment, retry on failure). A ticket that leaves the
    open hand-off set is pruned from the dedup. `dry_run` posts nothing and
    mutates NO persistent state (#516). `persist()` is called after a real push,
    like the sibling job-11 flows."""
    import working_time
    logs = []
    threshold_s = watchdog.GK_STALE_PUSH_S if threshold_s is None else threshold_s
    persist = persist or (lambda: None)
    seen_all = g.get("stale_push_seen")
    if not isinstance(seen_all, dict):
        seen_all = {}
    existing = seen_all.get(name)
    # Work on a COPY so a `dry_run` sweep mutates NO persistent state (#516):
    # `run_once`'s save_state persists `g` unconditionally, so the container +
    # this per-name map are written back into `g` ONLY in the non-dry_run branch
    # below — a dry-run never touches `g` (nor the resolved-ticket prune).
    seen = dict(existing) if isinstance(existing, dict) else {}

    live_nums, candidates = set(), []
    for n, upd in handoffs.items():
        if isinstance(upd, bool) or not isinstance(upd, (int, float)):
            continue                      # unmeasurable → never push on a guess
        try:
            num = int(n)
        except (TypeError, ValueError):
            continue
        live_nums.add(num)
        if working_time.working_seconds_between(upd, now) > threshold_s:
            candidates.append(num)

    # prune dedup entries for tickets no longer an open hand-off (resolved)
    for k in [k for k in list(seen)
              if not (str(k).isdigit() and int(k) in live_nums)]:
        seen.pop(k, None)

    pushed = []
    for num in sorted(candidates):
        key = str(num)
        prev = seen.get(key)
        due, count = ((True, 1) if not isinstance(prev, dict)
                      else watchdog._gkreq_reping_due(prev, now, schedule))
        if not due:
            continue
        if dry_run:
            logs.append("gkstale-push %s #%d -> WOULD-COMMENT (dry-run)"
                        % (name, num))
            continue
        try:
            ok = bool(comment_fn(num))
        except Exception:
            ok = False
        if not ok:
            logs.append("gkstale-push-failed %s #%d (comment did not post; "
                        "dedup unchanged, retry next sweep)" % (name, num))
            continue
        seen[key] = {"ts": int(now), "reping_count": count}
        pushed.append(num)
        logs.append("gkstale-push %s #%d (durable comment posted)" % (name, num))

    if not dry_run:
        g["stale_push_seen"] = seen_all
        if seen:
            seen_all[name] = seen
        else:
            seen_all.pop(name, None)
        if pushed:
            persist()
    return logs, pushed
