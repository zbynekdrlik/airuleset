# Reading a client's REACTION as an answer (mail.message.reaction)

**An emoji REACTION on OUR question message is a full client answer.** The
second READ-side counterpart of `SKILL.md`'s posting recipe (the first is
`read-with-attachments.md`): when a client-question Discuss thread is parked `W`
(`ops-wait`), "did the client reply?" must check `mail.message.reaction` on our
question message, NOT only new messages. A 👍 on the acceptance question IS the
acceptance (owner 2026-08-29 — „palec hore … je plnohodnotna odpoved").
The doctrine lives in `modules/core/statusline-vocabulary.md` W bullet (#745): a reaction is a **plnohodnotná** odpoveď → it clears `ops-wait` (supervisor, reaction as evidence) and NEVER earns a needless `stale!` reminder.

Incident: a client 👍'd our acceptance question (montalu PROD, discuss.channel_288, mail.message 1739648); the stream read only messages, reported „bez odpovede" 5 days.

## The read recipe (XML-RPC / JSON-RPC) — once the ACL below is live

Reactions are their own model, read `mail.message.reaction` via `search_read` filtered by `message_id` (our question's id):

```python
rx = models.execute_kw(db, uid, api_key,
    "mail.message.reaction", "search_read",
    [[["message_id", "=", question_msg_id]]],
    {"fields": ["content", "partner_id", "guest_id", "message_id"]})
# `content` = the emoji ("👍"); the reactor is `partner_id` OR `guest_id` — a
# Discuss GUEST reacts with `partner_id = False` + a set `guest_id`, so a client
# reaction from a guest session STILL counts (never read it as no-reaction).
# A CLIENT partner OR guest row on our question = an answer (escalate if unclear).
```

## The obstacle TODAY — real-time read is 403 (self-service fallback, never "can't verify")

The handover **base.group_user** account (the stream's read-only handover API user) currently gets a **403** on `mail.message.reaction` (even via `reaction_ids` on our own message), so real-time reads over `/json/2` do NOT work yet. This is a prod-STATE read with a self-service path (`autonomous-verification.md` #500/#608), NEVER an honest „nedá sa overiť":

- **Today:** read reactions off a FRESH prod copy — `REFRESH-DEV-BOX-FROM-PROD: <stream>`, then `psql` the `mail_message_reaction` table (`SELECT content, partner_id, guest_id FROM mail_message_reaction WHERE message_id = <id>;` — read `guest_id` too, a guest reaction is also the client). Stale by minutes, AUTHORITATIVE for a state read.
- **Real-time:** available once the ACL fix below lands.

## Pending ACL fix (cross-repo — filed in odoo-erp)

Real-time unblocks after the **odoo-erp** **ACL** fix granting the handover account read on `mail.message.reaction` (**company_base**, shared-benefit). Until it releases to PROD, use the fresh-prod-copy path; after it, switch to the real-time `search_read` recipe.

## Anti-pattern (all rewordings apply)

"Klient neodpovedal" / "no reply / 5 days silent" / a `stale!` reminder on a client-QUESTION `W` thread WITHOUT having checked `mail.message.reaction` on our question message is banned — a reaction you never read is an answer you missed. Reading only new messages is reading half the thread.
