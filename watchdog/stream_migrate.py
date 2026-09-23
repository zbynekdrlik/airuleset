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

Recognizers, the bounded transcript proof, and one decision function with
injected `Seams` (store / template / pending / record / reset) plus the wiring
helpers, so `watchdog.goal` only passes its request store and episode state in.
"""

import os
from datetime import datetime
from typing import Callable, NamedTuple

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


# The watchdog's OWN typed keystrokes that the shared #522
# `watchdog._MACHINE_PROMPT_PREFIXES` does not list (that list is tuned for the
# recent-human gates, where a nudge misread as human only DEFERS). Here a nudge
# misread as the owner's answer would re-arm over an unanswered ❓, so every
# known own prefix is rejected: stash's own-payload + own-submit sets, the #923
# batch prefix, the #890 detector's extras, the report-owed card nudge (#1133
# review). Resolved at call time from their owning modules (one source each).
_OWN_EXTRA_PREFIXES = ("recheck:", "UNPARK-AUDIT:", "report-owed:",
                       "gk-freshness backstop", "goal-guard:", "lane-check:",
                       "lane-reconcile:", "task-hygiene:", "Discord pripomienka:",
                       # the card_flags relay of an owner's ❓ REACTION: a new
                       # question about a card, never an answer to ours
                       "Užívateľ označil túto tvoju Discord správu")
_OWN_EXACT = ("resume",)


def _own_keystroke(text):
    from watchdog import nudge_gate, stash
    prefixes = (tuple(stash._JANITOR_OWN_PREFIXES)
                + tuple(stash._OWN_NUDGE_SUBMIT_PREFIXES)
                + (nudge_gate.BATCH_PREFIX,) + _OWN_EXTRA_PREFIXES)
    return (text in _OWN_EXACT or text.startswith("/goal")
            or any(text.startswith(p) for p in prefixes))


def _is_answer(entry):
    """A REAL human prompt: `watchdog._is_genuine_human_prompt` (#522 -- no
    `/goal ` / `<command-` / `<task-notification>` / `<system-reminder` /
    Stop-hook feedback / isMeta / compact summary / tool_result; a Discord-
    relayed answer IS human) AND not one of the watchdog's own keystrokes
    (`_own_keystroke`, incl. a bare `/goal`)."""
    if not watchdog._is_genuine_human_prompt(entry):
        return False
    return not _own_keystroke((_tx._entry_text(entry) or "").strip())


def question_state(tpath, mark_ts):
    """`(state, why)` for the transcript's LAST `❓ NEEDS YOU` turn (the marker
    line of a real assistant turn, the same `_turn_marker_line` extraction the
    #522 re-poke detector uses -- a `❓ ASKED` body line under a `⏳` terminal
    is not one): "answered" (at or after the current arm AND a real human prompt,
    `_is_answer`, follows it), "unanswered" (at or after the arm, no such
    prompt), or "none" (no `❓ NEEDS YOU` in the bounded tail, or it predates the
    arm, or a time is unknown -- fail CLOSED for the answered proof). Never
    raises."""
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
        return "none", "no ❓ NEEDS YOU in the transcript tail"
    q_ts = _epoch(entries[q].get("timestamp"))
    if not (isinstance(mark_ts, (int, float)) and q_ts is not None
            and q_ts >= mark_ts):
        return "none", "the last ❓ NEEDS YOU predates the arm (or no time)"
    if any(_is_answer(e) for e in entries[q + 1:]):
        return "answered", ""
    return "unanswered", "the last ❓ NEEDS YOU is unanswered"


def answered_question(tpath, mark_ts):
    """`(ok, why)` -- the ANSWERED-(A) proof (#1133): `question_state` is
    "answered"."""
    st, why = question_state(tpath, mark_ts)
    return st == "answered", why


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
    stream payload, the answered `❓` (#1133) -- and, for BOTH, never while the
    last `❓ NEEDS YOU` after the arm is unanswered. Read from `sid`'s OWN
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
    if sid is not None and getattr(tpath, "stem", None) != sid:
        return False, "newest transcript is not this session's"
    st, why = question_state(tpath, mark.get("ts"))
    if st == "unanswered":          # never type over an open owner question
        return False, why
    if is_old_stream_payload(payload):
        return True, "old stream template, transcript idle"
    return (True, "answered ❓, transcript idle") if st == "answered" \
        else (False, why)


def _reap(store, now):
    for sid in [k for k, v in list(store.items())
                if not (isinstance(v, dict)
                        and isinstance(v.get("seen"), (int, float))
                        and 0 <= now - v["seen"] <= _KEEP_S)]:
        store.pop(sid, None)


class Seams(NamedTuple):
    """The injected seams of `decide` (built by the caller's `seams()` wiring,
    so `watchdog.goal` keeps its request store and dead-loop episode state)."""
    store: dict
    template_fn: Callable
    pending_fn: Callable
    record_fn: Callable
    reset_fn: Callable


def decide(sid, cwd, tpath, payload, now, loc, dry_run, store, template_fn,
           pending_fn, record_fn, reset_fn, proof_fn=None, confirm_fn=None,
           mark_ts=None):
    """The dark-watch decision for a DARK, mark-"set" session whose armed goal
    is a stream template. Returns `(logline_or_None, handled)`. The MIGRATION
trigger (#1128: an old template, 🏁 proven by the caller) passes no
    `proof_fn` but the arm's `mark_ts`, so it never records while the last `❓
    NEEDS YOU` after the arm is unanswered (the SAME `question_state` predicate
    `delivery_ok` refuses on -- record and delivery never disagree); the
    ANSWERED-(A) trigger (#1133, `decide_answered`) passes its own `proof_fn() ->
    (ok, why)` and a `confirm_fn() -> bool` (the #524 dark-footer confirmation
    run: an answered `❓` alone does not prove the loop ENDED, since (A) does not
    stop a loop with a lane live), advanced LAST, only on the path that stays
    this trigger's own (so the dead-loop path never advances it twice a sweep).

    Order: idle; then the migration's cheap gates (1 h gap, a pending request)
    before its question read, or the answered trigger's proof; the template
    resolution and the shared `eligible()` core; then (answered only) the
    confirmation -- advanced every sweep, the 1 h gap included -- and the gap +
    pending gates. No newest-turn gate: dark-watch reaches only a DARK
    footer, so a 🏁 the evaluator rejected (loop still running, ◎ lit) never
    gets here, while a human turn after a real achievement must not block the
    migration (live david1, 2026-09-23). `handled` is False -- the caller's
    normal fulfilled / answer / dead-loop path applies -- for a non-stream box,
    an unreadable transcript, an unresolvable template, and (answered trigger
    only) a not-yet-idle transcript (silent) or a failed proof: neither is this
    trigger's own state, so the #459 visibility must not be hidden. Every other
    outcome is trigger-bound (True). A skip is journalled ONCE per (session,
    reason). Nothing is mutated on dry_run (the caller's `confirm_fn` included)."""
    if not dry_run:
        _reap(store, now)
    rec = store.get(sid) if isinstance(store.get(sid), dict) else {}
    label = "stream-migrate" if proof_fn is None else "stream-answered"

    def _skip(why, handled):
        if dry_run or rec.get("why") == why:
            return None, handled
        store[sid] = dict(rec, why=why, seen=now)
        return ("dark-watch %s sid=%s -> %s SKIP: %s"
                % (loc, sid, label, why)), handled

    age = _turn_liveness.transcript_age_s(tpath, now)
    if age is None:
        return _skip("transcript unreadable", False)
    if age < IDLE_MIN_S:
        if proof_fn is not None:   # not yet the answered trigger's state: silent
            return None, False
        return _skip("transcript not idle %ds" % IDLE_MIN_S, True)
    last = rec.get("last")
    gap = isinstance(last, (int, float)) and 0 <= now - last < MIN_GAP_S
    if proof_fn is not None:
        pok, pwhy = proof_fn()
        if not pok:
            return _skip(pwhy, False)
    else:                          # migration: the cheap gates before any read
        if gap:
            return _skip("1/h attempt gap", True)
        if pending_fn(sid):
            return _skip("a request is already pending", True)
        if (mark_ts is not None
                and question_state(tpath, mark_ts)[0] == "unanswered"):
            return _skip("the last ❓ NEEDS YOU is unanswered", False)
    text, authority = template_fn(cwd)
    if authority not in STREAM_AUTHORITIES:
        return None, False
    cok, cwhy = eligible(authority, payload, tpath, now)
    if not cok:
        return _skip(cwhy, False)
    if not text:
        return _skip("no current template resolved", False)
    if is_old_stream_payload(text):
        return _skip("the resolved template is still the old one", True)
    if confirm_fn is not None:     # advanced ONLY on this trigger-owned path
        if not confirm_fn():
            return _skip("dark footer not yet confirmed (#524 run)", True)
        if gap:
            return _skip("1/h attempt gap", True)
        if pending_fn(sid):
            return _skip("a request is already pending", True)
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


def decide_answered(sid, cwd, tpath, mark, now, loc, dry_run, seams_, memo,
                    confirm_fn):
    """#1133 -- the ANSWERED-(A) trigger over `decide`: a DARK, mark-"set"
    stream loop whose last `❓ NEEDS YOU` (after the arm) was answered by a real
    human prompt, re-armed once the dark footer is #524-confirmed. The proof is
    memoized in `memo[sid]` per (arm ts, transcript mtime) -- a dark idle pane
    is swept every 60 s but its transcript does not change, so the bounded
    8 MB read runs once per change (never written on dry_run; `seen` refreshed
    on every hit, so an entry is reaped 24 h after its last sighting)."""
    payload = mark.get("payload") if isinstance(mark, dict) else None
    mark_ts = mark.get("ts") if isinstance(mark, dict) else None

    def _proof():
        try:
            key = [mark_ts, os.stat(tpath).st_mtime_ns]
        except (OSError, TypeError):
            key = [mark_ts, None]
        hit = memo.get(sid)
        if isinstance(hit, dict) and hit.get("key") == key:
            ok, why = bool(hit.get("ok")), str(hit.get("why") or "")
        else:
            ok, why = answered_question(tpath, mark_ts)
        if not dry_run:
            memo[sid] = {"key": key, "ok": ok, "why": why, "seen": now}
        return ok, why

    if not dry_run:
        _reap(memo, now)
    return decide(sid, cwd, tpath, payload, now, loc, dry_run, *seams_,
                  proof_fn=_proof, confirm_fn=lambda: confirm_fn(mark_ts))


def state_store(state, key, dry_run):
    """A per-sid state dict of the stream triggers -- a COPY on dry_run, so an
    honest dry-run mutates no state."""
    return dict(state.get(key) or {}) if dry_run else state.setdefault(key, {})


def seams(sid, cwd, now, dry_run, state, episode_states, template_fn,
          record_request, load_requests):
    """`Seams` for both dark-watch triggers. `record_request` / `load_requests`
    are the caller's request-store functions (bound to its requests path);
    `record_fn` writes a request of THIS origin; `reset_fn` ends the session's
    dead-loop episode (`episode_states`)."""
    def _record(text, auth):
        record_request(sid, cwd, text, auth, now=now, origin=ORIGIN)

    def _reset():
        for st in episode_states:
            st.pop(sid, None)
    return Seams(state_store(state, "goal_stream_migrate", dry_run), template_fn,
                 lambda s: isinstance(load_requests().get(s), dict), _record,
                 _reset)


def confirm_run(confirm_state, sid, now, dry_run, advance_fn):
    """`confirm_fn` for the answered trigger: advance the caller's #524
    death-confirmation run (`advance_fn` = `goal._dark_confirm_advance`, the
    SAME `confirm_state` every armed / undeterminable / mtime-advance read in
    dark-watch resets) and return whether it is confirmed. Dry-run: evaluate,
    never persist."""
    def _fn(mark_ts):
        confirmed, win = advance_fn(confirm_state.get(sid), mark_ts, now)
        if not dry_run:
            confirm_state[sid] = win
        return confirmed
    return _fn


def dark_watch_answered(logs, sid, cwd, tpath, mark, now, loc, dry_run, state,
                        seams_fn, confirm_fn):
    """#1133 -- `goal_dark_watch`'s call of the ANSWERED-(A) trigger for a DARK,
    mark-"set" pane (after the fulfilled lane, before the #890 answer rider).
    A non-stream payload returns False at once, touching no state (a full box is
    unchanged); only then are `seams_fn()` and the proof memo built. Appends its
    one log line (if any) to `logs`; True = handled (the caller `continue`s),
    False = the caller's answer / dead-loop machinery applies."""
    if not is_stream_payload(mark.get("payload") if isinstance(mark, dict)
                             else None):
        return False
    line, handled = decide_answered(
        sid, cwd, tpath, mark, now, loc, dry_run, seams_fn(),
        state_store(state, "goal_stream_answered_memo", dry_run), confirm_fn)
    if line:
        logs.append(line)
    return handled
