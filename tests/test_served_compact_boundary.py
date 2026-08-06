"""Served (non-autopilot-worker) session compact boundary (#228).

#146's own verdict (quoted verbatim on #228) identified that a served,
interactive session -- one NOT running an armed `/goal` autopilot loop --
had NO natural moment at which it would call `airuleset.py compact-request
--self` (#225), even though the mechanism itself is fully generic (it
resolves purely from the calling pane's own `$TMUX_PANE` and records under
the `self-callback` proven-boundary origin -- no `agent_type` check
anywhere). The 2026-08-06 decision comment on #228 settled the design: a
served session's OWN compact boundary is a genuinely COMPLETED task/ticket
-- never right after an answered question, never mid-work -- taught as a
rule/prompt extension (no new hook/job, per this repo's FREEZE), mirroring
the `/goal` templates' existing worker-flow teaching.

These tests lock two things:
1. `modules/core/completion-report.md` carries the new teaching (so it
   cannot silently regress in a future edit).
2. `cmd_compact_request`'s `--self` branch in `airuleset.py` stays
   agent-type-agnostic -- the structural fact the whole design rests on.
   A future accidental gate addition there would make the completion-report
   teaching wrong without touching completion-report.md at all, so this is
   pinned independently.
"""

import inspect
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestCompletionReportTeachesServedSelfCompact(TestCase):
    MOD = "modules/core/completion-report.md"

    def test_self_call_is_taught(self):
        t = read(self.MOD)
        self.assertIn("compact-request --self", t)

    def test_scoped_to_served_non_worker_sessions(self):
        t = read(self.MOD)
        # Must explicitly name the population this teaching is for, not
        # just repeat the mechanism -- otherwise a reader can't tell this
        # applies outside the autopilot/goal-loop flow already covered in
        # skills/autopilot/SKILL.md.
        self.assertIn("served", t.lower())
        self.assertTrue(
            "non-worker" in t.lower() or "not running an armed" in t.lower(),
            "completion-report.md must explicitly scope the new teaching "
            "to served (non-autopilot-worker) sessions",
        )

    def test_never_right_after_answering_a_question(self):
        t = read(self.MOD)
        self.assertIn("NEVER", t)
        low = t.lower()
        self.assertIn("answer", low)
        self.assertTrue(
            "not a completion boundary" in low
            or "resumption" in low,
            "must explain WHY answering a question is not itself a "
            "compaction boundary",
        )

    def test_never_mid_work(self):
        t = read(self.MOD)
        self.assertIn("mid-work", t.lower())

    def test_references_the_deciding_tickets(self):
        t = read(self.MOD)
        self.assertTrue("#225" in t or "#228" in t)

    def test_fallback_hook_still_documented_as_the_safety_net(self):
        t = read(self.MOD)
        self.assertIn("notify-compact-request.sh", t)


class TestSelfCallbackBranchStaysAgentTypeAgnostic(TestCase):
    """Structural lock on the fact the whole #228 design depends on: the
    `--self` branch of `cmd_compact_request` must never grow an
    `agent_type`/`subagent_type` check. Bounded to just that branch (not
    the whole function/file) so it can't pass by coincidence, and not the
    whole file so it can't be defeated by an unrelated docstring mentioning
    those words elsewhere in the function."""

    def test_self_branch_has_no_agent_type_gate(self):
        import airuleset

        src = inspect.getsource(airuleset.cmd_compact_request)
        start = src.index('if getattr(args, "self", False):')
        # bounded by the next top-level branch in the same function
        end = src.index('if getattr(args, "record", False):', start)
        self.assertGreater(end, start)
        branch = src[start:end]
        self.assertNotIn("agent_type", branch)
        self.assertNotIn("subagent_type", branch)


if __name__ == "__main__":
    main()
