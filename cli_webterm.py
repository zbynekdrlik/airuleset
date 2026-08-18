"""airuleset web terminal gateway (#555) — dev1-only ttyd + tailscale-serve brána.

Nahrádza ownerových ~21 SSH tabov vo Windows Termináli. Owner otvorí jednu
tailnet adresu v Chrome, prihlási sa, a dostane dashboard so VŠETKÝMI tmux
sessions flotily (dev1, dev2, gk, subdev streamy) — klik = pripojený do žijúcej
tmux session daného targetu. Sessions žijú v tmuxoch na targetoch (už dnes), táto
web vrstva je len perzistentné, PC-nezávislé okno do nich.

Architektúra (viď design komentár na #555):
  browser --tailscale(WireGuard)--> dev1 `tailscale serve`
      :80   /  -> statický dashboard (webterm-dashboard.html, generovaný z inventára)
      :7682 /  -> ttyd(127.0.0.1:7681, basic-auth) -> connect -> ssh -t -> tmux targetu
Tailnet-only (serve = "tailnet only", off-tailnet nedosiahnuteľné), basic-auth
login na shell porte. Inventár generovaný z `_deployable_hosts()` + dev1, NIKDY
ručný zoznam. Provisioning dev1-only (`os.uname().nodename == "dev1"`), systemd
--user unit podľa vzoru `setup_filedrop_service()`, `ttyd` v RUNTIME_DEPS.

Dve úlohy modulu, oddelené aby CONNECT cesta (beží per-terminal-open, ttyd child)
mala minimálne importy: (1) INVENTORY/PROVISIONING (install-time, dev1) generuje
inventár + dashboard + unit; (2) CONNECT (`python3 cli_webterm.py webterm-connect
<id>`) validuje id proti allowlistu a execne ssh/tmux. Žiadny `import airuleset`
na connect ceste.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
SECRETS_DIR = Path.home() / ".secrets"

# dev1 is the SINGLE gateway: it already holds every fleet ssh identity + hosts
# filedrop/watchdog. Gated exactly like the coordinator-only watchdog jobs
# (16/19/35) — `os.uname().nodename == "dev1"`.
WEBTERM_GATEWAY_HOST = "dev1"
# ttyd binds loopback only; tailscale serve fronts it (tailnet-only).
WEBTERM_TTYD_PORT = 7681
# Two tailnet-only serve ports (root-mounted each, so no reverse-proxy base-path
# strip issue — tailscale serve `--set-path /x` STRIPS the prefix, which breaks
# ttyd's `window.location`-derived ws URL; a root mount forwards unstripped):
WEBTERM_DASH_SERVE_PORT = 80      # http://dev1.<tailnet>.ts.net/       -> dashboard
WEBTERM_TTYD_SERVE_PORT = 7682    # http://dev1.<tailnet>.ts.net:7682/  -> ttyd
# The owner tmux session group on his own boxes (dev1/dev2). A stream account's
# own session is named after its unix user (#264 whoami auto-attach convention).
OWNER_GROUP = "zbynek"
WEBTERM_LOGIN_USER = "zbynek"

WEBTERM_INVENTORY_PATH = CLAUDE_DIR / "webterm-inventory.json"
WEBTERM_DASHBOARD_PATH = CLAUDE_DIR / "webterm-dashboard.html"
WEBTERM_LAUNCH_PATH = CLAUDE_DIR / "airuleset-webterm-ttyd.sh"
WEBTERM_CRED_PATH = SECRETS_DIR / "webterm_credential"
WEBTERM_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-ttyd.service"
WEBTERM_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-ttyd.service.template"


# --------------------------------------------------------------------------- #
# Inventory — generated from _deployable_hosts() + dev1 (never a hand list).
# --------------------------------------------------------------------------- #

def _sanitize_id(name):
    """A URL-safe, `@`/`.`-free session id for the dashboard `?arg=` link and the
    connect allowlist. Deterministic + collision-free across the fleet names
    (montalu@subdev -> montalu-subdev, admin@forestshop-dev ->
    admin-forestshop-dev, dev2 -> dev2)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def webterm_inventory():
    """The session inventory: dev1 (localhost) + every `_deployable_hosts()`
    entry. Per-entry `preferred` tmux group = the unix user for a stream account
    (in AUTHORITY_BY_USER, the #264 whoami convention), else `zbynek` (owner
    group). Read the fleet table via the airuleset facade (test-patchable)."""
    import airuleset  # facade: AUTHORITY_BY_USER (patched by ~30 tests)
    from cli_remote import _deployable_hosts
    stream_users = set(airuleset.AUTHORITY_BY_USER)
    entries = [{
        "id": "dev1",
        "label": "dev1 (localhost)",
        "kind": "owner",
        "local": True,
        "host": None,
        "user": None,
        "identity": None,
        "preferred": OWNER_GROUP,
    }]
    for h in _deployable_hosts():
        user = h["user"]
        is_stream = user in stream_users
        entries.append({
            "id": _sanitize_id(h["name"]),
            "label": h["name"],
            "kind": "stream" if is_stream else "owner",
            "local": False,
            "host": h["host"],
            "user": user,
            "identity": h.get("identity"),
            "preferred": user if is_stream else OWNER_GROUP,
        })
    return entries


