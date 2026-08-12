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
import difflib
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

# Managed default effort: `high` is the persistent default the user wants in
# EVERY managed project so they never have to remember to set it (#56,
# 2026-07-25). Official Anthropic docs for the Claude 5 family (Opus 5, Fable
# 5) both say "start with `high`, the default" and explicitly warn against
# reusing an effort setting carried over from an earlier model — this was
# previously `xhigh`, set in the Opus 4.7/4.8 era ("start with xhigh for
# coding and agentic use cases"), exactly the carried-over case the docs now
# flag. `xhigh` stays reserved for demanding coding/agentic work (dispatched
# via `effort:` on a specific agent/task, e.g. the autopilot-worker, or a
# gated HARD-task escalation) — never this blanket MAIN-session default.
# `max`/`ultracode` are session-only (not valid here) — ultracode adds
# auto-workflow orchestration on top and stays a per-session `/effort
# ultracode`. The user can still raise/lower per session with `/effort`.
MANAGED_EFFORT_LEVEL = "high"

# Managed default MAIN-session model (2026-07-25 cost-fix package, #37):
# **Opus 5** is now the default main + judgment tier (model-awareness.md) —
# Opus 4.8's regression + Sonnet 5's coordinator gap that made Fable-as-main
# a deliberate WORKAROUND are both gone now that Opus 5 shipped, so managed
# boxes should default MAIN to Opus 5 instead of whatever a prior session
# left in settings.json. Measured over 8 days across the 6 managed boxes:
# Fable 5 accounted for 76% of all token spend ($10,350 of ~$13,600) —
# gatekeeper $2,115, dev2 $2,027, montalu $1,392 — largely automated streams
# still defaulting to Fable as MAIN rather than the gated advisor shape.
# The `[1m]` suffix is a DELIBERATE part of the id, not a typo: it is how
# Claude Code's own usage tracking keys the 1M-context variant (verified —
# `lastModelUsage` entries in ~/.claude.json store ids exactly like
# `claude-opus-4-8[1m]`, distinct from the bare `claude-opus-4-8` key) — kept
# so this change does NOT also shrink the context window. The user relies on
# the 1M window to avoid context-loss regressions; whether to reconsider that
# is a SEPARATE decision for a later step, not bundled into this one.
MANAGED_MODEL = "claude-opus-5[1m]"

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
# File-Drop integration — serve user files as clickable LAN URLs
# ---------------------------------------------------------------------------
# The file-drop service runs on EVERY machine — each serves the files produced on
# THAT machine, bound to THAT machine's own LAN IP (discovered at runtime by
# filedrop.host_ip()).
try:
    from filedrop import (PORT as FILEDROP_PORT, DEFAULT_PORT as FILEDROP_DEFAULT_PORT,
                          PORT_FILE as FILEDROP_PORT_FILE, persisted_port as filedrop_persisted_port,
                          host_ip as filedrop_host_ip, bind_ips as filedrop_bind_ips,
                          filedrop_url, FILEDROP_DIR)
except Exception:  # pragma: no cover — filedrop package should always import
    FILEDROP_PORT = int(os.environ.get("FILEDROP_PORT", "8788"))
    FILEDROP_DEFAULT_PORT = 8788
    FILEDROP_PORT_FILE = CLAUDE_DIR / "filedrop.port"
    FILEDROP_DIR = CLAUDE_DIR / "filedrop"

    def filedrop_persisted_port():
        return None

    def filedrop_host_ip():
        return os.environ.get("FILEDROP_HOST", "127.0.0.1")

    def filedrop_bind_ips():
        return [filedrop_host_ip()]

    def filedrop_url():
        return f"http://{filedrop_host_ip()}:{FILEDROP_PORT}/"

FILEDROP_SERVICE_TEMPLATE = REPO_DIR / "settings" / "filedrop.service.template"
FILEDROP_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "filedrop.service"


# Skills directories in the repo that should be symlinked
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check", "autopilot", "autopilot-dialog", "mutation-sweep", "meeting-analysis", "playbook-review", "playbook-cleanup", "mutation-testing", "local-builds", "batch-issue-development", "view-image-urls", "version-on-dashboard", "process-subdev", "autopilot-master", "fable-advisor",
               # Ruleset trim wave 2 (#37, 2026-07-25) — situational always-on
               # modules moved VERBATIM to hidden (user-invocable: false)
               # on-demand skills. See test_ruleset_conversion_wave2.py.
               "subagent-type-discipline", "verify-issue-still-valid", "investigate-existing-first",
               "post-deploy-verification", "regression-test-first", "ci-push-discipline",
               "comprehensive-logging", "verify-launched-work-liveness", "pr-merge-policy",
               "deliver-files-as-urls", "notification-mechanics",
               # #95 item 9 (2026-08-09) — the STREDNÁ CESTA split of
               # user-questions-slovak.md: long template/examples moved
               # VERBATIM to this hidden, on-demand skill, auto-loaded via
               # the AskUserQuestion PreToolUse matcher for every stream
               # (every box asks questions, so this deploys everywhere,
               # like its "Ruleset trim wave 2" siblings above).
               "user-questions-slovak"]

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
    "montalu": {"meeting-analysis"},
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


def skill_names_for_user(user=None):
    """The skill set THIS box's user should have installed (see scoping above)."""
    import getpass
    user = user or getpass.getuser()
    extra = SKILLS_EXTRA_BY_USER.get(user, set())
    names = list(SKILL_NAMES)
    if user not in MAINTAINER_USERS:
        names = [n for n in names if n not in SKILLS_MAINTAINER_ONLY or n in extra]
    if AUTHORITY_BY_USER.get(user, "full") != "full":
        names = [n for n in names if n not in SKILLS_FULL_AUTHORITY_ONLY or n in extra]
    return names

# ---------------------------------------------------------------------------
# Caveman plugin — managed wiring (kept correct on every host by `install`)
# ---------------------------------------------------------------------------
# Caveman (JuliusBrussee/caveman) is a third-party Claude Code plugin the user
# relies on for compressed output. airuleset does NOT own its code, but DOES own
# keeping it wired correctly on every machine — it kept HALF-installing / breaking
# (plugin not enabled in enabledPlugins; statusLine pointing at a stale cache
# hash). The recurring breakage is the cache hash: the plugin's real statusline
# script lives under a content-hashed dir
# (~/.claude/plugins/cache/caveman/caveman/<hash>/hooks/caveman-statusline.sh)
# that CHANGES on every `claude plugin update`, so any hard-coded hash in
# settings.json rots and the statusline silently dies. Fix: ship a STABLE shim at
# a fixed path that resolves the current hash at RUNTIME, and point settings.json
# statusLine -> shim. `install` then reconciles enable + marketplace + statusLine
# on every push, self-healing both machines (the user asked to "put it into
# maintenance"). See modules/core/machine-identities.md sibling docs + memory.
CAVEMAN_MARKETPLACE_REPO = "JuliusBrussee/caveman"
CAVEMAN_PLUGIN_KEY = "caveman@caveman"
CAVEMAN_SHIM_DEST = CLAUDE_DIR / "airuleset-caveman-statusline.sh"
CAVEMAN_MODE_FILE = CLAUDE_DIR / ".caveman-active"
CAVEMAN_DEFAULT_MODE = "lite"
VALID_CAVEMAN_MODES = {
    "lite", "full", "ultra",
    "wenyan-lite", "wenyan-full", "wenyan-ultra",
}
# Managed BASELINE plugins — every managed user's Claude must have these. The
# airuleset rules invoke their skills DIRECTLY (superpowers:brainstorming,
# writing-plans, subagent-driven-development, requesting-code-review are baked
# into the workflow + completion-report gates), so a user without them has
# commands like /brainstorming simply missing and gated audits reference
# nonexistent skills (david@gk, 2026-07-09). All from the "official"
# claude-plugins-official marketplace — NOT actually built into the CLI
# (issue: push: plugin installs fail on fresh stream accounts, 2026-08-06):
# it must be REGISTERED (`claude plugin marketplace add`, see
# MARKETPLACE_SOURCES / ensure_marketplace_registered() below) before any
# `claude plugin install X@claude-plugins-official` can resolve — confirmed
# empirically in an isolated scratch profile, and confirmed to be missing
# entirely on a fresh account (montalu2/montalu3/montalu4, #263) whose whole
# lifecycle is this headless install flow. A long-lived interactively-used
# account self-heals this via Claude Code's own internal
# officialMarketplaceAutoInstall* routine (visible in ~/.claude.json) or a
# manual `marketplace add` run long ago — neither ever fires headlessly.
#
# Playwright (#158, 2026-08-06): the ruleset MANDATES a real browser for
# verification (autonomous-verification.md's "ask the user to install
# plugin:playwright" branch, e2e-real-user-testing.md, post-deploy-
# verification / version-on-dashboard skills) but the plugin was only ever
# installed BY HAND, per account. Measured live across the whole fleet
# (adversarial review of the first version of this fix, 2026-08-06):
# dev1/dev2/gatekeeper/marek/montalu/simap already had it enabled by hand —
# david, montalu2, montalu3 and montalu4 did NOT (four accounts missing it,
# not just david). THE CONTEXT-COST DECISION: baseline-installed AND ENABLED
# everywhere, not project-scoped. Reasoning: (a) it was ALREADY the fleet's
# de facto norm on 6 of 10 accounts, (b) the rules require it as MANDATORY
# verification tooling on every project, not a subset, (c) `superpowers`
# already set the "baseline plugin, always enabled" precedent this repo
# already lives with, (d) true per-project scoping would need NEW machinery
# (project-level plugin overrides) out of this ticket's scope and against
# the standing FREEZE on inventing new supervision mechanisms. The actual
# context cost is smaller than earlier assumed: Claude Code DEFERS an MCP
# plugin's tool SCHEMAS (names only in the prompt, schemas fetched on
# demand) — skills/mdreview/SKILL.md's "expensive" note is about the tool
# LIST, not a full-schema injection every turn. Known accepted gap (like
# superpowers before it): there is no per-user opt-out for a baseline
# plugin — every install/push re-enables it, so an account that
# deliberately wants Playwright OFF (e.g. a pure backend-only stream) would
# need `MANAGED_DISABLED_PLUGINS` used deliberately against the baseline,
# which today's reconcile forbids by design (see the sanity check below).
#
# BENIGN, DOCUMENTED (#279, 2026-08-06): `claude plugin list` can show
# playwright's Version as the literal "unknown" instead of a git commit
# hash. Live-verified: montalu3 shows a hash (`da7dc3b5ac48`), montalu4
# shows "unknown" -- and a same-day adversarial review found dev1 ALSO
# shows "unknown", so this is not montalu4-specific; expect it on any
# account whose marketplace checkout lacks `.git` (case 3 below). The
# version-source hierarchy, confirmed by
# reading real plugin.json files + registry entries: (1) if the plugin's
# own `.claude-plugin/plugin.json` declares a `version` field, that string
# is used verbatim (e.g. discord@claude-plugins-official -> "0.0.4");
# (2) else, if the marketplace CHECKOUT the plugin was read from is a real
# git clone, a git-derived commit sha is used (playwright has NO `version`
# field in its own plugin.json on EITHER montalu3 or montalu4, yet montalu3
# still shows a hash -- because montalu3's `claude-plugins-official`
# checkout is a real git clone, confirmed via a live `.git/` with
# objects/refs); (3) else "unknown" (no declared version, no git info --
# montalu4's and dev1's `claude-plugins-official` checkouts have NO `.git`
# at all; both carry a `.gcs-sha` marker file instead, evidence of a
# GCS-blob delivery). That checkout materializes via TWO different Claude
# Code code paths -- this repo's own explicit `claude plugin marketplace
# add` (ensure_marketplace_registered(), a real `git clone`) OR Claude
# Code's OWN internal `officialMarketplaceAutoInstallAttempted`/
# `officialMarketplaceAutoInstalled` self-heal (both `true` in the affected
# accounts' own ~/.claude.json). Which path wins on a given account is a
# Claude Code internal race outside airuleset's control. Confirmed
# functionally IDENTICAL either way: montalu4's and montalu3's playwright
# `.mcp.json` and `.claude-plugin/plugin.json` are byte-for-byte identical,
# and `_managed_plugin_built("playwright@claude-plugins-official")` (the
# registry-truth check, #276) already correctly reports it installed on
# montalu4 regardless of the version string -- there is no install-loop
# defect here, only a cosmetic display label. Deliberately NOT "fixed" by
# forcing a re-`marketplace add`: `ensure_marketplace_registered()` already
# exists (this would not be new supervision machinery) but per the
# standing FREEZE ("fix only what has actually failed in production") a
# cosmetic label with zero functional impact does not qualify; it also was
# not validated live, since doing so would require modifying a remote box,
# and Claude Code's own auto-install could simply race ahead again on the
# very next invocation regardless.
MANAGED_PLUGINS = ("superpowers@claude-plugins-official",
                    "playwright@claude-plugins-official")
# Plugins explicitly DISABLED by managed policy (#39 item 3, 2026-07-25
# /doctor findings): rust-analyzer-lsp + claude-md-management had 0 lifetime
# uses on dev2 and `/doctor` disabled them directly in settings.json
# (backup: settings.json.bak-doctor). The plugin reconcile below only ever
# ENABLES MANAGED_PLUGINS and otherwise merges the existing enabledPlugins
# dict untouched, so these disables already survive a normal push — this
# list makes the intent EXPLICIT and durable (and applies it on every box,
# not just dev2) so a future change to the reconcile logic can never
# silently resurrect them.
MANAGED_DISABLED_PLUGINS = (
    "rust-analyzer-lsp@claude-plugins-official",
    "claude-md-management@claude-plugins-official",
)
# Marketplace SOURCES for `claude plugin marketplace add` (issue: push:
# plugin installs fail on fresh stream accounts — marketplace not
# registered, 2026-08-06). A plugin's marketplace must be REGISTERED before
# `claude plugin install X@Y` can find it — writing extraKnownMarketplaces
# into settings.json alone is NOT enough (empirically verified in an
# isolated scratch CLAUDE_CONFIG_DIR, CC 2.1.223: with only that JSON key
# present, install still fails "not found in marketplace Y ... try `claude
# plugin marketplace update`"; only `claude plugin marketplace add
# <source>` — which clones the marketplace repo onto disk AND declares it
# in user settings itself — makes install succeed). A long-lived account
# (montalu, dev1/dev2) has this from an old interactive session or CC's own
# `officialMarketplaceAutoInstall*` self-heal (confirmed present in
# ~/.claude.json); a fresh stream account provisioned entirely headlessly
# (montalu2/3/4, #263) never gets either, so `~/.claude/plugins/` doesn't
# exist there at all. `claude plugin marketplace add` is idempotent
# (confirmed live: re-running on an already-materialized marketplace
# returns rc=0 "already on disk"), so it is safe to run unconditionally on
# every install/push. Values are the `owner/repo` shorthand `claude plugin
# marketplace add` accepts directly (confirmed live for both).
OFFICIAL_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"
MARKETPLACE_SOURCES = {
    "caveman": CAVEMAN_MARKETPLACE_REPO,
    "claude-plugins-official": OFFICIAL_MARKETPLACE_SOURCE,
}


def _marketplace_names_for(plugin_keys) -> set:
    """Derive the set of marketplace NAMES a collection of `plugin@marketplace`
    keys needs registered — from the keys themselves, so there is never a
    second, driftable list of marketplace names to keep in sync by hand."""
    return {key.split("@", 1)[1] for key in plugin_keys if "@" in key}


def ensure_marketplace_registered(name: str) -> bool:
    """Best-effort, idempotent `claude plugin marketplace add <source>` —
    MUST run before any `claude plugin install X@<name>` on a fresh account
    (see MARKETPLACE_SOURCES' docstring above for why writing
    extraKnownMarketplaces alone is not sufficient). Returns True iff the
    marketplace is known to be usable afterward (rc==0, or `name` is one
    this repo doesn't manage a source for — nothing to do). Loud on
    failure, never raises."""
    import subprocess
    source = MARKETPLACE_SOURCES.get(name)
    if source is None:
        return True
    try:
        r = subprocess.run(
            ["claude", "plugin", "marketplace", "add", source],
            capture_output=True, text=True, timeout=150,
            env=_claude_cli_env())
    except Exception as e:
        print(f"    could not register marketplace {name} ({e})", file=sys.stderr)
        return False
    if r.returncode == 0:
        return True
    print(f"    could not register marketplace {name} (rc={r.returncode}): "
          f"{(r.stderr or r.stdout).strip()[:200]}\n"
          f"    Run manually: claude plugin marketplace add {source}",
          file=sys.stderr)
    return False


# Hash-independent entry to caveman's statusline + the usage-limit/ticket/
# account meter line (the standalone context-fill BAR was dropped, #223 --
# the context size stays visible via the 'ctx <size> ~$<cost>' segment).
# Must NEVER error (a broken statusline would break the prompt render).
# Caveman's real script lives under a content-hashed cache dir that changes
# on every `claude plugin update`; `ls -dt ... | head -1` resolves the
# newest hash at runtime so the path can't rot. BOTH cache layouts are
# globbed below: pre-2026-07 releases shipped <hash>/hooks/…, newer ones
# ship <hash>/src/hooks/… (a fresh install produces ONLY the new layout —
# the migrated gatekeeper box surfaced it: an old single-glob check saw
# "not built" forever and re-installed the plugin on every run). This is
# the ONLY place these two paths live -- _caveman_plugin_built() (#279)
# decides "installed" from claude's own installed_plugins.json registry,
# never from a cache-file glob; a former CAVEMAN_CACHE_GLOBS constant
# duplicating these same two strings was removed as dead code once nothing
# read it any more. A custom statusLine occupies
# the whole footer row, so the native context-fill indicator is unreliable —
# Claude Code pipes the session JSON on stdin (context_window.used_percentage
# etc., CC v2.1.132+) and caveman's script reads only its flag file, so the
# shim consumes stdin and renders the meter line itself, right next to the
# badge. Must NOT `exec` caveman (it has to keep running to append the
# meter). Prints nothing it can't safely render.
CAVEMAN_SHIM_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — caveman badge + usage/ticket/account meter.
# caveman's real statusline lives under a content-hashed cache dir resolved at
# runtime (ls -dt ... | head -1) so a `claude plugin update` can never rot it.
in=$(cat)
real=$(ls -dt "$HOME"/.claude/plugins/cache/caveman/caveman/*/hooks/caveman-statusline.sh \
       "$HOME"/.claude/plugins/cache/caveman/caveman/*/src/hooks/caveman-statusline.sh 2>/dev/null | head -1)
badge=""
if [ -n "$real" ] && [ -f "$real" ]; then badge=$(bash "$real" </dev/null 2>/dev/null); fi
# de-emphasize caveman (least-important info): strip its bright color, lowercase,
# drop the brackets, render faint so it stops grabbing attention.
cm=""
if [ -n "$badge" ]; then
  plain=$(printf '%s' "$badge" | sed 's/\x1b\[[0-9;]*m//g' | tr 'A-Z' 'a-z')
  plain=${plain#[}; plain=${plain%]}
  [ -n "$plain" ] && cm=$(printf '\033[2m%s\033[0m' "$plain")
fi
meter=$(CTX_JSON="$in" CM_TAG="$cm" python3 2>/dev/null <<'PY'
import os, json, time
try:
    d = json.loads(os.environ.get("CTX_JSON") or "{}")
except Exception:
    raise SystemExit
if not isinstance(d, dict):
    raise SystemExit
segs = []
def colr(pct, lo, hi):  # green below lo, yellow below hi, red at/above hi
    return 40 if pct < lo else (220 if pct < hi else 196)
# --- usage limits (5h + weekly), high % = near the cap ---
# (#223 dropped the fill-percentage bar that used to render right here — the
# context size stays visible via the 'ctx <size> ~$<cost>' segment further
# down, composed by statusbar.context_cost_segment)
rl = d.get("rate_limits") or {}
now = time.time()
def reset(ts):
    # CC stdin gives an epoch int; the watchdog cache gives an ISO-8601 string.
    # No leading space (#223) -- callers glue this straight onto '<pct>%'.
    if not ts:
        return ""
    try:
        s = int(ts) - now
    except (ValueError, TypeError):
        try:
            from datetime import datetime
            s = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() - now
        except Exception:
            return ""
    if s <= 0:
        return ""
    if s >= 86400:
        return "(%dd)" % round(s / 86400.0)
    if s >= 3600:
        return "(%dh)" % round(s / 3600.0)
    return "(%dm)" % max(1, round(s / 60.0))
for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
    w = rl.get(key) or {}
    p = w.get("used_percentage")
    if p is None:
        continue
    p = max(0, min(100, int(p)))
    c = colr(p, 70, 90)
    segs.append("\033[38;5;%dm%s %s%%\033[0m\033[2m%s\033[0m" % (c, label, p, reset(w.get("resets_at"))))
# --- per-model usage window (Fable etc.) from the api-watchdog's oauth/usage cache.
# CC stdin `rate_limits` only carries the SHARED 5h + weekly; the per-model weekly
# (e.g. Fable's own limit — the binding one under max-performance) lives only in the
# oauth/usage limits[], which the watchdog polls every ~15 min and caches here. The
# 5h "session" window is account-wide (no per-model 5h exists). Never calls the API.
try:
    cc = json.load(open(os.path.expanduser("~/.claude/airuleset-usage-cache.json")))
except Exception:
    cc = None
if isinstance(cc, dict) and (now - (cc.get("ts") or 0)) < 6 * 3600:
    for w in cc.get("windows") or []:
        model = w.get("model")
        if not model:            # skip the shared windows (already shown above)
            continue
        p = w.get("percent")
        if p is None:
            continue
        p = max(0, min(100, int(p)))
        c = colr(p, 70, 90)
        # Label shortened to the model's first letter, uppercased (#223):
        # "Fable 23%" -> "F 23%".
        label = model[:1].upper()
        segs.append("\033[38;5;%dm%s %s%%\033[0m\033[2m%s\033[0m" % (c, label, p, reset(w.get("resets_at"))))
# --- github ticket progress: autopilot done/total, else open issues ---
# Composed from local caches by statusbar.tickets_segment (a stale cache spawns a
# DETACHED `airuleset.py tickets-status --refresh`; the render never waits on gh).
# {{REPO_DIR}} is substituted at install time by render_caveman_shim().
# `line` starts as just the segments gathered so far (rate limits + per-model
# usage) -- if the statusbar-dependent block below fails entirely (a broken
# {{REPO_DIR}} import, say), those still render instead of losing the WHOLE
# line, matching this shim's pre-existing "never let one segment's failure
# take down the others" contract.
line = "  ".join(segs)
try:
    import sys
    sys.path.insert(0, "{{REPO_DIR}}")
    import statusbar
    cwd = ((d.get("workspace") or {}).get("current_dir")) or d.get("cwd") or ""
    # --- which model this session runs: 'opus'/'sonnet'/'fable'/'haiku',
    # highlighted when it differs from this box's MANAGED_MODEL default
    # (#133 -- passive replacement for the #37 model-cost signal).
    # {{MANAGED_MODEL}} is baked in at RENDER time (adversarial-review
    # MINOR-1: a lazy `import airuleset` on every prompt render measured
    # ~12ms steady-state / ~88ms right after a `push` invalidates the
    # .pyc -- the SAME shape render_caveman_shim() already uses for
    # {{REPO_DIR}}, and the launch script for {{MANAGED_MODEL}} itself). ---
    mdl = statusbar.model_segment(d, managed_model="{{MANAGED_MODEL}}")
    if mdl:
        segs.append(mdl)
    seg = statusbar.tickets_segment(cwd)
    if seg:
        segs.append(seg)
    q = statusbar.questions_segment(cwd)   # unanswered-❓ badge (this project only, #313 pt 5)
    if q:
        segs.append(q)
    # --- session context/cost: 'ctx 570K ~$0.57' (2026-07-25, #37; shortened #223) ---
    cc_full = statusbar.context_cost_segment(d)
    cc_short = statusbar.context_cost_segment(d, show_cost=False) if cc_full else ""
    if cc_full:
        segs.append(cc_full)
    # --- account identity: email + monthly renewal, combined as ONE
    # trailing unit (#313 pt 6 -- 'sub' moves NEXT TO the email, single
    # space, email first: 'drlik.marek@gmail.com sub 12.8.(4d)' -- both are
    # properties of the SAME oauthAccount, so they belong together instead
    # of scattered across the line). ---
    acct = statusbar.account_email_segment()
    sub = statusbar.subscription_segment()
    identity = " ".join(p for p in (acct, sub) if p)
    # --- caveman's own (already faint-toned) tag, composed in bash above ---
    cm_tag = os.environ.get("CM_TAG") or ""
    # --- width budget (#313 pt 4): fit inside the pane MINUS a reserve for
    # Claude Code's own right-edge indicators (the armed-'/goal' glyph --
    # live evidence: a 176-col row fully consumed truncated it clean off,
    # twice misread as "the goal died"). Trims least-important segments
    # FIRST -- the account identity block, then the caveman tag, then just
    # the ctx segment's own '~$<cost>' suffix -- dynamically, before ever
    # overflowing. An unmeasurable pane width (no TMUX_PANE, tmux missing,
    # any failure) never trims -- a statusline segment must never guess. ---
    width = statusbar.pane_width()
    # adversarial review MINOR-3 (round 1: `width` measured as `0` must
    # count as MEASURED, `is not None` not truthiness) + round-2 THEORETICAL
    # follow-up: clamp the reserve subtraction at 0 -- an unclamped
    # `width - RESERVE` on a genuinely tiny/degenerate measured width would
    # otherwise go negative, which `fit_statusline` would then treat as
    # "trim everything, and the line still overflows anyway" rather than
    # the more honest "nothing fits, so just don't add the reserve on top."
    budget = max(0, width - statusbar.STATUSLINE_RESERVE_COLS) \
        if width is not None else None
    line = statusbar.fit_statusline(segs, identity, cm_tag, cc_full, cc_short, budget)
except Exception:
    pass
if not line:
    raise SystemExit
print(line)
PY
)
# adversarial review MAJOR-3 (round 1) + round-2 re-review: moving `cm`
# into the python block via CM_TAG dropped the bash-side "no meter at all
# -> at least show the caveman badge" fallback the shim always had -- an
# early `raise SystemExit` (malformed stdin, a broken {{REPO_DIR}} import,
# a missing python3) used to still degrade to just the badge. The FIRST
# fix here only restored it for a totally-empty `$meter`, which is
# unreachable for the REALISTIC failure the comment names: `line` is
# pre-seeded from the rate-limit segments BEFORE the `try:` block ever
# runs, so `meter` is already non-empty on almost every render (Claude
# Code sends `rate_limits` on essentially every prompt) even when the
# python block's LATER statusbar-dependent half throws -- the
# `[ -z "$meter" ]` guard then never fires and the badge is silently lost.
# Fixed to be ADDITIVE instead of exclusive: append `$cm` whenever it
# is not ALREADY part of `$meter` (the happy path, where python composed
# it itself), covering every early-exit shape regardless of whether
# anything else rendered first.
case "$meter" in
  *"$cm"*) ;;
  *) [ -n "$cm" ] && meter="${meter:+$meter  }$cm" ;;
esac
printf '%s' "$meter"
exit 0
"""
CAVEMAN_STATUSLINE_COMMAND = f'bash "{CAVEMAN_SHIM_DEST}"'


def render_caveman_shim():
    """The shim content with per-machine placeholders substituted ({{REPO_DIR}} →
    this checkout, so the embedded python can import statusbar for the 🎫 ticket
    segment; {{MANAGED_MODEL}} -> this box's managed model default, so the
    model-identity segment (#133) never pays a per-render `import airuleset`).
    The install write site MUST use this, never the raw constant."""
    return (CAVEMAN_SHIM_CONTENT
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{MANAGED_MODEL}}", MANAGED_MODEL))

# Subagent definitions (single .md files) symlinked into ~/.claude/agents/
AGENT_NAMES = ["autopilot-worker", "ticket-validator"]

HOOKS_JSON = REPO_DIR / "settings" / "hooks.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_profile(profile_path: Path) -> list[str]:
    """Parse a .profile file and return list of module/rule paths (relative to repo)."""
    if not profile_path.exists():
        print(f"ERROR: Profile not found: {profile_path}", file=sys.stderr)
        sys.exit(1)

    entries = []
    for line in profile_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@include "):
            included = line.split(None, 1)[1]
            included_path = profile_path.parent / included
            entries.extend(parse_profile(included_path))
        else:
            entries.append(line)
    return entries


def categorize_entries(entries: list[str]) -> tuple[list[str], list[str]]:
    """Split profile entries into modules (for @import) and rules (for symlinks)."""
    modules = []
    rules = []
    for e in entries:
        if e.startswith("rules/"):
            rules.append(e)
        else:
            modules.append(e)
    return modules, rules


def symlink_global_rules(rule_entries: list[str], claude_dir: Path,
                          repo_dir: Path) -> list[str]:
    """Symlink each `rules/<name>.md` profile entry into
    `claude_dir/rules/<name>.md` -- Claude Code's native "User"-scope
    path-scoped-rules directory (#40; see RULES_DIR). Mirrors the
    skill-symlink pattern in cmd_install: idempotent, backs up a pre-existing
    real file before replacing it with a symlink, and prunes an
    airuleset-owned rule symlink that is no longer referenced (never touches
    a foreign symlink pointing anywhere else). Takes explicit params (not the
    module-level CLAUDE_DIR/REPO_DIR globals) so it's directly unit-testable
    with a tempdir. Returns human-readable log lines for the caller to print.
    """
    lines = []
    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    wanted_names = set()
    for entry in rule_entries:
        name = Path(entry).name
        wanted_names.add(name)
        source = repo_dir / entry
        link = rules_dir / name

        if not source.exists():
            lines.append(f"  SKIP rule (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                lines.append(f"  OK rule:   {name}")
                continue
            link.unlink()
        elif link.exists():
            backup = link.with_suffix(".md.bak")
            shutil.move(str(link), str(backup))
            lines.append(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        lines.append(f"  Linked:    {link} -> {source}")

    # Prune airuleset-owned rule symlinks no longer referenced by the profile
    # (same ownership check as the skill-pruning step: only unlink a symlink
    # that points into OUR repo's rules/ dir -- a foreign/hand-made rule file
    # is never touched).
    for link in rules_dir.glob("*.md"):
        if link.name in wanted_names:
            continue
        if not link.is_symlink():
            continue
        try:
            target = Path(os.readlink(link))
        except OSError:
            continue
        if str(target).startswith(str(repo_dir / "rules")):
            link.unlink()
            lines.append(f"  Pruned:    {link.name} (not in universal profile)")

    return lines


def generate_claude_md(modules: list[str]) -> str:
    """Generate the content for ~/.claude/CLAUDE.md with @import lines."""
    lines = [
        "# User-Wide Claude Code Instructions",
        "",
        f"{MANAGED_MARKER}",
        f"{MANAGED_HEADER} — https://github.com/zbynekdrlik/airuleset",
        "# Do not edit this file manually. Run: python airuleset.py install",
        "",
    ]

    # Group modules by category for readability
    groups: dict[str, list[str]] = {}
    for mod in modules:
        # Extract category from path like modules/core/foo.md -> core
        parts = mod.split("/")
        if len(parts) >= 3:
            category = parts[1]
        else:
            category = "other"
        groups.setdefault(category, []).append(mod)

    category_titles = {
        "core": "Core Workflow",
        "git": "Git Discipline",
        "ci": "CI/CD Standards",
        "deploy": "Deployment",
        "quality": "Code Quality",
    }

    for category, mods in groups.items():
        title = category_titles.get(category, category.title())
        lines.append(f"## {title}")
        lines.append("")
        for mod in mods:
            lines.append(f"@~/devel/airuleset/{mod}")
        lines.append("")

    return "\n".join(lines)


def preserve_external_blocks(old_text: str, new_text: str) -> str:
    """Re-attach externally-managed, delimited blocks (e.g. CodeGraph's guidance)
    from the OLD CLAUDE.md onto freshly-generated NEW content, so regenerating from
    the profile never silently deletes another tool's block. Pure + idempotent
    (a block already present in new_text is not duplicated; absent markers = no-op)."""
    result = new_text
    for start, end in EXTERNAL_BLOCK_MARKERS:
        if start in result:
            continue  # already present — don't duplicate
        si = old_text.find(start)
        ei = old_text.find(end)
        if si == -1 or ei == -1 or ei < si:
            continue  # no intact block in the old file
        block = old_text[si:ei + len(end)]
        result = result.rstrip("\n") + "\n\n" + block + "\n"
    return result


def load_hooks_json() -> dict:
    """Load the hooks definition from settings/hooks.json."""
    if not HOOKS_JSON.exists():
        return {}
    return json.loads(HOOKS_JSON.read_text())


def merge_hooks_into_settings(hooks_config: dict, existing_settings: dict) -> dict:
    """Merge airuleset hooks into existing settings.json, preserving other keys.

    Strategy: remove all airuleset-managed hooks (identified by 'airuleset/hooks/' in command),
    then add all hooks from hooks.json. This ensures hooks.json is always the source of truth.
    """
    result = dict(existing_settings)

    if "hooks" not in hooks_config:
        return result

    if "hooks" not in result:
        result["hooks"] = {}

    for event_type, event_hooks in hooks_config["hooks"].items():
        if event_type not in result["hooks"]:
            result["hooks"][event_type] = []

        # Remove existing airuleset-managed hooks
        cleaned = []
        for entry in result["hooks"][event_type]:
            is_ours = False
            for hook in entry.get("hooks", []):
                if "airuleset/hooks/" in hook.get("command", ""):
                    is_ours = True
                    break
            if not is_ours:
                cleaned.append(entry)
        result["hooks"][event_type] = cleaned

        # Add all airuleset hooks from config (skip exact duplicates already present)
        for entry in event_hooks:
            if entry not in result["hooks"][event_type]:
                result["hooks"][event_type].append(entry)

    return result


BASHRC = Path.home() / ".bashrc"
ULTRACODE_MARK_START = "# >>> airuleset: ultracode default >>>"
ULTRACODE_MARK_END = "# <<< airuleset: ultracode default <<<"
# The managed claude launcher (#77, 2026-07-26): a shell FUNCTION in ~/.bashrc
# is parsed ONCE at shell startup and then stays frozen in that shell's memory
# FOREVER. Panel shells are long-lived (tmux panes running for days), so any
# logic baked directly into the .bashrc function (flags, model pin, ultracode)
# kept resurrecting on every relaunch of an ALREADY-RUNNING stale shell, no
# matter how many times `push` rewrote .bashrc -- rewriting the file has zero
# effect on a shell that already parsed the old function into memory. Measured
# live: two sessions launched HOURS after #53 (which correctly made ultracode
# opt-in ON DISK) still carried the pre-#53 default, because the panel shells
# hosting them predated the fix.
#
# Fix: .bashrc holds ONLY thin one-line wrapper functions with NO flag
# literals -- each just execs the managed SCRIPT (CLAUDE_LAUNCH_SCRIPT_DEST),
# which carries ALL the actual logic (continue-or-new, --model, skip-perms,
# ultracode only for the `ultracode` mode). A script is read fresh from disk
# on EVERY invocation, so a `push` that rewrites the script changes behavior
# in every already-running shell IMMEDIATELY -- no `source ~/.bashrc`, no
# relaunch, no restart. Same shape as the caveman stable statusline shim
# (render_caveman_shim() below) -- read that first before changing this.
CLAUDE_LAUNCH_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-launch.sh"
# --- the script content itself -----------------------------------------------
# Ultracode is OPT-IN (#53, 2026-07-25): `--settings '{"ultracode":true}'` used
# to be baked into EVERY default launch, so ultracode mode silently came back
# on every session restart even after the user had turned it off for that
# session (found repeatedly on restreamer). Only the `ultracode` mode carries
# it now -- the `claude-ultracode()` bashrc function is the explicit opt-in
# escape hatch, carrying EXACTLY today's old default behavior.
#   --settings '{"ultracode":true}' : ultracode is SESSION-ONLY (never on disk, NOT
#       accepted in settings.json — GH #64817); --settings is the only doc-blessed
#       always-on route and MERGES per-key, so hooks/model/effortLevel stay intact.
#       Only the `ultracode` mode passes this now.
#   --dangerously-skip-permissions  : auto-approve (the user opted in for their dev boxes).
#   -c                              : continue the most recent conversation in the cwd.
#   --model '{{MANAGED_MODEL}}'     : baked in at RENDER time so EVERY mode except
#       `plain` — including a RESUMED (-c) session — explicitly requests the managed
#       model. Proven live on gatekeeper: settings.json said `claude-opus-5[1m]`, but a
#       resumed session's transcript kept showing `claude-opus-4-8` on every turn — `-c`
#       alone just continues whatever model the prior transcript was started with; only
#       an explicit --model on the launch command line forces it.
# The conversation probe globs ~/.claude/projects/<encoded-cwd>/*.jsonl — Claude Code
# encodes cwd by turning / . _ into dashes; a project dir holding only memory/ (no
# transcript) means nothing to continue. Unknown encoding chars fail toward the
# FRESH branch (worse case: a new session instead of a cryptic error).
# Modes: `default` (claude — continue-or-new, skip-perms, model, NO ultracode),
# `new` (claude-new — always FRESH, skip-perms, model, NO ultracode — force a
# clean start), `ultracode` (claude-ultracode — deliberate opt-in: continue-or-new
# + skip-perms + ultracode + model), `plain` (claude-plain — vanilla, no flags),
# `fullscreen` (claude-fullscreen — deliberate opt-in: continue-or-new + skip-perms
# + model, PLUS CLAUDE_CODE_NO_FLICKER=1).
#   CLAUDE_CODE_NO_FLICKER=1 : #376 REVERSED the `apply_managed_settings_defaults`
#       pin from `"tui": "default"` (classic) to `"tui": "fullscreen"` fleet-wide
#       (see that function's own docstring for the full history/tradeoff/citation)
#       -- so this launcher mode's env var is now REDUNDANT with the fleet default,
#       not an opt-in override away from it. Kept, harmless: it is an explicit way
#       to force fullscreen on a box whose LOCAL settings.json has drifted from the
#       managed pin (a manual `/tui default` switch, a pre-#376 install not yet
#       pushed), and it still fixes the SAME proven upstream Claude Code renderer
#       defect the mode was originally built to bypass (#253 --
#       anthropics/claude-code#84247 / #46834, both still open 2026-08-11: a
#       SIGWINCH/relayout re-emits a fresh copy of the transcript into the
#       terminal's PRIMARY scrollback, corrupting it with duplicate/interleaved
#       frames; reproduced live -- a real 25-line completion-report chunk found
#       duplicated verbatim in tmux pane history on dev1's own 3.7b tmux, the SAME
#       version the corruption was reproduced against, NOT the fleet's dev2/gk/
#       subdev 3.4 build). The alternate-screen TUI means Claude Code owns the
#       whole viewport and never writes into the terminal's native scrollback at
#       all, so the defect class has nothing to corrupt -- the same reasoning that
#       makes `"tui": "fullscreen"` the right managed default. `Ctrl+B [`
#       tmux-native scrollback going empty under it is real and EXPECTED
#       (fullscreen's own `PgUp`/`PgDn`/`Ctrl+O` are the documented replacement,
#       not a bug) -- see the #376 tradeoff discussion on `apply_managed_settings_
#       defaults`. Wins over any local `settings.json` override the SAME way it
#       always did -- confirmed against the installed CC binary that the env var
#       is read before the settings key. Also overrides upstream's own
#       tmux-control-mode / Windows-over-SSH auto-disable guards for fullscreen
#       mode, since those check the SAME env var this mode forces on -- an
#       intentional consequence of opting in explicitly, not something this mode
#       tries to work around.
CLAUDE_LAUNCH_SCRIPT_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — the claude launcher (#77). Read FRESH from
# disk on EVERY invocation (unlike a ~/.bashrc function, which is parsed once
# at shell startup and then stays frozen in that shell's memory forever), so a
# `push` that rewrites this file changes launch behavior immediately in every
# already-running shell — no `source ~/.bashrc`, no relaunch, no restart.
set -euo pipefail

mode="${1:-default}"
if [ "$#" -gt 0 ]; then shift; fi

# claude installs to ~/.local/bin, which NON-LOGIN interactive shells (su
# without -, tmux with a default-command, IDE terminals) never get — only
# ~/.profile adds it, and only login shells read that (montalu@dev1
# "claude: command not found", 2026-07-04).
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac

_has_conversation() {
  local ccdir="${PWD//\//-}"; ccdir="${ccdir//./-}"; ccdir="${ccdir//_/-}"
  compgen -G "$HOME/.claude/projects/$ccdir/*.jsonl" >/dev/null 2>&1
}

case "$mode" in
  plain)
    exec claude "$@"
    ;;
  new)
    exec claude --dangerously-skip-permissions --model '{{MANAGED_MODEL}}' "$@"
    ;;
  ultracode)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c \
        --settings '{"ultracode":true}' --model '{{MANAGED_MODEL}}' "$@"
    else
      exec claude --dangerously-skip-permissions \
        --settings '{"ultracode":true}' --model '{{MANAGED_MODEL}}' "$@"
    fi
    ;;
  fullscreen)
    export CLAUDE_CODE_NO_FLICKER=1
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c --model '{{MANAGED_MODEL}}' "$@"
    else
      exec claude --dangerously-skip-permissions --model '{{MANAGED_MODEL}}' "$@"
    fi
    ;;
  *)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c --model '{{MANAGED_MODEL}}' "$@"
    else
      exec claude --dangerously-skip-permissions --model '{{MANAGED_MODEL}}' "$@"
    fi
    ;;
esac
"""


def render_claude_launch_script():
    """The launch-script content with the managed model substituted in — the
    write site MUST use this, never the raw constant (same discipline as
    render_caveman_shim())."""
    return CLAUDE_LAUNCH_SCRIPT_CONTENT.replace("{{MANAGED_MODEL}}", MANAGED_MODEL)


def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_'
    become '-'. airuleset.py's own top-level copy (#267, reused by
    tests seeding a synthetic ~/.claude/projects/<enc>/ tree) -- the
    IDENTICAL logic also lives inline inside CLAUDE_HISTORY_SCRIPT_CONTENT
    below (that script is deployed standalone and must not import
    airuleset.py itself) and, independently, in watchdog/__init__.py."""
    return "".join("-" if c in "/._" else c for c in str(cwd))


# #267/#376: the "claude-history" companion -- FALLBACK, not primary, since
# #376. The PRIMARY answer for "what did claude do and write" is now
# fullscreen's own native scrollback: `PgUp`/`PgDn` scroll the whole session
# (survives repeated compaction, per Anthropic's own docs), `Ctrl+O` opens
# transcript-mode search -- see `apply_managed_settings_defaults`'s `tui`
# bullet and MANAGED_TUI for the full history/citation. This companion keeps
# a real, still-needed FALLBACK role fullscreen structurally cannot cover:
# checking a session's history from a DIFFERENT pane, or after the session
# has already EXITED (fullscreen's scrollback is a live, in-app view -- it
# is gone once the process is gone; this script instead reads the durable
# transcript JSONL straight off disk). Measured live (dev1, two replicates,
# real interactive sessions + real relayout events -- resizes, Ctrl+O,
# Shift+Tab -- via `scripts/measure_scrollback_holes.py`, results pinned to
# the ticket): CLAUDE_CODE_NO_FLICKER=1 does NOT fix tmux scrollback holes --
# it makes NATIVE tmux scrollback almost entirely EMPTY (78.5-87.33% of a
# generated response missing, even with ZERO relayout stress, because
# alternate-screen mode never writes into tmux's native history buffer at
# all), categorically WORSE than default mode's real-but-small corruption
# (0-6% of lines, ONLY after an actual relayout event). That finding is about
# TRANSIENT ON-SCREEN REDRAW during a live resize -- a different mechanism
# from the PERSISTENT, app-internal scrollback list `PgUp`/`Ctrl+O` read
# from (see the #376 design comment on the ticket for why the two don't
# actually contradict). This companion's own honest fix for "what did claude
# do and write" is unchanged either way: it reads the session's own
# transcript JSONL -- the API's source of truth, which the upstream renderer
# defect (#253: anthropics/claude-code#84247/#46834) cannot touch at all,
# since it never passes through the terminal renderer a second time -- and
# prints a plain, linear, readable log of every real user prompt / assistant
# message / tool call. A live key-by-key test on the installed CC 2.1.223
# confirmed there is no in-app pager to lean on instead under classic mode
# (Ctrl+O there is only an inline verbose toggle -- no pager, the documented
# PgUp/PgDn/{/}/[/] keys inside it do nothing at all); `/export` (a slash
# command, typed inside a LIVE session) is a validated alternative for a
# session you're currently in, but this script also covers the common case
# of checking a session's history AFTER it exited, or from a DIFFERENT pane
# entirely (`--pane`), with zero risk of ever typing a keystroke into
# someone else's live session.
CLAUDE_HISTORY_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-history.py"
CLAUDE_HISTORY_SCRIPT_CONTENT = r'''#!/usr/bin/env python3
# airuleset-managed (do NOT edit) -- claude-history (#267): a readable,
# un-corrupted view of what a Claude Code session did and wrote, built
# straight from its own transcript JSONL -- the source of truth, immune to
# the upstream TUI renderer's tmux-scrollback corruption (#253/#267).
import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path


def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_'
    become '-' (matches airuleset's own encode_project_dir verbatim)."""
    return "".join("-" if c in "/._" else c for c in str(cwd))


def find_transcripts(projects_dir, cwd):
    """Every *.jsonl transcript for `cwd`, newest first."""
    d = Path(projects_dir) / encode_project_dir(cwd)
    if not d.is_dir():
        return []
    rows = []
    for p in d.glob("*.jsonl"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        rows.append((m, p))
    rows.sort(reverse=True)
    return [p for _m, p in rows]


def resolve_pane_cwd(pane_id):
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5)
    except Exception as e:
        print("claude-history: could not resolve pane %r: %s" % (pane_id, e),
              file=sys.stderr)
        return None
    out = (r.stdout or "").strip()
    return out or None


def _read_jsonl(path):
    records = []
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        print("claude-history: cannot read %s: %s" % (path, e), file=sys.stderr)
        return records
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return records


def _tool_summary(name, inp, max_len=100):
    if name == "Bash":
        val = inp.get("command", "")
    elif name in ("Read", "Write", "Edit", "NotebookEdit"):
        val = inp.get("file_path") or inp.get("notebook_path") or ""
    elif name in ("Grep", "Glob"):
        val = inp.get("pattern", "")
    else:
        val = ", ".join("%s=%r" % (k, v) for k, v in list(inp.items())[:3])
    val = str(val).replace("\n", " ")
    if len(val) > max_len:
        val = val[:max_len - 1] + "…"
    return "%s: %s" % (name, val) if val else name


# #267 adversarial-review finding F5: a bare `text.startswith("<")` also ate
# a genuine user prompt that happens to start with a literal "<" (e.g. "<div>
# why does this render badly?") -- silently DROPPING a real question is
# worse than showing one noise line. Anchor on the actual wrapper tags
# Claude Code injects instead of a bare prefix character.
_WRAPPER_NOISE_PREFIXES = (
    "<local-command-stdout>", "<command-name>", "<command-message>",
    "<task-notification>", "<system-reminder>",
)


def merge_turns(records, seen_uuids=None):
    """Collapse consecutive same-role transcript lines into readable turns:
    {"role": "user"|"assistant"|"compact", "text": str, "tools": [str, ...],
    "ts": str|None}. A real assistant API response is written as SEVERAL
    jsonl lines (one per content block) -- this is display grouping, not
    the #131 request-level token dedup (a different, unrelated concern).
    "ts" (#294) is the ISO timestamp of the record that STARTED the turn --
    captured once, at turn creation, never overwritten by later merged
    lines -- or None when the source record carries no "timestamp" field
    (synthetic test fixtures only; a real transcript always has one).

    #376: this is DELIBERATELY a flat, unconditional walk over every
    record in file order -- it never reads `uuid`/`parentUuid` for branch
    SELECTION at all. Live-verified against a real 4MB/1757-line david2
    transcript carrying 5 real compaction boundaries: this shape already
    renders the file's COMPLETE content (nothing before the first
    compaction, nothing between any pair of compactions, and nothing after
    the last one is ever dropped) -- the acceptance is COMPLETENESS
    (never silently lose data), not picking "the one true branch" out of a
    retried/interrupted turn's orphaned sibling. `seen_uuids` (default
    None -> a fresh set, so every pre-#376 single-file call site keeps
    working unmodified) is a caller-shared set for DEDUPING a `uuid` that
    could otherwise appear more than once -- either within one corrupted/
    retried-write file (a real, previously-hit corruption class in this
    repo, see scripts/repair-session.py) or across several CHAINED session
    files for one project (main()'s own new multi-file chaining, below) --
    first occurrence wins, everything after is skipped outright, before
    any role-specific handling ever runs.

    A `system`/`compact_boundary` record becomes its OWN "compact"-role
    turn (never silently skipped) so render() can mark it readably instead
    of the pre-#376 behavior of dropping it with no trace at all."""
    if seen_uuids is None:
        seen_uuids = set()
    turns = []
    pending = None

    def flush():
        if pending is None:
            return
        text = "\n".join(t for t in pending["texts"] if t).strip()
        if text or pending["tools"]:
            turns.append({"role": pending["role"], "text": text,
                          "tools": pending["tools"], "ts": pending["ts"]})

    for rec in records:
        if not isinstance(rec, dict):
            continue
        uid = rec.get("uuid")
        if isinstance(uid, str) and uid:
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
        rtype = rec.get("type")
        if rtype == "system" and rec.get("subtype") == "compact_boundary":
            flush()
            pending = None
            meta = rec.get("compactMetadata")
            pre = meta.get("preTokens") if isinstance(meta, dict) else None
            post = meta.get("postTokens") if isinstance(meta, dict) else None
            turns.append({"role": "compact", "text": "", "tools": [],
                          "ts": rec.get("timestamp"), "pre": pre, "post": post})
            continue
        if rtype == "user":
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str):
                continue  # a tool_result entry, not a real user prompt
            text = content.strip()
            if not text or text.startswith(_WRAPPER_NOISE_PREFIXES):
                continue  # local-command-stdout / injected wrapper noise
            if pending and pending["role"] == "user":
                pending["texts"].append(text)
                continue
            flush()
            pending = {"role": "user", "texts": [text], "tools": [],
                       "ts": rec.get("timestamp")}
        elif rtype == "assistant":
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            texts, tools = [], []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    # #267 F3: a malformed transcript's "text" value can be
                    # anything JSON allows -- only a real string is a real
                    # message; anything else would crash "\n".join() later.
                    t = block.get("text", "")
                    if isinstance(t, str) and t:
                        texts.append(t)
                elif btype == "tool_use":
                    # #267 F3: `input` can be a malformed non-dict shape --
                    # _tool_summary() calls .get() on it unconditionally.
                    inp = block.get("input")
                    if not isinstance(inp, dict):
                        inp = {}
                    tools.append(_tool_summary(block.get("name", "?"), inp))
            if not texts and not tools:
                continue
            if pending and pending["role"] == "assistant":
                pending["texts"].extend(texts)
                pending["tools"].extend(tools)
                continue
            flush()
            pending = {"role": "assistant", "texts": texts, "tools": tools,
                       "ts": rec.get("timestamp")}
        # system / attachment / other entry types: not displayed turns.
    flush()
    return turns


# #294: restrained, muted palette reused verbatim from statusbar.py's own
# established convention (bare "\033[2m" for dim/secondary text, "\033[38;
# 5;<N>m" 256-color codes for accents) rather than inventing a new scheme --
# see the design comment on issue #294 for the full reasoning (why 75/108,
# why body text stays uncolored, why headers are wrapped whole-line).
_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_USER_HDR = "\033[1;38;5;75m"
_ANSI_CLAUDE_HDR = "\033[1;38;5;108m"

_TIMESTAMP_RX = re.compile(r"T(\d{2}:\d{2}:\d{2})")


def _turn_time_suffix(ts):
    """HH:MM:SSZ extracted from a transcript record's ISO "timestamp" field,
    prefixed with a space for direct header-line concatenation -- or "" when
    ts is missing/malformed (never crashes, never prints a "None" literal;
    #294 design comment). The trailing "Z" (#294 adversarial review, MINOR)
    marks the time as UTC explicitly -- a real transcript timestamp always
    is ("...Z" suffix), and a bare "HH:MM:SS" with no marker reads as
    ambiguous local-vs-UTC time; a real timezone CONVERSION was rejected as
    unnecessary complexity for a "decent" (per the ticket's own Slovak
    wording) timestamp display."""
    if not isinstance(ts, str):
        return ""
    m = _TIMESTAMP_RX.search(ts)
    return " %sZ" % m.group(1) if m else ""


def _wrap_plain(text, width):
    """Word-wrap TEXT to WIDTH columns, one PHYSICAL line at a time -- a
    literal "\\n" already in the source is a real paragraph break and is
    never merged into the wrap. `width` <=0 or None is a no-op (matches
    every pre-#376 caller's own behavior unchanged, width-independent).
    `break_long_words=False`/`break_on_hyphens=False`: a single long token
    (a URL, a hash, a path) is never chopped mid-word -- it simply
    overflows that one line rather than being silently corrupted, the same
    "never mangle a token" spirit `_tool_summary`'s own 100-char
    truncation already follows. #376: fixes the popup's own reported
    "scrolls right instead of wrapping" complaint for the TRANSCRIPT-
    reconstruction content -- applied here, to the PLAIN text, BEFORE any
    ANSI color codes are added, so a fold point can never land inside an
    escape sequence (the well-documented `less -R` limitation this
    sidesteps entirely: multiple embedded escape sequences on one line can
    defeat `less`'s own wrap-column tracking)."""
    if not width or width <= 0:
        return text
    out_lines = []
    for line in text.split("\n"):
        if not line:
            out_lines.append(line)
            continue
        wrapped = textwrap.wrap(line, width, break_long_words=False,
                                 break_on_hyphens=False)
        out_lines.extend(wrapped if wrapped else [line])
    return "\n".join(out_lines)


def render(turns, last=None, use_color=False, width=None):
    """#294: colors ADD to the existing plain layout, they never replace
    it -- the "===== USER =====" / "===== CLAUDE =====" header (the clear
    turn separator that pre-dates #294) is wrapped whole-line in the role
    color rather than restructured, tool-call lines and the optional
    timestamp suffix are dimmed, and body TEXT stays uncolored in both
    modes. ANSI codes are non-alphanumeric prefixes/suffixes only -- they
    never splice into the middle of a plain-text substring a caller might
    grep for, so every pre-#294 plain-text assertion still holds even when
    use_color=True.

    #376: a "compact"-role turn (a real `system`/`compact_boundary` record,
    see merge_turns) renders as its own distinct, readably-labelled marker
    -- "----- COMPACTED ... -----", never the "===== USER/CLAUDE ====="
    shape -- so a reader can tell at a glance where the session's own
    context got summarized, instead of the pre-#376 silent skip that left
    no trace of the boundary at all.

    `width` (#376, default None -- no wrap, byte-for-byte the pre-#376
    behavior): word-wraps body TEXT and tool-summary lines to that many
    columns via `_wrap_plain`, applied to the PLAIN string BEFORE any ANSI
    color code is appended -- see `_wrap_plain`'s own docstring for why."""
    if last is not None:
        turns = turns[-last:]
    lines = []
    for t in turns:
        if t["role"] == "compact":
            pre, post = t.get("pre"), t.get("post")
            detail = ""
            if pre is not None or post is not None:
                detail = " (preTokens=%s, postTokens=%s)" % (pre, post)
            header = "----- COMPACTED%s -----" % detail
            if use_color:
                line = _ANSI_DIM + header + _ANSI_RESET
                ts_suffix = _turn_time_suffix(t.get("ts"))
                if ts_suffix:
                    line += _ANSI_DIM + ts_suffix + _ANSI_RESET
            else:
                line = header
            lines.append(line)
            lines.append("")
            continue
        label = "USER" if t["role"] == "user" else "CLAUDE"
        header = "===== %s =====" % label
        if use_color:
            hdr_color = _ANSI_USER_HDR if t["role"] == "user" else _ANSI_CLAUDE_HDR
            line = hdr_color + header + _ANSI_RESET
            ts_suffix = _turn_time_suffix(t.get("ts"))
            if ts_suffix:
                line += _ANSI_DIM + ts_suffix + _ANSI_RESET
        else:
            line = header
        lines.append(line)
        if t["text"]:
            lines.append(_wrap_plain(t["text"], width))
        for tool in t["tools"]:
            tool_line = _wrap_plain("  -> %s" % tool, width)
            if use_color:
                tool_line = _ANSI_DIM + tool_line + _ANSI_RESET
            lines.append(tool_line)
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="claude-history",
        description="Readable Claude Code session history, built from the "
                     "transcript (source of truth -- immune to tmux "
                     "scrollback corruption, airuleset#267).")
    ap.add_argument("--cwd", default=None,
                     help="project directory (default: current directory)")
    ap.add_argument("--pane", default=None,
                     help="tmux pane id -- resolve ITS cwd instead of --cwd")
    ap.add_argument("--transcript", default=None,
                     help="read this transcript file directly")
    ap.add_argument("--last", type=int, default=20,
                     help="show only the last N turns (default 20)")
    ap.add_argument("--full", action="store_true",
                     help="show the whole session (overrides --last)")
    ap.add_argument("--list", action="store_true",
                     help="list available transcripts for this project and exit")
    ap.add_argument("--width", type=int, default=0,
                     help="word-wrap body text/tool lines to this many "
                          "columns (#376); 0 or omitted = no wrap, the "
                          "pre-#376 default")
    color_group = ap.add_mutually_exclusive_group()
    color_group.add_argument("--color", action="store_true",
                              help="force ANSI colors ON even when stdout is "
                                   "piped (e.g. into a pager) -- TTY "
                                   "auto-detection cannot see through a pipe")
    color_group.add_argument("--plain", action="store_true",
                              help="force ANSI colors OFF even on a real "
                                   "terminal (default: colors auto-detect "
                                   "off when piped, on on a real terminal)")
    args = ap.parse_args(argv)
    # #267 F4: `--last 0`/negative would print "showing last N" then
    # actually show something else entirely (Python slice semantics:
    # turns[-0:] is every turn, turns[-3:] drops the wrong end) -- the
    # printed header must never contradict what's actually rendered.
    args.last = max(1, args.last)
    # #294: --color/--plain force the decision explicitly; absent either
    # flag, auto-detect off a real TTY -- a piped subprocess.run(capture_
    # output=True) stdout is never a tty, so every pre-#294 test (and a
    # plain `claude-history | cat`) stays ANSI-free with zero new logic.
    if args.color:
        use_color = True
    elif args.plain:
        use_color = False
    else:
        use_color = sys.stdout.isatty()

    projects_dir = Path.home() / ".claude" / "projects"

    # #376: `--transcript` (explicit single-file, human by-path invocation)
    # keeps its EXACT pre-#376 single-file contract -- never chained. A
    # cwd/pane-resolved lookup can find MULTIPLE `.jsonl` files for one
    # project (`claude-new`'s always-fresh mode, or any other reason a
    # second session id exists) -- under `--full` (the mode prefix+h
    # invokes), ALL of them are chained together, oldest-first, so
    # an older sibling file's own content is never silently dropped just
    # because a newer one exists. `--last` (the default quick-glance mode)
    # deliberately keeps the pre-#376 single-newest-file behavior -- see
    # the #376 design comment on the ticket for why this is scoped to
    # `--full` only (minimize behavioral change / blast radius).
    if args.transcript:
        paths = [Path(args.transcript)]
        chain_all = False
    else:
        if args.pane:
            cwd = resolve_pane_cwd(args.pane)
            if not cwd:
                print("claude-history: pane %r not found or has no cwd" % args.pane,
                      file=sys.stderr)
                return 1
        else:
            cwd = args.cwd or os.getcwd()
        paths = find_transcripts(projects_dir, cwd)
        if not paths:
            print("claude-history: no Claude Code session transcript found for %s"
                  % cwd, file=sys.stderr)
            return 1
        if args.list:
            for p in paths:
                try:
                    when = time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(p.stat().st_mtime))
                except OSError:
                    when = "?"
                print("%s  %s" % (when, p))
            return 0
        chain_all = args.full

    # find_transcripts() returns newest-first (its own established
    # --list ordering, unchanged); chaining needs chronological
    # (oldest-first) order so turns from an older session file are never
    # shown AFTER turns from a newer one.
    #
    # ACCEPTED RESIDUAL (#376 M5, adversarial review, THEORETICAL --
    # never observed live, so left undone under this repo's FREEZE
    # policy rather than chased): this orders files by OS-level MTIME,
    # not by each file's own SESSION-START timestamp. The two normally
    # agree, but a file whose transcript stopped being written to long
    # before a LATER-started sibling file was itself created (e.g. an
    # abandoned/orphaned chain member) could sort out of true
    # chronological order. Fixing it would mean reading the first
    # real entry's `timestamp` out of every candidate file before
    # sorting -- a real, non-trivial change, not a one-line swap;
    # documented here rather than implemented pre-emptively.
    paths = list(reversed(paths)) if chain_all else paths[:1]

    if not any(p.exists() for p in paths):
        print("claude-history: transcript not found: %s" % paths[0], file=sys.stderr)
        return 1

    # #376: `seen_uuids` is shared across every chained file so a `uuid`
    # duplicated across files (or within one corrupted/retried-write file)
    # is rendered exactly once -- see merge_turns's own docstring.
    seen_uuids = set()
    turns = []
    for p in paths:
        if not p.exists():
            continue
        turns.extend(merge_turns(_read_jsonl(p), seen_uuids))
    if not turns:
        print("claude-history: transcript has no displayable turns: %s" % paths[-1],
              file=sys.stderr)
        return 1

    if len(paths) == 1:
        label = str(paths[0])
    else:
        label = "%s (+%d earlier session file%s chained)" % (
            paths[-1], len(paths) - 1, "" if len(paths) == 2 else "s")
    print("# %s" % label)
    if args.full:
        print("# %d turn(s) total" % len(turns))
    else:
        print("# %d turn(s) total -- showing last %d" % (len(turns), args.last))
    print("")
    print(render(turns, None if args.full else args.last, use_color=use_color,
                 width=args.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_claude_history_script():
    """The claude-history script content -- no template substitution needed
    (unlike render_claude_launch_script), but the same "always render
    through a function, never write the raw constant" discipline (same
    reason as render_caveman_shim/render_claude_launch_script: a future
    templated field must never be forgotten at the one real write site)."""
    return CLAUDE_HISTORY_SCRIPT_CONTENT


# #289: the popup's own logic lives in a SEPARATE SCRIPT FILE, invoked BY
# PATH from the tmux bind-key command line -- never inlined as a shell
# one-liner embedded in the conf. Verified live, tmux 3.7b: tmux's OWN
# conf-file DOUBLE-QUOTE parser expands `$VAR` at CONF-PARSE/bind time
# (using tmux's OWN process environment), not at shell-run time -- so
# `$CH_OUT`/`$CH_RC`/`$?` referenced inline in a double-quoted popup
# command silently blanked to EMPTY STRING before the shell ever ran
# (confirmed via `list-keys`: the bound command showed `if [ "" -ne 0 ]`
# where `"$CH_RC"` should have been). Single-quoted tmux strings do NOT
# expand `$VAR` (also verified live) but tmux's own single-quote parsing
# supports no escapes at all -- embedding a literal `'` (this command's
# own `printf '%s...'` calls need several) would require the POSIX
# quote-splice idiom (`'...'\''...'`, confirmed tmux honours it too) on
# EVERY embedded quote, which is exactly the class of hand-spliced-quoting
# bug this repo's own playbook already warns is fragile and easy to get
# wrong. A script file invoked by its own ABSOLUTE PATH sidesteps the
# whole landmine: the ONLY thing the tmux bind-key line needs to resolve
# is the path itself, baked in at Python RENDER time (this box's own
# `Path.home()`, correct for the user `install`/`push` runs as) -- no
# `$VAR` of any kind needs to survive the conf-parser at all.
CLAUDE_HISTORY_POPUP_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-history-popup.sh"
# HISTORY (kept for the still-relevant TECHNICAL FACTS the current
# fallback below depends on, not because the design they describe is
# still current -- #327 made `tmux capture-pane` this popup's PRIMARY
# source, #337 then split that behavior per-binding, and #376 REVERSES
# both: the transcript reconstruction is unconditionally primary again,
# see the module comment above CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT for
# the current design):
#
# A BARE `tmux capture-pane`/`display-message` call with NO explicit
# `-t`, issued from WITHIN this popup's own shell, resolves against the
# ORIGINATING pane (the one the popup key was pressed in) -- confirmed
# live TWICE, independently: once via an isolated `-L` socket with a real
# attached pty client switched across THREE windows (a decoy window's
# content never leaked into the capture), and again via a fresh-context
# adversarial review's own, stronger repro -- a genuine 2-SESSION/2-CLIENT
# server with the raw popup-key bytes injected into each client's own
# pty, confirming the resolution follows the PRESSING client correctly in
# both directions (#327). The mechanism is `display-popup` setting the
# popup job's own `$TMUX` to the PRESSING client's target session --
# never rely on `$TMUX_PANE` inside a popup as a shortcut for this (its
# value is unreliable/environment-dependent, not a documented tmux
# guarantee); the bare-target resolution above is the only proven path.
# The ONE proven way to break this: adding `-c <client>` to
# `display-popup`, or invoking this script via `run-shell` instead of as
# the popup's own shell-command -- NEVER do either; both were shown live
# to route the capture to the WRONG session's pane. `-e` preserves the
# pane's own real SGR/ANSI bytes; `-S -{{TMUX_HISTORY_LIMIT}}` matches
# TMUX_HISTORY_LIMIT (#235's own scrollback-retention mitigation) so this
# reaches everything tmux's own history buffer could possibly hold --
# still the exact fallback wired into CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT
# below, just no longer the PRIMARY path.
#
# #376: fullscreen is now the
# PRIMARY way to view history (PgUp/PgDn + Ctrl+O, app-internal, survives
# compaction -- see apply_managed_settings_defaults' own docstring). This
# popup is a FALLBACK ONLY, for cross-session / already-closed-pane
# history a live fullscreen scrollback cannot show -- so it no longer
# needs to impersonate the live terminal (#327's whole reason for
# existing) or juggle multiple bind-specific behaviors (#337's MODE
# branching): ONE binding (prefix-h, the only one the user personally
# confirmed opens -- see the module comment above TMUX_POPUP_PREFIX_KEY),
# ALWAYS the complete, hole-free transcript reconstruction, with a real
# `tmux capture-pane` as ITS OWN fallback only when the reconstruction
# itself resolves nothing.
CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT = r'''#!/usr/bin/env bash
# airuleset-managed (do NOT edit) -- claude-history popup companion
# (#289, unconditional transcript-primary fallback by #376). Invoked from
# the managed tmux prefix-h display-popup bind (TMUX_POPUP_BIND_ARGVS in
# airuleset.py) -- fullscreen rendering (PgUp/PgDn, Ctrl+O) is the
# PRIMARY way to view history; this popup is a fallback for cross-session
# history a live fullscreen scrollback can't show. FAILS LOUDLY, never
# silently: on total failure (every source this script tries) the last
# error is shown and the popup waits for a keypress before closing,
# rather than handing `less` empty stdin (which can close instantly with
# nothing to read).
set -euo pipefail

WIDTH="$(tput cols 2>/dev/null)" || WIDTH=0
[ -n "$WIDTH" ] || WIDTH=0

# #376 M1 (adversarial review, measured live on this repo's own real
# project data: ~25s wall / ~817MB peak RSS): the transcript
# reconstruction below can take long enough that, with nothing printed
# first, the popup appears BLANK/FROZEN for the whole window -- print
# this BEFORE starting it, to stderr (never stdout, which `less` will
# render as the final content once the real capture finishes and
# overwrites this line).
printf 'Loading claude-history...\n' >&2

# PRIMARY: the complete, hole-free transcript reconstruction, immune to
# the upstream Claude Code classic-renderer scrollback-duplication
# regression (anthropics/claude-code #84247/#46834, both still open as
# of #376) -- word-wrapped to the popup's own live column width so long
# lines never need horizontal scrolling (#376).
#
# `--color` is forced UNCONDITIONALLY here -- a deliberate REVERSAL of
# #327's own documented popup-neutrality choice (that ticket forced
# `--plain` specifically so the popup's rendering never impersonated a
# real terminal's exact colors). #376 no longer needs that neutrality:
# the popup is now a FALLBACK only (never claiming to mirror the live
# pane), so real color is a strict readability upgrade with nothing to
# stay neutral about.
#
# `set -e` + `VAR=$(failing_cmd)` would otherwise exit this script BEFORE
# the next line ever runs (a failing command substitution used in a plain
# assignment is an unhandled failure under -e) -- the `|| <NAME>_RC=$?`
# form is the established fix: it captures the real exit code without
# tripping -e, and each RC stays unset (defaulted to 0 below) on its own
# success path.
CH_OUT=$(python3 "$HOME/.claude/airuleset-claude-history.py" --full --color --width "$WIDTH" 2>&1) || CH_RC=$?
CH_RC="${CH_RC:-0}"
# The fallback triggers on EITHER a nonzero exit OR an empty result -- RC
# alone would miss the real "rc=0 but $(...) stripped the whole output to
# an empty string" case (claude-history returning nothing displayable),
# the exact mirror of a finding this repo's own playbook already records
# for the sibling #327 ticket's capture-pane-blank-pane case.
if [ "$CH_RC" -ne 0 ] || [ -z "$CH_OUT" ]; then
  TRANSCRIPT_OUT="$CH_OUT"
  # FALLBACK: a real tmux capture-pane of the ORIGINATING pane, for the
  # rare case the transcript reconstruction itself produces nothing at
  # all (no readable transcript file, e.g. a genuinely empty project). A
  # bare (no `-t`) capture-pane call issued from WITHIN a display-popup's
  # own shell-command resolves against the pane the popup key was
  # pressed in, never the popup's own new pseudo-pane -- verified live,
  # twice, independently (see the module comment above TMUX_POPUP_PREFIX_KEY in
  # airuleset.py). `-e` preserves the real colors/escape sequences the
  # pane actually rendered; `-p` prints to stdout for this command-
  # substitution capture; `-S -{{TMUX_HISTORY_LIMIT}}` reaches back
  # across the FULL configured scrollback -- the SAME value as the
  # managed history-limit itself (never a second hardcoded literal that
  # could silently drift shorter than what tmux actually retains).
  CP_OUT=$(tmux capture-pane -e -p -S -{{TMUX_HISTORY_LIMIT}} 2>&1) || CP_RC=$?
  CP_RC="${CP_RC:-0}"
  if [ "$CP_RC" -eq 0 ] && [ -n "$CP_OUT" ]; then
    CH_OUT="$CP_OUT"
    CH_RC=0
  else
    # M5 guard: BOTH sources genuinely failed/produced nothing -- fail
    # loudly with both diagnostics shown, never a silent instant-close.
    CH_OUT="claude-history (transcript, primary) produced nothing:
${TRANSCRIPT_OUT}

tmux capture-pane (fallback) also produced nothing:
${CP_OUT}"
    CH_RC=1
  fi
fi

if [ "$CH_RC" -ne 0 ]; then
  printf '%s\n\nclaude-history: press any key to close.\n' "$CH_OUT"
  read -n 1 -r -s _dummy || true
elif ! command -v less >/dev/null 2>&1; then
  # ADVERSARIAL-REVIEW FINDING (#289, M5): a box genuinely missing `less`
  # would otherwise hand the successfully-read transcript to a nonexistent
  # command, closing instantly with no visible cause -- the exact silent
  # instant-close this script's own header promises never to do. `less`
  # is tracked in RUNTIME_DEPS and installed fleet-wide, but this is the
  # box's own last-resort guard should it still be missing somehow.
  printf '%s\n\nclaude-history: "less" is not installed on this box.\n\npress any key to close.\n' "$CH_OUT"
  read -n 1 -r -s _dummy || true
else
  # #294: -R makes `less` render raw ANSI color bytes as color instead of
  # visibly escaping them; +G (jump to end) and less's own default
  # incremental search are both unaffected by -R.
  printf '%s\n' "$CH_OUT" | less -R +G
fi
'''


def render_claude_history_popup_script(limit=None):
    """The popup-script content, with the `{{TMUX_HISTORY_LIMIT}}`
    placeholder substituted. Default resolved INSIDE the body (never as a
    parameter default) since TMUX_HISTORY_LIMIT is defined LATER in this
    module than this function -- a parameter default is evaluated at
    function-DEFINITION time, which would raise NameError at import time.
    #376 dropped the `mode_transcript_primary` param/placeholder entirely
    -- the script is now unconditionally transcript-primary (see the
    module comment above CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT), so there
    is nothing left to select between."""
    if limit is None:
        limit = TMUX_HISTORY_LIMIT
    return CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT.replace(
        "{{TMUX_HISTORY_LIMIT}}", str(limit))


# .bashrc holds ONLY thin one-line functions -- no flag literal survives here,
# so nothing flag-shaped can ever be frozen in a shell's memory again.
ULTRACODE_BASHRC_BLOCK = (
    f"{ULTRACODE_MARK_START}\n"
    f'claude() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" default "$@"; }}\n'
    f'claude-new() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" new "$@"; }}\n'
    f'claude-ultracode() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" ultracode "$@"; }}\n'
    f'claude-plain() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" plain "$@"; }}\n'
    f'claude-fullscreen() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" fullscreen "$@"; }}\n'
    f'claude-history() {{ python3 "$HOME/.claude/{CLAUDE_HISTORY_SCRIPT_DEST.name}" "$@"; }}\n'
    f"{ULTRACODE_MARK_END}"
)


def apply_ultracode_launcher(bashrc_path: Path = None, script_path: Path = None,
                              history_script_path: Path = None,
                              popup_script_path: Path = None) -> bool:
    """Install/refresh the managed claude launcher (#77) AND the
    claude-history companion (#267 -- same mechanism, same self-heal
    discipline, deliberately extended in place rather than given its own
    parallel marker-block machinery) AND the claude-history POPUP
    companion script (#289 -- see the module comment above
    CLAUDE_HISTORY_POPUP_SCRIPT_DEST for why this is its OWN script file
    rather than an inline shell command in the tmux bind-key line).

    The SCRIPT (script_path, default CLAUDE_LAUNCH_SCRIPT_DEST) is written and
    chmod +x UNCONDITIONALLY on every call — like the caveman shim, it must
    self-heal any tampering/rollback, and a missing script after write is a
    loud RuntimeError, never a silent loss of `claude`. It carries ALL the
    actual logic, so a `push` changes launch behavior in every already-running
    shell immediately, with no `source ~/.bashrc` and no restart. The
    claude-history script (history_script_path, default
    CLAUDE_HISTORY_SCRIPT_DEST) and the claude-history POPUP script
    (popup_script_path, default CLAUDE_HISTORY_POPUP_SCRIPT_DEST) both get
    the IDENTICAL unconditional write + chmod +x + missing-after-write
    RuntimeError treatment.

    The ~/.bashrc block is idempotent (replaces the marked block if present,
    else appends it) and holds ONLY thin wrapper functions with no flag
    literals. Returns True iff the ~/.bashrc file changed."""
    import re
    bpath = bashrc_path or BASHRC
    spath = script_path or CLAUDE_LAUNCH_SCRIPT_DEST
    hpath = history_script_path or CLAUDE_HISTORY_SCRIPT_DEST
    ppath = popup_script_path or CLAUDE_HISTORY_POPUP_SCRIPT_DEST

    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(render_claude_launch_script())
    os.chmod(str(spath), 0o755)
    if not spath.exists():
        raise RuntimeError(f"claude launcher script missing right after write: {spath}")

    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text(render_claude_history_script())
    os.chmod(str(hpath), 0o755)
    if not hpath.exists():
        raise RuntimeError(f"claude-history script missing right after write: {hpath}")

    ppath.parent.mkdir(parents=True, exist_ok=True)
    ppath.write_text(render_claude_history_popup_script())
    os.chmod(str(ppath), 0o755)
    if not ppath.exists():
        raise RuntimeError(f"claude-history popup script missing right after write: {ppath}")

    existing = bpath.read_text() if bpath.exists() else ""
    if ULTRACODE_MARK_START in existing and ULTRACODE_MARK_END in existing:
        pattern = re.compile(
            re.escape(ULTRACODE_MARK_START) + r".*?" + re.escape(ULTRACODE_MARK_END),
            re.S)
        new = pattern.sub(lambda _m: ULTRACODE_BASHRC_BLOCK, existing)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{ULTRACODE_BASHRC_BLOCK}\n"
    if new != existing:
        bpath.write_text(new)
        return True
    return False


# --- #263/#264: subdev stream account dev-env convention -------------------
# The convention working directory for a subdev stream account's tmux
# session: every currently-provisioned account (montalu/marek/david/simap,
# live-verified; montalu2/montalu3/montalu4 per their own TODO-PROVISIONING.md
# gatekeeper Phase-1 contract) checks out the odoo-erp repo at exactly this
# path. Used by both #263's tmux bootstrap (_stream_session_cwd, below
# AUTHORITY_BY_USER) and #264's ssh auto-attach block (right below) -- ONE
# literal, not two independently-maintained copies.
STREAM_DEV_CWD_REL = "devel/odoo/odoo-erp"

# --- #264: subdev stream ssh auto-attach ------------------------------------
# One subdev stream account = one tmux session; an interactive ssh login
# should attach straight into it instead of the user attaching by hand.
STREAM_SSH_ATTACH_MARK_START = "# >>> airuleset: subdev ssh auto-attach >>>"
STREAM_SSH_ATTACH_MARK_END = "# <<< airuleset: subdev ssh auto-attach <<<"
STREAM_SSH_ATTACH_BLOCK = (
    f"{STREAM_SSH_ATTACH_MARK_START}\n"
    "# #264: one subdev stream account = one tmux session -- an interactive\n"
    "# ssh login attaches straight into it (create-or-attach, `-A`). NEVER\n"
    "# fires for a NON-interactive ssh run (push's `git pull && python3\n"
    "# airuleset.py install`, scp/rsync, watchdog/gatekeeper automation) --\n"
    "# those pass a COMMAND to ssh, which bash executes with `$-` carrying no\n"
    "# 'i' and no PTY at all, so this whole block is a no-op for them; guarded\n"
    "# on all three explicitly anyway (interactive shell, a real ssh TTY, not\n"
    "# already inside tmux) so nothing here can ever race a live session.\n"
    # command -v tmux: if tmux is ever missing/broken on a stream account,
    # `exec tmux ...` would fail AFTER the shell has already been replaced
    # -- closing the ssh session outright instead of leaving a working
    # interactive shell behind (an adversarial review's finding — this
    # guard keeps that failure mode from ever being reachable).
    'if [[ $- == *i* ]] && [ -n "${SSH_TTY:-}" ] && [ -z "${TMUX:-}" ] '
    '&& command -v tmux >/dev/null 2>&1; then\n'
    f'  __airuleset_cwd="$HOME/{STREAM_DEV_CWD_REL}"\n'
    '  [ -d "$__airuleset_cwd" ] || __airuleset_cwd="$HOME"\n'
    '  __airuleset_me="$(whoami)"\n'
    "  # #284: a tmux destroy-unattached sweep (#254) can reduce a\n"
    "  # multi-member session GROUP down to exactly one survivor whose\n"
    "  # NAME is iteration-order-arbitrary -- not necessarily this exact\n"
    "  # username the -A reattach below depends on. If a differently-named\n"
    "  # survivor is invisible to the exact check, the plain -A path would\n"
    "  # silently create a fresh EMPTY session while the real, populated\n"
    "  # one sits orphaned in its own group. Search for a live group\n"
    "  # survivor FIRST -- `=`-anchored EXACT match (#263's own\n"
    "  # established fix: a bare target does PREFIX matching and would\n"
    "  # wrongly match e.g. zbynek-4 for zbynek) -- and, if found, join it\n"
    "  # as a new independent VIEW onto the SAME windows (grouped session,\n"
    "  # `new-session -t`) -- the user's own decided reattach behaviour.\n"
    "  # The survivor's own name is captured into a variable and the\n"
    "  # actual `exec` happens AFTER the `while ... done < <(...)` loop\n"
    "  # closes, never inside it -- an adversarial review proved live\n"
    "  # that an `exec` sitting INSIDE the process-substitution loop\n"
    "  # inherits that pipe as its own stdin, so a real tmux client\n"
    "  # refuses to attach (`open terminal failed: not a terminal`) and\n"
    "  # the ssh login dies right there, since `exec` already replaced\n"
    "  # the shell -- worse than the pre-#284 behaviour it was meant to\n"
    "  # fix. Falls through to the plain exact-name path below when no\n"
    "  # survivor is found, or tmux itself is unreachable. Residual\n"
    "  # (documented, not chased): if the survivor is destroyed by a\n"
    "  # concurrent sweep in the narrow window between `list-sessions`\n"
    "  # returning its name and the `exec` below running, real tmux does\n"
    "  # NOT error -- it silently creates a brand-new session in a\n"
    "  # freshly-derived group name instead of falling through to -A -s;\n"
    "  # still a live, working session either way, just not the exact\n"
    "  # -A -s fallback this comment used to (wrongly) promise.\n"
    '  if ! tmux has-session -t "=$__airuleset_me" 2>/dev/null; then\n'
    '    __airuleset_survivor=""\n'
    '    while read -r __airuleset_g __airuleset_n; do\n'
    '      if [ -n "$__airuleset_n" ] '
    '&& [ "$__airuleset_g" = "$__airuleset_me" ]; then\n'
    '        __airuleset_survivor="$__airuleset_n"\n'
    "        break\n"
    "      fi\n"
    "    done < <(tmux list-sessions "
    "-F '#{session_group} #{session_name}' 2>/dev/null)\n"
    '    if [ -n "$__airuleset_survivor" ]; then\n'
    '      exec tmux new-session -t "$__airuleset_survivor"\n'
    "    fi\n"
    "  fi\n"
    '  exec tmux new-session -A -s "$__airuleset_me" -c "$__airuleset_cwd"\n'
    "fi\n"
    f"{STREAM_SSH_ATTACH_MARK_END}"
)


def _stream_marker_block_spans(existing, start=STREAM_SSH_ATTACH_MARK_START,
                                end=STREAM_SSH_ATTACH_MARK_END):
    """Left-to-right positional scan for CLEAN (start, end) marker pairs --
    NEVER a lazy regex `.*?` search, which silently deletes real content
    sitting between a stray leftover START and a LATER, genuine block's END
    on a second run against a conf/rc file externally corrupted with a
    marker literal (#235's own documented failure of that exact shape,
    `_clean_tmux_block_spans`). A pair CROSSED by another marker literal is
    skipped and left as inert text, never merged into a neighbouring block
    -- that specific corruption class is closed.

    Known residual (live-verified by an adversarial review, kept honest
    here rather than overclaimed): a single ISOLATED stray START/END pair
    with no OTHER marker literal between them (e.g. a truncated copy-paste
    accident) still reads as one clean span and its own real content IS
    still lost on rewrite -- no purely positional scan can distinguish that
    shape from "this is genuinely our own block". Also: an unpaired,
    never-matched marker line is never removed by this function on its
    own -- it stays as inert text forever (harmless, since it's a bash
    comment, but neither `apply_stream_ssh_attach` add nor remove path
    "self-heals" it)."""
    spans = []
    pos = 0
    s_len = len(start)
    while True:
        s = existing.find(start, pos)
        if s == -1:
            break
        e = existing.find(end, s + s_len)
        if e == -1:
            pos = s + s_len
            continue
        inner = existing[s + s_len:e]
        if start in inner or end in inner:
            pos = s + s_len
            continue
        e_full = e + len(end)
        spans.append((s, e_full))
        pos = e_full
    return spans


def apply_stream_ssh_attach(bashrc_path: Path = None, user: str = None) -> bool:
    """Idempotently add/remove the #264 ssh-auto-attach marker block in
    ~/.bashrc, scoped STRICTLY to subdev stream accounts (AUTHORITY_BY_USER's
    keys -- the exact registry #263's tmux bootstrap also keys off, single
    source of truth for "which accounts are subdev streams"). Absent from
    every other box (dev1/dev2/gatekeeper): the marker is actively REMOVED
    there if ever present, so a future AUTHORITY_BY_USER edit can never leave
    a stale attach block on the wrong account.

    Same overall idempotent-marker-block shape as apply_ultracode_launcher
    (#77) -- create/update if this account should have it, strip if not --
    but the presence check + rewrite use a positional span scan
    (`_stream_marker_block_spans`), not a lazy regex search, which closes
    the WORST failure of that class (a stray leftover START silently
    eating real content up to a LATER genuine block's END) without
    claiming to close every possible corruption shape -- see that
    function's own docstring for the residual it honestly leaves open.
    Returns True iff ~/.bashrc changed."""
    bpath = bashrc_path or BASHRC
    u = user or _current_user()
    should_have = u in AUTHORITY_BY_USER
    existing = bpath.read_text() if bpath.exists() else ""
    spans = _stream_marker_block_spans(existing)
    if should_have:
        if spans:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                out.append(STREAM_SSH_ATTACH_BLOCK)
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
        else:
            sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
            new = f"{existing}{sep}\n{STREAM_SSH_ATTACH_BLOCK}\n"
    else:
        if not spans:
            return False
        out, cursor = [], 0
        for s, e in spans:
            out.append(existing[cursor:s])
            cursor = e
        out.append(existing[cursor:])
        new = "".join(out)
    if new != existing:
        # Atomic write: a plain write_text() truncates-then-writes, a real
        # (if narrow) window for a killed process (e.g. this account's own
        # `push` install, now running longer network steps that can time
        # out) to leave ~/.bashrc half-written. tmp-write + os.replace
        # makes the swap atomic on the same filesystem.
        tmp = bpath.with_suffix(bpath.suffix + ".airuleset-tmp")
        tmp.write_text(new)
        os.replace(str(tmp), str(bpath))
        return True
    return False


TMUX_CONF = Path.home() / ".tmux.conf"
TMUX_HISTORY_LIMIT = 50000
TMUX_DEFAULT_SIZE = "176x50"
# #254: "keep-last", NOT "keep-group" -- despite the ticket's own title
# naming keep-group, that value DESTROYS EVERY ORDINARY STANDALONE
# (non-grouped) session the moment its one client detaches, identical to
# boolean `on` -- confirmed live against a real tmux 3.7b server via a
# genuine pty-attached client on an isolated `-L` scratch socket (never
# the box's real default server). Since almost every real project session
# on the fleet is a plain standalone session, keep-group would nuke
# essentially all of them on every detach -- far worse than the pile-up
# bug it exists to fix. keep-last is the value that matches what the
# ticket's OWN prose actually describes: destroy a detached GROUPED
# sibling only while another session remains in its group, and leave both
# a group's last surviving member and every standalone session untouched.
# See render_tmux_history_block/apply_tmux_history_limit below, and
# TestTmuxDestroyUnattached in tests/test_airuleset.py for the full
# regression lock against reverting to keep-group or bare `on`.
TMUX_DESTROY_UNATTACHED = "keep-last"
TMUX_MARK_START = "# >>> airuleset tmux >>>"
TMUX_MARK_END = "# <<< airuleset tmux <<<"
# #235: tmux's own built-in default (2000-line scrollback) plus the current
# Claude Code renderer re-rendering the viewport in place and stacking
# duplicate/partial frames into pane history on re-render events made real
# scrollback holey within minutes under agentic load (measured live: active
# panes saturated at ~1937-1942/2000). Fix: raise history-limit fleet-wide.
# Same idempotent-marker-block shape as apply_ultracode_launcher (#77) above
# -- create the file if missing, rewrite ONLY the block's content if the
# markers already exist, never touch anything outside them.
#
# #236: the identical frame-stacking mechanism also fires on every ATTACH
# from a different-sized terminal -- tmux's default `window-size latest`
# auto-resizes the whole window to fit the new client, and Claude Code
# re-renders the visible screen in place on that resize. #236 originally
# tried to pin `window-size manual` (stop the auto-resize) alongside
# `default-size 176x50` (the fixed size new windows get -- the user's own
# client, 176x51, is the confirmed smallest on the fleet, so 176x50 crops
# nobody and larger clients just get an unused margin).
#
# #241: `window-size manual` was REMOVED again -- it CRASHES tmux 3.4's
# server outright at startup (`server exited unexpectedly`), confirmed
# live against the real 3.4 binary every managed box runs, the only
# version Ubuntu 24.04 noble ships. A box whose conf carried the line
# could not start tmux at all. This is a DIFFERENT failure than #236's own
# live-apply finding (flipping window-size against a RUNNING server snaps
# every window back to its stored size -- a disruptive resize, not a
# crash): there is no safe way to ship the option at all, conf-only or
# otherwise, so it is gone from the managed block entirely. Cost: without
# `manual`, tmux keeps auto-tracking the smallest attached client's size,
# so the fixed geometry #236 wanted is only PARTLY delivered by
# `default-size` alone (new windows still start at 176x50; an existing
# window's LIVE size is no longer pinned against later attach/detach
# cycles) -- see #236's own comment thread for that trade-off.
# `default-size` stays: it starts cleanly on 3.4. This ticket's own
# incident history (two live-tmux destructions on dev1, the second a
# kernel segfault in tmux 3.4's format-expansion code) settled that a
# per-window resize call is NEVER part of this feature: setting the
# surviving default-size OPTION does not disturb any attached client's
# current window size, and resizing a window in place buys nothing new
# windows don't already get from `default-size` on their own -- see
# TestTmuxWindowSizeNoResize for the structural, whole-file lock (the
# exact tmux subcommand name is deliberately not spelled out here so this
# comment can't ever collide with that lock).
#
# #267: raising history-limit only fixed how much scrollback SURVIVES --
# the user's live complaint ("neviem sa v tom pretacat, kolieskom cez ssh
# sa to blbo pouziva") was that reaching it needs a MOUSE (tmux's default
# scroll-wheel-into-copy-mode binding), which is awkward over ssh, and the
# user explicitly asked for the keyboard shortcut old Linux virtual
# consoles used: Shift+PageUp/PageDown. `bind-key -n S-PageUp copy-mode
# -eu` (root table, no prefix key) enters copy-mode and scrolls up one
# page in one keystroke; `-e` auto-exits copy-mode the moment the user
# scrolls back down to the bottom, so Shift+PageDown alone (bound in BOTH
# copy-mode key tables, vi and emacs, since the managed conf pins neither
# `mode-keys` setting) returns to the live view -- matching the user's own
# "Shift+PgDn / navrat na spodok vrati live view" acceptance line.
# UNLIKE window-size/default-size above, a `bind-key` call is SAFE to
# live-apply against a running server: it only registers a key-table
# entry -- it does not touch any window's geometry, force a
# recalculate_sizes() pass, or read/write anything CC's renderer has
# already drawn, so it carries none of #236's live-apply hazard. Verified
# live (#267): bound against a real running server, then driven through a
# REAL attached pty client sending the actual xterm CSI byte sequences for
# Shift+PageUp/PageDown (`send-keys -t <pane>` alone does NOT exercise a
# key-table binding -- it writes bytes straight into the pane's pty,
# bypassing the server's key dispatch entirely; only a genuinely attached
# client's input passes through the binding tables) -- Shift+PageUp
# correctly entered copy-mode and scrolled up, Shift+PageDown correctly
# scrolled back down and auto-exited to the live view, with the pane's
# own content completely undisturbed throughout.
#
# #254: each attach to a tmux session-GROUP (e.g. zbynek-1..4, all sharing
# the same underlying windows -- the shape a grouped `new-session -t`
# attach produces) left the detached duplicate orphaned forever under
# tmux's factory-default `destroy-unattached off` -- reproduced live on
# dev1 against the real default socket with a genuine pty-attached-then-
# detached grouped sibling (STILL-VALID evidence on #254). Fix:
# `destroy-unattached keep-last` (see TMUX_DESTROY_UNATTACHED above for
# why NOT the ticket's own literally-named keep-group). UNLIKE window-size
# above, this is safe to LIVE-APPLY for a different reason: by
# definition it only ever evaluates sessions with ZERO attached clients,
# so it structurally cannot disturb anything currently on screen. Verified
# live: applying it against a running server holding a pre-existing
# pile-up (one attached session, two already-detached grouped duplicates
# -- the exact zbynek-1/2/3/4 shape before manual cleanup) immediately
# swept the two duplicates away with no new attach/detach cycle needed,
# while leaving the attached grouped session AND a separate attached
# standalone session completely untouched. This also answers "how do
# already-piled-up siblings get cleaned": the live-apply itself performs
# a one-time sweep on the very next push/install -- no new hook, no new
# watchdog job needed.
#
# ADVERSARIAL-REVIEW FINDING (#254, MINOR): live-apply-safe and conf-
# read-safe are INDEPENDENT claims (#236 vs #241's own lesson for
# window-size -- one option was unsafe live-applied, the OTHER unsafe
# merely READ from a conf file at server startup). This block's own live-
# apply proof above was run against tmux 3.7b; the cold conf-PARSE half
# was separately verified clean on BOTH the fleet's stock tmux 3.4 (the
# only version Ubuntu 24.04 noble ships, and the live server on any box
# not yet rebooted through #242's cutover) and 3.7b -- `set-option -g
# destroy-unattached keep-last` in a conf file starts cleanly on both, and
# a live `set-option` against a running 3.4 server also succeeds. No
# #241-shaped crash-at-parse-time hazard on either binary.
#
# Pane addressing (verified, not assumed): every keystroke-sending job in
# watchdog/__init__.py (list_claude_panes/_reconcile_candidate_panes)
# addresses panes exclusively by tmux's stable `#{pane_id}` (`%N`,
# server-global, independent of which session name currently references
# the underlying window -- their own docstrings already say "grouped
# sessions share the same pane_id"). `_pane_location()` (which renders a
# `session:window.pane` string like the `zbynek-4:2.0` the ticket cites)
# is used PURELY as human-readable text interpolated into log lines,
# never as a `tmux -t` target -- so destroying a detached grouped
# sibling's session name can never break pane resolution.


# #267: the three Shift+PgUp/PgDn keyboard-scrollback bindings, as tmux
# argv lists -- shared verbatim between the rendered conf lines
# (render_tmux_history_block, below) and the live-apply calls
# (apply_tmux_history_limit) so the two can never drift apart. A `bind-key`
# call is a pure key-table registration -- see the incident-history comment
# above for why that makes it safe to live-apply, unlike window-size/
# default-size.
#
# #338: the user repeatedly asked whether Claude Code's OWN native
# transcript viewer (Ctrl+O, CC v2.1.226+ -- reads the session's clean
# internal history, immune to the tmux-scrollback frame-duplication defect
# #267's own bind only ever scrolls INTO; PgUp/PgDn already work natively
# once it's open, no further wiring needed) could be reached via the same
# Shift+PageUp muscle memory #267 already trained. S-PageUp is now
# CONDITIONAL, via tmux's own `if -F` (alias of `if-shell -F`): inside a
# pane whose `pane_current_command` is literally `claude` it sends `C-o`;
# everywhere else it falls through to the ORIGINAL, byte-identical
# `copy-mode -eu`. `if -F`'s format string is evaluated by tmux PER
# KEYPRESS against the CURRENT client's pane, never once at conf-parse/
# bind time -- verified LIVE (not read from docs): a real attached pty
# client fed the real xterm CSI bytes for Shift+PageUp (`\x1b[5;2~`)
# against two different panes bound to the SAME key on an isolated
# scratch server -- a pane whose `pane_current_command` was `claude` (a
# real fixture: `bash -c 'stty raw -echo; exec -a claude cat > <file>'` --
# the `stty raw -echo` is load-bearing, a canonical-mode pty buffers a
# lone control byte with no trailing newline and never delivers it)
# received exactly one byte `0x0f` (Ctrl+O); a plain `sleep` pane on the
# identical bind entered copy-mode (`#{pane_in_mode}` flipped 0->1),
# unchanged from #267's own pre-#338 behaviour. `tmux send-keys -t <pane>`
# was deliberately NEVER used to exercise the binding itself -- it
# bypasses key-table dispatch and writes straight into the pty, proving
# nothing about whether a real keypress reaches the bound command (see
# the incident-history comment above TMUX_SCROLLBACK_KEYBINDS' own #267
# entry for the same lesson). Also confirmed live: the RENDERED
# (`_tmux_conf_quote`d) conf line starts cleanly from a COLD conf file,
# and live-applies cleanly against an already-running server, on BOTH the
# fleet's real deployed `/usr/bin/tmux` (3.4) and `/usr/local/bin/tmux`
# (3.7b) -- no crash-at-parse-time hazard of the `window-size manual`
# (#241) kind. `S-NPage` (Shift+PageDown) is deliberately left untouched
# -- no existing root-table bind, and the native viewer's own PgDn already
# works once it's open.
#
# This is the FIRST entry whose argv holds multi-word NESTED-COMMAND
# tokens (`"send-keys C-o"`, `"copy-mode -eu"`, each one single tmux
# argument tmux itself re-parses as an embedded command string) -- live
# apply passes them straight through subprocess argv (no shell, no
# quoting needed), but the RENDERED conf line now needs the same
# per-token `_tmux_conf_quote` the popup binds (TMUX_POPUP_BIND_ARGVS)
# already use, or the unquoted "send-keys C-o" would parse as FOUR
# separate tmux words instead of one, silently corrupting the `if -F`
# command's own argument count (see render_tmux_history_block below).
TMUX_SCROLLBACK_KEYBINDS = [
    ["bind-key", "-n", "S-PageUp", "if", "-F",
     "#{==:#{pane_current_command},claude}",
     "send-keys C-o", "copy-mode -eu"],
    ["bind-key", "-T", "copy-mode", "S-PageDown", "send-keys", "-X", "page-down"],
    ["bind-key", "-T", "copy-mode-vi", "S-PageDown", "send-keys", "-X", "page-down"],
]


# #289: a one-keystroke POPUP over `claude-history` (#267's companion --
# reads the session TRANSCRIPT, immune to the tmux frame-stacking defect
# S-PageUp above merely scrolls INTO). Root problem this closes: #267
# shipped claude-history but gave the user no discoverable path to it from
# a running session; #289 was reopened because nobody ever typed the bare
# command.
#
# KEY CHOICE (engineer's call, ask-before-assuming.md -- an internal/
# diagnostic element's placement has no user stake): originally Shift+F1
# (`S-F1`), root table, no prefix -- REMOVED by #376 (never confirmed to
# reach the user's real terminal/ssh client; see the module comment above
# TMUX_POPUP_PREFIX_KEY). `prefix + h` (mnemonic: history) -- unbound in
# stock tmux's prefix table (verified live, `-f /dev/null` throwaway
# socket) -- is the ONE surviving binding: the only one the user
# personally confirmed opens.
#
# MECHANISM: `display-popup`'s own SHELL-COMMAND argument is NOT format-
# expanded by tmux (verified live, tmux 3.7b: a literal `#{pane_id}`
# inside the command string reaches the shell UNSUBSTITUTED). `-d`
# (start-directory) IS format-expanded (verified live the same way) --
# so `-d '#{pane_current_path}'` puts the popup's shell in the
# ORIGINATING PANE's own cwd, and claude-history's own `--cwd` default
# (`os.getcwd()`) then resolves the right project with no `--pane`
# argument needed at all. The popup invokes the POPUP SCRIPT
# (CLAUDE_HISTORY_POPUP_SCRIPT_DEST, an absolute path baked in at Python
# render time) directly -- never the `claude-history` bashrc FUNCTION,
# since `display-popup` runs its shell-command non-interactively and
# `~/.bashrc` (where the function lives) is never sourced.
#
# CAPTURE-PANE RESOLUTION (#337, used by the popup's own capture-pane
# fallback -- the ONLY consumer as of #376, since S-DC's mode-only path is
# gone -- see CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT below): a bare
# (no `-t`) `tmux capture-pane` call issued from WITHIN a display-popup
# job's own shell-command resolves against the ORIGINATING pane -- the
# one the popup key was actually pressed in -- never the popup's own
# freshly-created pseudo-pane. Verified live, twice, independently, on
# both tmux 3.4 and 3.7b: an isolated multi-window/multi-client server
# with the raw popup-key bytes injected into a real attached pty client
# confirmed the resolution correctly follows whichever client pressed
# the key, including a run where the popup job's own `$TMUX_PANE`
# carried the popup's OWN pane id (e.g. `%3`) while the bare capture
# still returned the ORIGINATING pane's content -- proof this does not
# rely on `$TMUX_PANE` at all. Two proven ways to BREAK this
# resolution, never do either: adding `-c <client>` to `display-popup`
# (routes to the wrong session's pane when the popup is opened from an
# outside command client), or invoking the script via `tmux run-shell`
# instead of directly as the popup's own shell-command.
#
# ADVERSARIAL-REVIEW-CLASS FINDING (self-caught via live verification,
# #289): the shell-command argument was ORIGINALLY inlined directly on
# this bind-key line (a `CH_OUT=$(...); CH_RC=$?; if [ "$CH_RC" -ne 0 ]
# ...` one-liner) -- and it silently produced `if [ "" -ne 0 ]` at
# runtime (confirmed via `list-keys` on the ACTUAL bound command, and via
# a real S-F1 keypress through a genuinely attached pty client reading
# the raw popup overlay bytes -- `capture-pane` does NOT see a popup's
# content at all, since it is a client-side rendering overlay, never part
# of any pane's own buffer). Root cause: tmux's OWN conf-file DOUBLE-QUOTE
# parser EXPANDS `$VAR` at CONF-PARSE/bind time (using tmux's own process
# environment), not at shell-run time -- `$CH_OUT`/`$CH_RC`/`$?` don't
# exist in THAT environment, so they were silently blanked to empty
# string before the shell that eventually ran the command ever saw them.
# Single-quoted tmux strings do NOT expand `$VAR` (also verified live),
# but tmux's own single-quote parsing supports no escapes at all, so
# embedding this command's own several `printf '%s...'` single quotes
# would need the POSIX quote-splice idiom on every one of them (tmux DOES
# honour `'...'\''...'`, verified live) -- fragile, easy to get wrong, and
# exactly the class of hand-spliced-quoting bug this repo's own playbook
# already flags. Moving the logic into its OWN script file, invoked by
# absolute path, sidesteps the whole landmine: the only thing the
# bind-key line needs to resolve is the path itself, which needs no `$VAR`
# to survive tmux's conf parser at all. See CLAUDE_HISTORY_POPUP_SCRIPT_DEST.
#
# FAIL LOUDLY, NEVER SILENTLY: claude-history exits nonzero with a clear
# stderr message when no transcript exists for the resolved cwd (#267's
# own behavior, unchanged). A bare `claude-history | less` would then
# hand `less` empty stdin, which can close instantly with nothing to
# read -- the exact "no silent instant-close" failure this ticket's own
# acceptance forbids. The popup script captures claude-history's output +
# exit code explicitly and, on failure, prints the error and waits for a
# keypress instead of piping into `less` at all.
#
# LIVE-APPLY SAFETY: a `bind-key` call is a pure key-table registration
# (see the #267 comment above `TMUX_SCROLLBACK_KEYBINDS` for the full
# argument -- no window geometry read or written, nothing already
# rendered by the CC TUI touched) -- identical safety class to the
# S-PageUp/PageDown binds, so the popup bind is live-applied the same
# way, never conf-only.
#
# #376 CLEANUP: `S-F1` (root-table, never confirmed to reach the user's
# actual terminal/ssh client -- #294's own Windows-notebook report) and
# `S-DC` (root-table, confirmed delivered but explicitly downgraded by
# the user's own binding correction: "garantovaná skratka = výhradne
# prefix trieda; žiadna nová skratka bez potvrdeného doručenia") are
# REMOVED. `prefix+h` below is the ONE surviving binding -- the only one
# the user personally confirmed opens -- and this popup is now a FALLBACK
# for cross-session/closed-pane history only, not the primary answer
# (fullscreen rendering is -- see apply_managed_settings_defaults). The
# `#294`/`#337` research that picked S-F1/S-DC (candidate keys
# considered, Windows-client encoding evidence) is preserved in this
# ticket's own history/playbook, not repeated here since neither key
# survives to be re-derived from it.
TMUX_POPUP_PREFIX_KEY = "h"


def _tmux_popup_bind_argv(key, in_prefix_table):
    """The `bind-key ... display-popup ...` argv for `key` -- `-n` (root
    table, no prefix) when `in_prefix_table` is False, omitted (default
    "prefix" table) otherwise. Shared verbatim between the live-apply
    subprocess call (a plain argv list, no shell involved -- each element
    is already exactly one tmux token) and the rendered conf line (which
    needs REAL quoting, see `_tmux_conf_quote` -- unlike
    TMUX_SCROLLBACK_KEYBINDS, none of THESE tokens contain spaces, but
    `#{pane_current_path}` contains a literal `#`, which would start a
    tmux COMMENT if left unquoted at the start of a conf line -- the
    quoting here is load-bearing for THAT character, not for whitespace).
    The invoked command is the POPUP SCRIPT's own ABSOLUTE PATH (baked in
    at Python render time -- see the module comment above
    TMUX_POPUP_PREFIX_KEY for why this, not an inline shell command, is
    the safe shape). #376 dropped the `mode` parameter this function used
    to take (#337) -- the popup script is unconditional now, so there is
    nothing left to select between and no `-e AIRULESET_POPUP_MODE=`
    flag to build."""
    argv = ["bind-key"]
    if not in_prefix_table:
        argv.append("-n")
    argv += [key, "display-popup", "-E", "-w", "96%", "-h", "96%"]
    argv += ["-d", "#{pane_current_path}", "-T", "claude-history",
             str(CLAUDE_HISTORY_POPUP_SCRIPT_DEST)]
    return argv


TMUX_POPUP_BIND_ARGVS = [
    _tmux_popup_bind_argv(TMUX_POPUP_PREFIX_KEY, in_prefix_table=True),
]


def _tmux_conf_quote(word):
    """Quote a single conf-line WORD (argv element) for tmux's OWN config
    parser. `#{...}` format expansion works the same way quoted or bare.
    ADVERSARIAL-REVIEW FINDING (#289, M1): a literal `$VAR` is EXPANDED by
    tmux's OWN conf-parser at conf-parse/bind time -- using tmux's OWN
    process environment, NOT the shell's -- both INSIDE a tmux double-
    quoted string AND when left bare/unquoted (verified live; this is the
    exact landmine the module comment above TMUX_POPUP_PREFIX_KEY documents this
    ticket self-finding and fixing by moving shell logic into its own
    script file). No quoting form in THIS function protects a literal `$`
    from that expansion, so a word containing one is REFUSED outright
    rather than silently mis-rendered -- a future conf-line author needing
    a real shell-runtime variable must move it into a separate script file
    invoked by absolute path instead (see CLAUDE_HISTORY_POPUP_SCRIPT_DEST).
    For every other case this only escapes what tmux itself needs escaped
    (`\\` and `"`); single quotes need no escaping inside a tmux double-
    quoted string, but DO need quoting when they appear in an otherwise-
    bare word (an unquoted `'` starts real single-quote mode in tmux's own
    grammar too, per M2)."""
    if "$" in word:
        raise ValueError(
            "_tmux_conf_quote: refusing to render literal '$' in %r -- "
            "tmux's own conf-parser expands $VAR at conf-parse/bind time "
            "(both quoted and unquoted, verified live) and no quoting form "
            "here protects a literal '$' from that. Move logic needing a "
            "real shell-runtime variable into its own script file, invoked "
            "by absolute path, instead." % (word,)
        )
    if word and not re.search(r'[\s"\\;#\']', word):
        return word
    escaped = word.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def render_tmux_history_block(limit=TMUX_HISTORY_LIMIT,
                               default_size=TMUX_DEFAULT_SIZE,
                               destroy_unattached=TMUX_DESTROY_UNATTACHED):
    # #338: per-token _tmux_conf_quote (not a bare " ".join) -- required
    # the moment the S-PageUp entry's `"send-keys C-o"`/`"copy-mode -eu"`
    # nested-command tokens (each ONE tmux argv element, containing a
    # space) need to survive as single words in the rendered conf line.
    # No-op for the two S-PageDown entries (no token there needs quoting).
    keybind_lines = "\n".join(
        " ".join(_tmux_conf_quote(tok) for tok in argv)
        for argv in TMUX_SCROLLBACK_KEYBINDS)
    popup_lines = "\n".join(
        " ".join(_tmux_conf_quote(tok) for tok in argv)
        for argv in TMUX_POPUP_BIND_ARGVS)
    return (
        f"{TMUX_MARK_START}\n"
        f"set-option -g history-limit {limit}\n"
        f"set-option -g destroy-unattached {destroy_unattached}\n"
        f"set-option -g default-size {default_size}\n"
        f"{keybind_lines}\n"
        f"{popup_lines}\n"
        f"{TMUX_MARK_END}"
    )


def _clean_tmux_block_spans(existing):
    """[(start, end)] for every CLEAN (non-crossing) START...END marker
    pair in `existing`, left to right. "Clean" means no OTHER marker
    literal (START or END) falls strictly between a pair's own START and
    its END -- this deliberately refuses to treat an externally-corrupted
    or reordered marker set (e.g. END appearing before START) as a
    replaceable block.

    Why this matters (#235 adversarial-review finding): a naive whole-file
    `START.*?END` regex would, on a LATER run once a fresh clean block has
    been appended after a stray/orphaned marker, span from the stray
    marker all the way to the fresh block's END -- silently deleting every
    real tmux directive sitting in between. This left-to-right, position-
    tracking scan can never produce that outcome, at any point across any
    number of runs: an unpaired or crossed marker is simply skipped over
    and left as inert literal text, never merged with anything else."""
    spans = []
    pos = 0
    s_len = len(TMUX_MARK_START)
    while True:
        s = existing.find(TMUX_MARK_START, pos)
        if s == -1:
            break
        e = existing.find(TMUX_MARK_END, s + s_len)
        if e == -1:
            pos = s + s_len  # no END anywhere after this START -- skip it
            continue
        inner = existing[s + s_len:e]
        if TMUX_MARK_START in inner or TMUX_MARK_END in inner:
            pos = s + s_len  # another marker crosses this pair -- not clean
            continue
        e_full = e + len(TMUX_MARK_END)
        spans.append((s, e_full))
        pos = e_full
    return spans


def _default_tmux_run(argv):
    import subprocess
    return subprocess.run(argv, capture_output=True, text=True, timeout=8)


def apply_tmux_history_limit(tmux_conf_path: Path = None, limit: int = TMUX_HISTORY_LIMIT,
                              default_size: str = TMUX_DEFAULT_SIZE,
                              destroy_unattached: str = TMUX_DESTROY_UNATTACHED,
                              run=None) -> bool:
    """Ensure `~/.tmux.conf` carries the managed tmux block: history-limit
    (#235), destroy-unattached (#254), and default-size (#236).
    `window-size manual` was REMOVED again by #241 -- it crashes tmux
    3.4's server outright at startup, so it is never emitted here at all,
    conf-only or otherwise (see the module-level comment above
    `render_tmux_history_block` for the full incident history and the
    `default-size`-alone trade-off this leaves).

    Idempotent marker block: create the file if absent, rewrite ONLY the
    block's CONTENT in place if a clean pair of markers already exists
    (never touches anything outside them, byte-for-byte -- see
    `_clean_tmux_block_spans`), no-op on a second run with nothing
    changed. Returns True iff the conf file's bytes changed.

    Also live-applies history-limit on any RUNNING tmux server for this
    user (`tmux set-option -g history-limit N`), exactly #235's original,
    already-shipped, already-proven-safe scope: an already-running
    session's NEW panes/windows pick it up immediately, without waiting
    for the next server start; EXISTING panes keep their creation-time
    limit (tmux has no way to grow an existing pane's history buffer in
    place). This is a server OPTION set, never a keystroke into any pane.

    #254: destroy-unattached is ALSO live-applied, right after
    history-limit -- unlike window-size/default-size it only ever
    evaluates sessions with ZERO attached clients, so it structurally
    cannot disturb anything currently on screen (verified live: see the
    module comment above `render_tmux_history_block`). Live-applying it
    is what immediately self-heals any ALREADY-existing detached grouped
    pile-up (e.g. zbynek-1/2/3 while zbynek-4 stays attached) on the very
    next push, with no new hook and no new watchdog job needed -- tmux's
    own destroy-unattached evaluation re-fires on every future detach
    from then on.

    default-size is DELIBERATELY CONF-ONLY -- never live-applied via a
    real tmux subprocess call, in any code path. It lands in the conf
    file above, so it takes effect for the NEXT server/session/window --
    existing attached sessions simply keep tmux's factory default
    (`80x24`) until that server is next restarted, mirroring the
    identical, already-accepted trade-off this ticket made for a
    per-window resize call itself. See TestTmuxWindowSizeRemoved for the
    lock, and TestTmuxWindowSizeNoResize for the separate, unrelated
    per-window-resize / format-expansion-query lock.

    #267: the three `TMUX_SCROLLBACK_KEYBINDS` (Shift+PgUp/PgDn) are ALSO
    live-applied, unlike default-size -- a `bind-key` call only registers
    a key-table entry, so it carries none of window-size's live-apply
    hazard (see the module comment above `render_tmux_history_block`).
    Each keybind is attempted independently of the others and of the
    history-limit call above: a failure/nonzero-exit on one never skips
    the rest, so a session that has already reached a running server
    picks up the keyboard scrollback shortcut immediately, with no
    restart and no keystroke sent to any pane.

    #289: the `TMUX_POPUP_BIND_ARGVS` (prefix-h, the one surviving popup
    fallback binding as of #376 -- see the module comment above
    `TMUX_POPUP_PREFIX_KEY`) is live-applied the SAME way, for the SAME
    reason -- a `bind-key` call is a pure key-table registration,
    independent of and no riskier than the scrollback keybinds it sits
    alongside.

    `run` defaults to a real `tmux` invocation and is injectable so tests
    never touch a real tmux server. A missing server / a nonzero exit
    (which `subprocess.run` does NOT raise on without `check=True` -- a
    real `tmux set-option` against a dead socket exits 1 silently) / any
    other failure is logged and ignored, never raised, never affecting the
    conf-file write result above -- mirroring the ticket's own "ignore
    failure when no server" acceptance."""
    path = tmux_conf_path or TMUX_CONF
    block = render_tmux_history_block(limit, default_size, destroy_unattached)

    existing = path.read_text() if path.exists() else ""
    spans = _clean_tmux_block_spans(existing)
    if spans:
        out, cursor = [], 0
        for s, e in spans:
            out.append(existing[cursor:s])
            out.append(block)
            cursor = e
        out.append(existing[cursor:])
        new = "".join(out)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{block}\n"
    changed = new != existing
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)

    runner = run or _default_tmux_run
    live_argvs = [
        ["tmux", "set-option", "-g", "history-limit", str(limit)],
        ["tmux", "set-option", "-g", "destroy-unattached", str(destroy_unattached)],
    ]
    live_argvs += [["tmux"] + argv for argv in TMUX_SCROLLBACK_KEYBINDS]
    live_argvs += [["tmux"] + argv for argv in TMUX_POPUP_BIND_ARGVS]
    # #376 CLEANUP: an ALREADY-RUNNING server that was live-bound before
    # this fix deployed still has S-F1/S-DC registered -- rewriting the
    # CONF file (above) does not retroactively unbind an already-live
    # key-table entry, and `live_argvs` above only ever ADDS bindings, it
    # never removes stale ones. `unbind-key` on a key that was never
    # bound is a documented tmux no-op (rc 0), so this is safe to run
    # unconditionally on every box, whether or not it ever had them.
    live_argvs += [["tmux", "unbind-key", "-n", "S-F1"],
                   ["tmux", "unbind-key", "-n", "S-DC"]]
    for argv in live_argvs:
        try:
            result = runner(argv)
            rc = getattr(result, "returncode", 0)
            if rc:
                stderr = (getattr(result, "stderr", "") or "").strip()
                print(f"  tmux live-apply skipped (rc={rc}): "
                      f"{stderr or 'no server running?'}", file=sys.stderr)
        except Exception as e:
            # No server running / tmux missing from PATH / timeout, etc. --
            # expected and harmless (a new server reads the conf file we
            # just wrote anyway); logged for visibility, never re-raised,
            # and never affects the conf-file write result above. Each
            # call is independently guarded so one failure never skips
            # the rest (#267).
            print(f"  tmux live-apply skipped (non-fatal): {e}", file=sys.stderr)

    return changed


# ---------------------------------------------------------------------------
# tmux boot-time cutover (#242) -- points /usr/local/bin/tmux at the newest
# managed tmux build (tmux-3.7b) at the box's own NEXT boot, so the client
# and server binary always match. #240/#241: repointing the symlink while a
# tmux SERVER is live breaks every attach ("server exited unexpectedly"),
# and at boot no server exists yet -- the only moment a flip is provably
# safe for a box whose server is already live today (dev2/gatekeeper/
# subdev). This is what actually unlocks #236's fixed-geometry goal:
# `window-size manual` crashes tmux 3.4's server outright at startup
# (#241), starts cleanly on 3.7b.
#
# System-level (root-owned /etc/systemd/system + /usr/local/bin), unlike
# every OTHER airuleset-managed unit (file-drop/api-watchdog are --user).
# ---------------------------------------------------------------------------

TMUX_CUTOVER_UNIT_NAME = "airuleset-tmux-cutover.service"
TMUX_CUTOVER_SCRIPT_DEST = "/usr/local/bin/airuleset-tmux-cutover.sh"
TMUX_CUTOVER_SERVICE_DEST = "/etc/systemd/system/" + TMUX_CUTOVER_UNIT_NAME
TMUX_CUTOVER_SERVICE_TEMPLATE = REPO_DIR / "settings" / "tmux-cutover.service.template"
# tmux-3.7b is the only extra build any managed box carries today (#242); a
# future newer build is a distinct ticket with its own compatibility check
# (like #241 for 3.4), not something a silent "highest version wins" glob
# should decide -- deliberately hardcoded, not generalized.
TMUX_CUTOVER_NEWEST = "/usr/local/bin/tmux-3.7b"

# Env-var overrides (unset in production -- the defaults above always apply
# there) exist ONLY so this script's LOGIC can be exercised by a real `sh`
# subprocess against a throwaway sandbox in tests, instead of only ever
# being proven by string-matching its source.
TMUX_CUTOVER_SCRIPT_CONTENT = """#!/bin/sh
# airuleset-managed (do NOT edit) -- boot-time tmux symlink cutover (#242).
# Idempotently points /usr/local/bin/tmux at the newest managed tmux build
# present on this box. Runs once at boot, before any tmux server can exist
# -- see airuleset-tmux-cutover.service's own ordering (Before=sysinit.target
# ssh.service ssh.socket, DefaultDependencies=no) -- so it can never run
# while a server using the OLD binary is already live.
set -eu

NEWEST="${AIRULESET_TMUX_CUTOVER_NEWEST:-/usr/local/bin/tmux-3.7b}"
TARGET="${AIRULESET_TMUX_CUTOVER_TARGET:-/usr/local/bin/tmux}"

# No 3.7b build on this box (yet), or it isn't runnable (a truncated /
# interrupted copy, wrong permissions) -- leave the packaged binary alone.
# -x (not -e): a present-but-non-executable NEWEST must never become the
# boot-time target (review finding, #242).
if [ ! -x "$NEWEST" ]; then
    exit 0
fi

CURRENT=""
if [ -L "$TARGET" ]; then
    CURRENT=$(readlink "$TARGET")
fi

# Already correct -- no-op. This is what makes a re-run at any later boot,
# or a box that is already on 3.7b, safe: the symlink is only ever touched
# when it is actually stale.
if [ "$CURRENT" != "$NEWEST" ]; then
    ln -sfn "$NEWEST" "$TARGET"
fi
"""

# subdev's four stream accounts (montalu/marek/david/simap) have no sudo at
# all and share ONE box + ONE symlink -- root there is reachable ONLY from
# the gatekeeper VPS (never dev1), via this identity. Its mere PRESENCE is
# the discriminator for "am I the gatekeeper box" (mirrors #68's own
# identity-based trust in block-subdev-ssh-misuse.sh) -- never a
# hostname/whoami guess.
SUBDEV_ADMIN_IDENTITY = Path.home() / ".ssh" / "subdev_admin"


def _sudo_write_root_file(run, content, dest, mode):
    """Write `content` to root-owned `dest` LOCALLY via `sudo -n tee` +
    `sudo -n chmod` -- never an interactive password. Returns (ok, err)."""
    w = run(["sudo", "-n", "tee", dest], input=content,
            capture_output=True, text=True, timeout=15)
    if w.returncode != 0:
        return False, f"write {dest} failed: {(w.stderr or '').strip()}"
    c = run(["sudo", "-n", "chmod", mode, dest],
            capture_output=True, text=True, timeout=15)
    if c.returncode != 0:
        return False, f"chmod {dest} failed: {(c.stderr or '').strip()}"
    return True, None


def setup_tmux_cutover_provisioning(run=None):
    """Install the boot-time tmux symlink cutover unit on THIS box (#242).

    Non-interactive (`sudo -n`) throughout, matching check_runtime_deps's own
    "install what you can, skip loudly what you can't" shape: the four subdev
    stream accounts (montalu/marek/david/simap) have no sudo AT ALL -- probed
    up front and skipped with an expected, non-alarming reason, because the
    ONE shared box+symlink they sit behind is provisioned instead by
    `setup_tmux_cutover_subdev_via_gatekeeper` (the gatekeeper account's own
    `install` run performs that root hop).

    Rewrites the script + unit UNCONDITIONALLY on every call (same shape as
    apply_ultracode_launcher's own claude-launcher script -- cheap, and the
    content is a pure function of fixed constants, so a same-content rewrite
    is a true no-op on disk) and (re)enables the unit -- but NEVER starts it.
    Starting it now would flip the symlink under a POSSIBLY-LIVE tmux server;
    the actual flip only ever happens at the box's own NEXT boot, when no
    server can exist yet (see the shipped unit's own ordering). Running the
    unit/script directly at ANY time (including a manual `systemctl start`
    used to prove idempotency) is still provably safe on a box already on
    3.7b: the script's own compare-then-skip is what makes that true, not
    merely "we never invoke it".

    Returns (ok: bool, reason: str|None) -- reason is set only when this
    account genuinely cannot do it (the expected subdev-stream case) or a
    real command failed."""
    import subprocess
    run = run or subprocess.run

    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10)
        has_sudo = probe.returncode == 0
    except Exception:
        has_sudo = False
    if not has_sudo:
        return False, "no NOPASSWD sudo on this account (expected on the subdev stream accounts)"

    if not TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        return False, f"missing unit template: {TMUX_CUTOVER_SERVICE_TEMPLATE}"
    unit_content = TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()

    for content, dest, mode in (
        (TMUX_CUTOVER_SCRIPT_CONTENT, TMUX_CUTOVER_SCRIPT_DEST, "755"),
        (unit_content, TMUX_CUTOVER_SERVICE_DEST, "644"),
    ):
        ok, err = _sudo_write_root_file(run, content, dest, mode)
        if not ok:
            return False, err

    dr = run(["sudo", "-n", "systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=20)
    if dr.returncode != 0:
        return False, f"daemon-reload failed: {(dr.stderr or '').strip()}"
    en = run(["sudo", "-n", "systemctl", "enable", TMUX_CUTOVER_UNIT_NAME],
            capture_output=True, text=True, timeout=20)
    if en.returncode != 0:
        return False, f"enable failed: {(en.stderr or '').strip()}"

    return True, None


def setup_tmux_cutover_subdev_via_gatekeeper(run=None, identity_path: Path = None):
    """From the gatekeeper account ONLY, root-hop into the shared subdev VPS
    (`ssh -i ~/.ssh/subdev_admin root@subdev`) and install the SAME cutover
    unit there -- ONE root-level install covers all FOUR subdev stream
    accounts (montalu/marek/david/simap), which share one box and one
    /usr/local/bin/tmux symlink and individually have no sudo (see
    setup_tmux_cutover_provisioning's own no-op there). Root@subdev is
    reachable ONLY from gatekeeper, never from dev1 (machine-identities.md)
    -- which is why this is a distinct function rather than one more
    REMOTE_HOSTS deploy entry: root there is not one of the managed
    per-account checkouts `install` normally runs against.

    A true no-op on every box that isn't gatekeeper (dev1/dev2/the subdev
    accounts themselves never carry the identity file). Never starts the
    remote unit, for the identical live-server-safety reason as the local
    path above -- the remote's own next reboot is what actually flips it.

    Returns (ok: bool, reason: str|None)."""
    import subprocess
    run = run or subprocess.run
    identity = identity_path or SUBDEV_ADMIN_IDENTITY

    if not identity.exists():
        return False, "not the gatekeeper box (no subdev_admin identity) -- skipped"

    if not TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        return False, f"missing unit template: {TMUX_CUTOVER_SERVICE_TEMPLATE}"
    unit_content = TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()

    ssh_prefix = ["ssh", "-i", str(identity),
                  "-o", "StrictHostKeyChecking=no", "root@subdev"]

    for content, dest, mode in (
        (TMUX_CUTOVER_SCRIPT_CONTENT, TMUX_CUTOVER_SCRIPT_DEST, "755"),
        (unit_content, TMUX_CUTOVER_SERVICE_DEST, "644"),
    ):
        w = run(ssh_prefix + [f"tee {dest} >/dev/null && chmod {mode} {dest}"],
                input=content, capture_output=True, text=True, timeout=25)
        if w.returncode != 0:
            return False, f"write {dest} on subdev failed: {(w.stderr or '').strip()}"

    dr = run(ssh_prefix + ["systemctl daemon-reload"],
            capture_output=True, text=True, timeout=25)
    if dr.returncode != 0:
        return False, f"daemon-reload on subdev failed: {(dr.stderr or '').strip()}"
    en = run(ssh_prefix + [f"systemctl enable {TMUX_CUTOVER_UNIT_NAME}"],
            capture_output=True, text=True, timeout=25)
    if en.returncode != 0:
        return False, f"enable on subdev failed: {(en.stderr or '').strip()}"

    return True, None


def apply_managed_settings_defaults(settings: dict) -> dict:
    """Ensure airuleset's managed settings defaults are present (non-hook keys).

    - `effortLevel = xhigh` so deep adaptive reasoning is the persistent default in
      every managed project without the user remembering to raise it. The user can
      still override per session with `/effort`.
    - `disableAgentView = true` HARD-disables Claude Code's `claude agents` / fleet /
      `claude --bg` background daemon (the on-demand supervisor that spawns DETACHED
      background sessions which SURVIVE `/exit` and keep running/pinging untracked).
      The user runs explicit interactive `claude` in tmux and wants NO unmanaged
      background Claude — incident: a fleet session ran 2.9 days and kept pinging
      after the user `/exit`-ed it. Equivalent to env `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`.
      This does NOT affect in-session `run_in_background` subagents (the agent strip /
      autopilot-worker) — those are a separate, session-scoped mechanism that dies
      with the session. Takes effect on the NEXT `claude` launch.

    - `tui = "fullscreen"` (#376, REVERSING the earlier `tui = "default"` pin) pins
      Claude Code's fullscreen (alt-screen) renderer fleet-wide. History: this
      function used to pin CLASSIC specifically because `Ctrl+B [` tmux-native
      scrollback goes EMPTY under fullscreen (nothing reaches tmux's own scrollback
      by design — david@gatekeeper 2026-07-09). But CLASSIC's own failure mode is
      worse and is what #376 was actually filed about: classic draws into tmux's
      NATIVE scrollback, which a resize/relayout event duplicates/loses bands of
      (upstream anthropics/claude-code#84247 + #46834, both confirmed still OPEN
      2026-08-11) — on tmux <3.6 (no synchronized output) this is routine, not
      rare; the fleet is NOT uniformly on an old build here (dev2/gk/subdev run
      3.4, but dev1 itself runs 3.7b — the corruption was live-reproduced on
      dev1's own 3.7b, so this is not purely a pre-3.6 problem, just a WORSE one
      there). Fullscreen keeps the WHOLE conversation in its OWN
      app-internal message list (`PgUp`/`PgDn` scroll it, `Ctrl+O` opens
      `/`-searchable transcript mode) — confirmed by Anthropic's own docs
      (code.claude.com/docs/en/fullscreen) to survive repeated compaction and to
      need no mouse (`PgUp`/`PgDn` alone reach it), which is what actually answers
      the complaint this ticket exists for. The `Ctrl+B [` regression is real and
      EXPECTED, not a bug in this change: `PgUp`/`PgDn` + `Ctrl+O` are fullscreen's
      documented replacement for it, not merely a workaround — verify this trade
      lands as intended on gk/david2 post-deploy (their long-running CLASSIC
      sessions need a relaunch/`/tui fullscreen` to pick this up — see below).
      Equivalent to env `CLAUDE_CODE_NO_FLICKER=1` (docs: "The `tui` setting and
      the environment variable are equivalent") — so #253's opt-in
      `claude-fullscreen` launcher mode is now redundant with this default (kept
      anyway, harmless, see CLAUDE_LAUNCH_SCRIPT_CONTENT's own comment). Takes
      effect on the NEXT `claude` launch — an ALREADY-RUNNING session needs a
      relaunch (or a manual `/tui fullscreen`) to switch, same latching this
      function's own `promptSuggestionEnabled` bullet documents for a different
      key.

    - `model = MANAGED_MODEL` (Opus 5[1m]) is the default MAIN-session model on
      every managed box (2026-07-25 cost-fix package, #37) — see MANAGED_MODEL's
      own comment for the measured evidence. Same unconditional-managed-default
      treatment as effortLevel/disableAgentView/tui; the user can still switch
      per session with `/model`.

    - `promptSuggestionEnabled = False` turns OFF Claude Code's predicted-next-
      prompt suggestion in the input box (#189). CC renders that suggestion as
      DIM (SGR 246) text after the `❯` glyph; `tmux capture-pane -p` strips
      attributes, so the watchdog's boundary classifiers see it as byte-identical
      to a draft the user typed, and every keystroke-sending job then refuses to
      act (or routes to a stash that has nothing to park). It was present on dev1
      only as an UNMANAGED local edit and absent on gatekeeper and montalu — a
      managed default so a push lands and self-heals it on every box. The key is
      real, not guessed: the installed 2.1.220 build carries it in the same
      global-settings key vector as effortLevel / autoCompactWindow / tui.
      NOTE this removes the SOURCE of the ambiguity, it is NOT the delivery fix —
      the value is latched at process init, so sessions already running keep
      rendering suggestions until they restart, which is precisely why
      `deliver_with_stash` was made independent of what the box appears to hold.

    - `autoCompactWindow` is ACTIVELY STRIPPED (2026-07-25 correction batch —
      reverts the SAME-DAY "krok 1c" addition). A low auto-compact threshold
      cuts big tasks off mid-work and defeats the 1M context window; context
      is bounded at ticket boundaries instead (the per-ticket `/compact`,
      watchdog job 14). This must POP the key, not merely stop setting it —
      an already-deployed settings.json from the reverted feature would
      otherwise keep carrying it forward untouched on every future install.

    - `env["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] = MANAGED_MAX_SUBAGENTS_PER_SESSION`
      (#288) raises the default 200-cumulative-spawn-per-session cap so a
      long-running /goal-armed autopilot session doesn't lose the Agent
      tool mid-day. Same unconditional-managed-default treatment as every
      other key above, applied fleet-wide (see MANAGED_MAX_SUBAGENTS_PER_SESSION's
      own comment for why no per-authority carve-out). Merges into any
      existing `env` sub-object rather than overwriting it, so a future
      feature that also needs an `env` key does not silently clobber this
      one (or vice versa).

    - `cleanupPeriodDays = MANAGED_CLEANUP_PERIOD_DAYS` (#376) overrides
      Claude Code's OWN native transcript-retention auto-cleanup (default
      30 days when unset -- see MANAGED_CLEANUP_PERIOD_DAYS's own comment
      for the confirmed source) so a fresh box never silently loses chat
      history to a default the user never configured. Same unconditional-
      managed-default treatment as every other key here.

    Idempotent; preserves all other keys."""
    result = dict(settings)
    result["effortLevel"] = MANAGED_EFFORT_LEVEL
    result["disableAgentView"] = True
    # #376: fullscreen is now the pin -- see this function's own docstring
    # bullet above for the full history/tradeoff/citation. The old ordering
    # concern ("re-check env-var-vs-setting precedence before changing this
    # pin") is resolved by Anthropic's own docs, not re-derived here: `tui`
    # and `CLAUDE_CODE_NO_FLICKER` are stated equivalent, so #253's launcher
    # mode is redundant-but-harmless post-#376, not removed (see
    # CLAUDE_LAUNCH_SCRIPT_CONTENT's own comment).
    result["tui"] = MANAGED_TUI
    result["model"] = MANAGED_MODEL
    result["promptSuggestionEnabled"] = False
    result.pop("autoCompactWindow", None)
    # A malformed/legacy `env` (a string/int/list rather than an object) must
    # be SELF-HEALED to a fresh dict, never crashed on — `dict(existing or
    # {})` raises on a non-dict-but-truthy value, which would escape
    # cmd_install (no enclosing try/except around this step) and, worse,
    # escape cmd_push's local-install call mid-deploy (it catches only
    # SystemExit, per #273 — so an ordinary exception here would run AFTER
    # `git push` to GitHub but BEFORE the remote-deploy loop, leaving main
    # updated and every remote host untouched). Adversarial-review finding,
    # #288.
    existing_env = result.get("env")
    result["env"] = dict(existing_env) if isinstance(existing_env, dict) else {}
    result["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] = MANAGED_MAX_SUBAGENTS_PER_SESSION
    result["cleanupPeriodDays"] = MANAGED_CLEANUP_PERIOD_DAYS
    return result


def read_file_safe(path: Path) -> str:
    """Read a file, returning empty string if it doesn't exist."""
    if path.exists():
        return path.read_text()
    return ""


def unified_diff(old: str, new: str, label: str) -> str:
    """Compute a unified diff between two strings."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile=f"a/{label}",
                                tofile=f"b/{label}")
    return "".join(diff)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _validate_filedrop():
    """Validate the File-Drop service: each filedrop/*.py imports cleanly and the
    systemd service template exists with the repo-path placeholder + ExecStart."""
    import importlib

    errors = []
    fd_dir = REPO_DIR / "filedrop"
    if not fd_dir.is_dir():
        errors.append(f"File-drop package missing: {fd_dir}")
        return errors

    for mod in ("filedrop", "filedrop.share", "filedrop.server"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            errors.append(f"File-drop module failed to import: {mod} ({e})")

    if not FILEDROP_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing file-drop service template: {FILEDROP_SERVICE_TEMPLATE}")
    else:
        tmpl = FILEDROP_SERVICE_TEMPLATE.read_text()
        if "{{REPO_DIR}}" not in tmpl:
            errors.append("File-drop service template missing {{REPO_DIR}} placeholder")
        if "{{HOST_IP}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IP}} placeholder")
        if "{{HOST_IPS}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IPS}} placeholder")
        if "filedrop --serve" not in tmpl:
            errors.append("File-drop service template ExecStart missing `filedrop --serve`")

    return errors


def _validate_tmux_cutover():
    """Validate the tmux boot-time cutover unit (#242): the systemd unit
    template exists and points ExecStart at the managed script + is ordered
    to run before login/ssh; the inline script CONTENT constant (same shape
    as the claude launcher's own CLAUDE_LAUNCH_SCRIPT_CONTENT) carries the
    expected paths and never references the packaged /usr/bin/tmux."""
    errors = []
    if not TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing tmux-cutover unit template: {TMUX_CUTOVER_SERVICE_TEMPLATE}")
    else:
        t = TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()
        if TMUX_CUTOVER_SCRIPT_DEST not in t:
            errors.append("tmux-cutover unit template ExecStart missing the managed script path")
        if "WantedBy=sysinit.target" not in t:
            errors.append("tmux-cutover unit template missing WantedBy=sysinit.target")
        if "DefaultDependencies=no" not in t:
            errors.append("tmux-cutover unit template missing DefaultDependencies=no")
    if "/usr/bin/tmux" in TMUX_CUTOVER_SCRIPT_CONTENT:
        errors.append("tmux-cutover script must never reference the packaged /usr/bin/tmux")
    if TMUX_CUTOVER_NEWEST not in TMUX_CUTOVER_SCRIPT_CONTENT:
        errors.append("tmux-cutover script missing the managed NEWEST path")
    return errors


def _validate_watchdog():
    """Validate the api-watchdog: the package imports cleanly and the systemd
    service + timer templates exist with the repo-path placeholder + ExecStart."""
    import importlib

    errors = []
    wd_dir = REPO_DIR / "watchdog"
    if not wd_dir.is_dir():
        errors.append(f"api-watchdog package missing: {wd_dir}")
        return errors
    try:
        importlib.import_module("watchdog")
    except Exception as e:
        errors.append(f"api-watchdog module failed to import: ({e})")

    svc = REPO_DIR / "settings" / "api-watchdog.service.template"
    tmr = REPO_DIR / "settings" / "api-watchdog.timer.template"
    if not svc.exists():
        errors.append(f"Missing api-watchdog service template: {svc}")
    else:
        t = svc.read_text()
        if "{{REPO_DIR}}" not in t:
            errors.append("api-watchdog service template missing {{REPO_DIR}} placeholder")
        if "watchdog --once" not in t:
            errors.append("api-watchdog service template ExecStart missing `watchdog --once`")
    if not tmr.exists():
        errors.append(f"Missing api-watchdog timer template: {tmr}")
    elif "OnUnitActiveSec" not in tmr.read_text():
        errors.append("api-watchdog timer template missing OnUnitActiveSec")

    return errors


def cmd_validate(args):
    """Check all module/rule files exist and all @import paths resolve."""
    errors = []

    # Validate universal profile
    if not UNIVERSAL_PROFILE.exists():
        errors.append(f"Missing profile: {UNIVERSAL_PROFILE}")
    else:
        entries = parse_profile(UNIVERSAL_PROFILE)
        for entry in entries:
            full_path = REPO_DIR / entry
            if not full_path.exists():
                errors.append(f"Missing file referenced in profile: {entry}")

    # Validate all profile files
    for profile in (REPO_DIR / "profiles").glob("*.profile"):
        try:
            entries = parse_profile(profile)
            for entry in entries:
                full_path = REPO_DIR / entry
                if not full_path.exists():
                    errors.append(f"[{profile.name}] Missing: {entry}")
        except SystemExit:
            errors.append(f"Failed to parse profile: {profile}")

    # Validate skills
    for skill in SKILL_NAMES:
        skill_md = REPO_DIR / "skills" / skill / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"Missing skill: {skill_md}")

    # Validate agents
    for name in AGENT_NAMES:
        agent_md = REPO_DIR / "agents" / f"{name}.md"
        if not agent_md.exists():
            errors.append(f"Missing agent: {agent_md}")

    # Validate hooks
    if HOOKS_JSON.exists():
        try:
            hooks = json.loads(HOOKS_JSON.read_text())
            # Check that referenced hook scripts exist
            for event_type, event_hooks in hooks.get("hooks", {}).items():
                for entry in event_hooks:
                    for hook in entry.get("hooks", []):
                        cmd = hook.get("command", "")
                        # Extract script path from command like "bash ~/devel/airuleset/hooks/foo.sh"
                        if "airuleset/hooks/" in cmd:
                            script_name = cmd.split("airuleset/hooks/")[-1]
                            script_path = REPO_DIR / "hooks" / script_name
                            if not script_path.exists():
                                errors.append(f"Missing hook script: {script_path}")
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in hooks.json: {e}")

    # Validate rules have frontmatter
    for rule_file in (REPO_DIR / "rules").glob("*.md"):
        content = rule_file.read_text()
        if not content.startswith("---"):
            errors.append(f"Rule missing YAML frontmatter: {rule_file.name}")

    # Validate the File-Drop service: filedrop/*.py loads + service template ok.
    errors.extend(_validate_filedrop())
    # Validate the api-watchdog: watchdog/ imports + service/timer templates ok.
    errors.extend(_validate_watchdog())
    # Validate the tmux boot-time cutover unit: template + script content ok.
    errors.extend(_validate_tmux_cutover())

    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("All validations passed.")
        print(f"  Profiles: {len(list((REPO_DIR / 'profiles').glob('*.profile')))}")
        print(f"  Modules:  {len(list((REPO_DIR / 'modules').rglob('*.md')))}")
        print(f"  Rules:    {len(list((REPO_DIR / 'rules').glob('*.md')))}")
        print(f"  Skills:   {len(SKILL_NAMES)}")
        print(f"  Agents:   {len(AGENT_NAMES)}")


def cmd_diff(args):
    """Show what install would change (unified diff)."""
    modules, global_rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
    new_claude_md = generate_claude_md(modules)
    old_claude_md = read_file_safe(CLAUDE_MD)

    diff_md = unified_diff(old_claude_md, new_claude_md, "CLAUDE.md")
    if diff_md:
        print("=== ~/.claude/CLAUDE.md ===")
        print(diff_md)
    else:
        print("~/.claude/CLAUDE.md: no changes")

    # Settings diff
    hooks_config = load_hooks_json()
    if hooks_config:
        old_settings_str = read_file_safe(SETTINGS_JSON)
        old_settings = json.loads(old_settings_str) if old_settings_str else {}
        new_settings = apply_managed_settings_defaults(
            merge_hooks_into_settings(hooks_config, old_settings))
        new_settings_str = json.dumps(new_settings, indent=2) + "\n"
        old_for_diff = old_settings_str if old_settings_str else "{}\n"

        diff_settings = unified_diff(old_for_diff, new_settings_str, "settings.json")
        if diff_settings:
            print("\n=== ~/.claude/settings.json ===")
            print(diff_settings)
        else:
            print("~/.claude/settings.json: no changes")

    # Skills diff (this box's set — scoped per skill_names_for_user)
    print("\n=== ~/.claude/skills/ (symlinks) ===")
    for skill in skill_names_for_user():
        target = REPO_DIR / "skills" / skill
        link = SKILLS_DIR / skill
        if link.is_symlink():
            current_target = Path(os.readlink(link))
            if current_target == target:
                print(f"  {skill}: OK (already linked)")
            else:
                print(f"  {skill}: CHANGE ({current_target} -> {target})")
        elif link.exists():
            print(f"  {skill}: REPLACE (existing dir/file -> symlink to {target})")
        else:
            print(f"  {skill}: ADD (new symlink -> {target})")

    # Rules diff (global path-scoped rules symlinked into ~/.claude/rules/)
    print("\n=== ~/.claude/rules/ (symlinks) ===")
    for entry in global_rules:
        name = Path(entry).name
        target = REPO_DIR / entry
        link = RULES_DIR / name
        if link.is_symlink():
            current_target = Path(os.readlink(link))
            if current_target == target:
                print(f"  {name}: OK (already linked)")
            else:
                print(f"  {name}: CHANGE ({current_target} -> {target})")
        elif link.exists():
            print(f"  {name}: REPLACE (existing file -> symlink to {target})")
        else:
            print(f"  {name}: ADD (new symlink -> {target})")


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
                 "node", "npx", "less")

# The apt PACKAGE name differs from the BINARY name for node/npx (#158):
# Debian/Ubuntu's real "node" package is an unrelated amateur packet-radio
# program (installing it would never provide the `node` binary at all), and
# `npx` has no package of its own — both ship bundled inside "nodejs" (this
# fleet's own NodeSource package, confirmed live to explicitly `Replaces:
# npm`, i.e. npm+npx included). Every other tracked dep's binary name IS its
# apt package name, so this override only needs the two exceptions.
RUNTIME_DEP_PACKAGE = {"node": "nodejs", "npx": "nodejs"}


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


# --- Tier-0 target/ retention (#315) ---------------------------------------
# Tier 0 (no-local-builds.md's DEFAULT) bans HEAVY local builds but still
# legitimately fills target/ via the cheap checks it DOES allow (cargo
# check/clippy/test --no-run -- ~500 MB/project by the skill's own
# estimate) and via historical eras (an earlier /fast-iterate window, a
# since-abandoned Tier 2 opt-in). Nothing ever purged it automatically --
# the local-builds skill's own purge rule is prose, invoked ON-DEMAND
# only (no caller anywhere in this repo) -- so growth is monotonic:
# songplayer 10.1G, spinbike 8.3G, camera-box 4.4G, ~23 GB of dead weight
# on dev1 alone, "znova a znova" (user, 2026-08-08).

TARGET_PURGE_LOG_PATH = CLAUDE_DIR / "target-purge.log"
TARGET_PURGE_STATE_PATH = CLAUDE_DIR / "target-purge-state.json"
TARGET_PURGE_MAX_AGE_DAYS_DEFAULT = 7
# Cadence gate for the AUTOMATIC install/push wiring only -- a direct CLI
# call (or dry_run) always runs regardless. FREEZE: no new watchdog job, so
# the sweep itself has to rate-limit ITSELF via a plain state-file stamp
# rather than lean on one.
TARGET_PURGE_MIN_INTERVAL_S = 24 * 3600
_TARGET_PURGE_SKIP_DIRS = (".git", "node_modules", "target")


def _human_size(n) -> str:
    """1234567 -> '1.2MB'. Cheap du-style rendering for a log/report line."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0fB" % n if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


def discover_target_purge_candidates(home=None, max_depth: int = 4):
    """Every `target/` directory that is a genuine cargo build artefact --
    its PARENT holds a `Cargo.toml` -- sitting inside a real checkout root
    (`_checkout_roots()`: `.git` as a directory OR a file, so a worktree/
    submodule counts too -- reused rather than re-walking `$HOME` a second
    time with a second, driftable definition of "repo root").

    Covers a workspace's own root `target/` AND a member crate's
    independent one (e.g. `sp-ui/target`) via a bounded per-repo walk.
    Never descends into `.git`, `node_modules`, or an already-found
    `target/` (no nested-target scanning -- a target/ tree has no cargo
    packages of its own worth discovering). `os.walk`'s own
    `followlinks=False` default means this can never leave the repo by
    following a symlinked directory.

    A NESTED checkout (its own `.git` inside an outer repo) is discovered
    TWICE -- once mis-attributed to the OUTER root by that root's own
    bounded walk, once correctly attributed to itself once
    `_checkout_roots()` reaches it directly -- so results are deduped by
    the target's own realpath, keeping the LAST attribution seen (#315
    adversarial-review finding 1's secondary cleanup). This is always the
    MORE SPECIFIC one: `_checkout_roots()` is a single topdown walk, which
    always yields an ancestor directory before any of its descendants, so
    the outer (less specific) attribution is always discovered FIRST.

    Returns a list of (target_dir, repo_root) Path pairs.
    """
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    seen = {}
    for root in _checkout_roots(str(home)):
        root_p = Path(root)
        base_depth = str(root_p).rstrip("/").count("/")
        for dirpath, dirnames, filenames in os.walk(
                root_p, topdown=True, onerror=lambda e: None):
            depth = str(dirpath).rstrip("/").count("/") - base_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            has_target = "target" in dirnames
            dirnames[:] = [d for d in dirnames if d not in _TARGET_PURGE_SKIP_DIRS]
            if has_target and "Cargo.toml" in filenames:
                target_dir = Path(dirpath) / "target"
                try:
                    key = os.path.realpath(str(target_dir))
                except OSError:
                    key = str(target_dir)
                seen[key] = (target_dir, root_p)
    return list(seen.values())


def _tier0_via_hook(cwd, hook_path=None, timeout: int = 10) -> bool:
    """True iff `hooks/block-tier0-local-build.sh` would BLOCK a real
    `cargo build` from `cwd` -- i.e. Tier 0. That hook's own exit contract
    (its docstring): exit 2 = block (no marker, a managed Tier-0 project),
    exit 0 = allow (a Tier 1/2 marker present, OR no CLAUDE.md reachable
    at all -- an unmanaged directory, out of scope here either way).

    Literally SHELLS OUT to the real hook rather than re-implementing its
    CLAUDE.md upward-walk + marker regex a second time in Python -- #315's
    own design requirement (single source of truth for tier resolution;
    this repo has repeatedly been burned by a second, drifting
    implementation of the same check). The hook is pure bash + jq (no
    python dependency), fires in milliseconds, and is already the ONE
    place `no-local-builds.md`'s policy is enforced.

    Deliberate, accepted consequence (#315 adversarial-review finding 9,
    THEORETICAL, matches real `cargo build` behaviour exactly): a repo
    with NO CLAUDE.md of its own inherits the tier of the nearest ANCESTOR
    CLAUDE.md, same as a real build there would -- this is a property of
    the hook's own upward walk, not a gap specific to this caller.

    `cwd` must be the directory a real `cargo build` would actually run
    from (the crate directory holding the target/ in question, i.e.
    `target_dir.parent`) -- NEVER the enclosing checkout root, which can
    differ for a member crate or a nested checkout carrying its own tier
    marker (#315 adversarial-review finding 1, CRITICAL: passing the
    checkout root silently purged a Tier-1/2 crate's target/ whenever its
    own tier disagreed with its outer repo's).
    """
    hook_path = Path(hook_path) if hook_path else (REPO_DIR / "hooks" / "block-tier0-local-build.sh")
    if not hook_path.exists():
        return False
    import json as _json
    import subprocess
    payload = _json.dumps({"tool_input": {"command": "cargo build"}, "cwd": str(cwd)})
    env = dict(os.environ)
    # Strips every bypass env var the hook itself honours (currently just
    # this one) so the verdict is deterministic regardless of the CALLING
    # process's own environment -- grep hooks/block-tier0-local-build.sh
    # for `AIRULESET_ALLOW_` if it ever grows a second one.
    env.pop("AIRULESET_ALLOW_LOCAL_BUILD", None)  # deterministic regardless of caller's shell
    try:
        r = subprocess.run(["bash", str(hook_path)], input=payload,
                            capture_output=True, text=True, timeout=timeout, env=env)
    except Exception:
        return False
    return r.returncode == 2


def _target_in_live_use(target_dir, proc_dir=None) -> bool:
    """Mechanical, no-guessing substitute for "is there a live event/hot-
    swap using this build right now" -- approval-scope.md forbids ever
    ASKING the user about that (the user's hardest rule: NEVER gate on
    events/prod-usage/hardware). Instead: is any RUNNING process's
    executable, current working directory, or any open file descriptor
    currently pointing inside `target_dir`? If so -- or if this cannot be
    determined at all (no /proc, a read failure) -- this returns True and
    the caller SKIPS the whole target/, exactly the camera-box "never
    touch build artefacts while an event/hot-swap runs" rule, applied
    mechanically rather than by asking.

    Matches BOTH a link strictly INSIDE `target_dir` (the `startswith`
    check) and a link EQUAL to `target_dir` itself (#315 adversarial-
    review finding 8: a process whose `cwd`/`fd`/`exe` link is exactly
    `target_dir`, no trailing component -- a shell parked in target/, or a
    backup/file-manager process holding the bare directory open --
    `"/repo/target".startswith("/repo/target/")` alone is False and would
    have missed it).

    Known accepted residual (#315 adversarial-review finding 8,
    THEORETICAL): a per-PID `EACCES` (a foreign-uid process on a
    shared-uid box, or a root-owned service) is skipped individually and
    reads as "not using it" for THAT pid -- only a TOTAL /proc failure
    returns True. Unprivileged scanning cannot see into another uid's
    `/proc/<pid>/fd`; this is a structural limit, not a bug to fix here.
    """
    try:
        resolved_bare = os.path.realpath(str(target_dir))
    except OSError:
        return True
    resolved = resolved_bare + os.sep
    proc_dir = Path(proc_dir) if proc_dir is not None else Path("/proc")
    if not proc_dir.is_dir():
        return True
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except OSError:
        return True
    for pid in pids:
        pdir = proc_dir / pid
        for name in ("exe", "cwd"):
            try:
                link = os.readlink(pdir / name)
            except OSError:
                continue
            if link == resolved_bare or link.startswith(resolved):
                return True
        fd_dir = pdir / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if link == resolved_bare or link.startswith(resolved):
                return True
    return False


def _dir_stats(path):
    """(total_size_bytes, newest_mtime_or_None) for every regular file
    under `path`, via one bounded `os.walk`. `os.lstat` (never `stat`) on
    each entry so a symlinked file inside the tree reports the LINK's own
    metadata rather than following it out -- pairs with `os.walk`'s own
    default `followlinks=False` for directories."""
    total = 0
    newest = None
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, onerror=lambda e: None):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            total += st.st_size
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
    return total, newest


def _log_target_purge_results(results, log_path, now, dry_run: bool):
    """Append one line per candidate examined (never silent -- comprehensive-
    logging.md: this is a destructive action, log everything, purge AND
    skip alike) to `log_path`. Best-effort: a log write failure never
    blocks the purge itself, but is reported (never a bare silent pass).

    A `target is None` entry (a DISCOVERY error -- the sweep never even
    got a candidate list) is logged too, as `target=-` (#315 adversarial-
    review finding 7: previously silently dropped, so a persistent
    discovery bug went completely untraceable)."""
    import datetime as _dt
    ts = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).isoformat()
    lines = []
    for r in results:
        if r.get("target") is None:
            lines.append("%s ERROR - repo=- reason=%s" % (ts, r.get("reason", "")))
            continue
        if r["purged"]:
            action = "DRYRUN-WOULD-PURGE" if dry_run else "PURGED"
        else:
            action = "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _human_size(size) if size is not None else ""
        lines.append("%s %s %s repo=%s%s reason=%s" % (
            ts, action, r["target"], r.get("repo", ""), size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  target-purge: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def purge_stale_tier0_targets(home=None, max_age_days=None, dry_run: bool = False,
                              now=None, log_path=None, state_path=None,
                              force: bool = False, hook_path=None,
                              max_depth: int = 4, proc_dir=None,
                              candidates=None):
    """Delete a MAINTAINED Tier-0 repo's stale `target/` (workspace root or
    a member crate's own, e.g. `sp-ui/target`) -- #315.

    A candidate is purged only when ALL of these hold:
      - it is a real cargo build artefact inside a real checkout
        (`discover_target_purge_candidates`, unless `candidates=` is
        passed directly -- used by tests/callers that already have the
        pair list);
      - `_tier0_via_hook` says the repo is genuinely Tier 0 (no `=allowed`/
        `=fast-iterate` marker -- those are NEVER touched -- and NOT an
        unmanaged directory with no CLAUDE.md at all);
      - `_target_in_live_use` finds no process currently using it (the
        mechanical hot-swap/event guard -- never asks the user);
      - its newest mtime (recursively) is older than `max_age_days`
        (default 7) -- a directory with ZERO files inside is treated as
        infinitely stale (nothing to lose).

    `target_dir` is refused outright if it is itself a symlink, or if its
    RESOLVED path escapes the repo root (a symlink pointing elsewhere) --
    never followed, never deleted through.

    Returns a list of per-candidate dicts (`target`, `repo`, `purged`,
    `reason`, `size`, `age_days`) -- always, even a cadence-gated no-op run
    returns `[]`. Every candidate is appended to `log_path` (default
    ~/.claude/target-purge.log) with its size, purge or skip alike.

    Cadence: the automatic install/push wiring runs this at most once per
    `TARGET_PURGE_MIN_INTERVAL_S` (a small state file, not a new watchdog
    job -- the FREEZE forbids a new job; rate-limiting a plain function
    call needs none). `force=True` (the CLI's own manual invocation) or
    `dry_run=True` (a diagnostic run) always bypasses the gate.
    """
    import time as _time
    now = _time.time() if now is None else now
    max_age_days = TARGET_PURGE_MAX_AGE_DAYS_DEFAULT if max_age_days is None else max_age_days
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    log_path = Path(log_path) if log_path else TARGET_PURGE_LOG_PATH
    state_path = Path(state_path) if state_path else TARGET_PURGE_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        # #315 adversarial-review finding 5: a stamp in the FUTURE (an NTP
        # correction, a restored VM snapshot) makes `now - last` negative,
        # unconditionally < the interval -- clamp it to "no prior run"
        # rather than let a bad clock wedge the gate closed forever.
        if last > now:
            last = 0
        if now - last < TARGET_PURGE_MIN_INTERVAL_S:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_target_purge_candidates(home, max_depth=max_depth)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"target": None, "purged": False,
                            "reason": "discovery error: %s" % e})

    for target_dir, repo_root in candidates:
        target_dir = Path(target_dir)
        repo_root = Path(repo_root)
        entry = {"target": str(target_dir), "repo": str(repo_root), "purged": False}
        try:
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ -- never followed"
                results.append(entry)
                continue
            try:
                resolved = target_dir.resolve()
                resolved.relative_to(repo_root.resolve())
            except (OSError, ValueError):
                entry["reason"] = "resolved path escapes repo root -- skipped"
                results.append(entry)
                continue

            # #315 adversarial-review finding 1 (CRITICAL): tier must be
            # resolved against the directory a REAL `cargo build` would
            # actually run from -- target_dir.parent (the crate directory)
            # -- never repo_root. A member crate (or a nested checkout)
            # carrying its OWN marker, inside a markerless outer repo,
            # must be classified by ITS OWN CLAUDE.md, exactly like a real
            # build there would be; using repo_root silently deletes a
            # Tier-1/2 crate's target/ whenever its OWN tier differs from
            # its outer repo's.
            if not _tier0_via_hook(str(target_dir.parent), hook_path=hook_path):
                entry["reason"] = "not Tier 0 (allowed/fast-iterate marker, or unmanaged)"
                results.append(entry)
                continue

            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- skipped"
                results.append(entry)
                continue

            size_bytes, newest_mtime = _dir_stats(target_dir)
            entry["size"] = size_bytes
            age_days = float("inf") if newest_mtime is None else (now - newest_mtime) / 86400.0
            entry["age_days"] = None if age_days == float("inf") else age_days

            if age_days < max_age_days:
                entry["reason"] = "fresh (%.1fd < %sd)" % (age_days, max_age_days)
                results.append(entry)
                continue

            # #315 adversarial-review finding 2: _dir_stats can take a
            # while on a large tree -- re-verify nothing started using
            # target/ in that window, immediately before the actual
            # delete, rather than trusting the check made before the walk.
            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- skipped (re-checked before delete)"
                results.append(entry)
                continue

            age_txt = "empty" if age_days == float("inf") else "%.1fd" % age_days
            entry["reason"] = "stale (%s >= %sd), %s" % (
                age_txt, max_age_days, _human_size(size_bytes))
            if not dry_run:
                shutil.rmtree(target_dir)
            entry["purged"] = True
            results.append(entry)
        except Exception as e:
            entry["reason"] = "error: %s" % e
            results.append(entry)

    _log_target_purge_results(results, log_path, now, dry_run)

    # #315 adversarial-review finding 7: never stamp the cadence gate when
    # discovery itself failed -- nothing was actually examined, so the
    # sweep must retry on the VERY NEXT tick rather than sitting silent
    # (at most once/day) behind a stamp that claims a real run happened.
    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  target-purge: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_purge_targets(args):
    """`airuleset.py purge-targets [--dry-run] [--max-age-days N]` -- manual/
    testable entry point for the #315 sweep. Always `force=True` (bypasses
    the once/day cadence gate that guards the automatic install/push
    wiring -- a deliberate manual call should never be silently skipped)."""
    print("airuleset purge-targets")
    print("=" * 50)
    max_age_days = getattr(args, "max_age_days", None)
    dry_run = bool(getattr(args, "dry_run", False))
    results = purge_stale_tier0_targets(max_age_days=max_age_days, dry_run=dry_run, force=True)
    for r in results:
        if r.get("target") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        if r["purged"]:
            tag = "WOULD PURGE" if dry_run else "PURGED"
        else:
            tag = "skip"
        print("  %s: %s -- %s" % (tag, r["target"], r.get("reason", "")))
    purged = [r for r in results if r.get("purged")]
    total = sum(r.get("size", 0) or 0 for r in purged)
    print()
    verb = "would be " if dry_run else ""
    print("%d target/ dir(s) %spurged, %s %sreclaimed." % (
        len(purged), verb, _human_size(total), verb))
    print("Log: %s" % TARGET_PURGE_LOG_PATH)


# --- Stale worktree sweep (#345) --------------------------------------------
# The harness auto-removes a worker's `.claude/worktrees/agent-<id>` ONLY on
# a NORMAL agent exit -- a worker killed by an API error / session limit
# leaves the worktree registered (locked forever, so even `git branch -D`
# refuses) with nothing that ever deletes its branch once someone removes
# the directory by hand. A round's own close-out
# (`skills/autopilot/SKILL.md` ROUND INTEGRATION step 5) only ever cleans
# branches it actually merged -- never a sibling round's dead leftovers.
# Dead workers therefore leak one worktree + one branch each, unboundedly,
# fleet-wide. This reuses #315's own `purge_stale_tier0_targets` shape
# EXACTLY: a plain, cadence-gated function (its own state file, never the
# 60s watchdog timer -- the FREEZE forbids a new job, and rate-limiting a
# plain function call needs none) wired as a non-fatal step inside
# `cmd_install()`, plus a manual/testable CLI entry point.

STALE_WORKTREE_LOG_PATH = CLAUDE_DIR / "worktree-sweep.log"
STALE_WORKTREE_STATE_PATH = CLAUDE_DIR / "worktree-sweep-state.json"
STALE_WORKTREE_MIN_INTERVAL_S = 6 * 3600      # env AIRULESET_WORKTREE_SWEEP_INTERVAL_S
# A worktree can carry several GB of build artefacts (camera-box/songplayer/
# spinbike measured 4-10GB each under #315's own target-purge finding) --
# `git worktree remove` walks and deletes that whole tree, which can genuinely
# take longer than the lightweight git-plumbing default (_worktree_git's own
# 15s) on a busy, I/O-contended box. A short timeout here would make a large,
# perfectly-removable worktree read as "refused (dirty/in use)" every sweep,
# never actually reclaiming the disk it exists to reclaim.
STALE_WORKTREE_REMOVE_TIMEOUT_S = 120
_STALE_WORKTREE_PROTECTED_BRANCHES = ("main", "dev", "master")

# #348 -- the two dominant leak shapes #345's own registered-worktree-only
# scan structurally cannot see: a branch orphaned by a hand-removed
# directory, and a locked worktree whose owning session (the harness locks
# with the MAIN SESSION's pid, never the individual worker's) has exited.
# Both extra safeguards below are AGE gates -- "at least several days" per
# the user's own decision (issue comment 5233902115) -- on top of the
# existing zero-commits-ahead/clean-tree criteria; see `discover_stale_
# worktrees`'s locked-branch handling and `discover_orphaned_worktree_
# branches` for the full five/four-signal chain each requires.
STALE_ORPHAN_BRANCH_MIN_AGE_S = 3 * 24 * 3600  # env AIRULESET_WORKTREE_ORPHAN_MIN_AGE_S
STALE_LOCKED_DEAD_MIN_AGE_S = 3 * 24 * 3600    # env AIRULESET_WORKTREE_LOCKED_DEAD_MIN_AGE_S


def _worktree_env_age_s(env_key, default_s):
    """`int(os.environ.get(env_key, default_s))` -- an unparseable override
    falls back to `default_s`, never crashes the sweep over a typo'd env
    var (mirrors `sweep_stale_worktrees`'s own cadence-interval read)."""
    try:
        return int(os.environ.get(env_key, default_s))
    except ValueError:
        return default_s


def _worktree_git(args, cwd, timeout: int = 15):
    """`git -C <cwd> <args>` -- stdout text on rc==0, else None (never
    guess; every caller treats None as "skip this repo/candidate", never
    as a false positive or negative)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def _worktree_porcelain_entries(repo_root, git_run=None):
    """Parse `git worktree list --porcelain -z` -- one dict per registered
    worktree: {"path", "branch" (bare name, no `refs/heads/`, None if
    detached/bare), "locked": bool}. Entry 0 is ALWAYS the PRIMARY
    checkout -- callers must skip it by INDEX, never by path-matching (a
    renamed/symlinked primary checkout must still be protected). Returns
    [] on any read failure -- a repo git can't be read from is simply
    skipped, never guessed at.

    `-z` (NUL-delimited fields, record boundary = an empty field -- the
    NUL-mode equivalent of the blank line git's own non-`-z` format uses)
    is required, not cosmetic: #345 adversarial-review THEORETICAL-1,
    confirmed live -- a worktree whose PATH contains a literal `\\n`
    (legal on Linux) corrupts a plain newline-split parse into a phantom
    entry that can point at an UNRELATED, healthy worktree, which the
    sweep then genuinely removes. `-z` needs no escaping/quoting to be
    newline-safe by construction.
    """
    git_run = git_run or _worktree_git
    out = git_run(["worktree", "list", "--porcelain", "-z"], repo_root)
    if out is None:
        return []
    entries = []
    cur = None
    for field in out.split("\x00"):
        if field == "":
            if cur is not None:
                entries.append(cur)
                cur = None
            continue
        if field.startswith("worktree "):
            if cur is not None:
                entries.append(cur)
            cur = {"path": field[len("worktree "):], "branch": None, "locked": False,
                  "lock_reason": ""}
        elif cur is None:
            continue
        elif field.startswith("branch "):
            ref = field[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None
        elif field == "locked" or field.startswith("locked "):
            cur["locked"] = True
            # #348 -- the reason text (when the harness locked with
            # `--reason "..."`) is what carries the owning session's own
            # pid/starttime; an unadorned `locked` (no reason at all, e.g.
            # a manual `git worktree lock` with no --reason) leaves this "".
            cur["lock_reason"] = field[len("locked "):] if field.startswith("locked ") else ""
    if cur is not None:
        entries.append(cur)
    return entries


def _worktree_sweep_base_branch(repo_root, git_run=None):
    """`dev` if a LOCAL `dev` branch exists, else `main`, else `master`;
    `None` if none of the three exist (caller skips the whole repo).

    Deliberately NOT the ticket's own literal "ahead of main" wording --
    `skills/autopilot/SKILL.md`'s ROUND INTEGRATION step 2 documents a
    worktree worker as forked from "local main for a local-merge repo,
    local dev for a dev->main PR repo" -- most fleet repos use the
    two-branch dev+main flow (two-branch-workflow.md), where dev is
    normally AHEAD of main by whatever unreleased work is in flight.
    Comparing against bare main would count every one of those in-flight
    dev commits as "the worker's own real work" and permanently skip
    cleanup on every such repo. Preferring dev when present is strictly
    SAFER, never less safe, than bare main (dev >= main in a well-behaved
    two-branch repo, so 0-ahead-of-dev implies 0-ahead-of-main too, never
    the reverse) -- it only makes the sweep MORE conservative where it
    matters. For a single-branch repo (airuleset itself -- no local
    `dev`) this resolves to plain `main`, matching the ticket's literal
    wording and the one-off cleanup's own already-used criterion exactly.
    """
    git_run = git_run or _worktree_git
    for name in ("dev", "main", "master"):
        # #345 adversarial-review MINOR-1: `rev-parse --verify` resolves a
        # TAG named dev/main/master just as happily as a branch -- fully
        # qualify with `refs/heads/` so this can only ever resolve to a
        # genuine local branch, never a same-named tag.
        if git_run(["rev-parse", "--verify", "--quiet", "refs/heads/%s" % name],
                   repo_root) is not None:
            return name
    return None


_WORKTREE_LOCK_PID_RX = re.compile(r"claude agent \S+ \(pid (\d+) start (\d+)\)")


def _worktree_lock_pid(reason):
    """Extract `(pid, start)` from a harness-style lock reason string --
    STRICTLY the harness's OWN observed shape, in FULL, via
    `re.fullmatch`: "claude agent <agent-id> (pid <N> start <M>)"
    (verified live against a real worktree lock on this box), `start`
    matching `/proc/<pid>/stat` field 22 byte-for-byte. `(None, None)`
    for ANYTHING else -- including a human-authored reason that merely
    MENTIONS a "(pid N)"-shaped substring somewhere in its own text.

    #348 adversarial-review MAJOR-1 (TRIGGERED live, no injection): the
    original `re.search`-anywhere form with an OPTIONAL `start` accepted
    a human's own `--reason "debugging crash (pid 999999) - DO NOT
    REMOVE"` (unlocked + swept) and a reason ENDING "... (pid 1 start
    999999999)" (pid 1 genuinely alive, wrong starttime, still read as
    "confirmed dead"). Anchoring the WHOLE reason to the harness's own
    literal "claude agent <id> " prefix -- and requiring `start` (never
    optional; the harness always records it) -- rejects both: neither
    trigger starts with "claude agent ", so neither can ever match,
    regardless of where inside the string a pid-shaped substring sits.
    """
    if not reason:
        return (None, None)
    match = _WORKTREE_LOCK_PID_RX.fullmatch(reason.strip())
    if not match:
        return (None, None)
    return (int(match.group(1)), int(match.group(2)))


def _proc_stat_text(pid, proc_root=None):
    """Real `/proc/<pid>/stat` reader -- None when the pid does not exist
    (already exited) or is unreadable for any other reason. `proc_root`
    (default `/proc`) exists only so a test can point this at a fake
    directory tree -- production code always uses the real filesystem.

    `errors="replace"` is deliberate, not cosmetic (#348 adversarial-
    review MINOR-2, TRIGGERED live against a real forked process with a
    `comm` set to invalid UTF-8 via `prctl(PR_SET_NAME, ...)` -- legal,
    and NOT rare on a box running arbitrary tooling): a bare
    `Path.read_text()` raises `UnicodeDecodeError`, uncaught by the
    `except OSError` here, and that raise propagates all the way out of
    `sweep_stale_worktrees`'s blanket discovery-error handler -- silently
    disabling BOTH new #348 leak categories fleet-wide the instant any
    locked worktree's recorded pid happens to be occupied by such a
    process. Every field this module ever reads out of a stat line
    (state..starttime) is plain ASCII and sits AFTER the `comm` field's
    own closing paren, so replacing invalid bytes inside `comm` with
    U+FFFD can never corrupt the numeric fields this code actually
    parses -- it must NOT return `None` on decode failure either: that
    would read as "no such process" to `_pid_is_dead`, i.e. a manufactured
    FALSE POSITIVE for a process that is very much alive."""
    proc_root = proc_root or Path("/proc")
    try:
        return (proc_root / str(pid) / "stat").read_text(errors="replace")
    except OSError:
        return None


def _pid_is_dead(pid, start=None, stat_reader=None):
    """True ONLY when POSITIVELY confirmed the exact `(pid, start)`
    session no longer exists -- False when it IS alive, None when this
    cannot be determined at all. Neither `False` nor `None` may ever be
    treated as "dead" by a caller -- both mean "do not touch this".

    `start` is `/proc/<pid>/stat` field 22 (starttime, clock ticks since
    boot) -- cross-checking it closes the PID-REUSE window: if `pid` is
    now occupied by a DIFFERENT process (a different starttime), the
    ORIGINAL (pid, start) session is genuinely gone, so this correctly
    still returns True even though the bare pid number is "in use" again.
    Without `start` (a lock reason with no recorded starttime) a live pid
    is reported alive, per the "never guess dead" contract -- there is
    nothing to disambiguate a reused pid from the original session.
    """
    stat_reader = stat_reader or _proc_stat_text
    if not isinstance(pid, int) or pid <= 0:
        return None
    raw = stat_reader(pid)
    if raw is None:
        return True   # no such process at all -- positively confirmed gone
    if start is None:
        return False  # alive; nothing to cross-check -- never guessed dead
    try:
        # comm (field 2) can itself contain spaces/parens -- split on the
        # LAST ")" to skip past it safely, exactly like every real /proc
        # parser must. state=idx0, ppid=idx1, ... starttime=idx19 of what
        # remains (field 22 overall, minus the pid+comm fields already cut).
        after_comm = raw.rsplit(")", 1)[1]
        fields = after_comm.split()
        proc_start = int(fields[19])
    except (IndexError, ValueError):
        return None   # malformed/unexpected /proc shape -- never guessed
    return proc_start != start


def _worktree_admin_dir(repo_root, worktree_path):
    """Resolve `<repo_root>/.git/worktrees/<name>` for a REGISTERED
    worktree at `worktree_path`, by matching each admin subdir's own
    `gitdir` file (which records the worktree's own `.git` FILE path)
    against `worktree_path/.git` -- robust even when `worktree_path`
    itself no longer exists on disk (a LOCKED worktree whose directory
    was removed by hand keeps its admin entry forever -- `git worktree
    prune` never touches a locked one). None when no match is found."""
    admin_root = Path(repo_root) / ".git" / "worktrees"
    try:
        candidates = list(admin_root.iterdir())
    except OSError:
        return None
    target = str(Path(worktree_path) / ".git")
    for d in candidates:
        try:
            recorded = (d / "gitdir").read_text().strip()
        except OSError:
            continue
        if recorded == target:
            return d
    return None


def _worktree_lock_age_s(admin_dir, now):
    """Seconds since a worktree's OWN lock was created -- the admin dir's
    `locked` marker-file mtime (the harness writes this file ONCE, at
    dispatch time, and never touches it again). None when unmeasurable
    (no admin dir, no `locked` file, unreadable, or a future mtime from
    clock skew) -- unmeasurable is never "old enough"."""
    if admin_dir is None:
        return None
    try:
        mtime = (Path(admin_dir) / "locked").stat().st_mtime
    except OSError:
        return None
    age = now - mtime
    return age if age >= 0 else None


def _worktree_is_clean(worktree_path, git_run):
    """True only when `git status --porcelain` in the worktree returns
    exactly empty output. False when it reports ANY change. None when the
    check itself could not run (missing directory, git failure) --
    unmeasurable is never treated as clean."""
    out = git_run(["status", "--porcelain"], worktree_path)
    if out is None:
        return None
    return out.strip() == ""


def _classify_locked_worktree(root, path, branch, lock_reason, git_run, now,
                              pid_is_dead=None, min_age_s=None):
    """A LOCKED worktree is normally NEVER a candidate (#345's own
    NON-NEGOTIABLE #1) -- this is the ONE deliberate, narrowly-scoped
    exception (#348): promote it to a genuine candidate (`kind:
    "locked_dead"`) only when EVERY ONE of five independent signals
    agrees, in order, each refusing (never guessing) on its own failure:

      1. `lock_reason` parses to the harness's own `(pid N start M)`
         shape -- an unparseable/manual lock refuses outright;
      2. that EXACT (pid, start) is POSITIVELY confirmed dead
         (`_pid_is_dead` -- never merely "not currently found");
      3. the lock itself is at least `min_age_s` old (several days by
         default) -- a pure time buffer, not a substitute for #2;
      4. the branch carries ZERO commits ahead of the repo's own base --
         the identical fully-qualified rev-list check every other
         candidate gets; real unmerged work is never touched;
      5. the working tree is provably clean (`git status --porcelain`
         empty) -- a dead worker's own uncommitted edits are never
         silently discarded.

    Residual risk (not closed here, stated in the ticket's design
    comment): the harness's lock always records the MAIN SESSION's pid,
    never the individual dispatched worker's -- so this can only ever
    detect a worktree whose entire owning session has exited. A worker
    whose own task finished or died while its main session stays alive
    (busy on other tickets) is unreachable by signal 2 and is simply
    never swept -- a FALSE NEGATIVE only, never a false positive: a
    still-alive (or undeterminable) pid always refuses.

    A SECOND, narrower residual (#348 adversarial-review MINOR-4,
    confirmed): a worktree that is BOTH locked (dead session, otherwise
    reclaimable) AND has had its directory removed by hand is
    permanently unreachable by EITHER mechanism at once -- signal 5
    (`git status --porcelain`) cannot run against a missing directory
    and refuses forever, while `discover_orphaned_worktree_branches`
    never sees the branch either (it is still "registered" by the
    surviving locked admin entry). False negative only, never fixed
    here -- closing it would need a directory-existence-aware variant of
    signal 5, out of this ticket's own named scope.
    """
    pid_is_dead = pid_is_dead or _pid_is_dead
    min_age_s = (_worktree_env_age_s("AIRULESET_WORKTREE_LOCKED_DEAD_MIN_AGE_S",
                                     STALE_LOCKED_DEAD_MIN_AGE_S)
                if min_age_s is None else min_age_s)
    row = {"path": path, "branch": branch, "repo": root, "reason": None,
          "kind": "locked_dead", "lock_reason": lock_reason}
    pid, start = _worktree_lock_pid(lock_reason)
    if pid is None:
        row["reason"] = "locked, no parseable session pid -- never guessed at"
        return row
    dead = pid_is_dead(pid, start)
    if dead is not True:
        row["reason"] = ("locked (active worker)" if dead is False
                         else "locked, session liveness undeterminable")
        return row
    admin_dir = _worktree_admin_dir(root, path)
    age = _worktree_lock_age_s(admin_dir, now)
    if age is None or age < min_age_s:
        row["reason"] = ("locked, dead session but lock is too recent to "
                         "reclaim (< %d d) or age unmeasurable" %
                         (min_age_s / 86400))
        return row
    if branch is None:
        row["reason"] = "locked, dead session, detached HEAD -- never guessed at"
        return row
    if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
        row["reason"] = "protected branch name (%s)" % branch
        return row
    base = _worktree_sweep_base_branch(root, git_run=git_run)
    if not base:
        row["reason"] = "no dev/main/master to compare against"
        return row
    ahead = git_run(["rev-list", "--count",
                    "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
    if ahead is None or ahead.strip() != "0":
        row["reason"] = "locked, dead session, but has unmerged work -- never touched"
        return row
    clean = _worktree_is_clean(path, git_run)
    if clean is not True:
        row["reason"] = "locked, dead session, but tree not provably clean -- never touched"
        return row
    row["base"] = base
    return row     # reason stays None -- genuine candidate


def _worktree_branch_ref_age_s(repo_root, branch, now):
    """Seconds since a branch's own LOOSE ref file was last written
    (`.git/refs/heads/<branch>`'s mtime). None when the ref is PACKED (no
    individual loose file exists -- no per-branch mtime is recoverable at
    all) or the mtime is in the future (clock skew) -- unmeasurable is
    NEVER treated as "old enough" to touch."""
    try:
        mtime = (Path(repo_root) / ".git" / "refs" / "heads" / branch).stat().st_mtime
    except OSError:
        return None
    age = now - mtime
    return age if age >= 0 else None


def discover_orphaned_worktree_branches(home=None, git_run=None, now=None,
                                        min_age_s=None):
    """Every local branch, across every managed repo under `home`, with NO
    registered worktree pointing at it at all -- the #348 root cause: `git
    worktree prune` (already run at the top of `discover_stale_worktrees`'s
    own per-repo pass) silently cleans up the dangling ADMIN entry once a
    worktree directory is removed by hand, but never the branch it leaves
    behind. Same output shape as `discover_stale_worktrees`'s own rows --
    `{"branch", "repo", "reason", "base", "kind": "orphan_branch",
    "path": ""}` -- `path` is deliberately the empty string, never `None`
    (which `sweep_stale_worktrees`'s own discovery-error sentinel row
    already reserves), since there is no worktree directory to remove.

    Safety criteria, STRICTER than a registered worktree candidate (#348's
    own named residual risk: a bare, 0-commit branch is byte-for-byte
    identical whether it is a dead worker's abandoned leftover or a
    human's freshly `git branch`'d, not-yet-checked-out intent):
      - never `main`/`dev`/`master`;
      - never a branch a worktree entry (including the PRIMARY checkout)
        already references -- that candidate belongs to
        `discover_stale_worktrees`, never this function;
      - the branch's own ref-file mtime is at least `min_age_s` (several
        days by default) old -- a PACKED ref (no loose-file mtime at all)
        refuses outright, never guessed at;
      - zero commits ahead of the resolved base, via the SAME fully-
        qualified `refs/heads/<base>..refs/heads/<branch>` comparison
        `discover_stale_worktrees` already uses (so #345's own same-
        named-tag hardening covers this path for free).

    Residual (#348 adversarial-review MINOR-3, confirmed): `git pack-
    refs`/`git gc --auto` deletes the branch's own loose ref FILE (the
    thing the age check's mtime comes from) with nothing recreating it
    short of new activity on that branch -- so once a genuinely-old,
    genuinely-reclaimable orphan gets swept up in a routine repack, it
    reads "age unmeasurable (packed ref)" and is refused FOREVER after,
    never becoming eligible again on its own. Safe direction only (a
    packed ref can never look artificially OLDER than it is, only
    unmeasurable) -- it just means this sweep will under-fire on any
    repo whose git gc runs routinely, which is worth knowing, not fixing
    here.
    """
    min_age_s = (_worktree_env_age_s("AIRULESET_WORKTREE_ORPHAN_MIN_AGE_S",
                                     STALE_ORPHAN_BRANCH_MIN_AGE_S)
                if min_age_s is None else min_age_s)
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    out = []
    for root in _checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue          # a worktree/submodule itself -- never a primary repo
        registered = set()
        for e in _worktree_porcelain_entries(root, git_run=git_run):
            b = e.get("branch")
            if b:
                registered.add(b)
        listing = git_run(["for-each-ref", "--format=%(refname:short)",
                          "refs/heads/"], root)
        if listing is None:
            continue
        base = None
        base_resolved = False
        for branch in listing.splitlines():
            branch = branch.strip()
            if not branch or branch in registered:
                continue
            row = {"path": "", "branch": branch, "repo": root, "reason": None,
                  "kind": "orphan_branch"}
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                row["reason"] = "protected branch name (%s)" % branch
                out.append(row)
                continue
            age = _worktree_branch_ref_age_s(root, branch, now)
            if age is None or age < min_age_s:
                row["reason"] = ("orphan branch too recent to reclaim (< %d d) "
                                 "or age unmeasurable (packed ref)" %
                                 (min_age_s / 86400))
                out.append(row)
                continue
            if not base_resolved:
                base = _worktree_sweep_base_branch(root, git_run=git_run)
                base_resolved = True
            if not base:
                row["reason"] = "no dev/main/master to compare against"
                out.append(row)
                continue
            ahead = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
            if ahead is None:
                row["reason"] = "could not measure commits ahead of %s" % base
                out.append(row)
                continue
            ahead = ahead.strip()
            if ahead != "0":
                row["reason"] = "%s commit(s) ahead of %s -- has real work" % (ahead, base)
                out.append(row)
                continue
            row["base"] = base
            out.append(row)     # reason stays None -- genuine candidate
    return out


def discover_stale_worktrees(home=None, git_run=None, now=None, pid_is_dead=None):
    """Every worktree, across every managed repo under `home`, that is
    SAFE to reclaim -- a list of dicts {"path", "branch", "repo",
    "reason", "base", "kind"}. `reason` is `None` for a genuine candidate,
    else WHY it was excluded (a `--dry-run` report needs both). Pure
    discovery+classification -- `sweep_stale_worktrees` is the only
    function that ever mutates anything.

    Safety criteria (#345, NON-NEGOTIABLE):
      - never worktree-list entry 0 (the primary checkout);
      - never a branch literally named main/dev/master, wherever found;
      - never a LOCKED worktree UNLESS `_classify_locked_worktree`'s own
        5-signal dead-session chain (#348) positively confirms both the
        owning session is gone AND the branch/tree carry no real work --
        see that function's own docstring for the full criteria and the
        residual risk it explicitly does NOT close;
      - only a branch with ZERO commits ahead of `_worktree_sweep_base_branch`
        -- a branch carrying real, unmerged work is NEVER a candidate
        (salvage-before-discarding.md).
    Deliberately NOT filtered by branch-NAME shape (e.g.
    `worktree-agent-*`) -- the ticket's own evidence names five stale
    worktrees from the OLD custom-naming convention that predates
    `isolation: "worktree"` becoming the default; the objective safety
    criteria above are branch-name-agnostic and equally safe regardless
    of naming convention.
    """
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    out = []
    for root in _checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue          # a worktree/submodule itself -- never a primary repo
        git_run(["worktree", "prune"], root)   # dangling admin-only entries -- always safe
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        if len(entries) <= 1:
            continue          # nothing but the primary checkout
        base = None
        base_resolved = False
        for i, e in enumerate(entries):
            if i == 0:
                continue       # the primary worktree -- never a candidate
            branch = e.get("branch")
            row = {"path": e.get("path"), "branch": branch, "repo": root, "reason": None,
                  "kind": "worktree"}
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                row["reason"] = "protected branch name (%s)" % branch
                out.append(row)
                continue
            if e.get("locked"):
                out.append(_classify_locked_worktree(
                    root, e.get("path"), branch, e.get("lock_reason"),
                    git_run, now, pid_is_dead=pid_is_dead))
                continue
            if branch is None:
                row["reason"] = "detached HEAD -- never guessed at"
                out.append(row)
                continue
            if not base_resolved:
                base = _worktree_sweep_base_branch(root, git_run=git_run)
                base_resolved = True
            if not base:
                row["reason"] = "no dev/main/master to compare against"
                out.append(row)
                continue
            # #345 adversarial-review MAJOR-2 (confirmed data loss): a bare
            # short name here silently resolves to a same-named TAG ahead of
            # the branch (refs/tags/ before refs/heads/ in gitrevisions ref
            # resolution order) with only a stderr warning, rc 0 -- a branch
            # carrying real commits then reads as "0 ahead" and is deleted.
            # Fully-qualify both sides so this can only ever mean the branch.
            ahead = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
            if ahead is None:
                row["reason"] = "could not measure commits ahead of %s" % base
                out.append(row)
                continue
            ahead = ahead.strip()
            if ahead != "0":
                row["reason"] = "%s commit(s) ahead of %s -- has real work" % (ahead, base)
                out.append(row)
                continue
            row["base"] = base
            out.append(row)     # reason stays None -- genuine candidate
    return out


def _log_stale_worktree_results(results, log_path, now, dry_run: bool):
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        if dry_run:
            tag = "WOULD-REMOVE" if not r.get("reason") or "dry" in r.get("reason", "") else "SKIP"
        else:
            tag = "REMOVED" if r.get("removed") else "SKIP"
        lines.append("%s %s %s branch=%s repo=%s -- %s" % (
            ts, tag, r.get("path"), r.get("branch"), r.get("repo"), r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  worktree-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_stale_worktrees(home=None, dry_run: bool = False, now=None, log_path=None,
                          state_path=None, force: bool = False, git_run=None,
                          candidates=None, pid_is_dead=None):
    """Reclaim every stale worktree `discover_stale_worktrees` classifies
    as a genuine candidate (`reason is None`) -- `git worktree remove
    <path>` NEVER passed `--force` (a dirty/untracked-file tree makes git
    itself refuse; that refusal is reported, never overridden), and only
    once THAT succeeds is the branch deleted via `git branch -D <branch>`.

    `-D` (force) here is safe and deliberate, not the same `--force` the
    ticket forbids on `worktree remove`: `discover_stale_worktrees`
    already independently proved zero commits ahead of the resolved
    base via `git rev-list --count` -- a MORE precise, base-aware check
    than `-d`'s own "merged into whatever HEAD the primary checkout
    happens to have" heuristic, which would depend on an unrelated
    coincidence (what ref the primary checkout is on at sweep time). The
    worktree directory is already gone by the time this runs, so nothing
    can add a new commit to the branch in between.

    Cadence-gated via its own state file (`STALE_WORKTREE_STATE_PATH`)
    mirroring #315's `purge_stale_tier0_targets` exactly -- never leans
    on the 60s watchdog timer (FREEZE: no new job). `force=True` (the
    CLI's own manual invocation) or `dry_run=True` always bypasses it.

    Known, deliberate residual (#345 adversarial-review THEORETICAL-2):
    `git worktree remove` reports a worktree holding ONLY gitignored files
    (a stray `.env`, a `target/` build dir) as clean and removes it, taking
    those files with it -- this is the intended disk-reclaim behaviour and
    matches git's own definition of "safe" (it still correctly REFUSES on
    any untracked, non-ignored file). A per-worktree gitignored SECRET
    would be lost this way; a dead worker's own worktree is never the
    source of truth for one.
    """
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else STALE_WORKTREE_LOG_PATH
    state_path = Path(state_path) if state_path else STALE_WORKTREE_STATE_PATH
    git_run = git_run or _worktree_git

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = STALE_WORKTREE_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_WORKTREE_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = STALE_WORKTREE_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_stale_worktrees(home, git_run=git_run, now=now,
                                                  pid_is_dead=pid_is_dead)
            # #348 -- the two extra leak shapes #345's own registered-
            # worktree-only scan cannot see (a hand-removed directory's
            # orphaned branch, and a locked worktree whose owning session
            # has exited). Both share this SAME discovery failure handler:
            # if either raises, nothing from EITHER source is trusted --
            # a partial discovery is not safer than none at all.
            candidates = candidates + discover_orphaned_worktree_branches(
                home, git_run=git_run, now=now)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"path": None, "removed": False,
                            "reason": "discovery error: %s" % e})

    for c in candidates:
        entry = dict(c)
        entry["removed"] = False
        kind = c.get("kind", "worktree")
        if c.get("reason"):
            results.append(entry)
            continue
        if dry_run:
            entry["reason"] = "would remove (dry-run)"
            results.append(entry)
            continue

        if kind == "orphan_branch":
            # No worktree directory at all -- straight to the branch, with
            # the SAME TOCTOU re-check every other branch delete gets
            # (salvage-before-discarding-work.md): something could have
            # started using this branch again between discovery and now.
            base = c.get("base")
            still_zero = True
            if base:
                recheck = git_run(["rev-list", "--count",
                                  "refs/heads/%s..refs/heads/%s" % (base, c["branch"])],
                                 c["repo"])
                still_zero = recheck is not None and recheck.strip() == "0"
            if not still_zero:
                entry["reason"] = "branch now carries new commits -- left in place"
                results.append(entry)
                continue
            bd = git_run(["branch", "-D", c["branch"]], c["repo"])
            entry["removed"] = bd is not None
            entry["branch_deleted"] = bd is not None
            entry["reason"] = "removed" if bd is not None else "branch delete refused -- left in place"
            results.append(entry)
            continue

        if kind == "locked_dead":
            # #348 -- a lock created by a now-provably-dead session must be
            # released before `worktree remove` can touch it at all; git
            # itself refuses to remove a locked worktree without --force,
            # which the NON-NEGOTIABLE safety core forbids passing.
            unlocked = git_run(["worktree", "unlock", c["path"]], c["repo"])
            if unlocked is None:
                entry["reason"] = "unlock refused -- left in place"
                results.append(entry)
                continue
            # falls through to the SAME remove+branch-delete flow below,
            # identical to a plain "worktree" candidate from here on.

        rc = git_run(["worktree", "remove", c["path"]], c["repo"],
                     timeout=STALE_WORKTREE_REMOVE_TIMEOUT_S)
        if rc is None:
            if kind == "locked_dead":
                # #348 adversarial-review MINOR-1 (TRIGGERED live): the
                # unlock above already succeeded -- if remove now refuses
                # (a dirty file raced in between classification and this
                # candidate's own turn, a permissions blip, a timeout),
                # leaving the worktree UNLOCKED with its forensic pid/
                # start reason gone forever would silently strip a real
                # protection, and the very next ORDINARY #345 sweep would
                # finish the job with NONE of this function's 5 safety
                # checks. Best-effort restore the ORIGINAL lock+reason;
                # the outcome is not re-verified further here -- a failed
                # re-lock just means the next sweep re-discovers this
                # worktree with an unparseable/no reason and refuses
                # again on that basis, never worse than today's state.
                relock = ["worktree", "lock", c["path"]]
                if c.get("lock_reason"):
                    relock += ["--reason", c["lock_reason"]]
                git_run(relock, c["repo"])
            entry["reason"] = "worktree remove refused (dirty tree, in use, or timed out) -- left in place"
            results.append(entry)
            continue
        entry["removed"] = True
        entry["reason"] = "removed"
        # Re-verify 0-ahead immediately before deleting the branch -- closes the
        # window between discovery's own ahead-count read and THIS candidate's
        # turn in a (possibly long) candidate list, during which something could
        # have added a genuine commit to the branch elsewhere. Mirrors #315's own
        # adversarial-review finding 2 (re-check right before the destructive
        # step, not just at discovery time) -- salvage-before-discarding-work.md.
        base = c.get("base")
        still_zero = True
        if base:
            # #345 adversarial-review MAJOR-2: fully-qualify both sides here
            # too -- the same ambiguous-short-name-resolves-to-a-tag hazard
            # applies to this re-check exactly as it does to discovery's own.
            recheck = git_run(["rev-list", "--count",
                              "refs/heads/%s..refs/heads/%s" % (base, c["branch"])], c["repo"])
            still_zero = recheck is not None and recheck.strip() == "0"
        if not still_zero:
            entry["branch_deleted"] = False
            entry["reason"] = "removed worktree, but branch now carries new commits -- branch left in place"
            results.append(entry)
            continue
        bd = git_run(["branch", "-D", c["branch"]], c["repo"])
        entry["branch_deleted"] = bd is not None
        results.append(entry)

    _log_stale_worktree_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  worktree-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_worktrees(args):
    """`airuleset.py sweep-worktrees [--dry-run]` -- manual/testable entry
    point for the #345 sweep. Always `force=True` (bypasses the cadence
    gate that guards the automatic install/push wiring -- a deliberate
    manual call should never be silently skipped)."""
    print("airuleset sweep-worktrees")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    results = sweep_stale_worktrees(dry_run=dry_run, force=True)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        # #345 adversarial-review MAJOR-1: sweep_stale_worktrees() leaves
        # `removed=False` on EVERY row in dry-run (correct -- nothing was
        # actually deleted), so keying the tag/count on `removed` alone
        # mislabelled every genuine candidate "skip" and always reported
        # "0 worktree(s) would be removed". A dry-run candidate is
        # identified by its own distinct `reason` text instead.
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD REMOVE" if dry_run else "REMOVED"
        else:
            tag = "skip"
        print("  %s: %s (branch %s, repo %s) -- %s" % (
            tag, r["path"], r.get("branch"), r.get("repo"), r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    print()
    verb = "would be " if dry_run else ""
    print("%d worktree(s) %sremoved." % (len(acted_rows), verb))
    print("Log: %s" % STALE_WORKTREE_LOG_PATH)


# --- Old Claude CLI binary sweep (#355) -------------------------------------
# Every managed box installs the `claude` CLI natively (ensure_claude_cli_
# installed, #263): `~/.local/bin/claude` symlinks to ONE file inside
# `~/.local/share/claude/versions/<dotted-version>` (each ~280-300MB), and
# EVERY auto-update lays down a NEW versioned file while leaving the OLD
# one behind forever -- nothing has ever swept it. On subdev's 11 stream
# accounts this measured 9-12G reclaimable (each account carrying 3-4 old
# versions); on THIS box alone it was 4 versions / 1.2G (#355 STEP 0
# comment). Mirrors #315/#345's own shape exactly: discovery separated from
# destruction, own log+state file, cadence-gated (FREEZE: no new watchdog
# job, so a plain state-file stamp rate-limits this instead), wired as a
# non-fatal cmd_install() step plus a manual/testable CLI entry point.

CLI_VERSION_LOG_PATH = CLAUDE_DIR / "cli-version-sweep.log"
CLI_VERSION_STATE_PATH = CLAUDE_DIR / "cli-version-sweep-state.json"
CLI_VERSION_MIN_INTERVAL_S = 24 * 3600     # env AIRULESET_CLI_VERSION_SWEEP_INTERVAL_S
# Deliberately generous -- current+previous are KEPT unconditionally
# regardless of age (see discover_cli_version_candidates); this floor only
# protects a version ranked BELOW previous from being reclaimed while it
# might still be mid-download/mid-update-race.
CLI_VERSION_MIN_AGE_DAYS_DEFAULT = 2       # env AIRULESET_CLI_VERSION_MIN_AGE_DAYS
_CLI_VERSION_NAME_RX = re.compile(r"^\d+(\.\d+)+$")


def _min_age_days_env(explicit, env_key, default):
    """`explicit` if given (an actual `min_age_days=` CALL ARGUMENT always
    wins); else the env var `env_key` if it parses as a float; else
    `default`. Shared by both #355 sweeps below (#355 adversarial-review
    finding 2: the constant comments advertised `AIRULESET_CLI_VERSION_
    MIN_AGE_DAYS`/`AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS` but neither was
    ever actually read -- a silently no-op safety knob). An unparseable
    override falls back to `default`, never crashes the sweep over a
    typo'd env var (mirrors this repo's own established pattern for a
    cadence INTERVAL override, applied here to an AGE floor).

    `float("nan")` is explicitly refused too (#355 round-2 adversarial-
    review finding F3, live-executed): `"nan"` parses cleanly, but
    `age_days < nan` is `False` for EVERY value, which silently disables
    the ENTIRE age floor -- the one string that slips the docstring's own
    "never crashes" promise into "never PROTECTS" instead.
    `float("inf")` is deliberately still accepted (an operator setting it
    genuinely means "nothing is ever old enough" -- a legitimate,
    fail-SAFE disable switch, the opposite direction from `nan`)."""
    if explicit is not None:
        return explicit
    try:
        v = float(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default
    return default if v != v else v   # v != v is the portable NaN test


def _cli_versions_dir(home=None) -> Path:
    """`~/.local/share/claude/versions/` -- the native installer's own
    layout (confirmed live, #355 STEP 0: a flat dir of version-named FILES,
    never a subdirectory-per-version)."""
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    return home / ".local" / "share" / "claude" / "versions"


def _cli_version_key(name: str):
    """Parse a dotted-decimal version NAME into a tuple of ints for sorting
    (e.g. "2.1.226" -> (2, 1, 226)). `None` when `name` does not match the
    strict `^\\d+(\\.\\d+)+$` shape -- never guessed; the caller refuses any
    entry this returns `None` for, individually, rather than assume it's
    "probably" a version."""
    if not _CLI_VERSION_NAME_RX.match(name):
        return None
    return tuple(int(p) for p in name.split("."))


def _resolve_current_cli_version(versions_dir, env=None):
    """The REAL, currently-live version FILE inside `versions_dir` --
    resolved via `shutil.which("claude")` (the same repaired-PATH
    `_claude_cli_env()` `_claude_cli_installed` already uses) followed by
    `os.path.realpath`, NEVER guessed from mtime (#355 design comment: a
    genuinely-current-but-manually-downgraded version must never look
    deletable just because a newer file happens to exist in the dir).

    Returns the resolved absolute path STRING, or `None` when it cannot be
    confidently determined -- `claude` not on PATH at all, or it resolves
    to something OUTSIDE `versions_dir` entirely (an unexpected install
    method: a system package, a different install layout). Callers MUST
    refuse the WHOLE sweep on `None`, never guess which file is "probably"
    current.

    Known, deliberate residual (#355 adversarial-review finding 5,
    THEORETICAL): unlike `_claude_cli_installed`, this deliberately does
    NOT fall back to a real LOGIN shell's own `command -v claude` (nvm/
    login-only PATH machinery) -- a box whose `claude` resolves ONLY that
    way refuses the whole CLI-version sweep FOREVER, correctly (never
    guessed), but that refusal is loud and logged as an ERROR row on
    every sweep, never silent."""
    import shutil as _shutil
    e = env or _claude_cli_env()
    which = _shutil.which("claude", path=e.get("PATH", ""))
    if not which:
        return None
    try:
        resolved = os.path.realpath(which)
        vdir_resolved = os.path.realpath(str(versions_dir))
    except OSError:
        return None
    if os.path.dirname(resolved) != vdir_resolved:
        return None
    return resolved


def discover_cli_version_candidates(home=None, versions_dir=None, now=None,
                                    min_age_days=None, env=None, proc_dir=None):
    """Every installed Claude CLI version FILE under `~/.local/share/claude/
    versions/` that is safe to reclaim -- #355. A list of dicts
    `{"path", "version", "reason", "size"?, "age_days"?}` -- `reason` is
    `None` for a genuine candidate, else WHY it was excluded (mirrors
    `discover_stale_worktrees`/`discover_target_purge_candidates`'s own
    shape exactly). A discovery-level REFUSAL (current unresolvable, the
    dir unlistable) returns a SINGLE `{"path": None, "reason": ...}` row --
    the same ERROR-sentinel shape those two functions already use.

    Safety criteria (NON-NEGOTIABLE):
      - the CURRENT version (resolved via the real `~/.local/bin/claude`
        symlink target, never guessed) is NEVER a candidate;
      - the version ranked immediately BELOW current in a version-tuple-
        sorted-DESCENDING list is kept too (the rollback target) -- even
        when current is not the newest entry present (a manual downgrade,
        a newer build downloaded but not yet symlinked);
      - a THIRD, redundant guard: ANY entry whose own resolved realpath
        equals the resolved current path is kept regardless of index
        arithmetic -- belt-and-suspenders on the one truly non-negotiable
        invariant here ("NIKDY bežiacu/aktuálnu verziu");
      - an entry whose name does not parse as a plain dotted-decimal
        version, or that is not a plain regular file (never a symlink,
        never a directory), is refused INDIVIDUALLY -- unexpected layout,
        never guessed at;
      - if `versions_dir` doesn't exist at all, this returns `[]` (this box
        simply doesn't use the native install layout -- nothing to do, not
        an error); if it exists but the CURRENT version cannot be
        confidently resolved inside it, the WHOLE box is refused;
      - a surviving candidate still needs BOTH an age floor (mtime) AND a
        live-process check (`_target_in_live_use`, #315's own /proc
        exe-scan, reused verbatim -- catches a still-running OLD process
        that hasn't picked up a newer `current` yet) before being genuine.
    """
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLI_VERSION_MIN_AGE_DAYS",
                                     CLI_VERSION_MIN_AGE_DAYS_DEFAULT)
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    vdir = Path(versions_dir) if versions_dir else _cli_versions_dir(home)

    if not vdir.is_dir():
        return []

    try:
        names = sorted(os.listdir(vdir))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (vdir, e)}]

    current = _resolve_current_cli_version(vdir, env=env)
    if current is None:
        return [{"path": None,
                "reason": "current CLI version could not be confidently "
                          "resolved inside %s -- refusing the whole sweep "
                          "for this box" % vdir}]

    out = []
    parsed = []   # list of (key, name, path) -- name-parseable plain files only
    for name in names:
        p = vdir / name
        key = _cli_version_key(name)
        if key is None:
            out.append({"path": str(p), "version": name,
                       "reason": "name does not parse as a dotted-decimal "
                                 "version -- unexpected layout, skipped"})
            continue
        if p.is_symlink() or not p.is_file():
            out.append({"path": str(p), "version": name,
                       "reason": "not a plain regular file -- unexpected "
                                 "layout, skipped"})
            continue
        parsed.append((key, name, p))

    parsed.sort(key=lambda t: t[0], reverse=True)

    try:
        current_idx = next(i for i, (_, _, p) in enumerate(parsed)
                           if os.path.realpath(str(p)) == current)
    except StopIteration:
        # Never guess which discovered entry is "probably" current --
        # refuse the whole sweep (the ERROR row leads the result list; any
        # already-classified unexpected-layout rows above it stay reported
        # too, since a caller may still want to see the full picture).
        out.insert(0, {"path": None,
                      "reason": "resolved current version %s does not match "
                                "any discovered version entry -- refusing "
                                "the whole sweep for this box" % current})
        return out

    keep_idxs = {current_idx}
    if current_idx + 1 < len(parsed):
        keep_idxs.add(current_idx + 1)

    for i, (key, name, p) in enumerate(parsed):
        entry = {"path": str(p), "version": name, "reason": None}
        # Redundant guard (belt-and-suspenders on the non-negotiable
        # invariant): a resolved-path match against `current` is checked
        # independently of `i in keep_idxs`.
        try:
            is_current_path = os.path.realpath(str(p)) == current
        except OSError:
            is_current_path = False
        if is_current_path:
            entry["reason"] = "current version -- never deleted"
            out.append(entry)
            continue
        if i in keep_idxs:
            entry["reason"] = "rollback version (immediately below current) -- kept"
            out.append(entry)
            continue
        try:
            st = os.lstat(p)
        except OSError as e:
            entry["reason"] = "could not stat: %s" % e
            out.append(entry)
            continue
        entry["size"] = st.st_size
        age_days = (now - st.st_mtime) / 86400.0
        entry["age_days"] = age_days
        if age_days < min_age_days:
            entry["reason"] = "too recent (%.1fd < %sd)" % (age_days, min_age_days)
            out.append(entry)
            continue
        if _target_in_live_use(p, proc_dir=proc_dir):
            entry["reason"] = "in live use (or undeterminable) -- skipped"
            out.append(entry)
            continue
        out.append(entry)   # reason stays None -- genuine candidate

    return out


def _log_cli_version_sweep_results(results, log_path, now, dry_run: bool):
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        if dry_run:
            tag = "WOULD-REMOVE" if not r.get("reason") or "dry" in r.get("reason", "") else "SKIP"
        else:
            tag = "REMOVED" if r.get("removed") else "SKIP"
        lines.append("%s %s %s version=%s -- %s" % (
            ts, tag, r.get("path"), r.get("version"), r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  cli-version-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_stale_cli_versions(home=None, versions_dir=None, dry_run: bool = False,
                             now=None, log_path=None, state_path=None,
                             force: bool = False, min_age_days=None,
                             candidates=None, env=None, proc_dir=None):
    """Reclaim every stale CLI version `discover_cli_version_candidates`
    classifies as a genuine candidate (`reason is None`) -- #355. Never
    `--force`-deletes anything the discovery step already excluded;
    re-verifies "still a plain regular file, not in live use" immediately
    before EACH delete (a TOCTOU re-check, mirroring #315's own
    re-verify-before-delete pattern) rather than trusting discovery-time
    state.

    Cadence-gated via its own state file, mirroring #315/#345 exactly --
    never leans on the 60s watchdog timer (FREEZE: no new job).
    `force=True` (the CLI's own manual invocation) or `dry_run=True` always
    bypasses the gate."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else CLI_VERSION_LOG_PATH
    state_path = Path(state_path) if state_path else CLI_VERSION_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = CLI_VERSION_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_CLI_VERSION_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = CLI_VERSION_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_cli_version_candidates(
                home, versions_dir=versions_dir, now=now,
                min_age_days=min_age_days, env=env, proc_dir=proc_dir)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"path": None, "removed": False,
                            "reason": "discovery error: %s" % e})

    for c in candidates:
        entry = dict(c)
        entry["removed"] = False
        if c.get("path") is None:
            results.append(entry)
            continue
        if c.get("reason"):
            results.append(entry)
            continue
        if dry_run:
            entry["reason"] = "would remove (dry-run)"
            results.append(entry)
            continue

        p = Path(c["path"])
        try:
            if p.is_symlink() or not p.is_file():
                entry["reason"] = ("no longer a plain regular file -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = ("in live use (or undeterminable) -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            p.unlink()
            entry["removed"] = True
            entry["reason"] = "removed"
        except OSError as e:
            entry["reason"] = "delete failed: %s" % e
        results.append(entry)

    _log_cli_version_sweep_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  cli-version-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_cli_versions(args):
    """`airuleset.py sweep-cli-versions [--dry-run] [--min-age-days N]` --
    manual/testable entry point for the #355 CLI-version sweep. Always
    `force=True` (bypasses the cadence gate that guards the automatic
    install/push wiring -- a deliberate manual call should never be
    silently skipped)."""
    print("airuleset sweep-cli-versions")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    results = sweep_stale_cli_versions(dry_run=dry_run, force=True, min_age_days=min_age_days)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD REMOVE" if dry_run else "REMOVED"
        else:
            tag = "skip"
        print("  %s: %s (version %s) -- %s" % (
            tag, r["path"], r.get("version"), r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    total = sum(r.get("size", 0) or 0 for r in acted_rows)
    print()
    verb = "would be " if dry_run else ""
    print("%d CLI version(s) %sremoved, %s %sreclaimed." % (
        len(acted_rows), verb, _human_size(total), verb))
    print("Log: %s" % CLI_VERSION_LOG_PATH)


# --- Claude scratch/tmp sweep (#355) ----------------------------------------
# Every Claude Code session writes into `/tmp/claude-<uid>/<encoded-cwd>/
# <session-id>/scratchpad/...` (the harness's own convention -- this very
# session's scratchpad lives there) plus, in practice, loose scratch files
# dropped directly at the per-uid root. Nothing has ever swept it -- the
# worktree sweep (#345/#348) is scoped strictly to `.claude/worktrees/`
# git worktrees and never touches `/tmp` at all. Measured live on THIS box:
# 42 entries, 1.4G under /tmp/claude-1000 (#355 STEP 0 comment) -- and a
# same-owner, DIFFERENTLY-NAMED sibling (`/tmp/claude-286`) sits right next
# to it, proving name-only matching is not a safe enough anchor (see
# discover_claude_scratch_candidates's own docstring).

CLAUDE_SCRATCH_LOG_PATH = CLAUDE_DIR / "claude-scratch-sweep.log"
CLAUDE_SCRATCH_STATE_PATH = CLAUDE_DIR / "claude-scratch-sweep-state.json"
CLAUDE_SCRATCH_MIN_INTERVAL_S = 24 * 3600   # env AIRULESET_CLAUDE_SCRATCH_SWEEP_INTERVAL_S
CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT = 7      # env AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS


def _claude_scratch_root(tmp_dir=None, uid=None) -> Path:
    """`<tmp_dir>/claude-<uid>` -- THIS account's own per-uid Claude Code
    scratch root (session scratchpads + loose working files -- exactly the
    directory this very session's own scratchpad lives under). `tmp_dir`
    defaults to `/tmp`; `uid` defaults to `os.getuid()`."""
    tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")
    uid = os.getuid() if uid is None else uid
    return tmp_dir / ("claude-%d" % uid)


def discover_claude_scratch_candidates(tmp_dir=None, uid=None, now=None,
                                       min_age_days=None, proc_dir=None):
    """Every direct child (file OR directory) of THIS account's OWN
    `/tmp/claude-<uid>/` scratch root that is safe to reclaim -- #355. A
    list of dicts `{"path", "reason", "size"?, "age_days"?}` -- `reason` is
    `None` for a genuine candidate, else WHY it was excluded.

    Safety criteria (NON-NEGOTIABLE):
      - the root must be NAMED `claude-<N>` where N is LITERALLY
        `str(uid)` for THIS account, AND independently confirmed owned
        (`st_uid`) by that SAME uid -- both checks, never just one (a
        same-owner-but-DIFFERENTLY-NAMED sibling proves name alone is not
        a safe enough anchor -- live on this very box, see the module
        comment above). If the root doesn't exist, isn't a directory, is
        itself a symlink, or the ownership check fails, this returns `[]`
        -- and critically, NO OTHER user's `/tmp` content is EVER even
        listed, let alone touched;
      - a candidate's age is the NEWEST mtime found ANYWHERE inside its
        own subtree (`_dir_stats`'s recursive newest-file walk for a
        directory, or the bare file's own mtime) -- never the top entry's
        OWN mtime alone, so a session still actively writing somewhere
        deep inside an old-looking sibling tree is never wrongly judged
        idle (this is the "nikdy scratch ŽIVEJ session" mtime/age
        poistka). An EMPTY subtree (`_dir_stats` finds no file at all --
        the harness pre-creates `<cwd>/<session>/scratchpad` empty at
        session start, before a live session has written anything) falls
        back to the DIRECTORY's OWN mtime, NEVER "infinitely stale"
        (#355 adversarial-review finding 1, MAJOR, live-confirmed on
        dev1 -- unlike #315's target/ purge, where an empty `target/`
        genuinely has zero bytes to protect, an empty scratch tree has a
        live SESSION to protect instead);
      - a symlinked child is refused outright -- never followed, never
        deleted through;
      - a candidate still needs BOTH the age floor AND a live-process
        check (`_target_in_live_use`) before being genuine.

    Known, deliberate residuals (round 1/round 2 adversarial review,
    THEORETICAL, none closed under the FREEZE -- no new watchdog job):
      - finding 4: a session tmux-parked idle for MORE than `min_age_days`
        with no cwd/fd currently held inside its own tree can still have
        real (non-empty) scratch data reclaimed -- the live-use check
        only sees processes ACTIVELY holding a reference, and mtime only
        sees recent writes, neither of which "this session is parked but
        will resume" can express. Same residual every mtime-based ager in
        this repo accepts. A parked session whose tree stayed EMPTY the
        whole time hits the SAME gap through the empty-tree fallback
        above (round-2 finding F2) -- strictly weaker (zero bytes lost;
        worst case one failed scratch write later);
      - round-2 finding F1: `_dir_stats`'s `onerror=lambda e: None` walk
        silently SKIPS a subdirectory it cannot read -- a same-uid
        mode-000 dir hiding a genuinely FRESH file, under an otherwise
        stale top-level mtime, still reads as "empty" and ages by the
        stale fallback. Live-executed and confirmed reachable, but needs
        a self-inflicted unreadable subdir to trigger; not present in any
        real fleet scratch tree observed so far.
    """
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS",
                                     CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT)
    uid = os.getuid() if uid is None else uid
    root = _claude_scratch_root(tmp_dir, uid)

    if root.is_symlink() or not root.is_dir():
        return []
    try:
        if os.stat(str(root)).st_uid != uid:
            return []
    except OSError:
        return []

    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (root, e)}]

    out = []
    for name in names:
        p = root / name
        entry = {"path": str(p), "reason": None}
        if p.is_symlink():
            entry["reason"] = "symlink entry -- never followed, never deleted through"
            out.append(entry)
            continue
        try:
            if p.is_dir():
                size_bytes, newest_mtime = _dir_stats(p)
                if newest_mtime is None:
                    # #355 adversarial-review finding 1 (MAJOR, live-
                    # confirmed on dev1): the harness pre-creates
                    # <cwd>/<session>/scratchpad EMPTY at session start,
                    # before a live session has written anything -- an
                    # empty tree must NEVER read as "infinitely stale"
                    # (unlike #315's target/ purge, where an empty
                    # target/ genuinely has zero bytes to protect and
                    # "always reclaimable" is correct there). Fall back
                    # to the DIRECTORY's OWN mtime so a tree created
                    # seconds ago stays protected by the age floor
                    # exactly like a non-empty one would.
                    newest_mtime = os.lstat(p).st_mtime
            else:
                st = os.lstat(p)
                size_bytes, newest_mtime = st.st_size, st.st_mtime
        except OSError as e:
            entry["reason"] = "could not stat: %s" % e
            out.append(entry)
            continue
        entry["size"] = size_bytes
        age_days = (now - newest_mtime) / 86400.0
        entry["age_days"] = age_days
        if age_days < min_age_days:
            entry["reason"] = "too recent (%.1fd < %sd)" % (age_days, min_age_days)
            out.append(entry)
            continue
        if _target_in_live_use(p, proc_dir=proc_dir):
            entry["reason"] = "in live use (or undeterminable) -- skipped"
            out.append(entry)
            continue
        out.append(entry)   # reason stays None -- genuine candidate

    return out


def _log_claude_scratch_results(results, log_path, now, dry_run: bool):
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        if dry_run:
            tag = "WOULD-REMOVE" if not r.get("reason") or "dry" in r.get("reason", "") else "SKIP"
        else:
            tag = "REMOVED" if r.get("removed") else "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _human_size(size) if size is not None else ""
        lines.append("%s %s %s%s -- %s" % (
            ts, tag, r.get("path"), size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  claude-scratch-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_claude_scratch(tmp_dir=None, uid=None, dry_run: bool = False,
                         now=None, log_path=None, state_path=None,
                         force: bool = False, min_age_days=None,
                         candidates=None, proc_dir=None):
    """Reclaim every stale claude scratch/tmp path
    `discover_claude_scratch_candidates` classifies as a genuine candidate
    (`reason is None`) -- #355. Re-verifies "still not a symlink, still
    exists, still not in live use" immediately before EACH delete (a
    TOCTOU re-check), rather than trusting discovery-time state.

    Cadence-gated via its own state file, mirroring #315/#345/the CLI-
    version sweep exactly -- never leans on the 60s watchdog timer
    (FREEZE: no new job). `force=True` (the CLI's own manual invocation) or
    `dry_run=True` always bypasses the gate."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else CLAUDE_SCRATCH_LOG_PATH
    state_path = Path(state_path) if state_path else CLAUDE_SCRATCH_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0
        interval = CLAUDE_SCRATCH_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_CLAUDE_SCRATCH_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = CLAUDE_SCRATCH_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_claude_scratch_candidates(
                tmp_dir, uid=uid, now=now, min_age_days=min_age_days, proc_dir=proc_dir)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"path": None, "removed": False,
                            "reason": "discovery error: %s" % e})

    for c in candidates:
        entry = dict(c)
        entry["removed"] = False
        if c.get("path") is None:
            results.append(entry)
            continue
        if c.get("reason"):
            results.append(entry)
            continue
        if dry_run:
            entry["reason"] = "would remove (dry-run)"
            results.append(entry)
            continue

        p = Path(c["path"])
        try:
            if p.is_symlink():
                entry["reason"] = "symlink entry -- refused (re-checked before delete)"
                results.append(entry)
                continue
            if not p.exists():
                entry["reason"] = "already gone"
                results.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = ("in live use (or undeterminable) -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            entry["removed"] = True
            entry["reason"] = "removed"
        except OSError as e:
            entry["reason"] = "delete failed: %s" % e
        results.append(entry)

    _log_claude_scratch_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  claude-scratch-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_claude_scratch(args):
    """`airuleset.py sweep-claude-scratch [--dry-run] [--min-age-days N]` --
    manual/testable entry point for the #355 scratch/tmp sweep. Always
    `force=True` (bypasses the cadence gate that guards the automatic
    install/push wiring)."""
    print("airuleset sweep-claude-scratch")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    results = sweep_claude_scratch(dry_run=dry_run, force=True, min_age_days=min_age_days)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD REMOVE" if dry_run else "REMOVED"
        else:
            tag = "skip"
        print("  %s: %s -- %s" % (tag, r["path"], r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    total = sum(r.get("size", 0) or 0 for r in acted_rows)
    print()
    verb = "would be " if dry_run else ""
    print("%d claude scratch path(s) %sremoved, %s %sreclaimed." % (
        len(acted_rows), verb, _human_size(total), verb))
    print("Log: %s" % CLAUDE_SCRATCH_LOG_PATH)


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
            CLAUDE_MD.write_text(new_claude_md)
            print(f"  Updated:   {CLAUDE_MD}")
        else:
            print(f"  No change: {CLAUDE_MD}")
    else:
        CLAUDE_MD.write_text(new_claude_md)
        print(f"  Created:   {CLAUDE_MD}")

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
    # ultracode kept resurrecting after #53). Ultracode can't live in
    # settings.json (session-only, GH #64817) — only the `ultracode` mode
    # (claude-ultracode) passes it, deliberate opt-in only (#53). effortLevel
    # above is the persistent fallback for reasoning depth regardless of mode.
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

    # --- 3c. tmux managed block: every managed user's ~/.tmux.conf (#235/#236/#241) ---
    # tmux's own 2000-line default plus the current CC renderer's re-render
    # frame-stacking made real scrollback holey within minutes under
    # agentic load -- raise history-limit fleet-wide via the same
    # idempotent-marker-block shape as the launcher's ~/.bashrc block above.
    # #236 extended the SAME block with default-size 176x50 -- the
    # identical frame-stacking mechanism also fires on every per-attach
    # resize from a different-sized terminal, not just scrollback rotation.
    # #236 originally also shipped `window-size manual`; #241 removed it
    # again -- it crashes tmux 3.4's server outright at startup (every
    # managed box's version) -- so only history-limit + default-size ship
    # now. history-limit alone is live-applied to any RUNNING tmux server
    # (#235's original, proven-safe scope); default-size is conf-only and
    # takes effect for the next server/session (see apply_tmux_history_
    # limit's own docstring, and the module-level comment above
    # render_tmux_history_block, for the full history).
    try:
        tmux_changed = apply_tmux_history_limit()
        tmux_desc = (f"history-limit {TMUX_HISTORY_LIMIT}, "
                     f"default-size {TMUX_DEFAULT_SIZE}")
        if tmux_changed:
            print(f"  Updated:   {TMUX_CONF} ({tmux_desc})")
        else:
            print(f"  No change: {TMUX_CONF} ({tmux_desc})")
    except Exception as e:
        print(f"  tmux managed-block error: {e}", file=sys.stderr)

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
    # (#263), ssh auto-attach (#264), human-gap report (#263). True no-ops on
    # every non-stream box (dev1/dev2/gatekeeper) via AUTHORITY_BY_USER's own
    # scope.
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

    # --- 7. Discord notify config: warn LOUDLY if this host has no .env ---
    try:
        check_discord_notify_config()
    except Exception as e:
        print(f"  discord notify check error (non-fatal): {e}", file=sys.stderr)

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


# ---------------------------------------------------------------------------
# systemd --user helpers (shared by the File-Drop service install)
# ---------------------------------------------------------------------------


def _xdg_runtime_env():
    """A copy of os.environ with XDG_RUNTIME_DIR set explicitly.

    `systemctl --user` needs XDG_RUNTIME_DIR to find the user bus; when install
    runs over SSH (no login session) it is often unset. We set it deterministically
    to /run/user/<uid>."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run_systemctl(args):
    """Run `systemctl --user <args>` with the explicit XDG env. Returns
    (returncode, stdout, stderr). Never raises."""
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=30, env=_xdg_runtime_env())
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def _whoami():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")


# ---------------------------------------------------------------------------
# File-Drop systemd service + share/serve subcommands
# ---------------------------------------------------------------------------


def _render_filedrop_unit(port=None):
    """Read the file-drop unit template and substitute the per-machine placeholders.

    {{REPO_DIR}} -> this checkout's path (ExecStart). {{HOST_IP}} -> the primary
    (tailscale-first) IP, for status/URL. {{HOST_IPS}} -> the comma list of ALL
    private IPs to bind (tailscale + LAN — filedrop.bind_ips()), so the server
    answers on every interface the user might be on. Both are computed HERE
    (unsandboxed, so `hostname -I` / `tailscale ip` work) and baked into the
    Environment so the sandboxed server never needs AF_NETLINK to discover its own
    address. {{PORT}} -> the per-user port chosen by _choose_filedrop_port (a
    second airuleset user on the same host cannot reuse the first user's :8788)."""
    return (FILEDROP_SERVICE_TEMPLATE.read_text()
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{HOST_IPS}}", ",".join(filedrop_bind_ips()))
            .replace("{{HOST_IP}}", filedrop_host_ip())
            .replace("{{PORT}}", str(port if port is not None else FILEDROP_PORT)))


def _choose_filedrop_port(bind_ip):
    """The port this user's file-drop should serve on.

    Two airuleset users on ONE host (montalu@dev1, marek@gatekeeper) collide on
    the default :8788 — the second user's service restart-loops on Errno 98
    (observed on montalu@dev1, 2026-07-04). Precedence:
      1. FILEDROP_PORT env — explicit override, never second-guessed.
      2. A previously PERSISTED choice (~/.claude/filedrop.port) — kept when our
         own service actively serves it OR it still bind-tests free on this
         host; a port carried over by a ~/.claude migration that a DIFFERENT
         user's file-drop holds here is dropped and re-picked (montalu@subdev
         inherited dev1's 8789 == marek's subdev port, #33, 2026-07-24).
      3. The default, when OUR OWN service is already actively serving it.
      4. Probe bind on the actual bind IP: default free → default; taken by a
         FOREIGN instance → first free port in 8789-8798, persisted so the serve
         unit, the share CLI, and `filedrop status` all agree on the same URL.
    Fail-open to the default when nothing binds (the service then fails loudly,
    exactly as before)."""
    env = os.environ.get("FILEDROP_PORT")
    if env:
        return int(env)
    persisted = filedrop_persisted_port()
    rc, out, _err = _run_systemctl(["is-active", "filedrop.service"])
    our_active = rc == 0 and out.strip() == "active"
    if persisted:
        if our_active:
            return persisted        # our own live instance serves it
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.bind((bind_ip, persisted))
        except OSError:
            # a ~/.claude migrated from another box carries THAT box's port —
            # here it can be a DIFFERENT user's live file-drop (montalu@subdev
            # inherited dev1's 8789 == marek's subdev port; #33, 2026-07-24).
            # Stale → drop the file and fall through to the probe re-pick.
            print(f"  persisted file-drop port {persisted} is held by another "
                  f"instance on this host (not ours) — dropping "
                  f"{FILEDROP_PORT_FILE} and re-picking")
            FILEDROP_PORT_FILE.unlink(missing_ok=True)
        else:
            return persisted        # still free on THIS host
        finally:
            s.close()
    if our_active:
        return FILEDROP_DEFAULT_PORT     # our own live instance owns the default
    import socket as _socket
    for cand in range(FILEDROP_DEFAULT_PORT, FILEDROP_DEFAULT_PORT + 11):
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.bind((bind_ip, cand))
        except OSError:
            continue
        finally:
            s.close()
        if cand != FILEDROP_DEFAULT_PORT:
            try:
                FILEDROP_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
                FILEDROP_PORT_FILE.write_text(f"{cand}\n")
                print(f"  Port {FILEDROP_DEFAULT_PORT} taken by another file-drop "
                      f"on this host — using {cand} (persisted to {FILEDROP_PORT_FILE})")
            except OSError as e:
                print(f"  could not persist file-drop port choice ({e})",
                      file=sys.stderr)
        return cand
    return FILEDROP_DEFAULT_PORT


def _filedrop_is_live(url, timeout=2):
    """True iff GET <url> returns an HTTP response (root returns 404 by design,
    which still proves the server is up). Any completed request = live."""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # 404 at root is expected — the server answered
    except Exception:
        return False


def _wait_filedrop_live(url, attempts=5, delay=1.0):
    import time
    for _ in range(attempts):
        if _filedrop_is_live(url):
            return True
        time.sleep(delay)
    return False


def _restart_filedrop_service():
    rc, _o, err = _run_systemctl(["restart", "filedrop.service"])
    if rc != 0:
        print(f"  filedrop service restart failed (rc={rc}): {err.strip()}",
              file=sys.stderr)
    return rc == 0


def setup_filedrop_service():
    """Install + start the file-drop systemd --user service on THIS machine.

    Runs on every host (no board-style gating). Creates the served dir first
    (the read-only server never writes it), writes the unit, enables linger, and
    enable --now. On any failure it prints the manual command rather than claiming
    success."""
    import subprocess
    print("  Installing file-drop systemd --user service")

    # 1. served dir (0700) — the read-only server depends on it existing.
    try:
        FILEDROP_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(FILEDROP_DIR), 0o700)
    except OSError as e:
        print(f"  could not create {FILEDROP_DIR} ({e})", file=sys.stderr)

    # 2. write the unit — with the per-user port (a second airuleset user on the
    # same host must not restart-loop on the first user's :8788).
    if not FILEDROP_SERVICE_TEMPLATE.exists():
        print(f"  ERROR: file-drop service template missing: "
              f"{FILEDROP_SERVICE_TEMPLATE}", file=sys.stderr)
        return False
    port = _choose_filedrop_port(filedrop_host_ip())
    FILEDROP_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    FILEDROP_SERVICE_DEST.write_text(_render_filedrop_unit(port))
    print(f"  Wrote unit: {FILEDROP_SERVICE_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "filedrop.service")

    # 3. linger (best-effort)
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    # 4. daemon-reload + enable --now
    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    rc, _o, err = _run_systemctl(["enable", "--now", "filedrop.service"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False

    # 4b. restart to apply the freshly-written unit + latest filedrop code.
    # `enable --now` is a no-op for an already-running service, so a re-install
    # with a changed unit (e.g. a new bind IP) or new code needs an explicit
    # restart. Stateless file server — the brief blip is harmless.
    _run_systemctl(["restart", "filedrop.service"])

    # 5. liveness check on the LAN URL (server binds the LAN IP, not loopback).
    # Built from the port chosen ABOVE — the module-level PORT was resolved at
    # import time, i.e. before a fresh port choice was persisted this run.
    url = f"http://{filedrop_host_ip()}:{port}/"
    if _wait_filedrop_live(url):
        print(f"  File-drop is live. LAN base URL: {url}")
        return True
    print(f"  File-drop service started but did NOT answer on {url}. Check "
          f"`systemctl --user status filedrop.service`.", file=sys.stderr)
    return False


def _current_remote_host_entry():
    """Best-effort match of "the box currently running install" to its own
    REMOTE_HOSTS deploy-target entry (#151) -- keyed on the LOCAL system
    username via the existing `_whoami()` helper. Usernames are unique across
    every REMOTE_HOSTS entry today (newlevel/gatekeeper/montalu/marek/david/
    simap), so this needs no new per-entry data and can't confuse the four
    subdev-VPS users (montalu/marek/david/simap), which all share the SAME
    physical hostname and would be indistinguishable by a hostname-only match.

    Returns None when no entry's `user` matches -- expected on dev1 itself
    (the deploy SOURCE, never listed in REMOTE_HOSTS, and always the primary
    already-configured host in practice, so it essentially never reaches the
    caller's warning branch). Known edge case: dev1's local user is also
    `newlevel`, same as the `dev2` entry -- a mismatch there is harmless
    since dev2 pins no identity either."""
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


def maybe_setup_filedrop():
    """Install the file-drop service on this machine (every host runs one)."""
    setup_filedrop_service()


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


def caveman_mode_or_default(existing) -> str:
    """Pure: keep the user's current caveman mode if it's valid, else fall back
    to the managed default. Never clobbers a valid `/caveman` pick; only repairs
    a missing/empty/garbage mode file."""
    if existing is not None:
        mode = str(existing).strip()
        if mode in VALID_CAVEMAN_MODES:
            return mode
    return CAVEMAN_DEFAULT_MODE


def _caveman_plugin_built() -> bool:
    """True iff claude's OWN plugin registry (installed_plugins.json) has an
    entry for caveman@caveman -- never a cache-file-presence proxy for it.

    ISSUE #279 (2026-08-06): mirrors the sibling registry-truth fix that
    already replaced `_managed_plugin_built()`'s glob check verbatim. The
    OLD check globbed the cache dir for the real statusline script (in
    EITHER cache layout -- old <hash>/hooks/, new <hash>/src/hooks/) and
    treated its mere presence as "genuinely installed". Live evidence
    (montalu4): the cache dir for hash ec83e5bace4c is FULLY extracted --
    matching montalu3's own successful install byte-for-byte, satisfying
    BOTH globs -- while claude's own registry has ZERO entry for
    caveman@caveman: `claude plugin list` correctly reports it ABSENT, but
    the glob said "already built" and setup_caveman()'s `if not
    _caveman_plugin_built(): register + install` silently skipped the real
    `claude plugin install caveman@caveman` call forever, with no log
    output at all. Checking the registry instead makes a
    cache-present + registry-absent mismatch self-healing: the very next
    push retries the real install, no manual fix needed.

    The runtime SHIM's own bash lookup (`ls -dt ... | head -1`, resolving
    the CURRENT cache hash at render time -- a "where do I currently find
    the script" question, unrelated to "is the plugin genuinely installed")
    hardcodes its own two glob literals in CAVEMAN_SHIM_CONTENT and never
    reads this function or any Python constant.

    Adversarial-review confirmation (#279): reproduced the montalu4 shape
    live in an isolated scratch profile (cache dir pre-extracted, no
    installed_plugins.json) -- `claude plugin install caveman@caveman`
    genuinely adopts the pre-existing stale cache rather than choking on it,
    so the self-healing claim above is measured, not merely asserted."""
    return CAVEMAN_PLUGIN_KEY in _plugin_registry_keys()


def setup_caveman() -> bool:
    """Keep the caveman plugin correctly wired on THIS machine (idempotent).

    1. write the stable statusline shim (hash-independent),
    2. reconcile settings.json (enable + marketplace known + statusLine ->
       shim) — runs BEFORE any install attempt below (issue: push: plugin
       installs fail on fresh stream accounts, 2026-08-06 — this used to
       run AFTER the install attempt, so its own settings write landed too
       late to help),
    3. if the plugin's REGISTRY ENTRY is missing (claude's own
       installed_plugins.json — see _caveman_plugin_built()'s docstring;
       never a cache-file glob, #279): register the marketplace (idempotent
       `claude plugin marketplace add` — see ensure_marketplace_registered()'s
       docstring; writing extraKnownMarketplaces alone is not sufficient)
       THEN install (best-effort, time-boxed); a failed registration skips
       the install attempt entirely,
    4. seed a valid `.caveman-active` mode (preserve a valid user pick).
    Returns True iff nothing REQUIRED failed (marketplace registration +
    install, when the registry entry was missing) — see
    setup_managed_plugins()'s docstring for the fatal-vs-non-fatal split
    this return value encodes.
    Every OTHER step here (shim write, settings reconcile, mode seed) stays
    exactly as non-fatal-on-its-own as before."""
    import subprocess
    print("  Wiring caveman plugin (managed)")
    ok = True

    # 1. stable shim — survives `claude plugin update` cache-hash churn.
    try:
        CAVEMAN_SHIM_DEST.write_text(render_caveman_shim())
        os.chmod(str(CAVEMAN_SHIM_DEST), 0o755)
    except OSError as e:
        print(f"    could not write caveman shim ({e})", file=sys.stderr)

    # 2. reconcile settings.json FIRST.
    raw = read_file_safe(SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    settings.json invalid JSON — skipped caveman reconcile", file=sys.stderr)
        settings = None
        ok = False
    if settings is not None:
        new_str = json.dumps(reconcile_caveman_settings(settings), indent=2) + "\n"
        if new_str.strip() != raw.strip():
            if SETTINGS_JSON.exists():
                shutil.copy2(SETTINGS_JSON, SETTINGS_JSON.with_suffix(".json.bak"))
            SETTINGS_JSON.write_text(new_str)
            print("    settings.json: enabled + statusLine -> stable shim")
        else:
            print("    settings.json: already correct")

    # 3. register the marketplace THEN install if the plugin's registry
    #    entry is missing (#279 — never a cache-file glob).
    if not _caveman_plugin_built():
        market = CAVEMAN_PLUGIN_KEY.split("@", 1)[1]
        if not ensure_marketplace_registered(market):
            ok = False
        else:
            try:
                r = subprocess.run(
                    ["claude", "plugin", "install", CAVEMAN_PLUGIN_KEY],
                    capture_output=True, text=True, timeout=120,
                    env=_claude_cli_env())
                if r.returncode == 0:
                    print(f"    installed {CAVEMAN_PLUGIN_KEY}")
                else:
                    print(f"    could not install {CAVEMAN_PLUGIN_KEY} (rc={r.returncode}): "
                          f"{(r.stderr or r.stdout).strip()[:200]}\n"
                          f"    Run manually: claude plugin install {CAVEMAN_PLUGIN_KEY}",
                          file=sys.stderr)
                    ok = False
            except Exception as e:
                print(f"    caveman install skipped ({e}); run: "
                      f"claude plugin install {CAVEMAN_PLUGIN_KEY}", file=sys.stderr)
                ok = False

    # 4. seed a valid mode (preserve a valid user choice).
    # Adversarial-review MINOR finding: this read used to sit OUTSIDE any
    # try/except — an OSError here (e.g. the mode file replaced by a
    # directory) would propagate straight out of setup_caveman() UNCAUGHT,
    # past cmd_install()'s own outer try/except (which just prints
    # "(non-fatal)"), silently losing any `ok = False` step 3 already
    # recorded and letting "Install complete." ship anyway. Never touches
    # `ok` itself — a mode-read failure alone stays non-fatal, exactly as
    # before; it just can no longer SWALLOW a real tracked failure.
    try:
        existing = CAVEMAN_MODE_FILE.read_text() if CAVEMAN_MODE_FILE.exists() else None
    except OSError as e:
        print(f"    could not read caveman mode ({e})", file=sys.stderr)
        existing = None
    mode = caveman_mode_or_default(existing)
    if existing is None or existing.strip() != mode:
        try:
            CAVEMAN_MODE_FILE.write_text(mode)
            print(f"    mode: {mode}")
        except OSError as e:
            print(f"    could not write caveman mode ({e})", file=sys.stderr)

    return ok


def maybe_setup_caveman() -> bool:
    """Wire the caveman plugin on this machine (every host)."""
    return setup_caveman()


# ---------------------------------------------------------------------------
# Managed baseline plugins (every host) — see MANAGED_PLUGINS up top
# ---------------------------------------------------------------------------

def _claude_cli_env() -> dict:
    """Env for invoking the `claude` CLI from install: a push's remote install
    runs in a NON-LOGIN ssh shell whose PATH lacks ~/.local/bin — where the CLI
    lives — so a bare subprocess call dies with [Errno 2] 'claude' (seen live
    on the gatekeeper migration, 2026-07-05). Prepend it idempotently."""
    local_bin = str(Path.home() / ".local" / "bin")
    path = os.environ.get("PATH", "")
    if local_bin not in path.split(":"):
        path = f"{local_bin}:{path}" if path else local_bin
    return {**os.environ, "PATH": path}


def _claude_cli_installed(env: dict = None) -> bool:
    """True iff the `claude` CLI binary itself resolves on PATH (repaired via
    _claude_cli_env — a non-login ssh shell's raw PATH lacks ~/.local/bin,
    where the official installer puts it). Never just "a file exists at the
    expected spot" — `shutil.which` also confirms it's executable, same
    discipline as `_playwright_browsers_installed`'s guard against a
    partial/interrupted install looking permanently "done".

    Falls back to a real LOGIN shell's own `command -v claude` (sources
    `.profile`/nvm/etc — whatever PATH machinery an account actually uses,
    which `_claude_cli_env`'s hand-repaired PATH cannot anticipate) before
    declaring the binary truly absent. An adversarial review flagged that
    an account with `claude` resolvable ONLY via login-shell-only PATH
    machinery would otherwise read as missing and get a SECOND, shadowing
    native install laid down on top by ensure_claude_cli_installed()."""
    import shutil
    import subprocess
    e = env or _claude_cli_env()
    if shutil.which("claude", path=e.get("PATH", "")) is not None:
        return True
    try:
        r = subprocess.run(["bash", "-lc", "command -v claude"],
                            capture_output=True, text=True, timeout=10, env=e)
        return r.returncode == 0 and r.stdout.strip() != ""
    except Exception:
        return False


def ensure_claude_cli_installed(env: dict = None):
    """Best-effort, time-boxed, non-fatal install of the `claude` CLI BINARY
    itself, via Anthropic's own public installer (#263: three subdev stream
    accounts — montalu2/montalu3/montalu4 — had every OTHER piece airuleset
    manages (the launcher wrapper script, the ~/.bashrc marks, ~/.claude/
    CLAUDE.md) but `which claude` came back empty/rc=1, because nothing in
    push/install has ever installed the BINARY — only the WRAPPER around it
    (apply_ultracode_launcher's script just `exec`s `claude`, silently
    assuming it already resolves).

    `curl -fsSL https://claude.ai/install.sh | bash` needs NO login/OAuth for
    the install step itself — confirmed by reading the full script (it
    downloads + checksum-verifies a versioned binary, then runs `<binary>
    install` to lay down the launcher; the human OAuth step only happens on
    the FIRST interactive `claude` invocation) and by every already-working
    peer account's identical `~/.local/bin/claude -> ~/.local/share/claude/
    versions/<ver>` symlink shape (live-verified: montalu, marek, david,
    simap). This function only installs the BINARY — it never attempts the
    OAuth login itself; `ensure_stream_tmux_session()` launches `claude` into
    a session where a human can complete that later.

    Same shape as `ensure_playwright_browsers()`: no sudo needed (installs
    under $HOME), so this runs on the sudo-less subdev stream accounts too,
    and fleet-wide (a harmless no-op wherever `claude` already resolves) —
    enabling a plugin/wrapper is not the same as provisioning the runtime
    dependency it wraps (#158's own lesson, applied here to the binary
    itself rather than a plugin's downloaded assets)."""
    import subprocess
    e = env or _claude_cli_env()
    if _claude_cli_installed(e):
        return
    try:
        # `set -o pipefail`: without it, a curl failure (bad network, DNS,
        # the download host down) is MASKED by bash's own exit code (which
        # is the LAST command in the pipe, `bash`'s own — live-verified:
        # `curl <invalid-url> | bash` exits 0 even though curl failed).
        # `_claude_cli_installed(e)` already catches the resulting failure
        # correctly (the binary genuinely isn't there), but the printed
        # "rc=0" in that case is actively misleading to whoever reads it.
        r = subprocess.run(
            ["bash", "-c",
             "set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash"],
            capture_output=True, text=True, timeout=180, env=e)
        if r.returncode == 0 and _claude_cli_installed(e):
            print("    claude CLI: installed (curl -fsSL "
                  "https://claude.ai/install.sh | bash)")
        else:
            print("    ⚠ claude CLI MISSING and auto-install failed (rc=%s): "
                  "%s\n    Install manually: curl -fsSL "
                  "https://claude.ai/install.sh | bash"
                  % (r.returncode, (r.stderr or r.stdout).strip()[:300]),
                  file=sys.stderr)
    except Exception as ex:
        print("    ⚠ claude CLI MISSING and auto-install skipped (%s) — "
              "install manually: curl -fsSL https://claude.ai/install.sh | "
              "bash" % ex, file=sys.stderr)


FFMPEG_STATIC_URL = ("https://johnvansickle.com/ffmpeg/releases/"
                      "ffmpeg-release-amd64-static.tar.xz")
# ~/.local/bin, NOT ~/bin (#275 adversarial-review MAJOR-2): only
# `~/.profile` (a LOGIN shell) adds `~/bin` to PATH, but a Claude Code Bash
# tool call is NOT one — `~/.local/bin` is the one directory this repo's own
# managed claude launcher ALREADY prepends to PATH on every invocation (see
# the `case ":$PATH:" in ...` line above, "claude installs to ~/.local/bin"),
# so every Bash tool call inside a session started that way already has it,
# with zero new PATH machinery needed.
FFMPEG_STATIC_BIN_DIR = Path.home() / ".local" / "bin"
FFMPEG_STATIC_DEST = FFMPEG_STATIC_BIN_DIR / "ffmpeg"
# skills/meeting-analysis/scripts/extract.sh hard-fails at `command -v
# ffprobe` too (#275 adversarial-review MAJOR-1) -- ffmpeg alone leaves
# Phase 1 broken on the no-sudo accounts. The static tarball already
# contains both binaries in the ONE download; only one extra `cp` is needed.
FFPROBE_STATIC_DEST = FFMPEG_STATIC_BIN_DIR / "ffprobe"


def _binary_reachable(dest: Path, which_name: str) -> bool:
    """True iff `dest` is a genuinely executable file, or `which_name` (the
    bare command name the skill invokes, e.g. "ffmpeg"/"ffprobe") is
    otherwise on PATH already -- a system package, or a prior install here
    done BY HAND (montalu already installed a static ffmpeg before this
    function existed, #275; checking the destination path directly, not
    just PATH, is what recognizes that as already-done instead of
    reinstalling over it)."""
    if dest.is_file() and os.access(dest, os.X_OK):
        return True
    return shutil.which(which_name) is not None


def _ffmpeg_available(dest: Path = None, probe_dest: Path = None) -> bool:
    """True iff BOTH ffmpeg AND ffprobe are already reachable -- the skill's
    own extraction step needs both (#275 review MAJOR-1); ffmpeg alone
    being present is not "available" for this skill's purposes."""
    d = dest or FFMPEG_STATIC_DEST
    p = probe_dest or FFPROBE_STATIC_DEST
    return _binary_reachable(d, "ffmpeg") and _binary_reachable(p, "ffprobe")


def ensure_ffmpeg_static_binary(dest: Path = None, probe_dest: Path = None):
    """Best-effort, time-boxed, non-fatal static-ffmpeg(+ffprobe) install
    into `~/.local/bin` (#275): the subdev stream accounts have NO sudo at
    all, so `check_runtime_deps()`'s `apt-get install` path can never run
    there -- montalu already worked around this by hand, and montalu2/
    montalu3/montalu4 (and any future stream account) hit the identical
    wall the moment they run meeting-analysis. A per-user install needs no
    privilege at all.

    Same shape as `ensure_claude_cli_installed()`: ONE subprocess call does
    download + extract + place + chmod, so this needs no real network call
    or real tar archive to test -- only the constructed shell command and
    the subprocess's own returncode are asserted.

    The extract+chmod step writes into a SCRATCH subdirectory of the FINAL
    destination dir (never the final path directly), then `mv`s both
    binaries into place only once BOTH are confirmed extracted and
    chmod'd (#275 adversarial-review MAJOR-3): `cp` into a live destination
    path creates the target file with its final executable mode BEFORE its
    content is fully written, so a hard-killed subprocess (this call's own
    180s `timeout=` sends SIGKILL, which no shell `trap` can intercept)
    could otherwise leave a truncated-but-"executable" binary that
    `_ffmpeg_available()` would then report as done FOREVER. Placing the
    scratch dir under the SAME parent as the final destination (rather than
    a separate `/tmp`) keeps the final `mv` an atomic same-filesystem
    rename, not a cross-device copy.

    Harmless no-op wherever both binaries are already reachable (dev1/dev2/
    gatekeeper's system packages, or an already-completed prior run here --
    including montalu's own hand-installed ffmpeg)."""
    import subprocess
    import shlex
    d = dest or FFMPEG_STATIC_DEST
    p = probe_dest or FFPROBE_STATIC_DEST
    if _ffmpeg_available(d, p):
        return
    script = (
        "set -o pipefail; "
        "mkdir -p %s && "
        "TMP=$(mktemp -d -p %s) && trap 'rm -rf \"$TMP\"' EXIT && "
        "curl -fsSL %s | tar -xJ -C \"$TMP\" && "
        "MBIN=$(find \"$TMP\" -type f -name ffmpeg -perm -u+x | head -1) && "
        "PBIN=$(find \"$TMP\" -type f -name ffprobe -perm -u+x | head -1) && "
        "[ -n \"$MBIN\" ] && [ -n \"$PBIN\" ] && "
        "cp \"$MBIN\" \"$TMP/ffmpeg.new\" && cp \"$PBIN\" \"$TMP/ffprobe.new\" && "
        "chmod 755 \"$TMP/ffmpeg.new\" \"$TMP/ffprobe.new\" && "
        "mv \"$TMP/ffmpeg.new\" %s && mv \"$TMP/ffprobe.new\" %s"
    ) % (shlex.quote(str(d.parent)), shlex.quote(str(d.parent)),
         shlex.quote(FFMPEG_STATIC_URL), shlex.quote(str(d)), shlex.quote(str(p)))
    try:
        r = subprocess.run(["bash", "-c", script],
                            capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and _ffmpeg_available(d, p):
            print("    ffmpeg: installed static ffmpeg+ffprobe -> %s" % d.parent)
        else:
            print("    ⚠ ffmpeg static install failed (rc=%s): %s\n"
                  "    Install manually: curl -fsSL %s | tar -xJ -C /tmp && "
                  "cp /tmp/*/ffmpeg %s && cp /tmp/*/ffprobe %s && "
                  "chmod 755 %s %s"
                  % (r.returncode, (r.stderr or r.stdout).strip()[:200],
                     FFMPEG_STATIC_URL, d, p, d, p),
                  file=sys.stderr)
    except Exception as e:
        print("    ⚠ ffmpeg static install skipped (%s) — "
              "install manually: curl -fsSL %s | tar -xJ -C /tmp && "
              "cp /tmp/*/ffmpeg %s && cp /tmp/*/ffprobe %s && chmod 755 %s %s"
              % (e, FFMPEG_STATIC_URL, d, p, d, p), file=sys.stderr)


def reconcile_managed_plugins(settings: dict) -> dict:
    """Pure: return a new settings dict with every managed baseline plugin
    enabled, every MANAGED_DISABLED_PLUGINS key forced off (#39 item 3), and
    every marketplace those plugins live in REGISTERED in
    extraKnownMarketplaces (belt-and-suspenders alongside `claude plugin
    marketplace add` in setup_managed_plugins() — a fresh account has no
    marketplace registered at all otherwise; see MARKETPLACE_SOURCES).
    Every other key preserved untouched; idempotent."""
    result = dict(settings)
    enabled = dict(result.get("enabledPlugins", {}))
    for key in MANAGED_PLUGINS:
        enabled[key] = True
    for key in MANAGED_DISABLED_PLUGINS:
        enabled[key] = False
    result["enabledPlugins"] = enabled
    markets = dict(result.get("extraKnownMarketplaces", {}))
    for name in _marketplace_names_for(MANAGED_PLUGINS):
        repo = MARKETPLACE_SOURCES.get(name)
        if repo is not None:
            markets[name] = {"source": {"source": "github", "repo": repo}}
    result["extraKnownMarketplaces"] = markets
    return result


def _plugin_registry_keys(registry_path: Path = None) -> set:
    """Read claude's OWN plugin registry — `~/.claude/plugins/
    installed_plugins.json`, the exact backing store `claude plugin list`
    renders its output from (confirmed live, dev1: the registry's `plugins`
    dict keys match `claude plugin list`'s printed plugin names 1:1;
    shape `{"version": N, "plugins": {"<key>@<marketplace>": [{...}]}}`) —
    and return the set of `plugin@marketplace` keys it genuinely knows
    about. `registry_path` defaults to `CLAUDE_DIR / "plugins" /
    "installed_plugins.json"`, read at CALL time (never a precomputed
    constant) so patching `CLAUDE_DIR` in a test works exactly like it
    already does for every other CLAUDE_DIR-derived path in this file.
    Missing file / unreadable file (a directory, permission-denied,
    invalid UTF-8) / unparsable JSON / a `plugins` field that isn't a dict
    — all degrade to an empty set. Never guess a plugin is installed just
    because the registry can't be read (issue #276; the unreadable-file
    case is an adversarial-review MAJOR finding — `read_file_safe()`'s
    `exists()` -> `read_text()` only catches a MISSING file, so a path
    that EXISTS but genuinely cannot be read used to raise UNCAUGHT here,
    escaping `_managed_plugin_built()` at `setup_managed_plugins()`'s own
    `if _managed_plugin_built(key): continue` — which sits OUTSIDE the
    per-plugin try/except — so `cmd_install()`'s outer try/except silently
    swallowed it as "(non-fatal)": remaining plugins never ran, yet
    "Install complete." was still reported)."""
    path = registry_path or (CLAUDE_DIR / "plugins" / "installed_plugins.json")
    try:
        raw = read_file_safe(path)
    except (OSError, UnicodeDecodeError) as e:
        print(f"    warning: cannot read plugin registry {path} ({e})",
              file=sys.stderr)
        return set()
    if not raw.strip():
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return set(plugins.keys()) if isinstance(plugins, dict) else set()


def _managed_plugin_built(key: str) -> bool:
    """True iff claude's OWN plugin registry (installed_plugins.json) has
    an entry for this plugin key — never a proxy for it.

    ISSUE #276 (2026-08-06): the OLD check globbed for a cache file (e.g.
    playwright's `.mcp.json` under `plugins/cache/.../*/`) and treated its
    mere presence as "genuinely installed". A stale/partial cache dir left
    by a FAILED pre-#273 install (before marketplace registration existed,
    `claude plugin install` used to fail "not found in marketplace" after
    already half-extracting files) satisfies that glob while claude's own
    registry — and `claude plugin list` — correctly report the plugin
    ABSENT: settings.json says enabled, but `setup_managed_plugins()`'s
    `if _managed_plugin_built(key): continue` silently skipped the real
    `claude plugin install` forever (montalu2/montalu3: playwright never
    installed; montalu4: zero plugins ever installed this way). Checking
    the registry instead makes a settings-enabled + registry-absent
    mismatch self-healing: the very next push retries the real install,
    with no manual fix needed on any of the three stuck accounts.

    (Playwright's real cache layout, for context: a literal "unknown"
    version segment rather than a content hash, with `.mcp.json` — the
    actual load-bearing file for its MCP server — as the last thing written
    by a completed extraction, never the `.claude-plugin/plugin.json`
    manifest alone; #158 review finding. None of that matters to THIS
    check any more — it is entirely superseded by the registry read.)"""
    return key in _plugin_registry_keys()


PLAYWRIGHT_PLUGIN_KEY = "playwright@claude-plugins-official"
PLAYWRIGHT_BROWSER_CACHE = Path.home() / ".cache" / "ms-playwright"


def _playwright_browsers_installed(cache_dir: Path = None) -> bool:
    """True iff the browser cache genuinely has something in it — not just
    that the directory exists (an empty dir from an interrupted install
    would otherwise look 'done' forever)."""
    d = cache_dir or PLAYWRIGHT_BROWSER_CACHE
    return d.is_dir() and any(d.iterdir())


def ensure_playwright_browsers(cache_dir: Path = None):
    """Best-effort, time-boxed, non-fatal `npx playwright install chromium`
    (#158 review finding): enabling the plugin alone does NOT pull the
    actual browser binaries — measured live, three fleet accounts had node
    and the plugin enabled but an EMPTY browser cache, so every real browser
    call would fail with "Executable doesn't exist" until someone ran this
    by hand. No sudo needed (a per-user cache under $HOME), so this runs
    even on the sudo-less subdev stream accounts. A no-op when the baseline
    doesn't include Playwright, or the cache is already populated."""
    import subprocess
    if PLAYWRIGHT_PLUGIN_KEY not in MANAGED_PLUGINS:
        return
    if _playwright_browsers_installed(cache_dir):
        return
    try:
        r = subprocess.run(
            ["npx", "--yes", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=300, env=_claude_cli_env())
        if r.returncode == 0:
            print("    Playwright browsers: installed chromium (npx playwright install)")
        else:
            print("    ⚠ Playwright browsers missing and auto-install failed "
                  "(rc=%d): %s\n    Run manually: npx playwright install chromium"
                  % (r.returncode, (r.stderr or r.stdout).strip()[:200]),
                  file=sys.stderr)
    except Exception as e:
        print("    ⚠ Playwright browsers missing and auto-install skipped (%s) — "
              "run manually: npx playwright install chromium" % e, file=sys.stderr)


def setup_managed_plugins() -> bool:
    """Ensure the managed baseline plugins are installed + enabled (idempotent).

    1. reconcile settings.json (enabledPlugins keys true + marketplaces
       registered) — runs FIRST, before any install attempt below (issue:
       push: plugin installs fail on fresh stream accounts, 2026-08-06 —
       reconciling AFTER install, as this used to, means the settings write
       lands too late to help the very install call it's meant to unblock),
    2. for every plugin whose REGISTRY ENTRY is missing (claude's own
       installed_plugins.json — see _managed_plugin_built()'s docstring;
       never a cache-file glob, #276): register its marketplace
       (idempotent `claude plugin marketplace add` — see
       ensure_marketplace_registered()'s docstring) THEN install it
       (best-effort, time-boxed). Installing without a registered
       marketplace only reproduces the "not found in marketplace" failure,
       so a failed registration skips that plugin's install attempt
       entirely rather than trying anyway.
    Returns True iff nothing REQUIRED failed (marketplace registration and
    install, for every plugin whose registry entry was missing) — a still-failing
    plugin install after correct marketplace registration is a genuine
    failure the caller (cmd_install) turns into a non-zero exit, per
    script-failure-policy. The other best-effort step here
    (ensure_playwright_browsers) is unaffected — it stays exactly as
    non-fatal as it already was."""
    import subprocess
    print("  Wiring managed baseline plugins")
    ok = True

    raw = read_file_safe(SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    settings.json invalid JSON — skipped plugin reconcile",
              file=sys.stderr)
        settings = None
        ok = False
    if settings is not None:
        new_str = json.dumps(reconcile_managed_plugins(settings), indent=2) + "\n"
        if new_str.strip() != raw.strip():
            if SETTINGS_JSON.exists():
                shutil.copy2(SETTINGS_JSON, SETTINGS_JSON.with_suffix(".json.bak"))
            SETTINGS_JSON.write_text(new_str)
            print(f"    settings.json: enabled {', '.join(MANAGED_PLUGINS)}")
        else:
            print("    settings.json: already correct")

    market_ok = {}
    for key in MANAGED_PLUGINS:
        # Adversarial-review MINOR finding: `_marketplace_names_for`
        # deliberately tolerates a bare (no "@") key, but `key.split("@",
        # 1)[1]` a few lines below is unguarded — a raw IndexError there
        # would be swallowed by cmd_install()'s own outer try/except as
        # "(non-fatal)", with `ok`/`install_failed` never set, silently
        # reporting "Install complete." (`_managed_plugin_built()`, called
        # next, is a pure registry-membership check and can't raise on a
        # bare key — #276 — but the split below still can.) Check BEFORE
        # that call, not after. A bare key is a real misconfiguration of
        # MANAGED_PLUGINS; report it loudly and keep processing the rest.
        if "@" not in key:
            print(f"    skipping malformed plugin key {key!r} (missing "
                  f"'@marketplace')", file=sys.stderr)
            ok = False
            continue
        if _managed_plugin_built(key):
            continue
        market = key.split("@", 1)[1]
        if market not in market_ok:
            market_ok[market] = ensure_marketplace_registered(market)
        if not market_ok[market]:
            ok = False
            continue
        try:
            r = subprocess.run(
                ["claude", "plugin", "install", key],
                capture_output=True, text=True, timeout=180,
                env=_claude_cli_env())
            if r.returncode == 0:
                print(f"    installed {key}")
            else:
                print(f"    could not install {key} (rc={r.returncode}): "
                      f"{(r.stderr or r.stdout).strip()[:200]}\n"
                      f"    Run manually: claude plugin install {key}",
                      file=sys.stderr)
                ok = False
        except Exception as e:
            print(f"    {key} install skipped ({e}); run: "
                  f"claude plugin install {key}", file=sys.stderr)
            ok = False

    ensure_playwright_browsers()
    return ok


def _filedrop_serve():
    """Run the file-drop HTTP server in the FOREGROUND (systemd ExecStart target)."""
    from filedrop.server import run_server
    hosts_env = os.environ.get("FILEDROP_HOSTS", "").strip()
    hosts = [h for h in hosts_env.split(",") if h] or None
    run_server(host=filedrop_host_ip(), port=FILEDROP_PORT, hosts=hosts)


def cmd_share(args):
    """Copy a file into the file-drop server and print its clickable LAN URL.

    Prints ONLY the URL on stdout (easy to copy); diagnostics go to stderr. Per
    no-localhost-urls.md, the URL is live-checked before printing — if the server
    is down it tries one restart, and refuses to print a dead URL."""
    from urllib.parse import urlsplit

    from filedrop import advertise_urls
    from filedrop.share import ShareError, share
    try:
        url, dest = share(args.path)
    except ShareError as e:
        print(f"share: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"share: unexpected error ({e})", file=sys.stderr)
        sys.exit(1)

    if not _filedrop_is_live(url):
        # Down — try a single restart, then re-check the primary before printing.
        print("share: file-drop not responding — attempting service restart...",
              file=sys.stderr)
        _restart_filedrop_service()
        if not _wait_filedrop_live(url):
            print(f"share: file copied to {dest} but the file-drop server is DOWN at "
                  f"{filedrop_url()} — start it with "
                  f"`systemctl --user start filedrop.service`.", file=sys.stderr)
            sys.exit(1)

    # Primary is live — print ONE URL per private interface (tailscale + LAN) that
    # actually answers, so the user has a working link whichever network they are on.
    sp = urlsplit(url)
    reachable = [u for u in advertise_urls(port=sp.port, path=sp.path)
                 if _filedrop_is_live(u)]
    for u in (reachable or [url]):
        print(u)


def _filedrop_status():
    url = filedrop_url()
    live = _filedrop_is_live(url)
    print(f"file-drop: {url}")
    print(f"  this machine: serves {FILEDROP_DIR}")
    print(f"  liveness:     {'UP' if live else 'DOWN / unreachable'}")


def cmd_filedrop(args):
    """File-drop control: --serve (daemon), --url (live-check + print), status."""
    if getattr(args, "serve", False):
        _filedrop_serve()
        return
    if getattr(args, "url", False):
        url = filedrop_url()
        if _filedrop_is_live(url):
            print(url)
        else:
            print(f"file-drop: DOWN — {url} unreachable", file=sys.stderr)
            sys.exit(1)
        return
    _filedrop_status()


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
                        notification_channel, resolve_owner,
                        resolve_project_channel, resolve_questions_channel,
                        send)

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
                             args.cwd, question=q_text)
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

    if getattr(args, "mention_prefix", False):
        sys.stdout.write(mention_prefix())
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
        from notify import compose_api_error_alert, is_api_error
        text = args.text or ""
        if not is_api_error(text):
            return  # not a real API error → say nothing (no false ping)
        import hashlib
        project = args.project or ""
        sess = args.session or ""
        h = hashlib.sha1(text.strip().encode()).hexdigest()[:12]
        # One ping per distinct error text per session (a wedge that keeps showing
        # the same error across Stop events pings once, not every turn).
        dedup = args.dedup_key or ("apierr:%s:%s" % (sess, h))
        body = compose_api_error_alert(project, text)
        # #369: route via the SAME --project the caller already computed for
        # the alert body's own label — routes this alert to its per-project
        # Discord thread instead of the shared owner channel.
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
            gk = sum(1 for n_num in rows if handed.get(n_num))
            entry["open"] = None if failed else (len(rows) - gk)
            entry["gk"] = None if failed else gk
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
            entry["open"] = None if u_failed else len(seen)
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
    count (#191 M2)."""
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


def _run_card_refuse(name, issue, dry_run, log_reason, stderr_detail):
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
    print("notify --run-card: REFUSED (%s) — %s" % (key, stderr_detail),
          file=sys.stderr)
    sys.exit(1)


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
            return  # need --repo + --issue to build a card

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
            # Full-authority: scope to the CORE slice — never the whole-repo
            # count. A whole-repo 'ostáva 72' next to a core 'done' silently
            # divides two populations; the 'core' word (scope_label below)
            # states which population it is, so the number is
            # self-describing regardless of what else counts as "done" on
            # this box.
            #
            # #367 (2026-08-11): the FOOTER's own `I N` now counts the wider
            # OBLIGATION set (`_obligation_quals()` — core partition UNIONED
            # with every open needs-gatekeeper/ready-for-review ticket, since
            # only this box can act on those), while this card's `remaining`
            # deliberately stays scoped to the narrower CORE partition alone
            # — the two numbers are NOT the same any more, and the old claim
            # that scoping here "makes the two agree by construction" no
            # longer holds. Left this way on purpose rather than widened to
            # match: #367 was scoped to the statusline footer specifically,
            # this card already self-labels its own population with the
            # 'core' word, and widening it would be a separate design call
            # about what a completion card should report, not a footer fix.
            excl = _core_search_excl()
            # -L 1000, not 200 (#181 I5, round 2): the same clamp-difference
            # arithmetic that used to understate the pre-#367 footer's
            # `streamy` bucket also understated `remaining` here.
            rem_raw = _gh_out("issue", "list", "-R", repo, "--state", "open",
                              "--search", AUTOPILOT_SKIP_EXCL + " " + excl,
                              "-L", "1000", "--json", "number", "-q", "length",
                              timeout=20)
            try:
                remaining = int(rem_raw)
            except (TypeError, ValueError):
                remaining = None
            scope_label = "core"

        # Dedup / log key = the bare repo NAME (the LAST path segment of
        # `--repo`) — computed here (moved up from just before the send()
        # call) so a #272 content-validation refusal uses the SAME key
        # every other run-card log line already does, never the full
        # "owner/repo" form (round-2 adversarial review finding).
        name = str(repo).rstrip("/").split("/")[-1]
        raw_goal = getattr(args, "goal", None)
        raw_achieved = getattr(args, "achieved", None) or getattr(args, "result", None)
        dry_run = getattr(args, "dry_run", False)
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
            _run_card_refuse(
                name, issue, dry_run,
                log_reason="achieved is missing or generic",
                stderr_detail="achieved %r is missing or generic — pass a "
                              "real --achieved describing what actually "
                              "landed and was verified" % raw_achieved)
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

# Per-remote SSH deploy timeout (#263): `cmd_install()` now includes best-
# effort network-heavy provisioning that can legitimately take well over the
# old 60s bound on a first-time run — the sum of the INNER timeouts a single
# install can burn through: check_runtime_deps()'s `apt-get install` (300s
# PER missing package), ensure_claude_cli_installed()'s curl-install (180s),
# and ensure_playwright_browsers()'s npx install (300s) — up to ~780s of
# best-effort network work alone before any of the ordinary install steps.
# An adversarial review of the first version of this fix caught it set at
# 300s, LESS than that inner sum: on the exact scenario #263 exists for (a
# fresh subdev account missing both claude and the Playwright cache), the
# outer ssh call would hit ITS OWN bound first, sshd would SIGHUP the
# remote's still-running `python3 airuleset.py install` mid-run, and every
# step after the point it was killed (managed plugins, file-drop, the
# api-watchdog timer) would simply never execute on that remote — silently,
# since the timeout branch below reports it as a plain timeout, not as
# "install ran partway and was killed". A remote whose ssh call genuinely
# can't complete inside this window still gets caught below
# (subprocess.TimeoutExpired), never crashes the whole push loop.
#
# Re-sized (adversarial review, plugin-marketplace fix, 2026-08-06):
# ensure_marketplace_registered() adds up to TWO more 150s calls on a fresh
# account (caveman's marketplace + the shared, market_ok-cached
# claude-plugins-official one covering both superpowers and playwright's
# own installs). Worst-case inner sum in sequence: apt-get(300) + claude
# CLI curl(180) + caveman marketplace-add(150) + caveman install(120) +
# managed-plugins marketplace-add(150) + superpowers install(180) +
# playwright install(180) + npx playwright browsers(300) = 1560s. 1800s
# gives real headroom over that (see
# tests/test_dev_env_provisioning.py::TestPushRemoteDeployTimeoutAndStderr
# for the exact sum this must stay above).
REMOTE_DEPLOY_TIMEOUT_S = 1800

# Remote machines that should receive airuleset updates.
# host = the TAILSCALE IP (stable across LAN switches; see #1). Was 10.77.8.134.
REMOTE_HOSTS = [
    {
        "name": "dev2",
        "host": "100.82.64.27",
        "user": "newlevel",
        "repo_path": "~/devel/airuleset",
    },
    {
        # odoo-gatekeeper VPS (prod merge/deploy + hotfix box). Key-based SSH,
        # NOT the shared "newlevel" password — it is a prod-critical host.
        # Migrated 2026-07-07 to Hetzner cx23 "gk.newlevel.media": tailscale
        # IP 100.90.94.41 (node "gatekeeper-cx23", public 88.99.170.148 =
        # gk.newlevel.media). Do NOT use the MagicDNS name "odoo-gatekeeper"
        # — it resolves to a RETIRED node; the previous HostKey box
        # (100.77.52.43 / 202.148.55.31) is retired too.
        "name": "gatekeeper",
        "host": "100.90.94.41",
        "user": "gatekeeper",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # Isolated montalu odoo dev stream — MIGRATED 2026-07-24 from dev1 to
        # the subdev VPS (airuleset#33 + odoo-erp#1895; same box as marek and
        # david: tailscale 100.118.174.27 / MagicDNS "subdev", public
        # subdev.newlevel.media = fallback only — address by tailscale per
        # machine-identities). The old dev1 account (uid 1001) is LOCKED with
        # a ForceCommand redirect notice; /home/montalu on dev1 stays
        # untouched as the rollback backup per the #1895 contract. Unlike
        # marek/david, montalu authorizes the DEFAULT newlevel key (no
        # gatekeeper_access identity — live-verified at the swap).
        #
        # #258 (2026-08-05): this exact key (dev1's own default
        # ~/.ssh/id_ed25519, comment "david grena mac" for unrelated
        # historical reasons — NOT david@subdev's key) got stripped from
        # montalu's authorized_keys by a gatekeeper access review that
        # mistook the misleading comment for the real cross-company
        # david@subdev identity. Restored via root@subdev under a
        # corrected comment. If push to montalu@subdev ever silently
        # starts failing with "Permission denied" again, check
        # authorized_keys FIRST before assuming a code regression.
        "name": "montalu@subdev",
        "host": "100.118.174.27",
        "user": "montalu",
        "repo_path": "~/devel/airuleset",
    },
    {
        # montalu2/montalu3/montalu4 — three MORE full parallel montalu
        # streams (airuleset#251, odoo-erp#2961: "zhodné s dnešným
        # montalu" — same subdev box, same default-key shape, same
        # branch-merge authority). Accounts created by GATEKEEPER (Phase 1
        # of #2961 — SSH access/Hetzner ownership stays with gatekeeper per
        # the user's 2026-08-05 ownership split; airuleset only wires the
        # ALREADY-EXISTING accounts into its own push/authority registries).
        # Live-verified 2026-08-05: all three accounts' default-key push
        # access had to be restored via root@subdev (see the montalu
        # entry's own comment above — same #258 access-cleanup mistake hit
        # montalu2/3/4 too, since they were provisioned from the same
        # authorized_keys template the cleanup rewrote).
        "name": "montalu2@subdev",
        "host": "100.118.174.27",
        "user": "montalu2",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu3@subdev",
        "host": "100.118.174.27",
        "user": "montalu3",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu4@subdev",
        "host": "100.118.174.27",
        "user": "montalu4",
        "repo_path": "~/devel/airuleset",
    },
    {
        # Marek's isolated user — MIGRATED 2026-07-21/22 from the gatekeeper
        # VPS to the dedicated subdev VPS (Hetzner cx33/nbg1 "subdev", project
        # odoo-subdev, id 153587360): tailscale 100.118.174.27 / MagicDNS
        # "subdev", public 116.203.108.177 = subdev.newlevel.media (fallback
        # only — address by tailscale per machine-identities). Old marek@gk
        # account is BLOCKED (ForceCommand notice). Same gatekeeper_access key
        # (authorized_keys byte-copied in the migration). Evidence: airuleset
        # #23 + odoo-erp #1895 hand-over comments.
        "name": "marek@subdev",
        "host": "100.118.174.27",
        "user": "marek",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # David's isolated external-dev user (slovnormal odoo dev stream: no
        # sudo, no prod keys, can't read other homes) — MIGRATED 2026-07-22
        # from the gatekeeper VPS to the same subdev VPS as marek (see the
        # marek@subdev entry above for the box facts). Old david@gk account is
        # BLOCKED (ForceCommand notice). Same gatekeeper_access key.
        "name": "david@subdev",
        "host": "100.118.174.27",
        "user": "david",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # simap — 4th sub-dev stream, on the same subdev VPS as marek/david
        # (Odoo 15 -> Odoo 19 demo for the potential SIMAP client; tracking
        # ticket odoo-erp#2391, registered here via airuleset#143). Built by
        # gatekeeper 2026-07-28: uid 1003, no sudo, home /home/simap (750),
        # own fresh id_ed25519 (not copied from anywhere), authorized_keys =
        # the SAME operator public keys as marek — so it uses the identical
        # gatekeeper_access identity, not montalu's default-key path.
        "name": "simap@subdev",
        "host": "100.118.174.27",
        "user": "simap",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # miva1 -- 5th sub-dev stream, phase-1 isolated, on the same subdev
        # VPS as marek/david/simap (airuleset#300; tracking ticket for the
        # account itself is odoo-erp#3223). Built by gatekeeper: bare linux
        # user + own SSH keypair, read-only GitHub deploy key, `develop`
        # checkout, empty tmux session -- but no airuleset config until this
        # entry lands. Registered with the SAME operator gatekeeper_access
        # identity requirement as marek/david/simap (never montalu's
        # default-key path), matching this ticket's own "same phase-1
        # isolated shape as simap" framing.
        "name": "miva1@subdev",
        "host": "100.118.174.27",
        "user": "miva1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # david2 -- 6th/7th/8th sub-dev streams (airuleset#326, 2026-08-08):
        # THREE MORE parallel david streams, additional capacity for the
        # SAME external slovnormal odoo developer (fork-based, no sudo, no
        # prod keys), provisioned by gatekeeper on the SAME subdev VPS as
        # david itself (odoo-erp#3282). Registered here as a data-only
        # mirror of david's own entry (host + identity requirement) -- the
        # identity ASSUMPTION is unverified for these specific accounts
        # (mirroring david's shape is the registration; it does not confirm
        # THIS account's authorized_keys accepts the same operator key --
        # #300's own precedent for this exact caveat). No ssh was attempted
        # from this worktree to verify it (fail2ban risk, #300).
        "name": "david2@subdev",
        "host": "100.118.174.27",
        "user": "david2",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        "name": "david3@subdev",
        "host": "100.118.174.27",
        "user": "david3",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        "name": "david4@subdev",
        "host": "100.118.174.27",
        "user": "david4",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # montalu5/montalu6/montalu7/montalu8 (airuleset#378,
        # odoo-erp#3642): FOUR MORE full parallel montalu streams, same
        # shape as montalu2/3/4 (airuleset#251) -- same subdev box, same
        # default-key shape (no `identity` entry — the montalu family
        # authenticates via dev1's own default newlevel key, never
        # gatekeeper_access_ed25519), same branch-merge authority. Accounts
        # created by GATEKEEPER, repo side wired per odoo-erp#3642.
        "name": "montalu5@subdev",
        "host": "100.118.174.27",
        "user": "montalu5",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu6@subdev",
        "host": "100.118.174.27",
        "user": "montalu6",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu7@subdev",
        "host": "100.118.174.27",
        "user": "montalu7",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu8@subdev",
        "host": "100.118.174.27",
        "user": "montalu8",
        "repo_path": "~/devel/airuleset",
    },
]


# --- #275: deliver the meeting-analysis Soniox key to every subdev stream
# account, sourced from dev1's own local voiceagent checkout ------------------
SONIOX_KEY_SOURCE = Path.home() / "devel" / "voiceagent" / ".env"


def _soniox_key_line(source: Path = None):
    """Extract ONLY the `SONIOX_API_KEY=...` line out of dev1's voiceagent
    `.env` -- that file carries MANY other unrelated secrets (Discord
    tokens, etc.), so the whole file is never read out, only this one line
    ever leaves this process. Returns None when the source is missing or
    carries no such key -- never raises, so a caller can treat "not found"
    uniformly regardless of the reason."""
    src = source if source is not None else SONIOX_KEY_SOURCE
    if not src.is_file():
        return None
    for line in read_file_safe(src).splitlines():
        line = line.rstrip("\r\n")
        if line.startswith("SONIOX_API_KEY="):
            return line
    return None


def provision_subdev_soniox_key(hosts=None, run=None, source: Path = None,
                                 skip_names=None, control_opts=None):
    """Deliver `~/.soniox.env` (the meeting-analysis skill's canonical,
    UN-guarded Soniox key path -- see skills/meeting-analysis/SKILL.md and
    hooks/block-vault-store-read.sh) to every subdev stream account (#275).

    `control_opts` (#358): the SAME `_ssh_multiplex_opts()` list
    `cmd_push()`'s own deploy loop built for this run, so an account
    contacted here for a SECOND time (already dialed once by the deploy
    loop above) reuses that account's already-authenticated ssh master
    connection instead of paying for a fresh TCP+SSH handshake. Defaults
    to `[]` -- a caller with no multiplexing set up (or a direct test
    call) gets plain, unmultiplexed ssh, byte-identical to before this
    parameter existed.

    The value is piped to each remote via `input=` on the ssh subprocess
    call -- never embedded in argv, never printed by this process -- and
    the source read is scoped to the ONE matching line (`_soniox_key_line`),
    never the whole voiceagent `.env`. Targets are filtered to
    `AUTHORITY_BY_USER`'s keys FIRST, before the source is ever read, so a
    host list with no subdev stream account in it (dev2, gatekeeper) never
    touches the filesystem at all.

    A missing source on this box is a LOUD stderr failure -- every subdev
    target is reported failed, exactly like a real per-host delivery
    failure -- never a silent skip (the gatekeeper Discord `.env` lesson:
    a silent provisioning gap is a dead feature). Returns the list of
    `(remote_name, reason)` failures, mirroring `cmd_push()`'s own
    `failed` accumulator shape.

    `skip_names` (#341): a set of `remote["name"]` values ALREADY known to
    have failed ssh auth this run (`cmd_push()`'s own deploy loop passes its
    own `auth_failed` set here) -- each is skipped with a loud stderr line
    and a `failed` entry, never given a fresh ssh connection. A second
    connection attempt against an account the deploy loop already proved is
    unprovisioned/unreachable is exactly what compounded the fail2ban risk
    this parameter exists to remove; it never touches an account that has
    not already, independently, failed auth THIS run."""
    import subprocess
    import time
    run = run or subprocess.run
    control_opts = list(control_opts or [])
    targets = [h for h in (hosts if hosts is not None else REMOTE_HOSTS)
               if h.get("user") in AUTHORITY_BY_USER]
    if not targets:
        return []

    failed = []
    skip = set(skip_names or ())
    if skip:
        deliverable = []
        for h in targets:
            if h["name"] in skip:
                print("  ⚠ soniox key delivery to %s SKIPPED — its deploy "
                      "leg already failed auth this run (see the FAILED "
                      "line above); not opening a second ssh connection "
                      "against a known-unprovisioned/unreachable account."
                      % h["name"], file=sys.stderr)
                failed.append((h["name"], "skipped-known-auth-failure"))
            else:
                deliverable.append(h)
        targets = deliverable
        if not targets:
            return failed

    line = _soniox_key_line(source)
    if line is None:
        src = source if source is not None else SONIOX_KEY_SOURCE
        print("  ⚠ SONIOX KEY SOURCE MISSING (%s) — skipping ~/.soniox.env "
              "delivery to %d subdev stream account(s). Run `airuleset.py "
              "push` from dev1 (the maintainer box) instead."
              % (src, len(targets)), file=sys.stderr)
        return failed + [(h["name"], "soniox-key-source-missing")
                          for h in targets]

    for remote in targets:
        identity = remote.get("identity")
        if identity:
            # #341: BatchMode=yes -- a failed pubkey attempt (an
            # unprovisioned/misconfigured account) must fail IMMEDIATELY
            # rather than falling through to an interactive password/
            # keyboard-interactive attempt, which is what turned a single
            # "Permission denied" connection into several distinct
            # auth-failure log lines against subdev.
            ssh_prefix = ["ssh", "-i", os.path.expanduser(identity),
                          "-o", "StrictHostKeyChecking=no",
                          "-o", "BatchMode=yes"]
        else:
            # #341: NumberOfPasswordPrompts=1 -- caps a wrong/unprovisioned
            # password attempt to ONE try (openssh's own default is 3,
            # and sshpass happily re-supplies the same password for every
            # re-prompt), so a single sshpass call can burn at most one
            # fail2ban-countable strike instead of up to three.
            ssh_prefix = ["sshpass", "-p", "newlevel", "ssh",
                          "-o", "StrictHostKeyChecking=no",
                          "-o", "NumberOfPasswordPrompts=1"]
        argv = ssh_prefix + control_opts + [
            f"{remote['user']}@{remote['host']}",
            "umask 077; cat > ~/.soniox.env && "
            "chmod 600 ~/.soniox.env"]
        # #358 adversarial-review F2 (MAJOR): this call used to be a
        # single, un-retried attempt -- a connection-closed/reset drop
        # here permanently lost that account's soniox key, the exact hole
        # cmd_push()'s own deploy-loop retry exists to close, one
        # function over. Same bounded target-level retry shape as that
        # loop: only a genuine ssh connection-establishment failure
        # (never an ordinary write failure) gets retried, up to
        # SSH_RETRY_MAX_ATTEMPTS total attempts, with the SAME
        # env-tunable backoff.
        r = None
        exc = None
        for attempt in range(1, SSH_RETRY_MAX_ATTEMPTS + 1):
            exc = None
            try:
                r = run(argv, input=line + "\n", capture_output=True,
                        text=True, timeout=20)
            except Exception as e:
                exc = e
                break
            if r.returncode == 0:
                break
            if (attempt >= SSH_RETRY_MAX_ATTEMPTS
                    or not _is_ssh_transient_failure(r.returncode,
                                                      r.stderr)):
                break
            backoff = _ssh_retry_backoff_s()
            print(f"  transient ssh failure delivering soniox key to "
                  f"{remote['name']} (attempt {attempt}/"
                  f"{SSH_RETRY_MAX_ATTEMPTS}) — retrying in {backoff}s: "
                  f"{r.stderr.strip()[:200]}")
            time.sleep(backoff)
        if exc is not None:
            print("  ⚠ soniox key delivery to %s failed: %s"
                  % (remote["name"], exc), file=sys.stderr)
            failed.append((remote["name"], repr(exc)))
            continue
        if r.returncode != 0:
            print("  ⚠ soniox key delivery to %s failed (rc=%d): %s"
                  % (remote["name"], r.returncode,
                     (r.stderr or "").strip()[:200]), file=sys.stderr)
            failed.append((remote["name"], "rc=%d" % r.returncode))
        else:
            print("    soniox key: delivered to %s" % remote["name"])
    return failed


# #341 adversarial-review F1 (MAJOR, TRIGGERED): a bare `"Permission denied"
# in stderr` substring check misfires on a REMOTE COMMAND's own stderr —
# `subprocess.run(..., capture_output=True)` on an ssh call captures
# whatever the remote process itself prints too (ssh forwards it), and a
# real `git pull` hitting a root-owned file under `.git`, or an
# `airuleset.py install` traceback, can both legitimately contain that
# literal substring with ssh auth completely intact. ssh's OWN client-side
# auth-exhaustion message is structurally distinct on two properties no
# remote process has any reason to reproduce: it always exits 255, and it
# is always literally prefixed "<user>@<host>: " (openssh's
# `permission_denied()`, unchanged for decades). Requiring BOTH closes the
# false-positive without weakening detection of a genuine auth failure.
_SSH_AUTH_DENIED_RX = re.compile(r"(?m)^\S+@\S+: Permission denied")


def _is_ssh_auth_failure(returncode, stderr):
    """True only for ssh's OWN auth-exhaustion failure, never a remote
    COMMAND's stderr that merely happens to contain the same words."""
    return returncode == 255 and bool(_SSH_AUTH_DENIED_RX.search(stderr or ""))


# --- #358: ssh connection reuse + target-level retry with backoff ----------
# Live incident (2026-08-10, this repo's own gatekeeper/subdev push targets):
# a burst of rapid independent ssh handshakes within one push wave (11 of
# REMOTE_HOSTS' 13 entries share the subdev VPS; provision_subdev_soniox_key
# below re-dials several of the SAME accounts a second time in the same run)
# tripped a connection drop against gk with NO retry at all -- one flaky
# target permanently lost the whole wave's deploy to it.
#
# Root cause REVISED after live diagnostics on gk itself (issue comment
# 5245989172, supervisor session via the dev2 jump host): this is NOT a
# per-source ban (fail2ban's own ban list never contained dev1's address)
# and NOT a targeted fail2ban/MaxStartups window against ONE source -- gk's
# sshd has `MaxStartups 10:30:100` with `PerSourceMaxStartups none`, and an
# ongoing internet-wide bruteforce flood (126k+ failed attempts, 60+
# concurrently in flight at diagnosis time) keeps that GLOBAL unauthenticated-
# connection pool saturated, so sshd RANDOMLY drops legitimate connections
# too (the tailscale path shares the identical pool). The observed
# "recovery after 20-40 min" was the bruteforce wave itself ebbing, not a
# ban expiring. This makes a SHORT in-wave retry the directly-correct fix,
# not merely "worth trying anyway": each retry is a genuinely independent
# roll against the SAME saturated pool, with real per-attempt odds of
# landing while the pool has briefly cleared -- never a hopeless repeat
# against a fixed-duration ban.
#
# Same rc==255 + ssh-client-only-message discriminator shape as
# `_is_ssh_auth_failure` above, on purpose: it is the same false-positive
# class (a REMOTE command's own stderr must never be misread as ssh's OWN
# connection-level failure).
#
# #358 adversarial-review F5 (live-reproduced with a network-free
# ProxyCommand trick against the real /usr/bin/ssh binary): two further
# real client-only shapes were originally MISSED --
# `ssh_dispatch_run_fatal: Connection to <host> port <port>: Broken pipe`
# (a proxy that sends a banner then drops -- `ssh_dispatch_run_fatal:` is
# the same class of internal log tag as the two `*_exchange_identification:`
# prefixes, confirmed via `strings` on the real binary) and a bare
# `Connection reset by <host> port <port>` line (confirmed via `strings`
# to be `sshpkt_vfatal`'s own ECONNRESET message, the same shape as the
# already-covered "closed by" line but for the reset case). Both are now
# covered. Honest residual: `ssh_dispatch_run_fatal:` is not exclusively a
# PRE-auth message -- unlike the two `*_exchange_identification:` prefixes
# (which only ever fire before any remote shell exists), it can in
# principle also fire on a MID-session drop. That is still safe here: the
# retried remote command (`git pull --ff-only && ... install`, or the
# soniox `cat > ~/.soniox.env`) is idempotent, so a retry after a
# mid-session drop just repeats a safe operation, never double-applies one.
_SSH_TRANSIENT_RX = re.compile(
    r"(?m)^(?:kex_exchange_identification|ssh_exchange_identification|"
    r"ssh_dispatch_run_fatal):"
    r"|^Connection (?:closed|reset) by \S+ port \d+\s*$"
)

# Bounded: at most this many total attempts (initial + retries) per target.
# 3 (2 retries) -- "a few attempts", matching the revised diagnosis: since a
# drop is a random per-connection event against a saturated pool (not a
# fixed-duration ban), a second retry has real, undiminished odds of success,
# unlike retrying against an actual ban where every attempt is equally
# futile. Kept as a plain constant, not env-tunable -- only the backoff
# between attempts needs to be (see `_ssh_retry_backoff_s`'s own docstring).
SSH_RETRY_MAX_ATTEMPTS = 3

# ControlPersist window for a target's shared ssh master connection.
#
# #358 adversarial-review F1 (MAJOR, measured against the real
# REMOTE_HOSTS list): 60s was sized as "a few seconds to low tens of
# seconds" between an account's deploy call and its LATER soniox-key call
# -- but those two calls are NOT adjacent. montalu@subdev sits at deploy-
# loop index 2 and is also soniox-phase's FIRST target, so its own real
# gap is TEN complete `git pull --ff-only && python3 airuleset.py install`
# runs for every OTHER account still queued in the deploy loop -- a
# budget this file's own REMOTE_DEPLOY_TIMEOUT_S sizes at up to 1560s
# EACH for a slow first-time install (apt-get + claude-CLI curl +
# caveman/managed-plugin marketplace installs + Playwright browsers).
# 60s expired long before that gap in the realistic slow-account case,
# making the multiplexing structurally unable to help the account it was
# sized around. 1800s matches REMOTE_DEPLOY_TIMEOUT_S itself -- a natural,
# already-justified ceiling -- and costs nothing extra: the sockets live
# under a 0700 per-run temp dir the `finally` block deletes regardless of
# how long the underlying master process itself takes to notice and exit,
# so a longer window only ever leaves an IDLE, UNREACHABLE master
# process a little longer, never anything a caller can observe.
SSH_CONTROL_PERSIST_S = 1800


def _is_ssh_transient_failure(returncode, stderr):
    """True only for ssh's OWN connection-establishment failure -- a
    MaxStartups-pool-exhaustion drop mid-handshake (a random per-connection
    event, never a per-source ban -- see the #358 root-cause comment above
    this function). Matches a line starting `kex_exchange_identification:` /
    `ssh_exchange_identification:` / `ssh_dispatch_run_fatal:` (openssh's
    own internal log tags -- a remote command's own stderr structurally
    cannot reproduce them) or a bare `Connection closed|reset by <host>
    port <port>` line with nothing else on it. Requiring rc==255 (mirrors
    `_is_ssh_auth_failure`'s own discriminator) excludes an ordinary
    failed remote command (a bad `git pull`, a crashing `install`), which
    never exits via ssh's own 255 connection-failure path.

    NOT claimed: that every matched message can only ever appear BEFORE a
    remote shell exists (`ssh_dispatch_run_fatal:` is a documented
    exception -- see the classifier regex's own comment) -- only that
    every matched message is genuinely ssh-client-internal, never text a
    remote command could produce on its own."""
    return returncode == 255 and bool(_SSH_TRANSIENT_RX.search(stderr or ""))


def _ssh_retry_backoff_s():
    """`AIRULESET_SSH_RETRY_BACKOFF_S` override for the target-level retry
    delay, clamped to [1, 300]. Unclamped, a misconfigured 0/negative
    value would defeat the whole backoff (the same class of gap #172's own
    `AIRULESET_SWEEP_BUDGET_S` fix closed elsewhere in this file), and an
    absurdly large one would block the WHOLE wave behind one flaky target
    for minutes it will never actually need.

    Default 60s, "order of seconds to ~2 min" per the REVISED diagnosis
    (issue comment 5245989172): the drop is a RANDOM per-connection event
    against gk's globally-saturated MaxStartups unauthenticated-connection
    pool (internet-wide bruteforce noise, not a per-source ban), so each
    retry is a genuinely independent roll with real odds of landing once
    the pool has briefly cleared -- unlike retrying against a real ban,
    where a longer wait would be needed and every short retry would be
    equally futile. Combined with SSH_RETRY_MAX_ATTEMPTS=3 (2 retries),
    the worst-case added time for one exhausted target is ~2 backoffs of
    the default (about 2 min) -- still short enough that one flaky target
    never meaningfully delays the whole semi-foreground wave, and a
    genuinely exhausted target is reported loudly with the exact manual
    re-run command instead of silently vanishing from it.

    Stated explicitly for the WHOLE wave, not just one target (#358
    adversarial-review F7): retries do NOT meaningfully worsen the pool
    saturation (spread >=60s apart per target, a tiny fraction of the
    126k+ ambient bruteforce attempts already hitting gk), but the wave's
    own worst-case wall clock is real and grows with REMOTE_HOSTS' size --
    at today's 13 entries, EVERY target hitting a transient failure once
    adds up to `13 * 2 * 60s` = ~26 minutes at the default backoff, or up
    to `13 * 2 * 300s` = ~130 minutes at the `[1, 300]` clamp ceiling if an
    operator raises the override. This is the honest worst case, not the
    expected one -- the revised root cause means most retries either land
    on their first extra attempt or fail fast, not spend their whole
    backoff every time."""
    raw = os.environ.get("AIRULESET_SSH_RETRY_BACKOFF_S", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = 60
    return max(1, min(300, val))


def _ssh_control_dir_for_push():
    """One short-lived directory for this push run's ssh ControlMaster
    sockets -- multiplexes REPEATED connections to the SAME target within
    one push (the deploy call, then the LATER soniox-key call, #275) onto
    a single already-authenticated master, so the wave opens fewer
    distinct TCP+SSH handshakes against a target's sshd rather than more.
    A UNIX socket path is capped at roughly 104-108 bytes on Linux, so the
    prefix here stays short and ssh's own `%C` (a fixed-length per-target
    connection hash) supplies the per-target uniqueness -- one shared
    directory safely serves every REMOTE_HOSTS entry in the same push; a
    DIFFERENT target (different user/host/port) can never collide on, or
    reuse, another target's socket. Returns None (never raises) when the
    temp dir can't be created, so a caller degrades to plain unmultiplexed
    ssh calls rather than failing the whole push over a missing sandbox
    capability."""
    import tempfile
    try:
        return tempfile.mkdtemp(prefix="arsshcm-")
    except OSError:
        return None


def _ssh_multiplex_opts(control_dir):
    """The `-o ControlMaster=... -o ControlPersist=... -o ControlPath=...`
    triple built from a `_ssh_control_dir_for_push()` result. Returns []
    (plain, unmultiplexed ssh -- never a hard failure) when `control_dir`
    is falsy, e.g. because the temp dir itself could not be created."""
    if not control_dir:
        return []
    return [
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=%ds" % SSH_CONTROL_PERSIST_S,
        "-o", "ControlPath=%s/%%C" % control_dir,
    ]


def _redacted_ssh_cmd(cmd):
    """The same argv `ssh_cmd`/`ssh_prefix` list built for a deploy or
    soniox-key call, with any `sshpass -p <password>` password argument
    replaced -- this is printed into the push LOG as a manual single-
    target retry hint once retries are exhausted (#358), and the shared
    subdev password must never land in a log file (security-basics.md).
    Never mutates the input list."""
    out = list(cmd)
    for i, tok in enumerate(out):
        if tok == "-p" and i > 0 and out[i - 1] == "sshpass" and i + 1 < len(out):
            out[i + 1] = "<REDACTED>"
    return out


# --- #347: push-time guard against a newly-activated subdev stream account
# silently never gaining its own REMOTE_HOSTS registration -------------------
# Root cause of the incident this guards against: david2/david3/david4 were
# provisioned on the shared subdev VPS (odoo-erp#3282) days before airuleset
# gained their REMOTE_HOSTS/AUTHORITY_BY_USER/etc entries (#326) — nothing in
# `push` ever compared "which Linux accounts actually exist on the shared
# box" against "which accounts we think we manage", so the gap was invisible
# until the owner caught it live. montalu2/3/4 (#251/#263) and simap/miva1
# (#143/#300) hit variants of the identical class before that.
_HOME_AUDIT_MARKER = "===AIRULESET-HOMES==="


def _shared_remote_host_ips():
    """Host IPs backed by MORE than one REMOTE_HOSTS entry — boxes where a
    brand-new account can be hand-provisioned without ever gaining its own
    registration entry. A single-entry host (dev2, gatekeeper) has no such
    gap by construction, so auditing it would be pure overhead for zero
    possible finding."""
    seen = {}
    for r in REMOTE_HOSTS:
        h = r.get("host")
        seen[h] = seen.get(h, 0) + 1
    return {h for h, n in seen.items() if n > 1}


def _remote_cmd_with_home_audit(remote_cmd):
    """Append a same-connection `/home` listing to an existing remote
    install command, WITHOUT letting the trailing `ls` change the ssh
    call's own exit code — `;` sequencing after the `&&`-chained install
    steps would otherwise make the LAST command's exit code win, silently
    turning a genuine `git pull`/`install` failure into a false push
    success the moment this audit rides along on that connection. Capture
    the real exit code with `$?` immediately after the original chain, run
    the audit unconditionally, then `exit` with the ORIGINAL code."""
    return (
        remote_cmd
        + "; __ar_rc=$?; echo '%s'; ls -1 /home 2>/dev/null; exit $__ar_rc"
        % _HOME_AUDIT_MARKER
    )


def _parse_home_audit_output(stdout):
    """Extract the `/home` listing appended by `_remote_cmd_with_home_audit`
    from a completed ssh call's stdout. Returns "" when the marker never
    appears — e.g. ssh itself failed before the remote shell ever ran the
    audit at all, which is not this guard's concern (a real auth/timeout
    failure is already tracked by `cmd_push`'s own `failed` list)."""
    text = stdout or ""
    if _HOME_AUDIT_MARKER not in text:
        return ""
    return text.split(_HOME_AUDIT_MARKER, 1)[1]


def _parse_home_names(home_listing):
    """Home directory names from a raw `/home` listing, blank lines
    dropped. Shared by `unregistered_home_accounts` and
    `_home_listing_trustworthy` so both read the same real shape."""
    return {ln.strip() for ln in (home_listing or "").splitlines() if ln.strip()}


def _home_listing_trustworthy(user, home_listing):
    """#347 adversarial-review MAJOR finding (M1): `_HOME_AUDIT_MARKER`
    being present in an ssh call's stdout only proves the remote SHELL
    reached the audit — NOT that `ls -1 /home` itself actually succeeded.
    A hardened or root-owned `/home` makes `ls` fail with its stderr
    redirected to `/dev/null`, returning an EMPTY listing at rc 0 — which
    a marker-only check would read as "checked, no gap found" FOREVER,
    silently and permanently defeating the whole guard. Positive control:
    the very account THIS ssh connection authenticated AS must appear in
    any genuine listing (the same connection just `cd`'d into that
    account's own checkout, so its home directory unquestionably exists
    on this host) — if it's missing, the listing cannot be trusted at
    all, and the caller must fall through to a retry / an honest
    UNVERIFIED report rather than a false "clean"."""
    return user in _parse_home_names(home_listing)


def unregistered_home_accounts(host, home_listing):
    """Home directory names present on `host` (from a real `/home` listing)
    that have NO matching REMOTE_HOSTS entry for that EXACT host — #347's
    push-time guard against a newly-activated stream account silently never
    being registered as a push target. Read-only: the caller decides what
    to do with a non-empty result (a loud, non-blocking warning — refusing
    to push over an unrelated new account would be a worse failure mode
    than the gap this exists to report).

    "lost+found" is excluded unconditionally — a filesystem artifact on a
    `/home` that is its own mount point, never a stream account. Reporting
    it as a "gap" on every single push forever would train the reader to
    ignore the one warning that will someday be real (adversarial-review
    MINOR m2, alarm fatigue)."""
    registered = {r.get("user") for r in REMOTE_HOSTS if r.get("host") == host}
    names = _parse_home_names(home_listing)
    return sorted(names - registered - {"lost+found"})


def cmd_push(args):
    """Push to GitHub and deploy to all remote machines.

    Fail-closed: `ruff check .` runs FIRST, then the full test suite — a lint
    error or a single failing test aborts the push (and therefore the dev2
    deploy) so unlinted/untested code never ships. `git push` here is an
    internal subprocess call, so the PreToolUse pre-push-lint.sh hook (which
    only fires for a real Bash `git push` tool invocation) never sees this
    flow — this in-process gate is what actually protects it (issue #7)."""
    import shlex
    import subprocess
    import time

    # 0a. Lint the whole repo — fail-closed before any push/deploy. Unlike the
    # PreToolUse hook (which lints only the files a real `git push` command
    # changed), this runs from inside the process itself, so a whole-repo
    # check is the only way to guarantee it; keep it fast by keeping the repo
    # clean (see the ruff cleanup commit for #7 — this is cheap post-cleanup).
    print("Running ruff check (fail-closed before push)...")
    try:
        ruff_result = subprocess.run(
            ["ruff", "check", "."],
            cwd=str(REPO_DIR),
        )
    except FileNotFoundError:
        print("  RUFF NOT INSTALLED — refusing to push unlinted code.", file=sys.stderr)
        print("  Install ruff (e.g. `pip install ruff` / `pipx install ruff`) and retry.",
              file=sys.stderr)
        sys.exit(1)
    if ruff_result.returncode != 0:
        print("  RUFF FAILED — refusing to push unlinted code.", file=sys.stderr)
        sys.exit(1)
    print("  Ruff clean.")

    # 0b. Run the full test suite — fail-closed before any push/deploy.
    print("Running test suite (fail-closed before push)...")
    import tempfile
    # #271: `deliver_with_stash`/`_send_goal_verified` persist non-empty
    # input-box content to `watchdog.draft_rescue_dir()` (default
    # `~/.claude/draft-rescue/`) BEFORE any keystroke — and the live
    # systemd api-watchdog timer executes THIS repo's working tree every
    # 60s on this box, so a test process writing into the REAL directory
    # would be indistinguishable from production activity. Rather than
    # isolate every one of the ~19 test files whose fixtures transitively
    # reach these two functions via `run_once`, point the WHOLE test-suite
    # subprocess at one throwaway directory here — `AIRULESET_DRAFT_RESCUE_DIR`
    # is `draft_rescue_dir()`'s own env-override, so this is the single
    # place that has to know it exists.
    with tempfile.TemporaryDirectory() as _rescue_tmp, \
            tempfile.TemporaryDirectory() as _lock_tmp:
        test_env = dict(os.environ)
        test_env["AIRULESET_DRAFT_RESCUE_DIR"] = str(
            Path(_rescue_tmp) / "draft-rescue")
        # #400 owner kill-switch: the pre-push suite must stay green on a
        # box whose owner has genuinely disabled the compact/goal jobs --
        # the flag files are production state, not test state.
        test_env["AIRULESET_TEST_IGNORE_DISABLE"] = "1"
        # #385: `tests/test_autopilot_lock.py` spawns a REAL `autopilot-lock`
        # CLI subprocess on every test, against a fresh never-reused repo
        # path — without this, every push leaves a permanent orphaned lock
        # (plus `.mutex`/symlink/directory artifacts) in the REAL system
        # `/tmp`. `tests/conftest.py`'s `_isolate_autopilot_lock_dir` covers
        # a `pytest`-direct run; `conftest.py` is NOT read by `unittest
        # discover` at all, so this is the single place that has to know
        # `AIRULESET_AUTOPILOT_LOCK_DIR` exists for THIS subprocess.
        test_env["AIRULESET_AUTOPILOT_LOCK_DIR"] = str(
            Path(_lock_tmp) / "autopilot-lock")
        test_result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(REPO_DIR), env=test_env,
        )
    if test_result.returncode != 0:
        print("  TESTS FAILED — refusing to push untested code.", file=sys.stderr)
        sys.exit(1)
    print("  Tests passed.")

    # 1. Push to GitHub
    print("\nPushing to GitHub...")
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED: {result.stderr.strip()}")
        sys.exit(1)
    print(f"  {result.stdout.strip() or result.stderr.strip()}")

    # #263: `failed` accumulates every remote (and now the local install
    # itself) that did NOT deploy cleanly. An adversarial review of the
    # first version of this timeout fix caught a real regression it
    # introduced: BEFORE, an uncaught TimeoutExpired propagated out of
    # cmd_push() with a loud traceback and a non-zero exit — impossible to
    # miss. The `continue` below (needed so one slow remote can't abort
    # deployment to every REMAINING host) turned that into a single line
    # among hundreds, with the run still ending "All deployments complete."
    # at exit 0 — a SILENT partial deploy. Tracking every failure and
    # exiting non-zero if any occurred restores the "impossible to miss"
    # property without reintroducing the abort-the-whole-loop defect.
    failed = []
    # #341: host NAMES whose deploy leg failed with a genuine ssh AUTH
    # failure this run ("Permission denied" — never a timeout, never a
    # plain remote-command failure, both of which mean auth actually
    # succeeded). Passed to provision_subdev_soniox_key() below so it never
    # opens a SECOND connection against an account already proven
    # unprovisioned/unreachable this run — contacting the same known-bad
    # account twice (once here, once again independently in the soniox
    # phase) is what compounded the fail2ban risk this set exists to close.
    auth_failed = set()

    # 2. Install locally
    # Adversarial-review CRITICAL finding (plugin-marketplace fix,
    # 2026-08-06): cmd_install() can now sys.exit(1) on a still-failing
    # managed-plugin install (script-failure-policy). Left uncaught here,
    # that SystemExit propagated straight out of cmd_push() BEFORE the
    # remote-deploy loop below ever ran — git had ALREADY pushed to
    # GitHub by this point, so main would advance and ZERO of the 9 remote
    # hosts (including montalu2/montalu3/montalu4, the very accounts this
    # fix exists for) would ever deploy it. Give the local step the exact
    # same "track it, keep going" treatment the remote loop already gets.
    print("\nInstalling locally...")
    try:
        cmd_install(args)
    except SystemExit as e:
        if e.code:
            print(f"  FAILED: local install exited {e.code} — continuing to remotes")
            failed.append(("local(dev1)", "install rc=%s" % e.code))

    # 3. Deploy to each remote
    # #358: one per-run ssh ControlMaster socket directory, shared by the
    # deploy loop below AND the soniox-key phase (3b) -- multiplexes a
    # REPEATED connection to the SAME target within this one push onto a
    # single already-authenticated master. Degrades to `control_opts = []`
    # (plain, unmultiplexed ssh) if the temp dir can't be created; the
    # `finally` at the end of this block removes it regardless of how the
    # deploy phase finishes (success, a failed target, or a raised
    # SystemExit from the failure-summary branch below).
    control_dir = _ssh_control_dir_for_push()
    control_opts = _ssh_multiplex_opts(control_dir)
    try:
        # #347: audit ANY shared VPS host's /home listing for an account with
        # no REMOTE_HOSTS entry, exactly ONCE per host, riding the FIRST
        # already-happening connection to that host — never a dedicated extra
        # ssh call (#341: never re-probe the same host for a second, unrelated
        # purpose — that is exactly what compounded the fail2ban risk there).
        shared_hosts = _shared_remote_host_ips()
        audited_hosts = set()
        for remote in REMOTE_HOSTS:
            print(f"\n{'=' * 50}")
            print(f"Deploying to {remote['name']} ({remote['host']})...")
            remote_cmd = f"cd {remote['repo_path']} && git pull --ff-only && python3 airuleset.py install"
            # #347 adversarial-review CRITICAL finding: `audited_hosts` must
            # NOT be marked here (before the ssh call even runs) — a first
            # entry that fails (timeout/auth) before the remote shell ever
            # reaches the appended `ls` would then PERMANENTLY skip the audit
            # for the rest of this push, with the failure looking IDENTICAL to
            # "checked, no gap found" (unregistered_home_accounts() on an
            # empty/no-marker listing silently returns []). `audited_hosts` is
            # only marked below, once the marker is CONFIRMED present in the
            # real stdout — so a failed first connection lets the NEXT entry
            # sharing this host retry the audit instead of silently giving up.
            audit_this_call = (remote["host"] in shared_hosts
                                and remote["host"] not in audited_hosts)
            if audit_this_call:
                remote_cmd = _remote_cmd_with_home_audit(remote_cmd)
            identity = remote.get("identity")
            if identity:
                # key-based SSH (e.g. the gatekeeper — prod-critical, no shared
                # password). #341: BatchMode=yes -- a failed pubkey attempt
                # against an unprovisioned/misconfigured account must fail
                # IMMEDIATELY instead of falling through to an interactive
                # password/keyboard-interactive attempt (the "Permission denied
                # (publickey,password)" shape), which is what let a single
                # connection generate several distinct auth-failure log lines.
                ssh_cmd = [
                    "ssh", "-i", os.path.expanduser(identity),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "BatchMode=yes",
                ] + control_opts + [
                    f"{remote['user']}@{remote['host']}",
                    remote_cmd,
                ]
            else:
                # #341: NumberOfPasswordPrompts=1 -- caps a wrong/unprovisioned
                # password attempt to ONE try (openssh's own default is 3, and
                # sshpass happily re-supplies the same password on every
                # re-prompt), so one sshpass call burns at most one
                # fail2ban-countable strike instead of up to three.
                ssh_cmd = [
                    "sshpass", "-p", "newlevel",
                    "ssh", "-o", "StrictHostKeyChecking=no",
                    "-o", "NumberOfPasswordPrompts=1",
                ] + control_opts + [
                    f"{remote['user']}@{remote['host']}",
                    remote_cmd,
                ]
            # #263: never let ONE slow/hanging remote (a first-time claude-CLI
            # curl install, ensure_playwright_browsers()'s npx install) abort
            # deployment to every REMAINING host — the loop used to have no
            # try/except around this call at all, so a TimeoutExpired here
            # propagated straight out of cmd_push() and silently skipped every
            # host still queued after this one.
            #
            # #358: retry ONLY a genuine ssh connection-establishment failure
            # (a MaxStartups-pool-exhaustion drop mid-handshake -- a random
            # per-connection event, never a per-source ban) -- bounded to
            # SSH_RETRY_MAX_ATTEMPTS total attempts, with a short backoff
            # between them. This is a TARGET-LEVEL retry only: it never
            # re-runs step 0a/0b/1 (ruff/tests/git push), which already ran
            # exactly once above, before this loop even started -- a retry
            # here can only ever repeat the SAME ssh_cmd for THIS ONE target.
            timed_out = False
            ssh_result = None
            for attempt in range(1, SSH_RETRY_MAX_ATTEMPTS + 1):
                try:
                    ssh_result = subprocess.run(
                        ssh_cmd,
                        capture_output=True,
                        text=True,
                        timeout=REMOTE_DEPLOY_TIMEOUT_S,
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    break
                if ssh_result.returncode == 0:
                    break
                if (attempt >= SSH_RETRY_MAX_ATTEMPTS
                        or not _is_ssh_transient_failure(
                            ssh_result.returncode, ssh_result.stderr)):
                    break
                backoff = _ssh_retry_backoff_s()
                print(f"  transient ssh failure on {remote['name']} "
                      f"(attempt {attempt}/{SSH_RETRY_MAX_ATTEMPTS}) — "
                      f"retrying in {backoff}s: "
                      f"{ssh_result.stderr.strip()[:200]}")
                time.sleep(backoff)
            if timed_out:
                print(f"  FAILED: timed out after {REMOTE_DEPLOY_TIMEOUT_S}s "
                      f"— continuing to the next host")
                failed.append((remote["name"], "timeout"))
                continue
            if ssh_result.returncode != 0:
                print(f"  FAILED: {ssh_result.stderr.strip()}")
                failed.append((remote["name"], "rc=%d" % ssh_result.returncode))
                # #341: a genuine ssh AUTH failure (never a remote-command
                # failure with auth intact, e.g. a bad `git pull`) marks this
                # host so the soniox phase below skips it instead of opening a
                # second connection against an already-known-bad account.
                if _is_ssh_auth_failure(ssh_result.returncode, ssh_result.stderr):
                    auth_failed.add(remote["name"])
                # #358: an exhausted transient (connection-closed/reset)
                # failure names the exact single-target command to retry
                # manually once the target is reachable again -- never the
                # whole push (which would re-run the full-suite gate for
                # nothing), and never the shared subdev PASSWORD (a
                # redacted placeholder stands in for it, security-basics.md).
                if _is_ssh_transient_failure(ssh_result.returncode,
                                              ssh_result.stderr):
                    hint = " ".join(shlex.quote(c)
                                     for c in _redacted_ssh_cmd(ssh_cmd))
                    print(f"  Manual retry once reachable: {hint}")
            else:
                print(f"  {ssh_result.stdout.strip()}")
                # #263: a successful remote install's STDERR was being silently
                # discarded here — this repo's own ensure_playwright_browsers()
                # warning writes to stderr (RUNTIME_DEPS' and this diff's own
                # report_stream_dev_env() gap warnings write to STDOUT and were
                # already reaching the console via the branch above), so on the
                # common success path that warning never reached the push
                # console. Surface it too, so a gap really is reported loudly
                # rather than swallowed by push's own success branch.
                stderr_out = ssh_result.stderr.strip()
                if stderr_out:
                    print(f"  [stderr] {stderr_out}")

            # #347: report (never block) any home directory on this shared
            # host with no matching REMOTE_HOSTS entry — the systemic guard
            # against a newly-activated stream account silently never being
            # registered as a push target. Advisory only: one unrelated stray
            # /home entry (a not-yet-onboarded or test account) must never
            # abort deployment to every OTHER, already-registered account.
            # `audited_hosts` is marked HERE, only once the marker is
            # positively confirmed present AND the listing itself passes the
            # `_home_listing_trustworthy` positive control (adversarial-review
            # MAJOR M1: the marker alone only proves the remote shell reached
            # the audit, never that `ls -1 /home` actually succeeded — a
            # hardened/root-owned /home fails silently at rc 0 with an EMPTY
            # listing, which would otherwise read as "checked, clean" forever)
            # — so a never-executed OR untrustworthy audit is never mistaken
            # for "ran and found nothing".
            if audit_this_call and _HOME_AUDIT_MARKER in (ssh_result.stdout or ""):
                home_listing = _parse_home_audit_output(ssh_result.stdout)
                if _home_listing_trustworthy(remote["user"], home_listing):
                    audited_hosts.add(remote["host"])
                    gap = unregistered_home_accounts(remote["host"], home_listing)
                    if gap:
                        print(f"\n⚠ REGISTRATION GAP on {remote['host']}: /home has "
                              f"account(s) with NO REMOTE_HOSTS entry: "
                              f"{', '.join(gap)} — a stream account was activated "
                              f"but never registered as a push target. Register "
                              f"it: REMOTE_HOSTS + AUTHORITY_BY_USER + the "
                              f"block-subdev-ssh-misuse.sh allow-list + watchdog's "
                              f"_REDUCED_STREAM_USERS + notify's STREAM_NOTIFY_OWNER "
                              f"(see #251/#263/#300/#326/#347's own onboarding "
                              f"checklist).", file=sys.stderr)

        # #347: any shared host that never got a TRUSTWORTHY audit this run
        # (every connection failed before the appended `ls` ever ran, or every
        # listing that did run failed the positive control) must be reported
        # as UNVERIFIED, not silently read as "no gap found" — the exact
        # false-negative the adversarial review flagged (both the original
        # CRITICAL and the follow-up MAJOR M1). Every shared host gets
        # `audit_this_call = True` on its FIRST-seen entry (audited_hosts
        # starts empty), so any host still missing from `audited_hosts` here
        # was genuinely never confirmed this run.
        unverified_shared = shared_hosts - audited_hosts
        if unverified_shared:
            print(f"\n⚠ REGISTRATION AUDIT NOT VERIFIED this run for: "
                  f"{', '.join(sorted(unverified_shared))} — no connection ever "
                  f"returned a trustworthy /home listing for the shared host "
                  f"(either every attempt failed before reaching it, or the "
                  f"listing itself could not be trusted), so a registration gap "
                  f"there could exist and go unreported until a later push "
                  f"confirms it.", file=sys.stderr)

        # 3b. Deliver the meeting-analysis Soniox key to every subdev stream
        # account (#275) -- a true no-op when REMOTE_HOSTS has no such account
        # (dev2/gatekeeper-only pushes never reach the source read at all).
        # #358: shares this run's OWN `control_opts` with the deploy loop
        # above, so an account contacted here a second time reuses that
        # account's already-authenticated master connection.
        print(f"\n{'=' * 50}")
        print("Delivering Soniox key to subdev stream accounts...")
        failed.extend(provision_subdev_soniox_key(skip_names=auth_failed,
                                                    control_opts=control_opts))

        if failed:
            # #341 adversarial-review F3 (MINOR, TRIGGERED): an auth-failed
            # stream host now yields TWO `failed` entries by design (its own
            # deploy `rc=...` PLUS the soniox `skipped-known-auth-failure`), so
            # len(failed) double-counts -- report the DISTINCT host count, the
            # full list still shows each reason.
            distinct_failed = {name for name, _reason in failed}
            print(f"\n⚠ {len(distinct_failed)} of {len(REMOTE_HOSTS)} remote(s) "
                  f"FAILED: {failed}", file=sys.stderr)
            sys.exit(1)
        print("\nAll deployments complete.")
    finally:
        # #358: the ControlMaster socket directory is bounded on its own
        # (ControlPersist expires any leftover master promptly), but remove
        # it here too so a repeated push run never accumulates stale
        # per-run directories -- runs regardless of how the try block above
        # exits (success, a failed target, or `sys.exit(1)` above).
        if control_dir:
            shutil.rmtree(control_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# api-watchdog — auto-resume Claude Code sessions stalled on an API error
# ---------------------------------------------------------------------------

WATCHDOG_SERVICE_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.service.template"
WATCHDOG_TIMER_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.timer.template"
WATCHDOG_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.service"
WATCHDOG_TIMER_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.timer"


def _watchdog_bounce_fetch(root):
    """Job 8's real gh fetch (wired here so run_once unit tests stay network-free)."""
    from watchdog import _fetch_bounce_tickets
    return _fetch_bounce_tickets(root)


def _watchdog_gkreq_fetch(root):
    """Job 11's real gh fetch (#30) — same network-free-tests wiring as job 8."""
    from watchdog import _fetch_gkreq_tickets
    return _fetch_gkreq_tickets(root)


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
    unit tests stay network-free."""
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
            cwd=cwd, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


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


def _watchdog_repo_roots():
    """Jobs 27/28's repo enumeration (#137) — every `.git` this box hosts,
    per #138's own corrected lesson that the corpus is `$HOME`, never a
    guessed project directory. `discover_managed_repos` does the actual
    `os.walk`; this is just the injection point so run_once's unit tests
    never touch the real filesystem."""
    from watchdog import discover_managed_repos
    return discover_managed_repos()


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
    from watchdog import (run_once, fetch_usage, fetch_channel_messages,
                          compact_requests_path, goal_templates_path)
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
    logs = run_once(dry_run=getattr(args, "dry_run", False), usage_fetch=fetch_usage,
                    discord_fetch=fetch_channel_messages,
                    bounce_fetch=_watchdog_bounce_fetch,
                    gkreq_fetch=_watchdog_gkreq_fetch,
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
                    compact_requests_path=compact_requests_path(),
                    fleet_fetch=fleet_fetch, fleet_hosts=REMOTE_HOSTS,
                    fleet_path=burn.fleet_path(),
                    burn_alert_enabled=burn_alert_enabled,
                    # Job 20 (#76) runs on EVERY managed box — a silently
                    # dead /goal is a per-session failure, not a
                    # coordinator-only one (it was montalu's stream that
                    # lost its loop twice in a day).
                    goal_rearm_enabled=True,
                    # …and its STALE-TEMPLATE shape (#64) reads the skill
                    # `install` actually deployed on THIS box — the sub-dev
                    # users have no repo checkout, and the installed copy is
                    # by definition the text their own /autopilot prints.
                    goal_templates_path=str(goal_templates_path()),
                    # Job 21 (#84) likewise runs on EVERY managed box — a
                    # multi-hour turn starves compaction, question delivery
                    # and the keystroke queue of THAT session, wherever it
                    # runs. Detection only, so it never types into a pane.
                    long_turn_enabled=True,
                    # Job 26 (#140) runs on EVERY managed box: a session that
                    # asked for compaction and never got it is a per-session
                    # failure, and both measured incidents were on DIFFERENT
                    # boxes (forestshop@dev1, montalu@subdev) on the same day.
                    # Detection only, so it never types into a pane.
                    compact_stall_enabled=True,
                    # Jobs 27/28 (#137) run on EVERY managed box — both are
                    # per-repo local/gh reads, and each box holds the
                    # checkouts it can actually measure. Self-gated hourly
                    # internally, so wiring them costs nothing on the 59
                    # sweeps out of 60 that skip.
                    vault_purge=_watchdog_vault_purge,
                    repo_roots=_watchdog_repo_roots,
                    issue_counts_fetch=_watchdog_issue_counts_fetch,
                    git_fetch=_watchdog_git_fetch,
                    # #160 defects 1/4 run on EVERY managed box — both are
                    # per-repo `gh` reads (cached per cwd, 10-min TTL, so a
                    # box with several panes on one repo costs at most one
                    # extra call per window) consulted by job 20's
                    # goal-achieved backstop and job 10's widened wedge ping.
                    backlog_fetch=_watchdog_backlog_fetch,
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
    """Record a `/compact` request for a session at a safe ticket boundary
    ("krok 1c — ohraničenie kontextu", #39 follow-up), and ALSO attempt to
    DELIVER it SYNCHRONOUSLY in this SAME process (#65, 2026-07-26). Called
    by the Stop hook `notify-compact-request.sh` the MOMENT a turn's final
    message is a completed-ticket report.

    #65: waiting for watchdog job 14's next ~60s poll loses the race with an
    armed `/goal` loop, which can dispatch the next ticket within seconds —
    long before that poll ever sees the pane idle. So this command records
    the request FIRST (never lost, even if the immediate attempt below
    raises) and then calls `deliver_compact_now`, which resolves the pane
    hosting this EXACT session and, when safe, types `/compact` right now
    (a short send-keys reliably queues even into a BUSY pane, so this does
    not need to wait for idle the way job 14's poll does). On success the
    just-recorded request is cleared immediately — job 14 never needs to
    act on it. On any failure/exception the request stays recorded, exactly
    as before #65, for job 14's polled retry (its own draft-handling,
    #67, covers the one case this synchronous path stays conservative on:
    a genuine unsent draft).

    #71 (2026-07-26 live incident): a REPEAT `--record` for a boundary
    ALREADY delivered — live-observed as the armed goal loop's own
    re-evaluation re-running the Stop hook chain against an UNCHANGED
    `last_assistant_message` several times right after a compaction
    finishes, each fire independently reaching this exact code path — must
    be a complete no-op: no re-record, no second `deliver_compact_now`
    attempt. `--msg-hash` (from the hook, a fingerprint of the triggering
    message) is checked against `compact_already_delivered` BEFORE doing
    anything; on a match, this call returns immediately. On a genuine
    delivery, `mark_compact_delivered` records the hash so any LATER repeat
    (from this same synchronous path, or from job 14) is recognized. A
    blank/absent `--msg-hash` (every pre-#71 caller) never dedupes this
    way — the check and the mark are both no-ops on a blank hash.

    #121 (2026-07-28): `--origin` records WHAT proved the boundary. The Stop
    hook passes nothing (unchanged behavior); the SubagentStop hook
    `notify-compact-subagent-boundary.sh` passes `subagent-stop`, meaning an
    autopilot-worker concluded with zero other live tasks in the session's
    own task registry. That is the durable ticket boundary for a supervisor
    whose work is done by dispatched workers — its own turn ALWAYS ends `⏳`
    (it reports batch N and dispatches batch N+1 in the same turn), so the
    Stop-shaped boundary is structurally unreachable for it.

    #125 (2026-07-28): the printed word on a HANDLED request is whatever
    `deliver_compact_now` itself returns (see its own docstring) --
    "sent"/"claim-queued"/"queued-compact"/"dropped-no-work"/
    "dropped-small-context" -- never the single generic "delivered" this
    command used to print for all five dispositions regardless of what
    actually happened downstream.

    #225 -- `--self` is a SEPARATE mode, checked first: the SESSION itself
    calling this to ask "deliver /compact into MY OWN pane, right now, and
    hold (bounded) until it lands" -- no --session/--cwd/--origin needed at
    all, resolved from the calling pane via `$TMUX_PANE`. See
    `watchdog.deliver_compact_self`'s own docstring for the full mechanism
    (the primary fix for the ticket's "boundary window closes before the
    sweep looks" race).

    #250 -- the `req_now` captured right before `record_compact_request` is
    threaded into `deliver_compact_now` as `request_ts=` (and `now=`), so
    its live-tasks defer (#246) is bounded by the SAME grace window job 14
    applies -- see `watchdog.COMPACT_DEFER_GRACE_S`'s own comment."""
    if getattr(args, "self", False):
        from watchdog import deliver_compact_self
        word, sid = deliver_compact_self(hold_s=getattr(args, "hold", None))
        if not sid:
            print("compact-request --self: could not resolve this session's "
                  "own pane/transcript (not running inside a recognized "
                  "tmux Claude Code pane, or $TMUX_PANE unset) -- nothing "
                  "recorded", file=sys.stderr)
            sys.exit(1)
        sys.stdout.write(word)
        return
    from watchdog import (record_compact_request, deliver_compact_record,
                          clear_compact_request, compact_already_delivered,
                          mark_compact_delivered)
    if getattr(args, "record", False):
        import time
        msg_hash = (getattr(args, "msg_hash", "") or "").strip()
        origin = (getattr(args, "origin", "") or "").strip()
        if compact_already_delivered(args.session, msg_hash):
            sys.stdout.write("dup")
            return
        # #250 -- capture the SAME `ts` used for the record call and thread
        # it into `deliver_compact_record` as `request_ts=`, so its own
        # grace-bound live-tasks check (`_compact_live_tasks_in_grace`)
        # measures age from the request this exact call just recorded.
        #
        # #238 adversarial review 🔴1 -- this used to call
        # `deliver_compact_now` ONCE with `now=req_now` (the SAME instant
        # `request_ts` was captured at), which made the min-request-age
        # gate an unconditional off-switch for every real call (age was
        # always exactly 0.0). `deliver_compact_record` retries with a
        # FRESH `now` for a few real seconds instead.
        req_now = time.time()
        ok = record_compact_request(args.session, args.cwd, now=req_now,
                                    msg_hash=msg_hash, origin=origin)
        if not ok:
            sys.stdout.write("skip")
            return
        delivered = deliver_compact_record(args.session, args.cwd, origin=origin,
                                           request_ts=req_now)
        if delivered:
            clear_compact_request(args.session)
            if msg_hash:
                mark_compact_delivered(args.session, msg_hash)
            # #125 -- `deliver_compact_now` now returns the REASON word
            # itself (e.g. "sent"/"claim-queued"/"queued-compact"/
            # "dropped-no-work"/"dropped-small-context") instead of a bare
            # `True` that this command used to collapse into one generic
            # "delivered" for every disposition -- print it verbatim. A
            # caller (or test double) that still returns a bare truthy
            # non-string value is treated as the legacy generic "sent",
            # never a crash.
            word = delivered if isinstance(delivered, str) else "sent"
            sys.stdout.write(word)
        else:
            sys.stdout.write("recorded")
        return
    print("compact-request: nothing to do (use --record --session <sid> --cwd <cwd>)",
          file=sys.stderr)
    sys.exit(1)


# Autopilot authority profiles (issue #16, 2026-07-09). A stream's authority is a
# property of its LINUX USER (streams are separate users by construction: david /
# marek / montalu), resolved at RUNTIME — no per-box state to lose on a home-dir
# migration (the AIRULESET_NOTIFY_OWNER loss pattern), and every push carries the
# map to every managed target. Profiles:
#   full          — merge PR to main + main green + deploy verified (default)
#   branch-merge  — own PR merged into the project INTEGRATION branch (develop),
#                   THEN the same ready-for-review hand-off comment fork-no-merge
#                   uses (#349: a merge alone does NOT close the ticket, and
#                   skipping the comment leaves it invisible to the gatekeeper's
#                   review queue); never staging/main promotion, never deploy,
#                   never closes the issue itself
#   fork-no-merge — fork branch pushed + local verification green + ready-for-review
#                   hand-off on the issue; never opens/merges a PR, never closes
#                   the issue itself (the maintainer does, at merge)
# A project CLAUDE.md marker `airuleset:authority=<profile>` OVERRIDES the user
# default (checked by the /autopilot skill, not here). Only the user adds markers.
AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")
AUTHORITY_BY_USER = {
    "david": "fork-no-merge",
    "marek": "branch-merge",
    "montalu": "branch-merge",
    # simap (airuleset#143, 2026-07-28): phase-1 demo stream that MERGES
    # NOWHERE — no `develop`, no gatekeeper hand-off. fork-no-merge is the
    # existing lowest profile and already expresses exactly that ("never
    # opens/merges a PR, hand-off via comment only") — no new profile needed.
    "simap": "fork-no-merge",
    # montalu2/montalu3/montalu4 (airuleset#251, odoo-erp#2961): three MORE
    # full parallel montalu streams — same authority as montalu itself.
    "montalu2": "branch-merge",
    "montalu3": "branch-merge",
    "montalu4": "branch-merge",
    # miva1 (airuleset#300, 2026-08-07): phase-1 isolated stream, same shape
    # as simap -- merges nowhere, fork-no-merge is already correct.
    "miva1": "fork-no-merge",
    # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE clones of
    # the david external-developer fork stream (additional capacity for the
    # same slovnormal odoo developer) -- same authority as david itself.
    "david2": "fork-no-merge",
    "david3": "fork-no-merge",
    "david4": "fork-no-merge",
    # montalu5/montalu6/montalu7/montalu8 (airuleset#378, odoo-erp#3642):
    # four MORE full parallel montalu streams -- same authority as
    # montalu/montalu2/montalu3/montalu4.
    "montalu5": "branch-merge",
    "montalu6": "branch-merge",
    "montalu7": "branch-merge",
    "montalu8": "branch-merge",
}


# --- #263: subdev stream dev-env bootstrap (claude tmux session + gap report) --
def _stream_session_cwd() -> Path:
    """The convention working directory for a subdev stream account's tmux
    session (see STREAM_DEV_CWD_REL's own comment, above apply_ultracode_
    launcher). Falls back to $HOME when that checkout doesn't exist yet, so
    bootstrap never hard-fails on an account gatekeeper hasn't finished
    Phase 1 for."""
    p = Path.home() / STREAM_DEV_CWD_REL
    return p if p.is_dir() else Path.home()


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
AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")

# The maintainer's GitHub account. Some sub-dev boxes authenticate gh with a
# scoped PAT of THIS account (montalu), so @me search quals there match every
# maintainer-authored ticket — foreign streams leaked into the montalu footer
# (2026-07-20). A shared-account box scopes its slice by the stream LABEL only.
MAINTAINER_GH_LOGIN = "zbynekdrlik"


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


class SliceUnresolved(Exception):
    """This box's own gh identity could not be resolved, so "my slice" is
    undefined. Raised by `_slice_quals` instead of falling back to a default
    qual set — every caller must handle it in ITS OWN established way
    (the CLI refuses; the footer and the run-card keep `None`, never a
    wrong number). #181 I-2."""


# The label a stream applies to a PERMANENT ops-channel ticket — a
# self-declared "this issue never auto-closes" channel (odoo-erp #1861:
# "[TRVALÝ OPS KANÁL — NEZATVÁRAŤ] erp-test-* teardown/recreate/refresh";
# #3037: a snapshot-retention alert log). It is never workable /autopilot
# backlog, no matter how long it stays open (#362).
OPS_CHANNEL_LABEL = "ops-channel"

# The qualifying-set EXCLUSION fragment shared by every open-issue search in
# this file that must never surface a manually-skipped OR a PERMANENT
# ops-channel ticket as workable backlog — `core-quals`/`slice-quals` (the
# `/goal` stop-proof), the footer's own counts (`cmd_tickets_status`), and
# the Discord run-card's `remaining` count all AND this onto their base
# query, so none of them can ever disagree about which population is
# "qualifying" (#362, same tier as the pre-existing `autopilot-skip`
# exclusion — before this fix, `core-quals --count` could NEVER reach `0`
# while a permanent ops-channel ticket sat open, and the /autopilot loop
# dispatched a full worker onto one that found "nothing to do").
#
# Deliberately NOT extended to the two POSITIVE `label:autopilot-skip`
# "skipped" bucket queries (`cmd_tickets_status`'s own `entry["skipped"]`) —
# that bucket answers a DIFFERENT question ("how many of the qualifying
# tickets are also explicitly skip-labelled"), not "the qualifying set"
# itself, and an ops-channel ticket without the skip label already never
# appears there. Deliberately NOT rendered as its own statusline bucket
# either (documented-invisible instead) — #313 is a direct, repeated user
# request to SIMPLIFY the footer ("counter chaos"), and a permanent,
# rarely-applied label is exactly the kind of population a new bucket would
# be noise for.
AUTOPILOT_SKIP_EXCL = "-label:autopilot-skip -label:%s" % OPS_CHANNEL_LABEL


def _core_search_excl():
    """The full-authority CORE slice's exclusion fragment: every REDUCED-
    authority sub-dev stream's own `stream:<user>` label, so the footer
    (`cmd_tickets_status`), the Discord card (`_notify_run_card`), and the
    `/goal` stop-proof (`cmd_core_quals`) all exclude EXACTLY the same
    sub-dev-owned tickets from a full-authority box's own count — single
    source of truth (#164 / #181 I4).

    Only entries whose profile is NOT `full` are excluded (#181 M-5): a
    hypothetical `full` entry in AUTHORITY_BY_USER is not a sub-dev stream
    at all, and excluding its label would silently remove a whole population
    from every full-authority count."""
    return " ".join("-label:stream:%s" % u
                    for u, profile in sorted(AUTHORITY_BY_USER.items())
                    if profile != "full")


def _gh_app_token_dir():
    """The GitHub App installation-token directory for THIS stream box
    (`~/.config/gh-app-tokens/`), resolved at CALL time so a relocated
    `$HOME`/override is honoured on every call, mirroring
    `watchdog.draft_rescue_dir()`'s own established shape.

    This is the EXACT path the real, deployed odoo-erp#3281 mechanism
    reads/writes on the subdev VPS — confirmed directly against the
    shipped scripts in zbynekdrlik/odoo-erp: `push-stream-tokens.sh`'s
    `REMOTE_DIR_NAME=".config/gh-app-tokens"` (created via `install -d
    -m 700` on the first successful token delivery) and
    `gh-app-token.sh`'s `TOKEN_DIR="${GH_APP_TOKEN_DIR:-$HOME/.config/
    gh-app-tokens}"` — never an invented convention.

    `GH_APP_TOKEN_DIR` overrides it for tests, mirroring that SAME shell
    script's own env var name so both sides of the mechanism agree."""
    override = os.environ.get("GH_APP_TOKEN_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "gh-app-tokens"


def _is_gh_app_token_box():
    """True when this box authenticates `gh` via a GitHub App INSTALLATION
    token (odoo-erp#3281's `gh-app-stream-tokens` mechanism — david2/
    david3/david4, odoo-erp#3282), detected from a LOCAL, STATIC fact —
    the App-token directory's presence — never a network call (#356).

    Why a network call cannot answer this question at all: an App
    installation token carries no user identity, so `gh api user` 403s
    ("Resource not accessible by integration") on EVERY call, structurally,
    not intermittently — there is no failure signature to distinguish
    "this is an App-token box" from "this box's gh is genuinely broken for
    an unrelated reason", which is exactly the ambiguity `SliceUnresolved`
    exists to refuse rather than guess at (#181 I-2). A local signal
    removes the ambiguity instead of trying to classify it.

    `.is_dir()`, never a bare `.exists()` — a stray FILE at this path must
    not be misread as "provisioned".

    Known, accepted residual (adversarial review of #356): a stray or
    stale App-token directory delivered to an OWN-account (PAT) box —
    e.g. a misdirected `push-stream-tokens.sh` delivery, or a leftover
    from an App-token-to-PAT migration — silently NARROWS that box's own
    slice from 3 quals (assignee ∪ author ∪ label) down to 1 (label
    alone), dropping any assigned/authored-but-unlabeled ticket from the
    stop-proof with no refusal (the existing empty-result validators check
    the LABEL dimension, never the missing assignee/author one). This is
    an operational-error trigger, not something this local, static check
    can distinguish from a genuine App-token box — a real App token proves
    nothing beyond "this directory exists" either."""
    try:
        return _gh_app_token_dir().is_dir()
    except OSError:
        return False


def _slice_quals(user, cwd=None):
    """gh search quals for a reduced-authority stream's OWN ticket slice.
    Own-account streams (david/kvaskodev): assigned ∪ authored ∪ stream label.
    Shared-account boxes (gh login == the maintainer account): the stream
    LABEL alone — @me there matches the whole maintainer-authored backlog.
    App-token boxes (david2/david3/david4, #356): the stream LABEL alone,
    the SAME branch a shared-account box takes — an App installation token
    carries no user identity at all, so the assignee/author signal
    `assignee:@me`/`author:@me` would rely on is meaningless here, and the
    label is the only sound one. Detected via `_is_gh_app_token_box()`
    BEFORE `_gh_login()` is ever called, so this box never pays for (or
    depends on) a network call that is guaranteed to fail.

    Raises `SliceUnresolved` when the gh login cannot be resolved at all
    (#181 I-2) — an unresolvable identity cannot pick between those two
    branches, and guessing either one is a wrong answer on some box."""
    if _is_gh_app_token_box():
        return ["label:stream:" + user]
    login = _gh_login(cwd)
    if login is None:
        raise SliceUnresolved(
            "gh api user failed — cannot tell whether this box authenticates "
            "as the maintainer account (slice = the stream LABEL alone) or as "
            "its own (assignee ∪ author ∪ label). Refusing to guess: the two "
            "branches disagree on every shared-account box.")
    if login == MAINTAINER_GH_LOGIN:
        return ["label:stream:" + user]
    return ["assignee:@me", "author:@me", "label:stream:" + user]


# An open, non-skip ticket carrying ANY of these labels is an obligation of the
# FULL-authority (core / gatekeeper) box even when it also carries a sub-dev
# `stream:<user>` label: only this box can perform the action they stand for.
#
# `needs-gatekeeper` = a stream→supervisor action request (cross-stream
# protocol rule 7 — by definition nobody else can do it).
#
# `ready-for-review` = a hand-off awaiting this box's review / merge / close
# (rule 4, and the fork-no-merge template's "CLOSED by the maintainer") —
# while it is open the full-authority loop HOLDS: review-watch, stay alive,
# re-check hourly, never end the loop ("neither side ever finishes while the
# other holds its ball") — so `core-quals --count` legitimately never
# reaching 0 while a hand-off sits open is CORRECT, and is NOT the
# never-stops failure the original ticket rejected.
#
# `prio:bounce` is DELIBERATELY NOT one of these labels (#307, 2026-08-07). It
# means the gatekeeper returned this ticket to the SUB-DEV with findings that
# need a fix — the SUB-DEV acts next, not this box, so a BARE open
# `prio:bounce` (no `ready-for-review`/`needs-gatekeeper` alongside it) is the
# sub-dev's own work: it does not block this box's obligation set, and
# letting the count reach 0 while the sub-dev fixes it is CORRECT, not a
# regression of the never-stops failure. Live evidence (odoo-erp,
# 2026-08-07): `core-quals --count` was inflated 63 -> 77 by 14 open
# `prio:bounce` tickets that belonged entirely to `stream:david` — and a
# full-authority `/goal` SELECTING from that inflated set could start
# IMPLEMENTING a sub-dev's bounce fix, violating the standing rule that the
# gatekeeper never patches a sub-dev's branch. A ticket carrying BOTH
# `prio:bounce` AND `ready-for-review` still counts — the hand-off is the
# live signal, matched by the `ready-for-review` qual above regardless of
# `prio:bounce`. The sub-dev's own `slice-quals` still includes its own
# `prio:bounce` tickets unaffected (they always also carry `stream:<user>`,
# which `_slice_quals()` already queries) — the two sides stay complementary.
MAINTAINER_ACTION_LABELS = ("needs-gatekeeper", "ready-for-review")


def _obligation_quals():
    """The per-qual search fragments whose UNION is a full-authority box's
    OBLIGATION set: the CORE slice, PLUS every open ticket only this box can
    action regardless of which stream owns it (#181 round 3, CRITICAL).

    `_core_search_excl()` is the FOOTER's *display* partition — "which
    population am I showing". Round 2 reused it as the `/goal` stop-proof's
    *obligation* partition — "which tickets must I finish before I may stop" —
    and those are not the same set. Measured on zbynekdrlik/odoo-erp
    2026-07-30: 83 open non-skip, 40 in the core partition, and 13 tickets
    outside it that only this box could move at the time (#2396 and #2377
    are `stream:montalu` + `needs-gatekeeper`, plus 11 open `prio:bounce`).
    The gatekeeper would close its 40, the proof would print 0, the loop
    would stop — leaving those tickets blocked on the very box that just
    stopped. That is #181 verbatim at a new address.

    #307 (2026-08-07) correction: `prio:bounce` is NOT one of
    MAINTAINER_ACTION_LABELS any more — see that tuple's own comment. All 11
    open `prio:bounce` tickets in the round-3 measurement above belonged
    entirely to `stream:david`, the sub-dev's own work; counting them
    inflated a real `core-quals --count` on the SAME repo from 63 to 77 a
    few days later, and let the obligation SELECTION path (`--list`) surface
    a ticket a full-authority `/goal` worker must never implement. The union
    below now excludes `prio:bounce` — only `needs-gatekeeper` and
    `ready-for-review` remain.

    This is NOT a revert to the whole-repo count the original ticket rejected
    (that was the never-stops failure): a stream ticket the sub-dev is
    actively working carries none of these labels and still does not block
    this box. Union in Python, one query per qual — gh's `--search` ANDs
    space-joined qualifiers ACROSS qualifier types and cannot OR them.

    Known residual, deliberate: a hand-off is detected by the
    `ready-for-review` LABEL (the same signal the footer's `gk` bucket uses,
    applied by the repo's own subdev-handoff-label workflow), not by the
    `READY-FOR-REVIEW:` comment that is its primary signal. The only
    single-query comment form is `"READY-FOR-REVIEW:" in:comments`, and
    GitHub tokenizes quoted phrases (the 2026-07-24 `in:title` false match),
    so it over-matches — and over-counting the obligation set is the
    never-stops failure again."""
    return [_core_search_excl()] + ["label:" + lb
                                    for lb in MAINTAINER_ACTION_LABELS]


def _repo_root(cwd=None, runner=None):
    """The git repo root for `cwd`, or "" when it cannot be resolved.

    ONE definition, so `cmd_tickets_status` (the footer), `cmd_slice_quals`
    and `cmd_core_quals` all resolve authority — and run gh — against the
    SAME root. #181 I-5: the CLI commands used a bare `resolve_authority()`,
    which reads `Path.cwd()/CLAUDE.md`, while the footer passes the repo
    root; a project marker `airuleset:authority=...` was therefore invisible
    to the CLI whenever the session cwd was a subdirectory, so the "ONE
    definition, resolved per box" claim held only when cwd was exactly the
    repo root.

    Carries #61's fallback: the session cwd may be the PARENT of the actual
    repo (montalu's ~/devel/odoo with the repo at ~/devel/odoo/odoo-erp) and
    `git rev-parse` only ever walks UPWARD. Exactly one `.git` subdirectory
    is descended into; 0 or >1 stays ambiguous — never guess."""
    import subprocess

    cwd = cwd or os.getcwd()

    def _default_run(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20, env=_gh_env())
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    run = runner or _default_run
    root = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if root:
        return root
    try:
        candidates = [p for p in Path(cwd).iterdir()
                      if p.is_dir() and (p / ".git").exists()]
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return run(["git", "rev-parse", "--show-toplevel"], str(candidates[0]))
    return ""


def _ticket_is_stream_labeled(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...}
    dicts, or None/malformed) carries a stream:<user> label for any
    AUTHORITY_BY_USER stream — i.e. this ticket belongs to a sub-dev stream's
    slice, not the full-authority CORE slice (#164 defect 2: the D/T progress
    counter must not let a stream ticket's card inflate a core-scoped 'done'
    the core-scoped 'remaining' can't back)."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    return any(("stream:%s" % u) in names for u in AUTHORITY_BY_USER)


def _authority_marker(cwd=None):
    """Read an `<!-- airuleset:authority=<profile> -->` override from the project
    CLAUDE.md (cwd-relative), or None. Lets a project raise/lower a stream's default
    authority (e.g. grant `full` to a montalu repo). The single place the marker is
    parsed, so the CLI, the autopilot skill, and the issue-close guard hook agree.

    The marker MUST be the HTML-COMMENT form (exactly like `<!-- airuleset:merge=manual
    -->`) — a bare/prose mention of `airuleset:authority=…` is deliberately NOT honored:
    an unanchored match could let a documentation sentence naming a profile silently
    ELEVATE a fork-no-merge stream to `full` and disable the issue-close guard (the
    UNSAFE direction). If several comment markers exist (a misconfig) the LAST one wins,
    so an operative marker placed after any example cannot be shadowed."""
    import re
    try:
        p = (Path(cwd) if cwd else Path.cwd()) / "CLAUDE.md"
        if p.is_file():
            hits = re.findall(r"<!--\s*airuleset:authority=([a-z-]+)\s*-->",
                              p.read_text(errors="ignore"))
            for tok in reversed(hits):
                if tok in AUTHORITY_PROFILES:
                    return tok
    except OSError:
        return None
    return None


def resolve_authority(cwd=None) -> str:
    """The current stream's autopilot authority profile: a project CLAUDE.md
    `airuleset:authority=<profile>` marker (cwd-relative) OVERRIDES the per-user
    default map. This makes `airuleset.py authority` authoritative for both the
    autopilot skill and the `block-fork-no-merge-issue-close` hook (single source
    of truth) — cmd_authority's explain text has always PROMISED this override; it
    is now actually honored, not just documented."""
    return _authority_marker(cwd) or AUTHORITY_BY_USER.get(_current_user(), "full")


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    if getattr(args, "maintainer_login", False):
        print(MAINTAINER_GH_LOGIN)
        return
    profile = resolve_authority()
    print(profile)
    if getattr(args, "explain", False):
        user = _current_user()
        print(f"user={user} (map: {AUTHORITY_BY_USER.get(user, 'unmapped -> full')}); "
              f"a project CLAUDE.md marker airuleset:authority=<profile> overrides this.")


def _label_exists_on_repo(label, cwd=None):
    """True if `label` is a DEFINED label on the current repo (gh label list
    --search), False if confirmed absent, None if the query itself failed —
    an unreachable/erroring gh is NOT evidence the label is missing (#181
    C2, round 2)."""
    raw = _gh_out("label", "list", "--search", label, "--json", "name",
                  "-L", "50", cwd=cwd)
    try:
        names = {(x or {}).get("name") for x in json.loads(raw)}
    except (ValueError, TypeError):
        return None
    return label in names


def _search_index_healthy(cwd=None):
    """Does gh's SEARCH path demonstrably work for this identity/repo?
    True = yes; False = demonstrably not (or unprovable); None = the repo
    genuinely has no open issues at all, so an empty slice is trivially
    correct.

    #181 I-1: round 2's cross-check ran `involves:@me` and only required the
    response to PARSE — but `[]` parses, and "search returns nothing
    everywhere" IS `[]`, i.e. the exact state the check claimed to detect was
    the state it accepted. The reviewer executed it: login zbynekdrlik, user
    montalu, label present, every query `[]` → rc 0, stdout `0`. A real
    cross-check must ASSERT NON-EMPTY on a query that cannot legitimately be
    empty.

    A SORT-ONLY search (`sort:created-desc`) is that query: it carries no
    filtering qualifier, so it matches every open issue in the repo. If it
    comes back empty the repo may genuinely have none — settled by the REST
    listing path (`gh issue list` with no `--search`, a different gh code
    path that does not touch the search index). REST sees issues but search
    sees none ⇒ the search index is not answering ⇒ refuse."""
    probe = _gh_out("issue", "list", "--state", "open", "--search",
                    "sort:created-desc", "-L", "1", "--json", "number", cwd=cwd)
    try:
        rows = json.loads(probe)
    except (ValueError, TypeError):
        return False
    if not isinstance(rows, list):
        return False
    if rows:
        return True
    rest = _gh_out("issue", "list", "--state", "open", "-L", "1",
                   "--json", "number", cwd=cwd)
    try:
        rest_rows = json.loads(rest)
    except (ValueError, TypeError):
        return False
    if not isinstance(rest_rows, list):
        return False
    return None if not rest_rows else False


def _union_open_issues(quals, base, cwd=None):
    """Run ONE `gh issue list --search` per qual and union the rows by issue
    number, returning `(rows_by_number, failed)`.

    Per-qual queries are not an optimisation choice: gh's `--search` ANDs
    space-joined qualifiers across qualifier types and cannot OR them, so a
    caller that needs a UNION (assignee ∪ author ∪ label; core ∪ the
    maintainer-action labels) must union client-side. `failed` is True if ANY
    query failed to parse — a gh error is never an empty result.

    `labels` is fetched alongside (#181 round 4): one extra field on queries
    already being made, and the thing that lets `_print_issue_rows` mark every
    row with what THIS box may DO with it. Without it the mandated backlog
    SELECTION source emitted no not-mine-to-implement discriminator at all, so
    the only thing between the FULL template's bounce-lane seed ("the OLDEST
    open prio:bounce ticket" — live on odoo-erp that is #2150, `stream:david`)
    and a gatekeeper writing code on a sub-dev's ticket was a prose clause the
    worker may never have loaded."""
    seen, failed = {}, False
    for qual in quals:
        search = (base + " " + qual).strip() if qual else base
        raw = _gh_out("issue", "list", "--state", "open", "--search", search,
                      "-L", "1000", "--json", "number,title,createdAt,labels",
                      cwd=cwd, timeout=20)
        try:
            for x in json.loads(raw):
                seen[x["number"]] = x
        except (ValueError, TypeError, KeyError):
            failed = True
    return seen, failed


ROW_ACTION_ONLY = "action-only"
ROW_IMPLEMENT = "implement"


def _stream_owner_of(labels):
    """The REDUCED-authority stream that owns this ticket, or "" — read from a
    gh `--json labels` value (a list of {'name': ...} dicts, or None).

    Only non-`full` AUTHORITY_BY_USER entries count, the same filter
    `_core_search_excl()` applies (#181 M-5): a hypothetical `full` entry is
    not a sub-dev stream, and treating its label as ownership would wrongly
    mark its tickets untouchable."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for user, profile in sorted(AUTHORITY_BY_USER.items()):
        if profile != "full" and ("stream:%s" % user) in names:
            return user
    return ""


def _own_handoff_label():
    """This box's own `handed-by:<user>` hand-off origin marker, or None
    when this box is not a registered sub-dev stream (#191 Part C). Guards
    `cmd_gk_request`'s origin-marker write against a full-authority box
    (dev1/gatekeeper) stamping a meaningless `handed-by:newlevel`/
    `handed-by:gatekeeper` label onto a gk-request filed for its own
    testing or on another stream's behalf.

    #191 adversarial review, CRITICAL C1: deliberately `handed-by:<user>`,
    NEVER `stream:<user>` — see `cmd_gk_request`'s own docstring for why
    reusing the ownership label would have broken the `/goal` stop-proof's
    termination condition."""
    user = _current_user()
    return "handed-by:" + user if user in AUTHORITY_BY_USER else None


# A candidate's ORIGIN-shaped labeled events, both the legacy ownership
# convention (`stream:<user>`, applied by a human during triage — the
# original, pre-#191 signal `_last_origin_owner` recovers) and the new
# hand-off marker (`handed-by:<user>`, #191 Part C) — either settles who a
# ticket belongs to for re-attribution purposes.
_ORIGIN_LABEL_RE = re.compile(r"^(?:stream|handed-by):(.+)$")


def _last_origin_owner(numbers, cwd=None):
    """For each issue in `numbers`, the stream that owns it per the
    TEMPORALLY-LAST origin-shaped (`stream:<user>` or `handed-by:<user>`)
    labeled event in its history — regardless of who applied it (a SHARED
    gh identity, e.g. montalu/marek/simap all authenticating as the
    maintainer, carries ZERO discrimination power between the streams that
    share it) and regardless of whether that label is STILL present (the
    timeline event survives a LATER relabel that removes it, unlike the
    label itself). #191 root cause 2: a shared-account stream's slice is
    `label:stream:<user>` alone, so a ticket relabelled away from it (the
    fix moved to shared code the stream cannot push to) silently vanishes —
    this recovers it from GitHub's own event history instead of guessing.

    #191 adversarial review, MAJOR M3: the original version asked "was MY
    label EVER applied", which let TWO streams that both once owned a
    ticket (A -> B -> unlabelled) BOTH reclaim it — the current-label
    bounding a caller may ALSO apply only helps when a current label
    survives, exactly the case this function exists to handle the absence
    of. Taking the LAST (not "ever") origin-shaped event settles a genuine
    competing claim correctly: whichever stream's label was applied most
    recently is the one this returns.

    ONE batched GraphQL call for the WHOLE candidate set, aliased per issue
    number -- never one REST call per candidate. A per-candidate REST loop
    shares a rate-limit bucket across every stream authenticating as the
    same PAT (#191 design review); batching collapses N candidates to O(1)
    calls regardless of how many there are. `timelineItems(last: 20, ...)`
    (#191 m2: NOT `first:`, which would read the OLDEST events on a churned
    ticket and truncate away the very origin/hand-off label this function
    exists to find). `owner`/`name` resolve via gh's own `-F owner=
    '{owner}' -F name='{repo}'` placeholder expansion from `cwd`'s git
    remote (the SAME shape `_watchdog_closed_fetch` already uses) -- no
    separate `gh repo view` call needed.

    Returns `{number: user}` for every candidate with at least one
    origin-shaped event found; a candidate with none is simply absent
    (never a guess). Returns `{}` on any failure or malformed response."""
    if not numbers:
        return {}
    aliases = "\n".join(
        "i%d: issue(number: %d) { timelineItems(last: 20, "
        "itemTypes: [LABELED_EVENT]) { nodes { ... on LabeledEvent "
        "{ label { name } } } } }" % (int(n), int(n))
        for n in numbers)
    query = ("query($owner: String!, $name: String!) { repository(owner: "
             "$owner, name: $name) { %s } }" % aliases)
    raw = _gh_out("api", "graphql", "-f", "query=" + query,
                  "-F", "owner={owner}", "-F", "name={repo}",
                  cwd=cwd, timeout=20)
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get("errors"):
        return {}
    repo = (data.get("data") or {}).get("repository")
    if not isinstance(repo, dict):
        return {}
    owners = {}
    for n in numbers:
        node = repo.get("i%d" % int(n))
        if not isinstance(node, dict):
            continue
        items = (node.get("timelineItems") or {}).get("nodes") or []
        if not isinstance(items, list):
            continue
        # `nodes` is chronological (oldest first) — walk it in order and
        # keep the LAST origin-shaped match, so a later relabel always
        # wins over an earlier one.
        last_owner = None
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("label") or {}).get("name")
            m = _ORIGIN_LABEL_RE.match(name or "")
            if m:
                last_owner = m.group(1)
        if last_owner is not None:
            owners[n] = last_owner
    return owners


def _slice_mine_and_handed(quals, root, slug, extra=None):
    """`(rows, handed, failed)` for a reduced-authority stream's OWN ticket
    slice — the ONE shared derivation `cmd_tickets_status`'s footer AND
    `cmd_slice_quals`'s `/goal` stop-proof both consume (#391 consistency
    guard, mirroring the guard already established for the full-authority
    obligation set: never two independent derivations of "which of my
    tickets are still active" that could silently drift apart).

    `rows` is `_union_open_issues`'s own return shape (`{number: {"number",
    "title", "createdAt", "labels"}}`) — reused directly rather than a
    second, narrower fetch, so `--list`'s title/createdAt needs no extra gh
    call. `handed` maps a ticket number to whether it is already parked with
    the gatekeeper: a label check (`ready-for-review`/`needs-gatekeeper`,
    overridden by a `prio:bounce` label — #313 pt 2), PLUS — only when
    `extra` is None, i.e. the plain/unfiltered slice `cmd_tickets_status`
    always uses and `cmd_slice_quals` uses for its own `--count`/plain
    `--list` — the shared-account stream-owner recovery (`_last_origin_
    owner`) and the comment-based fallback (`_comment_readiness_signal`),
    moved here VERBATIM from `cmd_tickets_status`. `extra` (the bounce-lane
    seed's `--extra "label:prio:bounce"`) SKIPS that enrichment: the
    recovery step's own candidate query (`label:needs-gatekeeper,
    ready-for-review`, deliberately never filtered by `extra`) could recover
    a ticket that does not itself match `extra`, silently violating the
    filtered result's own contract — and a genuine `prio:bounce` ticket is
    already correctly un-handed via the label override alone, so the
    enrichment buys nothing there anyway.

    #391 adversarial review CRITICAL-1: the comment fallback (below) keeps
    the LAST comment signal, and a stream's own bounce nudge lane
    (skills/autopilot/SKILL.md: a BARE `prio:bounce` label + a sub-dev-
    authored ACK) applies the bounce with NO accompanying gatekeeper-shaped
    comment at all — so a ticket with a genuine, older READY-FOR-REVIEW
    comment and an INVISIBLE bounce (label only, no comment) would have its
    last-and-only comment signal read True, silently re-upgrading it to
    handed and discarding the label override just computed. `bounce_numbers`
    tracks every currently-`prio:bounce`-labeled row; the comment-fallback
    walk below may only upgrade one of THOSE numbers to handed when it also
    saw a recognised gatekeeper comment (a VISIBLE bounce) somewhere in the
    thread -- an invisible bounce fails toward "still unhandled", the safe
    (never-stop) direction for a `/goal` stop-proof. A non-bounce-labeled
    row is unaffected: the fallback's original "trust the last signal"
    behaviour is unchanged for it (this is the #313 broken-workflow case the
    fallback exists for, where no bounce is in play at all).

    `failed` is True on ANY gh query failure in the per-qual fetch — the
    caller must treat that as "cannot trust an unhandled count of 0", exactly
    like every other gh-search-derived zero in this file."""
    base = AUTOPILOT_SKIP_EXCL + ((" " + extra) if extra else "")
    rows, failed = _union_open_issues(quals, base, cwd=root)
    handed = {}
    bounce_numbers = set()
    for n_num, row in rows.items():
        labels = {(lb or {}).get("name") for lb in (row.get("labels") or [])}
        # #191 Part A ("different lane"): needs-gatekeeper is airuleset's OWN
        # hand-off lane (cmd_gk_request), not just the repo-workflow's
        # ready-for-review — either equally means "out of my hands, waiting
        # on someone else" (#223 folded both into the same gk bucket).
        label_handed = ("ready-for-review" in labels) or \
            ("needs-gatekeeper" in labels)
        # #313 pt 2 (F2/F3): `prio:bounce` is the gatekeeper's own "returned
        # to the sub-dev, not ready" verdict — it overrides a stale/lagged
        # hand-off LABEL so a bounced ticket reaches `unhandled` naturally;
        # the comment-fallback walk below is what still recognises a genuine
        # RE-hand-off after a bounce -- but (#391 CRITICAL-1) only when that
        # bounce is itself VISIBLE in the comment thread (see the docstring).
        if "prio:bounce" in labels:
            label_handed = False
            bounce_numbers.add(n_num)
        handed[n_num] = label_handed

    if extra is not None:
        return rows, handed, failed

    # #191 Part B ("ownership relabel"): a SHARED-account stream's slice is
    # `label:stream:<user>` ALONE — once a handed-off ticket's stream:<user>
    # label is removed, it vanishes from `rows` entirely. Own-account streams
    # (assignee/author quals present) already see this for free via
    # author:@me. `_last_origin_owner` resolves the TEMPORALLY-LAST
    # origin-shaped labeled event, settling a competing claim correctly
    # (#191 adversarial review M3). Deliberately NEVER filtered by `extra`
    # (see the docstring above) — this branch only runs when extra is None
    # anyway.
    #
    # #391 adversarial review THEORETICAL-6 (accepted residual, no known
    # reproduction, edge-of-edge): rows recovered here make `rows` non-empty
    # unconditionally, so `cmd_slice_quals`'s C2 label-existence refusal
    # (`_refuse_unless_empty_is_trustworthy`) is skipped even if the
    # `stream:<user>` label itself was deleted from the repo — a shared-
    # account box could then print a trusted-looking `0` while genuinely
    # unlabeled, unhandled tickets sit orphaned (recovered-but-not-owned).
    # Requires repo-label deletion PLUS a prior recovered hand-off PLUS an
    # orphaned open ticket, simultaneously — not chased.
    if not failed and len(quals) == 1 and quals[0].startswith("label:stream:"):
        user = _current_user()
        raw = _gh_out("issue", "list", "--state", "open", "--search",
                      AUTOPILOT_SKIP_EXCL +
                      " label:needs-gatekeeper,ready-for-review",
                      "-L", "200", "--json", "number,labels,title,createdAt",
                      cwd=root, timeout=20)
        try:
            candidates = json.loads(raw)
        except (ValueError, TypeError):
            candidates = []
        by_num = {}
        if isinstance(candidates, list):
            for x in candidates:
                try:
                    n_num = x["number"]
                except (TypeError, KeyError):
                    continue
                if n_num in rows:
                    continue
                if _stream_owner_of(x.get("labels")):
                    continue  # currently owned by ANOTHER stream
                by_num[n_num] = x
        to_check = list(by_num)[:50]
        if to_check:
            owners = _last_origin_owner(to_check, cwd=root)
            for n_num, owner in owners.items():
                if owner == user:
                    rows[n_num] = by_num[n_num]
                    handed[n_num] = True

    # #313 pt 2: the label alone is not a reliable hand-off signal — the
    # PRIMARY signal is the READY-FOR-REVIEW comment (agents/autopilot-
    # worker.md), always postable regardless of write access. A candidate
    # the label missed is checked directly against its own comments via
    # `_comment_readiness_signal`, in creation order, keeping the LAST
    # signal (a stale pre-bounce comment is correctly invalidated by a
    # later gatekeeper finding/bounce, and a genuine post-bounce
    # re-submission overrides that again).
    #
    # #391 CRITICAL-1: for a row in `bounce_numbers`, an upgrade to handed
    # additionally requires `saw_gatekeeper_comment` -- a recognised
    # gatekeeper-authored comment (`_comment_readiness_signal` returning
    # False) seen SOMEWHERE in the walk, proving the bounce is genuinely
    # VISIBLE in the thread (a real post-bounce re-hand-off) rather than a
    # bare-label bounce with no comment at all (which must never re-flip a
    # stale pre-bounce hand-off comment back to handed).
    if slug and not failed:
        unhandled_candidates = sorted(
            (n_num for n_num in rows if not handed.get(n_num)),
            reverse=True)
        for n_num in unhandled_candidates[:_HANDOFF_COMMENT_CHECK_LIMIT]:
            raw = _gh_out("api",
                          "repos/%s/issues/%d/comments" % (slug, n_num),
                          cwd=root, timeout=20)
            try:
                comments = json.loads(raw)
            except (ValueError, TypeError):
                comments = []
            if not isinstance(comments, list):
                continue   # e.g. a bare int -- never a real answer
            verdict = False
            saw_gatekeeper_comment = False
            for c in comments:
                body = c.get("body") if isinstance(c, dict) else None
                sig = _comment_readiness_signal(body)
                if sig is False:
                    saw_gatekeeper_comment = True
                if sig is not None:
                    verdict = sig
            if verdict and (n_num not in bounce_numbers or
                            saw_gatekeeper_comment):
                handed[n_num] = True

    return rows, handed, failed


def _row_action(row, own_stream=None):
    """What THIS box may do with an issue row: `action-only` or `implement`.

    A ticket owned by a stream OTHER than this box's is in the obligation set
    because only this box can REVIEW / MERGE / CLOSE / UNBLOCK it — never
    because this box should write its code. The discriminator is deliberately
    relative to this box (`own_stream`), not absolute: a reduced-authority
    stream's own `stream:<me>` tickets ARE its to implement, and an absolute
    "carries any stream label" rule would mark every row of its own slice
    untouchable.

    A row with NO `labels` key at all is UNDETERMINABLE, not "unlabelled", and
    takes the conservative side — the same `"labels" in view` discrimination
    `_notify_run_card` already makes, and for the same reason (#181 M11: a
    failed lookup read as the negative case silently restored a pre-existing
    wrong behaviour). The two errors here are not symmetric: a sub-dev's
    ticket printed `implement` invites this box to write code on a foreign
    stream's ticket — the exact harm the column exists to prevent — while a
    core ticket printed `action-only` merely stalls visibly. `labels: []` is a
    genuinely unlabelled core ticket and stays `implement`."""
    if not isinstance(row, dict) or "labels" not in row:
        return ROW_ACTION_ONLY
    labels = row.get("labels")
    if not isinstance(labels, list) or any(not isinstance(lb, dict)
                                           for lb in labels):
        # Present but not a list of dicts (bare strings, an explicit null):
        # UNREADABLE ownership, not an absence of it. `_stream_owner_of` skips
        # non-dict entries, which would silently render this as `implement` —
        # the dangerous direction again, one layer down (adversarial review,
        # round 4).
        return ROW_ACTION_ONLY
    owner = _stream_owner_of(labels)
    if owner and owner != (own_stream or ""):
        return ROW_ACTION_ONLY
    return ROW_IMPLEMENT


def _print_issue_rows(rows, own_stream=None):
    """`number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first (the bounce
    lane picks the oldest — no client-side sort needed downstream).

    The action column is third, ahead of the title, so a title containing a
    tab cannot shift it. It is the #181-round-4 fix for the SELECTION source
    emitting no ownership discriminator: `action-only` = only this box can act
    on it and it must never write its code; `implement` = ordinary work."""
    for n in sorted(rows, key=lambda k: rows[k].get("createdAt") or ""):
        row = rows[n]
        print("%s\t%s\t%s\t%s" % (n, row.get("createdAt") or "",
                                  _row_action(row, own_stream),
                                  row.get("title") or ""))


def _refuse_unless_empty_is_trustworthy(cmd, quals, cwd=None):
    """Refuse (stderr + non-zero exit, nothing on stdout) unless an EMPTY
    search-derived result is TRUSTWORTHY. Shared by BOTH `/goal` stop-proof
    commands, called at the identical point.

    #181 round 4, CRITICAL. `_search_index_healthy()` (round 3) is the right
    guard for this defect class, but it was installed as an extra validation
    for ONE caller's zero rather than as a precondition on trusting ANY zero
    derived from the GitHub issue SEARCH index — one call site, nested inside
    `cmd_slice_quals` behind `len(quals) == 1 and quals[0].startswith("label:")`,
    the SHARED-account shape. Two paths walked straight past it, both
    reproduced live on dev1 2026-07-30 against the shipped code:

      * `cmd_core_quals` never called it at all. In a checkout whose `origin`
        still points at the pre-rename name (GitHub's issue SEARCH index does
        not follow a repo rename; the REST/repository-listing path does):
        REST 110 open issues, every `--search` 0, `core-quals --count` -> `0`
        with rc 0. The gatekeeper pastes that 0, writes the mandated BACKLOG
        EMPTY line, and stops with the whole backlog outstanding.
      * `cmd_slice_quals` on an OWN-account stream. `_slice_quals("david")` is
        `['assignee:@me', 'author:@me', 'label:stream:david']`, so
        `len(quals) == 1` is False and the guard was skipped in the very
        command round 3 fixed: stdout `0`, no SystemExit.

    The rename is only the cheapest trigger; ANY state where search answers
    empty while REST does not reaches the same line. Rounds 1-3 each moved the
    guard one call frame outward instead of making the refusal a property of
    the RESULT — "this zero came out of the search index, and nothing has
    shown the search index is answering" — which is why the class survived
    three fixes. One helper, two callers, one contract: a third stop-proof
    command gets it by calling this.

    Runs ONLY when the union is empty; a non-empty union is itself proof the
    index answers, so the healthy path costs no extra gh call."""
    if len(quals) == 1 and quals[0].startswith("label:"):
        # C2 (round 2) — a shared-account slice's ONLY signal is one label,
        # and a forgotten/never-created label makes gh search return `[]` with
        # exit 0 for a query that can never match anything.
        label_name = quals[0].split(":", 1)[1]     # "label:stream:x" -> "stream:x"
        exists = _label_exists_on_repo(label_name, cwd=cwd)
        if exists is not True:
            print(
                "%s: cannot confirm label '%s' exists on this repo (%s) — a "
                "single-label slice of 0 resting on an unconfirmed label is "
                "UNRELIABLE. Refusing rather than reporting it (#181 C2)."
                % (cmd, label_name,
                   "not found" if exists is False else "the check itself failed"),
                file=sys.stderr)
            sys.exit(1)
    healthy = _search_index_healthy(cwd=cwd)
    if healthy is False:
        print(
            "%s: gh's SEARCH path is not answering for this identity/repo (a "
            "sort-only search that must match every open issue came back "
            "empty, or the probe itself failed, while the repository listing "
            "path does show open issues) — an empty result here is NOT "
            "evidence of an empty backlog. Refusing (#181 round 4)." % cmd,
            file=sys.stderr)
        sys.exit(1)
    # healthy is True (search demonstrably works) or None (the repo has no
    # open issues at all, so an empty result is trivially correct).


HANDOFF_LABEL_WORKFLOW_HINT = "handoff"

# A COMPLETED hand-off-labeller run whose conclusion is not one of these did
# not do its job. Stated as POSITIVE evidence rather than as a list of bad
# conclusions (adversarial review, round 4): the first cut tested
# `conclusion == "failure"` literally, so `startup_failure`, `timed_out`,
# `cancelled` and `action_required` all passed as healthy — and the live
# failing labeller this guard exists for is startup-SHAPED (its failing job
# records no failing STEP), i.e. the neighbouring spelling of its own
# motivating case. `skipped` is normal and must stay here: a labeller
# legitimately skips every comment that is not a hand-off.
HANDOFF_RUN_OK_CONCLUSIONS = frozenset({"success", "skipped"})


def _handoff_label_mechanism_health(cwd=None):
    """Is the mechanism the `ready-for-review` arm rests on actually working?
    Returns `(state, detail)` with state in `ok` / `broken` / `unknown` /
    `n/a`.

    #181 round 4. The obligation set detects an outstanding sub-dev hand-off
    by the `ready-for-review` LABEL, and a read-role collaborator gets a 403
    adding that label itself — so the arm depends ENTIRELY on the repo's own
    hand-off-label workflow. Measured on zbynekdrlik/odoo-erp 2026-07-30: the
    workflow is `active` but 23 of its last 30 runs FAILED (the 5 newest all
    failed, job `label`, startup-shaped), and the repo carries 0 open
    `ready-for-review` issues. So the arm contributes a zero while the only
    thing that can produce a non-zero is failing three runs in four — this
    ticket's own failure mode by a different road. Filed as odoo-erp #2584.

    A miss is made DETECTABLE, never guessed: the alternative is a comment
    query (`"READY-FOR-REVIEW:" in:comments`), and GitHub tokenizes quoted
    phrases so that over-matches — over-counting the obligation set is the
    never-stops failure the original ticket rejected.

    `n/a` when the repo is not enrolled in the gatekeeper<->sub-dev flow, or
    when enrollment itself cannot be determined: enrollment is a static local
    fact, and if it is unknowable then nothing here depends on the workflow.
    `unknown` (which the caller treats like `broken`, exactly as C2 treats a
    failed label probe) when the repo IS enrolled but the health probe fails —
    an unreachable gh is not evidence the mechanism is fine."""
    try:
        import notify
        from watchdog import _CROSS_STREAM_REPOS as enrolled
    except Exception:
        return ("n/a", "the cross-stream registry is not resolvable here")
    try:
        name = notify.repo_name_for(cwd or os.getcwd())
    except Exception:
        name = ""
    if not name:
        return ("n/a", "the repo name is not resolvable from origin")
    if name not in enrolled:
        return ("n/a", "%s is not enrolled in the cross-stream flow" % name)

    raw = _gh_out("workflow", "list", "--all", "--json", "name,state,path",
                  cwd=cwd, timeout=20)
    try:
        flows = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh workflow list` failed on %s" % name)
    if not isinstance(flows, list):
        return ("unknown", "`gh workflow list` returned no list on %s" % name)
    match = [f for f in flows if isinstance(f, dict)
             and HANDOFF_LABEL_WORKFLOW_HINT in str(f.get("path") or "").lower()]
    if not match:
        return ("broken",
                "%s is enrolled in the cross-stream flow but carries no "
                "hand-off-label workflow, so nothing can label a hand-off"
                % name)
    inactive = [f for f in match if f.get("state") != "active"]
    if inactive:
        return ("broken", "the hand-off-label workflow is %r, not active"
                % (inactive[0].get("state"),))

    path = str(match[0].get("path") or "")
    raw = _gh_out("run", "list", "-w", path, "-L", "1",
                  "--json", "conclusion,status", cwd=cwd, timeout=20)
    try:
        runs = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh run list` failed for %s" % path)
    if not isinstance(runs, list):
        return ("unknown", "`gh run list` returned no list for %s" % path)
    if not runs:
        # An enrolled repo whose labeller has never produced a run cannot have
        # labelled any hand-off — exactly as much evidence as a failing run.
        return ("unknown", "%s has never run on %s" % (path, name))
    newest = runs[0] if isinstance(runs[0], dict) else {}
    if newest.get("status") != "completed":
        # A run still in flight has a null conclusion and is not evidence of
        # breakage; refusing on it would spin the loop for the duration of
        # every labeller run.
        return ("ok", "%s (newest run still %s)" % (path, newest.get("status")))
    if newest.get("conclusion") not in HANDOFF_RUN_OK_CONCLUSIONS:
        return ("broken", "the newest %s run concluded %r"
                % (path, newest.get("conclusion")))
    return ("ok", path)


def cmd_slice_quals(args):
    """THE single definition of "my slice" (#181) — reused verbatim by the
    reduced-authority `/goal` stop-proof templates in skills/autopilot/SKILL.md
    instead of each one hand-rolling its own `--search` string.

    Before this command existed, the templates hardcoded `--assignee @me`,
    which silently resolves to `0` on a SHARED-gh-account box (montalu/marek/
    simap — see `_slice_quals`' own docstring): `@me` there is the maintainer
    account, matching nothing assigned, so the /goal loop declared the
    backlog empty with real labelled work open. `_slice_quals()` already
    fixed this for the footer/Discord-card paths via a LIST of quals unioned
    in Python — but gh's own `--search` syntax ANDs space-joined qualifiers,
    it cannot OR them, so a caller cannot just embed that list as one
    `--search` fragment (that would silently switch an own-account stream's
    3-qual union into an intersection). This command runs the SAME per-qual
    queries + Python-side union already used by `_notify_run_card`/
    `cmd_tickets_status`, and prints only the RESULT — never a raw fragment a
    template could misuse.

    C1 (round 2, live-confirmed on dev1): this command used to build quals
    unconditionally from `_current_user()`, never consulting
    `resolve_authority()` — on a FULL-authority box (no stream at all) that
    silently built `label:stream:<linux-user>`, which matches nothing, so
    `--count` printed a clean `0` with real open work sitting untouched. It
    now REFUSES outright when this box does not resolve to a
    reduced-authority profile — never a printed count.

    C2 (round 2): on a SHARED-gh-account box (montalu/marek/simap) the
    slice is `label:stream:<user>` ALONE — a forgotten/never-created label
    makes gh search return `[]` with exit 0 for a query that can never
    match anything. A ZERO result from a single-label (shared-account)
    slice is now VALIDATED before being trusted: the label must be
    confirmed to exist on the repo, AND an `involves:@me` cross-check query
    must itself succeed (proving gh search genuinely works for this
    identity/repo, not just silently returning nothing everywhere) —
    refusing rather than trusting an unconfirmed zero.

    --count: prints an integer (0 = own UNHANDLED work is empty — the /goal
             stop-proof). #391 (2026-08-11): reversed from a raw slice count
             to own UNHANDLED work (a ticket already handed off to the
             gatekeeper — `ready-for-review`/`needs-gatekeeper`, unless
             overridden by `prio:bounce` — no longer counts), via the SAME
             shared derivation (`_slice_mine_and_handed`) `cmd_tickets_
             status`'s footer uses — the #367-established consistency guard,
             so the footer's `I N` and this stop-proof cannot silently
             drift apart. Only for the PLAIN (no `--extra`) query — see
             `--extra` below.
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, one per open
             non-skip UNHANDLED issue in the slice, OLDEST first (the bounce
             lane picks the oldest — no client-side sort needed). `action` is
             relative to THIS box, so a stream's own `stream:<me>` tickets
             read `implement` (#181 round 4).
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed). The
             handed-off exclusion still applies via a cheap LABEL-only check
             (a genuine `prio:bounce` ticket is already un-handed by that
             label's own override) — the recovery/comment-fallback
             ENRICHMENT is skipped here, since its own candidate query is
             never filtered by `extra` and could otherwise leak a ticket
             into a filtered result that does not itself match `extra`.
    No flag: prints each qual defining this box's slice, one per line
             (informational).

    A gh query failure prints to stderr and exits non-zero — NEVER prints `0`
    on failure, which would be exactly the false-empty bug this exists to
    fix.

    Authority, the slice quals and every gh query resolve against the REPO
    ROOT, not the process cwd (#181 I-5) — a project CLAUDE.md marker
    `airuleset:authority=...` was invisible to this command whenever the
    session cwd was a subdirectory, while the footer saw it, so the two
    consumers of "THE one definition" could disagree about which profile the
    box was even running."""
    root = _repo_root() or None
    authority = resolve_authority(cwd=root)
    if authority == "full":
        print(
            "slice-quals: this box resolves to FULL authority — there is no "
            "stream slice to report here. Refusing rather than printing a "
            "plausible-looking 0 (this command answers 'my slice' for a "
            "reduced-authority branch-merge/fork-no-merge stream only; "
            "#181 C1).", file=sys.stderr)
        sys.exit(1)
    user = _current_user()
    try:
        quals = _slice_quals(user, cwd=root)
    except SliceUnresolved as exc:
        print("slice-quals: %s" % exc, file=sys.stderr)
        sys.exit(1)
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    if not (want_count or want_list):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    # -L 1000 (via `_union_open_issues`, matching core-quals — #181 M-2): a
    # single population can only be UNDER-counted by a clamp, never zeroed —
    # but the documented "0 = own unhandled work is empty" contract must not
    # silently cap either. `slug` feeds the comment-fallback recovery
    # (#391) — best-effort, "" on failure just skips that enrichment.
    slug = _repo_slug(cwd=root)
    rows, handed, failed = _slice_mine_and_handed(quals, root, slug, extra=extra)
    if failed:
        print("slice-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    if not rows:
        # Round 4: the validation is no longer nested behind the SHARED-account
        # shape. An own-account stream has THREE quals, so `len(quals) == 1`
        # was False and this command skipped its own guard entirely — a false
        # SLICE EMPTY for david@subdev whenever the search index is not
        # answering. One shared helper, identical contract in both commands.
        # This checks the RAW slice (`rows`, before subtracting handed-off) —
        # a non-empty slice that is ENTIRELY handed off is a real, trusted
        # 0-unhandled result and needs no validation (the search index
        # already demonstrably answered).
        #
        # #391 adversarial review THEORETICAL-5 (accepted residual, no known
        # reproduction): "non-empty `rows`" proves the index answered
        # SOMETHING, not that it answered COMPLETELY — a partial index
        # response returning only the already-handed subset (plausible for
        # the freshest-changed ticket, e.g. one just bounced) would still
        # clear this guard and print a clean `0`. Pre-#391 the identical
        # partial answer produced a non-zero undercount instead (loop stayed
        # alive). Not chased: moving the guard to check `unhandled` directly
        # would FALSE-refuse the legitimate all-handed case this branch
        # exists to accept (verified: `test_count_excludes_a_ready_for_
        # review_ticket` fails under that change), and no real partial-
        # index-response has ever been observed (only a full-empty one, the
        # repo-rename repro this guard was built from).
        _refuse_unless_empty_is_trustworthy("slice-quals", quals, cwd=root)
    unhandled = {n: v for n, v in rows.items() if not handed.get(n)}
    if want_count:
        print(len(unhandled))
        return
    _print_issue_rows(unhandled, own_stream=user)


def cmd_core_quals(args):
    """The full-authority box's OBLIGATION set: every open, non-skip issue
    THIS box must action before its `/goal` loop may stop (#181, round 3).

    That is the CORE slice — the backlog minus every reduced-authority
    stream's own `stream:<user>` tickets, the SAME exclusion the footer
    (`cmd_tickets_status`) and the Discord run-card (`_notify_run_card`) use
    — UNIONED with every open ticket carrying a MAINTAINER_ACTION_LABELS
    label, whatever stream owns it. See `_obligation_quals()` for why the
    core partition alone is the wrong set (it excluded odoo-erp #2396/#2377,
    `stream:montalu` + `needs-gatekeeper`, which only this box can move) and
    why this is not a revert to the whole-repo count.

    NOTE: this IS the same number the footer renders as `I N` on a
    full-authority box (#367, 2026-08-11) — `cmd_tickets_status`'s
    full-authority branch calls this SAME `_obligation_quals()`/
    `_union_open_issues()` derivation, never a parallel narrower one, so the
    footer and this stop-proof cannot silently disagree about what "done"
    means. (Before #367 they deliberately differed — the footer showed only
    the narrower core partition plus a separate `· streamy M` badge for the
    hidden population; both were dropped along with the split, which removed
    the reason to keep the two numbers apart.)

    --count: prints an integer (0 = nothing left for this box to action).
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first —
             so the skill's backlog SELECTION and its stop-proof read the same
             set. The `action` column is `action-only` for a ticket a SUB-DEV
             stream owns (review / merge / close / unblock it, NEVER write its
             code) and `implement` otherwise (#181 round 4): the discriminator
             lives in the data the worker reads, not in a prose clause it may
             never have loaded.
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed) AND unions in
             the BARE `extra` query alone (#307: `prio:bounce` is no longer
             one of MAINTAINER_ACTION_LABELS, so the per-qual AND can no
             longer find a ticket carrying ONLY `extra` — the common bounce
             shape) — this no longer mirrors `slice-quals`'s pure-AND
             contract; a caller passing `--extra` gets base∧extra (every
             non-skip, non-autopilot-skip ticket matching `extra`, a
             SUPERSET of obligation∧extra), each row still marked
             `action-only`/`implement`. Without it the full-authority bounce
             seed went through a raw `gh issue list`, so the single
             highest-priority SELECTION path was the one path with neither
             this command's guard nor its ownership column — while the
             oldest open `prio:bounce` ticket on odoo-erp is #2150,
             `stream:david`.
    No flag: prints each qual whose union defines the obligation set.

    A gh query failure prints to stderr and exits non-zero — NEVER prints a
    number on failure (mirrors `slice-quals`'s own contract), and an EMPTY
    result is refused unless it is demonstrably trustworthy — see
    `_refuse_unless_empty_is_trustworthy` (#181 round 4, CRITICAL: this
    command never consulted the search-index guard at all, so a repo whose
    search index answers empty while its REST listing does not produced a
    clean stop-proof `0` with the whole backlog open)."""
    root = _repo_root() or None
    authority = resolve_authority(cwd=root)
    if authority != "full":
        # I-3: C1's fix, applied in the mirror direction. `slice-quals`
        # correctly refuses on a full box; this one answered on ANY box, so
        # run on montalu it printed a number that is neither that box's slice
        # nor a valid stop-proof for it.
        print(
            "core-quals: this box resolves to %s authority — the core/"
            "obligation slice is a FULL-authority (core / gatekeeper) "
            "question. Use `slice-quals` here. Refusing rather than printing "
            "a plausible-looking number (#181 I-3)." % authority,
            file=sys.stderr)
        sys.exit(1)

    quals = _obligation_quals()
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    if not (want_count or want_list):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    if isinstance(extra, str):
        # A whitespace-only --extra is truthy but carries no real qualifier —
        # left unstripped it still passes `if extra:` below and the new
        # bare-extra branch would union in a BARE "-label:autopilot-skip"
        # query, the exact whole-repo never-stops shape #181 rejected
        # (adversarial review of #307).
        extra = extra.strip() or None
    base = AUTOPILOT_SKIP_EXCL + ((" " + extra) if extra else "")
    search_quals = quals
    if extra:
        # #307: `prio:bounce` is no longer one of MAINTAINER_ACTION_LABELS,
        # so a per-qual AND (base + each obligation qual) can never find a
        # ticket that carries ONLY `extra` (e.g. a bare `prio:bounce`, no
        # core membership, no `needs-gatekeeper`, no `ready-for-review`) —
        # the common real shape the bounce-lane SEED (Step 3.1) depends on.
        # The old code found it BY ACCIDENT (prio:bounce AND'd with itself
        # degenerates to itself); this restores that coverage explicitly, by
        # unioning in the BARE `extra` query (qual="" -> search=base alone)
        # alongside the per-qual AND queries. Safe only because `extra` is
        # non-empty here — with no `extra` this branch never runs, so the
        # plain obligation proof never gets the bare whole-repo query #181
        # rejected.
        search_quals = quals + [""]
    seen, failed = _union_open_issues(search_quals, base, cwd=root)
    if failed:
        print("core-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    if not seen:
        _refuse_unless_empty_is_trustworthy("core-quals", quals, cwd=root)
    if not seen and not extra:
        # The `ready-for-review` arm of this set rests ENTIRELY on the repo's
        # own hand-off-label workflow (a read-role stream gets a 403 adding
        # the label itself). A zero that rests on a mechanism which may have
        # MISSED a hand-off is not evidence — same shape as C2's "validate
        # the evidence's own existence before trusting its absence of hits".
        #
        # `not extra` is load-bearing: with a filter (`--extra
        # "label:prio:bounce"`, the bounce seed) the question asked is "any
        # open bounce ticket?", and "none" is an ordinary answer that does not
        # depend on hand-off labels at all. Gating that would refuse a
        # legitimate result and spin the loop forever on the one repo the
        # cross-stream flow runs on — the mirror of this ticket's own bug, not
        # a safer version of it. The arm belongs to the UNFILTERED obligation
        # set, which is what the stop-proof reads. (The search-index guard
        # above stays unconditional: a dead index makes a FILTERED answer just
        # as meaningless as an unfiltered one.)
        health, detail = _handoff_label_mechanism_health(cwd=root)
        if health not in ("ok", "n/a"):
            print(
                "core-quals: the obligation set is empty, but the "
                "`ready-for-review` arm rests on this repo's hand-off-label "
                "workflow and that mechanism is %s (%s) — a hand-off can be "
                "outstanding with no label, so this 0 is NOT evidence. "
                "Refusing (#181 round 4)." % (health, detail),
                file=sys.stderr)
            sys.exit(1)
    if want_count:
        print(len(seen))
        return
    # own_stream=None: a full-authority box owns no stream, so EVERY
    # stream-labelled row in its obligation set is action-only.
    _print_issue_rows(seen, own_stream=None)


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


def _pick_free_port(ips, ports):
    """The first port in `ports` that `ips` can actually BIND — None if none can.

    The pre-#115 scan probed `connect_ex(("127.0.0.1", cand))`, but
    `filedrop._is_private` EXCLUDES loopback and `upload_server.py` binds exactly
    `bind_ips()`, deliberately never 0.0.0.0 (a WRITE endpoint on a box that may
    have a public IP). The probe's address and the server's addresses were
    therefore disjoint BY CONSTRUCTION: a live endpoint answers on none of the
    addresses the scan asked about, so the scan handed out an occupied port
    (observed on dev1: five listeners on :8799, scan picked 8799, and the second
    endpoint then failed to bind anything).

    Binding the very addresses the server is about to bind asks the server's own
    question. Only EADDRINUSE rejects a candidate — any other error
    (EADDRNOTAVAIL from a stale or departed interface) is tolerated, because
    upload_server.py SKIPS such an address rather than dying on it and needs only
    one success; treating that as "occupied" would let one stale IP reject every
    candidate on a box that serves fine. SO_REUSEADDR mirrors
    HTTPServer.allow_reuse_address, so the probe's verdict is the server's."""
    import errno
    import socket

    for port in ports:
        for ip in ips:
            s = socket.socket()
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((ip, port))
            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    break
            finally:
                s.close()
        else:
            return port
    return None


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
    self-expires after --ttl seconds."""
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
    reachable = [u for u in urls if _live(u)] or [urls[0]]
    for u in reachable:   # one URL per interface — open whichever your network reaches
        print(u)
    print(f"dest={dest}  ttl={ttl}s  log={log}")
    print("Otvor ktorúkoľvek URL v prehliadači (podľa siete). Po nahratí over: grep SAVED "
          + str(log))


SECRET_ACTIONS = ("request", "status", "list", "exec", "forget", "purge")
# Both lifetimes are CLAMPED, not merely defaulted. `int(args.ttl or DEFAULT)`
# let a negative value through (0 is falsy and fell back; -1 is truthy), and the
# server armed its shutdown timer only for a positive TTL — so `--ttl -1` gave a
# credential-receiving endpoint with no timer at all, alive until reboot, while
# its store record was already expired and `status` reported `absent`.
SECRET_MIN_TTL_S, SECRET_MAX_TTL_S = 30, 3600
SECRET_MIN_KEEP_S, SECRET_MAX_KEEP_S = 60, 24 * 3600


def _secret_clamp_ttl(value):
    return max(SECRET_MIN_TTL_S, min(int(value), SECRET_MAX_TTL_S))


def _secret_clamp_keep(value):
    return max(SECRET_MIN_KEEP_S, min(int(value), SECRET_MAX_KEEP_S))
# A distinct range from `upload`'s 8799-8819, so the two endpoint kinds can
# never be confused for one another by a port alone.
SECRET_PORTS = range(8830, 8850)


def _secret_bindable(ip):
    """May a credential endpoint listen here?

    The CLI-side half of the two independent checks (the other is
    `filedrop/vault_server.py:is_private`, which re-validates its own argv). A
    public address is refused outright — the token in the path is the endpoint's
    only auth, and on a box with a public IP (the gatekeeper VPS) an open
    credential endpoint on the internet is not a recoverable mistake. Loopback
    is allowed here even though `filedrop._is_private` drops it for the file
    endpoints: it cannot leave the box, so it is strictly more private than
    tailscale — it is simply not reachable BY the user, which the URL print
    makes obvious.
    """
    from filedrop import _is_private
    return bool(_is_private(ip) or (isinstance(ip, str) and ip.startswith("127.")))


# Interfaces whose traffic is encrypted BEFORE it leaves the box, so a plain
# HTTP endpoint on them is not actually in the clear. Deliberately does NOT
# include `tun` — `tunl0` is IPIP, a tunnel with no encryption at all, and the
# only safe direction for this label is to under-claim.
_SECRET_ENCRYPTED_IFACE = ("tailscale", "wg", "wireguard", "zt")


def _secret_iface_for(ip):
    """The interface `ip` is configured on, or None."""
    from filedrop import _iface_ips
    for cand, ifname in _iface_ips():
        if cand == ip:
            return ifname
    return None


def _secret_opener():
    """A urllib opener with proxies DISABLED.

    The default opener reads $http_proxy/$https_proxy, so the liveness probe
    was sending its URL to a proxy host — and a proxy error page returning 200
    made a dead endpoint look live. This probe only ever targets an address on
    this very machine; a proxy is never right for it.
    """
    import urllib.request as _u
    return _u.build_opener(_u.ProxyHandler({}))


def _secret_health_url(ip, port):
    """The liveness probe's URL — deliberately token-free.

    The probe used to GET the token URL, which hands the endpoint's only
    authentication to whatever is listening on that port. If another local
    user's process won the pick-a-port race, that is their listener.
    """
    return "http://%s:%d/healthz" % (ip, port)


def _secret_probe_urls(ips, port):
    """Every address's liveness URL — the list the readiness loop consumes.

    A named helper rather than an inline comprehension because the inline one
    is exactly what silently kept probing the TOKEN urls after the health route
    was added: nothing could assert on it.
    """
    return [_secret_health_url(ip, port) for ip in ips]


def _secret_is_encrypted(ip, iface=None):
    """True when traffic to `ip` is encrypted before it leaves the box.

    Tailscale by CIDR, loopback (it never leaves at all), and any interface
    whose name starts with a known encrypted-overlay prefix. Everything else is
    genuine cleartext, whatever its address range says — the ranges do not
    carry the answer, since wg0 and a zerotier both look like plain LAN.
    """
    from filedrop import _is_tailscale
    if _is_tailscale(ip) or str(ip).startswith("127."):
        return True
    if iface is None:
        iface = _secret_iface_for(ip)
    return bool(iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE))


def _secret_partition_ips(ips):
    """(encrypted, cleartext) — the split `request` advertises from."""
    enc, plain = [], []
    for ip in ips:
        (enc if _secret_is_encrypted(ip) else plain).append(ip)
    return enc, plain


def _secret_select_ips(ips, allow_plain=False):
    """(chosen, dropped) addresses for a credential endpoint.

    Cleartext is OPT-IN. A LAN URL carries the token in the request line and
    the credential in the POST body with nothing around them, and offering it
    beside the encrypted one means some of the time it gets picked. A box with
    only cleartext returns an EMPTY chosen list rather than falling back — the
    caller then tells the user to re-run with --allow-plain, which is a
    decision, not a default.
    """
    enc, plain = _secret_partition_ips(ips)
    if allow_plain:
        return enc + plain, []
    return enc, plain


def _secret_url_line(ip, port, token, iface=None):
    """One advertised URL plus its TRANSPORT, spelled out.

    The ticket's own requirement: when several URLs are offered the user must be
    able to SEE which one is encrypted before deciding where to type a password.

    The label keys on the INTERFACE, not on the address range, because the
    ranges do not carry the answer: `bind_ips()` legitimately advertises real
    overlays, and on this box wg0 (10.88.*), wg-money (192.168.10.*) and a
    zerotier (10.243.*) all look exactly like plain LAN addresses. Calling an
    encrypted tunnel "NEŠIFROVANÉ" steers the user to the worse option on the
    one page where it matters most.
    """
    from filedrop import _is_tailscale
    url = "http://%s:%d/%s/" % (ip, port, token)
    if _is_tailscale(ip):
        return "%s   [tailscale — šifrované (WireGuard), odporúčané]" % url
    if str(ip).startswith("127."):
        return "%s   [loopback — len z tohto stroja]" % url
    if iface is None:
        iface = _secret_iface_for(ip)
    if iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE):
        return "%s   [%s — šifrovaný tunel]" % (url, iface)
    return "%s   [LAN — NEŠIFROVANÉ (plain HTTP), použi radšej tailscale]" % url


def _secret_redact(blob, value, marker=b"<<REDACTED>>"):
    """`blob` with every anticipated rendering of `value` replaced.

    A child of `secret exec` must not be able to put the credential on the
    CLI's stdout/stderr, because those are the agent's transcript — the one
    place this whole channel exists to keep the value out of. The child's argv
    is chosen by the agent, so `secret exec DB_PASS -- env` was a one-command
    leak and any verbose or failing child (`curl -v`, `bash -x`, a tool that
    echoes its config on error) was an accidental one.

    Fragments shorter than 4 bytes are NOT redacted: at that length the value
    matches ordinary text everywhere and the filter would destroy the child's
    output instead of protecting anything.

    TWO KINDS OF RENDERING, and the second was missing (#153 finding 2). An
    ENCODING re-encodes the value whole (b64, hex, percent) — those were
    covered from the start. ESCAPING leaves the value's own bytes in place and
    rewrites only its metacharacters, so a search for the raw value misses it
    completely: for a value containing a quote, a backslash or a newline,
    `json.dumps({"pw": v})` and `repr(v)` both passed straight through. That is
    the ACCIDENTAL class this filter exists for — a child dumping its config as
    JSON, a traceback printing a dict — not a deliberate transformation, so the
    gap was real and the old docstring's disclaimer did not cover it. The
    escaped forms a stdlib dump actually produces are now in the set: JSON,
    repr() of a str and of bytes, unicode_escape, HTML/XML escaping,
    shell-quoting, and configparser's %-doubling.

    HONEST LIMIT, stated rather than implied: this stops the value appearing
    VERBATIM, in an obvious encoding, or in an ordinary escaped rendering. A
    child that deliberately transforms it (encrypts it, reverses it, prints it
    a character per line, base64s it twice) still defeats the filter — nothing
    at this layer can prevent that, because the session genuinely has to be
    able to USE the credential. The containment that would close it —
    resolving the command from a user-written template instead of agent
    argv — was filed as #154 and is now IMPLEMENTED for a TEMPLATED name:
    `cmd_secret`'s `exec` action refuses agent-supplied `-- CMD` argv outright
    once `filedrop.vault.has_template(name)` is true, and runs only
    `read_template(name)`'s result instead — the child is whatever the
    operator wrote, never whatever the agent chose, so this whole class of
    deliberate transformation no longer applies to it. Templating is OPT-IN
    per name: an UNTEMPLATED name keeps the full residual above, unchanged.

    THE OTHER RESIDUAL, which redaction cannot touch at all: this filters the
    child's captured fd 1/2 ONLY. Nothing constrains where the child WRITES —
    `secret exec DB -- sh -c 'echo "$DB" > config.ini'` puts the value in a
    git-tracked file, and no output filter can see that happen.
    """
    import base64
    import html
    import json
    import shlex
    import urllib.parse

    if not value:
        return blob
    # The floor is on the VALUE, not on each derived form. Escaping EXPANDS:
    # `"` renders as `&quot;` and `<` as `&lt;`, both of which clear a
    # per-form floor — so a per-form test silently broke this docstring's own
    # promise and turned every `&quot;` in a child's HTML output into the
    # marker. Base64 had the same shape long before the escaped forms existed
    # (one byte encodes to four characters).
    if len(value.strip()) < 4:
        return blob
    forms = {value, value.strip()}
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        forms.add(urllib.parse.quote(text).encode())
        forms.add(urllib.parse.quote_plus(text).encode())
        # Escaped renderings. Each is sliced to the BODY the dump would embed,
        # without the quotes the dumper adds around it, so the form matches
        # wherever it is nested (a dict, a config line, a traceback).
        forms.add(json.dumps(text)[1:-1].encode())
        # ensure_ascii=False is an ordinary dump option and renders a
        # non-ASCII value completely differently from the default.
        forms.add(json.dumps(text, ensure_ascii=False)[1:-1].encode())
        forms.add(repr(text)[1:-1].encode())
        forms.add(repr(value)[2:-1].encode())
        forms.add(text.encode("unicode_escape"))
        forms.add(html.escape(text).encode())
        forms.add(html.escape(text, quote=False).encode())
        forms.add(shlex.quote(text).encode())
        forms.add(text.replace("%", "%%").encode())
    forms.add(base64.b64encode(value))
    forms.add(base64.b64encode(value).rstrip(b"="))
    forms.add(base64.urlsafe_b64encode(value))
    forms.add(base64.urlsafe_b64encode(value).rstrip(b"="))
    forms.add(value.hex().encode())
    forms.add(value.hex().upper().encode())      # plenty of tools print caps
    out = blob
    # Longest first, so a form that contains another does not leave a tail.
    for form in sorted((f for f in forms if len(f) >= 4), key=len, reverse=True):
        out = out.replace(form, marker)
    return out


def _secret_apply_remainder(args):
    """Move the flags argparse's REMAINDER swallowed back onto `args`.

    `cmd` is an `argparse.REMAINDER` (needed so `exec NAME -- CMD ...` can carry
    arbitrary child arguments), and REMAINDER stops parsing at the first token
    after the positional NAME — so `secret request DB_PASS --ttl 900` puts
    `--ttl 900` in the remainder and the flag is silently ignored. Found live:
    a request made with `--ttl 900 --keep 900` reported `endpoint-ttl=600s
    keep=28800s` and stored the value with the 8-hour default.

    Consume only OUR flags, only from the head, and stop dead at `--` so a flag
    meant for the child is never eaten (`exec DB -- psql --ttl 1` keeps its own
    `--ttl`). An explicitly parsed value wins — the remainder only ever fills a
    field argparse left unset.
    """
    ints = {"--ttl": "ttl", "--keep": "keep", "--port": "port"}
    strs = {"--env": "env"}
    rest = list(getattr(args, "cmd", None) or [])
    while rest:
        tok = rest[0]
        if tok == "--":
            rest.pop(0)
            break
        if tok in ("--stdin", "--replace", "--allow-plain"):
            setattr(args, tok[2:].replace("-", "_"), True)
            rest.pop(0)
            continue
        key, eq, inline = tok.partition("=")
        if key not in ints and key not in strs:
            break                           # not ours — it belongs to the child
        if eq:
            value, width = inline, 1
        elif len(rest) > 1:
            value, width = rest[1], 2
        else:
            break                           # a dangling flag: leave it visible
        dest = ints.get(key) or strs[key]
        if key in ints:
            try:
                value = int(value)
            except ValueError:
                break                       # not a flag of ours after all
        if getattr(args, dest, None) is None:
            setattr(args, dest, value)      # an explicit value always wins
        del rest[:width]
    args.cmd = rest


def cmd_secret(args):
    """Receive a CREDENTIAL from the user through a URL — never through chat.

    A password / SSH key / PAT / token typed into the chat is written
    permanently into the session transcript (`~/.claude/projects/**/*.jsonl`),
    survives compaction, and cannot be revoked. This command is the alternative:
    `request` prints a one-shot URL, the user posts the value from their own
    browser, and the session learns only that the NAME is ready. The value is
    stored 0600 under `~/.claude/secrets/` and is handed to a child process by
    `exec` — no action here prints it, and there is deliberately no action that
    could.

    WHAT THAT DOES NOT COVER — the residuals, stated here because this is where
    a reader forms their belief about the channel (#153 finding 3):

      * The store is readable by the AGENT'S OWN UID. "No action here prints
        it" is a claim about this command, not about the box: `cat` is not one
        of this command's actions. That path is now closed by an artifact,
        `hooks/block-vault-store-read.sh` — but it is a GUARDRAIL, not a
        boundary, because the agent's uid holds NOPASSWD sudo on these boxes,
        so no store location is out of its reach. It guarantees the unsafe path
        is refused by default and that circumventing it leaves an artifact.
      * `exec`'s CHILD is unconstrained on disk FOR AN UNTEMPLATED NAME.
        Output redaction covers the captured fd 1/2 only; nothing stops the
        child WRITING the value anywhere, including a git-tracked file —
        `secret exec DB -- sh -c 'echo "$DB" > config.ini'` is not something
        any output filter can see. #154 closes this for a TEMPLATED name:
        `secret exec NAME` for a name locked to a user-written command
        template (`~/.claude/secrets/NAME.template`, see filedrop/vault.py's
        `has_template`/`read_template`) runs ONLY that command —
        agent-supplied `-- CMD` argv is refused outright — so the child is
        whatever the operator wrote, never whatever the agent chose. An
        UNTEMPLATED name keeps the residual above unchanged: templating is
        opt-in per name, never a blanket requirement. Templating has its OWN
        honest limit, stated where the file lives (filedrop/vault.py's own
        module docstring) rather than gestured at here: this repo ships no
        function that WRITES a template, on purpose — any such function
        would be reachable by `python3 -c "..."`, a route no text-matching
        hook can see, so the only safe design is to not have one. A template
        is authored by placing the file directly, by a means outside
        anything this repo ships.
      * `exec` BUFFERS all child output until the child exits
        (`capture_output=True`, required in order to filter it). So there is no
        streaming for a long-running or interactive child, and if the CLI is
        killed mid-run the child's output is lost entirely.
      * `request` PRINTS a capability URL into the transcript BY DESIGN — that
        is how the user receives it. The token in it is live for the endpoint
        TTL (default 600s, capped at 3600s), so for that window anyone who can
        read the transcript AND reach a private interface can POST a SUBSTITUTE
        credential before the user does. The nonce binds the endpoint to the
        request, not to whoever posts. Keep the TTL short; `secret forget`
        cancels a pending endpoint.
      * TTL is swept HOURLY (watchdog job 29), so a value stored with the 60s
        minimum `keep` can survive up to ~1h past its expiry.
    """
    import secrets as _secrets
    import shlex
    import subprocess
    import time

    from filedrop import bind_ips
    from filedrop import vault as st

    _secret_apply_remainder(args)
    action = args.action
    name = getattr(args, "name", None)

    # Opportunistic TTL sweep on EVERY invocation — the guarantee that a value
    # cannot lie on disk indefinitely must not depend on anyone remembering to
    # run `purge` (the same shape filedrop.share.prune has).
    expired = st.purge()

    def _need_name():
        if not name:
            print("secret %s: needs a NAME" % action, file=sys.stderr)
            sys.exit(2)
        try:
            st.check_name(name)
        except st.SecretError as e:
            print("secret: %s" % e, file=sys.stderr)
            sys.exit(2)
        return name

    if action == "purge":
        print("purged: %s" % (", ".join(expired) if expired else "nothing"))
        return

    if action == "list":
        # Keyed by name so a #154 template-only entry (no `.secret`/`.meta`
        # at all) can be unioned in without touching `list_entries()`/
        # `_entry_names()` — those drive `purge()`'s sweep, and a template
        # has no expiry to purge (see `template_names()`'s own docstring).
        rows = {r[0]: r for r in st.list_entries()}
        templated = set(st.template_names())
        names = sorted(set(rows) | templated)
        if not names:
            print("no secrets stored")
            return
        print("%-24s %-8s %-9s %-26s %s"
              % ("NAME", "STATE", "TEMPLATE", "RECEIVED", "EXPIRES"))
        for nm in names:
            if nm in rows:
                _nm, row_state, _req, recv, exp = rows[nm]
            else:
                row_state, recv, exp = st.state(nm), "-", "-"
            print("%-24s %-8s %-9s %-26s %s"
                  % (nm, row_state, "yes" if nm in templated else "no", recv, exp))
        return

    if action == "status":
        nm = _need_name()
        state = st.state(nm)
        meta = st.read_meta(nm)
        extra = ""
        if state == "ready" and isinstance(meta.get("expires_at"), (int, float)):
            extra = "  expires=%s" % st._iso(meta["expires_at"])
        # The COMMAND, not the value — non-sensitive, and showing it is what
        # "does it know whether a name is templated" (#154) asked for.
        try:
            if st.has_template(nm):
                extra += "  templated=%s" % shlex.join(st.read_template(nm))
        except st.SecretError as e:
            extra += "  templated=<error: %s>" % e
        print("%s %s%s" % (nm, state, extra))
        return

    if action == "forget":
        nm = _need_name()
        try:
            done = st.forget(nm)
        except st.SecretError as e:
            print("secret forget: %s" % e, file=sys.stderr)
            sys.exit(1)
        print("%s %s" % (nm, "forgotten" if done else "was not stored"))
        return

    if action == "exec":
        nm = _need_name()
        cmd = list(getattr(args, "cmd", None) or [])
        # #154: a TEMPLATED name is LOCKED — agent-supplied `-- CMD` is
        # refused outright, and the template's own argv is used instead.
        # Resolved BEFORE `read_value` so a locked name with a bad CMD
        # refuses without ever touching the stored value.
        try:
            templated = st.has_template(nm)
        except st.SecretError as e:
            print("secret exec: %s" % e, file=sys.stderr)
            sys.exit(1)
        if templated:
            if cmd:
                print("secret exec: %s is locked to a command template — "
                      "the CMD after `--` is refused (omit it; the "
                      "templated command always runs)" % nm, file=sys.stderr)
                sys.exit(2)
            try:
                cmd = st.read_template(nm)
            except st.SecretError as e:
                print("secret exec: %s" % e, file=sys.stderr)
                sys.exit(1)
        if not cmd:
            print("secret exec: needs a command after `--`", file=sys.stderr)
            sys.exit(2)
        try:
            value = st.read_value(nm)
        except st.SecretError as e:
            print("secret exec: %s" % e, file=sys.stderr)
            sys.exit(1)
        st.log_event("used", nm)
        # NEVER let the child inherit fd 1/2: those are the agent's transcript.
        # Capture, filter, then re-emit — so a child that echoes its own
        # environment or config cannot write the credential into a file nobody
        # can revoke (adversarial review, finding 1).
        if getattr(args, "stdin", False):
            # stdin, so the value is not in the child's environment at all
            # (/proc/<pid>/environ is owner-only, but a child that dumps its own
            # env into a log is a real shape).
            res = subprocess.run(cmd, input=value, capture_output=True)
        else:
            env = dict(os.environ)
            given = getattr(args, "env", None)
            # `is not None`, not `or`: an explicitly EMPTY --env is a caller
            # error, not a request for the default.
            key = nm if given is None else given
            try:
                # The same grammar as a secret name, and for the same reason:
                # an unchecked key is an injection point (`BASH_FUNC_x%%` makes
                # a bash child EXECUTE the value; `A=B` splits into two).
                st.check_name(key)
            except st.SecretError as e:
                print("secret exec: bad --env key: %s" % e, file=sys.stderr)
                sys.exit(2)
            try:
                env[key] = value.decode("utf-8")
            except UnicodeDecodeError:
                print("secret exec: %s is not UTF-8 — use --stdin" % nm,
                      file=sys.stderr)
                sys.exit(1)
            res = subprocess.run(cmd, env=env, capture_output=True)
        for stream, data in ((sys.stdout, res.stdout), (sys.stderr, res.stderr)):
            if data:
                stream.buffer.write(_secret_redact(data, value))
                stream.flush()
        sys.exit(res.returncode)

    # --- request -----------------------------------------------------------
    nm = _need_name()
    state = st.state(nm)
    if state == "ready":
        print("%s is already stored — `secret forget %s` first" % (nm, nm),
              file=sys.stderr)
        sys.exit(1)
    if state == "pending":
        # A second endpoint for the same name means two live tokens, neither
        # invalidating the other. Replacing is fine, but it must be asked for.
        if not getattr(args, "replace", False):
            print("%s already has a pending request — finish it, or re-run "
                  "with --replace to cancel it and issue a new URL" % nm,
                  file=sys.stderr)
            sys.exit(1)
        print("cancelling the previous request: %s" % st.stop_endpoint(nm))
        st.forget(nm)
    ttl = _secret_clamp_ttl(getattr(args, "ttl", None) or st.DEFAULT_ENDPOINT_TTL_S)
    keep = _secret_clamp_keep(getattr(args, "keep", None) or st.DEFAULT_KEEP_S)

    private = [ip for ip in bind_ips() if _secret_bindable(ip)]
    if not private:
        print("secret: no private interface to bind (refusing a public bind)",
              file=sys.stderr)
        sys.exit(1)
    # Bind only what we advertise: an address the user is not being offered is
    # pure attack surface.
    ips, dropped = _secret_select_ips(private,
                                      allow_plain=getattr(args, "allow_plain", False))
    if not ips:
        print("secret: only unencrypted interfaces are available (%s). A "
              "credential would cross the LAN in cleartext — re-run with "
              "--allow-plain if that is acceptable here."
              % ", ".join(dropped), file=sys.stderr)
        sys.exit(1)

    port = int(getattr(args, "port", None) or 0) or _pick_free_port(ips, SECRET_PORTS)
    if port is None:
        print("secret: no free port in %d-%d" % (SECRET_PORTS[0], SECRET_PORTS[-1]),
              file=sys.stderr)
        sys.exit(1)

    token = _secrets.token_urlsafe(24)
    nonce = st.register_request(nm, endpoint_ttl_s=ttl, keep_s=keep)
    # The endpoint's own diagnostics (bind failures) — NOT the value log, and
    # the server deliberately never writes the token or the body here.
    endpoint_log = st.log_path().parent / ("endpoint-%d.log" % port)
    endpoint_log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(endpoint_log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # The token goes through the ENVIRONMENT, never argv: /proc/<pid>/cmdline is
    # world-readable (0444) and these boxes host foreign uids, while
    # /proc/<pid>/environ is owner-only (0400). In argv, the endpoint's only
    # auth would be readable by every local account for the whole TTL.
    child_env = dict(os.environ)
    child_env["AIRULESET_VAULT_TOKEN"] = token
    child_env["AIRULESET_VAULT_NONCE"] = nonce
    with os.fdopen(fd, "ab") as lf:
        child = subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "vault_server.py"),
             str(port), ",".join(ips), nm, str(ttl), str(keep)],
            stdout=subprocess.DEVNULL, stderr=lf, stdin=subprocess.DEVNULL,
            env=child_env, start_new_session=True)
    # Recorded so `forget` and the TTL sweep can actually STOP the endpoint,
    # rather than deleting the value while its URL stays open.
    st.record_endpoint(nm, child.pid)

    probes = _secret_probe_urls(ips, port)

    opener = _secret_opener()

    def _live(u):
        try:
            # /healthz answers 204. Accepting only 200 made every probe read
            # "dead" and `request` printed no URL at all — see the tests.
            return opener.open(u, timeout=2).status in (200, 204)
        except OSError:
            return False

    for _ in range(20):
        if any(_live(u) for u in probes):
            break
        time.sleep(0.25)
    else:
        print("secret: endpoint failed to come up — see %s" % endpoint_log,
              file=sys.stderr)
        sys.exit(1)

    for ip in ips:
        if _live(_secret_health_url(ip, port)):
            print(_secret_url_line(ip, port, token))
    if dropped:
        print("(skipped %s — cleartext; --allow-plain offers them too)"
              % ", ".join(dropped))
    print("name=%s  endpoint-ttl=%ds  keep=%ds" % (nm, ttl, keep))
    print("Otvor URL v prehliadači a vlož hodnotu — do chatu ju NEPÍŠ. "
          "Stav: `airuleset.py secret status %s`." % nm)


def cmd_fable_gate(args):
    """Budget gate for AUTOMATIC Fable escalation (model-tiering policy 2026-07-03):
    exit 0 + `OPEN ...` when the Fable weekly + shared weekly windows have headroom
    (< threshold, default 80% / AIRULESET_FABLE_GATE_PCT), exit 1 + `CLOSED ...`
    otherwise (incl. missing/stale cache — fail-safe: no blind Fable burn). The
    orchestrator / autopilot supervisor runs this ONCE per hard task/batch before
    dispatching `model: fable`; CLOSED → dispatch opus instead."""
    from watchdog import fable_gate
    ok, reason = fable_gate(threshold=getattr(args, "threshold", None))
    print(("OPEN " if ok else "CLOSED ") + reason)
    sys.exit(0 if ok else 1)


def _burn_remote_cmd(remote, days):
    """Pure ssh-command builder for a remote `burn` collection — invokes that
    box's OWN already-deployed `airuleset.py burn --json` (the box gets this
    module from the ordinary `push` deploy; never scp'd separately, per
    `deploy-from-clean-tree.md`). Split out from `_burn_remote` so the
    command shape is unit-testable without a real network call."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py burn --json --days {days}"]


def _remote_ssh_prefix(remote):
    """The identity/sshpass selection shared by every remote collection.

    ONE place, so a second collector (`_delegation_remote_cmd`, #130) reuses
    the sanctioned ssh shape byte-for-byte instead of inventing a parallel one
    — `hooks/block-subdev-ssh-misuse.sh` guards exactly this.

    #342: the same per-connection retry-cap hardening a sibling ticket
    already added to `cmd_push`'s deploy loop and
    `provision_subdev_soniox_key()` — BatchMode=yes on the identity branch
    so a failed pubkey attempt against an unprovisioned/misconfigured
    account fails IMMEDIATELY instead of falling through to an interactive
    password/keyboard-interactive retry, and NumberOfPasswordPrompts=1 on
    the sshpass branch so a wrong/unprovisioned password is tried ONCE
    instead of openssh's own default of 3 (sshpass happily re-supplies the
    same password on every re-prompt). Deliberately NOT porting that
    sibling ticket's cross-account "never re-probe a known-bad host this
    run" tracking set here — `_burn_remote`/`_delegation_remote` each open
    EXACTLY ONE connection per host per call and never retry a failed one
    (a `--host all` run still visits every host, but only once each, same
    as a single-host run), so there is no in-process retry-storm shape for
    that tracking set to guard against; and job 16's fleet fetch (the third
    caller, via `_fleet_remote_cmd` below) already gates each host to at
    most one attempt per UTC hour — regardless of whether that attempt
    succeeds or fails, since `fleet_burn_job` claims the hour unconditionally
    once its `fetch()` returns — spreading any retries comfortably inside a
    typical fail2ban findtime window without new state."""
    identity = remote.get("identity")
    if identity:
        return ["ssh", "-i", os.path.expanduser(identity),
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                f"{remote['user']}@{remote['host']}"]
    return ["sshpass", "-p", "newlevel", "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "NumberOfPasswordPrompts=1",
            f"{remote['user']}@{remote['host']}"]


def _burn_remote(remote, days):
    """Collect one remote box's burn report over ssh. Fail-safe: any ssh
    error, non-zero exit, or unparsable stdout prints a WARN to stderr and
    returns None — one unreachable box never aborts the whole report."""
    import subprocess
    cmd = _burn_remote_cmd(remote, days)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"  WARN: burn collection failed for {remote['name']}: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: burn collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print(f"  WARN: burn collection returned invalid JSON for {remote['name']}",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# #55 follow-up — fleet-wide hourly collection for the watchdog's job 16
# (fleet_burn_job). Unlike `_burn_remote` above (which re-runs a full
# `airuleset.py burn --json --days N` scan remotely — heavy, and NOT what an
# hourly poll needs), this just TAILS the box's own job-13 output
# (`~/.claude/burn-history/snapshots.jsonl`, already written locally every
# hour by every managed box) — cheap, no remote transcript scanning. Reuses
# the EXACT same identity/sshpass selection as `_burn_remote_cmd` — never
# invent a new ssh shape (hooks/block-subdev-ssh-misuse.sh guards this).
#
# #286 follow-up (2026-08-09) — the SAME ssh round-trip ALSO tails the
# remote's own `~/.claude/airuleset-usage-cache.json` (a marker line, never
# a second connection — #269's own design comment rejected a separate
# second ssh call for exactly the doubled-cost reason), so
# `group_fleet_by_account()` can resolve a real weekly %/reset for EVERY
# reachable account, not just the reporting box's own.
# --------------------------------------------------------------------------- #

# Separates the snapshot-tail output from the usage-cache output in ONE
# combined stdout — chosen to be something no real JSON line or shell
# output would ever legitimately contain.
_FLEET_CACHE_MARKER = "===AIRULESET-FLEET-CACHE==="


def _fleet_remote_cmd(remote):
    """Pure ssh-command builder — split out for unit-testability, mirroring
    `_burn_remote_cmd`'s own split. One ssh call, two commands: the
    pre-existing snapshot tail (byte-identical substring, still asserted
    verbatim by TestFleetRemoteCmd), then the marker, then a best-effort
    cat of the remote's own usage cache (`2>/dev/null || true` — a
    missing/unreadable cache must never make the WHOLE ssh call fail,
    since the snapshot half is still perfectly good data on its own).

    #342: this used to duplicate `_remote_ssh_prefix()`'s identity/sshpass
    branching inline instead of calling it — so this docstring's own claim
    of reusing "the EXACT same identity/sshpass selection" was false, and
    a hardening fix landing on `_remote_ssh_prefix()` alone would silently
    NOT reach job 16's fleet fetch. Calling the shared builder directly
    makes the claim true by construction and guarantees this stays in sync
    with `_burn_remote_cmd`/`_delegation_remote_cmd` automatically.
    (Merge note, #342+#286: the #286 combined-command extension above rides
    the SAME shared builder — the extended remote_cmd string is the one
    argument appended after the shared prefix.)"""
    remote_cmd = (
        "tail -n 1 ~/.claude/burn-history/snapshots.jsonl; "
        "echo '" + _FLEET_CACHE_MARKER + "'; "
        "cat ~/.claude/airuleset-usage-cache.json 2>/dev/null || true"
    )
    return _remote_ssh_prefix(remote) + [remote_cmd]


def _hour_bucket_of_ts(ts_str):
    """Epoch-hour bucket (`int(epoch_seconds // 3600)`) of an ISO-8601
    timestamp STRING, converted to UTC first — comparing raw hour-of-day
    digits (or the raw string) across differing UTC offsets is exactly the
    #60 bug (gk writes `+00:00`, dev1 `+02:00` — the SAME instant renders
    with different hour digits in each). None when `ts_str` is missing,
    None, or unparsable — the caller (`_fleet_remote_row`) treats that as
    "can't verify freshness" and errors rather than trusting it.

    Thin wrapper — the canonical implementation is `burn.hour_bucket_of_ts`
    (#63: shared with `watchdog.fleet_burn_job`'s own local-row freshness
    check, so the convention can never drift between the two call sites)."""
    import burn as burn_mod
    return burn_mod.hour_bucket_of_ts(ts_str)


def _parse_fleet_cache_section(text):
    """Best-effort parse of the `_FLEET_CACHE_MARKER`-delimited usage-cache
    half of a `_fleet_remote_row` ssh reply — mirrors `hourly_snapshot()`'s
    own degrade-to-None convention for a missing/malformed local cache read
    (never blocks, never crashes, never guesses). `None` on an empty
    section (no marker in stdout at all — every pre-#286 fixture/caller),
    unparsable JSON, or a JSON value that parses but isn't an object (the
    SAME "valid JSON, wrong shape" guard `hourly_snapshot()` already
    applies to its own local cache read)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        cache = json.loads(text)
    except ValueError:
        return None
    if not isinstance(cache, dict):
        return None
    return cache


def _fleet_remote_row(remote, want_hour_bucket, timeout=15):
    """One remote host's latest hourly burn-snapshot row FOR THE SPECIFIC
    `want_hour_bucket` (an epoch-hour index — see `_hour_bucket_of_ts`), or
    `{"error": ...}` on ANY failure: ssh, timeout, empty file, bad JSON, OR
    a STALE/mismatched-hour row (#60). The remote's tail line existing does
    NOT mean it is fresh for the hour being collected — the remote may not
    have written this hour's row yet (job 16 now waits until HH:05 to give
    the remote's own job 13 time to write it), or the remote's clock/offset
    may differ from ours (`_hour_bucket_of_ts` always converts to UTC
    before comparing — never the raw string/local hour-of-day). A
    stale/mismatched row is returned as `{"error": ..., "stale": True}` so
    callers (`merge_fleet_row`/`render_fleet`) can render it distinctly
    from a hard collection failure (`—` vs `ERR`) — this IS the #60 fix:
    silently reusing an old row produced a false fleet trend/total (5/6
    hosts double-counting the same stale sample read as "-39.8%
    (lepšie)"). A single unreachable/stale box must never crash the fleet
    job or the rest of the watchdog sweep. Never raises.

    #286: the ssh reply's stdout carries a SECOND section after
    `_FLEET_CACHE_MARKER` — the remote's own usage cache. Parsed
    best-effort ONLY when the snapshot half is fresh (an error/stale row
    already contributes nothing to `group_fleet_by_account`, so there is
    nothing useful to attach weekly data to — and skipping it keeps the
    #60 stale/error contract exactly as strict as before, never softened
    by a cache section happening to be present). On a fresh row: backfills
    `account_email` ONLY when the snapshot row itself is missing it (a
    legacy pre-#269 row — the snapshot's own value always wins when
    present, since it is the more directly-attributable source), and adds
    `weekly_pct`/`resets_at` via `burn.shared_weekly_window()` — the SAME
    account-wide-window selector `fleet_burn_job`/`cmd_burn` already use,
    never a new one. Also carries the cache's OWN `ts` (its write time,
    unix epoch) through as `weekly_ts` — an adversarial review of this
    same #286 branch flagged that `group_fleet_by_account()`'s cross-host
    MAX-percent selection had no way to tell a fresh candidate from a
    stale one (a remote box whose watchdog stopped refreshing its cache
    could otherwise win over a fresher, correct sample from another box on
    the same account); `weekly_ts` is what lets it gate on that."""
    import subprocess
    cmd = _fleet_remote_cmd(remote)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    if result.returncode != 0:
        return {"error": (result.stderr or "").strip()[:200] or "ssh failed"}
    snap_part, _, cache_part = (result.stdout or "").partition(_FLEET_CACHE_MARKER)
    lines = snap_part.strip().splitlines()
    if not lines:
        return {"error": "no snapshot data yet"}
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return {"error": "invalid JSON from remote"}
    if not isinstance(row, dict):
        return {"error": "unexpected JSON shape from remote"}
    row_hour = _hour_bucket_of_ts(row.get("ts"))
    if row_hour != want_hour_bucket:
        return {"error": "no sample for hour %s (latest %s)" % (want_hour_bucket, row.get("ts")),
               "stale": True}
    cache = _parse_fleet_cache_section(cache_part)
    if cache:
        import burn as burn_mod
        row = dict(row)
        if not row.get("account_email"):
            row["account_email"] = cache.get("account_email") or ""
        wk = burn_mod.shared_weekly_window(cache)
        if wk:
            row["weekly_pct"], row["resets_at"] = wk
            row["weekly_ts"] = cache.get("ts")
    return row


def _watchdog_fleet_fetch(hosts=None, want_hour_bucket=None):
    """Real remote collector used by cmd_watchdog's job 16 wiring — one row
    per REMOTE_HOSTS entry, hour-matched against `want_hour_bucket` (#60).
    Defaults to the CURRENT UTC epoch-hour when not given — the plain
    top-level/manual-invocation case; `fleet_burn_job` always passes its own
    `now`-derived bucket explicitly (this repo's convention of threading
    `now` through every job for determinism/testability — see
    `_fleet_remote_row`). Never raises; a single bad or stale host degrades
    to `{"error": ...}` in its own slot rather than dropping the whole
    fleet."""
    hosts = hosts if hosts is not None else REMOTE_HOSTS
    if want_hour_bucket is None:
        import datetime
        want_hour_bucket = int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 3600)
    return {h["name"]: _fleet_remote_row(h, want_hour_bucket) for h in hosts}


def cmd_burn(args):
    """Token-spend report from local transcripts — the measurement behind the
    2026-07-25 cost-fix package (Opus-5-default MANAGED_MODEL, this
    diagnostic, the statusline context/cost segment): ~$13,600 across all 6
    managed boxes over 8 days, 76% Fable 5 running as MAIN (not advisor), 92%
    of that in input context. The local box is always included; `--host
    <name>` (or `--host all`) also collects a remote box over ssh by
    invoking ITS OWN deployed `airuleset.py burn --json` — never scp (the
    clean-tree hook would block it anyway).

    `--mark "<text>"` / `--compare` are the follow-up AUTOMATIC feedback
    loop (#37): `--mark` records that a change was made NOW (or at
    `--mark-ts <iso>` for backdating from a known event, e.g. a git commit
    timestamp) to `~/.claude/burn-history/changes.jsonl`; `--compare` reads
    that alongside the watchdog's hourly `snapshots.jsonl` (AND, when
    present, the fleet-wide `fleet.jsonl` — #55 point D) and prints, per
    change, the mean $/h, avg context and msgs/h in `--window` hours (default
    6) before vs after it — so the user never has to check anything himself,
    the report just tells him whether a change made things better or worse.

    `--fleet [--hours N]` (#55) prints the monitored-fleet hourly report:
    per-host + total $ for the last N hours (default 24), the trend (latest
    hour vs mean of the previous 3), and a sustainability verdict against the
    watchdog's weekly usage-cache budget. The fleet.jsonl feed is written by
    watchdog job 16 (`fleet_burn_job`), coordinator-only (dev1)."""
    import burn
    if getattr(args, "mark", None):
        ts = None
        mark_ts = getattr(args, "mark_ts", None)
        if mark_ts:
            import datetime
            ts = datetime.datetime.fromisoformat(mark_ts)
        path = burn.mark_change(args.mark, now=ts)
        print("Marked: %s -> %s" % (args.mark, path))
        return
    if getattr(args, "compare", False):
        window = getattr(args, "window", None) or 6
        changes = burn.load_changes()
        results = burn.compare_changes(burn.load_snapshots(), changes, window_hours=window)
        fleet_rows = burn.load_fleet()
        fleet_results = None
        if fleet_rows:
            fleet_results = burn.compare_changes(burn.fleet_compare_rows(fleet_rows),
                                                 changes, window_hours=window)
        print(burn.render_compare(results, window_hours=window, fleet_results=fleet_results))
        return
    if getattr(args, "fleet", False):
        hours = getattr(args, "hours", None) or 24
        print(burn.render_fleet(burn.load_fleet(), hours=hours, cache=burn.load_usage_cache()))
        return
    days = getattr(args, "days", None) or 7
    reports = [burn.local_report(days=days)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        if host_arg == "all":
            targets = REMOTE_HOSTS
        else:
            targets = [h for h in REMOTE_HOSTS if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in REMOTE_HOSTS)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting burn from {remote['name']}...", file=sys.stderr)
            rep = _burn_remote(remote, days)
            if rep:
                reports.append(rep)
    combined = burn.merge_reports(reports)
    if getattr(args, "json", False):
        print(json.dumps(combined, indent=1))
    else:
        print(burn.render_human(combined, days))


# --------------------------------------------------------------------------- #
# #130 — `airuleset.py delegation`: the standing MAIN vs SUBAGENT cost meter.
#
# The ruleset's central move is to push work out of the main agent because a
# main turn re-sends the whole conversation. That reasoning has never been
# checked against what the subagents themselves cost, and it could not be:
# `burn.scan()` is structurally blind to subagent transcripts (see the header
# comment on `burn.scan_split`). This is the instrument, not a gate — it
# reports, it never blocks, and it changes no threshold anywhere.
# --------------------------------------------------------------------------- #

def _delegation_remote_cmd(remote, hours):
    """Pure ssh-command builder — invokes the remote box's OWN deployed
    `airuleset.py delegation --json`, exactly as `_burn_remote_cmd` does for
    `burn`, sharing the same identity/sshpass prefix. READ-ONLY on the remote:
    it scans that box's transcripts and prints JSON, writes nothing."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py delegation "
        f"--json --hours {hours}"]


def _delegation_remote(remote, hours):
    """Collect one remote box's split report. Fail-safe like `_burn_remote`:
    any ssh error, non-zero exit or unparsable stdout WARNs and returns None,
    so one unreachable box never aborts the fleet report."""
    import subprocess
    cmd = _delegation_remote_cmd(remote, hours)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"  WARN: delegation collection failed for {remote['name']}: {e}",
              file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: delegation collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print("  WARN: delegation collection returned invalid JSON for "
              f"{remote['name']}", file=sys.stderr)
        return None


def _gh_closed_issues_json(repo):
    import subprocess
    r = subprocess.run(
        ["gh", "issue", "list", "-R", repo, "--state", "closed",
         "--limit", "500", "--json", "number,closedAt"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def _closed_ticket_count(repo, start, end, _runner=None):
    """Issues on `repo` closed inside [start, end], or None.

    None — never 0 — when `gh` is unavailable or errors: a fabricated
    zero-ticket denominator would render as "spend with no ticket", which is a
    real and serious finding, and it must never be manufactured by a missing
    tool."""
    import burn
    import datetime
    runner = _runner or _gh_closed_issues_json
    try:
        raw = runner(repo)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = burn._parse_ts(r.get("closedAt"))
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        if start <= t <= end:
            n += 1
    return n


def _attach_ticket_counts(merged, hours, _counter=None):
    """Join closed-ticket counts onto each project row that resolved a repo.

    Opt-in (`--tickets`) because it needs network + `gh` auth the base
    measurement must not depend on. A project whose repo did not resolve keeps
    `closed_tickets: None` and renders no per-ticket line, rather than
    borrowing someone else's denominator."""
    import datetime
    counter = _counter or _closed_ticket_count
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(hours=hours)
    cache = {}
    for row in (merged.get("by_project") or {}).values():
        repo = row.get("repo")
        if not repo:
            continue
        if repo not in cache:
            cache[repo] = counter(repo, start, end)
        row["closed_tickets"] = cache[repo]
        if cache[repo]:
            import burn
            total = row["main"]["units"] + row["sub"]["units"]
            row["units_per_ticket"] = burn.units_per_ticket(total, cache[repo])
    return merged


def cmd_delegation(args):
    """Per-box, per-project MAIN vs SUBAGENT token attribution over a window.

    Reports turns, the four token sums, a weighted (relative, never a price)
    cost unit, mean context per turn, and — with `--tickets` — cost per closed
    ticket, for MAIN and SUBAGENT separately. `--host <name>` / `--host all`
    also collects the remote fleet over ssh via each box's own deployed copy.
    """
    import burn
    hours = getattr(args, "hours", None) or 12
    root = getattr(args, "root", None)
    if getattr(args, "floor", False):
        # #131 — a DIFFERENT question from the standing meter's, so it gets its
        # own report rather than extra columns on the by-project table: this one
        # is per dispatch and local-only (a remote box's split is already
        # folded per project and cannot be decomposed back into dispatches).
        rep = burn.scan_dispatches(root or os.path.expanduser(
            "~/.claude/projects"), hours=hours)
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=1))
        else:
            print(burn.render_floor(rep, hours=hours))
        return
    reports = [burn.split_report(hours=hours, root=root)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        if host_arg == "all":
            targets = REMOTE_HOSTS
        else:
            targets = [h for h in REMOTE_HOSTS if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in REMOTE_HOSTS)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting delegation split from {remote['name']}...",
                  file=sys.stderr)
            rep = _delegation_remote(remote, hours)
            if rep:
                reports.append(rep)
    merged = burn.merge_splits(reports)
    if getattr(args, "tickets", False):
        _attach_ticket_counts(merged, hours)
    if getattr(args, "json", False):
        print(json.dumps(merged, indent=1))
    else:
        print(burn.render_split(merged, hours=hours))


# ---------------------------------------------------------------------------
# autopilot-lock — cross-session serial-per-repo dispatch lock (issue #8)
# ---------------------------------------------------------------------------


def _autopilot_lock_path(repo):
    """Repo-path-keyed lockfile under the system tempdir. Resolved (realpath)
    so relative paths, symlinks, and a trailing slash all hash to the SAME
    lock — a real cross-session lock must not fork on cosmetic path forms.

    `AIRULESET_AUTOPILOT_LOCK_DIR`, when set, overrides the lock DIRECTORY
    (same shape as `watchdog.draft_rescue_dir()`'s `AIRULESET_DRAFT_RESCUE_DIR`
    and `_is_gh_app_token_box()`'s `GH_APP_TOKEN_DIR` — #385). It exists
    because `tests/test_autopilot_lock.py` genuinely needs to exercise the
    REAL `autopilot-lock` CLI subprocess end-to-end (real `fcntl.flock`, real
    PID liveness via `os.kill`, a real steal race — none of which an
    in-process call could faithfully test), and every one of those subprocess
    runs is keyed on a FRESH `tempfile.mkdtemp()` repo path that is never
    reused and never cleaned up — leaving a permanent, un-owned lock (or
    `.mutex` sibling, or symlink, or directory-shaped artifact) in the REAL
    system `/tmp` on every single test run. Thousands of these accumulated in
    production over weeks (measured live: 8350 `.lock` + 6329 `.lock.mutex` +
    1009 `.lock-real-target` symlinks + 1027 directory-shaped locks on this
    box alone) before this override existed. Unset (real `/autopilot`
    dispatch, and every OTHER caller) is byte-for-byte unchanged."""
    import hashlib
    import tempfile as _tempfile
    real = str(Path(repo).resolve())
    h = hashlib.sha1(real.encode()).hexdigest()
    lock_dir = os.environ.get("AIRULESET_AUTOPILOT_LOCK_DIR") or _tempfile.gettempdir()
    return Path(lock_dir) / f"airuleset-autopilot-{h}.lock"


def _proc_parent_pid(pid):
    """Linux-only /proc read (both managed machines are Linux). Returns None
    off-Linux or on any read failure — callers fall back gracefully."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


def _proc_comm(pid):
    """Linux-only /proc read of a process's command name (`/proc/<pid>/comm`).
    Returns None off-Linux or on any read failure — callers fall back
    gracefully. Used by `_campaign_pid` to recognize the long-lived `claude`
    (or `node`) process regardless of how many ephemeral shell layers sit
    between it and this process."""
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return None


_CAMPAIGN_LONG_LIVED_COMMS = {"claude", "node"}
_CAMPAIGN_ANCESTRY_MAX_HOPS = 10


def _campaign_pid():
    """The PID that should stay alive for the WHOLE autopilot campaign (the
    span between an `acquire` call and the LATER, separate `release` call).

    Each Claude Code Bash tool call spawns a fresh ephemeral shell that dies
    the instant that one tool call returns — so os.getppid() alone (this
    process's immediate parent) is USELESS for staleness detection: it would
    already look "dead" moments after `acquire` prints success. The
    long-lived `claude` CLI process itself, which persists for the entire
    session, sits further up the ancestry chain.

    This WALKS the ancestry (by `comm` name, not a fixed hop count) until it
    finds a known long-lived process. A FIXED one-hop walk (the previous
    implementation) is correct only when there is EXACTLY one ephemeral
    shell layer between this process and `claude` — an EXTRA layer (e.g. a
    `bash -c '...'` wrapper invoking this command) makes a fixed-hop walk
    land on ANOTHER ephemeral shell instead of `claude`. That shell dies the
    instant its own tool call returns, so the recorded holder PID looks
    stale almost immediately, and a concurrent `/autopilot` session on the
    same repo can steal the "live" lock — reintroducing the exact #8
    collision this lock exists to prevent. Bounded by
    `_CAMPAIGN_ANCESTRY_MAX_HOPS` as a sanity cap (real ancestry chains are
    a handful of hops); if no long-lived process is ever found, the last
    pid reached is returned (never None/0) — same fail-safe shape as the
    old implementation's `grandparent or ppid`.
    """
    pid = os.getppid()
    seen = set()
    for _ in range(_CAMPAIGN_ANCESTRY_MAX_HOPS):
        if not pid or pid in seen:
            break
        seen.add(pid)
        if _proc_comm(pid) in _CAMPAIGN_LONG_LIVED_COMMS:
            return pid
        parent = _proc_parent_pid(pid)
        if not parent or parent == pid:
            break
        pid = parent
    return pid


def _pid_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else — still alive
    except Exception:
        return False


def _autopilot_lock_read(lock_path):
    try:
        return json.loads(lock_path.read_text())
    except Exception:
        return {}


def cmd_autopilot_lock(args):
    """Cross-session serial-per-repo dispatch lock for /autopilot (issue #8).

    The "serial per repo" rule (skills/autopilot/SKILL.md,
    two-branch-workflow.md) previously had only SESSION-LOCAL enforcement (a
    supervisor checks its own agent strip) — a SEPARATE `/autopilot` session
    on the same repo has no visibility into that and can dispatch a
    colliding worker onto the same `dev` branch (camera-box #495, and the
    #499/#500-vs-#505 collision).

    `acquire` FAILS (exit 1) when a LIVE holder exists; a DEAD holder's lock
    is stolen (logged) and acquisition proceeds. `release` only removes a
    lock it actually owns (matched by pid) — it never touches someone
    else's lock, and is a no-op success when nothing is locked. `status` is
    a read-only report. The acquire critical section (check-then-write) is
    guarded by a brief `fcntl.flock` on a sibling `.mutex` file so two
    concurrent `acquire` calls on the SAME repo can't both win a
    stale-steal race — the lock's real persistence across the
    acquire/release CLI-invocation gap comes from the recorded holder PID
    staying alive (see `_campaign_pid`), not from the OS-held flock itself
    (which necessarily releases the instant this short-lived CLI process
    exits).
    """
    import fcntl
    from datetime import datetime, timezone

    action = args.action
    repo = args.repo or "."
    lock_path = _autopilot_lock_path(repo)
    holder_pid = args.pid if getattr(args, "pid", None) is not None else _campaign_pid()

    if action == "status":
        if not lock_path.exists():
            print(f"UNLOCKED {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        alive = _pid_alive(holder.get("pid"))
        state = "LOCKED" if alive else "LOCKED (stale — holder pid dead)"
        print(f"{state} pid={holder.get('pid')} session={holder.get('session', '')} "
              f"since={holder.get('acquired_at', '')} repo={holder.get('repo', '')}")
        sys.exit(0)

    if action == "release":
        if not lock_path.exists():
            print(f"already unlocked: {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        if holder.get("pid") == holder_pid:
            lock_path.unlink(missing_ok=True)
            print(f"RELEASED {lock_path}")
            sys.exit(0)
        print(f"REFUSING to release — held by a DIFFERENT holder "
              f"(pid={holder.get('pid')}, session={holder.get('session', '')}); "
              f"not releasing a lock this caller does not own.", file=sys.stderr)
        sys.exit(1)

    if action == "acquire":
        payload = {
            "pid": holder_pid,
            "session": args.session or "",
            "repo": str(Path(repo).resolve()),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        mutex_path = str(lock_path) + ".mutex"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        mfd = os.open(mutex_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(mfd, fcntl.LOCK_EX)
            if lock_path.is_dir():
                # A stale directory-shaped artifact (an older mkdir-style
                # lock implementation, or a manual mkdir) — `write_text`
                # below cannot write through a directory, so this must be
                # resolved BEFORE the exists()/read()/steal flow, never
                # discovered as an unhandled IsADirectoryError crash (#248,
                # hit live on dev2). An EMPTY directory is self-healed
                # (removed, acquisition proceeds exactly as if the path
                # never existed); a NON-EMPTY one is refused with a clear
                # message — deleting unknown directory contents is not this
                # command's call to make.
                try:
                    is_empty = not any(lock_path.iterdir())
                except OSError:
                    is_empty = False
                removed = False
                if is_empty:
                    try:
                        lock_path.rmdir()
                        removed = True
                    except OSError:
                        # A symlink to an empty directory reports is_dir()
                        # True and iterdir() succeeds, yet rmdir() itself
                        # raises NotADirectoryError (verified empirically) —
                        # a TOCTOU race (something repopulated the directory
                        # between the check above and here) raises
                        # "Directory not empty" the same way. Either way,
                        # fall through to the same clean refusal below —
                        # never an unhandled crash.
                        removed = False
                if not removed:
                    print(f"ERROR: lock path {lock_path} exists as a "
                          f"directory that could not be safely removed "
                          f"(non-empty, a symlink, or a filesystem race) — "
                          f"refusing to acquire. Inspect and remove it "
                          f"manually if safe: rm -rf {lock_path}",
                          file=sys.stderr)
                    sys.exit(1)
            elif lock_path.exists():
                holder = _autopilot_lock_read(lock_path)
                if _pid_alive(holder.get("pid")):
                    print(f"BLOCKED: {payload['repo']} already has an active "
                          f"autopilot worker (held by pid={holder.get('pid')}, "
                          f"session={holder.get('session', '')}, "
                          f"since={holder.get('acquired_at', '')}). Serial-per-repo "
                          f"dispatch — wait for it to finish (`autopilot-lock status "
                          f"--repo {repo}`), do NOT dispatch a second worker.",
                          file=sys.stderr)
                    sys.exit(1)
                # Holder's pid is dead — steal it, log the steal.
                steal_log = Path.home() / "devel" / "airuleset" / "audits" / "autopilot-lock-steals.log"
                steal_log.parent.mkdir(parents=True, exist_ok=True)
                with open(steal_log, "a") as f:
                    f.write(f"{datetime.now(timezone.utc).isoformat()}  "
                            f"repo={payload['repo']}  stole from dead "
                            f"pid={holder.get('pid')} session={holder.get('session', '')}\n")
            lock_path.write_text(json.dumps(payload))
            print(f"ACQUIRED {lock_path} pid={holder_pid}")
            sys.exit(0)
        finally:
            fcntl.flock(mfd, fcntl.LOCK_UN)
            os.close(mfd)


def watchdog_disable_marker():
    """`~/.claude/api-watchdog.disabled` — the opt-out that makes a deliberate
    `systemctl --user stop api-watchdog.timer` SURVIVE a deploy (#132).

    Resolved at CALL time, never frozen at import (same reasoning as
    `watchdog.compact_requests_path()`).

    Why this exists: on 2026-07-28 the watchdog typed `/exit` into a live
    session, the timer was stopped fleet-wide as the mitigation — and was found
    running again on all 6 boxes the next morning, because `install` ends with
    an unconditional `enable --now` and every `airuleset.py push` runs
    `install`. A mitigation a routine deploy silently undoes is not a
    mitigation. Touch this file to keep the timer off across pushes; delete it
    to hand control back to `install`."""
    return Path.home() / ".claude" / "api-watchdog.disabled"


def setup_watchdog_service():
    """Install + start the api-watchdog systemd --user timer on THIS machine
    (every host — autopilot runs on dev1 and dev2). Mirrors the file-drop setup:
    write the .service + .timer units, daemon-reload, enable --now the timer —
    unless `watchdog_disable_marker()` exists, in which case the units are still
    refreshed but the timer is left exactly as the operator set it (#132)."""
    import subprocess
    print("  Installing api-watchdog systemd --user timer")
    for tmpl in (WATCHDOG_SERVICE_TEMPLATE, WATCHDOG_TIMER_TEMPLATE):
        if not tmpl.exists():
            print(f"  ERROR: watchdog unit template missing: {tmpl}", file=sys.stderr)
            return False
    WATCHDOG_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_SERVICE_DEST.write_text(
        WATCHDOG_SERVICE_TEMPLATE.read_text().replace("{{REPO_DIR}}", str(REPO_DIR)))
    WATCHDOG_TIMER_DEST.write_text(WATCHDOG_TIMER_TEMPLATE.read_text())
    print(f"  Wrote unit: {WATCHDOG_TIMER_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "api-watchdog.timer")
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    if watchdog_disable_marker().exists():
        # ENFORCE the stop, don't merely decline to start it. A timer that is
        # stopped but still ENABLED comes back at the next boot or linger
        # restart, so "skip enable --now" alone would let the mitigation expire
        # on its own (#132). Both calls are idempotent, so a box that is
        # already stopped+disabled just no-ops.
        _run_systemctl(["stop", "api-watchdog.timer"])
        _run_systemctl(["disable", "api-watchdog.timer"])
        print(f"  api-watchdog timer STOPPED + DISABLED — disable marker "
              f"present ({watchdog_disable_marker()}).\n"
              f"  Units refreshed. To re-arm: delete the marker and run "
              f"`systemctl --user enable --now api-watchdog.timer`.")
        return True
    rc, _o, err = _run_systemctl(["enable", "--now", "api-watchdog.timer"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    print("  api-watchdog timer active (polls every 60s).")
    return True


def maybe_setup_watchdog():
    setup_watchdog_service()


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
                          help="Ping IF --text is a real Claude Code API error "
                               "(used by the notify-api-error.sh Stop hook)")
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
             "boundary (#39 krok 1c) — consumed by watchdog job 14")
    p_creq.add_argument("--record", action="store_true",
                        help="Record the request (called by the Stop hook)")
    p_creq.add_argument("--session", default="", help="Session id (transcript stem)")
    p_creq.add_argument("--cwd", default="", help="Session cwd")
    p_creq.add_argument("--msg-hash", dest="msg_hash", default="",
                        help="Fingerprint (e.g. sha256) of the triggering "
                             "last_assistant_message (#71 delivered-dedup — "
                             "a repeat with the SAME hash after a delivered "
                             "compact is a no-op)")
    p_creq.add_argument("--origin", default="",
                        help="What PROVED this is a ticket boundary (#121). "
                             "'subagent-stop' = an autopilot-worker concluded "
                             "with zero other live tasks in the session's task "
                             "registry; for such a request a `⏳` last line is "
                             "not evidence of anything and never holds the "
                             "delivery (a `❓` still does). Empty = the Stop-hook "
                             "origin, whose gate is unchanged.")
    p_creq.add_argument("--self", action="store_true",
                        help="#225 -- explicit self-callback: resolve THIS "
                             "session's own pane via $TMUX_PANE and attempt "
                             "synchronous /compact delivery under a proven "
                             "boundary origin, holding (bounded, default "
                             "60s) if the first attempt can't land yet. "
                             "Ignores --record/--session/--cwd/--origin -- "
                             "everything is resolved from the calling pane "
                             "itself. Call this as your OWN last tool call "
                             "right after finishing a ticket, before "
                             "dispatching anything else.")
    p_creq.add_argument("--hold", type=float, default=None,
                        help="--self only: seconds to keep retrying before "
                             "giving up and leaving the request for job "
                             "14's later sweep (default "
                             "AIRULESET_COMPACT_SELF_HOLD_S or 60)")

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
        "fable-gate", help="Budget gate for automatic Fable escalation — exit 0 "
                           "(OPEN, dispatch fable) / 1 (CLOSED, dispatch opus)")
    p_gate.add_argument("--threshold", type=int, default=None,
                        help="Gate percent (default 80 / AIRULESET_FABLE_GATE_PCT)")

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

    p_sec = sub.add_parser(
        "secret",
        help="Ask the user for a CREDENTIAL through a one-shot URL — never in "
             "chat (a value typed into chat is in the transcript forever)")
    p_sec.add_argument("action", choices=list(SECRET_ACTIONS),
                       help="request (stand up the URL) | status | list | "
                            "exec NAME -- CMD (hand the value to a child; a "
                            "name LOCKED to a template ignores CMD and runs "
                            "its own command instead, #154) | "
                            "forget NAME | purge (drop everything past its TTL)")
    p_sec.add_argument("name", nargs="?", default=None,
                       help="Secret name: letters/digits/underscore, also used "
                            "as the env var name for `exec`")
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
    p_sec.add_argument("--replace", action="store_true",
                       help="request: cancel an existing pending request for "
                            "this name (stopping its endpoint) and issue a new URL")
    p_sec.add_argument("--stdin", action="store_true",
                       help="exec: feed the value on the child's stdin instead "
                            "of through the environment")
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


# Command dispatch table (module-level so tests can assert registration).
SUBCOMMANDS = {
    "install": cmd_install,
    "diff": cmd_diff,
    "validate": cmd_validate,
    "status": cmd_status,
    "push": cmd_push,
    "purge-targets": cmd_purge_targets,
    "sweep-worktrees": cmd_sweep_worktrees,
    "sweep-cli-versions": cmd_sweep_cli_versions,
    "sweep-claude-scratch": cmd_sweep_claude_scratch,
    "share": cmd_share,
    "filedrop": cmd_filedrop,
    "notify": cmd_notify,
    "watchdog": cmd_watchdog,
    "compact-request": cmd_compact_request,
    "fable-gate": cmd_fable_gate,
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
}
# Backwards-compatible alias used by main() before SUBCOMMANDS existed.
commands = SUBCOMMANDS


if __name__ == "__main__":
    main()
