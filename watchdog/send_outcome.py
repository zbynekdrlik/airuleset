"""#1157 — what happened to the text the ONE keystroke primitive typed.

`tmux_io.send_verified` used to return a bare bool, so every caller had to
guess the rest. The gk-infra incident (25.9.2026) showed the cost: a
verify-failed type left a ~720-char batch nudge in the owner's input box for
4 h, the batch caller logged `deferred (not typed: box busy/raced)`, and every
later delivery aborted `box not bare pre-send`. This module holds:

* `SendOutcome` — the structured result (#486 truthful decision logs). It is
  truthy ONLY for a transcript-confirmed submit and compares equal to the old
  bool, so a caller that only needs success is unchanged.
* `janitor_undo_if_own_stranded` — the ONE own-provenance clear of our own
  stranded text (moved here from `watchdog/goal.py`, #1092 (b)). Both
  `send_verified` (after a verify-failed type) and the batch caller (after a
  swallowed / delivered-unconfirmed submit) call it; `goal.py` keeps the old
  name as an import alias.
* `after_verify_failure` — `send_verified`'s verify-failed branch: undo, then
  `typed-undone` or `typed-stranded`.
* `type_rest_verified` — the send path's per-chunk verify (#1157 (c)): a long
  nudge in a short pane SCROLLS the box, so after the head checkpoint every
  later chunk is proven on screen as it lands (nothing hides unverified).
* the STRANDED record (`state["stranded_own"]`): the exact text a
  `typed-stranded` send left, so the next sweep's janitor (`_janitor_recover`)
  reclaims it from ANY render, scrolled included. It is deliberately NOT the
  #488/#852 `stash_parks` record: a bare-box send never touched the stash slot,
  and a park record would license popping a slot the owner may occupy.

Every shared primitive is read as `watchdog.<name>` at call time, so the
existing monkeypatch seams hold."""
import logging
import time

import watchdog

_log = logging.getLogger(__name__)

NOT_TYPED = "not-typed"                    # nothing typed (busy / raced / held / OFF)
TYPED_UNDONE = "typed-undone"              # typed, verify failed, own text backed out
TYPED_STRANDED = "typed-stranded"          # typed, own text could not be removed
SWALLOWED = "swallowed"                    # Enter swallowed twice, own text backed out
UNCONFIRMED = "unconfirmed"                # Enter sent, box unreadable / unrecognized
DELIVERED_UNCONFIRMED = "delivered-unconfirmed"   # Enter cleared the box, turn not proven
SUBMITTED = "submitted"                    # transcript-confirmed user turn

# The outcomes in which our keystrokes reached the pane but nothing was
# delivered: a caller stamps its per-kind floor for these (a typing attempt,
# #1092) exactly like a swallow, so a pane that keeps failing is never re-typed
# every sweep.
TYPED_NOT_DELIVERED = frozenset({TYPED_UNDONE, TYPED_STRANDED, SWALLOWED,
                                 UNCONFIRMED})

STRANDED_KEY = "stranded_own"
STRANDED_TTL_S = 24 * 3600      # a record older than a day is dropped unread


class SendOutcome:
    """One `send_verified` result. `bool()` is True only for SUBMITTED and the
    object compares equal to that bool; `.kind` names what happened."""

    __slots__ = ("kind",)

    def __init__(self, kind):
        self.kind = kind

    def __bool__(self):
        return self.kind == SUBMITTED

    def __eq__(self, other):
        if isinstance(other, SendOutcome):
            return self.kind == other.kind
        if isinstance(other, bool):
            return bool(self) is other
        return NotImplemented

    def __hash__(self):                      # consistent with the bool equality
        return hash(bool(self))

    def __repr__(self):
        return "SendOutcome(%r)" % self.kind


OUT_NOT_TYPED = SendOutcome(NOT_TYPED)
OUT_SUBMITTED = SendOutcome(SUBMITTED)
OUT_SWALLOWED = SendOutcome(SWALLOWED)
OUT_UNCONFIRMED = SendOutcome(UNCONFIRMED)
OUT_DELIVERED_UNCONFIRMED = SendOutcome(DELIVERED_UNCONFIRMED)


def _pane_busy(cap):
    """True when a turn runs (or waits on background agents) under the box: an
    Escape there would interrupt it (#233/#1104), so no clear may start."""
    if watchdog._classify_boundary(cap)[0] != "input":
        return True
    from watchdog import ops_wait_recheck as _owr
    return _owr._pane_busy_waiting(cap)


