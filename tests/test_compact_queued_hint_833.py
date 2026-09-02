"""#833 — a queued `/compact` was classified `sent` / `--status NONE` because
`_compact_post_send_classify` did a SINGLE re-capture: when the queued row (and
CC's `Press up to edit [N] queued messages` box hint) render a beat AFTER the
submit, the one-shot read of a still-bare pane at t0 falsely concludes `sent`
(the owner's t0→t+1s RACE). A false `sent`/`NONE` lets the #822 Step-5 doctrine
dispatch the next batch OVER a still-queued compact, which then drains at the
next accepted Stop with live lanes (the #822/#723 break).

Fix: (1) `_compact_post_send_classify` BOUNDED-re-reads (a few captures, spaced),
concluding `sent` only if NO positive queued/hint/busy signal appears across
all; (2) a new `_pane_shows_queued_messages_hint` reads the queued-messages hint
straight off the input-box boundary (race-proof, and catches the case where the
`❯ /compact` row has scrolled off-screen), wired into the classifier AND into
`compact_queued_in_pane` (the `--status` live gate).

RED against the pre-#833 tree: `_pane_shows_queued_messages_hint` does not exist;
the race classifies `sent`; and `compact_queued_in_pane` on a hint-only pane is
False. GREEN after: QUEUED in every queued case, `sent` only when genuinely idle
across all re-reads. Fixtures are captured-pane-shaped renders (the #822 combined
`✔ Update installed … ◎ /goal active` banner form)."""

import unittest
import unittest.mock as m

import watchdog as wd
from watchdog import compact

_SEP = "─" * 74

# The fully-rendered queued state: a properly-structured idle box (boundary
# 'input') showing the `Press up to edit queued messages` hint, with the queued
# `❯ /compact` row above (indented, under the combined update-banner + goal
# indicator — the exact #833 render shape).
QUEUED_HINT = (
    "✅ DONE: batch merged\n"
    "\n"
    "◯ Goal not yet met… continuing\n"
    "\n"
    "  ❯ /compact\n"
    "                                                                          "
    "                        ✔ Update installed · Restart to update◎ /goal active (14m)\n"
    + _SEP + " ultracode ─\n"
    "❯ Press up to edit queued messages\n"
    + _SEP + "\n"
    "  5h 7%(4h)  fable  I 1 · W 10 · gk 2  ctx 448K ~$0.45  caveman\n"
)

# The box shows the hint but NO `❯ /compact` row is on screen (several queued,
# the row scrolled off) — the row walk sees nothing, only the box hint proves
# the queue.
HINT_ONLY = (
    "● Predošlá práca hotová.\n"
    "\n"
    + _SEP + " ultracode ─\n"
    "❯ Press up to edit 2 queued messages\n"
    + _SEP + "\n"
    "  5h 7%(4h)  fable  I 1  ctx 448K  caveman\n"
)

# A genuinely idle bare box (no queued anything) — the t0 pane of the race.
BARE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


def _seq_run(captures):
    """A tmux run() whose successive `capture-pane` calls return `captures` in
    order (repeating the last), so a bounded re-read observes a sequence."""
    state = {"n": 0}

    def run(argv, timeout=8):
        j = " ".join(argv)
        if "capture-pane" in j:
            i = min(state["n"], len(captures) - 1)
            state["n"] += 1
            return captures[i]
        if "display-message" in j:
            return "0" if argv[-1] == "#{pane_in_mode}" else "s:0.0"
        if "list-panes" in j:
            return "%1\tclaude\t/x\t111"
        return ""
    return run


class QueuedMessagesHint833(unittest.TestCase):
    def test_hint_predicate_detects_the_box_hint(self):
        self.assertTrue(wd._pane_shows_queued_messages_hint(QUEUED_HINT))
        self.assertTrue(wd._pane_shows_queued_messages_hint(HINT_ONLY))

    def test_hint_predicate_ignores_a_bare_idle_box(self):
        self.assertFalse(wd._pane_shows_queued_messages_hint(BARE))
        # and the box the hint sits in still classifies as an idle input box —
        # exactly why the hint is needed (the box looks idle to the classifier).
        self.assertEqual(wd._classify_boundary(HINT_ONLY), ("input", ""))


class PostSendRace833(unittest.TestCase):
    # Patch the real re-read sleep to a no-op so the bounded loop is instant,
    # and call the classifier WITHOUT an explicit sleep_fn — so this is a pure
    # BEHAVIOUR test: on the pre-#833 single-read classifier the t0 bare pane
    # reads "sent" (RED); on the bounded re-read it re-captures and sees the
    # queued render (GREEN).
    def setUp(self):
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def test_race_t0_bare_then_queued_row_classifies_queued(self):
        # THE core race: the queued render lands one capture late. A single-read
        # classifier reads the t0 bare pane and wrongly says "sent".
        v = compact._compact_post_send_classify("%1", _seq_run([BARE, QUEUED_HINT]))
        self.assertEqual(v, "queued")

    def test_hint_only_render_classifies_queued(self):
        # the box hint alone (no `❯ /compact` row on screen) is enough.
        v = compact._compact_post_send_classify("%1", _seq_run([HINT_ONLY]))
        self.assertEqual(v, "queued")

    def test_genuinely_idle_stays_sent(self):
        # fail-safe boundary: a truly idle pane across EVERY re-read is "sent",
        # never a false QUEUED (a false QUEUED costs a boundary-hold turn, but a
        # false NONE would break the batch gate — so we must not over-fire here).
        v = compact._compact_post_send_classify("%1", _seq_run([BARE]))
        self.assertEqual(v, "sent")


class StatusQueuedHint833(unittest.TestCase):
    def test_status_gate_sees_the_hint_when_no_row_is_visible(self):
        # `compact_queued_in_pane` (the `--status` live gate) must report QUEUED
        # off the box hint even when the `❯ /compact` row is not on screen.
        def run(argv, timeout=8):
            return HINT_ONLY if "capture-pane" in " ".join(argv) else ""
        self.assertTrue(compact.compact_queued_in_pane("%1", run))

    def test_status_gate_bare_is_not_queued(self):
        def run(argv, timeout=8):
            return BARE if "capture-pane" in " ".join(argv) else ""
        self.assertFalse(compact.compact_queued_in_pane("%1", run))


if __name__ == "__main__":
    unittest.main()
