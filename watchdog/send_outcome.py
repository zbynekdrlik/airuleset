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
* `presend_reclaim` — `send_verified`'s pre-send gate (#1157 slice 2): an
  idle pane whose readable box PROVABLY holds only our own stale machine text
  (a leftover typed before any record existed) is cleared, nothing is typed in
  that call, and the next sweep delivers. Anything else is held untouched.
* `machine_pointer` / `single_row_verified` — slice 3 (the structural fix):
  a MACHINE nudge is written to a file (`watchdog/nudge_file.py`) and only a
  one-row pointer line is typed, verified by one exact row compare;
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
from watchdog import nudge_file

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

# The per-chunk verify poll: a chunk renders within milliseconds, so a short
# bounded settle (8 x 0.25 s = 2 s per chunk at worst) keeps a 1400-char batch
# well inside the sweep budget; a genuine loss returns at the first bad chunk.
CHUNK_VERIFY_POLLS = 8
CHUNK_VERIFY_S = 0.25

STRANDED_KEY = "stranded_own"
STRANDED_TTL_S = 24 * 3600      # a record older than a day is dropped unread
# #1157 slice 2 — a pane whose box this module judged NOT ours (our text plus a
# human's words). The pre-send prefix proof refuses while the mark stands; it
# is dropped the moment the pane's box is seen bare (`note_bare`).
NOT_OWN_KEY = "box_not_own"
HELD_DRAFT = "box holds a non-machine draft — held"


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


def _spinner_above_box(cap):
    """#1104's running-turn spinner, found above a WRAPPED box too. The shared
    `_pane_activity_spinner_above_box` walks up from the box's LAST row, which
    for a wrapped box (our long nudge) is the box's own text, so a spinner above
    it is missed. Walk up from the box HEAD row instead, past spacers, borders,
    the `◎ /goal` indicator and queued `❯` rows, to the first content row."""
    rows = watchdog._input_box_rows_raw(cap)
    lines = [ln.strip() for ln in (cap or "").splitlines() if ln.strip()]
    heads = [i for i, ln in enumerate(lines) if rows and ln == rows[0]]
    if not heads:
        return False
    from watchdog import pane_text as _pt
    for ln in list(reversed(lines[:heads[-1]]))[:25]:   # bounded, like #1104
        if (watchdog._is_separator_line(ln) or watchdog._is_border_rule(ln)
                or watchdog._GOAL_HEADER_INDICATOR_RX.match(ln)
                or ln.startswith(("❯", "⎿"))):   # queued rows / Tip / todo rows
            continue
        return bool(_pt._ACTIVITY_SPINNER_RX.search(ln))
    return False


def _pane_busy(cap):
    """True when a turn runs (or waits on background agents) under the box: an
    Escape there would interrupt it (#233/#1104), so no clear may start."""
    if watchdog._classify_boundary(cap)[0] != "input" or _spinner_above_box(cap):
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
    box = watchdog._box_norm_from_capture(cap)
    if box == " ".join((own_text or "").split()):
        return True          # exactly our text, any length (`continue`, a card)
    if nudge_file.is_pointer_line(box, require_file=True):
        return True          # slice 3: our one-line pointer (the caller may hold
                             # the long original text, e.g. the batch undo)
    floor = (1 if watchdog._looks_like_own_payload(head)
             else watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR)
    return watchdog._box_is_own_leftover(cap, own_text, floor, provenance=True)


