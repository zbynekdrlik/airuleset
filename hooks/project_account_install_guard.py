"""Classifier of hooks/block-project-account-local-installs.sh (#1201).

A project account (#1184, a ``cli_account_bootstrap.SERVICE_ACCOUNTS`` member)
uses the toolchain airuleset installs ONCE per box (``cli_project_toolchain``).
It never installs its own copy into its home: that is how fohmixer pulled ~2 GB
of rustup, Playwright browsers and pipx venvs onto a disk at 90 % (owner,
30.9.2026). This module names the per-account install shapes; the bash wrapper
blocks them only when the invoking user is a declared project account.

Blocked (per command, after the sibling hooks' prefix strip + ``bash -c``
recursion + heredoc-body strip):

  * ``rustup`` install / update / default <x> / toolchain|component|target
    changes / self / set / override, ``rustup-init``, and a curl/wget of
    ``sh.rustup.rs``;
  * ``pipx install|inject|install-all`` without ``--global``, ``uv tool install``;
  * ``pip install --user`` or ``--break-system-packages`` (``pip``, ``pip3``,
    ``python -m pip``) — the two ways a pip install lands in ``~/.local``;
  * ``playwright install`` via ``npx``/``bunx``/``npm exec``/``pnpm dlx``/
    ``yarn dlx``, bare ``playwright`` or ``python -m playwright``;
  * ``npm i -g`` (``--global``, ``--location=global``), ``yarn global add``,
    ``pnpm add -g``;
  * ``cargo install`` / ``cargo binstall``.

Allowed: a project venv inside the repo (``.venv/bin/pip install -r …``,
``python3 -m venv .venv``), a local ``npm install``, ``npx playwright test``,
``rustup show``/``which``, ``cargo fmt``. Deliberately NO bypass marker: a tool
the account needs is added by airuleset (the owner's hard stop on duplication).

``main()`` prints the block message on STDOUT and exits 2 on a block (the bash
wrapper routes it to stderr, where the model sees it), 0 otherwise; any
internal error FAILS OPEN (exit 0), like every sibling classifier.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_guard_shell import ASSIGN_RE, split_segments  # noqa: E402

WRAP_NOARG = {"sudo", "env", "nohup", "command", "builtin", "time", "exec"}
WRAP_OPTS = {"timeout", "nice", "ionice", "stdbuf"}
SHELLS = {"bash", "sh", "zsh", "dash"}
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!"}
_DASH_C = re.compile(r"^-[A-Za-z]*c$")
_PIP_RE = re.compile(r"^pip(3(\.\d+)?)?$")
_PY_RE = re.compile(r"^python(3(\.\d+)?)?$")
_PW_PKG = re.compile(r"^(@playwright/test|playwright(-core)?)(@.*)?$")
_NPM_INSTALL = {"i", "in", "ins", "inst", "insta", "instal", "install", "isnt",
                "isnta", "isntal", "isntall", "add"}
_RUSTUP_ALWAYS = {"install", "update", "self", "set", "uninstall"}
_RUSTUP_SUB = {"toolchain": {"install", "add", "uninstall", "remove", "link",
                             "update"},
               "component": {"add", "remove"}, "target": {"add", "remove"},
               "override": {"set", "unset", "add", "remove"}}
_RUSTUP_URLS = ("sh.rustup.rs", "rustup-init", "static.rust-lang.org/rustup")


def _tokens(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if ASSIGN_RE.match(t) or t in WRAP_NOARG or t in KEYWORDS:
            i += 1
        elif t in WRAP_OPTS:
            i += 1
            while i < len(tk) and tk[i].startswith("-"):
                i += 1
            if t in ("timeout", "nice") and i < len(tk) and re.match(r"^\d", tk[i]):
                i += 1
        elif t.startswith("-") and i and tk[i - 1] in WRAP_NOARG:
            i += 1          # `env -i`, `sudo -u x` (the user is then a positional)
        else:
            break
    return tk[i:]


def _positionals(args):
    return [a for a in args if not a.startswith("-")]


def _rustup(args):
    pos = _positionals(args)
    if not pos:
        return ""
    sub = pos[0]
    if sub in _RUSTUP_ALWAYS or (sub == "default" and len(pos) > 1):
        return "rustup " + sub
    if sub in _RUSTUP_SUB and len(pos) > 1 and pos[1] in _RUSTUP_SUB[sub]:
        return "rustup %s %s" % (sub, pos[1])
    return ""


def _pip(args):
    pos = _positionals(args)
    if pos[:1] == ["install"] and ({"--user", "--break-system-packages"} & set(args)):
        return "pip install --user"
    return ""


def _playwright_install(args):
    """``args`` follow a runner (npx, …): the first positional is the package,
    the next one its command."""
    rest, i = [], 0
    while i < len(args):
        if args[i] in ("-p", "--package"):
            i += 2
            continue
        rest.append(args[i])
        i += 1
    pos = _positionals(rest)
    if len(pos) >= 2 and _PW_PKG.match(pos[0]) and pos[1] == "install":
        return "playwright install"
    return ""


def _npm(args):
    pos = _positionals(args)
    if not pos:
        return ""
    if pos[0] == "exec":
        return _playwright_install(args[args.index("exec") + 1:])
    glob = ("-g" in args or "--global" in args or "--location=global" in args
            or any(a == "--location" and b == "global" for a, b in zip(args, args[1:])))
    return "npm install -g" if pos[0] in _NPM_INSTALL and glob else ""


def command_reason(tk):
    """The block label of ONE command's tokens ('' = allowed)."""
    if not tk:
        return ""
    base, args = os.path.basename(tk[0]), tk[1:]
    pos = _positionals(args)
    if base == "rustup":
        return _rustup(args)
    if base.startswith("rustup-init"):
        return "rustup-init"
    if base in ("curl", "wget") and any(u in a for a in args for u in _RUSTUP_URLS):
        return "curl sh.rustup.rs"
    if base == "pipx":
        return ("pipx " + pos[0] if pos[:1] and pos[0] in (
            "install", "inject", "install-all") and "--global" not in args else "")
    if base == "uv":
        return "uv tool install" if pos[:2] == ["tool", "install"] else ""
    if _PIP_RE.match(base):
        return _pip(args)
    if _PY_RE.match(base) and args[:2] == ["-m", "pip"]:
        return _pip(args[2:])
    if _PY_RE.match(base) and args[:2] == ["-m", "playwright"]:
        return "playwright install" if args[2:3] == ["install"] else ""
    if base == "playwright":
        return "playwright install" if pos[:1] == ["install"] else ""
    if base in ("npx", "bunx"):
        return _playwright_install(args)
    if base in ("pnpm", "yarn") and pos[:1] == ["dlx"]:
        return _playwright_install(args[args.index("dlx") + 1:])
    if base == "npm":
        return _npm(args)
    if base == "yarn" and pos[:2] == ["global", "add"]:
        return "yarn global add"
    if base == "pnpm" and pos[:1] and pos[0] in _NPM_INSTALL and (
            "-g" in args or "--global" in args):
        return "pnpm add -g"
    if base == "cargo":
        sub = [a for a in pos if not a.startswith("+")]
        return "cargo " + sub[0] if sub[:1] and sub[0] in ("install", "binstall") else ""
    return ""


