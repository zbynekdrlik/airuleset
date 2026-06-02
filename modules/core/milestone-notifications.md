### Milestone Notifications — Ping on Phase Achieved, Not Only When Idle

**During long or autonomous runs (`/goal` loops, `/autopilot`, batch issue work, overnight CI drives), proactively notify the user at each meaningful phase completion — not just when you go idle waiting for input.** The user wants per-phase visibility ("merged to main and X achieved"), not silence until the very end.

#### When to notify (a phase was achieved)

- A PR **merged to main** — name the issues closed + the deployed version
- A **deploy verified live** (version read from the DOM)
- **CI driven green** after it was red
- A **batch / issue finished and committed** (for multi-batch loops)
- The **whole goal / backlog reached its end state** (backlog empty, condition met)
- **Stopped for a genuine question** (decision needed, destructive action, unfixable CI) — so the user comes back

#### Mechanism — Discord-first, push fallback

1. **Discord chat_id available** (session bridged via `--channels`, or any inbound `<channel source="discord" chat_id="…">` seen this session) → post via the discord `reply` tool: `reply(chat_id, "<milestone>")`. Lands in the Discord thread AND pings the device. Send a NEW reply (not `edit_message`) — edits don't ping.
2. **No Discord chat_id** → `PushNotification` with `status: proactive` — desktop notification + phone (if Remote Control connected).

#### Message rules

- One line, lead with the actionable fact + IDs: `merged #42+#45 to main → deployed v1.2.3-dev.7, CI green`.
- Under 200 chars. No markdown in `PushNotification` bodies.
- For a stop-for-question ping, say what's blocked: `autopilot paused — issue #51 needs a design call (reset to 0dB or last preset?)`.

#### Don't over-notify

Milestones only — never every commit, every CI poll, or routine progress. A ping the user didn't need erodes the signal. Test: "if they walked away, would they want to know THIS now?" Yes → notify. Routine step → no.

#### Anti-patterns (all rewordings apply)

- Pinging every commit / every CI poll → **WRONG.** Milestones only.
- Silent through a 2-hour loop, one ping at the end → **WRONG.** Ping each phase.
- `edit_message` for a completion instead of a new `reply` → **WRONG.** Edits don't ping the device.
- Notifying routine progress to "keep the user informed" → **WRONG.** That's noise, not a milestone.
