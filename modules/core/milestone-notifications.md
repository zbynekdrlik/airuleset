### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill + `project-autopilot-board` — per-phase progress lives on the BOARD, not the device

**The device (Discord / phone) is notified like the mobile Claude app: a ping arrives ONLY when Claude genuinely ASKS the user something (`❓ NEEDS YOU`) or has FULLY completed the work (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress.** This replaces the old "ping every phase" stance — the user found per-merge / per-CI pings to be noise; per-phase visibility lives on the live board instead.

#### The mechanism is AUTOMATIC — do NOT hand-fire per-phase pings

Two airuleset hooks implement this from the status marker, with no action from you:
1. `notify-discord-pending.sh` (Stop) reads the last message's marker → records a pending device line ONLY for `❓` / `✅`; clears it on `⏳` / no-marker.
2. `notify-discord.sh` (Notification : idle_prompt) sends that pending line ONLY when the user is genuinely idle/away — so there is NO ping during active back-and-forth, and NOTHING on `⏳`.

So you do NOT call the discord `reply` tool or `PushNotification` to announce a merge, a deploy, a green CI, or a finished issue. Just write the honest status marker; the hook decides whether the device pings. The ONE thing you control is the marker content (below).

#### Device content = Slovak, short, phone-readable

The hook forwards the text after `❓ NEEDS YOU:` / `✅ DONE:` verbatim. So write that content in **Slovak, short (1 line), self-contained, no jargon** — it must be understandable on a phone with no terminal context. Keep the English keyword (`NEEDS YOU` / `DONE` — the hooks key on it); the content after the colon is Slovak. A question: the actual decision in one Slovak sentence (`❓ NEEDS YOU: reset EQ na 0 dB alebo posledný preset?`). A done: the outcome in one Slovak line (`✅ DONE: nasadené v1.2.3, board zelený`).

#### ✅ pings only at FULL completion — the `⏳`-while-looping discipline makes this real

The device must ping on a `✅ DONE` only when the WHOLE job is finished — NOT per merged issue. This is enforced by the status marker itself, not by the hook guessing: during an `/autopilot` / `/goal` loop, each per-issue / per-batch turn is **still running the loop**, so it ends `⏳ WORKING` (the loop continues to the next issue) — and `⏳` CLEARS the pending payload, so no intermediate device ping. ONLY the terminal turn — backlog empty, loop stops, nothing running — ends `✅ DONE`, and THAT is the single ping. So: inside a loop, never end an intermediate turn with `✅ DONE` (something IS running — the loop); reserve `✅ DONE` for the true end. The hook keys on the last line, so a turn that did `✅ DONE: #5 merged` mid-loop but ends `⏳ WORKING: pokračujem na #6` correctly notifies nothing.

#### Per-phase progress → the BOARD, not the device

During long / autonomous runs (`/autopilot`, `/goal` loops, batch work), report each phase (merged, deployed, CI green, issue finished) to the **autopilot board** (`airuleset.py report …`) — that is the live per-phase view. The device stays quiet until a worker raises a real `❓` question or the whole run ends `✅`. The board is for watching; the device is for "Claude needs me" / "Claude finished".

#### Every device message @mentions the tmux owner (zbynek / marek)

Each project runs in a tmux session grouped `zbynek` or `marek`. EVERY Discord message (the idle `❓`/`✅` ping AND the autopilot card below) is prefixed with that owner's `<@id>` so it is unambiguous WHOM the message concerns. This is automatic — the `notify-discord.sh` hook and `airuleset.py notify` resolve the owner from the tmux session group and look up `DISCORD_MENTION_<OWNER>` in the channel `.env`. You do nothing; just never strip the mention. No tmux / no mapping → no tag (never pings the wrong person).

#### EXCEPTION — `/autopilot` per-ticket completion card (the ONE sanctioned per-ticket hand-fire)

The "no per-merge device ping" rule above has ONE explicit, user-requested exception: during an `/autopilot` run, **each ticket whose PR merges gets ONE structured Discord card** — the user wants per-ticket visibility on the phone during hands-off runs. This is NOT the banned per-merge noise: it is a single, deduped, structured message through the dedicated path, NOT a hand-fired `reply`/`PushNotification`.

- **AUTOMATIC — fired by `airuleset.py report`, NOT by the agent.** When the autopilot worker reports the `merge` phase (`report --run <rid> --phase merge --pr <url> --result "<what landed>"`), `report` detaches `notify --run-card`, which gathers the issue title from gh, composes the card, @mentions the tmux owner, and posts it. Neither the supervisor nor the worker calls `notify` by hand. Because the trigger lives in the `report` subprocess the worker runs EVERY phase, it works even for an `/autopilot` loop that started **before** this feature shipped (the running worker keeps invoking the on-disk `airuleset.py` — no restart).
- The card is **Slovak, structured markdown**: **🎯 Cieľ** (the issue title) + **✅ Dosiahnuté** (the worker's `--result`), a **🔍 Double-review** line (a clean merge ⇒ met), the PR, and **📊 Autopilot** progress (`ostáva Y` open non-skip issues). Structure is composed in code, so it is consistent every time.
- **Deduped on the run id** — one card per ticket-run; retries / repeated merge reports never double-post; a failed send releases the claim so it retries.
- This is distinct from the `✅ DONE` end-of-run ping: the per-ticket cards fire DURING the loop (the loop turns still end `⏳ WORKING`), and the single `✅ DONE` fires only when the WHOLE backlog is empty.

#### Anti-patterns (all rewordings apply)

- Calling `reply` / `PushNotification` to announce a per-merge / per-CI / per-deploy milestone → **WRONG.** That is the per-phase noise the user removed; let the board show it. (The `/autopilot` per-ticket card is NOT this — it goes through `airuleset.py notify --autopilot-done`, deduped + structured, per the sanctioned EXCEPTION above; a raw `reply`/`PushNotification` per-merge is still banned.)
- A device ping for a `⏳ WORKING` turn → **WRONG.** Working ≠ asking ≠ done; the hook sends nothing on `⏳`.
- Writing the `❓` / `✅` content in English or as a long jargon-filled line → **WRONG.** Slovak, one short phone-readable sentence.
- `edit_message` instead of a new message when you DO reply in an active Discord conversation → edits don't ping (only relevant to live chat, not milestones).