def _shell_script(tk):
    if not tk or os.path.basename(tk[0]) not in SHELLS:
        return None
    for j in range(1, len(tk)):
        if tk[j] == "-c" or _DASH_C.match(tk[j]):
            return tk[j + 1] if j + 1 < len(tk) else None
    return None


def strip_heredoc_bodies(text):
    """Drop heredoc BODIES (a ticket comment or commit message quoting a
    banned shape is documentation, not a command) — the siblings' shape."""
    lines, out, i = text.split("\n"), [], 0
    rx = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = rx.search(line)
        if not m:
            continue
        while i < len(lines):
            body = lines[i].lstrip("\t") if "<<-" in line else lines[i]
            i += 1
            if body == m.group(2):
                break
    return "\n".join(out)


def classify(script, _depth=0):
    """The label of the FIRST blocked command in ``script`` ('' = allowed)."""
    for seg, _term in split_segments(strip_heredoc_bodies(script)):
        tk = _strip_prefix(_tokens(seg))
        inner = _shell_script(tk)
        if inner is not None:
            label = classify(inner, _depth + 1) if _depth < 4 else ""
        else:
            label = command_reason(tk)
        if label:
            return label
    return ""


def is_project_account(user, repo_dir):
    """True iff ``user`` is a declared project account (``SERVICE_ACCOUNTS``)."""
    sys.path.insert(0, repo_dir)
    import cli_account_bootstrap as bootstrap
    return user in bootstrap.SERVICE_ACCOUNTS


MESSAGE = """\
BLOCKED (#1201): `{label}` would install a developer tool into this project
account's own home. Project accounts share ONE system-wide toolchain that
airuleset installs per box (ruff, rustup with rustfmt/clippy under /opt/rust,
Playwright browsers in /opt/ms-playwright); a per-account copy is how fohmixer
filled ~2 GB of a nearly full disk (owner, 30.9.2026).

nástroj pridá airuleset — založ tiket:
  gh issue create -R zbynekdrlik/airuleset --title "project account {user}: add <tool>" --body-file <file>
(name the tool + version; it lands in SERVICE_ACCOUNTS["{user}"]["tools"] and
the next root bootstrap installs it system-wide).

Allowed: a project venv INSIDE the repo for the project's own tests
(.venv/bin/pip install -r …), a local `npm install`, `npx playwright test`.
"""


def main(argv):
    """``argv`` = [user, repo_dir]; the PreToolUse payload is on stdin."""
    try:
        user, repo_dir = argv[1], argv[2]
        cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
        if not cmd or not is_project_account(user, repo_dir):
            return 0
        label = classify(cmd)
    except Exception:   # noqa: BLE001 — a classifier malfunction fails OPEN
        return 0
    if not label:
        return 0
    sys.stdout.write(MESSAGE.format(label=label, user=user))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
