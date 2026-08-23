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

#635 (owner ROZHODNUTÉ 2026-08-22): owner-ova doména `zbynek.newlevel.media`
prechádza z tailnet-only na Cloudflare Access (email OTP, ako Davidova), gated cez
`OWNER_GATEWAY_ACCESS_MODE` (default False). Keď je True, `setup_webterm_service`
provisiuje bránu v Access režime — LOOPBACK bind + `--trust-access-header` (heslo
zaniká), fronted cloudflared tunelom; default False necháva tailnet+heslo bránu
byte-identickú.

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

import cli_aliases  # #592: the shared fleet target-alias derivation (stdlib-only)
import cli_webterm_profiles as profiles  # #612: doména -> session set + auth realm

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
# #635 GO-LIVE GATE (owner ROZHODNUTÉ 2026-08-22): move zbynek.newlevel.media
# behind Cloudflare Access like David's side. When True, setup_webterm_service
# provisions the owner gateway in Cloudflare-Access mode — LOOPBACK bind (a
# cloudflared tunnel fronts it, so NO direct tailnet exposure and NO tailscale IP
# is needed) + `--trust-access-header` (the password/login form is RETIRED,
# Cloudflare email-OTP at the edge is the whole gate). Default **False** keeps the
# current password/tailnet gateway BYTE-IDENTICAL, so a routine `install`/`push`
# NEVER flips the owner's live terminal to loopback before the tunnel + DNS +
# Access app exist (which would lock him out — the exact failure the coordinator
# flagged). It is flipped to True as the LAST go-live step, AFTER the Access app is
# applied, the dedicated cloudflared tunnel routes zbynek.newlevel.media ->
# 127.0.0.1:8080, and the DNS grey A-record is cut over to a proxied CNAME onto
# that tunnel — all proven live first. See cli_webterm_access.WEBTERM_ACCESS_APPS
# ['owner'].
#
# FLIPPED TRUE at go-live (#635, 2026-08-23): the dedicated `webterm-owner`
# cloudflared tunnel (WEBTERM_OWNER_TUNNEL_UUID) is stood up + managed, the DNS
# grey A-record is cut over to a proxied CNAME onto it, and the Access app is live
# — so a routine install now correctly provisions the owner gateway in loopback +
# Access mode AND the managed tunnel that fronts it (setup_webterm_owner_tunnel).
OWNER_GATEWAY_ACCESS_MODE = True
# #613 REOPEN-2: a tmux CLIENT of C x R rows shows an (R - status_rows)-row
# WINDOW; the fleet runs the default 1-row status line (airuleset never sets
# `status off`), and the owner's live Windows-Terminal client is 176x51 -> a
# 176x50 window (== TMUX_DEFAULT_SIZE) + 1 status row. The browser xterm is
# force-fit to that SAME fixed CLIENT grid (see _webterm_term_grid / the
# dashboard fitFixedGrid JS) so it is a twin of the owner's WT.
WEBTERM_STATUS_ROWS = 1

WEBTERM_INVENTORY_PATH = CLAUDE_DIR / "webterm-inventory.json"
# The dashboard index the gateway serves at `/` for an authed session.
WEBTERM_DASH_DIR = CLAUDE_DIR / "webterm-dash"
WEBTERM_DASH_INDEX = WEBTERM_DASH_DIR / "index.html"
WEBTERM_LAUNCH_PATH = CLAUDE_DIR / "airuleset-webterm-ttyd.sh"
WEBTERM_CRED_PATH = SECRETS_DIR / "webterm_credential"
# #612: the david profile's OWN credential file — a SEPARATE auth realm, so a
# david login can never authenticate the owner gateway and vice versa. Lives on
# the subdev david gateway box; delivered to David via `secret show` (one-shot).
WEBTERM_DAVID_CRED_PATH = SECRETS_DIR / "webterm_david_credential"
WEBTERM_GATEWAY_MODULE = REPO_DIR / "cli_webterm_gateway.py"
WEBTERM_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-ttyd.service"
WEBTERM_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-ttyd.service.template"
WEBTERM_GATEWAY_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-gateway.service"
WEBTERM_GATEWAY_SERVICE_TEMPLATE = REPO_DIR / "settings" / "webterm-gateway.service.template"
# #612: the DAVID developer gateway (subdev) provisioning — its paths, units and
# the setup function — lives in cli_webterm_david.py (kept OUT of this module so
# it stays under its size cap and the owner path is untouched). maybe_setup_webterm
# dispatches to it by box profile. WEBTERM_DAVID_CRED_PATH stays here because the
# core `_cred_path` (the per-profile auth realm) needs it.
# #584: the #579 static-dashboard http.server unit is superseded by the gateway;
# its stale copy is removed at install (kept as a constant only for that cleanup).
WEBTERM_OLD_DASH_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "webterm-dash.service"

# #635: the MANAGED cloudflared tunnel provisioning (shared render helpers + the
# OWNER provisioner + the owner-tunnel path constants) lives in its OWN leaf
# cli_webterm_tunnel.py — the same size-cap-driven leaf split the webterm code
# already uses (cli_webterm_david / _access / _profiles / _gateway). It is imported
# LAZILY inside setup_webterm_service (no module-level cycle: the tunnel leaf imports
# cli_webterm lazily too). The david lane's tunnel provisioner (cli_webterm_david)
# reuses the same render helpers.


# --------------------------------------------------------------------------- #
# Inventory — generated from _deployable_hosts() + dev1 (never a hand list).
# --------------------------------------------------------------------------- #

