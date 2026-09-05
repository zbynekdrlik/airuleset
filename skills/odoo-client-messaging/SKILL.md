---
name: odoo-client-messaging
description: Channel-agnostic client messaging guidance for Odoo sub-dev streams. airuleset owns the STATE MACHINE (labels, U/W partition, tacit/stale, acceptance markers) and the COMPOSE doctrine (owner approval, identity signature, family batching) in the companion handover-compose.md. The project owns the CHANNEL (task chatter, Discuss, etc.) and MECHANISM (transport, scripts). For odoo-erp channel specifics see its own .claude/rules/odoo-task-sync.md.
user-invocable: false
---

# Odoo Client Messaging — Channel-Agnostic Guidance

**airuleset boundary (#891):** airuleset owns the STATE MACHINE + FORMAT;
the project owns the CHANNEL + MECHANISM. This skill is the fleet-level
compose and delivery doctrine — channel-agnostic, never prescribing
Discuss vs task chatter vs any other channel.

## Channel specifics — POINTER to the project

For the actual channel, transport, and scripts:

- **odoo-erp:** `.claude/rules/odoo-task-sync.md` — task chatter via
  `scripts/odoo-task-sync.py`, `/json/2` bearer-key transport (odoo-erp
  issues 6222, 3693). Discuss is for free conversation only (IT-support
  sub-threads), NOT client acceptance.
- **Other projects:** see the project's own `.claude/rules/` for its
  client messaging channel.

## Acceptance markers (fleet-level, channel-agnostic)

The close-time gate (`discuss_close_guard.py`) recognises:

**Binding** (ticket carries a client thread — either binds):
- `Acceptance-thread: <free ref>` — the generic form
- `Discuss-thread: <channel-id>` — legacy, kept forever
- `discuss.channel_<N>` deep-URL token — legacy auto-bind

**Disposition** (any satisfies the close gate):
- `Acceptance-cited: msg <message-id> [task <id>|thread <id>]` — closed form
- `Acceptance-defer: <reason — siblings #A #B still open>` — defer form
- `Discuss-closed: <msg-id>` / `Discuss-defer: <reason>` — legacy, kept forever

## Composing a client message — the fleet doctrine

The cross-stream rules for COMPOSE (what a message must contain) and
APPROVAL (every client message approved by the owner before posting) live
in the companion `handover-compose.md` in this directory.
