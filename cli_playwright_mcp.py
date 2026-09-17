"""airuleset #1058 -- managed Playwright MCP provisioning, split out of
cli_caveman_plugins.py (area-review rework of #1048, dispatch item 6).

The single leaf that owns WHERE the managed chromium lives + how it is
installed, healed, cleaned, and written into ~/.claude.json: the pinned-trio
constants, the browsers-path resolver, the idempotent installer (+ the #1058
per-user old-build cleanup), the system-lib heal, the MCP-server reconcile /
unreconcile, and the end-to-end provision / unprovision. Consumed by cmd_install
(airuleset), the bashrc applier's marker, and cli_remote's push post-check.

Self-contained stdlib leaf: never a module-level `import airuleset` (that would
re-execute the CLI as __main__). `_claude_cli_env` is imported DIRECTLY from the
shipped cli_binary_installers leaf (keeps `env=_claude_cli_env()` byte-verbatim,
a source-text the test suite asserts on); the #315/#1030 liveness check
(`_target_in_live_use`) is imported from cli_target_purge (also a stdlib-only
leaf). `_current_box_class` reaches watchdog.reaper LAZILY inside the body.
cli_caveman_plugins.py re-exports every name here (one facade import) so the
existing `cli_caveman_plugins.PLAYWRIGHT_*` / `airuleset.PLAYWRIGHT_*` surface is
unchanged.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

from cli_binary_installers import _claude_cli_env
# #1058 (item 2): the #315/#1030 "is any live process using this dir" /proc scan,
# reused for the per-user old-build cleanup. cli_target_purge is a stdlib-only
# leaf (no `import airuleset`), so a module-level import keeps THIS leaf
# airuleset-free (the split test asserts that).
from cli_target_purge import _target_in_live_use


# Retained ONLY for the export contract + as the disabled-list reference value
# (#1048 moved the old ensure_playwright_browsers guard to PLAYWRIGHT_MANAGED).
PLAYWRIGHT_PLUGIN_KEY = "playwright@claude-plugins-official"
PLAYWRIGHT_BROWSER_CACHE = Path.home() / ".cache" / "ms-playwright"

# --------------------------------------------------------------------------- #
# #1048 -- managed Playwright MCP server (chromium + headless, PINNED) + a
# browsers-path resolver that keeps the installed chromium build in lockstep
# with the pinned server across box classes.
# --------------------------------------------------------------------------- #

# `True` = airuleset manages a working Playwright MCP server on this box (the
# post-#1048 replacement for "playwright in MANAGED_PLUGINS"). Flip to False to
# opt the WHOLE FLEET out of a managed browser (the one-flag opt-out #542 valued).
#
# PER-BOX opt-out (#1048 fix-forward d): a marker file
# `~/.claude/airuleset-playwright-optout` (PLAYWRIGHT_OPTOUT_MARKER below; its
# CONTENT is the human reason line, e.g. "spinbike-vps: no root, chromium system
# libs unavailable") opts THIS ONE box out without touching the fleet flag —
# supervisor-set, owner-informed. When it exists: provision_playwright_mcp()
# skips the browser install AND the ~/.claude.json server write, removes any
# stale browsers-path marker, and prints one honest line; the push post-check
# (cli_remote._playwright_chromium_postcheck) SKIPs LOUDLY with the reason
# instead of failing the target. For a box that structurally cannot run chromium
# (missing system libs + no root, spinbike-vps), this keeps the push green
# without shipping a dead browser or a permanent false-red.
PLAYWRIGHT_MANAGED = True

# THE pin (never `@latest`, #1048). A MATCHED TRIO, verified against the npm
# registry on 2026-09-17 and lock-tested (test_playwright_mcp_pin_1048):
#   @playwright/mcp@0.0.81  depends on  playwright@1.64.0-alpha-2026-09-14
#                                 and  playwright-core@1.64.0-alpha-2026-09-14
#   playwright@1.64.0-alpha-2026-09-14  `install chromium`  ->  chromium build 1244
# so the MCP server, the browser we install, and the /opt build-match probe all
# agree by construction. Bump ALL THREE together (the lock test refuses `latest`).
#   #1048 review finding 2 (fidelity residual): the push health-check launches
#   chromium via `playwright@PW_VERSION` (the standalone pkg), NOT the production
#   `@playwright/mcp@MCP_VERSION` server. They share the SAME chromium build only
#   BECAUSE @playwright/mcp@0.0.81 depends on playwright@1.64.0-alpha-2026-09-14
#   above — so the probe exercises the correct browser TODAY, but a future bump
#   that moves the two out of lockstep would leave the probe green while the MCP
#   server is dead. This offline trio-lock cannot resolve the live npm dep edge;
#   the backstop is the first-push LIVE check (`claude mcp get playwright`
#   Connected + a real browser_navigate screenshot). Followup: a dep-edge lock
#   (install/probe via `npx --package @playwright/mcp@MCP_VERSION`, once its
#   transitive-bin behaviour is verified, or a CI job resolving the dependency).
PLAYWRIGHT_MCP_VERSION = "0.0.81"
PLAYWRIGHT_PW_VERSION = "1.64.0-alpha-2026-09-14"
PLAYWRIGHT_CHROMIUM_BUILD = "1244"

# The user-scope MCP server name written into ~/.claude.json (mcpServers.<name>).
PLAYWRIGHT_MCP_SERVER_NAME = "playwright"

# The root-owned shared browser copy (#950). #1058 made the resolver
# class-agnostic: reused by EVERY box class (shared-stream included) WHEN it holds
# the complete pinned chromium build, else the per-user cache — a no-sudo box
# cannot WRITE /opt but can READ a build-matched one (the #950 one-shared-copy,
# restored by an owner-present root refresh; see internals-archive #1058).
OPT_MS_PLAYWRIGHT = Path("/opt/ms-playwright")

# The remote install writes the RESOLVED browsers path here so the push-time
# health-check shell fragment (cli_remote._playwright_chromium_postcheck) can
# read it without re-implementing the resolver in shell (#1048).
PLAYWRIGHT_BROWSERS_PATH_MARKER = Path.home() / ".claude" / "airuleset-playwright-browsers-path"

# #1048 fix-forward (d): the per-box opt-out marker. Its CONTENT is the reason
# line surfaced by the install + the post-check. See PLAYWRIGHT_MANAGED above.
PLAYWRIGHT_OPTOUT_MARKER = Path.home() / ".claude" / "airuleset-playwright-optout"


def _has_pinned_chromium_build(browsers_dir: Path) -> bool:
    """True iff `browsers_dir` holds a COMPLETE pinned chromium install — BOTH
    `chromium-<b>` AND `chromium_headless_shell-<b>` present, each carrying its
    own `INSTALLATION_COMPLETE` marker file (#1048 fix-forward a). The ONE
    build-match predicate, shared by the /opt reuse check AND the install
    idempotency guard, so they can never diverge on what 'the right browser is
    present' means.

    Why BOTH halves + markers (the montalu3-6 incident): `playwright install
    chromium` writes `chromium-<b>/INSTALLATION_COMPLETE` FIRST and
    `chromium_headless_shell-<b>/INSTALLATION_COMPLETE` SECOND (then ffmpeg), so a
    download that dies between the two leaves `chromium-<b>` ONLY — the live half
    state the OLD `chromium-<b>.is_dir()` predicate false-positived as
    "installed", so the next push never re-installed and the `--headless` MCP
    server + the push post-check stayed dead forever (both need the headless-shell
    binary under `chromium_headless_shell-<b>`). The `INSTALLATION_COMPLETE`
    marker is playwright's own completion sentinel — a bare directory from an
    interrupted extraction does not carry it."""
    b = PLAYWRIGHT_CHROMIUM_BUILD
    for name in ("chromium-" + b, "chromium_headless_shell-" + b):
        if not (browsers_dir / name / "INSTALLATION_COMPLETE").is_file():
            return False
    return True


def _opt_has_pinned_build(opt_dir: Path = None) -> bool:
    """True iff the root-owned /opt/ms-playwright already holds the PINNED
    chromium build (`chromium-<PLAYWRIGHT_CHROMIUM_BUILD>`). The #1048 incident
    was exactly this being FALSE (it held chromium-1243 while the MCP needed
    1244), so a workstation only reuses /opt when the build matches; otherwise
    the per-user cache (always installable to the pinned build) is used."""
    return _has_pinned_chromium_build(opt_dir or OPT_MS_PLAYWRIGHT)


def _playwright_pinned_build_installed(cache_dir: Path = None) -> bool:
    """True iff the browsers cache holds the PINNED chromium build — NOT merely
    that the dir is non-empty (#1048 review-2 finding 1). #542 ran an UNPINNED
    `npx playwright install chromium` fleet-wide, so most stream boxes ALREADY
    have a populated `~/.cache/ms-playwright` holding a PRE-1244 build; a mere
    non-emptiness guard (`_playwright_browsers_installed`) would SKIP the pinned
    install on exactly those boxes, leaving the managed MCP server dead (the
    #2420 class the fix exists for) and the push post-check red. Gating on the
    pinned build re-installs the correct browser whenever the cache holds a
    wrong/old one."""
    d = cache_dir or PLAYWRIGHT_BROWSER_CACHE
    return _has_pinned_chromium_build(d)


def resolve_playwright_browsers_path(box_class, opt_has_pinned_build, home: Path = None) -> Path:
    """The single source of truth for WHERE the managed chromium lives (#1048,
    made CLASS-AGNOSTIC in #1058). ONE resolver used by the browser install, the
    MCP server env, the bashrc export, and the push post-check, so the four can
    never diverge.

    The decision is now the SAME for EVERY box class: reuse the root-owned shared
    `/opt/ms-playwright` when it holds the COMPLETE pinned chromium build, else
    the per-user `~/.cache/ms-playwright`. `opt_has_pinned_build` is the exact
    build-match predicate (`_has_pinned_chromium_build`), so a MISMATCHED or
    absent /opt is NEVER used — that is what keeps the odoo-erp#2420 class
    impossible (the failure was a no-sudo box resolving to a mismatched /opt).

    #1058 dropped the shared-stream special-case that #1048 added (which forced
    the per-user cache even when /opt matched): a no-sudo box cannot WRITE /opt,
    but it can READ a build-matched /opt, so once the owner-present root refresh
    lands the pinned build there, all 14 subdev accounts collapse back onto the
    #950 one-shared-copy instead of each keeping their own ~650 MB copy. `box_class`
    is retained in the signature for API stability (every caller passes it and a
    future per-class rule could return) but no longer changes the result."""
    home = home or Path.home()
    per_user = home / ".cache" / "ms-playwright"
    return OPT_MS_PLAYWRIGHT if opt_has_pinned_build else per_user


def _current_box_class() -> str:
    """This box's class marker, degrading to `workstation` when it cannot be
    resolved (the same fail-safe direction as the marker writer)."""
    try:
        from watchdog.reaper import default_box_class
        # #1048 review-2 finding 3: default_box_class() returns None on a
        # missing/unreadable marker (no exception) — coerce to "workstation" so
        # the docstring's promise holds and a None never slips into the resolver.
        return default_box_class() or "workstation"
    except Exception:
        return "workstation"


def resolved_browsers_path(box_class=None, home: Path = None) -> Path:
    """Convenience: resolve the browsers path for THIS box (or an injected
    box_class/home for tests), reading the live /opt build-match state."""
    bc = box_class or _current_box_class()
    return resolve_playwright_browsers_path(bc, _opt_has_pinned_build(), home=home)


def render_playwright_mcp_server(browsers_path) -> dict:
    """The managed user-scope MCP server entry (#1048): a PINNED
    `@playwright/mcp` on the `chromium` browser, headless, pointed at the
    resolved browsers path. `PLAYWRIGHT_MCP_BROWSER`/`PLAYWRIGHT_MCP_HEADLESS`
    are set in `env` as belt-and-suspenders alongside the authoritative CLI
    flags, so the server still selects chromium even if a future @playwright/mcp
    changes its flag parsing."""
    return {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@" + PLAYWRIGHT_MCP_VERSION,
                 "--browser", "chromium", "--headless"],
        "env": {
            "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path),
            "PLAYWRIGHT_MCP_BROWSER": "chromium",
            "PLAYWRIGHT_MCP_HEADLESS": "true",
        },
    }


def reconcile_playwright_mcp_server(claude_json: dict, browsers_path) -> dict:
    """Pure: return a NEW ~/.claude.json dict with the managed `playwright`
    user-scope MCP server set (when PLAYWRIGHT_MANAGED), every other top-level
    key and every other MCP server preserved untouched. Idempotent."""
    result = dict(claude_json)
    # #1048 review-2 finding 2: coerce a non-dict `mcpServers` (a live
    # ~/.claude.json with `"mcpServers": null` or a stray string) to {} rather
    # than letting `dict(...)` raise — the raise propagated OUTSIDE the caller's
    # try/except into cmd_install's "(non-fatal)" swallow, leaving the box
    # unprovisioned SILENTLY while the already-written marker let the postcheck
    # false-pass on the browser alone.
    raw_servers = result.get("mcpServers")
    servers = dict(raw_servers) if isinstance(raw_servers, dict) else {}
    if PLAYWRIGHT_MANAGED:
        servers[PLAYWRIGHT_MCP_SERVER_NAME] = render_playwright_mcp_server(browsers_path)
    result["mcpServers"] = servers
    return result


def unreconcile_playwright_mcp_server(claude_json: dict) -> dict:
    """Pure: return a NEW ~/.claude.json dict with the managed `playwright`
    user-scope MCP server REMOVED (the per-box opt-out, #1048 fix-forward d),
    every other top-level key and every other MCP server preserved untouched.
    Idempotent (a no-op when the managed server is already absent). Needed
    because an opted-out box may have been provisioned by an EARLIER push
    (v0.1.321 wrote the server) — merely SKIPPING the write would leave that dead
    `@playwright/mcp --headless chromium` server in place and Claude Code would
    try to launch it (with an absent/broken browser) every session."""
    result = dict(claude_json)
    raw_servers = result.get("mcpServers")
    if not isinstance(raw_servers, dict) or PLAYWRIGHT_MCP_SERVER_NAME not in raw_servers:
        return result
    servers = dict(raw_servers)
    del servers[PLAYWRIGHT_MCP_SERVER_NAME]
    result["mcpServers"] = servers
    return result


def _playwright_browsers_installed(cache_dir: Path = None) -> bool:
    """True iff the browser cache genuinely has something in it — not just
    that the directory exists (an empty dir from an interrupted install
    would otherwise look 'done' forever)."""
    d = cache_dir or PLAYWRIGHT_BROWSER_CACHE
    return d.is_dir() and any(d.iterdir())

def _is_per_user_cache(browsers_path) -> bool:
    """True iff `browsers_path` is a per-user cache (`<home>/.cache/ms-playwright`)
    rather than the root-owned shared `/opt/ms-playwright`. The install-deps
    system-library heal (#1048 fix-forward b) runs ONLY on the per-user cache — a
    workstation reusing /opt already had root-installed libs, and a no-sudo box
    cannot write /opt anyway (so `install-deps` there is both wrong and
    impossible). Compared by shape (name + parent name), so it needs no `home`
    argument and cannot confuse `.../.cache/ms-playwright` with `/opt/ms-playwright`."""
    bp = Path(browsers_path)
    return bp.name == "ms-playwright" and bp.parent.name == ".cache"


# #1058 (item 2): a `<family>-<revision>` build-dir name (same shape the disk-guard
# #892 sweep parses: family = everything before the LAST `-<digits>`).
_BUILD_DIR_RE = re.compile(r"^(.+)-(\d+)$")
# The ONLY families cleanup reaps: keep EXACTLY the pinned build
# (PLAYWRIGHT_CHROMIUM_BUILD) — the managed MCP server + the push post-check both
# need exactly this build. ffmpeg is deliberately NOT reaped here (see below).
_PINNED_EXACT_FAMILIES = ("chromium", "chromium_headless_shell")


def _iter_chromium_pair_builds(listing):
    """#1058 rework-2 review (A 🟡-3 / B): the ONE place the chromium-pair build
    filter lives — yields `(entry, revision)` for every NON-symlink build dir in
    `listing` whose family is a pinned chromium family (`chromium` /
    `chromium_headless_shell`). Shared by `_cleanup_old_builds` (keeps the pinned
    rev, removes the rest) and `_reap_per_user_copy_if_safe` (removes the whole
    pair once /opt is in use), so the symlink guard + `_BUILD_DIR_RE` parse +
    family filter are never duplicated (#993 no-patchwork). `listing` is the
    caller's already-sorted `base.iterdir()` — the caller owns the scan + its
    error handling, so a scan failure stays the caller's concern (both guard it)."""
    for entry in listing:
        # `is_symlink` FIRST: a symlink is never followed nor removed (matches the
        # disk-guard #892 stance); `is_dir()` follows links, so order matters.
        if entry.is_symlink() or not entry.is_dir():
            continue
        m = _BUILD_DIR_RE.match(entry.name)
        if m is None or m.group(1) not in _PINNED_EXACT_FAMILIES:
            continue
        yield entry, m.group(2)


def _cleanup_old_builds(browsers_path, *, live_check=None):
    """#1058 (item 2): reap SUPERSEDED chromium build dirs from the PER-USER
    cache so it stops accreting ~650 MB on every pin bump (the shared-box disk
    doctrine #925 + the #950 one-shared-copy rationale). Called by
    ensure_playwright_browsers after a pinned install AND when the pinned build is
    already present (so today's stale siblings on gk/spinbike — where playwright's
    own installer GC never ran because our guard skipped the install — go away on
    the next push).

    Scoped HARD:
      * ONLY the per-user cache (`_is_per_user_cache`) — NEVER the root-owned
        `/opt/ms-playwright` (that is root's, shared read-only #950);
      * a survivor guard FIRST: only reap when the COMPLETE pinned build is
        actually present (`_has_pinned_chromium_build`) — #1058 review (A 🟡-3),
        so a call in a state where the pinned build is absent can never delete
        every chromium dir and leave the box browserless (both call sites already
        guarantee it present; this is defence-in-depth on a destructive rmtree,
        matching the `_target_in_live_use` fail-safe posture);
      * `chromium` / `chromium_headless_shell`: keep EXACTLY the pinned build
        (`PLAYWRIGHT_CHROMIUM_BUILD`), remove every other build of those families;
      * ffmpeg is NOT reaped here (#1058 review B 🟡-2): airuleset pins no ffmpeg
        revision, so a "keep-highest" rule would DELETE the ffmpeg the current
        pinned install wrote on a playwright ROLLBACK (an older release bundles a
        lower ffmpeg rev) and the idempotency guard (chromium-halves only) would
        never re-install it — a non-self-healing hazard for a ~MB saving. The
        age-gated disk-guard #892 timer already reaps stale ffmpeg safely;
      * NEVER a dir a live process has a cwd / open fd inside (the #315/#1030
        `_target_in_live_use` /proc scan — injectable as `live_check` for tests),
        and NEVER a symlink (left untouched, never followed);
      * every other family (firefox / webkit an unrelated project installed) is
        left alone — this manages only the chromium pair airuleset pins.

    Idempotent (a second call finds nothing to remove) and best-effort: a scan or
    rmtree failure is logged and skipped, never fatal — the install must never
    crash on a cleanup problem. One journal line per removal / kept-live dir."""
    if live_check is None:
        live_check = _target_in_live_use
    base = Path(browsers_path)
    if not _is_per_user_cache(base) or not base.is_dir():
        return
    # Survivor guard (#1058 review A 🟡-3): never reap unless the pinned build we
    # would keep is genuinely present + complete.
    if not _has_pinned_chromium_build(base):
        return
    pinned = PLAYWRIGHT_CHROMIUM_BUILD
    try:
        listing = sorted(base.iterdir())
    except OSError as e:
        print("    ⚠ Playwright cleanup: could not scan %s (%s)" % (base, e),
              file=sys.stderr)
        return
    for entry, rev in _iter_chromium_pair_builds(listing):
        # Only a NON-pinned build of the pair is superseded — keep the pinned one.
        if rev == pinned:
            continue
        if live_check(entry):
            print("    Playwright cleanup: kept %s — a live process is using it"
                  % entry.name)
            continue
        try:
            shutil.rmtree(entry)
            print("    Playwright cleanup: removed superseded build %s" % entry.name)
        except OSError as e:
            print("    ⚠ Playwright cleanup: could not remove %s (%s)"
                  % (entry.name, e), file=sys.stderr)


def _read_browsers_path_marker() -> "Path | None":
    """#1058 rework-2: the PREVIOUS resolved browsers path recorded in the marker
    (`PLAYWRIGHT_BROWSERS_PATH_MARKER`) BEFORE this install overwrites it, or None
    when the marker is absent/unreadable/empty. `provision_playwright_mcp` reads
    it before `reconcile_playwright_mcp_file` rewrites the marker, so the reap can
    tell a FIRST flip to /opt (previous marker != /opt → keep one more cycle) from
    a settled /opt (previous marker == /opt → the per-user copy is now redundant).
    Never fatal — a read failure returns None (the reap then keeps, the safe way).
    Catches `ValueError` too (a non-UTF-8 marker raises `UnicodeDecodeError`), so
    the "never fatal → None" contract holds for a corrupt marker, not just an
    absent one (#1058 rework-2 review A 🟡-1)."""
    try:
        txt = PLAYWRIGHT_BROWSERS_PATH_MARKER.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return Path(txt) if txt else None


def _live_env_points_at(path, proc_dir=None) -> list:
    """#1058 rework-2 (item 2): the pids of the CURRENT user's live processes
    whose `/proc/<pid>/environ` carries `PLAYWRIGHT_BROWSERS_PATH=<path>` EXACTLY.

    This is the case the fd/cwd `_target_in_live_use` scan structurally MISSES: an
    IDLE managed MCP server that will `npx playwright` a chromium LATER holds the
    per-user path in its ENVIRON but has no open fd / cwd inside the build dir yet
    (the david3/david4 servers the v0.1.332 incident measured — a stale env after
    the markers flipped to /opt). So the reap must ALSO refuse when such a server
    is alive, or it would delete a build that a live server is about to launch.

    Matched EXACTLY (a full `KEY=VALUE` environ entry), never a prefix, so a
    sibling `…ms-playwright-old` never counts. Reads only the current user's
    processes — a foreign-uid `/proc/<pid>/environ` is unreadable (EACCES) and is
    SKIPPED per-pid, never fatal (the same accepted residual as `_target_in_
    live_use`). A TOTAL /proc failure returns [] (never fatal); the reap's
    `live_check` gate — `_target_in_live_use`, fail-safe True on a total /proc
    failure — is the backstop that still refuses the reap in that case, so this
    predicate never has to fail-safe on its own. `proc_dir` is injectable for a
    hermetic fake /proc tree."""
    needle = b"PLAYWRIGHT_BROWSERS_PATH=" + os.fsencode(str(path))
    proc = Path(proc_dir) if proc_dir is not None else Path("/proc")
    hits = []
    try:
        entries = os.listdir(proc)
    except OSError:
        return hits
    for name in entries:
        if not name.isdigit():
            continue
        try:
            raw = (proc / name / "environ").read_bytes()
        except OSError:
            continue  # unreadable (foreign uid) or gone — skip, never fatal
        if needle in raw.split(b"\x00"):
            hits.append(int(name))
    return sorted(hits)


def _reap_per_user_copy_if_safe(per_user, *, previous_marker,
                                live_env=None, live_check=None) -> bool:
    """#1058 rework-2 (item 2): the ACCOUNT reaps its OWN per-user chromium pair
    once the shared /opt has taken over — the SAFE replacement for the root sweep
    that caused the v0.1.332 incident (root never mutates user state; ownership is
    now clean). Removes the `chromium-*` / `chromium_headless_shell-*` dirs (and
    NOTHING else — never ffmpeg, per #1058 review B 🟡-2; never any other family)
    from the PER-USER cache, so a subdev account collapses back onto the #950
    one-shared-copy instead of keeping its own ~650 MB duplicate.

    Reaps ONLY when ALL gates are clear — otherwise it KEEPS and journals the
    reason (one line per decision):
      (i)   `previous_marker == OPT_MS_PLAYWRIGHT` — the marker ALREADY pointed at
            /opt BEFORE this install. The marker + the ~/.claude.json MCP env are
            written together by `reconcile_playwright_mcp_file`, so this proves at
            least THOSE surfaces resolved to /opt for a full cycle; the settings/
            bashrc appliers are separate, but the reap is still safe because /opt
            is monotonically complete from the flip onward and EVERY surface is
            rewritten to /opt on THIS reaping push (the caller only reaps when
            `resolved_browsers_path == /opt`). On the FIRST flip (previous marker
            was per-user) we keep, so nothing that might still hold the per-user
            path is deleted out from under it;
      (S)   survivor guard `_opt_has_pinned_build()` — /opt genuinely holds the
            COMPLETE pinned build right now. Defense-in-depth PARITY with
            `_cleanup_old_builds`'s survivor guard (review A 🟡-3): the reap
            deletes the per-user pinned pair — the ONLY chromium if /opt is
            incomplete — so a caller that ever reaches here without a complete
            /opt can never leave the box browserless. The sole call site already
            guards on `resolved == /opt` (⇔ this predicate); this is the belt to
            that braces;
      (ii)  `live_env(per_user)` is EMPTY — no live process of this user carries
            PLAYWRIGHT_BROWSERS_PATH=<per-user> in its environ (the idle-MCP-
            server case the fd/cwd scan misses);
      (iii) `live_check(entry)` is clear for the dir — the #315/#1030
            `_target_in_live_use` /proc fd/cwd scan (fail-safe True on any /proc
            read failure), checked per build dir exactly as `_cleanup_old_builds`.
    NEVER the root-owned /opt (guarded by `_is_per_user_cache`), NEVER a symlink
    (left untouched, never followed — `_iter_chromium_pair_builds`). Best-effort:
    a scan/rmtree failure is logged and skipped, never fatal. Returns True iff it
    reaped at least one dir.

    Residual (accepted, #950 one-shared-copy): after the reap the per-user cache
    holds no chromium, so an UNMANAGED playwright run with PLAYWRIGHT_BROWSERS_PATH
    unset would default to it and find nothing — but every airuleset-managed
    surface (MCP env, marker, bashrc export, settings.json env, push post-check)
    sets the var explicitly to /opt, so only a third-party consumer (none known on
    subdev) is affected."""
    if live_env is None:
        live_env = _live_env_points_at
    if live_check is None:
        live_check = _target_in_live_use
    base = Path(per_user)
    # (i) the marker must ALREADY have pointed at /opt before this install.
    if previous_marker != OPT_MS_PLAYWRIGHT:
        print("    Playwright cleanup: kept per-user copy — marker flipped to /opt "
              "this install (one more cycle before reap)")
        return False
    # Never /opt (root's), and only a real per-user cache dir.
    if not _is_per_user_cache(base) or not base.is_dir():
        return False
    # (S) survivor guard: /opt must actually hold the complete pinned build before
    # we delete the per-user pair (defense-in-depth, parity with cleanup).
    if not _opt_has_pinned_build():
        print("    Playwright cleanup: kept per-user copy — /opt lacks the complete "
              "pinned build (survivor guard)")
        return False
    try:
        listing = sorted(base.iterdir())
    except OSError as e:
        print("    ⚠ Playwright cleanup: could not scan %s (%s)" % (base, e),
              file=sys.stderr)
        return False
    # The chromium-pair dirs (never ffmpeg, never other families, never symlinks).
    pair = [entry for entry, _rev in _iter_chromium_pair_builds(listing)]
    if not pair:
        return False  # nothing to reap — idempotent
    # (ii) no live server env still points at the per-user cache.
    pids = live_env(base)
    if pids:
        print("    Playwright cleanup: kept per-user copy — live server pid %d "
              "still points at it (env)" % pids[0])
        return False
    reaped_any = False
    for entry in pair:
        # (iii) per-dir fd/cwd liveness (fail-safe True on any /proc failure).
        if live_check(entry):
            print("    Playwright cleanup: kept %s — a live process is using it"
                  % entry.name)
            continue
        try:
            shutil.rmtree(entry)
            print("    Playwright cleanup: reaped per-user copy (shared /opt in "
                  "use): %s" % entry.name)
            reaped_any = True
        except OSError as e:
            print("    ⚠ Playwright cleanup: could not remove %s (%s)"
                  % (entry.name, e), file=sys.stderr)
    return reaped_any


def _stderr_tail(text, n: int = 8) -> str:
    """The LAST `n` lines of a subprocess's output. The REAL playwright download
    error lives at the END of the output (its generic 'running npx playwright
    install without dependencies' WARNING box is at the HEAD), so the old
    `[:200]` head slice threw the actual error away on montalu3-6 (#1048
    fix-forward b) — a tail keeps it."""
    lines = (text or "").rstrip("\n").splitlines()
    return "\n".join(lines[-n:])


def _sudo_n_available() -> bool:
    """True iff passwordless sudo works here (`sudo -n true` exits 0), bounded.
    Injectable into `ensure_playwright_browsers` so the install-deps decision is
    unit-testable without real sudo."""
    import subprocess
    try:
        return subprocess.run(["sudo", "-n", "true"],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _headless_shell_binary(browsers_path) -> Path:
    """The installed headless-shell executable under `browsers_path` for the
    pinned build — the exact binary the `--headless` MCP server + the push
    post-check need (its absence is the montalu3-6 half-cache failure).

    Arch-agnostic (#1048 review-2): globs the platform subdir
    (`chrome-headless-shell-linux64` on x64, `chrome-headless-shell-linux-arm64`
    on ARM) rather than hardcoding linux64 — a hardcode would make the exit-127
    heal (b) silently no-op on a non-x64 box, exactly the no-root VPS class it
    targets. Falls back to the linux64 path (which then simply won't exist → the
    probe returns None → heal skipped) when nothing matches."""
    base = Path(browsers_path) / ("chromium_headless_shell-" + PLAYWRIGHT_CHROMIUM_BUILD)
    matches = sorted(base.glob("chrome-headless-shell-*/chrome-headless-shell"))
    return matches[0] if matches else base / "chrome-headless-shell-linux64" / "chrome-headless-shell"


def _probe_headless_shell_rc(browsers_path):
    """Bounded launch probe of the installed headless shell → its exit code
    (127 == missing system shared libraries: the ELF loader fails BEFORE the
    binary runs, the spinbike-vps failure), or None when the binary is absent
    (nothing to probe). Injectable into `ensure_playwright_browsers` so the
    install-deps decision is unit-testable with no real browser."""
    import subprocess
    binp = _headless_shell_binary(browsers_path)
    if not binp.exists():
        return None
    try:
        return subprocess.run([str(binp), "--version"],
                              capture_output=True, timeout=30).returncode
    except Exception:
        return None


def _heal_system_libs(browsers_path, env, *, sudo_ok, probe_rc):
    """#1048 fix-forward (b): after a successful pinned install into the per-user
    cache, if the installed headless shell fails to LAUNCH with exit 127 (missing
    system shared libraries — spinbike-vps), heal it ONCE with the vendor
    `playwright install-deps chromium` when passwordless sudo is available, then
    re-probe; without sudo, print the exact root command and continue
    non-fatally. A no-op on any other probe result (0 = healthy, None = binary
    absent, other = a non-lib failure not fixable by install-deps)."""
    import subprocess
    rc = probe_rc(browsers_path)
    if rc != 127:
        return
    if not sudo_ok():
        print("    ⚠ headless chromium is missing system shared libraries (exit "
              "127) and passwordless sudo is unavailable — run as root: "
              "npx -y playwright@%s install-deps chromium" % PLAYWRIGHT_PW_VERSION,
              file=sys.stderr)
        return
    # No explicit `sudo` prefix: `playwright install-deps` self-prepends sudo
    # when euid != 0 (its documented behaviour), and we only reach here after
    # `sudo -n true` proved passwordless sudo works — so the vendor's own sudo
    # call succeeds non-interactively. Running it under an explicit `sudo -n`
    # would instead re-root npm's HOME/cache; we rely on the vendor path.
    try:
        dr = subprocess.run(
            ["npx", "-y", "playwright@" + PLAYWRIGHT_PW_VERSION, "install-deps", "chromium"],
            capture_output=True, text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        print("    ⚠ playwright install-deps chromium timed out after 300 s",
              file=sys.stderr)
        return
    except Exception as e:
        print("    ⚠ playwright install-deps chromium skipped (%s)" % e,
              file=sys.stderr)
        return
    if dr.returncode != 0:
        print("    ⚠ playwright install-deps chromium failed (rc=%d). stderr tail:\n%s"
              % (dr.returncode, _stderr_tail(dr.stderr or dr.stdout)), file=sys.stderr)
        return
    if probe_rc(browsers_path) == 127:
        print("    ⚠ headless chromium still missing system shared libraries "
              "after install-deps (exit 127)", file=sys.stderr)
    else:
        print("    Playwright browsers: healed system libraries via "
              "install-deps chromium (headless shell now launches)")


def ensure_playwright_browsers(cache_dir: Path = None, box_class: str = None, *,
                               sleep=None, sudo_ok=None, probe_rc=None):
    """Best-effort, time-boxed, non-fatal install of the PINNED chromium
    (#158/#1048): enabling a browser MCP alone does NOT pull the browser
    binaries — measured live, fleet accounts had node + the server but an EMPTY
    cache, so every browser call failed "Executable doesn't exist" until someone
    ran this by hand. #1048: the version is now PINNED (`playwright@
    PLAYWRIGHT_PW_VERSION`, never `@latest`) so the installed chromium build
    (1244) matches the pinned managed MCP server, and the target dir is the
    RESOLVED browsers path (#1058 class-agnostic: the shared /opt/ms-playwright
    when it holds the complete pinned build, else the per-user
    `~/.cache/ms-playwright` — never a mismatched/absent /opt).

    A no-op when `PLAYWRIGHT_MANAGED` is False, or the resolved cache already
    holds the COMPLETE pinned build (idempotent — once per user, never per
    session, respecting the shared-box disk doctrine; a cache holding a WRONG/old
    build OR only the chromium HALF re-installs the pinned pair, #1048 review-2
    finding 1 + fix-forward a). Skips LOUDLY when `npx` is absent.

    #1048 fix-forward (b): on a failed install prints the stderr TAIL (never a
    head slice), retries ONCE after a pause, an honest line on a 300 s timeout,
    and — on the per-user cache — heals missing system libraries via
    `install-deps` when the launched headless shell exits 127. `sleep`/`sudo_ok`/
    `probe_rc` are injectable seams (default to the real time.sleep / sudo probe /
    launch probe) so the whole path is unit-testable with no network, sudo, or
    real browser."""
    import subprocess
    import time
    if not PLAYWRIGHT_MANAGED:
        return
    if sleep is None:
        sleep = time.sleep
    if sudo_ok is None:
        sudo_ok = _sudo_n_available
    if probe_rc is None:
        probe_rc = _probe_headless_shell_rc
    browsers_path = cache_dir or resolved_browsers_path(box_class)
    # #1048 review-2 finding 1 + fix-forward (a): gate on the COMPLETE pinned
    # build (BOTH halves + markers), not mere cache non-emptiness — a #542-era
    # OLD build, or a half download (chromium-<b> only), must re-install.
    if _playwright_pinned_build_installed(browsers_path):
        # #1048 review-3 (MAJOR): a build being PRESENT does not mean it LAUNCHES.
        # spinbike-vps has the complete pinned cache from v0.1.321 but its headless
        # shell exits 127 (missing system libs) — a plain early-return would never
        # heal it and the post-check would fail 127 on every re-push. So on the
        # per-user cache, probe + heal even when already installed (one bounded
        # `chrome-headless-shell --version` launch per push; a no-op on rc 0 /
        # None). install-deps needs npx, so skip the heal when npx is absent —
        # exactly as the post-check SKIPs a no-npx box; there is nothing else to
        # do for an already-complete build.
        if _is_per_user_cache(browsers_path) and shutil.which("npx") is not None:
            env = dict(_claude_cli_env())
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
            _heal_system_libs(browsers_path, env, sudo_ok=sudo_ok, probe_rc=probe_rc)
        # #1058 (item 2): even when the install is SKIPPED (pinned build already
        # present), playwright's own installer GC did NOT run, so a stale sibling
        # build left over from an earlier pin (gk/spinbike keep 1234 next to 1244)
        # would sit there forever. Reap it now. Self-guards to the per-user cache
        # (never /opt) and needs no npx (pure filesystem).
        _cleanup_old_builds(browsers_path)
        return
    if shutil.which("npx") is None:
        print("    ⚠ Playwright browsers missing and npx is absent — cannot "
              "install chromium; once npx exists run: PLAYWRIGHT_BROWSERS_PATH=%s "
              "npx -y playwright@%s install chromium"
              % (browsers_path, PLAYWRIGHT_PW_VERSION), file=sys.stderr)
        return
    env = dict(_claude_cli_env())
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    try:
        Path(browsers_path).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("    ⚠ could not create the Playwright browsers cache %s (%s)"
              % (browsers_path, e), file=sys.stderr)
        return
    argv = ["npx", "--yes", "playwright@" + PLAYWRIGHT_PW_VERSION, "install", "chromium"]
    for attempt in (1, 2):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
        except subprocess.TimeoutExpired:
            # #1048 fix-forward (b): an honest, distinct line — never the opaque
            # generic "auto-install skipped (<exc repr>)".
            print("    ⚠ Playwright browsers install timed out after 300 s — run "
                  "manually: PLAYWRIGHT_BROWSERS_PATH=%s npx -y playwright@%s "
                  "install chromium" % (browsers_path, PLAYWRIGHT_PW_VERSION),
                  file=sys.stderr)
            return
        except Exception as e:
            print("    ⚠ Playwright browsers missing and auto-install skipped (%s) "
                  "— run manually: PLAYWRIGHT_BROWSERS_PATH=%s npx -y playwright@%s "
                  "install chromium" % (e, browsers_path, PLAYWRIGHT_PW_VERSION),
                  file=sys.stderr)
            return
        if r.returncode == 0:
            print("    Playwright browsers: installed chromium %s into %s "
                  "(pinned playwright@%s)"
                  % (PLAYWRIGHT_CHROMIUM_BUILD, browsers_path, PLAYWRIGHT_PW_VERSION))
            break
        tail = _stderr_tail(r.stderr or r.stdout)
        if attempt == 1:
            # #1048 fix-forward (b): retry ONCE after a pause (many montalu
            # accounts hit a transient CDN throttle / cacache rename race).
            print("    ⚠ Playwright browsers install failed (rc=%d) — retrying "
                  "once in 10 s. stderr tail:\n%s" % (r.returncode, tail),
                  file=sys.stderr)
            sleep(10)
            continue
        print("    ⚠ Playwright browsers install failed again (rc=%d). stderr "
              "tail:\n%s\n    Run manually: PLAYWRIGHT_BROWSERS_PATH=%s npx -y "
              "playwright@%s install chromium"
              % (r.returncode, tail, browsers_path, PLAYWRIGHT_PW_VERSION),
              file=sys.stderr)
        return
    # Reached ONLY via the rc==0 `break` above (every rc!=0 / exception path
    # returns inside the loop), so the install succeeded here.
    # #1048 fix-forward (b): heal missing system libraries on the per-user cache
    # (the spinbike-vps exit-127 case). Skipped on /opt (root already provisioned
    # its libs; a no-sudo box cannot write /opt anyway).
    if _is_per_user_cache(browsers_path):
        _heal_system_libs(browsers_path, env, sudo_ok=sudo_ok, probe_rc=probe_rc)
    # #1058 (item 2): remove the superseded build this install just replaced
    # (playwright's GC usually does this when the install RUNS, but do it
    # ourselves too so a partial GC never leaves a stale sibling). Self-guards to
    # the per-user cache.
    _cleanup_old_builds(browsers_path)


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace `path` with `text` (mkstemp in the same dir → full
    write → os.replace). Uses `os.fdopen(...).write`, whose buffered writer
    writes ALL bytes or raises, so a POSIX short write can never truncate the
    target — the byte-count the raw `os.write` idiom ignored (#1048 review-2:
    ~/.claude.json holds multi-MB history/cache; a truncated atomic replace is
    silent corruption). Raises OSError on failure; the caller owns the non-fatal
    handling + its own message (each call site's error text differs). The tmp is
    cleaned on any failure."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(Path(path).parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode())
        os.replace(tmp, str(path))
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:  # airuleset:script-ok best-effort orphan cleanup
                pass


def reconcile_playwright_mcp_file(claude_json_path: Path = None, box_class: str = None) -> bool:
    """#1048: idempotently write the managed `playwright` MCP server into
    ~/.claude.json (top-level `mcpServers`, the user scope Claude Code reads at
    session start) AND record the resolved browsers path in the marker file the
    push post-check reads (`cli_remote._playwright_chromium_postcheck`).

    Best-effort + non-fatal, like every other reconcile step here: a write
    failure only loses the managed server for this run and MUST NEVER crash the
    install (returns False, the caller latches it into a non-zero exit; a
    concurrent Claude Code write to ~/.claude.json can at worst be a lost update
    that self-heals on the next push — the atomic replace prevents corruption).
    Returns True when nothing failed. A no-op returning True when
    PLAYWRIGHT_MANAGED is False."""
    if not PLAYWRIGHT_MANAGED:
        return True
    path = claude_json_path or (Path.home() / ".claude.json")
    browsers_path = resolved_browsers_path(box_class)
    ok = True

    # 1. marker file for the push post-check (best-effort, does not gate).
    try:
        PLAYWRIGHT_BROWSERS_PATH_MARKER.parent.mkdir(parents=True, exist_ok=True)
        PLAYWRIGHT_BROWSERS_PATH_MARKER.write_text(str(browsers_path) + "\n", encoding="utf-8")
    except OSError as e:
        print("    ⚠ could not write playwright browsers-path marker (%s)" % e,
              file=sys.stderr)

    # 2. ~/.claude.json managed server (atomic write, only when changed).
    try:
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        print("    ⚠ could not read ~/.claude.json for the playwright MCP server (%s)"
              % e, file=sys.stderr)
        return False
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    ⚠ ~/.claude.json is invalid JSON — skipped the playwright MCP server",
              file=sys.stderr)
        return False
    if not isinstance(data, dict):
        print("    ⚠ ~/.claude.json is not a JSON object — skipped the playwright MCP server",
              file=sys.stderr)
        return False
    # Compare PARSED dicts, not formatted strings: ~/.claude.json is owned by
    # Claude Code, whose own formatting may differ from json.dumps(indent=2) — a
    # string compare would then rewrite it on EVERY push (needless churn + a
    # concurrent-write window each time). A dict compare is idempotent whenever
    # the managed server is already present and identical, regardless of layout.
    new_data = reconcile_playwright_mcp_server(data, browsers_path)
    if new_data == data:
        return ok
    # ensure_ascii=False: ~/.claude.json is written by Claude Code with LITERAL
    # UTF-8 (prompt/history/cache text) — the default \uXXXX escaping would
    # re-encode all of it on our write, fighting Claude Code's own writer.
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False) + "\n"
    try:
        _atomic_write_text(path, new_str)
        print("    playwright MCP server: pinned @playwright/mcp@%s --browser "
              "chromium --headless (%s)" % (PLAYWRIGHT_MCP_VERSION, browsers_path))
    except OSError as e:
        print("    ⚠ could not write the playwright MCP server to ~/.claude.json (%s)"
              % e, file=sys.stderr)
        ok = False
    return ok


def unprovision_playwright_mcp_file(claude_json_path: Path = None) -> None:
    """#1048 fix-forward (d): on a per-box opt-out, TEAR DOWN any prior managed
    Playwright provisioning — remove BOTH the browsers-path marker AND the
    managed `playwright` MCP server entry from ~/.claude.json. A box provisioned
    by an EARLIER push (v0.1.321 wrote the server) must not keep launching a dead
    `--headless chromium` MCP server every session; and the absent marker makes
    the push post-check SKIP. Best-effort + non-fatal + idempotent (a no-op when
    neither is present), like every reconcile step here — a write failure only
    loses the teardown for this run and self-heals on the next push. (If
    ~/.claude.json is unreadable/locked at opt-out time, the server entry removal
    is skipped and the push still goes GREEN on the marker's presence — the dead
    server self-heals on the next successful push; an accepted best-effort residual.)"""
    # 1. the browsers-path marker (best-effort).
    try:
        PLAYWRIGHT_BROWSERS_PATH_MARKER.unlink(missing_ok=True)
    except OSError as e:
        print("    ⚠ could not remove the stale playwright browsers-path marker (%s)"
              % e, file=sys.stderr)
    # 2. the managed server entry in ~/.claude.json (atomic, only when present).
    path = claude_json_path or (Path.home() / ".claude.json")
    try:
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        print("    ⚠ could not read ~/.claude.json to remove the playwright MCP "
              "server (%s)" % e, file=sys.stderr)
        return
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("    ⚠ ~/.claude.json is invalid JSON — left the playwright MCP "
              "server entry as-is", file=sys.stderr)
        return
    if not isinstance(data, dict):
        return
    new_data = unreconcile_playwright_mcp_server(data)
    if new_data == data:
        return
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False) + "\n"
    try:
        _atomic_write_text(path, new_str)
        print("    playwright MCP server: removed (opted out on this box)")
    except OSError as e:
        print("    ⚠ could not remove the playwright MCP server from ~/.claude.json (%s)"
              % e, file=sys.stderr)


def provision_playwright_mcp(box_class: str = None) -> bool:
    """#1048: provision the managed Playwright MCP end-to-end on this box — the
    #542-availability invariant delivered by a working, version-PINNED server
    instead of the dead chrome-channel plugin. Two steps: (1) install the pinned
    chromium into the resolved browsers path (best-effort/non-fatal, as before);
    (2) write the managed pinned `playwright` MCP server + the browsers-path
    marker into ~/.claude.json. A genuine server-reconcile failure returns False
    → the caller (cmd_install) latches a non-zero exit (script-failure-policy),
    so a box that never got the working server is a LOUD failure, never a silent
    "Install complete.". A no-op returning True when PLAYWRIGHT_MANAGED is
    False.

    #1048 fix-forward (d): a per-box opt-out — when PLAYWRIGHT_OPTOUT_MARKER
    exists, skip BOTH steps (no browser install, no ~/.claude.json server write),
    REMOVE any stale browsers-path marker (so the push post-check SKIPs on the
    absent marker as well as on the opt-out file itself, never false-passes on a
    browser that is no longer being maintained), print ONE honest line with the
    reason, and return True (a deliberate opt-out is a success, not a failure)."""
    if not PLAYWRIGHT_MANAGED:
        return True
    if PLAYWRIGHT_OPTOUT_MARKER.exists():
        # errors="replace": a non-UTF-8 reason line must never raise
        # (UnicodeDecodeError) out of the opt-out branch — that would defeat the
        # whole "keep the push green on a box that cannot run chromium" purpose.
        try:
            reason = PLAYWRIGHT_OPTOUT_MARKER.read_text(
                encoding="utf-8", errors="replace").strip()
        except OSError:
            reason = ""
        # Tear DOWN any prior provisioning: remove the browsers-path marker AND
        # the managed server entry (v0.1.321 may have written a server that now
        # points at an absent/broken browser) — so the post-check SKIPs and
        # Claude Code never launches a dead --headless chromium here.
        unprovision_playwright_mcp_file()
        print("    playwright MCP: opted out on this box (%s) — skipping browser "
              "install + server write" % (reason or "no reason given"))
        return True
    # #1058 rework-2: capture the PREVIOUS marker value BEFORE reconcile
    # overwrites it — the reap needs to know whether the marker ALREADY pointed at
    # /opt on a prior install (item 2 gate (i)).
    previous_marker = _read_browsers_path_marker()
    ensure_playwright_browsers(box_class=box_class)
    ok = reconcile_playwright_mcp_file(box_class=box_class)
    # #1058 rework-2 (item 2): once the shared /opt is the resolved path, the
    # account reaps its OWN redundant per-user chromium copy — ONLY when provably
    # safe (marker already /opt, no live server env on the per-user path, no live
    # fd/cwd). Runs AFTER the marker/MCP env are written, so a reap can never
    # outrun the surfaces that point readers at /opt. Never on /opt itself.
    if resolved_browsers_path(box_class) == OPT_MS_PLAYWRIGHT:
        _reap_per_user_copy_if_safe(
            PLAYWRIGHT_BROWSER_CACHE, previous_marker=previous_marker)
    return ok
