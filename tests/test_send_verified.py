"""#490 — the shared transcript-proof send helper (`watchdog.send_verified`)
and its structured reader (`watchdog._submit_confirmed`).

`send_verified` is the missing "bare-box, transcript-proof" member of the
watchdog delivery family (`deliver_with_stash` / `_send_goal_verified` / the
compact submit-verify). It types + Enter, then verifies the submit landed by
reading the session TRANSCRIPT (a new `user` turn carrying the text), not the
pane render — the #486 delivery-bullet direction. These tests drive the four
outcomes it must get right end to end through the SAME stateful fake tmux the
goal tests use (an accepted submit appends a real `user` turn + clears the box;
a swallowed submit keeps the box text + writes nothing; a `BSpace` run trims
the box), so nothing is asserted against a frozen static capture.
"""

import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.goal as goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, GOAL_DRAFT_CAP,
)

PID = "%9"
TEXT = "štuchnutie: zaplň lány, backlog=5"


class _TruncTypeFake:
    """#617 -- models a TRUNCATED own /goal type into a box verified bare:
    the entry + fresh captures read BARE, then after the first `-l` type the
    box renders only a truncated PREFIX (so `_await_typed(want=True)` rejects
    it), and a BSpace run clears it back to bare (so the undo can converge)."""

    def __init__(self, bare_cap, trunc_cap):
        self.bare = bare_cap
        self.trunc = trunc_cap
        self.sent = []
        self.typed = False
        self.cleared = False

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "send-keys" in j:
            self.sent.append(argv)
            if "-l" in argv:
                self.typed = True
            elif argv[4:] and all(k == "BSpace" for k in argv[4:]):
                self.cleared = True
            return ""
        if "capture-pane" in j:
            return self.bare if (self.cleared or not self.typed) else self.trunc
        return ""


def _tails(sent):
    return [a[-1] for a in sent if a]


def _no_double_escape(sent):
    tails = _tails(sent)
    for a, b in zip(tails, tails[1:]):
        if a == "Escape" and b == "Escape":
            raise AssertionError("two consecutive Escape sends: %r" % sent)


class _Base(unittest.TestCase):
    def _tpath(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "sess.jsonl"
        # a real, non-empty starting transcript (an assistant entry), like a
        # live session — the baseline is taken past it.
        p.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predošlá práca"}}) + "\n")
        return p


class SubmitConfirmed(_Base):
    def test_matches_a_new_user_turn_past_baseline(self):
        p = self._tpath()
        base = p.stat().st_size
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user",
                                "message": {"content": TEXT},
                                "timestamp": "2026-08-15T10:00:00.000Z"}) + "\n")
        self.assertTrue(wd._submit_confirmed(p, base, TEXT))

    def test_a_DIFFERENT_new_user_turn_never_confirms(self):
        # F1 lock — a concurrent plain user turn with OTHER text (the user
        # submitting their own prompt, a job-7 Discord-relay injection, another
        # nudge) must NOT confirm OUR swallowed delivery. Without the text
        # match, ANY new user turn would false-confirm — booked delivered with
        # our foreign text left in the box (#490 recreated).
        p = self._tpath()
        base = p.stat().st_size
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user",
                                "message": {"content": "úplne iný prompt od usera"},
                                "timestamp": "2026-08-15T10:00:00.000Z"}) + "\n")
        self.assertFalse(wd._submit_confirmed(p, base, TEXT))

    def test_ignores_a_compact_summary_user_entry(self):
        # F4 — a /compact continuation summary is a top-level `user` entry that
        # can QUOTE a prior identical nudge; skipping it (like the sibling
        # _last_human_prompt_ts) closes that false-confirm.
        p = self._tpath()
        base = p.stat().st_size
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "isCompactSummary": True,
                                "message": {"content": "…continued… " + TEXT}}) + "\n")
        self.assertFalse(wd._submit_confirmed(p, base, TEXT))

    def test_ignores_a_matching_turn_BEFORE_the_baseline(self):
        # A prior identical nudge already in the transcript must never
        # false-positive — the byte baseline scopes the read to what WE added.
        p = self._tpath()
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user",
                                "message": {"content": TEXT},
                                "timestamp": "2026-08-15T09:00:00.000Z"}) + "\n")
        base = p.stat().st_size            # baseline AFTER the prior nudge
        self.assertFalse(wd._submit_confirmed(p, base, TEXT))

    def test_ignores_a_tool_result_user_entry(self):
        p = self._tpath()
        base = p.stat().st_size
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": TEXT}]}}) + "\n")
        self.assertFalse(wd._submit_confirmed(p, base, TEXT))

    def test_ignores_a_meta_user_entry(self):
        p = self._tpath()
        base = p.stat().st_size
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "isMeta": True,
                                "message": {"content": TEXT}}) + "\n")
        self.assertFalse(wd._submit_confirmed(p, base, TEXT))

    def test_missing_file_fails_safe_false(self):
        self.assertFalse(wd._submit_confirmed("/no/such/transcript.jsonl", 0, TEXT))

    def test_empty_text_is_never_confirmed(self):
        p = self._tpath()
        self.assertFalse(wd._submit_confirmed(p, 0, ""))


