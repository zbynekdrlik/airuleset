---
name: user-questions-slovak
description: Full template and worked examples for every question put to the user — Slovak, self-contained, no jargon. Auto-loads on AskUserQuestion.
user-invocable: false
---

### User Questions — Slovak, Human, No Jargon: Full Template + Worked Examples

#95 item 9 (2026-08-09, middle path): the always-on core (`user-questions-slovak.md`)
keeps the Slovak requirement and the self-contained gate — the load-bearing
QUALITATIVE half of this rule, which nothing mechanically checks. This skill
carries the DETAILED, hook-enforced STRUCTURAL half plus every worked
example, moved here VERBATIM (nothing summarised, nothing dropped) — the
Stop-hook (`stop-check-question-quality.sh`) enforces the SHAPE regardless
of whether this content is in context, so moving it costs no enforcement.

#### Deliver an away-user question as the `❓` TEXT marker — NOT a 60-second `AskUserQuestion` dialog

In an `/autopilot` / `/goal` / any autonomous run the user is AWAY. When a background subagent surfaces an `AskUserQuestion` to the main session, Claude Code **auto-continues after ~60 s** unanswered (observed live: `No response after 60s — continued without an answer`) — so an away user NEVER answers it in time and the loop wrongly proceeds as if resolved. That is a core cause of "it asked, I never got it, then it moved on / then it blamed me". **That 60 s is baked into the Claude Code binary — airuleset cannot safely raise it** (patching a 248 MB binary that is replaced on every CC update would rot instantly). The fix is not a bigger timeout — it is the RIGHT channel: the **`❓` text marker has NO timeout at all** (it pings the phone and waits UNLIMITED, however long you need — far better than any 30-minute dialog).

