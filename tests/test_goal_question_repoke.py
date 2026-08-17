"""#522 -- `goal_question_repoke_watch` disarms a `/goal` loop STUCK re-poking an
unanswered `❓ NEEDS YOU` (the native evaluator ignoring stop-condition (A) --
17+ re-poke incident), plus the `goal_dark_watch` re-entry veto and the pure
`question_repoke_run` detector. The only historical mechanical guard
(`_goal_blocked_on_unanswered_question`, #350) was deleted in #403 and
`watchdog/goal.py` delegated the safety to condition (A) -- the premise this
incident refutes.

#524 threshold-teeth discipline: the streak tests run PAST the N threshold and
each safety veto (human answer / byte-identical / recent-human / attempt-cap /
armed-gate / re-entry) is mutation-verified by hand (see the module's own
`Mutation-verified` notes on the class docstrings).
"""

import json
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal, transcripts

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_IDLE_CAP,
    _encode,
    _isolate_goal_state,
    DeliverGoalFakeTmux,
    _write_goal_marker,
)

Q = "❓ NEEDS YOU: schváliš prístup A alebo B?"
Q2 = "❓ NEEDS YOU: úplne iná otázka?"


def _asst(marker_line, body="Rozanalyzoval som to."):
    return {"type": "assistant", "message": {"content": body + "\n\n" + marker_line}}


def _machine(text="continue"):
    return {"type": "user", "message": {"content": text}}


def _human(text="použi prístup A"):
    return {"type": "user", "message": {"content": text}}


def _write_entries(proj, cwd, sid, entries):
    """Write a transcript at <proj>/<encoded-cwd>/<sid>.jsonl from a list of
    entry dicts (oldest -> newest). Returns the path."""
    d = Path(proj) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def _repoke_entries(n, q=Q, human_at=None):
    """`n` assistant `❓ NEEDS YOU` turns with a machine `continue` re-poke between
    each (the real `/goal` loop shape). `human_at` (0-based, from the OLDEST
    assistant turn) inserts a genuine human answer right BEFORE that assistant
    turn -- so the newest `human_at` re-pokes stay a clean streak."""
    entries = []
    for i in range(n):
        if human_at is not None and i == human_at:
            entries.append(_human())
        else:
            entries.append(_machine())
        entries.append(_asst(q))
    return entries


class _Base(unittest.TestCase):
    CWD = "/home/newlevel/devel/qrepoke"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _run_watch(self, proj, tmux, state, now=100000.0, dry_run=False,
                   human_ts_fn=None):
        return goal.goal_question_repoke_watch(
            now, run=tmux, state=state, projects_dir=proj,
            sleep_fn=lambda s: None, dry_run=dry_run,
            human_ts_fn=human_ts_fn or (lambda tp: None))

    def _armed_tmux(self, tpath, sid="sess"):
        return DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP,
            model_type=True, transcript_path=str(tpath))


# --------------------------------------------------------------------------- #
# PURE detector -- facts-in / verdict-out.
# --------------------------------------------------------------------------- #
class TestQuestionRepokeRun(unittest.TestCase):
    """Mutation-verified: dropping the `is_human_fn` break, the byte-identical
    branch, or the `_NEEDS_YOU_RX` gate each flips one of these."""

    def _ishuman(self, e):
        return wd._is_genuine_human_prompt(e)

    def test_counts_consecutive_identical_repokes(self):
        streak, ql = transcripts.question_repoke_run(_repoke_entries(6), self._ishuman)
        self.assertEqual(streak, 6)
        self.assertTrue(ql.startswith("❓ NEEDS YOU"))

    def test_genuine_human_answer_breaks_the_streak(self):
        # newest entry is a human answer -> streak 0
        entries = _repoke_entries(6) + [_human()]
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 0)

    def test_human_in_the_middle_bounds_the_streak(self):
        # a human answer before the 3rd-from-newest assistant turn: newest 2 clean
        entries = _repoke_entries(5, human_at=3)
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 2)

    def test_a_different_question_breaks_the_streak(self):
        entries = [_asst(Q2), _machine(), _asst(Q), _machine(), _asst(Q)]
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 2)

    def test_asked_plus_working_marker_never_trips(self):
        # ❓ ASKED (body) + ⏳ WORKING (last) resolves to ⏳, not ❓ NEEDS YOU
        aw = {"type": "assistant", "message": {"content":
              "telo\n\n❓ ASKED: nieco?\n\n⏳ WORKING: robím iné tickety"}}
        entries = [aw, _machine(), aw, _machine(), aw]
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 0)

    def test_discord_relayed_answer_counts_as_human(self):
        disc = {"type": "user", "message": {"content": "Odpoveď z Discordu: použi A"}}
        entries = _repoke_entries(5) + [disc]
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 0)

    def test_machine_repoke_and_bookkeeping_are_transparent(self):
        entries = [_machine("continue"), _asst(Q),
                   {"type": "system", "content": "x"}, _machine(),
                   {"type": "assistant", "message": {"content": ""}},  # sentinel skip
                   _machine(), _asst(Q)]
        # 2 real assistant ❓ turns, everything else transparent
        self.assertEqual(transcripts.question_repoke_run(entries, self._ishuman)[0], 2)


