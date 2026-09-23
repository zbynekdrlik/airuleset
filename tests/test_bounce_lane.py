"""Locks the prio:bounce priority lane (odoo-erp #1599, user request 2026-07-16).

Incident: gatekeeper /process-subdev and a sub-dev's running autopilot could not
run concurrently — gatekeeper findings arrived as raw tmux prompts that would
derail the running /goal loop, so the user serialized the two streams and dozens
of sub-dev tickets rotted. The convention (odoo-erp #1599 + PR #1600): findings
are filed as tickets labeled `prio:bounce` (+ `stream:<name>`), full content ON
the ticket; the tmux message is only a short nudge. The autopilot skill must:
(1) seed every NEW batch from open `prio:bounce` tickets FIRST (oldest first),
    never preempting a running batch;
(2) on an injected nudge, ACK + ensure the label + let the loop take the ticket
    next turn — never work the finding inline;
(3) keep the label a GENERIC cross-repo convention (no odoo-specific hardcode);
(4) the worker removes the label at its done-point so a resolved bounce leaves
    the lane automatically.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSkillPriorityLane(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def test_seed_ordering_takes_bounce_first_oldest_first(self):
        t = read(self.SKILL)
        self.assertIn("prio:bounce", t)
        self.assertIn("PRIORITY LANE", t)
        self.assertIn("OLDEST open `prio:bounce`", t)

    def test_running_batch_is_never_preempted(self):
        self.assertIn("NEVER preempted", read(self.SKILL))

    def test_all_three_goal_templates_carry_the_bounce_lane(self):
        # The /goal line is the durable engine text (survives compaction, the
        # skill body may not) — every authority profile's template must carry
        # the lane, or a compacted loop silently loses the ordering.
        goal_lines = re.findall(r"^/goal STOP CONDITIONS.*$",
                                read(self.SKILL), re.MULTILINE)
        self.assertEqual(len(goal_lines), 3)
        for line in goal_lines:
            self.assertIn("prio:bounce", line)

    def test_nudge_ack_never_works_the_finding_inline(self):
        t = read(self.SKILL)
        self.assertIn("nudge", t.lower())
        self.assertIn("NEVER start working the finding inline", t)

    def test_nudge_ensures_the_label_best_effort(self):
        t = read(self.SKILL)
        self.assertIn("gh label create prio:bounce", t)
        self.assertIn("--add-label prio:bounce", t)

    def test_label_is_a_generic_cross_repo_convention(self):
        self.assertIn("cross-repo convention", read(self.SKILL))


class TestWorkerClearsBounceLabel(TestCase):
    def test_worker_removes_bounce_label_at_done_point(self):
        self.assertIn("--remove-label prio:bounce",
                      read("agents/autopilot-worker.md"))


class TestReviewWatchLifecycle(TestCase):
    """2026-07-19 incident: BOTH sides of the gatekeeper↔sub-dev ping-pong
    stall because each side's loop ends while the counterpart still has work
    in flight (4 bounced david tickets sat re-handed-off with no re-review;
    a sub-dev loop that ended at hand-off never picks up later bounces). The
    sub-dev /goal templates must hold the loop ALIVE in an hourly REVIEW-WATCH
    until the gatekeeper closes/releases everything, and a nudge arriving with
    NO armed loop must dispatch the worker directly instead of a dead ACK."""

    SKILL = "skills/autopilot/SKILL.md"

    def reduced_goal_lines(self):
        import re
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(self.SKILL),
                           re.MULTILINE)
        self.assertEqual(len(lines), 3)
        # order in the file: full, branch-merge, fork-no-merge
        return lines[1], lines[2]

    # #1128 (owner ruling 2026-09-23) SUPERSEDES the review-watch lifecycle
    # below: a stream loop has NO backlog-empty end at all — gk/U/W work keeps
    # returning, so the old (B) (release containment + an hourly foreground
    # REVIEW-WATCH that was only PREFERRED, never a hard precondition since
    # #395) still let the loop END while a hand-off sat with the gatekeeper
    # (david1-4 idle). The strictly stronger invariant: the loop never ends;
    # its idle state is ONE background `stream-wait` that wakes on any change
    # to the stream's slice (a bounce, a gk comment, a client reply, a new
    # ticket). "Never ends" subsumes "holds until released".

    def test_branch_merge_never_ends_and_idles_on_stream_wait(self):
        bm, _ = self.reduced_goal_lines()
        self.assertIn("this stream loop never ends on an empty slice", bm)
        self.assertIn("airuleset.py stream-wait", bm)
        self.assertNotIn("REVIEW-WATCH", bm)

    def test_fork_closing_is_the_maintainers_job_not_mine_to_prove(self):
        # #395/#761 negatives stay locked (no unproven "CLOSED by the
        # maintainer" precondition, no false "maintainer's job" absolute);
        # since #1128 there is no (B) proof at all, so closure can never be
        # read as the fork loop's end either.
        _, fk = self.reduced_goal_lines()
        self.assertNotIn("the maintainer's job", fk)
        self.assertNotIn("CLOSED by the maintainer", fk)
        self.assertNotIn("(B)", fk)
        self.assertIn("this stream loop never ends on an empty slice", fk)

    def test_fork_holds_until_released_too(self):
        # 2026-07-20 incident ("ping pong moze skoncit az ked je vsetko
        # deploynute do produ a nie skor"): #1128 makes the fork loop never
        # end at all, which is strictly stronger than holding until release.
        _, fk = self.reduced_goal_lines()
        self.assertIn("Only I end this loop (`/goal clear`)", fk)
        self.assertNotIn("RELEASED", fk)

    def test_review_watch_cadence_is_hourly_and_working(self):
        # the hourly re-check became an event-driven waiter (default heartbeat
        # 3600 s, `stream-wait --max`); the turn still ends ⏳ WORKING and a
        # dead waiter is relaunched — never parked silently.
        for line in self.reduced_goal_lines():
            self.assertIn("end ⏳ WORKING", line)
            self.assertIn("relaunch it if gone", line)

    def test_nudge_without_armed_loop_dispatches_worker(self):
        t = read(self.SKILL)
        self.assertIn("NO `/goal` loop is armed", t)
        self.assertIn("dispatch the background `autopilot-worker` for the bounce ticket", t)


class TestCrossStreamProtocolCanonical(TestCase):
    """2026-07-19 user directive: airuleset OWNS the gatekeeper↔sub-dev
    protocol ('musis pochopit co vsetko sa pod gatekeeper rules riesilo a
    prevziat to pod svoju spravu'). The autopilot skill carries the canonical
    section BOTH sides read; repo-local commands (odoo-erp /process-subdev)
    must conform to it, never define their own variant.

    #426: the canonical section's body moved VERBATIM to
    `skills/autopilot/references/cross-stream-protocol.md` (SKILL.md itself
    exceeded its own #414 ~1000-line/file budget) — SKILL.md now carries only
    a short pointer paragraph. This class reads the reference file, where the
    actual protocol content (and this test's own anchors) now lives."""

    SKILL = "skills/autopilot/references/cross-stream-protocol.md"

    def test_canonical_section_exists(self):
        self.assertIn("## Cross-stream protocol", read(self.SKILL))

    def test_no_prompt_interrupts_rule(self):
        t = read(self.SKILL)
        self.assertIn("NEVER a payload prompt into a working session", t)

    def test_label_lifecycle_owned(self):
        t = read(self.SKILL)
        self.assertIn("who removes `prio:bounce`", t)
        self.assertIn("read-only role cannot remove labels", t)

    def test_both_loops_hold_alive(self):
        # #307 (2026-08-07): rule 4 now names TWO deliberately-different-scope
        # gatekeeper-side mechanisms instead of one blanket "both loops always
        # hold together" claim -- `/process-subdev`'s own per-stream loop
        # holds through the WHOLE bounce lifecycle (unchanged by this
        # ticket), while the FULL `/autopilot` loop's own stop-proof holds
        # only while a hand-off it can act on directly is open. Normalize
        # markdown line-wraps before checking, so a re-wrap of the prose
        # cannot silently defeat the lock.
        t = read(self.SKILL)
        i = t.index("TWO gatekeeper-side mechanisms")
        window = " ".join(t[i:i + 1400].split())
        self.assertIn("TWO gatekeeper-side mechanisms", window)
        self.assertIn("/process-subdev`'s own", window)
        self.assertIn("stop-proof (`core-quals`)", window)


if __name__ == "__main__":
    main()


class TestReviewWatchHoldsForeground(TestCase):
    """gk token burn 2026-07-20: inside an armed /goal, ScheduleWakeup does NOT
    pause the loop — the evaluator fires the next turn immediately, so the
    'hourly re-check' spun continuous turns. The hold must keep the turn OPEN
    with a FOREGROUND sleep-poll; ScheduleWakeup is banned from the reduced
    /goal templates."""

    SKILL = "skills/autopilot/SKILL.md"

    def goal_lines(self):
        import re
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(self.SKILL),
                           re.MULTILINE)
        self.assertEqual(len(lines), 3)
        return lines

    def test_reduced_templates_hold_foreground(self):
        # #1128: the foreground sleep-poll became ONE bounded background
        # `stream-wait` (Claude Code defers /goal evaluation while a background
        # task runs, so it costs no turns). ScheduleWakeup stays banned.
        for line in self.goal_lines()[1:]:
            self.assertNotIn("FOREGROUND sleep-poll", line)
            self.assertIn("ONE background `python3 ~/devel/airuleset/airuleset.py "
                          "stream-wait`", line)
            self.assertNotIn("ScheduleWakeup", line)


class TestBounceMeansOneThingInAllThreeHomes(TestCase):
    """#181 round 4, item 3. Three documents described `prio:bounce`
    differently, and the disagreement is load-bearing because `core-quals`
    counts the label: airuleset.py's `MAINTAINER_ACTION_LABELS` comment said
    the re-review is THIS box's ball; the skill's cross-stream rules 2 and 3
    said the gatekeeper returned it for the SUB-DEV to fix and the sub-dev's
    worker clears it; and the branch-merge template's own (B) makes "no open
    prio:bounce for my stream" the SUB-DEV's stop condition.

    The behaviour is DECIDED and is not up for redesign: the full-authority
    loop HOLDS in review-watch while a sub-dev bounce is open — stay alive,
    re-check hourly, never end the loop — so `core-quals --count` legitimately
    never reaching 0 in that state is CORRECT, not the never-stops failure the
    original ticket rejected. All three descriptions must say that one thing.
    """

    CANON = ("sub-dev", "holds", "review-watch", "not the never-stops failure")
    SKILL = "skills/autopilot/SKILL.md"
    # #426: cross-stream rules 2/3 themselves moved VERBATIM to this reference
    # file (SKILL.md exceeded its own #414 line budget) — only the two
    # cross-stream-rule methods below read from it; the others still read the
    # unmoved goal templates / airuleset.py.
    CROSS_STREAM = "skills/autopilot/references/cross-stream-protocol.md"

    def _window(self, text, start, end):
        """Comment markers stripped and whitespace collapsed: a canonical
        sentence hard-wrapped across two lines (or two `# ` comment lines) is
        still the same statement, and an assertion that fails on the wrapping
        instead of on the claim locks nothing."""
        i = text.index(start)
        j = text.index(end, i)
        window = re.sub(r"(?m)^\s*#\s?", "", text[i:j])
        return " ".join(window.split()).lower()

    def _assert_canonical(self, window, where):
        for needle in self.CANON:
            self.assertIn(
                needle, window,
                "%s does not state the settled prio:bounce meaning (missing "
                "%r)" % (where, needle))

    def test_the_code_comment_states_it(self):
        # #433 cluster I: MAINTAINER_ACTION_LABELS + its label comment moved
        # into the cli_quals.py leaf (footer/stop-proof split). Read it where
        # the constant now lives, not the stale airuleset.py home.
        src = read("cli_quals.py")
        window = self._window(
            src, "An open, non-skip ticket carrying ANY of these labels",
            "MAINTAINER_ACTION_LABELS = (")
        self._assert_canonical(window, "cli_quals.py's label comment")

    def test_cross_stream_rule_two_states_it(self):
        t = read(self.CROSS_STREAM)
        window = self._window(t, "2. **Priority = labels",
                              "3. **Label lifecycle")
        self._assert_canonical(window, "cross-stream rule 2")

    def test_cross_stream_rule_three_states_it(self):
        t = read(self.CROSS_STREAM)
        window = self._window(t, "3. **Label lifecycle",
                              "4. **The ping-pong")
        self._assert_canonical(window, "cross-stream rule 3")

    def test_the_branch_merge_stop_condition_is_still_the_subdevs_own(self):
        """The reduced-authority template's bounce scope was never the
        gatekeeper's. #1128 removed its (B) ("no open prio:bounce for my
        stream"); the bounce lane still works only MY prio:bounce tickets."""
        line = [ln for ln in read(self.SKILL).splitlines()
                if ln.startswith("/goal STOP CONDITIONS")][1]
        self.assertIn("prio:bounce", line)
        self.assertIn("my prio:bounce tickets", line)
