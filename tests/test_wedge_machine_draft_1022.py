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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd  # noqa: E402

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
        state = {}
        now = time.time()
        _seed(state, PID, GENERIC_TEXT, "resume", now)
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


if __name__ == "__main__":
    unittest.main()
