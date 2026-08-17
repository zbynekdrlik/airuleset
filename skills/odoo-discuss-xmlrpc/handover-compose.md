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
- **The message body MUST carry a direct deep-link URL to the LIVE feature** on
  the client's PROD — the actual route / record / page URL the client clicks to
  SEE it, never a menu path ("Predaj → Objednávky → …") and never the bare
  instance homepage. Open the URL and confirm it loads the feature before putting
  it in the proposal.
- **State the owner's thread membership EXPLICITLY in the proposal.** The posting
  recipe already puts the owner on `partner_ids` (control ping) — but the PROPOSAL
  text you show the owner must SAY so ("teba pridám do vlákna ako člena"), so the
  owner knows they will see the thread and can catch a broken delivery.
- **Announce ONLY functions that are ALREADY LIVE on the client's PROD.** Never a
  feature that is merged-but-not-yet-deployed or scheduled — the client must be
  able to act on it the moment they read the thread. Confirm it is live on their
  PROD first.
- **Close the client message with named recipients + the self-blame reassurance**,
  so a client who cannot see the feature reports it back to YOU instead of
  assuming they did something wrong. Slovak template (adapt names / feature / URL):

  > Ahoj `<mená>`, funkcia `<čo>` je už nasadená na vašom systéme —
  > `<deep-link URL>`. Ak ju u seba nevidíte, napíšte prosím sem do vlákna —
  > chyba je na našej strane a hneď to opravíme.

One thread = one topic, a sub-thread under the channel the owner named (montalu:
IT-support) — never a new top-level channel or group chat (see `## Channel +
recipients` in `SKILL.md`). Ask the owner ONE decision at a time, and re-ask a
question whole and fresh if you have to (`user-questions-slovak.md`).
