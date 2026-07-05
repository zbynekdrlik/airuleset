### Questions To The User — Slovak, Human, No Jargon

**Context gate — related rules you MUST also apply:**
- `ask-before-assuming.md` — WHEN to ask (this rule is HOW to phrase it)
- `message-status-marker.md` — the `❓ NEEDS YOU` line is already Slovak; this extends the SAME to the whole `AskUserQuestion` dialog
- `issue-reference-context.md` — a `#N` always carries its topic; here you ALSO explain it in plain words

**Every question you put to the user — FIRST AND FOREMOST the `AskUserQuestion` dialog shown IN the Claude session / terminal (the question text AND every option label AND every option description), and equally the `❓ NEEDS YOU` marker and any clarifying question written in prose — MUST be in SLOVAK and in the simplest, most human language possible.** This is about the question the user reads INSIDE Claude (the interactive dialog in the terminal), not only the Discord ping. The user keeps receiving English, jargon-dense questions there that they cannot easily understand. That is banned, no matter how technical the underlying topic is — YOU translate it.

#### The rule

- **Slovak — the whole thing.** Question + every option label + every option description. The ONLY English kept is the status keyword `NEEDS YOU` / `DONE` (the hooks key on it); everything the user READS to decide is Slovak.
- **Plain + human.** Explain in everyday words: (1) what is going on, (2) why you're asking / what is blocked, (3) what each choice means IN PRACTICE and its consequence (time, risk). A non-engineer must understand it on a phone with no terminal context.
- **Translate the jargon — do NOT paste it.** A raw issue number, gate name, infra term, class/exception name means nothing to the user. Say what it IS for the project. Keep a `#N` for reference (per `issue-reference-context.md`) but ALWAYS put the plain-language meaning right next to it.
- **Options = short Slovak label + plain consequence.** Each option's description says plainly what happens if chosen and the trade-off — not the technical mechanism. Lead with your recommendation and mark it `(odporúčam)`.

#### Self-contained — write EVERY question for someone with ZERO context (the #1 repeated complaint)

**The user is NOT at the terminal and has NOT read the history you printed while working.** They see ONLY the question — on a phone, cold, maybe days later, across many different projects. So the question must carry ALL the context needed to understand and decide FROM ITS OWN TEXT ALONE. Never assume the user knows what the ticket is, what you were doing, or how two things relate.

Every question OPENS with a 2–4 sentence plain-Slovak briefing, in this order:
1. **Which project + what it is** — one plain phrase: "V projekte camera-box (ovládanie kamier a OBS pre kostolný živý prenos)…". Never just "#137".
2. **What was happening / what led here** — the situation in everyday words: what you were doing, what happened, why a decision is now needed.
3. **Explain EVERY cross-reference** — any OTHER project, ticket, or component you name MUST be explained: what it IS *and* why it is relevant HERE. A bare "restreamer #255 už to opravil" is BANNED — the user has no idea camera-box and restreamer are connected, or that restreamer even touches OBS. Spell out the link in plain words.
4. **THEN the decision** + each option's real-world consequence (time, risk), recommendation marked `(odporúčam)`.

**The gate — apply to EVERY question before sending:** "Could a person who has NOT watched this terminal for a week, and does NOT know this project's internals, understand this question and decide — from the question text ALONE?" If no → it is BANNED; add the missing context and re-write. "It's all in the scrollback / I explained it earlier" is exactly the excuse this kills — the user does NOT read the scrollback and is not watching 24/7 (they say this over and over).

#### Deliver an away-user question as the `❓` TEXT marker — NOT a 60-second `AskUserQuestion` dialog

In an `/autopilot` / `/goal` / any autonomous run the user is AWAY. When a background subagent surfaces an `AskUserQuestion` to the main session, Claude Code **auto-continues after ~60 s** unanswered (observed live: `No response after 60s — continued without an answer`) — so an away user NEVER answers it in time and the loop wrongly proceeds as if resolved. That is a core cause of "it asked, I never got it, then it moved on / then it blamed me". **That 60 s is baked into the Claude Code binary — airuleset cannot safely raise it** (patching a 248 MB binary that is replaced on every CC update would rot instantly). The fix is not a bigger timeout — it is the RIGHT channel: the **`❓` text marker has NO timeout at all** (it pings the phone and waits UNLIMITED, however long you need — far better than any 30-minute dialog).

