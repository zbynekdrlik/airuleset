"""The shared system toolchain of the project accounts (#1201).

Owner, 30.9.2026: a project account (#1184) must never install its own copy of
a developer tool — fohmixer, the first one, had pulled ~2 GB of rustup,
Playwright browsers and pipx venvs into its home on a disk at 90 %. So the
toolchain is DECLARED here and installed ONCE per box, system-wide and
read-only for every account, by the #1184 root bootstrap:

  * ``PROJECT_TOOLCHAIN`` — the baseline every project account gets: ruff (a
    pipx venv), the stable Rust toolchain with rustfmt + clippy (a shared
    rustup), and the Playwright chromium the managed MCP server is pinned to;
  * ``SERVICE_ACCOUNTS[acct]["tools"]`` — what one project adds or pins on top
    (fohmixer: its ``rust-toolchain.toml`` channel with its components and
    target, the ruff its CI runs, the playwright its e2e lockfile resolves).

Tool grammar (one string per entry, validated fail-closed by ``validate_tools``
before anything is rendered into a root script):

  ``pipx:<pkg>[==<ver>]``          a pipx venv under ``/opt/pipx``, its entry
                                   points in ``/usr/local/bin``
  ``rust-toolchain:<channel>``     a toolchain in the shared ``/opt/rust``
  ``rust-component:<name>``        added to the account's toolchains (or stable)
  ``rust-target:<triple>``         likewise
  ``cargo:<crate>[@<ver>]``        ``cargo install`` into the shared cargo bin
  ``playwright:<ver>/<browser>``   browsers in ``/opt/ms-playwright``; ``pinned``
                                   is ``cli_playwright_mcp.PLAYWRIGHT_PW_VERSION``
  ``binaryen:version_<N>``         the official binaryen release (#1328), checked
                                   against a digest pinned in ``BINARYEN_SHA256``,
                                   ``wasm-opt`` linked into ``/usr/local/bin``

#1328: step 12 also installs ``airuleset-rust-stable-update.timer`` (weekly),
which runs ``rustup update stable`` on ``/opt/rust`` once no build of the
stable toolchain runs, under the same lock step 12's rust part takes.

pipx: Ubuntu 24.04 ships pipx 1.4.3, and ``pipx install --global`` arrived in
1.5. The render sets ``PIPX_HOME``/``PIPX_BIN_DIR`` to exactly the locations
``--global`` uses by default, which works on every pipx version.

The account side is ``render_account_env_step``: a root-owned
``/etc/airuleset/project-toolchain.sh`` (PATH with ``~/.local/bin`` first,
``RUSTUP_HOME``, ``PLAYWRIGHT_BROWSERS_PATH``) sourced from the account's
``~/.profile`` (login shells, ``bash -lc`` included) and ``~/.bashrc``
(interactive non-login shells). ``hooks/block-project-account-local-installs.sh``
refuses the per-account install shapes; ``cli_account_verify`` proves every
tool resolves outside the account's home.

Stdlib only; ``cli_playwright_mcp`` is imported lazily (the pin's home).
"""
import re
import shlex
import textwrap

RUST_ROOT = "/opt/rust"
RUSTUP_HOME = RUST_ROOT + "/rustup"
CARGO_HOME = RUST_ROOT + "/cargo"
RUST_BIN = CARGO_HOME + "/bin"
PIPX_HOME = "/opt/pipx"
PIPX_BIN_DIR = "/usr/local/bin"
PLAYWRIGHT_DIR = "/opt/ms-playwright"      # == cli_playwright_mcp.OPT_MS_PLAYWRIGHT
ENV_FILE = "/etc/airuleset/project-toolchain.sh"
RC_MARK_START = "# >>> airuleset project toolchain (#1201) >>>"
RC_MARK_END = "# <<< airuleset project toolchain (#1201) <<<"
PINNED = "pinned"
# The project tmux session's first pane (a shell word): an explicit LOGIN bash —
# fohmixer's pane had no ~/.local/bin on PATH (#1201).
TMUX_LOGIN_SHELL = shlex.quote("exec bash -l")
DEFAULT_TOOLCHAIN = "stable"
BINARYEN_ROOT = "/opt/binaryen"
UNIT_DIR = "/etc/systemd/system"
SBIN_DIR = "/usr/local/sbin"
RUST_LOCK = "/run/lock/airuleset-opt-rust.lock"
RUST_UPDATE = "airuleset-rust-stable-update"
# #1328: one digest per binaryen release and machine, from the release's own
# published asset digest. A version without a pin here is refused.
BINARYEN_SHA256 = {
    "version_131": {"x86_64": "b5bf1f0eaf17c63ee588ff7a5954dc8f6ce2c26989051c66f24dfe9ece3e46db"},
}
BINARYEN_URL = ("https://github.com/WebAssembly/binaryen/releases/download/"
                "{ver}/binaryen-{ver}-{arch}-linux.tar.gz")

