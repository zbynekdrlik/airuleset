"""Stash-around delivery + draft-rescue + own-stuck-content keystroke machinery.

Extracted verbatim from ``watchdog/__init__.py`` as item G step 5 of the
definitive module split (issue #433). This is the SAFETY-CRITICAL keystroke
cluster (#372 lineage): it types the watchdog's OWN text into a live Claude
Code pane WITHOUT destroying a human's in-progress draft. Native prompt STASH
(Ctrl+S, issue #35) parks a FOREIGN draft into CC's single, silently-overwriting
slot; we then type + submit our text, verify it landed against a FRESH capture,
and let CC auto-restore the parked draft when the delivered turn completes. Any
surprise aborts to the caller's pre-#35 fallback rather than guess — never data
loss, never a swallowed submit. Alongside the delivery path live the
draft-rescue persistence helpers (a durable on-disk copy of a draft before any
risky delivery: ``draft_rescue_dir`` / ``_draft_rescue_persist`` and friends)
and the #372 own-stuck-content ownership tests (``_looks_like_own_payload`` /
``_looks_like_own_stuck_content``) the janitor uses to know a stuck box can ONLY
be OUR own delivery, never a foreign draft.

Direction: back-reference module. Every cross-reference to a name that was a
top-level ``watchdog`` name goes through the package namespace CALL-TIME
(``import watchdog`` at module top; ``watchdog.<name>(...)`` in bodies) — the
``__init__.py`` C3 convention that keeps every ``monkeypatch.setattr(watchdog,
"<name>", ...)`` / ``patch.object(wd, "<name>")`` seam resolving identically to
before the move. That covers primitives still resident elsewhere
(``watchdog.capture_pane`` / ``_input_line_text`` / ``_input_box_rows_raw`` /
``_normalize_queued_hint`` / ``_is_draft_head`` / ``_default_run`` /
``_has_free_prompt`` / ``_strip_selected`` / ``_box_is_wrapped``) AND the
intra-module co-moved cross-calls (``deliver_with_stash`` -> ``_type_literal`` /
``_await_stash_settled`` / ``_typed_landed`` / ``_typed_exclusively`` /
``_pane_shows_collapsed_paste`` / ``_undo_and_release_slot`` /
``_undo_appended_text`` / ``_draft_rescue_persist``; ``_draft_rescue_persist`` ->
``draft_rescue_dir`` / ``_draft_rescue_text`` / ``_draft_rescue_ensure_dir`` /
``_draft_rescue_prune``; ``_draft_rescue_prune`` -> ``draft_rescue_dir``;
``_undo_and_release_slot`` -> ``_undo_typed_text``;
``_looks_like_own_stuck_content`` -> ``_looks_like_own_payload``).
``draft_rescue_dir`` and ``_draft_rescue_persist`` are grep-proven test seams
(conftest patches ``wd.draft_rescue_dir``; a ``test_draft_rescue`` spy patches
``wd._draft_rescue_persist`` then calls ``deliver_with_stash``), so those MUST go
through the package seam — and for uniform keystroke-safety every co-moved
cross-call does.

The co-moved module CONSTANTS (``STASH_*`` / ``DRAFT_RESCUE_*`` /
``GOAL_TYPE_CHUNK_*`` / ``_PASTED_PLACEHOLDER_RX`` / ``_PASTE_EXPAND_HINT_RX`` /
``_JANITOR_OWN_PREFIXES`` / ``JANITOR_CLEAR_*`` / ``JANITOR_WATCH_MAX_AGE_S`` —
the last four read by ``janitor.py`` via ``watchdog.<CONST>``) are referenced
BARE in bodies: the step-5 C5 grep found NO test patches any of them, so a
module-local read is correct and byte-verbatim. Every one of the 39 moved names
(16 functions + 23 constants) is re-exported into the ``watchdog`` namespace by
the positional facade import in ``__init__.py`` (replacing the earliest removed
line), so all existing ``watchdog.<name>`` seams keep resolving unchanged.
"""

import logging
import os
import re
import time
from pathlib import Path

import watchdog