# --------------------------------------------------------------------------- #
# Connect — the ttyd child entrypoint. Validates the id against the generated
# inventory allowlist, then execs the ssh/tmux command. No `import airuleset`.
# --------------------------------------------------------------------------- #

# Universal tmux attach, parameterized only by `$P` (the trusted inventory
# `preferred`): exact session name -> group survivor -> the single existing
# session -> create `$P`. Robust for grouped (zbynek-4 / group zbynek) AND
# standalone (david, gk "0") sessions without per-box knowledge. Reused for
# local (dev1) and remote (ssh) alike.
_ATTACH_BODY = (
    'if tmux has-session -t "=$P" 2>/dev/null; then exec tmux new-session -t "$P"; fi; '
    'S=$(tmux list-sessions -F "#{session_group}::#{session_name}" 2>/dev/null '
    "| awk -F '::' -v g=\"$P\" '$1==g{print $2; exit}'); "
    'if [ -n "$S" ]; then exec tmux new-session -t "$S"; fi; '
    'N=$(tmux list-sessions -F "#{session_name}" 2>/dev/null); '
    'if [ "$(printf %s "$N" | grep -c .)" = "1" ]; then exec tmux attach -t "$N"; fi; '
    'exec tmux new-session -A -s "$P"'
)


def _remote_command(preferred):
    """The single shell-command string run on the target (or locally for dev1).
    `preferred` is shell-quoted; the rest is a fixed body — no user input reaches
    the shell beyond the allowlisted, inventory-derived `preferred` value."""
    return "P=" + shlex.quote(preferred) + "; " + _ATTACH_BODY


def _ssh_interactive_prefix(entry):
    """Match the deploy loop's identity rule (cli_remote.py cmd_push /
    provision_subdev_soniox_key, ~lines 204-223 / 770-795): `identity` present ->
    `ssh -i <identity>`; else -> `sshpass -p newlevel ssh` (default-key/shared-
    password path). Interactive variant: force a PTY (-t), never write
    known_hosts, fast connect timeout so a dead host fails visibly. DRIFT GUARD:
    the identity-vs-sshpass DECISION is the same rule those two sites use — if the
    fleet's auth convention ever changes (password rotation, a new scheme), both
    those sites AND this one must move together; `test_webterm.py::
    test_identity_decision_matches_deploy_loop` fails on a decision drift."""
    common = ["-o", "StrictHostKeyChecking=no",
              "-o", "UserKnownHostsFile=/dev/null",
              "-o", "ConnectTimeout=10", "-t"]
    identity = entry.get("identity")
    if identity:
        return ["ssh", "-i", os.path.expanduser(identity)] + common
    return ["sshpass", "-p", "newlevel", "ssh"] + common


def build_connect_argv(entry):
    """The argv the ttyd child execs for `entry`: a local `sh -c` (dev1) or an
    interactive `ssh -t <target> <remote-command>`."""
    cmd = _remote_command(entry["preferred"])
    if entry.get("local"):
        return ["sh", "-c", cmd]
    prefix = _ssh_interactive_prefix(entry)
    target = "%s@%s" % (entry["user"], entry["host"])
    return prefix + [target, cmd]


def _load_inventory(path=None):
    path = path or WEBTERM_INVENTORY_PATH
    return json.loads(Path(path).read_text(encoding="utf-8"))