# The baseline every project account gets (the ticket's "declared baseline").
PROJECT_TOOLCHAIN = (
    "pipx:ruff",
    "rust-toolchain:" + DEFAULT_TOOLCHAIN,
    "rust-component:rustfmt",
    "rust-component:clippy",
    "playwright:%s/chromium" % PINNED,
)

_KIND_RE = {
    "pipx": re.compile(r"(?P<pkg>[A-Za-z0-9][A-Za-z0-9._-]*)"
                       r"(==(?P<ver>[0-9][A-Za-z0-9.+!-]*))?"),
    "rust-toolchain": re.compile(r"[a-z0-9][a-z0-9.-]*"),
    "rust-component": re.compile(r"[a-z0-9][a-z0-9-]*"),
    "rust-target": re.compile(r"[a-z0-9_][a-z0-9_.-]*"),
    "cargo": re.compile(r"(?P<crate>[A-Za-z0-9][A-Za-z0-9_-]*)"
                        r"(@(?P<ver>[0-9][A-Za-z0-9.+-]*))?"),
    "binaryen": re.compile(r"version_[0-9]+"),
    "playwright": re.compile(r"(?P<ver>%s|\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?)/"
                             r"(?P<browser>chromium|chromium-headless-shell|"
                             r"firefox|webkit)" % PINNED),
}


def is_project_account(user, marker_dir=None):
    """True when ``user`` is a LIVE #1184 project account on this box: declared
    in ``SERVICE_ACCOUNTS`` AND adopted by its root render, which writes
    ``/etc/airuleset/project-accounts/<user>``. A declared account the render
    has not adopted (claudy@controller, which manages fleet OAuth) keeps the
    legacy behaviour (#1201, push 0.1.517)."""
    import os
    import cli_account_bootstrap
    import cli_account_hardening
    if user not in cli_account_bootstrap.SERVICE_ACCOUNTS:
        return False
    return os.path.isfile(os.path.join(
        marker_dir or cli_account_hardening.MARKER_DIR, user))


def runs_as_project_account():
    """True when this process runs as a live project account
    (``is_project_account``); an unreadable identity is False."""
    try:
        import os
        import pwd
        return is_project_account(pwd.getpwuid(os.getuid()).pw_name)
    except Exception:   # noqa: BLE001 — never raise from an identity probe
        return False


def parse_tool(entry):
    """``(kind, match)`` of one tool entry; ValueError on anything else."""
    kind, sep, value = entry.partition(":") if isinstance(entry, str) else ("", "", "")
    rx = _KIND_RE.get(kind)
    m = rx.fullmatch(value) if (rx and sep) else None
    if not m:
        raise ValueError("tool %r is not one of pipx:<pkg>[==<ver>], "
                         "rust-toolchain:<channel>, rust-component:<name>, "
                         "rust-target:<triple>, cargo:<crate>[@<ver>], "
                         "binaryen:version_<N>, playwright:<ver>/<browser>"
                         % (entry,))
    return kind, m


def _pipx_pins(tools):
    """{pkg: version} of the PINNED pipx entries in ``tools``."""
    out = {}
    for t in tools:
        kind, m = parse_tool(t)
        if kind == "pipx" and m.group("ver"):
            out[m.group("pkg").lower()] = m.group("ver")
    return out