- **For a genuine question during an autonomous/away run, deliver it as the `❓ NEEDS YOU:` / `❓ ASKED:` TEXT marker** (Slovak, with the self-contained briefing above). That pings the phone AND waits indefinitely — it does not time out. The user replies in text whenever they see the ping.
- **The device ping carries the WHOLE final question block — write it there.** The delivery hook forwards the contiguous paragraph ending with the `❓` marker line (up to ~1500 chars; a bare marker after a blank line pulls in only the ONE paragraph directly above). So put the ENTIRE self-contained question in that block: briefing → možnosti s dôsledkami `(odporúčam)` → `❓ NEEDS YOU: <rozhodnutie>` as its last line, with NO blank lines inside the block. Do NOT park the briefing pages earlier in the turn and end with a bare one-line marker — only the final block reaches the phone; and keep the block under ~1500 chars (beyond that the middle gets elided; the decision line always survives). The live failure this fixes: a codex-bridge question arrived on the phone truncated mid-word ("…sklad zač") with the actual question missing (2026-07-04).
- **A timed-out `AskUserQuestion` is NOT an answer.** If you used the dialog and it auto-continued unanswered, do NOT treat that as resolved — re-deliver the SAME question (self-contained) as the `❓` text marker and wait / ask-and-continue (`message-status-marker.md`).
- `AskUserQuestion`'s structured dialog is fine only when the user is PRESENT (interactive design/brainstorm at the terminal). For an away user it is the wrong channel.

#### Povinná ŠTRUKTÚRA otázky — HOOK-ENFORCED template + ONE ping = ONE decision

Every `❓ NEEDS YOU` / `❓ ASKED` turn is HARD-GATED by `stop-check-question-quality.sh` — a non-conforming question is BLOCKED at Stop and must be rewritten. **The gate enforces only for an AWAY user** (no real prompt in the last ~10 min — presence marker `/tmp/claude-user-active-<sid>` from UserPromptSubmit): the template protects the cold phone read; when the user is PRESENT and typing, the question is a live conversation and hard-gating it just re-printed questions + hook errors into their chat (camera-box "Hruza", 2026-07-05). Still WRITE questions decently when present — the gate absence is not a style licence. Two rules, both from live 2026-07-05 failures:

1. **The question block MUST open with the briefing line** — this EXACT shape, contiguous (no blank lines inside), ending with the marker:

   ```
   **Otázka — projekt <meno> (<čo projekt robí>):** <čo sa deje a prečo sa pýtaš — 2–4 vety, po slovensky, bez žargónu>
   • <možnosť A> (odporúčam) — <dôsledok>
   • <možnosť B> — <dôsledok>
   ❓ NEEDS YOU: <jedno jasné rozhodnutie>
   ```

   The killed failure: *"Po zmazaní hneď overím voľné miesto…"* + a bare decision line — the phone reader has no idea WHAT is being deleted, in WHICH project, or WHY.

   **Úvod = 2–4 KRÁTKE vety, max ~600 znakov (hook-enforced).** WHAT project, WHAT happened, WHY you ask — nič viac. Technical detail (merania, architektúra, kód) patrí do ticketu/transkriptu, NIE do pingu — the camera-box wall (~700 chars of thread/lock jargon, 2026-07-05) is the banned outcome. **Odrážky s možnosťami sú POVINNÉ (hook-enforced)** — aj otvorená otázka ponúkne kandidátov + `• iné — napíš vlastnú odpoveď`. Delivery renders the structure for you (bold header + **NUMBERED** options `1.`/`2.` + spacing + bold decision + a small "odpovedz číslom" hint) — just write the template `•` block; never flatten it into prose. **A Discord reply may come back as a BARE NUMBER** ("1", "2") — map it to YOUR options in order (1 = first bullet); a reply "áno" to a two-option question was the ambiguity this kills.

2. **ONE ❓ ping = ONE decision.** Never `(1) …? (2) …? (3) …?` piles, never *"odpovedz na ktorékoľvek z 3, aj postupne"*. The Discord REPLY to a ping is typed back into the asking session as ONE prompt (watchdog job 7) — a multi-question ping is UNANSWERABLE (which of the 3 does the reply answer?). Multiple pending questions → ask the FIRST one now (its own structured block), track the rest on their tickets (`needs-answer`), and ask the NEXT one after the first answer arrives — small sequential questions are exactly what the user wants (the "Ask in SMALL parts" section below). `(1)/(2)` describing STEPS with a single final question is fine.

#### Anti-pattern #2 — assumed context + unexplained cross-project link (this exact question — BANNED)

> *Ticket #137: nasadenie novej OBS knižnice (obs.dll) reštartne OBS, čo predtým rozbíjalo stream. Skutočnú príčinu už vyriešil a živo nasadil susedný projekt restreamer (jeho #255, zmergované). Chýba len živé potvrdenie na rigu. Čo s #137?*

The user's real reaction: *"nerozumiem!!! akoze ty si zasiahol do projektu restreamer? restreamer projekt ma vlastne obs?!!! o co tu ide!!!"*. It assumes the user knows what #137 is, that camera-box and restreamer are related, and that restreamer touches OBS. Jargon: `obs.dll`, `rig`, "reštart prehryzne". **WRONG.**

