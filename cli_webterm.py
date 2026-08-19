"""airuleset web terminal gateway (#555) — dev1-only ttyd + tailscale-serve brána.

Nahrádza ownerových ~21 SSH tabov vo Windows Termináli. Owner otvorí jednu
tailnet adresu v Chrome, prihlási sa, a dostane dashboard so VŠETKÝMI tmux
sessions flotily (dev1, dev2, gk, subdev streamy) — klik = pripojený do žijúcej
tmux session daného targetu. Sessions žijú v tmuxoch na targetoch (už dnes), táto
web vrstva je len perzistentné, PC-nezávislé okno do nich.

Architektúra (viď design komentáre na #555 + #579):
  cross-node browser --tailscale(WireGuard)--> dev1 tailscale IP <ip>
      :8080 /  -> statický tabbed dashboard (webterm-dash/index.html, http.server)
      :7682 /  -> ttyd(<ip>:7682, basic-auth) -> connect -> ssh -t -> tmux targetu
#579: obe služby bindnú PRIAMO na dev1 tailscale IP (dynamicky `tailscale ip -4`,
nikdy hardcode/0.0.0.0) — `tailscale serve` matchoval len MagicDNS meno uzla,
takže raw-IP request cross-node 404'oval. Dashboard je single-page Windows-
Terminal tabbed UI (iframe/session, krátke aliasy). Tailnet-only, basic-auth
login na shell porte. Inventár generovaný z `_deployable_hosts()` + dev1, NIKDY
ručný zoznam. Provisioning dev1-only (`os.uname().nodename == "dev1"`), systemd
--user unity podľa vzoru `setup_filedrop_service()`, `ttyd` inštalovaný dev1-lokálne.

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
# #579: ttyd binds dev1's tailscale IP DIRECTLY on this port (no reverse proxy,
# no serve) — the browser reaches it at http://<tailscale-ip>:7682/ from any
# tailnet node by raw IP. (Was 7681 loopback fronted by `tailscale serve`, whose
# handler matched only the MagicDNS name → raw-IP request 404'd cross-node.)
WEBTERM_TTYD_PORT = 7682
# The static tabbed dashboard is served by `python3 -m http.server` bound to the
# same tailscale IP on this port (a systemd --user unit). Non-privileged, so no
# CAP_NET_BIND_SERVICE is needed (a privileged :80 would).
WEBTERM_DASH_PORT = 8080
# The owner tmux session group on his own boxes (dev1/dev2). A stream account's
# own session is named after its unix user (#264 whoami auto-attach convention).
OWNER_GROUP = "zbynek"
WEBTERM_LOGIN_USER = "zbynek"

WEBTERM_INVENTORY_PATH = CLAUDE_DIR / "webterm-inventory.json"
# #579: the dashboard is a directory served by http.server (`/` -> index.html),
# not a single loose HTML file fronted by serve.
WEBTERM_DASH_DIR = CLAUDE_DIR / "webterm-dash"
WEBTERM_DASH_INDEX = WEBTERM_DASH_DIR / "index.html"
WEBTERM_LAUNCH_PATH = CLAUDE_DIR / "airuleset-webterm-ttyd.sh"
WEBTERM_CRED_PATH = SECRETS_DIR / "webterm_credential"
WEBTERM_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-ttyd.service"
WEBTERM_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-ttyd.service.template"
WEBTERM_DASH_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-dash.service"
WEBTERM_DASH_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-dash.service.template"


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
# Dashboard — a single-page Windows-Terminal-style tabbed UI generated from the
# inventory (#579). A top tab bar (one short-alias tab per session) + one lazily
# created iframe per session pointing at the IP-first ttyd URL. Tabs switch
# instantly (hide/show; the iframe is kept alive once opened so terminal state
# persists), and ONE basic-auth login (cached per host:port) covers every tab.
# --------------------------------------------------------------------------- #

def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _short_alias(entry):
    """A Windows-Terminal-style SHORT tab alias mirroring the owner's own tab
    names (dev1, dev2, gk, m1..m8 for montalu, miva, d1..d4 for david); an
    unrecognized session gets a sensible short form. The FULL id/label stays as
    the tab's `title` tooltip, so a short alias is never ambiguous (#579)."""
    if entry.get("local") or entry.get("id") == "dev1":
        return "dev1"
    user = (entry.get("user") or "").strip()
    name = entry.get("id") or entry.get("label") or ""
    if user == "gatekeeper":
        return "gk"
    mo = re.match(r"^montalu(\d+)$", user)
    if mo:
        return "m" + mo.group(1)
    mo = re.match(r"^david(\d*)$", user)
    if mo:
        return "d" + (mo.group(1) or "1")   # base `david` == d1
    mo = re.match(r"^miva(\d+)$", user)
    if mo:
        return "miva" if mo.group(1) == "1" else "mv" + mo.group(1)
    mo = re.match(r"^simap(\d+)$", user)
    if mo:
        return "si" + mo.group(1)
    if user == "newlevel":
        # an owner box (dev2 / spinbike-vps) shares the `newlevel` unix user —
        # key on the box NAME, not the user.
        return (name.split("-")[0] or name)[:8]
    if user:
        return user[:8]
    return (name.split("-")[0] or name)[:8]