def validate_tools(spec, account=None, declared=()):
    """Every problem with a declaration's ``tools`` ([] = valid). A pipx venv
    is ONE copy per box, so two accounts on one host may not pin the same
    package to different versions."""
    tools = spec.get("tools", ())
    if not isinstance(tools, (list, tuple)):
        return ["tools must be a list"]
    errs = []
    for t in tools:
        try:
            parse_tool(t)
        except ValueError as e:
            errs.append(str(e))
    if errs:
        return errs
    if len(set(tools)) != len(tools):
        errs.append("tools lists an entry twice")
    errs += _binaryen_problems(tools, account, declared, spec.get("host", "controller"))
    mine = _pipx_pins(tools)
    for other, raw in sorted(dict(declared).items()):
        if other == account or raw.get("host", "controller") != spec.get(
                "host", "controller"):
            continue
        try:
            theirs = _pipx_pins(raw.get("tools") or ())
        except ValueError:
            continue            # the other declaration reports its own error
        for pkg, ver in sorted(mine.items()):
            if theirs.get(pkg, ver) != ver:
                errs.append("pipx:%s==%s conflicts with %s's pin %s on the same "
                            "host (one shared venv per box)"
                            % (pkg, ver, other, theirs[pkg]))
    return errs


def _binaryen(tools):
    return [parse_tool(t)[1].group(0) for t in tools if t.startswith("binaryen:")]


def _binaryen_problems(tools, account, declared, host):
    """#1328: a pinned digest per version, and ONE wasm-opt per box."""
    mine = _binaryen(tools)
    errs = ["binaryen:%s has no pinned digest in BINARYEN_SHA256" % v
            for v in mine if v not in BINARYEN_SHA256]
    if len(mine) > 1:
        errs.append("tools declares more than one binaryen version")
    for other, raw in sorted(dict(declared).items()):
        if other == account or raw.get("host", "controller") != host:
            continue
        try:
            theirs = set(_binaryen(raw.get("tools") or ()))
        except ValueError:
            continue
        errs += ["binaryen:%s conflicts with %s's binaryen:%s on the same host "
                 "(one /usr/local/bin/wasm-opt per box)" % (v, other, t)
                 for v in mine for t in sorted(theirs) if t != v]
    return errs


def plan(spec):
    """What the root render installs for this account: the baseline plus its
    ``tools`` (an account pin overrides the baseline's unpinned package).

    ``{"pipx": [(pkg, ver|None)], "rust": [(toolchain, [components],
    [targets])], "playwright": [(version, [browsers])]}``; the account's
    components/targets go to its own toolchains, or to stable when it
    declares none."""
    from cli_playwright_mcp import PLAYWRIGHT_PW_VERSION
    base = [parse_tool(t) for t in PROJECT_TOOLCHAIN]
    own = [parse_tool(t) for t in spec.get("tools") or ()]
    pipx = {}
    for kind, m in base + own:
        if kind == "pipx":
            pkg = m.group("pkg").lower()
            if m.group("ver") or pkg not in pipx:
                pipx[pkg] = m.group("ver")

    def _vals(entries, kind):
        return [m.group(0) for k, m in entries if k == kind]

    base_chains = _vals(base, "rust-toolchain")
    own_chains = [c for c in _vals(own, "rust-toolchain") if c not in base_chains]
    base_comps = _vals(base, "rust-component")
    own_comps = _vals(own, "rust-component")
    own_targets = _vals(own, "rust-target")
    gets_own = set(own_chains or [DEFAULT_TOOLCHAIN])
    rust = []
    for chain in base_chains + own_chains:
        extra = chain in gets_own
        comps = base_comps + [c for c in own_comps if extra and c not in base_comps]
        rust.append((chain, comps, own_targets if extra else []))
    browsers = {}
    for kind, m in base + own:
        if kind == "playwright":
            ver = PLAYWRIGHT_PW_VERSION if m.group("ver") == PINNED else m.group("ver")
            if m.group("browser") not in browsers.setdefault(ver, []):
                browsers[ver].append(m.group("browser"))
    cargo = sorted((m.group("crate"), m.group("ver")) for k, m in own if k == "cargo")
    return {"pipx": sorted(pipx.items()), "rust": rust, "cargo": cargo,
            "binaryen": sorted({m.group(0) for k, m in own if k == "binaryen"}),
            "playwright": sorted(browsers.items())}


