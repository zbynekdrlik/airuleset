### milestone-notifications.md — History & Incident Narratives

Moved VERBATIM from `modules/core/milestone-notifications.md` during context diet (#859 batch 2).
The enforcement rules stay in the always-on module.

---

#### #134 armed-goal silence incident (2026-07-23 to 2026-07-28)

**Why the armed-goal test alone was wrong (#134, 2026-07-23 → 2026-07-28).** It deferred to something nothing enforced. When workers drifted out of firing the card, this guard removed the only remaining signal: five days, ~85 merged PRs and ~103 closed issues, and zero reports on the user's phone — noticed only because token spend was 5× higher and results appeared to have stopped. An armed goal is also close to a permanent state on a loop box (18h+ across 7 re-arms with zero `Goal cleared:`), so the suppression was total rather than occasional. **A suppression that defers to an unenforced action is a silence generator** — if you add another one, gate it on the artifact the action leaves behind, not on the intent to act. A marker's mere PRESENCE is not that artifact either: it is written before the POST, so it proves a claim, not a delivery (`notify.marker_delivered`, #135).

#### Owner-scoped delivery mechanics (#710, #716, #791)

**Owner-scoped DELIVERY (#710, 2026-08-26):** the ❓ ping is delivered for **david**, OFF for **zbynek**/**marek** (webterm + footer `U N`) — SUPPRESSED, logged not silent; session marker + question-map code + `needs-answer` unchanged (ticket-carrying → `U N` via label; ticketless-`U N` fold = bounded #716 gap); mechanism in `notification-mechanics`. There is NO night/day difference (#791): a question is asked the moment it arises 24/7 — no night-hour cutoff, no time-of-day deferral — reaching the owner identically at 03:00 and at noon. Full ask-the-moment policy: `message-status-marker.md` + the autopilot skill.

#### API-error alert retirement (#546, 2026-08-18 owner directive)

**The api-error / limit / token-burn alert classes NO LONGER ping the device** — owner-suppressed at `notify.send()` (#546: airuleset does not Discord-alert on api-error / limit / subscription). airuleset's only job on an api-error is the watchdog's SILENT `continue` auto-resume (unchanged); the signal moves to the machine channel (the journal + a `suppressed` delivery-log line, never a silent drop). A genuinely DEAD fleet still alarms (watchdog job 35); a stuck-but-alive give-up is deliberately machine-channel-only; default is SILENCE. Preserved: `❓`/`✅`/run-cards/bounce/gk-req and the one-shot `acctblock:` alarm. **A repo-health FINDING (Job 27 net-drift) is the SAME machine-channel-only family (#850) — never an owner ping; the footer `I N▲` marker + the job's own journal line are the record.** Full mechanics in the `notification-mechanics` skill.

#### Card enforcement backstory (#134)

It used to be prose ending "if it fails, IGNORE it and continue", and prose failed at exactly this spot — that is the whole finding of #134.
