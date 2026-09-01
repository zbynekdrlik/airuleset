# Composing the client handover PROD Discuss proposal

**This is the SINGLE canonical handover-proposal rule for EVERY sub-dev stream.**
Streams composed client PROD Discuss threads differently until the owner tired of
re-teaching (montalu5 2026-08-16: a proposal with no deep-link URL, no owner
membership). Keep no private per-stream notes. SEND mechanics (body_is_html,
partner_ids incl. the owner, sub-thread by name, post-verify) live in the sibling
`odoo-discuss-xmlrpc` skill (`SKILL.md`);
THIS file is the COMPOSE — what a message must contain — and EVERY message, opening
AND follow-up, is presented to the OWNER for approval BEFORE posting.

- **The owner must APPROVE the exact text of EVERY client-facing Discuss message
  BEFORE it is posted — the OPENING message AND every follow-up reply / question /
  reminder into an EXISTING thread, without exception.** Not limited to a handover
  proposal or a new thread — whatever goes to a client is approved first (owner
  ruling after montalu6 posted an unapproved message into a live client thread on
  PROD, 2026-08-22, thread 283 — deleted within a minute, but the bus push +
  notification had already reached the client, irreversible). A stream reading the
  approval rule as applying only to thread CREATION is exactly what caused it; it
  applies to every message. Jediná výnimka: dva mechanické closure typy (final
  reminder + closing note) majú #799 STANDING template grant (Closure bullet
  nižšie). HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`,
  airuleset #628): a sub-dev stream `message_post` to a `discuss.channel` is
  BLOCKED until the content carries the falsifiable marker
  `airuleset:owner-approved <ref>` — a reference to HOW/WHEN the owner approved
  THIS text, never a bare "approved" (same model as `Discuss-closed:` /
  `Self-service-checked:`); also record the approval on the ticket
  (`durable-decisions-to-tickets.md`). Bypass for a genuine internal/non-client
  post: `airuleset:discuss-approval-ok`.
- **The proposal you present to the owner is COMPLETE and lives IN THE CHAT —
  and it is the approval question for EVERY message (opening handover AND every
  follow-up), never only a new thread.** Put in the chat message itself: (1) the
  target thread on its OWN SEPARATE, clearly-shown line — the exact thread name,
  the full human NAME (+ parent channel where it helps), NEVER only the internal
  channel number and NEVER only wrapped in prose:
  `Vlákno: „Tabula objednavok 1" (pod IT-support, montalu PROD)`; (2) the FULL
  message body verbatim; (3) the member list. NEVER "the text is on the ticket" —
  the owner does not read tickets, so a proposal pointing at a ticket instead of
  carrying the whole text is not a proposal. Naming the target only by its
  internal number is what forced the owner to ask „do akého vlákna to má ísť?"
  (airuleset #632, montalu1: an approval question referenced its target only as
  „vlákno 250" instead of a separate visible field). Every owner-facing mention
  of a thread also carries its own clickable deep URL, never a bare channel
  number (airuleset #657/#650, uniform doctrine in
  `modules/core/issue-reference-context.md`):
  `Vlákno: „Tabula objednavok 1" — https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288`
  — open it and confirm it loads before pasting; this is the ❓-approval-question
  case of the rule (`hooks/stop-check-question-quality.sh` Check 6, #650) as well
  as the wider owner-facing surface (`hooks/stop-check-prose-violations.sh`, #657).
- **The thread NAME ends with the owning stream's NUMBER**, so the owner sees at
  a glance which stream owns it (montalu3 → "Kontrola zákazníckych e-mailov 3"),
  formalizing the existing IT-support convention on montalu PROD. The suffix is
  the STREAM'S NUMBER: for a NUMBERED stream it is the trailing digits of the
  stream name (montalu2..8 → 2..8, david2..4 → 2..4, miva1 → 1). For an
  UNNUMBERED base stream (montalu, marek, david, simap) the suffix is "1" — the
  first stream of its client family — CONFIRMED by the owner on airuleset #532
  (2026-08-18): unnumbered base streams are being renamed to <name>1 (airuleset
  #537: montalu→montalu1, david→david1, simap→simap1; marek stays unnumbered,
  unused, no client handovers), so every active handover stream ends with its
  number. **The name is at most ~30 CHARACTERS including that trailing number**
  (airuleset #597): a longer name is truncated behind the Discuss sidebar's
  first page and the number disappears. Good: „Oprava filtra rozmerov 2" (24).
  Too long: „Viditeľnosť leadov pre obchodníkov 2" (36) — shorten to e.g.
  „Viditeľnosť leadov 2" (20). The proposal you present must ALREADY carry a
  name satisfying both conditions — never a long or un-numbered draft the owner
  has to fix. Both are HOOK-ENFORCED at create time
  (`hooks/block-discuss-thread-name.sh`, airuleset #596/#597): a create whose
  name breaks either condition is BLOCKED before it reaches PROD (a rename is
  never blocked; a `message_post` gets its own SIGNATURE check — next bullet,
  airuleset #609).
- **Every message ENDS with a stream-identity signature line.** The LAST line of
  every message body is the stream's identity signature `<WORD> <N>` — `ZbynekAI <N>`
  by DEFAULT, `MarekAI <N>` for a marek-owned stream (montalu4, posts via Marek's
  own handover account, airuleset #641 → odoo-erp #3864). The owner sees at a
  glance WHICH stream sent it (owner request, airuleset #598 — a bare shared
  sender name told the owner nothing). `<WORD>` is the compact form of the
  account's client-visible display name, derived from `notify.STREAM_NOTIFY_OWNER`
  — the SAME single source that routes Discord notifications, never a second map;
  signing the WRONG person's name is BLOCKED too. `<N>` is the SAME stream number
  as the thread-name suffix above — the trailing digits of the unix user, or "1"
  for an UNNUMBERED base stream (montalu, david, simap): montaluN → N, davidN →
  N (base david → 1), simapN → N (base → 1), miva1 → 1 / mivaN → N (montalu4
  signs MarekAI 4). It REUSES the project's existing stream number, NEVER a
  second derivation — for a NUMBERED stream it matches
  `cli_aliases.short_target_alias`'s family regexes and the #532 thread-name
  suffix; the base → 1 case is the #532/#537 convention's own mapping, NOT
  derived from those `\d+` regexes. On any single client Odoo instance only ONE
  stream family posts, so the bare number is unambiguous there. The signature
  stays on EVERY message — never dropped the way the greeting is. It is a
  POISTKA that works even while streams SHARE one Odoo account; if the accounts
  are ever renamed per stream (airuleset #598 → odoo-erp #4624) the signature
  layer stays uncontradicted. HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`,
  airuleset #609): a `message_post` with no valid identity signature (`ZbynekAI
  <N>` / `MarekAI <N>`, or the WRONG identity, #641) is BLOCKED before it
  reaches PROD, regardless of which skill you loaded (montalu6 shipped an
  unsigned client message from a skill that never carried this rule). Bypass
  for a genuine internal/legacy post: `airuleset:discuss-sig-ok` in the content.
- **The message body MUST carry a direct deep-link URL to the LIVE feature** on
  the client's PROD — the actual route/record/page URL the client clicks to SEE
  it, never a menu path ("Predaj → Objednávky → …") and never the bare instance
  homepage. Open the URL and confirm it loads before putting it in the proposal.
  **This applies to EVERY openable reference in the message, not only the
  handed-over feature:** a record, screen, action, report or dashboard — each gets
  its OWN direct functional URL, verified live before sending, never a prose menu
  path (owner directive, airuleset #595: msg 1723308 described two live features
  only by menu path, rejected). This generalizes completion-report.md's 🌐-line
  rule to client-facing Discuss messages.
- **State the owner's thread membership EXPLICITLY in the proposal.** The posting
  recipe already puts the owner on `partner_ids` (control ping) — but the PROPOSAL
  text you show the owner must SAY so ("teba pridám do vlákna ako člena"), so the
  owner knows they will see the thread and can catch a broken delivery.
- **Announce ONLY functions that are ALREADY LIVE on the client's PROD.** Never a
  merged-but-not-yet-deployed or scheduled feature — the client must be able to act
  the moment they read. Confirm it is live on their PROD first.
- **Len minulé, overené udalosti — klientska správa sa NIKDY neodvoláva na to,
  čo sa LEN STANE (airuleset #696, owner ruling 2026-08-25).** Incident (vlákno
  263): stream sľúbil „od zajtrajšieho ranného e-mailu" digest kým ešte
  neexistoval. Keď je viditeľný
  výstup funkcie plánovaný ARTEFAKT (digest e-mail, report, cron
  výsledok), máš dve legálne cesty — obe končia správou v MINULOM ČASE: (1)
  spusti artefakt TERAZ (vlastnou právomocou, alebo `GATEKEEPER-ACTION:`) a
  OVER, že odišiel S prisľúbeným obsahom (read-back z čerstvej prod-kópie,
  nikdy len „odoslané"); alebo (2) počkaj na najbližší plánovaný beh, over ho, a
  až potom píš — v minulom čase. HOOK-ENFORCED
  (`hooks/block-discuss-thread-name.sh`, airuleset #696): `message_post` s
  budúcim sľubom v tele je BLOKOVANÝ, kým obsah nenesie falsifikovateľnú značku
  `airuleset:artifact-verified <ref>` — referenciu na to, ČO si z reálneho
  artefaktu odčítal, kde a kedy (model `airuleset:owner-approved`) — doktrína
  platí na KAŽDÉ preformulovanie.
- **A client message NEVER tells the client what WE lack — it reports ONLY
  what is delivered and working (airuleset #742).** "Chýba nám X" / "nemáme
  prístup k Y" / "nevieme to overiť" / "nestihli sme Z" — any framing that
  surfaces OUR internal gap into a client-facing message is unprofessional and
  leaves the client nothing actionable ("čo s tým mám ako klient robiť?"). When
  something is missing or unfinished on OUR side, there are exactly two legal
  paths, mirroring the #696 verified-past-events rule above: (1) FIX it first —
  get the access/data from the owner, self-service verify it
  (`autonomous-verification.md`'s "What's on PROD?" tree), finish the step —
  THEN message the client about the COMPLETED result; or (2) DON'T message yet
  — wait until there is something real to report. The one legitimate exception
  is a genuine REQUEST for something FROM the client (an input, a decision, an
  access grant only they can give) — a normal ask phrased as a concrete request,
  never as a complaint about what is missing: "Potrebovali by sme od vás X…" —
  never "Nemáme od vás X". The closing template's self-blame line ("chyba je na
  našej strane…") is a HYPOTHETICAL fault-path, not a current gap, so it stays
  mandatory. This is a JUDGMENT call on message CONTENT a phrase-matching hook
  cannot gate without false-positive risk, so it rides the per-message
  owner-approval gate — the owner reviewing the text is the backstop.
- **Každý adresát je REÁLNE označený — mention anchor v tele je POVINNÝ popri
  `partner_ids`, na KAŽDEJ správe (airuleset #702, owner ruling 2026-08-25).**
  `partner_ids` správu DORUČÍ (inbox/e-mail + owner control ping); MENTION
  notifikáciu (klient s „len zmienky") spúšťa až mention ANCHOR v HTML tele. Tri
  schválené správy (montalu PROD 262/287) odišli len s `partner_ids` bez pingu —
  opravené unlink+repost (msg 1742837/1742838). Anchor pre KAŽDÉHO adresáta
  (atribúty podľa `SKILL.md` proti reálnemu 19.0 composeru):
  `<a href="/odoo/res.partner/<id>" class="o_mail_redirect" data-oe-id="<id>" data-oe-model="res.partner">@Meno</a>`.
  HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`, airuleset #702): stream
  `message_post` na `discuss.channel`, ktorého content menuje `partner_ids`, ale
  nenesie žiadny mention anchor, je BLOKOVANÝ — hook skenuje samotný tool-call
  payload, takže platí bez ohľadu na to, ktorý skill si načítal. Bypass (interný
  post bez adresátov): `airuleset:discuss-mention-ok` v contente (logged).
- **The greeting (oslovenie — „Dobrý deň…" / „Ahoj…") belongs ONLY in the FIRST
  (opening) message of a thread.** A follow-up reply in an existing thread
  carries NO greeting — it continues directly with the content (a REAL
  `@`-mention anchor for EVERY addressee — #702 above — and `partner_ids` for
  delivery ALWAYS, on every message). Repeating „Dobrý deň…" on every follow-up
  reads as machine-sent — the live miva PROD thread „Augustová dochádzka" had
  three same-day follow-ups all reopening with „Dobrý deň…" (airuleset #573,
  2026-08-19). Greet once, at the top of the thread; after that, just the
  message.
- **React to the client's previous answer FIRST — never drop a new question into
  a thread that ignores what the client last said.** Before posting any new
  question into an EXISTING client thread, check for the client's last
  unreflected answer and OPEN by briefly reacting to it; only THEN ask the next
  thing. This applies to a follow-up into an existing thread, not just to
  opening one — a reply that reflects nothing reads as machine-sent (incident
  montalu1, „Etapy zákaziek vo výrobe 1", airuleset #625).
- **Address register PER PERSON — vykanie only for the CEO, tykanie for the other
  named contacts, VYKANIE by default for anyone not yet listed.** Before every
  `message_post`, check the register below and use the right register for that
  person; never address a formal-register contact informally (airuleset
  #625/#626, owner ruling 2026-08-22). The register is PER-PROJECT and grows by
  ONE line as new client people appear — add a contact to its project's row:
  - **montalu** — VYKANIE: CEO Pavol Špetta (menovite). TYKANIE: Patrik Javorský,
    Dominik Volek, Peter Hollý. DEFAULT for anyone NOT listed here: VYKANIE, until
    the owner says otherwise.

  A NEW client person you have not been told how to address is VYKANIE by default
  (#626) — never guess tykanie; ask the owner, then add the line.
- **Close the client message with named recipients + the self-blame reassurance**,
  so a client who cannot see the feature reports it back to YOU instead of
  assuming they did something wrong. The `Ahoj <mená>…` opening below is the
  OPENING message of the thread; a follow-up reply drops the oslovenie and
  starts straight at the content (greeting rule above). Slovak template (adapt
  names/feature/URL):

  > Ahoj `<mená>`, funkcia `<čo>` je už nasadená na vašom systéme —
  > `<deep-link URL>`. Ak ju u seba nevidíte, napíšte prosím sem do vlákna —
  > chyba je na našej strane a hneď to opravíme.
  >
  > ZbynekAI `<N>`

  The `ZbynekAI <N>` line is MANDATORY on this and every message — the LAST
  line even in a template a stream copies verbatim. A marek-owned stream
  (montalu4) substitutes its own identity here: `MarekAI <N>` (#641) — sign
  YOUR stream's word, never the wrong person's.

- **Closure protokol — dodané + JEDNA pripomienka → ticho = akceptované → close
  (airuleset #799, owner 2026-09-01).** Dodané + overené (#446) a klient
  nepotvrdzuje → NEpushuj donekonečna (#570/#753); closure má TERMINÁLNY stav:
  (1) JEDNA vecná pripomienka v #607 pracovnom okne; (2) ticho **N = 3 PRACOVNÉ
  dni** po nej (víkendovo-vedomé per #607 `working_time`; ticho = žiadna správa
  ANI #745 emoji reakcia — over reakcie pred closure); (3) POSTni closing nótu
  (#627 — POSLEDNÁ správa vlákna, nikdy klientovo mlčanie); (4) close s citáciou
  `Acceptance-tacit: <msg-id doručenia> / <msg-id pripomienky>` + `Discuss-closed:
  msg <id>`; (5) thread disposition NEhardcoduj ako „archív" — deferuj ju na #788
  TTL-hide bullet nižšie. `stale!` eskalácia (#570) KONČÍ týmto closure, nie ďalším
  pushom. `Acceptance-tacit:` je DÔKAZ, nie dispozícia — close nesie AJ #627
  dispozíciu (`Discuss-defer:` / `Discuss-closed: msg <id>`), ako #755. Klient
  odpovie KÝM okno beží → NEuzatváraj tacitne: reaguj (#625); potvrdzuje → close
  cez #755, NOVÁ téma → peeluj ju per #728 KRÁTKOU šablónovou redirect odpoveďou.
  **Dva mechanické typy majú STANDING template grant:** finálna pripomienka +
  closing nóta citujú `airuleset:owner-approved template:final-reminder` /
  `template:closing-note` (owner schváli ŠABLÓNU raz; ref voliteľný) namiesto
  per-message; nesankcionovaný `template:<iný>` NEudelí — hook #628/#799.

- **Disposition po uzatváracej správe — SAMO-SCHOVANIE (TTL), nie archivácia
  (airuleset #788, owner 2026-08-31 „radsej davat vlakno schovat … na napr. 10h").**
  Keď #627 closing nóta landne, NEARCHIVUJ — ARMuj vláknu TTL self-hide (po čase
  samo zmizne členom, HISTÓRIA ostáva dohľadateľná). Mechanizmus HOTOVÝ + RELEASED
  (odoo-erp issue 5630, release 19.0.2.230.0, `company_base` — presné API tam):
  helper `_company_base_schedule_close_hide()` + ICP `mail.closed_thread_hide_hours`
  (default 10) poháňa natívny `unpin_dt`, NIKDY `active=False`. Archivácia
  (`active=False`) ostáva LEN ako fallback / gk cleanup, nikdy default.
  **Disarm-on-reply (odoo-erp#5630 delegoval SEM):** klientska odpoveď v ARMnutom
  okne DISARMuje hide — zlož / nere-armuj marker EXPLICITNE (nikdy sa nespoliehaj
  na `last_interest_dt` race). Odpoveď zachytí jej vlastná notifikácia + #625
  react-first duty, takže vlákno s čerstvou aktivitou nikdy ticho nezmizne; re-arm
  až po skutočnom uzavretí. Model dáva len primitív; policy je #788.

- **A ticket that BOUND an Odoo Discuss thread may be CLOSED only after a
  closing note lands in that thread — the LAST message in the thread is ALWAYS
  the sub-dev's (airuleset #627, owner directive 2026-08-22).** When you open
  or first post into a client thread for a ticket, record the binding as a
  line-anchored comment `Discuss-thread: <channel-id>` (the id already cited as
  "vlákno N") — a durable group key, orthogonal to the mutable `stream:` label.
  Before that ticket is closed, whoever CURRENTLY owns the thread posts a
  closing note into it ("Dobrý deň / Ahoj `<mená>`, všetko z tejto témy je
  vyriešené, vlákno uzatváram — ďakujeme"; still `partner_ids` incl. the owner,
  still the `ZbynekAI <N>` signature), then records the evidence on the ticket:
  `Discuss-closed: msg <message-id>` citing the posted note. **N tickets, one
  thread:** the note goes ONCE, at the LAST ticket bound to the thread; a
  non-last ticket closes with `Discuss-defer: siblings #<A> #<B> still open —
  note goes at the last close` instead (you self-declare last vs non-last,
  naming the siblings). **The obligation FOLLOWS THE TICKET to its current
  owner / the closing hand, never the author** — sub-dev path: YOU post the
  note + record the line + close; branch-merge path: the OWNING stream posts
  the note + records `Discuss-closed:` at hand-off, and the gatekeeper's later
  release-close finds the evidence (the gatekeeper never posts to the client
  thread — the owning stream does). This is HOOK-ENFORCED:
  `hooks/block-fork-no-merge-issue-close.sh` BLOCKS a `gh issue close` of a
  thread-bound odoo-erp ticket that carries no such disposition, for any
  authority. Bypass only a genuine non-client/meta ticket:
  `airuleset:discuss-close-ok` in the close command.

- **Rodinná (capability-group) akceptácia — jedno vlákno zavrie N ticketov
  (airuleset #755, owner-request 2026-08-30).** Tickety JEDNEJ capability rodiny
  (jedna dodaná vec z pohľadu klienta; rodina je **ĽUDSKÝ ÚSUDOK** v návrhu,
  **NIKDY kódová detekcia** — anti-heuristic `discuss_close_guard.py`) smú zdieľať
  JEDNO akceptačné vlákno; klientovo potvrdenie (správa ALEBO #745 emoji reakcia)
  je dôkaz pre VŠETKY tickety rodiny. „One thread = one topic" platí — téma je
  CAPABILITY, nie ticket. **Spätná citácia + same-cycle close:** keď akceptácia
  landne, session ju v **TOM ISTOM cykle** cituje na VŠETKÝCH ticketoch a zavrie
  ich — NIKDY nečaká na per-ticket udalosť (14× sa to nestalo, montalu3: dôkaz už
  ležal vo vlákne, necitovaný). Každý close nesie **`Acceptance-cited: vlákno
  „<meno>" (discuss.channel_<N>) / msg <id> / <kto> <kedy>`**. **`Acceptance-cited:` je DÔKAZ,
  NIKDY dispozícia:** close nesie VŽDY AJ #627 dispozíciu — `Discuss-defer:` pre
  ne-posledný, `Discuss-closed: msg <id>` pre POSLEDNÝ (ktorý postne zavieraciu
  nótu). Rodina NIKDY nezavrie len citáciou — inak posledná správa ostane
  klientova a #627 padne. `discuss_close_guard.py` ostáva **NEDOTKNUTÝ**: close
  len s `Acceptance-cited:` bez #627 dispozície správne BLOKUJE (#516). Batchovanie
  draftov rodiny je v `modules/core/statusline-vocabulary.md` (#755/#606).

- **One thread = one topic — now the WHOLE lifecycle, not just addressing
  (airuleset #728, owner directive 2026-08-26).** Verbatim: „treba vlakna
  drzat maximalne atomicke a ak sa otvori nejaka nova tema vo vlakne tak
  radsej vytvorit nove vlakno/ticket a spravu ktora temu vyvolala
  prekopirovat, presunut do toho noveho vlakna". The pre-#728 rule below
  ("one thread = one topic, a sub-thread under the channel the owner named")
  covered only ADDRESSING at creation — it now covers the thread's ENTIRE
  lifecycle: every follow-up, reminder and reply posted into an EXISTING
  thread must still belong to that thread's OWN topic, never a different one
  it merely happens to sit in. Incident: „Etapy zákaziek vo výrobe 1"
  (discuss.channel_257) grew to 36 messages across ~6 topics + a CEO new-topic —
  owner had to review + close by hand.
- **A NEW topic a participant (client / CEO / anyone) opens in an EXISTING
  client thread is NEVER developed there.** The stream creates a NEW ticket
  immediately — and, once it reaches client communication, a NEW thread once the
  owner approves its exact name + text (the SAME per-message approval doctrine as
  the FIRST bullet of this file — a split is never an excuse to skip approval) — and
  COPIES/quotes the triggering message into the new ticket/thread WITH A CITATION
  (msg id + author + date), so the context is never torn away from its origin. When
  the new ticket binds its thread, record it with the SAME `Discuss-thread:
  <channel-id>` key the #627 closure doctrine above already uses, never a second
  mechanism. A long/resolved/multi-topic thread is CLOSED (the #627 bullet above),
  never left to grow forever; THIS bullet peels a new topic off the moment it
  appears. If the triggering message is ALSO the client's not-yet-reacted last
  message (#625), a brief APPROVED acknowledgement in the EXISTING thread — pointing
  to the new ticket/thread, never developing the new topic itself there — satisfies
  #625; it needs the SAME owner approval as any other. Precedent: odoo-erp #5319
  (triggering msg 1724252/1724253 copied across), closure of thread 257 via msg
  1743448.
- **Atomicity also applies at CREATION, not only to organic growth
  (airuleset #742).** #728 above covers a topic that emerges INSIDE an
  already-open thread; this closes the other half — when a brand-new proposal
  would cover MORE THAN ONE topic, split it into SEPARATE threads from the start
  — never bundle them into one opening message "to save a round of owner
  approval". One thread = one topic is the rule at every point in a thread's
  life, including message zero. Each split thread gets its own name (naming rule
  above), its own `Discuss-ticket:` / `Discuss-thread:` binding, and its own
  owner approval — never a shortcut around any of those.

Every thread this file governs still follows the existing channel
placement rule: a sub-thread under the channel the owner named (montalu:
IT-support) — never a new top-level channel or group chat (see `## Channel +
recipients` in `SKILL.md`). Ask the owner ONE decision at a time, and re-ask a
question whole and fresh if you have to (`user-questions-slovak.md`).