# --------------------------------------------------------------------------- #
# the root render. Two steps, so a box that lacks a prerequisite (pipx, npx)
# still gets its checkout, tmux session and gh shim: step 8b (the env file +
# the account's rc wiring — no download, cannot fail on a missing tool) runs
# BEFORE the checkout and the tmux session; step 12 (the system-wide installs)
# runs LAST and fails the script loudly. Every root subshell `cd /`s first
# (#1190: never run code from the invoker's cwd) and python runs isolated (-I).
# --------------------------------------------------------------------------- #
_PIPX_HEAD = """\
command -v pipx >/dev/null 2>&1 || {{ echo "ERROR: pipx is missing on this box — install the distro pipx package as root, then re-run (#1201)" >&2; exit 1; }}
export PIPX_HOME={pipx_home} PIPX_BIN_DIR={pipx_bin} PIPX_MAN_DIR=/usr/local/share/man
_tc_pipx_version() {{   # the installed version of pipx venv $1, '' when absent
    {{ pipx list --json 2>/dev/null || echo '{{}}'; }} | python3 -I -c 'import json, sys
v = json.load(sys.stdin).get("venvs", {{}}).get(sys.argv[1], {{}})
print(v.get("metadata", {{}}).get("main_package", {{}}).get("package_version", ""))' "$1"
}}
"""

_RUST_HEAD = """\
export RUSTUP_HOME={rustup_home} CARGO_HOME={cargo_home}
if [ ! -x {rust_bin}/rustup ]; then
    install -d -m 0755 {rust_root}
    RUSTUP_INIT=$(mktemp)
    trap 'rm -f "$RUSTUP_INIT"' EXIT
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o "$RUSTUP_INIT"
    sh "$RUSTUP_INIT" -y --no-modify-path --profile minimal --default-toolchain none
fi
"""

_ENV_BODY = """\
# airuleset project-account toolchain env (#1201) — MANAGED by the #1184
# account bootstrap; do not edit. Sourced from every project account's
# ~/.profile and ~/.bashrc. The tools are installed ONCE per box, read-only for
# the accounts; an account never installs its own copy (the PreToolUse hook
# block-project-account-local-installs.sh refuses it).
case ":$PATH:" in *":{rust_bin}:"*) ;; *) PATH="{rust_bin}:$PATH" ;; esac
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac
export PATH
export RUSTUP_HOME={rustup_home}
export PLAYWRIGHT_BROWSERS_PATH={pw_dir}
"""


def render_env_file(*, rust_bin=RUST_BIN, rustup_home=RUSTUP_HOME,
                    pw_dir=PLAYWRIGHT_DIR):
    """The root-owned env file every project account sources (POSIX sh:
    ``~/.profile`` is also read by ``sh``)."""
    return _ENV_BODY.format(rust_bin=rust_bin, rustup_home=rustup_home,
                            pw_dir=pw_dir)


def _subshell(label, body):
    """One root part: a ``umask 022`` subshell run from ``/`` (a failure still
    aborts the ``set -e`` script)."""
    return "# %s\n(\numask 022\ncd /\n%s)\n" % (label, body)


def _pipx_lines(pipx):
    lines = []
    for pkg, ver in pipx:
        q = shlex.quote(pkg)
        if ver:
            spec = shlex.quote("%s==%s" % (pkg, ver))
            lines.append('have=$(_tc_pipx_version %s)\n'
                         'if [ -z "$have" ]; then pipx install %s\n'
                         'elif [ "$have" != %s ]; then pipx install --force %s\n'
                         'fi\necho "  pipx %s: $(_tc_pipx_version %s)"\n'
                         % (q, spec, shlex.quote(ver), spec, pkg, q))
        else:
            lines.append('[ -n "$(_tc_pipx_version %s)" ] || pipx install %s\n'
                         'echo "  pipx %s: $(_tc_pipx_version %s)"\n'
                         % (q, q, pkg, q))
    return "".join(lines)


