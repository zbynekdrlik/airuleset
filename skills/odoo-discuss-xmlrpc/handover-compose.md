# Composing the client handover PROD Discuss proposal

**This is the SINGLE canonical handover-proposal rule for EVERY sub-dev stream.**
It exists because each stream kept composing these client PROD Discuss threads
differently and the owner had to re-teach the same rules to each one (montalu5
2026-08-16: a thread proposal with no deep-link URL and no statement that the
owner would be a member; earlier montalu / montalu2). Do NOT keep private
per-stream notes for it. The SEND mechanics (body_is_html, partner_ids incl. the
owner, sub-thread by name, post-verify) live in the sibling `odoo-discuss-xmlrpc`
skill (`SKILL.md`); THIS is the COMPOSE — what the message must contain and how
it — and EVERY message it carries, the opening one AND every follow-up — is
presented to the OWNER for approval BEFORE it is posted (the approval rule below
is the FIRST rule for a reason).

- **The owner must APPROVE the exact text of EVERY client-facing Discuss message
  BEFORE it is posted — the OPENING message AND every follow-up reply / question /
  reminder into an EXISTING thread, without exception.** This is NOT limited to a
  handover proposal or a new thread: whatever you send to a client is approved
  first ("co posieláš" — owner ruling after montalu6 posted an unapproved message
  into a live client thread on PROD, 2026-08-22, thread 283; it was deleted within
  a minute but the bus push + notification had already reached the client —
  irreversible). A stream that read the approval rule as applying only to thread
  CREATION is exactly what caused it; it applies to every message. This is
  HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`, airuleset #628): a sub-dev
  stream `message_post` to a `discuss.channel` is BLOCKED until the tool-call
  content carries the falsifiable evidence marker `airuleset:owner-approved <ref>`
  — a reference to HOW/WHEN the owner approved THIS text, never a bare "approved"
  (the same logged-falsifiable-claim model as `Discuss-closed:` /
  `Self-service-checked:`); also record the approval on the ticket
  (`durable-decisions-to-tickets.md`). Bypass for a genuine internal / non-client
  post: `airuleset:discuss-approval-ok`. OPEN sub-question for the owner: do the
  daily #4515 substantive reminders need per-message approval, or a template
  approved ONCE (then cited by the marker `<ref>`)? — until the owner settles it,
  the safe default is per-message approval.
- **The proposal you present to the owner is COMPLETE and lives IN THE CHAT —
  and it is the approval question for EVERY message (the OPENING handover AND
  every follow-up reply / question / reminder), never only a new thread.** Put
  in the chat message itself: (1) the target thread on its OWN SEPARATE,
  clearly-shown line — the exact thread name, i.e. the full human NAME (+ its
  parent channel where that helps context), NEVER only the internal channel
  number and NEVER only wrapped in prose:
  `Vlákno: „Tabula objednavok 1" (pod IT-support, montalu PROD)`; (2) the FULL
  message body verbatim; (3) the member list. NEVER "the text is on the ticket /
  in the PR" — the owner does not read tickets, so a proposal that points at a
  ticket instead of carrying the whole text is not a proposal. Naming the target
  only by its internal number is exactly what forced the owner to ask „do akého
  vlákna to má ísť?" (airuleset #632, montalu1: an approval question for a
  production-board notice referenced its target only as „vlákno 250" — it should
  have carried „Tabula objednavok 1" as a separate, visible field from the start).
  This is now ALSO HOOK-ENFORCED at the ❓ owner-approval CHAT surface
  (`hooks/stop-check-question-quality.sh` Check 6, airuleset #650): a ❓ approval
  question carrying client-posting intent — send/approve a client Discuss message
  into a thread (INCLUDING an EXISTING one), or a closing/handover message — is
  BLOCKED unless it names the exact target thread (an explicit `Vlákno:` line, or
  a quoted thread name ending in the stream number), so a generic druhový opis
  like „výrobné vlákno" no longer passes. The prose rule failed AGAIN on montalu1
  2026-08-24 („to tu znova nie je!!!"), so #650 escalated it to the chat surface
  the phone ping is built from, in the #596/#609/#628 tool-call-gate family.
  **The `Vlákno:` line — and EVERY owner-facing mention of the thread, in a
  proposal, a status update, or narration — carries the thread NAME **and** its
  own clickable deep URL, never a bare channel number (airuleset #657, owner
  montalu3 2026-08-24: „co ja mam akoze robit s 'vlakno 288'?!" — a bare id they
  cannot decode across many client threads).** Canonical form:
  `Vlákno: „Tabula objednavok 1" — https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288`
  (the `active_id=discuss.channel_<N>` deep link opens the exact thread; open it
  and confirm it loads before pasting). This is the thread-reference sibling of
  the LIVE-feature deep-link rule below (#595), and the WIDER owner-facing
  surface (status/narration, not only the ❓ approval question) is now
  HARD-backstopped in `hooks/stop-check-prose-violations.sh` (#657): a bare
  Discuss channel id in an Odoo-context message with no `discuss.channel_<N>`
  URL is blocked. The uniform doctrine lives in
  `modules/core/issue-reference-context.md`.
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
  number and the rule is uniform. **The name is at most ~30 CHARACTERS including
  that trailing number** (airuleset #597, owner directive): a longer name is
  truncated behind the Odoo Discuss sidebar's first page and the number — the
  whole point — disappears from view (the owner kept hand-shortening them). Good:
  „Oprava filtra rozmerov 2" (24). Too long: „Viditeľnosť leadov pre obchodníkov 2"
  (36) — shorten to e.g. „Viditeľnosť leadov 2" (20). **The proposal you present
  to the owner must ALREADY carry a name that satisfies BOTH conditions** (ends
  with the stream number AND ≤ 30 chars) — never a long or un-numbered draft the
  owner then has to fix. Both conditions are now HOOK-ENFORCED at create time
  (`hooks/block-discuss-thread-name.sh`, airuleset #596/#597): a `discuss.channel`
  create whose name breaks either condition is BLOCKED before it reaches PROD (a
  `write`/rename is never blocked; a `message_post` gets its own SIGNATURE check
  instead — see the next bullet, airuleset #609).
- **Every message ENDS with a stream-identity signature line.** The LAST line of
  EVERY message body is the stream's identity signature `<WORD> <N>` — `ZbynekAI <N>`
  by DEFAULT, and `MarekAI <N>` for a marek-owned stream (montalu4, which posts via
  Marek's OWN handover account "Marek AI - odovzdávky", airuleset #641 → odoo-erp
  #3864). The owner sees at a glance WHICH stream sent it and where to go resolve
  what the thread discusses (owner request, airuleset #598: a bare shared sender
  display name told the owner nothing about which subdev owns it). `<WORD>` is the
  compact form of the account's client-visible display name (mirroring ZbynekAI),
  derived from the stream's OWNER via `notify.STREAM_NOTIFY_OWNER` — the SAME single
  source that routes the stream's Discord notifications, NEVER a second map. Signing
  the WRONG person's name (a marek stream signing ZbynekAI, or vice versa) is BLOCKED
  too, not silently accepted. `<N>` is the SAME stream number as the
  thread-name suffix above — the trailing digits of the unix user, or "1" for an
  UNNUMBERED base stream (montalu, david, simap): montaluN → N, davidN → N (base
  david → 1), simapN → N (base → 1), miva1 → 1 / mivaN → N (montalu4 = Marek's own
  stream, so it signs MarekAI 4). It REUSES the project's existing stream number, NEVER a
  second derivation — for a NUMBERED stream it matches `cli_aliases.short_target_alias`'s
  family regexes (montalu/david/simap/miva, each capturing that numeric suffix) and the
  #532 thread-name suffix; the base → 1 case is the #532/#537 convention's own mapping
  (base streams renamed to <name>1), NOT derived from those `\d+` regexes. On any
  single client Odoo instance only ONE stream family posts (montalu
  → erp.montalu.cloud, david → its own, …), so the bare number is unambiguous there.
  The signature stays on EVERY message — the OPENING thread message AND every
  follow-up — never dropped the way the greeting is. It is a POISTKA that works even
  while streams SHARE one Odoo account; if the accounts are ever renamed per stream
  ("ZbynekAI N" display name, airuleset #598 → odoo-erp #4624) the signature stays as
  the short number line and the two layers do not conflict. The signature is now
  HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`, airuleset #609): a sub-dev
  stream `message_post` to a `discuss.channel` that carries no valid identity
  signature (`ZbynekAI <N>` / `MarekAI <N>`, or that carries the WRONG identity, #641)
  is BLOCKED before it reaches PROD — regardless of which skill you
  loaded, because the guard scans the actual post content (montalu6 shipped an
  UNSIGNED client message from a skill that never carried this rule). Bypass for a
  genuine internal/legacy post: `airuleset:discuss-sig-ok` in the content.
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
- **Len minulé, overené udalosti — klientska správa sa NIKDY neodvoláva na to,
  čo sa LEN STANE (airuleset #696, owner ruling 2026-08-25).** Verbatim: „vzdy
  sa treba odvolavat na to co sa udialo nie na to co sa udeje. Bud mu iniciuj
  email report teraz a posli ked si si ze mu odisiel a obsahuje co si mu slubil
  alebo cakaj do zajtra!!!" — stream navrhol klientovi (vlákno 263) „od
  zajtrajšieho ranného e-mailu bude pri každej položke aj kód dodávateľa", kód
  bol na PROD, ale sľúbený artefakt (digest e-mail) ešte NEEXISTOVAL. Keď je
  viditeľný výstup funkcie plánovaný ARTEFAKT (digest e-mail, report, cron
  výsledok), máš presne dve legálne cesty — obe končia správou v MINULOM ČASE:
  (1) artefakt SPUSTI TERAZ — vlastnou právomocou, alebo `GATEKEEPER-ACTION:`
  ak ho na PROD vie spustiť len gatekeeper — a OVER, že odišiel S prisľúbeným
  obsahom (read-back: psql / `mail.mail` na čerstvej prod-kópii, nikdy len
  „odoslané"); alebo (2) POČKAJ na najbližší plánovaný beh, over ho, a až
  potom píš — v minulom čase. Toto je HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`,
  airuleset #696): stream `message_post` s budúcim sľubom v tele (od zajtra /
  zajtrajš… / bude pri|v|obsahovať / v ďalšom e-maile|reporte / od budúc /
  čoskoro / pripravujeme) je BLOKOVANÝ, kým obsah nenesie falsifikovateľnú
  značku `airuleset:artifact-verified <ref>` — referenciu na to, ČO si z
  reálneho artefaktu odčítal, kde a kedy; holá značka bez referencie neplatí
  (model `airuleset:owner-approved`). Iná budúca formulácia mimo zoznamu
  vzorov hookom prejde — táto doktrína platí na KAŽDÉ preformulovanie.
- **The greeting (oslovenie — „Dobrý deň…" / „Ahoj…") belongs ONLY in the FIRST
  (opening) message of a thread.** A follow-up reply in an existing thread carries
  NO greeting — it continues directly with the content (an `@`-mention anchor only
  where it genuinely belongs; `partner_ids` for delivery ALWAYS, on every message).
  Repeating „Dobrý deň, pani …" on every follow-up in the same thread reads as
  machine-sent — the live miva PROD thread „Augustová dochádzka" (discuss.channel
  19) had three same-day follow-ups all reopening with „Dobrý deň…" (airuleset
  #573, 2026-08-19). Greet once, at the top of the thread; after that, just the
  message.
- **React to the client's previous answer FIRST — never drop a new question into
  a thread that ignores what the client last said.** Before you post any new
  question or message into an EXISTING client thread, check the thread for the
  client's last answer that you have not yet reacted to, and OPEN by briefly
  reacting to it (confirmation / thanks / what it means for the work); only THEN
  ask the next thing. This applies to a follow-up into an existing thread, not
  just to opening one — a reply that reflects nothing reads as machine-sent
  (incident montalu1, CEO Špetta's thread „Etapy zákaziek vo výrobe 1": a draft
  would have asked a fresh question without a word about Špetta's own prior
  confirmation + new request in the same thread, airuleset #625, 2026-08-22).
- **Address register PER PERSON — vykanie only for the CEO, tykanie for the other
  named contacts, VYKANIE by default for anyone not yet listed.** Before every
  `message_post`, check the register below and use the right register for that
  person; never address a formal-register contact informally (airuleset #625/#626,
  owner ruling 2026-08-22: „vykame len ceo speta … ak nie je definovane tykanie
  tak sa vyka no uz ostatnym tykame"). The register is PER-PROJECT and grows by
  ONE line as new client people appear — add a contact to its project's row:
  - **montalu** — VYKANIE: CEO Pavol Špetta (menovite). TYKANIE: Patrik Javorský,
    Dominik Volek, Peter Hollý. DEFAULT for anyone NOT listed here: VYKANIE, until
    the owner says otherwise.

  A NEW client person you have not been told how to address is VYKANIE by default
  (#626) — never guess tykanie; ask the owner, then add the line.
- **Close the client message with named recipients + the self-blame reassurance**,
  so a client who cannot see the feature reports it back to YOU instead of
  assuming they did something wrong. The `Ahoj <mená>…` opening below is the
  OPENING message of the thread; a follow-up reply drops the oslovenie and starts
  straight at the content (greeting rule above). Slovak template (adapt names /
  feature / URL):

  > Ahoj `<mená>`, funkcia `<čo>` je už nasadená na vašom systéme —
  > `<deep-link URL>`. Ak ju u seba nevidíte, napíšte prosím sem do vlákna —
  > chyba je na našej strane a hneď to opravíme.
  >
  > ZbynekAI `<N>`

  The `ZbynekAI <N>` line is MANDATORY on this and every message (signature rule
  above) — it is the LAST line even here in the template a stream copies verbatim.
  A marek-owned stream (montalu4) substitutes its own identity here: `MarekAI <N>`
  (#641) — sign YOUR stream's word, never the wrong person's.

- **A ticket that BOUND an Odoo Discuss thread may be CLOSED only after a
  closing note lands in that thread — the LAST message in the thread is ALWAYS
  the sub-dev's (airuleset #627, owner directive 2026-08-22).** When you open or
  first post into a client thread for a ticket, record the binding on the ticket
  as a line-anchored comment `Discuss-thread: <channel-id>` (the id you already
  cite as "vlákno N") — that is the durable, non-guessy group key, orthogonal to
  the mutable `stream:` label, so it survives a ticket moving between streams.
  Before that ticket is closed, whoever CURRENTLY owns the thread posts a closing
  note into it ("Dobrý deň / Ahoj `<mená>`, všetko z tejto témy je vyriešené,
  vlákno uzatváram — ďakujeme"; still `partner_ids` incl. the owner, still the
  `ZbynekAI <N>` signature), then records the evidence on the ticket:
  `Discuss-closed: msg <message-id>` citing the posted note. **N tickets, one
  thread:** the note goes ONCE, at the LAST ticket bound to the thread; a
  non-last ticket closes with `Discuss-defer: siblings #<A> #<B> still open —
  note goes at the last close` instead (you self-declare last vs non-last,
  naming the siblings — falsifiable, so no code has to guess which is last).
  **The obligation FOLLOWS THE TICKET to its current owner / the closing hand,
  never the author** — on the sub-dev path YOU post the note + record the line +
  close; on the branch-merge path the OWNING stream posts the note + records
  `Discuss-closed:` at hand-off, and the gatekeeper's later release-close finds
  the evidence (the gatekeeper never posts to the client thread — the owning
  stream does). This is HOOK-ENFORCED: `hooks/block-fork-no-merge-issue-close.sh`
  BLOCKS a `gh issue close` of a thread-bound odoo-erp ticket that carries no
  such disposition, for any authority (its own `discuss_close_guard.py` reads the
  ticket text at close time). Bypass only a genuine non-client / meta ticket
  (`airuleset:discuss-close-ok` in the close command).

One thread = one topic, a sub-thread under the channel the owner named (montalu:
IT-support) — never a new top-level channel or group chat (see `## Channel +
recipients` in `SKILL.md`). Ask the owner ONE decision at a time, and re-ask a
question whole and fresh if you have to (`user-questions-slovak.md`).
