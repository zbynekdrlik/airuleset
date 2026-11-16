# Client Board Tasks — Stages, Assignee & "Done" (#1102)

Topic companion of `client-board-tasks.md` (CORE): its profile table holds each board's stages.

On the **miva** profile: awaiting client verification = **Čaká** (rule 3's handover note posts there), the client-question stage = **Požadujú sa zmeny** (rule 4/8 answers), **Hotové** moved by the OWNER only after client confirmation (rule 6), **Zrušené** never set by a stream. slovnormal has NO board (#1258).

---

### 3. Handover chatter note — MANDATORY on the "awaiting verification" transition

Moving a task to the profile's **awaiting-client-verification** stage
(**Verifikácia** / **Na overenie** / **Čaká**) REQUIRES a chatter note
(`message_post`) with exactly these sections, in order:

1. **Čo** — one sentence: what was delivered
2. **Kde** — the menu path AND a functional `https://` deep-link URL to the exact
   page/record/action on the client's PROD, verified 200 before posting
   (`handover-compose.md`: never a bare menu path or homepage)
3. **Čo skúsiť** — one sentence: what the employee should try / verify
4. **`stačí 👍`** — literal closing line (a 👍 reaction confirms acceptance;
   `read-reactions.md` detects it)

**Content — every profile (owner 28.9.2026, #1166):** list only what the reader
can use NOW, plus changes to what they ALREADY used; internal rework history and
the removal of a never-delivered feature stay OUT.

The note CONTENT is **PLAIN PROSE** (no rich formatting, no `@`-mention anchors,
no `partner_ids`); the TRANSPORT uses `body_is_html=True` per
`handover-compose.md`. The shape is Stop-checked.

**ONE step (#1180):** post it via `odoo_post.py --handover` (odoo-erp#8606:
post + move; until it ships, move by hand, same turn). A handover or `Acceptance-thread:` leaving a task unmoved is Stop-blocked.

### 5. Assignee — per profile

On the **miva** profile a client task carries **NO assignee**
(`user_ids` empty). Never add an assignee a profile does not name.

### 6. "Done" stage — per profile

A task reaches the profile's terminal stage (**Hotovo** / **Hotové**) ONLY after
the client confirms acceptance — a 👍 reaction, a reply, or an explicit "OK" —
never on the stream's own judgment. WHO moves it: the **OWNER** on miva.
Record it on the GitHub issue as
`Acceptance-cited: msg <message_id> task <task_id>` (`handover-compose.md`
family acceptance: one confirmation closes the family).
A Hotovo/Hotové a STREAM account set is never acceptance evidence — acceptance
is a client message or reaction (`msg <id>`), the owner's own move after a
confirmation or the client's own move (its tracking `msg <id>`); the close
gate rejects an `Acceptance-cited:` with no `msg <id>`, `meeting <recording id>`,
owner `issuecomment-<id>` or Discord message URL (#1185).

### 14. Udalosť → fáza (#1036)

👷 ACK + presun fázy = OKAMŽITÉ na každý komentár klienta; text pre klienta čaká na schválenie ownera (#606 U flow).

The phase names below are the miva stages; a new board maps them to its own profile row.

| Udalosť klienta | Fáza + akcia |
|---|---|
| Výhrada k dodanému | V riešení + ticket (bounce) |
| Otázka | Požadujú sa zmeny (rule 4) |
| Dodané na PROD | Čaká so správou + návrat späť (rule 3) |
