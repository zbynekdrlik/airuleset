"""watchdog #193 — a WRAPPED input box must not read as "there is no box".

Live incident (2026-07-30, gatekeeper): the gk-request backstop typed its
458-char nudge into the supervisor pane, could not verify it, aborted, and
left its own text sitting unsubmitted in the prompt — with no log line, no
retry that could ever succeed, and job 10 (the dedicated stuck-draft
backstop) blind to it. Nine `needs-gatekeeper` tickets went unanswered.

The mechanism is one condition, duplicated. `_find_boundary_line_raw`
correctly returns the LAST content row of the input box — documented, by
design, and why callers match with `endswith()`. `_input_line_text` and
`_has_free_prompt` then require THAT row to start with the `❯` prompt glyph.
The glyph is on the box's FIRST rendered row. The two coincide only while
the box is one row tall, so any payload long enough to WRAP (roughly
400-800 chars — past that Claude Code collapses it into a
`[Pasted text #N]` placeholder, which IS handled) is structurally invisible
to the verifier and reads identically to a running turn.

Every fixture here renders the box the way a live CC 2.1.220 pane really
does — read off three real panes with `tmux capture-pane -p` (read-only,
zero keystrokes): a bordered box, `❯` + U+00A0 on the first row, greedy
word wrap, continuation rows indented.
"""

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar
import watchdog as wd

# The real gatekeeper payload from the incident: nine ticket numbers.
TICKETS = [2377, 2396, 2448, 2513, 2550, 2583, 2593, 2594, 2599]
TICK_STR = " ".join("#%d" % n for n in TICKETS)
PAYLOAD = wd.GKREQ_NUDGE % (TICK_STR, "odoo-erp")

BOX_WIDTH = 176                 # the real width of every dev1 pane
USER_DRAFT = "nechať ako je"


def render_box(buf, stashed=False):
    """Render a Claude Code input box holding `buf`, the way a live pane does.

    Bordered (`────` above and below), the prompt glyph plus a NON-BREAKING
    SPACE on the FIRST row, greedy word wrap at `BOX_WIDTH`, continuation
    rows indented by two columns. A bare box is the lone glyph row.
    """
    if not buf:
        rows = ["❯\xa0"]
    else:
        rows, cur, prefix = [], "", "❯\xa0"
        for word in buf.split(" "):
            cand = (cur + " " + word) if cur else word
            if len(prefix) + len(cand) > BOX_WIDTH:
                rows.append(prefix + cur)
                cur, prefix = word, "  "
            else:
                cur = cand
        rows.append(prefix + cur)
    ctx = "  ctx ░░  5h 25% (4h)  wk 78% (23h)"
    if stashed:
        ctx += "  " + wd.STASH_MARKER
    return "\n".join(
        ["  ✅ DONE: predošlá práca hotová", "", "✻ Brewed for 24s", ""]
        + ["─" * 60] + rows + ["─" * 60, ctx, "  ⏵⏵ bypass permissions on"]
    ) + "\n"


WRAPPED_DRAFT = render_box(PAYLOAD)
BARE = render_box("")
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")
# The #233 scar: a lone `❯` echoed in the TRANSCRIPT above a live spinner.
# Reading upward for a glyph would call this pane idle and interrupt it.
SCAR = ("❯ an old prompt line echoed back into the transcript\n"
        "  ⎿ output…\n"
        "✳ Baking… (2m · esc to interrupt)\n")
# A bordered region whose first row is NOT a prompt — an open dialog.
DIALOG = ("  ✻ Brewed for 3s\n" + "─" * 60 + "\n"
          "Do you want to proceed?\n1. Yes\n2. No\n" + "─" * 60 + "\n"
          "  ctx ░░\n")


