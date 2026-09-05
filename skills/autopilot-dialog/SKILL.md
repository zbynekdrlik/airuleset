---
name: autopilot-dialog
description: Autopilot dialog mode — interactive issue selection with the user present. Use when the user wants to pick issues interactively.
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
