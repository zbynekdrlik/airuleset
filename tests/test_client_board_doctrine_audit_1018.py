"""#1014/#1018/#1028 — the doctrine-audit must recognise a per-stream
restatement of the client-board-tasks.md doctrine and route it to the RULE.

A stream that restates the client-board chatter/stage doctrine in its own
~/.claude/projects/*/memory/*.md is the exact „ako keby som uz jedneho
neinstruoval" drift #1014 exists to retire. cli_doctrine_audit's allowlist gains
a `client-board-tasks` entry so such a memory is flagged HIGH → rewrite
(archive + a one-line pointer to the fleet rule), while a client-specific board
memory (tenant token in the subject) is only LISTED for human review.
"""

from unittest import TestCase, main

import cli_doctrine_audit as da

MEM = "/home/david3/.claude/projects/-home-david3-x/memory/odoo-client-board-chatter-rules.md"
MEM_TENANT = "/home/david3/.claude/projects/-home-david3-x/memory/slovnormal-board-rules.md"

RESTATEMENT = """\
---
name: odoo-client-board-chatter-rules
description: "How to write to the client's Odoo project.task board and chatter"
metadata:
  node_type: memory
  type: project
---
# Odoo client board chatter rules

Each client board uses a per-board profile. One task = one topic — never a
chat-of-everything; when the client opens a new topic it becomes a new task
(téma pokračuje v úlohe Y). A GitHub needs-answer ticket is only a tracking
mirror; the client answers in the Odoo task. Moving a task to the awaiting
client verification stage requires the handover note.
"""

# Same doctrine, but the SUBJECT names a specific board/tenant (slovnormal =
# david) → MEDIUM/LIST, never a silent auto-rewrite (keeps the client part).
RESTATEMENT_TENANT = """\
---
name: slovnormal-board-rules
description: "slovnormal client board (david) — chatter + stages for Dávid Greňa"
metadata:
  node_type: memory
  type: project
---
# slovnormal board rules

Per-board profile for the slovnormal client board. One task = one topic — never
a chat-of-everything. A needs-answer ticket is a tracking mirror; answers come in
the Odoo task, awaiting client verification before Hotové.
"""


class TestCbAudit1018(TestCase):

    def test_restatement_is_high_rewrite_to_the_rule(self):
        m = da.classify_file(MEM, RESTATEMENT)
        self.assertIsNotNone(m, "a client-board doctrine restatement must be flagged")
        self.assertEqual(m.confidence, da.HIGH)
        self.assertEqual(m.action, da.ACTION_REWRITE)
        self.assertIn("client-board-tasks.md", m.fleet_source)

    def test_tenant_scoped_board_memory_is_medium_list(self):
        m = da.classify_file(MEM_TENANT, RESTATEMENT_TENANT)
        self.assertIsNotNone(m)
        self.assertEqual(m.confidence, da.MEDIUM)
        self.assertEqual(m.action, da.ACTION_LIST)
        self.assertIn("client-board-tasks.md", m.fleet_source)


if __name__ == "__main__":
    main()
