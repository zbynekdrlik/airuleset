"""watchdog.stream_migrate -- #1128 part 3: the MIGRATION-ONLY re-arm of an
achieved OLD-template stream loop with the current (no-done-state) template.

Why it exists: before #1128 the reduced-authority (fork-no-merge /
branch-merge) `/goal` templates carried a (B) SLICE-EMPTY stop, so a stream
whose own `I` hit 0 ACHIEVED and sat idle (david1-4, 2026-09-23). #1128 removed
that stop from the templates, but a session armed BEFORE the change still holds
the old text, and every watchdog re-arm is refused while its goal mark reads
"set" (the #1113 structured-armed refusal) -- an achieved loop always leaves the
mark "set" because Claude Code writes no achieved marker. The owner authorized
a narrow watcher for exactly this migration (ROZHODNUTÉ on #1128, option A).

The binding guards (ALL must hold, else a journalled skip):
  1. reduced authority AND the recorded armed goal text carries the REMOVED (B)
     (`is_old_stream_payload`) -- once every stream holds the new template this
     can never fire again;
  2. the transcript is idle >= `IDLE_MIN_S` (10 min) -- re-checked at delivery,
     where the idle-prompt / "Waiting for background agents" / running-tool
     gates of `deliver_goal` also apply;
  3. no active `◎ /goal` footer (dark-watch only reaches a dark pane;
     `deliver_goal` drops an armed pane);
  4. a `claude` pane (dark-watch walks claude panes only; delivery resolves the
     session's pane among claude panes only);
  5. the existing verified delivery (`deliver_goal` + the own-template janitor),
     ONE recorded attempt per session per `MIN_GAP_S` (1 h).
The caller also requires the old template's own `🏁 BACKLOG EMPTY:` line AFTER
the arm (the positive achieved record), so a loop that ended on a `❓` question
is never touched here -- the (A)-answer re-arm is a separate, later part.

Pure helpers + one decision function with injected seams (template / pending /
record / reset), so `watchdog.goal` only wires it in.
"""

from watchdog import goal_turn_liveness as _turn_liveness

ORIGIN = "stream-migrate"
STREAM_AUTHORITIES = ("branch-merge", "fork-no-merge")
IDLE_MIN_S = 600
MIN_GAP_S = 3600
_KEEP_S = 24 * 3600
# Both tokens sat in EVERY reduced template from #159 (the (B) presence proof)
# until #1128; no full template carries "SLICE EMPTY", no current template
# carries either (locked in tests/test_goal_stream_migrate_1128.py).
_OLD_MARKERS = ("(B) SLICE EMPTY", "🏁 BACKLOG EMPTY: 0 open")


def is_old_stream_payload(payload):
    """True iff an armed goal text is a pre-#1128 reduced template (it carries
    the removed (B) stop). Whitespace-normalized, so a soft-wrapped stored
    condition still matches."""
    s = " ".join(str(payload or "").split())
    return all(m in s for m in _OLD_MARKERS)


def eligible(authority, payload, tpath, now):
    """(ok, reason): guards 1 + 2, shared by the dark-watch decision and the
    delivery re-check (so the two can never disagree)."""
    if authority not in STREAM_AUTHORITIES:
        return False, "not a stream box (authority=%s)" % authority
    if not is_old_stream_payload(payload):
        return False, "armed goal is not the old (B) stream template"
    age = _turn_liveness.transcript_age_s(tpath, now)
    if age is None:
        return False, "transcript unreadable"
    if age < IDLE_MIN_S:
        return False, "transcript not idle %ds" % IDLE_MIN_S
    return True, ""


def delivery_ok(origin, authority, payload, tinfo, now):
    """True iff a `deliver_goal` request may pass the #1113 structured-armed
    refusal: only THIS origin, and only while guards 1 + 2 still hold at the
    moment of delivery (`tinfo` = `find_active_transcript(...)` or None)."""
    if origin != ORIGIN:
        return False
    return eligible(authority, payload, tinfo[0] if tinfo else None, now)[0]


def _reap(store, now):
    for sid in [k for k, v in list(store.items())
                if not (isinstance(v, dict)
                        and isinstance(v.get("seen"), (int, float))
                        and 0 <= now - v["seen"] <= _KEEP_S)]:
        store.pop(sid, None)


def decide(sid, cwd, tpath, payload, now, loc, dry_run, store, template_fn,
           pending_fn, record_fn, reset_fn):
    """The dark-watch decision for a DARK, mark-"set", 🏁-proven session whose
    armed goal is an old stream template. Returns `(logline_or_None, handled)`;
    `handled` is False only for a non-stream box (the caller's normal path
    applies), else True -- an ended old-template stream is migration-bound, so
    the dead-loop ping / dark-rearm must not run for it. A skip is journalled
    ONCE per (session, reason); all state mutations are skipped on dry_run."""
    _reap(store, now)
    text, authority = template_fn(cwd)
    if authority not in STREAM_AUTHORITIES:
        return None, False
    ok, why = eligible(authority, payload, tpath, now)
    rec = store.get(sid) if isinstance(store.get(sid), dict) else {}
    if ok and pending_fn(sid):
        ok, why = False, "a request is already pending"
    if ok and not text:
        ok, why = False, "no current template resolved"
    last = rec.get("last")
    if ok and isinstance(last, (int, float)) and 0 <= now - last < MIN_GAP_S:
        ok, why = False, "1/h attempt gap"
    if not ok:
        if dry_run or rec.get("why") == why:
            return None, True
        store[sid] = dict(rec, why=why, seen=now)
        return ("dark-watch %s sid=%s -> stream-migrate SKIP: %s"
                % (loc, sid, why)), True
    if dry_run:
        return ("dark-watch %s sid=%s -> STREAM-MIGRATE would record "
                "(dry-run, authority=%s)" % (loc, sid, authority)), True
    reset_fn()
    record_fn(text, authority)
    store[sid] = {"last": now, "seen": now, "why": "recorded"}
    return ("dark-watch %s sid=%s -> STREAM-MIGRATE: recording re-arm with the "
            "current stream template (authority=%s; old (B) template, "
            "transcript idle >= %ds, #1128)" % (loc, sid, authority,
                                               IDLE_MIN_S)), True