def _rust_lines(rust, rust_bin):
    r = shlex.quote(rust_bin + "/rustup")
    lines = []
    for chain, comps, targets in rust:
        c = shlex.quote(chain)
        args = "".join(" --component %s" % shlex.quote(x) for x in comps)
        args += "".join(" --target %s" % shlex.quote(x) for x in targets)
        lines.append("%s toolchain install %s --no-self-update --profile minimal%s\n"
                     % (r, c, args))
        if comps:
            lines.append("%s component add --toolchain %s %s\n"
                         % (r, c, " ".join(shlex.quote(x) for x in comps)))
        if targets:
            lines.append("%s target add --toolchain %s %s\n"
                         % (r, c, " ".join(shlex.quote(x) for x in targets)))
    lines.append("%s default %s\n" % (r, shlex.quote(DEFAULT_TOOLCHAIN)))
    return "".join(lines)


def _playwright_lines(browsers, pw_dir):
    """Every declared version into ONE browsers dir. PLAYWRIGHT_SKIP_BROWSER_GC
    keeps one version's install from collecting another's builds once root's
    npx cache for it is gone. The completeness check is the resolver's own
    marker list, so the render and ``_has_pinned_chromium_build`` agree."""
    from cli_playwright_mcp import PLAYWRIGHT_CHROMIUM_BUILD, pinned_build_markers
    d = shlex.quote(pw_dir)
    out = ['command -v npx >/dev/null 2>&1 || { echo "ERROR: npx is missing — the '
           'shared Playwright browsers need node/npx on root\'s PATH (#1201)" >&2; '
           'exit 1; }\n', "install -d -m 0755 %s\n" % d]
    for ver, names in browsers:
        out.append("PLAYWRIGHT_BROWSERS_PATH=%s PLAYWRIGHT_SKIP_BROWSER_GC=1 npx -y "
                   "%s install --with-deps %s\n"
                   % (d, shlex.quote("playwright@" + ver),
                      " ".join(shlex.quote(b) for b in names)))
    out.append("chmod -R a+rX,go-w %s\n" % d)
    for rel in pinned_build_markers():
        marker = shlex.quote("%s/%s" % (pw_dir, rel))
        out.append('[ -f %s ] || { echo "ERROR: %s has no complete pinned chromium '
                   'build %s (#1201)" >&2; exit 1; }\n'
                   % (marker, pw_dir, PLAYWRIGHT_CHROMIUM_BUILD))
    return "".join(out)


def _cargo_lines(tools, cargo_bin):
    """``cargo install`` per declared tool. An unpinned built binary stays (#1275).
    A pinned one always runs: cargo is a no-op on the same version and replaces any
    other, so a pin bump reaches the box (dstack cargo-mutants 25 to 27)."""
    return "".join(
        "%s%s/cargo +%s install --locked %s%s\n"
        % ("" if ver else "[ -x %s/%s ] || " % (cargo_bin, crate), cargo_bin, DEFAULT_TOOLCHAIN,
           "--version %s " % shlex.quote(ver) if ver else "", shlex.quote(crate))
        for crate, ver in tools)


