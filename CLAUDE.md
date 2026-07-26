# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

## Services

- **File-Drop** (`filedrop/` package, `:8788`) — serves user-facing files as clickable LAN URLs so the user (no direct FS access) can open them. `python3 airuleset.py share <file>` → **one URL per PRIVATE interface** (tailscale + LAN), because the user switches networks; the file DOWNLOAD server + the ephemeral `upload` WRITE endpoint (`airuleset.py upload`, `receive-files-via-upload-url.md`) both bind EVERY private IP from `filedrop.bind_ips()` (tailscale-first; interface-aware via `ip -o -4 addr` so container bridges docker0/cni-podman0 are dropped but real overlays wg*/zerotier kept) and NEVER the box's PUBLIC IP (gatekeeper 88.99.170.148 — the token is the only auth, so a public write-endpoint is banned). The systemd server can't enumerate its IPs under its AF_NETLINK sandbox → the bind list is baked into `Environment=FILEDROP_HOSTS` at install (unsandboxed); a stale baked LAN IP is skipped-not-fatal at bind. Per-file token = the link's auth. Governs via `modules/core/deliver-files-as-urls.md` + `receive-files-via-upload-url.md`.

- **Caveman plugin wiring + statusline** (`maybe_setup_caveman` in `airuleset.py`) — caveman (`JuliusBrussee/caveman`, third-party CC plugin for compressed output) is kept correctly wired on BOTH machines by `install` (so every `push` self-heals it). The managed shim ALSO renders the **Issues segment** (`statusbar.py`, renders `Issues N` / `Issues D/T` — text label per user, no emoji): autopilot `done/total` during a run (fed by `notify --run-card` → `~/.claude/autopilot-progress/<repo>.json`, 6h run window), else the count of open non-`autopilot-skip` GitHub issues — **scoped per stream**: a reduced-authority box (`resolve_authority` != full: david/montalu/marek) counts only ITS OWN slice (open non-skip `assignee:@me` ∪ `author:@me`; gh error → `open=None`, never a wrong number), full-authority boxes count the whole backlog (gatekeeper goal 2026-07-11 — David saw "Issues 16" instead of his 6). The render NEVER calls gh inline — it reads `~/.claude/tickets-status/<cwd-key>.json` and spawns a DETACHED `airuleset.py tickets-status --refresh --cwd <dir>` when stale (TTL 120s, spawn-guard 30s). `{{REPO_DIR}}` in the shim is substituted by `render_caveman_shim()` at install — never write the raw constant. airuleset does NOT own caveman's code, only its wiring: it kept half-installing / breaking because the plugin's real statusline script lives under a content-hashed cache dir (`plugins/cache/caveman/caveman/<hash>/…`) that **changes on every `claude plugin update`**, so any hard-coded hash in `settings.json` rots and the statusline silently dies. Fix: airuleset ships a **stable shim** at `~/.claude/airuleset-caveman-statusline.sh` that resolves the current hash at RUNTIME, points `settings.json` statusLine → the shim, ensures `enabledPlugins.caveman@caveman=true` + the marketplace, installs the plugin if its cache is missing, and seeds `.caveman-active` (preserving a valid `/caveman` mode pick, else `lite`). Pure reconcile logic is unit-tested (`tests/test_caveman.py`).

- **Playbook system** — per-project knowledge capture enforced after every autopilot ticket. Machinery in airuleset: rule module `modules/core/project-playbook-maintenance.md` (routing rule + `📔 Playbook:` marker mandate), `playbook-review` skill (runs post-ticket, inspects what was learnt, emits the gated marker), `playbook-cleanup` skill (one-time consolidation of accumulated notes into canonical how-to sections), and the Stop gate `hooks/stop-check-playbook-review.sh` (blocks completion reports missing the `📔 Playbook:` line). Content lives per-repo in `.claude/skills/<area>/SKILL.md` (per-area skill directories, indexed by a lean `## Playbook router` in the project CLAUDE.md — never in airuleset). Autopilot worker runs `playbook-review` at step 10 (after autopilot-log, before the final evidence block).

