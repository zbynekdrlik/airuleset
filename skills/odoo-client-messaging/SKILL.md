---
name: odoo-client-messaging
description: "Channel-agnostic Odoo client messaging: state machine (U/W, tacit/stale, acceptance markers) + compose doctrine (owner approval, signature, batching) in companion handover-compose.md."
user-invocable: false
---

# Odoo Client Messaging — Channel-Agnostic Guidance

**airuleset boundary (#891):** airuleset owns the STATE MACHINE + FORMAT; the
project owns the CHANNEL + MECHANISM. This skill is the fleet-level, channel-
agnostic compose + delivery doctrine — never prescribing Discuss vs task chatter.

## Channel specifics — POINTER to the project

- **odoo-erp:** `.claude/rules/odoo-task-sync.md` — task chatter via
  `scripts/odoo-task-sync.py`, `/json/2` bearer-key transport (odoo-erp issues
  6222, 3693). Discuss is free conversation only (IT-support sub-threads), NOT
  client acceptance.
- **Other projects:** the project's own `.claude/rules/`.

## Intake reaction FIRST (#1027/#1033)

First transition when a stream picks up a client message it will act on: react
👷 (`ack_reaction_emoji`; legacy 👀 selectable) BEFORE filing a ticket or
dispatching a lane — the owner's "being worked on" signal; recipe
`ack-reaction.md`. While a fix lane is in flight: NO interim workaround reply —
reply ONCE after the fix is on PROD (full rule in `handover-compose.md`).

## Acceptance markers (channel-agnostic)

Recognised by the close gate (`discuss_close_guard.py`):
- **Binding:** `Acceptance-thread: <ref>` (generic) / `Discuss-thread:
  <channel-id>` / `discuss.channel_<N>` deep URL (both legacy, kept).
- **Disposition:** `Acceptance-cited: msg <id> [task <id>|thread <id>]` /
  `Acceptance-defer: <reason — siblings #A #B still open>` / legacy
  `Discuss-closed:` / `Discuss-defer:`.

## Companions

- The cross-stream rules for COMPOSE + APPROVAL (what a message must contain;
  owner-approved before posting): `handover-compose.md`.
- Client board `project.task` formatting (name language, description,
  Verifikácia notes, stage discipline, no assignee/@mention):
  `client-board-tasks.md`.
