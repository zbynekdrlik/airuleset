"""airuleset #433 cluster L-C — caveman plugin wiring + managed baseline plugin
provisioning (extracted from airuleset.py).

Two cohesive install/deploy concerns kept together (design decision 3): caveman
plugin wiring (statusline shim + settings reconcile + registry-truth install +
mode seed) and managed baseline plugin provisioning (reconcile enabled/disabled
tiers + marketplace-registered install + Playwright browser-cache provisioning).
The shared `_plugin_registry_keys` (claude's own installed_plugins.json reader)
lives inside, used by BOTH `_caveman_plugin_built` and `_managed_plugin_built`.

Self-contained stdlib leaf: never a module-level `import airuleset` (that would
re-execute the CLI as __main__). Resident airuleset state + L-A deployer-glue
symbols (render_caveman_shim / ensure_marketplace_registered / _marketplace_
names_for / MARKETPLACE_SOURCES / CAVEMAN_SHIM_DEST / CAVEMAN_MODE_FILE /
SETTINGS_JSON / CLAUDE_DIR / read_file_safe / reconcile_caveman_settings) are
reached LAZILY via a per-body `import airuleset`
+ `airuleset.X` — robust whether those names are still resident (pre-L-A-merge)
or facade re-exports (post-L-A-merge). `_claude_cli_env` is imported DIRECTLY
from the shipped cli_binary_installers leaf (keeps `env=_claude_cli_env()` a
byte-verbatim source-text the test suite asserts on). airuleset.py re-exports
every name here via one facade `from cli_caveman_plugins import (…)`.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from cli_binary_installers import _claude_cli_env


CAVEMAN_PLUGIN_KEY = "caveman@caveman"
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
#
# #542 (2026-08-18): #415's default-OFF is REVERSED — Playwright is a force-
# enabled baseline plugin again (restoring the #158 "installed AND ENABLED
# everywhere" decision). #415 moved it to an OPTIONAL tier (force-DISABLED in
# user scope, opt-in per project) to avoid a resident ~144MB headless Chrome
# fleet-wide — but it left that per-project opt-in to CHANCE (no provisioning
# sweep), so fresh sessions plainly reported "nemám playwright" and skipped
# the UNTOUCHABLE browser verification (autonomous-verification.md's "YOU have
# eyes", e2e-real-user-testing.md, post-deploy-verification). The resident-
# Chrome premise was empirically wrong: measured live on dev1, SIX running
# `@playwright/mcp` node MCP servers coexist with only ONE Chrome tree (parked
# on about:blank) — if Chrome launched eagerly at MCP-server start there would
# be six. Chrome launches LAZILY, only on the first browser tool call and only
# in the session that makes it. So force-enabling everywhere costs a browser-
# free project only the cheap always-on node MCP server (~5-20MB RSS), never a
# resident Chrome; a project with browser duties gets Chrome on demand with
# zero user intervention. Verification availability is the invariant; the
# memory optimization adapts to IT, not the reverse (owner standard: rigor is
# untouchable). The #415 `OPTIONAL_PLUGINS` tier is removed — playwright was
# its only member and a browser-free project no longer opts out of anything; a
# future genuinely-optional plugin can reintroduce the tier. reconcile_managed
# _plugins() force-writes playwright True, which FLIPS the stale user-scope
# `false` every #415-pushed box carries back to true on the next push, so the
# restoration takes effect fleet-wide (symmetric to how #415 flipped
# true->false). This also dissolves the whole per-project opt-in list (#452's
# writes into 11 repos) and the incomplete #453 "which more projects need it"
# follow-up — every project now has it.
MANAGED_PLUGINS = ("superpowers@claude-plugins-official",
                    "playwright@claude-plugins-official")
# Plugins explicitly DISABLED by managed policy (#39 item 3, 2026-07-25
# /doctor findings): rust-analyzer-lsp + claude-md-management had 0 lifetime
# uses on dev2 and `/doctor` disabled them directly in settings.json
# (backup: settings.json.bak-doctor). The plugin reconcile below force-writes
# every key in this list to False, so these disables survive a normal push
# regardless of what a `claude plugin install`/`/doctor`/manual edit left
# behind — this list makes the intent EXPLICIT and durable (and applies it on
# every box, not just dev2) so a future change can never silently resurrect
# them. (This comment used to say the reconcile "only ever ENABLES
# MANAGED_PLUGINS and otherwise merges the existing dict untouched" — false
# since this list existed, #39.)
MANAGED_DISABLED_PLUGINS = (
    "rust-analyzer-lsp@claude-plugins-official",
    "claude-md-management@claude-plugins-official",
)

PLAYWRIGHT_PLUGIN_KEY = "playwright@claude-plugins-official"
PLAYWRIGHT_BROWSER_CACHE = Path.home() / ".cache" / "ms-playwright"


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
    import airuleset
    print("  Wiring caveman plugin (managed)")
    ok = True

    # 1. stable shim — survives `claude plugin update` cache-hash churn.
    try:
        airuleset.CAVEMAN_SHIM_DEST.write_text(airuleset.render_caveman_shim())
        os.chmod(str(airuleset.CAVEMAN_SHIM_DEST), 0o755)
    except OSError as e:
        print(f"    could not write caveman shim ({e})", file=sys.stderr)

    # 2. reconcile settings.json FIRST.
    raw = airuleset.read_file_safe(airuleset.SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    settings.json invalid JSON — skipped caveman reconcile", file=sys.stderr)
        settings = None
        ok = False
    if settings is not None:
        new_str = json.dumps(airuleset.reconcile_caveman_settings(settings), indent=2) + "\n"
        if new_str.strip() != raw.strip():
            if airuleset.SETTINGS_JSON.exists():
                shutil.copy2(airuleset.SETTINGS_JSON, airuleset.SETTINGS_JSON.with_suffix(".json.bak"))
            airuleset.SETTINGS_JSON.write_text(new_str)
            print("    settings.json: enabled + statusLine -> stable shim")
        else:
            print("    settings.json: already correct")

    # 3. register the marketplace THEN install if the plugin's registry
    #    entry is missing (#279 — never a cache-file glob).
    if not _caveman_plugin_built():
        market = CAVEMAN_PLUGIN_KEY.split("@", 1)[1]
        if not airuleset.ensure_marketplace_registered(market):
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
        existing = airuleset.CAVEMAN_MODE_FILE.read_text() if airuleset.CAVEMAN_MODE_FILE.exists() else None
    except OSError as e:
        print(f"    could not read caveman mode ({e})", file=sys.stderr)
        existing = None
    mode = caveman_mode_or_default(existing)
    if existing is None or existing.strip() != mode:
        try:
            airuleset.CAVEMAN_MODE_FILE.write_text(mode)
            print(f"    mode: {mode}")
        except OSError as e:
            print(f"    could not write caveman mode ({e})", file=sys.stderr)

    return ok

def maybe_setup_caveman() -> bool:
    """Wire the caveman plugin on this machine (every host)."""
    return setup_caveman()

def reconcile_managed_plugins(settings: dict) -> dict:
    """Pure: return a new settings dict with every managed baseline plugin
    enabled, every MANAGED_DISABLED_PLUGINS key forced OFF in user scope
    (#39 item 3), and every marketplace those plugins live in REGISTERED in
    extraKnownMarketplaces (belt-and-suspenders alongside `claude plugin
    marketplace add` in setup_managed_plugins() — a fresh account has no
    marketplace registered at all otherwise; see MARKETPLACE_SOURCES). Every
    other key preserved untouched; idempotent.

    #542: playwright is force-ENABLED here (it is back in MANAGED_PLUGINS),
    which actively FLIPS the stale user-scope `false` every #415-pushed box
    carries back to true on the next push — making the availability
    restoration take effect fleet-wide, not only on a fresh box (symmetric to
    how #415's force-disable flipped the stale true off)."""
    import airuleset
    result = dict(settings)
    enabled = dict(result.get("enabledPlugins", {}))
    for key in MANAGED_PLUGINS:
        enabled[key] = True
    for key in MANAGED_DISABLED_PLUGINS:
        enabled[key] = False
    result["enabledPlugins"] = enabled
    markets = dict(result.get("extraKnownMarketplaces", {}))
    for name in airuleset._marketplace_names_for(MANAGED_PLUGINS):
        repo = airuleset.MARKETPLACE_SOURCES.get(name)
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
    import airuleset
    path = registry_path or (airuleset.CLAUDE_DIR / "plugins" / "installed_plugins.json")
    data = _load_plugin_registry(path)
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return set(plugins.keys()) if isinstance(plugins, dict) else set()

def _load_plugin_registry(path: Path) -> dict:
    """Read and parse installed_plugins.json, returning the parsed dict.

    Returns ``{}`` on missing/unreadable/unparsable file -- never raises.
    Shared by ``_plugin_registry_keys()`` and
    ``_heal_stale_plugin_registry()`` to avoid duplicating the
    read/parse ladder (#845 review finding 4)."""
    import airuleset
    try:
        raw = airuleset.read_file_safe(path)
    except (OSError, UnicodeDecodeError):
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

def _heal_stale_plugin_registry(registry_path: Path = None) -> set:
    """Remove registry entries whose installPath no longer exists on disk.

    After an account rename (e.g. montalu → montalu1, #537/#845) every
    plugin installPath still points at the old home (/home/montalu/…) — a
    nonexistent directory.  ``_managed_plugin_built()`` checks only key
    PRESENCE, so ``setup_managed_plugins()`` reports "already built" and
    never reinstalls.  This function heals those stale entries: it reads
    the full registry, checks each entry's ``installPath``, removes any
    entry whose path does not exist on disk, writes back (only when
    something changed), and logs one line per healed key (machine
    channel — never an owner ping).  After healing, the existing
    ``_managed_plugin_built()`` naturally returns False for the healed
    keys, and ``claude plugin install`` reinstalls them fresh — the same
    flow a first-time box takes.

    Applies to EVERY plugin in the registry, not only the managed
    baseline set, so any future account rename or home-dir migration is
    self-healing.  Idempotent; read-only when nothing is stale.

    Returns the set of healed (removed) plugin keys."""
    import airuleset
    import tempfile
    path = registry_path or (airuleset.CLAUDE_DIR / "plugins" / "installed_plugins.json")
    data = _load_plugin_registry(path)
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return set()

    healed = set()
    for key in list(plugins.keys()):
        entries = plugins[key]
        if not isinstance(entries, list):
            continue
        cleaned = []
        stale_found = False
        for entry in entries:
            if not isinstance(entry, dict):
                cleaned.append(entry)
                continue
            install_path = entry.get("installPath")
            if install_path and not Path(install_path).exists():
                stale_found = True
                print(f"    healed stale registry entry: {key} "
                      f"(installPath {install_path!r} does not exist)")
            else:
                cleaned.append(entry)
        if stale_found:
            healed.add(key)
            if cleaned:
                plugins[key] = cleaned
            else:
                del plugins[key]
    if healed:
        new_content = json.dumps(data, indent=2) + "\n"
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            os.write(fd, new_content.encode())
            os.close(fd)
            os.replace(tmp, path)
            tmp = None  # replaced successfully — no orphan
        except OSError as e:
            print(f"    warning: could not write healed registry {path} "
                  f"({e}) -- install loop continues", file=sys.stderr)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:  # airuleset:script-ok best-effort orphan cleanup
                    pass
            return set()
    return healed


def _heal_stale_marketplace_registry(registry_path: Path = None) -> set:
    """Remove marketplace entries whose installLocation no longer exists.

    After an account rename (#537/#845) the SECOND registry file
    ``~/.claude/plugins/known_marketplaces.json`` keeps stale
    ``installLocation`` paths (e.g. ``/home/montalu/.claude/plugins/
    marketplaces/claude-plugins-official``).  ``ensure_marketplace_registered()``
    runs an idempotent ``claude plugin marketplace add`` that does NOT
    rewrite a stale ``installLocation`` — it sees the entry as "already
    registered" and exits 0, but the subsequent ``claude plugin install``
    fails "marketplace directory does not exist".

    This function heals those stale entries: it reads the full marketplace
    registry, checks each entry's ``installLocation``, removes any entry
    whose path does not exist on disk, writes back (only when something
    changed), and prints a ``heal:`` line per dropped entry.  After healing,
    ``ensure_marketplace_registered()`` re-adds the marketplace under the
    real ``$HOME``, and plugin install succeeds.

    Fail-safe: unreadable / malformed JSON → leave untouched + print a
    warning, never crash install.  Idempotent; read-only when nothing is
    stale.

    Returns the set of healed (dropped) marketplace names."""
    import airuleset
    import tempfile
    path = registry_path or (airuleset.CLAUDE_DIR / "plugins"
                             / "known_marketplaces.json")
    if not path.is_file():
        return set()
    try:
        raw = airuleset.read_file_safe(path)
    except (OSError, UnicodeDecodeError):
        return set()
    if not raw.strip():
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    warning: {path} is malformed JSON — left untouched",
              file=sys.stderr)
        return set()
    if not isinstance(data, dict):
        return set()

    healed = set()
    for name in list(data.keys()):
        entry = data[name]
        if not isinstance(entry, dict):
            continue
        install_loc = entry.get("installLocation")
        if install_loc and not Path(install_loc).exists():
            healed.add(name)
            del data[name]
            print(f"    healed stale marketplace entry: {name} "
                  f"(installLocation {install_loc!r} does not exist)")
    if healed:
        new_content = json.dumps(data, indent=2) + "\n"
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            os.write(fd, new_content.encode())
            os.close(fd)
            os.replace(tmp, path)
            tmp = None  # replaced successfully — no orphan
        except OSError as e:
            print(f"    warning: could not write healed marketplace "
                  f"registry {path} ({e}) -- install loop continues",
                  file=sys.stderr)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:  # airuleset:script-ok best-effort orphan cleanup
                    pass
            return set()
    return healed


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
    does not include Playwright, or the cache is already populated. #542:
    keyed on MANAGED_PLUGINS — Playwright is a force-enabled baseline plugin,
    so its browser cache must be provisioned on every box (a plugin that is
    enabled but has no browser binaries fails every browser call with
    "Executable doesn't exist")."""
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

def _reconcile_settings_file():
    """Read SETTINGS_JSON, apply reconcile_managed_plugins(), write back only
    when it changed (backing up first). Returns "invalid" (unparseable JSON,
    nothing written), "wrote" (reconciled + written), or "unchanged" (already
    correct). Used by setup_managed_plugins()'s reconcile pass (#542 removed
    the second re-assert-disable pass along with the OPTIONAL tier it guarded)."""
    import airuleset
    raw = airuleset.read_file_safe(airuleset.SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return "invalid"
    new_str = json.dumps(reconcile_managed_plugins(settings), indent=2) + "\n"
    if new_str.strip() != raw.strip():
        if airuleset.SETTINGS_JSON.exists():
            shutil.copy2(airuleset.SETTINGS_JSON, airuleset.SETTINGS_JSON.with_suffix(".json.bak"))
        airuleset.SETTINGS_JSON.write_text(new_str)
        return "wrote"
    return "unchanged"

def setup_managed_plugins() -> bool:
    """Ensure the managed baseline plugins are installed and reconciled (idempotent).

    1. reconcile settings.json (MANAGED_PLUGINS keys true, MANAGED_DISABLED_
       PLUGINS keys FALSE, marketplaces registered) — runs FIRST, before any
       install attempt below (issue: push: plugin installs fail on fresh
       stream accounts, 2026-08-06 — reconciling AFTER install, as this used
       to, means the settings write lands too late to help the very install
       call it's meant to unblock),
    2. for every MANAGED_PLUGINS plugin whose REGISTRY ENTRY is missing
       (claude's own installed_plugins.json — see _managed_plugin_built()'s
       docstring; never a cache-file glob, #276): register its marketplace
       (idempotent `claude plugin marketplace add` — see
       ensure_marketplace_registered()'s docstring) THEN install it
       (best-effort, time-boxed). Installing without a registered
       marketplace only reproduces the "not found in marketplace" failure,
       so a failed registration skips that plugin's install attempt
       entirely rather than trying anyway.
    (#542: playwright is a force-ENABLED baseline plugin again, so a `claude
    plugin install` that re-enables it is the DESIRED end state — the pre-#542
    second reconcile that flipped an install-re-enabled OPTIONAL key back OFF
    is gone with the OPTIONAL tier it existed to protect.)
    Returns True iff nothing REQUIRED failed (marketplace registration and
    install, for every plugin whose registry entry was missing) — a still-failing
    plugin install after correct marketplace registration is a genuine
    failure the caller (cmd_install) turns into a non-zero exit, per
    script-failure-policy. The other best-effort step here
    (ensure_playwright_browsers) is unaffected — it stays exactly as
    non-fatal as it already was."""
    import subprocess
    import airuleset
    print("  Wiring managed baseline plugins")
    ok = True

    status = _reconcile_settings_file()
    if status == "invalid":
        print("    settings.json invalid JSON — skipped plugin reconcile",
              file=sys.stderr)
        ok = False
    elif status == "wrote":
        print(f"    settings.json: enabled {', '.join(MANAGED_PLUGINS)}")
    else:
        print("    settings.json: already correct")

    _heal_stale_marketplace_registry()
    _heal_stale_plugin_registry()

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
            market_ok[market] = airuleset.ensure_marketplace_registered(market)
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