# --------------------------------------------------------------------------- #
# THE DISARM (`goal_question_repoke_watch`).
# --------------------------------------------------------------------------- #
class TestQuestionRepokeDisarm(_Base):
    def test_five_repokes_gets_disarmed(self):
        proj = self._dir()
        sid = "sess-disarm-1"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(5))
        tmux = self._armed_tmux(tpath)
        state = {}
        logs = self._run_watch(proj, tmux, state)
        self.assertIn("/goal clear", tmux.typed_texts(),
                      "5 byte-identical re-pokes on an armed pane must be disarmed")
        self.assertIn(sid, state["goal_disarmed_q"])
        self.assertTrue(any("DISARMED" in ln for ln in logs))

    def test_four_repokes_does_not_disarm(self):
        # THRESHOLD TEETH: one below GOAL_QUESTION_REPOKE_MIN must NOT type.
        self.assertEqual(goal.GOAL_QUESTION_REPOKE_MIN, 5)
        proj = self._dir()
        sid = "sess-disarm-2"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(4))
        tmux = self._armed_tmux(tpath)
        state = {}
        self._run_watch(proj, tmux, state)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertNotIn(sid, state.get("goal_disarmed_q", {}))

    def test_human_answer_in_streak_blocks_disarm(self):
        # MUTATION target: if `is_human_fn` is ignored, this streak reads 5 and
        # wrongly disarms.
        proj = self._dir()
        sid = "sess-disarm-3"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(5, human_at=1))
        tmux = self._armed_tmux(tpath)
        state = {}
        self._run_watch(proj, tmux, state)
        self.assertNotIn("/goal clear", tmux.typed_texts())

    def test_not_armed_pane_is_not_disarmed(self):
        # a served (non-/goal) session that ended one turn on ❓, or any un-armed
        # pane, must be left alone even with a long ❓ tail.
        proj = self._dir()
        sid = "sess-disarm-4"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=str(tpath))
        state = {}
        self._run_watch(proj, tmux, state)
        self.assertNotIn("/goal clear", tmux.typed_texts())

    def test_recent_human_skips_disarm(self):
        proj = self._dir()
        sid = "sess-disarm-5"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        state = {}
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "presence marker 12s ago")):
            logs = self._run_watch(proj, tmux, state)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertTrue(any("recent human" in ln for ln in logs))
        self.assertNotIn(sid, state.get("goal_disarmed_q", {}))

    def test_attempt_cap_blocks_after_two_in_24h(self):
        proj = self._dir()
        sid = "sess-disarm-6"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        # two prior attempts within the last 24h already spent the cap
        now = 100000.0
        state = {"goal_qdisarm_attempts": {sid: [now - 100, now - 50]}}
        logs = self._run_watch(proj, tmux, state, now=now)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertTrue(any("ATTEMPT-CAP" in ln for ln in logs))

    def test_dry_run_types_nothing(self):
        proj = self._dir()
        sid = "sess-disarm-7"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        state = {}
        logs = self._run_watch(proj, tmux, state, dry_run=True)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertNotIn(sid, state.get("goal_disarmed_q", {}))
        self.assertTrue(any("would disarm" in ln for ln in logs))

    def test_already_disarmed_skips_second_keystroke(self):
        proj = self._dir()
        sid = "sess-disarm-8"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        # veto already set, no human answer since -> skip, no re-type
        state = {"goal_disarmed_q": {sid: {"disarmed_ts": 99000.0}}}
        logs = self._run_watch(proj, tmux, state, now=100000.0)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertTrue(any("disarm" in ln and "already" in ln.lower() or
                            "veto ACTIVE" in ln for ln in logs))
        self.assertIn(sid, state["goal_disarmed_q"])   # still vetoed

    def test_reentry_clears_veto_when_human_answers(self):
        proj = self._dir()
        sid = "sess-disarm-9"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        state = {"goal_disarmed_q": {sid: {"disarmed_ts": 99000.0}}}
        # a genuine human answer landed AFTER the disarm
        logs = goal.goal_question_repoke_watch(
            100000.0, run=tmux, state=state, projects_dir=proj,
            sleep_fn=lambda s: None, human_ts_fn=lambda tp: 99500.0)
        self.assertNotIn(sid, state["goal_disarmed_q"], "veto must clear on a human answer")
        self.assertTrue(any("CLEARED" in ln for ln in logs))

    def test_state_reaper_drops_stale_entries(self):
        proj = self._dir()
        # a candidate pane keeps its own entry live; the reaper only touches
        # entries for sessions NOT visited this sweep.
        sid = "sess-live"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(6))
        tmux = self._armed_tmux(tpath)
        now = 100000.0
        old = now - goal.GOAL_QDISARM_STATE_TTL_S - 10
        state = {"goal_disarmed_q": {"gone-sid": {"disarmed_ts": old}},
                 "goal_qdisarm_attempts": {"gone-sid": [old]}}
        self._run_watch(proj, tmux, state, now=now)
        self.assertNotIn("gone-sid", state["goal_disarmed_q"])
        self.assertNotIn("gone-sid", state["goal_qdisarm_attempts"])


