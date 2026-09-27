"""#1163 — Job 52: WATCH-steered declared windows (durable scheduled triggers).

A declared managed window (`cli_fleet.REMOTE_HOSTS[...]["windows"]`) may say
`"steer": "watch"` instead of being steered by a continuous `/goal` loop. Its
work is schedule/event-driven (the gk-quality window: a 12-hourly bounce /
fix-forward / release class watch and a weekly post-release review), so a
`/goal` that re-fires turns with nothing to do is exactly the cost the owner
cut (odoo-erp 7924, 25.9.2026). The window instead DECLARES its triggers as
versioned data:

    "steer": "watch", "tz": "Europe/Bratislava",
    "triggers": [{"name": "watch", "cron": "17 7,19 * * *", "prompt": "..."},
                 {"name": "weekly-review", "cron": "17 8 * * 1", "prompt": "..."}]

and this job delivers each due slot into THAT window's own pane. It replaces
the session crons (CronCreate) the window used before: those are session
scoped, expire after 7 days, vanish on a restart, and a missed slot was
invisible (the 25.9. 19:17 run).

MECHANISM
  * CRON — a minimal stdlib 5-field matcher (`parse_cron` / `cron_matches`):
    `*`, numbers, lists, ranges and steps; no names. The cron is evaluated in
    the window's `tz` (the owner's zone, via `zoneinfo`) or, without one, the
    box's local time. Every EPOCH minute is converted to local wall time and
    matched, so the schedule is DST-safe by construction: a spring-forward
    minute that never exists never fires, and a fall-back hour that occurs
    twice fires ONCE (slots are keyed by local wall time, `slot_key`).
  * STATE — per slot, in the watchdog state store under `state["watch_
    triggers"]`: `slots[<window>/<trigger>@<local key>] = {"s": held|fired|
    missed, "slot", "at", "why"}` plus a `since` watermark per trigger (first
    sight = now, so a fresh declaration never fires retroactively). A due slot
    is `held` while the pane is not ready, `fired` once delivered, and
    `missed` when it could not be delivered within `GRACE_S`; a slot that fell
    inside a watchdog outage (≤ `LOOKBACK_S`) is also recorded `missed`, never
    silently skipped. Slots older than `KEEP_S` are pruned.
  * DELIVERY — through the ONE keystroke primitive `send_verified(nudge=
    "watch-trigger")`, so a slot arrives as ONE pointer line with the full text
    in `~/.claude/nudges/` (#1157 slice 3). Only into the pane whose cwd IS the
    window's own cwd (never a sub-pane), and only when that pane is at an idle
    prompt, not in copy-mode, with no recent human input, past the nudge-gate
    floor. `watch-trigger` is a stageable MACHINE kind (default OFF — the
    supervisor stages it `nudges on --kind watch-trigger`); it is exempt from
    the cross-kind TOTAL cap (the owner's declared schedule is its rate bound —
    Monday's 07:17 watch must not block the 08:17 review for 3 h), and it
    keeps its per-kind floor. At most ONE delivery per window per sweep.
  * STEERING — `/autopilot` / `goal-arm --self` in a watch window arm NO
    `/goal`: they print + record `watch armed (N triggers, next …)`
    (`arm_line` / `record_armed`). Job 9's virgin arm, the variant source
    `goal_template_for` and job 20's dark-watch all skip a watch window.
  * VISIBILITY — `airuleset.py status` renders the window's `goal:` row via
    `status_row` (next slot, last slot + its outcome, any missed slot).

MACHINE-CHANNEL only: this job never pings the owner.
"""
import datetime
import json
import os

