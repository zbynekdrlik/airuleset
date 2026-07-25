---
name: autopilot-dialog
description: "Usage: /autopilot-dialog. Thin alias for `/autopilot dialog` — runs TODAY's full interactive start-of-run flow (Step 1b skip-review/add-skip picker + Step 1c close-obsolete picker) before printing the /goal line. Exists so literal /autopilot-dialog works even though /autopilot dialog is the same thing (#52)."
argument-hint: ""
user-invocable: true
disable-model-invocation: true
---

# Autopilot Dialog — Alias for `/autopilot dialog`

Invoke the `autopilot` skill exactly as if the user had typed `/autopilot dialog`: run Step 1
(preflight), then Step 1b (skip-review + add-skip picker) and Step 1c (close-obsolete picker),
then Step 2 (print the `/goal` line and stop) — the full flow described in
`skills/autopilot/SKILL.md`. Nothing here overrides or duplicates that skill's body; this file
only exists so a user who types the literal command `/autopilot-dialog` (instead of `/autopilot
dialog`) gets the same interactive flow.
