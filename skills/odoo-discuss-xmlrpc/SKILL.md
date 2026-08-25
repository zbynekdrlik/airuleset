---
name: odoo-discuss-xmlrpc
description: Posting a message to an Odoo Discuss channel over XML-RPC / JSON-RPC (discuss.channel.message_post) from stream automation, AND composing the client handover proposal that message carries. The verified recipe — body_is_html=True for any HTML body (NEVER post-then-rewrite: the bus push already delivered the escaped body to live clients), address the channel BY NAME as a sub-thread, always include the owner as a control-ping recipient, then post-verify. The canonical cross-stream handover-proposal rules (complete proposal in the chat, a direct deep-link URL to the live feature, explicit owner membership, only functions already live on PROD, the self-blame reassurance) live in the companion handover-compose.md. Load before writing or reviewing ANY Odoo Discuss message_post call, and before drafting a handover Discuss thread for a client.
user-invocable: false
---

# Odoo Discuss over XML-RPC — the verified message_post recipe

**Load this before writing or reviewing any code that posts to an Odoo Discuss
channel over XML-RPC / JSON-RPC** (a `discuss.channel` / `mail.thread`
`message_post`). Incident that created it: an odoo-erp stream posted HTML bodies
WITHOUT `body_is_html=True` and clients saw raw `&lt;p&gt;/&lt;b&gt;` — **twice
in one day, on PROD** (odoo-erp thread 258; airuleset #464 → #465). The
"documented fix" of rewriting the body after posting is **wrong** (see below).

For the fuller Odoo-19 API surface (channel create in 19, the `[id]` unwrap,
API-key gotchas) see the odoo-erp-owned `montalu-odoo-19` skill — this one is
the focused Discuss-posting recipe.

## The one rule: a SINGLE `message_post(..., body_is_html=True)`

`message_post(body=<str>)` **escapes** a plain-`str` body (markupsafe `escape()`)
before it stores the message AND fires the live `discuss.channel/new_message`
bus push — so raw HTML sent as a plain string reaches clients as literal tags.

- **In-process Odoo Python** you would pass a `Markup` body (`body=Markup("<p>…</p>")`)
  — `escape()` keeps a `Markup` verbatim.
- **Over XML-RPC / JSON-RPC (this skill's context) you CANNOT** — a `Markup`
  object serializes to a plain `str` on the wire and gets re-escaped server-side.
  So pass a `str` body **plus `body_is_html=True`**, which Odoo's own docstring
  marks *"to be used only for RPC calls"*: it wraps the str in `Markup`
  server-side so the HTML is kept, not escaped. Verified in Odoo 19
  `addons/mail/models/mail_thread.py` — `def message_post(..., body_is_html=False, …)`
  (~L2201), docstring (~L2233), the wrap (~L2315-2318).

Two caveats from that source, both benign for stream automation:
- Odoo only honors `body_is_html` for an **internal user** — integration /
  system-admin RPC users (what a stream uses) qualify; a portal/public user
  would still get the body escaped.
- For an internal user it logs a harmless `"Posting HTML message using
  body_is_html=True, use a Markup object instead"` warning. That is a log nudge
  toward the in-process `Markup` path, **not** an error — the post is correct.
  (Do not "fix" the warning by dropping the flag; over RPC the flag is the path.)

## BANNED anti-pattern: post-then-rewrite

Do **NOT** post the escaped body and then "fix" it by re-reading and
`mail.message.write({'body': ...})`-ing the unescaped version back. Two reasons
it cannot work:

1. The bus push **already delivered the escaped body to every live client** at
   post time — a later DB change cannot un-send it.
2. A raw `mail.message.write({'body': ...})` emits **zero** bus notifications, so
   even the rewrite is invisible to connected clients until a manual reload (F5).

The DB looking correct afterwards just hides a delivery that was already wrong.
There is no reliable "correct it after" over RPC — get it right in the single
`body_is_html=True` post.

## Channel + recipients

- **Address the channel BY NAME**, and post into a **sub-thread under an
  existing channel** — never create a new top-level channel / group for a post.
  (Odoo 19 removed the public `channel_get` / `create_group`; creating a channel
  is a separate, deliberate act, not something a post does implicitly.)
- **`partner_ids`: the named recipients AND ALWAYS the owner** as a control ping,
  so a broken or missing delivery is always visible to the owner (odoo-erp
  #4006/#4011). Never post to a client thread without the owner on it.
- **Mention anchors are MANDATORY — EVERY addressee of a client message is
  REALLY @mentioned in the HTML body, alongside `partner_ids` (#702).**
  `partner_ids` drives only delivery + the owner control ping; the MENTION
  notification (a mentions-only client's ping) fires only from the embedded
  partner-mention anchor Odoo's own composer emits (an `<a>` carrying
  `data-oe-model="res.partner"` + `data-oe-id="<pid>"`) — hook-enforced.
  **Verify the exact attribute set against a real mention posted through the
  19.0 Discuss composer** before relying on a hand-built anchor; the markup
  has changed across versions.

## Minimal correct example

```python
import xmlrpc.client

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, login, api_key, {})   # login must be an internal user
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

body_html = ('<p><a href="/odoo/res.partner/7" class="o_mail_redirect" data-oe-id="7"'
             ' data-oe-model="res.partner">@Peter</a> Objednávka <b>2041</b> potvrdená.</p>')

# ONE post, HTML flagged, owner always on partner_ids.
res = models.execute_kw(db, uid, api_key,
    "discuss.channel", "message_post",
    [channel_id],
    {
        "body": body_html,
        "body_is_html": True,          # <-- non-negotiable for any HTML body over RPC
        "message_type": "comment",
        "partner_ids": [owner_pid, *recipient_pids],
    })
# message_post returns the mail.message record; over JSON/XML-RPC that
# serializes to a list [id] (montalu-odoo-19 §113) — unwrap defensively.
message_id = res[0] if isinstance(res, list) else res
```

## Post-verification (do it every time)

1. **Prove the HTML survived:** read the stored body back
   (`mail.message.read([message_id], ["body"])`) and assert the intended TAGS are
   present (e.g. it contains `<b>` / `<p>`), i.e. it was stored as HTML — NOT
   escaped to `&lt;b&gt;`. (Assert the tags render, not merely "no `&lt;`" — a
   legitimate `<` in text such as "5 < 10" is correctly escaped and would false-
   positive.)
2. **Delivery, per channel:** the live channel render is the fire-and-forget bus
   push and leaves NO `mail.notification` row, so it is not provable from
   `mail.notification`. `mail.notification` rows being `sent` (not `exception`)
   prove **email / inbox** dispatch to any NOTIFIED partners (`partner_ids`) —
   check them for the owner + recipients, but do not read them as proof the
   in-channel bus render succeeded.
3. If (1) fails, the post is broken — fix the call, do not rewrite the DB.

## Composing the client handover proposal (before you post)

The recipe above is the SEND. Before you post a client PROD Discuss handover
thread, its PROPOSAL (shown to the owner for approval) and its message body must
follow the canonical cross-stream rules in the companion file
**`handover-compose.md`** (same directory). That file auto-loads at proposal
time via its own situational trigger; kept separate from this recipe so the
recipe stays lean.
