# Client Board Task Attachments — recipe (#1098)

Companion of `client-board-tasks.md` (CORE). Carries rule 15 (moved verbatim, #1102) + the `project.task` attachment recipe (relocated verbatim out of `read-with-attachments.md`, #1098). Auto-loads on a `project.task` attachment read (its own row, #1102); also read on demand from the `read-with-attachments.md` pointer.

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

---

### 15. Prílohy v popise úlohy = primárny zdroj (#1098)

**Pri KAŽDEJ novej/zmenenej board úlohe — a vždy pred filing ticketu aj pred
parkovaním — prečítaj VŠETKY prílohy** z troch zdrojov (plný recept v `client-board-attachments.md`):

- `ir.attachment` na úlohe — `search_read([["res_model","=","project.task"],["res_id","=",tid],["res_field","=",False]], ["id","name","mimetype"])` (Odoo skryto predradí `('res_field','=',False)` — polia viazané prílohy vidno len s `["res_field","!=",False]`)
- att-id z popisu — `re.findall(r"/web/(?:image|content)/(\d+)", desc or "")` (vložený Excel = `/web/content/<id>`)
- prílohy zo správ úlohy — `search_read("mail.message",[["model","=","project.task"],["res_id","=",tid]],["attachment_ids"])`

Stiahni každú do `~/.claude/work-products/<projekt>-podklady-<D.M.YYYY>/t<task>-<att>.<ext>` a Read ju. V GH ticket-e cituj KAŽDÉ att-id + hodnoty z neho (úplnosť: každé att-id otvorenej board úlohy sa vyskytuje v body/komentároch ticketu — stream to audituje skriptom).

`needs-answer` daj LEN na to, čo v prílohe NIE JE — a aj vtedy implementuj s čestným (deklarovaným) null defaultom a pokračuj, NIKDY nečakaj na odpoveď.

KAŽDÁ otázka nesúca Odoo task URL (aj interná dev úloha) nesie riadok `Prílohy: att <ids> prečítané (<hodnoty>)` alebo `Prílohy: žiadne` (Check 9 v `stop-check-question-quality.sh`).

Owner 21.9.2026 verbatim (rule 12): „preco tuto ulohu vobec neriesis tam v popise je screenshot", „aj ostatne ulohy skontroluj popis fotky".
