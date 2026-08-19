"""airuleset web terminal gateway (#555) — dev1-only ttyd + tailscale-serve brána.

Nahrádza ownerových ~21 SSH tabov vo Windows Termináli. Owner otvorí jednu
tailnet adresu v Chrome, prihlási sa, a dostane dashboard so VŠETKÝMI tmux
sessions flotily (dev1, dev2, gk, subdev streamy) — klik = pripojený do žijúcej
tmux session daného targetu. Sessions žijú v tmuxoch na targetoch (už dnes), táto
web vrstva je len perzistentné, PC-nezávislé okno do nich.

Architektúra (viď design komentáre na #555 + #579 + #584):
  cross-node browser --tailscale(WireGuard)--> dev1 tailscale IP <ip>
      :8080 /        -> gateway: form login (Bitwarden-fillable) -> session cookie
      :8080 /        -> gateway: tabbed dashboard (authed)
      :8080 /t/*     -> gateway proxy (HTTP+WS) -> ttyd(127.0.0.1:7682, -b /t)
                        -> connect -> ssh -t -> tmux targetu
#584: JEDEN tailnet-only port. `cli_webterm_gateway.py` (stdlib asyncio) binduje
dev1 tailscale IP na :8080, serví HTML login formulár (autocomplete atribúty ->
password manager ho vyplní, na rozdiel od natívneho basic-auth dialógu) -> HttpOnly
SameSite=Strict session cookie, serví dashboard a transparentne proxuje /t/*
(HTTP aj WebSocket) na loopback ttyd. Iframy sú tým same-origin (Ctrl+Alt+N
funguje aj s fokusom v termináli). ttyd binduje LEN 127.0.0.1 s `-b /t`, bez
basic-auth (brána autentizuje). #579 dvoj-portová topológia (samostatný
NEautentizovaný http.server dashboard + priamo exponovaný ttyd) je nahradená.
Bind (tailscale IP) sa resolvuje dynamicky (`tailscale ip -4`, validované na
100.64.0.0/10), inak install REFUSE (nikdy 0.0.0.0/public). Inventár generovaný
z `_deployable_hosts()` + dev1, NIKDY ručný zoznam. Provisioning dev1-only
(`os.uname().nodename == "dev1"`), systemd --user unity podľa vzoru
`setup_filedrop_service()`, `ttyd` inštalovaný dev1-lokálne.

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
# #584: ttyd is now LOOPBACK-only (127.0.0.1) behind the same-origin gateway —
# NOT reachable from the tailnet directly, so the gateway (with its form login +
# session cookie) is the sole entry AND the sole authenticator. It runs under a
# base path (`-b /t`) so the gateway can front it AND the dashboard on one
# origin (`/` = dashboard/login, `/t/*` = ttyd). ttyd carries NO basic-auth of
# its own now (the native dialog is what Bitwarden could not fill — #584).
WEBTERM_TTYD_PORT = 7682
WEBTERM_TTYD_BIND = "127.0.0.1"
WEBTERM_TTYD_BASE = "/t"
# #584: the ONE tailnet-only port. The gateway (cli_webterm_gateway.py) binds
# dev1's tailscale IP here, serves the login form + tabbed dashboard, and
# transparently proxies /t/* (HTTP+WebSocket) to the loopback ttyd. Replaces the
# #579 two-port topology (a separate unauthenticated http.server dashboard +
# a directly-exposed ttyd). Non-privileged port, so no CAP_NET_BIND_SERVICE.
WEBTERM_GATEWAY_PORT = 8080
# The owner tmux session group on his own boxes (dev1/dev2). A stream account's
# own session is named after its unix user (#264 whoami auto-attach convention).
OWNER_GROUP = "zbynek"
WEBTERM_LOGIN_USER = "zbynek"

WEBTERM_INVENTORY_PATH = CLAUDE_DIR / "webterm-inventory.json"
# The dashboard index the gateway serves at `/` for an authed session.
WEBTERM_DASH_DIR = CLAUDE_DIR / "webterm-dash"
WEBTERM_DASH_INDEX = WEBTERM_DASH_DIR / "index.html"
WEBTERM_LAUNCH_PATH = CLAUDE_DIR / "airuleset-webterm-ttyd.sh"
WEBTERM_CRED_PATH = SECRETS_DIR / "webterm_credential"
WEBTERM_GATEWAY_MODULE = REPO_DIR / "cli_webterm_gateway.py"
WEBTERM_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-ttyd.service"
WEBTERM_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-ttyd.service.template"
WEBTERM_GATEWAY_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-gateway.service"
WEBTERM_GATEWAY_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-gateway.service.template"
# #584: the #579 static-dashboard http.server unit is superseded by the gateway;
# its stale copy is removed at install (kept as a constant only for that cleanup).
WEBTERM_OLD_DASH_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-dash.service"


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

# Universal, multi-attach-safe tmux join, parameterized only by `$P` (the
# trusted inventory `preferred`). #584: STANDARD tmux multispoj that NEVER
# disturbs an existing Windows-Terminal/ssh client on the same session (owner
# report: "rozpadne mi view ked ... sa prepnem do webtermu ... standartny tmux
# multispoj"):
#   1. size every window to the ACTIVE viewing client (window-size latest +
#      aggressive-resize on), so a small web client can never shrink the owner's
#      WT view (idempotent, benign — `latest` is already the tmux default);
#   2. resolve the base session to JOIN: exact `=$P` -> group survivor -> the
#      single existing session -> else create `$P` fresh;
#   3. an existing base is joined via a THROWAWAY GROUPED clone
#      (`new-session -t <base> -s <base>-web-$$`) — an independent VIEW onto the
#      same windows, never a mirror, NEVER `attach -d` (the only verb that
#      detaches other clients). The clone is killed on disconnect (trap, EXIT +
#      signals); the base — which holds the group's windows, so the shell
#      survives — is NEVER killed (the trap targets only the named clone).
# Mirrors the fleet ssh auto-attach convention
# (cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK). Reused for local (dev1) and
# remote (ssh) alike. Residual (documented, not chased): if a concurrent
# destroy-unattached sweep has already reduced the group to just this clone
# (owner's WT gone), disconnecting the web client ends that ownerless shell —
# same class the fleet's existing keep-last sweep already produces.
_ATTACH_BODY = (
    'tmux set-option -gw window-size latest >/dev/null 2>&1 || true; '
    'tmux set-option -gw aggressive-resize on >/dev/null 2>&1 || true; '
    'T=""; '
    'if tmux has-session -t "=$P" 2>/dev/null; then T="$P"; else '
    'T=$(tmux list-sessions -F "#{session_group}::#{session_name}" 2>/dev/null '
    "| awk -F '::' -v g=\"$P\" '$1==g{print $2; exit}'); "
    'if [ -z "$T" ]; then '
    'N=$(tmux list-sessions -F "#{session_name}" 2>/dev/null); '
    'if [ "$(printf %s "$N" | grep -c .)" = "1" ]; then T="$N"; fi; '
    'fi; fi; '
    'if [ -n "$T" ]; then '
    'C="${T}-web-$$"; '
    # `=$C` = tmux EXACT-match (like `=$P` above): a prefix `kill-session -t
    # "$C"` could match a live sibling clone whose pid is a numeric extension of
    # this one (…-web-123 vs …-web-1234) and kill the wrong web view (#584 review).
    "trap 'tmux kill-session -t \"=$C\" 2>/dev/null || true' EXIT HUP INT TERM; "
    'tmux new-session -t "$T" -s "$C"; '
    'exit; '
    'fi; '
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
# created iframe per session pointing at the SAME-ORIGIN ttyd URL (`/t/?arg=<id>`
# under the #584 gateway). Tabs switch instantly, and ONE gateway form login
# (session cookie) covers every tab — no per-tab auth. #585: the iframe ELEMENT
# is kept once opened, but only the VISIBLE tab holds a LIVE terminal client —
# every hidden tab is disconnected (its throwaway tmux clone dies), so a hidden
# tab can never shrink the base session's shared window (`window-size latest`);
# it reconnects on return and loses nothing (scrollback lives in tmux).
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
    """`json.dumps` with the chars that could break out of a <script> element
    neutralized, so an inventory label can never inject markup/JS: `<`/`>`/`&`
    (a literal `</script>` / entity) plus U+2028/U+2029 (JS line terminators
    that are legal in JSON but would break the `const CFG = {…}` literal)."""
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_dashboard_html(inventory, ttyd_base=None):
    """The single-page tabbed terminal UI. `ttyd_base` is the SAME-ORIGIN ttyd
    base path under the #584 gateway (`/t`); the page's JS builds each tab's
    iframe src as `<ttyd_base>/?arg=<id>` on first activation — same-origin, so
    the per-iframe Ctrl+Alt+N forwarder works while typing."""
    ttyd_base = (ttyd_base or "").rstrip("/")
    tabs = _tab_sessions(inventory)

    def _tab_button(i, t):
        # #582: an ordinal badge (1-9) on the first nine tabs = the VISIBLE
        # Ctrl+Alt+1..9 map, so the shortcut is discoverable and a specific tab
        # is faster to pick out by eye. It is a fixed POSITION digit, never
        # user data, so it adds no injection surface (unlike the escaped label).
        ordinal = ('<span class="ord">%d</span>' % (i + 1)) if i < 9 else ""
        return ('<button class="tab" data-idx="%d" title="%s">'
                '<span class="ico">&#9656;</span>%s<span class="al">%s</span></button>'
                % (i, _html_escape(t["title"]), ordinal, _html_escape(t["alias"])))

    buttons = "\n".join(_tab_button(i, t) for i, t in enumerate(tabs))
    cfg = {"ttyd_base": ttyd_base, "sessions": tabs}
    subst = {"@@COUNT@@": str(len(tabs)), "@@BUTTONS@@": buttons,
             "@@CFG_JSON@@": _json_for_script(cfg)}
    # SINGLE PASS over the TEMPLATE — inserted content (an inventory label in a
    # button, the config JSON) is never re-scanned, so a label that happens to
    # equal a `@@…@@` sentinel can't splice a later substitution into itself.
    return re.sub(r"@@(?:COUNT|BUTTONS|CFG_JSON)@@",
                  lambda mo: subst[mo.group(0)], _DASHBOARD_TEMPLATE)


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
.tab .ord { display: inline-flex; align-items: center; justify-content: center;
  min-width: 15px; height: 15px; padding: 0 3px; border-radius: 3px;
  background: #30363d; color: #8b949e; font-size: 10px; line-height: 1; }
.tab.active .ord { background: #2f81f7; color: #fff; }
#nav { position: sticky; left: 0; z-index: 1; display: inline-flex; gap: 2px;
  padding-right: 4px; margin-right: 2px; background: #161b22; flex: 0 0 auto; }
.cyc { cursor: pointer; border: 1px solid #30363d; border-radius: 6px;
  background: #21262d; color: #adbac7; font: inherit; line-height: 1;
  padding: 6px 9px; }
.cyc:hover { background: #2a3038; color: #e6edf3; }
#frames { position: relative; flex: 1 1 auto; }
#frames iframe.term { position: absolute; inset: 0; width: 100%; height: 100%;
  border: 0; background: #0d1117; }
#hint { flex: 0 0 auto; padding: 3px 10px; color: #6e7681; font-size: 11px;
  background: #161b22; border-top: 1px solid #21262d; }
</style>
</head>
<body>
<div id="tabbar">
<span id="nav"><button class="cyc" data-cyc="-1" title="Predošlá session">&#9664;</button><button class="cyc" data-cyc="1" title="Ďalšia session">&#9654;</button><button class="cyc" id="fs" title="Fullscreen — Ctrl+W pôjde do terminálu (Keyboard Lock)">&#9974;</button></span>
@@BUTTONS@@
</div>
<div id="frames"></div>
<div id="hint">@@COUNT@@ tmux sessions · klik na záložku alebo ◀ ▶ prepne vždy · Ctrl+Alt+1..9 skočí na záložku (funguje aj počas písania v termináli) · skrytý tab sa odpojí a pri návrate obnoví (história ostáva v tmuxe) · Ctrl+W: v bežnom okne vyskočí potvrdenie, &#9974; Fullscreen zapne Keyboard Lock a Ctrl+W pôjde do terminálu, alebo nainštaluj ako PWA (Chrome → Inštalovať) · prihlásenie raz (tailnet-only)</div>
<script>
const CFG = @@CFG_JSON@@;
const frames = document.getElementById('frames');
const made = {};
let current = 0;                          // the active tab index (drives cycle())
function ttydSrc(s) { return CFG.ttyd_base + '/?arg=' + encodeURIComponent(s.id); }
function connect(f, s) {                   // (re)attach a suspended/new iframe to its terminal
  if (f.dataset.live === '1') return;      // already live -> never a needless reload
  f.src = ttydSrc(s);                      // a fresh ttyd client re-attaches to the SAME base
  f.dataset.live = '1';                    // session; scrollback lives in tmux, not the browser
}
function suspend(f) {                       // #585(a): disconnect a HIDDEN tab so its throwaway
  if (f.dataset.live !== '1') return;      // tmux clone dies (WS close) and can NEVER drive the
  f.src = 'about:blank';                    // base session's shared window size (window-size latest)
  f.dataset.live = '0';
}
function hasLiveTerminal() {                // gate for the #585(b) beforeunload confirm
  for (const k in made) if (made[k].dataset.live === '1') return true;
  return false;
}
function activate(idx) {
  const s = CFG.sessions[idx];
  if (!s) return;
  if (!made[idx]) {                       // lazy: one iframe per activated tab, never N eager
    const f = document.createElement('iframe');
    f.className = 'term';
    f.dataset.live = '0';
    f.addEventListener('load', () => attachForwarder(f));  // #584 same-origin keydown
    frames.appendChild(f);
    made[idx] = f;
  }
  // #585(a): EXACTLY the visible tab holds a live terminal client — every hidden
  // tab is disconnected (its clone session dies), so a hidden tab can never
  // shrink the base session's shared tmux window (window-size latest sizes to the
  // most-recently-active client). Reconnect on return; scrollback lives in tmux,
  // so nothing is lost (a brief reconnect flash is the only UX cost).
  for (const k in made) {
    if (+k === idx) { connect(made[k], s); made[k].style.display = 'block'; }
    else { suspend(made[k]); made[k].style.display = 'none'; }
  }
  document.querySelectorAll('.tab').forEach((t) => {
    const on = +t.dataset.idx === idx;
    t.classList.toggle('active', on);
    // #582: keep the active tab visible even when the bar has scrolled past it
    // (the exact case the ◀ ▶ cycle buttons exist for — stepping past tab 9).
    // Optional call: a browser without scrollIntoView must never break switching.
    if (on) t.scrollIntoView?.({ inline: 'nearest', block: 'nearest' });
  });
  current = idx;
}
function cycle(delta) {                    // step to prev/next session, wrapping both ways
  const n = CFG.sessions.length;
  if (!n) return;
  activate(((current + delta) % n + n) % n);
}
// #584: ONE keydown handler shared by the parent tab bar AND every terminal
// iframe. Ctrl+Alt+1..9 jumps to a tab. Because the gateway now serves the
// dashboard AND ttyd on the SAME origin, each ttyd iframe is same-origin, so we
// can attach this listener INSIDE each terminal's own window (capture phase,
// before xterm consumes the key) — which is what makes the shortcut work even
// while focus is IN a terminal, the #582 residual this supersedes.
// stopPropagation keeps xterm from also acting on the (unused) Ctrl+Alt+digit
// chord. (Still no Ctrl+Alt+arrow binding: Ctrl+Alt+Left/Right is the Linux
// desktop workspace-switch shortcut, grabbed by the compositor before the page
// sees it — advertising it would over-promise; the ◀ ▶ buttons cover cycling.)
function onHotkey(e) {
  if (!(e.ctrlKey && e.altKey)) return;
  if (e.key >= '1' && e.key <= '9') {
    const idx = parseInt(e.key, 10) - 1;
    if (idx < CFG.sessions.length) {
      e.preventDefault(); e.stopPropagation(); activate(idx);
    }
  }
}
function attachForwarder(f) {
  // Reach into the terminal iframe's own window (same-origin) and intercept the
  // hotkey in the CAPTURE phase, before xterm. A cross-origin frame would throw
  // here — impossible under the gateway, but caught so one frame can never break
  // the page or the rest of switching.
  try {
    const w = f.contentWindow;
    if (w) w.addEventListener('keydown', onHotkey, true);
  } catch (err) { /* never same-origin under the gateway; switching stays alive */ }
}
document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => activate(+t.dataset.idx)));
document.querySelectorAll('.cyc[data-cyc]').forEach((b) =>   // fs button has no data-cyc
  b.addEventListener('click', () => cycle(+b.dataset.cyc)));
// #585(b): Ctrl+W is readline delete-word in the terminal but the browser
// consumes it as close-tab (a reserved shortcut a normal window cannot
// preventDefault). Layer 1 — a beforeunload confirm armed WHILE a terminal is
// connected, so a stray Ctrl+W (or any close) shows Chrome's confirm instead of
// a silent tab loss. Gated on hasLiveTerminal() so nothing warns before a
// terminal is open.
window.addEventListener('beforeunload', (e) => {
  if (!hasLiveTerminal()) return;
  e.preventDefault();
  e.returnValue = '';                     // Chrome's standard leave-page confirm
});
// Layer 2 — a Fullscreen button that requests fullscreen + Keyboard Lock, so
// Chrome delivers Ctrl+W (and Ctrl+T/N) to the PAGE => the terminal as
// delete-word, not the browser. Feature-detected: an unsupported browser gets a
// disabled button with an honest title (Layer 1 confirm + the PWA hint remain).
const KB_LOCK_KEYS = ['KeyW', 'KeyT', 'KeyN'];
function keyboardLockSupported() {
  return !!(document.documentElement.requestFullscreen
            && navigator.keyboard && navigator.keyboard.lock);
}
async function goFullscreen() {
  try {
    await document.documentElement.requestFullscreen();
    if (navigator.keyboard && navigator.keyboard.lock) {
      await navigator.keyboard.lock(KB_LOCK_KEYS);
    }
  } catch (err) { /* denied/unsupported — the hint documents the fallbacks */ }
}
const fsBtn = document.getElementById('fs');
if (fsBtn) {
  if (keyboardLockSupported()) {
    fsBtn.addEventListener('click', goFullscreen);
  } else {
    fsBtn.disabled = true;
    fsBtn.title = 'Fullscreen + Keyboard Lock nie je v tomto prehliadači podporený';
  }
}
window.addEventListener('keydown', onHotkey);   // Ctrl+Alt+1..9 when the bar is focused
if (CFG.sessions.length) activate(0);   // land in the first terminal, not a landing page
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Provisioning — dev1-only. Renders the systemd --user ttyd (loopback) + gateway
# (tailscale-IP) units + launcher + inventory + dashboard, enables them, removes
# the superseded #579 http.server dash unit, and RESETS any stale tailscale
# serve config (#579). Mirrors `setup_filedrop_service()`.
# --------------------------------------------------------------------------- #

def _ensure_credential():
    """`zbynek:<random>` login credential, generated once, mode 600, in
    ~/.secrets. Returns the `user:pass` string. #584: the GATEWAY's login form
    validates against this (constant-time) — ttyd no longer sees it, so the
    credential is NO LONGER exposed in `ps` (a security improvement over the
    #579 `ttyd -c` residual). The owner reads the file ONCE to save it in
    Bitwarden; thereafter Bitwarden autofills the form. The existing credential
    (from the basic-auth era) is preserved on re-install."""
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


# #584: ttyd binds LOOPBACK only (127.0.0.1) behind a `-b /t` base path. The
# gateway is the sole tailnet entry AND authenticator (cookie-gated), so ttyd
# needs NO basic-auth of its own (`-c` gone — the native dialog was exactly what
# Bitwarden could not fill) and NO `-O`/check-origin (the gateway performs the
# CSWSH Origin check on the WS upgrade; `-O` would in fact break the proxied WS,
# since ttyd would compare the browser's `Origin: <gateway>` against its own
# loopback `Host`). A tailnet peer cannot reach ttyd around the gateway (loopback
# bind), which is the boundary that used to be `-c` + `-O`.
_LAUNCH_TEMPLATE = """#!/usr/bin/env bash
# airuleset-managed (#555/#579/#584) — do NOT edit; regenerate via `python3 airuleset.py install`.
# Execs ttyd bound LOOPBACK-only (127.0.0.1) behind a `-b /t` base path; the
# same-origin gateway (cli_webterm_gateway.py) is the tailnet entry + login.
set -euo pipefail
exec ttyd -p %(ttyd_port)d -i %(ttyd_bind)s -b %(base_path)s -a -W \\
  python3 %(repo_dir)s/cli_webterm.py webterm-connect
"""


def render_webterm_launch_script():
    """The ttyd launcher: loopback bind + `-b /t` base path, no basic-auth, no
    `-O` (#584 — the gateway is the sole entry + authenticator)."""
    return _LAUNCH_TEMPLATE % {
        "ttyd_port": WEBTERM_TTYD_PORT,
        "ttyd_bind": shlex.quote(WEBTERM_TTYD_BIND),
        "base_path": shlex.quote(WEBTERM_TTYD_BASE),
        "repo_dir": shlex.quote(str(REPO_DIR)),
    }


def _render_webterm_unit():
    tmpl = WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_LAUNCH_PATH))


