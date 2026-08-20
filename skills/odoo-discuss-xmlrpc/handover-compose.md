# Composing the client handover PROD Discuss proposal

**This is the SINGLE canonical handover-proposal rule for EVERY sub-dev stream.**
It exists because each stream kept composing these client PROD Discuss threads
differently and the owner had to re-teach the same rules to each one (montalu5
2026-08-16: a thread proposal with no deep-link URL and no statement that the
owner would be a member; earlier montalu / montalu2). Do NOT keep private
per-stream notes for it. The SEND mechanics (body_is_html, partner_ids incl. the
owner, sub-thread by name, post-verify) live in the sibling `odoo-discuss-xmlrpc`
skill (`SKILL.md`); THIS is the COMPOSE — what the message must contain and how
its proposal is presented to the OWNER for approval BEFORE it is posted.

- **The proposal you present to the owner is COMPLETE and lives IN THE CHAT.**
  Put in the chat message itself: (1) the exact thread name, (2) the FULL message
  body verbatim, (3) the member list. NEVER "the text is on the ticket / in the
  PR" — the owner does not read tickets, so a proposal that points at a ticket
  instead of carrying the whole text is not a proposal.
- **The thread NAME ends with the owning stream's NUMBER**, so the owner sees at
  a glance which stream owns the thread (montalu3 → "Kontrola zákazníckych
  e-mailov 3"). This formalizes the de-facto IT-support convention already on
  montalu PROD ("VOP — finálne znenie 3", "Nedoručené e-maily 3", "Oprava filtra
  rozmerov 2", "Tabula objednavok 1"). The suffix is the STREAM'S NUMBER: for a
  NUMBERED stream it is the trailing digits of the stream name (montalu2..8 →
  2..8, david2..4 → 2..4, miva1 → 1). For an UNNUMBERED base stream (montalu,
  marek, david, simap) the suffix is "1" — the first stream of its client
  family (matches the existing montalu "Tabula objednavok 1" thread and miva1) —
  CONFIRMED by the owner on airuleset #532 (2026-08-18): unnumbered base streams
  are themselves a mistake and will be renamed to <name>1 (airuleset #537:
  montalu→montalu1, david→david1, simap→simap1; marek stays unnumbered, unused,
  and does no client handovers), so every active handover stream ends with its
  number and the rule is uniform.
- **Every message ENDS with a stream-identity signature line.** The LAST line of
  EVERY message body is `ZbynekAI <N>`, so the owner sees at a glance WHICH stream
  sent it and where to go resolve what the thread discusses (owner request,
  airuleset #598: the shared sender display name "zbynekai odovzdavac" told the
  owner nothing about which subdev owns it). `<N>` is the SAME stream number as the
  thread-name suffix above — the trailing digits of the unix user, or "1" for an
  UNNUMBERED base stream (montalu, david, simap): montaluN → N, davidN → N (base
  david → 1), simapN → N (base → 1), miva1 → 1 / mivaN → N. It is the SAME
  derivation, NEVER a second one — it matches both the #532 thread-name suffix and
  `cli_aliases.short_target_alias`'s family regexes (each captures that same numeric
  suffix). On any single client Odoo instance only ONE stream family posts (montalu
  → erp.montalu.cloud, david → its own, …), so the bare number is unambiguous there.
  The signature stays on EVERY message — the OPENING thread message AND every
  follow-up — never dropped the way the greeting is. It is a POISTKA that works even
  while streams SHARE one Odoo account; if the accounts are ever renamed per stream
  ("ZbynekAI N" display name, airuleset #598 → odoo-erp #4624) the signature stays as
  the short number line and the two layers do not conflict.
- **The message body MUST carry a direct deep-link URL to the LIVE feature** on
  the client's PROD — the actual route / record / page URL the client clicks to
  SEE it, never a menu path ("Predaj → Objednávky → …") and never the bare
  instance homepage. Open the URL and confirm it loads the feature before putting
  it in the proposal. **This applies to EVERY openable reference in the message,
  not only the handed-over feature:** anything the reader can open — a specific
  record, a screen, an action, a report, a dashboard — is named with its OWN direct
  functional URL, verified live before sending, never only a prose menu path (owner
  directive, airuleset #595: a handover message — montalu PROD msg 1723308 —
  described two live features only by the menu path "Výroba a montáž ▸ Operácie ▸
  Výrobná tabuľa" with no clickable URL and was rejected; nobody can click through
  and see it). This generalizes completion-report.md's 🌐-line rule to client-facing
  Discuss messages and notifications.
- **State the owner's thread membership EXPLICITLY in the proposal.** The posting
  recipe already puts the owner on `partner_ids` (control ping) — but the PROPOSAL
  text you show the owner must SAY so ("teba pridám do vlákna ako člena"), so the
  owner knows they will see the thread and can catch a broken delivery.
- **Announce ONLY functions that are ALREADY LIVE on the client's PROD.** Never a
  feature that is merged-but-not-yet-deployed or scheduled — the client must be
  able to act on it the moment they read the thread. Confirm it is live on their
  PROD first.
- **The greeting (oslovenie — „Dobrý deň…" / „Ahoj…") belongs ONLY in the FIRST
  (opening) message of a thread.** A follow-up reply in an existing thread carries
  NO greeting — it continues directly with the content (an `@`-mention anchor only
  where it genuinely belongs; `partner_ids` for delivery ALWAYS, on every message).
  Repeating „Dobrý deň, pani …" on every follow-up in the same thread reads as
  machine-sent — the live miva PROD thread „Augustová dochádzka" (discuss.channel
  19) had three same-day follow-ups all reopening with „Dobrý deň…" (airuleset
  #573, 2026-08-19). Greet once, at the top of the thread; after that, just the
  message.
- **Close the client message with named recipients + the self-blame reassurance**,
  so a client who cannot see the feature reports it back to YOU instead of
  assuming they did something wrong. The `Ahoj <mená>…` opening below is the
  OPENING message of the thread; a follow-up reply drops the oslovenie and starts
  straight at the content (greeting rule above). Slovak template (adapt names /
  feature / URL):

  > Ahoj `<mená>`, funkcia `<čo>` je už nasadená na vašom systéme —
  > `<deep-link URL>`. Ak ju u seba nevidíte, napíšte prosím sem do vlákna —
  > chyba je na našej strane a hneď to opravíme.

One thread = one topic, a sub-thread under the channel the owner named (montalu:
IT-support) — never a new top-level channel or group chat (see `## Channel +
recipients` in `SKILL.md`). Ask the owner ONE decision at a time, and re-ask a
question whole and fresh if you have to (`user-questions-slovak.md`).
