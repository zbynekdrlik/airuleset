### No Dropped Work — Fix It, Record It, or File a Real Defect

**The three fates of identified work (#1297; #1378, owner 19.11.2026):** fix it now (default); a `- [ ]` line in the DESCRIPTION of its ticket (plan steps never become tickets; one screen or one flow = ONE ticket; never comments; `block-close-with-open-checklist.sh` guards lines); its own linked ticket for a separate deliverable (another request, handover, real defect outside the change; `Part of #P` / `- [ ] #C`). A handover: `gh issue create -R <repo>` saying `Requested by owner/repo#N` + a `peer_send` tell, never a comment on its ticket.

**A Claude-born ticket only for a real defect** a user or business hits (wrong money, lost/wrong data, legal deadline, broken screen/flow, security, blocked work), with `Impact: <who is hit and how>`; a nit, cleanup or 🔵 is fixed in the PR or dropped with one line. Braked while its stream filed > it closed in 72 h (then: description line, fix or drop). Workers' `followup_candidates:` carry `Impact:`.

**Context gate:** `durable-decisions-to-tickets.md`. Failure modes, banned phrases: `skills/no-dropped-work-deep/DEEP.md` (auto-loads on `gh issue create`).
