"""cli_deployer_glue.py — the deployer's statusline-shim rendering, marketplace
registration glue, and per-box skill-subset selector (#433 cluster L-A).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as the H/I/J/K/L1/
L2 CLI leaves, the A-F watchdog leaves, and the L cli_tmux_provisioning.py /
cli_binary_installers.py leaves). airuleset.py keeps a single
`from cli_deployer_glue import (...)` re-export at the old definition site, so
`setup_caveman`'s `render_caveman_shim()` write, `setup_managed_plugins`'
`_marketplace_names_for` / `MARKETPLACE_SOURCES` / `ensure_marketplace_registered`
/ `CAVEMAN_MARKETPLACE_REPO` calls, `install`/`diff`'s `skill_names_for_user()`
per-box subset, and every test's `airuleset.render_caveman_shim(...)` /
`airuleset.CAVEMAN_SHIM_CONTENT` / `airuleset.ensure_marketplace_registered(...)`
/ `airuleset.skill_names_for_user(...)` reference all keep working unchanged.

This leaf carries the three SMALL, self-contained deployer-glue concerns that
formed the cleanest contiguous L region: (1) the per-box skill SUBSET selector
`skill_names_for_user`, (2) marketplace-source registration
(`ensure_marketplace_registered` + `MARKETPLACE_SOURCES` and their sources), and
(3) the caveman statusline-shim RENDERING (`CAVEMAN_SHIM_CONTENT` +
`render_caveman_shim`). The caveman/plugin-management WIRING that CONSUMES these
(`setup_caveman`, `reconcile_managed_plugins`, `MANAGED_PLUGINS`/`OPTIONAL_PLUGINS`,
the caveman-mode toggles, `CAVEMAN_SHIM_DEST` + `CAVEMAN_STATUSLINE_COMMAND`)
stays resident in airuleset.py — it is #433 step L-C, and `CAVEMAN_SHIM_DEST` /
`CAVEMAN_STATUSLINE_COMMAND` in particular derive from the resident `CLAUDE_DIR`
path at module load, so keeping them resident is behaviour-identical (a leaf
copy would duplicate `CLAUDE_DIR` and detach the `patch.object(airuleset,
"CLAUDE_DIR")` the tests rely on).

Stdlib only at module level (`sys`, `Path`); NO top-level `import airuleset`
(that would crash CLI mode, where airuleset runs as `__main__`). The three
moved functions reference RESIDENT shared state lazily, via a deferred
`import airuleset` inside their own bodies, referenced as `airuleset.X` — the
proven cli_quals.py idiom: `skill_names_for_user` reaches the resident skill
registries (`SKILL_NAMES` / `SKILLS_EXTRA_BY_USER` / `MAINTAINER_USERS` /
`SKILLS_MAINTAINER_ONLY` / `SKILLS_FULL_AUTHORITY_ONLY` / `AUTHORITY_BY_USER`,
all kept resident with their scoping comment); `ensure_marketplace_registered`
reaches `airuleset._claude_cli_env` (itself living in cli_binary_installers.py,
re-exported through airuleset's facade); `render_caveman_shim` reaches
`airuleset.MANAGED_MODEL`. `REPO_DIR` below is this file's own copy of the
canonical expression — identical value, this file sits in the same directory as
airuleset.py — so `render_caveman_shim`'s `{{REPO_DIR}}` substitution needs no
`import airuleset` for that half.
"""

import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def skill_names_for_user(user=None):
    """The skill set THIS box's user should have installed (the resident skill
    registries + their scoping comment stay in airuleset.py)."""
    import getpass
    import airuleset
    user = user or getpass.getuser()
    extra = airuleset.SKILLS_EXTRA_BY_USER.get(user, set())
    names = list(airuleset.SKILL_NAMES)
    if user not in airuleset.MAINTAINER_USERS:
        names = [n for n in names if n not in airuleset.SKILLS_MAINTAINER_ONLY or n in extra]
    if airuleset.AUTHORITY_BY_USER.get(user, "full") != "full":
        names = [n for n in names if n not in airuleset.SKILLS_FULL_AUTHORITY_ONLY or n in extra]
    return names


CAVEMAN_MARKETPLACE_REPO = "JuliusBrussee/caveman"

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
    import airuleset
    source = MARKETPLACE_SOURCES.get(name)
    if source is None:
        return True
    try:
        r = subprocess.run(
            ["claude", "plugin", "marketplace", "add", source],
            capture_output=True, text=True, timeout=150,
            env=airuleset._claude_cli_env())
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
    seg = statusbar.tickets_segment(cwd)   # I/U/W/gk/skip; #512 folds the old
    if seg:                                # standalone `Q` ❓ badge into `U N`
        segs.append(seg)
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


def render_caveman_shim():
    """The shim content with per-machine placeholders substituted ({{REPO_DIR}} →
    this checkout, so the embedded python can import statusbar for the 🎫 ticket
    segment; {{MANAGED_MODEL}} -> this box's managed model default, so the
    model-identity segment (#133) never pays a per-render `import airuleset`).
    The install write site MUST use this, never the raw constant."""
    import airuleset
    return (CAVEMAN_SHIM_CONTENT
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{MANAGED_MODEL}}", airuleset.MANAGED_MODEL))
