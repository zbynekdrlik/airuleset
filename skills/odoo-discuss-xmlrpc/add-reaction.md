# add-reaction — XML-RPC / JSON-RPC recipe for message_reaction_add_guarded

**The transport recipe for adding a 👀 ack reaction on a client Discuss message.**
Doctrine (WHEN to ack, the standing grant) lives in
`skills/odoo-client-messaging/ack-reaction.md` — this file is the HOW.

## Recipe — guarded method (odoo-erp #6808, `company_base`)

```python
models.execute_kw(
    db, uid, api_key,
    "mail.message", "message_reaction_add_guarded",
    [message_id, "👀"],
)
```

The guarded method on `mail.message` (the WRITE sibling of #5577's
`message_reactions_guarded` READ method) is designed as an ACL-safe
public endpoint for `base.group_user` accounts (per the #6808 spec,
not yet released as of 2026-09-10). Once shipped, it adds the reaction
idempotently — a duplicate call on the same message + emoji is a no-op.

## Availability — check per instance, fall back to ack-pending

Reaches a client's PROD only once its `company_base` has RELEASED #6808 —
not the instant it merged upstream.

- A successful call (no exception) means the method is live — use it.
- A transport error naming `message_reaction_add_guarded` as an unknown
  method, or a **404** "method does not exist" → not released on this
  instance yet — record `Ack-reaction: pending` per the doctrine in
  `ack-reaction.md`, re-check after the next release.