def _tab_order_key(alias):
    """Stable tab order (#579 owner spec): dev1, dev2, gk, m1.., miva, d1..,
    then everything else alphabetically. Uniform 3-tuples so no cross-group
    comparison ever puts a str next to an int."""
    if alias == "dev1":
        return (0, 0, "")
    if alias == "dev2":
        return (1, 0, "")
    if alias == "gk":
        return (2, 0, "")
    mo = re.match(r"^m(\d+)$", alias)
    if mo:
        return (3, int(mo.group(1)), "")
    if alias == "miva":
        return (4, 0, "")
    mo = re.match(r"^d(\d+)$", alias)
    if mo:
        return (5, int(mo.group(1)), "")
    return (6, 0, alias)


def _tab_sessions(inventory):
    """Inventory entries as tab descriptors (short alias + full-id title),
    sorted in the owner's stable Windows-Terminal order."""
    tabs = [{"id": e["id"], "alias": _short_alias(e),
             "title": e.get("label") or e["id"]} for e in inventory]
    tabs.sort(key=lambda t: _tab_order_key(t["alias"]))
    return tabs


def _json_for_script(obj):
    """`json.dumps` with the three chars that could break out of a <script>
    element neutralized, so an inventory label can never inject markup/JS."""
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def render_dashboard_html(inventory, ttyd_base=None):
    """The single-page tabbed terminal UI. `ttyd_base` is the tailnet IP origin
    of the ttyd port (e.g. `http://100.104.8.125:7682`); the page's JS builds
    each tab's iframe src as `<ttyd_base>/?arg=<id>` on first activation."""
    ttyd_base = (ttyd_base or "").rstrip("/")
    tabs = _tab_sessions(inventory)
    buttons = "\n".join(
        '<button class="tab" data-idx="%d" title="%s">'
        '<span class="ico">&#9656;</span><span class="al">%s</span></button>'
        % (i, _html_escape(t["title"]), _html_escape(t["alias"]))
        for i, t in enumerate(tabs))
    cfg = {"ttyd_base": ttyd_base, "sessions": tabs}
    return (_DASHBOARD_TEMPLATE
            .replace("@@COUNT@@", str(len(tabs)))
            .replace("@@BUTTONS@@", buttons)
            .replace("@@CFG_JSON@@", _json_for_script(cfg)))


# NOTE: `.replace()` substitution (not `%`-formatting) — the CSS/JS body is full
# of `{}`, `%`, and `:` that would otherwise need escaping.
_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>work.newlevel.media — fleet terminal</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; background: #0d1117; color: #e6edf3;
  font: 13px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow: hidden; }
#tabbar { display: flex; align-items: stretch; gap: 2px; padding: 4px 6px 0;
  background: #161b22; border-bottom: 1px solid #30363d; overflow-x: auto;
  flex: 0 0 auto; white-space: nowrap; }
.tab { display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 6px 12px; border: 1px solid transparent; border-bottom: none;
  border-radius: 7px 7px 0 0; background: #21262d; color: #adbac7;
  font: inherit; line-height: 1; max-width: 170px; flex: 0 0 auto; }
