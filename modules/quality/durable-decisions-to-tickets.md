### Durable Decisions — Every Finding & Decision Lands on a GitHub Ticket THE MOMENT It Is Made

**Context gate — related rules you MUST also apply:**
- `no-dropped-work.md` — identified WORK is done now or filed as `#N`; this rule extends the same discipline to DECISIONS and FINDINGS, and moves the deadline from "before you stop" to "the moment it lands"
- `verify-issue-still-valid.md` — a ticket you update is re-validated against current reality first
- `issue-reference-context.md` — every `#N` you write for the user carries its title
- `main-context-hygiene.md` — the context window is a scarce, VOLATILE buffer; tickets are the durable store

**The context window is DISPOSABLE. Compaction fires MID-SESSION, without warning, exactly when the conversation has grown rich — and everything that lived only in the conversation (the root cause you found, the approach the user approved, the "spravíme to takto" agreement) is gone.** The user's recurring loss: they converge on findings and decisions with Claude, nothing is written to GitHub, the context compacts, and the session "pozabuda všetko na čo sme prišli a čo sa rozhodol urobiť". The ONLY stores that survive are git, GitHub issues/PRs, and files on disk. So:

#### The rule — persist IN THE SAME TURN, not at the end

1. **A DECISION lands → write it to its ticket NOW.** The moment the user picks an option, approves an approach, or you settle a design fork ("robíme A, nie B"), append it to the relevant open issue in the SAME turn: `gh issue comment <N> --body "ROZHODNUTÉ: <decision + why>"` (or update the issue body for a scope change). No relevant issue exists → `gh issue create` one to carry it. THEN continue working.
2. **A FINDING lands → same.** A root cause identified, a constraint discovered ("API nevracia X"), a measurement, a dead end ruled out — comment it onto the ticket the investigation belongs to, as you go. A debugging session's conclusions must be readable from the ticket alone.
3. **A converged plan → tickets FIRST, implementation SECOND.** When a brainstorm/design conversation converges on multi-step work, decompose it into filed issues (or update existing ones) BEFORE the first line of implementation — `no-dropped-work.md` "prepared ≠ filed" applies to the plan's steps, and each issue body must carry enough of the agreement (decision, constraints, acceptance) that a FRESH session could work it with zero conversation context.
4. **Prefer `/autopilot` for executing a converged multi-ticket plan.** Ticket-by-ticket execution reads durable state per issue — a tangled or compacted session loses nothing, because the next worker starts from the ticket, not from the conversation. After filing the backlog, tell the user the plan is fully on tickets and suggest running `/autopilot` (only they can type it); in an already-autonomous run, dispatch the work per-ticket yourself. A long multi-step plan carried ONLY in-context is the anti-pattern this kills.

#### The self-test (apply continuously, not at stop)

> **"Keby sa context skompaktoval TERAZ — stratí sa niečo, na čom sme sa dohodli alebo na čo som prišiel?"**

If yes, you are already late: write it to the ticket(s) in THIS turn, before any other work. Run this test after every user answer that settles something and after every substantive discovery — not once at the end. (The Stop-hook net in `no-dropped-work.md` catches dropped-work PHRASES; nothing mechanical can catch an unsaved decision — this discipline is the only guard.)

#### Anti-patterns (intent — all rewordings and semantic equivalents)

- "Spravím to podľa toho, čo sme si povedali" / "as we discussed" / "per our agreement" — with the agreement existing ONLY in the conversation → **WRONG.** Write it to the ticket, then reference the ticket.
- Implementing a multi-step agreed plan with no ticket trail ("I'll file the issues once it works") → **WRONG.** Tickets first.
- Holding findings for the completion report ("zhrniem to na konci") → **WRONG.** The report may never come — compaction, a crash, a session end all eat it. Persist as you go.
- Answering the user's design question, getting their pick, and moving straight to code without `gh issue comment` → **WRONG.** The pick is a decision; it lands on the ticket first.
- Treating auto-memory as the store for project decisions → **WRONG.** Memory is for preferences and cross-session agent context; PROJECT decisions belong on the project's tickets where the user and every future session can read them.

The intent: nothing the conversation establishes is ever lost to compaction — every decision and finding is on a ticket within the turn it was made, and converged plans execute ticket-by-ticket. Applies to all rewordings and semantic equivalents.