NUDGE_KIND = "watch-trigger"
STEER = "watch"
STATE_KEY = "watch_triggers"
GRACE_S = 2 * 3600            # a due slot may wait this long for an idle pane
LOOKBACK_S = 24 * 3600        # a slot missed during an outage is still recorded
KEEP_S = 9 * 24 * 3600        # slot history kept (> one weekly period)
NEXT_HORIZON_S = 8 * 24 * 3600
MIN_BUDGET_S = 30             # one verified keystroke delivery (polls ~20 s)
ARMED_FILE = "watch-armed.json"
PROMPT_MAX_CHARS = 400
TERMINAL = ("fired", "missed")
# `send_outcome` kinds (kept as literals: this leaf imports no watchdog module
# at import time). Typed but not delivered -> the floor is stamped, retried.
_DELIVERED = ("submitted", "delivered-unconfirmed")
_TYPED_NOT_DELIVERED = ("typed-undone", "typed-stranded", "swallowed",
                        "unconfirmed")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# (lo, hi) per field: minute hour day-of-month month day-of-week (7 == 0).
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


class CronError(ValueError):
    """A cron expression or a watch declaration that cannot be evaluated."""


# --------------------------------------------------------------------------- #
# Cron matcher
# --------------------------------------------------------------------------- #

def _int(tok, expr):
    if not tok.isdigit():
        raise CronError("cron %r: %r is not a number" % (expr, tok))
    return int(tok)


def _field(text, lo, hi, expr):
    """The set of values one cron field allows."""
    out = set()
    for part in text.split(","):
        if not part:
            raise CronError("cron %r: empty list element" % expr)
        rng, _, step_s = part.partition("/")
        step = _int(step_s, expr) if step_s else 1
        if step < 1:
            raise CronError("cron %r: step must be >= 1" % expr)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a_s, _, b_s = rng.partition("-")
            a, b = _int(a_s, expr), _int(b_s, expr)
        else:
            a = _int(rng, expr)
            b = hi if step_s else a
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise CronError("cron %r: %r outside %d-%d" % (expr, part, lo, hi))
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse_cron(expr):
    """Parse a 5-field cron expression into a dict of allowed-value sets.
    Raises `CronError` on anything it cannot evaluate exactly."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise CronError("cron %r: need 5 fields, got %d" % (expr, len(parts)))
    sets = [_field(p, lo, hi, expr) for p, (lo, hi) in zip(parts, _BOUNDS)]
    dow = frozenset(0 if d == 7 else d for d in sets[4])
    return {"minute": sets[0], "hour": sets[1], "dom": sets[2],
            "month": sets[3], "dow": dow,
            "dom_star": parts[2].startswith("*"),
            "dow_star": parts[4].startswith("*")}


def cron_matches(spec, dt):
    """True iff the local wall time `dt` matches `spec`. Standard cron rule:
    when BOTH day fields are restricted, either one matching is enough."""
    if dt.minute not in spec["minute"] or dt.hour not in spec["hour"]:
        return False
    if dt.month not in spec["month"]:
        return False
    dom_ok = dt.day in spec["dom"]
    dow_ok = ((dt.weekday() + 1) % 7) in spec["dow"]
    if spec["dom_star"] or spec["dow_star"]:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def zone(name):
    """The tzinfo for a declared `tz` name, or None (box local time) when the
    declaration names none. Raises `CronError` for an unknown zone."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception as e:  # noqa: BLE001 -- ZoneInfoNotFoundError / ValueError
        raise CronError("unknown tz %r (%s)" % (name, e))


def _local(ts, tz):
    return (datetime.datetime.fromtimestamp(ts, tz) if tz is not None
            else datetime.datetime.fromtimestamp(ts))


def slot_key(ts, tz):
    """The slot's identity: its LOCAL wall time to the minute. A fall-back hour
    that occurs twice yields the same key, so it fires once."""
    return _local(ts, tz).strftime("%Y-%m-%dT%H:%M")


def due_slots(spec, start_ts, end_ts, tz):
    """Every `(ts, key)` slot of `spec` with `start_ts <= ts <= end_ts`, oldest
    first, one per local wall-time key. Scans epoch minutes (bounded by the
    caller's window, ≤ `LOOKBACK_S`)."""
    out, seen = [], set()
    t = int(start_ts) - int(start_ts) % 60
    if t < start_ts:
        t += 60
    while t <= end_ts:
        dt = _local(t, tz)
        if cron_matches(spec, dt):
            key = dt.strftime("%Y-%m-%dT%H:%M")
            if key not in seen:
                seen.add(key)
                out.append((t, key))
        t += 60
    return out


