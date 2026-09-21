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

## project.task — a client board task's attachments (#1098)

A client often puts the spec as an IMAGE/spreadsheet in the `project.task`
DESCRIPTION, not the text — see `client-board-tasks.md` rule 15. A board task
carries attachments in THREE places; read ALL of them before filing a ticket
from the task or parking it on `needs-answer`.

1. **`ir.attachment` rows on the task** — `res_model='project.task'`,
   `res_id=<task id>`. Odoo's `ir.attachment._search` silently PREPENDS
   `('res_field', '=', False)` when the domain names neither `id` nor
   `res_field` — that HIDES every field-bound attachment (an inline description
   image is stored with `res_field='description'`). Name `res_field` in TWO
   passes:
   ```python
   base = [["res_model", "=", "project.task"], ["res_id", "=", task_id]]
   unbound = models.execute_kw(db, uid, api_key, "ir.attachment", "search_read",
       [base + [["res_field", "=", False]]],
       {"fields": ["id", "name", "mimetype"]})
   bound = models.execute_kw(db, uid, api_key, "ir.attachment", "search_read",
       [base + [["res_field", "!=", False]]],
       {"fields": ["id", "name", "mimetype", "res_field"]})
   ```
2. **Inline images / files in the description** — extract every attachment id
   from the description HTML. A screenshot is `/web/image/<id>`; a spreadsheet
   dropped in is emitted as `/web/content/<id>?download=true`:
   ```python
   import re
   task = models.execute_kw(db, uid, api_key, "project.task", "read",
       [[task_id]], {"fields": ["description"]})[0]
   ids = [int(m) for m in re.findall(r"/web/(?:image|content)/(\d+)", task["description"] or "")]
   ```
   Caveat: `/web/image/<model>/<id>/<field>` (THREE path segments) carries a
   RECORD id, not an attachment id — do NOT feed it to `ir.attachment.read`.
3. **The task's message attachments** — `mail.message` on
   `model='project.task'`, `res_id=<task id>`; read each message's
   `attachment_ids`:
   ```python
   msgs = models.execute_kw(db, uid, api_key, "mail.message", "search_read",
       [[["model", "=", "project.task"], ["res_id", "=", task_id]]],
       {"fields": ["body", "author_id", "attachment_ids", "date"]})
   ```

**Download + Read every attachment** into
`~/.claude/work-products/<projekt>-podklady-<D.M.YYYY>/t<task>-<att>.<ext>`
(base64 `datas` → bytes) and open each with the Read tool BEFORE interpreting
the task — never the Discuss recipe's `/tmp/{name}` scratch path (a board task's
podklady are a durable work-product, #1098):
```python
import base64, pathlib
d = pathlib.Path.home() / ".claude" / "work-products" / f"{projekt}-podklady-{datum}"
d.mkdir(parents=True, exist_ok=True)
(d / f"t{task_id}-{att['id']}.{ext}").write_bytes(base64.b64decode(att["datas"]))
```
**Cite** each att-id + the values you read in the ticket body — e.g.
`att 37652: rozmery 1575/1924`; a question to the owner about the task carries
`Prílohy: att <ids> prečítané (<hodnoty>)` or `Prílohy: žiadne` (enforced by
Check 9 in `stop-check-question-quality.sh`).

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
