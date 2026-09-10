# Acknowledging a client message — 👀 reaction (WRITE side, #978)

**The WRITE-side counterpart of `read-reactions.md` (#745, the READ side).**
When a stream reads a NEW client message in a Discuss thread it owns (a W
`ops-wait` re-entry event, a watch-job check, or a manual read), it MUST
add a 👀 reaction on THAT client message BEFORE any reply is composed.

The 👀 is the standing fleet ack emoji — one emoji fleet-wide, so the client
learns its meaning: "evidujeme, pracujeme na tom". When the work is delivered,
the reply follows the normal approval flow; the 👀 stays.

## Standing grant — NO owner text-approval required

A bare 👀 reaction carries NO text, NO client-facing content, and NO
information beyond "we saw it". It is therefore **NOT** subject to the
per-message owner text-approval rule (`handover-compose.md` first bullet).
The grant is STANDING (fleet-wide, permanent) — the stream adds the reaction
on its own authority, silently, without queuing an owner question.

## Recipe — guarded method (odoo-erp #6808, `company_base`)

```python
models.execute_kw(
    db, uid, api_key,
    "mail.message", "message_reaction_add_guarded",
    [message_id, "👀"],
)
```

The guarded method on `mail.message` (the WRITE sibling of #5577's
`message_reactions_guarded` READ method) is an ACL-safe public endpoint
for `base.group_user` accounts. It adds the reaction idempotently — a
duplicate call on the same message + emoji is a no-op, never an error.

## Availability — check per instance, fall back below

Reaches a client's PROD only once its `company_base` has RELEASED #6808 —
not the instant it merged upstream.

- A successful call (no exception) means the method is live — use it.
- A `Fault` naming `message_reaction_add_guarded` as unknown (XML-RPC), or
  a **404** "method does not exist" via JSON-2 → not released on this
  instance yet — fall back below, re-check after the next release.

## Fallback — `ack-pending` marker (NEVER a fake ack, NEVER a silent skip)

When the guarded method is not yet live on this instance, the stream
CANNOT add the reaction. In this case:

1. Do NOT silently skip the acknowledgement.
2. Do NOT fake it (no emoji appears on the client's screen → lying).
3. Record `Ack-reaction: pending — message_reaction_add_guarded not on
   <instance>` on the ticket (`gh issue comment`), so the gap is visible.
4. The ticket carries the `ack-pending` state until the next
   `company_base` release; re-check availability after each release.

## Evidence line — `Ack-reaction:`

Every turn that processes a NEW client message MUST carry an evidence line:

- `Ack-reaction: msg <message_id> 👀` — reaction added successfully.
- `Ack-reaction: pending — <reason>` — method not available, gap recorded.

The `stop-check-prose-violations.sh` check blocks a turn reporting a new
client message without this line (the same enforcement shape as #916's
read-back check).

## Anti-patterns (all rewordings apply)

- Reading a new client message and composing a reply with no 👀 → **WRONG.**
  The 👀 goes FIRST, before any reply composition.
- Silently skipping the ack because the method is not released → **WRONG.**
  Record `Ack-reaction: pending`.
- Adding a text reply as an "acknowledgement" instead of the emoji → **WRONG.**
  The 👀 is mechanical and instant; a text reply needs owner approval.
- Asking the owner whether to add the 👀 → **WRONG.** Standing grant — add
  it on your own authority.
