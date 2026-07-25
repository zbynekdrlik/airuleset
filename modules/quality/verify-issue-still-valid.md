### Verify the Issue Is Still Real — BEFORE You Touch It → on-demand skill `verify-issue-still-valid`

The full validation protocol moved VERBATIM to the `verify-issue-still-valid` skill — load it before implementing ANY ticket, and it is the mandatory gate `/autopilot` and `/issue-planner` run (via the `ticket-validator` subagent) before selecting/dispatching a ticket. Non-negotiable that survives here: **tickets rot** — never trust stale issue text, reproduce the bug/feature LIVE with your own tools before implementing, and close/rescope with evidence when a ticket is already overcome.
