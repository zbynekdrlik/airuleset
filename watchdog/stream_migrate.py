"""watchdog.stream_migrate -- the ONE stream-watcher rule (#1143): a DARK
stream loop that the owner did not end is re-armed with the current stream
template.

Why it exists: every watchdog re-arm is refused while a session's structured
goal mark reads "set" (the #1113 structured-armed refusal) -- and a stream
loop that went dark without the owner ending it leaves the mark "set": an
achieved pre-#1128 old-template loop (Claude Code writes no achieved marker,
#1128), a loop that ended on an answered stop-(A) `❓` (#1133), and a session
that was relaunched, or crashed and was resumed, without its `/goal` (live:
david2 + david3 after the #1142 respawn, 24.9.2026). #1128 and #1133 were two
special triggers; each incident found a new shape. The owner's rule is general
-- only a `/goal clear` or a stopped Claude ends a stream loop -- so #1143
replaced both triggers with ONE eligibility rule (Design-by: main on #1143).

The rule (ALL must hold, else a journalled skip):
  1. a reduced-authority stream box (`STREAM_AUTHORITIES`) whose recorded
     armed payload is a stream template, old or current (`is_stream_payload`);
  2. the structured mark is "set", never "cleared" (an owner `/goal clear` is
     the stop and never reaches dark-watch's "set" branch);
  3. the pane runs `claude`, never a bare shell (dark-watch walks claude panes
     only; delivery resolves the session's pane among claude panes only);
  4. the footer is DARK -- no `◎` -- proven by the #524 confirmation run (8
     dark reads over >= 10 min on the SAME `confirm_state` every armed /
     undeterminable / mtime-advance read resets), never one read;
  5. the transcript is idle >= `IDLE_MIN_S` (10 min), re-checked at delivery;
  6. no UNANSWERED `❓ NEEDS YOU` at or after the arm (`open_question`: the
     #1133 real-human-prompt proof -- a `/goal` command, a task-notification,
     a hook/system/meta record and the watchdog's own nudges never answer it;
     an unknown arm time fails CLOSED);
  7. ONE recorded attempt per session per `MIN_GAP_S` (1 h), no pending
     request, and the resolved template is not itself old;
  8. the existing verified delivery (`deliver_goal` + the own-template
     janitor, one typed attempt per request).
`delivery_ok` re-checks 1, 5, 6 and the session's own transcript at the
moment of delivery -- the ONLY origin that passes the #1113 refusal. A full
box is unchanged: a non-stream payload returns before any state is touched
(a stream payload on a non-stream box only journals its skip, never records).

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
# #1133 -- the open-question proof reads a LARGER bounded tail than the #890
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
    is not one): "answered" (a real human prompt, `_is_answer`, follows it),
    "unanswered" (no such prompt), or "none" (no `❓ NEEDS YOU` in the bounded
    tail, or it provably predates the current arm -- the owner re-armed after
    it). An unknown `❓` or arm time never makes it "none" (fail CLOSED: it can
    only make a question count as open). Never raises."""
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
    if _is_time(mark_ts) and q_ts is not None and q_ts < mark_ts:
        return "none", "the last ❓ NEEDS YOU predates the arm"
    if any(_is_answer(e) for e in entries[q + 1:]):
        return "answered", ""
    return "unanswered", "the last ❓ NEEDS YOU is unanswered"


def _is_time(ts):
    return isinstance(ts, (int, float)) and not isinstance(ts, bool)


def open_question(tpath, mark_ts):
    """`(open, why)` -- guard 6: True while the loop's last `❓ NEEDS YOU` at or
    after the current arm is unanswered (stop (A) is waiting on the owner), and
    also when the arm time is unknown (the guard is then unprovable -- fail
    CLOSED, never re-arm over a question we cannot place)."""
    if not _is_time(mark_ts):
        return True, "the arm time is unknown (an open ❓ is unprovable)"
    st, why = question_state(tpath, mark_ts)
    return st == "unanswered", why


