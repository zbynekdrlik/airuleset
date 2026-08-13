# Autopilot — Session mechanics reference

> Extracted verbatim from `skills/autopilot/SKILL.md` (#426, the file's own
> #414 ~1000-line/file budget) — background-worker watching/steering and
> context-hygiene/resume mechanics: FYI on HOW the loop runs, not operative
> per-ticket instructions. Content below is UNCHANGED from its prior in-file
> form — only its location moved.

## Watching & steering

The worker runs in the **background** (`run_in_background: true`), so your **main session stays FREE
and interactive while it works** (you can keep messaging it) AND it stays VISIBLE in the **agent
strip** (`main` + `autopilot-worker`, `↑/↓` to select, `Enter` to view). Its questions **surface in
your main session**, so you discuss the important calls; everything routine runs without you. (This
uses in-session subagents — the strip mechanism — NOT hidden `claude --bg` daemon sessions. Why
background not foreground: Claude Code 2.1.x makes a FOREGROUND dispatch BLOCK the main session —
you couldn't message it while a worker ran, CC issue #71768 — and its 2026-W26 change made
background-subagent prompts surface in the parent, removing the only reason foreground was used.)

## Context hygiene & resume

GitHub-as-state + `docs/autopilot-log.md` (re-read each cycle) hold the truth; workers return only
summaries so the main session stays thin and auto-compaction is harmless. Lasting conventions a
worker discovers go into the repo `CLAUDE.md`. If the session ends, `--resume` continues the
`/goal`; in-flight work is already on `dev`, so an unclosed issue just gets re-dispatched.

