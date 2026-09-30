"""#1176 census item 3 — tell the STREAM SESSION that its checkout runs on stale
project rules.

The 30.9.2026 fleet census: david2-4 and montalu6 sat on work branches
4 000-16 000 commits behind with 86-152 rule files (`CLAUDE.md` / `.claude/`)
behind the base, and nothing told the session — Job 53 and the SessionStart
hook only reported into a footer segment the owner does not act on and a
`status` nobody reads. A work branch with unmerged commits, or a dirty tree,
must never be touched mechanically; only the session itself can merge the
base in or switch to it.

So Job 53 records, on every lagging checkout with rule-file lag that nothing
is fixing, the ONE `cli_checkout_freshness.notice_line` (the same words the
SessionStart hook prints) as `notice` in status.json, and this leaf types it
into the ONE claude pane whose cwd is inside that checkout — on this box and
this unix account only (the sweep's own pane list), so it is always the
stream's own session and never the owner. Delivery is the machine channel:
the staged `checkout-lag` nudge kind (default OFF, on the `stream` nudge
profile), the shared idle-pane gate set (`watchdog.idle_pane`), the
`nudge_gate` per-kind floor + total cap, and `send_verified` (which turns it
into the #1157 pointer row). On top of that it is rate-limited per
(checkout, branch, base): once, then again only after REPEAT_S while the lag
lasts. A dry-run types and records nothing.
"""
import os

NUDGE_KIND = "checkout-lag"
STATE_KEY = "checkout_notice"
REPEAT_S = 24 * 3600          # the same (checkout, branch, base) again at most daily
KEEP_S = 7 * 24 * 3600        # a record whose checkout stopped lagging is pruned
MIN_BUDGET_S = 30             # one verified keystroke delivery (polls ~20 s)
KIND_OFF_LOG_S = 3600         # the kind-off decision line at most hourly
_LANE_DIR = os.sep + os.path.join(".claude", "worktrees") + os.sep


def inside(cwd, path):
    """`cwd` is the checkout `path` or a directory in it — but never one of its
    `.claude/worktrees/*` lane checkouts (a lane pane is not the session)."""
    c, p = os.path.realpath(cwd), os.path.realpath(path).rstrip(os.sep)
    if c == p:
        return True
    return c.startswith(p + os.sep) and not (c + os.sep).startswith(p + _LANE_DIR)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _due(rec, sig, now):
    """None when a notice for `sig` may go now, else the hold reason."""
    if not isinstance(rec, dict) or rec.get("sig") != sig:
        return None
    at = _num(rec.get("at"))
    if at is None or at > now or now - at >= REPEAT_S:
        return None
    return "told %d min ago" % ((now - at) // 60)


def notice_job(now, state, panes, *, status, ready, deliver, mark_sent,
               nudges_enabled, handled=None, dry_run=False):
    """One pass over status.json's `notice` entries; returns journal lines.

    Injected deps (production wiring in `run_job`):
      ready(pid, cwd) -> (ok, why, sid, tpath)   the shared idle-pane gate set
      deliver(pid, tpath, text) -> SendOutcome   the verified keystroke
      mark_sent(state, sid, kind, now)           the nudge_gate floor stamp
      nudges_enabled(kind) -> bool               the per-kind staging switch
    """
    from watchdog import idle_pane
    logs = []
    entries = [(p, e) for p, e in sorted(((status or {}).get("checkouts") or {}).items())
               if isinstance(e, dict) and e.get("notice")]
    if not entries:
        return logs
    if not nudges_enabled(NUDGE_KIND):
        last = _num(state.get(STATE_KEY + "_kindoff"))
        if last is None or last > now or now - last >= KIND_OFF_LOG_S:
            logs.append("checkout-notice: skip:kind-off (%d checkout(s) with rule lag; "
                        "stage: airuleset.py nudges on --kind %s)" % (len(entries), NUDGE_KIND))
            if not dry_run:
                state[STATE_KEY + "_kindoff"] = now
        return logs
    store = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    store = dict(store)
    for path, e in entries:
        sig = "%s|%s" % (e.get("branch") or "detached", e.get("base"))
        hold = _due(store.get(path), sig, now)
        if hold:
            logs.append("checkout-notice: %s hold:repeat (%s)" % (path, hold))
            continue
        own = [(pid, cwd) for pid, cwd in panes if cwd and inside(cwd, path)]
        if len(own) != 1:
            logs.append("checkout-notice: %s skip:%s" % (
                path, "no-pane" if not own else "ambiguous-pane(%d)" % len(own)))
            continue
        pid, cwd = own[0]
        ok, why, sid, tpath = ready(pid, cwd)
        if not ok:
            logs.append("checkout-notice: %s -> %s hold:%s" % (path, pid, why))
            continue
        if dry_run:
            logs.append("checkout-notice: %s -> %s would tell [dry-run]" % (path, pid))
            continue
        if handled is not None:
            handled.add(sid)
        try:
            outcome = deliver(pid, tpath, e["notice"])
        except Exception as exc:  # noqa: BLE001 -- keys may be in: at-most-once
            logs.append("checkout-notice: %s delivery raised %r" % (path, exc))
            outcome = None
        kind = idle_pane.outcome_kind(outcome)
        if kind in idle_pane.DELIVERED or kind in idle_pane.TYPED_NOT_DELIVERED:
            mark_sent(state, sid, NUDGE_KIND, now)     # keys reached the pane: floor
        if kind in idle_pane.DELIVERED:
            store[path] = {"sig": sig, "at": now}
        logs.append("checkout-notice: %s -> %s %s (%s)" % (path, pid, kind, sig))
    if not dry_run:
        live = {p for p, _ in entries}
        state[STATE_KEY] = {p: r for p, r in store.items()
                            if p in live or (isinstance(r, dict) and _num(r.get("at"))
                                             is not None and now - r["at"] < KEEP_S)}
    return logs


def run_job(now, state, panes, *, status, run, sleep_fn, projects_dir,
            dry_run=False, handled=None, budget_left=None):
    """The production wiring: the shared `watchdog.idle_pane` gates and
    delivery, resolved at call time (every `watchdog.<name>` seam stays
    effective); held while the sweep has less than MIN_BUDGET_S left."""
    import watchdog
    from watchdog import idle_pane
    left = budget_left() if budget_left is not None else None
    if left is not None and left < MIN_BUDGET_S:
        if any(isinstance(e, dict) and e.get("notice")
               for e in ((status or {}).get("checkouts") or {}).values()):
            return ["checkout-notice: hold:budget (%.0fs left)" % left]
        return []
    jl = []
    ready_deps, deliver, mark_sent = idle_pane.production_deps(
        now, state, NUDGE_KIND, run=run, sleep_fn=sleep_fn,
        budget_left=budget_left, journal=jl)

    def ready(pid, cwd):
        return idle_pane.pane_ready(pid, cwd, NUDGE_KIND, projects_dir=projects_dir,
                                    handled=handled, state=state, now=now, **ready_deps)

    return notice_job(now, state, panes, status=status, ready=ready, deliver=deliver,
                      mark_sent=mark_sent, nudges_enabled=watchdog.nudges_enabled,
                      handled=handled, dry_run=dry_run) + jl
