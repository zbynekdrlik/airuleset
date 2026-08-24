"""airuleset claude-CLI + ffmpeg/ffprobe static-binary installers — cluster L
sub-split (#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as the earlier
H/I/J/K/L1/L2 CLI leaves, the A-F watchdog leaves, and the L-tmux
cli_tmux_provisioning.py leaf). airuleset.py keeps a single
`from cli_binary_installers import (...)` re-export at the old definition
site, so cmd_install's binary-install steps, ensure_marketplace_registered /
setup_caveman / ensure_playwright_browsers / setup_managed_plugins (all of
which call _claude_cli_env), and every test's
`airuleset.ensure_ffmpeg_static_binary(...)` / `airuleset._claude_cli_env(...)`
/ `airuleset.FFMPEG_STATIC_*` reference all keep working unchanged.

This region FETCHES-AND-INSTALLS the external runtime binaries a (frequently
no-sudo) managed box needs: the `claude` CLI (via its curl installer) and the
`ffmpeg`/`ffprobe` static binaries (johnvansickle amd64-static tarball into
~/.local/bin).

Deliberately SELF-CONTAINED: stdlib only at module level (`os`, `shutil`,
`sys`, `Path`). `subprocess` and `shlex` are imported LOCALLY inside the
functions that use them, verbatim — the same never-top-level-import-subprocess
idiom airuleset.py itself follows. NO top-level
`import airuleset` — this leaf has ZERO airuleset.py-resident outbound
COUPLINGS: the only names the region referenced from module scope were its own
four ffmpeg constants (FFMPEG_STATIC_URL / FFMPEG_STATIC_BIN_DIR /
FFMPEG_STATIC_DEST / FFPROBE_STATIC_DEST), which are ffmpeg-only and move here
with the functions, resolving internally.
"""

import os
import shutil
import sys
from pathlib import Path


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
            _claude_installer_argv(),
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


def _claude_installer_argv():
    """The one bash argv that runs Anthropic's official user-space installer.
    Factored out so `ensure_claude_cli_installed()` (install when NOTHING
    resolves) and `ensure_claude_native_userspace()` (FORCE the native install
    when only a root/system copy resolves, #659) run byte-identical installer
    commands. `set -o pipefail` so a curl failure is not masked by bash's own
    exit code (the LAST command in the pipe)."""
    return ["bash", "-c",
            "set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash"]


# --- #659: native user-space claude, never a root-npm copy -----------------
# The system bin dirs to look for a NON-user-writable `claude` in -- a root
# `npm -g` install (the spinbike-vps case: `/usr/bin/claude` that cannot
# self-update, `Auto-update failed: no write permission to npm prefix`) or any
# other system-path copy. Deliberately EXCLUDES ~/.local/bin so the native
# install is never mistaken for a system copy to remove.
_CLAUDE_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"


def _native_claude_present() -> bool:
    """True iff the native user-space claude (`~/.local/bin/claude`) resolves
    and is executable -- the install we WANT (self-updating in user space).
    Uses `shutil.which` scoped to `~/.local/bin` so a dangling symlink from a
    partial install reads as absent, same discipline as `_claude_cli_installed`."""
    local_bin = str(Path.home() / ".local" / "bin")
    return shutil.which("claude", path=local_bin) is not None


def _system_claude_path():
    """The path of a `claude` that resolves on a SYSTEM bin dir OUTSIDE $HOME
    (a root-npm / system install -- the un-self-updatable kind, #659), or None.
    Resolves the symlink so a `/usr/bin/claude -> ../lib/node_modules/...`
    still counts as outside $HOME; a copy whose real target lives under $HOME
    (an unusual layout) is NOT a system copy and returns None."""
    p = shutil.which("claude", path=_CLAUDE_SYSTEM_PATH)
    if not p:
        return None
    home = os.path.realpath(str(Path.home()))
    if os.path.realpath(p).startswith(home + os.sep):
        return None
    return p