#### Correct #2 (same question — self-contained, cross-link explained, plain)

> **Otázka — projekt camera-box (ovláda kamery a OBS pre kostolný živý prenos):** Chystáme aktualizáciu jednej súčasti OBS. Pri takej aktualizácii sa OBS musí reštartovať a kedysi to na pár sekúnd rozhodilo zvuk a obraz na výstupe. Medzitým sa ukázalo, že tú istú chybu (~25 s rozladenie pri reštarte) opravil náš DRUHÝ projekt — restreamer (ten berie hotový prenos z OBS a posiela ho ďalej na web); oprava je už nasadená a beží, takže reštart OBS by dnes mal prejsť bez rozhodenia. Ostáva jediné: overiť si to naživo priamo na kostolnom počítači. Ako s tým naložiť?
> • **Zavrieť ako vyriešené (odporúčam)** — príčinu naozaj opravil restreamer a beží; úlohu zavriem s odkazom naň. (rýchle)
> • **Najprv overiť naživo** — nechám úlohu otvorenú, počkám a pri najbližšom prenose reálne vyskúšam aktualizáciu OBS, až potom zavriem. (istejšie, čaká na živý prenos)

#### Anti-pattern — English + jargon (this exact question — BANNED)

> *FB-push E2E gate (#227) is fragile and now BLOCKING #258 … only the unrelated FB-push job fails … it runs on EVERY PR, so it blocks the whole cluster. How to proceed?*
> *1. Fix #227 now, then resume cluster — … widen VPS-registration timeout root-cause, scope/shorten FB soak, stabilize.*

English, and dense with `#227`/`#258`, "E2E gate", "VPS-registration timeout", "cluster", "FB soak" — the user cannot parse it. **WRONG.**

#### Correct (same question — Slovak + human)

> **Otázka:** Test, ktorý overuje odosielanie streamu na Facebook, je nestabilný — spadol už dvakrát, zakaždým z inej príčiny (problém s časovaním na strane servera, nie chyba v našom kóde). Beží pri každej zmene, takže teraz zastavuje celú dávku rozpracovaných úloh. Ako ďalej?
> • **Opraviť ten test teraz (odporúčam)** — najprv spravíme test spoľahlivým, potom všetko prejde hladko. (~2 h, odstráni blokádu natrvalo.)
> • **Skúsiť ešte raz** — keďže zakaždým padol inak, môže to byť len výkyv. (~2 h CI, nemusí pomôcť.)
> • **Najprv zistiť prečo** — preskúmať, prečo sa Facebook nestihne pripojiť. (môže odhaliť skutočnú chybu.)

#### Tickets in a question — explain EACH in plain words, NEVER a bare number or range

The user does NOT remember what a ticket number means and CANNOT decode a range at all. In any question, EVERY ticket you mention carries a SHORT, HUMAN Slovak explanation of what it is ABOUT — not just its number, not just its (often jargon) title.

- A bare `#258` / `#227` → **WRONG.** `#258 (kontrola obrazu+zvuku pred spustením)`.
- A RANGE like `#684–#740`, or "the 52-ticket rollout", or "tie skip'd tickety" → **WRONG, doubly so** — it names dozens of tickets the user cannot see. Either list the FEW that matter, each with a one-line plain meaning, OR describe the GROUP in plain words ("~50 starších úloh okolo prerábky prehrávača") — never a bare range expecting the user to know what is inside it.
- Copy the title from `gh issue view`, then TRANSLATE it to plain Slovak — the raw title is usually technical.

#### Ask in SMALL parts — one decision at a time, iterate (NEVER one universal answer to a pile)

The user wants to decide **part by part**, NOT give a single sweeping answer covering many different tickets at once. When a decision spans many tickets / topics:

- Break it into the SMALLEST useful pieces and ask about ONE at a time; let the user answer, then move to the next. Iterating over several short, clear questions is GOOD — the user PREFERS that to one dense mega-question.
- NEVER present a big heterogeneous batch ("tu je 52 ticketov / celý rollout — čo chceš?") expecting one universal answer — the tickets differ, so one answer can't fit them all.
- Smaller + clearer + sequential beats big + sweeping. Each piece explained in plain Slovak (above).
- **This governs QUESTIONS the user must answer — it does NOT change `autonomous-batch-issue-development.md`** (still bundle the WORK silently, no asking between issues). When you genuinely MUST ask, ask small and explain each piece.

The intent: the user understands every question instantly, in their language, without engineering knowledge, and decides in small clear steps — never a number they can't decode, never one sweeping answer to a pile. Applies to all rewordings and semantic equivalents — every question the user reads, in any project, via any tool.