# #852 -- a real WARNING channel for a leave-path that could not remove our own
# typed text (`_park_unreclaimed`). The module's aborts are surfaced through the
# `logs` list the caller prints; a genuine UNRECLAIMED leak is ALSO emitted at
# WARNING here (the same shape `compact.py` uses) so it is loud in the journal
# regardless of whether the caller collects `logs`.
_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Stash-around delivery (issue #35) — CC's native prompt STASH (Ctrl+S) lets
# the watchdog deliver its own text into a pane that is idle but holds a
# FOREIGN draft, without losing that draft: park it, type + submit ours, and
# CC auto-restores the parked draft the instant the delivered turn completes
# (empirically verified against CC 2.1.220 — NOT a second Ctrl+S press, which
# the docs wrongly imply). The stash is SINGLE-SLOT with a SILENT overwrite,
# so every step is verified against a fresh capture and any surprise aborts
# to the caller's pre-#35 fallback (pending / ticket-fallback / skip) —
# never a guess, never data loss.
# --------------------------------------------------------------------------- #

STASH_MARKER = "› stashed"          # "› stashed" — CC's stash-slot indicator

# CC COLLAPSES a long literal `send-keys -l` into a single placeholder row
# (`[Pasted text #3]`) instead of rendering the text — live-observed
# 2026-07-26 on job 20's first real re-arm (a 3152-char /goal). Every
# verified delivery here matches the typed text against the boundary line,
# which can NEVER match that placeholder; the check only ever worked because
# previous callers all sent short text.
#
# #372 — CC also renders a SECOND, MULTI-LINE collapse shape for the same
# kind of long single-line send: `[Pasted text #N +K lines]` (live incident,
# david2@subdev, 2026-08-11 — even AFTER #354's own settle-poll fix). A
# single-line-only regex refuses to recognize this as landed, so a
# GENUINELY SUCCESSFUL delivery gets declared a verify FAILURE, which then
# triggers a destructive undo (`_undo_typed_text`) against a box its own
# docstring explicitly assumes it will never have to reason about — the
# undo backspaces `len(payload)` (~3800) characters against what is really
# a ~26-char placeholder, sometimes failing to confirm bare within the
# settle-poll's budget and leaving the box AND the single stash slot stuck.
# Rather than enumerate this exact second shape as its own alternative (CC
# has now shown 2 distinct collapse renderings sharing the same `[Pasted
# text #N` prefix — assume there will be a 3rd), the regex is PREFIX-
# anchored and suffix-TOLERANT: `[Pasted text #N` followed by anything,
# closing on `]`. This is deliberately more permissive than enumerating
# every observed suffix, and is safe: nothing else in a Claude Code pane's
# input box legitimately renders this literal prefix.
_PASTED_PLACEHOLDER_RX = re.compile(r"^\[Pasted text #\d+.*\]$")


def _typed_landed(text, itext):
    """True if the input-box content `itext` is evidence that `text` was
    typed IN FULL — either the literal tail (a short type renders verbatim;
    a WRAPPED one puts its tail on the boundary line, hence endswith) or
    CC's collapsed `[Pasted text #N]` placeholder for a long one. A
    genuinely TRUNCATED partial type matches neither and is still refused —
    submitting one is the #36 disaster this verification exists to prevent."""
    if not itext:
        return False
    if text.endswith(itext):
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


# #322 — CC 2.1.226 introduced a SECOND, DIFFERENT paste-collapse shape: a
# single long literal `send-keys -l` burst renders as `paste again to
# expand` rather than `[Pasted text #N]` — an INCOMPLETE/unexpanded paste
# that is never parsed as a slash command. Pressing Enter on it submits
# whatever content IS committed as an ordinary chat message instead of
# arming the goal — the exact live incident (dev1's own job 20, and
# montalu2@subdev, both CC 2.1.226): the delivery "succeeds" with no error,
# the model receives the `/goal ...` text as a normal prompt, and the goal
# never arms. A controlled live experiment DISPROVED the earlier "busy
# pane" hypothesis directly — a 23-char payload armed fine on a BUSY pane,
# while a 3960-char one failed on an IDLE one; the only variable that
# discriminated pass/fail was LENGTH, and typing the SAME long payload in
# small chunks (instead of one burst) armed correctly every time.
GOAL_TYPE_CHUNK_THRESHOLD = 200      # below this, unchanged single-burst
                                     # send-keys (never observed to trigger
                                     # the collapse at this size)
GOAL_TYPE_CHUNK_SIZE = 120
GOAL_TYPE_CHUNK_DELAY_S = 0.12
_PASTE_EXPAND_HINT_RX = re.compile(r"paste again to expand", re.I)


def _pane_shows_collapsed_paste(itext):
    """True when the input box shows CC's 'paste again to expand' hint — an
    incomplete/unexpanded paste. `_type_literal`'s chunking exists
    specifically to avoid ever reaching this state; this check is a fast,
    explicit abort for the case something still does (a coalesced terminal
    write, a lower-than-expected collapse threshold), rather than relying
    on the generic type-verify timeout to eventually give up."""
    return bool(itext) and bool(_PASTE_EXPAND_HINT_RX.search(itext))


def _type_literal(pid, run, text, sleep_fn=None):
    """Send `text` into the pane's input box literally — in ONE burst below
    `GOAL_TYPE_CHUNK_THRESHOLD` chars (unchanged from before this ticket),
    or in small CHUNKS at/above it (#322) so CC never treats the whole
    payload as one terminal-paste event.

    #322 REOPENED (adversarial-review CRITICAL-1, both live-verified against
    a real tmux 3.7b pane): `tmux send-keys -l <chunk>` parses `<chunk>` as a
    getopt-style ARGUMENT — a chunk whose first character happens to be `-`
    (e.g. a 120-char slice landing mid-word on "...`-self` (holds..." in a
    real shipped template) is read as an unknown FLAG and the whole call
    fails (`command send-keys: unknown flag -X`, rc 1). `_default_run`
    silently swallows a non-zero-rc command as `""` (no exception, no log),
    so the chunk is DROPPED with zero indication — every later chunk still
    lands, so `_typed_landed`'s tail-based `endswith()` check is satisfied
    by the (now internally corrupted) remainder, and a GOAL WITH A
    120-CHARACTER HOLE IN THE MIDDLE gets armed. This is a materially WORSE
    outcome than the paste-collapse bug this function exists to fix (which
    armed nothing, rather than arming something silently wrong) — and the
    same `-l` call is on `deliver_with_stash`'s only type path, so an
    arbitrary Discord-reply `prompt` (job 7) can be mangled and SUBMITTED
    the identical way. `--` (end-of-options) makes every following argument
    literal regardless of a leading `-`; live-verified: `send-keys -l --
    '-DASH-OK'` lands the literal text, `send-keys -l '-DASH-OK'` (no `--`)
    fails exactly as above. Applied to BOTH the single-burst and the
    chunked path — a single-burst payload can equally start with `-` (job
    7's arbitrary `prompt` argument is not `/goal`-prefixed)."""
    sleep_fn = sleep_fn or time.sleep
    if len(text) < GOAL_TYPE_CHUNK_THRESHOLD:
        run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
        return
    for i in range(0, len(text), GOAL_TYPE_CHUNK_SIZE):
        run(["tmux", "send-keys", "-t", pid, "-l", "--",
            text[i:i + GOAL_TYPE_CHUNK_SIZE]])
        if i + GOAL_TYPE_CHUNK_SIZE < len(text):
            sleep_fn(GOAL_TYPE_CHUNK_DELAY_S)


GOAL_TYPE_VERIFY_RETRIES = 2         # #670: first-byte-race undo+retype budget
# #670-review R1 -- bounded render-SETTLE poll BEFORE concluding a type failed,
# the SAME 8x1s magnitude as tmux_io's SEND_TYPE_SETTLE_* the old send_verified
# path (`_await_typed_landed`) carried: a ~700-char chunked type renders with
# lag (#354/#176 F4), so a single immediate capture can read a genuinely-landed
# type mid-render -- and reacting to that with a DESTRUCTIVE undo+retype is
# worse than the bug this fixes. Duplicated here (not imported) to keep stash's
# `import watchdog`-only module boundary, the same idiom the STASH_*_SETTLE
# constants above already use.
TYPE_VERIFY_SETTLE_POLLS = 8
TYPE_VERIFY_SETTLE_S = 1

# #746 -- a payload at/above this length can WRAP past the input box's visible
# height and CC SCROLLS it, so the head row scrolls off-screen and head-is-prefix
# becomes structurally unsatisfiable. `_type_literal_verified` runs the two-phase
# HEAD-CHECKPOINT (below) only for such a payload. This is a scroll-length PROXY,
# not an "is-a-/goal" test: it sits above EVERY current `send_verified` nudge
# payload (all short by construction -- the #714 ops-wait cap is 700, the other
# card/reply/cross_stream keystrokes are short fixed strings), and below the
# ~3-4k /goal template that is the only shape that actually scrolls in practice,
# so every nudge stays on the byte-identical pre-#746 single-phase path. If a
# future nudge/card ever crossed 1000 it would harmlessly get the checkpoint too
# -- two-phase is SAFE for any payload (it never junk-submits; a scrolled non-goal
# nudge verifies identically), so a proxy-length switch degrades correctness in
# no direction.
GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD = 1000
# #746 -- the checkpoint types this many chars as the FIRST burst (a single
# sub-`GOAL_TYPE_CHUNK_THRESHOLD` send-keys, so the first-byte race applies to
# exactly this burst) into the still-UNSCROLLED box, then head-is-prefix proves
# the leading `/` landed before the box ever scrolls. The proof assumes 120 chars
# do NOT themselves scroll the box -- true at any realistic pane width; on a
# pathologically narrow pane (<~10 cols) even the checkpoint could scroll, which
# only DEGRADES to a safe denial (head-not-prefix -> CORRUPT -> undo+retry -> give
# up, the goal simply not armed), NEVER a junk submit.
GOAL_TYPE_CHECKPOINT_CHARS = 120

# #670-review R2 -- the three shapes `_type_verify_class` distinguishes so the
# undo fires ONLY where it is safe: only a CORRUPT box (readable, holding OUR
# OWN text with a swallowed head) may be backspaced; a HOLD box (unreadable, or
# a collapsed-paste buffer) must NEVER get keystrokes.
_TV_LANDED = "landed"
_TV_HOLD = "hold"
_TV_CORRUPT = "corrupt"


def _type_verify_class(pid, run, text, cap=None, allow_scrolled=False):
    """#670 -- classify the box after a type into LANDED / HOLD / CORRUPT.

    LANDED: the HEAD row (`_input_box_head_text`) is a whitespace-normalised,
    NON-EMPTY PREFIX of `text` AND the TAIL satisfies the `_typed_landed` suffix
    contract -- OR the box is CC's collapsed `[Pasted text #N]` placeholder
    (landed exactly as `_typed_landed` treats it). The tail check ALONE is
    head-blind (`ane-check...` IS a suffix of `lane-check...`, the #670 root);
    the head PREFIX is the missing half. (Head+tail is not a full byte-exact
    proof of the interior, but the first-byte swallow this ticket is about lands
    on the head, and the mid-chunk drop vector was closed by #322's `--`.)

    #746 -- `allow_scrolled` (default False) is the SCROLLED-LANDED escape hatch:
    a long own /goal WRAPS past the box's visible height and CC scrolls it, so
    the head row is MID-payload and head-is-prefix is STRUCTURALLY unsatisfiable
    even for a perfectly-typed box. When `allow_scrolled` AND the tail already
    landed AND the head is not a prefix, LANDED is granted iff the WHOLE VISIBLE
    box is a >=`GOAL_ARM_LEFTOVER_MIN_SUBSTR`-char contiguous substring of `text`
    (the #737 own-leftover signature). This is gated -- NOT the default -- on
    purpose: a HEAD-SWALLOWED long payload has the SAME visible tail (the dropped
    `/` is off-screen at the head), so substring alone cannot tell scrolled-
    landed from head-swallowed, and would submit a ~3700-char junk prompt (#720).
    Only `_type_literal_verified`, AFTER a first-chunk head-checkpoint has proven
    the leading char landed, passes `allow_scrolled=True`; every other caller
    (deliver_with_stash's bare branch, the stranded path, the nudge send path)
    keeps the default and stays byte-identical to pre-#746 -- a scrolled box is
    CORRUPT for them, exactly as before. A short (non-scrolling) payload never
    reaches this branch (its head stays visible -> head-is-prefix), so the flag
    is a no-op there.

    HOLD: the box is UNREADABLE (`_input_line_text` None -- a turn/dialog started
    mid-type) OR shows the `paste again to expand` collapse hint. NO keystrokes
    may follow (the #233 unreadable-pane discipline; the #322/#372 collapsed-
    buffer state `_undo_typed_text` explicitly excludes) -- the caller aborts.

    CORRUPT: a READABLE box holding a variant of OUR OWN text whose head lost its
    first char, or a truncated type. The box was verified BARE before the type,
    so every char in it is ours -> the caller may safely undo + retype.

    `cap` (optional): a capture the caller ALREADY took right after the type --
    passed by `deliver_with_stash` so head and tail come from the SAME snapshot
    and NO extra `capture-pane` is issued (its fixed-sequence test fakes count
    captures). Default None -> a fresh capture."""
    if cap is None:
        cap = watchdog.capture_pane(pid, run, lines=40)
    tail = watchdog._input_line_text(cap)
    if tail is None:
        return _TV_HOLD                          # unreadable pane -> withhold (#233)
    if _PASTED_PLACEHOLDER_RX.match(tail.strip()):
        return _TV_LANDED                        # collapsed placeholder = landed
    if _pane_shows_collapsed_paste(tail):
        return _TV_HOLD                          # 'paste again to expand' -> withhold (#322/#372)
    if not _typed_landed(text, tail):
        return _TV_CORRUPT                        # tail not a suffix -> our own truncated type
    head = watchdog._input_box_head_text(cap)
    if head and " ".join(text.split()).startswith(" ".join(head.split())):
        return _TV_LANDED
    if allow_scrolled and _box_is_own_leftover(
            cap, text, GOAL_ARM_LEFTOVER_MIN_SUBSTR):
        return _TV_LANDED                        # #746 scrolled own /goal (head off-screen)
    return _TV_CORRUPT                            # head not a prefix -> swallowed first char


def _type_verify_landed(pid, run, text, cap=None, allow_scrolled=False):
    """#670 -- thin bool wrapper (`_type_verify_class == LANDED`). Used by
    `deliver_with_stash`'s bare branch for DETECTION only: it aborts to the next
    sweep on not-landed via its OWN `_undo_and_release_slot`, exactly as it did
    with the head-blind `_typed_landed` (a HOLD/CORRUPT both read not-landed, its
    pre-#670 behaviour on a non-suffix box -- unchanged). The undo+retry loop
    lives in `_type_literal_verified` (send_verified's path) ONLY.

    #747 -- `allow_scrolled` is forwarded to `_type_verify_class`. It defaults
    False (every pre-#747 caller stays byte-identical -- a scrolled box is
    CORRUPT for them). `deliver_with_stash`'s bare branch passes True ONLY for a
    scroll-length payload it has already run the two-phase head-checkpoint on
    (`_type_two_phase_head_checkpoint` proved the leading char landed), so the
    scrolled own-substring acceptance can never mask a #720 head-swallow."""
    return _type_verify_class(
        pid, run, text, cap=cap, allow_scrolled=allow_scrolled) == _TV_LANDED


def _settle_type_verify(pid, run, text, sleep_fn, allow_scrolled=False):
    """#670-review R1 -- `_type_verify_class` behind the bounded render-SETTLE
    poll (`TYPE_VERIFY_SETTLE_*`, the 8x1s magnitude the old `_await_typed_landed`
    carried): return the FIRST non-CORRUPT verdict, so a genuinely-landed type
    read mid-render is not mistaken for a swallow and destructively undone. A
    stable CORRUPT (every poll) settles to CORRUPT. `allow_scrolled` is forwarded
    unchanged (#746)."""
    cls = _TV_HOLD
    for i in range(TYPE_VERIFY_SETTLE_POLLS):
        cls = _type_verify_class(pid, run, text, allow_scrolled=allow_scrolled)
        if cls != _TV_CORRUPT:                    # LANDED or a stable HOLD -> stop settling
            break
        if i < TYPE_VERIFY_SETTLE_POLLS - 1:
            sleep_fn(TYPE_VERIFY_SETTLE_S)
    return cls


def _type_two_phase_head_checkpoint(pid, run, text, sleep_fn):
    """#746/#747 -- the SHARED first-phase of a scroll-length two-phase type: type
    the short FIRST chunk (`GOAL_TYPE_CHECKPOINT_CHARS`) into the still-UNSCROLLED
    box and settle-verify head-is-prefix, then -- ONLY if that checkpoint LANDED
    -- type the REST. The checkpoint is a cheap first-byte-swallow catch: it
    proves the leading `/` landed BEFORE the box scrolls the head off-screen, so
    the caller's FINAL verify can accept the scrolled own-substring
    (`allow_scrolled=True`) without ever masking a #720 head-swallow.

    Returns the checkpoint VERDICT, leaving the RECOVERY to the caller (the two
    callers differ):
      * `_TV_LANDED`  -- checkpoint passed AND the rest was typed; the caller runs
                         its own FINAL `allow_scrolled=True` verify.
      * `_TV_CORRUPT` -- the head was swallowed; the box holds `<= head_chunk`
                         chars of OUR OWN text on a bare-verified box, so the
                         caller may undo exactly `GOAL_TYPE_CHECKPOINT_CHARS`
                         (over-backspace safe) -- `_type_literal_verified` undoes
                         + retypes, `deliver_with_stash` undoes + releases the
                         slot + aborts to next sweep.
      * `_TV_HOLD`    -- the box went unreadable / collapsed after the chunk; NO
                         further keystroke may follow (#233/#322/#372) -- both
                         callers abort with the head chunk left as CC rendered it.
    Only ever called for a scroll-length payload (`len >= GOAL_TYPE_SCROLL_
    CHECKPOINT_THRESHOLD`); a short payload never reaches here."""
    head_chunk = text[:GOAL_TYPE_CHECKPOINT_CHARS]
    _type_literal(pid, run, head_chunk, sleep_fn)
    # #763 -- the verify REFERENCE is the chunk sans trailing whitespace: an
    # arbitrary [:120] slice can end mid-whitespace (ALL three real templates
    # do -- '...MY '), and `_input_line_text` STRIPS the box read, so verifying
    # against the raw chunk fails `endswith` on a perfectly-typed box and the
    # checkpoint settles CORRUPT forever (fleet-wide zero `SEND typed`). The
    # TYPED text stays the exact `head_chunk` (the rest continues at the same
    # offset); only what the settle-verify compares against is normalised.
    # Inherent residual: a type dropping exactly the chunk's TRAILING space
    # now reads LANDED (the stripped pane read renders "typed the space" and
    # "dropped it" identically) — undetectable in principle at this layer,
    # no known last-byte drop vector (#670's race is first-byte), and the
    # pre-#763 code offered no protection either (it CORRUPTed even a
    # perfect type).
    hc = _settle_type_verify(pid, run, head_chunk.rstrip(), sleep_fn)
    if hc != _TV_LANDED:
        return hc
    _type_literal(pid, run, text[GOAL_TYPE_CHECKPOINT_CHARS:], sleep_fn)
    return _TV_LANDED


def _type_literal_verified(pid, run, text, sleep_fn=None):
    """#670 -- type `text` into a BARE box and VERIFY the box holds it head+tail,
    retrying (undo + re-type) ONLY on a genuine first-byte swallow. This is
    `send_verified`'s verified typed path (all nudge kinds -- lane-check, job-1
    stuck-check/continue, ops-wait, release-gap, Discord reply, cards,
    cross_stream) AND the bare-box goal-arm send (`_send_goal_verified`, #720);
    `deliver_with_stash`'s bare branch calls the lighter `_type_verify_landed`
    (detection, abort-to-next-sweep) instead.

    Each verify runs a bounded render-SETTLE poll (`_settle_type_verify`) BEFORE
    concluding failure (#670-review R1). On the settled verdict:
      * LANDED  -> True.
      * HOLD (unreadable / collapsed paste) -> False with ZERO keystrokes -- not
        a box `_undo_typed_text` may safely backspace (#233/#322/#372,
        #670-review R2); the caller aborts + retries next sweep, and the box is
        left as CC rendered it (the #372 janitor backstops any residual, never a
        blind backspace).
      * CORRUPT (readable, OUR OWN swallowed-head text) -> back it off
        (`_undo_typed_text`; over-backspace safe -- the caller verified the box
        BARE, so every char is ours) and retry. If undo cannot confirm bare,
        abort. On a CORRUPT give-up the last undo leaves the box BARE (no
        stranded `ane-check...`).

    #746 -- for a SCROLL-length payload (`len >= GOAL_TYPE_SCROLL_CHECKPOINT_
    THRESHOLD`, i.e. a long /goal, never a nudge) the type is TWO-PHASE: type a
    short FIRST chunk (`GOAL_TYPE_CHECKPOINT_CHARS`) into the still-UNSCROLLED box
    and CHECKPOINT head-is-prefix (a cheap first-byte-swallow catch with a small
    120-char undo), THEN type the rest, THEN the FINAL verify runs with
    `allow_scrolled=True`. The checkpoint is what makes the scrolled-substring
    acceptance safe: it proves the leading `/` landed BEFORE the box scrolls the
    head off-screen, so a head-swallowed long /goal (whose visible tail is an
    identical own-substring) can never slip through to a junk submit (#720). A
    short payload skips the checkpoint entirely (`allow_scrolled` stays False,
    its head stays visible) -> byte-identical to pre-#746.
    Returns True iff head+tail-verified within GOAL_TYPE_VERIFY_RETRIES retries."""
    sleep_fn = sleep_fn or time.sleep
    two_phase = len(text) >= GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD
    head_chunk = text[:GOAL_TYPE_CHECKPOINT_CHARS]
    for _attempt in range(GOAL_TYPE_VERIFY_RETRIES + 1):
        if two_phase:
            # #746/#747 -- the two-phase type + head-checkpoint is SHARED with
            # deliver_with_stash (`_type_two_phase_head_checkpoint`); only the
            # RECOVERY differs, and here it is undo-the-chunk + RETRY.
            hc = _type_two_phase_head_checkpoint(pid, run, text, sleep_fn)
            if hc == _TV_HOLD:
                return False                     # unreadable / collapsed -> NO keystrokes
            if hc == _TV_CORRUPT:                # head swallowed -> undo the chunk + retry
                if not _undo_typed_text(pid, run, head_chunk, sleep_fn):
                    return False
                continue
        else:
            _type_literal(pid, run, text, sleep_fn)
        cls = _settle_type_verify(pid, run, text, sleep_fn,
                                  allow_scrolled=two_phase)
        if cls == _TV_LANDED:
            return True
        if cls == _TV_HOLD:
            return False                         # unreadable / collapsed -> NO keystrokes (R2)
        # CORRUPT: our own swallowed-head text on a bare-verified box -> back it
        # off and retry. If undo cannot confirm bare, abort (no blind retype).
        if not _undo_typed_text(pid, run, text, sleep_fn):
            return False
    return False


STASH_VERIFY_SETTLE_POLLS = 3      # bounded: a `C-s` toggle's render can lag
STASH_VERIFY_SETTLE_S = 0.3        # behind the keystroke actually landing (#176 F4)

# The four honest outcomes of our own `C-s` toggle (#189). The mechanism used
# to collapse them into one boolean ("did the box go bare WITH the marker?")
# and treat everything else as a failed stash — which is how an EMPTY box
# became an error condition, and why zero `continue`s were delivered in 24h
# box-wide. Each outcome now names a genuinely different pane state and gets
# a genuinely different recovery.
STASH_PARKED = "parked"            # bare + marker lit: a real draft is in the slot
STASH_NOOP = "noop"                # bare, no marker: there was nothing to park
STASH_UNRESOLVED = "unresolved"    # still shows content, no marker: a rendered
                                   # ghost suggestion, or a genuinely lost
                                   # keystroke — DELIBERATELY not distinguished
STASH_NO_BOUNDARY = "no-boundary"  # the input line itself is gone (spinner /
                                   # dialog / unreadable): touch nothing more


def _await_stash_settled(pid, run, sleep_fn):
    """Poll (bounded) for the pane to settle after our OWN `C-s`, and report
    WHICH of the four states above it settled into.

    Still a render-SETTLE poll, never a blind timeout — it returns the instant
    the box agrees, because an IMMEDIATE re-capture can show the UNCHANGED
    screen even though the toggle already landed server-side (#176 F4).

    What changed in #189 is the question being asked. The old poll demanded
    `input == "" AND marker lit` and called anything else a failure. But a
    bare box with NO marker is not a failure at all: it means there was
    nothing to park, and stashing nothing is a no-op. Since Claude Code's grey
    prompt suggestion renders as ordinary text once `capture-pane -p` strips
    its SGR attributes, "the box looks non-empty" was never evidence that a
    draft existed — so the poll reports what it can actually see and lets the
    caller act on each state, rather than folding three deliverable states
    into one abort. Returns (last_capture, outcome, boundary_text) where
    boundary_text is the input line as it stands at the end (None when there
    is no input line at all)."""
    cap = None
    for i in range(STASH_VERIFY_SETTLE_POLLS):
        cap = watchdog.capture_pane(pid, run, lines=30)
        if watchdog._input_line_text(cap) == "":
            return cap, (STASH_PARKED if STASH_MARKER in (cap or "")
                         else STASH_NOOP), ""
        if i < STASH_VERIFY_SETTLE_POLLS - 1:
            sleep_fn(STASH_VERIFY_SETTLE_S)
    itext = watchdog._input_line_text(cap)
    if itext is None:
        return cap, STASH_NO_BOUNDARY, None
    return cap, STASH_UNRESOLVED, itext


def _typed_exclusively(text, itext):
    """True only if the input box holds NOTHING BUT `text` — the STRICT form of
    `_typed_landed`, required when we typed into a box that visibly held
    something (#189).

    `_typed_landed` accepts a TAIL of `text` on the boundary line, because a
    wrapped input renders its tail there. That allowance is safe on a box we
    verified bare first, but not on a box that already showed content: if the
    content was a real draft our text APPENDS to it, and a wrap landing inside
    our own text would leave a boundary that is a legitimate suffix of `text`
    — passing the loose check and submitting the user's draft with our text
    glued on. Requiring an exact match (or Claude Code's collapsed
    `[Pasted text #N]` placeholder for a long payload) has no such hole: a
    ghost suggestion is REPLACED by the keystroke, so the box holds exactly
    our text, while a real draft never can."""
    if not itext:
        return False
    if itext == text:
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


# Bounds how many backspaces an undo may ever spray at a pane. It sat at 400,
# BELOW a real payload — the gk-request nudge is 458 chars — so the recovery
# #193 added for a box verified bare could not have run for the very delivery
# that stranded its text. Sized off the largest payload this helper actually
# carries: job 20 delivers `"/goal " + <condition>`, and Claude Code caps that
# condition at 4000 (#169), so 4096 covers the whole line with headroom rather
# than refusing it by six characters.
STASH_UNDO_MAX_BACKSPACES = 4096

# Backs `_undo_typed_text`/`_undo_appended_text`'s own post-backspace verify
# (#354). A single immediate capture right after backspacing can read the
# box as still non-bare purely from render lag — the SAME mechanism #176 F4
# already found and fixed for `deliver_with_stash`'s own post-toggle verify
# (`_await_stash_settled`), never before extended to this post-backspace
# verify. Reported live on gatekeeper (#354): two real deliveries logged
# "stash-abort: typed-NOT-undone, draft left parked" with the /goal text
# visibly stuck, unsent, in the pane — the user's own draft left
# permanently parked in the single stash slot because the (genuinely
# successful, just not-yet-rendered) undo was declared failed.
#
# `_await_stash_settled` is the SHAPE precedent (bounded-poll-never-a-blind-
# timeout, #176 F4) but NOT the magnitude one — its 3x0.3s budget backs a
# single `C-s` toggle, a far smaller render event than backspacing up to
# `STASH_UNDO_MAX_BACKSPACES` individual keystrokes. The MAGNITUDE here is
# deliberately borrowed from `GOAL_TYPE_SETTLE_POLLS`/`_S` (`_await_typed`)
# instead — this job's own already-established budget for the render lag a
# multi-KB PASTE causes, the closer analogue by payload size. Accepted cost
# (adversarial-review finding, #354): ~12x `_await_stash_settled`'s own
# budget, worst case ~7s added to ONE pane's delivery inside job 20's own
# per-sweep wall-clock budget — reached only on a genuine settle-window-
# exhausting failure, never on the already-working happy path.
STASH_UNDO_SETTLE_POLLS = 8
STASH_UNDO_SETTLE_S = 1


def _undo_appended_text(pid, run, pre_text, text, sleep_fn=None):
    """Remove exactly the characters WE typed from a box that already held
    `pre_text`, and VERIFY the box came back to `pre_text` (#189).

    Reached only when nothing was parked and the box shows the exact append
    signature `pre_text + text` — i.e. our own keystroke landed on top of a
    real draft because the stash toggle was lost. A restoring `C-s` would be
    the WRONG recovery here: with an empty slot it would PARK the polluted
    text, jamming the single slot into the one decline this design keeps and
    leaving the user's draft invisible. Backspacing our own characters is the
    only recovery that touches nothing of the user's. Returns whether the
    draft verifiably came back.

    #354 — a bounded settle poll (never a blind timeout — returns the
    instant the box agrees) backs the verify, see `STASH_UNDO_SETTLE_POLLS`'s
    own comment for why a single immediate capture is not enough."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    sleep_fn = sleep_fn or time.sleep
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    for i in range(STASH_UNDO_SETTLE_POLLS):
        if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30)) == pre_text:
            return True
        if i < STASH_UNDO_SETTLE_POLLS - 1:
            sleep_fn(STASH_UNDO_SETTLE_S)
    return False


def _undo_typed_text(pid, run, text, sleep_fn=None):
    """Remove exactly the characters WE typed from a box that was VERIFIED
    BARE immediately before we typed, and confirm it came back to bare (#193).

    Reached only from the PARKED / NOOP outcomes, where the settle poll had
    already read `_input_line_text(cap) == ""`. Every character in the box is
    therefore ours, so backspacing exactly `len(text)` provably cannot reach
    anything of the user's — whatever fraction of the type actually landed, a
    surplus backspace lands on an empty box. That proof is a property of the
    BOX (observed bare by this function's own caller a moment earlier), never
    of who called or of what they passed.

    Note it can only run at all when the verify FAILED: a payload Claude Code
    collapsed into `[Pasted text #N]` verifies through that placeholder and is
    submitted, so this never has to reason about a collapsed buffer.

    #354 — a bounded settle poll (never a blind timeout — returns the
    instant the box agrees) backs the verify, see `STASH_UNDO_SETTLE_POLLS`'s
    own comment for why a single immediate capture is not enough."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    sleep_fn = sleep_fn or time.sleep
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    for i in range(STASH_UNDO_SETTLE_POLLS):
        if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30)) == "":
            return True
        if i < STASH_UNDO_SETTLE_POLLS - 1:
            sleep_fn(STASH_UNDO_SETTLE_S)
    return False