def next_slot(spec, after_ts, tz, horizon_s=NEXT_HORIZON_S):
    """The first `(ts, key)` slot strictly after `after_ts`, or None within
    `horizon_s` (a cron that matches nothing, e.g. 30 February)."""
    slots = due_slots(spec, after_ts + 1, after_ts + horizon_s, tz)
    return slots[0] if slots else None


# --------------------------------------------------------------------------- #
# Declaration
# --------------------------------------------------------------------------- #

def _box_windows(user=None):
    import cli_concurrency
    import cli_fleet
    return cli_fleet.box_windows(user or cli_concurrency._current_user())


def box_watch_windows(user=None):
    """This box's declared windows steered by a watch (usually none)."""
    try:
        return [w for w in _box_windows(user) if w.get("steer") == STEER]
    except Exception:  # noqa: BLE001 -- unresolvable user/table -> none
        return []


def watch_window_for(cwd, windows=None, user=None):
    """The declared window `cwd` belongs to (the ONE resolver's containment
    match, `cli_concurrency._match_window`) when it is watch-steered, else
    None. Fail-safe None on any error: a resolver fault never changes today's
    `/goal` behaviour."""
    if not cwd:
        return None
    try:
        import cli_concurrency
        wins = windows if windows is not None else _box_windows(user)
        w = cli_concurrency._match_window(cwd, None, wins or [], None)
    except Exception:  # noqa: BLE001
        return None
    return w if isinstance(w, dict) and w.get("steer") == STEER else None


def validate_watch(window):
    """Error strings for a window's steering declaration (`[]` == valid). A
    window with no `steer` is valid and carries no triggers."""
    steer = window.get("steer")
    if steer is None:
        return (["triggers declared without steer=watch"]
                if window.get("triggers") else [])
    if steer != STEER:
        return ["steer %r is not %r" % (steer, STEER)]
    errs = []
    try:
        zone(window.get("tz"))
    except CronError as e:
        errs.append(str(e))
    trigs = window.get("triggers")
    if not isinstance(trigs, list) or not trigs:
        return errs + ["steer=watch needs a non-empty triggers list"]
    seen = set()
    for i, t in enumerate(trigs):
        if not isinstance(t, dict):
            errs.append("trigger[%d] is not a dict" % i)
            continue
        name = t.get("name")
        if (not isinstance(name, str) or not name or name in seen
                or not all(c.isascii() and (c.isalnum() or c == "-")
                           for c in name)):
            errs.append("trigger[%d] name %r missing, duplicate or unsafe"
                        % (i, name))
        seen.add(name)
        try:
            parse_cron(t.get("cron"))
        except CronError as e:
            errs.append("trigger[%d] %s" % (i, e))
        prompt = t.get("prompt")
        if (not isinstance(prompt, str) or not prompt.strip() or "\n" in prompt
                or len(prompt) > PROMPT_MAX_CHARS):
            errs.append("trigger[%d] prompt must be one non-empty line of at "
                        "most %d chars" % (i, PROMPT_MAX_CHARS))
    return errs


def _specs(window):
    """`[(trigger, spec)]` for a window's parseable triggers + the error lines
    for the rest, and the window's tz (None on a bad tz, with an error)."""
    errs = []
    try:
        tz = zone(window.get("tz"))
    except CronError as e:
        return [], None, ["%s" % e]
    out = []
    for t in window.get("triggers") or []:
        try:
            out.append((t, parse_cron(t.get("cron"))))
        except (CronError, AttributeError) as e:
            errs.append("%r: %s" % (t, e))
    return out, tz, errs


# --------------------------------------------------------------------------- #
# Slot state
# --------------------------------------------------------------------------- #

def _store(state):
    st = state.get(STATE_KEY) if isinstance(state, dict) else None
    if not isinstance(st, dict):
        st = {}
    if not isinstance(st.get("since"), dict):
        st["since"] = {}
    if not isinstance(st.get("slots"), dict):
        st["slots"] = {}
    return st


