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
  ``playwright:<ver>/<browser>``   browsers in ``/opt/ms-playwright``; ``pinned``
                                   is ``cli_playwright_mcp.PLAYWRIGHT_PW_VERSION``

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
                         "rust-target:<triple>, playwright:<ver>/<browser>"
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
    return {"pipx": sorted(pipx.items()), "rust": rust,
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


def render_system_step(spec, *, rust_root=RUST_ROOT, pipx_home=PIPX_HOME,
                       pipx_bin=PIPX_BIN_DIR, pw_dir=PLAYWRIGHT_DIR):
    """Root bootstrap step 12 (the LAST step): install the baseline + the
    account's tools system-wide (idempotent) and make them read-only for
    everyone. The keyword arguments exist for the tests (a fake root); the
    bootstrap always renders the defaults."""
    p = plan(spec)
    cargo_home = rust_root + "/cargo"
    pipx = (_PIPX_HEAD.format(pipx_home=shlex.quote(pipx_home),
                              pipx_bin=shlex.quote(pipx_bin))
            + _pipx_lines(p["pipx"]))
    rust = (_RUST_HEAD.format(rustup_home=shlex.quote(rust_root + "/rustup"),
                              cargo_home=shlex.quote(cargo_home),
                              rust_bin=shlex.quote(cargo_home + "/bin"),
                              rust_root=shlex.quote(rust_root))
            + _rust_lines(p["rust"], cargo_home + "/bin")
            + "chmod -R a+rX,go-w %s\n" % shlex.quote(rust_root))
    return ("\n# 12. Shared project toolchain, system-wide + read-only (#1201)\n"
            + _subshell("pipx venvs (ruff, …)", pipx)
            + _subshell("shared rustup", rust)
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
