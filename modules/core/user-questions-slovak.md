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

#### Anti-pattern (this exact question — BANNED)

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