def _sanitize_id(name):
    """A URL-safe, `@`/`.`-free session id for the dashboard `?arg=` link and the
    connect allowlist. Deterministic + collision-free across the fleet names
    (montalu@subdev -> montalu-subdev, admin@forestshop-dev ->
    admin-forestshop-dev, dev2 -> dev2)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _login_user(profile):
    """The gateway login username for `profile` — the david realm is `david`,
    the owner realm is `zbynek` (WEBTERM_LOGIN_USER). Used by the credential
    generator + the go-live message."""
    return "david" if profile == profiles.DAVID else WEBTERM_LOGIN_USER


def _cred_path(profile):
    """The credential file for `profile` — a SEPARATE realm per profile. Reads
    the module globals at call time so tests can patch either path."""
    return WEBTERM_DAVID_CRED_PATH if profile == profiles.DAVID else WEBTERM_CRED_PATH


def webterm_inventory(profile=profiles.OWNER):
    """The session inventory for `profile`. `david` -> the SCOPED david set
    (david1..4 + codex-bridge) — self-contained in cli_webterm_profiles, so no
    airuleset/fleet import is needed for it. `owner` (default) -> dev1
    (localhost) + every `_deployable_hosts()` entry, byte-identical to the
    pre-#612 single-tenant inventory. Per-entry `preferred` tmux group = the
    unix user for a stream account (in AUTHORITY_BY_USER, the #264 whoami
    convention), else `zbynek` (owner group). Read the fleet table via the
    airuleset facade (test-patchable)."""
    if profile == profiles.DAVID:
        return profiles.david_inventory()
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
#   1. #584 tried `-gw window-size latest` + `aggressive-resize on` per-connect
#      to make the webterm fit — the OPPOSITE regression (a small webterm client
#      shrinks/blackens the owner's WT view). Those overrides are REMOVED.
#      #586 then pinned the window fleet-wide (`window-size manual` +
#      `default-size 176x50`) AND made the clone `-f ignore-size` so the webterm
#      could never influence sizing. #613 REOPEN removed both on a theory that
#      measured the BROWSER client (wrong client) -- switching to `latest` let a
#      SMALLER browser re-pin the owner's real windows, so his larger Windows
#      Terminal was left rendering a DARK unused region (his SURFACE; the browser
#      the CAUSE). #613 REOPEN-2 (owner directive 2026-08-22) RESTORES the
#      fixed-size invariant: `window-size manual` + `default-size 176x50`
#      (cli_tmux_provisioning) pins every window regardless of any client, and
#      this clone re-attaches with `-f ignore-size` so a box still running the
#      first-reopen `latest` server is fixed immediately (no restart). No client
#      resizes another's window -- no "resizovanie hore-dole". The browser's OWN
#      appearance at the fixed grid (no dark area) is solved on the BROWSER side
#      (the dashboard fit-to-fixed-grid JS), never by resizing tmux.
#   2. resolve the base session to JOIN: exact `=$P` -> group survivor -> the
#      single existing session -> else create `$P` fresh;
#   3. an existing base is joined via a THROWAWAY GROUPED clone: created
#      DETACHED (`new-session -d -t <base> -s <base>-web-$$`), armed with a
#      per-session `client-attached` destroy-unattached hook + `mouse on`
#      (#615, session-scoped), then attached with `-f ignore-size`
#      (`attach-session -t <clone> -f ignore-size`, #613 REOPEN-2) — an
#      independent VIEW onto the same
#      windows, never a mirror, NEVER `attach -d` (the only verb that detaches
#      other clients). The clone is killed on disconnect (trap, EXIT + signals)
#      AND self-destructs on its own client-detach via the per-session
#      `destroy-unattached on` (#591 belt-and-suspenders for a trap that never
#      fires). The base — which holds the group's windows, so the shell
#      survives — is NEVER killed (the trap targets only the named clone, and
#      the base keeps tmux's default `off`). The `-A` fresh-base fallback is the
#      owner's OWN real view.
# Mirrors the fleet ssh auto-attach convention
# (cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK). Reused for local (dev1) and
# remote (ssh) alike. Residual (documented, not chased): if the owner's WT is
# already gone so this clone is the group's only remaining session,
# disconnecting the web client ends that ownerless shell — an expected, benign
# outcome (nothing left to protect), NOT the #591 base-kill the per-session
# scoping fixes (that was a GLOBAL keep-last destroying a LIVE base).
_ATTACH_BODY = (
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
    # #591: the clone is created DETACHED, then a PER-SESSION `client-attached`
    # hook arms its OWN `destroy-unattached on`, then it is attached. This scopes
    # the sweep to the clone ALONE — the base session keeps tmux's default `off`
    # and is NEVER destroyed, so the owner detaching from the base while the clone
    # lives can no longer kill the base (the gk 2026-08-20 total-death; the old
    # GLOBAL `destroy-unattached keep-last` in cli_tmux_provisioning did that and
    # is removed). Two live-verified tmux constraints shape this: (1) setting
    # `destroy-unattached on` on a DETACHED (zero-client) session destroys it
    # IMMEDIATELY, so the hook defers the set to attach time, when `on` is safe;
    # (2) `set-option`/`set-hook` `-t` do NOT accept the `=` exact-match anchor
    # (only has-session/kill-session do), so `$C` is targeted bare — safe because
    # the exact-named session was just created, so prefix resolution matches it
    # exactly. #613 REOPEN-2: the attach carries `-f ignore-size` (#586, restored
    # -- see the header comment + the `attach-session` line below for why: it
    # keeps a smaller browser from shrinking the owner's window). The attach is
    # NOT `exec`ed, so the EXIT/HUP trap still fires to kill the clone (the
    # per-session `on` is the belt-and-suspenders for a trap that never fires).
    # TRANSITION RESIDUAL (#591-review B1, documented not guarded): on a target
    # NOT yet re-installed after #591 whose RUNNING server still carries the old
    # live GLOBAL `keep-last`, `new-session -d` creates the clone DETACHED and
    # keep-last destroys it AT CREATION (before set-hook/attach), so this connect
    # FAILS (the base is still safe — the #591 goal holds; only the webterm view
    # to that one target breaks). Closed by CO-DEPLOYMENT: the same install/push
    # that ships this code runs apply_tmux_history_limit's `set-option -gu
    # destroy-unattached` on that box's running server, reverting keep-last to
    # `off`. So the window is a transient failed-connect (never a death) that
    # self-heals on that target's install; global tmux policy is deliberately
    # kept OUT of this connect script (cli_tmux_provisioning owns it). Distinct
    # from the ownerless-clone residual noted in the header comment above.
    'tmux new-session -d -t "$T" -s "$C"; '
    'tmux set-hook -t "$C" client-attached "set-option destroy-unattached on"; '
    # #615: enable mouse on the CLONE session only (a SESSION option, so the
    # owner's own SSH session stays `mouse off` — its terminal is unchanged),
    # so the browser scroll-wheel reaches tmux copy-mode (the 50000-line
    # history is otherwise unreachable — the wheel just spews raw `^[[A`
    # escapes into the shell). NEVER a global `set-option -g mouse on`.
    'tmux set-option -t "$C" mouse on; '
    # #613 REOPEN-2 (owner directive 2026-08-22): `-f ignore-size` is RESTORED.
    # The owner's invariant is a FIXED terminal size for every client so no
    # client resizes another's window. `-f ignore-size` EXCLUDES the webterm
    # clone from tmux's window-size calc, so a smaller browser client can never
    # shrink the owner's Windows-Terminal window (which then rendered a dark
    # unused region -- his SURFACE; the browser is the CAUSE). This bridges a box
    # still RUNNING the first-reopen `latest` server immediately, and is belt-and-
    # suspenders under the restored conf `window-size manual` (which already pins
    # every window regardless of any client). The browser's OWN appearance (it
    # must show the fixed 176x50 window filling its viewport, not a dead region)
    # is solved on the BROWSER side (the dashboard fit-to-fixed-grid JS below),
    # never by letting the browser resize tmux. Proven live (isolated tmux 3.7b +
    # pty clients): under `latest`+plain-clone a 160x46 webterm shrinks the owner's
    # window to 160x45 (owner DARK); with `-f ignore-size` the owner's window
    # stays 176x50 (full) at every attach + window-switch from both sides.
    'tmux attach-session -t "$C" -f ignore-size; '
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
    it execs and never returns.

    #612: the PROFILE's scoped inventory is selected via the `WEBTERM_INVENTORY`
    ENV VAR that the profile launcher exports (david -> david's inventory). This
    is the security boundary made physical: david's ttyd child reads david's
    inventory, so an owner-fleet id is simply not present in the allowlist and is
    refused below. It is an ENV var, NOT a client argv flag, PRECISELY because
    ttyd's `-a` appends client-controlled `?arg=` values as argv — an argv
    `--inventory` flag would be client-injectable (a client could point the
    allowlist at an arbitrary JSON), whereas an env var cannot be injected
    through ttyd url args (#612 adversarial review). `inventory_path` kwarg (tests)
    wins; else the env var; else the default WEBTERM_INVENTORY_PATH."""
    if inventory_path is None:
        inventory_path = os.environ.get("WEBTERM_INVENTORY") or None
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
# under the #584 gateway). Tab switching is a PURE show/hide (every tab is
# preloaded + stays connected, see `preloadAll()` below — instant, no reconnect),
# and ONE gateway form login
# (session cookie) covers every tab — no per-tab auth. #585 originally
# disconnected every hidden tab so it could not shrink the shared window;
# #586's preload-all (every tab kept connected, see `preloadAll()`) SUPERSEDED
# that. #613 REOPEN-2 (owner directive 2026-08-22): the tmux side pins a FIXED
# window size (`window-size manual` + `default-size 176x50`) and the clone is
# `-f ignore-size`, so no tab (hidden or active) can EVER resize a window --
# keeping every tab connected is unconditionally safe. On the BROWSER side, each
# ttyd xterm is forced to the owner's fixed grid (176 cols x 51 rows = the 176x50
# window + 1 status row) and its font is scaled to fill the viewport (see
# `fitFixedGrid` in the page JS), so the fixed window fills the browser with no
# dark unused region and without the browser ever influencing tmux sizing.
# --------------------------------------------------------------------------- #