def _remove_system_claude(syspath, run):
    """Best-effort removal of a root/system `claude` at `syspath`, gated on
    passwordless sudo (`sudo -n true` -- never prompts). NEVER raises. Only the
    resolved system-bin path (from `_system_claude_path`, not user input) is
    passed to `sudo -n rm -f` via argv (no shell), so there is no injection
    surface. The caller has already confirmed the native `~/.local` install is
    present, so this can never leave the box with no `claude`."""
    if not shutil.which("sudo"):
        print("    claude: system copy %s left in place (no sudo to remove it)"
              % syspath)
        return
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as ex:  # noqa: BLE001 -- best-effort, never break install
        print("    ⚠ claude: sudo probe error, leaving system copy %s: %r"
              % (syspath, ex), file=sys.stderr)
        return
    if getattr(probe, "returncode", 1) != 0:
        print("    claude: root/system copy %s left in place (no passwordless "
              "sudo) -- the native ~/.local install already shadows it on the "
              "managed PATH; remove it manually to stop a non-managed shell's "
              "auto-update error" % syspath)
        return
    try:
        r = run(["sudo", "-n", "rm", "-f", syspath], capture_output=True,
                text=True, timeout=15)
    except Exception as ex:  # noqa: BLE001
        print("    ⚠ claude: removing system copy %s failed: %r"
              % (syspath, ex), file=sys.stderr)
        return
    if getattr(r, "returncode", 1) == 0:
        print("    claude: removed root/system copy %s (native ~/.local "
              "install is now authoritative)" % syspath)
    else:
        print("    ⚠ claude: removing system copy %s failed (rc=%s): %s"
              % (syspath, r.returncode, (r.stderr or "").strip()[:200]),
              file=sys.stderr)


def ensure_claude_native_userspace(env: dict = None, run=None):
    """#659: make the resolvable `claude` the NATIVE user-space install
    (`~/.local/bin/claude`, self-updating in user space), never a root-npm /
    system copy that cannot self-update (the spinbike-vps `no write permission
    to npm prefix` error). Runs AFTER `ensure_claude_cli_installed()` in
    cmd_install, fleet-wide, best-effort, non-fatal:

      * A box already fully native (no system-path `claude`) -> pure no-op.
      * A box with ONLY a root/system copy -> lay down the native install
        (`ensure_claude_cli_installed` skipped it precisely because the system
        copy resolved), then remove the system copy (gated on passwordless
        sudo, so a no-sudo subdev account is a safe no-op).
      * The native install is NEVER removed, and the system copy is removed
        ONLY once the native one is confirmed present -- so a box can never be
        left with no `claude`.
    """
    import subprocess
    e = env or _claude_cli_env()
    run = run or subprocess.run
    syspath = _system_claude_path()
    if syspath is None:
        return  # fully native or claude-less -- nothing to migrate
    if not _native_claude_present():
        # a root/system claude resolved, so ensure_claude_cli_installed()
        # short-circuited; FORCE the native user-space install now.
        try:
            r = run(_claude_installer_argv(), capture_output=True, text=True,
                    timeout=180, env=e)
            if r.returncode == 0 and _native_claude_present():
                print("    claude: native ~/.local user-space install laid "
                      "down (was root/system-only: %s)" % syspath)
        except Exception as ex:  # noqa: BLE001 -- non-fatal
            print("    ⚠ claude: native user-space install skipped (%s) -- "
                  "leaving system copy %s in place" % (ex, syspath),
                  file=sys.stderr)
    if not _native_claude_present():
        print("    ⚠ claude: native user-space install still missing -- "
              "leaving system copy %s in place (never removing the only "
              "claude)" % syspath, file=sys.stderr)
        return
    _remove_system_claude(syspath, run)


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


# tsl0922/ttyd's per-release STATIC x86_64 asset. The GitHub `/releases/latest/
# download/<asset>` form is an UNPINNED "latest" redirect (302 to the newest
# release's asset) — owner decision 2026-08-23 chose exactly this (#614 Approach
# 2): "always the newest static version, no checksum", precisely the
# FFMPEG_STATIC_URL precedent above. `curl -fsSL` follows the redirect (`-L`).
TTYD_STATIC_URL = ("https://github.com/tsl0922/ttyd/releases/latest/download/"
                   "ttyd.x86_64")
