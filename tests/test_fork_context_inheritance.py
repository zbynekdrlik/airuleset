"""`fork` inherits the parent's UNEXECUTED plans too (#50, 2026-07-27).

Incident (restreamer autopilot-worker, 2026-07-25, batch #267/#268/#281): late
in a long session, a `fork` was dispatched for a narrow post-deploy check (2 MCP
calls + a Playwright DOM read). The fork inherited the parent's full
conversation, which by then contained three ready-to-run
`airuleset.py notify --run-card ...` command lines the parent had not sent yet,
plus the parent's own meta-rules about polling transcripts for subagent
completion. The fork dispatched a nested agent, that layer started inspecting
its own `subagents/*.jsonl` for "fork completion status", and three
`~/.claude/autopilot-notify-sent/restreamer#{267,268,281}` dedup claims appeared
minutes in -- i.e. it executed the parent's pending broader task instead of the
narrow directive it was given, and returned a bare `⏳ WORKING` placeholder.

Swept before writing anything: no module, skill or agent file stated what fork
inherits or when to use it. Every `fork` hit in `modules/` was an unrelated word
("design fork", the `fork-no-merge` authority profile). The only description
anywhere was a comment inside `pre-agent-validate-subagent-type.sh` -- on no
agent's context path. A live run of `inject-situational-rule.sh` against a real
Agent payload confirmed `fork` and `inherit` both absent from what it injects.

Deliberately no hook: the ticket's own conclusion, confirmed here, is that "this
fork is about to act on stale inherited context" has no static signature -- the
PreToolUse payload holds only the narrow prompt, which looks correct. This is a
SELECTION rule, and the selection happens in the model, so it goes where the
selection happens: the skill body (injected at every Agent dispatch) and the
always-on module. The always-on copy carries the weight here -- the incident
shape is a fork dispatched LATE in a long session, by which time the hook's
once-per-session injection was spent hours earlier.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent

MODULE = "modules/core/subagent-continuation.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestAlwaysOnModuleCarriesTheRule(TestCase):
    """The hook injects once per session; the incident is a LATE dispatch."""

    def test_module_carries_the_fork_selection_rule(self):
        t = read(MODULE)
        self.assertIn("fork", t)
        self.assertIn("not executed yet", t.lower())

    def test_module_stays_short(self):
        """One paragraph, not a second copy of the skill."""
        self.assertLess(len(read(MODULE).splitlines()), 24)


if __name__ == "__main__":
    main()
