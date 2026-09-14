"""#1022 - the wedge (job 10) must NEVER submit a MACHINE draft; it janitor-
CLEARS it. Only a USER-authored wedged draft is ever submitted.

Decision of record (#1002, supervisor 2026-09-13): a nudge the watchdog itself
typed (a goal rider / continue / cross-stream nudge) whose Enter was swallowed
sits stuck in the box. ``watchdog/wedge.py`` classifies it MACHINE and SUBMITS it
via ``keys(kind="wedge")`` - a RECOVERY keystroke that runs even under the #994/
#1023 kill switch. Submitting it is a nudge through the back door: exactly what
the switch forbids. The #1002/#1023 keystroke primitive already knows what it
typed; the fix RECORDS every machine nudge the primitive types (``_type_literal``
with a machine ``nudge=`` identity, not ``user_authored``) into
``state["nudge_typed"]`` and has the wedge CONSULT that record: a wedged draft
matching a recorded machine nudge is janitor-CLEARED (``_janitor_clear_box``),
never Entered - regardless of the switch state. A draft with NO machine record
(the owner's own Discord reply, a stash-slot owner draft) is still submitted
(recovery preserved). The record is dropped on a confirmed submit
(``_janitor_clear_watch``) and on a janitor clear.

Drives the real job through the same fake-tmux harness the sibling wedge /
keystroke-primitive suites use.
"""
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd  # noqa: E402
from watchdog import stash as _stash  # noqa: E402

PID = "%1"

NUDGE_TEXT = "nudge: queue-arrival - nove tikety v backlogu, spusti dalsiu varku"
MACHINE_PANE = ("✱ Waiting for 1 background agent to finish\n"
                "──── ultracode ─\n"
                "❯\xa0" + NUDGE_TEXT + "\n"
                "────\n"
                "  ctx ██░░  caveman\n")

GENERIC_TEXT = "continue"
GENERIC_PANE = ("✳ hotovo - suhrn turnu\n"
                "──── ultracode ─\n"
                "❯\xa0" + GENERIC_TEXT + "\n"
                "────\n"
                "  ctx ██░░  caveman\n")

USER_TEXT = "toto je moja vlastna rozpisana sprava ktoru nechcem zmazat"
USER_PANE = ("✳ hotovo - suhrn turnu\n"
             "──── ultracode ─\n"
             "❯\xa0" + USER_TEXT + "\n"
             "────\n"
             "  ctx ██░░  caveman\n")

# A bare box capture (`_input_line_text` == "") - what the janitor clear's own
# re-captures read, so `_janitor_clear_box` converges to True after the Escape.
BARE_CAPTURE = ("──── ultracode ─\n"
                "❯\xa0\n"
                "────\n")


def _seed(state, pid, text, kind, now):
    """Seed the machine-nudge record exactly as `_type_literal` will write it."""
    state.setdefault("nudge_typed", {})[pid] = {
        "head": text[:160], "tail": text[-160:], "kind": kind, "ts": now}


def _mkrun():
    """Fake tmux `run`: bare capture (so the clear converges), never in mode."""
    calls = []

    def run(argv, timeout=8):
        calls.append(list(argv))
        j = " ".join(map(str, argv))
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return BARE_CAPTURE
        return ""
    run.calls = calls
    return run


# A capture with NO locatable input box (running-turn spinner) -> the janitor
# clear's re-capture reads None and `_janitor_clear_box` returns False fast
# (no BSpace loop, no sleep) -> the clear PERSISTENTLY FAILS.
UNCLEARABLE_CAPTURE = "✻ Baking... (2m · esc to interrupt)\n"


def _mkrun_unclearable():
    """Fake tmux `run` whose janitor-clear re-capture is unreadable, so every
    clear attempt FAILS (returns False) and the record is never dropped."""
    calls = []

    def run(argv, timeout=8):
        calls.append(list(argv))
        j = " ".join(map(str, argv))
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return UNCLEARABLE_CAPTURE
        return ""
    run.calls = calls
    return run


def _enters(run):
    return [a for a in run.calls if a[:2] == ["tmux", "send-keys"] and a[-1] == "Enter"]


def _sweep2(state, pane, run, now, send=None):
    """Two sweeps (PWEDGE_SWEEPS=2): first records the hash, second acts."""
    send = send or (lambda *a, **k: "sent")
    wd.prompt_wedge_check(now, state, PID, pane, now, "zbynek", "airuleset",
                          send, run=run)
    return wd.prompt_wedge_check(now + 70, state, PID, pane, now, "zbynek",
                                 "airuleset", send, run=run)


