# Client Board Tasks — Stages, Assignee & "Done" (#1102)

Topic companion of `client-board-tasks.md` (CORE): its profile table holds each board's stages.

The profile's "awaiting client verification" stage is **Verifikácia** (montalu) or **Na overenie** (slovnormal) — rule 3's handover note fires on the transition INTO it. The "blocked on a client question" stage is **Potrebuje ujasniť** (montalu); slovnormal has no dedicated question stage, so a question stays in **V práci** with the chatter question of rule 4 (`client-board-questions.md`).

On the **miva** profile the stage names differ: awaiting client verification = **Čaká** (rule 3's handover note posts there), the client-question stage = **Požadujú sa zmeny** (rule 4/8 answers), **Hotové** moved by the OWNER only after client confirmation (rule 6), **Zrušené** never set by a stream.

---

### 3. Handover chatter note — MANDATORY on the "awaiting verification" transition

Moving a task to the profile's **awaiting-client-verification** stage
(**Verifikácia** / **Na overenie** / **Čaká**) REQUIRES a chatter note
(`message_post`) with exactly these sections, in this order:

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
no `partner_ids`) — except on **montalu** ONE mention anchor for the addressed
person (rule 5), its partner in `partner_ids`; the TRANSPORT uses `body_is_html=True` per
`handover-compose.md`. The shape is Stop-checked.

**ONE step (#1180):** post it via `odoo_post.py --handover` (odoo-erp#8606:
post + rule-5 assignee + move; until it ships, move + assign by hand, same
turn). A handover or `Acceptance-thread:` leaving a task unmoved is Stop-blocked.

### 5. Assignee — per profile

On the **montalu** profile the person the handover addresses is @mentioned in
the message AND set as the task's assignee (`user_ids`) when the rule 3 note is
posted — the employee the note is written for (owner
28.9.2026, #1166). On the **miva** profile a client task carries **NO assignee**
(`user_ids` empty). On the **slovnormal** profile the assignee
is **Dávid Greňa** — the profile row governs; never add an
assignee a profile does not name.

### 6. "Done" stage — per profile

A task reaches the profile's terminal stage (**Hotovo** / **Hotové**) ONLY after
the client confirms acceptance — a 👍 reaction, a reply, or an explicit "OK" —
never on the stream's own judgment (except the montalu auto-close below). WHO
moves it: the **OWNER** on miva and, after a confirmation,
on montalu; **Dávid Greňa himself** on slovnormal. Record it on the GitHub
issue as
`Acceptance-cited: msg <message_id> task <task_id>` (`handover-compose.md`
family acceptance: one confirmation closes the family).
A Hotovo/Hotové a STREAM account set is never acceptance evidence — acceptance
is a client message or reaction (`msg <id>`), the owner's own move after a
confirmation or the client's own move (its tracking `msg <id>`), or the
odoo-erp#8507 auto-close note below; the close gate rejects an
`Acceptance-cited:` with no `msg <id>`, `meeting <recording id>` or owner
`issuecomment-<id>` (#1185).

On **montalu** a task in Verifikácia also reaches Hotovo by the odoo-erp#8507
auto-close (owner ROZHODNUTÉ 28.9.2026, #1167): ONE reminder after 14 days
without a reaction, then Hotovo at ≥ 21 days and ≥ 7 days after the reminder;
any non-stream chatter message or a move out of Verifikácia cancels the
countdown. The mechanism moves the task, never the stream by hand, and replaces
#799's tacit closure there. The GitHub ticket then closes with
`Acceptance-cited: auto-close after 21 days without reaction (owner ROZHODNUTÉ 28.9., odoo-erp#8507) msg <auto-close note id> task <task_id>`.

### 14. Udalosť → fáza (#1036)

👷 ACK + presun fázy = OKAMŽITÉ na každý komentár klienta; text pre klienta čaká na schválenie ownera (#606 U flow).

The phase names below are the montalu vocabulary — map each to YOUR profile's stages (miva: **V riešení** / **Požadujú sa zmeny** / **Čaká**; slovnormal: **V práci** / **Na overenie**).

| Udalosť klienta | Fáza + akcia |
|---|---|
| Výhrada k dodanému | Realizácia + ticket (bounce) |
| Otázka | Potrebuje ujasniť (rule 4) |
| Dodané na PROD | Verifikácia so správou + návrat späť (rule 3) |
