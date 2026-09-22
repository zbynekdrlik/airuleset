# Client Board Tasks — Questions, Answers & Mixed Communication (#1102)

Topic companion of `client-board-tasks.md` (CORE). Auto-loads on a `project.task` question (`needs-answer` / Potrebuje ujasniť) or a ❓ about a board task. These rules govern asking the client a question and routing every answer.

---

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

### 8. Client answers arrive ONLY in the Odoo task / owner chat — GitHub is a mirror

Nobody human answers on GitHub — only the gatekeeper Claude works there. The
client answers ONLY in the Odoo task; the owner answers ONLY in chat (owner: „na
githube ti ziadne odpovede nepribudnu, maximalne v odoo a tu", #1018). A GitHub
`needs-answer` ticket is a TRACKING MIRROR of the question; the QUESTION itself,
and every answer, lives in the Odoo task. Never write „odpovedz sem alebo do
GitHubu" to a client — never point a client at GitHub at all.

### 10. Mixed communication — redirect + new task + pointer

When the client (or anyone) writes about ANOTHER topic — off-topic inside a task,
on GitHub, or in a Discuss thread — the stream does the SAME thing everywhere:
redirect it, create a NEW task for that topic, post a one-line pointer to it, and
continue only there. NEVER answer the off-topic message in place. One rule for
all streams and all channels.
