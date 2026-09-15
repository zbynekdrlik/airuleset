# add-reaction — XML-RPC / JSON-RPC recipe for the guarded ack reaction

**The transport recipe for adding the ack reaction on a client Discuss message.**
Doctrine (WHEN to ack, the standing grant, WHICH emoji) lives in
`skills/odoo-client-messaging/ack-reaction.md` — this file is the HOW. The emoji
is the fleet default WORKER 👷 (U+1F477) unless a per-owner/per-tenant override
selects another via the `ack_reaction_emoji` config key (`ack_reaction.py`);
the legacy 👀 stays selectable there.

## Recipe — guarded method (odoo-erp #6808 / `company_base`)

```python
# LIVE method (add/remove), verified on montalu/miva1 PROD via /json/2:
models.execute_kw(
    db, uid, api_key,
    "mail.message", "message_reaction_guarded",
    [message_id, "👷", "add"],          # 👷 = ack_reaction_emoji() default
)

# #6808-spec sibling (add-only), where released:
models.execute_kw(
    db, uid, api_key,
    "mail.message", "message_reaction_add_guarded",
    [message_id, "👷"],
)
```

The guarded methods on `mail.message` are designed as ACL-safe public endpoints
for `base.group_user` accounts. `message_reaction_guarded(message_id, content,
action)` is the live add/remove method used at intake; `message_reaction_add_guarded`
(#6808 spec) is the add-only sibling. Each adds the reaction idempotently — a
duplicate call on the same message + emoji is a no-op. Read side:
`message_reactions_guarded` (#5577).

## Availability — check per instance, fall back to ack-pending

Reaches a client's PROD only once its `company_base` has RELEASED the method —
not the instant it merged upstream.

- A successful call (no exception) means the method is live — use it.
- A transport error naming `message_reaction_add_guarded` /
  `message_reaction_guarded` as an unknown method, or a **404** "method does not
  exist" → not released on this instance yet — record `Ack-reaction: pending`
  per the doctrine in `ack-reaction.md`, re-check after the next release.