class TestMachineDraftJanitorCleared(unittest.TestCase):
    def test_recorded_prefixed_nudge_is_cleared_not_submitted_at_off(self):
        state = {}
        now = time.time()
        _seed(state, PID, NUDGE_TEXT, "queue-arrival", now)
        run = _mkrun()
        logs = _sweep2(state, MACHINE_PANE, run, now)
        self.assertEqual(_enters(run), [],
                         "a machine draft must NEVER be Entered: %r" % run.calls)
        self.assertTrue(any("janitor-clear" in ln for ln in logs),
                        "expected a janitor-clear journal line: %r" % logs)
        self.assertTrue(any("machine draft" in ln for ln in logs), logs)
        self.assertNotIn(PID, state.get("nudge_typed", {}),
                         "the record must be dropped on a janitor clear")

    def test_recorded_generic_nudge_without_prefix_is_cleared(self):
        # A GATED machine nudge whose text the prefix heuristics do NOT
        # recognize ("stuck-check"-style, a subagent-stuck rider): ONLY the
        # record identifies it as ours. It must be cleared, not left pinged.
        state = {}
        now = time.time()
        _seed(state, PID, GENERIC_TEXT, "subagent-stuck", now)
        run = _mkrun()
        logs = _sweep2(state, GENERIC_PANE, run, now)
        self.assertEqual(_enters(run), [], run.calls)
        self.assertTrue(any("janitor-clear" in ln for ln in logs),
                        "a recorded generic nudge must be janitor-cleared: %r" % logs)

    def test_machine_draft_cleared_even_when_nudges_on(self):
        state = {}
        now = time.time()
        _seed(state, PID, NUDGE_TEXT, "queue-arrival", now)
        run = _mkrun()
        old = os.environ.get("AIRULESET_TEST_IGNORE_DISABLE")
        os.environ["AIRULESET_TEST_IGNORE_DISABLE"] = "1"
        try:
            logs = _sweep2(state, MACHINE_PANE, run, now)
        finally:
            if old is None:
                os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
            else:
                os.environ["AIRULESET_TEST_IGNORE_DISABLE"] = old
        self.assertEqual(_enters(run), [], run.calls)
        self.assertTrue(any("janitor-clear" in ln for ln in logs), logs)

    def test_persistently_unclearable_machine_draft_never_expires_to_submit(self):
        # MAJOR-1 (review 2): a matched machine nudge whose janitor-clear keeps
        # FAILING must have its record refreshed on every match, so it NEVER
        # TTL-expires while still wedged. Otherwise at t + _NUDGE_TYPED_TTL_S the
        # record lapses, `machine` recomputes True via the `nudge:` prefix, and
        # the wedge SUBMITS it (Escape+Enter) under the kill switch = the exact
        # #1022 back door, reopened after the TTL.
        state = {}
        t0 = time.time()
        _seed(state, PID, NUDGE_TEXT, "queue-arrival", t0)
        run = _mkrun_unclearable()
        send = lambda *a, **k: "sent"  # noqa: E731
        t = t0
        # Sweep across well beyond the TTL (each pair = one 2-sweep act cycle),
        # advancing the clock by a TTL-fraction each cycle so cumulative >> TTL.
        step = wd._NUDGE_TYPED_TTL_S // 6
        for _ in range(24):
            wd.prompt_wedge_check(t, state, PID, MACHINE_PANE, t0, "zbynek",
                                  "airuleset", send, run=run)
            t += step
            wd.prompt_wedge_check(t, state, PID, MACHINE_PANE, t0, "zbynek",
                                  "airuleset", send, run=run)
            t += 70
        self.assertEqual(
            _enters(run), [],
            "a persistently-unclearable machine nudge must NEVER be submitted, "
            "even past the record TTL (the back door must stay shut): %r" % run.calls)
        self.assertIn(PID, state.get("nudge_typed", {}),
                      "the record must be refreshed on each match, never expire while wedged")

    def test_user_authored_draft_still_submitted(self):
        state = {}
        now = time.time()
        wd._record_dreply_typed(state, PID, USER_TEXT, now)
        run = _mkrun()
        logs = _sweep2(state, USER_PANE, run, now)
        self.assertEqual(len(_enters(run)), 1,
                         "the owner's own reply must still be submitted: %r" % run.calls)
        self.assertFalse(any("janitor-clear" in ln for ln in logs),
                         "an owner-authored draft must NOT be cleared: %r" % logs)
        self.assertTrue(any("wedge: user draft → submit" in ln for ln in logs),
                        "the user-draft submit decision must journal its phrase: %r" % logs)