def connect_main(argv, inventory_path=None):
    """ttyd child entrypoint. `argv[0]` is the session id ttyd appended from the
    URL (`?arg=<id>`, via ttyd's `-a`). Validate it against the generated
    inventory (allowlist — an unknown id is refused, never interpolated into a
    shell), then exec. Returns a non-zero rc only on a refusal/error; on success
    it execs and never returns."""
    if not argv:
        sys.stderr.write("webterm: no session id given\r\n")
        return 2
    sid = argv[0]
    try:
        inv = _load_inventory(inventory_path)
    except (OSError, ValueError) as e:
        sys.stderr.write("webterm: inventory unreadable: %s\r\n" % e)
        return 2
    entry = next((e for e in inv if e.get("id") == sid), None)
    if entry is None:
        sys.stderr.write("webterm: unknown session id %r\r\n" % sid)
        return 2
    cmd = build_connect_argv(entry)
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:  # unreachable on success (execvp replaces the process)
        sys.stderr.write("webterm: exec failed: %s\r\n" % e)
        return 127
    return 0  # pragma: no cover


# --------------------------------------------------------------------------- #
# Dashboard — a static HTML page generated from the inventory. Owner boxes
# grouped first, then subdev streams. Each card opens the terminal in a NEW tab
# (one browser tab per session — the direct analogue of one Windows Terminal
# tab per SSH session).
# --------------------------------------------------------------------------- #

def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _dashboard_card(entry, ttyd_base):
    href = "%s/?arg=%s" % (ttyd_base, _html_escape(entry["id"]))
    label = _html_escape(entry["label"])
    sub = "localhost" if entry.get("local") else _html_escape(
        "%s@%s" % (entry["user"], entry["host"]))
    return ('<a class="card" href="%s" target="_blank" rel="noopener">'
            '<span class="lbl">%s</span><span class="sub">%s</span></a>'
            % (href, label, sub))


def render_dashboard_html(inventory, ttyd_base=None):
    """The dashboard HTML. `ttyd_base` is the tailnet origin of the ttyd serve
    port (e.g. `http://dev1.tail547bba.ts.net:7682`); each card links there with
    `?arg=<id>`."""
    ttyd_base = (ttyd_base or "").rstrip("/")
    owners = [e for e in inventory if e.get("kind") == "owner"]
    streams = [e for e in inventory if e.get("kind") == "stream"]

    def section(title, items):
        if not items:
            return ""
        cards = "\n".join(_dashboard_card(e, ttyd_base) for e in items)
        return ('<h2>%s</h2>\n<div class="grid">\n%s\n</div>\n'
                % (_html_escape(title), cards))

    body = (section("Moje boxy", owners)
            + section("Subdev streamy", streams))
    return _DASHBOARD_TEMPLATE % {
        "count": len(inventory),
        "body": body,
    }


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>work.newlevel.media — fleet terminal</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #0d1117; color: #e6edf3;
  font: 15px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