def decide_slot(rec, slot_ts, now, grace_s, ready):
    """The transition for one due slot: `skip` (already terminal), `miss`
    (past its grace window, never delivered), `fire` (the pane is ready) or
    `hold` (wait for the next sweep)."""
    if isinstance(rec, dict) and rec.get("s") in TERMINAL:
        return "skip"
    if now - slot_ts > grace_s:
        return "miss"
    return "fire" if ready else "hold"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _prune(st, now, live_ids):
    for k in [k for k, v in st["slots"].items()
              if not isinstance(v, dict) or _num(v.get("slot")) is None
              or now - v["slot"] > KEEP_S
              or k.split("@", 1)[0] not in live_ids]:     # trigger undeclared
        st["slots"].pop(k, None)
    for k in [k for k in st["since"] if k not in live_ids]:
        st["since"].pop(k, None)


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

def compose_text(window_name, trigger, key, tzname):
    """The full nudge text. Its first sentence is the declared prompt, so the
    typed pointer line's headline IS the instruction (#1157 slice 3)."""
    return ("%s Plánovaný spúšťač `%s` okna %s, slot %s (%s), deklarovaný v "
            "cli_fleet (airuleset #1163, steer=watch) — po dokončení skonči "
            "turn, ďalší slot príde sám."
            % (trigger["prompt"].strip(), trigger["name"], window_name,
               key.replace("T", " "), tzname or "lokálny čas boxu"))


def _readiness(window, panes, *, projects_dir, find_transcript, capture,
               in_mode, at_idle, recent_human, gate_ok, nudges_enabled, state,
               now):
    """`(ready, why, pid, sid, tpath)` for the window's OWN pane."""
    import cli_concurrency
    if nudges_enabled is not None and not nudges_enabled(NUDGE_KIND):
        return False, "kind-off", None, None, None
    own = [(pid, cwd) for pid, cwd in panes
           if cwd and cli_concurrency.is_exact_declared_window(
               cwd, windows=[window])]
    if not own:
        return False, "no-pane", None, None, None
    if len(own) > 1:
        return False, "ambiguous-pane(%d)" % len(own), None, None, None
    pid, cwd = own[0]
    tinfo = find_transcript(projects_dir, cwd)
    tpath = tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo
    if not tpath:
        return False, "no-transcript", pid, None, None
    sid = os.path.basename(str(tpath))
    sid = sid[:-len(".jsonl")] if sid.endswith(".jsonl") else sid
    if in_mode(pid):
        return False, "in-mode", pid, sid, tpath
    if not at_idle(capture(pid)):
        return False, "busy-pane", pid, sid, tpath
    if recent_human(sid, cwd, tpath, pid):
        return False, "recent-human", pid, sid, tpath
    if not gate_ok(state, sid, NUDGE_KIND, now):
        return False, "floor", pid, sid, tpath
    return True, "", pid, sid, tpath


def _set(st, sid_key, slot_ts, status, why, now, logs, label, dry_run):
    """Record a slot transition (never on a dry-run); journal it only when
    the status or reason changed, so a held slot is not one line per sweep."""
    prev = st["slots"].get(sid_key) or {}
    if prev.get("s") == status and prev.get("why") == why:
        if not dry_run:
            prev["at"] = now
        return
    logs.append("watch-trigger: %s %s%s%s" % (
        label, status, " (%s)" % why if why else "",
        " [dry-run]" if dry_run else ""))
    if not dry_run:
        st["slots"][sid_key] = {"s": status, "slot": slot_ts, "at": now,
                                "why": why}