class WrapTmux:
    """A fake tmux whose pane REALLY reacts: an input buffer that wraps, and
    Claude Code's SINGLE-SLOT prompt stash with its SILENT overwrite.

    The overwrite is modelled faithfully and deliberately: `C-s` into a
    NON-EMPTY box PARKS that box's content, replacing whatever was already in
    the slot. A fake that no-ops there cannot see a recovery path destroying
    the very draft it exists to protect.
    """

    def __init__(self, box="", stash=None, panes=None, in_mode=False,
                 type_lands=None, swallow_enter=0):
        self.buf = box
        self.stash = stash
        self.panes = panes or []
        self.in_mode = in_mode
        self.type_lands = type_lands      # None = all of it; N = only N chars
        self.swallow_enter = swallow_enter
        self.sent = []
        self.submitted = []

    def render(self):
        return render_box(self.buf, self.stash is not None)

    def _key(self, k):
        if k == "C-s":
            if self.buf:
                self.stash, self.buf = self.buf, ""      # park (overwrites!)
            elif self.stash is not None:
                self.buf, self.stash = self.stash, None  # pop
        elif k == "Enter" and self.buf:
            if self.swallow_enter > 0:
                self.swallow_enter -= 1
                return
            self.submitted.append(self.buf)
            self.buf = ""
        elif k == "BSpace" and self.buf:
            self.buf = self.buf[:-1]

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self.render()
        if "display-message" in j:
            return "1" if self.in_mode else "0"
        if argv[:2] == ["tmux", "send-keys"]:
            if "-l" in argv:
                text = argv[-1]
                self.buf += (text if self.type_lands is None
                             else text[:self.type_lands])
            else:
                for k in argv[4:]:
                    self._key(k)
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


def _nosleep(*_a, **_k):
    return None


# --------------------------------------------------------------------------- #
# 1. The verifier itself.
# --------------------------------------------------------------------------- #

class AWrappedDraftIsAnInputBox(unittest.TestCase):
    def test_the_fixture_really_wraps(self):
        self.assertGreater(len(PAYLOAD), 400, len(PAYLOAD))
        glyph_rows = [ln for ln in WRAPPED_DRAFT.splitlines()
                      if ln.startswith("❯")]
        self.assertEqual(len(glyph_rows), 1)
        boundary = wd._find_boundary_line_raw(WRAPPED_DRAFT)
        self.assertFalse(boundary.startswith("❯"),
                         "the wrapped tail must NOT carry the glyph: %r" % boundary)
        self.assertTrue(PAYLOAD.endswith(boundary), boundary)

    def test_input_line_text_reads_the_wrapped_draft(self):
        got = wd._input_line_text(WRAPPED_DRAFT)
        self.assertIsNotNone(
            got, "a wrapped draft must not read as 'there is no input box'")
        self.assertTrue(PAYLOAD.endswith(got),
                        "the documented endswith() contract: %r" % got)

    def test_has_free_prompt_sees_the_box(self):
        self.assertTrue(wd._has_free_prompt(WRAPPED_DRAFT, bare_only=False))

    def test_classify_boundary_reports_input_not_busy(self):
        kind, draft = wd._classify_boundary(WRAPPED_DRAFT)
        self.assertEqual(kind, "input", (kind, draft))
        self.assertTrue(PAYLOAD.endswith(draft), draft)

    # --- the change must be STRICTLY ADDITIVE: everything below already
    # --- answers correctly today and must keep answering the same way.

    def test_bare_only_still_refuses_a_box_holding_a_draft(self):
        self.assertFalse(wd._has_free_prompt(WRAPPED_DRAFT, bare_only=True))
        self.assertFalse(wd.pane_at_idle_prompt(WRAPPED_DRAFT))

    def test_a_bare_box_is_still_bare(self):
        self.assertEqual(wd._input_line_text(BARE), "")
        self.assertTrue(wd.pane_at_idle_prompt(BARE))
        self.assertEqual(wd._classify_boundary(BARE), ("input", ""))

    def test_a_single_row_draft_is_unchanged(self):
        cap = render_box(USER_DRAFT)
        self.assertEqual(wd._input_line_text(cap), USER_DRAFT)
        self.assertTrue(wd._has_free_prompt(cap, bare_only=False))
        self.assertFalse(wd._has_free_prompt(cap, bare_only=True))

    def test_a_busy_pane_is_still_busy(self):
        for cap in (BUSY, SCAR, DIALOG):
            self.assertIsNone(wd._input_line_text(cap), cap[:40])
            self.assertFalse(wd._has_free_prompt(cap, bare_only=False), cap[:40])
            self.assertNotEqual(wd._classify_boundary(cap)[0], "input", cap[:40])

    def test_the_233_scar_is_never_reached_by_scanning_upward(self):
        # A lone `❯` in the transcript above a live spinner must NEVER be
        # mistaken for the input box — typing there interrupts a running turn.
        self.assertFalse(wd._has_free_prompt(SCAR))
        run = WrapTmux()
        self.assertFalse(wd.deliver_with_stash("%1", PAYLOAD, run,
                                               captured=SCAR, sleep_fn=_nosleep))
        self.assertEqual(run.sent, [], "zero keystrokes into a busy pane")