def janitor_undo_if_own_stranded(pid, run, own_text, loc, sleep_fn, logs,
                                 out=None, state=None):
    """#1092 (b) / #1157 — capture the pane ONCE and, when it still shows OUR
    OWN text (`_box_is_ours`), run the #372 janitor clear so the owner never
    finds a machine paragraph in his prompt. Every caller calls this right
    after its OWN type into a box it verified bare (first-person provenance).
    NO keystroke goes into an UNREADABLE box or a BUSY pane (a running /
    waiting turn: the clear's first Escape would interrupt it), and a box that
    is not provably ours (a foreign draft, our text plus a human's append) is
    left untouched — and with `state` the caller's pre-send janitor watch is
    dropped, so the generic own-prefix clear can never eat the human's words on
    a later sweep. Returns True only once the clear converged; `out["box"]`
    (optional dict) gets the status: bare / cleared / busy / not-own /
    not-converged / unreadable. Explicit journal verbs on every branch (#486)."""
    cap = watchdog.capture_pane(pid, run, lines=40)
    itext = watchdog._input_line_text(cap)
    if itext is None:
        status, line = "unreadable", "box unreadable, no keystroke"
    elif itext == "":
        status, line = "bare", "box clean (nothing stranded)"
        note_bare(state, pid)
    elif _pane_busy(cap):
        status, line = "busy", "turn running under the box, no keystroke"
    elif not _box_is_ours(cap, own_text):
        status, line = "not-own", "box not provably ours, left untouched"
        if state is not None:
            state.get("janitor_watch", {}).pop(pid, None)
            mark_not_own(state, pid)
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
        for poll in range(CHUNK_VERIFY_POLLS):
            cap = watchdog.capture_pane(pid, run, lines=40)
            tail = watchdog._input_line_text(cap)
            if tail is None or _st._pane_shows_collapsed_paste(tail):
                return _st._TV_HOLD
            if _rows_end_with(watchdog._input_box_rows_raw(cap), text[:end]) \
                    or _st._PASTED_PLACEHOLDER_RX.match(tail.strip()):
                break
            if poll == CHUNK_VERIFY_POLLS - 1:
                return _st._TV_CORRUPT
            sleep_fn(CHUNK_VERIFY_S)
    return _st._TV_LANDED


# #1157 slice 3 — the kinds whose text is a SLASH COMMAND and stays typed in
# full: the `/goal` arm (the evaluator reads only the transcript, so the
# condition must be typed whole), the `/goal clear` disarm and `/compact`.
POINTER_EXEMPT_KINDS = frozenset({"goal-arm", "goal-disarm", "compact"})


def pane_width(pid, run):
    """The pane's real width from tmux `#{pane_width}`, or None."""
    out = run(["tmux", "display-message", "-p", "-t", pid, "#{pane_width}"])
    try:
        width = int((out or "").strip())
    except ValueError:
        return None
    return width if width > 0 else None


def machine_pointer(pid, run, text, nudge, user_authored, log_fn):
    """#1157 slice 3 — the text `send_verified` actually types. A MACHINE nudge
    (a `nudge=` identity, not the owner's own reply, not a slash command, not a
    `POINTER_EXEMPT_KINDS` kind) is written in full to a 0600 file first
    (`nudge_file.write`) and replaced by ONE pointer line sized to the pane's
    real width. Returns `(typed_text, one_row)`: `one_row` is True only when
    tmux reported the width and the line fits one row, so the caller verifies
    it by an exact single-row compare; otherwise the caller keeps the
    wrap-aware verify. Returns None when the file cannot be written: nothing
    is typed (never the long paragraph this slice removes), logged. A kind the
    kill switch withholds writes no file (its type is suppressed anyway)."""
    if (user_authored or not nudge or nudge in POINTER_EXEMPT_KINDS
            or (text or "").lstrip().startswith("/")
            or watchdog._keystroke_suppressed("send", user_authored, nudge)):
        return text, False
    width = pane_width(pid, run)
    try:
        path = nudge_file.write(nudge, text)
    except OSError as e:
        log_fn("send-verified abort: nudge file not written (%s) -- nothing typed"
               % e)
        _log.warning("send-verified: nudge file write failed pane=%s kind=%s: %s",
                     pid, nudge, e)
        return None
    line = nudge_file.pointer_line(nudge, text, nudge_file.display_path(path),
                                   width)
    one_row = (width is not None
               and nudge_file.cells(line) <= nudge_file.row_budget(width))
    log_fn("nudge-file %s -> %s (%d chars); typing a %d-cell pointer, pane "
           "width %s%s" % (nudge, path, len(text or ""), nudge_file.cells(line),
                           width if width is not None else "unknown",
                           "" if one_row else " -- not one row, wrap-aware verify"))
    return line, one_row


def single_row_verified(pid, run, line, sleep_fn):
    """#1157 slice 3 — the one-row pointer's read-back: the input box must be
    EXACTLY one row equal to `line` (bounded settle poll, the per-chunk
    budget). No wrap, no scroll, no substring heuristic."""
    for poll in range(CHUNK_VERIFY_POLLS):
        rows = watchdog._input_box_rows_raw(watchdog.capture_pane(pid, run,
                                                                  lines=40))
        if len(rows) == 1 and rows[0].lstrip("❯").strip() == line:
            return True
        if poll < CHUNK_VERIFY_POLLS - 1:
            sleep_fn(CHUNK_VERIFY_S)
    return False