# --------------------------------------------------------------------------- #
# #1328: binaryen wasm-opt + the weekly /opt/rust stable update
# --------------------------------------------------------------------------- #
def _binaryen_lines(versions, root, bin_dir):
    """Download each pinned release, verify its digest, keep ``bin/wasm-opt``
    (statically linked) under ``<root>/<version>`` and link it into
    ``bin_dir``. A machine without a pinned digest fails loud."""
    out = []
    for ver in versions:
        d = shlex.quote("%s/%s" % (root, ver))
        arches = "".join("    %s) sha=%s ;;\n" % (a, shlex.quote(h))
                         for a, h in sorted(BINARYEN_SHA256[ver].items()))
        url = shlex.quote(BINARYEN_URL.format(ver=ver, arch="ARCH"))
        out.append(
            'case "$(%s/wasm-opt --version 2>/dev/null)" in\n'
            '  *"(%s)"*) ;;\n'
            '  *) arch=$(uname -m)\n'
            '    case "$arch" in\n%s'
            '    *) echo "ERROR: no pinned binaryen %s digest for $arch (#1328)" >&2; exit 1 ;;\n'
            '    esac\n'
            '    t=$(mktemp -d)\n'
            '    trap \'rm -rf "$t"\' EXIT\n'
            '    url=%s\n'
            '    curl --proto \'=https\' --tlsv1.2 -sSfL "${url//ARCH/$arch}" -o "$t/b.tgz"\n'
            '    echo "$sha  $t/b.tgz" | sha256sum -c --quiet -\n'
            '    tar -xzf "$t/b.tgz" -C "$t" %s\n'
            '    install -d -m 0755 %s/bin\n'
            '    install -m 0755 "$t/"%s %s/bin/wasm-opt\n'
            '    ln -sfn %s/bin/wasm-opt %s/wasm-opt ;;\n'
            'esac\n'
            'echo "  wasm-opt: $(%s/wasm-opt --version)"\n'
            % (shlex.quote(bin_dir), ver, arches, ver, url,
               shlex.quote("binaryen-%s/bin/wasm-opt" % ver), d,
               shlex.quote("binaryen-%s/bin/wasm-opt" % ver), d, d,
               shlex.quote(bin_dir), shlex.quote(bin_dir)))
    return "".join(out)


_UPDATE_SCRIPT = r"""#!/bin/bash
# @NAME@ -- MANAGED by airuleset (#1328), written by the project-account root
# bootstrap step 12; do not edit. Keeps the shared stable toolchain under
# @ROOT@ current. Waits while a build of the stable toolchain runs (up to
# MAX_WAIT), takes the lock step 12 takes, then `rustup update stable`.
set -euo pipefail
export RUSTUP_HOME=@ROOT@/rustup CARGO_HOME=@ROOT@/cargo
RUSTUP=@ROOT@/cargo/bin/rustup
STEP=${AIRULESET_RUST_WAIT_STEP:-300}
MAX_WAIT=${AIRULESET_RUST_MAX_WAIT:-21600}
PROC=${AIRULESET_RUST_PROC:-/proc}
building() {   # prints one pid running a stable-toolchain build tool
    local e t
    for e in "$PROC"/[0-9]*/exe; do
        t=$(readlink "$e" 2>/dev/null) || continue
        case "$t" in
            @ROOT@/rustup/toolchains/stable-*/bin/cargo|@ROOT@/rustup/toolchains/stable-*/bin/rustc|\
            @ROOT@/rustup/toolchains/stable-*/bin/rustdoc|@ROOT@/rustup/toolchains/stable-*/bin/clippy-driver)
                e=${e%/exe}; echo "${e##*/} $t"; return 0 ;;
        esac
    done
    return 1
}
exec 9>"@LOCK@"
waited=0
while :; do
    if busy=$(building); then
        why="a stable build is running ($busy)"
    elif flock -n 9; then
        break
    else
        why="another /opt/rust writer holds @LOCK@"
    fi
    if [ "$waited" -ge "$MAX_WAIT" ]; then
        echo "@NAME@: SKIPPED this run -- $why after ${waited}s" >&2
        exit 0
    fi
    echo "@NAME@: $why -- waiting ${STEP}s"
    sleep "$STEP"
    waited=$((waited + STEP))
done
before=$("$RUSTUP" run stable rustc --version 2>/dev/null || echo none)
"$RUSTUP" update stable --no-self-update
chmod -R a+rX,go-w @ROOT@
echo "@NAME@: stable $before -> $("$RUSTUP" run stable rustc --version)"
"""

_UPDATE_SERVICE = """\
# MANAGED by airuleset (#1328): rustup update stable on the shared @ROOT@
[Unit]
Description=airuleset: keep @ROOT@ stable current (#1328)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=8h
ExecStart=@SCRIPT@
"""