def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _short_alias(entry):
    """A Windows-Terminal-style SHORT tab alias mirroring the owner's own tab
    names (dev1, dev2, gk, m1..m8 for montalu, miva, d1..d4 for david); an
    unrecognized session gets a sensible short form. The FULL id/label stays as
    the tab's `title` tooltip, so a short alias is never ambiguous (#579).

    #592: the alias derivation itself lives in the shared `cli_aliases` leaf so
    the tmux WINDOW names (cli_tmux_provisioning) draw from the SAME source —
    never a parallel map. This wrapper only unwraps the webterm inventory entry
    into (user, box_name): a `local` entry (dev1) forces the box name to `dev1`
    (the old `local or id=="dev1"` short-circuit), otherwise the box name is the
    inventory id/label."""
    box_name = "dev1" if entry.get("local") else (
        entry.get("id") or entry.get("label") or "")
    return cli_aliases.short_target_alias(entry.get("user"), box_name)


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


def _webterm_term_grid():
    """The owner's FIXED terminal CLIENT grid `(cols, rows)` the browser xterm is
    force-fit to (#613 REOPEN-2). Derived from the tmux WINDOW size
    (cli_tmux_provisioning.TMUX_DEFAULT_SIZE, e.g. `176x50`) + WEBTERM_STATUS_ROWS
    status rows -- NOT a duplicated literal, so it can never drift from the conf's
    `default-size`. Lazy import so the connect path (connect_main) stays
    import-light -- this is only ever called at dashboard render (install time)."""
    from cli_tmux_provisioning import TMUX_DEFAULT_SIZE
    w, h = (int(x) for x in TMUX_DEFAULT_SIZE.lower().split("x"))
    return (w, h + WEBTERM_STATUS_ROWS)