# --------------------------------------------------------------------------- #
# 2. deliver_with_stash — the real entry point every job goes through.
# --------------------------------------------------------------------------- #

class DeliveringAWrappedPayload(unittest.TestCase):
    def test_a_wrapped_payload_is_delivered_around_a_foreign_draft(self):
        run = WrapTmux(box=USER_DRAFT)
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertTrue(ok, logs)
        self.assertEqual(run.submitted, [PAYLOAD], logs)
        self.assertEqual(run.stash, USER_DRAFT,
                         "the user's draft must still be parked, intact")
        self.assertEqual(run.buf, "", "nothing of ours may be left behind")

    def test_a_wrapped_payload_is_delivered_into_a_bare_box(self):
        run = WrapTmux()
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertTrue(ok, logs)
        self.assertEqual(run.submitted, [PAYLOAD], logs)

    def test_a_swallowed_submit_of_a_wrapped_payload_is_recovered(self):
        run = WrapTmux(box=USER_DRAFT, swallow_enter=1)
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertTrue(ok, logs)
        self.assertEqual(run.submitted, [PAYLOAD], logs)
        self.assertEqual(run.buf, "",
                         "a swallowed submit must never be reported as success "
                         "while the text still sits in the box")

    def test_the_stranded_text_from_the_incident_is_recoverable(self):
        # The retry the incident could never make: the pane holds our OWN
        # stranded nudge, wrapped. Before the fix `_has_free_prompt` reads it
        # as "no free prompt" and the job no-ops forever.
        run = WrapTmux(box=PAYLOAD)
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertTrue(ok, logs)
        self.assertEqual(run.submitted, [PAYLOAD], logs)


class AFailedDeliveryLeavesTheBoxAsItFoundIt(unittest.TestCase):
    """Acceptance 3 — a path that has already typed must undo its own text."""

    def test_a_bare_box_is_left_bare_when_the_type_does_not_verify(self):
        run = WrapTmux(type_lands=120)          # a TRUNCATED type (#36 class)
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertFalse(ok, logs)
        self.assertEqual(run.submitted, [], logs)
        self.assertEqual(run.buf, "",
                         "our own text was left sitting in the prompt: %r"
                         % run.buf[:80])
        self.assertTrue(any("undone" in ln for ln in logs), logs)

    def test_a_parked_draft_is_never_overwritten_by_our_own_text(self):
        run = WrapTmux(box=USER_DRAFT, type_lands=120)
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertFalse(ok, logs)
        self.assertNotIn(PAYLOAD[:60], str(run.stash),
                         "a `C-s` fired while our text was still in the box "
                         "parks OUR text over the user's parked draft")
        self.assertEqual(run.buf, USER_DRAFT,
                         "the user's draft must be back in the box: %r" % run.buf)
        self.assertIsNone(run.stash, run.stash)
        self.assertEqual(run.submitted, [], logs)

    def test_an_unprovable_append_sends_no_further_keystrokes(self):
        # UNRESOLVED (the box shows content our C-s did not park) plus a
        # payload long enough to wrap: the append signature is unprovable from
        # the viewport, so protecting the possible draft wins — no backspaces.
        run = WrapTmux(box=USER_DRAFT, stash="už niečo parkuje")
        logs = []
        ok = wd.deliver_with_stash("%1", PAYLOAD, run, logs=logs,
                                   sleep_fn=_nosleep)
        self.assertFalse(ok, logs)
        keys = [a for a in run.sent if "send-keys" in " ".join(a)]
        self.assertEqual(keys, [], "an occupied slot aborts before typing")
        self.assertEqual(run.stash, "už niečo parkuje", run.stash)
        self.assertTrue(logs, "the abort must say why")


# --------------------------------------------------------------------------- #
# 3. Job 11 — a failed delivery must be VISIBLE.
# --------------------------------------------------------------------------- #

