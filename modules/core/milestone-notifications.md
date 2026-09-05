### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill — the ONE per-ticket device card (the EXCEPTION below) is fired by the worker directly at merge
- `notification-mechanics` skill — the retired api-error/limit/burn alert-class suppression (#546), per-owner thread + `DISCORD_MIRROR` routing, and the per-ticket card's field-by-field composition moved there VERBATIM — load it before authoring/debugging the notify hooks or watchdog

<!-- History: .claude/rules-reference/milestone-notifications-history.md -->

**The device (Discord / phone) is notified like the mobile Claude app: a ping arrives ONLY when Claude genuinely ASKS the user something (`❓ NEEDS YOU`) or has FULLY completed the work (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress.**

#### The mechanism is AUTOMATIC — do NOT hand-fire per-phase pings

Three airuleset hooks implement this from the status marker, with no action from you: `notify-discord-pending.sh` (Stop) delivers a **question** to the phone IMMEDIATELY (one ping per DISTINCT question — a verbatim repeat while still unanswered is deduped, a REWORD edits the existing card instead of re-posting), `notify-discord.sh` (idle_prompt) delivers a pending **`✅`** only once you are genuinely idle/away, and `notify-discord-send.sh` is the single send path both use. Nothing is ever sent on `⏳`. Consequence for YOU: re-poked while still blocked on the SAME question → repeat the bare `❓ NEEDS YOU` line ONLY, VERBATIM; a `❓ ASKED` question is NEVER re-emitted later (footer `U N` carries it, hook exit 2, #740); a reworded repeat reads as a new question. Byte-level detail of all three scripts lives in the `notification-mechanics` skill.

So you do NOT call the discord `reply` tool or `PushNotification` to announce a merge, a deploy, a green CI, or a finished issue. Just write the honest status marker; the hook decides whether the device pings. The ONE thing you control is the marker content (below).

#### Device content = Slovak, phone-readable — ❓ carries the WHOLE question, ✅ one line

For `✅ DONE:` the hook forwards the text after the colon verbatim — **ONE short Slovak line** (`✅ DONE: nasadené v1.2.3, CI zelené`). For `❓ NEEDS YOU:` / `❓ ASKED:` it forwards the **FULL final question BLOCK** — everything from the `**Otázka — projekt …:**` head line to the marker line, up to ~1500 chars, blank lines inside welcome (structured paragraphs render readably in the terminal — 2026-07-18; without a head line only the marker's paragraph + a short context pull is forwarded; an oversize block keeps its head + the decision line). So write the question SELF-CONTAINED under its head line (`user-questions-slovak.md`): úvod 2–4 vety (ktorý projekt + ČO tá vec JE + čo sa deje), možnosti s dôsledkami a `(odporúčam)`, posledný riadok `❓ NEEDS YOU: <rozhodnutie>` — the whole block in **Slovak, no jargon**, understandable on a phone with zero terminal context. NEVER compress the question to one line "to keep the ping short" — the truncated, context-free codex-bridge ping ("…sklad zač", 2026-07-04) is the banned outcome. Keep the English keyword (`NEEDS YOU` / `ASKED` / `DONE` — the hooks key on it); everything after it is Slovak. Shape is hook-enforced (`stop-check-question-quality.sh`): briefing line `**Otázka — projekt …:**` povinná + ONE decision per ping.

#### ✅ per-ticket completion is a REAL done-state — the ARMED GOAL, not `⏳`, signals continuation (revised 2026-07-25)

A completed ticket/batch inside a `/goal` / `/autopilot` loop ends its own report with `✅ DONE: <plain outcome>` — a genuine FULL completion (the worker returned, verification is done, its run-card already fired), not a lie. The signal that the loop CONTINUES is the ARMED GOAL Claude Code shows in its footer (`◎ /goal`), never a `⏳ WORKING` tail tacked onto an otherwise-finished ticket. Reserve `⏳ WORKING` for work GENUINELY still in flight when the message ends (a background worker still running, a CI poll spinning, a held review-watch turn) — not a "loop will fire again" placeholder. This is also what lets the ticket-boundary `/compact` (recorded by the session's own `## ✅ Work Complete` self-callback + watchdog job 14, #400; worker-SubagentStop channel retired #610) fire once PER TICKET instead of once for the whole backlog — a completed ticket's durable state already lives in git/GitHub, so it is a safe compaction boundary every time.

**The device ping is guarded ONLY when a card was actually DELIVERED** — a per-ticket `✅ DONE` must NOT queue a SECOND idle Discord ping when the sanctioned per-ticket run-card (the EXCEPTION below) already gave phone visibility for that ticket, because a second ping per ticket is exactly the per-phase noise the user removed. But the condition is DELIVERY, never an armed goal. `notify-discord-pending.sh` still reads the SAME `◎ /goal` signal the watchdog's own goal jobs key on (never a second, invented detector), and then additionally requires a DELIVERED card marker for this repo newer than the previous `✅` boundary in this session: card delivered → suppress; **no card, a card that failed to send, or anything unprovable (no cwd, no `origin`) → the ping goes through**, exactly as it did before the guard existed.

(The `❓ ASKED` / `❓ NEEDS YOU` question ping is UNCHANGED and unaffected by this — a genuine question ALWAYS pings regardless of an armed goal; only the ROUTINE per-ticket `✅` is goal-guarded. Owner-scoped delivery (#710): david = Discord phone ping; zbynek/marek = footer `U N` + webterm (no phone ping). 24/7 no-night-cutoff (#791). Full policy: `message-status-marker.md`.)

#### API-error / limit / token-burn alerts are RETIRED from Discord (#546, 2026-08-18 owner directive)

**The api-error / limit / token-burn alert classes NO LONGER ping the device** (#546). Preserved: `❓`/`✅`/run-cards/bounce/gk-req and the one-shot `acctblock:` alarm. Full mechanics in the `notification-mechanics` skill.

#### Per-phase progress is NOT pinged

During long / autonomous runs (`/autopilot`, `/goal` loops, batch work), routine per-phase progress (merged, deployed, CI green) does NOT ping the device — the device stays quiet until a worker raises a real `❓` question, fires the per-ticket merge card (the EXCEPTION below), or the whole run ends `✅`. Do NOT hand-fire a per-phase ping.

#### Every device message goes to the owner's OWN thread AND @mentions them (zbynek / marek)

Each project runs in a tmux session grouped `zbynek` or `marek`. EVERY Discord message (the idle `❓`/`✅` ping AND the autopilot card below) is automatically POSTED to that owner's **own thread** (`claude-zbynek` / `claude-marek`, never shared) and prefixed with their `<@id>` — including session-personas PARALLEL-mirrored to a real human via `DISCORD_MIRROR_<OWNER>` (e.g. `david`'s pings also reach `zbynek`'s thread). **Exception (#296): a `❓` question ping routes to a separate per-owner questions thread, `claude-<owner>-q`** — `✅` and the autopilot card stay in the normal `claude-<owner>` thread (the api-error/limit/burn alert pings that used to share it are retired, #546). You do nothing; never strip the mention. Full thread + mirror + questions-thread routing lives in the `notification-mechanics` skill.

#### EXCEPTION — `/autopilot` per-ticket completion card (the ONE sanctioned per-ticket hand-fire)

The "no per-merge device ping" rule above has ONE explicit, user-requested exception: during an `/autopilot` run, **each ticket whose PR merges gets ONE structured Discord card** — the user wants per-ticket visibility on the phone during hands-off runs. This is NOT the banned per-merge noise: it is a single, deduped, structured message through the dedicated path (`airuleset.py notify --run-card`, fired by the WORKER after merge + post-deploy verification — the actual dispatch instructions live in `agents/autopilot-worker.md` / `skills/autopilot/SKILL.md`), NOT a hand-fired `reply`/`PushNotification`. The card's full field-by-field composition (🎫/🎯/✅/📦/🔗/📊) and dedup mechanics moved to the `notification-mechanics` skill.

**The card is ENFORCED, not advisory (#134).** Three mechanisms now hold it, and none of them is more prose: `notify --run-card` **exits non-zero when Discord never received the card**; a **SubagentStop gate** (`hooks/subagent-stop-check-run-card.sh`) blocks a worker once per issue if it claims a real `merge_sha` and `issue_state: #N=closed` with no delivered card; and **watchdog job 25** reconciles merged-but-unreported tickets independently, which is the only one of the three that still works when the worker DIED mid-run or the delivery failed after it returned. Every non-delivery also lands in `~/.claude/notify-delivery.log` with its reason (#135) — a failed card is never silent again.

#### Anti-patterns (all rewordings apply)

- Calling `reply` / `PushNotification` to announce a per-merge / per-CI / per-deploy milestone → **WRONG.** That is the per-phase noise the user removed. (The `/autopilot` per-ticket card is NOT this — the worker fires it via `airuleset.py notify --run-card`, deduped + structured, per the sanctioned EXCEPTION above; a raw `reply`/`PushNotification` per-merge is still banned.)
- A device ping for a `⏳ WORKING` turn → **WRONG.** Working ≠ asking ≠ done; the hook sends nothing on `⏳`.
- Writing the `❓` / `✅` content in English or as a long jargon-filled line → **WRONG.** Slovak, one short phone-readable sentence.
- `edit_message` instead of a new message when you DO reply in an active Discord conversation → edits don't ping (only relevant to live chat, not milestones).
