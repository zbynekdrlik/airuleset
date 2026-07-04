# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

## Services

- **File-Drop** (`filedrop/` package, `:8788`) — serves user-facing files as clickable LAN URLs so the user (no direct FS access) can open them. `python3 airuleset.py share <file>` → `http://<lan-ip>:8788/<token>/<name>`. systemd `--user` service on BOTH machines (each binds its own LAN IP, baked into the unit at install). Read-only static server; per-file token = the link's auth. Governs via `modules/core/deliver-files-as-urls.md`.

- **Caveman plugin wiring** (`maybe_setup_caveman` in `airuleset.py`) — caveman (`JuliusBrussee/caveman`, third-party CC plugin for compressed output) is kept correctly wired on BOTH machines by `install` (so every `push` self-heals it). airuleset does NOT own caveman's code, only its wiring: it kept half-installing / breaking because the plugin's real statusline script lives under a content-hashed cache dir (`plugins/cache/caveman/caveman/<hash>/…`) that **changes on every `claude plugin update`**, so any hard-coded hash in `settings.json` rots and the statusline silently dies. Fix: airuleset ships a **stable shim** at `~/.claude/airuleset-caveman-statusline.sh` that resolves the current hash at RUNTIME, points `settings.json` statusLine → the shim, ensures `enabledPlugins.caveman@caveman=true` + the marketplace, installs the plugin if its cache is missing, and seeds `.caveman-active` (preserving a valid `/caveman` mode pick, else `lite`). Pure reconcile logic is unit-tested (`tests/test_caveman.py`).

- **Playbook system** — per-project knowledge capture enforced after every autopilot ticket. Machinery in airuleset: rule module `modules/core/project-playbook-maintenance.md` (routing rule + `📔 Playbook:` marker mandate), `playbook-review` skill (runs post-ticket, inspects what was learnt, emits the gated marker), `playbook-cleanup` skill (one-time consolidation of accumulated notes into canonical how-to sections), and the Stop gate `hooks/stop-check-playbook-review.sh` (blocks completion reports missing the `📔 Playbook:` line). Content lives per-repo in `.claude/skills/<area>/SKILL.md` (per-area skill directories, indexed by a lean `## Playbook router` in the project CLAUDE.md — never in airuleset). Autopilot worker runs `playbook-review` at step 10 (after autopilot-log, before the final evidence block).

- **api-watchdog** (`watchdog/` package) — systemd `--user` timer (every 60s, BOTH machines) with SEVEN jobs: (1) detect a Claude Code session **stalled on an API error** (529 / ConnectionRefused / rate limit) and auto-resume it with `tmux send-keys "continue"` (retry every 5 min up to 3×, then ping "gave up"); (2) ping (never act) when a session is **waiting on the user** (an AskUserQuestion / permission dialog is open); (3) a rate-limited (~15 min) poll of Anthropic's `oauth/usage` endpoint that **alerts on Discord when the WEEKLY token limit reaches 98%** (the same data `/usage` shows; the endpoint 429s hard so it is polled rarely); (4) **WORKING-STALL self-check NUDGE** — a session parked on `⏳ WORKING` with no advancing subagent for ≥30 min gets an automatic `stuck-check` nudge typed in (`send-keys` — the autonomous form of the user hand-typing "stucked?"), telling it to verify the liveness of its launched work and intervene if it died silently; retries up to 3×, escalate-pings ONLY if the session never responds (its Claude process is itself wedged). Safe where blind `continue` was the user's scar because the nudge is a self-check QUESTION that delegates the healthy-vs-dead call back to the session (which has eyes) — a landed nudge resets idle and self-resolves with no Discord noise. 30 min is the user's chosen cadence: an occasional nudge that resolves to "not stuck" is wanted (a liveness confirmation beats hoping nothing is wedged and losing a day). It supersedes the prior PING-ONLY design (a ping to an offline user did nothing overnight). The in-session rule `modules/quality/verify-launched-work-liveness.md` is the real fix; job 4 is the model-independent backstop; (5) **deliver a pending ✅** the unreliable `idle_prompt` event failed to send (only while the session is STILL ✅ — a re-fired one is cleared silently); (6) **5-HOUR SESSION-LIMIT auto-resume** — a session showing Claude Code's session-limit banner in its pane (`You've hit your session limit · resets <time>`) is PINGED ONCE with the reset time, then sent ONE `continue` **after the reset clock passes** (never before — `continue` pre-reset just re-hits the limit, the user's incident). Read from the PANE (the banner isn't reliably a transcript api-error); the reset clock is parsed from the banner (Europe/Prague tz), stable per episode in `sesslimit:<sid>` state; detection is precise (matches "hit your session limit" / "/usage-credits", NOT prose merely mentioning the limit). Why a poller, not a hook: when a turn dies on an API error CC ABORTS the turn and does NOT reliably fire the `Stop` hook with the error text, so `notify-api-error.sh` is blind. A session waiting on a real `❓` is never auto-continued (its last entry isn't an api error). (7) **DISCORD REPLY → the asking session** — when a `❓` ping is delivered, the send path records the ping's Discord message id → the asking session (`notify.record_question` → machine-local `~/.claude/discord-questions.json`); the user ANSWERS by REPLYING to that ping in Discord (phone), and the watchdog reads recent messages in the notification thread(s), matches a reply's referenced message id to the local map, and **types the answer into that exact session's IDLE pane** (`deliver_discord_replies` → `send-keys`, gated on `pane_at_idle_prompt` = the #233 never-interrupt-a-running-turn invariant), then reacts ✅. **SECURITY**: a reply is actioned ONLY when its author id is a KNOWN OWNER of this machine (`notify.known_owner_ids` = the `DISCORD_MENTION_*` set), it explicitly REPLIES to a `❓` THIS machine sent, and the target pane is idle at a free `❯`; delivered once (dedup on reply id + the question dropped on delivery). Capture side: `hooks/notify-discord-send.sh` in CONFIRM mode extracts the created message's `.id` and calls `airuleset.py notify --record-question` (only when `ND_SESSION_ID` is set — the `❓` path). `python3 airuleset.py watchdog --once [--dry-run --verbose]`.

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

- Python stdlib only — no third-party dependencies
- Every module must be standalone, actionable, and 5-20 lines
- Rules must have YAML `paths:` frontmatter
- Skills use the SKILL.md format with YAML frontmatter
- Test with `python -m pytest tests/` before committing
- **Adding a `--flag` early-return to a `cmd_*` dispatcher that has `m.Mock(...)`-based tests:** `m.Mock` auto-creates EVERY attribute as a truthy Mock, so `getattr(args, "flag", False)` is truthy and the new branch hijacks unrelated tests. Pin the new flag `=False` in every `m.Mock(...)` args builder for that command (e.g. the run_card builders for `cmd_notify`).
- **Wiring local (non-git) config to dev2 while the tree is dirty:** the `pre-deploy-clean-tree.sh` hook blocks `scp`/`rsync` from a dirty tree. For a genuine non-repo transfer (e.g. writing the local `~/.claude/.../.env`), pipe the helper via `ssh dev2 'python3 - <args>' < script.py` (stdin) instead of `scp` — no file-copy, no bypass marker needed.

## Skill Ownership — DO NOT manage skills belonging to other projects

airuleset only manages skills it created: `ci-monitor`, `deploy-ssh`, `windows-remote-gui`.

These skills are NOT managed by airuleset — do not add, symlink, or modify them:

- `win-mcp.md` — belongs to `winremote-setup` project
- `test-contact-form.md` — belongs to `website-bakerion.ai` project