.tab:hover { background: #2a3038; color: #e6edf3; }
.tab.active { background: #0d1117; color: #fff; border-color: #30363d; }
.tab .ico { color: #2f81f7; font-size: 11px; }
.tab .al { overflow: hidden; text-overflow: ellipsis; }
#frames { position: relative; flex: 1 1 auto; }
#frames iframe.term { position: absolute; inset: 0; width: 100%; height: 100%;
  border: 0; background: #0d1117; }
#hint { flex: 0 0 auto; padding: 3px 10px; color: #6e7681; font-size: 11px;
  background: #161b22; border-top: 1px solid #21262d; }
</style>
</head>
<body>
<div id="tabbar">
@@BUTTONS@@
</div>
<div id="frames"></div>
<div id="hint">@@COUNT@@ tmux sessions · klik na záložku alebo Ctrl+Alt+1..9 · prihlásenie raz (tailnet-only)</div>
<script>
const CFG = @@CFG_JSON@@;
const frames = document.getElementById('frames');
const made = {};
function activate(idx) {
  const s = CFG.sessions[idx];
  if (!s) return;
  if (!made[idx]) {
    const f = document.createElement('iframe');
    f.className = 'term';
    f.src = CFG.ttyd_base + '/?arg=' + encodeURIComponent(s.id);
    frames.appendChild(f);
    made[idx] = f;
  }
  for (const k in made) made[k].style.display = (+k === idx) ? 'block' : 'none';
  document.querySelectorAll('.tab').forEach((t, i) =>
    t.classList.toggle('active', i === idx));
}
document.querySelectorAll('.tab').forEach((t, i) =>
  t.addEventListener('click', () => activate(i)));
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.altKey && e.key >= '1' && e.key <= '9') {
    const idx = parseInt(e.key, 10) - 1;
    if (idx < CFG.sessions.length) { e.preventDefault(); activate(idx); }
  }
});
if (CFG.sessions.length) activate(0);   // land in the first terminal, not a landing page
</script>
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
# airuleset-managed (#555/#579) — do NOT edit; regenerate via `python3 airuleset.py install`.
# Reads the basic-auth credential from a mode-600 file (keeps it out of the unit
# file) and execs ttyd bound DIRECTLY to dev1's tailscale IP (tailnet-only, no
# reverse proxy — #579: `tailscale serve` matched only the MagicDNS name, so a
# raw-IP request 404'd cross-node).
set -euo pipefail
cred="$(cat %(cred_path)s)"
exec ttyd -p %(ttyd_port)d -i %(bind_ip)s -a -W -O -c "$cred" \\
  python3 %(repo_dir)s/cli_webterm.py webterm-connect