def draft_rescue_dir():
    """`~/.claude/draft-rescue/`, resolved at CALL time (same reasoning as
    `watchdog.compact.compact_requests_path()`: never a frozen module-level
    constant, so a
    relocated `$HOME`/override is honoured on every call, not just the one
    that happened to run at import time).

    `AIRULESET_DRAFT_RESCUE_DIR`, when set, overrides the default. This is
    NOT a security boundary (there is nothing to bypass here — see the
    vault-store env-override lesson in the playbook, which is about a
    guard an attacker could defeat; this is a plain write-only, self-pruning
    diagnostic directory nothing else reads or acts on). It exists so
    `airuleset.py`'s own push-gate test-suite subprocess (`cmd_push`) can
    point the WHOLE `unittest discover` run at a throwaway directory in one
    place, instead of adding per-file test isolation to every one of the
    ~19 test files whose fixtures transitively reach `deliver_with_stash`/
    `_send_goal_verified` via `run_once` (#271) — the live systemd watchdog
    executes this repo's own working tree every 60s, so a test process that
    wrote into the REAL directory would be indistinguishable from production
    activity. A single test wanting a precise, deterministic rescue-file
    assertion still patches this function directly
    (`unittest.mock.patch.object(wd, "draft_rescue_dir", return_value=<tmp>)`
    — the same established shape `watchdog.compact.compact_requests_path()`
    already uses)."""
    override = os.environ.get("AIRULESET_DRAFT_RESCUE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "draft-rescue"


# 14 days: deliberately generous (#271). This file exists ONLY so a human can
# recover text nothing else could save — the cost of keeping one around too
# long is a few KB, the cost of pruning too eagerly is the exact
# unrecoverable loss this mechanism exists to prevent. There is no "restore
# confirmed, safe to delete now" signal available to this process (the
# restore is Claude Code's own async, on-screen-only effect, fired once the
# DELIVERED turn completes — minutes away, and never observed here), so age
# is the only thing that ever removes a rescue file.
DRAFT_RESCUE_TTL_S = 14 * 24 * 3600

# `<safe-pid>-<epoch-ms, >=10 digits>[-<retry-n>].txt` — the EXACT shape
# `_draft_rescue_persist` writes. `_draft_rescue_prune` matches against this
# (adversarial-review MAJOR finding, #271) rather than unlinking every name
# in the directory: a misconfigured `AIRULESET_DRAFT_RESCUE_DIR` pointed at
# an already-populated directory must never let a routine prune delete
# unrelated content it happens to share a mtime-age with.
_DRAFT_RESCUE_NAME_RX = re.compile(r"^.+-\d{10,}(?:-\d+)?\.txt$")


def _draft_rescue_text(captured):
    """Best-effort plain-text reconstruction of the pane's CURRENT input-box
    content — "" when the box is bare or unlocatable (nothing to rescue).

    Never the exact original bytes: a WRAPPED draft loses its true line
    breaks the instant Claude Code re-flows it (#189) — this is the
    RENDERED rows (`_input_box_rows_raw`, already head-first and chrome-
    stripped) joined back together, with the leading `❯` glyph + its
    separator stripped off the head row only. Enough for a human to read
    and retype, which is the whole point of a rescue file.

    `_input_box_rows_raw`'s own glyph-based fallback (a borderless capture)
    deliberately returns exactly ONE row and never more — so a borderless
    capture can never pull agent-strip/chrome text into a rescue file; only
    the STRUCTURAL (bordered) strategy can return multiple rows, and it
    returns strictly the box's own interior, already guarded (#243) against
    a transcript-quoted box being misread as the real one."""
    rows = watchdog._input_box_rows_raw(captured)
    if not rows:
        return ""
    head = watchdog._normalize_queued_hint(rows[0])
    if not watchdog._is_draft_head(head):
        return ""            # bare box ("❯" alone) — nothing to rescue
    first = head[1:].lstrip(" \xa0")
    body = [first] + list(rows[1:])
    return "\n".join(ln for ln in body if ln)


def _draft_rescue_prune(now, dir_path=None, ttl_s=None):
    """Remove rescue files older than the TTL. Best-effort (never raises) —
    called inline from EVERY write (`_draft_rescue_persist`), never a
    separate job: the repo FREEZE forbids a new numbered watchdog job, and
    there is no "confirmed delivered" event to hang a dedicated sweep off
    of anyway (see `DRAFT_RESCUE_TTL_S`'s own docstring) — an inline prune
    on every write is sufficient to keep the directory bounded.

    Only names matching `_DRAFT_RESCUE_NAME_RX` are ever candidates for
    deletion — never a bare "everything in this directory" sweep."""
    dir_path = dir_path or watchdog.draft_rescue_dir()
    ttl_s = DRAFT_RESCUE_TTL_S if ttl_s is None else ttl_s
    try:
        names = os.listdir(dir_path)
    except OSError:
        return
    for name in names:
        if not _DRAFT_RESCUE_NAME_RX.match(name):
            continue
        p = Path(dir_path) / name
        try:
            # A matching-named SYMLINK is never one of our own writes (we
            # only ever create real files, via O_EXCL|O_NOFOLLOW) — remove
            # it unconditionally, on age or not. `Path.unlink()` removes the
            # link itself, never follows it, so this is safe either way.
            if os.path.islink(p) or now - p.stat().st_mtime > ttl_s:
                p.unlink()
        except OSError:
            continue


def _draft_rescue_ensure_dir(dir_path):
    """Create `dir_path` as an owner-only (0700) directory if missing, and
    refuse — creating nothing — if it is, or becomes, a symlink.

    Mirrors this repo's own vault-store discipline (checked BEFORE *and*
    AFTER `os.makedirs`, since that call happily succeeds against a symlink
    to an existing directory and a later `chmod` would then tighten the
    TARGET, not a real directory of our own) — these boxes host foreign
    uids by design, and the rescue directory holds arbitrary user-typed
    text that can include a pasted credential (adversarial-review CRITICAL
    finding, #271). Returns True only when `dir_path` is a genuine,
    owner-only directory ready to receive a write."""
    try:
        if os.path.islink(dir_path):
            return False
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
        if os.path.islink(dir_path):
            return False
        os.chmod(dir_path, 0o700)
        return True
    except OSError:
        return False


# Bounds the filename-collision retry loop below — two rescue writes for the
# SAME pane landing in the same millisecond needs two full tmux round-trips
# inside 1ms, which is not realistic in production, but `O_EXCL` makes the
# collision loud (FileExistsError) instead of a silent O_TRUNC overwrite, so
# bound the retry rather than loop forever on a persistently occupied name.
_DRAFT_RESCUE_MAX_RETRIES = 5


def _draft_rescue_persist(pid, captured, now=None, dir_path=None, logs=None):
    """Persist the pane's CURRENT input-box content to disk BEFORE any
    keystroke of OURS lands in it (#271).

    `deliver_with_stash` parks a real draft via `C-s` and Claude Code
    auto-restores it once the delivered turn completes — but that restore is
    entirely on-screen and asynchronous; nothing in this process ever
    observes it landing. The reported incident (2026-08-06): a long typed
    message sat in the box, a goal-autoarm delivery ran, and the draft was
    gone with NO trace at all — CC's input box renders in-place via cursor
    addressing, so unsent text never even enters tmux scrollback (confirmed
    against the real incident capture, forensics comment on this issue).

    So every primitive that is about to touch a pane's box
    (`deliver_with_stash`, `_send_goal_verified`) calls this FIRST,
    unconditionally, with the SAME capture it is about to act on — before
    its own first `send-keys`. Stash+restore stays the PRIMARY recovery
    path; this file is the safety net and is NEVER deleted here on a claimed
    success (there is no verifiable "the restore actually landed" signal to
    delete on) — only `_draft_rescue_prune`'s generous TTL, run inline on
    every write, ever removes one. `send_continue` (job 1/4/7's short-nudge
    primitive) needs no equivalent: it only ever types into a box its own
    caller already verified bare, and on the rare swallowed-submit case a
    stuck draft is APPENDED to and later submitted or auto-recovered — never
    silently discarded the way an unrestored stash can be.

    The write is owner-only (0600, via `os.O_EXCL | os.O_NOFOLLOW` — never
    followed through a planted symlink, never silently overwritten by a
    filename collision) into an owner-only (0700) directory
    (`_draft_rescue_ensure_dir`) — the content can be arbitrary user-typed
    text, including a pasted credential (adversarial-review CRITICAL
    finding, #271), and these boxes host foreign uids by design.

    Returns the path written, or None (box was bare, the directory could not
    be secured, or the write failed after exhausting the collision-retry
    budget — best-effort; never blocks the delivery it is protecting, but
    now LOGS why whenever it fails with a real draft in hand, so a failure
    here is never as silent as the incident this whole mechanism exists to
    prevent)."""
    text = watchdog._draft_rescue_text(captured)
    if not text:
        return None
    now = time.time() if now is None else now
    dir_path = dir_path or watchdog.draft_rescue_dir()
    if not watchdog._draft_rescue_ensure_dir(dir_path):
        if isinstance(logs, list):
            logs.append("draft-rescue: FAILED to secure %s (pane %s, %d "
                        "chars would have been lost with no trace)"
                        % (dir_path, pid, len(text)))
        return None
    watchdog._draft_rescue_prune(now, dir_path=dir_path)
    safe_pid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(pid)).strip("_") or "pane"
    # #479 -- content dedup: a LIVE draft parked across sweeps (a stash slot
    # held by the user's OWN text, retype-verify-failed every ~60s) must NOT
    # spawn a fresh rescue file every sweep -- the 2026-08-14 storm wrote the
    # SAME 702-char draft 7x over ~3h on pane %1, and the 14-day TTL held all
    # of them. If an existing rescue file of THIS pane already holds
    # byte-identical content, reuse it: refresh its mtime so the TTL prune
    # keeps the one surviving copy while the draft is still parked, and skip
    # the duplicate write. Scoped to OUR OWN filename shape for THIS pane
    # (`<safe_pid>-<digits>[-<n>].txt`) -- a tighter, pane-specific form of
    # `_DRAFT_RESCUE_NAME_RX` -- so it can never dedup against unrelated
    # content a misconfigured rescue dir might hold, and never across panes
    # (each is an independent conversation). A genuinely EDITED draft is
    # different content -> a fresh rescue, so the safety net is never weakened.
    pane_rx = re.compile(r"^" + re.escape(safe_pid) + r"-\d+(?:-\d+)?\.txt$")
    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        names = []
    for name in names:
        if not pane_rx.match(name):
            continue
        cand = Path(dir_path) / name
        # Open with O_NOFOLLOW (matching the write path's O_EXCL|O_NOFOLLOW
        # discipline, #271) and operate on that ONE fd for BOTH the content
        # compare and the mtime refresh: O_NOFOLLOW refuses a planted symlink
        # outright, and a single fd removes the check-then-use race a separate
        # islink()+read_text()+utime(path) sequence would carry (adversarial-
        # review MINOR, #479). The dir is already an owner-only 0700 of ours,
        # so this is defense-in-depth, not the sole guard.
        try:
            fd = os.open(str(cand), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            continue                    # symlink / vanished / unreadable
        try:
            with os.fdopen(fd, "rb") as f:
                if f.read().decode("utf-8") != text:
                    continue            # different content -> a fresh rescue
                refreshed = "ok"
                try:
                    os.utime(f.fileno(), (now, now))
                except OSError as exc:
                    refreshed = "failed:%r" % (exc,)   # surfaced, never silent
        except (OSError, ValueError):
            continue                    # read error / non-utf8 -> not a match
        if isinstance(logs, list):
            logs.append("draft-rescue: identical content already parked "
                        "(pane %s, %d chars, mtime-refresh=%s) -> %s"
                        % (pid, len(text), refreshed, cand))
        return str(cand)
    base = "%s-%d" % (safe_pid, int(now * 1000))
    for attempt in range(_DRAFT_RESCUE_MAX_RETRIES):
        suffix = "" if attempt == 0 else "-%d" % (attempt + 1)
        path = Path(dir_path) / (base + suffix + ".txt")
        try:
            fd = os.open(str(path),
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600)
        except FileExistsError:
            continue                       # a genuine name collision — retry
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to open %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to write %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        if isinstance(logs, list):
            logs.append("draft-rescue: saved %d chars (pane %s) -> %s"
                        % (len(text), pid, path))
        return str(path)
    if isinstance(logs, list):
        logs.append("draft-rescue: FAILED for pane %s -> %d consecutive "
                    "filename collisions, %d chars would have been lost "
                    "with no trace" % (pid, _DRAFT_RESCUE_MAX_RETRIES,
                                       len(text)))
    return None


def _park_unreclaimed(pid, run, text, log_fn, state, prefix, now=None):
    """#852 E -- the ONE leave-path a `deliver_with_stash` recovery may take
    when it could not remove our own typed text: it NEVER stays silent. It (a)
    emits `<prefix>: left-in-box UNRECLAIMED pane=<p> typed=<repr(text)[:120]>`
    at WARNING (both the module logger and the caller's `logs`), and (b) writes
    a durable janitor park record CARRYING the exact string, so the janitor can
    reclaim exactly the OWN part later (#852 C), preserving any human prefix.
    No path that leaves typed text may skip this (the #852 lock invariant)."""
    now = time.time() if now is None else now
    reason = ("%s: left-in-box UNRECLAIMED pane=%s typed=%s"
              % (prefix, pid, repr(text)[:120]))
    _log.warning(reason)
    log_fn(reason)
    watchdog._janitor_park_record(state, pid, now, text=text)


def _undo_and_release_slot(pid, run, text, parked, log_fn, prefix, sleep_fn=None,
                           state=None):
    """Backspace exactly `text` and, only once bare is CONFIRMED, pop a
    PARKED draft back with one corrective `C-s` -- the shared recovery
    THREE call sites need (`deliver_with_stash`'s original #193
    PARKED/NOOP verify-failure branch and its new #306
    swallowed-submit-not-recovered branch, plus `_send_goal_verified`'s own
    #306 swallowed-submit path, always with `parked=False`).

    The precondition every caller has proven before reaching here -- the box
    was VERIFIED BARE immediately before typing (#852 A removed the only
    UNRESOLVED caller, so every caller now enters from a bare-box settle) --
    means every character `_undo_typed_text` backspaces is provably our own.

    #852 B/E -- when `_undo_typed_text` cannot confirm the box came back to
    bare, this function NEVER leaves our text silently. A stray human char that
    raced to the FRONT after the settle is PRESERVED (our text is backspaced off
    the END; `_typed_landed` proves it is gone) and the recovery succeeds; a box
    it genuinely cannot clear is left with a WARNING + a durable park record
    (`_park_unreclaimed`, `state`), so the janitor reclaims exactly the OWN part
    later. This replaces the old silent `typed-NOT-undone` / `append-unprovable`
    leak (the #852 incident).

    `log_fn(reason)` receives ONE reason string per call, built from `prefix`
    (e.g. `"stash-abort"`, or a fuller `"stash-abort: swallowed-submit-not-
    recovered"`). `state` (default None) is threaded so `_park_unreclaimed` can
    record a leak durably; a None `state` is a no-op there, exactly like
    `_janitor_park_record`.

    `sleep_fn` (#354) backs `_undo_typed_text`'s own bounded settle poll --
    threaded through from the caller's existing `sleep_fn` param (never a
    new one invented here), so a genuine render-lagged undo verify is
    retried before this function ever calls it a failure and abandons the
    parked draft. Production defaults to real `time.sleep`; tests inject a
    no-op."""
    undone = watchdog._undo_typed_text(pid, run, text, sleep_fn=sleep_fn)
    if not undone:
        # #852 B -- the box did NOT come back to bare after len(text) backspaces.
        # A stray human char may have raced to the FRONT after the settle (the
        # incident's `s`): re-read, and if our OWN text is PROVABLY gone (the
        # `_typed_landed` suffix contract no longer holds on the box tail), the
        # residual is the human's -- preserved, success. We do NOT pop then: a
        # `C-s` would park the stray over the slot. Only when our text is STILL
        # present (a truncated type, a genuinely wedged box) do we leave it, and
        # then NEVER silently -- `_park_unreclaimed` WARNS + writes a durable
        # park record carrying the exact string (#852 E), replacing the old
        # silent `typed-NOT-undone` leak.
        itext = watchdog._input_line_text(
            watchdog.capture_pane(pid, run, lines=30))
        if not watchdog._typed_landed(text, itext):
            log_fn("%s: typed-undone (stray prefix preserved)" % prefix)
            return
        _park_unreclaimed(pid, run, text, log_fn, state, prefix)
        return
    if not parked:
        log_fn("%s: typed-undone" % prefix)
        return
    # ONLY now, with the box confirmed bare again, may the toggle pop the
    # parked draft back. Firing it while our own text was still in the box
    # would PARK that text instead — the slot is single with a SILENT
    # overwrite — destroying the very draft this protocol exists to
    # protect (#193).
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    # Read the pop back rather than asserting it: a log line that claims a
    # delivery it never checked is the #134 mistake.
    log_fn("%s: typed-undone, parked draft popped back" % prefix
           if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30))
           else "%s: typed-undone, pop UNVERIFIED (draft still parked)"
                % prefix)


