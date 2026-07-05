### Message Status Marker — Every Message Ends With ❓ / ⏳ / ✅

**Context gate — related rules you MUST also apply:**
- `completion-report.md` — the ✅ Work Complete report IS a done-state; its ❓ Question line is the question marker
- `milestone-notifications.md` — the device (Discord/phone) is pinged ONLY on `❓` / `✅` (mobile-app model); the marker IS what fires it — a `✅` is one short Slovak line, a `❓` carries the WHOLE self-contained Slovak question block
- `autonomous-verification.md` — ⏳ WORKING means YOU keep working, never "user, go check it"
- `verify-launched-work-liveness.md` — a `⏳ WORKING` turn must have a bounded re-check ARMED (a dead background job sends no "done"); `⏳` is a promise to re-verify on a cadence, not a blind indefinite wait

**The user must NEVER have to guess whether you are asking them something, working in the background, or done. End EVERY message with exactly ONE status marker, on its own line, as the VERY LAST line:**

- `❓ NEEDS YOU: <the question / decision / approval, 1-2 sentences>` — you cannot proceed without the user; you STOP and wait. This marker means "your turn, I'm blocked". (For a question you raise while STILL doing other answer-independent work, use the `❓ ASKED:` + `⏳ WORKING` ask-and-continue combo instead — see the Rules — but a genuine question ALWAYS pings the phone either way.)
- `⏳ WORKING: <what is running> — nothing needed from you, I'll report when it's done` — a background task (background Bash, CI monitor, long build, a loop you'll continue, **OR a dispatched async subagent / Agent / workflow that will re-invoke you when it finishes**) is running and you will keep going autonomously.
- `✅ DONE: <one-line outcome>` — the turn's work is finished, nothing is running, you are idle and awaiting the next instruction.

#### Rules

- Exactly ONE terminal marker, on its own line, as the very last line of the message (terminal scrolls — the last line is what the user sees). ONE exception: an `❓ ASKED: <q>` question-ping line may ALSO appear in the body when you ask-and-continue — the terminal marker is still the single `⏳ WORKING` last line; the `❓ ASKED` line is the phone-ping for a question you raised while continuing other work (Rules below). That is NOT "two markers".
- A background task is running → ⏳ WORKING, **NEVER** ✅ DONE. ✅ means nothing is running. Claiming "done" while something runs is the exact mislead this rule kills. **This includes a dispatched async subagent / Agent / workflow / background job that will RE-INVOKE you when it finishes** — if anything will wake this session back up other than the user, you are NOT idle-done → `⏳ WORKING`. (A session that says `✅ DONE` while a background task is still in flight, then "continues on its own" when that task re-fires, confuses the user into thinking something injected input — see `verify-launched-work-liveness.md`.)
- The only thing left is the user's decision (merge, approve, pick an option) → ❓ NEEDS YOU, not a vague "standing by".
- **A genuine question to the user ALWAYS pings the phone — non-negotiable, and the ONE thing that must never fail.** The user does NOT sit at the terminal 24/7 reading everything you print; the ping is the ONLY way they learn a question exists. A question that was "printed on the fly" but never pinged does NOT count as asked — and you may NEVER, hours later, reproach the user for "not answering" it. Banned as a reason to stop / as blame: "stopping — the other tickets are waiting on your answers", "since you haven't replied", "čakajú na tvoje odpovede / rozhodnutie", "nemám ako pokračovať, lebo si neodpovedal". If it did not ping, that is YOUR bug, not the user's silence. **In a `/goal` / autopilot loop a genuine per-ticket question is ASKED THE MOMENT the ticket needs it — never buried under other tickets** (the user WANTS the per-ticket questions; answering them is their job). You then pick ONE of two honest forms, and **BOTH ping immediately**:
  - **`❓ NEEDS YOU: <q>` (the LAST line) — you are BLOCKED.** There is no other useful work you can do without this answer, so you STOP and wait. The loop pauses on this ticket and resumes the instant the user answers. Use this when the question blocks everything and nothing else is workable.
  - **`❓ ASKED: <q>` (a body line) + `⏳ WORKING: <what you continue>` (the LAST line) — ask-and-continue.** You raised the question (it PINGS the phone NOW), you tracked it DURABLY on its ticket (label `needs-answer` + a comment carrying the question, so it is never lost N screens back in the transcript), and you keep doing OTHER answer-independent work meanwhile. When the user answers (async, any time), you resume THAT ticket from its durable state. Give the user a real chance (~10 min) before you bulldoze a ticket that hinges on their taste — but do NOT sit idle-blocked when independent work exists. This is the model the user asked for: ask + notify + keep moving on what doesn't need the answer.
  - **BANNED — the buried question:** continuing PAST a question WITHOUT having asked+pinged+tracked it. Continuing is allowed ONLY after the ping fires (`❓ ASKED`). And NEVER write `❓ NEEDS YOU` (which means "I stopped, I'm waiting") while actually moving on to another ticket — if you continue, it is `❓ ASKED` + `⏳ WORKING`, never `❓ NEEDS YOU` + continuing language.
  - **Re-poked while STILL blocked on the SAME unanswered question** (a `/goal` evaluator or task-notification re-fires the turn, nothing changed): repeat the previous `❓` line **VERBATIM — byte-identical.** The ping hook dedups an identical question per session (no user input in between), so a verbatim repeat does NOT re-spam the phone; a REWORDED repeat reads as a new question and re-pings — that was the 9× "rovnaká otázka ako predtým" spam (restreamer, 2026-07-04). The first ask always pings; repeats stay silent until the user speaks.
  - **The ONE exception — the sleep window 00:00–05:59 Europe/Bratislava** (the user is asleep; `TZ=Europe/Bratislava date +%H` → hour `00..05`): during it ONLY, do NOT ping. DEFER — queue the question (label `needs-decision`, leave the ticket open), keep working, end `⏳ WORKING` with NO `❓ ASKED` line (so nothing pings), and raise the queued questions as a real `❓ NEEDS YOU` / `❓ ASKED` ping once the window ends (after 06:00) or the user is next active.
- "Standing by", "waiting on", "let me know", "your go", "should I", "no merge without your go" are AMBIGUOUS alone — they MUST carry a ❓ (if it's a question for the user) or ⏳ (if you're waiting on a background result, not on the user).
- A completion report's `## ✅ Work Complete` heading counts as the ✅ DONE marker — and in a manual-marker (`airuleset:merge=manual`) project, where the report waits on the user's merge, end it with the structured question block closing `❓ NEEDS YOU: schváliš merge PR #N?` instead (`user-questions-slovak.md` template). Default-auto projects merge first and report ✅ DONE (`pr-merge-policy.md`).
- **The `❓` / `✅` content is forwarded to the device** (Discord/phone) — it is the ONLY thing that pings them (`milestone-notifications.md`, mobile-app model). **✅ DONE = ONE short Slovak line** (the outcome). **❓ = the FULL self-contained question, NOT one line**: the delivery forwards the whole final question BLOCK — the contiguous paragraph ending with the marker line (up to ~1500 chars) — so write the briefing + options THERE, per `user-questions-slovak.md`: 2–4 vety úvodu (ktorý projekt + čo sa deje), možnosti s dôsledkami a `(odporúčam)`, posledný riadok `❓ NEEDS YOU: <rozhodnutie>`. Keep the block CONTIGUOUS (no blank lines inside it; a bare marker after a blank line pulls in only the ONE paragraph directly above). NEVER shrink a question to "fit one line" — a context-free/truncated question on the phone is the exact reported failure ("nemá úvod, je urezaná" — codex-bridge, 2026-07-04). The shape is HOOK-ENFORCED (`stop-check-question-quality.sh`): the block must open with `**Otázka — projekt …:**` and carry exactly ONE decision per ping (no `(1)/(2)/(3)` piles — the Discord reply routes back as ONE prompt). Keep the English keyword (`NEEDS YOU` / `DONE`); everything the user reads is Slovak. `⏳ WORKING` is NEVER forwarded (working ≠ asking ≠ done).
- **The `❓` marker IS the channel for a question the user must answer — it pings the phone and waits UNLIMITED. NEVER rely on a bare `AskUserQuestion` dialog for an away user** (from a background / autonomous run that dialog auto-continues after ~60 s — baked into Claude Code, unchangeable by airuleset — so the away user never answers and the loop wrongly proceeds). The `❓` marker has no timeout. And the question must be **SELF-CONTAINED**: open with a plain briefing (which project + what it does, what happened, EVERY cross-project / cross-ticket link explained) so someone with ZERO terminal context can understand and decide from the text alone (`user-questions-slovak.md`). The user does NOT read the scrollback.

#### Anti-pattern (this exact message misled the user)

> "Standing by for the mutation result, then the final green-PR report. No merge without your go."

Done? Working? Asking? Unmarked → the user cannot tell. **WRONG.**

Correct, when a background run is in progress:

> ⏳ WORKING: mutation run in progress — I'll report the green-PR result when it lands. Nothing needed from you.

Correct, default-auto project after the gates went green (merge is YOURS to do, not a question):

> ✅ DONE: PR #5 merged to main (a1b2c3d), v1.2.3 deployed and verified on the dashboard.

Correct, manual-marker project, truly idle and the only open item is the merge:

> **Otázka — projekt iem (mixovanie zvuku v kostole):** PR #5 (reset EQ) je celé zelené — lint, testy, build, coverage, security aj mutation prešli. Projekt má manuálny merge marker, takže čakám na tvoje schválenie.
> ❓ NEEDS YOU: schváliš merge PR #5?

#### Banned (the Stop hook `stop-check-status-marker.sh` blocks these)

- Ending a message with background / "in progress" / "still running" / "monitoring" / "will report" language but NO ⏳ marker.
- Asking the user anything (a question, "should I", "your go", "merge it?", a trailing `?`) with NO ❓ marker.
- Claiming progress / completion (done, fixed, pushed, deployed, merged) with NO marker at all.
- **Asking a question in a way that does NOT ping the phone**, or writing `❓ NEEDS YOU` + "continuing / next ticket / keep going" language (that is the buried-question / false-block form — use `❓ ASKED` + `⏳ WORKING` to ask-and-continue, which DOES ping).
- **Reproaching the user for an unanswered question** — stopping / reporting done and blaming their silence ("tickets are waiting on your answers", "you didn't reply", "čakajú na tvoje odpovede") for why you stopped. Every question you raised was pinged when raised; an unanswered pinged question just waits (tracked on its ticket) while you do other work — it is never the user's fault and never a reason to guilt them.

The intent is banned: leaving the user to guess your state, OR asking them something they never got pinged about and then blaming them. Applies to all messages and all rewordings.
