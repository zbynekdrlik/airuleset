"""#738 -- a worktree-isolated autopilot-worker is ITSELF a subagent, and
CYCLE step 6 tells it to dispatch ONE fresh-context `general-purpose` review
subagent but says NOTHING about foreground-vs-background or how to safely
wait on it. If the worker dispatches the review async and ends its turn with
the dispatch outstanding, it TERMINATES -- the completion notification fires
to its PARENT, not to it (the EXACT `subagent-stop-check-bg-work.sh` /
bg-CI-poll class already warned at CYCLE step 5). Every wait primitive it
then reaches for is blocked or unsuitable: a bare `sleep N` is harness-
blocked ("use Monitor"); `Bash(run_in_background=True)` terminates a
subagent (the same bug); `TaskStop` on its OWN Agent dispatch is refused
("owned by <id>"); there is no `TaskOutput`/status tool.

The empirically-proven fix (issue #738 + the #569 playbook lesson) lived
ONLY in a playbook rule body, which a dispatched worker NEVER reads (#104).
This file locks the fix into the worker's OWN system prompt
(`agents/autopilot-worker.md`) -- the only surface a dispatched worker
actually receives -- plus a parallel pointer in the supervisor-facing
`skills/autopilot/SKILL.md` Worktree/fleet-mode note.

Chosen doctrine, two-tier:
  1. PRIMARY -- dispatch the review FOREGROUND (do NOT pass
     `run_in_background: true`): a foreground Agent dispatch blocks and
     returns the verdict AS its tool_result, so it never leaves outstanding
     background work and the turn-end termination class never triggers.
  2. FALLBACK (if the platform surfaces it async anyway) -- ride it out
     FOREGROUND with bounded `inotifywait -e close_write` event-waits on the
     dispatch's own `output_file`, reading "done" from the LAST JSON object
     only; NEVER a bare `sleep`, NEVER `Bash(run_in_background=True)`, NEVER
     `TaskStop` your own dispatch.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"
SKILL_MD = ROOT / "skills" / "autopilot" / "SKILL.md"


def _norm(text):
    """Collapse markdown line-wrapping/indentation so a phrase spanning a
    wrap still matches a substring search -- the established technique this
    repo's own prose-lock tests already use.
    """
    return " ".join(text.split())


class TestWorkerReviewWaitDoctrine(unittest.TestCase):
    """The wait doctrine has to land in the worker's OWN system prompt --
    a skill/rule body does not reach a dispatched subagent (#104), so a
    playbook lesson alone (as #569 was) leaves every worker to rediscover
    the workaround live.
    """

    # The operative wait doctrine is a dedicated CYCLE-step-6 paragraph led by
    # this unique heading -- anchor the mechanism assertions on IT, not on the
    # file's first #738 (which is the earlier worktree-mode STOP POINT pointer).
    HEADING = "WAIT ON THE REVIEW DISPATCH SAFELY"

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")
        self.norm = _norm(self.text)
        self.assertIn(
            "#738", self.text,
            "the worker prompt must cite #738 so the review-wait doctrine "
            "points at the incident that motivated it")
        self.assertIn(
            self.HEADING, self.text,
            "CYCLE step 6 must carry a dedicated wait-doctrine paragraph led "
            "by the %r heading" % self.HEADING)
        idx = self.text.index(self.HEADING)
        # The paragraph itself is the mechanism home; window forward from its
        # heading (a generous span covers the whole paragraph after re-wrap).
        self.window = _norm(self.text[idx:idx + 1900])

    def test_mandates_foreground_review_dispatch_not_run_in_background(self):
        # PRIMARY tier: dispatch the review FOREGROUND -- do NOT pass
        # run_in_background: true -- so the dispatch never becomes
        # outstanding background work the worker must wait on.
        self.assertRegex(
            self.window,
            r"(?i)foreground",
            "the review-wait doctrine must tell the worker to dispatch the "
            "review FOREGROUND")
        self.assertRegex(
            self.window,
            r"(?i)(do ?NOT|never|without)[^.]{0,80}run_in_background",
            "the doctrine must explicitly say NOT to pass "
            "`run_in_background: true` for the review dispatch")

    def test_warns_ending_the_turn_terminates_the_subagent(self):
        # The termination hazard is the whole reason this exists -- it must
        # be named as the SAME class already warned at CYCLE step 5.
        self.assertRegex(
            self.norm,
            r"(?i)TERMINATE",
            "the doctrine must warn that ending the turn with the dispatch "
            "outstanding TERMINATES the subagent")
        self.assertRegex(
            self.window,
            r"(?i)(bg-CI-poll|CI-poll|step 5|terminat)",
            "the review-wait doctrine must tie the hazard to the same "
            "bg-CI-poll termination class the worker already knows")

    def test_names_the_foreground_wait_primitive(self):
        # FALLBACK tier: the proven safe wait is a bounded inotifywait
        # event-wait on the dispatch's own output file (#569).
        self.assertIn(
            "inotifywait", self.window,
            "the fallback wait must name `inotifywait` -- the proven "
            "foreground event-wait primitive (#569), never a bare sleep")

    def test_bans_the_unsafe_wait_primitives(self):
        # A bare sleep is harness-blocked; Bash(run_in_background) terminates
        # a subagent; TaskStop refuses to stop one's OWN Agent dispatch.
        self.assertRegex(
            self.window,
            r"(?i)(bare |standalone )?sleep",
            "the doctrine must name a bare `sleep` as a banned/blocked wait")
        self.assertIn(
            "TaskStop", self.window,
            "the doctrine must name `TaskStop` as unusable on your OWN "
            "Agent dispatch")
        self.assertRegex(
            self.window,
            r"(?i)TaskStop[^.]{0,120}(own|owned)",
            "the doctrine must explain TaskStop is refused for your OWN "
            "dispatch (\"owned by\")")

    def test_reads_done_from_the_last_json_object_not_the_whole_transcript(self):
        # Never wholesale-Read the dispatch's JSONL (context overflow) --
        # read done from the LAST object only.
        self.assertRegex(
            self.window,
            r"(?i)(tail -n ?1|last (json )?object|last line)",
            "the doctrine must read completion from the LAST JSON object of "
            "the dispatch output, not by reading the whole transcript")
        self.assertRegex(
            self.window,
            r"(?i)(never|not|do ?NOT)[^.]{0,80}(Read|tail|wholesale|content)",
            "the doctrine must warn NOT to wholesale-Read the dispatch's "
            "output JSONL (context overflow)")


class TestWorktreeStopPointAndSkillPointAtTheWaitDoctrine(unittest.TestCase):
    """#738's own incident ran INSIDE a worktree-dispatched worker; the
    worktree-mode STOP POINT paragraph and the supervisor-facing
    Worktree/fleet-mode note both route the worker through CYCLE step 6's
    review dispatch, so both must point at the #738 wait doctrine too.
    """

    def test_worktree_stop_point_points_at_738(self):
        text = WORKER_MD.read_text(encoding="utf-8")
        marker = "Worktree-mode STOP POINT"
        self.assertIn(marker, text)
        idx = text.index(marker)
        window = _norm(text[idx:idx + 1400])
        self.assertIn(
            "#738", window,
            "the worktree-mode STOP POINT paragraph must point at the #738 "
            "review-wait doctrine -- it is exactly the surface #738's own "
            "incident ran on")

    def test_skill_worktree_fleet_note_points_at_738(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        marker = "Worktree/fleet mode"
        self.assertIn(marker, text)
        idx = text.index(marker)
        window = _norm(text[idx:idx + 1100])
        self.assertIn(
            "#738", window,
            "the supervisor-facing Worktree/fleet-mode note must carry a "
            "#738 pointer alongside its existing #363 one, so the "
            "review-dispatch wait mechanism is consistent on both surfaces")


if __name__ == "__main__":
    unittest.main()
