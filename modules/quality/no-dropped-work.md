### No Dropped Work — Fix It, Record It, or File a Real Defect

**The three fates of identified work (#1297, #1378):** fix it now (default); a `- [ ]` line in the DESCRIPTION of the ticket it belongs to (plan steps never become tickets; one screen or one flow = ONE ticket of description lines, never comments: the close hook guards them); its own linked ticket for a separate deliverable (`Part of #P` / `- [ ] #C`). A task another project hands over = `gh issue create -R <receiving repo>` saying `Requested by owner/repo#N` + a `peer_send` tell, never a comment on its ticket.

**A Claude-born ticket (no owner request, no handover) only for a real defect** a user or business hits (wrong money, lost/wrong data, legal deadline, broken screen/flow, security, blocked work), stated as `Impact: <who is hit and how>`; a nit, cleanup or 🔵 finding is fixed in the PR in hand or dropped with one line. A stream filing ≥ what it closed in 72 h is braked. A worker returns `followup_candidates:` with an `Impact:` each.

**Context gate:** `durable-decisions-to-tickets.md`. Failure modes, banned phrases: `skills/no-dropped-work-deep/DEEP.md` (auto-loads on `gh issue create`).
