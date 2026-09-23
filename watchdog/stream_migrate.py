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
the arm (the positive achieved record) for this MIGRATION trigger.

#1133 adds the second trigger, ANSWERED-(A) (`decide_answered`): a stream loop
(old OR current template) that ended on stop (A) -- its last `❓ NEEDS YOU` --
is re-armed with the current template once a REAL human prompt followed that
`❓` (`answered_question`: never a `/goal` command, a task-notification, a
hook/system/meta record or a tool result) and the `❓` is at or after the
current arm. Same guards 2-5, the same `stream-migrate` origin, the same 1/h
store; `eligible()` is the shared core (reduced authority + a stream template +
idle) and each trigger adds only its own proof -- the old (B) payload for the
migration, the answered `❓` for (A). `delivery_ok` re-checks core + proof at
the moment of delivery. An unanswered `❓` is exactly what stop (A) protects,
so it is never re-armed; an owner `/goal clear` leaves the mark "cleared",
which never reaches dark-watch's "set" branch.

Pure helpers + one decision function with injected seams (template / pending /
record / reset), so `watchdog.goal` only wires it in.
"""

import os
from datetime import datetime

import watchdog
from watchdog import goal_turn_liveness as _turn_liveness
from watchdog import transcripts as _tx

ORIGIN = "stream-migrate"
STREAM_AUTHORITIES = ("branch-merge", "fork-no-merge")
IDLE_MIN_S = 600
MIN_GAP_S = 3600
_KEEP_S = 24 * 3600
# Both tokens sat in EVERY reduced template from #159 (the (B) presence proof)
# until #1128; no full template carries "SLICE EMPTY", no current template
# carries either (locked in tests/test_goal_stream_migrate_1128.py).
_OLD_MARKERS = ("(B) SLICE EMPTY", "🏁 BACKLOG EMPTY: 0 open")
# #1133 -- the current reduced templates' own `stream-idle` clause (every
# reduced variant carries it, no full variant does; locked in
# tests/test_goal_stream_answered_1133.py).
_NEW_MARKERS = ("NO BACKLOG-EMPTY END", "airuleset.py stream-wait",
                "Only the OWNER ends this loop")
# #1133 -- the answered-(A) proof reads a LARGER bounded tail than the #890
# 200-entry one: the owner's answer starts a real work turn whose tool entries
# must not scroll the `❓` out. Memoized per (arm, transcript mtime) in dark-watch.
ANSWER_TAIL_BYTES = 8_000_000
ANSWER_TAIL_ENTRIES = 20_000


def is_old_stream_payload(payload):
    """True iff an armed goal text is a pre-#1128 reduced template (it carries
    the removed (B) stop). Whitespace-normalized, so a soft-wrapped stored
    condition still matches."""
    s = " ".join(str(payload or "").split())
    return all(m in s for m in _OLD_MARKERS)


def is_stream_payload(payload):
    """#1133: True iff an armed goal text is a reduced STREAM template -- the
    old pre-#1128 one (`is_old_stream_payload`) or a current one (its
    `stream-idle` clause). Whitespace-normalized like `is_old_stream_payload`."""
    s = " ".join(str(payload or "").split())
    return is_old_stream_payload(s) or all(m in s for m in _NEW_MARKERS)


def _epoch(ts):
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _is_answer(entry):
    """A REAL human prompt: `watchdog._is_genuine_human_prompt` (#522 -- no
    `/goal ` / `<command-` / `<task-notification>` / `<system-reminder` /
    Stop-hook feedback / isMeta / compact summary / tool_result; a Discord-
    relayed answer IS human) plus a belt for a bare `/goal` (no trailing space,
    so the shared prefix list does not catch it)."""
    if not watchdog._is_genuine_human_prompt(entry):
        return False
    return not (_tx._entry_text(entry) or "").strip().startswith("/goal")


def answered_question(tpath, mark_ts):
    """`(ok, why)` -- the ANSWERED-(A) proof (#1133): the transcript's LAST
    `❓ NEEDS YOU` turn (the marker line of a real assistant turn, the same
    `_turn_marker_line` extraction the #522 re-poke detector uses -- a
    `❓ ASKED` body line under a `⏳` terminal is not one) is at or after the
    current arm (`mark_ts`; fail CLOSED when either time is unknown) AND a real
    human prompt (`_is_answer`) follows it. Bounded tail read, never raises."""
    entries = _tx._read_jsonl_byte_tail(tpath, ANSWER_TAIL_BYTES,
                                        ANSWER_TAIL_ENTRIES) if tpath else []
    q = None
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if (not isinstance(e, dict) or e.get("type") != "assistant"
                or e.get("isApiErrorMessage") is True):
            continue
        if _tx._NEEDS_YOU_RX.match(_tx._turn_marker_line(
                (_tx._entry_text(e) or "").strip())):
            q = i
            break
    if q is None:
        return False, "no ❓ NEEDS YOU in the transcript tail"
    q_ts = _epoch(entries[q].get("timestamp"))
    if not (isinstance(mark_ts, (int, float)) and q_ts is not None
            and q_ts >= mark_ts):
        return False, "the last ❓ NEEDS YOU predates the arm (or no time)"
    if any(_is_answer(e) for e in entries[q + 1:]):
        return True, ""
    return False, "the last ❓ NEEDS YOU is unanswered"


def eligible(authority, payload, tpath, now):
    """(ok, reason): the shared CORE -- guard 1's box half + a stream template +
    guard 2 -- used by both dark-watch triggers and the delivery re-check (so
    they can never disagree). Each trigger adds only its own proof."""
    if authority not in STREAM_AUTHORITIES:
        return False, "not a stream box (authority=%s)" % authority
    if not is_stream_payload(payload):
        return False, "armed goal is not a stream template"
    age = _turn_liveness.transcript_age_s(tpath, now)
    if age is None:
        return False, "transcript unreadable"
    if age < IDLE_MIN_S:
        return False, "transcript not idle %ds" % IDLE_MIN_S
    return True, ""


def delivery_ok(origin, authority, mark_fn, tinfo_fn, now, sid=None):
    """`(ok, why)`: may a `deliver_goal` request pass the #1113 structured-armed
    refusal? Only THIS origin (checked FIRST, so every other origin pays no
    read), and only while the core AND a trigger proof still hold at the moment
    of delivery: the old (B) payload (migration, #1128) or, for any other
    stream payload, the answered `❓` (#1133) -- read from `sid`'s OWN
    transcript (a different newest transcript in the cwd refuses). `mark_fn()`
    gives the structured mark (payload + ts); `tinfo_fn()` gives
    `find_active_transcript(...)` -- the NEWEST transcript in the cwd, never
    older than the session's own, so the idle re-check errs toward refusing.
    On a pass `why` names the trigger (journalled by the caller)."""
    if origin != ORIGIN:
        return False, ""
    tinfo = tinfo_fn()
    tpath = tinfo[0] if tinfo else None
    mark = mark_fn() or {}
    payload = mark.get("payload")
    ok, why = eligible(authority, payload, tpath, now)
    if not ok:
        return False, why
    if is_old_stream_payload(payload):
        return True, "old stream template, transcript idle"
    if sid is not None and getattr(tpath, "stem", None) != sid:
        return False, "newest transcript is not this session's"
    ok, why = answered_question(tpath, mark.get("ts"))
    return (True, "answered ❓, transcript idle") if ok else (False, why)


def _reap(store, now):
    for sid in [k for k, v in list(store.items())
                if not (isinstance(v, dict)
                        and isinstance(v.get("seen"), (int, float))
                        and 0 <= now - v["seen"] <= _KEEP_S)]:
        store.pop(sid, None)


def decide(sid, cwd, tpath, payload, now, loc, dry_run, store, template_fn,
           pending_fn, record_fn, reset_fn, proof_fn=None,
           label="stream-migrate"):
    """The dark-watch decision for a DARK, mark-"set" session whose armed goal
    is a stream template. Returns `(logline_or_None, handled)`. The MIGRATION
    trigger (#1128: an old template, 🏁 proven by the caller) passes no
    `proof_fn`; the ANSWERED-(A) trigger (#1133, `decide_answered`) passes its
    own `proof_fn() -> (ok, why)` and `label`.

    Cheap gates first (idle, then the trigger proof if any, the 1 h gap, a
    pending request), then the template resolution. No newest-turn gate:
    dark-watch reaches only a DARK footer, so a 🏁 the evaluator rejected (loop
    still running, ◎ lit) never gets here, while a human turn after a real
    achievement must not block the migration (live david1, 2026-09-23).
    `handled` is False -- the caller's normal fulfilled / answer / dead-loop
    path applies -- for a non-stream box, an unreadable transcript, an
    unresolvable template, and (answered trigger only) a not-yet-idle
    transcript (silent) or a failed proof: neither is this trigger's own state,
    so the #459 visibility must not be hidden. Every other outcome is trigger-
    bound (True). A skip is journalled ONCE per (session, reason). Nothing is
    mutated on dry_run."""
    if not dry_run:
        _reap(store, now)
    rec = store.get(sid) if isinstance(store.get(sid), dict) else {}
    handled = True
    ok, why = True, ""
    age = _turn_liveness.transcript_age_s(tpath, now)
    last = rec.get("last")
    if age is None:
        ok, why, handled = False, "transcript unreadable", False
    elif age < IDLE_MIN_S:
        if proof_fn is not None:   # not yet the answered trigger's state: silent
            return None, False
        ok, why = False, "transcript not idle %ds" % IDLE_MIN_S
    else:
        pok, pwhy = proof_fn() if proof_fn is not None else (True, "")
        if not pok:
            ok, why, handled = False, pwhy, False
        elif isinstance(last, (int, float)) and 0 <= now - last < MIN_GAP_S:
            ok, why = False, "1/h attempt gap"
        elif pending_fn(sid):
            ok, why = False, "a request is already pending"
    text = authority = None
    if ok:
        text, authority = template_fn(cwd)
        if authority not in STREAM_AUTHORITIES:
            return None, False
        if not text:
            ok, why, handled = False, "no current template resolved", False
        elif is_old_stream_payload(text):
            ok, why = False, "the resolved template is still the old one"
    if not ok:
        if dry_run or rec.get("why") == why:
            return None, handled
        store[sid] = dict(rec, why=why, seen=now)
        return ("dark-watch %s sid=%s -> %s SKIP: %s"
                % (loc, sid, label, why)), handled
    if dry_run:
        return ("dark-watch %s sid=%s -> %s would record "
                "(dry-run, authority=%s)" % (loc, sid, label.upper(),
                                             authority)), True
    reset_fn()
    record_fn(text, authority)
    store[sid] = {"last": now, "seen": now, "why": "recorded"}
    return ("dark-watch %s sid=%s -> %s: recording re-arm with the current "
            "stream template (authority=%s; %s, transcript idle >= %ds, %s)"
            % (loc, sid, label.upper(), authority,
               "old (B) template" if proof_fn is None else "answered ❓ NEEDS YOU",
               IDLE_MIN_S, "#1128" if proof_fn is None else "#1133")), True


def decide_answered(sid, cwd, tpath, mark, now, loc, dry_run, store, memo,
                    template_fn, pending_fn, record_fn, reset_fn):
    """#1133 -- the ANSWERED-(A) trigger over `decide`: a DARK, mark-"set"
    stream loop whose last `❓ NEEDS YOU` (after the arm) was answered by a real
    human prompt. `(None, False)` at once for a non-stream payload. The proof
    is memoized in `memo[sid]` per (arm ts, transcript mtime) -- a dark idle
    pane is swept every 60 s but its transcript does not change, so the bounded
    8 MB read runs once per change (never written on dry_run; reaped 24 h after
    its last sighting, like `store`)."""
    payload = mark.get("payload") if isinstance(mark, dict) else None
    if not is_stream_payload(payload):
        return None, False
    mark_ts = mark.get("ts")

    def _proof():
        try:
            key = [mark_ts, os.stat(tpath).st_mtime_ns]
        except (OSError, TypeError):
            key = [mark_ts, None]
        hit = memo.get(sid)
        if isinstance(hit, dict) and hit.get("key") == key:
            return bool(hit.get("ok")), str(hit.get("why") or "")
        ok, why = answered_question(tpath, mark_ts)
        if not dry_run:
            memo[sid] = {"key": key, "ok": ok, "why": why, "seen": now}
        return ok, why

    if not dry_run:
        _reap(memo, now)
    return decide(sid, cwd, tpath, payload, now, loc, dry_run, store,
                  template_fn, pending_fn, record_fn, reset_fn,
                  proof_fn=_proof, label="stream-answered")


def state_store(state, key, dry_run):
    """A per-sid state dict of the stream triggers -- a COPY on dry_run, so an
    honest dry-run mutates no state."""
    return dict(state.get(key) or {}) if dry_run else state.setdefault(key, {})


def seams(sid, cwd, now, dry_run, state, episode_states, template_fn,
          record_request, load_requests):
    """The injected seams of `decide`, shared by both dark-watch triggers:
    `(store, template_fn, pending_fn, record_fn, reset_fn)`. `record_request`
    / `load_requests` are the caller's request-store functions (bound to its
    requests path); `record_fn` writes a request of THIS origin; `reset_fn`
    ends the session's dead-loop episode (`episode_states`)."""
    def _record(text, auth):
        record_request(sid, cwd, text, auth, now=now, origin=ORIGIN)

    def _reset():
        for st in episode_states:
            st.pop(sid, None)
    return (state_store(state, "goal_stream_migrate", dry_run), template_fn,
            lambda s: isinstance(load_requests().get(s), dict), _record, _reset)


def dark_watch_answered(logs, sid, cwd, tpath, mark, now, loc, dry_run, state,
                        seams_):
    """#1133 -- `goal_dark_watch`'s call of the ANSWERED-(A) trigger for a DARK,
    mark-"set" pane (after the fulfilled lane, before the #890 answer rider).
    Appends its one log line (if any) to `logs`; True = handled (the caller
    `continue`s), False = the caller's answer / dead-loop machinery applies."""
    st, template_fn, pending_fn, record_fn, reset_fn = seams_
    line, handled = decide_answered(
        sid, cwd, tpath, mark, now, loc, dry_run, st,
        state_store(state, "goal_stream_answered_memo", dry_run),
        template_fn, pending_fn, record_fn, reset_fn)
    if line:
        logs.append(line)
    return handled