class TestRecordLifecycle(unittest.TestCase):
    def test_type_literal_writes_the_record_for_a_machine_nudge(self):
        state = {}
        run = _mkrun()
        ok = wd._type_literal(PID, run, NUDGE_TEXT, kind="type",
                              nudge="queue-arrival", state=state)
        self.assertTrue(ok)
        rec = state.get("nudge_typed", {}).get(PID)
        self.assertIsInstance(rec, dict, state)
        self.assertEqual(rec.get("kind"), "queue-arrival")
        self.assertTrue(NUDGE_TEXT.endswith(rec.get("tail")))
        self.assertTrue(NUDGE_TEXT.startswith(rec.get("head")))

    def test_type_literal_does_not_record_owner_authored_text(self):
        state = {}
        run = _mkrun()
        wd._type_literal(PID, run, USER_TEXT, kind="send", nudge="queue-arrival",
                         user_authored=True, state=state)
        self.assertNotIn(PID, state.get("nudge_typed", {}),
                         "owner-authored text is never a machine-nudge record")

    def test_type_literal_does_not_record_without_a_nudge_identity(self):
        state = {}
        run = _mkrun()
        wd._type_literal(PID, run, NUDGE_TEXT, kind="type", nudge=None, state=state)
        self.assertNotIn(PID, state.get("nudge_typed", {}),
                         "a keystroke with no machine nudge identity is not recorded")

    def test_record_removed_on_confirmed_submit(self):
        state = {}
        now = time.time()
        _seed(state, PID, NUDGE_TEXT, "queue-arrival", now)
        wd._janitor_clear_watch(state, PID)
        self.assertNotIn(PID, state.get("nudge_typed", {}),
                         "a confirmed submit must drop the machine-nudge record")

    def test_two_phase_scroll_length_batched_nudge_is_recorded(self):
        # #1022 adversarial-review MAJOR: a #923 batched `nudge:` machine nudge
        # of >= GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD chars takes the two-phase
        # type path, which types PARTIAL chunks. The FULL text must still be
        # recorded ONCE (at the two-phase level), or such a common nudge shape
        # stays submittable through the wedge back door under the kill switch.
        long_text = "nudge: " + ("lane refill priorita backlog polozka cislo " * 40)
        self.assertGreaterEqual(len(long_text),
                                _stash.GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD,
                                "test payload must exceed the two-phase threshold")
        state = {}
        run = _mkrun()
        # Spy on the record write so we can assert it fires EXACTLY ONCE with the
        # FULL text (MINOR-2): the inner partial-chunk `_type_literal` calls must
        # never record (they thread NO state) — if a future edit wrongly threaded
        # state into them, the last-wins outer write would still leave the final
        # record correct and a text-only assertion would pass, masking the bug.
        real_record = wd._record_machine_nudge
        effective = []

        def _spy(st, pid, text, nudge, now):
            if st is not None:
                effective.append((pid, text, nudge))
            return real_record(st, pid, text, nudge, now)

        with m.patch.object(_stash, "_settle_type_verify",
                            lambda *a, **k: _stash._TV_LANDED), \
                m.patch.object(wd, "_record_machine_nudge", _spy):
            hc = wd._type_two_phase_head_checkpoint(
                PID, run, long_text, None, kind="send", nudge="queue-arrival",
                state=state)
        self.assertEqual(hc, _stash._TV_LANDED)
        self.assertEqual(len(effective), 1,
                         "the FULL text must be recorded exactly once, never a "
                         "partial chunk: %r" % effective)
        self.assertEqual(effective[0][1], long_text)
        rec = state.get("nudge_typed", {}).get(PID)
        self.assertIsInstance(rec, dict, state)
        self.assertEqual(rec.get("kind"), "queue-arrival")
        self.assertTrue(long_text.endswith(rec.get("tail")))
        self.assertTrue(long_text.startswith(rec.get("head")))

    def test_two_phase_does_not_record_owner_authored(self):
        long_text = "nudge: " + ("lane refill priorita backlog polozka cislo " * 40)
        state = {}
        run = _mkrun()
        with m.patch.object(_stash, "_settle_type_verify",
                            lambda *a, **k: _stash._TV_LANDED):
            wd._type_two_phase_head_checkpoint(
                PID, run, long_text, None, kind="send", nudge="queue-arrival",
                user_authored=True, state=state)
        self.assertNotIn(PID, state.get("nudge_typed", {}),
                         "owner-authored two-phase text is never recorded")


if __name__ == "__main__":
    unittest.main()
