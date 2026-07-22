### Statusline Vocabulary — "issues N", "gk N", "skipped N" = the Footer Segment

**When the user says "issues N", "gk N", or "skipped N", they usually mean the airuleset statusline segment at the BOTTOM of Claude Code** (the footer next to the ctx meter) — not a GitHub query for you to invent (user directive, 2026-07-19). The segment's forms:

- `Issues N` — open non-`autopilot-skip` GitHub issues for this repo (a reduced-authority sub-dev box counts only ITS OWN slice).
- `Issues D/T` — an ACTIVE autopilot run: D tickets done of T total (green when the backlog empties).
- `· gk N` — sub-dev boxes only: own tickets already handed off to the gatekeeper (`ready-for-review` label); gk 0 still renders.
- `· skipped K` — tickets labeled `autopilot-skip` (excluded from runs); hidden at 0.
- `otazky N` (orange, own chunk next to the Issues segment) — UNANSWERED ❓ pings awaiting the user's reply, from the machine-local question map `~/.claude/discord-questions.json` (entry added per ❓ ping, dropped when the watchdog routes the answer into the asking session; 24h TTL). User-global (cwd-independent), hidden at 0.

The segment renders from machine-local caches — read THOSE when the user asks about it (never guess, never re-derive differently): `~/.claude/tickets-status/<cwd-key>.json` (refresh: `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh --cwd <dir>`) and `~/.claude/autopilot-progress/<repo>.json`. A number the user quotes that mismatches your own `gh` count usually means a stale cache (TTL 120 s) or the per-stream scoping above — explain, don't dismiss. Applies to all rewordings ("v pätičke", "dole v claude", "ten counter").
