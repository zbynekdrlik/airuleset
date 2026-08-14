---
name: odoo-discuss-xmlrpc
description: Posting a message to an Odoo Discuss channel over XML-RPC / JSON-RPC (discuss.channel.message_post) from stream automation. The verified recipe — body_is_html=True for any HTML body (NEVER post-then-rewrite: the bus push already delivered the escaped body to live clients), address the channel BY NAME as a sub-thread, always include the owner as a control-ping recipient, then post-verify. Load before writing or reviewing ANY Odoo Discuss message_post call.
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
`message_update_content`, API-key gotchas) see the odoo-erp-owned
`montalu-odoo-19` skill — this one is the focused Discuss-posting recipe.

## The one rule: a SINGLE `message_post(..., body_is_html=True)`

`message_post(body=<str>)` runs the body through `plaintext2html` (HTML-escape)
before it stores the message AND fires the live `discuss.channel/new_message`
bus push. So a raw HTML string sent without the flag is escaped — clients see
literal tags. Pass **`body_is_html=True`** and Odoo stores + pushes the HTML
verbatim (Odoo 19 `mail_thread.py` ~2188/2220/2302).

## BANNED anti-pattern: post-then-rewrite

Do **NOT** post the escaped body and then "fix" it by re-reading and
`mail.message.write({'body': ...})`-ing the unescaped version back. The bus push
**already delivered the escaped body to every live client** at post time, and a
raw `write` emits **zero** bus notifications — so live clients keep the broken
version until a manual reload (F5). The DB looking correct afterwards hides a
delivery that was already wrong. There is no "correct it after" — get it right
in the single post.

(If you genuinely must EDIT an already-posted message, use
`discuss.channel.message_update_content`, which DOES emit a `mail.record/insert`
bus notification. A plain `write` never does.)

## Channel + recipients

- **Address the channel BY NAME**, and post into a **sub-thread under an
  existing channel** — never create a new top-level channel / group for a post.
  (Odoo 19 removed `channel_get` / `create_group`; creating channels is a
  separate, deliberate act, not something a post does implicitly.)
- **`partner_ids`: the named recipients AND ALWAYS the owner** as a control ping,
  so a broken or missing delivery is always visible to the owner (odoo-erp
  #4006/#4011). Never post to a client thread without the owner on it.
- Use the correct **mention anchor** format for any `@`-mention
  (`<a href="#" data-oe-model="res.partner" data-oe-id="<pid>">@Name</a>`
  inside the HTML body) so mentions resolve instead of rendering as text.

## Minimal correct example

```python
import xmlrpc.client

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, login, api_key, {})
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

body_html = "<p>Objednávka <b>2041</b> potvrdená.</p>"

# ONE post, HTML flagged, owner always on partner_ids.
res = models.execute_kw(db, uid, api_key,
    "discuss.channel", "message_post",
    [channel_id],
    {
        "body": body_html,
        "body_is_html": True,          # <-- non-negotiable for any HTML body
        "message_type": "comment",
        "partner_ids": [owner_pid, *recipient_pids],
    })
message_id = res[0] if isinstance(res, list) else res  # JSON/XML-RPC returns [id]
```

## Post-verification (do it every time)

1. Read the stored body back and assert it has **no `&lt;`** (i.e. it was NOT
   escaped) — `mail.message.read([message_id], ["body"])`.
2. Assert the `mail.notification` rows for the message are **`sent`** (not
   `exception`) for each recipient — that is proof the bus actually delivered.
3. If either fails, the post is broken — fix the call, do not rewrite the DB.

## Related

- `montalu-odoo-19` (odoo-erp-owned) — fuller Odoo-19 dev gotchas incl. the
  Discuss API section this recipe distills.