# ~/.local/bin, NOT ~/bin — the ONLY directory on PATH inside a real Bash tool
# call / the managed launcher's own fix-up, AND the exact dir the webterm-david
# ttyd unit's self-contained PATH env prepends (#614). Same rule the ffmpeg
# dest follows (#275 review MAJOR-2).
TTYD_STATIC_BIN_DIR = Path.home() / ".local" / "bin"
TTYD_STATIC_DEST = TTYD_STATIC_BIN_DIR / "ttyd"


def _ttyd_available(dest: Path = None) -> bool:
    """True iff ttyd is already reachable — our own ~/.local/bin/ttyd is a
    genuinely executable file, or `ttyd` is otherwise on PATH (dev1's system
    /usr/bin/ttyd, or a prior/hand install here — the #612 go-live installed it
    by hand). Single-binary sibling of `_ffmpeg_available`; reuses
    `_binary_reachable`'s dest-or-PATH check so a hand-installed ttyd is
    recognized as already-done and never reinstalled over."""
    d = dest or TTYD_STATIC_DEST
    return _binary_reachable(d, "ttyd")


def ensure_ttyd_static_binary(dest: Path = None):
    """Best-effort, time-boxed, non-fatal static-ttyd install into `~/.local/bin`
    (#614, owner decision 2026-08-23 — Approach 2: unpinned "latest" static
    binary, NO checksum, EXACTLY the `ensure_ffmpeg_static_binary` precedent):
    the DAVID webterm gateway on subdev has NO sudo, so `ttyd` — which the ttyd
    unit's launcher `exec`s — must live as a user-space static binary in
    ~/.local/bin. #612 go-live placed it BY HAND; this makes a fresh subdev
    re-provision self-sufficient (the gate `prerequisites_ready()` REQUIRES ttyd
    present, so an absent binary would no-op provisioning forever).

    Same shape as `ensure_ffmpeg_static_binary()`, minus the tar step: ttyd
    ships as a SINGLE static binary asset (ttyd.x86_64), so ONE `curl -o`
    downloads it — no extraction. The download+chmod happen inside a SCRATCH
    subdir of the FINAL destination's OWN parent (never the final path
    directly, and never a separate /tmp — same filesystem is what makes the
    final `mv` an atomic rename), only `mv`d into place once the file is
    confirmed non-empty and chmod'd: a hard-killed subprocess (this call's own
    180s `timeout=` sends SIGKILL, which no shell `trap` can intercept) must
    never leave a truncated-but-"executable" binary at the final path that
    `_ttyd_available()` would then report as done FOREVER (#275 review MAJOR-3,
    same guarantee for ttyd).

    Harmless no-op wherever ttyd already resolves (dev1's system /usr/bin/ttyd,
    or an already-completed prior run here — including the #612 hand install).
    Only ever dispatched on the DAVID-profile host (subdev) via
    `setup_webterm_david_service`, so it never runs on dev1/dev2/gatekeeper."""
    import subprocess
    import shlex
    d = dest or TTYD_STATIC_DEST
    if _ttyd_available(d):
        return
    script = (
        "set -o pipefail; "
        "mkdir -p %s && "
        "TMP=$(mktemp -d -p %s) && trap 'rm -rf \"$TMP\"' EXIT && "
        "curl -fsSL %s -o \"$TMP/ttyd.new\" && "
        "[ -s \"$TMP/ttyd.new\" ] && "
        "chmod 755 \"$TMP/ttyd.new\" && "
        "mv \"$TMP/ttyd.new\" %s"
    ) % (shlex.quote(str(d.parent)), shlex.quote(str(d.parent)),
         shlex.quote(TTYD_STATIC_URL), shlex.quote(str(d)))
    try:
        r = subprocess.run(["bash", "-c", script],
                            capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and _ttyd_available(d):
            print("    ttyd: installed static ttyd -> %s" % d.parent)
        else:
            print("    ⚠ ttyd static install failed (rc=%s): %s\n"
                  "    Install manually: curl -fsSL %s -o %s && chmod 755 %s"
                  % (r.returncode, (r.stderr or r.stdout).strip()[:200],
                     TTYD_STATIC_URL, d, d),
                  file=sys.stderr)
    except Exception as e:
        print("    ⚠ ttyd static install skipped (%s) — "
              "install manually: curl -fsSL %s -o %s && chmod 755 %s"
              % (e, TTYD_STATIC_URL, d, d), file=sys.stderr)