# #643: the owner's Windows-Terminal "Campbell" palette — the SINGLE source of
# truth for the terminal colours. It is injected into the dashboard <script> as
# an xterm.js `ITheme` object and applied via `term.options.theme` on the
# same-origin `window.term` (the #613 integration point), NOT via a ttyd `-t
# theme=` flag — so it is DAEMON-AGNOSTIC and survives a possible ttyd -> GoTTY
# switch (#642). `background_color`/`theme_color` in the PWA manifest (#644)
# reuse CAMPBELL_THEME["background"]. Exact values from the #643 issue body.
# NB: xterm.js `ITheme` names the magenta slots `magenta`/`brightMagenta` — the
# Windows-Terminal scheme's own `purple`/`brightPurple` keys are UNKNOWN to
# xterm.js and would silently leave ANSI magenta at the xterm default. The VALUES
# are the owner's Campbell purples; the KEYS are the xterm.js ones (#643 review).
CAMPBELL_THEME = {
    "background": "#0C0C0C", "foreground": "#CCCCCC", "cursor": "#FFFFFF",
    "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "yellow": "#C19C00",
    "blue": "#0037DA", "magenta": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
    "brightBlack": "#767676", "brightRed": "#E74856", "brightGreen": "#16C60C",
    "brightYellow": "#F9F1A5", "brightBlue": "#3B78FF", "brightMagenta": "#B4009E",
    "brightCyan": "#61D6D6", "brightWhite": "#F2F2F2",
}


def render_dashboard_html(inventory, ttyd_base=None, term_grid=None):
    """The single-page tabbed terminal UI. `ttyd_base` is the SAME-ORIGIN ttyd
    base path under the #584 gateway (`/t`); the page's JS builds each tab's
    iframe src as `<ttyd_base>/?arg=<id>` on first activation — same-origin, so
    the per-iframe Ctrl+Alt+N forwarder works while typing. `term_grid` is the
    fixed (cols, rows) client grid the browser xterm is force-fit to (#613
    REOPEN-2, defaults to `_webterm_term_grid()` = the owner's fixed terminal)."""
    ttyd_base = (ttyd_base or "").rstrip("/")
    term_cols, term_rows = term_grid or _webterm_term_grid()
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
    cfg = {"ttyd_base": ttyd_base, "sessions": tabs,
           "term_cols": term_cols, "term_rows": term_rows}
    subst = {"@@COUNT@@": str(len(tabs)), "@@BUTTONS@@": buttons,
             "@@CFG_JSON@@": _json_for_script(cfg),
             # #643: the Campbell palette as an xterm.js theme object literal.
             "@@THEME_JSON@@": _json_for_script(CAMPBELL_THEME)}
    # SINGLE PASS over the TEMPLATE — inserted content (an inventory label in a
    # button, the config JSON, the theme object) is never re-scanned, so a label
    # that happens to equal a `@@…@@` sentinel can't splice a later substitution
    # into itself.
    return re.sub(r"@@(?:COUNT|BUTTONS|CFG_JSON|THEME_JSON)@@",
                  lambda mo: subst[mo.group(0)], _DASHBOARD_TEMPLATE)


# NOTE: `.replace()` substitution (not `%`-formatting) — the CSS/JS body is full
# of `{}`, `%`, and `:` that would otherwise need escaping.
_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>work.newlevel.media — fleet terminal</title>
<!-- #644: installable PWA — standalone window, no browser chrome. The manifest
     (per-domain name), icons and service worker are served by the gateway from
     the dash dir (behind Cloudflare Access). theme-color matches #643 Campbell. -->
<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">
<meta name="theme-color" content="#0C0C0C">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
/* #643: Campbell-consistent dark chrome (pure black + neutral near-black
   shades), so the whole surface matches the vivid Campbell terminals inside
   the iframes instead of the old grey GitHub-dark theme. */
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; background: #0C0C0C; color: #CCCCCC;
  font: 13px/1.3 "Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", Menlo, Monaco, "Liberation Mono", monospace;
  overflow: hidden; }
#tabbar { display: flex; align-items: stretch; gap: 2px; padding: 4px 6px 0;
  background: #0C0C0C; border-bottom: 1px solid #2b2b2b; overflow-x: auto;
  flex: 0 0 auto; white-space: nowrap; }
.tab { display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 6px 12px; border: 1px solid transparent; border-bottom: none;
  border-radius: 7px 7px 0 0; background: #1b1b1b; color: #9a9a9a;
  font: inherit; line-height: 1; max-width: 170px; flex: 0 0 auto; }