def deliver_with_stash(pid, text, run, captured=None, logs=None, sleep_fn=None,
                       state=None):
    """Deliver `text` into an IDLE pane, parking whatever the input box holds.

    #189 — STASH UNCONDITIONALLY. This helper used to require a NON-EMPTY
    draft before it would act, and then required the box to go bare WITH the
    `› stashed` marker before it would type. Both conditions are answers to
    the same question — "is there really something in the prompt?" — and that
    question is UNANSWERABLE from what we can see: `capture_pane` shells
    `tmux capture-pane -p` with no `-e`, so Claude Code's dim (SGR 246) prompt
    suggestion arrives stripped of its colour and is byte-identical to text
    the user typed. Every classifier in this file therefore reported drafts
    that did not exist; the stash then had nothing to park, no marker could
    light, and the verify failed forever. Measured cost: `stash-delivered = 0`
    across the whole fleet in 24h, with a 529 sitting unattended on gatekeeper
    until the user typed `continue` by hand.

    So we stop computing the answer. Park first, look after, and let each
    OBSERVED outcome choose its own recovery:

      1. Abort if the stash slot is ALREADY occupied (`› stashed` anywhere in
         the capture). This is the ONE genuine decline that remains: the slot
         is single and overwrites silently, so stashing over it would destroy
         a parked draft with no way back.
      2. Require only that the pane is idle at an input boundary — a free `❯`
         (bare or holding anything at all) and no live-turn signal
         ('esc to interrupt'). Emptiness is explicitly NOT a precondition.
      3. If the agent-strip selector holds focus (`_strip_selected`, #36),
         ONE Escape first — never two (a rapid double-Escape PERMANENTLY
         DELETES a draft, empirically confirmed).
      4. Ctrl+S. Then a bounded render-SETTLE poll (never a blind timeout)
         reports which of four states the pane is in: PARKED (bare + marker —
         a real draft is in the slot), NOOP (bare, no marker — there was
         nothing to park, which is a fine outcome and not an error),
         UNRESOLVED (still shows content, no marker — a rendered ghost, or a
         genuinely lost keystroke, deliberately NOT distinguished), or
         NO_BOUNDARY (the input line is gone — abort, touch nothing else).
      5. Type `text` literally and verify. Typing is itself the discriminator
         the pane refuses to give us: a ghost suggestion is REPLACED by the
         keystroke, while a real draft is APPENDED to. After PARKED/NOOP the
         box was verified bare, so the usual tail-tolerant `_typed_landed`
         applies; after UNRESOLVED the box held something, so the STRICT
         `_typed_exclusively` is required — otherwise a wrap landing inside
         our own text could pass an append off as a clean type.
      6. Verify failure recovers by what actually happened, never blindly, and
         never leaves our own text behind (#193). PARKED/NOOP → the box was
         VERIFIED BARE a moment ago, so every character in it is ours:
         `_undo_typed_text` backspaces exactly what we typed and confirms the
         box is bare again. Only THEN, and only when PARKED, does one Ctrl+S
         pop the draft back — firing it while our text still sat in the box
         would PARK that text over the user's parked draft, since the slot is
         single and overwrites silently. UNRESOLVED → nothing is parked, so a
         Ctrl+S would jam the slot into step 1's decline; `_undo_appended_text`
         backspaces once the box provably ends with our own characters. When
         it does not — a truncated type — no FURTHER keystrokes are sent and
         the log says plainly that the payload we already typed is still in
         the box, because "is there a draft I would destroy?" resolves an
         unknown to YES. Typing into an already-WRAPPED unresolved box is
         refused back at step 4, while refusing is still free.
      7. Enter submits. If the text is STILL at the boundary (a swallowed
         Enter — the agent-strip-selector class of bug, #36), ONE corrective
         Escape+Enter (never a second bare Enter, never two Escapes).
      8. Success = the box no longer shows our text. A parked draft
         auto-restores itself once the delivered turn completes.

    `logs`, if a list, gets one reason string appended on every abort/success
    path — callers that want visibility (or tests) pass one in; the default
    (None) is silent, matching every other keystroke helper in this file.
    `sleep_fn` backs step 4's settle poll (default `time.sleep`; tests inject
    a no-op)."""
    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep
    cap = captured if captured is not None else watchdog.capture_pane(pid, run, lines=30)
    if cap and STASH_MARKER in cap:
        _log("stash-abort: slot occupied")
        return False
    if not watchdog._has_free_prompt(cap, bare_only=False):
        _log("stash-abort: no free prompt")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("stash-abort: live turn")
        return False
    # #271: from here on we are genuinely about to act on this box — persist
    # whatever it currently holds BEFORE the first keystroke of ours lands,
    # so a stash whose eventual on-screen restore silently fails still has a
    # recoverable trace. A no-op (returns None, writes nothing) whenever the
    # box is bare, which is the common case. Honesty note (adversarial
    # review, #271): `cap` is the CALLER's own capture, which for some
    # callers (job 7's Discord reply, job 20's silent-death re-arm) can be a
    # few tmux round-trips stale by the time the actual `C-s` fires a couple
    # of lines below — the rescued text is therefore a best-effort SNAPSHOT
    # near the moment of action, not a byte-exact guarantee of what the box
    # holds at the literal instant `C-s` lands.
    watchdog._draft_rescue_persist(pid, cap, logs=logs)
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    cap, outcome, pre_text = watchdog._await_stash_settled(pid, run, sleep_fn)
    if outcome in (STASH_NO_BOUNDARY, STASH_UNRESOLVED):
        # #852 A -- NEVER type into a box that is not PROVABLY bare. After our
        # own `C-s`, only PARKED (bare + marker) and NOOP (bare, nothing to
        # park) mean the box is empty and every later keystroke is provably our
        # own. UNRESOLVED means the box STILL shows content our toggle did not
        # park -- a POTENTIAL foreign draft (single-row OR wrapped, deliberately
        # not distinguished) -- and NO_BOUNDARY means the input line vanished
        # (a turn/dialog started). Typing the nudge onto either is exactly how
        # the owner's forgotten `s` got `slane-check:` glued onto it (the #852
        # incident): the append can neither be cleanly verified nor undone
        # without risking the human's text. Abort BEFORE any keystroke -- the
        # nudge can never be appended to a human draft. Backoff is the caller's
        # (a refused attempt is retried next sweep), exactly as today.
        _log("stash-abort: stash-unresolved")
        return False
    parked = outcome == STASH_PARKED
    if parked:
        # #488 -- we have DEFINITIVELY parked a draft into the single slot: the
        # box went bare AND the marker lit after our OWN `C-s`, which the
        # earlier `slot occupied` abort (top of this function) proves the slot
        # did NOT show before us. So this park is unambiguously OURS, never a
        # pre-existing FOREIGN one -- recording it here (not on a bare
        # abort-outcome at the caller) is what keeps the janitor's age-
        # unbounded reclaim from ever adopting a human's own parked draft
        # (#488 review MAJOR). Persisted in the shared, already-durable
        # watchdog `state` so the janitor can reclaim it after ANY delay if
        # this delivery aborts before popping the draft back (the gk `(1d)`
        # gap). Cleared on our own verified success below (CC then owns the
        # async auto-restore); a `None` state (a caller/test not threading it)
        # is a no-op, exactly like `_janitor_mark_watch`.
        watchdog._janitor_park_record(state, pid, time.time())
    # #747 -- a scroll-length own /goal WRAPS past the box height and CC SCROLLS
    # it, so the head row goes off-screen and head-is-prefix (the default verify)
    # reads CORRUPT even for a perfect type -- the exact #746 class the BARE route
    # already fixed, still open on this route. Give the bare (PARKED/NOOP) branch
    # its OWN two-phase head-checkpoint (the SHARED `_type_two_phase_head_
    # checkpoint`) so the FINAL verify can pass `allow_scrolled=True` SAFELY: the
    # checkpoint proves the leading `/` landed BEFORE the box scrolls, so a
    # head-swallowed long /goal (identical own-substring tail) can never slip to a
    # junk submit (#720). DETECTION only -- unlike `_type_literal_verified`, this
    # route does NOT retype a swallow (it protects a parked draft); it aborts to
    # the next sweep. #852 A -- the UNRESOLVED path aborts BEFORE reaching here,
    # so from this point the box is always PROVABLY BARE (PARKED/NOOP) and
    # `pre_text` is always "": every character typed below is provably our own.
    two_phase = len(text) >= GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD
    if two_phase:
        hc = watchdog._type_two_phase_head_checkpoint(pid, run, text, sleep_fn)
        if hc != _TV_LANDED:
            _log("stash-abort: head-checkpoint-%s" % hc)
            if hc == _TV_CORRUPT:
                # the box holds `<= head_chunk` chars of OUR OWN text on a
                # bare-verified box -> back the chunk off (over-backspace safe) +
                # pop the parked draft. A HOLD box takes NO keystroke (#233/#322/
                # #372) -- the #488 park record + janitor reclaim the parked draft.
                watchdog._undo_and_release_slot(
                    pid, run, text[:GOAL_TYPE_CHECKPOINT_CHARS], parked, _log,
                    "stash-abort", sleep_fn=sleep_fn, state=state)
            return False
    else:
        watchdog._type_literal(pid, run, text, sleep_fn)
    cap = watchdog.capture_pane(pid, run, lines=30)
    itext = watchdog._input_line_text(cap)
    if watchdog._pane_shows_collapsed_paste(itext):
        # #322 — CC's "paste again to expand" is a DIFFERENT collapse shape
        # than `[Pasted text #N]` (which `_typed_landed` already treats as
        # landed) — it is never parsed as a slash command, so submitting it
        # would send whatever IS committed as an ordinary chat message.
        # `_undo_typed_text`'s OWN docstring states its backspace-N-times
        # proof depends on the box never being a collapsed buffer ("this
        # never has to reason about a collapsed buffer") — that
        # precondition does NOT hold for this shape, so routing it through
        # the existing recovery below would send an unproven backspace
        # count against a state this function was never designed to undo.
        # No further keystrokes; the box is left exactly as CC rendered it.
        # Known, deliberate residual: a PARKED draft stays in the single
        # stash slot until a LATER sweep's own "slot occupied" check
        # surfaces it — rare in practice, since `_type_literal`'s chunking
        # exists specifically to avoid ever reaching this state.
        _log("stash-abort: collapsed-paste")
        return False
    # #670 -- the BARE (PARKED/NOOP) branch verifies HEAD-INCLUSIVELY
    # (`_type_verify_landed`: head-row prefix + tail suffix, placeholder-exempt),
    # not the head-blind tail-only `_typed_landed` it used before: a swallowed
    # FIRST char (`ane-check...`) IS a suffix of `lane-check...`, so it passed
    # and was submitted. On a swallow this now reads not-landed -> the existing
    # `_undo_and_release_slot` backs our text off + pops the parked draft, and
    # the caller retries next sweep -- never a submitted corruption. The
    # UNRESOLVED (`pre_text`) branch already uses the exact-match
    # `_typed_exclusively`, which a swallow already fails, so it is unchanged.
    # #747 -- the two-phase (scroll-length) bare branch settle-verifies with
    # `allow_scrolled=True` (the head-checkpoint above proved the leading char
    # landed): the ~3.3k-char rest-type renders with lag, so a single immediate
    # capture can read mid-render -- settle-poll before concluding, exactly as
    # `_type_literal_verified`'s own final verify does. It keeps the settled
    # CLASS (not a bool) so an observed HOLD aborts keystroke-free (below). The
    # short (single-phase) bare branch keeps the pre-#747 single-capture
    # `_type_verify_landed(cap=cap)`, which shares the collapsed-paste guard's
    # own snapshot above (a collapse there already aborts keystroke-free).
    # #852 A -- the box is provably bare here (UNRESOLVED aborted earlier), so
    # there is no `pre_text` verify branch left; only the two verified-bare
    # verify shapes remain.
    if two_phase:
        cls = watchdog._settle_type_verify(
            pid, run, text, sleep_fn, allow_scrolled=True)
        landed = cls == _TV_LANDED
        # #747-review -- the two-phase settle RE-CAPTURES (a fresh snapshot), so
        # an observed HOLD here is a DIFFERENT capture than the collapsed-paste
        # guard above; the ~3.3k-char rest-type is exactly the collapse-prone
        # payload. A HOLD box takes NO keystroke (#233/#322/#372) -- undoing
        # len(text) into it is the exact discipline the checkpoint-HOLD branch
        # and _type_literal_verified both keep -- so branch it out of the undo
        # recovery below (the #488 park record + janitor reclaim a parked draft).
        two_phase_hold = cls == _TV_HOLD
    else:
        landed = watchdog._type_verify_landed(pid, run, text, cap=cap)
        two_phase_hold = False
    if not landed:
        if two_phase_hold:
            _log("stash-abort: type-verify-hold")
            return False
        _log("stash-abort: type-verify-failed")
        # PARKED or NOOP -- the settle poll VERIFIED this box bare a moment ago,
        # so every character in it is ours and backspacing exactly what we typed
        # can reach nothing of the user's.
        watchdog._undo_and_release_slot(pid, run, text, parked, _log, "stash-abort",
                                        sleep_fn=sleep_fn, state=state)
        return False
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    cap = watchdog.capture_pane(pid, run, lines=30)
    itext2 = watchdog._input_line_text(cap)
    if watchdog._typed_landed(text, itext2):
        # swallowed submit (#36 class) — ONE corrective Escape+Enter, never a
        # second Escape.
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        cap = watchdog.capture_pane(pid, run, lines=30)
        itext3 = watchdog._input_line_text(cap)
        if watchdog._typed_landed(text, itext3):
            # #306 — delivery is genuinely dead here, but by construction the
            # box's ENTIRE content is exactly `text`: PARKED/NOOP means it was
            # verified bare before we typed (#852 A removed the UNRESOLVED
            # caller). Recover the SAME way: backspace our own text and, if we
            # parked something in THIS call, pop it back — and on a box we
            # cannot clear, WARN + park it durably (#852 E) rather than leave
            # the pane stuck with our garbled text and the single stash slot
            # occupied forever (the live david@subdev ~2h wedge).
            watchdog._undo_and_release_slot(pid, run, text, parked, _log,
                                   "stash-abort: swallowed-submit-not-recovered",
                                   sleep_fn=sleep_fn, state=state)
            return False
    # #488 -- verified success: CC now owns the async auto-restore of the
    # parked draft, so the janitor must NOT reclaim (a double-restore) -> drop
    # the durable park record we wrote above. Only meaningful when we actually
    # parked; a no-op otherwise.
    if parked:
        watchdog._janitor_clear_park(state, pid)
    _log("stash-delivered")
    return True


