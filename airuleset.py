#!/usr/bin/env python3
"""airuleset — Claude Code configuration management CLI.

Manages ~/.claude/CLAUDE.md imports, skills symlinks, and hook settings
from a centralized airuleset repository.

Usage:
    python airuleset.py install   # Deploy config to ~/.claude/
    python airuleset.py diff      # Show what install would change
    python airuleset.py validate  # Check all module/rule files exist
    python airuleset.py status    # Show current managed config
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
SKILLS_DIR = CLAUDE_DIR / "skills"
AGENTS_DIR = CLAUDE_DIR / "agents"
# Claude Code's native "User"-scope path-scoped-rules directory (#40):
# confirmed against the installed CC binary that the User rules dir is
# join(<user config base>, "rules") -- the same base as the well-known User
# CLAUDE.md (join(<user config base>, "CLAUDE.md") == ~/.claude/CLAUDE.md).
RULES_DIR = CLAUDE_DIR / "rules"

MANAGED_HEADER = "# Managed by airuleset"
MANAGED_MARKER = "<!-- airuleset-managed -->"

# Externally-managed CLAUDE.md blocks to PRESERVE across regeneration. airuleset
# fully regenerates ~/.claude/CLAUDE.md from the profile; that would otherwise wipe
# a delimited block another tool injects. CodeGraph (`codegraph install`) appends
# its guidance block here — preserve it so a `push` doesn't silently delete it.
EXTERNAL_BLOCK_MARKERS = [("<!-- CODEGRAPH_START -->", "<!-- CODEGRAPH_END -->")]

# Managed default effort: `high` — owner directive 2026-08-30 ("Chcel by som
# este aby sa claude v targetoch nespustali s zapnutym ultracode ale s effort
# high") REVERSES the launch-flag half of #445 (which had set `xhigh` + a
# standing ultracode launch flag): managed sessions no longer launch with
# ultracode, and the effort baseline drops `xhigh` → `high`. `effortLevel`
# accepts only low|medium|high|xhigh (docs: `max`/`ultracode` session-only);
# the launch script (CLAUDE_LAUNCH_SCRIPT_CONTENT) no longer bakes
# `--settings '{"ultracode":true}'` into any mode. Only the LAUNCH FLAGS
# reversed — max-acceleration doctrine + per-phase model tiering UNCHANGED.
# User can still raise per session with `/effort`, or opt into ultracode by hand.
MANAGED_EFFORT_LEVEL = "high"

# Managed default MAIN-session model (user directive 2026-08-13): **Opus 5
# is BANNED everywhere** ("opus 5 sa nesmie pouzivat... by default pri
# spusteni claude fable") — the managed MAIN default is **Fable 5**, and the
# unconditional-managed-default treatment is exactly what makes the ban
# self-healing fleet-wide: a stale banned id a prior session left in
# settings.json is overwritten on the next install/push (the live dev1
# regression the #440 STEP 0 validation observed — a hand-flip to Fable did
# not survive the next push while this constant still carried the old id).
# The previous value was the 2026-07-25 cost-fix package's Opus 5 default;
# the full policy history lives in the fable-advisor skill.
# The `[1m]` suffix is a DELIBERATE part of the id, not a typo: it is how
# Claude Code's own usage tracking keys the 1M-context variant (verified —
# `lastModelUsage` entries in ~/.claude.json store ids exactly like
# `claude-fable-5[1m]`, distinct from the bare key) — kept so this change
# does NOT also shrink the context window. The user relies on the 1M window
# to avoid context-loss regressions. burn.tier("claude-fable-5[1m]") →
# "fable", so the statusline highlight keeps working unchanged.
MANAGED_MODEL = "claude-fable-5[1m]"

# Managed default subagent-spawn ceiling (#288, 2026-08-07): Claude Code's
# own default `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` is 200, and on CC
# builds up to 2.1.223 it is a CUMULATIVE per-session spawn cap, not a
# concurrency limit — every dispatch across the whole life of a session
# (workers, reviewers, ticket-validators, verifiers, TURBO parallel lanes)
# counts against it. A long-running `/goal`-armed autopilot session burns
# through 200 dispatches inside a single day and then loses the `Agent` tool
# entirely ("Subagent spawn limit reached (200 of 200 agents spawned)") —
# hit live on gatekeeper 2026-08-07 during a critical delivery push. Raised
# fleet-wide (no full-authority-only carve-out — the cap is authority-
# independent: reduced-authority sub-dev streams run equally long /goal
# loops and can hit it too). Confirmed the key is real (not guessed): the
# installed CC binary's own settings-`env` allowlist string table carries it
# in the same Set as `BASH_DEFAULT_TIMEOUT_MS`/`CLAUDE_CODE_MAX_RETRIES`/etc
# — a genuine, documented settings.json `env`-block key. Value is a STRING,
# like every other key in that block (env vars are always strings).
#
# VERSION-SCOPED, not universally effective (adversarial-review binary
# forensics, #288/#290): confirmed the cumulative-cap CHECK genuinely reads
# this env var on 2.1.222/2.1.223 — but Anthropic REMOVED the whole
# cumulative-cap mechanism in 2.1.224 (occurrence count of the enforcement
# code drops from 8 to 3, all 3 being the now-unread allowlist entry and its
# V8-snapshot copies; "Subagent spawn limit reached" / "agents spawned" both
# drop to 0 hits). The key is still ALLOWLISTED on 2.1.224+ (harmless to
# set, never an error) but nothing reads it any more — the only remaining
# launch-time bound there is `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`
# (concurrency, default 20 — a DIFFERENT variable). So this constant is a
# genuine, effective fix for any session on <=2.1.223 and a harmless no-op
# on 2.1.224+ — never a downside either way. Whether/how to react to
# upstream removing the mechanism entirely is tracked as its own decision,
# #290 — do not "fix" this constant based on that ticket alone without
# reading it first.
#
# Also note: this raises the deliberate trade explicitly, not silently —
# nothing else in this repo bounds a session's total subagent-dispatch
# count (the concurrency cap and per-repo cost dashboards are separate,
# unrelated instruments), so on <=2.1.223 this removes the only de-facto
# cumulative circuit-breaker in exchange for headroom against premature
# exhaustion. Deliberately accepted — the failure mode this fixes (losing
# the Agent tool mid-run) is worse than a runaway session eventually costing
# more, and the concurrency cap still bounds instantaneous load either way.
MANAGED_MAX_SUBAGENTS_PER_SESSION = "1000"

# #376: Claude Code's own installed binary (2.1.227) documents a NATIVE
# transcript-retention auto-cleanup in its Zod settings schema --
# `cleanupPeriodDays:it().int().positive().optional().describe("Number of
# days to retain chat transcripts before automatic cleanup (default: 30).
# ...")` -- confirmed by reading the binary directly, not guessed. A box
# with no explicit override (a fresh sub-dev stream account, e.g. david2,
# gatekeeper-provisioned 2026-08-08) is exposed to that 30-day default;
# dev1 only avoids it because someone set 365 manually, outside airuleset,
# which this repo's own history has zero record of (never airuleset-
# managed, so it would never propagate to any other box). The VALUE is CC's
# own suggested one, quoted verbatim from its own "too_small" validation
# tip string ("cleanupPeriodDays must be at least 1. ... set a large number
# (e.g. 3650 for ~10 years) ...") -- never an invented number. Same
# unconditional-managed-default treatment as every other key in
# apply_managed_settings_defaults: a managed box always gets this on the
# next install, even one already carrying a smaller manual value.
MANAGED_CLEANUP_PERIOD_DAYS = 3650

# #376: fleet-managed `tui` setting.json pin -- "fullscreen" (alt-screen
# renderer), confirmed against the installed CC binary and Anthropic's own
# docs (code.claude.com/docs/en/fullscreen) as one of exactly two accepted
# values ("classic" | "fullscreen"). See apply_managed_settings_defaults'
# own docstring bullet for the full history/tradeoff this REVERSES a prior
# `"default"` (classic) pin for.
MANAGED_TUI = "fullscreen"

# REVERTED (2026-07-25 correction batch, same day it was added): a managed
# `MANAGED_AUTOCOMPACT_WINDOW = 300000` ("krok 1c") briefly capped the
# auto-compact threshold. The user's call, which overrides that decision:
# a LOW auto-compact threshold cuts big tasks off MID-WORK and defeats the
# entire point of the 1M context window — compaction should never fire on
# an artificial token budget. Context is bounded at SAFE BOUNDARIES instead
# — the per-ticket `✅ DONE` completion report + the ticket-boundary
# `/compact` (watchdog job 14 — see `notify-compact-request.sh` and
# `milestone-notifications.md`) for autopilot-style sessions, AND, for a
# long-lived session that never reports a ticket, an IDLE-based backstop
# (watchdog job 15, #39/#43 follow-up: a session whose context exceeds
# 400K tokens AND has sat genuinely idle >= 20 minutes — no draft, no
# worker in flight — gets `/compact`'d automatically) — never by a blanket
# token window that could fire mid-work. No replacement constant:
# `apply_managed_settings_defaults` now actively STRIPS `autoCompactWindow`
# from settings.json on every deploy so the 6 managed boxes go back to
# Claude Code's own default.

UNIVERSAL_PROFILE = REPO_DIR / "profiles" / "universal.profile"

# ---------------------------------------------------------------------------
# File-Drop + api-watchdog systemd legs -- extracted to cli_filedrop_watchdog.py
# (#433 cluster L-B). ONE facade re-export at the earliest removed block's site:
# the filedrop-package constants/helpers, the FILEDROP_/WATCHDOG_ service-unit
# paths, and the 17 install/CLI functions all resolve here as bare airuleset.py
# names (late binding) for cmd_install, SUBCOMMANDS, _validate_filedrop + tests.
# ---------------------------------------------------------------------------
from cli_filedrop_watchdog import (  # noqa: E402
    FILEDROP_PORT as FILEDROP_PORT,
    FILEDROP_DEFAULT_PORT as FILEDROP_DEFAULT_PORT,
    FILEDROP_PORT_FILE as FILEDROP_PORT_FILE,
    filedrop_persisted_port as filedrop_persisted_port,
    filedrop_default_port_for_uid as filedrop_default_port_for_uid,
    filedrop_host_ip as filedrop_host_ip,
    filedrop_bind_ips as filedrop_bind_ips,
    filedrop_url as filedrop_url,
    FILEDROP_DIR as FILEDROP_DIR,
    FILEDROP_SERVICE_TEMPLATE as FILEDROP_SERVICE_TEMPLATE,
    FILEDROP_SERVICE_DEST as FILEDROP_SERVICE_DEST,
    WATCHDOG_SERVICE_TEMPLATE as WATCHDOG_SERVICE_TEMPLATE,
    WATCHDOG_TIMER_TEMPLATE as WATCHDOG_TIMER_TEMPLATE,
    WATCHDOG_SERVICE_DEST as WATCHDOG_SERVICE_DEST,
    WATCHDOG_TIMER_DEST as WATCHDOG_TIMER_DEST,
    _xdg_runtime_env as _xdg_runtime_env,
    _run_systemctl as _run_systemctl,
    _whoami as _whoami,
    _render_filedrop_unit as _render_filedrop_unit,
    _choose_filedrop_port as _choose_filedrop_port,
    _filedrop_is_live as _filedrop_is_live,
    _wait_filedrop_live as _wait_filedrop_live,
    _restart_filedrop_service as _restart_filedrop_service,
    setup_filedrop_service as setup_filedrop_service,
    maybe_setup_filedrop as maybe_setup_filedrop,
    _filedrop_serve as _filedrop_serve,
    cmd_share as cmd_share,
    _filedrop_status as _filedrop_status,
    cmd_filedrop as cmd_filedrop,
    watchdog_disable_marker as watchdog_disable_marker,
    setup_watchdog_service as setup_watchdog_service,
    maybe_setup_watchdog as maybe_setup_watchdog,
)

# --- web terminal gateway (#555): dev1-only ttyd + tailscale-serve brána.
# cmd_install calls maybe_setup_webterm() (no-op off dev1). ---
from cli_webterm import (  # noqa: E402
    maybe_setup_webterm as maybe_setup_webterm,
    setup_webterm_service as setup_webterm_service,
    webterm_inventory as webterm_inventory,
)

# --- webterm Cloudflare Access (#612): email-OTP gate in front of the public
# david gateway; declared allow-list applied idempotently via the Access API. ---
from cli_webterm_access import (  # noqa: E402
    cmd_webterm_access as cmd_webterm_access,
)
from cli_drop_gateway import (  # noqa: E402  #664 public-TLS drop lane
    cmd_drop_gateway as cmd_drop_gateway,
)


# Skills directories in the repo that should be symlinked
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check", "autopilot", "autopilot-dialog", "mutation-sweep", "meeting-analysis", "playbook-review", "playbook-cleanup", "mutation-testing", "local-builds", "batch-issue-development", "view-image-urls", "version-on-dashboard", "process-subdev", "autopilot-master", "fable-advisor",
               # Ruleset trim wave 2 (#37, 2026-07-25) — situational always-on
               # modules moved VERBATIM to hidden (user-invocable: false)
               # on-demand skills. See test_ruleset_conversion_wave2.py.
               "subagent-type-discipline", "verify-issue-still-valid", "investigate-existing-first",
               "post-deploy-verification", "regression-test-first", "ci-push-discipline",
               "comprehensive-logging", "verify-launched-work-liveness", "pr-merge-policy",
               "deliver-files-as-urls", "notification-mechanics", "cloudflare-api-tokens",
               # #420 (2026-08-14) — adopt claude-code-log (external, maintained)
               # for transcript browsing/HTML export; hidden on-demand skill,
               # deploys everywhere. Documents the #410 gzip interplay (reads
               # only plain .jsonl; claude-history is the gzip-aware fallback).
               "claude-code-log",
               # #95 item 9 (2026-08-09) — the STREDNÁ CESTA split of
               # user-questions-slovak.md: long template/examples moved
               # VERBATIM to this hidden, on-demand skill, auto-loaded via
               # the AskUserQuestion PreToolUse matcher for every stream
               # (every box asks questions, so this deploys everywhere,
               # like its "Ruleset trim wave 2" siblings above).
               "user-questions-slovak",
               # #465 (2026-08-14) — the verified Odoo Discuss XML-RPC
               # message_post recipe (body_is_html=True, post-then-rewrite
               # BANNED). FREEZE-compliant surface for #464 (a new hook is
               # banned); hidden on-demand, deploys everywhere at zero
               # slash-noise cost, description-triggered when a session
               # posts to an Odoo Discuss channel over XML-RPC.
               "odoo-discuss-xmlrpc",
               # #569 (2026-08-19) — thin wrapper over `airuleset.py
               # onboard-project`; all onboarding logic lives in the CLI
               # (cli_onboard.py), the skill just invokes + reports. Deploys
               # everywhere so any box can onboard a project the SAME way.
               "onboard-project"]

# --- Per-box skill scoping (user complaint 2026-07-11: "slash cmd by nemali byt
# vsetky vsade ale len relevantne k danemu projektu") ---
# Skills deploy per USER at install time; every user-invocable skill shows in that
# box's slash-command list, so an irrelevant skill is pure noise there. Two scopes:
#   MAINTAINER_ONLY — relevant only on the airuleset-maintainer's own boxes
#     (newlevel@dev1/dev2): airuleset self-maintenance (mdreview, rules-audit),
#     his personal workflows (meeting-analysis), his projects' tooling
#     (windows-remote-gui = win-* MCP rigs, fast-iterate + mutation-sweep = his
#     Rust/mutation-era repos). Sub-dev / gatekeeper boxes never invoke these.
#   FULL_AUTHORITY_ONLY — deploys are OUTSIDE a reduced-authority stream's job
#     (pr-merge-policy scope), so deploy-ssh stays off david/marek/montalu boxes.
# Hidden on-demand skills (user-invocable: false — mutation-testing, local-builds,
# batch-issue-development, view-image-urls, version-on-dashboard) deploy EVERYWHERE:
# rule stubs point at them and they never appear in the slash list, so they cost
# nothing. install prunes previously-linked skills that fall outside the box's set.
SKILLS_MAINTAINER_ONLY = {"mdreview", "rules-audit", "meeting-analysis",
                          "mutation-sweep", "windows-remote-gui", "fast-iterate"}
SKILLS_FULL_AUTHORITY_ONLY = {"deploy-ssh", "process-subdev", "autopilot-master"}
MAINTAINER_USERS = {"newlevel"}
# Per-user re-grants: a scoped-away skill that IS relevant on one specific box
# (montalu meeting recordings get analyzed IN that stream's session — the
# 2026-07-14 incident where the scoping prune took /meeting-analysis off montalu).
# montalu2/3/4 (airuleset#251): three MORE full parallel montalu streams,
# same working style — same re-grant.
SKILLS_EXTRA_BY_USER = {
    # montalu1 is the renamed base montalu stream (#537, live 2026-08-19) —
    # it keeps the meeting-analysis re-grant the base montalu had.
    "montalu1": {"meeting-analysis"},
    "montalu2": {"meeting-analysis"},
    "montalu3": {"meeting-analysis"},
    "montalu4": {"meeting-analysis"},
    # montalu5/6/7/8 (airuleset#378): four MORE full parallel montalu
    # streams, same re-grant.
    "montalu5": {"meeting-analysis"},
    "montalu6": {"meeting-analysis"},
    "montalu7": {"meeting-analysis"},
    "montalu8": {"meeting-analysis"},
}

# --- #433 cluster L-A: skill_names_for_user (the per-box skill SUBSET
# selector) moved to cli_deployer_glue.py — re-exported at the L-A facade
# below (after MANAGED_DISABLED_PLUGINS). It reaches the resident SKILL_NAMES
# / SKILLS_MAINTAINER_ONLY / SKILLS_FULL_AUTHORITY_ONLY / SKILLS_EXTRA_BY_USER
# / MAINTAINER_USERS / AUTHORITY_BY_USER registries (kept resident) via a
# deferred `import airuleset`.

# --- caveman plugin wiring + managed baseline plugin provisioning —
#     extracted to cli_caveman_plugins.py (#433 cluster L-C) ---
# F401: facade re-export — several names (setup_caveman/_plugin_registry_keys/
# the plugin constants) are consumed by the test-suite via `airuleset.<name>`,
# and cmd_install calls maybe_setup_caveman()/setup_managed_plugins() bare-name.
from cli_caveman_plugins import (  # noqa: E402, F401
    caveman_mode_or_default,
    _caveman_plugin_built,
    setup_caveman,
    maybe_setup_caveman,
    reconcile_managed_plugins,
    _plugin_registry_keys,
    _managed_plugin_built,
    _playwright_browsers_installed,
    ensure_playwright_browsers,
    _reconcile_settings_file,
    setup_managed_plugins,
    CAVEMAN_PLUGIN_KEY,
    CAVEMAN_DEFAULT_MODE,
    VALID_CAVEMAN_MODES,
    MANAGED_PLUGINS,
    MANAGED_DISABLED_PLUGINS,
    PLAYWRIGHT_PLUGIN_KEY,
    PLAYWRIGHT_BROWSER_CACHE,
)
CAVEMAN_SHIM_DEST = CLAUDE_DIR / "airuleset-caveman-statusline.sh"
CAVEMAN_MODE_FILE = CLAUDE_DIR / ".caveman-active"
# --- #433 cluster L-A: the statusline-shim RENDERING, marketplace-
# registration glue, and per-box skill-subset selector moved VERBATIM to
# cli_deployer_glue.py — re-exported here so every existing reference
# (setup_caveman's render_caveman_shim() write, setup_managed_plugins'
# _marketplace_names_for / MARKETPLACE_SOURCES / ensure_marketplace_registered
# / CAVEMAN_MARKETPLACE_REPO calls, install/diff's skill_names_for_user()
# subset, and every test's airuleset.X) keeps resolving through this module
# unchanged. CAVEMAN_STATUSLINE_COMMAND stays resident just below — a module-
# level f-string over the resident CAVEMAN_SHIM_DEST path (the caveman WIRING
# that CONSUMES the shim is #433 step L-C).
from cli_deployer_glue import (  # noqa: E402
    skill_names_for_user as skill_names_for_user,
    CAVEMAN_MARKETPLACE_REPO as CAVEMAN_MARKETPLACE_REPO,
    OFFICIAL_MARKETPLACE_SOURCE as OFFICIAL_MARKETPLACE_SOURCE,
    MARKETPLACE_SOURCES as MARKETPLACE_SOURCES,
    _marketplace_names_for as _marketplace_names_for,
    ensure_marketplace_registered as ensure_marketplace_registered,
    CAVEMAN_SHIM_CONTENT as CAVEMAN_SHIM_CONTENT,
    render_caveman_shim as render_caveman_shim,
)
CAVEMAN_STATUSLINE_COMMAND = f'bash "{CAVEMAN_SHIM_DEST}"'

# Subagent definitions (single .md files) symlinked into ~/.claude/agents/
AGENT_NAMES = ["autopilot-worker", "ticket-validator"]

HOOKS_JSON = REPO_DIR / "settings" / "hooks.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# --- config-authoring + validate/diff logic --- extracted to cli_config.py
# (#433 cluster L-D). F401: facade re-export -- several of these names are
# consumed only by the test-suite via `airuleset.<name>` (cmd_install /
# cmd_status / SUBCOMMANDS / provision_subdev_soniox_key call the rest through
# this facade), not by resident airuleset.py code. E402: import intentionally
# at the original block site, not top-of-file.
from cli_config import (  # noqa: E402, F401
    parse_profile as parse_profile,
    categorize_entries as categorize_entries,
    symlink_global_rules as symlink_global_rules,
    generate_claude_md as generate_claude_md,
    preserve_external_blocks as preserve_external_blocks,
    load_hooks_json as load_hooks_json,
    merge_hooks_into_settings as merge_hooks_into_settings,
    apply_managed_settings_defaults as apply_managed_settings_defaults,
    read_file_safe as read_file_safe,
    unified_diff as unified_diff,
    _validate_filedrop as _validate_filedrop,
    _validate_tmux_cutover as _validate_tmux_cutover,
    _validate_watchdog as _validate_watchdog,
    cmd_validate as cmd_validate,
    cmd_diff as cmd_diff,
)


BASHRC = Path.home() / ".bashrc"
ULTRACODE_MARK_START = "# >>> airuleset: ultracode default >>>"
ULTRACODE_MARK_END = "# <<< airuleset: ultracode default <<<"


# --- claude launcher + history-viewer scripts + ~/.bashrc appliers --- extracted to
# cli_claude_scripts.py + cli_bashrc_appliers.py (#433 cluster L-F). F401: facade
# re-export -- several of these names are consumed only by the test-suite via
# `airuleset.<name>`, not by airuleset.py's own resident code.
from cli_claude_scripts import (  # noqa: E402, F401
    CLAUDE_LAUNCH_SCRIPT_DEST as CLAUDE_LAUNCH_SCRIPT_DEST,
    CLAUDE_LAUNCH_SCRIPT_CONTENT as CLAUDE_LAUNCH_SCRIPT_CONTENT,
    render_claude_launch_script as render_claude_launch_script,
    encode_project_dir as encode_project_dir,
    CLAUDE_HISTORY_SCRIPT_DEST as CLAUDE_HISTORY_SCRIPT_DEST,
    CLAUDE_HISTORY_SCRIPT_CONTENT as CLAUDE_HISTORY_SCRIPT_CONTENT,
    render_claude_history_script as render_claude_history_script,
    CLAUDE_HISTORY_POPUP_SCRIPT_DEST as CLAUDE_HISTORY_POPUP_SCRIPT_DEST,
    CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT as CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT,
    render_claude_history_popup_script as render_claude_history_popup_script,
)
from cli_bashrc_appliers import (  # noqa: E402, F401
    ULTRACODE_BASHRC_BLOCK as ULTRACODE_BASHRC_BLOCK,
    apply_ultracode_launcher as apply_ultracode_launcher,
    STREAM_DEV_CWD_REL as STREAM_DEV_CWD_REL,
    STREAM_DEV_CWD_CHAIN as STREAM_DEV_CWD_CHAIN,
    STREAM_SSH_ATTACH_MARK_START as STREAM_SSH_ATTACH_MARK_START,
    STREAM_SSH_ATTACH_MARK_END as STREAM_SSH_ATTACH_MARK_END,
    STREAM_SSH_ATTACH_BLOCK as STREAM_SSH_ATTACH_BLOCK,
    _stream_marker_block_spans as _stream_marker_block_spans,
    apply_stream_ssh_attach as apply_stream_ssh_attach,
    is_single_session_box_user as is_single_session_box_user,
    TMUX_ATTACH_MARK_START as TMUX_ATTACH_MARK_START,
    TMUX_ATTACH_MARK_END as TMUX_ATTACH_MARK_END,
    render_tmux_attach_block as render_tmux_attach_block,
    _owner_session_default as _owner_session_default,
    apply_tmux_attach_helpers as apply_tmux_attach_helpers,
    OWNER_VPS_SSH_ATTACH_MARK_START as OWNER_VPS_SSH_ATTACH_MARK_START,
    OWNER_VPS_SSH_ATTACH_MARK_END as OWNER_VPS_SSH_ATTACH_MARK_END,
    OWNER_VPS_PROJECTS as OWNER_VPS_PROJECTS,
    render_owner_vps_ssh_attach_block as render_owner_vps_ssh_attach_block,
    _owner_vps_project as _owner_vps_project,
    apply_owner_vps_ssh_attach as apply_owner_vps_ssh_attach,
)


# --- tmux.conf / tmux-server provisioning — extracted to cli_tmux_provisioning.py (#433 cluster L) ---
# F401: this is a facade re-export — several of these names (the internal
# helpers + constants) are consumed only by the test-suite via
# `airuleset.<name>`, not by airuleset.py's own resident code.
from cli_tmux_provisioning import (  # noqa: E402, F401
    TMUX_CONF,
    TMUX_HISTORY_LIMIT,
    TMUX_DEFAULT_SIZE,
    TMUX_WINDOW_SIZE,
    _MIN_WINDOW_SIZE_MANUAL_VERSION,
    TMUX_MARK_START,
    TMUX_MARK_END,
    TMUX_SCROLLBACK_KEYBINDS,
    TMUX_POPUP_PREFIX_KEY,
    TMUX_POPUP_BIND_ARGVS,
    TMUX_CHOOSE_TREE_BIND_ARGVS,
    TMUX_CUTOVER_UNIT_NAME,
    TMUX_CUTOVER_SCRIPT_DEST,
    TMUX_CUTOVER_SERVICE_DEST,
    TMUX_CUTOVER_SERVICE_TEMPLATE,
    TMUX_CUTOVER_NEWEST,
    TMUX_CUTOVER_SCRIPT_CONTENT,
    SUBDEV_ADMIN_IDENTITY,
    _tmux_popup_bind_argv,
    _tmux_conf_quote,
    _parse_tmux_version,
    _tmux_supports_window_size_manual,
    render_tmux_history_block,
    _clean_tmux_block_spans,
    _default_tmux_run,
    converge_tmux_window_geometry,
    apply_tmux_history_limit,
    STREAM_TMUX_WINDOW_MARK_START,
    STREAM_TMUX_WINDOW_MARK_END,
    render_stream_tmux_window_block,
    _live_revert_stream_window_name,
    _live_normalize_owner_session,
    _owner_box_stray_name_res,
    apply_stream_tmux_window_name,
    apply_owner_session_created_audit,
    _sudo_write_root_file,
    setup_tmux_cutover_provisioning,
    setup_tmux_cutover_subdev_via_gatekeeper,
)


# apply_managed_settings_defaults / read_file_safe / unified_diff moved to
# cli_config.py (#433 cluster L-D) -- re-exported via the facade above.


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


# _validate_filedrop / _validate_tmux_cutover / _validate_watchdog / cmd_validate
# / cmd_diff moved to cli_config.py (#433 cluster L-D) -- re-exported via the
# facade above; SUBCOMMANDS['validate'/'diff'] bind the re-exports.


# Binaries the deployed surface depends on at RUNTIME: jq + curl (every
# notify/stop hook parses its stdin payload with jq and sends via curl), git +
# gh (tickets-status, autopilot, bounce), tmux (watchdog pane jobs), sshpass
# (the burn-metrics remote-ssh helpers, #98 — was used by the code but never
# tracked here, so a box missing it was never auto-installed or warned about).
# A box missing one degrades SILENTLY at hook time — subdev 2026-07-23: provisioned
# without jq, so david's ❓ never pinged Discord, never entered the question
# map and the statusline badge stayed empty. The check AUTO-INSTALLS the gap
# (user directive 2026-07-24: 'ak ti nieco chyba mas to doinstalovat') and
# re-verifies; only a box where the install itself fails (no sudo — the
# isolated sub-dev users) keeps the LOUD warning in every install/push output
# (per-machine gaps are invisible to git-deploy — the gatekeeper .env lesson).
# A sudo-less box that hits a STILL-missing dep files a gk-request naming the
# package (autonomous-verification.md's sudo-less branch, #98); fulfilling it
# means adding the package HERE and running push, which installs + verifies
# it on every target in one shot.
# Push runs install on EVERY target, so every deploy verifies + heals the
# whole fleet's toolset.
#
# node/npx (#158): the managed Playwright plugin's MCP server needs a real
# node/npx runtime, never tracked here before.
#
# less (#289): the claude-history popup (TMUX_POPUP_BIND_ARGVS) pipes into
# `less +G` for paging -- verified present on every box checked so far
# (dev1's own `dpkg -l` shows it as an ordinary Ubuntu base-image package,
# not a nice-to-have), but never TRACKED here before, so a box that somehow
# lacks it would silently break the popup with zero warning. This is the
# sanctioned mechanism (autonomous-verification.md) for closing that gap.
RUNTIME_DEPS = ("jq", "curl", "git", "gh", "tmux", "sshpass", "btop",
                 "node", "npx", "less", "pdftoppm")
# NOTE (#600): `pdftoppm` (from the `poppler-utils` apt package, see
# RUNTIME_DEP_PACKAGE below) IS fleet-wide — Claude's Read tool shells out to
# it to render PDF pages, a universally useful capability on EVERY box, and a
# no-sudo subdev box (montalu1) lacking it is exactly the gap that filed this
# ticket (odoo-erp#4634). Unlike `ttyd` (below), a warning on a box that lacks
# it is a TRUE signal — the dep is genuinely wanted everywhere — so it belongs
# in the flat fleet-wide list alongside jq/btop/less/node, not dev1-local.
# NOTE (#555): `ttyd` is NOT in the fleet-wide RUNTIME_DEPS — the web terminal
# gateway is dev1-ONLY, and `check_runtime_deps()` runs on EVERY box (incl. the
# ~19 no-sudo subdev accounts, where a `sudo -n apt-get install ttyd` would fail
# every install and cry wolf on the "MISSING RUNTIME DEP" channel). It is
# installed dev1-locally inside setup_webterm_service() instead.

# The apt PACKAGE name differs from the BINARY name for node/npx (#158) and
# pdftoppm (#600):
# Debian/Ubuntu's real "node" package is an unrelated amateur packet-radio
# program (installing it would never provide the `node` binary at all), and
# `npx` has no package of its own — both ship bundled inside "nodejs" (this
# fleet's own NodeSource package, confirmed live to explicitly `Replaces:
# npm`, i.e. npm+npx included). The `pdftoppm` binary (Claude's Read tool
# shells out to it to render PDF pages) ships inside "poppler-utils",
# alongside its pdftotext/pdfinfo siblings — there is no apt package named
# "pdftoppm". Every other tracked dep's binary name IS its apt package name,
# so this override only needs these three exceptions.
RUNTIME_DEP_PACKAGE = {"node": "nodejs", "npx": "nodejs",
                       "pdftoppm": "poppler-utils"}


def check_runtime_deps(deps=RUNTIME_DEPS):
    """Auto-install each missing runtime binary (sudo -n apt-get, non-
    interactive) and re-verify; a failed install (no sudo) prints the LOUD
    warning instead. Returns the still-missing list (never fatal — install
    proceeds, the gap stays visible)."""
    import shutil
    import subprocess
    still = []
    # Memoized per apt PACKAGE (not per binary): node/npx both resolve to
    # "nodejs" via RUNTIME_DEP_PACKAGE, so if both are missing this makes
    # sure `apt-get install -y nodejs` (and its warning, on failure) fires
    # only ONCE for the pair, not twice (#158 review).
    pkg_install_ok = {}
    for d in deps:
        if shutil.which(d):
            continue
        pkg = RUNTIME_DEP_PACKAGE.get(d, d)
        if pkg not in pkg_install_ok:
            try:
                r = subprocess.run(["sudo", "-n", "apt-get", "install", "-y", pkg],
                                   capture_output=True, text=True, timeout=300)
                pkg_install_ok[pkg] = r.returncode == 0
            except Exception:
                pkg_install_ok[pkg] = False
        # Re-verify by the BINARY name every time, even on a memoized package
        # hit — the apt install can succeed while a SPECIFIC binary a
        # package claims to provide is still missing (e.g. a non-NodeSource
        # "nodejs" build that doesn't bundle npm/npx).
        ok = pkg_install_ok[pkg] and shutil.which(d)
        if ok:
            print("  ✓ runtime dep '%s' was missing — auto-installed "
                  "(apt-get) and verified." % d)
        else:
            still.append(d)
            print("  ⚠ MISSING RUNTIME DEP: '%s' is not installed on this box "
                  "and auto-install failed (no sudo?) — hooks/notify/watchdog "
                  "will degrade SILENTLY. Install it as root: apt-get install "
                  "%s." % (d, pkg))
    return still


FLEET_TIMEZONE = "Europe/Bratislava"


def ensure_timezone(want=FLEET_TIMEZONE):
    """Enforce the fleet system timezone on THIS box at install time (#387).

    The user is in Slovakia; a box left on its OS-image default (Hetzner ships
    Etc/UTC) makes every Claude session and `date` report UTC, which the user
    has flagged repeatedly as a hard regression (gatekeeper VPS, 2026-08-11).
    Idempotent + self-healing, run on every box on every `airuleset.py push`:
    set via `sudo -n timedatectl` where sudo exists, print a LOUD warning
    (never fatal) where it does not -- exactly like check_runtime_deps. Returns
    the timezone now in effect (the still-wrong value if it could not be set)."""
    import subprocess

    def _cur():
        try:
            r = subprocess.run(
                ["timedatectl", "show", "-p", "Timezone", "--value"],
                capture_output=True, text=True, timeout=15)
            return (r.stdout or "").strip()
        except Exception:
            return ""

    cur = _cur()
    if cur == want:
        return cur                        # already correct -- silent no-op
    try:
        r = subprocess.run(["sudo", "-n", "timedatectl", "set-timezone", want],
                           capture_output=True, text=True, timeout=30)
        set_ok = r.returncode == 0
    except Exception:
        set_ok = False
    if set_ok and _cur() == want:
        print("  ✓ timezone was '%s' -- set to %s and verified." % (cur or "unknown", want))
        return want
    print("  ⚠ TIMEZONE is '%s', not %s -- could not set it automatically "
          "(no sudo?). This box will report wrong (e.g. UTC) times to Claude "
          "and `date`. Set it as root: timedatectl set-timezone %s."
          % (cur or "unknown", want, want))
    return cur


# --- Tier-0 target/ retention (#315) — extracted to cli_target_purge.py (#433 cluster L2) ---
from cli_target_purge import (  # noqa: E402
    TARGET_PURGE_LOG_PATH as TARGET_PURGE_LOG_PATH,
    TARGET_PURGE_STATE_PATH as TARGET_PURGE_STATE_PATH,
    TARGET_PURGE_MAX_AGE_DAYS_DEFAULT as TARGET_PURGE_MAX_AGE_DAYS_DEFAULT,
    TARGET_PURGE_MIN_INTERVAL_S as TARGET_PURGE_MIN_INTERVAL_S,
    _TARGET_PURGE_SKIP_DIRS as _TARGET_PURGE_SKIP_DIRS,
    _human_size as _human_size,
    _disk_usage_summary_line as _disk_usage_summary_line,
    discover_target_purge_candidates as discover_target_purge_candidates,
    _tier0_via_hook as _tier0_via_hook,
    _target_in_live_use as _target_in_live_use,
    _dir_stats as _dir_stats,
    _log_target_purge_results as _log_target_purge_results,
    purge_stale_tier0_targets as purge_stale_tier0_targets,
    cmd_purge_targets as cmd_purge_targets,
)


# --- Stale worktree sweep (#345/#348) — extracted to cli_worktree_sweep.py (#433 cluster L) ---
from cli_worktree_sweep import (  # noqa: E402
    STALE_WORKTREE_LOG_PATH as STALE_WORKTREE_LOG_PATH,
    STALE_WORKTREE_STATE_PATH as STALE_WORKTREE_STATE_PATH,
    STALE_WORKTREE_MIN_INTERVAL_S as STALE_WORKTREE_MIN_INTERVAL_S,
    STALE_WORKTREE_REMOVE_TIMEOUT_S as STALE_WORKTREE_REMOVE_TIMEOUT_S,
    _STALE_WORKTREE_PROTECTED_BRANCHES as _STALE_WORKTREE_PROTECTED_BRANCHES,
    STALE_ORPHAN_BRANCH_MIN_AGE_S as STALE_ORPHAN_BRANCH_MIN_AGE_S,
    STALE_LOCKED_DEAD_MIN_AGE_S as STALE_LOCKED_DEAD_MIN_AGE_S,
    STALE_WORKTREE_IDLE_MIN_AGE_S as STALE_WORKTREE_IDLE_MIN_AGE_S,
    _worktree_env_age_s as _worktree_env_age_s,
    _worktree_git as _worktree_git,
    _worktree_porcelain_entries as _worktree_porcelain_entries,
    _worktree_sweep_base_branch as _worktree_sweep_base_branch,
    _WORKTREE_LOCK_PID_RX as _WORKTREE_LOCK_PID_RX,
    _worktree_lock_pid as _worktree_lock_pid,
    _proc_stat_text as _proc_stat_text,
    _pid_is_dead as _pid_is_dead,
    _worktree_admin_dir as _worktree_admin_dir,
    _worktree_lock_age_s as _worktree_lock_age_s,
    _worktree_is_clean as _worktree_is_clean,
    _worktree_recency_age_s as _worktree_recency_age_s,
    _worktree_in_live_use as _worktree_in_live_use,
    _classify_locked_worktree as _classify_locked_worktree,
    _worktree_branch_ref_age_s as _worktree_branch_ref_age_s,
    discover_orphaned_worktree_branches as discover_orphaned_worktree_branches,
    discover_stale_worktrees as discover_stale_worktrees,
    discover_salvage_worktrees as discover_salvage_worktrees,
    _log_stale_worktree_results as _log_stale_worktree_results,
    sweep_stale_worktrees as sweep_stale_worktrees,
    cmd_sweep_worktrees as cmd_sweep_worktrees,
    LANE_TARGET_LOG_PATH as LANE_TARGET_LOG_PATH,
    LANE_TARGET_STATE_PATH as LANE_TARGET_STATE_PATH,
    LANE_TARGET_MIN_INTERVAL_S as LANE_TARGET_MIN_INTERVAL_S,
    LANE_TARGET_MERGED_MIN_IDLE_S as LANE_TARGET_MERGED_MIN_IDLE_S,
    _branch_reflog_has_authored_commit as _branch_reflog_has_authored_commit,
    _iter_lane_target_dirs as _iter_lane_target_dirs,
    _log_lane_target_results as _log_lane_target_results,
    purge_merged_lane_targets as purge_merged_lane_targets,
    cmd_purge_lane_targets as cmd_purge_lane_targets,
)


# --- Old Claude CLI binary sweep (#355) — extracted to cli_target_purge.py (#433 cluster L2) ---
from cli_target_purge import (  # noqa: E402
    CLI_VERSION_LOG_PATH as CLI_VERSION_LOG_PATH,
    CLI_VERSION_STATE_PATH as CLI_VERSION_STATE_PATH,
    CLI_VERSION_MIN_INTERVAL_S as CLI_VERSION_MIN_INTERVAL_S,
    CLI_VERSION_MIN_AGE_DAYS_DEFAULT as CLI_VERSION_MIN_AGE_DAYS_DEFAULT,
    _CLI_VERSION_NAME_RX as _CLI_VERSION_NAME_RX,
    _min_age_days_env as _min_age_days_env,
    _cli_versions_dir as _cli_versions_dir,
    _cli_version_key as _cli_version_key,
    _resolve_current_cli_version as _resolve_current_cli_version,
    discover_cli_version_candidates as discover_cli_version_candidates,
    _log_cli_version_sweep_results as _log_cli_version_sweep_results,
    sweep_stale_cli_versions as sweep_stale_cli_versions,
    cmd_sweep_cli_versions as cmd_sweep_cli_versions,
)


# --- Claude scratch/tmp sweep (#355) — extracted to cli_scratch_sweep.py (#433 cluster L2) ---
from cli_scratch_sweep import (  # noqa: E402
    CLAUDE_SCRATCH_LOG_PATH as CLAUDE_SCRATCH_LOG_PATH,
    CLAUDE_SCRATCH_STATE_PATH as CLAUDE_SCRATCH_STATE_PATH,
    CLAUDE_SCRATCH_MIN_INTERVAL_S as CLAUDE_SCRATCH_MIN_INTERVAL_S,
    CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT as CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT,
    _claude_scratch_root as _claude_scratch_root,
    discover_claude_scratch_candidates as discover_claude_scratch_candidates,
    _classify_scratch_entry as _classify_scratch_entry,
    discover_stray_worktree_tmp_candidates as discover_stray_worktree_tmp_candidates,
    _log_claude_scratch_results as _log_claude_scratch_results,
    sweep_claude_scratch as sweep_claude_scratch,
    cmd_sweep_claude_scratch as cmd_sweep_claude_scratch,
    _TMP_MKDTEMP_RX as _TMP_MKDTEMP_RX,
    TMP_STRAY_LOG_PATH as TMP_STRAY_LOG_PATH,
    TMP_STRAY_STATE_PATH as TMP_STRAY_STATE_PATH,
    TMP_STRAY_MIN_INTERVAL_S as TMP_STRAY_MIN_INTERVAL_S,
    TMP_STRAY_MAX_SCAN_DEFAULT as TMP_STRAY_MAX_SCAN_DEFAULT,
    TMP_STRAY_LIVE_ENV as TMP_STRAY_LIVE_ENV,
    _scan_live_tmp_tops as _scan_live_tmp_tops,
    discover_stray_tmp_candidates as discover_stray_tmp_candidates,
    _log_tmp_stray_summary as _log_tmp_stray_summary,
    sweep_stray_tmp as sweep_stray_tmp,
    cmd_sweep_stray_tmp as cmd_sweep_stray_tmp,
    _AIRULESET_STATE_RX as _AIRULESET_STATE_RX,
    AIRULESET_STATE_EXCLUDE_PREFIXES as AIRULESET_STATE_EXCLUDE_PREFIXES,
    AIRULESET_STATE_LOG_PATH as AIRULESET_STATE_LOG_PATH,
    AIRULESET_STATE_STATE_PATH as AIRULESET_STATE_STATE_PATH,
    AIRULESET_STATE_MIN_AGE_DAYS_DEFAULT as AIRULESET_STATE_MIN_AGE_DAYS_DEFAULT,
    AIRULESET_STATE_LIVE_ENV as AIRULESET_STATE_LIVE_ENV,
    discover_stray_airuleset_state_candidates as discover_stray_airuleset_state_candidates,
    sweep_airuleset_state as sweep_airuleset_state,
)


# --- Transcript gzip-at-rest retention (#410) — extracted to cli_scratch_sweep.py (#433 cluster L2) ---
from cli_scratch_sweep import (  # noqa: E402
    TRANSCRIPT_COMPRESS_LOG_PATH as TRANSCRIPT_COMPRESS_LOG_PATH,
    TRANSCRIPT_COMPRESS_STATE_PATH as TRANSCRIPT_COMPRESS_STATE_PATH,
    TRANSCRIPT_COMPRESS_MIN_INTERVAL_S as TRANSCRIPT_COMPRESS_MIN_INTERVAL_S,
    TRANSCRIPT_COMPRESS_MIN_AGE_DAYS_DEFAULT as TRANSCRIPT_COMPRESS_MIN_AGE_DAYS_DEFAULT,
    TRANSCRIPT_COMPRESS_MIN_SIZE_BYTES_DEFAULT as TRANSCRIPT_COMPRESS_MIN_SIZE_BYTES_DEFAULT,
    _claude_projects_dir as _claude_projects_dir,
    _min_size_bytes_env as _min_size_bytes_env,
    discover_old_transcript_candidates as discover_old_transcript_candidates,
    _compress_transcript_file as _compress_transcript_file,
    _log_transcript_compress_results as _log_transcript_compress_results,
    sweep_old_transcripts as sweep_old_transcripts,
    cmd_sweep_transcripts as cmd_sweep_transcripts,
    _run_transcript_compress_step as _run_transcript_compress_step,
)


def _record_conformance_baseline_step(claude_md_content, record_fn=None):
    """cmd_install step 1b (#535): record the ``{claude_md_md5, head_sha}`` baseline
    the per-box conformance check (watchdog job 34) reads. Called AFTER CLAUDE.md is
    written, with the EXACT bytes on disk, so the md5 and the file agree atomically
    and the recorded HEAD lets the check skip the md5 dimension on a mid-push box
    (repo advanced but install not yet re-run) — the mid-push false-alarm immunity.

    Extracted with an injectable ``record_fn`` (#410-F2) so the wiring is testable
    without a real install; best-effort (``record_conformance_baseline`` never
    raises, so a baseline write failure can never crash install)."""
    from watchdog.conformance import (record_conformance_baseline,
                                      CONFORMANCE_BASELINE_NAME)
    record_fn = record_fn or record_conformance_baseline
    dest = CLAUDE_DIR / CONFORMANCE_BASELINE_NAME
    return record_fn(claude_md_content, REPO_DIR, dest)


def _configure_ratchet_merge_driver(repo_dir=REPO_DIR, run=None):
    """cmd_install step 1c (#553): idempotently register the ``ratchet-union``
    git merge driver in the repo-local (worktree-shared) ``.git/config`` so
    every fleet integration round auto-merges ``tests/size_ratchet.json``
    per-key union-max instead of conflicting on ceilings a machine can
    reconcile. The driver is versioned as ``scripts/ratchet_union_merge.py`` and
    wired via the committed ``.gitattributes``; git refuses to run a driver
    named only in committed config (security), so install writes the LOCAL
    config — worktrees share the common ``.git/config``, so one write covers
    every lane. ``git config <k> <v>`` overwrites in place, so this is
    inherently idempotent.

    Returns the driver command string, or ``None`` when *repo_dir* is not a git
    checkout (nothing to configure — a safe no-op; a clone without the driver
    simply falls back to git's default text merge). Best-effort: never raises,
    so a git/config failure can never crash install."""
    if run is None:
        import subprocess
        run = subprocess.run
    try:
        inside = run(["git", "-C", str(repo_dir), "rev-parse",
                      "--is-inside-work-tree"], capture_output=True, text=True)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None
        driver_script = Path(repo_dir) / "scripts" / "ratchet_union_merge.py"
        value = f'python3 "{driver_script}" %O %A %B %P'
        name = run(["git", "-C", str(repo_dir), "config",
                    "merge.ratchet-union.name", "ratchet per-key union-max (#553)"],
                   capture_output=True, text=True)
        drv = run(["git", "-C", str(repo_dir), "config",
                   "merge.ratchet-union.driver", value], capture_output=True, text=True)
        if name.returncode != 0 or drv.returncode != 0:
            return None  # a config write failed -> honestly report "not registered"
        return value
    except Exception:  # best-effort: no git/config failure may ever crash install
        return None


def cmd_install(args):
    """Deploy config: generate CLAUDE.md, symlink skills, merge hooks."""
    print("airuleset install")
    print("=" * 50)
    check_runtime_deps()
    ensure_timezone()

    # Ensure ~/.claude/ exists
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. Generate ~/.claude/CLAUDE.md ---
    modules, global_rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
    new_claude_md = generate_claude_md(modules)

    if CLAUDE_MD.exists():
        old_content = CLAUDE_MD.read_text()
        # Preserve externally-managed blocks (CodeGraph) that live outside the profile.
        new_claude_md = preserve_external_blocks(old_content, new_claude_md)
        if old_content != new_claude_md:
            # Create backup
            backup = CLAUDE_MD.with_suffix(".md.bak")
            shutil.copy2(CLAUDE_MD, backup)
            print(f"  Backed up: {CLAUDE_MD} -> {backup}")
            CLAUDE_MD.write_text(new_claude_md, encoding="utf-8")
            print(f"  Updated:   {CLAUDE_MD}")
        else:
            print(f"  No change: {CLAUDE_MD}")
    else:
        CLAUDE_MD.write_text(new_claude_md, encoding="utf-8")
        print(f"  Created:   {CLAUDE_MD}")

    # --- 1b. Record the conformance baseline ({md5, HEAD}) for job 34 (#535) ---
    _record_conformance_baseline_step(new_claude_md)

    # --- 1c. Register the ratchet-union git merge driver (#553): auto-merge
    # tests/size_ratchet.json union-max instead of a manual conflict each round. ---
    if _configure_ratchet_merge_driver():
        print("  Merge driver: ratchet-union registered (tests/size_ratchet.json)")
    else:
        print("  Merge driver: skipped (not a git checkout)")

    # --- 2. Symlink skills (per-box set — see skill_names_for_user) ---
    box_skills = skill_names_for_user()
    for skill in box_skills:
        source = REPO_DIR / "skills" / skill
        link = SKILLS_DIR / skill

        if not source.exists():
            print(f"  SKIP skill (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                print(f"  OK skill:  {skill}")
                continue
            link.unlink()
        elif link.exists():
            # Back up existing skill directory/file
            backup = link.with_suffix(".bak")
            if link.is_dir():
                if backup.exists():
                    shutil.rmtree(backup)
                shutil.move(str(link), str(backup))
            else:
                shutil.move(str(link), str(backup))
            print(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        print(f"  Linked:    {link} -> {source}")

    # --- 2a. Prune managed skills OUTSIDE this box's set (a maintainer-only /
    # full-authority-only skill previously linked here shows as slash-command noise
    # on this box). Only OUR symlinks pointing into this repo's skills/ are removed —
    # foreign or hand-made skills are never touched (skill-ownership rule).
    for skill in SKILL_NAMES:
        if skill in box_skills:
            continue
        link = SKILLS_DIR / skill
        if link.is_symlink():
            try:
                target = Path(os.readlink(link))
            except OSError:
                continue
            if str(target).startswith(str(REPO_DIR / "skills")):
                link.unlink()
                print(f"  Pruned:    {skill} (not relevant on this box)")

    # --- 2b. Symlink agents (subagent definitions, single .md files) ---
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in AGENT_NAMES:
        source = REPO_DIR / "agents" / f"{name}.md"
        link = AGENTS_DIR / f"{name}.md"

        if not source.exists():
            print(f"  SKIP agent (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                print(f"  OK agent:  {name}")
                continue
            link.unlink()
        elif link.exists():
            backup = link.with_suffix(".md.bak")
            shutil.move(str(link), str(backup))
            print(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        print(f"  Linked:    {link} -> {source}")

    # --- 2c. Symlink global path-scoped rules (rules/*.md referenced by the
    # universal profile) into ~/.claude/rules/ -- Claude Code's native
    # User-scope path-scoped-rules directory. #40: these were parsed by
    # categorize_entries() but previously discarded, never installed anywhere.
    for line in symlink_global_rules(global_rules, CLAUDE_DIR, REPO_DIR):
        print(line)

    # --- 3. Merge hooks into settings.json ---
    hooks_config = load_hooks_json()
    if hooks_config:
        old_settings_str = read_file_safe(SETTINGS_JSON)
        old_settings = json.loads(old_settings_str) if old_settings_str else {}
        new_settings = apply_managed_settings_defaults(
            merge_hooks_into_settings(hooks_config, old_settings))
        new_settings_str = json.dumps(new_settings, indent=2) + "\n"

        if old_settings_str.strip() != new_settings_str.strip():
            if SETTINGS_JSON.exists():
                backup = SETTINGS_JSON.with_suffix(".json.bak")
                shutil.copy2(SETTINGS_JSON, backup)
                print(f"  Backed up: {SETTINGS_JSON} -> {backup}")
            SETTINGS_JSON.write_text(new_settings_str)
            print(f"  Updated:   {SETTINGS_JSON}")
        else:
            print(f"  No change: {SETTINGS_JSON}")

    # --- 3b. claude launcher: managed script + thin ~/.bashrc wrappers (#77) ---
    # The SCRIPT (CLAUDE_LAUNCH_SCRIPT_DEST) is rewritten every install/push and
    # takes effect in every already-running shell IMMEDIATELY -- no `source
    # ~/.bashrc`, no relaunch, no restart (a bashrc FUNCTION, by contrast, is
    # frozen in a shell's memory at startup forever, which is exactly how
    # ultracode kept resurrecting after #53). Owner directive 2026-08-30 (#751)
    # REVERSED the launch-flag half of #445: the script bakes ultracode into NO
    # mode (effortLevel above dropped `xhigh` → `high` in the same reversal);
    # `claude-ultracode` is retained only as a muscle-memory alias of `default`.
    try:
        changed = apply_ultracode_launcher()
        print(f"  Updated:   {CLAUDE_LAUNCH_SCRIPT_DEST} (claude launcher script — "
              f"takes effect immediately, no restart needed)")
        if changed:
            print(f"  Updated:   {BASHRC} (claude launcher wrappers)")
        else:
            print(f"  No change: {BASHRC} (claude launcher wrappers)")
    except Exception as e:
        print(f"  claude launcher error: {e}", file=sys.stderr)

    # --- 3b-bis. tmux attach-or-create interactive helpers (#651) ---
    # `t [name]` + a `tmux()` wrapper that rewrites the simple `new|new-session|
    # a|attach|attach-session -t NAME` shapes to `command tmux new-session -A -s
    # NAME` -- so an accidental `tmux new -t zbynek` from shell history attaches
    # instead of piling up grouped siblings. Installed on EVERY managed box;
    # interactive-only via the block's own `$-` guard, so scripts/webterm/
    # watchdog never see it. Same idempotent ~/.bashrc marker-block shape as the
    # launcher above; the baked-in `t` default is the box's owner session.
    try:
        attach_changed = apply_tmux_attach_helpers()
        if attach_changed:
            print(f"  Updated:   {BASHRC} (tmux attach-or-create helpers, #651)")
        else:
            print(f"  No change: {BASHRC} (tmux attach-or-create helpers, #651)")
    except Exception as e:
        print(f"  tmux attach-or-create helpers error (non-fatal): {e}", file=sys.stderr)

    # --- 3b-ter. Owner SSH key provisioning (#653): append the owner's laptop
    # public key(s) to THIS account's authorized_keys (idempotent, append-only,
    # keyed on the key blob — never truncates/removes), so the owner reaches
    # every managed box key-only, never a password. Runs on every target
    # through the deploy loop's existing connection AND locally — non-fatal, so
    # a provisioning hiccup never aborts the rest of install. ---
    try:
        provision_owner_keys()
    except Exception as e:
        print(f"  owner-key provisioning error (non-fatal): {e}", file=sys.stderr)

    # --- 3b-quater. Owner VPS NOPASSWD sudo (#659): on an owner VPS-class box
    # (the deploy loop set AIRULESET_OWNER_VPS=1 for its owner_vps REMOTE_HOSTS
    # entry), install visudo-validated NOPASSWD sudo for the owner user so the
    # claude working there has full operational capability. A pure no-op on
    # every other box (the env is unset) and on a box without passwordless sudo
    # yet (LOUD one-time-bootstrap report). Non-fatal. ---
    try:
        provision_owner_sudo()
    except Exception as e:
        print(f"  owner-sudo provisioning error (non-fatal): {e}", file=sys.stderr)

    # --- 3c. tmux managed block: every managed user's ~/.tmux.conf (#235/#236/#241) ---
    # tmux's own 2000-line default plus the current CC renderer's re-render
    # frame-stacking made real scrollback holey within minutes under
    # agentic load -- raise history-limit fleet-wide via the same
    # idempotent-marker-block shape as the launcher's ~/.bashrc block above.
    # #236 extended the SAME block with default-size 176x50 -- the
    # identical frame-stacking mechanism also fires on every per-attach
    # resize from a different-sized terminal, not just scrollback rotation.
    # #236 originally also shipped `window-size manual`; #241 removed it
    # (it crashes tmux 3.4 at conf-parse startup); #586 RESTORED it
    # version-gated + conf-only; #613 REOPEN removed it (mis-targeting the browser
    # client); #613 REOPEN-2 (owner directive 2026-08-22) RESTORES it version-
    # gated + conf-only again -- the owner's fixed-size invariant (`manual` pins
    # every window to default-size so no client resizes another's window; the
    # browser's OWN appearance is solved on the browser side). history-limit is
    # live-applied to any RUNNING tmux server (#235's proven-safe scope);
    # window-size + default-size land in the conf AND (#685) are live-CONVERGED
    # on a running >= 3.5 server, since a conf-only pin never reaches a server
    # started before it (apply_tmux_history_limit's docstring has the history).
    try:
        tmux_changed = apply_tmux_history_limit()
        _ws = ("window-size manual (tmux>=3.5 only)"
               if _tmux_supports_window_size_manual(_default_tmux_run)
               else "no window-size (tmux<3.5 -- would crash 3.4)")
        tmux_desc = (f"history-limit {TMUX_HISTORY_LIMIT}, "
                     f"default-size {TMUX_DEFAULT_SIZE}, {_ws} (#613)")
        if tmux_changed:
            print(f"  Updated:   {TMUX_CONF} ({tmux_desc})")
        else:
            print(f"  No change: {TMUX_CONF} ({tmux_desc})")
    except Exception as e:
        print(f"  tmux managed-block error: {e}", file=sys.stderr)

    # --- 3c-bis. tmux owner-session normalization (#651/#660). Two branches:
    #   * `<owner>` ABSENT -> live-rename a lone `<owner>-N` grouped-sibling
    #     survivor to `<owner>` (#651) so the `-A -s <owner>` helpers hit it.
    #   * `<owner>` PRESENT -> KILL-SWEEP (#660): absorb every STRAY-named
    #     session (`<owner>-N`, plus on an owner box the fleet stream families
    #     `marek`/`montalu*`/... from the dev2 fleet incident) that is PROVABLY
    #     idle -- unattached AND ungrouped AND every pane a bare shell with no
    #     child process; NEVER a session running claude / a suspended job / a
    #     grouped or attached session / anything outside the stray namespaces.
    # Every kill/skip is logged to ~/.claude/tmux-audit/normalize.log; the
    # stream-family widening is owner-boxes-only (never a subdev box, which
    # would target the account's own real session). No-op with no live server.
    try:
        _owner = _owner_session_default(_current_user())
        _stray_res = _owner_box_stray_name_res(
            _owner, is_single_session_box_user(_current_user()))
        _live_normalize_owner_session(_owner, stray_name_res=_stray_res)
    except Exception as e:
        print(f"  tmux owner-session normalize error (non-fatal): {e}", file=sys.stderr)

    # --- 3d. tmux boot-time cutover unit: points /usr/local/bin/tmux at the
    # newest managed build (tmux-3.7b) at THIS box's own next boot (#242).
    # Non-interactive (sudo -n); skipped with a loud-but-expected reason on
    # the four subdev stream accounts, which have no sudo at all.
    try:
        ok, reason = setup_tmux_cutover_provisioning()
        if reason:
            print(f"  tmux cutover unit: skipped ({reason})")
        elif ok:
            print(f"  tmux cutover unit: installed + enabled ({TMUX_CUTOVER_UNIT_NAME})")
    except Exception as e:
        print(f"  tmux cutover unit error (non-fatal): {e}", file=sys.stderr)

    # --- 3e. tmux boot-time cutover unit, subdev VIA the gatekeeper root hop
    # (#242) -- a true no-op except when this install run is genuinely on the
    # gatekeeper box (identity file present); covers all FOUR subdev stream
    # accounts with the ONE root-level install their shared box needs.
    try:
        ok, reason = setup_tmux_cutover_subdev_via_gatekeeper()
        if reason:
            print(f"  tmux cutover unit (subdev via gatekeeper): skipped ({reason})")
        elif ok:
            print("  tmux cutover unit (subdev via gatekeeper): installed + enabled")
    except Exception as e:
        print(f"  tmux cutover unit (subdev) error (non-fatal): {e}", file=sys.stderr)

    # --- 3f. claude CLI binary: fleet-wide, best-effort (#263) ---
    # Every OTHER piece of dev-env provisioning above manages a WRAPPER
    # around `claude` (the launcher script, the tmux cutover) -- nothing
    # has ever installed the BINARY itself. Harmless no-op wherever it
    # already resolves.
    try:
        ensure_claude_cli_installed()
    except Exception as e:
        print(f"  claude CLI install error (non-fatal): {e}", file=sys.stderr)

    # --- 3f-native. claude native user-space install, never root-npm (#659) ---
    # A box carrying a root-npm `/usr/bin/claude` looks "installed" to
    # ensure_claude_cli_installed() above, so it kept the un-updatable copy
    # (the spinbike-vps `no write permission to npm prefix` error). Force the
    # native ~/.local install where only a system copy resolves and remove the
    # system copy (gated on passwordless sudo -- a safe no-op on a fully-native
    # box and on the sudo-less subdev accounts). Fleet-wide, non-fatal.
    try:
        ensure_claude_native_userspace()
    except Exception as e:
        print(f"  claude native user-space migration error (non-fatal): {e}",
              file=sys.stderr)

    # --- 3f-bis. ffmpeg static binary: fleet-wide, best-effort, no sudo
    # needed (#275) -- the meeting-analysis skill's own ffmpeg extraction
    # step has no other install path on the no-sudo subdev stream accounts
    # (RUNTIME_DEPS' apt-get path structurally cannot run there). Same
    # shape as the claude-CLI step right above.
    try:
        ensure_ffmpeg_static_binary()
    except Exception as e:
        print(f"  ffmpeg static install error (non-fatal): {e}", file=sys.stderr)

    # --- 3g. subdev stream dev-env bootstrap: tmux session + claude launched
    # (#263), ssh auto-attach (#264), human-gap report (#263). The tmux
    # session/window-name/gap-report legs are true no-ops on every non-stream
    # box (dev1/dev2/gatekeeper) via AUTHORITY_BY_USER's own scope. The ssh
    # auto-attach leg is the ONE exception: it ALSO installs on the gk box
    # `gatekeeper` account (#562, via SSH_ATTACH_EXTRA_USERS), and stays a
    # no-op only on dev1/dev2 (`newlevel`).
    try:
        result = ensure_stream_tmux_session()
        if result:
            print(f"  stream tmux session: {result}")
    except Exception as e:
        print(f"  stream tmux session error (non-fatal): {e}", file=sys.stderr)
    try:
        ssh_attach_changed = apply_stream_ssh_attach()
        if ssh_attach_changed:
            print(f"  Updated:   {BASHRC} (subdev ssh auto-attach, #264)")
    except Exception as e:
        print(f"  ssh auto-attach setup error (non-fatal): {e}", file=sys.stderr)
    try:
        # #656: the OWNER-VPS counterpart of the #264 subdev block. A no-op on
        # every box except a registered owner single-project VPS (spinbike-vps,
        # `_owner_vps_project`); there it installs (or idempotently replaces the
        # interim hand-block) an ssh auto-attach into the owner session's
        # project window (session=<owner group>, window=<project>, cwd=<dev
        # dir>), and STRIPS a stale block from any box that must not carry it.
        vps_attach_changed = apply_owner_vps_ssh_attach()
        if vps_attach_changed:
            print(f"  Updated:   {BASHRC} (owner-VPS ssh auto-attach, #656)")
    except Exception as e:
        print(f"  owner-VPS ssh auto-attach setup error (non-fatal): {e}",
              file=sys.stderr)
    try:
        # #554/#592: name the tmux WINDOW after the box's short TARGET ALIAS
        # (gk/mN/dN/...) so the owner sees WHERE they are. #593: renders ONLY on
        # SINGLE-SESSION-per-account boxes (gk + subdev streams), NEVER an owner/
        # newlevel MULTI-PROJECT box (dev1/dev2) -- one fixed name there froze
        # every project window and destroyed navigation (the #592 regression).
        # The alias comes from the SAME source the webterm tabs use
        # (cli_aliases.short_target_alias).
        window_name_changed = apply_stream_tmux_window_name()
        if window_name_changed:
            # neutral wording: on a single-session box this ADDS the alias block,
            # on an owner multi-project box it STRIPS a stale one (#593) -- the
            # message must not claim a direction it may not have done.
            print(f"  Updated:   {TMUX_CONF} (tmux window-name block, #592/#593)")
    except Exception as e:
        print(f"  stream tmux window-name setup error (non-fatal): {e}", file=sys.stderr)

    # --- 3g-bis. #660: native session-created AUDIT hook on the OWNER box, to
    # capture a future stray's creator deterministically (full rationale +
    # ordering note in apply_owner_session_created_audit's docstring; MUST run
    # AFTER apply_stream_tmux_window_name, whose owner-box #593 revert live-
    # UNSETS session-created).
    try:
        audit_changed = apply_owner_session_created_audit()
        if audit_changed:
            print(f"  Updated:   {TMUX_CONF} (tmux session-created audit, #660)")
    except Exception as e:
        print(f"  tmux session-created audit setup error (non-fatal): {e}",
              file=sys.stderr)

    try:
        report_stream_dev_env()
    except Exception as e:
        print(f"  stream dev-env gap report error (non-fatal): {e}", file=sys.stderr)

    # --- 4. File-Drop service: installed on EVERY machine (serves local files) ---
    try:
        maybe_setup_filedrop()
    except Exception as e:
        print(f"  filedrop setup error (non-fatal): {e}", file=sys.stderr)

    # --- 5. api-watchdog timer: every machine (auto-resume API-error stalls) ---
    try:
        maybe_setup_watchdog()
    except Exception as e:
        print(f"  watchdog setup error (non-fatal): {e}", file=sys.stderr)

    # --- 5b. web terminal gateway (#555/#612): dispatch by (nodename, account) —
    # dev1->owner, subdev+marek->marek, subdev(david1/default)->david; else no-op. ---
    try:
        maybe_setup_webterm()
    except Exception as e:
        print(f"  webterm setup error (non-fatal): {e}", file=sys.stderr)

    # --- 5c. #664: re-assert the public-TLS drop ingress AFTER webterm setup.
    # setup_webterm_david_tunnel rewrites subdev's config.yml from scratch, which
    # would delete a live drop ingress; this idempotently re-adds it (no-op unless
    # this box has a LIVE drop lane). Never raises. ---
    try:
        import cli_drop_gateway
        cli_drop_gateway.reconcile_drop_ingress_on_install()
    except Exception as e:                             # pragma: no cover - defensive
        print(f"  drop-gateway ingress re-assert error (non-fatal): {e}",
              file=sys.stderr)

    # --- 6. caveman plugin: every machine (enable + stable statusline shim) ---
    # A still-failing plugin install (after correct marketplace registration)
    # is now a REQUIRED-step failure, not a silent best-effort one (issue:
    # push: plugin installs fail on fresh stream accounts, 2026-08-06) — it
    # latches `install_failed`, which turns "Install complete." into a loud
    # non-zero exit below, per script-failure-policy. An unexpected
    # EXCEPTION from either function stays non-fatal-to-the-whole-install
    # (the outer try/except here guards against a bug in OUR OWN code, not
    # against the plugin-marketplace failure this ticket is about — that
    # case is already caught INSIDE each setup_* function and reflected in
    # its own `ok` return value).
    install_failed = False
    try:
        if not maybe_setup_caveman():
            install_failed = True
    except Exception as e:
        print(f"  caveman setup error (non-fatal): {e}", file=sys.stderr)

    # --- 6b. managed baseline plugins: superpowers (the rules invoke its skills) ---
    try:
        if not setup_managed_plugins():
            install_failed = True
    except Exception as e:
        print(f"  managed plugins setup error (non-fatal): {e}", file=sys.stderr)

    # --- 6c. subagent status line: model+effort in the agent strip (#538) ---
    # Native subagentStatusLine (CC v2.1.205+): surfaces each inline
    # subagent's RESOLVED model per strip row. Runs AFTER caveman's settings
    # write (step 6) so it reconciles the already-updated settings.json.
    # Non-fatal — a status-line shim must never fail the whole install (a
    # broken one just leaves CC's default rows), matching the discord-check
    # step below.
    try:
        import subagent_statusline
        subagent_statusline.setup(REPO_DIR, CLAUDE_DIR, str(SETTINGS_JSON))
    except Exception as e:
        print(f"  subagent status line setup error (non-fatal): {e}", file=sys.stderr)

    # --- 7. Discord notify config: warn LOUDLY if this host has no .env ---
    try:
        check_discord_notify_config()
    except Exception as e:
        print(f"  discord notify check error (non-fatal): {e}", file=sys.stderr)

    # --- 7b. Discord questions (-q) thread: self-heal + LOUD gap report (#718) ---
    # check_discord_notify_config above only REPORTS whether Discord is wired;
    # it does not close the #296 gap that the per-owner questions thread
    # (claude-<owner>-q) is NEVER auto-created — so a question-delivery-ENABLED
    # owner (david; NOT the #710-suppressed zbynek/marek) whose box never ran
    # the explicit --provision-question-thread has its ❓ pings fall back into
    # the main thread forever (live incident #718: david on subdev). This
    # self-heals it fleet-wide at every install, scoped to the one owner THIS
    # box delivers as; machine channel only (stdout), never an owner ping.
    try:
        from notify import (provision_owner_question_thread_for_install,
                            format_qthread_install_report)
        _q_result = provision_owner_question_thread_for_install()
        for _q_line in format_qthread_install_report(_q_result):
            print(_q_line)
    except Exception as e:
        print(f"  discord questions-thread provision error (non-fatal): {e}",
              file=sys.stderr)

    # --- 8. Tier-0 target/ retention: purge stale build artefacts (#315) ---
    # Existing Tier-0 (default) local-builds policy bans HEAVY local builds
    # but still legitimately fills target/ via the cheap checks it DOES
    # allow (cargo check/clippy/test --no-run) -- and nothing ever purged
    # it (the local-builds skill's own purge rule is prose, called
    # on-demand only). Cadence-gated to at most once/day via
    # purge_stale_tier0_targets' own state file, so this doesn't add a
    # filesystem sweep to every push -- non-fatal, best-effort, matches
    # every other step above.
    try:
        purge_results = purge_stale_tier0_targets()
        purged = [r for r in purge_results if r.get("purged")]
        if purged:
            total = sum(r.get("size", 0) or 0 for r in purged)
            print(f"  Purged {len(purged)} stale Tier-0 target/ dir(s), "
                  f"{_human_size(total)} reclaimed (log: {TARGET_PURGE_LOG_PATH})")
    except Exception as e:
        print(f"  target/ purge error (non-fatal): {e}", file=sys.stderr)

    # --- 9. Stale worktree sweep: reclaim dead-worker leaks fleet-wide (#345)
    # A worker killed mid-run by an API error / session limit leaves its
    # `.claude/worktrees/agent-<id>` worktree + branch registered forever —
    # a round's own close-out only ever cleans branches it MERGED, never a
    # sibling round's dead leftovers. Cadence-gated to at most once per
    # STALE_WORKTREE_MIN_INTERVAL_S via sweep_stale_worktrees' own state
    # file, so this doesn't add a git-porcelain sweep to every push either —
    # non-fatal, best-effort, matches every other step above.
    try:
        sweep_results = sweep_stale_worktrees()
        removed = [r for r in sweep_results if r.get("removed")]
        if removed:
            print(f"  Removed {len(removed)} stale worktree(s)/branch(es) "
                  f"(log: {STALE_WORKTREE_LOG_PATH})")
    except Exception as e:
        print(f"  worktree sweep error (non-fatal): {e}", file=sys.stderr)

    # --- 9b. Merged worktree-lane target/ reclaim (#545) -------------------
    # A worktree LANE's target/ (1-2 GB of cargo build output) is NOT
    # reclaimed by the #315 target-purge (its 7-day newest-mtime floor is
    # defeated by cargo fingerprint churn) nor by step 9 (which waits the full
    # 24h idle floor to remove the WHOLE lane). This reclaims ONLY the target/
    # of a lane whose branch is MERGED (0-ahead + authored-commit reflog),
    # idle + not in live use -- pure regenerable waste (6.3 GB measured on
    # dev1, #545). Cadence-gated by its own state file, non-fatal, best-
    # effort -- matches every other step above.
    try:
        lane_results = purge_merged_lane_targets()
        lane_purged = [r for r in lane_results if r.get("purged")]
        if lane_purged:
            total = sum(r.get("size", 0) or 0 for r in lane_purged)
            print(f"  Reclaimed {len(lane_purged)} merged-lane target/ dir(s), "
                  f"{_human_size(total)} freed (log: {LANE_TARGET_LOG_PATH})")
        # #545 tier-0 classification: surface any bypass finding (a merged lane
        # whose target/ held post-#557 build artifacts -- a local cargo build
        # that escaped the tier-0 gate, auto-filed on the offending repo).
        lane_bypass = [r for r in lane_results if r.get("tier0_bypass")]
        for r in lane_bypass:
            print(f"  ⚠ TIER-0 BYPASS: {r.get('branch')} -- "
                  f"{r.get('tier0_bypass_filed') or '?'}")
    except Exception as e:
        print(f"  merged-lane target reclaim error (non-fatal): {e}", file=sys.stderr)

    # --- 10. Old Claude CLI binary sweep: keep current + previous only (#355)
    # Every native `claude` auto-update leaves the OLD versioned binary
    # behind (~280-300MB each) under ~/.local/share/claude/versions/ --
    # nothing has ever swept it, fleet-wide. Cadence-gated the same way as
    # step 8/9 above -- non-fatal, best-effort.
    try:
        cli_sweep_results = sweep_stale_cli_versions()
        cli_removed = [r for r in cli_sweep_results if r.get("removed")]
        if cli_removed:
            total = sum(r.get("size", 0) or 0 for r in cli_removed)
            print(f"  Removed {len(cli_removed)} old CLI version(s), "
                  f"{_human_size(total)} reclaimed (log: {CLI_VERSION_LOG_PATH})")
    except Exception as e:
        print(f"  cli-version sweep error (non-fatal): {e}", file=sys.stderr)

    # --- 11. Claude scratch/tmp sweep: reclaim aging session scratchpads (#355)
    # /tmp/claude-<uid>/<encoded-cwd>/<session-id>/scratchpad/... accumulates
    # unboundedly -- nothing has ever swept it either. Scoped strictly to
    # THIS account's own uid-named root (never another user's /tmp content).
    # Cadence-gated the same way as every step above -- non-fatal, best-effort.
    try:
        scratch_sweep_results = sweep_claude_scratch()
        scratch_removed = [r for r in scratch_sweep_results if r.get("removed")]
        if scratch_removed:
            total = sum(r.get("size", 0) or 0 for r in scratch_removed)
            print(f"  Removed {len(scratch_removed)} claude scratch path(s), "
                  f"{_human_size(total)} reclaimed (log: {CLAUDE_SCRATCH_LOG_PATH})")
    except Exception as e:
        print(f"  claude-scratch sweep error (non-fatal): {e}", file=sys.stderr)

    # --- 11b. Fleet age-gated /tmp litter reaper (#548, flips #513's report-
    # only default) -- the dev1 inode-exhaustion incident (721k /tmp entries:
    # 465k `tmp[a-z0-9_]{8}` mkdtemp litter + 136k `airuleset-*` hook state).
    # This install step runs on EVERY managed box at every push/install (the
    # deploy loop), so it is the fleet-wide reaper. LIVE reaping is #548's owner
    # sign-off; both sweeps are mtime-gated (tmp* >24h, airuleset-* >3d --
    # anything live is <2h), regular-file/dir only, /proc live-scan + TOCTOU
    # re-checked, cadence-gated (own 24h state), non-fatal. Camera-box runs on
    # dev1, so the same reaper covers it (its target/ class is #544/#545).
    try:
        stray = sweep_stray_tmp(live=True, min_age_days=1)
        if stray.get("total_matched"):
            print(f"  Stray /tmp tempfile litter: {stray['total_matched']} matched, "
                  f"removed {stray['removed']} ({_human_size(stray['reclaimed_bytes'])}"
                  f" reclaimed) (log: {TMP_STRAY_LOG_PATH})")
    except Exception as e:
        print(f"  stray-tmp sweep error (non-fatal): {e}", file=sys.stderr)
    # --- 11c. Stray /tmp/airuleset-* hook-state litter reaper (#548) -- the
    # OTHER half of the incident (hardcoded-/tmp session markers the CORE TMPDIR
    # redirect never catches). Same LIVE mtime/uid/proc/TOCTOU quad-gate, 3-day
    # floor, exec-permission markers EXCLUDED (job 22's live-checked domain).
    try:
        astate = sweep_airuleset_state(live=True, min_age_days=3)
        if astate.get("total_matched"):
            print(f"  Stray /tmp/airuleset-* state litter: {astate['total_matched']} matched, "
                  f"removed {astate['removed']} ({_human_size(astate['reclaimed_bytes'])}"
                  f" reclaimed) (log: {AIRULESET_STATE_LOG_PATH})")
    except Exception as e:
        print(f"  airuleset-state sweep error (non-fatal): {e}", file=sys.stderr)

    # --- 12. Disk-usage visibility (#380 point 4) -- one more line in the
    # SAME print block every sweep step above already writes summaries
    # into. No new mechanism, no new state/log file -- best-effort, never
    # fails install on a measurement error.
    try:
        print(f"  {_disk_usage_summary_line(CLAUDE_DIR)}")
    except OSError as e:
        print(f"  disk-usage check error (non-fatal): {e}", file=sys.stderr)
    # --- 13. Transcript gzip-at-rest sweep: size-aware retention for
    # unbounded chat history (#410, #376's own missing half). REPORT-ONLY
    # is the wired DEFAULT everywhere -- set
    # AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1 to enable LIVE compression on
    # THIS box, and only after the user has personally signed off
    # following /resume + history-browsing verification (see the #410
    # design comment). This env var is never set anywhere by this PR, on
    # any box. Cadence-gated the same way as every step above once live
    # -- non-fatal, best-effort. The actual gate+call is factored into
    # `_run_transcript_compress_step()` so it can be tested directly
    # (#410 review F2) -- this step is just that call, non-fatal.
    try:
        _run_transcript_compress_step()
    except Exception as e:
        print(f"  transcript-compress sweep error (non-fatal): {e}", file=sys.stderr)

    # --- 14. Autopilot-lock litter one-time cleanup: pre-#385 backlog
    # (#409). #385 stopped the ongoing leak; this clears what had already
    # accumulated. Wired LIVE (not dry-run-gated like step 13's transcript
    # sweep) -- the safety here is the discriminator itself (never more
    # willing to disturb a lock than cmd_autopilot_lock's own already-
    # shipped acquire() self-heal logic already is), not a human sign-off
    # gate. Cadence-gated the same way as every step above -- non-fatal,
    # best-effort. Idempotent for its ACTIONS (never re-removes what is
    # already gone), but NOT a full no-op even once the reclaimable
    # backlog is cleared -- a real fraction of pre-#385 rows is
    # PERMANENTLY unreclaimable by this discriminator (a lock file whose
    # recorded holder pid coincides with a long-lived system pid like 1,
    # or a legacy directory-shaped lock that is not empty) and keeps
    # getting re-discovered and re-skipped every 24h forever; the sweep's
    # own logger (#409 review finding 2) deliberately does NOT write a
    # line for those routine skips, so this residual cost stays a cheap,
    # silent re-scan rather than an unbounded log.
    try:
        lock_sweep_results = sweep_autopilot_lock_litter()
        lock_removed = [r for r in lock_sweep_results if r.get("removed")]
        if lock_removed:
            print(f"  Removed {len(lock_removed)} autopilot-lock litter artifact(s) "
                  f"(log: {AUTOPILOT_LOCK_LITTER_LOG_PATH})")
    except Exception as e:
        print(f"  autopilot-lock-litter sweep error (non-fatal): {e}", file=sys.stderr)

    print()
    if install_failed:
        print("Install FAILED — a managed plugin's marketplace registration "
              "or install did not complete (see warnings above).",
              file=sys.stderr)
        sys.exit(1)
    print("Install complete. Restart Claude Code for changes to take effect.")


def cmd_status(args):
    """Show current managed config (imports, skills, hooks)."""
    print("airuleset status")
    print("=" * 50)

    # --- CLAUDE.md ---
    print("\n~/.claude/CLAUDE.md:")
    if CLAUDE_MD.exists():
        content = CLAUDE_MD.read_text()
        if MANAGED_MARKER in content:
            imports = [ln.strip() for ln in content.splitlines()
                       if ln.strip().startswith("@~/")]
            print(f"  Managed by airuleset ({len(imports)} imports)")
            for imp in imports:
                # Check if the referenced file exists
                # @~/devel/airuleset/modules/... -> expand ~ to home
                path_str = imp[1:]  # remove @
                expanded = Path(path_str.replace("~/", str(Path.home()) + "/"))
                status = "OK" if expanded.exists() else "MISSING"
                print(f"    [{status}] {imp}")
        else:
            print("  Not managed by airuleset (no marker found)")
    else:
        print("  Does not exist")

    # --- Skills (this box's set — scoped per skill_names_for_user) ---
    print("\n~/.claude/skills/:")
    for skill in skill_names_for_user():
        link = SKILLS_DIR / skill
        expected_target = REPO_DIR / "skills" / skill
        if link.is_symlink():
            actual = Path(os.readlink(link))
            if actual == expected_target:
                print(f"  {skill}: OK (symlinked to airuleset)")
            else:
                print(f"  {skill}: MISMATCH (points to {actual})")
        elif link.exists():
            print(f"  {skill}: NOT MANAGED (exists but not a symlink)")
        else:
            print(f"  {skill}: NOT INSTALLED")

    # Other skills present
    if SKILLS_DIR.exists():
        all_skills = {p.name for p in SKILLS_DIR.iterdir()}
        managed = set(SKILL_NAMES)
        unmanaged = all_skills - managed
        if unmanaged:
            print(f"\n  Unmanaged skills: {', '.join(sorted(unmanaged))}")

    # --- Hooks ---
    print("\n~/.claude/settings.json hooks:")
    if SETTINGS_JSON.exists():
        try:
            settings = json.loads(SETTINGS_JSON.read_text())
            hooks = settings.get("hooks", {})
            if hooks:
                for event_type, entries in hooks.items():
                    for entry in entries:
                        matcher = entry.get("matcher", "*")
                        for hook in entry.get("hooks", []):
                            cmd = hook.get("command", hook.get("type", "?"))
                            is_ours = "airuleset" in cmd
                            tag = " (airuleset)" if is_ours else ""
                            print(f"  {event_type}[{matcher}]: {cmd}{tag}")
            else:
                print("  No hooks configured")
        except json.JSONDecodeError:
            print("  ERROR: Invalid JSON in settings.json")
    else:
        print("  settings.json does not exist")


# (systemd --user helpers + File-Drop service install: _run_systemctl / _whoami /
#  setup_filedrop_service / ... -> cli_filedrop_watchdog.py, #433 L-B)


def _current_remote_host_entry():
    """Best-effort match of "the box currently running install" to its own
    REMOTE_HOSTS deploy-target entry (#151) -- keyed on the LOCAL system
    username via the existing `_whoami()` helper. This needs no new
    per-entry data and can't confuse the four subdev-VPS users (montalu/
    marek/david/simap), which all share the SAME physical hostname and
    would be indistinguishable by a hostname-only match.

    Returns None when no entry's `user` matches -- expected on dev1 itself
    (the deploy SOURCE, never listed in REMOTE_HOSTS, and always the primary
    already-configured host in practice, so it essentially never reaches the
    caller's warning branch). Known edge case, NOT actually unique any more
    (airuleset#408, 2026-08-12): `user: "newlevel"` is now shared by THREE
    entries -- the implicit dev1 identity, `dev2` (no identity pinned), and
    `spinbike-vps` (identity pinned, list order AFTER dev2). A mismatch on
    dev2 is harmless (neither dev1 nor dev2 pins an identity, so the printed
    hint is right either way); a mismatch on spinbike-vps is NOT -- running
    `install` there as `newlevel` would resolve to dev2's entry first and
    print a hint missing spinbike's required `-i ~/.ssh/spinbike_vps` flag.
    Cosmetic (this only affects a print-only Discord-config hint on a box
    that has not yet wired its own local `.env`), left as a documented
    residual per the FREEZE rather than redesigned (see airuleset#408's own
    review discussion)."""
    me = _whoami()
    if not me:
        return None
    for entry in REMOTE_HOSTS:
        if entry.get("user") == me:
            return entry
    return None


def check_discord_notify_config():
    """Report whether Discord notifications are wired on THIS host (no secrets printed).

    The Discord `.env` (bot token + per-owner channels/mentions) is LOCAL and NOT
    git-deployed — `install` cannot carry it. A host that never got it wired sends
    NOTHING: every notify call fail-safes to a silent no-op. That is exactly how the
    gatekeeper box went dark (the `.env` was never wired when it was added). This
    check makes the gap LOUD at install time instead of a silent failure discovered
    weeks later. It NEVER prints the token value — only presence.

    The "wire it from an already-configured host" ssh one-liner (#151) is built
    from THIS box's own REMOTE_HOSTS entry (`_current_remote_host_entry()`) so it
    carries a pinned `-i <identity>` when one exists -- e.g. simap/marek/david on
    subdev all pin `~/.secrets/gatekeeper_access_ed25519`. A wrong-key ssh attempt
    against subdev trips its fail2ban and bans dev1 on every interface (tailscale
    included) for an hour, so a bare host-agnostic hint there is not cosmetic.
    When no REMOTE_HOSTS entry matches this box, the old `<this-host>` placeholder
    is kept unchanged rather than guessing."""
    env = CLAUDE_DIR / "channels" / "discord" / ".env"
    print("  Checking Discord notify config")
    if not env.is_file():
        print("    ⚠ Discord notify DISABLED — no ~/.claude/channels/discord/.env on this host.")
        print("      Pings (❓/✅, api-error, autopilot cards) will silently NOT send.")
        print("      Wire it from an already-configured host (secrets stay local, not git):")
        entry = _current_remote_host_entry()
        target = f"{entry['user']}@{entry['host']}" if entry else "<this-host>"
        identity = entry.get("identity") if entry else None
        id_flag = f"-i {identity} " if identity else ""
        print(f"        cat ~/.claude/channels/discord/.env | ssh {id_flag}{target} \\")
        print("          'umask 077 && mkdir -p ~/.claude/channels/discord && "
              "cat > ~/.claude/channels/discord/.env'")
        return
    token = ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
            break
    if not token:
        print("    ⚠ Discord .env present but DISCORD_BOT_TOKEN is empty — pings will not send.")
    else:
        print("    Discord notify: configured (bot token present).")


# (maybe_setup_filedrop -> cli_filedrop_watchdog.py, #433 L-B)


# ---------------------------------------------------------------------------
# Caveman plugin wiring (every host) — see the constants block up top
# ---------------------------------------------------------------------------

def reconcile_caveman_settings(settings: dict,
                               statusline_command: str = CAVEMAN_STATUSLINE_COMMAND) -> dict:
    """Pure: return a new settings dict with caveman correctly wired —
    statusLine -> the stable shim, the plugin enabled, the marketplace known.
    Every other key is preserved untouched. Idempotent (same input -> same output)."""
    result = dict(settings)
    result["statusLine"] = {"type": "command", "command": statusline_command}
    enabled = dict(result.get("enabledPlugins", {}))
    enabled[CAVEMAN_PLUGIN_KEY] = True
    result["enabledPlugins"] = enabled
    markets = dict(result.get("extraKnownMarketplaces", {}))
    markets["caveman"] = {"source": {"source": "github", "repo": CAVEMAN_MARKETPLACE_REPO}}
    result["extraKnownMarketplaces"] = markets
    return result


# ---------------------------------------------------------------------------
# Managed baseline plugins (every host) — see MANAGED_PLUGINS up top
# ---------------------------------------------------------------------------

# --- claude CLI + ffmpeg/ffprobe static-binary installers — extracted to cli_binary_installers.py (#433 cluster L) ---
# F401: this is a facade re-export — several of these names (the internal
# helpers + the ffmpeg constants) are consumed only by the test-suite via
# `airuleset.<name>`, not by airuleset.py's own resident code.
from cli_binary_installers import (  # noqa: E402, F401
    _claude_cli_env,
    _claude_cli_installed,
    ensure_claude_cli_installed,
    _claude_installer_argv,
    _native_claude_present,
    _system_claude_path,
    _remove_system_claude,
    ensure_claude_native_userspace,
    FFMPEG_STATIC_URL,
    FFMPEG_STATIC_BIN_DIR,
    FFMPEG_STATIC_DEST,
    FFPROBE_STATIC_DEST,
    _binary_reachable,
    _ffmpeg_available,
    ensure_ffmpeg_static_binary,
    TTYD_STATIC_URL,
    TTYD_STATIC_BIN_DIR,
    TTYD_STATIC_DEST,
    _ttyd_available,
    ensure_ttyd_static_binary,
)


# (File-Drop share/serve subcommands _filedrop_serve / cmd_share / _filedrop_status /
#  cmd_filedrop -> cli_filedrop_watchdog.py, #433 L-B)


def cmd_notify(args):
    """Send a Discord notification (with the tmux-owner @mention prepended).

    Modes:
      --mention-prefix     print just the '<@id> ' prefix for the current tmux
                           owner (used by hooks/notify-discord.sh) and exit.
      --channel-id         print the resolved per-owner Discord channel/thread id
                           (DISCORD_NOTIFICATION_CHANNEL_<OWNER>, else the shared
                           DISCORD_NOTIFICATION_CHANNEL_ID) and exit — the single
                           source of truth the shell send path reads.
      --owner              print the resolved tmux owner and exit — lets the shell
                           hook resolve ONCE and force the same owner onto both the
                           --mention-prefix and --channel-id calls (so they agree).
      --autopilot-done     compose + send the canonical per-ticket completion card
                           from fields (--repo --pr --merge-sha --version --review
                           --done --remaining --tickets-json). Deduped on repo#pr.
      --body "<markdown>"  send arbitrary markdown (the general primitive,
                           used by external/foreign callers e.g. codex-bridge
                           — never by anything inside this repo). With no
                           --owner-name, falls through to resolve_owner()'s
                           tmux auto-detect, which is correct ONLY for a
                           caller genuinely tied to the current pane; a
                           headless/detached caller must pass --owner-name
                           to pin the real owner explicitly (#334).
    """
    from notify import (compose_autopilot_card, mention_prefix, mirror_owners,
                        notification_channel, question_ping_off, resolve_owner,
                        resolve_project_channel, resolve_questions_channel,
                        send)

    # #476: the read-only query/print flags (--repo-name, --channel-id, …) are
    # each checked and RETURN before the send actions (--run-card etc.) below, so
    # a caller who typos `--repo-name` for `--repo` on a `--run-card` invocation
    # short-circuits into the print path and exits 0 with NO card sent — a silent
    # no-send that looks like success (codex-bridge, 2026-08-14: three cards
    # "sent", zero delivered — the misused-flag path bypasses #134's exit-nonzero
    # guarantee because the send path is never entered). A send action combined
    # with a read-only query flag is always a mistake; refuse it LOUD (non-zero)
    # rather than let a print branch silently short-circuit it.
    if getattr(args, "run_card", False):
        _query_only = [name for name, on in (
            ("--repo-name", getattr(args, "repo_name", False)),
            ("--channel-id", getattr(args, "channel_id", False)),
            ("--owner", getattr(args, "owner", False)),
            ("--mirror-owners", getattr(args, "mirror_owners", False)),
            ("--project-label", getattr(args, "project_label", False)),
            ("--newest-card", getattr(args, "newest_card", False)),
            ("--mention-prefix", getattr(args, "mention_prefix", False)),
            ("--question-ping-off", getattr(args, "question_ping_off", False)),
        ) if on]
        if _query_only:
            print("notify --run-card: %s is a read-only query flag, not a send "
                  "target — did you mean --repo? Refusing rather than printing "
                  "and exiting 0 with no card sent (#476)."
                  % ", ".join(_query_only), file=sys.stderr)
            sys.exit(1)

    if getattr(args, "record_question", False):
        # Record a ❓ ping's Discord message id → the session that asked, so the
        # watchdog can route the user's Discord REPLY back into that session.
        # The send hook pipes the posted ❓ CONTENT on stdin (arbitrary quotes /
        # backticks never touch shell argv) — stored so the reply delivery can
        # wrap the answer with the question it answers (2026-07-17).
        # stdin is read ONLY on the explicit --question-stdin flag (the hook
        # sets it when piping). An unconditional isatty()-guarded read HUNG
        # forever when a caller spawned this command with an inherited
        # never-closing pipe as stdin (the 2026-07-19 push-gate hang — the
        # unittest child inherited the push's stdin and blocked in read()).
        from notify import record_question
        q_text = ""
        if getattr(args, "question_stdin", False):
            try:
                q_text = sys.stdin.read()
            except (OSError, ValueError):
                q_text = ""
        ok = record_question(args.message_id, args.channel, args.session,
                             args.cwd, question=q_text,
                             suppressed=getattr(args, "suppressed", False))
        sys.stdout.write("recorded" if ok else "skip")
        return

    if getattr(args, "edit_question", False):
        # EDIT the session's recent ❓ ping in place with the reworded question
        # from stdin (edits don't push-ping — the pending hook's anti-spam path;
        # camera-box got 3 pings in 3 min for one reworded question, 2026-07-05).
        # rc 2 = nothing recent/editable → the caller falls back to a fresh POST.
        from notify import update_question
        ok = update_question(getattr(args, "session", "") or "", sys.stdin.read())
        sys.stdout.write("edited" if ok else "no-recent-question")
        sys.exit(0 if ok else 2)

    if getattr(args, "owner", False):
        sys.stdout.write(resolve_owner())
        return

    if getattr(args, "question_ping_off", False):
        # #710: read-only predicate — does the resolved owner have ❓ QUESTION
        # Discord delivery turned OFF (zbynek/marek)? Prints "1" (off) / "0"
        # (on), always exit 0. hooks/notify-discord-send.sh consults this ONCE
        # for the PRIMARY owner (passed via --owner-name "$PRIMARY_OWNER"),
        # before the emit loop, to suppress the interactive ❓ ping for an off
        # owner while leaving david untouched — primary-owner-scoped, matching
        # the notify.send(kind="questions") chokepoint. An explicit --owner-name
        # overrides the tmux-resolved owner (empty falls back to resolve_owner).
        owner = (getattr(args, "owner_name", "") or "").strip() or resolve_owner()
        sys.stdout.write("1" if question_ping_off(owner) else "0")
        return

    if getattr(args, "mention_prefix", False):
        sys.stdout.write(mention_prefix())
        return

    if getattr(args, "content_dedup_claim", False):
        # #687: cross-session (cross-USER) content dedup for the ✅ ping. The
        # shell send hook pipes the ✅ TEXT on stdin (arbitrary quotes/backticks
        # never touch argv) and passes --owner/--project. Print "claim"
        # (deliver) or "dup" (suppress); ALWAYS exit 0 — a claim helper failure
        # must never break the send path (fail-open lives inside the function).
        from notify import content_dedup_claim
        try:
            text = sys.stdin.read()
        except (OSError, ValueError):
            text = ""
        sys.stdout.write(content_dedup_claim(
            text,
            owner=getattr(args, "owner_name", "") or "",
            project=getattr(args, "project", "") or ""))
        return

    if getattr(args, "channel_id", False):
        # #296: --kind questions resolves the owner's SEPARATE questions
        # thread; omitted/--kind default is the pre-#296 behaviour unchanged.
        # #330: "questions" goes through resolve_questions_channel(), which
        # additionally makes a not-yet-provisioned "-q" thread LOUD (a
        # distinguishable delivery-log line, not an indistinguishable "sent")
        # and SELF-HEALING (a guarded background provision attempt) instead
        # of silently falling back forever — the exact gap that let
        # gatekeeper's ❓ history route to the wrong thread with zero trace.
        # #369: --project resolves the owner's PER-PROJECT thread the SAME
        # way — but ONLY under --kind default; a project flag alongside
        # --kind questions is deliberately IGNORED (the questions thread
        # stays centralized, by design — see the ticket's own design
        # comment). `resolve_project_channel` is NOT side-effect-free (it
        # writes a "fallback" delivery-log line and may spawn a background
        # self-heal) — mirroring `send()`'s own documented split, a
        # --dry-run preview (the shell hook's DISCORD_NOTIFY_DRYRUN path)
        # must resolve via the plain, side-effect-free `notification_channel`
        # instead, or a mere PREVIEW call silently pollutes the delivery
        # log / spawns a real background process (regression caught by
        # test_notify_delivery_log.py's pre-existing
        # test_dry_run_is_not_a_failure_and_logs_nothing).
        kind = getattr(args, "kind", None) or "default"
        project = getattr(args, "project", None)
        dry_run = getattr(args, "dry_run", False)
        if kind == "questions":
            sys.stdout.write(resolve_questions_channel())
        elif project and not dry_run:
            sys.stdout.write(resolve_project_channel(project=project))
        elif project:
            sys.stdout.write(notification_channel(kind=kind, project=project))
        else:
            sys.stdout.write(notification_channel(kind=kind))
        return

    if getattr(args, "mirror_owners", False):
        # space-separated parallel/CC recipients for the current owner (shell path)
        sys.stdout.write(" ".join(mirror_owners()))
        return

    if getattr(args, "provision_question_thread", False):
        # #296: one-time setup — create (if missing) + persist the owner's
        # questions thread claude-<owner>-q into the local .env.
        # --owner-name is normalized THE SAME WAY resolve_owner() normalizes
        # its own result (adversarial-review finding): an un-normalized typo
        # like a trailing space would otherwise create a REAL Discord thread
        # and persist it under a dead .env key
        # ("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK _Q") that no reader ever
        # resolves — a silent misprovision, not a loud refusal.
        from notify import log_delivery, provision_question_thread, resolve_owner
        owner_name = getattr(args, "owner_name", None)
        owner = (re.sub(r"[^a-z0-9]", "", owner_name.strip().lower())
                if owner_name else resolve_owner())
        # #330: --find-only is the AUTOMATIC self-heal's own mode (never
        # auto-CREATE unattended) — omitted (the human-typed CLI default)
        # keeps calling provision_question_thread(owner) with NO extra
        # kwarg, so its call signature is byte-identical to before #330 for
        # every EXISTING caller.
        find_only = bool(getattr(args, "find_only", False))
        tid = (provision_question_thread(owner, create=False) if find_only
              else provision_question_thread(owner))
        if tid:
            sys.stdout.write(tid)
            return
        # #330 F7: the automatic background self-heal runs fully detached
        # (stdout/stderr both DEVNULL'd) — without this, its own failure
        # was completely invisible, forever, even though `resolve_questions_
        # channel`'s own "fallback" line already told the operator a self-heal
        # WAS attempted. This closes the loop: did it work?
        log_delivery("provision-failed", kind="questions", key=owner,
                     reason=("find-only, none visible" if find_only
                             else "find-and-create both failed"))
        print("notify: could not provision the questions thread for owner=%r"
             % owner, file=sys.stderr)
        sys.exit(1)

    if getattr(args, "provision_project_thread", False):
        # #369: one-time setup — create (if missing) + persist the owner's
        # PROJECT thread claude-<owner>-<project-slug> into the local .env.
        # Mirrors --provision-question-thread verbatim, including the SAME
        # --owner-name normalization (a typo must never silently create a
        # real Discord thread under a dead .env key) and the SAME
        # --find-only find-only mode.
        from notify import log_delivery, provision_project_thread, resolve_owner
        owner_name = getattr(args, "owner_name", None)
        owner = (re.sub(r"[^a-z0-9]", "", owner_name.strip().lower())
                if owner_name else resolve_owner())
        project = getattr(args, "project", None) or ""
        find_only = bool(getattr(args, "find_only", False))
        tid = (provision_project_thread(owner, project, create=False) if find_only
              else provision_project_thread(owner, project))
        if tid:
            sys.stdout.write(tid)
            return
        log_delivery("provision-failed", kind="project",
                     key="%s:%s" % (owner, project),
                     reason=("find-only, none visible" if find_only
                             else "find-and-create both failed"))
        print("notify: could not provision the project thread for "
             "owner=%r project=%r" % (owner, project), file=sys.stderr)
        sys.exit(1)

    if getattr(args, "project_label", False):
        # #369: the per-project routing/display LABEL for --cwd — the SAME
        # value used to route --channel-id --project and to name a
        # project's own Discord thread, so a hook computing this ONCE and
        # reusing it for both the header text and the routing call can
        # never have the two disagree.
        from notify import project_label_for
        sys.stdout.write(project_label_for(getattr(args, "cwd", "") or "."))
        return

    if getattr(args, "repo_name", False):
        # The GitHub repo NAME for a cwd, from its `origin` remote — never the
        # directory basename (marek's checkout is `parovanie_produktov` while
        # every marker is keyed `parovanie-produktov`). Used by
        # notify-discord-pending.sh's delivery-conditional suppression (#134).
        from notify import repo_name_for
        sys.stdout.write(repo_name_for(getattr(args, "cwd", "") or "."))
        return

    if getattr(args, "newest_card", False):
        # mtime of the newest DELIVERED card marker for a repo, or nothing.
        # "Delivered", not "claimed" — the marker is written before the POST
        # (#135), so presence alone would let a FAILED card suppress the
        # fallback ping, which is precisely the hole being closed.
        from notify import newest_delivered_card
        ts = newest_delivered_card(getattr(args, "repo", "") or "")
        if ts is not None:
            sys.stdout.write(repr(ts))
        return

    if getattr(args, "backfill_digest", False):
        _notify_backfill_digest(args, send)
        return

    if getattr(args, "run_card", False):
        _notify_run_card(args, compose_autopilot_card, send)
        return

    if getattr(args, "api_error", False):
        # #546: RETIRED. The Stop hook that used to call this is now a no-op
        # (hooks/notify-api-error.sh), so this branch is dead in production and
        # kept only as defence-in-depth: the `apierr:` dedup_key below is an
        # owner-suppressed alert class (SUPPRESSED_ALERT_PREFIXES), so send()
        # POSTs nothing and returns "suppressed". The composition still runs so
        # the false-positive guard (is_api_error) keeps its "normal prose → say
        # nothing" contract byte-for-byte.
        from notify import compose_api_error_alert, is_api_error
        text = args.text or ""
        if not is_api_error(text):
            return  # not a real API error → say nothing (no false ping)
        import hashlib
        project = args.project or ""
        sess = args.session or ""
        h = hashlib.sha1(text.strip().encode()).hexdigest()[:12]
        dedup = args.dedup_key or ("apierr:%s:%s" % (sess, h))
        body = compose_api_error_alert(project, text)
        print(send(body, dedup_key=dedup, dry_run=args.dry_run,
                   project=project or None))
        return

    if getattr(args, "autopilot_done", False):
        try:
            tickets = json.loads(args.tickets_json) if args.tickets_json else []
        except (ValueError, TypeError):
            print("notify: --tickets-json is not valid JSON", file=sys.stderr)
            sys.exit(1)
        body = compose_autopilot_card(
            repo=args.repo, tickets=tickets, pr=args.pr,
            version=args.version, merge_sha=args.merge_sha,
            review_ok=(args.review != "fail"),
            done=args.done, remaining=args.remaining)
        dedup = args.dedup_key
        if dedup is None and args.repo and args.pr:
            dedup = "%s#%s" % (args.repo, args.pr)
        # #369 review M1 (TRIGGERED): same ticket-work-scoped traffic as
        # --run-card's own project=stream_qualified(name) — a multi-ticket
        # completion card belongs on the project thread too.
        project = None
        if args.repo:
            from notify import stream_qualified
            project = stream_qualified(str(args.repo).rstrip("/").split("/")[-1])
        print(send(body, dedup_key=dedup, dry_run=args.dry_run, project=project))
        return

    if args.body is not None:
        # #334: --body is the general-purpose EXTERNAL send primitive (no
        # caller inside this repo itself uses it — only foreign scripts like
        # codex-bridge shell out to it). With no explicit owner it falls
        # entirely through to resolve_owner()'s tmux auto-detect, which is
        # correct for a caller genuinely tied to the current pane (the
        # ❓/✅ idle pings, autopilot cards) but WRONG for a headless/
        # detached caller whose ancestor process merely inherited an
        # unrelated pane's $TMUX — the claude-david misroute regression.
        # --owner-name (normalized the same way provision_question_thread
        # already normalizes it) lets such a caller pin the real owner
        # explicitly, bypassing tmux detection entirely. Omitting it keeps
        # today's exact behavior — send(owner=None) resolves internally.
        owner_name = getattr(args, "owner_name", None)
        owner = None
        if owner_name:
            owner = re.sub(r"[^a-z0-9]", "", owner_name.strip().lower())
            if not owner:
                # #334 adversarial-review MINOR-2: a normalized-empty
                # value (punctuation-only, etc.) must be REFUSED, never
                # silently passed through as owner="" — "" is not None,
                # so it would skip resolve_owner() entirely and send
                # mention-less to the shared channel, even overriding an
                # otherwise-correct AIRULESET_NOTIFY_OWNER. Validate and
                # refuse, never mangle (#198's own established rule).
                print("notify: --owner-name %r has no usable characters "
                     "after normalization — refusing rather than silently "
                     "falling back to no-owner routing" % owner_name,
                     file=sys.stderr)
                sys.exit(1)
        print(send(args.body, owner=owner, dedup_key=args.dedup_key,
                   dry_run=args.dry_run))
        return

    print("notify: nothing to send (use --autopilot-done, --run-card, --body, "
          "or --mention-prefix)", file=sys.stderr)
    sys.exit(1)


def _gh_out(*gh_args, timeout=8, cwd=None):
    """Best-effort `gh ...` stdout (stripped), or "" on any failure/timeout.

    Runs under `_gh_env()` — the SAME token resolution `cmd_tickets_status`
    has always used (#181 I-6, live-confirmed on david@subdev 2026-07-30).
    A reduced-authority stream box never runs `gh auth login` and carries no
    GH_TOKEN/GITHUB_TOKEN in its shell env; it authenticates per-command from
    ~/.git-credentials instead. Without that env a bare `gh` there exits 4
    ("To get started with GitHub CLI, please run: gh auth login"), so EVERY
    query through this helper failed closed — `slice-quals --count` printed
    "a gh query failed" and exited 1, which meant the fork-no-merge /goal
    template's condition (B) could NEVER hold on that box and the loop could
    never legitimately finish. A real token already in the env always wins,
    so this is a no-op on every box that has one.

    `cwd` runs gh inside a specific checkout (gh resolves the repo from the
    git remote there), so a caller that resolved the repo ROOT does not
    depend on the process cwd happening to be it (#181 I-5)."""
    import subprocess
    try:
        r = subprocess.run(["gh", *gh_args], capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, env=_gh_env())
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# #370: the whole fleet shares ONE GitHub account → ONE 5000/h GraphQL bucket,
# and `gh issue list`/`gh repo view` are BOTH `POST /graphql` (measured), so
# every periodic footer refresh draws on that single shared budget. Default
# floor: yield cosmetic reads once >80% of the shared budget is spent, keeping
# ~1000 headroom for the FUNCTIONAL calls (`gh issue create`, autopilot,
# run-cards) that fail when the bucket hits 0. Env-tunable for the fleet.
GH_GRAPHQL_REFRESH_FLOOR = 1000


def _gh_graphql_floor():
    """The graphql-budget floor below which cosmetic periodic gh work yields,
    from `AIRULESET_GH_GRAPHQL_FLOOR` (default `GH_GRAPHQL_REFRESH_FLOOR`).
    A non-integer/negative override falls back to the default rather than
    disabling the guard by accident."""
    try:
        v = int(os.environ.get("AIRULESET_GH_GRAPHQL_FLOOR", ""))
        return v if v >= 0 else GH_GRAPHQL_REFRESH_FLOOR
    except (TypeError, ValueError):
        return GH_GRAPHQL_REFRESH_FLOOR


def _graphql_budget_ok(floor, cwd=None, runner=None):
    """`(ok, remaining)` for the shared GraphQL rate-limit bucket — whether it
    has at least `floor` calls left, read from GitHub's FREE `rate_limit`
    endpoint (`gh api rate_limit` does NOT count against any bucket — measured:
    graphql.remaining is unchanged across the call).

    Fails OPEN: on ANY probe failure or unparseable payload it returns
    `(True, None)` so the caller proceeds EXACTLY as it does today. The guard
    only ever tells a caller to SKIP on POSITIVE evidence of a low budget —
    it is a pure-additive optimisation, never a new failure mode. (`rate_limit`
    is itself never rate-limited, so a probe failure means a genuine
    connectivity/auth problem, in which case the expensive calls would fail
    too and the existing error path already handles that.)

    On an App-token box (david2/3/4, #356) the token has its OWN 5000/h bucket,
    so this reads that box's own budget and gates it independently — exactly
    the right per-box behaviour, no special-casing needed."""
    run = runner or _gh_out
    raw = run("api", "rate_limit", cwd=cwd, timeout=8)
    try:
        remaining = int(json.loads(raw)["resources"]["graphql"]["remaining"])
    except (ValueError, TypeError, KeyError):
        return (True, None)
    return (remaining >= floor, remaining)


def _repo_slug(cwd=None):
    """`owner/repo` via `gh repo view`, or "" on any failure — the REST-API
    slug a hand-off comment-fallback fetch needs (`repos/<slug>/issues/N/
    comments`). Shared by `cmd_tickets_status`'s footer and
    `cmd_slice_quals`'s `/goal` stop-proof (#391 consistency guard) so a
    comment-fallback recovery a reduced-authority box's stop-proof relies on
    cannot resolve a different repo than the one the footer resolves."""
    return _gh_out("repo", "view", "--json", "nameWithOwner",
                   "-q", ".nameWithOwner", cwd=cwd, timeout=20)


def _write_autopilot_progress(name, remaining, bump_done=True):
    """Persist per-repo autopilot run progress (~/.claude/autopilot-progress/
    <repo>.json). `done` counts the completion cards sent within ONE run
    window; a card after a ≥6h gap starts a new run.

    UPDATED (#367, 2026-08-11): this file no longer feeds the statusline
    footer at all — `tickets_segment()` (statusbar.py) dropped its own read
    of this cache entirely, along with the `run D/T` render it used to
    produce, since it duplicated the SAME live backlog the idle `I N` form
    already showed. The file itself, and everything this function does, is
    UNCHANGED: it is still written on every completion card, and watchdog
    job 20 still reads it as goal-armed evidence (a fresh `ts` here is one
    of the signals it uses to judge whether a session is genuinely still
    working). The history below (why the write is guarded the way it is)
    stays accurate — it is about THIS function's own correctness, not about
    what used to render from its output.

    `bump_done=False` (#181 I6, round 2 regression fix) writes the HEARTBEAT
    (`ts`) and `remaining` WITHOUT incrementing `done`. This is the ONLY
    writer of `ts`, which is what keeps the 6h AUTOPILOT_RUN_WINDOW_S run
    window alive. #164's original fix SKIPPED CALLING this function entirely
    for a full-authority box's stream-ticket card, which correctly kept
    `done` from inflating but ALSO stopped refreshing `ts` — a run that
    cards only sub-dev stream tickets never opened/refreshed a progress
    file at all. Always call this function; use `bump_done` to separate the
    two concerns instead of skipping the call.

    A heartbeat-only write REFRESHES an already-live run window; it never
    OPENS one (#164 M-1, round 3). With no prior progress file — or one whose
    `ts` has fallen outside AUTOPILOT_RUN_WINDOW_S, which zeroes `base_done` —
    an unconditional heartbeat wrote `{"done": 0, "ts": now}`: an assertion
    that a run is active and has achieved nothing. Round 2 fixed a heartbeat
    that stopped too early by introducing one that started too eagerly.

    Round 4 corrects the REASON this docstring used to give for declining that
    write. It used to justify the guard by asserting that the run a heartbeat
    exists to keep alive must already have a progress file inside the window;
    that is FALSE for a review-only gatekeeper run,
    whose cards are all sub-dev stream tickets, so nothing ever sets
    `bump_done` and no file is ever created — a shape the obligation-set
    change makes more common, since a gatekeeper's obligation set is
    explicitly the action-only tickets. The real reason is simpler and holds
    everywhere: a heartbeat carries no evidence of CORE progress, so opening a
    window on one would assert `0/N` — an active run that has achieved nothing
    — for a full 6h window, which is M-1 verbatim.

    The accepted consequence, deliberately NOT "fixed": a review-only run's
    progress file never opens at all until the first core-scoped card lands —
    job 20 sees no run-in-progress evidence for such a run until then, which
    is correct, since a heartbeat alone is not evidence of finished work.

    Best-effort — a failure here never blocks the card send."""
    import re
    import time
    import statusbar
    try:
        name = re.sub(r"[^A-Za-z0-9._-]", "", str(name or "")).lstrip(".")
        if not name:
            return
        d = statusbar.progress_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / (name + ".json")
        now = int(time.time())
        try:
            prev = json.loads(p.read_text())
        except (OSError, ValueError):
            prev = None
        if not isinstance(prev, dict):
            prev = None
        within_window = bool(
            prev and now - (prev.get("ts") or 0) <= statusbar.AUTOPILOT_RUN_WINDOW_S)
        if not bump_done and not within_window:
            return          # refresh a live run, never open one (#164 M-1)
        base_done = int(prev.get("done") or 0) if within_window else 0
        done = base_done + 1 if bump_done else base_done
        if not isinstance(remaining, int):
            remaining = prev.get("remaining") if prev else None
        tmp = str(p) + ".tmp"
        Path(tmp).write_text(json.dumps({"done": done, "remaining": remaining,
                                         "ts": now}))
        os.replace(tmp, p)
    except Exception:
        pass


def _gh_env():
    """Env for the `git`/`gh` subprocess calls in tickets-status --refresh, with
    a fallback GH_TOKEN extracted from ~/.git-credentials when the shell has
    none (#25). A reduced-authority sub-dev stream (david) never runs
    `gh auth login` — its CLAUDE.md External Developer Workflow authenticates
    per-command by extracting the token from ~/.git-credentials instead. Without
    this, every `gh` call in that shell fails silently and the cache is stuck
    at open=None forever. A real GH_TOKEN/GITHUB_TOKEN already in the env
    always wins — never overridden by a stale credentials-file token.

    #401: on an App-token box (odoo-erp#3281's gh-app-stream-tokens mechanism
    — david2-4, marek, montalu/2/3/4) ~/.git-credentials can INDEPENDENTLY
    hold a one-shot snapshot of a 60-minute App installation token, written
    once by whatever process last wrote it and never refreshed — while the
    box's real live-refresh path (~/.config/gh-app-tokens/, refreshed every
    45 min by a gatekeeper timer) sits right next to it. Live-diagnosed on
    montalu3@subdev 2026-08-12: the .git-credentials line was ~11.5h stale
    (App tokens live 60 min — unconditionally dead) while
    ~/.config/gh-app-tokens/primary was ~31 min old and fully live.

    Detected via `_is_gh_app_token_box()` (#356's existing, local/static
    directory-presence signal `_slice_quals()` already uses — no network
    call), such a box reads the FRESH per-call token file instead of the
    corpse .git-credentials snapshot. When no fresh token has been
    delivered yet (timer lag / mid-provisioning), this deliberately does
    NOT `return` early — it FALLS THROUGH to the same .git-credentials
    logic every other box uses (adversarial review of this fix, MAJOR-1):
    `_is_gh_app_token_box()`'s own docstring already documents a known
    residual — a STRAY/leftover App-token dir can exist on a genuine
    own-account PAT box (a misdirected delivery, or an App->PAT migration
    leftover) — and an unconditional early-return there would silently kill
    that box's real, working PAT auth the moment such a stray dir appears.
    The x-access-token belt below still refuses a genuine App-token corpse
    either way, so falling through never resurrects the #401 bug — it only
    ever additionally *finds* a real credential when one legitimately
    exists.

    As a second, independent belt — reached on EVERY box, App-token or
    not — a .git-credentials line whose username is literally
    `x-access-token` is never treated as authoritative: on these managed
    boxes that username is the FIXED placeholder the App-token mechanism
    always uses (confirmed against the real `git-credential-gh-app.sh` in
    zbynekdrlik/odoo-erp) and is therefore always a corpse snapshot of an
    App installation token here, never a genuine hand-issued PAT — refused
    even if the directory-presence detector somehow disagrees (a
    relocated/renamed GH_APP_TOKEN_DIR, a box mid-migration). The scan
    tries every github.com credential line in the file (not just the
    first), so an x-access-token corpse earlier in the file can never hide
    a genuine PAT recorded later in it.

    `primary`'s own freshness is deliberately NOT re-checked here (no mtime
    gate) — this mirrors the real, deployed `gh-app-token.sh`'s own stance
    that expiry is "advisory only ... there to make a stale-token failure
    diagnosable, rather than to gate the read"; a dead `primary` still
    surfaces as a real, loud auth failure downstream, never a silent false
    0 (same "not a reliable 0" contract as everywhere else in this file)."""
    import re

    env = os.environ.copy()
    if env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"):
        return env
    if _is_gh_app_token_box():
        try:
            token = (_gh_app_token_dir() / "primary").read_text().strip()
        except (OSError, ValueError):
            # ValueError also catches UnicodeDecodeError -- a `primary` a
            # timer is rewriting mid-read (or a genuinely corrupt file)
            # must degrade to "no token found", never crash the caller.
            token = ""
        if token:
            token = token.splitlines()[0]      # never smuggle a 2nd line
        if token:
            env["GH_TOKEN"] = token
            return env
        # No usable token file yet -- fall through to .git-credentials
        # below rather than unconditionally disabling auth (see docstring).
    try:
        text = (Path.home() / ".git-credentials").read_text()
    except OSError:
        return env
    for m in re.finditer(r"https://([^:/@\s]+):([^@\s]+)@github\.com(?:[/\s]|$)",
                         text):
        if m.group(1) != "x-access-token":
            env["GH_TOKEN"] = m.group(2)
            break
    return env


# #313 pt 2: bound on the per-candidate READY-FOR-REVIEW comment fallback
# check inside cmd_tickets_status's reduced-authority refresh -- mirrors the
# existing GraphQL re-attribution bound (`to_check[:50]`) so one repo with an
# unusually large "mine" slice can never blow the refresh's own budget.
_HANDOFF_COMMENT_CHECK_LIMIT = 40

# #313 pt 2 adversarial review MAJOR-2: a bare `"ready-for-review" in
# body.lower()` substring check re-introduces the EXACT over-match incident
# `skills/process-subdev/templates/subdev-handoff-match.sh` (#1500) was
# written to fix -- a GATEKEEPER finding/review comment, or any comment
# merely MENTIONING the marker mid-sentence (live incident 2026-07-14 on
# odoo-erp#1489: "**Po READY-FOR-REVIEW pokračujem hneď.**"), falsely reads
# as a hand-off. These three regexes are a direct Python port of that
# script's own matching contract -- case-SENSITIVE and line-anchored, same
# as its `grep -E` (no `-i`). #340 review MAJOR-2: the gatekeeper-opening
# exclusion also recognises a markdown-HEADING opening ("## Gatekeeper
# review — BOUNCE", the real odoo-erp#2878 corpus shape), matching the
# shell script's own #340 widening -- kept as an alternation branch on the
# SAME compiled pattern (a single `re.match`, no locale/grep concerns
# apply to Python's `re`), requiring the corpus's exact observed casing
# ("Gatekeeper", title case) so a sub-dev's own heading-decorated
# ALL-CAPS marker never collides, exactly like the shell script.
_READINESS_GATEKEEPER_FIRST_LINE_RE = re.compile(
    r"^\s*(?:\*\*GATEKEEPER|#{1,6}\s*Gatekeeper)")
_READINESS_LINE_RE = re.compile(
    r"^\s*([#*_-]+\s*)?READY-FOR-REVIEW", re.MULTILINE)
_READINESS_CROSS_FORK_RE = re.compile(
    r"Ready for gatekeeper cross-fork review[.!]?\s*$", re.MULTILINE)


def _is_readiness_comment(body):
    """Is `body` a GENUINE hand-off comment, per the same contract
    `subdev-handoff-match.sh` (#1500) already enforces for the repo's own
    hand-off-label workflow? A GATEKEEPER-authored comment never counts,
    whatever it contains; otherwise a hand-off needs either a line starting
    with (optional markdown emphasis/header/list prefix +) READY-FOR-REVIEW,
    or the closing cross-fork-review phrase ending a line. Anything else
    (a mid-sentence mention, a quoted `> READY-FOR-REVIEW`, review prose
    asking for one) is NOT a hand-off."""
    if not isinstance(body, str) or not body:
        return False
    first_line = body.split("\n", 1)[0]
    if _READINESS_GATEKEEPER_FIRST_LINE_RE.match(first_line):
        return False
    if _READINESS_LINE_RE.search(body):
        return True
    return bool(_READINESS_CROSS_FORK_RE.search(body))


def _comment_readiness_signal(body):
    """Per-comment signal for the fallback's LAST-VERDICT-WINS walk over a
    ticket's comments, in creation order (#313 pt 2 adversarial review
    round 2, F1/F2): True = a genuine hand-off comment; False = an
    EXPLICIT GATEKEEPER-authored rejection (a bounce's own review/finding
    comment, which overrides an EARLIER True from a now-stale hand-off);
    None = neutral -- an unrelated comment ("still working on it") must
    NEVER reset an already-established verdict, only a GATEKEEPER-authored
    one is a genuine negative signal. Reading comments in order and
    keeping the LAST signal (rather than stopping at the first match) is
    what lets a genuine post-bounce re-hand-off comment correctly override
    a stale pre-bounce one, without needing the bounce's own timestamp."""
    if not isinstance(body, str) or not body:
        return None
    first_line = body.split("\n", 1)[0]
    if _READINESS_GATEKEEPER_FIRST_LINE_RE.match(first_line):
        return False
    if _is_readiness_comment(body):
        return True
    return None


# #589: the queue labels whose REMOVAL is a gk-resolution signal. A gatekeeper
# who reviews + merges + releases a hand-off REMOVES these when done (the live
# odoo-erp #4502 shape) -- the `unlabeled` timeline event for one of these,
# occurring AFTER the last READY-FOR-REVIEW comment, is the fallback's END
# CONDITION (see `_timeline_handoff_signal`). Same set as cli_quals'
# MAINTAINER_ACTION_LABELS -- kept local to this module so the pure signal
# helper has no cli_quals import; a parity lock test (test_gk_comment_end_
# condition_589) asserts the two sets stay equal so a future third queue label
# can't silently desync them. `prio:bounce` is deliberately NOT here (a bounce
# is handled separately via `bounce_numbers`, not as a resolution).
_HANDOFF_QUEUE_LABELS = ("ready-for-review", "needs-gatekeeper")


def _timeline_handoff_signal(ev):
    """#589 END CONDITION: per-TIMELINE-EVENT signal for the fallback's
    LAST-VERDICT-WINS walk over a ticket's issue TIMELINE (which, unlike
    `/comments`, carries label + close events alongside the comment bodies).
    Returns `(verdict_signal, is_gatekeeper_comment)`:

      verdict_signal -- True = a genuine READY-FOR-REVIEW hand-off comment;
        False = a NEGATIVE (unhandled) signal, either an EXPLICIT
        gatekeeper FINDING comment (the pre-existing #313 signal) OR a
        gk-RESOLUTION EVENT (a `ready-for-review`/`needs-gatekeeper` queue
        label REMOVED, or the issue CLOSED -- the #4502 shape where the gk
        finished the hand-off and left NO finding comment); None = neutral.

      is_gatekeeper_comment -- True ONLY for a genuine gatekeeper FINDING
        COMMENT, never for a resolution event. This preserves the #391
        CRITICAL-1 bounce-visibility gate byte-for-byte: a `prio:bounce`
        row may only be re-upgraded to handed when a VISIBLE gatekeeper
        COMMENT was seen, and a label-removal / close event must never
        satisfy that gate.

    Walking the timeline in chronological order and keeping the LAST non-None
    verdict is what makes the END CONDITION work with zero extra ordering
    logic: a resolution event AFTER the hand-off comment overwrites True with
    False (the hand-off is done), while a later comment-only RE-hand-off
    (fork-no-merge 403 / broken auto-labeller -- the legitimate case the
    fallback exists for) overwrites it back to True. A `labeled
    ready-for-review` event is NOT a positive signal here: a ticket that
    reaches the fallback has no CURRENT queue label (else `label_handed` is
    already True and it never enters the candidate walk), so a historical
    add is irrelevant -- only the comment and the resolution events matter.

    The `unlabeled` resolution is deliberately ACTOR-AGNOSTIC: any actor
    removing the queue label reads as resolution, not only the gatekeeper.
    A broken auto-labeller that spuriously removes the label would flip a
    still-live hand-off to unhandled -- but that is the SAFE direction (the
    ticket returns to the stream's own workable `I`, where the stream re-hands
    it off with a fresh comment that flips it back), and identifying "the
    gatekeeper" on a shared-account box is itself unreliable, so an
    actor-agnostic label-removal signal is both simpler and safe.

    THREE accepted residuals (all fail-SAFE -- toward the stream's court, never
    a false stop): (1) the caller reads only the FIRST timeline page
    (`per_page=100`, oldest-first) to stay at zero added gh calls, so a
    resolution beyond event 100 on a hyper-active ticket is missed and its
    stale comment keeps counting as gk -- never WORSE than the pre-#589
    `/comments` window (which read only the oldest ~30 comments), and the last
    page cannot be fetched without an extra per-ticket call (the query
    explosion #589's cost constraint forbids). (2) `closed` fires on ANY close,
    so an accidental stream close+reopen (with no re-hand-off comment after)
    reads the ticket as resolved -- correct enough (a reopened ticket with no
    fresh hand-off IS back in the stream's court, not parked with gk; a fresh
    hand-off comment after the reopen flips it back). (3) a gk resolution that
    leaves NO issue-side timeline event at all (a fork PR merged with the issue
    left OPEN and its queue label never present) stays counted as gk -- rare
    (a fork-no-merge resolution normally CLOSES the issue, which drops it from
    the open-state slice), and detecting a merged referenced PR needs a per-PR
    call the timeline `cross-referenced` embed does not carry.
    """
    if not isinstance(ev, dict):
        return None, False
    etype = ev.get("event")
    if etype == "commented":
        sig = _comment_readiness_signal(ev.get("body"))
        return sig, (sig is False)
    if etype == "unlabeled":
        name = (ev.get("label") or {}).get("name")
        if name in _HANDOFF_QUEUE_LABELS:
            return False, False   # gk cleared the queue label -- resolution
        return None, False
    if etype == "closed":
        return False, False       # gk closed/resolved -- resolution
    return None, False


def cmd_tickets_status(args):
    """Statusline github-tickets segment. Default: PRINT the segment for --cwd
    (composed from local caches; may spawn a detached refresh). --refresh: the
    SLOW path — resolve the repo at --cwd via git+gh and rewrite its cache
    (~/.claude/tickets-status/). The statusline shim never runs the slow path
    inline; it reads the caches and lets this command refresh in the background."""
    import subprocess
    import time
    import statusbar

    cwd = getattr(args, "cwd", None) or os.getcwd()
    if not getattr(args, "refresh", False):
        sys.stdout.write(statusbar.tickets_segment(cwd))
        return

    # #689 hygiene: sweep dead-worktree / long-stale cache entries FIRST — a
    # pure local FS op, so it runs unconditionally (before the gh block AND
    # before the #370 graphql-budget early return, which serves stale cache).
    # sweep_stale_cache is contractually never-raise; the guard is defense-in-
    # depth on the statusline hot path (logs, never breaks a refresh).
    try:
        statusbar.sweep_stale_cache()
    except Exception as e:
        sys.stderr.write("tickets-status: cache sweep skipped (%s)\n" % e)

    gh_env = _gh_env()

    def _out(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20, env=gh_env)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    entry = {"ts": int(time.time()), "open": None, "name": "", "root": ""}
    # ONE root definition, shared with cmd_slice_quals / cmd_core_quals (#181
    # I-5) — including #61's fallback for a session cwd that is the PARENT of
    # the actual repo (montalu's ~/devel/odoo, repo at ~/devel/odoo/odoo-erp).
    root = _repo_root(cwd, runner=_out)
    if root:
        entry["root"] = root
        # #370: before ANY of the ~5 GraphQL calls below (repo view + the
        # per-qual `_union_open_issues` searches + the skipped query, all
        # `POST /graphql`), yield if the SHARED graphql bucket is low. The
        # probe is FREE (`gh api rate_limit`), so this adds zero quota; on a
        # low budget we make zero GraphQL calls and leave the existing cache
        # untouched, so the footer serves the last-known counts instead of
        # draining the last of the shared budget on a cosmetic read (the
        # functional calls — gh issue create / autopilot / run-cards — keep
        # their headroom). Fails OPEN, so an unmeasurable budget proceeds
        # exactly as before. A later render re-probes (free) under the same
        # 120s TTL + 30s spawn-guard cadence and resumes real refreshes once
        # the budget recovers.
        floor = _gh_graphql_floor()
        budget_ok, remaining = _graphql_budget_ok(floor, cwd=root)
        if not budget_ok:
            print("skipped: graphql budget low (remaining=%s < floor=%s) — "
                  "served stale cache" % (remaining, floor))
            return
        slug = _out(["gh", "repo", "view", "--json", "nameWithOwner",
                     "-q", ".nameWithOwner"], root)
        entry["name"] = slug.rstrip("/").split("/")[-1] if slug else ""
        # A reduced-authority stream (sub-dev box: david/montalu/marek — resolved
        # marker-aware via resolve_authority) counts only ITS OWN slice: open,
        # non-skip, assigned-to-me OR authored-by-me. The full repo backlog on a
        # sub-dev statusline was noise ("Issues 16" where David's slice is 6 —
        # gatekeeper goal, 2026-07-11). Full-authority boxes keep the full count.
        # The counter's meaning on EVERY box: "tickets THIS box should work via
        # /autopilot so they don't rot" (stream-label ownership convention — odoo-erp
        # PR #1440: stream:<name> labels a sub-dev-owned ticket; unlabeled = core).
        if resolve_authority(cwd=root) != "full":
            entry["scope"] = "mine"
            # Own slice = assigned-to-me ∪ authored-by-me ∪ labeled stream:<me>,
            # partitioned into own UNHANDLED work vs already HANDED OFF to the
            # gatekeeper: a ticket carrying `ready-for-review` (auto-labeled
            # by the repo's subdev-handoff-label workflow at the hand-off
            # comment) is waiting on the gatekeeper — the statusline shows
            # both ("Issues 1 · gk 5"). SHARED-ACCOUNT boxes (montalu's PAT
            # logs in as the MAINTAINER account) must NOT use @me —
            # author:@me matched every user-authored ticket and the footer
            # showed foreign streams' numbers (2026-07-20); there the slice
            # is the stream LABEL alone.
            #
            # #391 (2026-08-11) REVERSES the prior "N is the FULL slice"
            # choice for this reduced-authority path ONLY: a sub-dev's own
            # responsibility for a ticket is fulfilled the moment it is
            # HANDED OFF, not once the gatekeeper has also closed it — the
            # full-authority side (below) is unchanged, since THAT box is
            # the one still on the hook for a hand-off until it acts on it.
            # `_slice_mine_and_handed` is the SAME shared derivation
            # `cmd_slice_quals`'s own `/goal` stop-proof calls (#391
            # consistency guard, mirroring the guard already established for
            # the full-authority obligation set) — never a parallel
            # re-derivation that could silently drift from it.
            #
            # SliceUnresolved (#181 I-2): an unresolvable gh identity is a gh
            # ERROR, and a gh error is never an empty slice — keep open=None,
            # exactly as a failed query already does. Guessing a qual set here
            # would show a shared-account box the maintainer's whole backlog.
            try:
                quals = _slice_quals(_current_user(), root)
            except SliceUnresolved:
                quals, rows, handed, failed = [], {}, {}, True
            else:
                rows, handed, failed = _slice_mine_and_handed(quals, root, slug)
            # #468: partition the tickets parked on the USER's answer
            # (needs-answer/needs-decision) OUT of the workable slice FIRST, then
            # split the workable remainder into own-unhandled (I N) vs handed-off
            # (gk). A user-waiting ticket is the user's responsibility, counted in
            # `U N`, never in `I N` or `gk`. ONE partition of the SAME fetch
            # `slice-quals --count` uses (#391/#367 consistency guard).
            if failed:
                entry["open"] = None
                entry["gk"] = None
                entry["user_waiting"] = None
                entry["ops_wait"] = None
            else:
                # #510: partition ops-wait (external-event/evidence) tickets OUT
                # of the workable slice too, alongside #468's user-waiting split —
                # both leave `I N`/`gk`, surfacing as `U N`/`W N`. ONE partition of
                # the SAME fetch `slice-quals --count` uses (#367/#468 guard). `gk`
                # is the handed-off subset of the WORKABLE remainder only, so a
                # ticket that is BOTH handed-off AND parked (user-waiting/ops-wait)
                # is counted in its parked bucket (`U`/`W`), never `gk` — the same
                # surface treatment #468 already gives a handed + user-waiting row.
                # #622: a bare needs-acceptance → U unconditionally (queued for
                # owner approval, never dispatchable-now I). Pure label partition;
                # the queued/delivered display distinction lives on the on-demand
                # `--waiting` path, never this hot footer refresh. #654:
                # own_stream keeps THIS box's OWN stream rows in its own U.
                workable_rows, waiting, ops_wait = _partition_workable(rows, own_stream=_current_user())
                gk = sum(1 for n_num in workable_rows if handed.get(n_num))
                entry["open"] = len(workable_rows) - gk
                entry["gk"] = gk
                entry["user_waiting"] = len(waiting)
                entry["ops_wait"] = len(ops_wait)
            # Skipped bucket (2026-07-16): same slice quals, POSITIVE label
            # filter — how many of MY tickets are excluded from autopilot runs.
            # `quals` empty ⟺ SliceUnresolved above (it is otherwise always 1
            # or 3 quals), so the skipped bucket stays None for the same
            # reason the open count does.
            skipped, sfailed = set(), not quals
            for qual in quals:
                raw = _out(["gh", "issue", "list", "--state", "open", "--search",
                            "label:autopilot-skip " + qual, "-L", "200",
                            "--json", "number"], root)
                try:
                    skipped.update(x["number"] for x in json.loads(raw))
                except (ValueError, TypeError, KeyError):
                    sfailed = True   # gh error ≠ zero skips — keep skipped=None
            entry["skipped"] = None if sfailed else len(skipped)
        else:
            # Full-authority (core/gatekeeper) box: N = the LIVE OBLIGATION
            # set — the SAME `_obligation_quals()` union `core-quals --count`
            # itself computes for the `/goal` stop-proof (#367 consistency
            # guard: never a parallel derivation, so an ORDINARY code change
            # cannot make the footer and the loop's stop condition silently
            # drift apart the way they used to. Not an infallibility claim:
            # `core-quals --count` additionally REFUSES an untrustworthy
            # empty result — a broken search index, #181 round 4 — while
            # this cache can still record a plain `0` from the same
            # condition; that gap pre-dates #367 and is unchanged by it.
            # `_obligation_quals()` already folds `_core_search_excl()`
            # (the core partition — the whole backlog MINUS the sub-dev-owned
            # stream:<user> tickets, each reduced stream in AUTHORITY_BY_USER)
            # together with every open ticket carrying a MAINTAINER_ACTION_LABELS
            # label (needs-gatekeeper/ready-for-review, regardless of which
            # stream owns it) — so the whole-repo `gk-req`/`streamy` queries
            # #367 dropped from the footer are no longer needed: their
            # populations are already counted inside N.
            entry["scope"] = "core"
            excl = _core_search_excl()
            seen, u_failed = _union_open_issues(_obligation_quals(),
                                                AUTOPILOT_SKIP_EXCL, cwd=root)
            # #468: user-waiting tickets (needs-answer/needs-decision) leave the
            # workable `I N` and surface as `U N` — ONE partition of the SAME
            # fetch the /goal stop-proof (`core-quals --count`) uses, never a
            # parallel query that could drift (#367 lesson).
            if u_failed:
                entry["open"] = None
                entry["user_waiting"] = None
                entry["ops_wait"] = None
            else:
                # #510: ops-wait leaves the workable `I N` alongside #468's
                # user-waiting split (both surface as their own footer buckets —
                # `U N`/`W N`). ONE partition of the SAME fetch the /goal
                # stop-proof (`core-quals --count`) uses (#367/#468 guard).
                # #622: bare needs-acceptance → U unconditionally (queued for owner
                # approval, never dispatchable-now I).
                workable, waiting, ops_wait = _partition_workable(seen)
                entry["open"] = len(workable)
                entry["user_waiting"] = len(waiting)
                entry["ops_wait"] = len(ops_wait)
            # Skipped bucket (2026-07-16): the POSITIVE label query over the
            # CORE partition — how many tickets are excluded from autopilot.
            # #367 left this scoped to the core partition (unchanged) rather
            # than the wider obligation set — never named as needing a
            # semantic change, and a maintainer-action-labelled ticket owned
            # by another stream was never something THIS box would
            # autopilot-skip in the first place.
            s = _out(["gh", "issue", "list", "--state", "open", "--search",
                      "label:autopilot-skip " + excl, "-L", "1000",
                      "--json", "number", "-q", "length"], root)
            try:
                entry["skipped"] = int(s)
            except (TypeError, ValueError):
                entry["skipped"] = None
    cache = statusbar.cache_dir() / (statusbar.cwd_key(cwd) + ".json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(cache) + ".tmp"
    Path(tmp).write_text(json.dumps(entry))
    os.replace(tmp, cache)
    print("refreshed open=%s name=%s" % (entry["open"], entry["name"] or "-"))


def _ensure_origin_label_usable(gh_fn, label, R):
    """Best-effort idempotent `gh label create` — returns True when `label`
    is confirmed usable (already existed, or was just created), and logs
    (never raises) on any failure. #191 Part C: closes the data gap
    `_last_origin_owner` (statusbar's `cmd_tickets_status`) relies on — a
    repo seeing this stream's FIRST ticket ever has no `handed-by:<user>`
    label yet, and `--add-label`/`--label` on a nonexistent label errors
    rather than creating it.

    #191 adversarial review, MAJOR M1 (live-verified against
    zbynekdrlik/odoo-erp's real `stream:*` labels — each stream had its own
    hand-curated colour + Slovak description): the original version used
    `--force`, which OVERWRITES an EXISTING label's colour/description on
    EVERY call, unconditionally. Read-only existence check FIRST — an
    already-existing label (this marker's own prior run, or any label that
    happens to share the name) is never touched.

    This is the ONE place that failure is allowed to be loud: gk-request's
    PRIMARY job (the needs-gatekeeper hand-off) must never be blocked by
    this best-effort enrichment, so callers only attempt the origin-label
    APPLY once this returns True."""
    have = gh_fn(["gh", "label", "list", "--search", label,
                  "--json", "name", "-q", ".[].name"] + R)
    if have.returncode == 0 and label in (have.stdout or "").splitlines():
        return True   # already exists — never touch its colour/description
    r = gh_fn(["gh", "label", "create", label, "--color", "5319e7",
               "--description",
               "Sub-dev stream hand-off origin marker (airuleset#191)"] + R)
    if r.returncode != 0:
        print("gk-request: could not ensure origin label %r exists: %s"
              % (label, (r.stderr or "").strip()))
        return False
    return True


def cmd_gk_request(args):
    """Stream→supervisor action request (#30): file (or mark) the ticket that
    asks the gatekeeper/supervisor for an action the stream cannot perform
    itself (box access, workflow re-dispatch, infra). Canonical form = label
    `needs-gatekeeper` in the upstream repo; a stream whose PAT cannot label
    degrades AUTOMATICALLY to the `GATEKEEPER-ACTION:` title/comment prefix,
    which the watchdog's job-11 query also matches (the supervisor adds the
    label on pickup). Delivery to the supervisor is the watchdog's job — the
    stream files and keeps working; no user middleman, no ssh to foreign
    boxes.

    #191 Part C: when this box IS a registered sub-dev stream
    (`_own_handoff_label()`), it ALSO best-effort applies its own
    `handed-by:<user>` label at the moment of filing/hand-off — origin
    provenance for `cmd_tickets_status`'s own-slice recovery
    (`_last_origin_owner`), which can otherwise never tell a handed-off
    ticket apart from anyone else's once GitHub's shared-identity author
    field is the only signal left. Best-effort in both directions: never
    blocks the needs-gatekeeper hand-off, never applied at all on a
    full-authority box (a `handed-by:newlevel`/`handed-by:gatekeeper` label
    would be meaningless).

    #191 adversarial review, CRITICAL C1: the marker is deliberately
    `handed-by:<user>`, NEVER `stream:<user>` — `stream:<user>` is the
    repo's OWNERSHIP primitive (`_slice_quals`/`_core_search_excl`/
    `_row_action` all key on it), and a needs-gatekeeper ticket tagged with
    it would become PERMANENTLY part of the stream's own `/goal` stop-proof
    slice (`slice-quals --count` could never reach 0 while one is open —
    #181's never-stops failure at a new address) and would be misclassified
    `implement` instead of `action-only`. `handed-by:*` is invisible to all
    three of those ownership consumers by construction, so a gk-request
    ticket is recoverable in the FOOTER (via `_last_origin_owner`) without
    ever entering the stop-proof's own slice or the gatekeeper's own core
    count (#191 M2).

    VERIFIED DELIVERY — always use THIS command, NEVER hand-write a
    `GATEKEEPER-ACTION` comment (#551). This is the delivery-verified-by-
    construction hand-off path: it applies `needs-gatekeeper` DIRECTLY
    (`gh issue edit --add-label` — a direct API write, immediately
    observable, NOT dependent on the repo's comment-triggered auto-label
    workflow), and on a label-permission 403 it degrades to a PROPER
    `GATEKEEPER-ACTION: ` prefix in BOTH a comment and the TITLE, which the
    watchdog's job-11 `in:title` query catches. A hand-written raw comment
    delivers NONE of that: the repo auto-label workflow matches ONLY a
    line-start `GATEKEEPER-ACTION:` (so a MUTATED shape like
    `GATEKEEPER-ACTION (spresnenie …):` — a parenthetical before the colon —
    silently produces no label), and job 11 never scans comment bodies, so
    the request is invisible to the gatekeeper queue and the stream parks on
    a NEVER-DELIVERED hand-off (the miva1 incident, odoo-erp issue 3244).
    If a raw marker comment is ever unavoidable, do a bounded post-check:
    confirm `needs-gatekeeper` (or the `GATEKEEPER-ACTION:` title) is
    observable within a few minutes, and re-file via this command if not.
    The watchdog's job-36 orphan-marker backstop
    (`watchdog/cross_stream.py::gk_orphan_marker_sweep`) is a supervisor-side
    safety net for a slip, not a substitute for using this command."""
    import subprocess

    def _gh(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=30)
        except Exception as e:
            return subprocess.CompletedProcess(argv, 1, "", str(e))

    repo = getattr(args, "repo", None)
    R = ["-R", repo] if repo else []
    stream_label = _own_handoff_label()
    issue = getattr(args, "issue", None)
    if issue:
        labeled = _gh(["gh", "issue", "edit", str(issue), "--add-label",
                       "needs-gatekeeper"] + R).returncode == 0
        if stream_label and _ensure_origin_label_usable(_gh, stream_label, R):
            origin = _gh(["gh", "issue", "edit", str(issue), "--add-label",
                          stream_label] + R)
            if origin.returncode != 0:
                print("gk-request: could not apply origin label %r to #%s: %s"
                      % (stream_label, issue, (origin.stderr or "").strip()))
        text = getattr(args, "comment", None) or (
            "Žiadosť o akciu supervízora — detail v tickete.")
        if not labeled and not text.startswith("GATEKEEPER-ACTION:"):
            text = "GATEKEEPER-ACTION: " + text
        c = _gh(["gh", "issue", "comment", str(issue), "--body", text] + R)
        if c.returncode != 0:
            print("gk-request FAILED: could not comment #%s: %s"
                  % (issue, c.stderr.strip()))
            return 1
        retitled = False
        # #283: distinct from `retitled` -- the escalation can ALSO already
        # be discoverable by job 11's `in:title` query without us having
        # made an edit call at all (the title already carries the marker).
        # Both together answer "is this escalation visible to the
        # supervisor by SOME title-based path" -- neither alone does.
        already_prefixed = False
        if not labeled:
            # a comment-only marker is INVISIBLE to job 11's queries (label +
            # in:title) — best-effort retitle so the request stays
            # machine-discoverable (works when the issue is the stream's own)
            v = _gh(["gh", "issue", "view", str(issue),
                     "--json", "title", "-q", ".title"] + R)
            old = (v.stdout or "").strip()
            if v.returncode == 0 and old:
                if old.startswith("GATEKEEPER-ACTION:"):
                    already_prefixed = True
                else:
                    retitled = _gh(["gh", "issue", "edit", str(issue),
                                    "--title", "GATEKEEPER-ACTION: " + old
                                    ] + R).returncode == 0
            # v failed, or `old` came back empty: we cannot tell whether
            # the title already carries the marker, and no retitle was
            # even attempted -- neither `retitled` nor `already_prefixed`
            # is set, so the loud-failure check below correctly refuses
            # rather than guessing the escalation is fine.
        if not labeled and not retitled and not already_prefixed:
            print("gk-request FAILED: #%s commented but neither the "
                  "needs-gatekeeper label nor the GATEKEEPER-ACTION title "
                  "prefix could be applied (or verified) — escalation "
                  "would be invisible to the supervisor; consider filing "
                  "a NEW ticket via gk-request --title instead" % issue)
            return 1
        print("gk-request: #%s commented (label %s)"
              % (issue, "added" if labeled
                 else ("DENIED — title already carries GATEKEEPER-ACTION"
                       if already_prefixed
                       else "DENIED — retitled with GATEKEEPER-ACTION")))
        return 0

    title = getattr(args, "title", None)
    if not title:
        print("gk-request: --title (new ticket) or --issue N required")
        return 1
    body_file = getattr(args, "body_file", None)
    B = (["--body-file", body_file] if body_file
         else ["--body", getattr(args, "body", None) or title])
    # #221: `gh issue create --label X` SILENTLY DROPS a label the actor
    # lacks push access for while still returning success and creating the
    # issue — GitHub's issue-create endpoint only enforces push access for
    # labels/assignees/milestone by IGNORING them, not by failing the
    # request (unlike the dedicated add-label endpoint, which correctly
    # 403s). A read-only fork's create therefore looked like success with
    # no needs-gatekeeper label anywhere on the resulting ticket — the
    # exact silent-drop this channel exists to prevent, invisible to
    # `r.returncode == 0` since that only reports the CREATE succeeding.
    # Fix: never bake the primary label into the create call either — this
    # is the SAME shape #191/M4 already established for the origin label
    # ("baking it into create meant a rejected label failed the WHOLE
    # create"). Applying `needs-gatekeeper` via its OWN `gh issue edit
    # --add-label` call gets an honest exit code from GitHub's real
    # (non-silent) label endpoint for free, and a denial degrades to the
    # GATEKEEPER-ACTION title prefix exactly like the `--issue` mode
    # already does. If BOTH the label add and the retitle are denied (a
    # genuinely fully-read-only actor), fail loudly — never report success
    # while the escalation would be invisible to the supervisor
    # (script-failure-policy.md).
    r = _gh(["gh", "issue", "create", "--title", title] + B + R)
    if r.returncode == 0:
        new_num = (r.stdout or "").strip().rsplit("/", 1)[-1]
        # #221 adversarial review, MAJOR: an unparseable issue number must
        # NEVER fall through to a false "filed" success — that used to
        # short-circuit BOTH the label-add and the retitle attempt
        # (both were gated on `new_num.isdigit()`), silently reproducing
        # the same invisible-escalation class this whole fix exists to
        # kill. We cannot verify or fix up the escalation's visibility
        # without a real issue number to `gh issue edit`, so fail loudly
        # immediately rather than guess.
        if not new_num.isdigit():
            print("gk-request FAILED: create reported success but no "
                  "parseable issue number in its output (%r) — cannot "
                  "verify or fix up the escalation's visibility"
                  % r.stdout.strip())
            return 1
        labeled = _gh(["gh", "issue", "edit", new_num, "--add-label",
                       "needs-gatekeeper"] + R).returncode == 0
        if stream_label and _ensure_origin_label_usable(_gh, stream_label, R):
            origin = _gh(["gh", "issue", "edit", new_num, "--add-label",
                         stream_label] + R)
            if origin.returncode != 0:
                print("gk-request: could not apply origin label %r to #%s: %s"
                      % (stream_label, new_num, (origin.stderr or "").strip()))
        if not labeled:
            ft = (title if title.startswith("GATEKEEPER-ACTION:")
                  else "GATEKEEPER-ACTION: " + title)
            retitled = ft == title or _gh(
                ["gh", "issue", "edit", new_num, "--title", ft] + R
            ).returncode == 0
            if not retitled:
                print("gk-request FAILED: #%s created but neither the "
                      "needs-gatekeeper label nor the GATEKEEPER-ACTION "
                      "title prefix could be applied — escalation would "
                      "be invisible to the supervisor" % new_num)
                return 1
        print("gk-request filed: %s" % r.stdout.strip())
        return 0
    # #221: since the primary create call no longer carries `--label`
    # (the whole point of this fix), `r` failing here can no longer mean
    # "the label was denied" — the create call itself failed (e.g. the
    # actor can't even open new issues, an invalid title, a transient
    # error). Retry once with the GATEKEEPER-ACTION prefix baked into the
    # title, since that costs nothing and keeps the escalation
    # discoverable if the retry happens to succeed.
    ft = (title if title.startswith("GATEKEEPER-ACTION:")
          else "GATEKEEPER-ACTION: " + title)
    r2 = _gh(["gh", "issue", "create", "--title", ft] + B + R)
    if r2.returncode == 0:
        print("gk-request filed (initial create failed — retried with "
              "GATEKEEPER-ACTION title prefix): %s" % r2.stdout.strip())
        return 0
    print("gk-request FAILED: %s / %s"
          % (r.stderr.strip(), r2.stderr.strip()))
    return 1


# How many tickets the digest NAMES. Also the suppression bound: the caller
# may only mark what this body listed by number.
BACKFILL_MAX_SHOWN = 10


def compose_backfill_digest(repo_name, tickets, since_label):
    """ONE catch-up message per repo for a window that went unreported (#134).

    Deliberately a DIGEST and not N cards: the silent window held ~103 closed
    issues across two repos, and firing a retroactive card per ticket would
    put a hundred pings on the user's phone to apologise for having sent
    none. Plain Slovak, phone-readable, bounded — the numbers plus a few
    titles, never a wall.

    `BACKFILL_MAX_SHOWN` is shared with the caller on purpose: the tickets
    this body NAMES are exactly the tickets the caller may then suppress."""
    shown = tickets[:BACKFILL_MAX_SHOWN]
    n = len(tickets)
    # Slovak plural: 1 ticket / 2-4 tickety / 5+ ticketov.
    word = "ticket" if n == 1 else ("tickety" if 2 <= n <= 4 else "ticketov")
    lines = ["\U0001f4ec **%s** — dobiehacie hlásenie" % repo_name,
             # NOT "a nasadená": these closures were never verified as
             # deployed by anything here, and a catch-up message that
             # overclaims is a worse repair than the silence it apologises
             # for.
             "> Od %s sa uzavrelo **%d** %s, ale správa o nich neprišla na "
             "telefón. Práca je hotová — chýbalo len hlásenie o nej."
             % (since_label, n, word)]
    for t in shown:
        title = (t.get("title") or "").strip()
        if len(title) > 90:
            title = title[:87] + "…"
        lines.append("> • #%s %s" % (t.get("number"), title))
    if len(tickets) > len(shown):
        lines.append("> • …a ďalších %d" % (len(tickets) - len(shown)))
    lines.append("> Odteraz sa hlásenie posiela automaticky a jeho vynechanie "
                 "sa kontroluje.")
    return "\n".join(lines)


# A repo's own checkout can sit deeper than the watchdog's repo sweep looks,
# and a worktree or submodule keeps `.git` as a FILE rather than a directory
# — `discover_managed_repos` sees neither (it stops at depth 4 and tests only
# `dirnames`). Measured on dev1: 40 discovered against 55 real checkouts,
# among them a real depth-4 repo and a submodule. That sweep is shared with
# watchdog jobs 27/28 and is right for what THEY do, so this walks its own
# way rather than widening a function two other jobs depend on.
_CHECKOUT_MAX_DEPTH = 6
_CHECKOUT_SKIP = {"node_modules", ".cache", ".local", "venv", ".venv",
                  "__pycache__", ".npm", "target", "dist", "build"}


def _checkout_roots(home=None):
    """Every checkout root under `home` — `.git` as a directory OR a file."""
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    roots, base = [], str(home).rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(home, topdown=True,
                                                onerror=lambda e: None):
        if dirpath.rstrip("/").count("/") - base >= _CHECKOUT_MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _CHECKOUT_SKIP]
        if ".git" in dirnames:
            roots.append(dirpath)
            dirnames.remove(".git")          # never descend into .git itself
        elif ".git" in filenames:
            roots.append(dirpath)            # worktree / submodule
    return roots


def _local_checkout_for_repo(name, cwd=None, home=None, roots=None,
                             name_of=None):
    """The local checkout root whose `origin` resolves to repo NAME `name`,
    or None when this box holds no such checkout.

    Matched on the NAME, not `owner/name`, because that is exactly the
    granularity of the marker namespace this answer guards
    (`notify._dedup_path` keys cards as `<name>#<n>`). It is a NECESSARY
    condition, never a sufficient one — a fork, a same-named repo of another
    owner, or a repo checked out on two boxes all pass it — so the caller
    pairs it with an explicit override rather than treating it as proof.

    The invocation cwd is consulted FIRST: standing in the repo is the
    common case and answers the question with no walk at all. A root that
    cannot be identified is skipped, never fatal — one unreadable checkout
    must not hide the real match further down the list."""
    if name_of is None:
        from notify import repo_name_for
        name_of = repo_name_for

    def named(path):
        try:
            return name_of(path)
        except Exception:
            return ""

    here = cwd if cwd is not None else os.getcwd()
    if here and named(here) == name:
        return here
    try:
        if roots is None:
            candidates = _checkout_roots(home)
        else:
            candidates = roots() if callable(roots) else roots
    except Exception:
        candidates = []
    for root in candidates:
        if named(root) == name:
            return root
    return None


def _checkout_pane_owner(path, panes=None, owner_of=None):
    """The tmux owner of the live Claude pane sitting in checkout `path`, or
    "" when no pane answers or more than one owner does.

    This is the only source that actually KNOWS who a repo belongs to. The
    operator's own tmux does not: a catch-up digest for someone else's repo
    is typically issued from a third box, and over ssh there is no tmux at
    all. On 2026-07-29 a codex-bridge digest — a repo whose only pane sits in
    the `david` session group — was addressed from a dev1 session and landed
    in the wrong thread.

    Two owners on one checkout resolve to "" for the same reason job 5 stops
    guessing on a multi-owner box: an unaddressed report costs less than a
    misdirected one. Every failure degrades to "" (no tmux, an unreadable
    pane list, an owner lookup that raises) so the caller's own override
    still works on a box with no panes at all."""
    if not path:
        return ""
    try:
        from watchdog import list_claude_panes, pane_owner
    except Exception:                      # a box without the package
        if panes is None:
            return ""
        list_claude_panes = pane_owner = None
    panes = panes or list_claude_panes
    owner_of = owner_of or pane_owner
    try:
        rows = panes()
    except Exception:
        return ""
    try:
        target = os.path.realpath(path)
    except Exception:
        target = path
    found = set()
    for pid, cwd in rows or ():
        if not cwd:
            continue
        try:
            here = os.path.realpath(cwd)
        except Exception:
            here = cwd
        # `startswith` alone would match a SIBLING sharing the prefix
        # (`proj-old` vs `proj`); the separator is what makes it containment.
        if here != target and not here.startswith(target.rstrip("/") + os.sep):
            continue
        try:
            # #302: the raw owner (a tmux session name / unix account, e.g.
            # a stream persona's own account) must be redirected through
            # notify.STREAM_NOTIFY_OWNER the same way watchdog.run_once
            # already redirects every pane-owner lookup — see
            # TestPaneOwnerAlwaysRedirected / #212. Imported lazily, same
            # reason `watchdog` itself is imported lazily above (a box
            # without the package must still degrade to ""). The real
            # production `owner_of` (watchdog.pane_owner) already returns a
            # stripped/lowercased value, so redirecting first and
            # normalizing the (possibly-passthrough) result after is exactly
            # equivalent for every real caller, and keeps this the literal
            # `stream_redirect(owner_of(...))` shape the structural lock
            # (TestAirulesetOwnerResolutionAlwaysRedirected) requires.
            from notify import stream_redirect
            owner = stream_redirect(owner_of(pid) or "").strip().lower()
        except Exception:
            owner = ""
        if owner:
            found.add(owner)
    return found.pop() if len(found) == 1 else ""


def _notify_backfill_digest(args, send):
    """One catch-up digest for a repo whose completion cards never fired.

    Reads the closed issues in the window from gh, drops any that DID get a
    delivered card, and sends a single message. Idempotent through the same
    dedup path as every other notification.

    MACHINE-LOCAL BY CONSTRUCTION: `marker_delivered` reads this box's own
    `~/.claude/autopilot-notify-sent/`. Job 25 never trips over that (it
    only examines checkouts a live pane sits in), but this command takes a
    bare `owner/name` from an operator, so it refuses outright when the repo
    has no checkout here — under-reporting would produce a digest
    apologising for reports another box really delivered."""
    from notify import (backfill_marker_key, mark_backfill_reported,
                        marker_delivered, stream_redirect)
    repo = getattr(args, "repo", None)
    since = getattr(args, "since", None)
    if not repo or not since:
        print("notify --backfill-digest needs --repo owner/name and --since",
              file=sys.stderr)
        sys.exit(1)
    name = str(repo).rstrip("/").split("/")[-1]
    checkout = _local_checkout_for_repo(name)
    if getattr(args, "force", False):
        print("notify --backfill-digest: --force — skipping the local-checkout "
              "check for '%s' on %s. If this box does not hold that repo's "
              "markers, the digest will over-report."
              % (name, os.uname().nodename), file=sys.stderr)
    elif checkout is None:
        print("notify --backfill-digest: no local checkout of '%s' on this "
              "box (%s).\nThe digest is computed from THIS box's card markers "
              "(~/.claude/%s/), which are machine-local — with no checkout "
              "here every ticket reads as unreported and the digest would "
              "apologise for reports another box already delivered.\nRun it "
              "on the box holding the '%s' checkout, or leave it to that "
              "box's watchdog job 25. If the checkout IS here and this "
              "check simply missed it, re-run with --force."
              % (name, os.uname().nodename, "autopilot-notify-sent", name),
              file=sys.stderr)
        sys.exit(1)
    raw = _gh_out("issue", "list", "-R", repo, "--state", "closed", "-L", "200",
                  "--json", "number,title,closedAt", timeout=60)
    try:
        issues = json.loads(raw or "[]")
    except ValueError:
        issues = []
    # Unreported = no card of its own AND not already accounted for by an
    # earlier DELIVERED digest — so a second run over a wider window does
    # not re-report what the user has already been told.
    tickets = [i for i in issues
               if (i.get("closedAt") or "") >= since
               and not marker_delivered("%s#%s" % (name, i.get("number")))
               and not marker_delivered(
                   backfill_marker_key(name, i.get("number")))]
    tickets.sort(key=lambda i: i.get("number") or 0)
    if not tickets:
        print("backfill: nothing unreported for %s since %s" % (name, since))
        return
    # WHO it goes to is resolved from the checkout's own pane, not from the
    # operator. `--owner-name` remains for a box where the repo has no live
    # pane, but it may no longer CONTRADICT one: obeying a flag over the pane
    # is exactly how a codex-bridge catch-up reached the wrong thread.
    derived = _checkout_pane_owner(checkout)
    # #302 review MAJOR: `derived` is redirected (inside _checkout_pane_owner
    # itself), but a raw `--owner-name` value was reaching notify.send()
    # UN-redirected — exactly the documented use case for the flag (no live
    # pane on this box), so a raw stream-persona account name (e.g.
    # 'montalu2') would land in the wrong Discord thread every time.
    stated = stream_redirect(
        (getattr(args, "owner_name", None) or "").strip().lower())
    if derived and stated and derived != stated:
        print("notify --backfill-digest: --owner-name %s contradicts the "
              "checkout's own pane, which belongs to %s.\n'%s' is where this "
              "repo's reports go; sending it anywhere else puts the report in "
              "the wrong thread and gives someone else the noise.\nDrop "
              "--owner-name (the pane answers it), or correct it to '%s'."
              % (stated, derived, derived, derived), file=sys.stderr)
        sys.exit(1)
    body = compose_backfill_digest(name, tickets, since[:10])
    # #369 review M1 (TRIGGERED): "N finished tickets you never heard
    # about" is exactly the ticket-work-scoped traffic #369 exists to
    # split out of the shared owner pile — mirrors _notify_run_card's own
    # `project=stream_qualified(name)`.
    from notify import stream_qualified
    status = send(body, owner=derived or stated or None,
                  dedup_key="backfill:%s:%s" % (name, since[:10]),
                  dry_run=getattr(args, "dry_run", False),
                  project=stream_qualified(name))
    print("%s (%d tickets)" % (status, len(tickets)))
    # Record what this digest reported, so watchdog job 25 stops re-flagging
    # tickets the user has already heard about — but ONLY the tickets the
    # message actually NAMED. The body shows at most BACKFILL_MAX_SHOWN
    # titles and then "a ďalších N": the overflow is counted, never named,
    # so the user has no number to chase and a marker for it would claim
    # per-ticket coverage the message never gave. Suppressing exactly what
    # was named leaves job 25 surfacing the rest a batch at a time, which is
    # the direction that cannot lose a ticket. `mark_backfill_reported`
    # itself writes only on a proven 'sent' (#134).
    named = tickets[:BACKFILL_MAX_SHOWN]
    marked = mark_backfill_reported(
        name, [t.get("number") for t in named], status)
    if marked:
        print("backfill: %d of %d tickets recorded as reported (only those "
              "the message named)" % (marked, len(tickets)))
    if status == "dedup":
        # The digest-level key is date-truncated, so a same-day re-run after
        # a new ticket closed hits the claim and sends NOTHING. Printing
        # 'dedup' and exiting 0 read exactly like a delivery while those
        # tickets stayed unreported.
        print("notify --backfill-digest: NOTHING SENT — an earlier digest "
              "for %s/%s already claimed this key, so these %d ticket(s) "
              "were not reported. Re-run with a different --since."
              % (name, since[:10], len(tickets)), file=sys.stderr)
        sys.exit(1)
    if status not in ("sent", "dry-run"):
        sys.exit(1)


# #272 — a card whose 🎯 Cieľ is only the bare ticket number, or whose
# ✅ Dosiahnuté is empty/generic filler, is undecodable from a phone and must
# never reach Discord silently (the codex-bridge #457-#460 report: worker
# calls that reached `--goal "#457"` / an omitted `--achieved`). A GOAL that
# merely looks omitted is auto-enriched from the already-fetched issue title
# when the title itself is usable; an ACHIEVED that is empty or generic
# filler has nothing to enrich it FROM (it is worker-authored content, not
# something `gh` can supply) and is always a hard refusal.
#
# Round-2 adversarial review (#272): the FIRST cut only rejected a bare
# numeric ref like "457"/"#457" and an achieved value that matched a
# denylist EXACTLY after casefold+whitespace-collapse — both were defeated
# live by trivial variants: a trailing period, a missing diacritic, an
# extra space before a comma, "##457"/"#457.", or plain junk like ".", "-",
# "n/a", "None", "TODO", a bare emoji. The classifier below is shape-based
# instead of a literal-match enumeration: "is bare" means "contains not one
# single letter anywhere" (so ANY digits/punctuation/symbol/emoji-only
# string is caught regardless of exact spelling) OR "normalizes to a known
# placeholder word" (n/a, none, todo, ok, hotovo, …) — normalization folds
# diacritics (NFKD, drop combining marks — this also fixes an NFD-decomposed
# input the plain casefold() never caught), casefolds, and replaces EVERY
# punctuation run (not just leading/trailing) with a space before collapsing
# whitespace, so spacing/punctuation variants of the same phrase converge to
# one key. The known-generic PHRASES are listed with their real diacritics
# and normalized ONCE at import time — this is what makes "hotovo" and
# "hotové" (two genuinely different Slovak words) both land correctly
# without hand-duplicating an ASCII-folded copy of every entry.
_RUN_CARD_ALNUM_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_RUN_CARD_PUNCT_RE = re.compile(r"[.,;:!?\-_()\[\]{}'\"/\\*+~`#]+")


def _run_card_norm(raw):
    """Shape-normalize for content-equality checks: fold diacritics away
    (NFKD, drop combining marks), casefold, replace every punctuation RUN
    (anywhere, not just at the edges) with one space, collapse whitespace."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _RUN_CARD_PUNCT_RE.sub(" ", s.casefold())
    return " ".join(s.split())


def _run_card_content_fp(goal, achieved):
    """A stable fingerprint of a run-card's worker-authored CONTENT
    (--goal + --achieved), for the #474 terminal content-refusal marker.

    Normalizes via `_run_card_norm` so trivially-different spellings of the
    SAME refused content (case, trailing space, a dropped diacritic) do not
    each spawn their own marker + re-log. Guards None -> "" BEFORE
    normalizing: `_run_card_norm(None)` folds str(None) = "None" to "none",
    which would make an ABSENT --achieved collide with the literal
    placeholder word "none" and defeat the 'a genuinely fixed card
    re-enables' guarantee (the fp is exactly what distinguishes the refused
    content from a later real value). `\x1f` (unit separator, never in real
    content) keeps the two fields unambiguous."""
    import hashlib
    g = _run_card_norm(goal) if goal is not None else ""
    a = _run_card_norm(achieved) if achieved is not None else ""
    return hashlib.sha256(("%s\x1f%s" % (g, a)).encode("utf-8")).hexdigest()[:16]


def _run_card_has_no_letters(s):
    """True when `s` carries zero alphabetic characters anywhere — a bare
    ticket ref ("457", "#457", "##457", "#457."), pure punctuation
    (".", "-", "..."), or a symbol/emoji-only string ("✅") all land here,
    regardless of exact spelling/formatting."""
    return not _RUN_CARD_ALNUM_RE.search(s)


_RUN_CARD_GOAL_PLACEHOLDERS = frozenset(
    _run_card_norm(p) for p in (
        "n/a", "na", "none", "null", "nil", "todo", "tbd", "tba", "xxx",
    ))

_RUN_CARD_GENERIC_ACHIEVED = frozenset(
    _run_card_norm(p) for p in (
        "PR zmergnutý, deploy beží", "PR zmergnutý", "zmergnuté",
        "zmergnutý", "deploy beží", "hotovo", "hotové", "done", "merged",
        "dokončené", "ok", "áno", "ano", "yes", "y",
        "n/a", "na", "none", "null", "nil", "tbd", "tba", "todo", "xxx",
    ))


def _run_card_goal_is_bare(goal_raw):
    """True when `goal_raw` carries no plain-language meaning: absent,
    blank/whitespace-only, contains no letter at all (a bare ticket ref /
    pure punctuation / a symbol), or normalizes to a known placeholder
    word ("n/a", "TODO", …)."""
    if goal_raw is None:
        return True
    s = str(goal_raw).strip()
    if not s or _run_card_has_no_letters(s):
        return True
    return _run_card_norm(s) in _RUN_CARD_GOAL_PLACEHOLDERS


def _run_card_achieved_is_bad(achieved_raw):
    """True when `achieved_raw` is empty/whitespace, contains no letter at
    all, or normalizes to one of the known generic filler phrases (incl.
    the hardcoded default this fix removes below)."""
    if achieved_raw is None:
        return True
    s = str(achieved_raw).strip()
    if not s or _run_card_has_no_letters(s):
        return True
    return _run_card_norm(s) in _RUN_CARD_GENERIC_ACHIEVED


def _run_card_refuse(name, issue, dry_run, log_reason, stderr_detail,
                     content_fp=None):
    """A #272 content-validation refusal: never send a contentless card, but
    never crash the worker's turn either — exit non-zero with the reason on
    stderr, the SAME shape a genuine delivery failure already uses (#135),
    so a Bash caller sees it. Logged durably UNLESS --dry-run (dry-run's
    contract is preview-only, no state written — the same split the
    pre-existing view_ok diagnostic in `_notify_run_card` already uses).
    NOTE: --dry-run still exits non-zero + prints to stderr — a PREVIEW
    that would be refused for real ought to say so — it is only the
    DURABLE log write that dry-run skips, unlike the view_ok diagnostic a
    few lines up which suppresses both.

    `log_reason` is a short CLASSIFICATION only (never the raw --goal/
    --achieved text) — the durable log is a second place worker-authored
    content could come to rest (#157's own lesson), and the classification
    alone is enough to diagnose a refusal. `stderr_detail` MAY include the
    raw value — the command line is already in the transcript, so this is
    not a new exposure. `name` is the bare repo-name key (`repo.rstrip("/")
    .split("/")[-1]`), matching the SAME key every other run-card log line
    already uses (never the full "owner/repo" form)."""
    key = "%s#%s" % (name, issue)
    if not dry_run:
        from notify import log_delivery
        log_delivery("refused", kind="run-card", key=key, reason=log_reason)
        if content_fp:
            # #474: record a TERMINAL content-refusal marker so a later
            # IDENTICAL retry of this same-content card short-circuits before
            # any gh fetch or new log line (the 3502x/33h `x#457` spam). Only
            # refusals that are deterministic on the CLI args ALONE
            # (achieved-bad — title-INDEPENDENT) pass content_fp; a
            # goal-bare-AND-title-bare refusal depends on the gh-fetched
            # title (which could gain content on a later run and make the
            # card valid), so it is deliberately NOT terminal-marked.
            from notify import mark_run_card_content_refused
            mark_run_card_content_refused(name, issue, content_fp, log_reason)
    print("notify --run-card: REFUSED (%s) — %s" % (key, stderr_detail),
          file=sys.stderr)
    sys.exit(1)


def _run_card_require_repo_and_issue(args, repo, issue):
    """#590: a run-card missing --repo/--issue is a NON-DELIVERY, and #134/#135
    require it to EXIT NON-ZERO and write a durable delivery-log line with the
    reason — never a silent `return` (exit 0, no log, silent even under
    --dry-run). That silent `return` (here since 9bee24a1, 2026-06-20) is the
    branch the airuleset supervisor hit by firing run-cards without --repo,
    silently dropping the completion cards for a run of closed tickets. The
    diagnostic evidence of the drop is the MARKER GAP — the newest delivered
    airuleset card marker is #529 (2026-08-17), none after — NOT the delivery
    log: a SUCCESSFUL card writes only its inner `_send` line
    (`kind=python key=<repo>#<issue>`), never a `kind=run-card` line (that
    outer line fires ONLY on refuse/failure), so "zero kind=run-card lines"
    alone is a null signal (#523). Routes through the SAME `_run_card_refuse`
    shape every other refusal uses (log + stderr + exit 1; --dry-run prints +
    exits but skips only the durable log), with a `?` sentinel for whichever
    field is missing so the log key stays greppable
    (`?#586` / `x#?` / `?#?`). Never returns."""
    missing = ([] if repo else ["--repo"]) + \
              ([] if issue is not None else ["--issue"])
    joined = " and ".join(missing)
    _run_card_refuse(
        str(repo).rstrip("/").split("/")[-1] if repo else "?",
        issue if issue is not None else "?",
        getattr(args, "dry_run", False),
        log_reason="missing required %s" % joined,
        stderr_detail="missing required %s; a completion card needs both "
                      "--repo <owner/name> and --issue <N> to build. No card "
                      "sent." % joined)


def _notify_run_card(args, compose_autopilot_card, send):
    """Send the per-ticket completion card, gathering the issue title (the Cieľ)
    and the remaining backlog count from gh. The autopilot worker fires this
    DIRECTLY at merge (`notify --run-card --repo <owner/name> --issue <N> --pr
    <url> --achieved "<slovak>"`), so it runs in the worker's context (gh auth,
    tmux owner, the channel .env). REQUIRES --repo + --issue. Best-effort, never
    raises."""
    try:
        repo = getattr(args, "repo", None)
        issue = getattr(args, "issue", None)
        if not repo or issue is None:
            _run_card_require_repo_and_issue(args, repo, issue)

        # #474 terminal-skip: an external caller re-firing an IDENTICAL
        # contentless card (empty/generic --goal or --achieved) would
        # otherwise re-fetch the gh view AND re-log a `refused` line on
        # EVERY retry (3502x for x#457 over 33h on dev1). A content refusal
        # is deterministic on these CLI args, so the FIRST such refusal
        # writes a terminal marker (below, in _run_card_refuse) and a later
        # identical retry short-circuits HERE — before the gh fetch, before
        # a new log line. `name`/`raw_goal`/`raw_achieved`/`dry_run` are also
        # RE-USED by the send path below (round-2 review: the log/dedup key
        # must be the bare repo NAME#ISSUE), so they are computed ONCE here.
        name = str(repo).rstrip("/").split("/")[-1]
        raw_goal = getattr(args, "goal", None)
        raw_achieved = getattr(args, "achieved", None) or getattr(args, "result", None)
        dry_run = getattr(args, "dry_run", False)
        content_fp = _run_card_content_fp(raw_goal, raw_achieved)
        from notify import run_card_content_refused
        if run_card_content_refused(name, issue, content_fp):
            print("notify --run-card: REFUSED (%s#%s) — content unchanged "
                  "since a prior refusal; not re-fetching or re-logging "
                  "(#474)." % (name, issue), file=sys.stderr)
            sys.exit(1)

        # title + labels in ONE call — labels decide whether THIS ticket is a
        # sub-dev stream:<user> hand-off (#164 defect 2): the D/T progress
        # counter must stay within ONE population, so a stream ticket's card
        # must never inflate a CORE-scoped 'done' the core-scoped 'remaining'
        # cannot back.
        view_raw = _gh_out("issue", "view", str(issue), "-R", repo,
                           "--json", "title,labels")
        try:
            view = json.loads(view_raw) if view_raw else {}
        except ValueError:
            view = {}
        if not isinstance(view, dict):
            view = {}
        title = view.get("title") or ("#%s" % issue)
        # M11 (round 2): on a parse/lookup FAILURE (empty view_raw, malformed
        # JSON, non-dict) `view` is {} and `view.get("labels")` is None ->
        # `_ticket_is_stream_labeled(None)` is False -> the OLD
        # `not _ticket_is_stream_labeled(...)` collapsed to True, silently
        # restoring the PRE-#164 wrong behaviour: a ticket we could not
        # identify was treated as CORE and its card could inflate the
        # core-scoped 'done'. A failed lookup now defaults the SAFE
        # direction — the worst case is an undercounted 'done' (annoying),
        # never a corrupted cross-population ratio (wrong).
        view_ok = "labels" in view
        is_core_ticket = view_ok and not _ticket_is_stream_labeled(view.get("labels"))
        if not view_ok and not getattr(args, "dry_run", False):
            # --dry-run stays silent on stderr (an established contract —
            # test_dry_run_still_exits_zero_and_says_nothing_on_stderr); this
            # diagnostic is genuinely best-effort visibility, not part of
            # dry-run's "preview with no side effects, no noise" promise.
            print("notify --run-card: could not determine whether #%s is a "
                  "sub-dev stream ticket (gh issue view returned no labels) "
                  "-- treating conservatively as non-core so this card does "
                  "not inflate 'done'." % issue, file=sys.stderr)
        # #181 I-5 residual (round 4): resolve identity against the REPO ROOT,
        # like cmd_tickets_status / cmd_slice_quals / cmd_core_quals already
        # do. This was the FOURTH call site and the only one still resolving
        # against the PROCESS cwd — so a session running from a subdirectory
        # made the run-card and the footer disagree about which profile the
        # box is even on (a project CLAUDE.md `airuleset:authority=` marker is
        # invisible from a subdirectory), and "one definition, resolved per
        # box" was not true until all four agreed.
        card_root = _repo_root() or None
        is_full = resolve_authority(cwd=card_root) == "full"
        # `remaining` feeds this card's own 📊 progress line — on a
        # reduced-authority box it must be the STREAM's slice, not the whole
        # repo (david saw 'Issues 2/26' while his slice was 5 — 2026-07-19).
        # #367 removed the statusline's own D/T render, but this card still
        # shows its own 'ostáva N' line and still needs the correct scope.
        # Same quals as tickets-status; gh error → None, never a wrong number.
        if not is_full:
            nums, failed = set(), False
            try:
                slice_quals = _slice_quals(_current_user(), cwd=card_root)
            except SliceUnresolved:
                # #181 I-2: an unresolvable gh identity is a gh error, and a
                # gh error is never a wrong number here — remaining stays None.
                slice_quals, failed = [], True
            for qual in slice_quals:
                raw = _gh_out("issue", "list", "-R", repo, "--state", "open",
                              "--search", AUTOPILOT_SKIP_EXCL + " " + qual,
                              "-L", "200", "--json", "number")
                try:
                    nums.update(x["number"] for x in json.loads(raw))
                except (ValueError, TypeError, KeyError):
                    failed = True
            remaining = None if failed else len(nums)
            scope_label = None
        else:
            # Full-authority: `remaining` is the SAME OBLIGATION set the
            # footer's `I N` (`cmd_tickets_status`) and the `/goal`
            # stop-proof (`core-quals --count`) already compute —
            # `_obligation_quals()` unioned via `_union_open_issues()`, the
            # SAME shared derivation, never a parallel one (the #367
            # consistency guard: "an ORDINARY code change cannot make the
            # footer and the loop's stop condition silently drift apart the
            # way they used to" — extended here to the card, the THIRD
            # consumer of that derivation).
            #
            # #382 (2026-08-12): before this, the card computed a NARROWER
            # count here — the core partition alone (`_core_search_excl()`,
            # a single `-q length` query) — and self-labelled it "core".
            # #367's own adversarial review (F4) found the two had already
            # drifted: a repo with a stream-owned needs-gatekeeper/
            # ready-for-review ticket showed a DIFFERENT number on the card
            # than on the footer for the exact same box, even though #164's
            # original comment claimed scoping to core here "makes the two
            # agree by construction" — that claim stopped being true the
            # moment #367 widened the footer alone. Recurrence chain: #164
            # -> #181 -> #307 -> #362 -> #367 all hit this SAME "two counts,
            # two populations" shape; the fix each time was to collapse to
            # ONE shared derivation, never to add a documented exception.
            # Doing that again here — reusing `_obligation_quals()`/
            # `_union_open_issues()` instead of adding a third parallel
            # count — is the same fix, applied to the third caller.
            #
            # scope_label drops to None (not "core"): the card's `remaining`
            # now IS the same population the footer's unlabeled `I N`
            # already shows, so a "core" word next to it would claim a
            # narrower scope than the number actually has.
            seen, u_failed = _union_open_issues(_obligation_quals(),
                                                AUTOPILOT_SKIP_EXCL, repo=repo)
            remaining = None if u_failed else len(seen)
            scope_label = None

        # `name`/`raw_goal`/`raw_achieved`/`dry_run`/`content_fp` were
        # computed once at the top of the try (for the #474 terminal-skip);
        # the goal/achieved validation below re-uses them, so a #272
        # content-validation refusal shares the SAME bare-repo NAME#ISSUE key
        # every other run-card log line uses (never the full "owner/repo").
        # 🎯 Cieľ = the worker's PLAIN-language --goal (simple, understandable).
        # #272: a GOAL that merely LOOKS omitted is auto-enriched from the
        # fetched issue title — extending the pre-existing "goal genuinely
        # omitted" fallback — but only when that title itself carries real
        # content; otherwise refuse.
        if _run_card_goal_is_bare(raw_goal):
            if not _run_card_goal_is_bare(title):
                goal = title
                print("notify --run-card: --goal %r is contentless — "
                      "auto-enriched from the issue title." % raw_goal,
                      file=sys.stderr)
            else:
                _run_card_refuse(
                    name, issue, dry_run,
                    log_reason="goal is contentless (no usable title to "
                              "enrich from)",
                    stderr_detail="goal %r is contentless and no usable "
                                  "issue title was available to enrich it "
                                  "— pass a real --goal (plain Slovak, "
                                  "what the ticket wants)" % raw_goal)
        else:
            goal = raw_goal
        # ✅ Dosiahnuté has nothing to enrich it FROM (it is worker-authored
        # content describing what actually landed and was verified) — empty
        # or generic filler is always a hard refusal, never a silently-sent
        # generic default (#272).
        if _run_card_achieved_is_bad(raw_achieved):
            # content_fp passed -> this refusal is TERMINAL-marked (#474): it
            # is deterministic on the CLI args alone (independent of the
            # gh-fetched title), so an identical retry short-circuits.
            _run_card_refuse(
                name, issue, dry_run,
                log_reason="achieved is missing or generic",
                stderr_detail="achieved %r is missing or generic — pass a "
                              "real --achieved describing what actually "
                              "landed and was verified" % raw_achieved,
                content_fp=content_fp)
        achieved = raw_achieved
        # --pr is the full PR URL → a clickable "kód (PR)" link (the number was
        # dropped, the link kept). --url = "where to see it live" link(s).
        body = compose_autopilot_card(
            repo=repo,
            tickets=[{"n": issue, "title": title, "goal": goal,
                      "achieved": achieved}],
            pr=getattr(args, "pr", None), version=getattr(args, "version", None),
            merge_sha=getattr(args, "merge_sha", None),
            review_ok=(getattr(args, "review", "ok") != "fail"),
            done=None, remaining=remaining, urls=getattr(args, "url", None),
            handoff=getattr(args, "handoff", False), scope_label=scope_label)
        # Dedup on the REPO-NAME#ISSUE — the stable unit. /autopilot re-dispatches a
        # fresh worker each turn a stopped worker is presumed dead, so the same issue
        # can be carded more than once; keying on repo-name#issue collapses those to one.
        # Use only the repo's last path segment so a bare name ("odoo-erp") and the
        # full "owner/odoo-erp" collapse to one key. (`name` computed earlier, above
        # the goal/achieved validation block, so a refusal shares this same key.)
        dedup = getattr(args, "dedup_key", None) or ("%s#%s" % (name, issue))
        # Print the outcome (sent/dedup/dry-run/error) for visibility; harmless in
        # the detached spawn (its stdout is /dev/null).
        # return_message_id=True (#298): capture the sent card's OWN Discord
        # message id so a later Discord reply/reaction on it can be routed
        # back to this repo#issue (job 7 poll-pass extension). A pre-#298
        # test double for `send` may still return a bare status STRING (the
        # old contract) rather than the opt-in tuple -- tolerate both so no
        # existing mock needs updating.
        # MINOR-6 (#297/#298 review): resolve the owner ONCE and pass it to
        # both calls explicitly, rather than letting `send()` resolve it
        # internally and `notification_channel()` re-resolve it a second
        # time right after -- the two calls must always agree on WHICH
        # owner's thread the card actually posted to.
        # #369: `project` routes this card to its OWN per-project Discord
        # thread (never the shared owner channel) — stream-qualified so a
        # sub-dev stream box's project label matches the SAME rule the
        # watchdog/shell-hook project labels already use (`stream_qualified`),
        # and computed from `name` (the bare repo NAME) so a personal box
        # (newlevel/root) stays unqualified. `notification_channel` is called
        # a SECOND time (for `record_card_message`) with the SAME `project`
        # so the stored channel always matches where the card actually posted.
        # #369 review M5 (TRIGGERED — mechanism proven, race THEORETICAL):
        # `notification_channel()` with `env=None` re-reads .env from DISK,
        # while `send()` resolves its own channel from a snapshot taken
        # earlier — a concurrent self-heal (`resolve_project_channel`'s
        # background provision writing the newly-provisioned key) landing
        # in that window would make the two calls disagree about which
        # channel the card actually posted to, and `record_card_message`
        # would store the WRONG one (job 7's later reply/reaction routing
        # would poll a channel the card never reached). Reading ONE env
        # snapshot and passing it to BOTH calls closes the window.
        from notify import (_read_env, notification_channel,
                            record_card_message, resolve_owner,
                            stream_qualified)
        env = _read_env()
        owner = resolve_owner()
        project = stream_qualified(name)
        result = send(body, env=env, owner=owner, dedup_key=dedup,
                      dry_run=getattr(args, "dry_run", False),
                      return_message_id=True, project=project)
        status, message_id = result if isinstance(result, tuple) else (result, None)
        print(status)
        if status == "sent":
            if message_id:
                record_card_message(
                    message_id,
                    notification_channel(env=env, owner=owner, project=project),
                    repo, issue)
            # Feed the statusline github done/total segment — a card that actually
            # went out counts one ticket done in this run (dedup re-sends don't).
            # On a full-authority box a STREAM ticket's card must NOT advance
            # the CORE-scoped D/T counter (#164 defect 2) — but it must STILL
            # refresh the `ts` window heartbeat, or a run that cards only
            # stream tickets never keeps 'Issues D/T' alive at all (#181 I6,
            # round 2 regression). ALWAYS write; only the done-bump is gated.
            # Reduced-authority boxes have no core/stream split (their own
            # slice IS their whole population) — always bump.
            _write_autopilot_progress(
                name, remaining, bump_done=(not is_full or is_core_ticket))
        elif status not in ("dedup", "dry-run"):
            # #135: a card that never reached Discord must NOT report success.
            # It still never raises and never blocks the work — but the
            # worker's own Bash call now SEES the failure instead of being
            # told to ignore it, and the reason is durable.
            from notify import log_delivery
            log_delivery(status, kind="run-card", key=dedup,
                         reason="send-returned-%s" % status)
            print("notify --run-card: NOT delivered (%s) for %s — the ticket "
                  "has no completion card. Re-run it, or report it in the "
                  "evidence block." % (status, dedup), file=sys.stderr)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        # Never raise into the worker, but never vanish either (#135).
        try:
            from notify import log_delivery
            log_delivery("error", kind="run-card",
                         key="%s#%s" % (getattr(args, "repo", "-"),
                                        getattr(args, "issue", "-")),
                         reason=type(exc).__name__)
        except Exception as log_exc:              # logging must not mask `exc`
            print("notify --run-card: logging failed (%r)" % log_exc,
                  file=sys.stderr)
        print("notify --run-card: FAILED (%r) — no completion card was sent."
              % exc, file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Remote deployment
# ---------------------------------------------------------------------------

# --- #433 cluster L-E: the Remote deployment function group
# (REMOTE_DEPLOY_TIMEOUT_S, the Soniox-key provisioning, the ssh-retry/multiplex
# helpers, the /home-audit helpers, and cmd_push) lives in cli_remote.py now —
# re-exported here so every existing reference (SUBCOMMANDS, main()'s argparse
# wiring, tests' airuleset._X access + inspect.getsource) keeps resolving
# through this module unchanged. cli_remote.py is a self-contained leaf; its 5
# REMOTE_HOSTS/AUTHORITY_BY_USER/cmd_install/read_file_safe-referencing
# functions reach those resident/promoted names via a deferred `import
# airuleset` (C/D/J technique), never a module-top back-import.
from cli_remote import (  # noqa: E402, F401
    REMOTE_DEPLOY_TIMEOUT_S as REMOTE_DEPLOY_TIMEOUT_S,
    SONIOX_KEY_SOURCE as SONIOX_KEY_SOURCE,
    _soniox_key_line as _soniox_key_line,
    _deliver_secret_to_hosts as _deliver_secret_to_hosts,
    _deployable_hosts as _deployable_hosts,
    provision_subdev_soniox_key as provision_subdev_soniox_key,
    _SSH_AUTH_DENIED_RX as _SSH_AUTH_DENIED_RX,
    _is_ssh_auth_failure as _is_ssh_auth_failure,
    _SSH_TRANSIENT_RX as _SSH_TRANSIENT_RX,
    SSH_RETRY_MAX_ATTEMPTS as SSH_RETRY_MAX_ATTEMPTS,
    SSH_CONTROL_PERSIST_S as SSH_CONTROL_PERSIST_S,
    _is_ssh_transient_failure as _is_ssh_transient_failure,
    _ssh_retry_backoff_s as _ssh_retry_backoff_s,
    _ssh_control_dir_for_push as _ssh_control_dir_for_push,
    _ssh_multiplex_opts as _ssh_multiplex_opts,
    host_key_check_opts as host_key_check_opts,
    _materialize_pinned_known_hosts as _materialize_pinned_known_hosts,
    _redacted_ssh_cmd as _redacted_ssh_cmd,
    _HOME_AUDIT_MARKER as _HOME_AUDIT_MARKER,
    _shared_remote_host_ips as _shared_remote_host_ips,
    _remote_cmd_with_home_audit as _remote_cmd_with_home_audit,
    _parse_home_audit_output as _parse_home_audit_output,
    _parse_home_names as _parse_home_names,
    _home_listing_trustworthy as _home_listing_trustworthy,
    unregistered_home_accounts as unregistered_home_accounts,
    _deploy_to_all_remotes as _deploy_to_all_remotes,
    cmd_push as cmd_push,
)

# --- #653: owner SSH public-key provisioning — a self-contained leaf, consumed
# by cmd_install below so every managed target (and the local box) gets the
# owner's laptop key key-only, with no extra ssh round. Re-exported here so
# cmd_install's `provision_owner_keys()` call resolves as a module global (and
# stays test-patchable via `airuleset.provision_owner_keys`).
from cli_owner_keys import (  # noqa: E402, F401
    OWNER_PUBKEYS as OWNER_PUBKEYS,
    provision_owner_keys as provision_owner_keys,
)

# --- #659: owner VPS-class sudo provisioning -- a self-contained leaf,
# consumed by cmd_install below (gated on the AIRULESET_OWNER_VPS=1 env the
# deploy loop sets ONLY for owner_vps targets) so an owner VPS gets NOPASSWD
# sudo for the owner user via the same zero-extra-ssh Pattern A as owner keys.
# Re-exported here so cmd_install's `provision_owner_sudo()` call resolves as a
# module global (and stays test-patchable via `airuleset.provision_owner_sudo`).
from cli_owner_vps import (  # noqa: E402, F401
    provision_owner_sudo as provision_owner_sudo,
    _owner_vps_signalled as _owner_vps_signalled,
    _sudoers_install_script as _sudoers_install_script,
)

# --- #433 cluster L-E: REMOTE_HOSTS (the fleet deploy-target registry) promoted
# to the constants-only leaf cli_fleet.py — re-exported here so every resident
# reader (_current_remote_host_entry, cmd_watchdog), every shipped leaf that
# reads airuleset.REMOTE_HOSTS (cli_burn), and every test that patches
# airuleset.REMOTE_HOSTS keep working unchanged.
from cli_fleet import (  # noqa: E402, F401
    REMOTE_HOSTS as REMOTE_HOSTS,
)


# ---------------------------------------------------------------------------
# api-watchdog — auto-resume Claude Code sessions stalled on an API error
# ---------------------------------------------------------------------------

# (WATCHDOG_* systemd unit paths -> cli_filedrop_watchdog.py, #433 L-B)


def _watchdog_bounce_fetch(root):
    """Job 8's real gh fetch (wired here so run_once unit tests stay network-free)."""
    from watchdog import _fetch_bounce_tickets
    return _fetch_bounce_tickets(root)


def _watchdog_gkreq_fetch(root):
    """Job 11's real gh fetch (#30) — same network-free-tests wiring as job 8."""
    from watchdog import _fetch_gkreq_tickets
    return _fetch_gkreq_tickets(root)


def _watchdog_gk_selfservice_fetch(root):
    """Job 31's real gh fetch (#516) — the gk self-service auto-bounce
    candidate set (open needs-gatekeeper / GATEKEEPER-ACTION action requests
    with their labels + Self-service-checked-line + origin-stream facts). Same
    network-free-tests wiring as jobs 8/11."""
    from watchdog import _fetch_gk_action_requests
    return _fetch_gk_action_requests(root)


def _watchdog_gkorphan_fetch(root):
    """Job 36's real gh fetch (#551) — the orphaned gk-hand-off-marker
    candidate facts (an `in:comments` search narrowed by per-candidate
    comment/label/timeline reads). Same network-free-tests wiring as jobs
    8/11/31."""
    from watchdog import _fetch_gk_orphan_candidates
    return _fetch_gk_orphan_candidates(root)


def _watchdog_gkorphan_handoff_fetch(root):
    """Job 36's #570 comment-handoff real gh fetch — the PROPER
    `GATEKEEPER-ACTION:`/`READY-FOR-REVIEW:` marker-comment-in-window candidate
    facts (window-bounded `in:comments` searches narrowed by per-candidate
    comment/label/timeline reads). Computes its own `now` (a ms skew across a
    48h window is irrelevant) and uses the default gh env (home=None), exactly
    like `_watchdog_gkorphan_fetch`. Same network-free-tests wiring as jobs
    8/11/31: run_once gates the whole handoff pass on THIS being wired."""
    import time as _t
    from watchdog import _comment_handoff_window_s, _fetch_gk_comment_handoffs
    return _fetch_gk_comment_handoffs(root, None, _t.time(),
                                      _comment_handoff_window_s())


def _watchdog_u_reconcile_clear(cwd, num):
    """Job 32's real gh side-effect (#515) — remove needs-answer/needs-decision
    from open ticket #`num` in the repo at `cwd`. Wired here (not inside
    run_once) so every OTHER job's run_once unit test stays network-free,
    exactly like the job 8/11/31 fetches. Returns the removed-label list (`[]`
    = nothing to clear / closed) or None (unmeasurable → keep + retry)."""
    from watchdog import _clear_owner_question_labels
    return _clear_owner_question_labels(cwd, num)


def _watchdog_card_probe(root, base):
    """Job 25's confirming fetch (#134).

    Wired HERE, like jobs 8/11/16/24, so run_once's unit tests stay
    network-free. Job 25 measures which issues a merge on the base branch
    closed, so a base ref that has merely gone stale in the local checkout
    would under-report — the opposite of job 24's failure mode, and the
    reason the same fetch is required before the measurement rather than
    after it. Writes ONLY the remote-tracking ref; every repo this touches
    belongs to somebody else. No `gh` half at all: the `Closes #N` in the
    merge commit is the whole fact this job needs, for free.

    #172 (reopened) smaller item: timeout cut 90s -> 15s, matching jobs
    27/28's own cuts (`_watchdog_git_fetch`). Job 25 runs BEFORE jobs 27/28
    in every sweep — one hung fetch here used to still eat 90s of the 120s
    `TimeoutStartSec` unit budget on its own, leaving jobs 27/28 no chance
    to run at all even after their OWN timeouts were bounded.
    """
    import subprocess
    remote, _, branch = (base or "origin/main").partition("/")
    try:
        subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                        remote or "origin", branch or "main"],
                       capture_output=True, timeout=15)
    except Exception as e:
        # Degrade to the local-only read rather than going quiet: an
        # unreported ticket the user never hears about is the failure this
        # job exists to prevent, and the worst case of a stale base ref is
        # that the ping arrives a sweep later.
        return {"fetch_error": repr(e)}
    return None


# #230: the fallback used to run `gh issue list --state closed`, which
# cannot tell WHO closed a ticket — a hand close, a close-by-a-bare-commit,
# and a close-by-a-genuinely-merged-PR all looked identical, so every
# closed issue in the window read as "merged but unreported". This
# GraphQL query reads each closed issue's CLOSED_EVENT `closer` in the SAME
# single call (never one call per issue — that would be N+1 against the
# "roughly one call per trailer-less repo per sweep" budget below); only a
# `closer` that is a PullRequest with `merged: true` is kept.
_CLOSED_FETCH_GRAPHQL = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(states: CLOSED, first: 100,
           orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number
        closedAt
        timelineItems(itemTypes: CLOSED_EVENT, last: 1) {
          nodes {
            ... on ClosedEvent {
              closer {
                __typename
                ... on PullRequest { merged }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _watchdog_closed_fetch(root, since_ts):
    """Job 25's fallback for a repo that never writes `Closes #N` trailers.

    Wired HERE, like every other network call, so run_once's unit tests stay
    network-free. Job 25 asks for this ONLY when the local read found no
    trailers at all yet the base branch did take fresh merges — the exact
    signature of a repo whose issues close from the PR body instead. tvdole
    is that repo (20 commits in 48h, zero trailers in its last 200 commits),
    and it is one of the two in #134's evidence, so without this the backstop
    would have been blind to half the incident it was written for.

    Cost stays where the design intended: a repo that answers locally never
    reaches this, so it is roughly one call per trailer-less repo per sweep
    rather than one per repo — and returning None on any failure degrades to
    the local-only answer rather than to silence.

    #172 (reopened) smaller item: timeout cut 45s -> 10s, matching jobs
    27/28's own `gh` cuts (`_watchdog_issue_counts_fetch`) for the same
    reason `_watchdog_card_probe` above was cut — this call dispatches
    before jobs 27/28 in every sweep.

    #224: returns `{number: closed_epoch}` rather than a bare list, so the
    same per-ticket grace period `merged_closes` applies to the local-git
    path also applies here — `gh` already hands back `closedAt`, so this
    costs nothing extra. A ticket whose `closedAt` fails to parse still gets
    reported (`ts=None`), never dropped for lack of a clean timestamp.

    #230: only counts an issue whose CLOSED_EVENT `closer` is a merged pull
    request — a hand close or a close-by-commit is not a report anyone was
    ever owed. `owner`/`name` are resolved via `-F owner='{owner}' -F
    name='{repo}'`: gh's `-F`/`--field` (typed) values expand the
    `{owner}`/`{repo}` placeholders from `cwd`'s git remote, `-f`/
    `--raw-field` (string) values do NOT (verified empirically before
    writing this) — so this stays one `gh` call, exactly like before, with
    no separate `gh repo view` needed to learn the owner/name first.
    """
    import burn
    import subprocess
    import time
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_ts))
    try:
        r = subprocess.run(
            ["gh", "api", "graphql",
             "-f", "query=" + _CLOSED_FETCH_GRAPHQL,
             "-F", "owner={owner}", "-F", "name={repo}"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}")
        if data.get("errors"):
            return None
        repo_data = (data.get("data") or {}).get("repository") or {}
        nodes = (repo_data.get("issues") or {}).get("nodes") or []
        out = {}
        for i in nodes:
            closed_at = i.get("closedAt") or ""
            if closed_at < since:
                continue
            items = (i.get("timelineItems") or {}).get("nodes") or []
            closer = items[0].get("closer") if items else None
            if not closer or closer.get("__typename") != "PullRequest":
                continue
            if not closer.get("merged"):
                continue
            dt = burn._parse_ts(closed_at)
            out[i["number"]] = dt.timestamp() if dt is not None else None
        return out
    except Exception:
        return None


def _watchdog_reopened_fetch(root, numbers):
    """Job 25's `reopen_fetch` (#182): given candidate issue NUMBERS that
    already have a run-card marker for this repo, return the SUBSET that are
    OPEN again right now — i.e. reopened since their marker's card fired.
    One `gh issue list --state open` call per repo per sweep, bounded by
    `numbers` (never per-issue), same cost shape as `_watchdog_closed_fetch`.
    `root` is a local checkout path — `gh` resolves owner/repo from its
    `origin` remote via `cwd=root`, no `-R` needed. Any failure degrades to
    an EMPTY set: never guess a ticket reopened."""
    import subprocess
    if not numbers:
        return set()
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--state", "open",
             "--json", "number", "-L", "1000"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return set()
        rows = json.loads(r.stdout or "[]")
    except Exception:
        return set()
    if not isinstance(rows, list):
        return set()
    open_now = {row.get("number") for row in rows if isinstance(row, dict)}
    return {n for n in numbers if n in open_now}


def _watchdog_delivery_probe(root, base):
    """Job 24's confirming fetch + best-effort blocker lookup (#138).

    Wired HERE, like jobs 8/11/16, so run_once's unit tests stay network-free.
    Two halves with very different standing:

      * The FETCH is load-bearing. Job 24 RE-MEASURES its verdict after this
        call returns, so an `origin/<base>` ref that had merely gone stale in
        the local checkout is corrected here and the "stall" disappears
        instead of paging anyone. It writes ONLY the remote-tracking ref —
        never the worktree, index or a local branch — which matters because
        every repo this touches belongs to somebody else.
      * The gh lookup is pure ENRICHMENT: it names the open PR that is
        BLOCKED and the check that is red, so the ping says *why* nothing is
        landing. Any failure (no gh, no network, a repo with no PRs) costs
        that one sentence and nothing else.

    #172 (reopened) smaller item: timeouts cut 90s -> 15s / 45s -> 10s,
    matching jobs 27/28's own cuts. Job 24 runs before jobs 27/28 in every
    sweep — one hung fetch or gh call here used to still eat most of the
    120s `TimeoutStartSec` unit budget on its own.
    """
    import subprocess
    remote, _, branch = (base or "origin/main").partition("/")
    try:
        subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                        remote or "origin", branch or "main"],
                       capture_output=True, timeout=15)
    except Exception as e:
        # No fetch means no confirmation: job 24 then re-reads the SAME local
        # refs, so its verdict simply stands and the job degrades to the
        # local-only heuristic rather than going quiet. Deliberate — a MISSED
        # delivery stall is the failure this job exists to prevent, and the
        # price of the alternative is at most one extra ping a day.
        return {"fetch_error": repr(e)}
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "5", "--json",
             "number,mergeStateStatus,statusCheckRollup"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        for pr in json.loads(r.stdout or "[]"):
            if pr.get("mergeStateStatus") != "BLOCKED":
                continue
            red = [c.get("name") or c.get("context")
                   for c in (pr.get("statusCheckRollup") or [])
                   if (c.get("conclusion") or c.get("state")) in
                   ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED")]
            return {"pr": pr.get("number"), "check": red[0] if red else None}
    except Exception as e:
        return {"probe_error": repr(e)}
    return None


# #618 -- how fresh the tickets-status cache must be for the watchdog backlog
# read to trust it over a live (slow) `--count` subprocess. Generous: a
# saturation-nudge decision tolerates a count minutes old. The cache
# periodically dips just out of this bound by design (the watchdog re-warms it
# only when it reads stale) and self-heals one sweep later — it is not held
# strictly under the bound at every instant.
_BACKLOG_STATUS_CACHE_MAX_AGE_S = 15 * 60

# #619 -- the cache-miss LIVE `--count` fallback's subprocess timeout. The
# measured `slice-quals --count` on a big shared-account slice is 16-17s
# (O(backlog) serial gh timeline reads, #618), so the #618 timeout=15 ALWAYS
# expired -> backlog=None on every cache-miss sweep (92x/9h on montalu1), which
# starved the saturation nudge's under-saturation confirmation. Bumped to 30s
# (comfortable margin over 17s) so the synchronous fallback COMPLETES instead of
# bailing; the result then caches for 10min (BACKLOG_CHECK_INTERVAL_S) PER cwd, so
# the 30s cost is paid at most once per that window per REPO, never per sweep.
# Interaction noted (adversarial review): a cold fetch starting late in a sweep can
# overshoot the unit's TimeoutStartSec (~120s) by ~15s more than the old 15s did --
# but a mid-sweep kill is designed-for + self-recovering (a few/day), the cold path
# is rare (cache-first), and the warmed cache serves the next sweep, so the wider
# overshoot window is an accepted tradeoff for a reliable backlog read.
_BACKLOG_LIVE_COUNT_TIMEOUT_S = 30


def _watchdog_backlog_fetch(cwd):
    """#160 defect 1 / defect 4 — does THIS BOX's own slice of the repo at
    `cwd` still have open, non-`autopilot-skip` backlog work, or None on any
    failure/refusal.

    #238-review-style finding (🔴F1, this ticket's own review): the FIRST
    version of this function counted the WHOLE REPO via a raw `gh issue
    list`, which is the wrong population to verify a session's own `🏁
    BACKLOG EMPTY` claim against — a full-authority box's `/goal` loop stops
    on the CORE/OBLIGATION partition (`core-quals`), and a reduced-authority
    stream's loop stops on its OWN slice (`slice-quals`), NOT on the whole
    repo (which routinely still has plenty of OTHER streams' open tickets on
    a shared tracker). Counting the whole repo would make this check refuse
    to trust a genuinely-true "backlog empty" claim on any repo with other
    streams' work still open — exactly the false-positive direction #164/
    #181 already fixed for the footer and the `/goal` stop-proof commands.

    Reuses those SAME commands (`core-quals --count` / `slice-quals
    --count`), run as a subprocess against THIS repo (`cwd=cwd`) so
    `resolve_authority`/`_repo_root` resolve exactly as they would inside
    that session's own pane — never a second, independently-derived
    partition that could drift from the one the session's own stop-proof
    reads. Both commands already refuse (non-zero exit, no printed number)
    on an untrustworthy empty result (#181's search-index guard) rather than
    ever printing a false `0` — this function inherits that refusal as
    `None` (unmeasurable), which every caller already treats as "never
    guess, skip acting" (`_cached_backlog_open`).

    #160-review-style finding 🟡F3 (this ticket's own review, proven live)
    — the very #181 I-5 bug `_repo_root`'s own docstring describes (a
    project's `airuleset:authority=...` marker invisible whenever cwd is a
    SUBDIRECTORY of the repo) was reintroduced ONE LEVEL UP here: `cwd` is
    the PANE's cwd, which can be a subdirectory of the actual repo root, so
    resolving authority against it directly can pick a DIFFERENT profile
    than the CHILD subprocess (which always resolves against `_repo_root()`
    inside `cmd_core_quals`/`cmd_slice_quals`) — the child then refuses
    outright, permanently and silently disabling both defect 1 and defect 4
    for any such repo. Resolving authority against `_repo_root(cwd=cwd)`
    here (falling back to the raw `cwd` only when the root itself cannot be
    resolved) guarantees this function picks the SAME command the child
    would independently choose for itself.

    Wired HERE, like every other network call in this file, so run_once's
    unit tests stay network-free.

    #618: reads the tickets-status cache FIRST — the SAME workable `open` the
    footer renders (`statusbar.obligation_count`, written by an UNTIMED
    background refresh; the ONE-derivation principle #367/#468, sibling of the
    #459 goal_dark_watch reader) — and only shells the live `--count`
    subprocess as a cache-miss/stale/untrusted FALLBACK. That subprocess is
    16-17s on a big shared-account slice (O(backlog) serial gh timeline reads);
    the #618 timeout=15 ALWAYS expired → backlog=n/a → the saturation nudge
    (goal_lane_occupancy_nudge) could never confirm under-saturation (92
    backlog=None sweeps/9h on montalu1). #619 bumped it to
    `_BACKLOG_LIVE_COUNT_TIMEOUT_S` (30s) so the synchronous fallback COMPLETES
    on THIS sweep instead of bailing. A fresh POSITIVE cache read is still the
    fast path (instant, never times out). A cached 0 is NOT trusted (the
    tickets-status writer has no #181 refuse-guard — cmd_tickets_status's own
    note — so a broken-index 0 can land in it), so it falls through to the
    live count, which DOES refuse an untrustworthy empty as None: the #181
    contract above is preserved. A stale/missing/untrusted read ALSO WARMS the
    cache for the next sweep via the same detached, rate-limited `_spawn_refresh`
    the statusline uses (so an idle box whose footer isn't rendering — montalu1's
    7h-stale cache — is re-warmed by the watchdog's own 60s cadence). The bumped
    live count now serves THIS sweep; the warmed cache is the belt-and-suspenders
    for the next one."""
    import time
    try:
        import statusbar
    except Exception:
        statusbar = None
    open_n = ts = None
    if statusbar is not None:
        try:
            open_n, ts = statusbar.obligation_count(cwd)
        except Exception:
            open_n, ts = None, None
    # Trust the cache ONLY when POSITIVE and non-future-fresh (see the docstring
    # for why a cached 0 / future ts is not trusted).
    if isinstance(open_n, int) and open_n > 0 and isinstance(ts, (int, float)) \
            and 0 <= (time.time() - ts) < _BACKLOG_STATUS_CACHE_MAX_AGE_S:
        return open_n
    # Cache missing/stale/untrusted: warm it for the NEXT sweep (detached,
    # untimed, rate-limited by SPAWN_GUARD_S; _spawn_refresh swallows its own
    # errors), then attempt the live count for THIS sweep.
    if statusbar is not None:
        statusbar._spawn_refresh(cwd)
    import subprocess
    try:
        root = _repo_root(cwd=cwd) or cwd
        authority = resolve_authority(cwd=root)
    except Exception:
        return None
    cmd_name = "core-quals" if authority == "full" else "slice-quals"
    try:
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), cmd_name, "--count"],
            cwd=cwd, capture_output=True, text=True,
            timeout=_BACKLOG_LIVE_COUNT_TIMEOUT_S)   # #619: 30s, was 15s (always timed out on a 16-17s slice)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


def _watchdog_ops_wait_fetch(cwd):
    """#547 — the parked W (`ops-wait`) member NUMBERS for THIS box's slice of
    the repo at `cwd`, or None on any failure/refusal. The 1:1 sibling of
    `_watchdog_backlog_fetch`: same authority-aware command choice
    (`core-quals`/`slice-quals`), same `_repo_root(cwd=cwd)` resolution so the
    child subprocess resolves authority exactly as it would inside that session's
    own pane, same refuse→None contract — only the flag differs (`--ops-wait`
    instead of `--count`) and the parse (member numbers, not a count). This RAW
    fetch is uncached; the caller reads it through `ops_wait_recheck._cached_ops_wait`
    (a per-repo TTL cache, the sibling of `_cached_backlog_count`) so it fires at
    most once per repo per TTL, never every sweep.

    The members come from the SAME `_partition_workable` derivation the footer's
    `W N`, the `/goal` stop-proof's `--ops-wait` list, and the count all use —
    NEVER a parallel query (#367/#181). The job-20 W re-check nudge (via
    `goal_lane_sweep`'s `ops_wait_fetch` seam) reads these to re-surface a
    long-parked W ticket into an armed loop's attention.

    `--ops-wait` prints `number<TAB>createdAt<TAB>action<TAB>reason<TAB>title`
    per member (oldest-first); field 0 is the issue number, field 3 the reason
    (which carries a ` stale!` warning for a member with no fresh (≤24h) stream
    push — #570 — and/or a ` gk-handoff!` warning for a member ALSO carrying a
    gk hand-off label — #636). Returns a list of `{"number": int, "stale": bool,
    "gk_handoff": bool, "title": str}` so the job 20 nudge can NAME the stale +
    gk-handoff members and detect release-SHAPED titles (#698 — field 4, which
    `--ops-wait` already prints; a degraded short line reads as title "");
    the sibling `ops_wait_recheck` helpers accept BOTH this dict shape
    AND a legacy bare `int` (back-compat). A None
    return (non-zero exit — the #181 untrustworthy-empty refusal — or an
    unparsable line) is UNDETERMINED and the nudge job fails safe to no-nudge.
    An empty but SUCCESSFUL result (exit 0, no lines) returns `[]` (genuinely no
    W parked), which the job treats as "clear the tracking state".

    Timeout (#570): 35s, not the sibling `--count`'s 15s — `--ops-wait` now does
    up to OPS_WAIT_STALE_MAX_FETCHES (25) per-member `gh issue view` comment
    reads to compute `stale!`. This is RARE on the sweep: it runs at most once
    per repo per `_cached_ops_wait` TTL (30 min), so on a 60s sweep a given
    repo's cache is expired only ~3% of the time — the 120s sweep budget absorbs
    an occasional cached fetch (~25 × <1s), and a genuine timeout returns None
    (the W-clause of that day's nudge is dropped, re-checked next TTL via the
    60s fail_ttl — a bounded, self-healing degradation, #570 review 🔵). Wired
    HERE, like every other network call in this file, so run_once's unit tests
    stay network-free."""
    import subprocess
    try:
        root = _repo_root(cwd=cwd) or cwd
        authority = resolve_authority(cwd=root)
    except Exception:
        return None
    cmd_name = "core-quals" if authority == "full" else "slice-quals"
    try:
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), cmd_name, "--ops-wait"],
            cwd=cwd, capture_output=True, text=True, timeout=35)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    members = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        try:
            num = int(parts[0])
        except (ValueError, IndexError):
            return None   # a malformed line -> undetermined, never a partial set
        # `--ops-wait` always prints the FULL 5-field form (reason_fn is always
        # given), so field 3 IS the reason column (`ops-wait`/`acceptance` +
        # optional ` gk-handoff!` (#636) + optional ` stale!` + optional
        # ` recheck!` (#699)). Require >=5 fields so a hypothetical degraded
        # 4-field line (title at index 3) can never be misread as a flag (#570
        # review nit); anything shorter -> no flag.
        reason = parts[3] if len(parts) >= 5 else ""
        # #698: the TITLE (field 4, tab-joined in case a title itself carries a
        # tab) feeds the job-20 release-shaped detection — zero new gh calls.
        title = "\t".join(parts[4:]) if len(parts) >= 5 else ""
        members.append({"number": num, "stale": "stale!" in reason,
                        "gk_handoff": "gk-handoff!" in reason,
                        "release_recheck": "recheck!" in reason,
                        "title": title})
    return members


def _watchdog_queue_fetch(cwd):
    """#733 — the gk QUEUE UNION (`ready-for-review ∪ needs-gatekeeper ∪
    prio:bounce`) open issue numbers for the repo at `cwd`, or None on any
    failure/refusal. The job-20 queue-arrival rider reads this to detect a NEW
    hand-off landing while an armed FULL-authority session is parked on a waiter.

    FULL-authority ONLY: only a gk/full box PROCESSES this cross-stream union (a
    reduced stream hands off to gk; its own returned `prio:bounce` is job 8's).
    Resolved against `_repo_root(cwd=cwd)` so the authority matches what the
    session's own pane would resolve — a non-full box returns None (the rider
    also gates, so this is belt-and-suspenders).

    Uses the ticket's OWN proven shape: THREE exact-match `--label` queries
    (never a `label:a,b,c` search string — `prio:bounce` carries a colon that a
    search qualifier mis-parses), unioned + deduped + sorted. Any query error →
    None (the #181 fail-safe: an auth/network hiccup must never look like 'no
    queue'). Wired HERE, like every other network call in this file, so
    run_once's unit tests stay network-free."""
    import subprocess
    try:
        root = _repo_root(cwd=cwd) or cwd
        authority = resolve_authority(cwd=root)
    except Exception:
        return None
    if authority != "full":
        return None
    nums = set()
    for label in ("ready-for-review", "needs-gatekeeper", "prio:bounce"):
        try:
            # `-L 200` is a per-label truncation window (#616 LIMIT-TRUNCATION
            # class), but the failure direction here is MILD: a new arrival sorts
            # into the newest window, and a long-tail member (>200 open of ONE
            # label) that falls out then re-enters reads as a spurious re-arrival
            # — a redundant nudge, never a wrong keystroke or a missed arrival.
            r = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--label", label,
                 "-L", "200", "--json", "number"],
                cwd=cwd, capture_output=True, text=True, timeout=15)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        try:
            nums.update(int(x["number"]) for x in json.loads(r.stdout or "[]"))
        except (ValueError, KeyError, TypeError):
            return None
    return sorted(nums)


def _parse_origin_slug(url):
    """owner/name from a git remote URL, or None. Pure + testable (#616). Handles
    the https form (`https://github.com/owner/name[.git]`), the scp form
    (`git@github.com:owner/name[.git]`) and the ssh-url form
    (`ssh://git@github.com/owner/name[.git]`) by stripping the `.git` tail,
    folding the scp `:` into `/`, and taking the last two path components."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None
    if u.endswith(".git"):
        u = u[:-4]
    parts = [p for p in u.replace(":", "/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    if not owner or not name:
        return None
    return "%s/%s" % (owner, name)


def _gh_not_found(stderr):
    """True iff a `gh api` stderr is a STRUCTURAL 404 (the resource genuinely
    doesn't exist — e.g. a branch that isn't there) as opposed to a transient
    network/auth failure. `gh` prints `Not Found (HTTP 404)` for a real 404;
    anything else (a connection error, a 5xx, a rate-limit) is treated as
    transient. Used to distinguish "not a release-train repo" (a clean, full-TTL
    'no gap' result) from "couldn't check right now" (None -> fail-TTL retry)."""
    s = (stderr or "").lower()
    return "404" in s or "not found" in s


def _release_train_run_in_flight(runs, staging, prod):
    """#616 (review F1) — PURE: is any workflow run a genuine release/deploy in
    flight? A run counts ONLY when it is in_progress/queued AND event-triggered
    by `push`/`workflow_dispatch` (this EXCLUDES the constant `issue_comment` /
    `issues` utility workflows that a busy repo runs on `main` — 'Sub-dev Handoff
    Gate', 'Bounce Label Hygiene', … — which would otherwise ALL read as a
    release-in-flight on `main` and starve the nudge forever) AND is on the
    staging/prod branch OR is a deploy/release-named workflow. Malformed elements
    are skipped; an empty list -> False."""
    for r in runs or []:
        if not isinstance(r, dict):
            continue
        if r.get("status") not in ("in_progress", "queued"):
            continue
        if r.get("event") not in ("push", "workflow_dispatch"):
            continue
        nm = (r.get("name") or "").lower()
        if (r.get("headBranch") in (staging, prod)
                or "deploy" in nm or "release" in nm):
            return True
    return False


def _watchdog_release_state_fetch(cwd):
    """#616 — the release-train state for the repo at `cwd`:
    `{"ahead": <integration commits ahead of prod>, "in_flight": <bool>,
    "train": <bool — the staging branch verified to exist, i.e. a REAL 3-branch
    release train (#698)>}`, or None on any TRANSIENT failure/refusal
    (undetermined -> both job-20 consumers fail safe: the release-gap rider to
    no-nudge, the release-landed escalation to the generic wording). This RAW
    fetch is uncached; the callers read it
    through `release_gap._cached_release_state` (a per-repo TTL cache, the sibling
    of `_cached_ops_wait`) so it fires at most once per repo per TTL, never every
    sweep per pane. Wired HERE like every network call so run_once's unit tests
    stay network-free.

    Repo slug comes from the LOCAL `remote.origin.url` (no network). It is used
    ONLY when the URL names an allowed host (default github.com,
    AIRULESET_RELEASE_ALLOWED_HOST) — a foreign/self-hosted origin returns None
    rather than resolving a same-named github.com repo (review F5). The gap is
    read from GitHub (`gh api .../compare/{prod}...{integration} .ahead_by`) so a
    stale local ref never mis-measures it.

    A repo that is NOT a 3-branch release repo returns a CLEAN `{"ahead": 0,
    "in_flight": False, "train": False}` (full-TTL cached, decider -> clear),
    NEVER None — so a 2-branch repo does not re-fetch every 60s sweep (review
    F2): a compare 404 (no integration branch) OR a MISSING `staging` branch
    (review F6 — the distinctive 3-branch marker; a stray/legacy `develop` on a
    2-branch repo must not nudge and must never read as a drained TRAIN, #698 —
    so staging AND the in-flight PR/run state are verified on the DRAINED path
    too, extra cached gh calls per drained train repo per TTL)
    both resolve to "no release train". A NON-404 error stays None (transient
    -> fail-TTL retry) — since #698 that includes a transient staging error on
    the drained path (previously the ahead==0 verdict short-circuited before
    staging).

    On a PROVEN train (staging exists) — gap AND drained alike (#698 review
    fix: the drained verdict must never fabricate `in_flight` False while a
    just-merged release's deploy still runs) — in-flight is TRUE iff an open
    release PR (base staging OR prod, server-side `--base`-filtered so a busy
    repo's older release PR is never truncated out of a default-30 window —
    review F1) exists, OR a genuine deploy/release workflow is running
    (`_release_train_run_in_flight`, event-filtered). Branch names default
    develop/staging/main, env-overridable (AIRULESET_RELEASE_*), read at call
    time (#574). Timeouts are kept tight (git 5s, each gh 15s); the PR/run
    calls run once per proven-train repo per TTL (cached)."""
    import subprocess
    import json
    integ = os.environ.get("AIRULESET_RELEASE_INTEGRATION_BRANCH", "develop")
    prod = os.environ.get("AIRULESET_RELEASE_PROD_BRANCH", "main")
    staging = os.environ.get("AIRULESET_RELEASE_STAGING_BRANCH", "staging")
    allowed_host = os.environ.get("AIRULESET_RELEASE_ALLOWED_HOST", "github.com")
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    url = r.stdout or ""
    if allowed_host.lower() not in url.lower():
        return None
    repo = _parse_origin_slug(url)
    if not repo:
        return None
    # (1) gap: integration ahead of prod. A 404 (no integration branch) -> not a
    # release repo (clean "no gap", `train` False, full-TTL). Any OTHER error ->
    # None (transient).
    try:
        r = subprocess.run(
            ["gh", "api", "repos/%s/compare/%s...%s" % (repo, prod, integ),
             "--jq", ".ahead_by"],
            cwd=cwd, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        if _gh_not_found(r.stderr):
            return {"ahead": 0, "in_flight": False, "train": False}
        return None
    try:
        ahead = int((r.stdout or "").strip())
    except (ValueError, TypeError):
        return None
    # (2) 3-branch gate (review F6, widened by #698): a real release train has a
    # `staging` branch — checked on the DRAINED path too, because the job-20
    # release-landed escalation may only ever claim "train drained" for a
    # PROVEN train (a 2-branch repo with a stray `develop` == prod must read
    # `train` False, never silently pass as drained). Costs extra cached gh
    # calls on the drained path (staging here, the in-flight checks below); a
    # transient staging error now also defers the drained verdict for one
    # fail-TTL (None) instead of guessing the train shape. Missing staging ->
    # not a release repo (with a gap, that stays the clean "no gap" the F6
    # review demanded).
    try:
        r = subprocess.run(
            ["gh", "api", "repos/%s/branches/%s" % (repo, staging), "--jq", ".name"],
            cwd=cwd, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0 and not _gh_not_found(r.stderr):
        return None
    train = r.returncode == 0
    if not train:
        return {"ahead": 0, "in_flight": False, "train": False}
    # (3) in-flight: an open release PR whose base is staging or prod, queried
    # server-side per base (review F1 — never a truncated default-limit window).
    # #698 review fix: MEASURED for the DRAINED (ahead 0) verdict too, not only
    # the gap — right after a staging->prod release PR merges, `ahead` is
    # already 0 while the push-triggered deploy still runs, and the
    # release-landed escalation must never claim "no release PR / deploy
    # running" it never checked (2-4 extra cached gh calls per drained train
    # repo per TTL, same trade the staging gate above already accepted).
    in_flight = False
    for base in (staging, prod):
        try:
            r = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "open",
                 "--base", base, "--json", "number", "--limit", "1"],
                capture_output=True, text=True, timeout=15)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        try:
            prs = json.loads(r.stdout or "[]")
        except (ValueError, TypeError):
            return None
        if isinstance(prs, list) and prs:
            in_flight = True
            break
    # (4) a genuine deploy/release workflow running/queued (only if no release PR),
    # server-side status-filtered + event-filtered (review F1 both directions).
    if not in_flight:
        runs = []
        for st in ("in_progress", "queued"):
            try:
                r = subprocess.run(
                    ["gh", "run", "list", "--repo", repo, "--status", st,
                     "-L", "30", "--json", "status,headBranch,name,event"],
                    capture_output=True, text=True, timeout=15)
            except Exception:
                return None
            if r.returncode != 0:
                return None
            try:
                page = json.loads(r.stdout or "[]")
            except (ValueError, TypeError):
                return None
            if isinstance(page, list):
                runs.extend(page)
        in_flight = _release_train_run_in_flight(runs, staging, prod)
    return {"ahead": ahead if ahead > 0 else 0, "in_flight": in_flight,
            "train": True}


def _watchdog_vault_purge():
    """Job 29's credential-store sweep (#144) — the injection point so run_once
    never imports the store (or touches a real `~/.claude/secrets/`) in a test.

    The store's TTL used to be enforced only by the NEXT `airuleset.py secret`
    invocation, so the ordinary one-off shape — request a credential, use it
    once, never run the command again — left the value on disk indefinitely.
    A box that already sweeps every 60s is the right place for an expiry that
    must not depend on anyone remembering to run something."""
    from filedrop.vault import purge
    return purge()


def _watchdog_vault_backstop():
    """Job 29's durable-persistence backstop (#529) — the injection point so
    run_once never imports the store in a test. Returns [(name, path)] for every
    `ready` credential that registered a durable ~/.secrets/<name> target whose
    FILE is missing (delivery without persistence — the #134 artifact-gate).
    Pure read: it never touches the credential value."""
    from filedrop.vault import durable_backstop
    return durable_backstop()


def _watchdog_repo_roots():
    """Jobs 27/28's repo enumeration (#137) — every `.git` this box hosts,
    per #138's own corrected lesson that the corpus is `$HOME`, never a
    guessed project directory. `discover_managed_repos` does the actual
    `os.walk`; this is just the injection point so run_once's unit tests
    never touch the real filesystem."""
    from watchdog import discover_managed_repos
    return discover_managed_repos()


def _watchdog_is_deploy_target():
    """Job 34 (#535 review MAJOR-A): True iff THIS box is a fleet DEPLOY TARGET —
    its tailscale IP is one of the REMOTE_HOSTS `host` values — i.e. a box that
    receives read-only `git pull --ff-only` deploys and NEVER develops airuleset,
    so a dirty airuleset tree there is unambiguously a hand-edit (DRIFT). The DEPLOY
    SOURCE (dev1) is NOT in REMOTE_HOSTS and develops airuleset directly, so its main
    checkout is legitimately dirty even at HEAD==origin; the conformance dirty
    dimension must skip it. Username matching (`_current_remote_host_entry`) is
    INSUFFICIENT — dev1 + dev2 + spinbike all share `newlevel`, so it would
    misclassify dev1 as dev2's entry — the tailscale IP is the reliable box identity.
    Fail-safe: any error (no tailscale, non-zero rc, parse failure) → False → the
    dirty dimension is SKIPPED (never a false alarm), the module's prime invariant."""
    import subprocess
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                           text=True, timeout=5)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    my_ips = {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}
    target_hosts = {e.get("host") for e in REMOTE_HOSTS if e.get("host")}
    return bool(my_ips & target_hosts)


def _watchdog_git_fetch(root):
    """Job 28's best-effort ref refresh — same shape as job 24's own probe
    fetch, minus the enrichment half (job 28 needs no blocker lookup, only
    fresh refs). Errors are swallowed by the caller (logged, never raised).

    #172: timeout cut 90s -> 15s. One hung `git fetch` must never eat most
    of the 120s `TimeoutStartSec` unit budget — the repo-batch cap
    (`_repo_sweep_batch`, `AIRULESET_REPO_SWEEP_BATCH`) bounds how many
    repos this costs per sweep; this bounds what ONE of them can cost."""
    import subprocess
    subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                    "origin"], capture_output=True, timeout=15, check=True)


def _watchdog_issue_counts_fetch(repo_label, window_s):
    """Job 27's trailing-window opened/closed count via `gh` (#137).

    Wired HERE, like every other network call, so run_once's unit tests stay
    network-free. `repo_label` is `owner/name` (from `_repo_label`, i.e. the
    remote, never a directory basename). Returns `(opened, closed)` or None
    on any failure — never treated as a stall, per the "never block on
    don't-know" contract every other fetch in this file already follows.

    #172: timeout cut 45s -> 10s per `gh` call (two calls per repo, so 20s
    worst case per repo instead of 90s) — same reasoning as
    `_watchdog_git_fetch`, paired with the repo-batch cap."""
    import subprocess
    import time
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_s))
    try:
        opened = subprocess.run(
            ["gh", "issue", "list", "-R", repo_label, "--state", "all",
             "--search", "created:>=%s" % since[:10], "--limit", "1000",
             "--json", "number"],
            capture_output=True, text=True, timeout=10)
        closed = subprocess.run(
            ["gh", "issue", "list", "-R", repo_label, "--state", "closed",
             "--search", "closed:>=%s" % since[:10], "--limit", "1000",
             "--json", "number"],
            capture_output=True, text=True, timeout=10)
        if opened.returncode != 0 or closed.returncode != 0:
            return None
        return (len(json.loads(opened.stdout or "[]")),
                len(json.loads(closed.stdout or "[]")))
    except Exception:
        return None


def cmd_watchdog(args):
    """One poll cycle: scan `claude` tmux panes, auto-`continue` the ones stalled
    on an API error, ping on stall + give-up + on a session waiting on the user,
    (rate-limited) alert when the weekly token limit nears its cap, route an
    owner's Discord REPLY back into the session that asked the ❓, and backstop
    gatekeeper-returned prio:bounce tickets (nudge idle pane / Discord ping),
    RESTARTS any long-lived session still parked on Fable/Opus-4 so it picks
    up the managed default model by construction — never `/model`, which a
    running session's fixed-at-start model list can never accept (#42
    rework of job 12), writes an hourly burn snapshot
    (#37 job 13, the automatic --compare feedback loop), types `/compact`
    into a session whose Stop hook just recorded a completed-ticket report
    once its pane goes genuinely idle (#39 krok 1c job 14), and — for a
    long-lived session that never reports a ticket — `/compact`'s any
    session whose context exceeds 400K tokens once it has sat genuinely
    idle for 20+ minutes with no worker in flight (#39/#43 job 15), and — ONLY
    on the coordinator box (dev1) — merges every managed box's own hourly
    burn-snapshot row into one combined fleet.jsonl row, pinging when the
    observed weekly-%/day pace exceeds budget (#55 job 16), pings a SECOND,
    independent way right after that merge when the completed hour itself
    crosses an absolute/relative/weekly-step threshold (#81 job 19), and — since
    Claude Code snapshots its hook set once at process start and never
    re-reads it — RESTARTS any long-lived session whose settings.json hooks
    block content hash no longer matches the hash it started under, so a
    newly-deployed hook actually takes effect instead of staying inert for
    that session's whole remaining lifetime (#70 job 18, coalesced with job
    12's model restart into one restart when both fire the same sweep), and
    — since an armed `/goal` can die SILENTLY, in the same process, without
    ever writing a `Goal cleared:` marker — cross-checks each session's
    transcript marker against CC's own `◎ /goal` footer indicator and
    re-arms a proven mismatch with the marker's exact bytes, bounded, with
    one ping on give-up (#76 job 20). Driven by the systemd timer.

    Job logs print UNCONDITIONALLY (issue #36) — the systemd unit runs
    `watchdog --once` with NO `--verbose`, so gating the print behind that
    flag meant every job's arm/skip/ping decision was silently lost in
    production; the journal showed only systemd boilerplate, and the
    strip-selection keystroke bug (#36 itself) was undebuggable from it.
    `--verbose` is kept for any additional debug output a caller wants later.

    #172: printing is now INCREMENTAL (`log_fn=`, below) rather than
    "collect the whole list, print it after run_once() returns" — a sweep
    killed mid-way by systemd's TimeoutStartSec=120 never returns at all,
    so the old collect-then-print shape showed NOTHING in the journal for
    the whole 14h the #172 livelock recurred, even though early jobs (incl.
    job 1's 529 auto-resume) had already decided plenty before a later
    job's hung network call ate the rest of the unit's budget.

    #172 (reopened) finding 1: a bare `log_fn=print` still did not fix the
    "prints nothing" symptom in production. `ExecStart` runs with no `-u`
    and no `PYTHONUNBUFFERED=1`, so under systemd stdout is a PIPE and
    CPython block-buffers it (8 KiB) — `print()` WITHOUT `flush=True` never
    actually leaves that buffer, and SIGTERM discards it unread. Measured:
    `print('x')` + SIGTERM 1s later captured nothing; `print('x',
    flush=True)` + the same SIGTERM captured 'x'. `log_fn` below now wraps
    `print` with an explicit flush so a decision line is genuinely durable
    the instant it is logged, not merely "printed" into a buffer a kill can
    still erase."""
    import burn
    from watchdog import run_once, fetch_usage, fetch_channel_messages
    from watchdog import compact as _compact_mod
    from watchdog import goal as _goal_mod
    # Job 16 (#55) is coordinator-only: every OTHER managed box already writes
    # its own local hourly row via job 13, so only dev1 fans out over ssh to
    # merge them. `os.uname().nodename` is the same "which host am I" check
    # `burn.local_report()`/`hourly_snapshot()` already use as their own
    # host tag — the machine hostnames ARE the tailscale/MagicDNS names now
    # (machine-identities.md), so this is a plain string compare, no ssh probe.
    fleet_fetch = _watchdog_fleet_fetch if os.uname().nodename == "dev1" else None
    # Job 19 (#81) is coordinator-only for the identical reason job 16 is:
    # every OTHER managed box never writes fleet.jsonl at all (only dev1
    # collects the merged fleet view), so evaluating it anywhere else would
    # just see an empty file. Same host check, reused verbatim.
    burn_alert_enabled = os.uname().nodename == "dev1"
    # Job 35 (#543) is coordinator-only for the SAME reason job 16/19 are: only
    # dev1 collects the merged fleet.jsonl this dead-box detector reads. Same
    # host check, reused verbatim — every OTHER box would just see an empty file.
    conformance_hb_enabled = os.uname().nodename == "dev1"
    logs = run_once(dry_run=getattr(args, "dry_run", False), usage_fetch=fetch_usage,
                    discord_fetch=fetch_channel_messages,
                    bounce_fetch=_watchdog_bounce_fetch,
                    gkreq_fetch=_watchdog_gkreq_fetch,
                    # #516 — job 31 gk self-service auto-bounce, gated on this
                    # fetch being wired (network-free tests for every other job).
                    gk_selfservice_fetch=_watchdog_gk_selfservice_fetch,
                    # (#461's owner-decision digest fetch was wired here until
                    # #707 retired the whole message class.)
                    # #515 — job 32 mechanical U-label lifecycle, gated on this
                    # clear-fn being wired (network-free tests for every other
                    # job). Clears a needs-answer/needs-decision label whose
                    # question the owner already answered on Discord.
                    u_reconcile_clear=_watchdog_u_reconcile_clear,
                    # Job 24 (#138) runs on EVERY managed box — a loop whose
                    # merges have stopped is a per-repo failure, not a
                    # coordinator-only one, and the box that hosts the loop
                    # is the one holding the checkout it has to read.
                    delivery_probe=_watchdog_delivery_probe,
                    # Job 25 (#134) runs on EVERY managed box for the same
                    # reason: a ticket that merged without a report is a
                    # per-repo failure, and the box hosting the loop holds
                    # the checkout that proves it.
                    card_probe=_watchdog_card_probe,
                    closed_fetch=_watchdog_closed_fetch,
                    # #182: same job (25), an ADDITIVE step — a reopened
                    # ticket's stale run-card marker is cleared so its next
                    # close's card claims fresh. Changes zero consumers of
                    # the existing <repo>#<issue> key format.
                    reopen_fetch=_watchdog_reopened_fetch,
                    burn_snapshot_path=burn.snapshots_path(),
                    compact_requests_path=_compact_mod.compact_requests_path(),
                    fleet_fetch=fleet_fetch, fleet_hosts=REMOTE_HOSTS,
                    fleet_path=burn.fleet_path(),
                    burn_alert_enabled=burn_alert_enabled,
                    # Jobs 9/20 (#403, collapsing #76) run on EVERY managed
                    # box — a silently dead /goal, or a still-pending arm
                    # request nothing has re-evaluated yet, is a per-session
                    # failure, not a coordinator-only one (it was montalu's
                    # stream that lost its loop twice in a day). Templates
                    # are resolved ONCE, at `goal-arm` record time (never
                    # per-sweep any more) — see `watchdog/goal.py`'s own
                    # module docstring.
                    goal_jobs_enabled=True,
                    goal_requests_path=_goal_mod.goal_requests_path(),
                    # Job 21 (#84) likewise runs on EVERY managed box — a
                    # multi-hour turn starves compaction, question delivery
                    # and the keystroke queue of THAT session, wherever it
                    # runs. Detection only, so it never types into a pane.
                    long_turn_enabled=True,
                    # Job 26 (#140) — REMOVED (#402, 2026-08-12). Used to
                    # watch the shared /compact claim file for a stuck
                    # entry; that whole claim system was retired by the
                    # compact collapse. See run_once's own docstring.
                    # Jobs 27/28 (#137) run on EVERY managed box — both are
                    # per-repo local/gh reads, and each box holds the
                    # checkouts it can actually measure. Self-gated hourly
                    # internally, so wiring them costs nothing on the 59
                    # sweeps out of 60 that skip.
                    vault_purge=_watchdog_vault_purge,
                    vault_backstop=_watchdog_vault_backstop,
                    repo_roots=_watchdog_repo_roots,
                    issue_counts_fetch=_watchdog_issue_counts_fetch,
                    git_fetch=_watchdog_git_fetch,
                    # #160 defects 1/4 run on EVERY managed box — both are
                    # per-repo `gh` reads (cached per cwd, 10-min TTL, so a
                    # box with several panes on one repo costs at most one
                    # extra call per window) consulted by job 20's
                    # goal-achieved backstop and job 10's widened wedge ping.
                    backlog_fetch=_watchdog_backlog_fetch,
                    # #547 — job 20's W/ops-wait re-check nudge reads the parked
                    # W member numbers per repo. The raw fetch is a per-cwd `gh`
                    # read, but the orchestrator reads it through the module's own
                    # per-repo TTL cache (`_cached_ops_wait`, the sibling of
                    # `_cached_backlog_count`), so it fires at most once per repo
                    # per OPS_WAIT_FETCH_TTL_S — never every sweep per pane.
                    ops_wait_fetch=_watchdog_ops_wait_fetch,
                    # #714 — the job-20 partition-audit nudge is now a compact
                    # COUNT trigger, so the watchdog no longer FETCHES the I
                    # members to name them: it points the session at `slice-quals
                    # --audit`, which the session runs itself. The old
                    # `_watchdog_i_members_fetch`/`_parse_i_audit_lines` seam was
                    # removed (#486 net-LOC-down); the `--audit` CLI stays.
                    # #616 — job 20's release-gap rider reads the release-train
                    # state (integration-ahead-of-prod + release-in-flight) per
                    # repo. Read on EVERY recheck of a FULL-authority armed pane
                    # (the decision NEEDS it — the #547 placement, not the #578
                    # nudge-branch one), but through `release_gap.
                    # _cached_release_state` (per-repo TTL cache) so the gh calls
                    # fire at most once per repo per TTL, never every 60s sweep.
                    # #698: the SAME seam also feeds the ops-wait rider's
                    # release-landed escalation (nudge-branch-only, shared
                    # cache) — TWO job-20 consumers, one fetch.
                    release_state_fetch=_watchdog_release_state_fetch,
                    # #733 — job 20's gk queue-arrival rider reads the queue
                    # union per repo (3 exact-label `gh` queries), cached per
                    # repo per TTL (~5 min) inside the module, FULL-authority
                    # only. Wired on EVERY box; the rider self-gates authority.
                    queue_fetch=_watchdog_queue_fetch,
                    # Job 34 (#535) — per-box conformance check runs on EVERY
                    # managed box: config/repo drift is a per-box failure, and
                    # each box holds the airuleset checkout it can measure.
                    # REPO_DIR is this box's airuleset repo (the systemd unit
                    # runs the watchdog from it). Internally daily-cadenced.
                    conformance_root=REPO_DIR,
                    # #535 review MAJOR-A: a CALLABLE (invoked only on the daily
                    # check, not every 60s sweep) so the dirty dimension runs ONLY
                    # on a confirmed deploy target — the dev1 SOURCE box is
                    # legitimately dirty and must not false-alarm.
                    conformance_is_target=_watchdog_is_deploy_target,
                    # #543 job 35 — dev1-only central dead-box detector, gated on
                    # the SAME coordinator host check job 16/19 use (only dev1
                    # has the fleet.jsonl it reads). Internally cadence-gated.
                    conformance_hb_enabled=conformance_hb_enabled,
                    # Job 36 (#551) — orphaned gk hand-off marker backstop.
                    # Runs on the SUPERVISOR box only (internally gated), for
                    # cross-stream repos; gated on this fetch being wired
                    # (network-free tests for every other job, like jobs
                    # 8/11/31). Internally 6h-cadenced.
                    gkorphan_fetch=_watchdog_gkorphan_fetch,
                    # #570: the parallel comment-handoff pass (proper
                    # GATEKEEPER-ACTION/READY-FOR-REVIEW marker comment in a ~48h
                    # window that never got its label) — wired = on, same
                    # network-free-tests convention.
                    gkorphan_handoff_fetch=_watchdog_gkorphan_handoff_fetch,
                    # #172: print each job's decision line AS IT HAPPENS,
                    # not only from the list run_once() returns — a sweep
                    # killed mid-way (systemd TimeoutStartSec=120) used to
                    # print NOTHING at all, for 14h, because the only print
                    # path was this loop running AFTER run_once() returned.
                    # #172 REOPENED finding 1: bare `print` alone is NOT
                    # enough — under systemd's piped, non-tty stdout, an
                    # unflushed print sits in CPython's 8 KiB buffer and a
                    # SIGTERM discards it unread, reproducing the exact
                    # "prints nothing" symptom this fix exists to kill.
                    log_fn=lambda line: print(line, flush=True))
    del logs   # already streamed via log_fn above; nothing left to print


def cmd_compact_request(args):
    """The two proven-origin entry points for the collapsed `/compact`
    delivery model (#402 — see `watchdog/compact.py`'s own module
    docstring for the full design).

    `--self`: the calling SESSION's own explicit callback ("I am at a safe
    boundary right now") — resolves the calling pane via `$TMUX_PANE`
    (`resolve_self_pane`), then makes ONE immediate synchronous delivery
    attempt via `compact._compact_sync_attempt` (records the request under
    the `self-callback` proven-boundary origin, waits at most a small
    bounded margin over `COMPACT_MIN_REQUEST_AGE_S` — never a multi-second
    hold, never a retry loop — then evaluates once). If the attempt is not
    safe right now (a live sibling task, a busy pane, ...), the request is
    simply LEFT PENDING for the periodic sweep (job 14, ~60s cadence) to
    pick up — bounded, eventually, by the hard age cap.

    `--record --session <sid> --cwd <cwd> --origin <origin>`: the hook-side
    entry point (SAME ONE synchronous attempt). In production only
    `stop-check-prose-violations.sh` fires it (`--origin self-callback`); #610
    retired the `subagent-stop` producer (still accepted generically).

    `--status` (#741): a read-only HOLD probe — resolves the session like
    `--self` (or an explicit `--session <sid>`), prints one line (`PENDING
    sid=<sid> age=<n>s` / `NONE`) and exits 0. Records nothing, types nothing;
    the hold-turn doctrine's first action so a goal-fired turn can PROVE from the
    transcript whether the drained-boundary compact is still pending (hold) or
    done (dispatch the next batch).

    Prints the disposition word verbatim (`sent` / `expired` /
    `already-queued` / `cooldown` / `skip:<reason>` — `skip:no-session`
    covers BOTH a blank session id and a genuine record-time disk-write
    failure, since neither can be told apart from the caller's side) so
    the calling hook's own decision log stays a faithful trace of what
    actually happened."""
    from watchdog import compact
    if getattr(args, "status", False):
        # #741 read-only HOLD probe. Resolves the session like `--self` (via
        # $TMUX_PANE) or takes an explicit `--session <sid>`; prints exactly one
        # line — `PENDING sid=<sid> age=<n>s` (a `/compact` request is still
        # pending for this session) or `NONE` — and always exits 0. The hold-turn
        # doctrine (skills/autopilot Step 5) runs this as a goal-fired turn's
        # FIRST action: PENDING -> end the turn immediately with one `⏳ WORKING`
        # line and ZERO dispatches; NONE -> the boundary compact is done, the next
        # batch may be dispatched. Transcript-provable, no pane keystroke.
        sid = (getattr(args, "session", "") or "").strip()
        if not sid:
            _pane_id, _cwd, sid = compact.resolve_self_pane()
        entry = compact.load_compact_requests().get(sid) if sid else None
        if isinstance(entry, dict):
            import time as _time
            ts = entry.get("ts")
            age = "?"
            if isinstance(ts, (int, float)):
                age = "%d" % max(0, int(_time.time() - ts))
            # Trailing newline (UNLIKE the hook-captured --self/--record words):
            # --status is read by the SESSION from its own transcript, so it
            # renders cleanly on its own line.
            sys.stdout.write("PENDING sid=%s age=%ss\n" % (sid, age))
        else:
            sys.stdout.write("NONE\n")
        return
    if getattr(args, "self", False):
        pane_id, cwd, sid = compact.resolve_self_pane()
        if not sid:
            print("compact-request --self: could not resolve this session's "
                  "own pane/transcript (not running inside a recognized "
                  "tmux Claude Code pane, or $TMUX_PANE unset) -- nothing "
                  "recorded", file=sys.stderr)
            sys.exit(1)
        word = compact._compact_sync_attempt(
            sid, cwd, compact._COMPACT_SELF_CALLBACK_ORIGIN)
        sys.stdout.write(word)
        return
    if getattr(args, "record", False):
        session = (getattr(args, "session", "") or "").strip()
        origin = (getattr(args, "origin", "") or "").strip()
        if not session:
            sys.stdout.write("skip:no-session")
            return
        word = compact._compact_sync_attempt(session, args.cwd, origin)
        sys.stdout.write(word)
        return
    print("compact-request: nothing to do (use --record --session <sid> --cwd <cwd>)",
          file=sys.stderr)
    sys.exit(1)


def cmd_goal_arm(args):
    """The ONE proven entry point for the collapsed `/goal` arming model
    (#403 -- see `watchdog/goal.py`'s own module docstring for the full
    design). Called by the `/autopilot` skill's Step 2, as the session's
    OWN last tool call right after PRINTING the `/goal` line -- that print
    IS the callback moment.

    `--self`: resolves the calling SESSION's own pane/cwd/sid via
    `$TMUX_PANE` (the SAME `compact.resolve_self_pane()` job 14 already
    uses -- a `/goal` session and a `/compact` session are the identical
    kind of caller, so nothing new is needed here). The authority profile
    is `--template` if given, else `resolve_authority(cwd)` -- matching
    exactly what the skill's own printed `/goal` line was computed from,
    so the two can never disagree. The exact shipped template text for
    that profile is resolved fresh (`goal.goal_template_for_authority`,
    never a stale copy), the request is recorded, and ONE immediate
    synchronous delivery attempt is made. Prints the disposition word
    verbatim (mirrors `compact-request`'s own contract: `sent` / `expired`
    / `skip:<reason>`) so the calling turn's own decision log stays
    honest -- though the REAL delivery path is the periodic sweep (job 9,
    `goal.goal_sweep`) picking the still-pending request back up once the
    pane genuinely goes idle."""
    from watchdog import goal as _goal_mod
    from watchdog import compact as _compact_mod
    if not getattr(args, "self", False):
        print("goal-arm: nothing to do (only --self is supported)",
              file=sys.stderr)
        sys.exit(1)
    pane_id, cwd, sid = _compact_mod.resolve_self_pane()
    if not sid:
        print("goal-arm --self: could not resolve this session's own "
              "pane/transcript (not running inside a recognized tmux "
              "Claude Code pane, or $TMUX_PANE unset) -- nothing "
              "recorded", file=sys.stderr)
        sys.exit(1)
    authority = (getattr(args, "template", "") or "").strip() or resolve_authority(cwd)
    text = _goal_mod.goal_template_for_authority(authority)
    if not text:
        print("goal-arm --self: could not resolve a /goal template for "
              "authority=%r (unreadable SKILL.md, no matching block, or "
              "over Claude Code's 4000-char cap) -- nothing recorded"
              % authority, file=sys.stderr)
        sys.exit(1)
    word = _goal_mod._goal_sync_attempt(sid, cwd, text, authority,
                                        "self-callback")
    sys.stdout.write(word)


# --- #433 cluster L-E: the autopilot authority profiles (AUTHORITY_PROFILES +
# AUTHORITY_BY_USER) promoted to the constants-only leaf cli_fleet.py —
# re-exported here so every resident reader + every shipped leaf that reads
# airuleset.AUTHORITY_BY_USER (cli_quals, cli_bashrc_appliers, cli_deployer_glue,
# watchdog) + every test patching airuleset.AUTHORITY_BY_USER keep working
# unchanged.
from cli_fleet import (  # noqa: E402, F401
    AUTHORITY_PROFILES as AUTHORITY_PROFILES,
    AUTHORITY_BY_USER as AUTHORITY_BY_USER,
    STREAM_RENAME_ALIASES as STREAM_RENAME_ALIASES,
)


# --- #263: subdev stream dev-env bootstrap (claude tmux session + gap report) --
def _stream_session_cwd() -> Path:
    """The convention working directory for the #263 tmux bootstrap of a
    subdev STREAM account (see STREAM_DEV_CWD_CHAIN's own comment, above
    apply_ultracode_launcher). Its only caller, ensure_stream_tmux_session(),
    early-returns for any account NOT in AUTHORITY_BY_USER, so this function is
    never reached for gatekeeper -- gatekeeper's own ssh-attach cwd is computed
    by the bash block's inline chain loop over the SAME STREAM_DEV_CWD_CHAIN,
    not here. #563: a FALLBACK CHAIN -- the first EXISTING dir of
    STREAM_DEV_CWD_CHAIN wins (odoo-erp, then devel/odoo), else $HOME. The old
    binary "odoo-erp or $HOME" fallback dropped montalu1 (project dir
    ~/devel/odoo, no odoo-erp subdir) into $HOME. Falling back to $HOME keeps
    bootstrap from hard-failing on a stream account gatekeeper hasn't finished
    Phase 1 for."""
    home = Path.home()
    for rel in STREAM_DEV_CWD_CHAIN:
        p = home / rel
        if p.is_dir():
            return p
    return home


def _tmux_session_exists(name, run=None):
    """None = "can't tell" (tmux unreachable/missing) -- the caller must
    treat that as "don't touch", never as "doesn't exist".

    `-t "=%s"` is the EXACT-match target form -- a bare `-t name` does
    PREFIX matching (live-verified against tmux 3.7b: with only a session
    named `montalu2-review` alive, `has-session -t montalu2` returns rc=0,
    i.e. "exists", even though no session named exactly `montalu2` does).
    Without the `=` anchor this function would silently report a stream
    account "already has its session" and skip provisioning it forever."""
    run = run or _default_tmux_run
    try:
        result = run(["tmux", "has-session", "-t", "=%s" % name])
    except Exception:
        return None
    return getattr(result, "returncode", 1) == 0


def _tmux_session_pane_cwd(name, run=None):
    """The pane cwd of the EXACT-match tmux session `name`, or None when it
    can't be determined (tmux unreachable, no matching session/pane,
    empty/failed output) -- the caller must treat None as "inconclusive,
    stay quiet", never as a mismatch. Read-only: `list-panes` never mutates
    anything, so this is safe to call even against a session
    ensure_stream_tmux_session() will never touch (#308).

    `list-panes -s -t "=<name>"`, NOT `display-message -t "=<name>"` (#308
    review CRITICAL finding, live-verified against this box's own real
    tmux 3.7b): `display-message`'s `-t` wants a PANE target -- a bare
    `=<session>` with no `:<window>` qualifier resolves to no pane at all,
    every `#{pane_*}` field expands to empty, and it STILL exits 0. That
    made the mismatch check below permanently inert (every session, live
    or not, read back as "can't determine" -> quiet). `list-panes -s`
    correctly targets the session and exits non-zero for a genuinely
    missing one, so "inconclusive" becomes a real, distinguishable signal.
    A session can have multiple panes/windows with different cwds; the
    FIRST non-empty line is the one compared (the common case is one
    window, one pane)."""
    run = run or _default_tmux_run
    try:
        result = run(["tmux", "list-panes", "-s", "-t", "=%s" % name,
                      "-F", "#{pane_current_path}"])
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    out = getattr(result, "stdout", "") or ""
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line
    return None


STREAM_TMUX_BOOTSTRAP_SENTINEL = CLAUDE_DIR / ".airuleset-stream-session-bootstrapped"


def ensure_stream_tmux_session(user=None, run=None, launch_script=None,
                                sentinel_path=None):
    """#263: bootstrap the ONE tmux session a subdev stream account is
    expected to have -- session name == the linux username (one user = one
    session, matching every already-working peer account's live-verified
    shape: montalu/marek/david/simap all have exactly one tmux session named
    after themselves), cwd the stream's odoo-erp checkout, `claude` launched
    inside it via a typed `send-keys` -- NEVER as the session's own
    foreground command. A still-missing (or first-run-OAuth-prompting)
    `claude` would otherwise kill the pane+session outright the instant it
    exits (tmux's `remain-on-exit` is off by default); typing it into an
    ordinary bash-backed pane degrades to a visible error at a live prompt
    instead, matching what peer accounts already look like today (e.g.
    david's own second window sits on a bare `bash` right now).

    Scoped STRICTLY to AUTHORITY_BY_USER's keys -- dev1/dev2/gatekeeper are
    the human's own interactive login and are NEVER touched.

    ONE-TIME CREATE ONLY, ever, per account -- gated on `sentinel_path`
    (default `STREAM_TMUX_BOOTSTRAP_SENTINEL`). An adversarial review of the
    first version of this fix caught the real defect: "never touches an
    EXISTING session" alone is not enough, because a session that a human
    DELIBERATELY killed (out of token budget, done for the day, a VPS
    reboot) also reads as "doesn't exist" to has-session -- so the OLD code
    would silently re-create the session and auto-launch claude into it on
    the very next `push`, which is exactly the standing, angry, repeated
    user complaint this repo's own memory already records ('never touch a
    session the user deliberately stopped'). The sentinel is written the
    MOMENT this function makes its one real CREATE-OR-NOT decision (right
    after resolving `exists`, before creating anything) -- so even a failed
    first attempt never retries automatically, and a session that later
    disappears (killed on purpose) is never resurrected. A genuinely
    UNREACHABLE tmux (`exists is None`) does NOT write the sentinel --
    nothing was decided, so a later push may still get the very first real
    attempt.

    #309: the sentinel gates ONLY that create decision -- it does NOT gate
    the #308 cwd-mismatch probe below. Before #309, the sentinel-exists
    check was the function's very FIRST statement, so the probe was
    structurally unreachable for any account already bootstrapped by a
    prior push (i.e. the whole existing fleet -- #308's own fix had been
    inert in production for all of them). The probe is read-only (never
    kills/re-cwds/sends keys, per #308's own rule below) and cheap, so it
    now runs on EVERY call whenever a session exists, bootstrapped or not."""
    user = user or _current_user()
    if user not in AUTHORITY_BY_USER:
        return None
    sentinel = sentinel_path or STREAM_TMUX_BOOTSTRAP_SENTINEL
    run = run or _default_tmux_run
    exists = _tmux_session_exists(user, run)
    if exists is None:
        return "tmux unreachable -- left untouched"
    bootstrapped = sentinel.exists()
    if not bootstrapped:
        try:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("bootstrapped for %s\n" % user)
        except OSError as e:
            # Best-effort; a failed write just means one more push may retry
            # this same one-time decision -- never a resurrection of an
            # already-seen-and-stopped session, since none has been seen yet
            # on this account. Logged so a persistently-failing write (e.g. a
            # permissions problem) is visible rather than silently retried
            # forever.
            print("  ⚠ could not write %s (%s) -- this one-time bootstrap "
                  "decision may repeat on the next install" % (sentinel, e),
                  file=sys.stderr)
    if exists:
        # #308 (the miva1 incident): a session created by ANY path other
        # than this function's own bootstrap (manual account provisioning,
        # before the stream's registration) silently wins the first login
        # with the WRONG cwd forever -- ssh auto-attach's `-A` ignores `-c`
        # on attach, and this function deliberately never touches an
        # already-existing session. Report the mismatch LOUDLY; never
        # auto-kill, never re-cwd, never send keys -- that is the standing,
        # repeatedly-angry user rule ("never touch a session the user
        # deliberately stopped"). `actual is None` (tmux unreachable for
        # JUST this probe, no matching pane) stays quiet -- an inconclusive
        # read must never manufacture a false WARNING. #309: unconditional
        # now -- runs whether or not `bootstrapped` is true.
        expected = _stream_session_cwd()
        actual = _tmux_session_pane_cwd(user, run)
        if actual is not None:
            # #308 review MAJOR: a raw string compare false-positives on a
            # perfectly healthy session -- `cd`'d into a SUBDIRECTORY of
            # the checkout (routine odoo-erp work), or a symlinked $HOME
            # (tmux reads the pane cwd from /proc/<pid>/cwd, fully
            # resolved; Path.home() is not). Resolve both sides and accept
            # CONTAINMENT, not bare equality.
            try:
                exp_real = os.path.realpath(str(expected))
                act_real = os.path.realpath(actual)
            except Exception:
                exp_real, act_real = str(expected), actual
            contained = (act_real == exp_real
                         or act_real.startswith(exp_real.rstrip("/") + os.sep))
            if not contained:
                return ("WARNING: session '%s' already exists with cwd %s "
                         "(expected %s or a subdirectory of it) -- if this "
                         "is a leftover pre-registration session, kill it "
                         "manually; a push alone will NOT re-create it "
                         "(sentinel already bootstrapped) -- the account's "
                         "next ssh login rebuilds it fresh with the right "
                         "cwd (#264)"
                         % (user, actual, expected))
        if bootstrapped:
            return ("already bootstrapped once for '%s' -- never re-created "
                     "(a since-stopped session stays stopped)" % user)
        return "session '%s' already exists -- left untouched" % user
    if bootstrapped:
        return ("already bootstrapped once for '%s' -- never re-created "
                 "(a since-stopped session stays stopped)" % user)
    cwd = _stream_session_cwd()
    script = launch_script or CLAUDE_LAUNCH_SCRIPT_DEST
    try:
        r = run(["tmux", "new-session", "-d", "-s", user, "-c", str(cwd)])
        rc = getattr(r, "returncode", 1)
        if rc != 0:
            return ("FAILED to create session '%s' (rc=%s): %s"
                     % (user, rc, (getattr(r, "stderr", "") or "").strip()))
    except Exception as e:
        return "FAILED to create session '%s': %s" % (user, e)
    try:
        run(["tmux", "send-keys", "-t", user, "%s default" % script, "Enter"])
    except Exception as e:
        return ("session '%s' created in %s, but claude launch failed: %s"
                 % (user, cwd, e))
    return "created session '%s' in %s, claude launched" % (user, cwd)


def _stream_provisioning_gaps() -> list:
    """The genuinely-human-only steps remaining for the CURRENT subdev
    stream account (#263): the claude CLI's OWN first-run OAuth login, and
    a GitHub PAT / `gh auth login`. Neither is automatable (OAuth is an
    interactive human flow; a PAT is generated by a human in GitHub's UI)
    -- this only DETECTS and reports them, loudly, every install/push,
    matching #98's existing 'a sudo-less box that hits a still-missing dep'
    LOUD-reporting shape. Returns a list of human-readable gap strings
    (empty when fully provisioned).

    Deliberately takes NO `user` parameter -- an adversarial review of an
    earlier version flagged that every probe below is inherently
    `Path.home()`-relative, i.e. this account's own environment, and there
    is no way to introspect ANOTHER account's credentials without switching
    uid. A `user=` argument that was silently ignored is exactly the shape
    a future caller mis-uses (calls it for a DIFFERENT account expecting a
    per-account answer); dropping it removes the possibility entirely."""
    gaps = []
    creds = Path.home() / ".claude" / ".credentials.json"
    try:
        creds_ok = creds.is_file() and creds.stat().st_size > 0
    except OSError:
        creds_ok = False
    if not creds_ok:
        gaps.append("Claude Code login/session (human OAuth step) — run "
                     "`claude` interactively in this account's tmux session "
                     "and complete the login flow.")
    gh_hosts = Path.home() / ".config" / "gh" / "hosts.yml"
    git_creds = Path.home() / ".git-credentials"
    try:
        has_gh = gh_hosts.is_file() and gh_hosts.stat().st_size > 0
    except OSError:
        has_gh = False
    try:
        has_git_creds = git_creds.is_file() and git_creds.stat().st_size > 0
    except OSError:
        has_git_creds = False
    if not (has_gh or has_git_creds):
        gaps.append("GitHub PAT / `gh auth login` — no ~/.config/gh/"
                     "hosts.yml and no ~/.git-credentials found.")
    return gaps


def report_stream_dev_env(user=None):
    """#263: called from cmd_install() for every subdev stream account
    (AUTHORITY_BY_USER's keys) — reports the two still-human gaps LOUDLY
    (stderr, never silently, never merely stdout where it can scroll off
    among hundreds of routine install lines) and, once both are satisfied,
    RENAMES ~/TODO-PROVISIONING.md to ~/TODO-PROVISIONING.md.done if present
    (the file's own text: "Once all of the above land, delete this file" —
    gatekeeper's Phase-1 handoff contract; renaming rather than deleting is
    the same signal with zero data loss if a gap-closed read ever turns out
    to be a false positive — the file is gatekeeper-authored and airuleset
    cannot recreate it). No-op (prints nothing) for a non-stream account."""
    user = user or _current_user()
    if user not in AUTHORITY_BY_USER:
        return
    gaps = _stream_provisioning_gaps()
    todo = Path.home() / "TODO-PROVISIONING.md"
    if gaps:
        print("  ⚠ dev-env gap(s) on this stream account (human step required):",
              file=sys.stderr)
        for g in gaps:
            print("    - %s" % g, file=sys.stderr)
        if todo.exists():
            print("    (%s left in place — human steps above still missing)"
                  % todo, file=sys.stderr)
    elif todo.exists():
        done = todo.with_name(todo.name + ".done")
        try:
            todo.rename(done)
            print("  Renamed: %s -> %s (all provisioning steps confirmed complete)"
                  % (todo, done))
        except OSError as e:
            print("  ⚠ could not rename %s (%s) — remove/rename by hand"
                  % (todo, e), file=sys.stderr)


# Which Discord OWNER key a stream's linux user routes its pings under lives
# in `notify.STREAM_NOTIFY_OWNER` (airuleset#151/#259, 2026-08-06), not here —
# it is checked directly inside `notify.resolve_owner()` so it takes effect on
# the very next hook invocation everywhere, never via a bashrc export (which
# only reaches a shell started AFTER the write — an adversarial review of the
# first version of this fix live-verified simap's own already-running session
# kept misrouting after the bashrc line was applied, since restarting another
# user's live session is never done). `TestStreamAuthorityHasNotifyRouting`
# below cross-checks every AUTHORITY_BY_USER key against it.

# The maintainer's GitHub account. Some sub-dev boxes authenticate gh with a
# scoped PAT of THIS account (montalu), so @me search quals there match every
# maintainer-authored ticket — foreign streams leaked into the montalu footer
# (2026-07-20). A shared-account box scopes its slice by the stream LABEL only.
MAINTAINER_GH_LOGIN = "zbynekdrlik"

# The GitHub App whose INSTALLATION token every odoo-erp subdev stream
# (montalu, montalu2/3/4, marek, david2/3/4) authenticates `gh` as after the
# App-token migration (odoo-erp #3284, 2026-08-11). On such a box `gh api user`
# returns `403 Resource not accessible by integration` — an App installation
# token carries no user identity (see `cli_quals._is_gh_app_token_box()`'s own
# docstring) — so the box's OWN identity for the self-authored-close carve-out
# in `block-fork-no-merge-issue-close.sh` cannot be read from /user. But every
# ticket the stream FILES is authored by this fixed bot identity, so THAT is
# the box's self-close identity. Rendered in the GraphQL `app/<slug>` form
# because the hook compares it against `gh issue view --json author -q
# .author.login`, which renders this bot as exactly `app/odoo-erp-stream-tokens`
# (verified live 2026-08-14; the REST `.user.login` form is the different
# `odoo-erp-stream-tokens[bot]`, which the hook never reads).
#
# Scope (#463 adversarial review T-1): every montalu-family box authenticates as
# this SAME shared App identity, so the self-close carve-out fires for ANY ticket
# authored by it — including a sub-finding filed by a DIFFERENT subdev stream, not
# only the box's own. This is broader than strict per-stream self-close but is NOT
# a maintainer-review bypass: maintainer-ASSIGNED work is filed from dev1 and
# authored by MAINTAINER_GH_LOGIN (`zbynekdrlik`), never this App identity, so it
# stays blocked. Only self-vs-maintainer distinguishability is what the guard needs
# and preserves; stream-A-own vs stream-B-own is deliberately not distinguished.
STREAM_APP_BOT_LOGIN = "app/odoo-erp-stream-tokens"


def _current_user() -> str:
    import getpass

    return getpass.getuser()


def _gh_login(cwd=None):
    """The active gh login for this box, or **None** when the query itself
    FAILED. Cheap single call, run under `_gh_env()` (#181 I-6).

    It used to return `""` on any error, which made "gh api user failed"
    indistinguishable from "this box is not the maintainer" — and the ONE
    caller (`_slice_quals`) treats those two oppositely (#181 I-2). A broken
    `gh` therefore silently produced the own-account 3-qual union: the
    shared-account C2 validation was skipped entirely, and on odoo-erp
    `author:@me` re-opens the 2026-07-20 foreign-stream leak the branch
    exists to prevent. Live-reproduced on david@subdev, where `slice-quals`
    printed all three quals because this query fails, not because the login
    differs. None forces the caller to decide explicitly."""
    import subprocess
    try:
        r = subprocess.run(["gh", "api", "user", "-q", ".login"], cwd=cwd,
                           capture_output=True, text=True, timeout=15,
                           env=_gh_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


# --- #433 cluster I (footer/stop-proof: slice/core-quals/authority) split into
# two sibling leaf modules; re-exported here so every bare-name caller in this
# file + every `airuleset.X` consumer keeps resolving exactly as before. The
# leaves reach back for airuleset-resident names via a deferred `import airuleset`
# (internals #1481/#1484). File A = cli_quals.py (derivation core), File B =
# cli_quals_cmd.py (CLI + issue-row render).
from cli_quals import (  # noqa: E402  (#433 cluster I facade — leaf re-export)
    SliceUnresolved as SliceUnresolved,
    OPS_CHANNEL_LABEL as OPS_CHANNEL_LABEL,
    AUTOPILOT_SKIP_EXCL as AUTOPILOT_SKIP_EXCL,
    _core_search_excl as _core_search_excl,
    _gh_app_token_dir as _gh_app_token_dir,
    _is_gh_app_token_box as _is_gh_app_token_box,
    _stream_rename_equivalents as _stream_rename_equivalents,
    _slice_quals as _slice_quals,
    MAINTAINER_ACTION_LABELS as MAINTAINER_ACTION_LABELS,
    _obligation_quals as _obligation_quals,
    _repo_root as _repo_root,
    _ticket_is_stream_labeled as _ticket_is_stream_labeled,
    USER_WAITING_LABELS as USER_WAITING_LABELS,
    NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS as NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS,
    _row_is_user_waiting as _row_is_user_waiting,
    _user_waiting_reason as _user_waiting_reason,
    _partition_user_waiting as _partition_user_waiting,
    OPS_WAIT_LABELS as OPS_WAIT_LABELS,
    _row_is_ops_wait as _row_is_ops_wait,
    _ops_wait_reason as _ops_wait_reason,
    _partition_workable as _partition_workable,
    _acceptance_present_set as _acceptance_present_set,
    _comment_carries_question as _comment_carries_question,
    _issue_question_comment_state as _issue_question_comment_state,
    _no_question_flagged as _no_question_flagged,
    OPS_WAIT_EVIDENCE_MAX_S as OPS_WAIT_EVIDENCE_MAX_S,
    OPS_WAIT_STALE_MAX_FETCHES as OPS_WAIT_STALE_MAX_FETCHES,
    _stream_self_login as _stream_self_login,
    _issue_comment_ages as _issue_comment_ages,
    _stale_ops_wait_flagged as _stale_ops_wait_flagged,
    _release_recheck_flagged as _release_recheck_flagged,
    _gk_handoff_ops_wait_flagged as _gk_handoff_ops_wait_flagged,
    _authority_marker as _authority_marker,
    resolve_authority as resolve_authority,
    cmd_authority as cmd_authority,
    _label_exists_on_repo as _label_exists_on_repo,
    _search_index_healthy as _search_index_healthy,
    _union_open_issues as _union_open_issues,
    ROW_ACTION_ONLY as ROW_ACTION_ONLY,
    ROW_IMPLEMENT as ROW_IMPLEMENT,
    _stream_owner_of as _stream_owner_of,
    _own_handoff_label as _own_handoff_label,
    _ORIGIN_LABEL_RE as _ORIGIN_LABEL_RE,
    _last_origin_owner as _last_origin_owner,
    _slice_mine_and_handed as _slice_mine_and_handed,
)
from cli_quals_cmd import (  # noqa: E402  (#433 cluster I facade — leaf re-export)
    _row_action as _row_action,
    _print_issue_rows as _print_issue_rows,
    _refuse_unless_empty_is_trustworthy as _refuse_unless_empty_is_trustworthy,
    HANDOFF_LABEL_WORKFLOW_HINT as HANDOFF_LABEL_WORKFLOW_HINT,
    HANDOFF_RUN_OK_CONCLUSIONS as HANDOFF_RUN_OK_CONCLUSIONS,
    _handoff_label_mechanism_health as _handoff_label_mechanism_health,
    cmd_slice_quals as cmd_slice_quals,
    cmd_core_quals as cmd_core_quals,
)


UPLOAD_LOG_DIR_ENV = "AIRULESET_UPLOAD_LOG_DIR"


def _upload_log_path(port):
    """Where `upload` writes an endpoint's log — under the USER'S OWN tree.

    It used to be `/tmp/airuleset-upload-<port>.log`: a world-shared directory
    with the port as the only key, so the FIRST user to run on a port owned that
    filename for every other user on the box. dev1 still carries
    `-rw-rw-r-- montalu /tmp/airuleset-upload-8811.log`, and `--port 8811` as
    anyone else died with an unhandled `PermissionError` instead of a diagnosis
    (#115) — from the one CLI whose whole job is to be reachable the moment
    someone needs to hand a file over. Two users have two $HOMEs, so this cannot
    collide by construction rather than by luck of the port. AIRULESET_UPLOAD_LOG_DIR
    relocates it (tests — the same escape hatch filedrop gives itself with
    FILEDROP_DIR); the name stays port-keyed over a 21-port range, so the file set
    is bounded and reused by append, never accumulating."""
    base = os.environ.get(UPLOAD_LOG_DIR_ENV) or (Path.home() / ".claude" / "upload-logs")
    return Path(base) / f"upload-{port}.log"


def cmd_upload(args):
    """Stand up a web UPLOAD endpoint the user opens in their own browser.

    The user works over SSH with NO local filesystem access to any managed box —
    receiving a file FROM them is ALWAYS a drag-drop web URL, NEVER an scp/sftp
    ask (modules/core/receive-files-via-upload-url.md; incident david@gk
    2026-07-10). Spawns filedrop/upload_server.py DETACHED with an unguessable
    token, binds every PRIVATE interface (tailscale + LAN — bind_ips(); never the
    public IP, since this is a WRITE endpoint) and advertises ONE URL per interface
    so the user has a working link whether they are on tailscale or the LAN. Each
    URL is verified to answer 200 BEFORE printing (no-localhost-urls); the endpoint
    self-expires after --ttl seconds.

    #664 public-TLS drop lane: on a box with a LIVE drop lane AND (--public OR no
    tailscale), it instead binds 127.0.0.1 on the fixed drop port that the box's
    cloudflared tunnel fronts and advertises ONE public HTTPS URL (TLS at the
    edge, the token unchanged) — never an scp / ssh -L ask. --port/--allow-plain
    do not apply on that lane."""
    import secrets as _secrets
    import subprocess
    import time
    import urllib.request

    from filedrop import bind_ips

    dest = Path(getattr(args, "dir", None) or (Path.home() / "uploads")).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    ttl = int(getattr(args, "ttl", None) or 7200)

    # Bind + advertise every private interface (tailscale first, then LAN). Computed
    # FRESH here (unsandboxed) so it always reflects the current network.
    ips = bind_ips()

    # Public-TLS drop lane (#664): channel order tailscale -> public. On a box
    # with a LIVE drop lane AND (--public OR no tailscale), bind loopback on the
    # fixed drop port a managed cloudflared tunnel fronts and advertise ONE public
    # HTTPS URL — never an scp/ssh -L ask.
    from filedrop import _is_tailscale
    import cli_drop_gateway as _dg
    have_tailscale = any(_is_tailscale(ip) for ip in ips)
    public_lane = _dg.resolve_public_lane(getattr(args, "public", False), have_tailscale)
    if public_lane:
        public_host, port = public_lane
        if getattr(args, "port", None):
            print("upload: public drop lane — ignoring --port (fixed loopback "
                  "port %d, TLS via the tunnel)" % port, file=sys.stderr)
        ips = ["127.0.0.1"]
        if _pick_free_port(ips, [port]) is None:
            print("upload: public drop port %d is busy — another drop endpoint "
                  "(secret/upload) holds it; wait for it to close" % port,
                  file=sys.stderr)
            sys.exit(1)
    else:
        public_host = None
        port = int(getattr(args, "port", None) or 0) or None
        if port is None:
            # Probe the addresses the server is ABOUT TO BIND, not loopback (#115).
            port = _pick_free_port(ips, range(8799, 8820))
            if port is None:
                print("upload: no free port in 8799-8819", file=sys.stderr)
                sys.exit(1)

    token = _secrets.token_urlsafe(12)
    log = _upload_log_path(port)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        lf = open(log, "ab")
    except OSError as e:
        # A diagnosis, never the bare traceback #115 was filed for.
        print(f"upload: cannot open log {log}: {e}", file=sys.stderr)
        sys.exit(1)
    with lf:
        subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "upload_server.py"),
             token, str(port), ",".join(ips), str(dest), str(ttl)],
            stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True)

    def _live(u):
        try:
            return urllib.request.urlopen(u, timeout=2).status == 200
        except OSError:
            return False

    urls = [f"http://{ip}:{port}/{token}/" for ip in ips]
    # Wait for ANY interface to come up (no-localhost-urls) — NOT urls[0]
    # specifically: the upload_server skips an interface that fails to bind (a
    # transiently-down tailscale while the LAN binds fine), so gating on the first
    # URL alone would abort + orphan a working endpoint on another interface.
    for _ in range(20):
        if any(_live(u) for u in urls):
            break
        time.sleep(0.25)
    else:
        print(f"upload: endpoint failed to come up — see {log}", file=sys.stderr)
        sys.exit(1)
    if public_host:
        # The loopback bind is the origin; the tunnel fronts it under TLS. Print
        # the ONE public URL (its reachability is a go-live property gated by the
        # marker the reconciler wrote), not the un-routable loopback address.
        print(_dg.public_url_line(public_host, token))
    else:
        reachable = [u for u in urls if _live(u)] or [urls[0]]
        for u in reachable:   # one URL per interface — open whichever your network reaches
            print(u)
    print(f"dest={dest}  ttl={ttl}s  log={log}")
    if public_host:
        print("Otvor URL v prehliadači. Po nahratí over: grep SAVED " + str(log))
    else:
        print("Otvor ktorúkoľvek URL v prehliadači (podľa siete). Po nahratí over: "
              "grep SAVED " + str(log))


# --- #433 cluster H: the whole `secret` (credential-vault) CLI cluster +
# `_pick_free_port` live in cli_vault.py now — re-exported here so every
# existing reference (SUBCOMMANDS["secret"], main()'s SECRET_ACTIONS argparse
# wiring, cmd_upload's bare-name _pick_free_port call, tests' airuleset._secret_*)
# keeps resolving through this module unchanged.
from cli_vault import (  # noqa: E402
    SECRET_ACTIONS as SECRET_ACTIONS,
    SECRET_MIN_TTL_S as SECRET_MIN_TTL_S,
    SECRET_MAX_TTL_S as SECRET_MAX_TTL_S,
    SECRET_MIN_KEEP_S as SECRET_MIN_KEEP_S,
    SECRET_MAX_KEEP_S as SECRET_MAX_KEEP_S,
    SECRET_PORTS as SECRET_PORTS,
    _SECRET_ENCRYPTED_IFACE as _SECRET_ENCRYPTED_IFACE,
    _pick_free_port as _pick_free_port,
    _secret_clamp_ttl as _secret_clamp_ttl,
    _secret_clamp_keep as _secret_clamp_keep,
    _secret_bindable as _secret_bindable,
    _secret_iface_for as _secret_iface_for,
    _secret_opener as _secret_opener,
    _secret_health_url as _secret_health_url,
    _secret_probe_urls as _secret_probe_urls,
    _secret_is_encrypted as _secret_is_encrypted,
    _secret_partition_ips as _secret_partition_ips,
    _secret_select_ips as _secret_select_ips,
    _secret_url_line as _secret_url_line,
    _secret_redact as _secret_redact,
    _secret_apply_remainder as _secret_apply_remainder,
    _secret_request_names as _secret_request_names,
    _secret_parse_persist_map as _secret_parse_persist_map,
    _secret_request as _secret_request,
    cmd_secret as cmd_secret,
)


# --- #433 cluster J: the whole burn/fable-gate/delegation CLI cluster
# (cmd_fable_gate, the burn/fleet/delegation remote helpers + _FLEET_CACHE_MARKER,
# cmd_burn, cmd_delegation, and the #131/#130 delegation-meter helpers) lives in
# cli_burn.py now — re-exported here so every existing reference (SUBCOMMANDS,
# main()'s argparse wiring, cmd_watchdog's `fleet_fetch = _watchdog_fleet_fetch`,
# tests' airuleset._X access) keeps resolving through this module unchanged.
# cli_burn.py is a self-contained leaf; its 3 REMOTE_HOSTS-referencing functions
# reach that shared deploy registry via a lazily-placed deferred `import
# airuleset` (C/D technique), never a module-top back-import.
from cli_burn import (  # noqa: E402
    cmd_fable_gate as cmd_fable_gate,
    _burn_remote_cmd as _burn_remote_cmd,
    _remote_ssh_prefix as _remote_ssh_prefix,
    _burn_remote as _burn_remote,
    _FLEET_CACHE_MARKER as _FLEET_CACHE_MARKER,
    _fleet_remote_cmd as _fleet_remote_cmd,
    _hour_bucket_of_ts as _hour_bucket_of_ts,
    _parse_fleet_cache_section as _parse_fleet_cache_section,
    _fleet_remote_row as _fleet_remote_row,
    _watchdog_fleet_fetch as _watchdog_fleet_fetch,
    cmd_burn as cmd_burn,
    _delegation_remote_cmd as _delegation_remote_cmd,
    _delegation_remote as _delegation_remote,
    _gh_closed_issues_json as _gh_closed_issues_json,
    _closed_ticket_count as _closed_ticket_count,
    _attach_ticket_counts as _attach_ticket_counts,
    cmd_delegation as cmd_delegation,
)


# --- #433 cluster K: the whole autopilot-lock cluster (cmd_autopilot_lock,
# the #409 lock-litter discover/sweep + its constants, and the /proc campaign-
# ancestry helpers) lives in cli_autopilot_lock.py now — re-exported here so
# every existing reference (SUBCOMMANDS wiring, cmd_install's litter-sweep
# step, main()'s argparse default, tests' airuleset._autopilot_lock_* attrs)
# keeps resolving through this module unchanged.
from cli_autopilot_lock import (  # noqa: E402
    AUTOPILOT_LOCK_LITTER_LOG_PATH as AUTOPILOT_LOCK_LITTER_LOG_PATH,
    AUTOPILOT_LOCK_LITTER_STATE_PATH as AUTOPILOT_LOCK_LITTER_STATE_PATH,
    AUTOPILOT_LOCK_LITTER_MIN_INTERVAL_S as AUTOPILOT_LOCK_LITTER_MIN_INTERVAL_S,
    AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT as AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT,
    _CAMPAIGN_ANCESTRY_MAX_HOPS as _CAMPAIGN_ANCESTRY_MAX_HOPS,
    _CAMPAIGN_LONG_LIVED_COMMS as _CAMPAIGN_LONG_LIVED_COMMS,
    _autopilot_lock_path as _autopilot_lock_path,
    _autopilot_lock_read as _autopilot_lock_read,
    _autopilot_lock_litter_min_age_s as _autopilot_lock_litter_min_age_s,
    _campaign_pid as _campaign_pid,
    _pid_alive as _pid_alive,
    _proc_comm as _proc_comm,
    _proc_parent_pid as _proc_parent_pid,
    _log_autopilot_lock_litter_sweep_results as _log_autopilot_lock_litter_sweep_results,
    cmd_autopilot_lock as cmd_autopilot_lock,
    cmd_sweep_autopilot_locks as cmd_sweep_autopilot_locks,
    discover_autopilot_lock_litter as discover_autopilot_lock_litter,
    sweep_autopilot_lock_litter as sweep_autopilot_lock_litter,
)


# (watchdog_disable_marker / setup_watchdog_service / maybe_setup_watchdog +
#  WATCHDOG_* -> cli_filedrop_watchdog.py, #433 L-B)


from cli_onboard import (  # noqa: E402
    cmd_onboard_project as cmd_onboard_project,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="airuleset",
        description="Claude Code configuration management tool",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("install", help="Deploy config to ~/.claude/")
    sub.add_parser("diff", help="Show what install would change")
    sub.add_parser("validate", help="Check all files exist and resolve")
    sub.add_parser("status", help="Show current managed config")
    sub.add_parser("push", help="Push to GitHub + install locally + deploy to all remotes")

    # --- Tier-0 target/ retention: manual/testable purge entry point (#315)
    p_purge = sub.add_parser(
        "purge-targets",
        help="Purge stale target/ dirs in maintained Tier-0 repos (#315)")
    p_purge.add_argument("--dry-run", dest="dry_run", action="store_true",
                         help="Report what would be purged without deleting anything")
    p_purge.add_argument("--max-age-days", dest="max_age_days", type=int, default=None,
                         help=f"Age threshold in days (default {TARGET_PURGE_MAX_AGE_DAYS_DEFAULT})")

    # --- Stale worktree sweep: manual/testable entry point (#345) ---------
    p_sweep_wt = sub.add_parser(
        "sweep-worktrees",
        help="Reclaim dead-worker worktrees + branches (0 commits ahead, "
             "unlocked, clean) fleet-wide (#345)")
    p_sweep_wt.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="Report what would be removed without deleting anything")
    p_sweep_wt.add_argument("--salvage", dest="salvage", action="store_true",
                            help="Also LOUDLY report abandoned worktrees carrying unmerged/"
                                 "dirty work (never auto-removed) -- scans every managed repo "
                                 "+ a network ls-remote per candidate, so it is opt-in (#513)")

    # --- Merged worktree-lane target/ reclaim: manual/testable entry (#545) --
    p_sweep_lane = sub.add_parser(
        "sweep-lane-targets",
        help="Reclaim the target/ of a worktree LANE whose branch is MERGED "
             "(0-ahead + authored-commit reflog), idle + not in live use (#545)")
    p_sweep_lane.add_argument("--dry-run", dest="dry_run", action="store_true",
                              help="Report what would be reclaimed without deleting anything")

    # --- Old Claude CLI binary sweep: manual/testable entry point (#355) --
    p_sweep_cli = sub.add_parser(
        "sweep-cli-versions",
        help="Reclaim old Claude CLI binaries under ~/.local/share/claude/"
             "versions/ -- keeps current + one rollback version (#355)")
    p_sweep_cli.add_argument("--dry-run", dest="dry_run", action="store_true",
                             help="Report what would be removed without deleting anything")
    p_sweep_cli.add_argument("--min-age-days", dest="min_age_days", type=int, default=None,
                             help=f"Age threshold in days for a NON-kept version "
                                  f"(default {CLI_VERSION_MIN_AGE_DAYS_DEFAULT})")

    # --- Autopilot-lock litter one-time cleanup: manual/testable entry
    # point (#409, a follow-up to #385) -------------------------------
    p_sweep_locks = sub.add_parser(
        "sweep-autopilot-locks",
        help="Reclaim pre-#385 autopilot-lock litter (.lock/.lock.mutex/"
             ".lock-real-target) accumulated in the real system tempdir (#409)")
    p_sweep_locks.add_argument("--dry-run", dest="dry_run", action="store_true",
                               help="Report what would be removed without deleting anything")
    p_sweep_locks.add_argument("--min-age-s", dest="min_age_s", type=float, default=None,
                               help=f"Age threshold in seconds "
                                    f"(default {AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT})")

    # --- Claude scratch/tmp sweep: manual/testable entry point (#355) -----
    p_sweep_scratch = sub.add_parser(
        "sweep-claude-scratch",
        help="Reclaim aging /tmp/claude-<uid>/ session scratchpads for THIS "
             "account only (#355)")
    p_sweep_scratch.add_argument("--dry-run", dest="dry_run", action="store_true",
                                 help="Report what would be removed without deleting anything")
    p_sweep_scratch.add_argument("--min-age-days", dest="min_age_days", type=int, default=None,
                                 help=f"Age threshold in days "
                                      f"(default {CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT})")

    # --- Stray tempfile.mkdtemp litter sweep: manual entry point (#513) ----
    p_sweep_stray = sub.add_parser(
        "sweep-stray-tmp",
        help="Reclaim aged, uid-owned /tmp/tmp[a-z0-9_]{8} tempfile litter "
             "(ext4 htree ENOSPC source) -- REPORT-ONLY unless "
             "AIRULESET_TMP_PYTEST_RECLAIM_LIVE=1 (#513)")
    p_sweep_stray.add_argument("--dry-run", dest="dry_run", action="store_true",
                               help="Report the count/reclaimable summary without deleting anything")
    p_sweep_stray.add_argument("--min-age-days", dest="min_age_days", type=int, default=None,
                               help=f"Age threshold in days "
                                    f"(default {CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT})")

    # --- Transcript gzip-at-rest sweep: manual/testable entry point (#410) --
    p_sweep_transcripts = sub.add_parser(
        "sweep-transcripts",
        help="Gzip-at-rest old session transcripts under ~/.claude/projects/ "
             "-- NEVER deletes, top-level transcripts only (#410)")
    p_sweep_transcripts.add_argument("--dry-run", dest="dry_run", action="store_true",
                                     help="Report what would be compressed without touching anything")
    p_sweep_transcripts.add_argument("--min-age-days", dest="min_age_days", type=int, default=None,
                                     help=f"Age threshold in days "
                                          f"(default {TRANSCRIPT_COMPRESS_MIN_AGE_DAYS_DEFAULT})")
    p_sweep_transcripts.add_argument("--min-size-bytes", dest="min_size_bytes", type=int, default=None,
                                     help=f"Size floor in bytes "
                                          f"(default {TRANSCRIPT_COMPRESS_MIN_SIZE_BYTES_DEFAULT})")

    # --- File-Drop: share (give the user a clickable LAN URL) + filedrop (control)
    p_share = sub.add_parser(
        "share", help="Copy a file into the file-drop server and print its LAN URL")
    p_share.add_argument("path", help="Path to the file to serve to the user")

    p_filedrop = sub.add_parser("filedrop", help="File-drop service control")
    p_filedrop.add_argument("filedrop_action", nargs="?", default=None,
                            choices=["status"],
                            help="status (default when no flag)")
    p_filedrop.add_argument("--url", action="store_true",
                            help="Live-check the file-drop server and print its LAN base URL")
    p_filedrop.add_argument("--serve", action="store_true",
                            help="Run the file-drop HTTP server in the foreground (systemd ExecStart)")

    # --- Discord notify: @mention the tmux owner + the autopilot completion card
    p_notify = sub.add_parser(
        "notify", help="Send a Discord notification (@mentions the tmux owner)")
    p_notify.add_argument("--mention-prefix", dest="mention_prefix",
                          action="store_true",
                          help="Print just the '<@id> ' mention prefix for the current tmux owner")
    p_notify.add_argument("--channel-id", dest="channel_id", action="store_true",
                          help="Print the resolved per-owner Discord channel/thread id "
                               "(DISCORD_NOTIFICATION_CHANNEL_<OWNER>, else the shared id)")
    p_notify.add_argument("--owner", dest="owner", action="store_true",
                          help="Print the resolved tmux owner (so a caller can resolve "
                               "once and pass AIRULESET_NOTIFY_OWNER to keep mention+channel in sync)")
    p_notify.add_argument("--question-ping-off", dest="question_ping_off",
                          action="store_true",
                          help="#710: print '1' if the resolved owner has ❓ QUESTION "
                               "Discord delivery turned OFF (zbynek/marek — they take "
                               "questions in webterm + the footer 'U N'), else '0'. The "
                               "interactive send hook consults this once for the PRIMARY "
                               "owner to suppress an off owner's ❓ ping; david keeps "
                               "full delivery")
    p_notify.add_argument("--mirror-owners", dest="mirror_owners", action="store_true",
                          help="Print the space-separated parallel/CC recipients for the "
                               "current owner (DISCORD_MIRROR_<OWNER>) — the shell send path "
                               "posts a copy to each one's own thread + @mention")
    p_notify.add_argument("--kind", choices=["default", "questions"],
                          default="default",
                          help="With --channel-id (#296): 'questions' resolves the "
                               "owner's SEPARATE questions thread "
                               "(DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q) instead of "
                               "their normal thread. Default: 'default' (unchanged "
                               "pre-#296 behaviour)")
    p_notify.add_argument("--provision-question-thread",
                          dest="provision_question_thread", action="store_true",
                          help="One-time setup (#296): create (if missing) + persist "
                               "the owner's questions thread claude-<owner>-q into "
                               "the local .env; prints the thread id. Owner from "
                               "--owner-name or the resolved tmux owner")
    p_notify.add_argument("--provision-project-thread",
                          dest="provision_project_thread", action="store_true",
                          help="One-time setup (#369): create (if missing) + persist "
                               "the owner+project thread claude-<owner>-<project-slug> "
                               "into the local .env; prints the thread id. Owner from "
                               "--owner-name or the resolved tmux owner. Requires "
                               "--project (the project LABEL, e.g. from "
                               "--project-label)")
    p_notify.add_argument("--project-label", dest="project_label",
                          action="store_true",
                          help="Print the per-project routing/display LABEL for "
                               "--cwd (#369): the origin-derived repo name, "
                               "stream-qualified — the SAME label used to route "
                               "--channel-id --project and to name a project's "
                               "own Discord thread")
    p_notify.add_argument("--content-dedup-claim", dest="content_dedup_claim",
                          action="store_true",
                          help="#687: cross-session (cross-USER) content dedup "
                               "for the ✅ ping. Reads the ✅ TEXT from stdin, "
                               "uses --owner-name + --project; prints 'claim' "
                               "(first sender — deliver) or 'dup' (identical "
                               "payload already claimed in the window — "
                               "suppress). Always exits 0 (fail-open).")
    p_notify.add_argument("--find-only", dest="find_only", action="store_true",
                          help="With --provision-question-thread / "
                               "--provision-project-thread (#330/#369): only FIND "
                               "an existing thread, never CREATE one. The "
                               "AUTOMATIC background self-heal's own mode (never "
                               "wielded unattended) — the explicit, human-typed "
                               "CLI action stays find-then-CREATE unless this "
                               "flag is also given.")
    p_notify.add_argument("--autopilot-done", dest="autopilot_done",
                          action="store_true",
                          help="Compose + send the per-ticket completion card from fields")
    p_notify.add_argument("--run-card", dest="run_card", action="store_true",
                          help="Send a per-ticket card (requires --repo + --issue), "
                               "gathering goal/progress from gh — fired by the "
                               "autopilot worker directly at merge")
    p_notify.add_argument("--backfill-digest", dest="backfill_digest",
                          action="store_true",
                          help="ONE catch-up digest for --repo covering the "
                               "tickets closed since --since that never got a "
                               "delivered card (never one card per ticket). "
                               "Card markers are machine-local, so this MUST "
                               "run on the box holding the repo's checkout — "
                               "it refuses otherwise rather than reporting "
                               "another box's delivered cards as missing")
    p_notify.add_argument("--since", help="ISO8601 window start (--backfill-digest)")
    p_notify.add_argument("--force", action="store_true",
                          help="With --backfill-digest: run even though no "
                               "local checkout of --repo was found. The "
                               "check can miss a real checkout (a worktree, "
                               "an unusual location), and repairing a "
                               "reporting gap must never be impossible on "
                               "the box that holds it")
    p_notify.add_argument("--owner-name", dest="owner_name",
                          help="Deliver to this owner's thread. With "
                               "--backfill-digest: rarely needed, the owner "
                               "is read from the checkout's own live pane, "
                               "which is the only thing that actually knows "
                               "whose repo it is — a value contradicting the "
                               "pane is refused, not obeyed. With --body "
                               "(#334): pins the owner explicitly, bypassing "
                               "tmux auto-detection entirely — the caller's "
                               "choice is trusted unconditionally (no pane "
                               "to validate against), for a headless/foreign "
                               "caller whose notification is not about the "
                               "current tmux pane's own state at all")
    p_notify.add_argument("--repo-name", dest="repo_name", action="store_true",
                          help="Print the GitHub repo NAME for --cwd, from its "
                               "origin remote (never the directory basename)")
    p_notify.add_argument("--newest-card", dest="newest_card",
                          action="store_true",
                          help="Print the mtime of the newest DELIVERED "
                               "per-ticket card marker for --repo")
    # NOTE: `--cwd` already exists further down (--record-question) and is
    # reused by --repo-name; re-declaring it raises ArgumentError at import
    # and breaks EVERY `notify` subcommand.
    p_notify.add_argument("--api-error", dest="api_error", action="store_true",
                          help="RETIRED (#546): the api-error Discord ping class "
                               "is owner-suppressed at notify.send(); this now "
                               "posts nothing and prints 'suppressed'")
    p_notify.add_argument("--record-question", dest="record_question",
                          action="store_true",
                          help="Record a ❓ ping's Discord message id → the session "
                               "that asked (for Discord-reply routing); needs "
                               "--message-id --channel --session --cwd")
    p_notify.add_argument("--question-stdin", dest="question_stdin",
                          action="store_true",
                          help="With --record-question: read the posted ❓ text "
                               "from stdin (the send hook pipes it). Without "
                               "this flag stdin is NEVER touched — an "
                               "unconditional read blocked forever on an "
                               "inherited never-closing pipe")
    p_notify.add_argument("--suppressed", dest="suppressed",
                          action="store_true",
                          help="With --record-question (#716): record a "
                               "Discord-LESS 'suppressed' entry for a #710 "
                               "OFF owner (zbynek/marek) so the ticketless ❓ "
                               "still folds into the footer U N. No "
                               "--message-id/--channel needed (a synthetic "
                               "non-Discord key keyed on --session is used)")
    p_notify.add_argument("--edit-question", dest="edit_question",
                          action="store_true",
                          help="EDIT the session's recent ❓ ping in place with "
                               "the reworded question from stdin (edits don't "
                               "push-ping); rc 2 = nothing recent to edit")
    p_notify.add_argument("--message-id", dest="message_id",
                          help="Discord message id of the ❓ ping (--record-question)")
    p_notify.add_argument("--channel", help="Discord channel/thread id the ❓ ping "
                                            "was posted to (--record-question)")
    p_notify.add_argument("--cwd", help="Project cwd of the asking session (--record-question)")
    p_notify.add_argument("--text", help="The turn's last assistant message (API-error check)")
    p_notify.add_argument("--session", help="Session id (API-error dedup scope / --record-question)")
    p_notify.add_argument("--project",
                          help="Project LABEL (#369) — used for the API-error "
                               "ping's message text, and (with --channel-id or "
                               "--provision-project-thread) to route/provision "
                               "the owner's PER-PROJECT thread. Ignored by "
                               "--channel-id when --kind is 'questions' (the "
                               "questions thread stays centralized, by design)")
    p_notify.add_argument("--issue", type=int, help="Issue number (for --run-card)")
    p_notify.add_argument("--achieved", help="What landed (card 'Dosiahnuté') — plain language")
    p_notify.add_argument("--goal", help="Plain-language ticket goal (card 'Cieľ') — "
                                         "simple/understandable, NOT the technical issue title")
    p_notify.add_argument("--body", help="Arbitrary markdown body to send")
    p_notify.add_argument("--repo", help="owner/name (autopilot card)")
    p_notify.add_argument("--pr", help="PR URL → clickable 'kód (PR)' link on the card")
    p_notify.add_argument("--url", action="append",
                          help="'Where to see it live' link for the card — a bare URL "
                               "or 'Label=URL' (e.g. 'Prod=https://…'); repeatable")
    p_notify.add_argument("--merge-sha", dest="merge_sha", help="Merge commit SHA")
    p_notify.add_argument("--version", help="Deployed version read from the DOM")
    p_notify.add_argument("--handoff", action="store_true",
                          help="Fork-no-merge card: fired at the READY-FOR-REVIEW "
                               "hand-off (locally verified, waiting for gatekeeper "
                               "merge) — no merge/version, shows a 🔎 review status")
    p_notify.add_argument("--review", choices=["ok", "fail"], default="ok",
                          help="Double-review verdict (default ok)")
    p_notify.add_argument("--done", help="Tickets completed so far this run")
    p_notify.add_argument("--remaining", help="Open non-skip issues still to do")
    p_notify.add_argument("--tickets-json", dest="tickets_json",
                          help='JSON: [{"n":41,"title":..,"goal":..,"achieved":..}]')
    p_notify.add_argument("--dedup-key", dest="dedup_key",
                          help="Dedup key (default repo#pr) — same key sends once")
    p_notify.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="Print the composed message instead of sending")

    p_watchdog = sub.add_parser(
        "watchdog", help="Detect Claude Code sessions stalled on an API error and "
                         "auto-resume them (tmux `continue`) — run by a systemd timer")
    p_watchdog.add_argument("--once", action="store_true",
                            help="Run one poll cycle and exit (the systemd-timer mode)")
    p_watchdog.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="Detect + log, but do NOT send `continue` or ping")
    p_watchdog.add_argument("--verbose", action="store_true",
                            help="Print the actions taken this cycle")

    p_creq = sub.add_parser(
        "compact-request",
        help="Record a /compact request for a session at a safe ticket "
             "boundary (#39 krok 1c, collapsed by #402) — consumed by "
             "watchdog job 14 (watchdog.compact.compact_sweep)")
    p_creq.add_argument("--record", action="store_true",
                        help="Record the request (fired by "
                             "stop-check-prose-violations.sh; #610 retired the subagent-stop producer)")
    p_creq.add_argument("--session", default="", help="Session id (transcript stem)")
    p_creq.add_argument("--cwd", default="", help="Session cwd")
    p_creq.add_argument("--origin", default="",
                        help="What PROVED this is a ticket boundary. "
                             "'self-callback' = the session's own Work-Complete "
                             "boundary (sole production origin post-#610; the "
                             "subagent-stop producer hook was retired). Required "
                             "for --record; --self supplies 'self-callback'.")
    p_creq.add_argument("--self", action="store_true",
                        help="#225 -- explicit self-callback: resolve THIS "
                             "session's own pane via $TMUX_PANE, record the "
                             "request under the self-callback proven-boundary "
                             "origin, and attempt ONE immediate synchronous "
                             "/compact delivery (#402 -- no retry/hold loop "
                             "any more; an attempt that isn't safe right now "
                             "is simply left for the periodic sweep). "
                             "Ignores --record/--session/--cwd/--origin -- "
                             "everything is resolved from the calling pane "
                             "itself. Call this as your OWN last tool call "
                             "right after finishing a ticket, before "
                             "dispatching anything else.")
    p_creq.add_argument("--status", action="store_true",
                        help="#741 read-only HOLD probe: resolve THIS session "
                             "(via $TMUX_PANE, or --session <sid>) and print one "
                             "line -- `PENDING sid=<sid> age=<n>s` or `NONE` -- "
                             "then exit 0. The hold-turn doctrine's first action: "
                             "PENDING => end the turn `⏳ WORKING` with ZERO "
                             "dispatches; NONE => the boundary compact is done. "
                             "Records + types nothing.")

    p_garm = sub.add_parser(
        "goal-arm",
        help="Record + attempt one /goal arm for a session (#403) -- the "
             "/autopilot skill's own Step 2 callback, consumed by watchdog "
             "job 9 (watchdog.goal.goal_sweep)")
    p_garm.add_argument("--self", action="store_true",
                        help="Resolve THIS session's own pane via "
                             "$TMUX_PANE, resolve the authority profile "
                             "and its shipped /goal template, record the "
                             "request, and attempt ONE immediate "
                             "synchronous delivery. The ONLY supported "
                             "mode -- goal-arming has exactly one proven "
                             "origin, unlike compact's two.")
    p_garm.add_argument("--template", default="",
                        help="Authority profile to arm (full / "
                             "branch-merge / fork-no-merge). Defaults to "
                             "resolve_authority(cwd) -- the same "
                             "resolution the /autopilot skill's own "
                             "printed line already used, so they can "
                             "never disagree.")

    p_tickets = sub.add_parser(
        "tickets-status",
        help="Statusline github-tickets segment — autopilot done/total or open issues")
    p_tickets.add_argument("--cwd", help="Session cwd (defaults to $PWD)")
    p_tickets.add_argument("--refresh", action="store_true",
                           help="Slow path: refresh the per-repo cache via git+gh "
                                "(run detached by the statusline, never inline)")

    p_gkr = sub.add_parser(
        "gk-request",
        help="Stream→supervisor action request (#30): file/mark a "
             "needs-gatekeeper ticket the watchdog delivers to the supervisor "
             "(no user middleman)")
    p_gkr.add_argument("--repo", help="owner/name (default: current repo)")
    p_gkr.add_argument("--issue", type=int,
                       help="Mark an EXISTING issue instead of creating one")
    p_gkr.add_argument("--title", help="New ticket title (create mode)")
    p_gkr.add_argument("--body", help="New ticket body text")
    p_gkr.add_argument("--body-file", dest="body_file",
                       help="New ticket body from a file (backtick-safe)")
    p_gkr.add_argument("--comment",
                       help="Request text for --issue mode (Slovak, plain)")

    p_gate = sub.add_parser(
        "fable-gate", help="Budget gate for the automatic Fable judgment layer — exit "
                           "0 (OPEN, dispatch fable) / 1 (CLOSED, run on claude-opus-4-8)")
    p_gate.add_argument("--threshold", type=int, default=None,
                        help="Gate percent (default 80 / AIRULESET_FABLE_GATE_PCT)")

    p_wacc = sub.add_parser(
        "webterm-access",
        help="#612: reconcile the Cloudflare Access email-OTP app(s) in front of "
             "the public webterm hostname(s) from the declared allow-list")
    p_wacc.add_argument("--apply", action="store_true",
                        help="perform the create/update (default is dry-run: reads "
                             "only, prints the plan, changes nothing)")
    p_wacc.add_argument("--dry-run", action="store_true",
                        help="explicit no-op flag (dry-run is already the default "
                             "without --apply); accepted for clarity")
    p_wacc.add_argument("--profile", default=None,
                        help="limit to one profile (default: every declared profile)")

    p_dg = sub.add_parser(
        "drop-gateway",
        help="#664: reconcile THIS box's public-TLS drop lane — add a drop-host "
             "ingress to its existing cloudflared tunnel so secret/upload can "
             "print a simple public HTTPS URL (no tailscale, no ssh -L)")
    p_dg.add_argument("--apply", action="store_true",
                      help="perform the augment + Access reconcile + tunnel restart "
                           "+ marker write (default is dry-run: reads only, prints "
                           "the plan, changes nothing)")
    p_dg.add_argument("--dry-run", action="store_true",
                      help="explicit no-op flag (dry-run is already the default "
                           "without --apply); accepted for clarity")

    p_burn = sub.add_parser(
        "burn",
        help="Token-spend report from local Claude Code transcripts — "
             "by model / day / project")
    p_burn.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default 7)")
    p_burn.add_argument("--json", action="store_true",
                        help="Print the raw aggregated JSON instead of a table")
    p_burn.add_argument("--host", default=None,
                        help="Also collect a remote box by REMOTE_HOSTS name, "
                             "or 'all' for every managed remote (over ssh)")
    p_burn.add_argument("--mark", default=None,
                        help="Record a change NOW to burn-history/changes.jsonl "
                             "(the automatic --compare feedback loop, #37)")
    p_burn.add_argument("--mark-ts", dest="mark_ts", default=None,
                        help="Backdate --mark to this ISO8601 timestamp "
                             "(e.g. a git commit's --format=%%cI)")
    p_burn.add_argument("--compare", action="store_true",
                        help="Print mean $/h, avg context, msgs/h before vs "
                             "after each --mark'd change (--window hours each side)")
    p_burn.add_argument("--window", type=int, default=None,
                        help="--compare lookback/lookahead window in hours (default 6)")
    p_burn.add_argument("--fleet", action="store_true",
                        help="Print the monitored-fleet hourly report (#55) — "
                             "per-host + total $, trend, sustainability verdict")
    p_burn.add_argument("--hours", type=int, default=None,
                        help="--fleet lookback window in hours (default 24)")

    p_del = sub.add_parser(
        "delegation",
        help="MAIN vs SUBAGENT cost meter — per box, per project: turns, "
             "token sums, weighted units, mean context per turn")
    p_del.add_argument("--hours", type=int, default=12,
                       help="Lookback window in hours (default 12)")
    p_del.add_argument("--json", action="store_true",
                       help="Print the raw merged JSON instead of a table")
    p_del.add_argument("--host", default=None,
                       help="Also collect a remote box by REMOTE_HOSTS name, "
                            "or 'all' for every managed remote (read-only, over ssh)")
    p_del.add_argument("--tickets", action="store_true",
                       help="Join closed-issue counts per repo and report cost "
                            "per completed ticket (needs gh; opt-in)")
    p_del.add_argument("--root", default=None,
                       help="Transcript store to scan (default ~/.claude/projects)")
    p_del.add_argument("--floor", action="store_true",
                       help="Per-DISPATCH report (#131): the fixed floor a "
                            "dispatch starts with vs the growth it "
                            "accumulates, as distributions, plus turns per "
                            "dispatch and a per-subagent_type breakdown")

    p_up = sub.add_parser(
        "upload",
        help="Web upload URL for receiving a file FROM the user (never ask for scp)")
    p_up.add_argument("--dir", default=None, help="Destination dir (default ~/uploads)")
    p_up.add_argument("--ttl", type=int, default=7200,
                      help="Endpoint self-shutdown after N seconds (default 7200)")
    p_up.add_argument("--port", type=int, default=None,
                      help="Port (default: first free in 8799-8819)")
    p_up.add_argument("--public", action="store_true",
                      help="#664: force the public-TLS drop URL (loopback fronted "
                           "by the box's cloudflared tunnel) — auto-used anyway on "
                           "a box with no tailscale; needs `drop-gateway --apply` first")

    p_sec = sub.add_parser(
        "secret",
        help="Ask the user for a CREDENTIAL through a one-shot URL — never in "
             "chat (a value typed into chat is in the transcript forever)")
    p_sec.add_argument("action", choices=list(SECRET_ACTIONS),
                       help="request NAME [NAME2 ...] (stand up the URL — several "
                            "names share ONE page with a field each, #603) | "
                            "status | list | "
                            "exec NAME -- CMD (hand the value to a child; a "
                            "name LOCKED to a template ignores CMD and runs "
                            "its own command instead, #154) | "
                            "forget NAME | purge (drop everything past its TTL) | "
                            "show NAME|--file PATH (render a value the box holds "
                            "to YOUR browser ONCE, then tear down — #580)")
    p_sec.add_argument("name", nargs="?", default=None,
                       help="Secret name: letters/digits/underscore, also used "
                            "as the env var name for `exec`. `request` also "
                            "takes MORE names after the first (one URL, a field "
                            "per name, one atomic submit — #603)")
    p_sec.add_argument("--ttl", type=int, default=None,
                       help="Endpoint self-shutdown after N seconds "
                            "(default 600 — minutes, not hours)")
    p_sec.add_argument("--keep", type=int, default=None,
                       help="Delete the stored value after N seconds "
                            "(default 28800); `forget` removes it sooner")
    p_sec.add_argument("--port", type=int, default=None,
                       help="Port (default: first free in 8830-8849)")
    p_sec.add_argument("--env", default=None,
                       help="exec: environment variable to set (default: NAME)")
    p_sec.add_argument("--allow-plain", action="store_true",
                       help="request: also offer UNENCRYPTED LAN URLs (a "
                            "credential would cross the network in cleartext)")
    p_sec.add_argument("--public", action="store_true",
                       help="#664: request/show over the public-TLS drop URL "
                            "(loopback fronted by the box's cloudflared tunnel) — "
                            "auto-used on a no-tailscale box; go-live: `drop-gateway`")
    p_sec.add_argument("--replace", action="store_true",
                       help="request: cancel an existing pending request for "
                            "this name (stopping its endpoint) and issue a new URL")
    p_sec.add_argument("--stdin", action="store_true",
                       help="exec: feed the value on the child's stdin instead "
                            "of through the environment")
    p_sec.add_argument("--persist", default=None,
                       help="request/exec: DURABLE opt-in — a mode-600 file "
                            "(e.g. ~/.secrets/<name>) written at paste (request) "
                            "or self-healed on use (exec), so the credential "
                            "survives the vault's <=24h TTL (#529). For a "
                            "MULTI-name request use --persist-map instead")
    p_sec.add_argument("--persist-map", default=None,
                       help="request: durable targets for a MULTI-name request — "
                            "NAME1=path1,NAME2=path2 (each a mode-600 file like "
                            "~/.secrets/<name>). Names not listed stay one-shot; "
                            "mutually exclusive with --persist (#603)")
    p_sec.add_argument("--file", default=None,
                       help="show: render a mode-600 file (e.g. ~/.secrets/"
                            "<name>) instead of a vault NAME — the file must be "
                            "owner-only and outside any git repo (#580)")
    p_sec.add_argument("cmd", nargs=argparse.REMAINDER,
                       help="exec: the command to run, after `--`")

    p_auth = sub.add_parser(
        "authority",
        help="Print this stream's autopilot authority profile "
             "(full / branch-merge / fork-no-merge)")
    p_auth.add_argument("--explain", action="store_true",
                        help="Also print how the profile was resolved")
    p_auth.add_argument("--maintainer-login", action="store_true",
                        help="Print MAINTAINER_GH_LOGIN instead of the profile "
                             "(#349: lets a shared-gh-identity reduced-authority "
                             "stream's own hook tell a genuine self-authored "
                             "sub-finding apart from the maintainer-authored "
                             "assigned work every such stream shares an "
                             "identity with)")
    p_auth.add_argument("--self-login", action="store_true",
                        help="Print THIS box's own gh identity for the "
                             "self-authored-close carve-out in "
                             "block-fork-no-merge-issue-close.sh (#463): on a "
                             "GitHub App-token box `gh api user` 403s, so return "
                             "the fixed stream bot login (STREAM_APP_BOT_LOGIN) "
                             "without a network call; on every other box return "
                             "the real gh login. Prints nothing when the "
                             "identity cannot be resolved (the hook then refuses "
                             "the exemption / fails safe).")
    p_auth.add_argument("--stream-label", action="store_true",
                        help="Print THIS stream's ownership label "
                             "`stream:<unix-user>` for the acceptance-close "
                             "carve-out in block-fork-no-merge-issue-close.sh "
                             "(#533) — only on a REDUCED-authority box "
                             "(marker-aware); a full-authority box prints "
                             "nothing, so the hook's fail-safe refuses the "
                             "exemption.")

    p_slice = sub.add_parser(
        "slice-quals",
        help="THE single definition of a reduced-authority stream's own "
             "ticket slice (#181) — used by the /goal stop-proof templates")
    p_slice.add_argument("--count", action="store_true",
                         help="Print the slice's open non-skip issue count "
                              "(0 = slice empty)")
    p_slice.add_argument(
        "--list", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>title, oldest first")
    p_slice.add_argument(
        "--waiting", action="store_true",
        help="List the user-waiting remainder (needs-answer/needs-decision) "
             "parked on the user's answer — excluded from --count/--list (#468)")
    p_slice.add_argument(
        "--ops-wait", action="store_true",
        help="List the ops-wait remainder (ops-wait label) parked on an "
             "external event/evidence — excluded from --count/--list (#510)")
    p_slice.add_argument(
        "--audit", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>labels for each WORKABLE "
             "member (the --list set + a labels column) — the job-20 named "
             "partition-audit nudge reads this to name each I member (#578)")
    p_slice.add_argument("--extra", default=None,
                         help="Extra search qualifier ANDed onto every query "
                              "(e.g. label:prio:bounce)")

    p_core = sub.add_parser(
        "core-quals",
        help="A full-authority box's OBLIGATION set — the CORE slice plus "
             "every ticket only this box can action (#181) — used by the "
             "FULL /goal stop-proof template and its backlog listing")
    p_core.add_argument("--count", action="store_true",
                        help="Print the obligation set's open non-skip issue "
                             "count (0 = nothing left for this box to action)")
    p_core.add_argument(
        "--list", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>title, oldest first "
             "(action = action-only for a sub-dev stream's ticket, implement "
             "otherwise)")
    p_core.add_argument(
        "--waiting", action="store_true",
        help="List the user-waiting remainder (needs-answer/needs-decision) "
             "parked on the user's answer — excluded from --count/--list (#468)")
    p_core.add_argument(
        "--ops-wait", action="store_true",
        help="List the ops-wait remainder (ops-wait label) parked on an "
             "external event/evidence — excluded from --count/--list (#510)")
    p_core.add_argument(
        "--audit", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>labels for each WORKABLE "
             "obligation member (the --list set + a labels column) — the job-20 "
             "named partition-audit nudge reads this to name each I member (#578)")
    p_core.add_argument("--extra", default=None,
                        help="Extra search qualifier ANDed onto every query "
                             "(e.g. label:prio:bounce for the bounce seed)")

    p_lock = sub.add_parser(
        "autopilot-lock",
        help="Cross-session serial-per-repo dispatch lock for /autopilot")
    p_lock.add_argument("action", choices=["acquire", "release", "status"],
                        help="acquire (fails if a LIVE holder exists), "
                             "release (only removes a lock this caller owns), "
                             "or status (read-only report)")
    p_lock.add_argument("--repo", default=".", help="Repo path to lock (default: cwd)")
    p_lock.add_argument("--session", default="",
                        help="Free-text session id recorded for display only "
                             "(matching for release/steal is by pid, not this)")
    p_lock.add_argument("--pid", type=int, default=None,
                        help="Override the recorded/compared holder pid "
                             "(default: auto-detect the long-lived campaign process)")

    p_onboard = sub.add_parser(
        "onboard-project",
        help="Idempotent onboarding of a project under airuleset management "
             "(git/remote/branches/.gitignore/CLAUDE.md/foundation+notification "
             "tickets/registry) + --audit drift mode (#569)")
    p_onboard.add_argument("path", nargs="?",
                           help="Path to the project to onboard (or audit); "
                                "omit with --audit to sweep the whole registry")
    p_onboard.add_argument("--host", default=None,
                           help="Machine the project lives on (dev1 default; "
                                "a REMOTE_HOSTS name runs the steps over ssh)")
    p_onboard.add_argument("--name", default=None,
                           help="Explicit repo name (overrides deterministic "
                                "path derivation — e.g. a client-cluster prefix)")
    p_onboard.add_argument("--override", action="append", default=[],
                           help="Convention override tag, repeatable "
                                "(3-branch, merge=manual, local-builds=...)")
    p_onboard.add_argument("--audit", "--check", dest="audit",
                           action="store_true",
                           help="READ-ONLY: report drift from the canonical "
                                "checklist; no mutations, no auto-fixes")
    p_onboard.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="Report would-apply for every step; change nothing")
    p_onboard.add_argument("--registry", default=None,
                           help="Registry file path (default: the repo's "
                                "projects-registry.json)")

    p_goalinv = sub.add_parser(
        "goal-inventory",
        help="Inventory the /goal autopilot condition COMPOSED from goal_registry.py "
             "(#621) — per profile: which clauses it carries, the rendered length, "
             "and the remaining char budget; --check drift vs SKILL.md, --write "
             "re-renders the shipped /goal lines from the registry")
    p_goalinv.add_argument(
        "--profile", choices=["full", "branch-merge", "fork-no-merge"], default=None,
        help="Limit to one authority profile (also prints its per-clause breakdown)")
    p_goalinv.add_argument(
        "--check", action="store_true",
        help="Verify SKILL.md's /goal lines equal render(registry); exit 1 on drift")
    p_goalinv.add_argument(
        "--write", action="store_true",
        help="Re-render SKILL.md's /goal lines from the registry (regeneration)")
    p_goalinv.add_argument(
        "--json", action="store_true", help="Print the inventory as JSON")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    rc = commands[args.command](args)
    # Propagate a command's non-zero int return code to the process exit status
    # (#664 review: a failed `drop-gateway --apply` / `webterm-access` must not
    # exit 0, so a scripted go-live can see the failure). None / 0 → exit 0.
    if isinstance(rc, int) and rc != 0:
        sys.exit(rc)


def cmd_goal_inventory(args):
    """Mechanical answer to "which /goal solves what and what does it contain"
    (#621): reads the composed goal_registry.py, never SKILL.md's prose. --check
    and --write reconcile the shipped /goal lines with the registry."""
    import goal_registry as gr

    path = gr.skill_path()

    if args.write:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            print("goal-inventory: SKILL.md not found at %s" % path)
            sys.exit(1)
        new = gr.render_into(text)
        # render_into can only rewrite a block whose /goal line still starts
        # with `STOP CONDITIONS`; if any block is left drifted, it could NOT be
        # re-rendered (a corrupted prefix) — fail loudly instead of a false
        # "in sync" (which --check would still flag).
        residual = gr.drift(new)
        if residual:
            print("goal-inventory: could NOT re-render %d block(s) in %s — a "
                  "/goal line's `STOP CONDITIONS` prefix is corrupted: %s"
                  % (len(residual), gr.SKILL_REL,
                     ", ".join(p for p, _, _ in residual)))
            sys.exit(1)
        if new == text:
            print("goal-inventory: SKILL.md already in sync with the registry")
            return
        changed = [p for p, _, _ in gr.drift(text)]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("goal-inventory: re-rendered %d /goal line(s) in %s (%s)"
              % (len(changed), gr.SKILL_REL, ", ".join(changed)))
        return

    if args.check:
        try:
            with open(path, encoding="utf-8") as fh:
                d = gr.drift(fh.read())
        except FileNotFoundError:
            print("goal-inventory: SKILL.md not found at %s" % path)
            sys.exit(1)
        if d:
            print("goal-inventory: DRIFT — SKILL.md /goal lines differ from the "
                  "registry (run: airuleset.py goal-inventory --write):")
            for profile, _got, _exp in d:
                print("  %-14s shipped != render(registry)" % profile)
            sys.exit(1)
        print("goal-inventory: SKILL.md matches the registry (%d profiles)"
              % len(gr.PROFILES))
        return

    profiles = [args.profile] if args.profile else list(gr.PROFILES)

    if args.json:
        data = (gr.inventory(profiles[0]) if args.profile
                else [gr.inventory(p) for p in profiles])
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    for profile in profiles:
        inv = gr.inventory(profile)
        flag = "  ** OVER BUDGET **" if inv["over_budget"] else ""
        print("%-14s %d/%d chars  headroom %d  %d clauses%s"
              % (profile, inv["length"], inv["cap"], inv["headroom"],
                 inv["clause_count"], flag))
        if inv["missing_required"]:
            print("  MISSING REQUIRED CLAUSE(S): %s"
                  % ", ".join(inv["missing_required"]))
        if args.profile:
            for clause in inv["clauses"]:
                print("    %-22s %4d" % (clause["id"], clause["len"]))


# Command dispatch table (module-level so tests can assert registration).
SUBCOMMANDS = {
    "install": cmd_install,
    "diff": cmd_diff,
    "validate": cmd_validate,
    "status": cmd_status,
    "push": cmd_push,
    "purge-targets": cmd_purge_targets,
    "sweep-worktrees": cmd_sweep_worktrees,
    "sweep-lane-targets": cmd_purge_lane_targets,
    "sweep-cli-versions": cmd_sweep_cli_versions,
    "sweep-autopilot-locks": cmd_sweep_autopilot_locks,
    "sweep-claude-scratch": cmd_sweep_claude_scratch,
    "sweep-stray-tmp": cmd_sweep_stray_tmp,
    "sweep-transcripts": cmd_sweep_transcripts,
    "share": cmd_share,
    "filedrop": cmd_filedrop,
    "notify": cmd_notify,
    "watchdog": cmd_watchdog,
    "compact-request": cmd_compact_request,
    "goal-arm": cmd_goal_arm,
    "fable-gate": cmd_fable_gate,
    "webterm-access": cmd_webterm_access,
    "drop-gateway": cmd_drop_gateway,
    "burn": cmd_burn,
    "delegation": cmd_delegation,
    "authority": cmd_authority,
    "slice-quals": cmd_slice_quals,
    "core-quals": cmd_core_quals,
    "upload": cmd_upload,
    "secret": cmd_secret,
    "tickets-status": cmd_tickets_status,
    "gk-request": cmd_gk_request,
    "autopilot-lock": cmd_autopilot_lock,
    "onboard-project": cmd_onboard_project,
    "goal-inventory": cmd_goal_inventory,
}
# Backwards-compatible alias used by main() before SUBCOMMANDS existed.
commands = SUBCOMMANDS


if __name__ == "__main__":
    main()