def record_stranded(state, pid, text, now):
    """Remember the exact text a `typed-stranded` send left in `pid` (#1157)."""
    if state is not None:
        state.setdefault(STRANDED_KEY, {})[pid] = {"ts": now, "typed": text}


def clear_stranded(state, pid):
    if state is not None:
        state.get(STRANDED_KEY, {}).pop(pid, None)


def stranded_reclaimable(state, pid, captured, now, dry_run=False):
    """#1157 — the janitor's verdict on a pane a `typed-stranded` send recorded.
    True: the box is still just our text — its TAIL row ends where the record
    ends and the whole box is a run of it (`_box_is_ours`, wrap- and scroll-
    safe), or it is a head-anchored PREFIX of it (a partly-undone remnant) at
    least `GOAL_ARM_LEFTOVER_MIN_SUBSTR` non-space chars long.
    False: a readable, idle box that is neither (a human typed there): the
    record AND the janitor watch are dropped and the pane is marked not-own
    (slice 2), so nothing clears the human's words; a BARE box drops them too
    (our text is gone) but is no human draft, so it clears the mark instead.
    None: no record, a busy / unreadable pane (record kept), a record past
    `STRANDED_TTL_S` (dropped), or a SHORT head-prefix of the record (kept,
    undecided): a few chars like `nud` or `/goal all` are as likely the owner
    starting a draft as our remnant, so the record alone never clears them."""
    rec = (state or {}).get(STRANDED_KEY, {}).get(pid)
    ts = rec.get("ts") if isinstance(rec, dict) else None
    text = rec.get("typed") if isinstance(rec, dict) else None
    if not isinstance(ts, (int, float)) or not isinstance(text, str) or not text:
        return None
    if not 0 <= now - ts < STRANDED_TTL_S:
        if not dry_run:
            clear_stranded(state, pid)
        return None
    if watchdog._input_line_text(captured) is None or _pane_busy(captured):
        return None                           # cannot judge now; keep the record
    from watchdog import stash as _stash
    tail = _stash._box_tail_row_norm(captured)
    box_ns = "".join(watchdog._box_norm_from_capture(captured).split())
    whole = (bool(tail) and " ".join(text.split()).endswith(tail)
             and _box_is_ours(captured, text))
    prefix = bool(box_ns) and "".join(text.split()).startswith(box_ns)
    if not whole and prefix \
            and len(box_ns) < watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR:
        return None                           # too short to tell from a draft
    own = whole or prefix
    if not own and not dry_run:
        clear_stranded(state, pid)            # our text alone is gone from the box
        state.get("janitor_watch", {}).pop(pid, None)
        if box_ns:
            mark_not_own(state, pid, now)
        else:
            note_bare(state, pid)
    return own


def mark_not_own(state, pid, now=None):
    """Remember that `pid`'s box held more than our text (#1157 slice 2)."""
    if state is not None:
        state.setdefault(NOT_OWN_KEY, {})[pid] = (time.time() if now is None
                                                  else now)


def note_bare(state, pid):
    """`pid`'s box was seen bare: a not-own mark names content that is gone."""
    if state is not None:
        state.get(NOT_OWN_KEY, {}).pop(pid, None)


def watch_provenance(state, pid, now):
    """The janitor's generic 6 h watch as provenance, VETOED while the pane
    carries a not-own mark: every delivering caller re-stamps the watch right
    before its send, so after a not-own verdict the fresh watch would license
    the janitor's prefix `clear` to eat the human's words one sweep later."""
    return (watchdog._janitor_watch_seen(state, pid, now)
            and (state or {}).get(NOT_OWN_KEY, {}).get(pid) is None)