# --------------------------------------------------------------------------- #
# #372 — GENERIC JANITOR backstop. Root-cause (the multi-line placeholder
# regex, fixed above) closes TODAY's specific trigger, but the user's own
# binding acceptance criteria demand more: an invariant that watchdog NEVER
# leaves its own typed text in a pane across a sweep boundary (submitted +
# verified / fully undone / a loud one-time ping — never silent), a GENERIC
# backstop that self-heals an UNKNOWN future failure shape (not just today's
# two evidenced ones), and a REACHABLE exit for the "stash occupied"
# deadlock. This is deliberately keyed on OBSERVING our own recognizable
# content sitting stuck — never on a persisted "delivery in flight" marker
# threaded through every one of jobs 7/9/14/20's own call sites (a much
# larger, riskier signature change to a primitive already reviewed 5 times)
# — so it works for ANY caller of `deliver_with_stash`/`send_continue`
# without touching their signatures at all. Hosted in job 20 (`goal_rearm`,
# below) because it is the one job that already sweeps every candidate pane
# every cycle, per this ticket's own FREEZE-compatible design (extend an
# existing job, not a new one).
# --------------------------------------------------------------------------- #

# The two payload shapes THIS codebase's own delivery jobs are known to
# type (#372's two forensically-confirmed incidents): a `/goal <condition>`
# re-arm (jobs 9/20) and a bare `/compact` request (job 14, `COMPACT_TEXT`).
# `"lane-check: "` (#490) is job 20's lane-fill nudge delivered via
# `send_verified` — a plain-text nudge, not a slash command, but equally an
# unambiguous OWN payload (a human never types a message starting with it), so
# a partial/stuck lane-check residue is recognized and reclaimed by the janitor
# recover that runs BEFORE the lane sweep re-processes the pane (goal_dark_watch
# → _janitor_recover), never left to be mis-read as a draft and re-injected.
# Used ONLY to recognize a box's content as PROVABLY our own — never to
# guess about a genuine foreign draft that merely starts with a slash.
# (#497) the transcript-proof nudges adopted alongside the lane-fill one — each
# an unambiguous OWN diagnostic prefix no human ever starts a prompt with, so a
# chunk-typed partial residue left by a swallowed send is reclaimed by the #372
# janitor instead of being mis-read as a draft and re-injected (the #490 Round-2
# re-injection class, same fix, sibling sites):
#   "bounce-backstop: "     — the cross_stream bounce nudge (job 8)      [batch 1]
#   "gk-request backstop: " — the cross_stream gk-request nudge          [batch 1]
#   "stuck-check: "         — the working (job 4, 431c) + textcall (job 4a, 271c)
#                             + dying-subagent (jobs 1b/4a-sub, 238-248c) nudges,
#                             all three chunk-typed and all sharing this exact
#                             leading prefix (batch 3). Deliberately NOT in
#                             `_OWN_NUDGE_SUBMIT_PREFIXES` below: a swallowed
#                             stuck-check is RECLAIMED by the janitor (decide_working
#                             re-fires it next sweep), never SUBMITTED in place —
#                             the #501 own-submit path stays scoped to the
#                             supervisor's own lane/bounce/gkreq nudges.
# (#505) the #297 flag prompt (chunk-typed, up to 1183c) was NOT registered here.
# Its head is a NATURAL-LANGUAGE Slovak sentence, so the only registerable
# fingerprint is that head itself — but `_janitor_recover` recognizes an own
# residue via `_input_line_text` (the box TAIL, the #193 endswith contract), and
# a wrapped flag prompt's head sits on the HEAD row, ABSENT from the tail: a
# head-prefix here would be a #501-class DEAD branch (verified empirically — the
# same tail-vs-head bug the `lane-check: `/`stuck-check: ` prefixes above ALSO
# latently carry against a wrapped residue). The wrapped flag-prompt residue is
# instead cleaned by `send_verified`'s OWN `_undo_and_release_slot` (its
# `_typed_landed` matches the wrapped tail via `endswith`), and a collapsed-paste
# residue is reclaimed prefix-free by `_PASTED_PLACEHOLDER_RX` under the per-send
# janitor mark — so no prefix is needed. The systemic janitor head-read fix (which
# would revive the wrapped reclaim for ALL these prefixes) is tracked in #506.
_JANITOR_OWN_PREFIXES = ("/goal ", "/compact", "lane-check: ",
                         "bounce-backstop: ", "gk-request backstop: ",
                         "stuck-check: ")

