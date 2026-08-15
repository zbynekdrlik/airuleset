"""#84 (2026-07-26) — a turn that runs for HOURS is a fault state of its own,
and a pane that already has a `/compact` queued must never get a second one.

Live incident, gatekeeper, 2026-07-26 15:43 — the pane read:

    · Germinating… (2h 40m 36s · ↓ 69.3k tokens)

      ❯ /compact
      ❯ /compact
      ❯ /compact
    ────────────────────────────
    ❯ Press up to edit queued messages
    ────────────────────────────
      ctx 398K · ~$0.20/tah                          ◎ /goal active (2h)

Two independent defects, both fixed here:

1. QUEUED-COMPACT GUARD. Three `/compact` sat queued. Claude Code drains the
   type-ahead queue only where a turn actually ENDS, so they all fired
   back-to-back when the turn finally broke — the first really compacted, the
   rest answered "Not enough messages to compact". That is the duplicate
   `/compact` spam the user keeps reporting. The cheap fix is at the SOURCE:
   a pane whose capture ALREADY shows a queued `❯ /compact` gets no second
   one, from ANY sender (`deliver_compact_now`, jobs 14/15/17). It composes
   with — never replaces — the shared claim (#78) and proc-fingerprint
   (#82/#83) mechanics.

2. LONG-TURN DETECTION (job 21). While one turn runs for hours nothing
   compacts, no question is delivered and keystrokes just pile up. The
   watchdog could only see CONTEXT before; the turn's own duration is now its
   own signal.

   The forensic read of that session's transcript (posted on #84) is what
   fixes the DESIGN here: CC logged those 2h40m as THREE internal turns, each
   ended by the armed `/goal` loop's Stop hook REJECTING the stop and forcing
   continuation — and the input queue drained at NONE of those boundaries,
   only at the user's manual interrupt. So a transcript-boundary-based
   detector would have missed this incident entirely. The PANE's own elapsed
   label is the authoritative signal: it runs from the last genuine external
   input, which is exactly "how long the queue has been unable to drain".
   (The same read also DISPROVED the ticket's original hypothesis — no
   foreground subagent dispatch was involved; every `Agent` call returned
   async in ~100 ms and none was in flight during the long stretch.)
"""

import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd


# --------------------------------------------------------------------------- #
# Fixtures — real pane shapes
# --------------------------------------------------------------------------- #

# The live #84 panel: a long-running spinner, three queued `/compact` rows, a
# separator-bounded input box holding CC's greyed queued-messages placeholder.
LT_LIVE_QUEUED_CAP = (
    "· Germinating… (2h 40m 36s · ↓ 69.3k tokens)\n"
    "\n"
    "  ❯ /compact\n"
    "  ❯ /compact\n"
    "  ❯ /compact\n"
    "────────────────────────────\n"
    "❯ Press up to edit queued messages\n"
    "────────────────────────────\n"
    "  ctx 398K · ~$0.20/tah                          ◎ /goal active (2h)\n")

# One queued `/compact`, borderless box (the shape most of this repo's older
# fixtures use).
LT_ONE_QUEUED_CAP = (
    "● Pracujem na tickete.\n"
    "✳ Baking… (3m 12s · esc to interrupt)\n"
    "  ❯ /compact\n"
    "❯\n"
    "  ctx ███░  caveman:lite\n")

# Busy pane, NOTHING queued — a `/compact` here is legitimate.
LT_BUSY_NO_QUEUE_CAP = (
    "● Pracujem na tickete.\n"
    "✳ Baking… (3m 12s · esc to interrupt)\n"
    "❯\n"
    "  ctx ███░  caveman:lite\n")

# Plain idle pane — no spinner at all.
LT_IDLE_CAP = "● Hotovo.\n❯\n  ctx ███░  caveman:lite\n"

# A long-running turn with NOTHING queued (the pure job-21 case).
LT_LONG_TURN_CAP = (
    "● Čakám na CI.\n"
    "· Germinating… (2h 40m 36s · ↓ 69.3k tokens)\n"
    "❯\n"
    "  ctx 398K · ~$0.20/tah                          ◎ /goal active (2h)\n")

LT_SHORT_TURN_CAP = (
    "● Čakám na CI.\n"
    "✳ Baking… (4m 2s · ↑ 1.2k tokens · esc to interrupt)\n"
    "❯\n"
    "  ctx 120K\n")