class GkRequestReportsAFailedDelivery(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / (statusbar.cwd_key(self.root) + ".json")).write_text(
            '{"open": 1, "name": "demo", "root": "%s", "ts": %d}'
            % (self.root, int(time.time())))
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def test_a_delivery_that_cannot_run_logs_the_reason(self):
        # Stash slot already occupied → the one genuine decline. Today the
        # caller just `continue`s and nothing anywhere records it.
        run = WrapTmux(box=USER_DRAFT, stash="parked already",
                       panes=[("%1", self.root)])
        logs = wd.gk_request_backstop(
            time.time(), run, {}, self._send, home=self.home,
            gh_fetch=lambda root: TICKETS, user="gatekeeper")
        self.assertTrue(any("gkreq-nudge-failed" in ln for ln in logs),
                        "a failed delivery must be visible: %r" % logs)
        self.assertTrue(any("slot occupied" in ln for ln in logs),
                        "the log must name the REASON: %r" % logs)

    def test_a_wrapped_stranded_nudge_is_re_delivered(self):
        # The incident's own retry: the pane holds our stranded nudge.
        run = WrapTmux(box=PAYLOAD, panes=[("%1", self.root)])
        logs = wd.gk_request_backstop(
            time.time(), run, {}, self._send, home=self.home,
            gh_fetch=lambda root: TICKETS, user="gatekeeper")
        self.assertTrue(any("gkreq-nudge " in ln for ln in logs), logs)
        self.assertTrue(run.submitted, run.sent)


# --------------------------------------------------------------------------- #
# 4. Job 10 — "cannot read the input line" is not "there is no draft".
# --------------------------------------------------------------------------- #

class PromptWedgeAndTheUnreadableBox(unittest.TestCase):
    def setUp(self):
        self.pings = []
        self.now = 1.0e9

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _sweep(self, state, captured, run=None, tmtime=0, waiting=True):
        return wd.prompt_wedge_check(
            self.now, state, "%1", captured, tmtime, "zbynek", "demo",
            self._send, run=run, waiting=waiting)

    def test_an_unreadable_capture_does_not_forget_a_tracked_draft(self):
        for cap in (BUSY, SCAR, DIALOG):
            state = {"pwedge:%1": {"hash": "deadbeef", "n": 1, "pinged": False}}
            self._sweep(state, cap)
            self.assertIn(
                "pwedge:%1", state,
                "an unreadable sweep must neither advance nor FORGET the "
                "episode (%s)" % cap[:30])

    def test_a_provably_bare_box_still_forgets_the_episode(self):
        state = {"pwedge:%1": {"hash": "deadbeef", "n": 1, "pinged": False}}
        self._sweep(state, BARE)
        self.assertNotIn("pwedge:%1", state)

    def test_a_wrapped_machine_nudge_is_submitted_not_pinged(self):
        state, run, logs = {}, WrapTmux(box=PAYLOAD), []
        for _ in range(wd.PWEDGE_SWEEPS):
            logs = self._sweep(state, render_box(PAYLOAD), run=run)
        self.assertTrue(any("machine-nudge submit" in ln for ln in logs),
                        "a WRAPPED machine nudge carries its prefix on the "
                        "box's HEAD row, never on the tail: %r" % logs)
        self.assertFalse(self.pings, self.pings)
        self.assertTrue(run.submitted, run.sent)

    def test_a_wrapped_user_draft_is_tracked_and_pinged_with_its_start(self):
        draft = ("toto je moja dlhá poznámka ktorú som rozpísal a ešte som ju "
                 "neodoslal, lebo som si nebol istý formuláciou a chcel som si "
                 "ju najprv poriadne premyslieť, kým ju pošlem ďalej kolegom")
        self.assertGreater(len(draft), BOX_WIDTH)
        cap, state, logs = render_box(draft), {}, []
        for _ in range(wd.PWEDGE_SWEEPS):
            logs = self._sweep(state, cap, tmtime=self.now - 3600)
        self.assertTrue(any("prompt-wedge ping" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertIn(draft[:30], self.pings[0][0],
                      "the ping must quote the START of the draft, not its "
                      "wrapped tail: %r" % self.pings[0][0])


if __name__ == "__main__":
    unittest.main()