.tab:hover { background: #262626; color: #CCCCCC; }
.tab.active { background: #0C0C0C; color: #F2F2F2; border-color: #2b2b2b; }
.tab .ico { color: #13A10E; font-size: 11px; }
.tab .al { overflow: hidden; text-overflow: ellipsis; }
.tab .ord { display: inline-flex; align-items: center; justify-content: center;
  min-width: 15px; height: 15px; padding: 0 3px; border-radius: 3px;
  background: #2b2b2b; color: #767676; font-size: 10px; line-height: 1; }
.tab.active .ord { background: #3B78FF; color: #F2F2F2; }
#nav { position: sticky; left: 0; z-index: 1; display: inline-flex; gap: 2px;
  padding-right: 4px; margin-right: 2px; background: #0C0C0C; flex: 0 0 auto; }
.cyc { cursor: pointer; border: 1px solid #2b2b2b; border-radius: 6px;
  background: #1b1b1b; color: #9a9a9a; font: inherit; line-height: 1;
  padding: 6px 9px; }
.cyc:hover { background: #262626; color: #CCCCCC; }
#frames { position: relative; flex: 1 1 auto; }
#frames iframe.term { position: absolute; inset: 0; width: 100%; height: 100%;
  border: 0; background: #0C0C0C; }
#hint { flex: 0 0 auto; padding: 3px 10px; color: #767676; font-size: 11px;
  background: #0C0C0C; border-top: 1px solid #1e1e1e; }
</style>
</head>
<body>
<div id="tabbar">
<span id="nav"><button class="cyc" data-cyc="-1" title="Predošlá session">&#9664;</button><button class="cyc" data-cyc="1" title="Ďalšia session">&#9654;</button><button class="cyc" id="fs" title="Fullscreen — Ctrl+W pôjde do terminálu (Keyboard Lock)">&#9974;</button></span>
@@BUTTONS@@
</div>
<div id="frames"></div>
<div id="hint">@@COUNT@@ tmux sessions · klik na záložku alebo ◀ ▶ prepne vždy · Ctrl+Alt+1..9 skočí na záložku (funguje aj počas písania v termináli) · všetky záložky sú prednačítané a stále pripojené — prepínanie je instantné, bez reconnectu (scrollback ostáva v tmuxe) · Ctrl+W: potvrdenie pri zatváraní chráni vždy; &#9974; Fullscreen (Keyboard Lock) pošle Ctrl+W priamo do terminálu — vyžaduje HTTPS/localhost; PWA/app-okno zmenší riziko náhodného zatvorenia · prihlásenie raz (tailnet-only)</div>
<script>
const CFG = @@CFG_JSON@@;
// #643: the Campbell palette (single source of truth) + a Cascadia-ish system
// monospace stack (no external font fetch — CSP/Cloudflare-Access safe). Applied
// to each terminal via `term.options.theme` on the same-origin `window.term`
// (the #613 integration point), so it is DAEMON-AGNOSTIC (ttyd AND a future
// GoTTY both expose `window.term`) — never baked into a ttyd -t theme= flag.
const CAMPBELL_THEME = @@THEME_JSON@@;
const TERM_FONT_STACK = '"Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", Menlo, Monaco, "Liberation Mono", monospace';
function themeTerminal(term) {           // idempotent: applied once per terminal
  if (!term || term.__wtThemed) return;
  term.options.theme = CAMPBELL_THEME;
  term.options.fontFamily = TERM_FONT_STACK;
  term.__wtThemed = true;
}
const frames = document.getElementById('frames');
const made = {};
let current = 0;                          // the active tab index (drives cycle())
function ttydSrc(s) { return CFG.ttyd_base + '/?arg=' + encodeURIComponent(s.id); }
function makeFrame(idx, s) {                // #586: create + CONNECT one iframe ONCE, hidden.
  if (made[idx]) return made[idx];         // idempotent — an iframe is never re-created/reloaded,
  const f = document.createElement('iframe');   // so it is never navigated after its first load
  f.className = 'term';
  f.addEventListener('load', () => attachForwarder(f));  // #584 same-origin keydown
  f.style.display = 'none';
  f.src = ttydSrc(s);                      // connected at creation (preloaded, keepalive)
  f.dataset.live = '1';
  frames.appendChild(f);
  made[idx] = f;
  return f;
}
function preloadAll() {                     // #586: connect EVERY tab at login. Supersedes #585's
  CFG.sessions.forEach((s, i) => makeFrame(i, s));   // disconnect-on-hide, which made switching
}                                           // slow (a reconnect each time) AND fired ttyd's own
                                            // beforeunload ("Leave site?") on every tab click.
                                            // #613 REOPEN-2: the tmux window is FIXED (window-size
                                            // manual + default-size 176x50) and the clone is
                                            // -f ignore-size, so NO tab — hidden or active — can ever
                                            // resize a window; keeping every tab connected is
                                            // unconditionally safe. Each ttyd xterm is force-fit to the
                                            // fixed grid on the browser side (applyFixedGrid).
function hasLiveTerminal() {                // gate for the beforeunload close-confirm
  for (const k in made) if (made[k].dataset.live === '1') return true;
  return false;
}
function activate(idx) {
  const s = CFG.sessions[idx];
  if (!s) return;
  makeFrame(idx, s);                        // already preloaded — idempotent no-op
  // #586: PURE show/hide. Every tab stays CONNECTED (preloaded), so switching is
  // instant with no reconnect AND no iframe navigation — which is exactly why a
  // tab click never fires ttyd's own beforeunload. Only `display` toggles here.
  for (const k in made) {
    made[k].style.display = (+k === idx) ? 'block' : 'none';
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
  applyFixedGrid(made[idx]);                 // #613 REOPEN-2: fit the now-VISIBLE tab
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
  if (f.dataset.live !== '1') return;      // defensive: only forward for a live frame
  try {
    const w = f.contentWindow;
    if (w) w.addEventListener('keydown', onHotkey, true);
  } catch (err) { /* never same-origin under the gateway; switching stays alive */ }
}
// #613 REOPEN-2: force each ttyd xterm to the owner's FIXED client grid
// (CFG.term_cols x CFG.term_rows = the fixed 176x50 tmux window + 1 status row)
// and scale the font so that grid FILLS the iframe viewport, centred. The tmux
// window is a FIXED size (window-size manual) and the clone is -f ignore-size,
// so the browser NEVER resizes tmux; this only makes the browser SHOW the fixed
// window filling its viewport instead of a dark unused region. ttyd 1.7.4
// exposes the xterm Terminal as `window.term` in each same-origin iframe; we
// clamp term.resize so ttyd's own FitAddon can never change the grid, then
// scale term.options.fontSize (crisp re-render, unlike a blurry CSS transform).
// Verified live against real ttyd + headless Chrome: grid forced 176x51, no
// dead dotted region, status bar full width.
function fitFixedGrid(win) {
  const term = win && win.term, cols = CFG.term_cols, rows = CFG.term_rows;
  if (!term || !cols || !rows) return false;   // ttyd not connected yet -> retry
  const doc = win.document;
  if (!term.__wtClamped) {                      // clamp resize -> defeat ttyd's FitAddon
    const real = term.resize.bind(term);
    term.resize = () => real(cols, rows);
    term.__wtClamped = true;
  }
  try { term.resize(cols, rows); } catch (e) { return false; }
  const bg = (term.options.theme && term.options.theme.background) || '#0C0C0C';
  if (!doc.getElementById('wt-fit-style')) {    // centre + letterbox = terminal bg
    const st = doc.createElement('style');
    st.id = 'wt-fit-style';
    st.textContent =
      'html,body{width:100%;height:100%;margin:0;overflow:hidden;background:' + bg + ';}' +
      '#terminal-container{position:absolute!important;inset:0!important;display:flex!important;' +
      'align-items:center!important;justify-content:center!important;background:' + bg + ';}' +
      '#terminal-container .xterm{position:static!important;}';
    doc.head.appendChild(st);
  }
  const screenEl = () => doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
  const el = screenEl();
  if (!el) return false;                        // xterm not painted yet -> retry
  // reads the element size right after resize/fontSize assuming xterm updates
  // the DOM synchronously (verified live: real ttyd + headless Chrome); the
  // bounded shrink loop below is the safety net if it ever lags by a frame.
  const r = el.getBoundingClientRect();
  const availW = win.innerWidth, availH = win.innerHeight;
  if (!r.width || !r.height || !availW || !availH) return false;  // hidden/0 -> retry
  const F0 = term.options.fontSize || 13;
  let F = Math.max(6, Math.min(40, Math.floor(F0 * Math.min(availW / r.width, availH / r.height))));
  term.options.fontSize = F;
  for (let i = 0; i < 8 && F > 6; i++) {        // bounded shrink so the grid never overflows
    const rr = (screenEl() || el).getBoundingClientRect();
    if (rr.width <= availW + 1 && rr.height <= availH + 1) break;
    term.options.fontSize = --F;
  }
  return true;
}
function applyFixedGrid(f) {                     // poll for window.term, fit, then watch resize
  if (!f) return;
  const win = f.contentWindow;
  if (!win) return;
  let tries = 0;
  const poll = () => {
    themeTerminal(win.term);                     // #643: Campbell palette + font, once term exists
    if (fitFixedGrid(win)) {
      if (!win.__wtResize) {                     // re-fit when the browser window resizes
        win.__wtResize = true;
        try { win.addEventListener('resize', () => fitFixedGrid(win)); } catch (e) {}
      }
      return;
    }
    if (++tries < 100) setTimeout(poll, 100);    // ttyd connects async after iframe load
  };
  poll();
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
// delete-word, not the browser. Feature-detected + gated on a SECURE CONTEXT:
// the Keyboard Lock API is only exposed on HTTPS/localhost, so over the plain-HTTP
// tailnet `navigator.keyboard` is undefined and the button is disabled — with a
// title that names the REAL reason (needs HTTPS) rather than a false "browser
// unsupported". Layer 1 (the close-confirm) still protects Ctrl+W there.
const KB_LOCK_KEYS = ['KeyW', 'KeyT', 'KeyN'];
function keyboardLockSupported() {
  return !!(document.documentElement.requestFullscreen
            && window.isSecureContext
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
    fsBtn.title = !window.isSecureContext
      ? 'Keyboard Lock vyžaduje HTTPS/localhost — cez HTTP tailnet Ctrl+W chráni potvrdenie pri zatváraní'
      : 'Fullscreen + Keyboard Lock nie je v tomto prehliadači podporený';
  }
}
window.addEventListener('keydown', onHotkey);   // Ctrl+Alt+1..9 when the bar is focused
preloadAll();                           // #586: connect every tab up front (instant switching)
if (CFG.sessions.length) activate(0);   // land in the first terminal, not a landing page
// #644: register the minimal NETWORK-ONLY service worker (Chromium
// installability). Best-effort — a registration failure never breaks the page.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(function () {});
}
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

def _ensure_credential(profile=profiles.OWNER):
    """`<login>:<random>` login credential for `profile`, generated once, mode
    600, in ~/.secrets. Returns the `user:pass` string. #584: the GATEWAY's
    login form validates against this (constant-time) — ttyd no longer sees it,
    so the credential is NO LONGER exposed in `ps`. #612: the login user + file
    are per-profile (owner -> `zbynek` in webterm_credential; david -> `david`
    in webterm_david_credential), so each profile is a SEPARATE auth realm. The
    owner reads the file ONCE for Bitwarden; David's is delivered via
    `secret show`. The existing credential is preserved on re-install."""
    import secrets
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)  # never world-traversable
    cred_path = _cred_path(profile)
    if cred_path.exists():
        cred = cred_path.read_text(encoding="utf-8").strip()
        if cred and ":" in cred:
            return cred
    cred = "%s:%s" % (_login_user(profile), secrets.token_hex(16))
    # Atomically create mode-600 (O_EXCL over a fresh temp, then rename) so the
    # 128-bit shell credential is NEVER briefly world-readable under the umask.
    tmp = cred_path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (cred + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(str(tmp), str(cred_path))
    return cred


def _retire_owner_credential():
    """#635: delete the now-dead owner password credential when the gateway moves
    to Cloudflare-Access mode (email OTP at the edge replaces the password, so the
    login form + credential file are retired — mirrors
    cli_webterm_david._retire_david_credential). Best-effort + idempotent — a
    missing file is the normal steady state, never an error. Returns True iff a
    file was actually removed."""
    cred = Path(str(WEBTERM_CRED_PATH))
    try:
        if cred.exists():
            cred.unlink()
            print("  webterm: retired dead password credential %s "
                  "(Cloudflare Access replaces it)." % cred, file=sys.stderr)
            return True
    except OSError as e:
        print("  webterm: could not remove old credential %s (%s) — harmless, "
              "Cloudflare Access is the gate now." % (cred, e), file=sys.stderr)
    return False


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
%(inventory_export)sexec ttyd -p %(ttyd_port)d -i %(ttyd_bind)s -b %(base_path)s -a -W \\
  python3 %(repo_dir)s/cli_webterm.py webterm-connect
"""


def render_webterm_launch_script(inventory_path=None, ttyd_port=None):
    """The ttyd launcher: loopback bind + `-b /t` base path, no basic-auth, no
    `-O` (#584 — the gateway is the sole entry + authenticator). #612: when
    `inventory_path` is given (the david profile), it is EXPORTED as the
    `WEBTERM_INVENTORY` env var that the ttyd child (`webterm-connect`) reads —
    NOT a client-injectable argv flag (ttyd's `-a` appends client `?arg=` values
    as argv, so an argv `--inventory` would be injectable — #612 review). That
    makes the ttyd child's allowlist physically the profile's scoped inventory.
    `ttyd_port` overrides the owner default (the david box uses its own loopback
    port). With no `inventory_path`/`ttyd_port` (owner), the emitted script is
    BYTE-IDENTICAL to pre-#612 — no env line, owner port."""
    inventory_export = ""
    if inventory_path:
        inventory_export = ("export WEBTERM_INVENTORY=%s\n"
                            % shlex.quote(str(inventory_path)))
    return _LAUNCH_TEMPLATE % {
        "ttyd_port": ttyd_port if ttyd_port is not None else WEBTERM_TTYD_PORT,
        "ttyd_bind": shlex.quote(WEBTERM_TTYD_BIND),
        "base_path": shlex.quote(WEBTERM_TTYD_BASE),
        "repo_dir": shlex.quote(str(REPO_DIR)),
        "inventory_export": inventory_export,
    }


def _render_webterm_unit():
    tmpl = WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_LAUNCH_PATH))


# #635: prepended to the OWNER gateway unit ONLY in Cloudflare-Access mode, so a
# human reading the installed file is not misled by the shared template's
# tailnet/password wording — the SAME honesty-bar correction cli_webterm_david's
# _DAVID_UNIT_NOTE makes for the david lane. Every claim it corrects is FALSE for
# the loopback + cloudflared + Access owner gateway.
_OWNER_ACCESS_UNIT_NOTE = (
    "# NOTE (#635, owner ROZHODNUTÉ 2026-08-22): this is the OWNER gateway in\n"
    "# CLOUDFLARE-ACCESS mode — it binds LOOPBACK (127.0.0.1) and is fronted by a\n"
    "# cloudflared tunnel for https://zbynek.newlevel.media/. The shared template's\n"
    "# 'bound to dev1's tailscale IP', 'the ONE tailnet-only entry point' and\n"
    "# 'security boundary is tailnet-only exposure' wording below is FALSE here: this\n"
    "# gateway binds LOOPBACK (not a tailscale IP) and is PUBLIC behind Cloudflare\n"
    "# Access (the edge email-OTP check is the boundary).\n"
    "#\n"
    "# AUTH: NO password / credential / login form / constant-time compare.\n"
    "# Cloudflare Access does email one-time-PIN verification at the EDGE before any\n"
    "# request reaches the tunnel; the gateway runs in --trust-access-header mode and\n"
    "# just trusts the Cf-Access-Authenticated-User-Email header. The template's\n"
    "# 'credential (…) validated constant-time' + 'Bitwarden login form' wording is\n"
    "# the OWNER (password) deployment being RETIRED here — it does NOT apply.\n"
    "#\n"
    "# 'failed logins rate-limited per source IP' does NOT hold at the origin: behind\n"
    "# cloudflared the gateway sees only 127.0.0.1, so per-real-IP brute-force\n"
    "# protection lives on the Cloudflare EDGE. And 'install REFUSES to provision\n"
    "# rather than bind a public interface' does not apply — Access mode binds\n"
    "# loopback and needs no tailscale IP at all.\n#\n")


def _render_webterm_gateway_unit(bind_ip, access_mode=False):
    """The same-origin gateway systemd --user unit: runs
    `cli_webterm_gateway.py` bound to `bind_ip` on the single gateway port,
    proxying /t/* to the loopback ttyd.

    Default (password mode): `bind_ip` MUST be a validated tailscale IP (see
    `_tailscale_ip`) — never 0.0.0.0/public — and the login form validates the
    credential (`--cred`).

    #635 `access_mode=True` (Cloudflare Access): the `--cred {{CRED_PATH}}` in the
    shared template's ExecStart is swapped for `--trust-access-header <header>`
    (the SAME transform David's lane uses, cli_webterm_david.render_david_gateway_
    unit) — NO password/credential is validated; Cloudflare email-OTP at the edge
    is the whole gate, and `bind_ip` is loopback (a cloudflared tunnel fronts it).
    The password-model `{{CRED_PATH}}` still present in the template's COMMENT is
    neutralised to n/a, AND `_OWNER_ACCESS_UNIT_NOTE` is prepended (mirroring
    cli_webterm_david's _DAVID_UNIT_NOTE) to correct every OTHER now-false
    tailnet/password claim in the shared template header, so a human reading the
    installed Access-mode unit is never misled. When off, the emitted unit is
    BYTE-IDENTICAL to the pre-#635 render."""
    tmpl = WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    if access_mode:
        import cli_webterm_access as access
        tmpl = tmpl.replace(
            "--cred {{CRED_PATH}}",
            "--trust-access-header " + access.WEBTERM_ACCESS_TRUST_HEADER)
        cred_sub = "n/a (Cloudflare Access — no credential)"
        # Defense in depth: in Access mode the gateway ALWAYS binds loopback
        # (cloudflared is the front), regardless of what the caller passes — the
        # render is the single place that guarantees no tailnet bind can leak here
        # (mirrors cli_webterm_david's hardcoded loopback bind).
        bind_ip = WEBTERM_TTYD_BIND
        note = _OWNER_ACCESS_UNIT_NOTE
    else:
        cred_sub = str(WEBTERM_CRED_PATH)
        note = ""
    return note + (tmpl.replace("{{BIND_IP}}", bind_ip)
                   .replace("{{GATEWAY_MODULE}}", str(WEBTERM_GATEWAY_MODULE))
                   .replace("{{GATEWAY_PORT}}", str(WEBTERM_GATEWAY_PORT))
                   .replace("{{DASH_INDEX}}", str(WEBTERM_DASH_INDEX))
                   .replace("{{CRED_PATH}}", cred_sub)
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

    # #635: Cloudflare-Access mode (owner go-live) binds LOOPBACK — a cloudflared
    # tunnel is the public front, so NO tailscale IP is needed and there is no
    # direct tailnet exposure. Default (password mode): resolve dev1's tailscale IP
    # ONCE as the GATEWAY's bind (ttyd is loopback, so this IP is only the
    # gateway's `--bind`); REFUSE LOUDLY if there is none — NEVER write a unit that
    # could bind 0.0.0.0.
    access_mode = OWNER_GATEWAY_ACCESS_MODE
    if access_mode:
        bind_ip = WEBTERM_TTYD_BIND          # 127.0.0.1 — cloudflared fronts it
    else:
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
    # #644: the installable-PWA assets (manifest + network-only SW + icons) next
    # to index.html; the gateway serves them from the dash dir. Lazy import to
    # avoid a module-level cycle (cli_webterm_pwa imports this module).
    import cli_webterm_pwa
    cli_webterm_pwa.write_pwa_assets(WEBTERM_DASH_DIR, profiles.OWNER)
    # Remove the old serve-fronted single-file dashboard (superseded — #579).
    (CLAUDE_DIR / "webterm-dashboard.html").unlink(missing_ok=True)
    # #635: Cloudflare-Access mode has NO password — retire the dead credential;
    # password mode ensures it as before.
    if access_mode:
        _retire_owner_credential()
    else:
        _ensure_credential()
    WEBTERM_LAUNCH_PATH.write_text(render_webterm_launch_script(), encoding="utf-8")
    os.chmod(WEBTERM_LAUNCH_PATH, 0o755)

    WEBTERM_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_SERVICE_DEST.write_text(_render_webterm_unit(), encoding="utf-8")
    WEBTERM_GATEWAY_SERVICE_DEST.write_text(
        _render_webterm_gateway_unit(bind_ip, access_mode=access_mode),
        encoding="utf-8")

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

    # #635: in Access mode the public front is the MANAGED cloudflared tunnel —
    # provision it here (prereq-gated no-op until the creds JSON exists) so a routine
    # install reconciles it, and it survives reboot. A tunnel skip does NOT fail the
    # gateway (the loopback gateway still serves for the tunnel to front). Lazy import
    # of the tunnel leaf avoids a module-level cycle.
    if access_mode and ok_all:
        import cli_webterm_tunnel
        cli_webterm_tunnel.setup_webterm_owner_tunnel(run=run)

    if ok_all:
        if access_mode:
            print("  webterm: gateway live (Cloudflare Access mode, #635) — bound "
                  "127.0.0.1:%d, fronted by cloudflared for https://"
                  "zbynek.newlevel.media/ (email-OTP gate; password retired). "
                  "ttyd loopback 127.0.0.1:%d behind /t."
                  % (WEBTERM_GATEWAY_PORT, WEBTERM_TTYD_PORT))
        else:
            print("  webterm: gateway live — http://%s:%d/ (tailnet-only, form "
                  "login user %r; credential in %s — read it once to save in "
                  "Bitwarden). ttyd loopback 127.0.0.1:%d behind /t."
                  % (bind_ip, WEBTERM_GATEWAY_PORT, WEBTERM_LOGIN_USER,
                     WEBTERM_CRED_PATH, WEBTERM_TTYD_PORT))
    return ok_all


def maybe_setup_webterm():
    """Install-time entry point (cmd_install). Dispatches by box profile: dev1
    -> owner gateway (unchanged); subdev -> the david developer gateway
    (cli_webterm_david, lazily imported to avoid a module-level cycle;
    prerequisite-gated no-op until go-live setup); any other box -> no-op."""
    prof = profiles.profile_for_host(os.uname().nodename)
    if prof == profiles.OWNER:
        return setup_webterm_service()
    if prof == profiles.DAVID:
        import cli_webterm_david
        return cli_webterm_david.setup_webterm_david_service()
    return False


def main(argv):
    if argv and argv[0] == "webterm-connect":
        return connect_main(argv[1:])
    sys.stderr.write("usage: cli_webterm.py webterm-connect <session-id>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