def _render_webterm_gateway_unit(bind_ip):
    """The same-origin gateway systemd --user unit: runs
    `cli_webterm_gateway.py` bound to `bind_ip` (dev1's tailscale IP) on the
    single gateway port, proxying /t/* to the loopback ttyd. `bind_ip` MUST be a
    validated tailscale IP (see `_tailscale_ip`) — never 0.0.0.0/public."""
    tmpl = WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return (tmpl.replace("{{BIND_IP}}", bind_ip)
            .replace("{{GATEWAY_MODULE}}", str(WEBTERM_GATEWAY_MODULE))
            .replace("{{GATEWAY_PORT}}", str(WEBTERM_GATEWAY_PORT))
            .replace("{{DASH_INDEX}}", str(WEBTERM_DASH_INDEX))
            .replace("{{CRED_PATH}}", str(WEBTERM_CRED_PATH))
            .replace("{{TTYD_PORT}}", str(WEBTERM_TTYD_PORT))
            .replace("{{TTYD_BASE}}", WEBTERM_TTYD_BASE))


# dev1's tailscale IP must be inside the CGNAT block tailscale uses
# (100.64.0.0/10 — second octet 64..127); anything else is NOT a tailnet address
# and must never become a bind target (that could expose a shell publicly). Every
# octet is range-checked (0..255) so a malformed value like 100.64.999.1 is
# rejected too, not merely "second octet in range".
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_TS_CGNAT_RE = re.compile(
    r"^100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\." + _OCTET + r"\." + _OCTET + r"$")


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
    only the MagicDNS name — the exact #579 cross-node 404). `serve reset` is a
    NODE-WIDE reset: on dev1 the webterm gateway is the ONLY `tailscale serve`
    user (filedrop binds directly), so there is no collateral today; a future
    dev1 service that uses `tailscale serve` would need this narrowed to the
    specific ports. Best-effort: an already-empty serve config makes it a no-op."""
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

    # #579/#584: resolve dev1's tailscale IP ONCE — the GATEWAY's bind. ttyd is
    # loopback now, so this IP is only the gateway's `--bind`. REFUSE LOUDLY if
    # there is none — NEVER write a unit that could bind 0.0.0.0.
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
    # #584: the ttyd base is now RELATIVE (`/t`) — same-origin under the gateway,
    # so the iframes are same-origin (Ctrl+Alt+N works while typing).
    WEBTERM_DASH_INDEX.write_text(
        render_dashboard_html(inv, ttyd_base=WEBTERM_TTYD_BASE), encoding="utf-8")
    # Remove the old serve-fronted single-file dashboard (superseded — #579).
    (CLAUDE_DIR / "webterm-dashboard.html").unlink(missing_ok=True)
    _ensure_credential()
    WEBTERM_LAUNCH_PATH.write_text(render_webterm_launch_script(), encoding="utf-8")
    os.chmod(WEBTERM_LAUNCH_PATH, 0o755)

    WEBTERM_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_SERVICE_DEST.write_text(_render_webterm_unit(), encoding="utf-8")
    WEBTERM_GATEWAY_SERVICE_DEST.write_text(
        _render_webterm_gateway_unit(bind_ip), encoding="utf-8")

    try:
        run(["loginctl", "enable-linger", _whoami()], capture_output=True, text=True)
    except Exception as e:
        print("  webterm: loginctl enable-linger skipped (%s)" % e, file=sys.stderr)

    # #584: the #579 static-dashboard http.server unit is superseded by the
    # gateway — stop + disable + remove any stale copy so it can't keep serving
    # the OLD unauthenticated dashboard on :8080 alongside the gateway.
    if WEBTERM_OLD_DASH_SERVICE_DEST.exists():
        _run_systemctl(["disable", "--now", "webterm-dash.service"])
        WEBTERM_OLD_DASH_SERVICE_DEST.unlink(missing_ok=True)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print("  webterm: systemctl daemon-reload FAILED: %s" % (err or "").strip(),
              file=sys.stderr)

    ok_all = True
    for svc in ("webterm-ttyd.service", "webterm-gateway.service"):
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
        # new dashboard/gateway) needs an explicit restart to take effect.
        rc, _o, err = _run_systemctl(["restart", svc])
        if rc != 0:
            print("  webterm: systemctl restart %s FAILED (new config may not be "
                  "live): %s" % (svc, (err or "").strip()), file=sys.stderr)

    if ok_all:
        print("  webterm: gateway live — http://%s:%d/ (tailnet-only, form login "
              "user %r; credential in %s — read it once to save in Bitwarden). "
              "ttyd loopback 127.0.0.1:%d behind /t."
              % (bind_ip, WEBTERM_GATEWAY_PORT, WEBTERM_LOGIN_USER,
                 WEBTERM_CRED_PATH, WEBTERM_TTYD_PORT))
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
