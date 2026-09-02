"""watchdog #189 — stash UNCONDITIONALLY; never ask what is in the prompt.

Measured symptom (2026-07-30, gatekeeper + fleet journals): watchdog job 1
delivered `continue` ZERO times automatically in 24h box-wide
(`stash-delivered = 0`). A 529 Overloaded sat unattended until the user typed
`continue` by hand. Six consecutive sweeps produced the identical trio
`stash-abort: verify-bare-failed` -> `stash-abort: draft-recovered` ->
`skip stash-abort (api-error)`.

ROOT CAUSE, confirmed by pane capture rather than theory. `capture_pane`
shells `tmux capture-pane -p` with no `-e`, so SGR attributes are stripped.
Claude Code renders its predicted-next-prompt GHOST as dim SGR 246 after the
`❯` glyph plus a U+00A0 separator, i.e. exactly the shape a genuinely typed
draft has. Stripped of colour the two are the SAME BYTES, so every classifier
downstream (`_find_boundary_line` -> `_input_line_text` -> `_has_free_prompt`
-> `_classify_boundary`) reports a draft that does not exist. Believing a
draft is held, job 1 routes to `deliver_with_stash`; Ctrl+S then has nothing
to park, no `› stashed` marker can ever light, and the old bare-verify could
never succeed. `verify-bare-failed` was the CORRECT observation of a delivery
that should never have had to verify anything at all.

THE FIX IS NOT A BETTER DETECTOR. The user directive is to stop computing the
answer: stash UNCONDITIONALLY, and then it does not matter whether anything is
in the prompt. Concretely, and locked below:

  * an already-bare prompt is a SUCCESS, not `verify-bare-failed` — stashing
    nothing is a no-op and a no-op is a fine outcome;
  * a ghost-only prompt DELIVERS, under EITHER possible pane model (whether
    or not Ctrl+S dismisses the suggestion) — a design whose correctness hangs
    on a guessed pane model is precisely what hid the space-vs-U+00A0 bug for
    the whole life of the stash mechanism (#100/#101);
  * the ONE genuine decline that remains is an ALREADY-OCCUPIED stash slot —
    Claude Code has a single slot with a silent overwrite, so stashing over it
    destroys a parked draft;
  * a real draft is still never submitted-over and never lost: typing is the
    discriminator (a ghost is REPLACED by a keystroke, a real draft is
    APPENDED to), the type-verify gates the submit, and recovery is chosen by
    the OBSERVED outcome — pop the slot when we parked, undo the append when
    we did not.

The pane here is a MODEL, not a queue of hand-written strings: it holds a real
input buffer, a real single stash slot and a real ghost suggestion, and it
renders itself the way `capture-pane -p` renders a pane. Fixtures that encode
an assumed pane are what this file exists to stop trusting.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

TEXT = "continue"
DRAFT = "nechaj tak"
GHOST = "zdvihnem limit"

# The REAL bytes, read off the live gatekeeper pane (issue #189 comment 2):
# `cat -A` gave `M-bM-^]M-/M-BM- $` = U+276F then U+00A0 then end of line, and
# `capture-pane -e` rendered the boundary as SGR 246 ... SGR 39. Colour 246 is
# the same dim grey the surrounding chrome uses, which is why an
# attribute-stripped capture cannot tell the ghost from typed text.
SGR_DIM = "\x1b[38;5;246m"
SGR_OFF = "\x1b[39m"
_SGR_RX = re.compile(r"\x1b\[[0-9;]*m")


def strip_sgr(s):
    """What `tmux capture-pane -p` (no `-e`) hands the watchdog: the same row
    with every attribute removed."""
    return _SGR_RX.sub("", s)


class FakePane:
    """A tmux pane running Claude Code, modelled at the level the watchdog can
    actually observe: an input buffer, ONE stash slot, and a ghost suggestion
    that renders like text but is not text.

    Knobs exist only for the behaviours that are genuinely UNKNOWN, so a test
    can assert an outcome holds under every one of them:

      `ghost_survives_ctrl_s` — whether a Ctrl+S press dismisses the rendered
        suggestion. Nobody has established this against a live build, so the
        delivery path must not depend on it.
      `lose_next_ctrl_s` — the keystroke is genuinely dropped and toggles
        nothing (the hazard #176 R3 was reopened for).
      `lag_captures` — the next N captures render the PRE-toggle screen even
        though the toggle already took effect server-side (#176 F4).
      `bspace_lag_captures` — the next N captures AFTER a BSpace batch
        render the PRE-batch screen even though every backspace already
        landed server-side (#354) — a SEPARATE, independent knob from
        `lag_captures` (which is scoped to the `C-s` toggle only): a
        `/goal`-sized undo can flood up to `STASH_UNDO_MAX_BACKSPACES`
        individual BSpace keystrokes into ONE `send-keys` call, and the
        render genuinely can lag behind that flood the same way a single
        `C-s` toggle's render can. `self.draft` itself keeps updating
        correctly underneath throughout — only the RENDER a capture reads
        is stale, exactly like the real incident (gk journal: "typed-NOT-
        undone" although the box was, moments later, provably bare).
    """

    def __init__(self, draft="", ghost="", stash=None,
                 ghost_survives_ctrl_s=True, lose_next_ctrl_s=False,
                 lag_captures=0, busy=False, swallow_enters=0,
                 bspace_lag_captures=0):
        self.draft = draft
        self.ghost = ghost
        self.stash = stash
        self.ghost_survives_ctrl_s = ghost_survives_ctrl_s
        self.lose_next_ctrl_s = lose_next_ctrl_s
        self.lag_captures = lag_captures
        self.busy = busy
        # #306 -- the #36 agent-strip-selector class of bug: Enter is
        # swallowed (neither submits nor clears the box) instead of being
        # dropped/lost outright. `deliver_with_stash` sends ONE corrective
        # Escape+Enter retry on a swallowed submit, so a value of 2 here
        # models BOTH the original Enter and that one retry being swallowed
        # -- the "swallowed-submit-not-recovered" terminal outcome.
        self.swallow_enters = swallow_enters
        self.bspace_lag_captures = bspace_lag_captures
        self.submitted = []
        self.sent = []
        self._lagged = None
        self._bspace_lagged = None

    # -- rendering ---------------------------------------------------------
    def _render(self, draft, ghost, stash):
        visible = draft if draft else ghost
        attributed = (SGR_DIM + "❯\xa0" + visible + SGR_OFF) if ghost and not draft \
            else ("❯\xa0" + visible if visible else "❯\xa0")
        footer = "  ctx ░░" + ("  " + wd.STASH_MARKER if stash is not None else "")
        if self.busy:
            return "● Validate issue\n  ⎿ running…\n✳ Baking… (2m · esc to interrupt)\n"
        return "\n".join(["● turn done", "────────────", attributed,
                          "────────────", footer]) + "\n"

    def capture(self):
        """One `capture-pane -p` — attributes stripped, exactly like tmux."""
        if self.bspace_lag_captures > 0 and self._bspace_lagged is not None:
            self.bspace_lag_captures -= 1
            return strip_sgr(self._bspace_lagged)
        if self.lag_captures > 0 and self._lagged is not None:
            self.lag_captures -= 1
            return strip_sgr(self._lagged)
        return strip_sgr(self._render(self.draft, self.ghost, self.stash))

    def capture_e(self):
        """`capture-pane -e` — attributes preserved. Used ONLY to prove the two
        renderings are byte-identical once stripped; production never asks."""
        return self._render(self.draft, self.ghost, self.stash)

    # -- keystrokes --------------------------------------------------------
    def _snapshot(self):
        self._lagged = self._render(self.draft, self.ghost, self.stash)

    def _dismiss_ghost(self):
        if not self.ghost_survives_ctrl_s:
            self.ghost = ""

    def key(self, k):
        if k == "C-s":
            self._snapshot()
            if self.lose_next_ctrl_s:
                self.lose_next_ctrl_s = False
                self._dismiss_ghost()
                return
            if self.draft:
                self.stash = self.draft       # single slot, silent overwrite
                self.draft = ""
            elif self.stash is not None:
                self.draft = self.stash       # toggle pops it back
                self.stash = None
            self._dismiss_ghost()
        elif k == "Enter":
            if self.swallow_enters > 0:
                self.swallow_enters -= 1
                return                          # neither submits nor clears
            if self.draft:
                self.submitted.append(self.draft)
                self.draft = ""
        elif k == "BSpace":
            if self.bspace_lag_captures and self._bspace_lagged is None:
                # #354 -- snapshot BEFORE this batch's first backspace takes
                # visible effect: the whole flood is one synchronous Python
                # loop (`run()`'s send-keys branch), so this fires once, on
                # the batch's first key, and holds the PRE-batch render for
                # `bspace_lag_captures` capture() calls after the batch ends.
                self._bspace_lagged = self._render(self.draft, self.ghost,
                                                    self.stash)
            self.draft = self.draft[:-1]
            self.ghost = ""
        elif k == "Escape":
            pass                              # one Escape never deletes a draft

    def type(self, text):
        self.draft += text                    # a keystroke always kills the ghost
        self.ghost = ""

    # -- the `run` callable the watchdog uses ------------------------------
    def run(self, argv, timeout=8):
        self.sent.append(argv)
        joined = " ".join(argv)
        if "capture-pane" in joined:
            return self.capture()
        if "display-message" in joined:
            return "0"
        if "-l" in argv:
            self.type(argv[-1])
            return ""
        if argv[0] == "tmux" and argv[1] == "send-keys":
            for k in argv[4:]:
                self.key(k)
            return ""
        return ""

    # -- assertions helpers ------------------------------------------------
    def keystrokes(self):
        out = []
        for a in self.sent:
            j = " ".join(a)
            if "capture-pane" in j or "display-message" in j:
                continue
            out.extend(a[4:] if "-l" not in a else ["-l"])
        return out

    def ctrl_s_count(self):
        return self.keystrokes().count("C-s")


def deliver(pane, text=TEXT, logs=None):
    return wd.deliver_with_stash("%1", text, pane.run, logs=logs,
                                 sleep_fn=lambda s: None)


class GhostTextIsIndistinguishableOnceStripped(unittest.TestCase):
    """The measurement the whole ticket rests on — kept as an executable fact
    so nobody re-derives the fix from the assumption that it is detectable."""

    def test_ghost_and_a_real_draft_capture_to_the_same_bytes(self):
        ghosty = FakePane(draft="", ghost=GHOST)
        drafty = FakePane(draft=GHOST)
        self.assertNotEqual(ghosty.capture_e(), drafty.capture_e(),
                            "with attributes the ghost IS distinguishable")
        self.assertEqual(ghosty.capture(), drafty.capture(),
                         "stripped of attributes they must be identical — this "
                         "is why no plain-capture classifier can ever tell them "
                         "apart")
        self.assertEqual(wd._input_line_text(ghosty.capture()), GHOST)


class GhostOnlyPaneStillDelivers(unittest.TestCase):
    """A pane whose input box is genuinely EMPTY but renders a grey suggestion.
    #189 typed into it to discriminate a ghost (replaced) from a real draft
    (appended). #852 A CHANGED that: typing is allowed only from a PROVEN-bare
    box, so it depends on whether `C-s` dismisses the suggestion:
      * dismissed (`ghost_survives_ctrl_s=False`) -> box is bare (NOOP) -> we
        deliver, exactly as before;
      * survives -> box still shows content our C-s did not park (UNRESOLVED),
        indistinguishable from a real forgotten draft -> we abort THIS sweep
        (`stash-unresolved`) and retry when the ghost is gone, rather than ever
        risk appending onto what might be a human draft (the #852 incident).
    A safe degradation: the delivery is deferred, never a leak. (Whether a
    ghost survives C-s in real CC is not established; when it does not, this is
    byte-identical to the pre-#852 behaviour.)"""

    def test_delivers_when_ctrl_s_dismisses_the_suggestion(self):
        pane = FakePane(draft="", ghost=GHOST, ghost_survives_ctrl_s=False)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertTrue(ok, logs)
        self.assertEqual(pane.submitted, [TEXT], logs)
        self.assertIsNone(pane.stash,
                          "nothing was ever there to park: %r" % logs)

    def test_aborts_this_sweep_when_the_suggestion_survives_ctrl_s(self):
        # #852 A -- a surviving ghost reads UNRESOLVED (indistinguishable from a
        # real draft); we never type onto it, so nothing is submitted and the
        # box is left byte-identical for a retry.
        pane = FakePane(draft="", ghost=GHOST, ghost_survives_ctrl_s=True)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertEqual(pane.submitted, [], logs)
        self.assertNotIn("-l", pane.keystrokes(),
                         "an unresolved box is never typed into: %r" % pane.sent)
        self.assertNotIn("Enter", pane.keystrokes(), pane.sent)
        self.assertTrue(any("stash-unresolved" in ln for ln in logs), logs)

    def test_the_ghost_is_never_treated_as_a_draft_to_protect(self):
        pane = FakePane(draft="", ghost=GHOST)
        logs = []
        deliver(pane, logs=logs)
        self.assertFalse(any("verify-bare-failed" in ln for ln in logs), logs)
        self.assertFalse(any("draft-recovered" in ln for ln in logs), logs)


class AlreadyBarePromptIsANoopSuccess(unittest.TestCase):
    """Stashing nothing is a no-op, and a no-op is a fine outcome. The old
    precondition refused a bare box outright (`not idle-with-draft`) and the
    old verify then called the very same emptiness a failure."""

    def test_bare_box_delivers_instead_of_aborting(self):
        pane = FakePane(draft="", ghost="")
        logs = []
        self.assertTrue(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [TEXT], logs)

    def test_bare_box_parks_nothing_and_leaves_the_slot_free(self):
        pane = FakePane(draft="", ghost="")
        deliver(pane)
        self.assertIsNone(pane.stash)


class OccupiedSlotIsTheOnlyDecline(unittest.TestCase):
    """The matrix. A free-slot box that our own C-s leaves PROVABLY BARE
    delivers; an occupied-slot cell declines with the ONE distinct reason and
    touches the pane with ZERO keystrokes (the single slot overwrites silently
    and would destroy a parked draft). #852 A -- a free-slot box our C-s does
    NOT leave bare (a surviving ghost) reads UNRESOLVED and is deferred, not
    delivered, so the 'always delivers' matrix is scoped to the provably-bare
    cells (`bare` + `draft`); the `ghost`-survives cell is covered by
    GhostOnlyPaneStillDelivers."""

    # Cells whose own C-s leaves the box PROVABLY BARE: a bare box (NOOP) and a
    # real draft (PARKED into the slot). A surviving ghost is NOT here -- it is
    # the UNRESOLVED-defer case (GhostOnlyPaneStillDelivers).
    CASES = (("bare", dict(draft="", ghost="")),
             ("draft", dict(draft=DRAFT, ghost="")))

    def test_free_slot_always_delivers(self):
        for name, kw in self.CASES:
            with self.subTest(box=name):
                pane = FakePane(stash=None, **kw)
                logs = []
                self.assertTrue(deliver(pane, logs=logs), logs)
                self.assertEqual(pane.submitted, [TEXT], logs)

    def test_occupied_slot_always_declines_with_the_distinct_reason(self):
        for name, kw in self.CASES:
            with self.subTest(box=name):
                pane = FakePane(stash="uz tam nieco parkuje", **kw)
                logs = []
                self.assertFalse(deliver(pane, logs=logs), logs)
                self.assertEqual(logs, ["stash-abort: slot occupied"])
                self.assertEqual(pane.keystrokes(), [],
                                 "never touch a pane whose slot is occupied: %r"
                                 % pane.sent)
                self.assertEqual(pane.stash, "uz tam nieco parkuje")
                self.assertEqual(pane.submitted, [])


class RealDraftIsStillProtected(unittest.TestCase):
    """Removing the precondition must not cost the draft protections that were
    paid for in #35 / #36 / #176 — only their shape changes, from `refuse to
    act` to `act, verify, and recover by the observed outcome`."""

    def test_parked_draft_is_delivered_around_and_left_in_the_slot(self):
        pane = FakePane(draft=DRAFT)
        logs = []
        self.assertTrue(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [TEXT], logs)
        self.assertEqual(pane.stash, DRAFT,
                         "CC auto-restores it when the delivered turn ends")
        self.assertEqual(pane.ctrl_s_count(), 1, pane.sent)

    def test_a_lost_ctrl_s_never_submits_and_the_append_is_undone(self):
        # The hazard #176 R3 was reopened for: the toggle is genuinely
        # DROPPED, so the draft is still in the box when we type. Typing is
        # the discriminator — a real draft APPENDS, which the exclusive
        # type-verify refuses to submit — and the recovery must undo our own
        # characters rather than send another Ctrl+S, which would park the
        # polluted text and jam the slot into a permanent decline.
        pane = FakePane(draft=DRAFT, lose_next_ctrl_s=True)
        logs = []
        self.assertFalse(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [], logs)
        self.assertEqual(pane.draft, DRAFT,
                         "the user's draft must survive byte-identical: %r" % logs)
        self.assertIsNone(pane.stash, "nothing may be left parked: %r" % logs)
        self.assertEqual(pane.ctrl_s_count(), 1,
                         "no blind second/third toggle: %r" % pane.sent)

    def test_a_lag_within_the_settle_window_still_delivers(self):
        # #176 F4: the toggle DID take (draft parked, box really bare) but the
        # render lags a FEW captures -- within the settle window. The settle
        # poll absorbs it, reads PARKED, and delivers cleanly. (A only defers
        # when the box is STILL non-bare after the whole settle window.)
        pane = FakePane(draft=DRAFT,
                        lag_captures=wd.STASH_VERIFY_SETTLE_POLLS - 1)
        logs = []
        self.assertTrue(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [TEXT], logs)
        self.assertEqual(pane.stash, DRAFT, logs)

    def test_a_lag_past_the_whole_settle_window_defers_this_sweep(self):
        # #852 A -- if the render lags PAST the entire settle window, the box
        # reads UNRESOLVED even though it is really bare server-side. We cannot
        # PROVE it bare this sweep, so we defer (`stash-unresolved`) and deliver
        # on a later sweep once the render catches up -- never risk typing onto
        # what could equally be a real draft. Safe degradation: a bounded
        # retry, never a leak.
        pane = FakePane(draft=DRAFT, lag_captures=wd.STASH_VERIFY_SETTLE_POLLS)
        logs = []
        self.assertFalse(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [], logs)
        self.assertNotIn("-l", pane.keystrokes(),
                         "an unprovable box is never typed into: %r" % pane.sent)
        self.assertTrue(any("stash-unresolved" in ln for ln in logs), logs)


class ChromeAndKeystrokeGatesSurvive(unittest.TestCase):
    def test_busy_pane_is_never_typed_into(self):
        pane = FakePane(draft="", ghost="", busy=True)
        logs = []
        self.assertFalse(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.keystrokes(), [], pane.sent)

    def test_never_two_consecutive_escapes(self):
        for name, kw in (("bare", dict(draft="", ghost="")),
                         ("ghost", dict(draft="", ghost=GHOST)),
                         ("draft", dict(draft=DRAFT)),
                         ("lost", dict(draft=DRAFT, lose_next_ctrl_s=True))):
            with self.subTest(box=name):
                pane = FakePane(**kw)
                deliver(pane)
                keys = pane.keystrokes()
                for a, b in zip(keys, keys[1:]):
                    self.assertFalse(a == "Escape" and b == "Escape",
                                     "a rapid double-Escape permanently deletes "
                                     "a draft: %r" % pane.sent)


class TheOnlyDeclineIsNamedDistinctly(unittest.TestCase):
    """The occupied slot must stay identifiable in the journal on its own, so
    a genuinely jammed slot is never confused with the delivery bugs this
    ticket removes — every other pre-send refusal has a different reason."""

    def test_occupied_slot_reason_is_not_shared_with_any_other_refusal(self):
        occupied = FakePane(draft=DRAFT, stash="parked")
        busy = FakePane(draft="", busy=True)
        o_logs, b_logs = [], []
        deliver(occupied, logs=o_logs)
        deliver(busy, logs=b_logs)
        self.assertEqual(o_logs, ["stash-abort: slot occupied"])
        self.assertNotEqual(o_logs, b_logs)


class SwallowedSubmitRecoversTheBoxAndReleasesTheSlot(unittest.TestCase):
    """#306 — david@subdev live regression. Before this fix,
    `deliver_with_stash`'s "swallowed-submit-not-recovered" path (our own
    type-verify succeeded, but neither the Enter nor the one corrective
    Escape+Enter retry ever cleared the box) returned `False` with ZERO
    recovery attempted: our own typed text was left glued in the box
    ("poslal text a neodentroval"), and — whenever the outcome that got us
    here was a genuine PARK (our own `C-s` earlier in this same call really
    did stash the pane's prior content) — that parked draft stayed stuck in
    CC's single, silently-overwriting stash slot forever, since nothing
    else in this codebase ever pops it back. Every LATER caller of
    `deliver_with_stash` then saw `STASH_MARKER` in its very first capture
    and aborted with "slot occupied", which is exactly the ~2h wedge the
    ticket's journal evidence shows.

    The fix reuses the SAME recovery the PARKED/NOOP verify-failure branch
    already has (backspace our own typed text — provably safe, since by
    construction the box's ENTIRE content at this point is exactly `text`
    — then, only once bare is confirmed, pop the parked draft back with one
    corrective `C-s`). Delivery still genuinely failed (`False`), but the
    pane ends the call either fully delivered or fully restored — never
    stuck in between."""

    def test_parked_draft_survives_a_permanently_swallowed_submit(self):
        # Our own C-s parks DRAFT; our own type-verify then succeeds (the
        # box was bare, so `text` lands cleanly) — but BOTH the Enter and
        # the one corrective Escape+Enter retry are swallowed.
        pane = FakePane(draft=DRAFT, swallow_enters=2)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertEqual(pane.submitted, [], logs)
        self.assertEqual(pane.draft, DRAFT,
                         "the user's original draft must survive "
                         "byte-identical, popped back out of the slot: %r"
                         % logs)
        self.assertIsNone(pane.stash,
                          "the stash slot must be RELEASED, not left "
                          "occupied forever — this is the ~2h wedge #306 "
                          "reports: %r" % logs)
        self.assertTrue(any("swallowed-submit-not-recovered" in ln
                            for ln in logs), logs)

    def test_bare_box_survives_a_permanently_swallowed_submit(self):
        # Nothing was parked (the box was already bare, NOOP outcome) — so
        # there is no draft to restore, but our own typed text must still
        # not be left glued in the box.
        pane = FakePane(draft="", ghost="", swallow_enters=2)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertEqual(pane.submitted, [], logs)
        self.assertEqual(pane.draft, "",
                         "our own text must be backspaced out, never left "
                         "sitting in the box: %r" % logs)
        self.assertIsNone(pane.stash, logs)

    def test_never_a_rapid_double_escape_during_the_recovery(self):
        # The recovery path must not violate the #35 "never two consecutive
        # Escapes" rule either — a rapid double-Escape permanently deletes
        # a draft.
        pane = FakePane(draft=DRAFT, swallow_enters=2)
        deliver(pane)
        keys = pane.keystrokes()
        for a, b in zip(keys, keys[1:]):
            self.assertFalse(a == "Escape" and b == "Escape",
                             "a rapid double-Escape permanently deletes a "
                             "draft: %r" % pane.sent)


class UndoVerifyRaceIsSettledNotLeftHanging(unittest.TestCase):
    """#354 — gatekeeper live incident: `_undo_typed_text`'s own post-
    backspace verify did ONE immediate capture, exactly the render-lag class
    #176 F4 already found and fixed for the STASH TOGGLE's post-`C-s`
    verify — never extended to the UNDO's own post-backspace verify. A
    `/goal` payload backspaces up to `STASH_UNDO_MAX_BACKSPACES` individual
    BSpace keystrokes in ONE `send-keys` call; the render genuinely can lag
    behind that flood, and the single-shot verify read the box as still
    holding our text and gave up — logging "stash-abort:
    typed-NOT-undone, draft left parked" with the user's real draft
    stranded, invisible, in the single stash slot forever (the exact
    journal line the ticket's own comment quotes, twice, 3m20s apart).

    Reached here via the ALREADY-established `swallow_enters=2` trigger
    (#306's own "swallowed-submit-not-recovered" branch, which shares the
    identical `_undo_and_release_slot` -> `_undo_typed_text` recovery this
    ticket fixes) — `deliver_with_stash`'s own public signature is
    UNCHANGED by this fix, so driving it this way (rather than reaching for
    the private undo helpers directly) proves the fix through the SAME
    entry point job 7/job 20 actually call, with zero risk of a
    signature-only false RED."""

    def test_a_lagged_undo_verify_still_recovers_and_pops_the_draft(self):
        pane = FakePane(draft=DRAFT, swallow_enters=2, bspace_lag_captures=1)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)             # the delivery itself still failed
        self.assertEqual(pane.draft, DRAFT,
                         "the user's original draft must survive "
                         "byte-identical, popped back out of the slot: %r"
                         % logs)
        self.assertIsNone(pane.stash,
                          "the user's real draft must still be popped back "
                          "out of the slot — a render-lagged verify must "
                          "not be treated as a genuine undo failure: %r"
                          % logs)
        self.assertTrue(any(ln == "stash-abort: swallowed-submit-not-"
                            "recovered: typed-undone, parked draft "
                            "popped back" for ln in logs), logs)
        self.assertFalse(any("draft left parked" in ln for ln in logs), logs)

    def test_a_genuinely_stuck_undo_still_ends_loud_not_silent(self):
        # The settle window is BOUNDED (`no-timeout-band-aids.md`) — when
        # the box NEVER actually clears (a real, non-transient failure, not
        # a render race), the recovery still gives up — but LOUDLY. #852 E
        # replaced the old silent `typed-NOT-undone, draft left parked` leak
        # with a `left-in-box UNRECLAIMED` WARNING (naming the pane + the exact
        # typed string) + a durable park record, so the janitor can reclaim it.
        pane = FakePane(draft=DRAFT, swallow_enters=2,
                        bspace_lag_captures=999999)
        logs = []
        state = {}
        ok = wd.deliver_with_stash("%1", TEXT, pane.run, logs=logs,
                                   sleep_fn=lambda s: None, state=state)
        self.assertFalse(ok, logs)
        self.assertTrue(any("left-in-box UNRECLAIMED" in ln and "typed=" in ln
                            for ln in logs),
                        "a genuine double failure must still be logged "
                        "LOUDLY, never silently: %r" % logs)
        self.assertEqual(wd._janitor_park_typed(state, "%1"), TEXT,
                         "the leaked text must be parked for reclaim: %r" % state)


def _box(buf):
    """A single-row Claude Code input box holding `buf`, the way a stripped
    `capture-pane -p` renders it — enough for `_input_line_text` to read the
    tail."""
    return "\n".join(["● turn done", "────────────",
                      "❯\xa0" + buf if buf else "❯\xa0",
                      "────────────", "  ctx ░░"]) + "\n"


class _UndoRun:
    """A minimal recording `run` for a DIRECT `_undo_appended_text` call: the
    box holds `pre + text`; the BSpace batch trims exactly what it removes;
    `lag` captures render the PRE-backspace state first (the #354 render-lag).
    `never_settles` models a genuinely-stuck box (the backspaces do nothing)."""

    def __init__(self, pre, text, lag=0, never_settles=False):
        self.pre, self.buf = pre, pre + text
        self.lag, self.never_settles = lag, never_settles
        self.sent = []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "capture-pane" in j:
            if self.lag > 0:
                self.lag -= 1
                return _box(self.pre + self.buf[len(self.pre):])
            return _box(self.buf)
        if argv[:2] == ["tmux", "send-keys"] and "BSpace" in argv:
            if not self.never_settles:
                n = argv.count("BSpace")
                self.buf = self.buf[:-n] if n < len(self.buf) else ""
        return ""


class _StrayRun:
    """A recording `run` for a DIRECT `_undo_and_release_slot` call: the box
    holds `stray + text`; a BSpace batch trims exactly what it removes from the
    END (leaving the stray at the FRONT), unless `stuck` (the backspaces do
    nothing -- a genuinely wedged box). `unreadable` renders a busy pane with no
    input box (so `_input_line_text` reads None -- a turn started mid-recovery)."""

    def __init__(self, stray, text, stuck=False, unreadable=False):
        self.buf, self.stray, self.text = stray + text, stray, text
        self.stuck, self.unreadable, self.sent = stuck, unreadable, []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "capture-pane" in j:
            if self.unreadable:
                return ("● Validate\n  ⎿ running…\n"
                        "✳ Baking… (2m · esc to interrupt)\n")
            return _box(self.buf)
        if argv[:2] == ["tmux", "send-keys"] and "BSpace" in argv and not self.stuck:
            n = argv.count("BSpace")
            self.buf = self.buf[:-n] if n < len(self.buf) else ""
        return ""


class SuffixProofUndoNeverLeaks(unittest.TestCase):
    """#852 B/E -- the type-verify-failed recovery never SILENTLY leaves our
    own typed text. A stray human char that raced to the FRONT after the settle
    is preserved (our text backspaced off the END); a box we cannot clear at
    all is left with a WARNING + a durable park record carrying the exact
    string, never the old silent `typed-NOT-undone`/`append-unprovable` leak."""

    NUDGE = "lane-check: backlog=7"     # does NOT end with the stray char

    def test_a_stray_leading_char_is_preserved_our_text_removed(self):
        run = _StrayRun("s", self.NUDGE)     # the incident shape: `s` + nudge
        logs, state = [], {}
        wd._undo_and_release_slot("%1", run, self.NUDGE, False, logs.append,
                                  "stash-abort", state=state,
                                  sleep_fn=lambda *a: None)
        self.assertEqual(run.buf, "s",
                         "the human's stray char must survive: %r" % run.buf)
        self.assertTrue(any("stray prefix preserved" in ln for ln in logs), logs)
        # our text is gone -> no UNRECLAIMED leak, no park record needed
        self.assertFalse(any("UNRECLAIMED" in ln for ln in logs), logs)
        self.assertNotIn("%1", state.get("stash_parks", {}))

    def test_a_genuinely_stuck_box_warns_and_parks_the_typed_string(self):
        run = _StrayRun("", self.NUDGE, stuck=True)   # backspaces do nothing
        logs, state = [], {}
        wd._undo_and_release_slot("%1", run, self.NUDGE, False, logs.append,
                                  "stash-abort", state=state,
                                  sleep_fn=lambda *a: None)
        self.assertTrue(any("left-in-box UNRECLAIMED" in ln for ln in logs),
                        "a box we cannot clear must WARN loudly: %r" % logs)
        self.assertEqual(wd._janitor_park_typed(state, "%1"), self.NUDGE,
                         "the exact typed string must be parked for reclaim: %r"
                         % state)

    def test_an_unreadable_box_after_undo_parks_never_false_success(self):
        # #852-review 🟡-3 -- if the box is UNREADABLE after the backspaces (a
        # turn/dialog started: `_input_line_text` None), our full text may still
        # be there -> PARK, never a false `typed-undone` success with no record.
        run = _StrayRun("", self.NUDGE, unreadable=True)
        logs, state = [], {}
        wd._undo_and_release_slot("%1", run, self.NUDGE, False, logs.append,
                                  "stash-abort", state=state,
                                  sleep_fn=lambda *a: None)
        self.assertTrue(any("left-in-box UNRECLAIMED" in ln for ln in logs),
                        "an unreadable box must park, not claim success: %r" % logs)
        self.assertFalse(any("stray prefix preserved" in ln for ln in logs), logs)
        self.assertEqual(wd._janitor_park_typed(state, "%1"), self.NUDGE, state)

    def test_a_long_foreign_residue_parks_never_false_success(self):
        # #852-review 🟡-3 -- a foreign SUFFIX raced in and our len(text)
        # backspaces left a LONG residue that is not a suffix of our text: it
        # reads "our text gone" but is NOT a short stray -> PARK, never claim a
        # stray-preserved success.
        run = _StrayRun("moja dlha rozpisana poznamka ", self.NUDGE)
        logs, state = [], {}
        wd._undo_and_release_slot("%1", run, self.NUDGE, False, logs.append,
                                  "stash-abort", state=state,
                                  sleep_fn=lambda *a: None)
        self.assertTrue(any("left-in-box UNRECLAIMED" in ln for ln in logs),
                        "a long residue must park, not claim a stray success: %r"
                        % logs)
        self.assertEqual(wd._janitor_park_typed(state, "%1"), self.NUDGE, state)


class _CollapseRun:
    """A NOOP-settle (bare box) whose type then renders CC's 'paste again to
    expand' collapse hint -- the #322 shape `deliver_with_stash` must leave
    keystroke-free. Used to prove the #852 E invariant: even a HOLD/collapsed
    leave writes a durable park record + WARNs, never a silent leak."""

    def __init__(self, hint="paste again to expand"):
        self.typed, self.hint, self.sent = False, hint, []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "capture-pane" in j:
            return _box(self.hint if self.typed else "")
        if "display-message" in j:
            return "0"
        if "-l" in argv:
            self.typed = True
        return ""


class NoPathLeavesTypedTextSilently(unittest.TestCase):
    """#852 E -- the lock: no `deliver_with_stash` code path that types leaves
    our text without a WARNING + a durable park record carrying the exact
    string. The genuinely-stuck-undo leak is covered in SuffixProofUndoNeverLeaks;
    this locks the OTHER typed-text leave (a HOLD / collapsed-paste box that
    cannot be backspaced) -- it too parks + warns now."""

    NUDGE = "lane-check: backlog=7"

    def test_a_collapsed_paste_leave_still_parks_and_warns(self):
        run = _CollapseRun()
        logs, state = [], {}
        ok = wd.deliver_with_stash("%1", self.NUDGE, run, logs=logs,
                                   sleep_fn=lambda *a: None, state=state)
        self.assertFalse(ok, logs)
        self.assertTrue(any("collapsed-paste" in ln for ln in logs), logs)
        # the #852 E invariant: the leaked text is parked for reclaim.
        self.assertEqual(wd._janitor_park_typed(state, "%1"), self.NUDGE,
                         "a collapsed-paste leave must park the typed string "
                         "for the janitor to reclaim: %r" % state)

    def test_source_never_leaves_typed_text_without_a_park(self):
        # A structural backstop (the #852 lock): every typed-text leave in
        # deliver_with_stash must hand off to `_park_unreclaimed` (WARN + park)
        # or `_undo_and_release_slot` (which itself parks on failure). #852-review
        # 🔵-6: count ACTUAL CALLS, not comment mentions, so a mutant that deletes
        # the calls but keeps the comments is caught. An AST walk over the
        # function counts `_park_unreclaimed(...)` and `_undo_and_release_slot(...)`
        # Call nodes -- immune to comment/docstring text entirely.
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(wd.deliver_with_stash)))
        called = [n.func.attr if isinstance(n.func, ast.Attribute)
                  else getattr(n.func, "id", None)
                  for n in ast.walk(tree) if isinstance(n, ast.Call)]
        self.assertGreaterEqual(
            called.count("_park_unreclaimed"), 3,
            "the collapsed-paste / two_phase-HOLD / head-checkpoint-HOLD leaves "
            "must each CALL _park_unreclaimed (not just mention it): %r"
            % [c for c in called if c and "park" in c])
        self.assertIn("_undo_and_release_slot", called,
                      "the verify-failure recovery must CALL the parking helper")


class AppendUndoVerifyRaceIsSettledToo(unittest.TestCase):
    """#354 — the SAME render-lag hazard hits `_undo_appended_text`
    (the box-with-residual-content sibling of `_undo_typed_text`) for the
    identical reason: one immediate capture right after the backspace batch,
    no settle poll. #852 A stopped `deliver_with_stash` from ever typing into
    an unresolved box, so `_undo_appended_text` is now exercised DIRECTLY as
    the standalone suffix-proof helper it is — the #354 settle coverage is
    preserved on the function itself."""

    def test_a_lagged_append_undo_still_verifies_as_recovered(self):
        run = _UndoRun(DRAFT, TEXT, lag=1)
        ok = wd._undo_appended_text("%1", run, DRAFT, TEXT,
                                    sleep_fn=lambda *a: None)
        self.assertTrue(ok, "a lagged undo verify must still confirm recovery: "
                        "%r" % run.sent)

    def test_a_genuinely_stuck_append_undo_still_reports_failure(self):
        run = _UndoRun(DRAFT, TEXT, never_settles=True)
        ok = wd._undo_appended_text("%1", run, DRAFT, TEXT,
                                    sleep_fn=lambda *a: None)
        self.assertFalse(ok, "a genuinely stuck undo must report failure, never "
                         "a false success: %r" % run.sent)


class UndoSettlePollHasRealTeeth(unittest.TestCase):
    """#354 adversarial-review findings 2/3 — the settle loop's own CORE
    properties (it genuinely waits between polls; it never sleeps after the
    LAST, already-given-up-on attempt) had no test proving them: a mutant
    setting `STASH_UNDO_SETTLE_S = 0` or dropping the loop's own
    `if i < POLLS - 1` guard survived the whole suite untouched, because
    every existing test injects a no-op `sleep_fn` (via the `deliver()`
    helper) and none of them ever inspected what it was called WITH."""

    def test_the_settle_interval_is_a_real_positive_wait(self):
        # No sleep_fn recording needed at all -- a zero-valued settle
        # interval makes the "bounded settle poll" into 8 back-to-back
        # captures in milliseconds, i.e. #354's own bug reproduced. This is
        # a direct, structural check on the constant itself.
        self.assertGreater(wd.STASH_UNDO_SETTLE_S, 0)
        self.assertGreater(wd.STASH_UNDO_SETTLE_POLLS, 1)

    def test_it_never_sleeps_after_the_final_failed_attempt(self):
        # A genuinely-stuck undo (every poll sees the same stale capture)
        # must sleep EXACTLY `STASH_UNDO_SETTLE_POLLS - 1` times -- between
        # each pair of attempts, never once more after the last, already-
        # given-up-on one. Reached via the SAME swallow_enters=2 trigger
        # the sibling #354 tests above already use.
        pane = FakePane(draft=DRAFT, swallow_enters=2,
                        bspace_lag_captures=999999)
        slept = []
        ok = wd.deliver_with_stash("%1", TEXT, pane.run, logs=[],
                                   sleep_fn=slept.append)
        self.assertFalse(ok)
        self.assertEqual(len(slept), wd.STASH_UNDO_SETTLE_POLLS - 1,
                         "the settle loop must sleep BETWEEN attempts, "
                         "never after the last one: %r" % slept)

    def test_a_single_retry_sleeps_exactly_once(self):
        pane = FakePane(draft=DRAFT, swallow_enters=2, bspace_lag_captures=1)
        slept = []
        ok = wd.deliver_with_stash("%1", TEXT, pane.run, logs=[],
                                   sleep_fn=slept.append)
        self.assertFalse(ok)                   # the delivery itself failed
        self.assertEqual(len(slept), 1,
                         "converging on the 2nd attempt sleeps exactly "
                         "once, between attempt 1 and attempt 2: %r" % slept)


class Issue488DurableParkRecord(unittest.TestCase):
    """#488 -- deliver_with_stash durably records `state['stash_parks'][pid]`
    the moment it DEFINITIVELY parks a draft (STASH_PARKED: the box went bare
    AND the marker lit after our OWN C-s, which the earlier `slot occupied`
    abort proves the slot did not show before us). This is what lets the
    shared janitor reclaim a genuinely-ours park after ANY delay while NEVER
    adopting a pre-existing foreign park (review MAJOR) -- the record is
    scoped to our own park, not any bare abort outcome."""

    def _deliver(self, pane, state):
        return wd.deliver_with_stash("%1", TEXT, pane.run, logs=[],
                                     state=state, sleep_fn=lambda s: None)

    def test_confirmed_park_then_abort_records_a_durable_park(self):
        # A real draft (free slot) parks on C-s, then the submit is swallowed
        # twice -> swallowed-submit-not-recovered abort. The park was OURS, so
        # a durable record is left for the janitor to reclaim after any delay.
        pane = FakePane(draft="human draft parked by us", swallow_enters=2)
        state = {}
        ok = self._deliver(pane, state)
        self.assertFalse(ok)
        self.assertIn("%1", state.get("stash_parks", {}))

    def test_verified_success_clears_the_durable_park_record(self):
        # A normal park+deliver+submit success -> CC owns the async
        # auto-restore, so the janitor must NOT reclaim; the record we wrote at
        # STASH_PARKED is dropped on success.
        pane = FakePane(draft="a real draft")
        state = {"stash_parks": {"%1": 1.0}}
        ok = self._deliver(pane, state)
        self.assertTrue(ok)
        self.assertNotIn("%1", state.get("stash_parks", {}))

    def test_pre_occupied_foreign_slot_never_records_a_park(self):
        # THE review MAJOR fix: the slot is ALREADY occupied (a human's own
        # parked draft) -> deliver_with_stash aborts `slot occupied` before any
        # C-s and never reaches STASH_PARKED, so NO record is written. The
        # janitor can therefore never adopt a foreign park via the age-
        # unbounded path.
        pane = FakePane(draft="my new draft", stash="a HUMAN's parked draft")
        state = {}
        logs = []
        ok = wd.deliver_with_stash("%1", TEXT, pane.run, logs=logs,
                                   state=state, sleep_fn=lambda s: None)
        self.assertFalse(ok)
        self.assertEqual(logs, ["stash-abort: slot occupied"])
        self.assertEqual(state.get("stash_parks", {}), {})

    def test_bare_box_noop_records_nothing(self):
        # A bare box has nothing to park (C-s is a NOOP, no marker lights), so
        # no park record is written -- the record is scoped to a genuine park,
        # not merely to "deliver_with_stash was called".
        pane = FakePane(draft="")
        state = {}
        ok = self._deliver(pane, state)
        self.assertTrue(ok)
        self.assertNotIn("%1", state.get("stash_parks", {}))

    def test_state_none_is_a_no_op(self):
        # A caller/test that does not thread state through pays nothing and
        # never crashes -- mirrors _janitor_mark_watch's None-safety.
        pane = FakePane(draft="a real draft")
        ok = wd.deliver_with_stash("%1", TEXT, pane.run, logs=[],
                                   state=None, sleep_fn=lambda s: None)
        self.assertTrue(ok)
