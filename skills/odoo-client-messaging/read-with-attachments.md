# Reading a client Discuss message — attachments FIRST (ir.attachment)

**A client message may carry an Odoo system attachment (`ir.attachment` on a
`mail.message`) — reading the message means reading BOTH the text AND every
attachment, never the text alone.** This is the READ-side counterpart of
`SKILL.md`'s posting recipe: whenever you fetch a `mail.message` (or a
`discuss.channel.message`) to interpret a client's intent — before answering
it, before filing a ticket from it, before drafting a handover — the fetch
MUST include `attachment_ids`, and every attachment MUST be downloaded and
Read (or viewed) BEFORE you interpret the text. An attachment is a PRIMARY
source, equal to the text, never optional context you can skip.

Incident that created this: an odoo-erp stream read a client's Discuss reply
(mail.message **1742799**) via the API WITHOUT `attachment_ids`, interpreted
the request from the bare text alone, and shipped odoo-erp #5162 with the
wrong/incomplete interpretation — the client had attached a screenshot
(ir.attachment **13204**) circling the EXACT UI element ("aj tu" = the form's
statusbar) that the text alone did not make clear. Corrected only after the
owner asked about the image (odoo-erp #5214). airuleset #709, 2026-08-25/26.

## The recipe (XML-RPC / JSON-RPC)

1. **Fetch the message WITH `attachment_ids`** — never a bare `body`/`subject`
   read:
   ```python
   msgs = models.execute_kw(db, uid, api_key,
       "mail.message", "search_read",
       [[["id", "=", message_id]]],
       {"fields": ["body", "author_id", "attachment_ids", "date"]})
   ```
2. **For every id in `attachment_ids`, download the real bytes** —
   `ir.attachment.read` returns base64 in `datas`:
   ```python
   atts = models.execute_kw(db, uid, api_key,
       "ir.attachment", "read",
       [msgs[0]["attachment_ids"]],
       {"fields": ["name", "mimetype", "datas"]})
   import base64
   for att in atts:
       raw = base64.b64decode(att["datas"])
       path = f"/tmp/{att['name']}"          # use your own scratchpad path
       with open(path, "wb") as f:
           f.write(raw)
   ```
3. **Read it BEFORE interpreting the text** — an image attachment: open the
   downloaded file with the Read tool (renders local image pixels — the same
   no-browser-needed path as the `view-image-urls` skill); a PDF/document
   attachment: Read it too, or convert first if the format needs it. Only
   AFTER seeing every attachment do you interpret what the message is asking.

## Anti-pattern (all rewordings apply)

"Spracoval som správu" / "I processed the message" / "I read the message and
responded" — when the fetch never carried `attachment_ids`, or an attachment
was fetched but never actually downloaded-and-Read — is banned. A message
with an unread attachment is a message you have not actually read. Applies to
every client-message-reading context, not only Discuss: any system
attachment channel (Odoo, a ticket system, a support inbox) that a message
can carry follows the same rule — see `view-image-urls`'s "download it and
Read it" doctrine, generalized here to a SYSTEM attachment channel rather
than a pasted URL.
