### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill — the ONE per-ticket device card (the EXCEPTION below) is fired by the worker directly at merge
- `notification-mechanics` skill — the API-error watchdog, per-owner thread + `DISCORD_MIRROR` routing, and the per-ticket card's field-by-field composition moved there VERBATIM — load it before authoring/debugging the notify hooks or watchdog

**The device (Discord / phone) is notified like the mobile Claude app: a ping arrives ONLY when Claude genuinely ASKS the user something (`❓ NEEDS YOU`) or has FULLY completed the work (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress.** This replaces the old "ping every phase" stance — the user found per-merge / per-CI pings to be noise.

#### The mechanism is AUTOMATIC — do NOT hand-fire per-phase pings

Three airuleset hooks implement this from the status marker, with no action from you:
1. `notify-discord-pending.sh` (Stop) scans the message for a question or terminal marker. On **`❓ NEEDS YOU`** (last line, blocked) OR **`❓ ASKED:`** (a body line raised while the turn continues other work and ends `⏳ WORKING`) it **SENDS the device ping IMMEDIATELY** (via the shared `notify-discord-send.sh`) — a genuine question must reach the phone even over tmux/SSH, where Claude Code's `idle_prompt` event is unreliable (depending on it is exactly why pings "stopped" arriving). **A question is NEVER suppressed** — the old "❓ + continuing language → swallow the ping" behavior was a bug (the user asked, no ping came, then got reproached hours later); it is removed. **But one ping per DISTINCT question:** a repeat with IDENTICAL content and NO user prompt in between (a `/goal`-loop re-poke of a session still blocked on the same unanswered question) is DEDUPED — the first ask always pinged and the phone already has it; re-pinging every re-poked turn was the 9× "rovnaká otázka ako predtým" spam (restreamer, 2026-07-04). A real user prompt clears the dedup (`clear-question-dedup.sh`, UserPromptSubmit), so a fresh ask pings again even with identical text. Consequence for YOU: re-poked while still blocked on the SAME question → repeat the `❓` line VERBATIM (byte-identical); a reworded repeat reads as a new question and re-pings the phone. On `✅ DONE` it records a pending line; on `⏳` with no `❓ ASKED` line / no-marker it clears any pending. **Anti-spam (2026-07-05):** a Stop attempt a blocking gate REJECTED never pings (the hook skips when a fresh `/tmp/airuleset-*-block-<sid>` exists — a draft being rewritten is not a delivered question), and a REWORD of a still-unanswered question EDITS the existing Discord message in place via `notify --edit-question` (edits don't push-ping; the phone got its push on the first ask) — a fresh POST fires only for a genuinely new ask after the user typed. This killed the 3-pings-in-3-minutes-for-one-question camera-box spam.
2. `notify-discord.sh` (Notification : idle_prompt) sends the pending `✅` line ONLY when the user is genuinely idle/away — a finished turn is less urgent than a question, and pinging every completed turn while the user watches the terminal is spam. NOTHING on `⏳`.
3. `notify-discord-send.sh` is the single send path both call (compose structured line + @mention owner + POST) — one place for the curl, no duplication.

So: a **question (`❓`) reaches the phone right away**; a **done (`✅`) reaches it when you're away**. Neither needs action from you beyond writing the honest marker.

So you do NOT call the discord `reply` tool or `PushNotification` to announce a merge, a deploy, a green CI, or a finished issue. Just write the honest status marker; the hook decides whether the device pings. The ONE thing you control is the marker content (below).

#### Device content = Slovak, phone-readable — ❓ carries the WHOLE question, ✅ one line

