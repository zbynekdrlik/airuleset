### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill — the ONE per-ticket device card (the EXCEPTION below) is fired by the worker directly at merge

**The device (Discord / phone) is notified like the mobile Claude app: a ping arrives ONLY when Claude genuinely ASKS the user something (`❓ NEEDS YOU`) or has FULLY completed the work (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress.** This replaces the old "ping every phase" stance — the user found per-merge / per-CI pings to be noise.

#### The mechanism is AUTOMATIC — do NOT hand-fire per-phase pings

Three airuleset hooks implement this from the status marker, with no action from you:
1. `notify-discord-pending.sh` (Stop) reads the last message's marker. On `❓ NEEDS YOU` it **SENDS the device ping IMMEDIATELY** (via the shared `notify-discord-send.sh`) — the user is BLOCKED on you, and Claude Code's `idle_prompt` event is unreliable over tmux/SSH, so a question must NOT wait for idle (depending on it is exactly why pings "stopped" arriving). On `✅ DONE` it records a pending line; on `⏳` / no-marker it clears any pending.
2. `notify-discord.sh` (Notification : idle_prompt) sends the pending `✅` line ONLY when the user is genuinely idle/away — a finished turn is less urgent than a question, and pinging every completed turn while the user watches the terminal is spam. NOTHING on `⏳`.
3. `notify-discord-send.sh` is the single send path both call (compose structured line + @mention owner + POST) — one place for the curl, no duplication.

So: a **question (`❓`) reaches the phone right away**; a **done (`✅`) reaches it when you're away**. Neither needs action from you beyond writing the honest marker.

So you do NOT call the discord `reply` tool or `PushNotification` to announce a merge, a deploy, a green CI, or a finished issue. Just write the honest status marker; the hook decides whether the device pings. The ONE thing you control is the marker content (below).

#### Device content = Slovak, short, phone-readable

The hook forwards the text after `❓ NEEDS YOU:` / `✅ DONE:` verbatim. So write that content in **Slovak, short (1 line), self-contained, no jargon** — it must be understandable on a phone with no terminal context. Keep the English keyword (`NEEDS YOU` / `DONE` — the hooks key on it); the content after the colon is Slovak. A question: the actual decision in one Slovak sentence (`❓ NEEDS YOU: reset EQ na 0 dB alebo posledný preset?`). A done: the outcome in one Slovak line (`✅ DONE: nasadené v1.2.3, CI zelené`).

#### ✅ pings only at FULL completion — the `⏳`-while-looping discipline makes this real

The device must ping on a `✅ DONE` only when the WHOLE job is finished — NOT per merged issue. This is enforced by the status marker itself, not by the hook guessing: during an `/autopilot` / `/goal` loop, each per-issue / per-batch turn is **still running the loop**, so it ends `⏳ WORKING` (the loop continues to the next issue) — and `⏳` CLEARS the pending payload, so no intermediate device ping. ONLY the terminal turn — backlog empty, loop stops, nothing running — ends `✅ DONE`, and THAT is the single ping. So: inside a loop, never end an intermediate turn with `✅ DONE` (something IS running — the loop); reserve `✅ DONE` for the true end. The hook keys on the last line, so a turn that did `✅ DONE: #5 merged` mid-loop but ends `⏳ WORKING: pokračujem na #6` correctly notifies nothing.

#### API-error watchdog — the device pings when a turn ends on a real API error (you do nothing)

A third sanctioned device ping, fully AUTOMATIC: the **`notify-api-error.sh` Stop hook** fires ONE Discord ping `@owner` when a turn ENDS on a genuine Claude Code API error (rate-limit, overload, socket-closed, usage-limit). Claude Code marks a real error with `isApiErrorMessage` and ends the turn on it, so the Stop payload's `last_assistant_message` IS the error text — `airuleset.py notify --api-error` only sends when the text actually matches an API-error pattern (a normal turn → nothing) and dedups one ping per distinct error per session. This surfaces a stall caused by an API failure without false positives. It is hook-driven (`notify-api-error.sh`) — you do nothing; just never strip the hook.

#### Per-phase progress is NOT pinged

During long / autonomous runs (`/autopilot`, `/goal` loops, batch work), routine per-phase progress (merged, deployed, CI green) does NOT ping the device — the device stays quiet until a worker raises a real `❓` question, fires the per-ticket merge card (the EXCEPTION below), or the whole run ends `✅`. Do NOT hand-fire a per-phase ping.

#### Every device message @mentions the tmux owner (zbynek / marek)

Each project runs in a tmux session grouped `zbynek` or `marek`. EVERY Discord message (the idle `❓`/`✅` ping AND the autopilot card below) is prefixed with that owner's `<@id>` so it is unambiguous WHOM the message concerns. This is automatic — the `notify-discord.sh` hook and `airuleset.py notify` resolve the owner from the tmux session group and look up `DISCORD_MENTION_<OWNER>` in the channel `.env`. You do nothing; just never strip the mention. No tmux / no mapping → no tag (never pings the wrong person).

#### EXCEPTION — `/autopilot` per-ticket completion card (the ONE sanctioned per-ticket hand-fire)

The "no per-merge device ping" rule above has ONE explicit, user-requested exception: during an `/autopilot` run, **each ticket whose PR merges gets ONE structured Discord card** — the user wants per-ticket visibility on the phone during hands-off runs. This is NOT the banned per-merge noise: it is a single, deduped, structured message through the dedicated path, NOT a hand-fired `reply`/`PushNotification`.

- **Fired by the `/autopilot` WORKER after merge + post-deploy verification.** The worker runs `airuleset.py notify --run-card --repo <owner/name> --issue <N> --goal "<plain goal>" --achieved "<plain what landed>" --version "<deployed version read from the DOM>"` (one per member, even in a batch). `--goal`/`--achieved` are PLAIN, simple, non-technical Slovak (the worker translates the technical issue, NOT the raw title); `notify --run-card` gathers the remaining backlog from gh, @mentions the tmux owner, and posts the card. The supervisor does NOT call `notify` by hand — it just confirms the worker carded each merged member.
- The card is **Slovak, structured markdown**: header **🎫 #N** (number only — the technical title is dropped), **🎯 Cieľ** (the worker's plain `--goal`) + **✅ Dosiahnuté** (the worker's plain `--achieved`), the **📦 deployed version** (`--version`), a **🔗 links** line — the `--url` "WHERE to SEE the change live" link(s): the web page / the specific dashboard sub-page the change is visible on, labelable (`Money Gate stav=…`); NOT a PR/diff link (the user doesn't want it) — and **📊 Autopilot** progress (`ostáva Y` open non-skip issues). Structure is composed in code, so it is consistent every time. (The "🔍 Double-review" line, the "PR #N" number / PR link, and the verbatim technical title were all removed at the user's request.)
- **Deduped on repo-name#issue** — one card per ticket; a re-dispatched fresh worker (SendMessage is gated) never double-posts; a failed send releases the claim so it retries.
- This is distinct from the `✅ DONE` end-of-run ping: the per-ticket cards fire DURING the loop (the loop turns still end `⏳ WORKING`), and the single `✅ DONE` fires only when the WHOLE backlog is empty.

#### Anti-patterns (all rewordings apply)

- Calling `reply` / `PushNotification` to announce a per-merge / per-CI / per-deploy milestone → **WRONG.** That is the per-phase noise the user removed. (The `/autopilot` per-ticket card is NOT this — the worker fires it via `airuleset.py notify --run-card`, deduped + structured, per the sanctioned EXCEPTION above; a raw `reply`/`PushNotification` per-merge is still banned.)
- A device ping for a `⏳ WORKING` turn → **WRONG.** Working ≠ asking ≠ done; the hook sends nothing on `⏳`.
- Writing the `❓` / `✅` content in English or as a long jargon-filled line → **WRONG.** Slovak, one short phone-readable sentence.
- `edit_message` instead of a new message when you DO reply in an active Discord conversation → edits don't ping (only relevant to live chat, not milestones).