# #36 lesson — the agent strip renders BELOW the input box and its activity
# label is ARBITRARY model-generated text. A worker whose label literally
# contains "/compact" must NEVER read as a queued command.
LT_AGENT_STRIP_MENTIONS_COMPACT_CAP = (
    "● Pracujem.\n"
    "✳ Baking… (30s · esc to interrupt)\n"
    "❯\n"
    "● main\n"
    "◯ autopilot-worker  running /compact regression tests\n"
    "❯ ◯ watchdog-worker  ❯ /compact guard\n"
    "  ↑/↓ to select · Enter to view\n"
    "  ctx ███░\n")

# A pane whose CONVERSATION quotes the panel (a session working on THIS very
# ticket). The quoted rows are not adjacent to the input box — real content
# sits below them — so they are not the live queue.
LT_CONVERSATION_QUOTES_PANEL_CAP = (
    "● Ticket #84 ukazuje tento panel:\n"
    "❯ /compact\n"
    "❯ /compact\n"
    "● Takže guard musí byť presný.\n"
    "❯\n"
    "  ctx ███░\n")

# A queued row that is some OTHER command — not a reason to skip a /compact.
LT_OTHER_QUEUED_CAP = (
    "● Pracujem.\n"
    "✳ Baking… (30s · esc to interrupt)\n"
    "  ❯ pokracuj prosim\n"
    "❯\n"
    "  ctx ███░\n")


class LTFakeTmux:
    """Minimal `run` fake — same shape as CompactFakeTmux (test_compact_request)
    but for a job that is handed `panes_by_sid` directly and never captures on
    its own, so there is no first-call special case (see this repo's CLAUDE.md
    bullet on which fake special-cases capture-pane call #1)."""

    def __init__(self, captured="", in_mode=False):
        self.captured = captured
        self.in_mode = in_mode
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            return self.captured
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]


# --------------------------------------------------------------------------- #
# 1. The queued-compact detector itself
# --------------------------------------------------------------------------- #

class TestPaneHasQueuedCompact(unittest.TestCase):

    def test_the_live_incident_panel_reads_as_queued(self):
        self.assertTrue(wd._pane_has_queued_compact(LT_LIVE_QUEUED_CAP))

    def test_a_single_queued_compact_reads_as_queued(self):
        self.assertTrue(wd._pane_has_queued_compact(LT_ONE_QUEUED_CAP))

    def test_busy_pane_with_nothing_queued_is_not_queued(self):
        self.assertFalse(wd._pane_has_queued_compact(LT_BUSY_NO_QUEUE_CAP))

    def test_idle_pane_is_not_queued(self):
        self.assertFalse(wd._pane_has_queued_compact(LT_IDLE_CAP))

    def test_empty_capture_is_not_queued(self):
        self.assertFalse(wd._pane_has_queued_compact(""))
        self.assertFalse(wd._pane_has_queued_compact(None))

    def test_agent_strip_row_containing_the_literal_text_is_not_queued(self):
        # #36 — the strip renders BELOW the box with arbitrary label text.
        self.assertFalse(
            wd._pane_has_queued_compact(LT_AGENT_STRIP_MENTIONS_COMPACT_CAP))

    def test_conversation_quoting_the_panel_is_not_queued(self):
        self.assertFalse(
            wd._pane_has_queued_compact(LT_CONVERSATION_QUOTES_PANEL_CAP))

    def test_a_different_queued_command_is_not_a_queued_compact(self):
        self.assertFalse(wd._pane_has_queued_compact(LT_OTHER_QUEUED_CAP))


# The old "2. Every /compact sender honours the guard" section
# (TestQueuedCompactGuardJob14 / TestQueuedCompactGuardDeliverNow, driving
# job 14's `compact_ticket_boundary` and the synchronous `deliver_compact_
# now`) is SUPERSEDED by #402's compact collapse — both functions are gone,
# replaced by one `watchdog.compact.deliver_compact`. The identical
# already-queued-compact behaviour is now locked directly against that
# function in `tests/test_compact.py::TestDeliverCompact.
# test_already_queued_compact_is_handled_not_resent` (and the periodic-sweep
# equivalent, `TestCompactSweep`) — `_pane_has_queued_compact` itself (the
# primitive `TestPaneHasQueuedCompact` above locks) is UNCHANGED and still
# lives in `watchdog/pane_text.py` (moved there in #433 step 3), verbatim.


# --------------------------------------------------------------------------- #
# 3. Reading the turn's elapsed time off the pane
# --------------------------------------------------------------------------- #

