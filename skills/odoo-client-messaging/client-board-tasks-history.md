# Client Board Tasks — Rationale & History (#1098)

Prose moved VERBATIM out of the injected `client-board-tasks.md` to fit the
situational-injection co-fire budget (MAX_TOTAL=14000, #1098 Option C
ROZHODNUTÉ 2026-09-21) — nothing is condensed or lost. This file has NO
situational-trigger row, so it is never auto-injected; its size is harmless
(same principle as the `.claude/rules-reference/` archive).

## Framing + provenance (was the injected file's header)

**Canonical rule for HOW a stream writes to a client's Odoo project board
(`project.task`) and to a client Discuss thread.** ONE fleet doctrine, every
client board — the universal rules below hold for EVERY stream/instance; a small
**per-board profile** supplies only what genuinely differs (stage vocabulary,
assignee, addressee, who moves to Done). Origin: operator directives montalu4
2026-09-08 (#949); the fleet-unification directives of 13.–14.9.2026
(#1014 / #1018 / #1024); lint odoo-erp#6605.

## Why per-board profiles, not one table

**Why per-board profiles, not one table:** the montalu-shaped table applied
literally is WRONG on the slovnormal board (different stages, an assignee, a
named addressee). The universal rules are identical everywhere; only the profile
row changes. A correction the owner makes to a board changes THIS file (one place
for all streams), never a per-stream memory (#1028) — see rule 12.

## Odoo-19 stage-set convergence + VERIF_STAGE_RX coupling (was under Per-board profiles)

The stage names above are the CURRENT
(transitional) PROD vocabulary; the column-vocabulary convergence to the native
Odoo 19 stage set — **Nové → Požadujú sa zmeny → V riešení → Čaká → Hotové →
Zrušené** (with **Čaká** as the awaiting-verification stage), opt-in per
`board_standard_managed` — and the shared `odoo_post.py` posting template are
owned by **odoo-erp#7101**; this file's profile rows are updated from there,
never invented locally. **COUPLING:** when that convergence lands on a managed
board, rule 3's Verifikácia Stop-hook stage names (`VERIF_STAGE_RX` in
`hooks/stop-check-prose-violations.sh`) MUST be updated together with the profile
rows (add `Čaká`), or the shape check silently stops firing on the renamed
stage — this belongs to the odoo-erp#7101 rollout, tracked as a follow-up.

## Rule 15 — incident background + why the attachment calls are inline

Klient často dá špecifikáciu ako OBRÁZOK/tabuľku priamo do popisu úlohy
(`project.task.description`), nie textom — text-only sken popisu ju MINIE
(montalu úloha 1010 „Sieťka robust", 20.9.2026: podklad bol
`<img src="/web/image/37652-…">`, Excel s rozmermi; stream zaparkoval ticket na
`needs-answer` na dáta, ktoré boli v tom screenshote). Prílohy sú PRIMÁRNY zdroj
— rovnaká povinnosť ako `view-image-urls` pri správach.

The injected rule 15 keeps the three source calls inline; the reasoning for that
(the recipe companion defers on a `project.task` write) is: (plný recept +
`res_field`/fence caveaty v `read-with-attachments.md`, ktorý sa na
`project.task` write nemusí injektnúť — preto minimálne volania INLINE)

## Rules 7, 9, 11, 12, 13 — provenance + side notes (moved for rule 16, #1156)

Moved VERBATIM out of the injected CORE so rule 16 fits the #1102 co-fire budget
(CORE ≤ 6000 stripped chars, ≥ 500 headroom on the tightest co-fire fixture):
owner quotes, ticket provenance, the one-time `res.users` rename scope note, the
enforcing-hook citation and a sentence that repeated rule 13's own
`body_is_html=True`. Every operative instruction stayed in the CORE (the
owner-override sentence of rule 11 is what rule 12 already says: an owner
correction changes the CORE rule itself).

- **rule 7:** (owner: „preco do commentarov do odoo taskov vypisujes technicke veci o githube!!", #1018)
- **rule 11 (a):** (the form the owner accepted when david3 renamed `res.users` 366 on slovnormal to „ZbynekAI 3"; odoo-erp #4624 / #4721)
- **rule 11 (b):** (owner: „tvoj účet sa volá ZbynekAI - odovzdávky, čo je zle", #1024)
- **rule 12:** — the „ako keby som už jedného neinštruoval" drift, #1014
- **rule 9:** (otherwise nothing ever closes, #1024)
- **rule 11 (c):** The owner may override the fleet form; the `res.users` rename itself on each Odoo instance is that stream's OWN odoo-erp task, not this file.
- **rule 13 (a):** hook `block-odoo-message-post-without-html.sh`, #1054 (enforces the stream-approved poster)
- **rule 13 (b):** The `handover-compose.md` `body_is_html` rule applies to task chatter too.

## Rule 16 — origin (#1156)

Owner, 25.9.2026 (montalu stream), verbatim: „chcel by som mať info o tom, čo sa
dohodlo na meetingu aj v úlohách, lebo tu to zasa zabudneš a issues sú v princípe
len pre teba. Tasky sú zdieľaný stav medzi nimi, mnou a tebou." — „a v issues
môžeš mať technické veci, ktoré rozumieš len ty, no nikto z nás im nerozumie."
Streams recorded meeting agreements only on GitHub issues, which the client and
the owner do not read, so the agreements were lost for the people who share the
Odoo tasks. GitHub keeps the technical record (file:line, tests, root cause).
