# Composing the client handover proposal

**This is the SINGLE canonical handover-proposal rule for EVERY sub-dev stream.**
Keep no private per-stream notes (montalu5 2026-08-16). SEND mechanics live in the PROJECT's own rules (odoo-erp: `.claude/rules/odoo-task-sync.md`);
`SKILL.md` is the channel-agnostic pointer (airuleset issue 891).
THIS file is the COMPOSE — what a message must contain — and EVERY message, opening
AND follow-up, is presented to the OWNER for approval BEFORE posting.

- **The owner must APPROVE the exact text of EVERY client-facing Discuss message
  BEFORE it is posted — the OPENING message AND every follow-up reply / question /
  reminder into an EXISTING thread, without exception.** Not limited to a handover
  proposal or a new thread — whatever goes to a client is approved first (owner ruling 2026-08-22, montalu6 thread 283 — irreversible). A stream reading the
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
  it is the approval question for EVERY message (opening handover AND every
  follow-up), never only a new thread.** The FULL proposed client text MUST be
  INLINE as a `> ` quoted block — NEVER a share URL or path instead (#977; a
  share URL may only accompany an attachment — `stop-check-question-quality.sh`
  Check 8 blocks a share URL with no `>` quote). Put in the chat itself: (1) the
  target thread on its OWN SEPARATE, clearly-shown line — the exact thread name,
  the full human NAME (+ parent channel where it helps), NEVER only the internal
  channel number and NEVER only wrapped in prose:
  `Vlákno: „Tabula objednavok 1" (pod IT-support, montalu PROD)`; (2) the FULL
  message body verbatim; (3) the member list. NEVER "the text is on the ticket" —
  the owner does not read tickets (airuleset #632: „vlákno 250"). Every thread
  mention carries its deep URL, never a bare channel number (airuleset #657/#650):
  `Vlákno: „Tabula objednavok 1" — https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288`
  — confirm it loads before pasting (Check 6 #650 + `stop-check-prose-violations.sh` #657).
- **The thread NAME ends with the owning stream's NUMBER**, so the owner sees at
  a glance which stream owns it (montalu3 → "Kontrola zákazníckych e-mailov 3").
  The suffix is
  the STREAM'S NUMBER: for a NUMBERED stream it is the trailing digits of the
  stream name (montalu2..8 → 2..8, david2..4 → 2..4, miva1 → 1). For an
  UNNUMBERED base stream (montalu, marek, david, simap) the suffix is "1" — the
  first stream of its client family — CONFIRMED by the owner on airuleset #532
  (2026-08-18; base streams renamed <name>1 per airuleset #537), so every active
  handover stream ends with its number. **The name is at most ~30 CHARACTERS including that trailing number**
  (airuleset #597). The proposal you present must ALREADY carry a name satisfying
  both conditions. Both are HOOK-ENFORCED at create time
  (`hooks/block-discuss-thread-name.sh`, airuleset #596/#597): a create whose
  name breaks either condition is BLOCKED before it reaches PROD (a rename is
  never blocked; a `message_post` gets its own SIGNATURE check — next bullet,
  airuleset #609).
- **Every message ENDS with a stream-identity signature line.** The LAST line of
  every message body is the stream's identity signature `<WORD> <N>` — `ZbynekAI <N>`
  by DEFAULT, `MarekAI <N>` for a marek-owned stream (montalu4, via Marek's own
  handover account, airuleset #641). The owner sees at a glance WHICH stream sent
  it (owner request, airuleset #598). `<WORD>` is the account's client-visible
  display name, derived from `notify.STREAM_NOTIFY_OWNER` — the SAME source that
  routes Discord, never a second map; signing the WRONG name is BLOCKED. `<N>` is
  the SAME stream number as the thread-name suffix — the trailing digits of the
  unix user, or "1" for an UNNUMBERED base stream (montalu, david, simap):
  montaluN → N, davidN → N (base david → 1), simapN → N, miva1 → 1 (montalu4
  signs MarekAI 4). It REUSES the existing stream number, NEVER a second
  derivation — NUMBERED matches `cli_aliases.short_target_alias` + the #532
  thread-name suffix; base → 1 is the #532/#537 convention's own mapping, NOT
  derived from those `\d+` regexes. On any single client Odoo instance only ONE
  stream family posts, so the bare number is unambiguous. The signature stays on
  EVERY message — never dropped the way the greeting is (a POISTKA while streams
  share one Odoo account; per-stream rename airuleset #598 → odoo-erp #4624). HOOK-ENFORCED
  (`hooks/block-discuss-thread-name.sh`, airuleset #609): a `message_post` with
  no valid identity signature (`ZbynekAI <N>` / `MarekAI <N>`, or the WRONG
  identity, #641) is BLOCKED before PROD, regardless of the loaded skill.
  Bypass a genuine internal/legacy post: `airuleset:discuss-sig-ok`.
- **Chatter = helper only; `body_is_html=True` is MANDATORY on every
  `message_post` with HTML tags.** Without it Odoo escapes the HTML and clients
  see raw `<p>` tags. HOOK-ENFORCED (`hooks/block-odoo-message-post-without-html.sh`,
  airuleset #915): payload with HTML + no `body_is_html` is BLOCKED fleet-wide.
  After posting, read back and verify 0 escaped messages (#916 Stop check). **Gk
  NEVER posts via an ad-hoc driver — NIKDY ad-hoc driver mimo stream recept:** use
  the stream-approved poster (its read-back); a driver shipped by
  `scp` / `ssh … odoo shell < driver.py` is OPENED + gated too — dvojité
  escapnutie (`&lt;p&gt;`) or a missing read-back = BLOCK (#1054, odoo-erp #4650).
- **The message body MUST carry a direct deep-link URL to the LIVE feature** on
  the client's PROD — the actual route/record/page URL the client clicks to SEE
  it, never a menu path ("Predaj → Objednávky → …") and never the bare homepage.
  Open it and confirm it loads before putting it in the proposal. **This applies
  to EVERY openable reference in the message, not only the handed-over feature:**
  each such reference gets its OWN functional URL, verified live before sending,
  never a prose menu path (airuleset #595: msg 1723308, rejected). This generalizes completion-report.md's 🌐-line
  rule to Discuss messages.
- **State the owner's thread membership EXPLICITLY in the proposal.** The recipe
  already puts the owner on `partner_ids` (control ping) — but the PROPOSAL text
  must SAY so ("teba pridám do vlákna ako člena"), so the owner knows they will
  see the thread and can catch a broken delivery.
- **Announce ONLY functions that are ALREADY LIVE on the client's PROD.** Never a
  merged-but-undeployed or scheduled feature; confirm it is live first.
- **Len minulé, overené udalosti — klientska správa sa NIKDY neodvoláva na to,
  čo sa LEN STANE (airuleset #696, owner ruling 2026-08-25).** Incident: stream sľúbil „od zajtrajšieho ranného e-mailu" digest kým ešte
  neexistoval. Keď je viditeľný výstup funkcie plánovaný ARTEFAKT (digest
  e-mail, report, cron), máš dve cesty — obe končia správou v MINULOM ČASE: (1)
  spusti artefakt TERAZ (vlastnou právomocou / `GATEKEEPER-ACTION:`) a OVER, že
  odišiel S prisľúbeným obsahom (read-back z čerstvej prod-kópie, nikdy len
  „odoslané"); alebo (2) počkaj na najbližší plánovaný beh, over ho, potom píš
  — v minulom čase. HOOK-ENFORCED
  (`hooks/block-discuss-thread-name.sh`, airuleset #696): `message_post` s
  budúcim sľubom v tele je BLOKOVANÝ, kým obsah nenesie falsifikovateľnú značku
  `airuleset:artifact-verified <ref>` — referenciu na to, čo si z artefaktu
  odčítal, kde a kedy (model `airuleset:owner-approved`) — doktrína platí na
  KAŽDÉ preformulovanie.
- **A client message NEVER tells the client what WE lack — it reports ONLY
  what is delivered and working (airuleset #742).** "Chýba nám X" / "nemáme
  prístup k Y" / "nevieme to overiť" / "nestihli sme Z". When something is missing on OUR side, two legal
  paths (mirroring #696 above): (1) FIX it first —
  get the access/data from the owner, self-service verify it
  (`autonomous-verification.md`'s "What's on PROD?" tree), finish the step —
  THEN message the client about the COMPLETED result; or (2) DON'T message yet
  — wait until there is something real to report. The one legitimate exception
  is a genuine REQUEST for something FROM the client — a normal ask phrased as a concrete request,
  never as a complaint about what is missing: "Potrebovali by sme od vás X…" —
  never "Nemáme od vás X". A JUDGMENT call on message CONTENT (a hook cannot gate it without false-positive
  risk), so it rides the per-message owner-approval gate.
- **Každý adresát je REÁLNE označený — mention anchor v tele je POVINNÝ popri
  `partner_ids`, na KAŽDEJ správe (airuleset #702, owner ruling 2026-08-25).**
  `partner_ids` správu DORUČÍ (inbox/e-mail + owner control ping); MENTION
  notifikáciu (klient s „len zmienky") spúšťa až mention ANCHOR v HTML tele
  (incident: msg 1742837/1742838 odišli bez pingu → repost). Anchor pre KAŽDÉHO adresáta
  (atribúty podľa `SKILL.md`, 19.0 composer):
  `<a href="/odoo/res.partner/<id>" class="o_mail_redirect" data-oe-id="<id>" data-oe-model="res.partner">@Meno</a>`.
  HOOK-ENFORCED (`hooks/block-discuss-thread-name.sh`, airuleset #702): stream
  `message_post` na `discuss.channel`, ktorého content menuje `partner_ids`, ale
  nenesie žiadny mention anchor, je BLOKOVANÝ — platí bez ohľadu na načítaný
  skill. Bypass (interný
  post bez adresátov): `airuleset:discuss-mention-ok` v contente (logged).
- **The greeting (oslovenie — „Dobrý deň…" / „Ahoj…") belongs ONLY in the FIRST
  (opening) message of a thread.** A follow-up reply in an existing thread
  carries NO greeting — it continues directly with the content (a REAL
  `@`-mention anchor for EVERY addressee — #702 above — and `partner_ids` for
  delivery ALWAYS, on every message). Repeating „Dobrý deň…" on every follow-up
  reads as machine-sent (airuleset #573). Greet once, at the top of the thread.
- **Explain the concept to the client, not just a link + feature list.** For a
  non-technical client, explain in one plain sentence WHAT each named thing is and
  HOW it fits their day before any link (#1028).
- **No promises on the client's behalf.** Keep closings neutral and async — never
  promise the user will personally demo/explain, never offer live demos or video
  calls to a non-technical client (#1028).
- **React to the client's previous answer FIRST — never drop a new question into
  a thread that ignores what the client last said.** Before posting a new
  question into an EXISTING client thread, check the client's last unreflected
  answer and OPEN by briefly reacting to it; only THEN ask the next thing. This
  applies to a follow-up into an existing thread, not just to opening one — a
  reply that reflects nothing reads as machine-sent („Etapy zákaziek vo výrobe
  1", airuleset #625).
- **Address register PER PERSON — vykanie only for the CEO, tykanie for the other
  named contacts, VYKANIE by default for anyone not yet listed.** Before every
  `message_post`, check the register below and use the right register for that
  person; never address a formal-register contact informally (airuleset
  #625/#626, owner ruling 2026-08-22). The register is PER-PROJECT and grows by
  ONE line as new client people appear — add a contact to its project's row:
  - **montalu** — VYKANIE: CEO Pavol Špetta (menovite), Barbora Čuhaničová.
    TYKANIE: Patrik Javorský, Dominik Volek, Peter Hollý. DEFAULT for anyone NOT
    listed here: VYKANIE.

  A NEW client person you have not been told how to address is VYKANIE by default
  (#626) — never guess tykanie; ask the owner, then add the line.
- **Close the client message with named recipients — NO mandatory self-blame
  line (#823, owner ruling 2026-09-01: it read as spam once repeated across
  client threads).** The `Ahoj <mená>…` opening below is the OPENING message
  of the thread; a follow-up reply drops the oslovenie and starts straight at
  the content (greeting rule above). Slovak template (adapt names/feature/URL)
  — ends after the deep-link. An optional short non-blame line ("Ak niečo
  nesedí, napíšte sem do vlákna.") may fit THIS message — never mandated:

  > Ahoj `<mená>`, funkcia `<čo>` je už nasadená na vašom systéme —
  > `<deep-link URL>`.
  >
  > ZbynekAI `<N>`

  The `ZbynekAI <N>` line is MANDATORY on this and every message — the LAST
  line even in a template a stream copies verbatim. A marek-owned stream
  (montalu4) substitutes its own identity here: `MarekAI <N>` (#641) — sign
  YOUR stream's word, never the wrong person's.

- **Closure protokol — dodané + JEDNA pripomienka → ticho = akceptované → close
  (#799, owner 2026-09-01).** Dodané + overené (#446), klient nepotvrdzuje →
  NEpushuj donekonečna (#570/#753); closure: (1) JEDNA vecná pripomienka v #607
  pracovnom okne; (2) ticho **N = 3 PRACOVNÉ dni** (víkendovo-vedomé; ticho =
  žiadna správa ANI #745 reakcia); (3) closing nóta (#627 — POSLEDNÁ správa);
  (4) close `Acceptance-tacit: <msg-id doručenia>/<pripomienky>` +
  `Discuss-closed: msg <id>`; (5) thread disposition → #788 TTL-hide nižšie.
  `stale!` (#570) KONČÍ týmto closure. `Acceptance-tacit:` je DÔKAZ, nie
  dispozícia — close nesie AJ #627 dispozíciu, ako #755. Klient odpovie KÝM
  okno beží → reaguj (#625); potvrdzuje → #755, NOVÁ téma → #728 redirect.
  **STANDING template grant:** finálna pripomienka + closing nóta citujú
  `airuleset:owner-approved template:final-reminder` / `template:closing-note`;
  nesankcionovaný `template:<iný>` NEudelí — hook #628/#799.
- **INTAKE reaction FIRST + STANDING ack grant (#978/#1027/#1033):** the MOMENT
  you pick up a client message you will act on — before filing/dispatching —
  react 👷 (`ack_reaction_emoji`, legacy 👀 selectable) via
  `message_reaction_guarded(msg_id,"👷","add")`. A bare reaction carries no text
  → no owner text-approval (standing grant); a `user-request` ticket quoting it
  cites
  an `Ack-reaction:` line (filing-gate enforced). An explicit owner instruction
  about HIS channel overrides the fleet default → escalate to airuleset. Recipe:
  `ack-reaction.md`.
- **No interim "how to work around it" reply while a fix is in flight (#1027,
  owner 2026-09-14).** Client reports something + a fix lane is dispatched → send
  NO interim manual-workaround message; reply ONCE, after the fix is on PROD and verified. Exception: a yes/no question the client explicitly asked that the fix
  does not answer. Enforced by `stop-check-question-quality.sh` /
  `gates.questionscope`: a `❓` approving a client message with workaround
  phrasing (zatiaľ/medzitým/dovtedy/obísť/ručne/workaround) while the cited `#N`
  has an open lane is blocked.

- **Disposition po uzatváracej správe — SAMO-SCHOVANIE (TTL), nie archivácia
  (#788; #853 compliance).** Po #627 closing nóte ARMuj TTL self-hide (POVINNÝ
  krok — nóta bez arm-u = nedokončený close). **Archivácia (`active=False`) =
  disposition MIS-SHAPE** (trieda #601) — NIKDY default/fallback; HOOK-ENFORCED
  (`hooks/block-discuss-archive.sh`, #853): `action_archive`/`toggle_active`/
  `active=False` BLOKOVANÝ na `shared-stream` boxoch (bypass
  `# airuleset:discuss-archive-ok <reason>`). **Arm (odoo-erp 5946):** stream si
  hide armne SÁM cez `/json/2` — `schedule_close_hide_guarded(channel_id,
  hours=None)`; kým release nie je na PROD, `GATEKEEPER-ACTION:` ostáva arm path.
  Mechanizmus (#5630): `_company_base_schedule_close_hide()` + ICP
  `mail.closed_thread_hide_hours` (10h) poháňa `unpin_dt`, NIKDY `active=False`. **Disarm-on-reply:** klientska
  odpoveď DISARMuje hide — zlož marker EXPLICITNE (nikdy `last_interest_dt`
  race); re-arm až po uzavretí. **Per-stream sweep:** hotová téma s nótou → arm;
  bez nóty → nóta + arm; živá → nechať.

- **A ticket that BOUND an Odoo Discuss thread may be CLOSED only after a
  closing note lands in that thread — the LAST message in the thread is ALWAYS
  the sub-dev's (airuleset #627, owner directive 2026-08-22).** When you open or
  first post into a client thread, record the binding `Discuss-thread: <channel-id>`
  (the id cited as "vlákno N") — a durable group key, orthogonal to `stream:`.
  Before close, whoever CURRENTLY owns the thread posts a closing note ("Dobrý deň / Ahoj `<mená>`, téma vyriešená, vlákno
  uzatváram"; still `partner_ids` incl. the owner + the `ZbynekAI <N>`
  signature) and records `Discuss-closed: msg <message-id>`. **N tickets, one
  thread:** the note goes ONCE at the LAST ticket; a non-last ticket closes with
  `Discuss-defer: siblings #<A> #<B> still open — note goes at the last close`
  (self-declare last vs non-last, naming the siblings). **The obligation FOLLOWS
  THE TICKET to its current owner, never the author** — sub-dev: YOU post the
  note + line + close; branch-merge: the OWNING stream posts the note +
  `Discuss-closed:` at hand-off, the gatekeeper's later release-close finds it
  (the gatekeeper never posts to the client thread). HOOK-ENFORCED:
  `hooks/block-fork-no-merge-issue-close.sh` BLOCKS a `gh issue close` of a
  thread-bound odoo-erp ticket with no such disposition, for any authority.
  Bypass a genuine non-client/meta ticket: `airuleset:discuss-close-ok`.

- **Rodinná (capability-group) akceptácia — jedno vlákno zavrie N ticketov
  (airuleset #755, owner-request 2026-08-30).** Tickety JEDNEJ capability rodiny
  (jedna dodaná vec z pohľadu klienta; rodina je **ĽUDSKÝ ÚSUDOK** v návrhu,
  **NIKDY kódová detekcia** — anti-heuristic `discuss_close_guard.py`) smú zdieľať
  JEDNO akceptačné vlákno; klientovo potvrdenie (správa ALEBO #745 emoji reakcia)
  je dôkaz pre VŠETKY tickety rodiny. „One thread = one topic" platí — téma je
  CAPABILITY, nie ticket. **Spätná citácia + same-cycle close:** keď akceptácia
  landne, session ju v **TOM ISTOM cykle** cituje na VŠETKÝCH ticketoch a zavrie
  — NIKDY nečaká na per-ticket udalosť. Každý close nesie **`Acceptance-cited: vlákno „<meno>"
  (discuss.channel_<N>) / msg <id>`**; **`Acceptance-cited:` je DÔKAZ aj
  dispozícia (#891 channel-agnostic reversal)** — channel-agnostic close marker
  (nahrádza `Discuss-closed:` pre task-chatter). Rodina STÁLE nesie citáciu na
  VŠETKÝCH ticketoch; `Acceptance-defer:` pre ne-posledný. Batchovanie draftov
  rodiny: `modules/core/statusline-vocabulary.md` (#755/#606).

- **One thread = one topic — now the WHOLE lifecycle, not just addressing
  (airuleset #728, owner directive 2026-08-26).** Verbatim: „treba vlakna
  drzat maximalne atomicke a ak sa otvori nejaka nova tema vo vlakne tak
  radsej vytvorit nove vlakno/ticket a spravu ktora temu vyvolala
  prekopirovat, presunut do toho noveho vlakna". It now covers the thread's ENTIRE lifecycle: every follow-up, reminder and reply
  posted into an EXISTING thread must still belong to that thread's OWN topic,
  never a different one it merely happens to sit in. Incident: „Etapy zákaziek vo výrobe 1"
  (discuss.channel_257) grew to 36 messages across ~6 topics — owner had to close by hand.
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
  (triggering msg 1724252/1724253 copied across; thread 257 closed via msg 1743448).
- **Atomicity also applies at CREATION, not only to organic growth
  (airuleset #742).** #728 above covers a topic that emerges INSIDE an
  already-open thread; this closes the other half — when a brand-new proposal
  would cover MORE THAN ONE topic, split it into SEPARATE threads from the start
  — never bundle them into one opening message "to save a round of owner
  approval". One thread = one topic is the rule at every point in a thread's
  life, including message zero. Each split thread gets its own name, its own
  `Discuss-ticket:` / `Discuss-thread:` binding, and its own owner approval —
  never a shortcut around any of those.

- **Every client Discuss report → Odoo project.task IMMEDIATELY (#924,
  owner 7.9.).** Before reply/GitHub ticket: create the task in the client's
  project (origin: thread + deep URL + msg id, summary, dev ref). Stages +
  Hotovo authority per `client-board-tasks.md`; lag=violation. Discuss is NEVER
  the source of truth; the task is.

- **Štartovací Introduction v produkte — udržiavaný per-tenant návod (airuleset
  #1042/#1073, owner 18.9.2026: „návody buduj a udržiavaj").** Dodaj JEDEN
  štartovací „Introduction / Začíname" v produkte (Návody), z ktorého vie NOVÁ
  osoba bez kontextu rovno začať — nikdy externý dokument, nikdy školiace vlákno.
  FORMA: statické self-contained HTML v repo
  `docs/<tenant>/navody-<oblasť>.html` (screenshoty z PROD kópie tenanta), pod
  token cestou; Odoo `ir.attachment` NIE JE OK — CSP láme obrázky. Sekcia per
  zariadenie (kiosk + Fully Kiosk; vlastný PC/telefón cez OTP), kontakt AI
  pomocníka + IT. Findability proof nad Introduction. Akceptačné vlákno klientovi
  ODKAZUJE na Introduction (sekcia, deep-link), neopisuje kroky. Per-tenant fakt
  `navody_url:` v `.claude/streams/<stream>.md` (`<https://…>` / `NONE — #<ticket>`);
  kým návod nie je → v hand-offe `Návody: pripravujeme, #<ticket>`, NIKDY
  fabrikovaný odkaz (zmenu obrazovky sprevádza úprava návodu v tom istom PR, gate
  #1073). **`needs-acceptance` hand-off musí niesť ŽIVÝ deep-link na návod — inak
  nekompletná odovzdávka** — HOOK-ENFORCED (`stop-check-prose-violations.sh` →
  `gates/navody.py`, #1073: `curl -sI` 200); bypass (API-only):
  `# airuleset:intro-link-ok <dôvod>`.

Every thread this file governs follows the project's own channel placement
rule — for odoo-erp see `.claude/rules/odoo-task-sync.md` (task chatter for
client acceptance, Discuss IT-support sub-threads for free conversation).
Ask the owner ONE decision at a time, and re-ask a question whole and fresh
if you have to (`user-questions-slovak.md`).