class TestPaneTurnElapsed(unittest.TestCase):

    def test_the_live_incident_elapsed_is_parsed(self):
        # 2h 40m 36s
        self.assertEqual(wd.pane_turn_elapsed(LT_LONG_TURN_CAP),
                         2 * 3600 + 40 * 60 + 36)

    def test_elapsed_is_parsed_even_with_queued_rows_between(self):
        # the live panel: the spinner is NOT the last row above the box
        self.assertEqual(wd.pane_turn_elapsed(LT_LIVE_QUEUED_CAP),
                         2 * 3600 + 40 * 60 + 36)

    def test_minutes_and_seconds(self):
        self.assertEqual(wd.pane_turn_elapsed(LT_SHORT_TURN_CAP), 4 * 60 + 2)

    def test_seconds_only(self):
        cap = "● x\n✳ Baking… (7s · esc to interrupt)\n❯\n  ctx ███░\n"
        self.assertEqual(wd.pane_turn_elapsed(cap), 7)

    # CC drops the seconds component once a turn is a few minutes old — the
    # `(2m · esc to interrupt)` form appears verbatim in this repo's own
    # live-captured fixtures. A seconds-mandatory parser silently reports
    # "no turn running" for exactly the turns that are getting LONG, which is
    # the one case job 21 exists for. Found by validating the parser against
    # every real spinner string in tests/ instead of only hand-written ones.
    def test_minutes_only_no_seconds_component(self):
        cap = "● x\n✳ Baking… (2m · esc to interrupt)\n❯\n  ctx ███░\n"
        self.assertEqual(wd.pane_turn_elapsed(cap), 120)

    def test_hours_and_minutes_no_seconds_component(self):
        cap = "● x\n· Germinating… (3h 5m · ↓ 12k tokens)\n❯\n  ctx ███░\n"
        self.assertEqual(wd.pane_turn_elapsed(cap), 3 * 3600 + 5 * 60)

    def test_hours_only(self):
        cap = "● x\n· Germinating… (2h · ↓ 12k tokens)\n❯\n  ctx ███░\n"
        self.assertEqual(wd.pane_turn_elapsed(cap), 7200)

    def test_spinner_with_no_duration_at_all_is_not_a_timed_turn(self):
        # `✻ Herding… (esc to interrupt)` — a real fixture shape. No elapsed
        # time is claimed, so none may be invented (0s would read as "a turn
        # that just started" and reset the incident's identity).
        cap = "● x\n✻ Herding… (esc to interrupt)\n❯\n  ctx ███░\n"
        self.assertIsNone(wd.pane_turn_elapsed(cap))

    def test_a_parenthesised_non_duration_is_not_an_elapsed_time(self):
        cap = "● x\n✻ Reading… (3 messages)\n❯\n  ctx ███░\n"
        self.assertIsNone(wd.pane_turn_elapsed(cap))

    def test_idle_pane_has_no_running_turn(self):
        self.assertIsNone(wd.pane_turn_elapsed(LT_IDLE_CAP))

    def test_empty_capture_has_no_running_turn(self):
        self.assertIsNone(wd.pane_turn_elapsed(""))
        self.assertIsNone(wd.pane_turn_elapsed(None))

    def test_prose_mentioning_a_duration_is_not_a_spinner(self):
        cap = ("● Ten tah bezal (2h 40m 36s) a nic sa nekompaktovalo.\n"
               "❯\n  ctx ███░\n")
        self.assertIsNone(wd.pane_turn_elapsed(cap))


# --------------------------------------------------------------------------- #
# 4. Job 21 — long_turn_watch
# --------------------------------------------------------------------------- #

