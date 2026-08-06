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

A fresh-context adversarial review of the first draft found two real
problems (both fixed before this test file was finalized, both locked
below so they cannot silently regress):

- The first draft's trigger included a bare `✅ DONE:` line, not just the
  full `## ✅ Work Complete` heading. Since the `self-callback` origin
  deliberately SKIPS the #99/#48 substantiality gates (same exemption the
  autopilot-worker's own boundary uses -- "the boundary is the TICKET, not
  the size of its diff", `watchdog/__init__.py`'s own `#126` comment), a
  bare `✅ DONE:` (which can mean nothing more than "this turn is over") is
  NOT a safe trigger for that exemption -- it would compact on every
  trivial exchange. The fix restricts the trigger to the full heading.
- The first draft told the session to call `--self` AFTER writing the
  report, "as your OWN last tool call" -- risking the report no longer
  being `last_assistant_message`, which several Stop hooks (the phone
  ping, the passive fallback itself, the report-structure gate) key on.
  The fix reorders: call `--self` FIRST, then write the report as the
  turn's actual final content.

These tests lock three things:
1. `modules/core/completion-report.md` carries the new teaching (so it
   cannot silently regress in a future edit) -- with POLARITY-sensitive
   assertions, not bare keyword presence, so a rewrite that reverses the
   meaning of a NEVER (e.g. "ALWAYS mid-work qualifies") still fails.
2. The trigger is scoped to the FULL `## ✅ Work Complete` heading, never
   a bare `✅ DONE:` marker alone.
3. `cmd_compact_request`'s `--self` branch, `deliver_compact_self`, and
   `resolve_self_pane` in this repo stay agent-type-agnostic -- the
   structural fact the whole design rests on. A future accidental gate
   addition in ANY of the three (not just the CLI branch) would make the
   completion-report teaching wrong without touching completion-report.md
   at all, so all three are scanned, not just the entry point.
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

    def test_trigger_is_the_full_heading_never_a_bare_done_marker(self):
        t = read(self.MOD)
        low = t.lower()
        self.assertIn("never a bare", low)
        self.assertIn("## ✅ work complete", low)
        # the reasoning (#99/#48 exemption) must actually be present, not
        # just the restriction itself -- else a future editor could widen
        # the trigger back to a bare marker without understanding why it
        # was narrowed.
        self.assertTrue("#99" in t and "#48" in t)

    def test_call_happens_before_the_report_not_after(self):
        t = read(self.MOD)
        low = t.lower()
        self.assertIn("first", low)
        self.assertIn("before writing the report", low)
        # the rejected ordering must be explicitly named as rejected, not
        # merely absent -- a bare "call it first" sentence could still be
        # satisfied by a rewrite that puts it back after the report as
        # long as SOME "first" appears elsewhere in the file.
        self.assertTrue(
            "never after" in low or "not after" in low,
            "must explicitly reject calling --self AFTER the report",
        )

    def test_never_right_after_answering_a_question(self):
        t = read(self.MOD)
        # polarity: forbid the INVERSION of this NEVER, not just require
        # the phrase to appear somewhere in the file.
        self.assertRegex(
            t, r"NEVER right after just answering a question",
        )
        self.assertNotRegex(
            t.upper(), r"ALWAYS RIGHT AFTER (JUST )?ANSWERING A QUESTION",
        )
        low = t.lower()
        self.assertTrue(
            "not a completion boundary" in low
            or "resumption" in low,
            "must explain WHY answering a question is not itself a "
            "compaction boundary",
        )

    def test_never_mid_work(self):
        t = read(self.MOD)
        # polarity: the literal NEVER-mid-work bullet must be present,
        # and no rewrite may flip it to a permissive "ALWAYS" form.
        self.assertRegex(t, r"NEVER mid-work")
        self.assertNotRegex(t.upper(), r"ALWAYS MID-WORK")

    def test_durability_precondition_present(self):
        t = read(self.MOD)
        low = t.lower()
        # a clean ✅/no-⏳ report proves the TURN ended, not that the work
        # is durable -- the compaction is unsafe if the only record of
        # what happened lives in the conversation itself.
        self.assertTrue(
            "only record" in low or "only in this chat" in low,
            "must guard against compacting away work that has no durable "
            "record outside the conversation",
        )

    def test_references_the_deciding_tickets(self):
        t = read(self.MOD)
        self.assertTrue("#225" in t or "#228" in t)

    def test_fallback_hook_still_documented_as_the_safety_net(self):
        t = read(self.MOD)
        self.assertIn("notify-compact-request.sh", t)


class TestSelfCallbackStaysAgentTypeAgnostic(TestCase):
    """Structural lock on the fact the whole #228 design depends on: no
    part of the `--self` call chain may grow an `agent_type`/
    `subagent_type` check. Scoped to the THREE functions that make up the
    chain (never the whole file, which would be defeated by an unrelated
    docstring mentioning those words elsewhere), and NOT just the CLI
    entry point -- a fresh-context adversarial review demonstrated that a
    gate inserted inside `deliver_compact_self` or `resolve_self_pane`
    (where a future editor would most naturally add "only for the
    autopilot-worker" logic, since that is where the session is actually
    resolved) was invisible to a scan bounded to the CLI branch alone."""

    GATE_WORDS = ("agent_type", "subagent_type")

    def _assert_clean(self, src, label):
        for word in self.GATE_WORDS:
            self.assertNotIn(
                word, src,
                "%s must stay agent-type-agnostic -- found %r" % (label, word),
            )

    def test_self_branch_has_no_agent_type_gate(self):
        import airuleset

        src = inspect.getsource(airuleset.cmd_compact_request)
        start = src.index('if getattr(args, "self", False):')
        # bounded by the next top-level branch in the same function
        end = src.index('if getattr(args, "record", False):', start)
        self.assertGreater(end, start)
        self._assert_clean(src[start:end], "cmd_compact_request's --self branch")

    def test_deliver_compact_self_has_no_agent_type_gate(self):
        import watchdog

        self._assert_clean(
            inspect.getsource(watchdog.deliver_compact_self),
            "deliver_compact_self",
        )

    def test_resolve_self_pane_has_no_agent_type_gate(self):
        import watchdog

        self._assert_clean(
            inspect.getsource(watchdog.resolve_self_pane),
            "resolve_self_pane",
        )


if __name__ == "__main__":
    main()