- **For a genuine question during an autonomous/away run, deliver it as the `❓ NEEDS YOU:` / `❓ ASKED:` TEXT marker** (Slovak, with the self-contained briefing above). That pings the phone AND waits indefinitely — it does not time out. The user replies in text whenever they see the ping.
- **The device ping carries the WHOLE final question block — write it there, STRUCTURED.** The delivery hook anchors on the `**Otázka — projekt …:**` head line and forwards EVERYTHING from it to the `❓` marker line — blank lines inside the block are fine and WANTED (since 2026-07-18): write the briefing as its own paragraph, a blank line, one option per `•` line, a blank line, the `❓` line. That renders READABLY in the terminal (the odoo-erp #1173 complaint: a single-paragraph wall was "necitatelne, nema to uvod, hnus") AND reaches the phone whole. So put the ENTIRE self-contained question between the head and the marker: briefing → možnosti s dôsledkami `(odporúčam)` → `❓ NEEDS YOU: <rozhodnutie>` as the last line. Do NOT park the briefing pages earlier in the turn ABOVE the head line — only head..marker reaches the phone; keep the block under ~1500 chars (beyond that the middle gets elided; the decision line always survives). The live failures this fixes: a codex-bridge question truncated mid-word with the question missing (2026-07-04), and the #1173 wall (2026-07-18).
- **A timed-out `AskUserQuestion` is NOT an answer.** If you used the dialog and it auto-continued unanswered, do NOT treat that as resolved — re-deliver the SAME question (self-contained) as the `❓` text marker and wait / ask-and-continue (`message-status-marker.md`).
- `AskUserQuestion`'s structured dialog is fine only when the user is PRESENT (interactive design/brainstorm at the terminal). For an away user it is the wrong channel.

#### Povinná ŠTRUKTÚRA otázky — HOOK-ENFORCED template + ONE ping = ONE decision

Every `❓ NEEDS YOU` / `❓ ASKED` turn is HARD-GATED by `stop-check-question-quality.sh` — a non-conforming question is BLOCKED at Stop and must be rewritten. **The gate enforces only for an AWAY user** (no real prompt in the last ~10 min — presence marker `/tmp/claude-user-active-<sid>` from UserPromptSubmit): the template protects the cold phone read; when the user is PRESENT and typing, the question is a live conversation and hard-gating it just re-printed questions + hook errors into their chat (camera-box "Hruza", 2026-07-05). Still WRITE questions decently when present — the gate absence is not a style licence. Two rules, both from live 2026-07-05 failures:

1. **The question block MUST open with the briefing line** — this EXACT shape, STRUCTURED with blank lines (readable in the terminal; the delivery anchors on the `**Otázka` head, so blank lines inside are safe since 2026-07-18), ending with the marker:

   ```
   **Otázka — projekt <meno> (<čo projekt robí>):** <ČO je tá vec, o ktorej sa rozhoduje + čo sa deje a prečo sa pýtaš — 2–4 vety, po slovensky, bez žargónu>

   • <možnosť A> (odporúčam) — <dôsledok>
   • <možnosť B> — <dôsledok>

   ❓ NEEDS YOU: <jedno jasné rozhodnutie>
   ```

   The killed failure: *"Po zmazaní hneď overím voľné miesto…"* + a bare decision line — the phone reader has no idea WHAT is being deleted, in WHICH project, or WHY.

   **Úvod = 2–4 KRÁTKE vety, max ~600 znakov (hook-enforced) — a PRVÁ veta povie, ČO tá vec JE.** When the question is about a ticket/PR/object, the FIRST sentence states its NATURE in plain words ("Ticket #1173 je nástenkový — zoznam, ktorý sleduje…"), never only what the run achieved around it. The live failure (odoo-erp #1173, 2026-07-18): the briefing narrated seven merged PRs but never said the ticket itself is an umbrella tracking list — the user exploded ("zasa neuvedzas o com je ticket!!! ako ti mam odpovedat?"); the rewrite that opened with "je to nástenkový ticket = zoznam" got an instant answer. WHAT the thing is → WHAT happened → WHY you ask — nič viac. Technical detail (merania, architektúra, kód) patrí do ticketu/transkriptu, NIE do pingu — the camera-box wall (~700 chars of thread/lock jargon, 2026-07-05) is the banned outcome. **Odrážky s možnosťami sú POVINNÉ (hook-enforced)** — aj otvorená otázka ponúkne kandidátov + `• iné — napíš vlastnú odpoveď`. Delivery renders the structure for you (bold header + **NUMBERED** options `1.`/`2.` + spacing + bold decision + a small "odpovedz číslom" hint) — just write the template `•` block; never flatten it into prose. **A Discord reply may come back as a BARE NUMBER** ("1", "2") — map it to YOUR options in order (1 = first bullet); a reply "áno" to a two-option question was the ambiguity this kills.

2. **ONE ❓ ping = ONE decision.** Never `(1) …? (2) …? (3) …?` piles, never *"odpovedz na ktorékoľvek z 3, aj postupne"*. The Discord REPLY to a ping is typed back into the asking session as ONE prompt (watchdog job 7) — a multi-question ping is UNANSWERABLE (which of the 3 does the reply answer?). Multiple pending questions → ask the FIRST one now (its own structured block), track the rest on their tickets (`needs-answer`), and ask the NEXT one after the first answer arrives — small sequential questions are exactly what the user wants (the "Ask in SMALL parts" section below). `(1)/(2)` describing STEPS with a single final question is fine.

#### Nezodpovedaná otázka po inej konverzácii sa kladie NANOVO a CELÁ — zákaz odvolávok do histórie

**The user does not search your scrollback for what an old question was about — this is the same self-contained principle above, applied to a question that was ALREADY asked once and is still open.** Repeated complaint (naposledy 2026-07-25): the model refers back to an earlier question instead of restating it — *"jediné otvorené rozhodnutie je ultracode (pýtal som sa skôr)"* — and expects the user to remember or dig up what that was. The user's own words: *"dokolečka ťa žiadam, aby si sa neodvolával na nejaké staré otázky niekde v histórii… vždy sa musíš nanovo spýtať celú otázku s úvodom a nie chcieť odo mňa, aby som v tvojej mega dlhej histórii hľadal, o čo ti vlastne ide."* An allusion reaches the phone with no briefing, no options, and often no ping at all — it is the equivalent of never having asked.

**Two branches — do not confuse them (`message-status-marker.md` documents both):**

- **Re-poke with NO user input since the last ask** (a `/goal` evaluator or task-notification re-fires the same blocked turn, nothing changed) → write ONLY the bare `❓ NEEDS YOU: <text>` marker line, **VERBATIM, byte-identical** — never the full briefing/options block (#740). This bare line is the ONLY re-emission allowed; an **ask-and-continue** `❓ ASKED` question is emitted ONCE too — LATER turns NEVER re-emit the `❓ ASKED` line or the block while it is unanswered and no user message arrived (footer `U N` + `needs-answer` carry it). The device-ping dedup AND the quality gate's bypass key on that bare line alone, so ONLY the bare `❓ NEEDS YOU:` re-poke passes; every OTHER repeat (a `❓ ASKED` line, the full block, or the marker alongside `⏳`/`✅`) is now hook-blocked (`stop-check-question-quality.sh` exit 2 — recidíva miva1 2026-09-03, 27×/8h). See `message-status-marker.md`.
- **ANY conversation happened in between** (the user answered something else, a different ticket got worked, a turn with real user input passed) → the question is **NEW** and is asked **NANOVO A CELÁ**: the full `**Otázka — projekt …:**` block again — briefing, options, `❓` line — exactly as if asking for the first time. It is a genuinely new ask and MUST ping again.

**Banned formulations (and every rewording) — a reference INSTEAD OF the full block:** "pýtal som sa skôr", "ako som spomínal vyššie", "ako som už písal/uviedol", "tá otázka o X stále platí", "jediné otvorené rozhodnutie je X (pýtal som sa)", "vráť sa k mojej otázke o…", "stále čakám na odpoveď ohľadom X" — used in place of restating the question. Adding a `**Otázka — projekt …:**` head line to one of these does NOT satisfy the rule if the briefing itself is still just a pointer to history instead of the real content; write it fresh, as if the user has genuinely never seen it (they may not have — they don't watch the terminal). Hook-enforced: `stop-check-question-quality.sh` scans the delivered block for these phrases (Check 5) whenever it is not a byte-identical verbatim repeat.

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