JANITOR_CLEAR_MAX_ITER = 25       # bounds the observed-state clear loop
JANITOR_CLEAR_BATCH_MAX = 200     # per-iteration backspace batch, sized to
                                  # the CURRENTLY OBSERVED remaining content
                                  # -- never a blind `len(text)`-sized single
                                  # burst (#372's own root cause: exactly
                                  # that assumption left a box
                                  # "typed-NOT-undone" in production,
                                  # guessing at a placeholder's true length
                                  # instead of reading it)
JANITOR_CLEAR_SETTLE_S = 0.3

# Bounds `state['janitor_watch'][pid]` (#372 adversarial-review CRITICAL-1
# — the janitor's provenance gate). Generous on purpose: a stuck-forever
# episode this mechanism exists to fix can legitimately sit unresolved for
# many sweeps (the `JANITOR_CLEAR_MAX_ITER`-bounded clear loop can itself
# fail, escalate, and need to be retried later) — the bound exists only to
# stop a genuinely stale mark (a delivering job's own state got wedged, or
# this pane was never revisited) from licensing action on unrelated LATER
# content forever, not to cut off a real, still-unresolved episode.
JANITOR_WATCH_MAX_AGE_S = 6 * 3600


def _looks_like_own_payload(text):
    """#372 — True when `text` starts with one of THIS codebase's own
    recognized delivery payloads (`_JANITOR_OWN_PREFIXES`): the `/goal `/
    `/compact` slash commands, or (#490) the `lane-check: ` plain-text
    lane-fill nudge — each an unambiguous OWN payload no human would type."""
    return bool(text) and text.startswith(_JANITOR_OWN_PREFIXES)