def _box_is_ours(cap, own_text):
    """True when the WHOLE visible box provably holds OUR OWN text: CC's
    collapsed-paste placeholder, or (via the shared `_box_is_own_leftover`
    proof, first-person provenance) a run of `own_text` at least
    `GOAL_ARM_LEFTOVER_MIN_SUBSTR` chars long — or any length when the head row
    carries an own-payload prefix. An own prefix ALONE is not proof: a human's
    words appended behind our nudge make the box no longer a run of our text."""
    head = watchdog._input_box_head_text(cap)
    if head and watchdog._PASTED_PLACEHOLDER_RX.match(head.strip()):
        return True
    floor = (1 if watchdog._looks_like_own_payload(head)
             else watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR)
    return watchdog._box_is_own_leftover(cap, own_text, floor, provenance=True)


def janitor_undo_if_own_stranded(pid, run, own_text, loc, sleep_fn, logs,
                                 out=None):
    """#1092 (b) / #1157 — capture the pane ONCE and, when it still shows OUR
    OWN text (`_box_is_ours`), run the #372 janitor clear so the owner never
    finds a machine paragraph in his prompt. Every caller calls this right
    after its OWN type into a box it verified bare (first-person provenance).
    NO keystroke goes into an UNREADABLE box or a BUSY pane (a running /
    waiting turn: the clear's first Escape would interrupt it), and a box that
    is not provably ours (a foreign draft, our text plus a human's append) is
    left untouched. Returns True only once the clear converged; `out["box"]`
    (optional dict) gets the status: bare / cleared / busy / not-own /
    not-converged / unreadable. Explicit journal verbs on every branch (#486)."""
    cap = watchdog.capture_pane(pid, run, lines=40)
    itext = watchdog._input_line_text(cap)
    if itext is None:
        status, line = "unreadable", "box unreadable, no keystroke"
    elif itext == "":
        status, line = "bare", "box clean (nothing stranded)"
    elif _pane_busy(cap):
        status, line = "busy", "turn running under the box, no keystroke"
    elif not _box_is_ours(cap, own_text):
        status, line = "not-own", "box not provably ours, left untouched"
    elif watchdog._janitor_clear_box(pid, run, sleep_fn, logs.append):
        status, line = "cleared", "cleared stranded machine text"
    else:
        status, line = "not-converged", "clear did not converge (retry next sweep)"
    logs.append("janitor-undo %s -> %s" % (loc, line))
    if isinstance(out, dict):
        out["box"] = status
    return status == "cleared"


def _rows_end_with(rows, typed):
    """True when the box rows, read bottom-up, are consecutive exact slices of
    `typed` ending at its end. Only whitespace may sit BETWEEN two rows (the
    break a word-wrap consumes; a hard-broken token has none); inside a row every
    char must match, so a dropped byte — a space included — is caught."""
    rows = [r.lstrip("❯").strip() for r in rows]
    pos = len(typed.rstrip())
    if not rows or not rows[-1]:
        return False
    for row in reversed(rows):
        if not typed[:pos].endswith(row):
            return False
        pos -= len(row)
        while pos > 0 and typed[pos - 1].isspace():
            pos -= 1
    return True


def type_rest_verified(pid, run, text, sleep_fn, kind, user_authored, nudge,
                       logs):
    """#1157 — type `text` past the head checkpoint chunk by chunk and verify
    each chunk as it lands: the visible rows must END with everything typed so
    far (`_rows_end_with`). A chunk is smaller than any readable box, so every
    byte is on screen right after its own chunk, including the rows a scrolled
    box later hides. Returns
    a `stash._TV_*` verdict: LANDED, CORRUPT (a byte was lost: the caller undoes
    the whole text) or HOLD (unreadable / collapsed: no further keystroke). A
    suppressed chunk (an OFF-flip mid-delivery) returns LANDED, so the caller's
    final verify reads not-landed and recovers (#1002)."""
    from watchdog import stash as _st
    for i in range(_st.GOAL_TYPE_CHECKPOINT_CHARS, len(text),
                   _st.GOAL_TYPE_CHUNK_SIZE):
        end = i + _st.GOAL_TYPE_CHUNK_SIZE
        if not _st._type_literal(pid, run, text[i:end], sleep_fn, kind=kind,
                                 user_authored=user_authored, logs=logs,
                                 nudge=nudge):
            return _st._TV_LANDED
        sleep_fn(_st.GOAL_TYPE_CHUNK_DELAY_S)
        for poll in range(_st.TYPE_VERIFY_SETTLE_POLLS):
            cap = watchdog.capture_pane(pid, run, lines=40)
            tail = watchdog._input_line_text(cap)
            if tail is None or _st._pane_shows_collapsed_paste(tail):
                return _st._TV_HOLD
            if _rows_end_with(watchdog._input_box_rows_raw(cap), text[:end]) \
                    or _st._PASTED_PLACEHOLDER_RX.match(tail.strip()):
                break
            if poll == _st.TYPE_VERIFY_SETTLE_POLLS - 1:
                return _st._TV_CORRUPT
            sleep_fn(_st.TYPE_VERIFY_SETTLE_S)
    return _st._TV_LANDED