class TestLongTurnWatch(unittest.TestCase):

    SID = "sess-lt21"
    PANE = "%9"

    def _go(self, captured, state=None, now=None, threshold=1800, sends=None):
        sends = [] if sends is None else sends

        def send_fn(msg, owner=None, dedup_key=None, dry_run=False):
            sends.append({"msg": msg, "owner": owner, "dedup_key": dedup_key})
            return "sent"

        state = {} if state is None else state
        logs = wd.long_turn_watch(
            time.time() if now is None else now, LTFakeTmux(captured), state,
            {self.SID: (self.PANE, captured)}, send_fn=send_fn,
            threshold=threshold)
        return logs, state, sends

    def test_a_turn_over_the_threshold_is_detected_and_logged(self):
        logs, _state, _sends = self._go(LT_LONG_TURN_CAP)
        self.assertTrue(any("long-turn" in ln for ln in logs), logs)
        self.assertTrue(any("9636" in ln for ln in logs), logs)

    def test_a_turn_over_the_threshold_pings_once(self):
        _logs, _state, sends = self._go(LT_LONG_TURN_CAP)
        self.assertEqual(len(sends), 1, sends)

    def test_a_short_turn_is_not_reported(self):
        logs, _state, sends = self._go(LT_SHORT_TURN_CAP)
        self.assertEqual(sends, [])
        self.assertFalse(any("long-turn PING" in ln for ln in logs), logs)

    def test_an_idle_pane_is_not_reported(self):
        logs, _state, sends = self._go(LT_IDLE_CAP)
        self.assertEqual(sends, [])
        self.assertEqual(logs, [])

    def test_the_same_turn_is_not_pinged_twice(self):
        now = 1_700_000_000.0
        _l, state, sends = self._go(LT_LONG_TURN_CAP, now=now)
        self.assertEqual(len(sends), 1)
        # next sweep, 60s later — SAME turn (its start is unchanged)
        cap2 = LT_LONG_TURN_CAP.replace("2h 40m 36s", "2h 41m 36s")
        logs2, state, sends = self._go(cap2, state=state, now=now + 60,
                                       sends=sends)
        self.assertEqual(len(sends), 1, "one ping per incident, not per sweep")
        # …but it is still LOGGED unconditionally, every sweep
        self.assertTrue(any("long-turn" in ln for ln in logs2), logs2)

    def test_a_new_long_turn_pings_again(self):
        now = 1_700_000_000.0
        _l, state, sends = self._go(LT_LONG_TURN_CAP, now=now)
        self.assertEqual(len(sends), 1)
        # much later: a NEW turn, itself already over the threshold — its start
        # is hours away from the previous one, so it is a distinct incident.
        _l2, state, sends = self._go(LT_LONG_TURN_CAP, state=state,
                                     now=now + 40_000, sends=sends)
        self.assertEqual(len(sends), 2, sends)

    def test_the_ping_is_deduped_per_turn_by_key(self):
        _l, _state, sends = self._go(LT_LONG_TURN_CAP)
        self.assertTrue(sends[0]["dedup_key"].startswith("long-turn:"))
        self.assertIn(self.SID, sends[0]["dedup_key"])

    def test_the_ping_text_is_plain_slovak_with_the_elapsed_time(self):
        _l, _state, sends = self._go(LT_LONG_TURN_CAP)
        msg = sends[0]["msg"]
        self.assertIn("2h", msg)
        for jargon in ("threshold", "elapsed", "watchdog", "sid="):
            self.assertNotIn(jargon, msg)

    def test_dry_run_logs_but_never_pings(self):
        state = {}
        logs = wd.long_turn_watch(
            time.time(), LTFakeTmux(LT_LONG_TURN_CAP), state,
            {self.SID: (self.PANE, LT_LONG_TURN_CAP)},
            send_fn=lambda *a, **k: self.fail("dry_run must not send"),
            dry_run=True)
        self.assertTrue(any("long-turn" in ln for ln in logs), logs)

    def test_threshold_is_env_overridable(self):
        with m.patch.dict("os.environ", {"AIRULESET_LONG_TURN_S": "60"}):
            _logs, _state, sends = self._go(LT_SHORT_TURN_CAP, threshold=None)
        self.assertEqual(len(sends), 1, "a 4m turn is long under a 60s threshold")


# --------------------------------------------------------------------------- #
# 5. run_once wiring — "wired = on", same convention as jobs 13/14/16/18/19/20
# --------------------------------------------------------------------------- #

class TestLongTurnWiring(unittest.TestCase):

    def _run(self, **kw):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        with m.patch.object(wd, "list_claude_panes", return_value=[]), \
             m.patch.object(wd, "long_turn_watch",
                            return_value=["long-turn stub"]) as job:
            wd.run_once(now=time.time(), run=LTFakeTmux(""),
                        send_fn=lambda *a, **k: "sent",
                        projects_dir=Path(d.name) / "projects",
                        state_path=str(Path(d.name) / "state.json"), **kw)
        return job

    def test_not_wired_means_the_job_never_runs(self):
        self.assertFalse(self._run().called)

    def test_wired_means_the_job_runs(self):
        self.assertTrue(self._run(long_turn_enabled=True).called)


class TestRunOnceDocstringNumbering(unittest.TestCase):
    """`run_once`'s docstring is this repo's SINGLE SOURCE OF TRUTH for job
    numbering (CLAUDE.md) — a new job that is not in it is undiscoverable."""

    def test_job_21_is_documented(self):
        doc = wd.run_once.__doc__
        self.assertIn("(21)", doc)
        self.assertIn("long_turn_watch", doc)


if __name__ == "__main__":
    unittest.main()