def _presend_own(pid, cap, state, now):
    """#1157 slice 2 — which proof shows the (readable, idle) box holds only
    OUR OWN stale machine text: "record", "shape", or None (not provably ours).
    In order:
      * a `stranded_own` record decides ALONE when it can
        (`stranded_reclaimable`): our exact text is "record" (the record proves
        the WHOLE box, scrolled renders included), a box that is no longer just
        it is not ours, and neither falls through to the shape check;
      * a standing not-own mark (`mark_not_own`) refuses;
      * the existing own recogniser `_looks_like_own_stuck_content` on the HEAD
        row, narrowed to the one shape a human never types: the batch composer's
        own head `nudge: [<known category>] ` (#923, registered machine-only) on
        a box no longer than a batch (`BATCH_MAX_CHARS`), or (slice 3) a box
        that is exactly one of our pointer lines whose file exists
        (`nudge_file.is_pointer_line`: the whole line must match, so words
        appended after it never pass). `/goal ` and
        `/compact` (human-typeable) never qualify, and neither does CC's
        collapsed-paste placeholder: a human paste renders the identical
        placeholder, and every delivering caller stamps this pane's
        `janitor_watch` right before the send, so the watch proves nothing.
    Residual, stated: with no record and no mark, our batch head followed by
    words a human appended is not told apart (nor a batch head the owner quoted
    at the top of a SCROLLED draft); the gate's draft-rescue snapshot holds
    them, and the clear never reaches past the visible, proven text."""
    rec = stranded_reclaimable(state, pid, cap, now)
    if rec is not None:
        return "record" if rec else None
    if (state or {}).get(NOT_OWN_KEY, {}).get(pid) is not None:
        return None
    head = (watchdog._input_box_head_text(cap) or "").strip()
    if not watchdog._looks_like_own_stuck_content(head):
        return None
    box = watchdog._box_norm_from_capture(cap)
    if nudge_file.is_pointer_line(box, require_file=True):
        return "shape"       # slice 3: the WHOLE box is our pointer line
    from watchdog import nudge_gate as _ng
    ok = (len(box) <= _ng.BATCH_MAX_CHARS
          and any((head + " ").startswith("%s [%s] " % (_ng.BATCH_PREFIX, cat))
                  for cat in _ng.GATED_CATEGORIES))    # a row may end at `]`
    return "shape" if ok else None


def _ns(cap):
    return "".join(watchdog._box_norm_from_capture(cap).split())


def _clear_proven_own(pid, run, proven_ns, sleep_fn):
    """Backspace the box down to bare, never past the text proven ours: every
    pass the visible box must still be a (whitespace-free) prefix of
    `proven_ns`, readable and idle, and a pass removes at most the visible
    box's NON-SPACE char count: the buffer behind the visible rows holds at
    least that many chars, so a pass never reaches past them into hidden rows
    of a scrolled box, and words typed during the clear stop the loop instead
    of being deleted. No Escape (never a double-Escape into a draft, #35).
    Returns `(status, removed)`: bare / changed / unreadable / busy /
    stash-occupied / not-converged, and how many chars were backspaced."""
    removed = 0
    for _ in range(watchdog.JANITOR_CLEAR_MAX_ITER):
        cap = watchdog.capture_pane(pid, run, lines=40)
        tail = watchdog._input_line_text(cap)
        if tail == "":
            return "bare", removed
        if tail is None:
            return "unreadable", removed
        if _pane_busy(cap):
            return "busy", removed
        if watchdog.STASH_MARKER in (cap or ""):
            return "stash-occupied", removed
        visible = _ns(cap)
        if not proven_ns.startswith(visible):
            return "changed", removed
        batch = min(len(visible), watchdog.JANITOR_CLEAR_BATCH_MAX)
        watchdog.keys(pid, *(["BSpace"] * batch), kind="janitor", run=run)
        removed += batch
        sleep_fn(watchdog.JANITOR_CLEAR_SETTLE_S)
    return "not-converged", removed