# #852 C — the max stray-prefix offset: a known own prefix appearing at box-head
# position 1..STRAY_PREFIX_MAX_OFFSET means a stray HUMAN char (the owner's
# forgotten `s`) raced to the FRONT of our own swallowed nudge (the incident's
# `slane-check:` shape). Bounded at 3 on purpose: a genuine foreign draft that
# merely MENTIONS an own phrase deep in its text never has the prefix in its
# first few chars, so this admits only the corrupted-prefix leftover, never a
# real draft. Recognition here NEVER acts on its own — the janitor's
# `_janitor_watch_seen` / park-record provenance still decides WHETHER to act.
STRAY_PREFIX_MAX_OFFSET = 3


def _own_prefix_stray_offset(itext):
    """#852 C — the position (1..STRAY_PREFIX_MAX_OFFSET) at which a known own
    prefix appears in `itext`, or None. A match at position 0 is
    `_looks_like_own_payload`'s job (a clean own leftover); this catches ONLY
    the shifted case (a stray human char at the very front). Shortest-prefix,
    earliest-position wins."""
    if not itext:
        return None
    window = itext[:STRAY_PREFIX_MAX_OFFSET + max(len(p)
                   for p in _JANITOR_OWN_PREFIXES)]
    best = None
    for p in _JANITOR_OWN_PREFIXES:
        i = window.find(p)
        if 0 < i <= STRAY_PREFIX_MAX_OFFSET and (best is None or i < best):
            best = i
    return best