_UPDATE_TIMER = """\
# MANAGED by airuleset (#1328): weekly; Persistent catches a missed run
[Unit]
Description=airuleset: weekly @ROOT@ stable update (#1328)

[Timer]
OnCalendar=Mon *-*-* 04:30:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
"""


def render_rust_update_files(*, rust_root=RUST_ROOT, lock=RUST_LOCK, sbin_dir=SBIN_DIR):
    """``{"script", "service", "timer"}`` text of the #1328 weekly update."""
    script = "%s/%s" % (sbin_dir, RUST_UPDATE)
    sub = (("@NAME@", RUST_UPDATE), ("@ROOT@", rust_root), ("@LOCK@", lock),
           ("@SCRIPT@", script))
    out = {}
    for key, text in (("script", _UPDATE_SCRIPT), ("service", _UPDATE_SERVICE),
                      ("timer", _UPDATE_TIMER)):
        for a, b in sub:
            text = text.replace(a, b)
        out[key] = text
    return out


def _rust_update_lines(rust_root, lock, unit_dir, sbin_dir):
    """Write the script + units only when they changed, then enable the timer."""
    files = render_rust_update_files(rust_root=rust_root, lock=lock, sbin_dir=sbin_dir)
    out = ['command -v systemctl >/dev/null 2>&1 || { echo "ERROR: systemctl is '
           'missing -- the /opt/rust stable timer needs systemd (#1328)" >&2; exit 1; }\n',
           "install -d -m 0755 %s %s\n" % (shlex.quote(unit_dir), shlex.quote(sbin_dir)),
           '_tc_put() {   # $1 = dest, $2 = mode; content on stdin, written if changed\n'
           '    local t; t=$(mktemp "$1.XXXXXX"); cat > "$t"; chmod "$2" "$t"\n'
           '    if cmp -s "$t" "$1"; then rm -f "$t"; else mv -f "$t" "$1"; echo "  $1: written"; fi\n'
           '}\n']
    for key, dest, mode in (
            ("script", "%s/%s" % (sbin_dir, RUST_UPDATE), "0755"),
            ("service", "%s/%s.service" % (unit_dir, RUST_UPDATE), "0644"),
            ("timer", "%s/%s.timer" % (unit_dir, RUST_UPDATE), "0644")):
        out.append("_tc_put %s %s << 'RUST_STABLE_UPDATE_EOF'\n%sRUST_STABLE_UPDATE_EOF\n"
                   % (shlex.quote(dest), mode, files[key]))
    out.append("systemctl daemon-reload\nsystemctl enable --now %s.timer\n"
               'echo "  %s.timer: $(systemctl is-enabled %s.timer)"\n'
               % (RUST_UPDATE, RUST_UPDATE, RUST_UPDATE))
    return "".join(out)


def render_system_step(spec, *, rust_root=RUST_ROOT, pipx_home=PIPX_HOME,
                       pipx_bin=PIPX_BIN_DIR, pw_dir=PLAYWRIGHT_DIR,
                       binaryen_root=BINARYEN_ROOT, unit_dir=UNIT_DIR,
                       sbin_dir=SBIN_DIR, rust_lock=RUST_LOCK):
    """Root bootstrap step 12 (the LAST step): install the baseline + the
    account's tools system-wide (idempotent) and make them read-only for
    everyone. The keyword arguments exist for the tests (a fake root); the
    bootstrap always renders the defaults."""
    p = plan(spec)
    cargo_home = rust_root + "/cargo"
    pipx = (_PIPX_HEAD.format(pipx_home=shlex.quote(pipx_home),
                              pipx_bin=shlex.quote(pipx_bin))
            + _pipx_lines(p["pipx"]))
    lock = ('exec 8>%s\nflock -w 1800 8 || { echo "ERROR: %s is held -- the weekly '
            'stable update is running (#1328)" >&2; exit 1; }\n'
            % (shlex.quote(rust_lock), rust_lock))
    rust = lock + (_RUST_HEAD.format(rustup_home=shlex.quote(rust_root + "/rustup"),
                              cargo_home=shlex.quote(cargo_home),
                              rust_bin=shlex.quote(cargo_home + "/bin"),
                              rust_root=shlex.quote(rust_root))
            + _rust_lines(p["rust"], cargo_home + "/bin")
            + _cargo_lines(p["cargo"], cargo_home + "/bin")
            + "chmod -R a+rX,go-w %s\n" % shlex.quote(rust_root))
    return ("\n# 12. Shared project toolchain, system-wide + read-only (#1201)\n"
            + _subshell("pipx venvs (ruff, …)", pipx)
            + _subshell("shared rustup", rust)
            + (_subshell("binaryen wasm-opt (#1328)",
                         _binaryen_lines(p["binaryen"], binaryen_root, pipx_bin))
               if p["binaryen"] else "")
            + _subshell("weekly /opt/rust stable update (#1328)",
                        _rust_update_lines(rust_root, rust_lock, unit_dir, sbin_dir))
            + _subshell("shared Playwright browsers",
                        _playwright_lines(p["playwright"], pw_dir)))


