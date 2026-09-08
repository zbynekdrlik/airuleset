# Client Board Tasks — project.task Formatting Doctrine

**Canonical rule for HOW a stream writes to a client's Odoo project board
(project.task).** All streams, all boards (montalu, slovnormal, miva).
Origin: operator directive (montalu4, 2026-09-08); #949; lint odoo-erp#6605.

---

## 1. Task name — CLIENT language

The task name (`project.task.name`) is written v jazyku KLIENTA (dnes
slovensky pre každý board — montalu, slovnormal, miva) — nikdy anglicky,
nikdy interný/developer žargón. A task name the client's employee cannot
understand without asking defeats the board's purpose.

## 2. Description — 2–4 sentences for the employee

The description (`project.task.description`) carries **2–4 plain sentences
telling the employee what was done**, followed by `Je nasadené. (#N)`.

**BANNED in the description** (all rewordings): Discuss thread names /
channel ids, `PROD` / environment names, version numbers, PR / commit refs,
internal notes / root-cause analysis, anything the employee cannot act on.

## 3. Verifikácia chatter note — MANDATORY on stage transition

Moving a task to the **Verifikácia** stage REQUIRES a chatter note
(`message_post` on the task) with exactly these sections, in this order:

1. **Čo** — one sentence: what was delivered
2. **Kde** — the menu path AND a functional `https://` deep-link URL to the
   exact page/record/action on the client's PROD, verified 200 before posting
   (the `handover-compose.md` URL rule applies here too — never a bare menu
   path, never the instance homepage)
3. **Čo skúsiť** — one sentence: what the employee should try / verify
4. **`stačí 👍`** — literal closing line (tells the client a 👍 reaction
   confirms acceptance; the `read-reactions.md` companion detects the reaction)

The note CONTENT is **PLAIN PROSE** (no rich formatting, no `@`-mention
anchors, no `partner_ids`); the TRANSPORT uses `body_is_html=True` as
required by the `handover-compose.md` posting rules. The employee reads the
task's own chatter; an inbox ping for a board task is noise.

## 4. Client question — stage + chatter mention

When a task needs clarification FROM the client:

1. Move the task to stage **Potrebuje ujasniť**
2. Post a chatter note with the question, mentioning ONLY the ONE person
   who must answer — the mention text is derived from
   `res.partner.name` by `data-oe-id`, never a literal name typed by hand
   (the `handover-compose.md` mention-anchor rule applies:
   `<a href="/odoo/res.partner/<id>" class="o_mail_redirect" data-oe-id="<id>" data-oe-model="res.partner">@Meno</a>`)
3. Mention at most ONE person per question — never mass-mention, never
   mention the boss unless the boss IS the one who must answer

## 5. No assignee on client tasks

Client board tasks (`project.task` on the client's project) carry **NO
assignee** (`user_ids` empty). Every assignee triggers an Odoo notification
mail to that person — 17 tasks with an assignee = 17 mails the operator
must triage. The board's stage column IS the status; an assignee adds
nothing the stage does not already show.

## 6. Hotovo — ONLY after client confirmation

A task moves to **Hotovo** ONLY after the client confirms acceptance:
a thumbs-up reaction (👍), a reply, or an explicit "OK" — never on
the stream's own judgment. The confirmation is recorded on the GitHub
issue as `Acceptance-cited: msg <message_id> task <task_id>` (the
`handover-compose.md` family-acceptance doctrine applies — one task per
capability family, one confirmation closes the family).

## 7. Stage names — fixed vocabulary

Every client project board uses exactly these stages, in this order:

| Stage | Meaning |
|---|---|
| **ToDo** | Planned, not started |
| **Potrebuje ujasniť** | Blocked on a client question (rule 4 above) |
| **Realizácia** | In progress |
| **Verifikácia** | Delivered, awaiting client verification (rule 3 above) |
| **Hotovo** | Client-confirmed done (rule 6 above) |

## 8. Posting mechanics — repo poster only

Every `message_post` on a client task goes through the repo's task-sync
poster (`body_is_html=True`, recipient guard) from the stream's OWN Odoo
account — never a manual browser post, never another stream's account,
never the shared admin account. The `handover-compose.md` signature and
`body_is_html` rules apply to task chatter too. Never edit or delete a
posted chatter message.
