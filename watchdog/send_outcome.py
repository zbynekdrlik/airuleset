"""#1157 — what happened to the text the ONE keystroke primitive typed.

`tmux_io.send_verified` used to return a bare bool, so every caller had to
guess the rest. The gk-infra incident (25.9.2026) showed the cost: a
verify-failed type left a ~720-char batch nudge in the owner's input box for
4 h, the batch caller logged `deferred (not typed: box busy/raced)`, and every
later delivery aborted `box not bare pre-send`. This module holds:

* `SendOutcome` — the structured result (#486 truthful decision logs). It is
  truthy ONLY for a transcript-confirmed submit, and compares equal to the old
  bool, so a caller that only needs success is unchanged.
* `janitor_undo_if_own_stranded` — the ONE own-provenance clear of our own
  stranded text (moved here from `watchdog/goal.py`, #1092 (b)). Both
  `send_verified` (after a verify-failed type) and the batch caller (after a
  swallowed / delivered-unconfirmed submit) call it; `goal.py` keeps the old
  name as an import alias.
* `after_verify_failure` — `send_verified`'s verify-failed branch: undo, then
  either `typed-undone` or `typed-stranded` (janitor watch armed + the exact
  text parked for the janitor's next-sweep reclaim, #852 E).

Like the other watchdog leaves, every shared primitive is read as
`watchdog.<name>` at call time, so the existing monkeypatch seams hold."""
import time

import watchdog

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


class SendOutcome:
    """One `send_verified` result. `bool()` is True only for SUBMITTED and the
    object compares equal to that bool (and to its own `kind` string)."""

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
        if isinstance(other, str):
            return self.kind == other
        return NotImplemented

    def __hash__(self):
        return hash(self.kind)

    def __repr__(self):
        return "SendOutcome(%r)" % self.kind

    @property
    def typed(self):
        """True when keystrokes reached the pane but nothing was delivered."""
        return self.kind in TYPED_NOT_DELIVERED


OUT_NOT_TYPED = SendOutcome(NOT_TYPED)
OUT_SUBMITTED = SendOutcome(SUBMITTED)
OUT_SWALLOWED = SendOutcome(SWALLOWED)
OUT_UNCONFIRMED = SendOutcome(UNCONFIRMED)
OUT_DELIVERED_UNCONFIRMED = SendOutcome(DELIVERED_UNCONFIRMED)


def outcome_kind(res):
    """The outcome word for any `send_verified`-shaped result: a SendOutcome's
    `kind`, else `submitted` / `not-delivered` for a legacy bool (a stub)."""
    kind = getattr(res, "kind", None)
    if kind:
        return kind
    return SUBMITTED if res else "not-delivered"


def _box_is_ours(cap, own_text):
    """True when the WHOLE visible box provably holds OUR OWN text: CC's
    collapsed-paste placeholder, or a contiguous run of `own_text` (whitespace-
    insensitive, so a wrap-joined render still matches) that is either >=
    `GOAL_ARM_LEFTOVER_MIN_SUBSTR` chars or starts with an own-payload prefix.
    An own prefix ALONE is not proof: a human's words appended behind our nudge
    make the box no longer a run of our text, and clearing it would eat them."""
    head = watchdog._input_box_head_text(cap)
    if head and watchdog._PASTED_PLACEHOLDER_RX.match(head.strip()):
        return True
    box_ns = "".join(watchdog._box_norm_from_capture(cap).split())
    own_ns = "".join((own_text or "").split())
    if not box_ns or not own_ns or box_ns not in own_ns:
        return False
    return (len(box_ns) >= watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR
            or watchdog._looks_like_own_payload(head))


def janitor_undo_if_own_stranded(pid, run, own_text, loc, sleep_fn, logs,
                                 out=None):
    """#1092 (b) / #1157 — capture the pane ONCE and, when it still shows OUR
    OWN text (`_box_is_ours`), run the #372 janitor clear so the owner never
    finds a machine paragraph in his prompt. Every caller calls this right
    after its OWN type into a box it verified bare, so the text is ours by
    first-person provenance; a box that is not provably ours (a foreign draft,
    our text plus a human's append) is left COMPLETELY untouched, and an
    UNREADABLE box (a dialog / turn frame) gets no keystroke at all. Returns
    True only once the clear converged; `out["box"]` (optional dict) gets the
    status: bare / cleared / not-own / not-converged / unreadable. Explicit
    journal verbs on every branch (#1092 (d), #486)."""
    cap = watchdog.capture_pane(pid, run, lines=40)
    itext = watchdog._input_line_text(cap)
    if itext is None:
        status, line = "unreadable", "box unreadable, no keystroke"
    elif itext == "":
        status, line = "bare", "box clean (nothing stranded)"
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


def after_verify_failure(pid, run, text, sleep_fn, log_fn, logs, state, now):
    """`send_verified`'s verify-failed branch (our keystrokes DID reach the
    box). Undo our own text; on a clean box return `typed-undone`. Otherwise
    the text may still sit there: arm the janitor watch and park the exact text
    (`_park_unreclaimed`, #852 E — WARN + durable record) so the next sweep's
    janitor reclaims it from ANY render, scrolled included, and return
    `typed-stranded`."""
    jlogs, box = [], {}
    janitor_undo_if_own_stranded(pid, run, text, pid, sleep_fn, jlogs, out=box)
    if isinstance(logs, list):
        logs.extend(jlogs)
    if box.get("box") in ("bare", "cleared"):
        log_fn("send-verified abort: typed then undone")
        return SendOutcome(TYPED_UNDONE)
    log_fn("send-verified abort: typed, undo did not converge (%s) -- janitor "
           "retries next sweep" % box.get("box"))
    from watchdog import stash as _stash
    now = time.time() if now is None else now
    watchdog._janitor_mark_watch(state, pid, now)
    _stash._park_unreclaimed(pid, text, log_fn, state, "send-verified", now=now)
    return SendOutcome(TYPED_STRANDED)