# --------------------------------------------------------------------------- #
# THE dark_watch RE-ENTRY VETO.
# --------------------------------------------------------------------------- #
class TestDarkWatchHonoursVeto(_Base):
    """Mutation-verified: removing the `_qdisarm_veto` call (or its `if vetoed:
    continue`) lets dark_watch re-accumulate / re-arm the disarmed loop."""

    def _dark(self, proj, tmux, state, now, human_ts_fn):
        return goal.goal_dark_watch(
            now, run=tmux, state=state, projects_dir=proj,
            send_fn=lambda msg, **k: None, sleep_fn=lambda s: None,
            rearm_fn=lambda cwd: ("/goal x", "full"),
            obligation_fn=lambda cwd: (5, now),
            human_ts_fn=human_ts_fn)

    def test_veto_active_blocks_rearm_accumulation(self):
        proj = self._dir()
        sid = "sess-veto-1"
        # mark=set + un-armed footer = the silently-dark shape dark_watch acts on
        from _goal_arm_helpers import _write_marker_transcript
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000.0
        state = {"goal_disarmed_q": {sid: {"disarmed_ts": now - 10}}}
        logs = self._dark(proj, tmux, state, now, human_ts_fn=lambda tp: None)
        # no death-confirmation run started for a vetoed sid
        self.assertNotIn(sid, state.get("goal_dark_confirm", {}))
        self.assertTrue(any("disarm veto ACTIVE" in ln for ln in logs))

    def test_veto_clears_and_rearm_resumes_after_human_answer(self):
        proj = self._dir()
        sid = "sess-veto-2"
        from _goal_arm_helpers import _write_marker_transcript
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000.0
        state = {"goal_disarmed_q": {sid: {"disarmed_ts": now - 100}}}
        # a human answered AFTER the disarm -> veto clears, dark_watch proceeds
        logs = self._dark(proj, tmux, state, now, human_ts_fn=lambda tp: now - 5)
        self.assertNotIn(sid, state["goal_disarmed_q"])
        self.assertTrue(any("disarm veto CLEARED" in ln for ln in logs))
        # and the standard debounce path ran (a first-observation entry appeared)
        self.assertIn(sid, state.get("goal_dark_seen", {}))


if __name__ == "__main__":
    unittest.main()