class SendVerified(_Base):
    def _fake(self, enters_swallowed=0, transcript_path=None, captured=GOAL_IDLE_CAP):
        return DeliverGoalFakeTmux([(PID, "claude", "/x", "111")], captured,
                                   model_type=True,
                                   enters_swallowed=enters_swallowed,
                                   transcript_path=transcript_path)

    def test_confirmed_submit_returns_true_with_no_recovery_keystrokes(self):
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None, logs=[])
        self.assertTrue(ok, _tails(tmux.sent))
        self.assertNotIn("Escape", _tails(tmux.sent))
        self.assertNotIn("BSpace", _tails(tmux.sent))
        self.assertEqual(tmux.box, "")

    def test_swallowed_submit_is_undone_and_returns_false(self):
        p = self._tpath()
        tmux = self._fake(enters_swallowed=99, transcript_path=p)
        logs = []
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        # foreign text restored off the user's box — never left behind
        self.assertEqual(tmux.box, "", _tails(tmux.sent))
        # exactly ONE corrective Escape, never two in a row (#35)
        self.assertEqual(_tails(tmux.sent).count("Escape"), 1, _tails(tmux.sent))
        _no_double_escape(tmux.sent)
        self.assertTrue(any("swallowed" in ln for ln in logs), logs)

    def test_corrective_escape_enter_recovers(self):
        # first Enter swallowed; the corrective Escape+Enter lands the submit.
        p = self._tpath()
        tmux = self._fake(enters_swallowed=1, transcript_path=p)
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None, logs=[])
        self.assertTrue(ok, _tails(tmux.sent))
        self.assertEqual(_tails(tmux.sent).count("Escape"), 1, _tails(tmux.sent))
        self.assertNotIn("BSpace", _tails(tmux.sent))   # confirmed, nothing undone

    def test_box_bare_but_unconfirmed_never_escapes_into_a_live_turn(self):
        # The Enter cleared the box (a turn may have STARTED) but the transcript
        # never confirmed — send_verified must NOT send a corrective Escape
        # (it could interrupt that turn, the #233 harm) and must undo nothing.
        p = self._tpath()
        tmux = self._fake(transcript_path=None)   # Enter clears box, writes no turn
        logs = []
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertNotIn("Escape", _tails(tmux.sent))
        self.assertNotIn("BSpace", _tails(tmux.sent))
        self.assertTrue(any("box bare" in ln for ln in logs), logs)

    def test_second_bare_recheck_aborts_a_draft_that_raced_in(self):
        # F2 lock — a draft racing into the box AFTER the strip-Escape but
        # BEFORE the type must be caught by the SECOND bare re-check and
        # rescued, never typed over. cap_seq: bare pre-check, then a draft on
        # the fresh re-check. (Removing the second re-check makes this type.)
        with TemporaryDirectory() as rescue:
            with m.patch.dict("os.environ",
                              {"AIRULESET_DRAFT_RESCUE_DIR": rescue}):
                p = self._tpath()
                tmux = DeliverGoalFakeTmux([(PID, "claude", "/x", "111")],
                                           GOAL_IDLE_CAP,
                                           cap_seq=(GOAL_IDLE_CAP, GOAL_DRAFT_CAP))
                logs = []
                ok = wd.send_verified(PID, TEXT, tmux, p,
                                      sleep_fn=lambda s: None, logs=logs)
                self.assertFalse(ok)
                self.assertNotIn("-l", [x for a in tmux.sent for x in a])
                self.assertTrue(any("raced busy" in ln for ln in logs), logs)

    def test_raced_draft_is_rescued_and_nothing_typed(self):
        # A draft raced into the box since the caller's own bare check —
        # rescue it and refuse, never type over it.
        with TemporaryDirectory() as rescue:
            with m.patch.dict("os.environ",
                              {"AIRULESET_DRAFT_RESCUE_DIR": rescue}):
                p = self._tpath()
                tmux = DeliverGoalFakeTmux([(PID, "claude", "/x", "111")],
                                           GOAL_DRAFT_CAP)   # box holds a draft
                logs = []
                ok = wd.send_verified(PID, TEXT, tmux, p,
                                      sleep_fn=lambda s: None, logs=logs)
                self.assertFalse(ok)
                self.assertNotIn("-l", [x for a in tmux.sent for x in a])
                self.assertTrue(any("not bare" in ln for ln in logs), logs)

    def test_missing_tpath_refuses_to_send(self):
        tmux = self._fake(transcript_path=None)
        logs = []
        ok = wd.send_verified(PID, TEXT, tmux, None,
                              sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("no transcript path" in ln for ln in logs), logs)

    def test_delivered_unconfirmed_sets_out_flag(self):
        # #594: the Enter clears the box (delivered/queued) but no `user` turn
        # is written -> False, and `out["delivered_unconfirmed"]` records that
        # it was DELIVERED (the box-bare branch), so a caller can dedup on it.
        p = self._tpath()
        tmux = self._fake(transcript_path=None)   # Enter clears box, writes no turn
        out = {}
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None,
                              logs=[], out=out)
        self.assertFalse(ok)
        self.assertTrue(out.get("delivered_unconfirmed"))

    def test_swallowed_does_not_set_delivered_flag(self):
        # #594: a GENUINE swallow (text stuck -> undone, never accepted) returns
        # False WITHOUT the delivered flag -> the caller keeps retrying it.
        p = self._tpath()
        tmux = self._fake(enters_swallowed=99, transcript_path=p)
        out = {}
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None,
                              logs=[], out=out)
        self.assertFalse(ok)
        self.assertFalse(out.get("delivered_unconfirmed"))

    def test_confirmed_submit_leaves_out_flag_unset(self):
        # A confirmed submit returns True; the delivered-unconfirmed flag is a
        # False-return-only signal, so it stays unset on the True path.
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        out = {}
        ok = wd.send_verified(PID, TEXT, tmux, p, sleep_fn=lambda s: None,
                              logs=[], out=out)
        self.assertTrue(ok)
        self.assertFalse(out.get("delivered_unconfirmed"))