- **api-watchdog** (`watchdog/` package) — systemd `--user` timer (every 60s, every managed box), currently **21 numbered jobs**. `run_once()`'s own docstring (`watchdog/__init__.py`) is the SINGLE SOURCE OF TRUTH for the exact behavior of job N — this bullet is a POINTER + a highlight reel, not a re-narration, precisely so it stops silently rotting every time a new job lands (#57 — this bullet used to say "EIGHT jobs" while the code had grown to 16). Operationally the most important: **(1)** auto-resumes a session STALLED on an API error (`continue` via `send-keys`, retry/backoff, ping on give-up); **(2)** pings (never acts) when a session is waiting on the user; **(4)/(4a)** nudge a session idle on `⏳ WORKING` with no advancing subagent, or one whose last turn emitted a tool call as unexecuted TEXT — in case its launched work died silently (the in-session rule is `modules/quality/verify-launched-work-liveness.md`; these are the model-independent backstop); **(6)** the 5-hour session-limit banner: ping once, retry the auto-resume only AFTER the reset clock passes (never before — that just re-hits the limit); **(7)** routes an owner's Discord REPLY back into the exact session that asked the `❓` (known-owner + explicit-reply-to-our-`❓` + idle-pane gated); **(8)/(11)** BOUNCE / gk-request BACKSTOPs so a cross-stream gatekeeper↔sub-dev hand-off (`## Cross-stream protocol` in the autopilot skill) never silently rots; **(9)** auto-arms a printed `/goal` template into an idle pane; **(12)** MODEL RECONCILE — restarts a session onto a newer target model (`/exit` + relaunch; a running session's model list is fixed at its own start, so `/model` can never pick up anything released later); **(13)/(16)** hourly per-host burn snapshot + (dev1-only coordinator) fleet-wide merge, feeding `airuleset.py burn --compare`; **(14)/(15)** `/compact` at a completed-ticket boundary or when a long-lived idle session's live context grows past threshold; **(20)** GOAL RE-ARM BACKSTOP — cross-checks each session's transcript `Goal set:` marker (INTENT) against CC's own `◎ /goal` footer indicator (REALITY) and heals the mismatch, since an armed `/goal` can die silently with NO `Goal cleared:` marker; the same job nudges a goal that IS armed but whose loop stopped firing (#76), and re-arms a loop still running a STALE `/goal` template after a template change reached the fleet — identity by exact hash of a NORMALIZED template line, never a similarity threshold, and only for a session earlier OBSERVED matching a template, so a user's own goal is untouchable by construction (#64); **(21)** LONG-TURN WATCH — a turn running past a threshold is its own fault state (nothing compacts, no question is delivered, keystrokes pile up unexecuted), read from the PANE's spinner elapsed label because CC logs a Stop-hook-rejected continuation as a fresh turn (#84). `python3 airuleset.py watchdog --once [--dry-run --verbose]`.

- **Discord notify** (`notify/` package + `hooks/notify-discord*.sh`) — the single device-ping path (mobile-app model: ❓ ask / ✅ done / api-error / autopilot card). **Per-owner thread routing:** each tmux owner (zbynek / marek) is posted to THEIR OWN thread (`claude-zbynek` / `claude-marek`) so two people's pings don't mix — an @mention alone was not enough. `notify.notification_channel(env, owner)` resolves `DISCORD_NOTIFICATION_CHANNEL_<OWNER>` → shared `DISCORD_NOTIFICATION_CHANNEL_ID` fallback → "". A Discord thread IS a channel in the API, so the per-owner id is just a different POST target. Both send paths are owner-aware: the shell `notify-discord-send.sh` (❓/✅) resolves the owner ONCE via `airuleset.py notify --owner` and forces it onto `--mention-prefix` + `--channel-id` (so they can never disagree); the Python `notify.send()` (api-error + run-card) resolves once internally. **Parallel mirror recipients (`DISCORD_MIRROR_<OWNER>`):** a notification for one owner can ALSO fan out to other owners' threads, each with THEIR OWN @mention — so an automated session-persona (e.g. `david` on dev2 running codex-bridge) gets its OWN `claude-david` thread AND the real human `zbynek` is pinged in parallel (`DISCORD_MIRROR_DAVID=zbynek`). Both send paths honour it: `notify.mirror_owners(env, owner)` returns the extra owners; `notify.send()` and the shell loop post one message per target (primary first), skipping a mirror whose thread equals the primary's (no double-post). A normal single-owner box (zbynek / marek — no mirror configured) fans out to exactly one target = unchanged. Env keys (`DISCORD_BOT_TOKEN`, `DISCORD_MENTION_<OWNER>`, `DISCORD_NOTIFICATION_CHANNEL_*`, `DISCORD_MIRROR_<OWNER>`) live in the LOCAL `~/.claude/channels/discord/.env` (NOT git). Governs via `modules/core/milestone-notifications.md`.

## Structure

- `modules/` — Atomic rule blocks (standalone .md files), organized by category
- `rules/` — Path-scoped rules with YAML frontmatter (for `.claude/rules/` symlinks)
- `profiles/` — Named sets of modules for different project types
- `skills/` — Global skills in SKILL.md format
- `hooks/` — Hook scripts referenced by settings.json
- `settings/` — JSON fragments for settings.json merging
- `airuleset.py` — CLI tool (Python, stdlib only)

## Commands

```bash
python3 airuleset.py install    # Deploy to ~/.claude/ (CLAUDE.md + skills + hooks)
python3 airuleset.py diff       # Preview changes before installing
python3 airuleset.py validate   # Check all files exist and resolve
python3 airuleset.py status     # Show current managed config state
python3 airuleset.py push       # Push to GitHub + install locally + deploy to ALL remote machines
```

## Deployment Policy — BOTH MACHINES

**After ANY change to airuleset, you MUST deploy to ALL machines.** Use `python3 airuleset.py push` instead of `git push` — it pushes to GitHub, installs locally, AND deploys to all remote machines automatically.

Remote machines:

- **dev2**: 100.82.64.27 (user: newlevel) — `~/devel/airuleset/`

**Never use bare `git push` for airuleset changes.** Always use `python3 airuleset.py push`.

## Development Rules

Moved VERBATIM to `.claude/rules/airuleset-internals.md` (#92) — a path-scoped rule
Claude Code injects automatically the moment you read `airuleset.py`, `statusbar.py`,
`watchdog/`, `hooks/`, `notify/`, `filedrop/`, `burn/`, `tests/`, `settings/` or
`scripts/`. It used to sit on the always-on prefix of every session in this repo,
including the many that never touch that code.

## Rule intake gate — before ADDING any new always-on module

Every new rule originates from a real problem — but it must land on the RIGHT surface, not reflexively in the always-on profile (content loads context every turn of every project; the gate picks the surface, never drops the content):

1. **Mechanically checkable?** → a hook (deterministic) + at most a one-line pointer in an existing module.
2. **Situational** (fires only for one task-type/area — deploy, migration, hardware, mutation, one language)? → an on-demand skill or a `rules/` path-scoped rule.
3. **Topic already owned by an existing module?** → extend that module; never create a parallel new one.
4. Only a genuinely always-relevant, cross-project discipline becomes a NEW module — and its body cites the originating incident + date, so future `/mdreview` native-now passes have something to re-validate against.

## Skill Ownership — DO NOT manage skills belonging to other projects

airuleset only manages skills it created: `ci-monitor`, `deploy-ssh`, `windows-remote-gui`.

These skills are NOT managed by airuleset — do not add, symlink, or modify them:

- `win-mcp.md` — belongs to `winremote-setup` project
- `test-contact-form.md` — belongs to `website-bakerion.ai` project