def eligible(authority, payload, tpath, now):
    """(ok, reason): the rule's CORE -- a stream box + a stream payload + the
    idle window -- used by the dark-watch decision AND the delivery re-check
    (so they can never disagree)."""
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
    read), and only while the rule still holds at the moment of delivery: the
    core (stream box + stream payload + idle), the session's OWN transcript (a
    different newest transcript in the cwd refuses) and no open `❓` after the
    arm (`open_question`). `mark_fn()` gives the structured mark (payload +
    ts); `tinfo_fn()` gives `find_active_transcript(...)` -- the NEWEST
    transcript in the cwd, never older than the session's own, so the idle
    re-check errs toward refusing. The dark footer is re-checked by
    `deliver_goal` itself (an armed pane drops). On a pass `why` names the
    rule (journalled by the caller)."""
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
    is_open, why = open_question(tpath, mark.get("ts"))
    if is_open:                     # never type over an open owner question
        return False, why
    return True, "dark stream loop not owner-ended, transcript idle"


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
           pending_fn, record_fn, reset_fn, question_fn=None, confirm_fn=None,
           mark_ts=None):
    """The ONE rule's dark-watch decision for a DARK, mark-"set" claude pane
    whose armed goal is a stream template. Returns `(logline_or_None,
    handled)`. `question_fn() -> (open, why)` is guard 6 (default
    `open_question(tpath, mark_ts)`; dark-watch passes a memoized one);
    `confirm_fn() -> bool` advances the #524 confirmation run (guard 4) --
    None fails CLOSED (never confirmed).

    Order: the idle window and the open question first (a stat + a memoized
    read, so the template -- `resolve_authority` + the template file -- is
    resolved only for an idle pane with no open question, not every 60 s for
    every dark stream pane); then the template's authority (a non-stream box
    returns, touching nothing beyond a journalled skip reason); the core + the
    template; then the confirmation run -- advanced every sweep that gets this
    far, the 1 h gap included, and ONLY on a path that stays this rule's own
    (handled), so the caller's dead-loop path never advances it twice a sweep
    -- then the gap and pending gates. `eligible()` re-checks the idle window
    on purpose: it is the ONE core `delivery_ok` shares, so record and
    delivery can never disagree. `handled` is False -- the caller's fulfilled
    / answer / dead-loop path applies -- for a non-stream box, an unreadable or
    not-yet-idle transcript, an open question, a failed core and an
    unresolvable template: none is this rule's own state, so the #459
    visibility and the #890 answer rider stay. Every other outcome is
    rule-bound (True). A skip is journalled ONCE per (session, reason).
    Nothing is mutated on dry_run (the caller's `confirm_fn` included)."""
    if not dry_run:
        _reap(store, now)
    rec = store.get(sid) if isinstance(store.get(sid), dict) else {}

    def _skip(why, handled):
        if dry_run or rec.get("why") == why:
            return None, handled
        store[sid] = dict(rec, why=why, seen=now)
        return ("dark-watch %s sid=%s -> stream-migrate SKIP: %s"
                % (loc, sid, why)), handled

    age = _turn_liveness.transcript_age_s(tpath, now)
    if age is None:
        return _skip("transcript unreadable", False)
    if age < IDLE_MIN_S:
        return _skip("transcript not idle %ds" % IDLE_MIN_S, False)
    is_open, owhy = (question_fn() if question_fn is not None
                     else open_question(tpath, mark_ts))
    if is_open:
        return _skip(owhy, False)
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
    if confirm_fn is None or not confirm_fn():
        return _skip("dark footer not yet confirmed (#524 run)", True)
    last = rec.get("last")
    if isinstance(last, (int, float)) and 0 <= now - last < MIN_GAP_S:
        return _skip("1/h attempt gap", True)
    if pending_fn(sid):
        return _skip("a request is already pending", True)
    case = ("old (B) template" if is_old_stream_payload(payload)
            else "current template")
    if dry_run:
        return ("dark-watch %s sid=%s -> STREAM-MIGRATE would record "
                "(dry-run, authority=%s, %s)" % (loc, sid, authority, case)), True
    reset_fn()
    record_fn(text, authority)
    store[sid] = {"last": now, "seen": now, "why": "recorded"}
    return ("dark-watch %s sid=%s -> STREAM-MIGRATE: recording re-arm with the "
            "current stream template (authority=%s; dark stream loop not "
            "owner-ended, %s: #524-confirmed dark footer, transcript idle >= "
            "%ds, no open ❓ NEEDS YOU; #1143)"
            % (loc, sid, authority, case, IDLE_MIN_S)), True


def state_store(state, key, dry_run):
    """A per-sid state dict of the stream rule -- a COPY on dry_run, so an
    honest dry-run mutates no state."""
    return dict(state.get(key) or {}) if dry_run else state.setdefault(key, {})


def seams(sid, cwd, now, dry_run, state, episode_states, template_fn,
          record_request, load_requests):
    """`Seams` for the dark-watch rule. `record_request` / `load_requests`
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
    """`confirm_fn` for the rule (guard 4): advance the caller's #524
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


def dark_watch(logs, sid, cwd, tpath, mark, now, loc, dry_run, state,
               seams_fn, confirm_fn):
    """`goal_dark_watch`'s call of the ONE rule for a DARK, mark-"set" pane
    (after the #524 liveness vetoes, before the fulfilled / answer / dead-loop
    lanes). A non-stream payload returns False at once, touching no state (a
    full box is unchanged); only then are `seams_fn()` and the open-question
    memo built. The memo (`state["goal_stream_answered_memo"]`, the #1133 key
    kept so the persisted state is reused, not orphaned) holds `open_question`
    per (arm ts, transcript mtime): a dark idle pane is swept every 60 s but
    its transcript does not change, so the bounded 8 MB read runs once per
    change (never written on dry_run; `seen` refreshed on every hit, so an
    entry is reaped 24 h after its last sighting). Appends its one log line
    (if any) to `logs`; True = handled (the caller `continue`s)."""
    payload = mark.get("payload") if isinstance(mark, dict) else None
    if not is_stream_payload(payload):
        return False
    mark_ts = mark.get("ts")
    memo = state_store(state, "goal_stream_answered_memo", dry_run)
    if not dry_run:
        _reap(memo, now)

    def _question():
        try:
            key = [mark_ts, os.stat(tpath).st_mtime_ns]
        except (OSError, TypeError):
            key = [mark_ts, None]
        hit = memo.get(sid)
        if (isinstance(hit, dict) and hit.get("key") == key
                and isinstance(hit.get("open"), bool)):
            res = hit["open"], str(hit.get("why") or "")
        else:
            res = open_question(tpath, mark_ts)
        if not dry_run:
            memo[sid] = {"key": key, "open": res[0], "why": res[1], "seen": now}
        return res

    line, handled = decide(sid, cwd, tpath, payload, now, loc, dry_run,
                           *seams_fn(), question_fn=_question,
                           confirm_fn=lambda: confirm_fn(mark_ts),
                           mark_ts=mark_ts)
    if line:
        logs.append(line)
    return handled