header { padding: 22px 26px 6px; }
header h1 { margin: 0; font-size: 20px; font-weight: 600; }
header p { margin: 6px 0 0; color: #8b949e; font-size: 13px; }
main { padding: 8px 26px 40px; }
h2 { margin: 26px 0 10px; font-size: 13px; text-transform: uppercase;
  letter-spacing: .08em; color: #8b949e; font-weight: 600; }
.grid { display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
.card { display: flex; flex-direction: column; gap: 3px;
  padding: 13px 15px; border: 1px solid #30363d; border-radius: 9px;
  background: #161b22; text-decoration: none; color: inherit;
  transition: border-color .12s, background .12s; }
.card:hover { border-color: #2f81f7; background: #1c2330; }
.card .lbl { font-weight: 600; font-size: 15px; }
.card .sub { color: #8b949e; font-size: 12px; }
footer { padding: 0 26px 30px; color: #6e7681; font-size: 12px; }
</style>
</head>
<body>
<header>
<h1>work.newlevel.media</h1>
<p>%(count)d tmux sessions flotily · klik = nový tab pripojený do tmuxu · tailnet-only</p>
</header>
<main>
%(body)s</main>
<footer>Prihlásenie sa vyžiada pri prvom termináli. Sessions žijú na targetoch — prežijú reboot aj iné PC.</footer>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Provisioning — dev1-only. Renders the systemd --user ttyd unit + launcher +
# inventory + dashboard, enables the unit, configures tailscale serve. Mirrors
# `setup_filedrop_service()`.
# --------------------------------------------------------------------------- #

def _ensure_credential():
    """`zbynek:<random>` basic-auth credential, generated once, mode 600, in
    ~/.secrets. Returns the `user:pass` string. (ttyd's `-c` shows it in `ps` to
    local unix users — a documented residual; the real boundary is tailnet-only
    + the login, and the box's only other unix users are managed/locked.)"""
    import secrets
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)  # never world-traversable
    if WEBTERM_CRED_PATH.exists():
        cred = WEBTERM_CRED_PATH.read_text(encoding="utf-8").strip()
        if cred and ":" in cred:
            return cred
    cred = "%s:%s" % (WEBTERM_LOGIN_USER, secrets.token_hex(16))
    # Atomically create mode-600 (O_EXCL over a fresh temp, then rename) so the
    # 128-bit shell credential is NEVER briefly world-readable under the umask.
    tmp = WEBTERM_CRED_PATH.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (cred + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(str(tmp), str(WEBTERM_CRED_PATH))
    return cred


# `-O`/--check-origin: reject a cross-origin websocket upgrade (CSWSH). VERIFIED
# live through tailscale serve — a raw ws upgrade with a foreign Origin gets no
# 101 (serve surfaces a 502), a same-origin one gets 101, and the legit browser
# terminal keeps working. This is defence-in-depth on top of the PRIMARY CSWSH
# defence, which holds even without `-O`: ttyd requires a fixed AuthToken in the
# ws init message (an empty/wrong token spawns no shell), and its `/token`
# endpoint sends NO CORS headers, so a browser attacker's cross-origin
# `fetch("/token")` is blocked from READING the token — it can never construct a
# valid ws init. `-c` basic-auth gates both /token and the ws.
_LAUNCH_TEMPLATE = """#!/usr/bin/env bash
# airuleset-managed (#555) — do NOT edit; regenerate via `python3 airuleset.py install`.
# Reads the basic-auth credential from a mode-600 file (keeps it out of the unit
# file) and execs ttyd on loopback; tailscale serve fronts it tailnet-only.
set -euo pipefail
cred="$(cat %(cred_path)s)"
exec ttyd -p %(ttyd_port)d -i 127.0.0.1 -a -W -O -c "$cred" \\
  python3 %(repo_dir)s/cli_webterm.py webterm-connect
"""


def render_webterm_launch_script():
    return _LAUNCH_TEMPLATE % {
        "cred_path": shlex.quote(str(WEBTERM_CRED_PATH)),
        "ttyd_port": WEBTERM_TTYD_PORT,
        "repo_dir": shlex.quote(str(REPO_DIR)),
    }


def _render_webterm_unit():
    tmpl = WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_LAUNCH_PATH))


def _ts_dns_name(run=None):
    """dev1's tailscale MagicDNS name (e.g. dev1.tail547bba.ts.net), for the
    printed URL. Falls back to the bare hostname."""
    run = run or subprocess.run
    try:
        out = run(["tailscale", "status", "--json"],
                  capture_output=True, text=True, timeout=5)
        name = json.loads(out.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
        return name or WEBTERM_GATEWAY_HOST
    except Exception:
        return WEBTERM_GATEWAY_HOST


def _ttyd_serve_base(run=None):
    return "http://%s:%d" % (_ts_dns_name(run=run), WEBTERM_TTYD_SERVE_PORT)


def _configure_tailscale_serve(run=None):
    """Idempotent tailnet-only serve: dashboard at `/` on :80, ttyd at `/` on
    :7682. Two ROOT mounts (no `--set-path`) so nothing strips ttyd's path.
    Needs the tailscale operator grant (`tailscale set --operator=$USER`, done
    once); prints the manual command on failure rather than claiming success."""
    run = run or subprocess.run
    steps = [
        ["tailscale", "serve", "--bg", "--http=%d" % WEBTERM_DASH_SERVE_PORT,
         str(WEBTERM_DASHBOARD_PATH)],
        ["tailscale", "serve", "--bg", "--http=%d" % WEBTERM_TTYD_SERVE_PORT,
         "http://127.0.0.1:%d" % WEBTERM_TTYD_PORT],
    ]
    ok = True
    for cmd in steps:
        r = run(cmd, capture_output=True, text=True)
        if getattr(r, "returncode", 1) != 0:
            ok = False
            print("  webterm: tailscale serve step failed — run manually:\n"
                  "    %s\n    %s" % (" ".join(cmd), (getattr(r, "stderr", "") or "").strip()),
                  file=sys.stderr)
    return ok


def is_webterm_gateway():
    return os.uname().nodename == WEBTERM_GATEWAY_HOST


def setup_webterm_service(run=None, configure_serve=True):
    """dev1-only: provision + enable the ttyd web-terminal gateway. Idempotent.
    Returns True on success, False on any skip/failure (never raises for a
    non-fatal step — mirrors setup_filedrop_service)."""
    if not is_webterm_gateway():
        return False  # webterm is dev1-only (the single gateway)
    run = run or subprocess.run
    if shutil.which("ttyd") is None:
        # dev1-local install (ttyd is deliberately NOT in the fleet-wide
        # RUNTIME_DEPS — #555: it would cry wolf on the ~19 no-sudo subdev
        # accounts where webterm never runs). dev1 has sudo.
        run(["sudo", "-n", "apt-get", "install", "-y", "ttyd"],
            capture_output=True, text=True)
        if shutil.which("ttyd") is None:
            print("  webterm: ttyd not installed and `sudo -n apt-get install "
                  "ttyd` failed — skipping the gateway", file=sys.stderr)
            return False
    from cli_filedrop_watchdog import _run_systemctl, _whoami

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    inv = webterm_inventory()
    WEBTERM_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    WEBTERM_DASHBOARD_PATH.write_text(
        render_dashboard_html(inv, ttyd_base=_ttyd_serve_base(run=run)),
        encoding="utf-8")
    _ensure_credential()
    WEBTERM_LAUNCH_PATH.write_text(render_webterm_launch_script(), encoding="utf-8")
    os.chmod(WEBTERM_LAUNCH_PATH, 0o755)

    WEBTERM_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_SERVICE_DEST.write_text(_render_webterm_unit(), encoding="utf-8")

    try:
        run(["loginctl", "enable-linger", _whoami()], capture_output=True, text=True)
    except Exception as e:
        print("  webterm: loginctl enable-linger skipped (%s)" % e, file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print("  webterm: systemctl daemon-reload FAILED: %s" % (err or "").strip(),
              file=sys.stderr)
    rc, _o, err = _run_systemctl(["enable", "--now", "webterm-ttyd.service"])
    if rc != 0:
        print("  webterm: systemctl enable --now FAILED: %s\n"
              "    Manual: XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user "
              "enable --now webterm-ttyd.service" % (err or "").strip(),
              file=sys.stderr)
        return False
    # `enable --now` is a no-op for an already-running service, so a re-install
    # that changed the launcher/unit (e.g. new ttyd flags) needs an explicit
    # restart to take effect — mirrors setup_filedrop_service (#555 review).
    rc, _o, err = _run_systemctl(["restart", "webterm-ttyd.service"])
    if rc != 0:
        print("  webterm: systemctl restart FAILED (new ttyd flags may not be "
              "live): %s" % (err or "").strip(), file=sys.stderr)

    serve_ok = _configure_tailscale_serve(run=run) if configure_serve else True
    if serve_ok:
        # NOTE (#555 review): the dashboard port (:80) is served UNAUTHENTICATED
        # over the tailnet — it discloses fleet session labels + user@host to any
        # tailnet peer (largely `tailscale status`-discoverable already; the shell
        # port :7682 stays basic-auth-gated). Accepted residual; tighten with a
        # tailscale ACL restricting dev1:80 to owner devices if desired.
        print("  webterm: gateway live — http://%s/ (tailnet-only, login user %r)"
              % (_ts_dns_name(run=run), WEBTERM_LOGIN_USER))
    else:
        print("  webterm: ttyd service is up but tailscale serve is NOT "
              "configured — the gateway is not reachable until serve is set "
              "(see the manual command above)", file=sys.stderr)
    return True


def maybe_setup_webterm():
    """Install-time entry point (cmd_install). No-op off dev1."""
    return setup_webterm_service()


def main(argv):
    if argv and argv[0] == "webterm-connect":
        return connect_main(argv[1:])
    sys.stderr.write("usage: cli_webterm.py webterm-connect <session-id>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