class TruncatedTypeUndone617(unittest.TestCase):
    def test_truncated_own_type_is_undone_never_left_in_box(self):
        # #617 -- montalu1@subdev: a partial /goal type (a send-keys chunk
        # interrupted) rendered a TRUNCATED prefix that _await_typed rejects.
        # BEFORE this fix `_send_goal_verified` returned False and LEFT the
        # truncated /goal fragment in the box -> the poisoned draft the next
        # sweep can neither submit nor clear (the live 674-char stuck draft).
        # The verify-fail branch MUST undo its own partial type; the box was
        # verified bare before typing, so backspacing len(text) is safe.
        text = "/goal STOP CONDITIONS " + ("x " * 400)   # long, wraps
        trunc = ("● wip\n❯ /goal STOP CONDITIONS x x python3 ~\n"
                 "  ctx ███░  caveman:lite\n")
        fake = _TruncTypeFake(GOAL_IDLE_CAP, trunc)
        logs = []
        ok = goal._send_goal_verified(PID, text, fake, captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=logs)
        self.assertFalse(ok)
        self.assertIn("BSpace", _tails(fake.sent))          # the undo happened
        self.assertTrue(any("undone" in ln for ln in logs), logs)

    def test_confirmed_type_is_never_undone(self):
        # Control: a type that DOES land + submits must NOT trigger the undo.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tp = Path(d.name) / "s.jsonl"
        tp.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "x"}}) + "\n")
        tmux = DeliverGoalFakeTmux([(PID, "claude", "/x", "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   transcript_path=tp)
        ok = goal._send_goal_verified(PID, "/goal STOP CONDITIONS ok", tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok)
        self.assertNotIn("BSpace", _tails(tmux.sent))


if __name__ == "__main__":
    unittest.main()
