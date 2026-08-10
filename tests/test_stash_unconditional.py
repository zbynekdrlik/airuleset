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
    """THE regression. A pane whose input box is genuinely EMPTY but renders a
    grey suggestion must still get its keystroke delivered — under either
    possible answer to `does Ctrl+S dismiss the suggestion`, because that
    answer is not established and the fix must not depend on it."""

    def test_delivers_whether_or_not_ctrl_s_dismisses_the_suggestion(self):
        for survives in (True, False):
            with self.subTest(ghost_survives_ctrl_s=survives):
                pane = FakePane(draft="", ghost=GHOST,
                                ghost_survives_ctrl_s=survives)
                logs = []
                ok = deliver(pane, logs=logs)
                self.assertTrue(ok, logs)
                self.assertEqual(pane.submitted, [TEXT], logs)
                self.assertIsNone(pane.stash,
                                  "nothing was ever there to park: %r" % logs)

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
    """The full matrix. Every free-slot cell delivers regardless of what the
    box appears to hold; every occupied-slot cell declines with the ONE
    distinct reason and touches the pane with ZERO keystrokes, because the
    single slot overwrites silently and would destroy a parked draft."""

    CASES = (("bare", dict(draft="", ghost="")),
             ("ghost", dict(draft="", ghost=GHOST)),
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

    def test_a_merely_lagged_ctrl_s_now_delivers_instead_of_aborting(self):
        # #176 F4's case, one step further: the toggle DID take (the draft is
        # really parked and the box is really bare) but the render lags for
        # the whole settle window. The old code called that verify-bare-failed
        # and spent two more toggles trying to undo a stash that was doing
        # exactly its job. Typing into the genuinely-bare box lands cleanly,
        # so the correct outcome is a delivery with the draft still parked.
        pane = FakePane(draft=DRAFT, lag_captures=wd.STASH_VERIFY_SETTLE_POLLS)
        logs = []
        self.assertTrue(deliver(pane, logs=logs), logs)
        self.assertEqual(pane.submitted, [TEXT], logs)
        self.assertEqual(pane.stash, DRAFT, logs)


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
        # a render race), the recovery still gives up — but LOUDLY: the
        # reason string is never silently dropped.
        pane = FakePane(draft=DRAFT, swallow_enters=2,
                        bspace_lag_captures=999999)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertTrue(any(ln == "stash-abort: swallowed-submit-not-"
                            "recovered: typed-NOT-undone, draft left parked"
                            for ln in logs),
                        "a genuine double failure must still be logged "
                        "LOUDLY, never silently: %r" % logs)


class AppendUndoVerifyRaceIsSettledToo(unittest.TestCase):
    """#354 — the SAME render-lag hazard hits `_undo_appended_text`
    (the UNRESOLVED-box sibling of `_undo_typed_text`, reached when the
    `C-s` toggle is lost and our type APPENDS to a real draft rather than
    landing on a bare box) for the identical reason: one immediate capture
    right after the backspace batch, no settle poll."""

    def test_a_lagged_append_undo_still_verifies_as_recovered(self):
        pane = FakePane(draft=DRAFT, lose_next_ctrl_s=True,
                        bspace_lag_captures=1)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertEqual(pane.draft, DRAFT,
                         "the user's draft must survive byte-identical, "
                         "even when the undo's own verify races: %r" % logs)
        self.assertIn("stash-abort: append-undone", logs, logs)
        self.assertNotIn("stash-abort: append-NOT-undone", logs, logs)

    def test_a_genuinely_stuck_append_undo_still_logs_the_reason(self):
        pane = FakePane(draft=DRAFT, lose_next_ctrl_s=True,
                        bspace_lag_captures=999999)
        logs = []
        ok = deliver(pane, logs=logs)
        self.assertFalse(ok, logs)
        self.assertIn("stash-abort: append-NOT-undone", logs,
                      "a genuine double failure must still be logged "
                      "LOUDLY, never silently: %r" % logs)