def record_stranded(state, pid, text, now):
    """Remember the exact text a `typed-stranded` send left in `pid` (#1157)."""
    if state is not None:
        state.setdefault(STRANDED_KEY, {})[pid] = {"ts": now, "typed": text}


def clear_stranded(state, pid):
    if state is not None:
        state.get(STRANDED_KEY, {}).pop(pid, None)


def stranded_reclaimable(state, pid, captured, now, dry_run=False):
    """#1157 — True when `pid` holds the exact text a `typed-stranded` send
    recorded: the box TAIL row ends where the record ends and the whole visible
    box is a run of it (`_box_is_ours`, wrap- and scroll-safe), on a pane that
    is not busy. A record that is past `STRANDED_TTL_S`, or whose text a
    readable idle box no longer shows, is dropped (the record bounds itself)."""
    rec = (state or {}).get(STRANDED_KEY, {}).get(pid)
    ts = rec.get("ts") if isinstance(rec, dict) else None
    text = rec.get("typed") if isinstance(rec, dict) else None
    if not isinstance(ts, (int, float)) or not isinstance(text, str) or not text:
        return False
    if not 0 <= now - ts < STRANDED_TTL_S:
        if not dry_run:
            clear_stranded(state, pid)
        return False
    if watchdog._input_line_text(captured) is None or _pane_busy(captured):
        return False                          # cannot judge now; keep the record
    from watchdog import stash as _stash
    tail = _stash._box_tail_row_norm(captured)
    own = (bool(tail) and " ".join(text.split()).endswith(tail)
           and _box_is_ours(captured, text))
    if not own and not dry_run:
        clear_stranded(state, pid)            # our text is gone from the box
    return own


def after_verify_failure(pid, run, text, sleep_fn, log_fn, logs, state, now):
    """`send_verified`'s verify-failed branch (our keystrokes DID reach the
    box). Undo our own text; a clean box is `typed-undone`. Otherwise it is
    `typed-stranded`: a box that is not provably ours (a human typed behind our
    text) is left alone and NOTHING is armed, so no later clear can eat the
    human's words; a busy / unreadable / non-converging box may still hold our
    text, so the janitor watch is armed and the exact text recorded for the
    next sweep's reclaim (`stranded_reclaimable`)."""
    jlogs, box = [], {}
    janitor_undo_if_own_stranded(pid, run, text, pid, sleep_fn, jlogs, out=box)
    if isinstance(logs, list):
        logs.extend(jlogs)
    status = box.get("box")
    if status in ("bare", "cleared"):
        clear_stranded(state, pid)
        log_fn("send-verified abort: typed then undone")
        return SendOutcome(TYPED_UNDONE)
    if status == "not-own":
        log_fn("send-verified abort: typed, box holds more than our text -- "
               "left untouched, nothing armed")
        return SendOutcome(TYPED_STRANDED)
    now = time.time() if now is None else now
    if state is None:
        log_fn("send-verified abort: typed, undo did not converge (%s) -- no "
               "state threaded, nothing recorded for the janitor" % status)
    else:
        log_fn("send-verified abort: typed, undo did not converge (%s) -- "
               "janitor retries next sweep" % status)
        watchdog._janitor_mark_watch(state, pid, now)
        record_stranded(state, pid, text, now)
    _log.warning("send-verified: own text left in box pane=%s status=%s typed=%s",
                 pid, status, repr(text)[:120])
    return SendOutcome(TYPED_STRANDED)
