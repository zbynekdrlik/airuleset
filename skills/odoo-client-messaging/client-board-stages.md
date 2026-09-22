# Client Board Tasks — Stages, Assignee & "Done" (#1102)

Topic companion of `client-board-tasks.md` (CORE). Auto-loads on a `project.task` stage move. The per-board stage vocabulary + profiles live in the CORE profile table; these are the rules that govern MOVING a task between stages.

The profile's "awaiting client verification" stage is **Verifikácia** (montalu) or **Na overenie** (slovnormal) — rule 3's handover note fires on the transition INTO it. The "blocked on a client question" stage is **Potrebuje ujasniť** (montalu); slovnormal has no dedicated question stage, so a question stays in **V práci** with the chatter question of rule 4 (`client-board-questions.md`).

On the **miva** profile the stage names differ (canonical set, odoo-erp #7101): awaiting client verification = **Čaká** (rule 3's handover note posts there), the client-question stage = **Požadujú sa zmeny** (rule 4/8 answers), **Hotové** moved by the OWNER only after client confirmation (rule 6), **Zrušené** never set by a stream.

Adding a board = a profile row here (+ its GitHub `needs-answer` mirror if a
client answer is pending).

---

### 3. Handover chatter note — MANDATORY on the "awaiting verification" transition

Moving a task to the profile's **awaiting-client-verification** stage
(**Verifikácia** / **Na overenie** / **Čaká**) REQUIRES a chatter note
(`message_post` on the task) with exactly these sections, in this order:

1. **Čo** — one sentence: what was delivered
2. **Kde** — the menu path AND a functional `https://` deep-link URL to the exact
   page/record/action on the client's PROD, verified 200 before posting (the
   `handover-compose.md` URL rule applies — never a bare menu path, never the
   instance homepage)
3. **Čo skúsiť** — one sentence: what the employee should try / verify
4. **`stačí 👍`** — literal closing line (a 👍 reaction confirms acceptance; the
   `read-reactions.md` companion detects the reaction)

The note CONTENT is **PLAIN PROSE** (no rich formatting, no `@`-mention anchors,
no `partner_ids`); the TRANSPORT uses `body_is_html=True` per the
`handover-compose.md` posting rules. This four-section shape is ENFORCED by a
Stop-hook check (#1018) — a turn that reports posting a verification note without
the four sections is blocked.

### 5. Assignee — per profile

On the **montalu** and **miva** profiles a client task carries **NO assignee**
(`user_ids` empty): every assignee triggers an Odoo notification mail, and the
stage column already IS the status. On the **slovnormal** profile the assignee is
**Dávid Greňa** (owner ruling #1018) — the profile row governs; never add an
assignee a profile does not name.

### 6. "Done" stage — per profile, ONLY after client confirmation

A task reaches the profile's terminal stage (**Hotovo** / **Hotové**) ONLY after
the client confirms acceptance — a 👍 reaction, a reply, or an explicit "OK" —
never on the stream's own judgment. WHO moves it is the profile's call: the
**OWNER** on montalu / miva (#924); **Dávid Greňa himself** on slovnormal. The
confirmation is recorded on the GitHub issue as
`Acceptance-cited: msg <message_id> task <task_id>` (the `handover-compose.md`
family-acceptance doctrine applies — one task per capability family, one
confirmation closes the family).

### 14. Udalosť → fáza (#1036)

👷 ACK + presun fázy = OKAMŽITÉ na každý komentár klienta; text pre klienta čaká na schválenie ownera (#606 U flow).

The phase names below are the montalu vocabulary — map each to YOUR board's own profile stages (miva: **V riešení** / **Požadujú sa zmeny** / **Čaká**; slovnormal: **V práci** / **Na overenie**).

| Udalosť klienta | Fáza + akcia |
|---|---|
| Výhrada k dodanému | Realizácia + ticket (bounce) |
| Otázka | Potrebuje ujasniť (rule 4) |
| Dodané na PROD | Verifikácia so správou + návrat späť (rule 3) |
