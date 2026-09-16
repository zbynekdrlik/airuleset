# Client Board Tasks — project.task Formatting Doctrine

**Canonical rule for HOW a stream writes to a client's Odoo project board
(`project.task`) and to a client Discuss thread.** ONE fleet doctrine, every
client board — the universal rules below hold for EVERY stream/instance; a small
**per-board profile** supplies only what genuinely differs (stage vocabulary,
assignee, addressee, who moves to Done). Origin: operator directives montalu4
2026-09-08 (#949); the fleet-unification directives of 13.–14.9.2026
(#1014 / #1018 / #1024); lint odoo-erp#6605.

**Why per-board profiles, not one table:** the montalu-shaped table applied
literally is WRONG on the slovnormal board (different stages, an assignee, a
named addressee). The universal rules are identical everywhere; only the profile
row changes. A correction the owner makes to a board changes THIS file (one place
for all streams), never a per-stream memory (#1028) — see rule 12.

---

## Per-board profiles

| Board / instance | Stage vocabulary (in order) | Assignee | Addressee / @mention | Moves a task to "Done" |
|---|---|---|---|---|
| **montalu** (montaluN) | ToDo → Potrebuje ujasniť → Realizácia → Verifikácia → Hotovo | NONE — `user_ids` empty (rule 5) | no standing addressee; mention ONLY the one person who must answer a question (rule 4) | the **OWNER** only, after client confirmation (rule 6) |
| **slovnormal** (davidN) | Nové → V práci → Na overenie → Hotové | **Dávid Greňa** (CEO, Granč) | every message ADDRESSES Dávid Greňa | **Dávid Greňa** moves it to **Hotové** himself (owner ruling #1018) |
| **miva** (mivaN) | montalu shape, until the owner rules otherwise | NONE — `user_ids` empty | as montalu | the OWNER only, after client confirmation |

The profile's "awaiting client verification" stage is **Verifikácia** (montalu /
miva) or **Na overenie** (slovnormal) — rule 3's handover note fires on the
transition INTO it. The "blocked on a client question" stage is **Potrebuje
ujasniť** (montalu / miva); slovnormal has no dedicated question stage, so a
question stays in **V práci** with the chatter question of rule 4.

Adding a new board = adding a profile row here (and its GitHub `needs-answer`
mirror if a client answer is pending). The stage names above are the CURRENT
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

---

## Universal rules (every board)

### 1. Task name — CLIENT language

The task name (`project.task.name`) is written in the CLIENT's language
(today: Slovak for every board) — never English, never internal/developer
jargon. A task name the client's employee cannot understand without asking
defeats the board's purpose.

### 2. Description — 2–4 sentences for the employee

The description (`project.task.description`) carries **2–4 plain sentences
telling the employee what was done**, followed by `Je nasadené. (GitHub #N)`
(the canonical description trailer odoo-task-sync uses; the older `(#N)` form is
also accepted).

**BANNED in the description** (all rewordings): Discuss thread names / channel
ids, `PROD` / environment names, version numbers, PR / commit refs, internal
notes / root-cause analysis, anything the employee cannot act on. See rule 7 for
the client-facing-body jargon ban that a hook now ENFORCES.

### 3. Handover chatter note — MANDATORY on the "awaiting verification" transition

Moving a task to the profile's **awaiting-client-verification** stage
(**Verifikácia** / **Na overenie**) REQUIRES a chatter note (`message_post` on
the task) with exactly these sections, in this order:

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

### 4. Client question — the board's question stage + chatter mention

When a task needs clarification FROM the client:

1. Move it to the profile's question stage (**Potrebuje ujasniť**; slovnormal
   keeps it in **V práci**).
2. Post a chatter note with the question. Mention text is derived from
   `res.partner.name` by `data-oe-id`, never a literal name typed by hand
   (`handover-compose.md` mention-anchor rule).
3. Mention AT MOST ONE person per question — never mass-mention, never the boss
   unless the boss IS the one who must answer. On the **slovnormal** profile the
   addressee is always **Dávid Greňa**.
4. The FULL question lives in the Odoo task. A GitHub `needs-answer` ticket is a
   TRACKING MIRROR only — see rule 8.

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

### 7. NO GitHub / technical jargon in a client-facing body (ENFORCED)

A client-facing body — a `project.task` chatter note, a Discuss message, or a
rewrite of the stream's own message — carries ZERO developer jargon: no
`github.com` links, no `#`-number issue refs, no PR / commit / branch / RFR / gk
/ hand-off / CI / merge / worktree tokens (owner: „preco do commentarov do odoo
taskov vypisujes technicke veci o githube!!", #1018). The client reads plain
business Slovak about what changed for THEM — that is the DOCTRINE (all of the
above is discouraged). The MECHANICAL gate (`gates/clientbody.py`, PreToolUse) is
deliberately CONSERVATIVE so it never false-blocks a legitimate client message
(the owner's worst outcome): it blocks a GitHub mention (`github.com` / the word
„GitHub"), a github-context issue number (`issue #NNNN` / `ticket #NNNN`), and
the unambiguous dev tokens `commit` / `worktree` / `hand-off` / `RFR` / `gk`. It
does NOT mechanically block a bare number with no GitHub context (`objednávka
#1058`, a hex colour `#003366`) nor ambiguous business terms (`PR` = public
relations, `CI` = corporate identity, `merge`, `branch`) — those stay
discouraged by this doctrine but are left to review, because blocking them
false-blocks ordinary business Slovak. The ONE sanctioned exception the gate
allowlists is the internal `GitHub ticket: #N` marker note (and the
`(GitHub #N)` description trailer) the odoo-task-sync tooling depends on. A
genuine edge case bypasses with `# airuleset:client-body-ok REASON` (logged).

### 8. Client answers arrive ONLY in the Odoo task / owner chat — GitHub is a mirror

Nobody human answers on GitHub — only the gatekeeper Claude works there. The
client answers ONLY in the Odoo task; the owner answers ONLY in chat (owner: „na
githube ti ziadne odpovede nepribudnu, maximalne v odoo a tu", #1018). A GitHub
`needs-answer` ticket is a TRACKING MIRROR of the question; the QUESTION itself,
and every answer, lives in the Odoo task. Never write „odpovedz sem alebo do
GitHubu" to a client — never point a client at GitHub at all.

### 9. ATOMIC tasks — one task = one topic

One task = one topic; a task is NEVER a chat-of-everything (otherwise nothing
ever closes, #1024). When the client opens a NEW topic inside an existing task,
the stream IMMEDIATELY: (a) creates a NEW task for it (with its own GitHub
`GitHub ticket: #N` marker note), (b) posts a one-line pointer in the source task
— „téma X pokračuje v úlohe Y" — and (c) continues that topic ONLY in the new
task. Never answer the second topic in place.

### 10. Mixed communication — redirect + new task + pointer

When the client (or anyone) writes about ANOTHER topic — off-topic inside a task,
on GitHub, or in a Discuss thread — the stream does the SAME thing everywhere:
redirect it, create a NEW task for that topic, post a one-line pointer to it, and
continue only there. NEVER answer the off-topic message in place. One rule for
all streams and all channels.

### 11. Stream account name — canonical `ZbynekAI <N>`

Each stream's Odoo `res.users` display name, and its message signature, use ONE
canonical form per stream: **`ZbynekAI <N>`** by default (the form the owner
accepted when david3 renamed `res.users` 366 on slovnormal to „ZbynekAI 3";
odoo-erp #4624 / #4721), where `<N>` is the stream number; a NAMED stream uses
its stream name (e.g. `MarekAI <N>` for a marek-owned stream). Never
„ZbynekAI - odovzdávky" or any ad-hoc variant (owner: „tvoj účet sa volá ZbynekAI
- odovzdávky, čo je zle", #1024). The owner may override the fleet form; the
`res.users` rename itself on each Odoo instance is that stream's OWN odoo-erp
task, not this file.

### 12. Owner corrections change THIS rule, not a per-stream memory

An owner correction about board chatter / stages / addressing / "Done" NEVER
goes into a per-stream `~/.claude/projects/*/memory/*.md` (where the other
streams never see it — the „ako keby som už jedného neinštruoval" drift, #1014).
It changes THIS file, so every stream inherits it: propose the edit via
`python3 ~/devel/airuleset/airuleset.py gk-request --repo zbynekdrlik/airuleset`.
A memory-write guard refuses a new per-stream client-board memory and points
here; the #1028 doctrine-audit retires any that slip through.

### 13. Posting mechanics — repo poster only; the EDIT rule

Every `message_post` on a client task goes through the repo's task-sync poster
(`body_is_html=True`, recipient guard) from the stream's OWN Odoo account — never
a manual browser post, never another stream's account, never the shared admin
account. The `handover-compose.md` signature and `body_is_html` rules apply to
task chatter too.

**Never edit or delete a posted chatter message**, with ONE exception: an
OWNER-ORDERED cleanup of the stream's OWN messages (e.g. „zmaž ten technický spam
z komentárov", #1018) — a `mail.message.write` on own-author messages only, never
another author's, recorded on the relevant GitHub ticket so the edit is logged.
Absent an explicit owner order, a posted message stands.
