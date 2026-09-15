# Acknowledging a client message — 👷 reaction (WRITE side, #978/#1027/#1033)

**The WRITE-side counterpart of `read-reactions.md` (#745, the READ side).**
When a stream reads a NEW client message in a Discuss thread it owns (a W
`ops-wait` re-entry event, a watch-job check, or a manual read), it MUST
add the ack reaction on THAT client message BEFORE any reply is composed.

The ack emoji is the standing fleet ack emoji — one emoji fleet-wide, so the
client learns its meaning: "evidujeme, pracujeme na tom". **The fleet default
is the WORKER 👷** (U+1F477, pracovnik) — the owner's standing instruction for
his client channels (#1027/#1033, owner escalation 2026-09-14:
„Preco davas oci ked som sa velakrat vyjadril ze treba dat pracovnika!!!“).
When the work is delivered, the reply follows the normal approval flow; the 👷
stays.

## The ack emoji is CONFIGURABLE — one key, `ack_reaction_emoji`

The emoji is NOT hard-coded per stream. It is the single config key
`ack_reaction_emoji` in `ack_reaction.py` (`ack_reaction_emoji(owner, tenant)`):
fleet default 👷, per-owner and per-tenant overridable, and the legacy 👀
(eyes, the #978 default) stays selectable via an override. Every consumer reads
this ONE source — this doctrine, the `add-reaction.md` recipe, the
`stop-check-prose-violations.sh` evidence check, and the watchdog nudge — so the
owner never has to re-teach it per sub-dev.

**An explicit owner instruction about HIS client channel OVERRIDES the fleet
default.** If an owner has told you a specific emoji for his channels and the
fleet default differs, follow the OWNER, and ESCALATE the conflict to airuleset
(a per-owner override in `ack_reaction.py`) instead of silently following the
default — the #978→#1033 regression (26 client messages got 👀 while the owner
had demanded 👷) was exactly the fleet default overriding an explicit owner
instruction with no escalation.

## Standing grant — NO owner text-approval required

A bare reaction (👷 by default) carries NO text, NO client-facing content, and
NO information beyond "we saw it / a worker is on it". It is therefore **NOT**
subject to the per-message owner text-approval rule (`handover-compose.md` first
bullet). The grant is STANDING (fleet-wide, permanent) — the stream adds the
reaction on its own authority, silently, without queuing an owner question.

## Recipe — guarded method (odoo-erp #6808, `company_base`)

Call the guarded reaction method on `mail.message` with the configured emoji
(`ack_reaction_emoji()` — 👷 by default) — the transport recipe is in the
`add-reaction.md` transport companion. Two guarded methods exist on
`mail.message`: `message_reaction_guarded(message_id, content, action)`
(add/remove — the LIVE method used for intake, verified on montalu/miva1) and
the `message_reaction_add_guarded(message_id, content)` sibling (#6808 spec).
Both are designed as ACL-safe public endpoints for `base.group_user` accounts;
each adds the reaction idempotently — a duplicate call on the same message +
emoji is a no-op.

## Availability — check per instance, fall back below

Reaches a client's PROD only once its `company_base` has RELEASED the guarded
method — not the instant it merged upstream.

- A successful call (no exception) means the method is live — use it.
- A transport error naming `message_reaction_add_guarded` /
  `message_reaction_guarded` as an unknown method, or a **404** "method does not
  exist" → not released on this instance yet — fall back below, re-check after
  the next release.

## Fallback — `ack-pending` marker (NEVER a fake ack, NEVER a silent skip)

When the guarded method is not yet live on this instance, the stream
CANNOT add the reaction. In this case:

1. Do NOT silently skip the acknowledgement.
2. Do NOT fake it (no emoji appears on the client's screen → lying).
3. Record `Ack-reaction: pending — <method> not on <instance>` on the ticket
   (`gh issue comment`), so the gap is visible.
4. The ticket carries the `ack-pending` state until the next
   `company_base` release; re-check availability after each release.

## Evidence line — `Ack-reaction:`

Every turn that processes a NEW client message MUST carry an evidence line:

- `Ack-reaction: msg <message_id> 👷` — reaction added successfully (or the
  emoji the owner's override selected).
- `Ack-reaction: pending — <reason>` — method not available, gap recorded.

The `stop-check-prose-violations.sh` check blocks a turn reporting a new
client message without this line (the same enforcement shape as #916's
read-back check); it accepts the configured ack emoji (👷 or the legacy 👀).

## Anti-patterns (all rewordings apply)

- Reading a new client message and composing a reply with no 👷 → **WRONG.**
  The 👷 goes FIRST, before any reply composition.
- Silently skipping the ack because the method is not released → **WRONG.**
  Record `Ack-reaction: pending`.
- Adding a text reply as an "acknowledgement" instead of the emoji → **WRONG.**
  The 👷 is mechanical and instant; a text reply needs owner approval.
- Asking the owner whether to add the 👷 → **WRONG.** Standing grant — add
  it on your own authority.
- Putting 👀 when the owner asked for 👷 (or ignoring an owner's channel
  instruction) → **WRONG.** Follow the owner and escalate the conflict to
  airuleset; the fleet default never overrides an explicit owner instruction.
