### message-status-marker.md — History & Incident Narratives

Moved VERBATIM from `modules/core/message-status-marker.md` during context diet (#859 batch 2).
The enforcement rules and banned phrases stay in the always-on module.

---

#### Compact boundary mechanics backstory (#400, #610)

The ticket-boundary `/compact` is recorded by the session's own `## ✅ Work Complete` → `self-callback` request, delivered by watchdog job 14 — the passive text-sniffing trigger was retired to a permanent no-op #400, and the worker-SubagentStop record channel was retired #610 as a worker return is not the supervisor's boundary under the fleet model.

#### #740 re-emission incident (2026-08-30, RECIDÍVA 2026-09-03)

#740, 2026-08-30, RECIDÍVA 2026-09-03 — first the owner saw 8 identical FULL blocks in one night, then a recurrence where miva1 re-emitted ONE question 27× in 8 h; the phone pinged only once each time (delivery-side dedup held), but every re-emission still burned a full turn re-printing text the owner already had and cluttered the chat — pure noise for zero new information. The block was always durable in Discord + on the ticket (`needs-answer`/`needs-decision`) + visible via the footer `U N`; owner ruling 2026-09-03: „točenie otázok dokolečka už nie je potrebné keďže mám v pätičke U".

#### Restreamer 9× spam incident (2026-07-04)

A REWORDED repeat still reads as a new question and re-pings — that was the 9× "rovnaká otázka ako predtým" spam (restreamer, 2026-07-04).

#### Delivery-side safety nets

Delivery-side safety nets: a reworded still-unanswered question EDITS the existing Discord card instead of re-posting, and a draft a Stop gate rejected never pings — but the VERBATIM discipline stays your job; the nets bound the damage, they don't excuse rewording.

#### #791 owner directive (2026-09-01) — 24/7 no night/day difference

The owner's rule verbatim: "Nech nie je rozdiel medzi nocou a dnom. Claude ma robit 24/7."

#### Idle-park definition

NEVER idle-park a blocked session (repeated `⏳ WORKING: parked` turns with no work done and no question asked) — under an armed `/goal` that spins re-poked turns into the block cap and floods the chat; blocked = ASK, never park.

#### Codex-bridge truncated question incident (2026-07-04)

The truncated, context-free codex-bridge ping ("…sklad zač", 2026-07-04) was the reported failure ("nemá úvod, je urezaná").

#### Blank-lines-in-questions adoption (2026-07-18)

Blank lines inside question blocks are fine and WANTED since 2026-07-18 — briefing paragraph, blank line, options, blank line, marker renders readably in the terminal instead of a wall.