# --------------------------------------------------------------------------- #
# step 8b: the env file (root) + the account's rc wiring (AS the account)
# --------------------------------------------------------------------------- #
_ACCOUNT_BODY = """set -euo pipefail
umask 022
mkdir -p "$HOME/.local/bin"
rcs="$HOME/.profile $HOME/.bashrc"
if [ -f "$HOME/.bash_profile" ]; then rcs="$rcs $HOME/.bash_profile"; fi
for rc in $rcs; do
    touch "$rc"
    if grep -qxF {start} "$rc"; then
        echo "  $rc: toolchain env block present"
    else
        printf '\\n%s\\n%s\\n%s\\n' {start} {source} {end} >> "$rc"
        echo "  $rc: toolchain env block added"
    fi
done
"""


def render_account_body(env_file=ENV_FILE):
    """The shell body run AS the account: ``~/.local/bin`` exists and every rc
    file sources the root-owned env file (one marker block, idempotent)."""
    source = "if [ -r %s ]; then . %s; fi" % (env_file, env_file)
    return _ACCOUNT_BODY.format(start=shlex.quote(RC_MARK_START),
                                source=shlex.quote(source),
                                end=shlex.quote(RC_MARK_END))


def render_account_env_step(spec, *, env_file=ENV_FILE, rust_root=RUST_ROOT,
                            pw_dir=PLAYWRIGHT_DIR):
    """Root bootstrap step 8b: write the root-owned env file (atomic, 0644),
    then wire the account's shells to it AS the account (root never writes
    through a path the account controls). Runs BEFORE step 10 creates the tmux
    session, so its first pane already starts from this environment."""
    del spec    # every project account gets the same wiring
    env_dir = env_file.rsplit("/", 1)[0]
    env = ("install -d -m 0755 %s\n"
           "t=$(mktemp %s)\n"
           "cat > \"$t\" << 'PROJECT_TOOLCHAIN_ENV_EOF'\n%sPROJECT_TOOLCHAIN_ENV_EOF\n"
           "chmod 0644 \"$t\"\nmv -f \"$t\" %s\n"
           "echo \"  toolchain env: $(ls -l %s)\"\n"
           % (shlex.quote(env_dir), shlex.quote(env_dir + "/.project-toolchain.XXXXXX"),
              render_env_file(rust_bin=rust_root + "/cargo/bin",
                              rustup_home=rust_root + "/rustup", pw_dir=pw_dir),
              shlex.quote(env_file), shlex.quote(env_file)))
    return ("\n# 8b. Shell env of the shared toolchain — #1201\n"
            + _subshell("the root-owned env file every project account sources", env)
            + "# the account's rc files source it (as the account)\n"
            "runuser -l \"$ACCOUNT\" -c %s\n"
            % shlex.quote(render_account_body(env_file)))


def render_next_steps(spec):
    """The bootstrap's LAST printed step: ``accounts verify`` (#1201)."""
    del spec
    return textwrap.dedent("""\
        echo "  LAST (REQUIRED, #1201), on the controller: python3 ~/devel/airuleset/airuleset.py accounts verify $ACCOUNT — the migration is NOT done, and nobody is told to work in the account, until every line prints OK"
    """)