def _window_sweep(window, st, state, now, panes, deps, grace_s, dry_run, logs,
                  live_ids):
    wname = window.get("name", "?")
    specs, tz, errs = _specs(window)
    for e in errs:
        logs.append("watch-trigger: %s declaration error: %s" % (wname, e))
    pending = []
    for trig, spec in specs:
        tid = "%s/%s" % (wname, trig.get("name"))
        live_ids.add(tid)
        since = _num(st["since"].get(tid))
        if since is None:
            if not dry_run:
                st["since"][tid] = now
            continue                       # first sight: never retro-fire
        for slot_ts, key in due_slots(spec, max(since, now - LOOKBACK_S),
                                      now, tz):
            k = "%s@%s" % (tid, key)
            rec = st["slots"].get(k)
            verdict = decide_slot(rec, slot_ts, now, grace_s, ready=False)
            if verdict == "miss":
                why = (rec or {}).get("why") or "not-delivered"
                _set(st, k, slot_ts, "missed", why, now, logs,
                     "%s %s" % (tid, key), dry_run)
            elif verdict == "hold":
                pending.append((slot_ts, key, k, trig))
    if not pending:
        return
    ready, why, pid, sid, tpath = _readiness(window, panes, state=state,
                                             now=now, **deps["ready"])
    slot_ts, key, k, trig = pending[0]     # oldest first; one per sweep
    for s_ts, s_key, s_k, _t in pending[1:]:
        _set(st, s_k, s_ts, "held", "queued", now, logs,
             "%s/%s %s" % (wname, _t.get("name"), s_key), dry_run)
    label = "%s/%s %s" % (wname, trig.get("name"), key)
    if not ready:
        _set(st, k, slot_ts, "held", why, now, logs, label, dry_run)
        return
    if dry_run:
        logs.append("watch-trigger: %s would fire into %s [dry-run]"
                    % (label, pid))
        return
    text = compose_text(wname, trig, key, window.get("tz"))
    outcome = deps["deliver"](pid, tpath, text)
    kind = getattr(outcome, "kind", None) or ("submitted" if outcome
                                              else "not-typed")
    if kind in _DELIVERED:
        deps["mark_sent"](state, sid, NUDGE_KIND, now)
        _set(st, k, slot_ts, "fired", "" if kind == "submitted" else kind,
             now, logs, "%s -> %s" % (label, pid), dry_run)
    else:
        if kind in _TYPED_NOT_DELIVERED:
            deps["mark_sent"](state, sid, NUDGE_KIND, now)
        _set(st, k, slot_ts, "held", kind, now, logs, label, dry_run)


def watch_trigger_job(now, state, panes, *, windows, projects_dir,
                      find_transcript, capture, in_mode, at_idle,
                      recent_human, gate_ok, mark_sent, nudges_enabled,
                      deliver, dry_run=False, grace_s=GRACE_S):
    """One sweep over this box's watch windows; returns journal lines.

    Injected deps (production wiring in `run_job`):
      deliver(pid, tpath, text)          -> SendOutcome   verified keystroke
      gate_ok / mark_sent(state, sid, kind, now)          nudge_gate floor
      find_transcript(projects_dir, cwd) -> (tpath, mtime) | tpath | None
      capture(pid) -> str; in_mode(pid) -> bool; at_idle(captured) -> bool
      recent_human(sid, cwd, tpath, pid) -> bool          VETO
      nudges_enabled(kind) -> bool                        per-kind staging
    A dry-run mutates nothing and types nothing."""
    logs = []
    if not windows:
        return logs
    st = _store(state)
    deps = {"deliver": deliver, "mark_sent": mark_sent,
            "ready": {"projects_dir": projects_dir,
                      "find_transcript": find_transcript, "capture": capture,
                      "in_mode": in_mode, "at_idle": at_idle,
                      "recent_human": recent_human, "gate_ok": gate_ok,
                      "nudges_enabled": nudges_enabled}}
    live_ids = set()
    for w in windows:
        _window_sweep(w, st, state, now, panes, deps, grace_s, dry_run, logs,
                      live_ids)
    if not dry_run and isinstance(state, dict):
        _prune(st, now, live_ids)
        state[STATE_KEY] = st
    return logs


