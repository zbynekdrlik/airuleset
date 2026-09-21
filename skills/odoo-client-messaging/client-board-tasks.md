# Client Board Tasks — project.task Formatting Doctrine (CORE)

Always-injected CORE for a `project.task` write; background: `client-board-tasks-history.md` (#1098). **Companions** (each auto-loads on its own `project.task` action, #1102): `client-board-stages.md` (rules 3, 5, 6, 14); `client-board-questions.md` (rules 4, 8, 10); `client-board-attachments.md` (rule 15).

---

## Per-board profiles

| Board / instance | Stage vocabulary (in order) | Assignee | Addressee / @mention | Moves a task to "Done" |
|---|---|---|---|---|
| **montalu** (montaluN) | ToDo → Potrebuje ujasniť → Realizácia → Verifikácia → Hotovo | NONE — `user_ids` empty (rule 5) | no standing addressee; mention ONLY the one person who must answer a question (rule 4) | the **OWNER** only, after client confirmation (rule 6) |
| **slovnormal** (davidN) | Nové → V práci → Na overenie → Hotové | **Dávid Greňa** (CEO, Granč) | every message ADDRESSES Dávid Greňa | **Dávid Greňa** moves it to **Hotové** himself (owner ruling #1018) |
| **miva** (mivaN) | montalu shape, until the owner rules otherwise | NONE — `user_ids` empty | as montalu | the OWNER only, after client confirmation |

---

## Universal rules (every board)

### 1. Task name — CLIENT language

The task name (`project.task.name`) is written in the CLIENT's language
(today: Slovak for every board) — never English, never internal/developer
jargon. A name the employee cannot understand defeats the board's purpose.

### 2. Description — 2–4 sentences for the employee

The description (`project.task.description`) carries **2–4 plain sentences
telling the employee what was done**, followed by `Je nasadené. (GitHub #N)`
(the canonical description trailer odoo-task-sync uses; the older `(#N)` form is
also accepted).

**BANNED in the description** (all rewordings): Discuss thread names / channel
ids, `PROD` / environment names, version numbers, PR / commit refs, internal
notes / root-cause analysis, anything the employee cannot act on. See rule 7 for
the client-facing-body jargon ban that a hook now ENFORCES.

### 7. NO GitHub / technical jargon in a client-facing body (ENFORCED)

A client-facing body — a `project.task` chatter note, a Discuss message, or a
rewrite of the stream's own message — carries ZERO developer jargon: no
`github.com` links, no `#`-number issue refs, no PR / commit / branch / RFR / gk
/ hand-off / CI / merge / worktree tokens (owner: „preco do commentarov do odoo
taskov vypisujes technicke veci o githube!!", #1018). The client reads plain
business Slovak. The MECHANICAL gate
(`gates/clientbody.py`, PreToolUse) is deliberately CONSERVATIVE (never
false-blocks a legit client message): it blocks a `github.com` / „GitHub"
mention, a github-context issue number (`issue #NNNN` / `ticket #NNNN`), and the
dev tokens `commit` / `worktree` / `hand-off` / `RFR`. It does NOT block a bare
number (`objednávka #1058`, hex `#003366`) nor ambiguous business terms (`PR`,
`CI`, `merge`, `branch`) — those stay discouraged, left to review. The ONE
allowlisted exception is the internal `GitHub ticket: #N` marker note (and the
`(GitHub #N)` description trailer) the odoo-task-sync tooling depends on; a
genuine edge case bypasses with `# airuleset:client-body-ok REASON` (logged).

### 9. ATOMIC tasks — one task = one topic

One task = one topic; a task is NEVER a chat-of-everything (otherwise nothing
ever closes, #1024). When the client opens a NEW topic inside an existing task,
the stream IMMEDIATELY: (a) creates a NEW task for it (with its own GitHub
`GitHub ticket: #N` marker note), (b) posts a one-line pointer in the source task
— „téma X pokračuje v úlohe Y" — and (c) continues that topic ONLY in the new
task. Never answer the second topic in place.

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
account, and **never an ad-hoc driver `.py`** (gk posts on a stream's behalf via
the stream-approved poster + its read-back — `handover-compose.md`; a shipped
driver is gated by `block-odoo-message-post-without-html.sh`, #1054). The
`handover-compose.md` `body_is_html` rule applies to task chatter too.

**Never edit or delete a posted chatter message**, with ONE exception: an
OWNER-ORDERED cleanup of the stream's OWN messages (#1018) — a
`mail.message.write` on own-author messages only, recorded on the GitHub ticket
so the edit is logged. Absent an owner order, a posted message stands.
