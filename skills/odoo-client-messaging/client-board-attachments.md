# Client Board Task Attachments — recipe (#1098)

The extended `project.task` board-task attachment recipe, relocated VERBATIM out of the injected `read-with-attachments.md` so the #745 three-way co-fire (comprehensive-logging + read-attachments + read-reactions) fits under `MAX_TOTAL=14000` (`hooks/inject-situational-rule.sh`, #1098) — nothing is condensed; this file has NO situational-trigger row (never auto-injected) and is read on demand from the pointer in `read-with-attachments.md` and from rule 15 in `client-board-tasks.md`.

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