def run_job(now, state, panes, *, run, sleep_fn, projects_dir, dry_run=False):
    """The production wiring: the real primitives, resolved at call time
    through the `watchdog` package (the back-reference convention, so every
    `watchdog.<name>` test seam stays effective)."""
    import watchdog
    from watchdog import goal as _goal
    from watchdog import nudge_gate

    def _deliver(pid, tpath, text):
        return watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                      nudge=NUDGE_KIND, state=state, now=now)

    def _recent_human(sid, cwd, tpath, pid):
        return _goal._recovery_recent_human(sid, cwd, tpath, now, pid=pid,
                                            run=run)

    return watch_trigger_job(
        now, state, panes, windows=box_watch_windows(),
        projects_dir=projects_dir,
        find_transcript=watchdog.find_active_transcript,
        capture=lambda pid: watchdog.capture_pane(pid, run),
        in_mode=lambda pid: watchdog.pane_in_mode(pid, run),
        at_idle=watchdog.pane_at_idle_prompt, recent_human=_recent_human,
        gate_ok=nudge_gate.gate_ok, mark_sent=nudge_gate.mark_sent,
        nudges_enabled=watchdog.nudges_enabled, deliver=_deliver,
        dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Steering + visibility (`goal-arm --self`, `airuleset.py status`)
# --------------------------------------------------------------------------- #

def _when(ts, now, tz):
    d, n = _local(ts, tz), _local(now, tz)
    if d.date() == n.date():
        return d.strftime("%H:%M")
    return "%s %s" % (_DAYS[d.weekday()], d.strftime("%H:%M"))


def _next(window, now):
    """`(name, ts)` of the soonest next slot across the window's triggers."""
    specs, tz, _errs = _specs(window)
    best = None
    for trig, spec in specs:
        nx = next_slot(spec, now, tz)
        if nx and (best is None or nx[0] < best[1]):
            best = (trig.get("name"), nx[0])
    return best, tz


def arm_line(window, now):
    """`watch armed (N triggers, next <name> <when>)` — what `/autopilot` /
    `goal-arm --self` print in a watch window instead of arming a `/goal`."""
    n = len(window.get("triggers") or [])
    best, tz = _next(window, now)
    nxt = ("next %s %s" % (best[0], _when(best[1], now, tz)) if best
           else "no upcoming slot")
    return "watch armed (%d trigger%s, %s)" % (n, "" if n == 1 else "s", nxt)


def armed_path(home=None):
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".claude", ARMED_FILE)


def read_armed(home=None):
    try:
        with open(armed_path(home), encoding="utf-8") as h:
            data = json.load(h)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_armed(window_name, sid, now, home=None):
    """Record that the window's session acknowledged its watch (the `/goal`
    arm's counterpart). Atomic write; never raises."""
    data = read_armed(home)
    data[str(window_name)] = {"ts": now, "sid": sid or ""}
    path = armed_path(home)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(data, h)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_watchdog_state():
    """The watchdog state store, read-only (the status row's slot history)."""
    try:
        import watchdog
        with open(str(watchdog.STATE_PATH), encoding="utf-8") as h:
            data = json.load(h)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, ImportError):
        return {}


def status_row(window, now, state, kind_on=True):
    """The `status` `goal:` row of a watch window: next slot, last slot with
    its outcome, a slot held now, and every missed slot still in history."""
    wname = window.get("name", "?")
    best, tz = _next(window, now)
    n = len(window.get("triggers") or [])
    parts = ["%d trigger%s" % (n, "" if n == 1 else "s")]
    if best:
        parts.append("next %s %s" % (best[0], _when(best[1], now, tz)))
    recs = []
    for k, v in (_store(state)["slots"]).items():
        if (k.startswith(wname + "/") and isinstance(v, dict)
                and _num(v.get("slot")) is not None):
            recs.append((v["slot"], k.split("/", 1)[1].split("@", 1)[0], v))
    recs.sort(key=lambda r: r[0])
    done = [r for r in recs if r[2].get("s") in TERMINAL]
    if done:
        ts, name, v = done[-1]
        parts.append("last %s %s %s" % (name, _when(ts, now, tz), v["s"]))
    for ts, name, v in recs:
        if v.get("s") == "held":
            parts.append("held %s %s (%s)" % (name, _when(ts, now, tz),
                                              v.get("why") or "?"))
    missed = ["%s %s" % (name, _when(ts, now, tz))
              for ts, name, v in done if v.get("s") == "missed"]
    if missed:
        parts.append("missed " + ", ".join(missed[-3:]))
    if not kind_on:
        parts.append("nudge kind OFF — stage: nudges on --kind %s" % NUDGE_KIND)
    return "goal: watch armed (%s)" % "; ".join(parts)
