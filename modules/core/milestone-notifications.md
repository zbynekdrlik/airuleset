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

#### Anti-patterns (all rewordings apply)

- Calling `reply` / `PushNotification` to announce a per-merge / per-CI / per-deploy milestone → **WRONG.** That is the per-phase noise the user removed; let the board show it.
- A device ping for a `⏳ WORKING` turn → **WRONG.** Working ≠ asking ≠ done; the hook sends nothing on `⏳`.
- Writing the `❓` / `✅` content in English or as a long jargon-filled line → **WRONG.** Slovak, one short phone-readable sentence.
- `edit_message` instead of a new message when you DO reply in an active Discord conversation → edits don't ping (only relevant to live chat, not milestones).