"""


def render_webterm_launch_script(bind_ip):
    return _LAUNCH_TEMPLATE % {
        "cred_path": shlex.quote(str(WEBTERM_CRED_PATH)),
        "ttyd_port": WEBTERM_TTYD_PORT,
        "bind_ip": shlex.quote(bind_ip),
        "repo_dir": shlex.quote(str(REPO_DIR)),
    }


def _render_webterm_unit():
    tmpl = WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_LAUNCH_PATH))


def _render_webterm_dash_unit(bind_ip):
    """The static-dashboard systemd --user unit: `python3 -m http.server` bound
    to `bind_ip` (dev1's tailscale IP) on WEBTERM_DASH_PORT, serving the
    generated `webterm-dash/` directory. `bind_ip` MUST be a validated tailscale
    IP (see `_tailscale_ip`) — never 0.0.0.0/public."""
    tmpl = WEBTERM_DASH_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return (tmpl.replace("{{BIND_IP}}", bind_ip)
            .replace("{{DASH_DIR}}", str(WEBTERM_DASH_DIR))
            .replace("{{DASH_PORT}}", str(WEBTERM_DASH_PORT)))


# dev1's tailscale IP must be inside the CGNAT block tailscale uses
# (100.64.0.0/10 — second octet 64..127); anything else is NOT a tailnet address
# and must never become a bind target (that could expose a shell publicly).
_TS_CGNAT_RE = re.compile(
    r"^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}$")


def _tailscale_ip(run=None):
    """dev1's tailscale IPv4 (validated to 100.64.0.0/10), for binding the
    gateway services tailnet-only. Returns the IP string, or None if tailscale
    has no usable tailnet IP — the caller MUST then REFUSE to provision (never
    fall back to 0.0.0.0 or a public interface)."""
    run = run or subprocess.run
    try:
        out = run(["tailscale", "ip", "-4"], capture_output=True, text=True,
                  timeout=5)
    except Exception:
        return None
    if getattr(out, "returncode", 1) != 0:
        return None
    for line in (getattr(out, "stdout", "") or "").splitlines():
        ip = line.strip()
        if _TS_CGNAT_RE.match(ip):
            return ip
    return None


def _reset_tailscale_serve(run=None):
    """Idempotently clear any leftover `tailscale serve` config from the old
    (#555) serve-fronted design — the services now bind the tailscale IP
    directly, so no serve mapping should remain (a stale one would keep matching
    only the MagicDNS name — the exact #579 cross-node 404). Best-effort: an
    already-empty serve config makes this a no-op."""
    run = run or subprocess.run
    try:
        run(["tailscale", "serve", "reset"], capture_output=True, text=True)
    except Exception as e:
        print("  webterm: `tailscale serve reset` skipped (%s)" % e,
              file=sys.stderr)


def is_webterm_gateway():
    return os.uname().nodename == WEBTERM_GATEWAY_HOST


def setup_webterm_service(run=None):
    """dev1-only: provision + enable the ttyd web-terminal gateway + static
    dashboard, both bound DIRECTLY to dev1's tailscale IP (#579). Idempotent.
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

    # #579: resolve dev1's tailscale IP ONCE (single source of truth for the
    # dashboard links, the ttyd `-i` bind, and the dashboard `--bind`). REFUSE
    # LOUDLY if there is none — NEVER write a unit that could bind 0.0.0.0.
    bind_ip = _tailscale_ip(run=run)
    if not bind_ip:
        print("  webterm: no tailscale IP (`tailscale ip -4` gave nothing in "
              "100.64.0.0/10) — REFUSING to provision (never binds 0.0.0.0/"
              "public). Bring tailscale up and re-run install.", file=sys.stderr)
        return False

    from cli_filedrop_watchdog import _run_systemctl, _whoami

    # The old serve-fronted design (#555) is gone — clear any leftover serve
    # config so a stale MagicDNS-only mapping can't linger and re-cause #579.
    _reset_tailscale_serve(run=run)

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    WEBTERM_DASH_DIR.mkdir(parents=True, exist_ok=True)
    inv = webterm_inventory()
    WEBTERM_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ttyd_base = "http://%s:%d" % (bind_ip, WEBTERM_TTYD_PORT)
    WEBTERM_DASH_INDEX.write_text(
        render_dashboard_html(inv, ttyd_base=ttyd_base), encoding="utf-8")
    _ensure_credential()
    WEBTERM_LAUNCH_PATH.write_text(render_webterm_launch_script(bind_ip),
                                   encoding="utf-8")
    os.chmod(WEBTERM_LAUNCH_PATH, 0o755)

    WEBTERM_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_SERVICE_DEST.write_text(_render_webterm_unit(), encoding="utf-8")
    WEBTERM_DASH_SERVICE_DEST.write_text(_render_webterm_dash_unit(bind_ip),
                                         encoding="utf-8")

    try:
        run(["loginctl", "enable-linger", _whoami()], capture_output=True, text=True)
    except Exception as e:
        print("  webterm: loginctl enable-linger skipped (%s)" % e, file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print("  webterm: systemctl daemon-reload FAILED: %s" % (err or "").strip(),
              file=sys.stderr)

    ok_all = True
    for svc in ("webterm-ttyd.service", "webterm-dash.service"):
        rc, _o, err = _run_systemctl(["enable", "--now", svc])
        if rc != 0:
            print("  webterm: systemctl enable --now %s FAILED: %s\n"
                  "    Manual: XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user "
                  "enable --now %s" % (svc, (err or "").strip(), svc),
                  file=sys.stderr)
            ok_all = False
            continue
        # `enable --now` is a no-op for an already-running service, so a
        # re-install that changed the launcher/unit (new ttyd flags, new bind IP,
        # new dashboard) needs an explicit restart to take effect.
        rc, _o, err = _run_systemctl(["restart", svc])
        if rc != 0:
            print("  webterm: systemctl restart %s FAILED (new config may not be "
                  "live): %s" % (svc, (err or "").strip()), file=sys.stderr)

    if ok_all:
        # NOTE: the dashboard port is served UNAUTHENTICATED over the tailnet — it
        # discloses fleet session labels to any tailnet peer (largely `tailscale
        # status`-discoverable already; the shell port :7682 stays basic-auth-
        # gated). Accepted residual; tighten with a tailscale ACL if desired.
        print("  webterm: gateway live — dashboard http://%s:%d/ , shell "
              "http://%s:%d/ (tailnet-only, login user %r)"
              % (bind_ip, WEBTERM_DASH_PORT, bind_ip, WEBTERM_TTYD_PORT,
                 WEBTERM_LOGIN_USER))
    return ok_all


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