def presend_reclaim(pid, run, cap, sleep_fn, state, now, log_fn):
    """#1157 slice 2 — `send_verified`'s pre-send gate found the box not bare.
    Clear it ONLY when state is threaded (the not-own mark and the records live
    there; a stateless caller, the owner's own Discord reply among them, never
    clears), the pane is idle (no running / waiting turn), the box is readable,
    the stash slot is free, the agent strip is not selected, and the box
    provably holds only our own stale text (`_presend_own`). A fresh capture
    right before the first key must show the same box. A RECORD-proven box
    gets the janitor's own full clear (`_janitor_clear_box`, exactly what
    `_janitor_recover` does for that record: a scrolled own box shifts its
    window as it shrinks, which the bounded clear would misread as a change);
    a SHAPE-proven box gets `_clear_proven_own`, which never reaches past the
    visible proven text. A shape-proven box that changed, before or during the
    clear, is marked not-own: a human is typing. One clear attempt per pane per
    episode (the #1113 (c) lock the janitor's provenance-free clears share).
    The caller types NOTHING in this call either way (the next sweep delivers
    into the bare box). The clear is recovery (`kind="janitor"`, ungated by the
    #994 switch, #1002), like every other janitor clear of our own text.
    Returns the verdict the gate logs (#486)."""
    if state is None:
        return "no state threaded — held"
    if watchdog._input_line_text(cap) is None:
        return "box unreadable — held"
    if watchdog.STASH_MARKER in (cap or ""):
        return "stash slot occupied — held (the janitor pops it)"
    if _pane_busy(cap) or watchdog._pane_activity_spinner_above_box(cap):
        return "turn running under the box — held"
    if watchdog._strip_selected(cap):          # keys would go to the strip
        return "agent strip selected — held"
    now = time.time() if now is None else now
    mode = _presend_own(pid, cap, state, now)
    if mode is None:
        return HELD_DRAFT
    proven = _ns(cap)
    fresh = watchdog.capture_pane(pid, run, lines=40)
    if _ns(fresh) != proven:
        mark_not_own(state, pid, now)
        return "box changed under the check — held, pane marked not-own"
    from watchdog import janitor as _jan
    if _jan._template_clear_locked(state, pid, now):
        return "pre-send clear already tried this episode — held"
    _jan._mark_template_clear(state, pid, now)
    if mode == "record":
        status = ("bare" if watchdog._janitor_clear_box(pid, run, sleep_fn,
                                                        log_fn)
                  else "not-converged")
        removed = len(watchdog._box_norm_from_capture(cap))
    else:
        status, removed = _clear_proven_own(pid, run, proven, sleep_fn)
    if status == "changed":
        mark_not_own(state, pid, now)
        status = "changed, pane marked not-own"
    if status != "bare":
        return ("pre-send: stopped clearing stale own machine text (%s, %d "
                "chars removed) — held" % (status, removed))
    clear_stranded(state, pid)
    note_bare(state, pid)
    _log.warning("send-verified: pre-send cleared own stale text pane=%s "
                 "chars=%d", pid, removed)
    return ("pre-send: cleared stale own machine text (%d chars); nothing "
            "typed this call, the next sweep delivers" % removed)


def after_verify_failure(pid, run, text, sleep_fn, log_fn, logs, state, now):
    """`send_verified`'s verify-failed branch (our keystrokes DID reach the
    box). Undo our own text; a clean box is `typed-undone`. Otherwise it is
    `typed-stranded`: a box that is not provably ours (a human typed behind our
    text) is left alone and the caller's pre-send janitor watch is DROPPED, so
    no later own-prefix clear can eat the human's words; a busy / unreadable /
    non-converging box may still hold our text, so the watch is armed and the
    exact text recorded for the next sweep's reclaim (`stranded_reclaimable`;
    `_janitor_recover` itself holds while the pane is busy)."""
    jlogs, box = [], {}
    loc = watchdog._pane_location(pid, run) or pid
    janitor_undo_if_own_stranded(pid, run, text, loc, sleep_fn, jlogs, out=box,
                                 state=state)
    if isinstance(logs, list):
        logs.extend(jlogs)
    status = box.get("box")
    if status in ("bare", "cleared"):
        clear_stranded(state, pid)
        log_fn("send-verified abort: typed then undone")
        return SendOutcome(TYPED_UNDONE)
    if status == "not-own":                  # the undo already dropped the watch
        log_fn("send-verified abort: typed, box holds more than our text -- "
               "left untouched, janitor watch dropped")
        return SendOutcome(TYPED_STRANDED)
    now = time.time() if now is None else now
    verb = ("undo did not converge" if status == "not-converged"
            else "undo withheld (%s)" % status)
    if state is None:
        log_fn("send-verified abort: typed, %s -- no state threaded, nothing "
               "recorded for the janitor" % verb)
    else:
        log_fn("send-verified abort: typed, %s -- janitor retries next sweep"
               % verb)
        watchdog._janitor_mark_watch(state, pid, now)
        record_stranded(state, pid, text, now)
    _log.warning("send-verified: own text left in box pane=%s status=%s typed=%s",
                 pid, status, repr(text)[:120])
    return SendOutcome(TYPED_STRANDED)
