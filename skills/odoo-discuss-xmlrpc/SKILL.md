---
name: odoo-discuss-xmlrpc
description: "RENAMED to odoo-client-messaging (#891). Channel/transport specifics: odoo-erp .claude/rules/odoo-task-sync.md. Compose doctrine: skills/odoo-client-messaging/handover-compose.md."
user-invocable: false
---

# odoo-discuss-xmlrpc — RENAMED

**This skill has been renamed to `odoo-client-messaging` (airuleset issue 891).**

- **Channel-agnostic compose doctrine** (owner approval, identity signature,
  family batching, closure protocol): load `odoo-client-messaging`.
- **Channel + transport specifics for odoo-erp** (task chatter, `/json/2`,
  `odoo-task-sync.py`): see the odoo-erp project's own
  `.claude/rules/odoo-task-sync.md`.

This stub is kept for foreign references. Remove it when odoo-erp confirms
its playbooks point at the new name.