# #501 — the STRICT SUBSET of `_JANITOR_OWN_PREFIXES` a human PROVABLY never
# types, so a box whose content starts with one is UNAMBIGUOUSLY our own
# swallowed nudge and may be SUBMITTED IN PLACE on content alone (no janitor
# provenance needed) by the lane guard's `submit_own_draft_verified` path.
# Deliberately EXCLUDES the two human-typeable members of
# `_JANITOR_OWN_PREFIXES` — `/goal ` (the documented manual autopilot-arming
# flow) and `/compact` — because content is NOT proof of ownership for those:
# auto-submitting a human's half-composed `/goal <condition>` would ARM a
# broken goal, so those stay provenance-GATED in the #372 janitor and never
# reach the content-alone submit path (HARD CONSTRAINT a — the foreign-draft
# protection is never weakened). Every prefix here is asserted machine-only by
# `_JANITOR_OWN_PREFIXES`' own registration ("a human never types a message
# starting with it"): `lane-check: ` (the lane-fill nudge, #490), and the two
# cross_stream transcript-proof nudges (#497).
_OWN_NUDGE_SUBMIT_PREFIXES = ("lane-check: ", "bounce-backstop: ",
                              "gk-request backstop: ")


def _own_nudge_submit_prefix(text):
    """#501 — the `_OWN_NUDGE_SUBMIT_PREFIXES` prefix `text` starts with, or
    None. Used by the lane guard + `submit_own_draft_verified` to (a) recognize
    a swallowed OWN nudge safe to submit in place on content alone, and (b) as
    the wrap-free TRANSCRIPT VERIFICATION TOKEN (the matched prefix sits at the
    very start of the submitted draft, unaffected by any wrap artifact). A
    STRICT SUBSET of `_looks_like_own_payload`'s `_JANITOR_OWN_PREFIXES`,
    excluding the human-typeable `/goal `/`/compact` — see
    `_OWN_NUDGE_SUBMIT_PREFIXES`."""
    if not text:
        return None
    for p in _OWN_NUDGE_SUBMIT_PREFIXES:
        if text.startswith(p):
            return p
    return None


def _looks_like_own_stuck_content(itext):
    """#372 — True when a pane's CURRENT input-box content can ONLY be OUR
    OWN stuck delivery: either Claude Code's own collapsed-paste placeholder
    shape (only a long single-line `send-keys -l` burst ever renders this —
    a human pasting something that happens to render identically is a far
    rarer coincidence than a routine automated delivery) or a literal,
    uncollapsed KNOWN own-payload prefix (`_looks_like_own_payload`).
    Ownership is never guessed from resemblance alone; anything else
    (including a genuinely truncated/partial own payload with no
    recognizable prefix) is left completely untouched — the janitor would
    rather miss a recoverable case than risk a genuine foreign draft.

    #852 C — ALSO True when a known own prefix appears within the first
    `STRAY_PREFIX_MAX_OFFSET` characters (position 1..3), i.e. a stray HUMAN
    char raced to the FRONT of our own swallowed nudge (the incident's
    `slane-check:` shape). This only widens RECOGNITION; the janitor's own
    provenance gate (`_janitor_watch_seen` / a park record) still decides
    WHETHER to act, so a foreign draft that merely mentions the phrase is
    recognized-but-never-touched (no provenance)."""
    if not itext:
        return False
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip())) \
        or watchdog._looks_like_own_payload(itext) \
        or _own_prefix_stray_offset(itext) is not None


# #737 -- the minimum contiguous NORMALIZED-char overlap a box-vs-payload
# SUBSTRING match must clear to count as our own leftover. A real scrolled /goal
# leftover is 2500+ chars; 80 is the safety floor whose SOLE justification is
# empirical + provenance-backed: a human never types 80 consecutive chars
# byte-identical to the /goal template, and every consumer of this proof is ALSO
# gated by janitor provenance (`_janitor_watch_seen`/park record) or first-person
# provenance (the arm-confirm cleanup proved the box bare seconds before typing).
# NOTE: a substring match is LOOSER, not tighter, than the `GOAL_STRANDED_MIN_
# MATCH` (200) exact/prefix window at equal length -- it accepts any payload
# window, not only the head -- so the floor + those gates are what keep it safe,
# never "substring is stricter". Do NOT re-derive the floor from a tightness
# argument (#562/#563 honesty bar).
GOAL_ARM_LEFTOVER_MIN_SUBSTR = 80


def _box_norm_from_capture(captured):
    """#737 -- the whole VISIBLE input-box content, whitespace-normalized,
    reconstructed HEAD-FIRST from every wrapped row (`_input_box_rows_raw`, the
    `❯`/NBSP glyph stripped off the head row). '' when no box is located. A
    SCROLLED long draft renders only its TAIL rows, so this is strictly the
    visible box -- never the full off-screen payload."""
    rows = watchdog._input_box_rows_raw(captured)
    if not rows:
        return ""
    head = rows[0].lstrip("❯").strip()
    full = (head + " " + " ".join(rows[1:])).strip() if len(rows) > 1 else head
    return " ".join(full.split())


def _box_is_own_leftover(captured, payload, min_chars):
    """#737 -- True when the VISIBLE input-box content is a contiguous SUBSTRING
    of `payload`, at least `min_chars` normalized chars long: the render
    signature of a SCROLLED long own /goal (head rows scrolled off, only the
    tail visible, so the `/goal ` prefix `_looks_like_own_stuck_content` keys on
    is gone). A short human draft (< `min_chars`) never matches, and a genuine
    foreign draft is never a contiguous substring of our own frozen payload --
    so the fail-safe direction (no proof -> untouched, foreign draft nikdy) is
    preserved. `payload` is the request's own `text` / the /goal template the
    caller supplies -- NEVER a rescue snapshot of the box itself (that would be
    a tautology and could match a raced-in foreign draft, #737 design fork)."""
    if not payload:
        return False
    box_norm = _box_norm_from_capture(captured)
    if not box_norm or len(box_norm) < min_chars:
        return False
    return box_norm in " ".join(payload.split())
