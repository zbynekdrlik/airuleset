### No Dropped Work — Every Separate Deliverable Gets Its Own Ticket

**A NEW ticket for every separate deliverable (#1297, owner 10.11.2026: „nasilu sa pcha do existujucich ticketov aj veci ktore mali ist o novych … sa straca praca").** Separate = a different request, a task another project hands over (`gh issue create -R <receiving repo>` saying `Requested by owner/repo#N` + a `peer_send` tell, never a comment on its ticket), or a defect outside the change in hand. Link both ways: the child says `Part of #P`, the parent's description gets `- [ ] #C`.

**The three fates of identified work:** fix it now (default); a `- [ ]` line in the description for a step of the SAME deliverable (plan steps never become tickets); its own linked ticket when separate. The description is the task list: a comment that adds work also adds its `- [ ]` line, and a ticket closes only when every line is `- [x]` with evidence (hook: `block-close-with-open-checklist.sh`). A worktree WORKER returns `followup_candidates:` instead.

**Context gate:** `durable-decisions-to-tickets.md`. Failure modes, banned phrases: `skills/no-dropped-work-deep/DEEP.md` (auto-loads on `gh issue create`).