For `✅ DONE:` the hook forwards the text after the colon verbatim — **ONE short Slovak line** (`✅ DONE: nasadené v1.2.3, CI zelené`). For `❓ NEEDS YOU:` / `❓ ASKED:` it forwards the **FULL final question BLOCK** — everything from the `**Otázka — projekt …:**` head line to the marker line, up to ~1500 chars, blank lines inside welcome (structured paragraphs render readably in the terminal — 2026-07-18; without a head line only the marker's paragraph + a short context pull is forwarded; an oversize block keeps its head + the decision line). So write the question SELF-CONTAINED under its head line (`user-questions-slovak.md`): úvod 2–4 vety (ktorý projekt + ČO tá vec JE + čo sa deje), možnosti s dôsledkami a `(odporúčam)`, posledný riadok `❓ NEEDS YOU: <rozhodnutie>` — the whole block in **Slovak, no jargon**, understandable on a phone with zero terminal context. NEVER compress the question to one line "to keep the ping short" — the truncated, context-free codex-bridge ping ("…sklad zač", 2026-07-04) is the banned outcome. Keep the English keyword (`NEEDS YOU` / `ASKED` / `DONE` — the hooks key on it); everything after it is Slovak. Shape is hook-enforced (`stop-check-question-quality.sh`): briefing line `**Otázka — projekt …:**` povinná + ONE decision per ping.

#### ✅ per-ticket completion is a REAL done-state — the ARMED GOAL, not `⏳`, signals continuation (revised 2026-07-25)

A completed ticket/batch inside a `/goal` / `/autopilot` loop ends its own report with `✅ DONE: <plain outcome>` — a genuine FULL completion (the worker returned, verification is done, its run-card already fired), not a lie. The signal that the loop CONTINUES is the ARMED GOAL Claude Code shows in its footer (`◎ /goal`), never a `⏳ WORKING` tail tacked onto an otherwise-finished ticket. Reserve `⏳ WORKING` for work GENUINELY still in flight when the message ends (a background worker still running, a CI poll spinning, a held review-watch turn) — not a "loop will fire again" placeholder. This is also what lets the ticket-boundary `/compact` (`notify-compact-request.sh` + watchdog job 14) fire once PER TICKET instead of once for the whole backlog — a completed ticket's durable state already lives in git/GitHub, so it is a safe compaction boundary every time.

**The device ping is still guarded while the goal stays armed** — a per-ticket `✅ DONE` inside an ARMED `/goal` loop must NOT queue a SECOND idle Discord ping: the sanctioned per-ticket run-card (the EXCEPTION below) already gives phone visibility for that ticket, and a second ping per ticket is exactly the per-phase noise the user removed. `notify-discord-pending.sh` checks the SAME `◎ /goal` signal the watchdog's own goal jobs key on (never a second, invented detector) before queuing a `✅` pending line: goal ARMED → suppress (the run-card covers it); goal NOT armed (a normal non-loop session, or the true end of a run once `/goal` has resolved) → queue the ping as before.

(The `❓ ASKED` / `❓ NEEDS YOU` question ping is UNCHANGED and unaffected by this — a genuine question ALWAYS pings regardless of an armed goal; only the ROUTINE per-ticket `✅` is goal-guarded. Sleep window (00:00–06:00, hours `00..05`, Europe/Bratislava): a question is deferred and nothing pings, but ONLY while other answer-independent work exists; a NECESSARY question is asked as `❓ NEEDS YOU` even at night and DOES ping. Full ask-the-moment policy: `message-status-marker.md` + the autopilot skill.)

#### API-error watchdog — the device pings when a turn ends on a real API error (you do nothing)

A third sanctioned device ping, fully AUTOMATIC (hook-driven, `notify-api-error.sh` — you do nothing, just never strip the hook): a Stop hook fires ONE Discord ping `@owner` when a turn ENDS on a genuine Claude Code API error (rate-limit, overload, socket-closed, usage-limit), deduped per distinct error per session. Full mechanics moved to the `notification-mechanics` skill.

#### Per-phase progress is NOT pinged

During long / autonomous runs (`/autopilot`, `/goal` loops, batch work), routine per-phase progress (merged, deployed, CI green) does NOT ping the device — the device stays quiet until a worker raises a real `❓` question, fires the per-ticket merge card (the EXCEPTION below), or the whole run ends `✅`. Do NOT hand-fire a per-phase ping.

#### Every device message goes to the owner's OWN thread AND @mentions them (zbynek / marek)

Each project runs in a tmux session grouped `zbynek` or `marek`. EVERY Discord message (the idle `❓`/`✅` ping AND the autopilot card below) is automatically POSTED to that owner's **own thread** (`claude-zbynek` / `claude-marek`, never a shared thread) and prefixed with that owner's `<@id>` — including automated session-personas that get PARALLEL-mirrored to a real human via `DISCORD_MIRROR_<OWNER>` (e.g. `david`'s pings also reach `zbynek`'s thread). You do nothing; never strip the mention. Full per-owner thread + mirror routing mechanics moved to the `notification-mechanics` skill.

#### EXCEPTION — `/autopilot` per-ticket completion card (the ONE sanctioned per-ticket hand-fire)

The "no per-merge device ping" rule above has ONE explicit, user-requested exception: during an `/autopilot` run, **each ticket whose PR merges gets ONE structured Discord card** — the user wants per-ticket visibility on the phone during hands-off runs. This is NOT the banned per-merge noise: it is a single, deduped, structured message through the dedicated path (`airuleset.py notify --run-card`, fired by the WORKER after merge + post-deploy verification — the actual dispatch instructions live in `agents/autopilot-worker.md` / `skills/autopilot/SKILL.md`), NOT a hand-fired `reply`/`PushNotification`. The card's full field-by-field composition (🎫/🎯/✅/📦/🔗/📊) and dedup mechanics moved to the `notification-mechanics` skill.

#### Anti-patterns (all rewordings apply)

- Calling `reply` / `PushNotification` to announce a per-merge / per-CI / per-deploy milestone → **WRONG.** That is the per-phase noise the user removed. (The `/autopilot` per-ticket card is NOT this — the worker fires it via `airuleset.py notify --run-card`, deduped + structured, per the sanctioned EXCEPTION above; a raw `reply`/`PushNotification` per-merge is still banned.)
- A device ping for a `⏳ WORKING` turn → **WRONG.** Working ≠ asking ≠ done; the hook sends nothing on `⏳`.
- Writing the `❓` / `✅` content in English or as a long jargon-filled line → **WRONG.** Slovak, one short phone-readable sentence.
- `edit_message` instead of a new message when you DO reply in an active Discord conversation → edits don't ping (only relevant to live chat, not milestones).
