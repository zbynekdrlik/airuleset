# Reading a client's REACTION as an answer (mail.message.reaction)

**An emoji REACTION on OUR question message is a full client answer.** The
second READ-side counterpart of `SKILL.md`'s posting recipe (the first is
`read-with-attachments.md`): when a client-question Discuss thread is parked `W`
(`ops-wait`), "did the client reply?" must check `mail.message.reaction` on our
question message, NOT only new messages. A 👍 on the acceptance question IS the
acceptance (owner 2026-08-29 — „palec hore … je plnohodnotna odpoved").
The doctrine lives in `modules/core/statusline-vocabulary.md` W bullet (#745): a reaction is a **plnohodnotná** odpoveď → it clears `ops-wait`.

Incident: a client 👍'd our acceptance question (montalu PROD, discuss.channel_288, mail.message 1739648); the stream read only messages, reported „bez odpovede" 5 days.

## The real-time read recipe — GUARDED METHOD (odoo-erp #5577, `company_base`), never a raw `mail.message.reaction` search_read (#784)

The handover **base.group_user** account gets a **403 on the raw model** (`mail.message.reaction` `search_read`) — BY DESIGN, permanently. odoo-erp **#5577** shipped a **guarded method** instead of widening that ACL — widening was REJECTED upstream (leaks every message's reactor+emoji to every internal user). Call the guarded method, never the raw model:

```python
rx = models.execute_kw(db, uid, api_key, "mail.message", "message_reactions_guarded", [message_id])
# Guarded method on mail.message (company_base, FLEET-WIDE, not montalu-only).
# `content`=emoji, reactor is `partner_id` OR `guest_id` — a Discuss GUEST
# reacts with partner_id=False + guest_id set, so a guest reaction STILL
# counts. A CLIENT partner OR guest row = an answer.
```

Montalu instances also keep the pre-#5577 `montalu_message_reactions(message_id)` alias (thin delegation, back-compat) — prefer the neutral name for any NEW recipe.

## Availability — check per instance, fall back below

Reaches a client's PROD only once that instance's `company_base` has RELEASED #5577 — not the instant it merged upstream.

- **`200`** (a list, possibly empty) → live here, use it.
- **`404`** on `message_reactions_guarded` → not released here yet — fall back below, re-check after the next release.
- The raw model staying **403** is never a sign of breakage.

## Fallback — fresh prod copy (while the guarded method isn't live yet on THIS instance)

Prod-STATE read, self-service path (`autonomous-verification.md` #500/#608), NEVER an honest „nedá sa overiť": `REFRESH-DEV-BOX-FROM-PROD: <stream>`, then `psql` the `mail_message_reaction` table (`SELECT content, partner_id, guest_id FROM mail_message_reaction WHERE message_id = <id>;` — read `guest_id` too, a guest reaction is also the client). Stale by minutes, AUTHORITATIVE for a state read.

## Anti-pattern (all rewordings apply)

"Klient neodpovedal" / "bez odpovede" / a `stale!` reminder on a client-QUESTION `W` thread WITHOUT checking `mail.message.reaction` (via `message_reactions_guarded`, or the fallback above) is banned. Reaching for the raw `search_read` instead of the guarded method is also banned — it 403s by design.
