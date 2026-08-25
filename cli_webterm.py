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
provisiuje bránu v Access režime — #663 UNIX-socket bind v account runtime dir +
`--trust-access-header` (heslo zaniká), fronted cloudflared tunelom (service:
unix:); default False necháva tailnet+heslo bránu byte-identickú.

Dve úlohy modulu, oddelené aby CONNECT cesta (beží per-terminal-open, ttyd child)
mala minimálne importy: (1) INVENTORY/PROVISIONING (install-time, dev1) generuje
inventár + dashboard + unit; (2) CONNECT (`python3 cli_webterm.py webterm-connect
<id>`) validuje id proti allowlistu a execne ssh/tmux. Žiadny `import airuleset`
na connect ceste.
"""
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
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
# #663: on the SHARED subdev box a TCP `127.0.0.1:<port>` origin is reachable by
# EVERY local unix account, so a peer account could forge the Access trust header at
# another lane's gateway port OR reach its auth-less ttyd directly. In Access mode
# the gateway + ttyd instead bind mode-0700 UNIX-domain sockets in the account's XDG
# runtime dir (/run/user/<uid>) — filesystem permissions become the account
# boundary. The systemd --user units reference `%t/<basename>` (%t == the account's
# $XDG_RUNTIME_DIR); the cloudflared config + the ttyd launch bash use the absolute
# /run/user/<uid>/<basename>. (Password mode keeps its TCP tailscale bind — a
# different, deliberately tailnet-reachable threat model.)
WEBTERM_GATEWAY_SOCK_BASENAME = "webterm-gateway.sock"
WEBTERM_TTYD_SOCK_BASENAME = "webterm-ttyd.sock"


def webterm_runtime_socket_abs(basename):
    """#663: the absolute path of a webterm UNIX socket in THIS account's XDG
    runtime dir (/run/user/<uid>/<basename>). Resolved from os.getuid() — the
    render runs AS the gateway account (owner on dev1, david1/marek on subdev), so
    the uid is the service account's — NOT from $XDG_RUNTIME_DIR (a push-driven
    non-login ssh shell may not set it). It is the SAME file the systemd unit reaches
    via `%t/<basename>` (systemd resolves %t to /run/user/<uid> for a --user unit)."""
    return "/run/user/%d/%s" % (os.getuid(), basename)
# The owner tmux session group on his own boxes (dev1/dev2). A stream account's
# own session is named after its unix user (#264 whoami auto-attach convention).
OWNER_GROUP = "zbynek"
WEBTERM_LOGIN_USER = "zbynek"
# #635 GO-LIVE GATE (owner ROZHODNUTÉ 2026-08-22): move zbynek.newlevel.media
# behind Cloudflare Access like David's side. When True, setup_webterm_service
# provisions the owner gateway in Cloudflare-Access mode — #663 UNIX-socket bind in
# the account runtime dir (a cloudflared tunnel fronts it, so NO direct tailnet
# exposure and NO tailscale IP is needed) + `--trust-access-header` (the
# password/login form is RETIRED,
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

# #672 REWORK (owner ruling 2026-08-25): there is NO per-stream grid any more.
# The original #672 gave a FOREIGN-STREAM tab its own LARGER browser grid
# (WEBTERM_STREAM_TERM_GRID 320x64) so the owner's -f ignore-size client was >=
# the stream window and tmux never cropped the footer. But the owner's browser
# viewport is FIXED (his lowest-res notebook, PWA zoom 100%), so a larger grid =
# the font-fit shrinks it = micro fonts on the m1..m6 tabs (unusable; owner:
# "nie je ani jeden dovod aby boli tmuxi a windows v nich rozdielne, vsetky
# musia maximalne vyhovovat mne"). REVERSED: EVERY tab renders at the ONE owner
# canonical grid (_webterm_term_grid() = 176x51). The foreign-stream footer crop
# is instead solved on the TMUX side by the fleet-wide `window-size manual` +
# `default-size 176x50` pin (cli_tmux_provisioning.apply_tmux_history_limit, on
# EVERY box incl. subdev), which pins every window to the owner size regardless
# of any client -- so the owner's 176x51 -f ignore-size client shows every
# window whole (footer included) and David/Marek get the owner's size (a
# harmless cosmetic dark border, which the owner explicitly wants; this reverses
# the #648 "never degrade David" invariant by owner decree).

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
    the marek realm is `marek`, the owner realm is `zbynek` (WEBTERM_LOGIN_USER).
    Used by the go-live message (the david/marek gateways are Cloudflare-Access
    mode, so no credential is generated for them)."""
    if profile == profiles.DAVID:
        return "david"
    if profile == profiles.MAREK:
        return profiles.MAREK_GATEWAY_USER
    return WEBTERM_LOGIN_USER


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
    if profile == profiles.MAREK:
        return profiles.marek_inventory()
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
            # #680: carry the committed PUBLIC host-key pin (spinbike-vps today)
            # into the inventory so the connect child -- which must NOT import
            # the fleet table (the inventory JSON IS its security allowlist) --
            # can verify the raw public IP STRICTLY via cli_remote's #669 helper.
            # PUBLIC key material, safe in the inventory JSON; absent (None) for
            # every tailscale/subdev host, which stays =no.
            "host_keys": h.get("host_keys"),
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
#      `default-size 176x50`) AND attached the (then-existing) clone with
#      `-f ignore-size` so the webterm could never influence sizing. #613
#      REOPEN removed both on a theory that measured the BROWSER client (wrong
#      client) -- switching to `latest` let a SMALLER browser re-pin the
#      owner's real windows, so his larger Windows Terminal was left rendering
#      a DARK unused region (his SURFACE; the browser the CAUSE). #613
#      REOPEN-2 (owner directive 2026-08-22) RESTORES the fixed-size
#      invariant: `window-size manual` + `default-size 176x50`
#      (cli_tmux_provisioning) pins every window regardless of any client, and
#      the webterm attach carries `-f ignore-size` so a box still running the
#      first-reopen `latest` server is fixed immediately (no restart). No
#      client resizes another's window -- no "resizovanie hore-dole". The
#      browser's OWN appearance at the fixed grid (no dark area) is solved on
#      the BROWSER side (the dashboard fit-to-fixed-grid JS), never by
#      resizing tmux.
#   2. resolve the base session to JOIN: exact `=$P` -> group survivor -> the
#      single existing session -> else create `$P` fresh;
#   3. #613 REOPEN-3 (supervisor finding, 2026-08-23, issue #613 comment
#      5387073996): an existing base used to be JOINED via a THROWAWAY
#      GROUPED CLONE — a second, same-group session (`new-session -d -t
#      <base> -s <base>-web-$$`), its own per-session `destroy-unattached on`
#      hook + `mouse on` (#615), attached with `-f ignore-size`, killed on
#      disconnect (trap). That topology is GONE — it silently broke
#      `Ctrl+B w` (tmux's own window-chooser, `choose-tree`/`choose-window`)
#      for the OTHER client already attached to the base (the owner's
#      ssh/Windows Terminal) the instant the webterm browser also joined:
#      the chooser's active mode still received keys (Enter still worked)
#      but painted NOTHING — a black screen. Measured on an isolated scratch
#      tmux 3.7b server: one client alone renders ~4500 printable chars
#      after Ctrl+B w (full tree); the SAME client with a grouped clone ALSO
#      attached renders ~300 (status line only, dead) — reproduced across
#      three chooser variants (`choose-tree -Zw`/`-w`, `choose-window`) and
#      two client sizes, ruling out both the `-Z` zoom flag and any size
#      mismatch. The grouped-CLONE topology itself was the cause.
#
#      THE FIX: join an existing base by attaching to it DIRECTLY --
#      `tmux attach-session -t "$T" -f ignore-size` -- no clone, no `$$`
#      session name, no disconnect trap, no per-session `destroy-unattached`
#      hook to arm. Verified live (same comment): a 49-row ssh client
#      renders 4681 chars and a web client renders 14669, BOTH after
#      Ctrl+B w, with every window staying the owner's fixed 176x50 for the
#      whole test — see tests/test_webterm_ctrlbw_darkening.py for the
#      mechanical regression lock (RED on the removed clone shape, GREEN on
#      this direct attach) plus a structural lock against the clone shape
#      ever coming back.
#
#      `-f ignore-size` is KEPT on the direct attach for the SAME reason
#      #586/#613 REOPEN-2 put it on the (now-removed) clone: together with
#      the conf's `window-size manual` (cli_tmux_provisioning, version-gated)
#      it is what holds the owner's fixed-size invariant ("dohodli sme sa ze
#      budeme mat fixnu velkost terminalov pre vsetkych") -- `window-size
#      manual` already pins every window regardless of any client, and
#      `ignore-size` is belt-and-suspenders for a box still running an older
#      server. Nothing about THAT invariant changed; only the clone
#      TOPOLOGY that used to carry it is gone.
#
#      The base session is NEVER destroyed by a browser disconnect: there is
#      no more a clone to arm a per-session `destroy-unattached on` onto
#      (#591's own fix), and there never is (#591 also removed) a GLOBAL
#      `destroy-unattached` -- the base keeps tmux's factory default (`off`),
#      so a client detaching (browser OR ssh) just leaves; nothing in this
#      file arms a sweep against the base. Proven live in
#      TestBaseSessionSurvivesBrowserDisconnect
#      (tests/test_webterm_ctrlbw_darkening.py).
#
#      DELIBERATE TRADE-OFF (recorded on #613's design comment, not to be
#      reopened here): the browser and the owner's ssh client now attach to
#      the SAME session, so they share its CURRENT window -- switching
#      windows in one moves the other. The removed clone kept those
#      independent (each grouped client keeps its own current-window
#      pointer), but that independence was a SIDE EFFECT of the old
#      resize-protection shape, never something the owner asked for, while a
#      broken window-chooser was reported repeatedly -- a working switcher
#      beats independent views.
#
#      MOUSE (#615) on the shared base -- session-scoped `mouse on` on
#      connect, the session-local override UNSET on disconnect (trap, below;
#      #648 FIX LANDED, Option 2). `mouse` is a tmux SESSION option (never
#      per-client): #615's `mouse on` once lived on the throwaway clone (an
#      independent session), so #613's direct attach retargeted it to `$T`
#      itself -- the SAME single session the owner's ssh is also attached to.
#      #646 CONTEXT: cli_tmux_provisioning now sets `set-option -g mouse on`
#      as the managed FLEET-WIDE default (the owner WANTS the wheel to reach
#      tmux scrollback over ssh -- the very "kolieskom cez ssh sa to blbo
#      pouziva" #267 built the Shift+PageUp keybind for). So the connect-side
#      `mouse on` here is REDUNDANT with that global on a provisioned box --
#      but it is KEPT so a box WITHOUT the #646 conf still gets browser
#      wheel->scrollback.
#      #648 FIX (verified on an isolated tmux server): the disconnect trap
#      MUST NOT force `mouse off` -- a session-LOCAL `mouse off` WINS over
#      `-g mouse on`, so a forced-off revert left the owner's OWN ssh session
#      (attached to the SAME session) `mouse off` after every webterm
#      connect+disconnect until it restarted. The trap therefore UNSETS the
#      session-local override (`set-option -u -t "$T" mouse`), restoring
#      inheritance: the effective value falls back to the #646 global (`on`),
#      or to tmux's factory default off on an unprovisioned box -- never a
#      forced value. (Rejected: dropping the block entirely and relying on
#      the #646 global -- that would lose browser wheel->scrollback on a box
#      the #646 conf never reached.)
#      WHAT STAYS TRUE: this join path still NEVER emits a global `-g mouse`
#      -- the fleet default is cli_tmux_provisioning's job (#646), not this
#      join script's, so the `assertNotIn("set-option -g mouse")` lock in
#      tests/test_webterm.py stays valid; and the fresh-base fallback (no
#      existing session) still touches no mouse option at all. The trap is
#      NOT reference-counted against multiple simultaneous webterm tabs to
#      the SAME target -- the dashboard's own design (#579) is one tab per
#      DISTINCT target, so an unconditional on-connect/unset-on-disconnect
#      toggle matches how the feature is used; a second simultaneous connect is a
#      rare, self-correcting edge (a reconnect re-arms for its own duration).
#      While connected, the owner's ssh client (attached to the SAME session
#      via the separate fleet ssh auto-attach convention below, not this
#      script) carries tmux mouse-reporting mode -- which, post-#646, simply
#      matches the fleet default; native click-drag selection then needs
#      Shift+drag on most terminals (incl. Windows Terminal), stated as the
#      general tmux/terminal convention, not verified against this fleet's
#      actual client software.
# Mirrors the fleet ssh auto-attach convention
# (cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK). Reused for local (dev1) and
# remote (ssh) alike. The `-A` fresh-base fallback (no existing session at
# all) is the owner's own real, ONLY view -- unaffected by any of the above,
# and (like before) never `-f ignore-size` (it must size its own windows).
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
    # #615 (SCOPED to this connection -- see the block comment above for the
    # full record + the #646/#648 interaction): mouse mode so the browser's
    # scroll-wheel reaches tmux copy-mode. Post-#646 this session-scoped set
    # is REDUNDANT with the fleet `-g mouse on`, but KEPT so a box without
    # the #646 conf still gets browser wheel->scrollback. Session-scoped
    # (`-t "$T"`), never `-g` (global) -- the fresh-base fallback below stays
    # untouched.
    'tmux set-option -t "$T" mouse on; '
    # Revert on disconnect by UNSETTING the session-local override (#648,
    # Option 2): `set-option -u` restores inheritance so the effective value
    # falls back to the #646 global `-g mouse on` (or factory default off on
    # an unprovisioned box) -- NEVER a forced `mouse off`, which as a
    # session-LOCAL override would win over `-g mouse on` and leave the
    # owner's own ssh session mouse-off (the #648 bug). SAME shell-level
    # deferred-expansion trap pattern the removed clone used for its own
    # cleanup (`$T` is expanded when the trap FIRES, not when it is armed,
    # exactly like the old `$C` trap). This does NOT create any session
    # (unlike the removed clone), so it cannot reproduce the #613 REOPEN-3
    # chooser bug -- only the GROUPED-CLONE topology caused that, never "a
    # trap exists" alone.
    "trap 'tmux set-option -u -t \"$T\" mouse 2>/dev/null || true' EXIT HUP INT TERM; "
    # #613 REOPEN-3: DIRECT attach, no clone (see the block comment above
    # for the full root-cause + fix + trade-off record). NOT `exec`ed --
    # the wrapper shell must stay alive so the trap above can run once the
    # client detaches (mirrors the pre-clone-removal code, which never
    # exec'd this branch either). The explicit `exit` after keeps
    # execution from ever falling through to the fresh-create fallback
    # once a real join was resolved.
    'tmux attach-session -t "$T" -f ignore-size; '
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
    password path). Interactive variant: force a PTY (-t), never write the
    USER's known_hosts (an unpinned host uses /dev/null; a #680-pinned host
    reads a freshly materialized temp pin instead, with UpdateHostKeys=no so ssh
    never appends to it either), fast connect timeout so a dead host fails
    visibly. DRIFT GUARD:
    the identity-vs-sshpass DECISION is the same rule those two sites use — if the
    fleet's auth convention ever changes (password rotation, a new scheme), both
    those sites AND this one must move together; `test_webterm.py::
    test_identity_decision_matches_deploy_loop` fails on a decision drift."""
    # #680: a target carrying a committed PUBLIC host-key pin (`host_keys`,
    # threaded through the inventory from the fleet -- spinbike-vps today) is
    # verified STRICTLY against that pin via the #669 helper, so the owner's
    # interactive shell to the raw public IP can no longer be MITM'd (the pin
    # file is materialized in THIS connect child at connect time, never
    # persisted into the inventory JSON). Every unpinned tailscale/subdev host
    # keeps the unchanged `=no` + `UserKnownHostsFile=/dev/null` posture: a MITM
    # on a private address is implausible, and dropping /dev/null there would
    # give the interactive shell the deploy leg's changed-key password-auth
    # downgrade + a "Permanently added" warning for zero security gain.
    if entry.get("host_keys") is not None:
        from cli_remote import host_key_check_opts  # the ONE #669 pin source
        hostkey_opts = host_key_check_opts(entry)
    else:
        hostkey_opts = ["-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null"]
    common = hostkey_opts + ["-o", "ConnectTimeout=10", "-t"]
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
# window size (`window-size manual` + `default-size 176x50`) and every webterm
# attach is `-f ignore-size` (#613 REOPEN-3: a DIRECT attach now, no clone --
# see the `_ATTACH_BODY` header comment), so no tab (hidden or active) can EVER
# resize a window -- keeping every tab connected is unconditionally safe. On
# the BROWSER side, each
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
    inventory id/label. #612: the OWNER dev1 local entry (no `user`) keeps the
    `dev1` short-circuit; a NON-owner local entry (marek's `marek-subdev`, with
    `user="marek"`) keeps its own id so it aliases via its user (`marek`), never
    mislabelled `dev1`."""
    box_name = ("dev1" if entry.get("local") and not entry.get("user")
                else (entry.get("id") or entry.get("label") or ""))
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


# --------------------------------------------------------------------------- #
# #661 -- per-domain OWNER-DEFINED dashboard tab lists. The owner never asked for
# a tab per fleet target (#579/#612 surfaced OTHER people's personal accounts on
# his own dashboard); the tab set is now an EXCLUSIVE, owner-defined list per
# domain: a domain renders EXACTLY the inventory ids listed for it, in the
# owner's order, and an entry renders on NO dashboard unless explicitly listed.
# The list VALUES are the OWNER's to edit; the mechanism is ours.
#
# This is tab VISIBILITY, NOT an auth boundary: the connect allowlist
# (WEBTERM_INVENTORY_PATH) stays the FULL fleet, so the owner keeps webterm
# reachability to any session he already has SSH access to (via dev1's keys), and
# Cloudflare Access remains the sole auth layer. Filtering here only decides which
# tabs the dashboard SHOWS.
#
# Keyed by the gateway's login user (== the domain's human): "zbynek" for the
# owner gateway (WEBTERM_LOGIN_USER, zbynek.newlevel.media), "david"/"marek" for
# theirs. A human with no list, or a render with human=None (the david gateway
# path, whose scoped cli_webterm_profiles.david_inventory is ALREADY exactly
# david1-4 + codex-bridge), is not filtered here. The ids are the sanitized fleet
# inventory ids (_sanitize_id: marek@subdev -> marek-subdev).
WEBTERM_DASHBOARD_TABS = {
    # zbynek.newlevel.media -- owner ROZHODNUTÉ 2026-08-24, EXACT order (verbatim
    # "dev1, dev2, gk, m1..m6, d1, d2, miva, sb"). EXCLUDES montalu7/8, david3/4,
    # simap1, marek@subdev, stepan@forestshop-dev, admin@forestshop-dev.
    "zbynek": [
        "dev1", "dev2", "gatekeeper",
        "montalu1-subdev", "montalu2-subdev", "montalu3-subdev",
        "montalu4-subdev", "montalu5-subdev", "montalu6-subdev",
        "david1-subdev", "david2-subdev", "miva1-subdev", "spinbike-vps",
    ],
    # marek.newlevel.media -- Marek's set (owner #661 rework 2026-08-25): his
    # own subdev account first (default-active tab), his montalu4 stream, his
    # `marek` tmux sessions on dev1 + dev2, and his forestshop VPS (handled
    # like the owner's spinbike `sb` tab). The ids are the MAREK LANE inventory
    # ids (cli_webterm_profiles.marek_inventory) -- his lane render consumes
    # this list via LaneSpec.dashboard_human="marek", so it dictates order +
    # exclusivity there. NB: `dev1`/`dev2` here name MAREK's lane entries
    # (which attach HIS `marek` tmux group), not the fleet's owner entries --
    # the two id namespaces meet only in tests, never in a prod render.
    "marek": ["marek-subdev", "montalu4-subdev", "dev1", "dev2", "forestshop"],
    # david.newlevel.media -- David's working accounts. The david GATEWAY renders
    # its own physically-scoped inventory (cli_webterm_profiles.david_inventory,
    # ids david1..4 + codex-bridge) and does NOT consume this list; this records
    # the same per-domain policy declaratively (and is what a full-fleet render
    # for "david" would filter to -- exercised by the tests).
    "david": ["david1-subdev", "david2-subdev", "david3-subdev", "david4-subdev"],
}


def _dashboard_tab_list(human):
    """The owner-defined ordered tab-id list for `human`'s dashboard, or None if
    `human` has no configured list (caller renders the given inventory
    unfiltered)."""
    return WEBTERM_DASHBOARD_TABS.get(human)


def entries_for_tab_list(inventory, tab_ids):
    """The inventory entries named by `tab_ids`, in the LIST's order, dropping any
    id not present in `inventory`. EXCLUSIVE -- an entry not named is never
    returned; ORDER is the list's, never the #579 WT `_tab_order_key` sort (the
    owner gives an explicit order this must honour)."""
    by_id = {e["id"]: e for e in inventory}
    return [by_id[i] for i in tab_ids if i in by_id]


def _tab_sessions(inventory, preserve_order=False):
    """Inventory entries as tab descriptors (short alias + full-id title). By
    default sorted in the owner's stable Windows-Terminal order (#579);
    `preserve_order=True` keeps the given inventory order untouched -- used when
    an EXCLUSIVE owner-defined tab list already dictates the exact order (#661)."""
    tabs = []
    for e in inventory:
        # #672 REWORK (owner ruling 2026-08-25): NO per-tab grid override -- every
        # tab renders at the ONE owner canonical grid (CFG.term_cols/term_rows =
        # _webterm_term_grid()); the foreign-stream footer crop is solved on the
        # tmux side (window-size manual pin), not by a bigger browser grid.
        t = {"id": e["id"], "alias": _short_alias(e),
             "title": e.get("label") or e["id"]}
        tabs.append(t)
    if not preserve_order:
        tabs.sort(key=lambda t: _tab_order_key(t["alias"]))
    return tabs


# --------------------------------------------------------------------------- #
# #677: per-box U (a question/approval waiting on the OWNER) -> tab red dots.
# Ground truth is each box's tickets-status cache (`user_waiting`, the #512/#526 U
# bucket). The dev1 gateway aggregates it via a low-frequency ssh PULL of the
# boxes' caches (ZERO gh -- the box already paid the gh cost writing them), writes
# ~/.claude/webterm-u-status.json, serves it at /u-status, and the dashboard polls
# it to render/remove a small corner dot per tab. #584 posture (loopback ttyd,
# same-origin auth-gated gateway) unchanged; no new port; a minutes-fresh hint.
# --------------------------------------------------------------------------- #

WEBTERM_U_STATUS_PATH = CLAUDE_DIR / "webterm-u-status.json"

# #686: how fresh a tickets-status cache must be to contribute to a box's U. A
# cache is only ever refreshed by the OWNER of its cwd (the statusline shim on
# render, TTL 120s; plus the watchdog's 60s-cadence re-warm of an ACTIVE cwd,
# airuleset.py `_watchdog_backlog_fetch` -> `_spawn_refresh`), so a DEAD session's
# cache (a removed worktree, or an exited session in a real repo dir) FREEZES its
# `user_waiting` forever and would inflate the box U indefinitely (footer U0 /
# /u-status 8 -- the red dot never clears). 30 min = 15x the 120s TTL: an enormous
# margin over any live-session refresh cadence (even a live-but-idle WAITING
# session, the case that legitimately has U>0, whose render hiccups for minutes
# keeps its dot), yet ~20x below the smallest observed dead entry (>=10h). Slightly
# more generous than the sibling `_BACKLOG_STATUS_CACHE_MAX_AGE_S` (15 min, the
# same cache) because a false-negative here loses a navigation dot, not just a
# fallback to a live count. Fail-safe direction (cli_webterm.py `_read_box_u`): a
# stale / undatable entry is DROPPED (dot lost, self-heals on next refresh), never
# summed as a false positive.
_U_FRESH_MAX_AGE_S = 30 * 60

# The inline python reader the collector runs over ssh on each REMOTE box -- a
# self-contained mirror of `_box_u_count` (NO dependency on the remote airuleset
# version, so it works across a deploy window). A FIXED string (no client input),
# passed shell-quoted, so it carries no injection surface. Kept equal to
# `_box_u_count` by tests/test_webterm_u_status.TestUReaderSnippet -- including the
# #686 freshness filter (the max-age constant is baked in below).
_U_READER_SNIPPET = (
    "import glob,json,os,time\n"
    "M=%d;N=time.time()\n"
    "def _u(p):\n"
    " try:\n"
    "  d=json.load(open(p));t=d.get('ts')\n"
    "  if not isinstance(t,(int,float)) or not (0<=N-t<=M):return 0\n"
    "  u=d.get('user_waiting');return u if isinstance(u,int) and u>0 else 0\n"
    " except Exception:return 0\n"
    "print(sum(_u(p) for p in glob.glob(os.path.expanduser('~/.claude/tickets-status/*.json'))))"
) % _U_FRESH_MAX_AGE_S


def _box_u_count(home=None, now=None):
    """Sum `user_waiting` (the statusline U bucket: needs-answer / needs-decision
    / needs-acceptance / needs-owner-action) across THIS box's tickets-status
    caches. Pure -- no gh, no network. A missing / None / non-int field counts 0;
    a bad cache file is skipped, never fatal. `home` overrides ~ (tests); `now`
    overrides the clock (tests).

    #686: only a FRESH cache contributes -- an entry whose `ts` is absent /
    non-numeric / older than `_U_FRESH_MAX_AGE_S` (or implausibly future-dated) is
    skipped, so a DEAD session's frozen `user_waiting` (a removed worktree, or an
    exited session in a real repo dir) can never inflate the box U. Fail-safe
    direction: undatable / stale -> dropped, never a false positive. The
    `0 <= now - ts` lower bound rejects a future-dated ts for doctrine parity with
    the sibling `airuleset._watchdog_backlog_fetch` (#459) -- a same-box read never
    sees a future ts, so this only guards clock skew / a synced cache."""
    base = Path(home) if home else Path.home()
    now = time.time() if now is None else now
    total = 0
    for p in (base / ".claude" / "tickets-status").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                        # unreadable/broken cache -> skip, never fatal
        if not isinstance(d, dict):
            continue
        ts = d.get("ts")
        if not isinstance(ts, (int, float)) or not (0 <= now - ts <= _U_FRESH_MAX_AGE_S):
            continue                        # #686: stale / undatable / future -> drop
        u = d.get("user_waiting")
        if isinstance(u, int) and u > 0:
            total += u
    return total


def _ssh_read_prefix(entry):
    """A NON-interactive ssh prefix for reading a remote box's U -- mirrors the
    identity-vs-sshpass DECISION of `_ssh_interactive_prefix` / the deploy loop,
    but WITHOUT a PTY (-t) and with BatchMode + a short ConnectTimeout so a dead
    box fails fast instead of prompting or hanging the collection."""
    common = ["-o", "StrictHostKeyChecking=no",
              "-o", "UserKnownHostsFile=/dev/null",
              "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    identity = entry.get("identity")
    if identity:
        return ["ssh", "-i", os.path.expanduser(identity)] + common
    return ["sshpass", "-p", "newlevel", "ssh"] + common


def _read_box_u(entry, run, timeout_s):
    """The U for one inventory entry, or None on ANY failure (unknown != 0, so a
    transiently-unreachable box loses its dot rather than showing a false zero).
    dev1 (local) reads its own caches directly; a remote box runs the inline
    reader over ssh (the remote command is ONE shell-quoted argument, so the
    remote shell passes the fixed snippet to python intact)."""
    if entry.get("local"):
        try:
            return _box_u_count()
        except Exception:
            return None
    target = "%s@%s" % (entry.get("user"), entry.get("host"))
    remote_cmd = "python3 -c " + shlex.quote(_U_READER_SNIPPET)
    argv = _ssh_read_prefix(entry) + [target, remote_cmd]
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout_s)
    except Exception:                       # timeout / OSError / sshpass absent
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except (ValueError, TypeError):
        return None


def collect_fleet_u(entries, run=None, timeout_s=8, max_workers=8):
    """Read U per box for `entries` (bounded-parallel, per-box timeout), returning
    `{inv_id: U}` for the boxes that read OK. A box that errors / times out /
    returns a non-int is OMITTED (never a false 0). `run` (subprocess.run) is
    injectable for tests."""
    run = run or subprocess.run
    import concurrent.futures as _cf

    def _one(e):
        return e["id"], _read_box_u(e, run, timeout_s)

    out = {}
    workers = max(1, min(max_workers, len(entries) or 1))
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for eid, u in ex.map(_one, entries):
            if isinstance(u, int):
                out[eid] = u
    return out


def _owner_u_entries():
    """The owner dashboard's tab targets (WEBTERM_DASHBOARD_TABS['zbynek']) with
    their reach info, for the U collection -- the SAME inventory + tab-list filter
    the dashboard renders, so the collected ids match the tab ids exactly."""
    inv = webterm_inventory(profiles.OWNER)
    return entries_for_tab_list(inv, _dashboard_tab_list(WEBTERM_LOGIN_USER) or [])


def cmd_webterm_u_collect(argv):
    """Collect per-box U for the owner dashboard tabs and write the aggregate to
    WEBTERM_U_STATUS_PATH atomically. Spawned DETACHED by the gateway on a stale
    /u-status read, so it never blocks a request. Best-effort: a box that can't be
    read is simply omitted (no dot). Returns 0 always (a collection failure must
    not crash the spawn)."""
    try:                                    # #677 review 🔵: the whole collect is
        entries = _owner_u_entries()        # guarded, so the docstring's "always 0"
        u = collect_fleet_u(entries)        # holds even for a malformed entry.
    except Exception as e:
        sys.stderr.write("webterm-u-collect: collection failed: %r\n" % e)
        u = {}
    payload = {"u": u, "ts": int(time.time())}
    try:
        WEBTERM_U_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # #677 review 🔵: a PER-PROCESS unique tmp (a slow fleet read can outlast the
        # 60 s in-memory spawn guard, so two collectors can overlap) -> no torn write.
        tmp = WEBTERM_U_STATUS_PATH.with_name(
            WEBTERM_U_STATUS_PATH.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(str(tmp), str(WEBTERM_U_STATUS_PATH))
    except OSError as e:                     # disk-pressure is exactly when this runs
        sys.stderr.write("webterm-u-collect: could not write %s: %s\n"
                         % (WEBTERM_U_STATUS_PATH, e))
    return 0


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


def render_dashboard_html(inventory, ttyd_base=None, term_grid=None, human=None):
    """The single-page tabbed terminal UI. `ttyd_base` is the SAME-ORIGIN ttyd
    base path under the #584 gateway (`/t`); the page's JS builds each tab's
    iframe src as `<ttyd_base>/?arg=<id>` on first activation — same-origin, so
    the per-iframe Ctrl+Alt+N forwarder works while typing. `term_grid` is the
    fixed (cols, rows) client grid the browser xterm is force-fit to (#613
    REOPEN-2, defaults to `_webterm_term_grid()` = the owner's fixed terminal).

    #661: when `human` names a domain with an owner-defined tab list
    (WEBTERM_DASHBOARD_TABS), the inventory is filtered+ordered to EXACTLY that
    list — unlisted entries are not rendered, and the tab ORDER is the owner's
    list order (not the WT sort). `human=None` (the david gateway path, whose
    inventory is already physically scoped) renders the given inventory
    unfiltered. Filtering is tab VISIBILITY only; the connect allowlist is
    unaffected (see WEBTERM_DASHBOARD_TABS)."""
    ttyd_base = (ttyd_base or "").rstrip("/")
    term_cols, term_rows = term_grid or _webterm_term_grid()
    # #661: `human=None` = the david gateway path (its inventory is already
    # physically scoped) -> render unfiltered. A TRUTHY human ALWAYS filters to
    # its owner-defined list; an unconfigured one FAILS CLOSED to an empty tab set
    # (`... or []`) rather than leaking the full fleet onto a personal domain --
    # the exact bug this ticket fixes (#661 review 🔵). Prod only ever passes
    # "zbynek" or None; this bounds a future mis-wiring to a loud-empty dashboard.
    if human is None:
        tabs = _tab_sessions(inventory)
    else:
        inventory = entries_for_tab_list(inventory, _dashboard_tab_list(human) or [])
        tabs = _tab_sessions(inventory, preserve_order=True)

    def _tab_button(i, t):
        # #661 (owner ruling 2026-08-25): the #582 ordinal badge (a visible 1-9
        # chip on the first nine tabs) is REMOVED — it added no needed info and
        # ate space. The Ctrl+Alt+1..9 SHORTCUT stays fully functional (onHotkey
        # below); only the visible digit went. The green ▸ .ico separator stays.
        # #677: a per-tab corner dot, hidden until the tab's box has U > 0
        # (toggled by applyUStatus). It carries no data -> no injection surface.
        return ('<button class="tab" data-idx="%d" title="%s">'
                '<span class="ico">&#9656;</span><span class="al">%s</span>'
                '<span class="udot"></span></button>'
                % (i, _html_escape(t["title"]), _html_escape(t["alias"])))

    buttons = "\n".join(_tab_button(i, t) for i, t in enumerate(tabs))
    # #677 review 🟡: the U-dot poll is OWNER-ONLY. The david/marek gateways render
    # the SAME dashboard (human=None / "marek"); if they polled /u-status their
    # gateway would spawn an owner-fleet collector AS the sub-dev account (a
    # cross-tenant ssh read via the shared password). Gate the poll ACTIVATION on
    # this flag so only the owner dashboard (human == WEBTERM_LOGIN_USER) ever polls
    # -- the gateway route is ALSO default-off and owner-enabled (defence in depth).
    cfg = {"ttyd_base": ttyd_base, "sessions": tabs,
           "term_cols": term_cols, "term_rows": term_rows,
           "u_status": human == WEBTERM_LOGIN_USER}
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
<title>fleet terminal</title><!-- #655: real domain set client-side from location.hostname (below) -->
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
  position: relative; /* #677: anchor for the corner U dot */
  padding: 6px 12px 6px 16px; border: 1px solid transparent; border-bottom: none; /* #661: left 12->16px, tab names indented a touch further from the left edge */
  border-radius: 7px 7px 0 0; background: #1b1b1b; color: #CCCCCC;
  font: inherit; line-height: 1; max-width: 170px; flex: 0 0 auto; }
/* #677: a small restrained corner dot on a tab whose box has a question/approval
   waiting on the owner (U > 0). Campbell brightRed with a 1px body-coloured ring
   so it reads clearly against any tab state; hidden until applyUStatus adds
   .has-u, and removed again the moment U falls to 0. */
.tab .udot { position: absolute; top: 3px; right: 4px; width: 6px; height: 6px;
  border-radius: 50%; background: #E74856; box-shadow: 0 0 0 1px #0C0C0C;
  display: none; }
.tab.has-u .udot { display: block; }
/* #661: unselected tab text lightened from #9a9a9a to the Campbell foreground
   #CCCCCC (owner: hard to read); hover brightens to #F2F2F2; the ACTIVE tab stays
   the lightest (#F2F2F2) and is further set apart by its #0C0C0C body-matching
   background + border. Restrained Campbell greys, never garish. */
.tab:hover { background: #262626; color: #F2F2F2; }
.tab.active { background: #0C0C0C; color: #F2F2F2; border-color: #2b2b2b; }
.tab .ico { color: #13A10E; font-size: 11px; }
.tab .al { overflow: hidden; text-overflow: ellipsis; }
#nav { position: sticky; left: 0; z-index: 1; display: inline-flex; gap: 2px;
  padding-right: 4px; margin-right: 2px; background: #0C0C0C; flex: 0 0 auto; }
.cyc { cursor: pointer; border: 1px solid #2b2b2b; border-radius: 6px;
  background: #1b1b1b; color: #9a9a9a; font: inherit; line-height: 1;
  padding: 6px 9px; }
.cyc:hover { background: #262626; color: #CCCCCC; }
#frames { position: relative; flex: 1 1 auto; }
#frames iframe.term { position: absolute; inset: 0; width: 100%; height: 100%;
  border: 0; background: #0C0C0C; }
/* #671 REWORK (owner ruling 2026-08-25, verbatim "znova si napchal text na copy
   paste ktory zerie spodnu cast !!!!! nic take som nechcel, potrebujem hlavne
   pracovnu plochu nie tvoje blbe vysvetlivky"): the copy/paste footer hint strip
   is removed entirely so the terminal reclaims the height. The select/copy/paste
   FUNCTIONALITY (attachClipboard) stays; only the visible strip is gone. */
</style>
</head>
<body>
<div id="tabbar">
<span id="nav"><button class="cyc" id="fs" title="Fullscreen — Ctrl+W pôjde do terminálu (Keyboard Lock)">&#9974;</button></span>
@@BUTTONS@@
</div>
<div id="frames"></div>
<script>
const CFG = @@CFG_JSON@@;
// #672 REWORK (owner ruling 2026-08-25): ONE canonical grid for every tab, so
// CFG.term_cols/term_rows are a plain constant (no per-tab defineProperty
// getter). fitFixedGrid/fillFixedGrid read them unchanged; the foreign-stream
// footer crop is solved on the tmux side (window-size manual pin), not here.
// #643: the Campbell palette (single source of truth) + a Cascadia-ish system
// monospace stack (no external font fetch — CSP/Cloudflare-Access safe). Applied
// to each terminal via `term.options.theme` on the same-origin `window.term`
// (the #613 integration point), so it is DAEMON-AGNOSTIC (ttyd AND a future
// GoTTY both expose `window.term`) — never baked into a ttyd -t theme= flag.
const CAMPBELL_THEME = @@THEME_JSON@@;
const TERM_FONT_STACK = '"Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", Menlo, Monaco, "Liberation Mono", monospace';
// #655: dynamic document title from the ACTUAL serving host. The old hardcoded
// legacy domain was NXDOMAIN; the live hosts are zbynek/david.newlevel.media, and
// this static file is served across BOTH. location.hostname is the honest
// per-viewer value, so the PWA/browser window title always names the real domain.
try { document.title = location.hostname + ' — fleet terminal'; } catch (e) {}
// #678: caps that BOUND the residual NATIVE-cell fill in fillFixedGrid. The fixed
// 176x51 grid letterboxes on any viewport whose aspect != the grid's; fillFixedGrid
// fills the residual by growing the REAL xterm cell -- letterSpacing (width) +
// lineHeight (height), NEVER a CSS transform (which would scale getBoundingClientRect
// but not xterm's cssCellHeight and break mouse hit-testing, the #678 regression).
// The fontSize min-fit does the crisp bulk. These cap the per-axis cell stretch so an
// extreme viewport (a phone) degrades to a residual letterbox instead of a grotesque
// stretch.
const WT_FILL_MAX_CELL_STRETCH = 1.5;   // cell WIDTH (letterSpacing) may grow up to 1.5x
const WT_FILL_MAX_LINE_STRETCH = 1.8;   // cell HEIGHT (lineHeight) may grow up to 1.8x
function themeTerminal(term) {           // idempotent: applied once per terminal
  if (!term || term.__wtThemed) return;
  term.options.theme = CAMPBELL_THEME;
  term.options.fontFamily = TERM_FONT_STACK;
  term.__wtThemed = true;
}
const frames = document.getElementById('frames');
const made = {};
let current = 0;                          // the active tab index (tab click / Ctrl+Alt+1..9)
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
                                            // manual + default-size 176x50) and every webterm attach
                                            // is -f ignore-size (#613 REOPEN-3: a direct attach now,
                                            // no clone), so NO tab — hidden or active — can ever
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
    // (e.g. a Ctrl+Alt+9 jump to a tab scrolled off-screen). #674 removed the
    // ◀ ▶ cycle buttons, so tab clicks + Ctrl+Alt+1..9 are the switch paths now.
    // Optional call: a browser without scrollIntoView must never break switching.
    if (on) t.scrollIntoView?.({ inline: 'nearest', block: 'nearest' });
  });
  current = idx;
  applyFixedGrid(made[idx]);                 // #613 REOPEN-2: fit the now-VISIBLE tab
  focusTerminal(made[idx], idx);             // #661: type immediately after a switch
  reviveTerminal(made[idx], idx);            // #673: auto-reconnect a slept tab, no manual Enter
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
// sees it — advertising it would over-promise; a tab click covers arbitrary jumps.)
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
// #661: move keyboard focus INTO the shown terminal after a tab switch (a tab
// click AND Ctrl+Alt+N both route through activate()), so the owner can type
// immediately with no extra click into the prompt. Same-origin under
// the gateway, so we reach the iframe's own xterm: `window.term.focus()` (which
// focuses xterm's helper textarea). ttyd connects async, so the term/textarea
// may not exist for the first activate(0) at load — retry briefly, best-effort.
// A cross-origin frame would throw (impossible under the gateway) — caught so a
// focus attempt can never break switching.
// GENERATION GUARD (#661 review): `idx` is the tab this chain focuses. ttyd may
// still be connecting, so a chain started for tab A can outlive a fast switch to
// tab B; without the guard, A's late-connecting term would steal focus back to
// the now-HIDDEN A. Each retry bails the moment `current` has moved on, so
// last-activate always wins and a superseded chain becomes a no-op.
function focusTerminal(f, idx) {
  if (!f) return;
  let tries = 0;
  const tryFocus = () => {
    if (idx !== current) return;            // superseded by a newer switch -> stop
    try {
      const w = f.contentWindow;
      if (w && w.term && typeof w.term.focus === 'function') { w.term.focus(); return; }
      const ta = w && w.document &&
        w.document.querySelector('.xterm-helper-textarea, textarea');
      if (ta) { ta.focus(); return; }
    } catch (err) { return; }               // never same-origin-throws under gateway
    if (++tries < 30) setTimeout(tryFocus, 100);   // ttyd/xterm still connecting
  };
  tryFocus();
}
// #673: detect ttyd 1.7.4's PERSISTENT reconnect-wait overlay. Empirically (real
// ttyd + Playwright): a child that merely EXITS closes with WS code 1006 and ttyd
// AUTO-reconnects; but a failed reconnect ATTEMPT fires ttyd's WS `error` handler
// (doReconnect=false) and the next close parks on "Press ⏎ to Reconnect" -- an
// OverlayAddon div (position:absolute, fontSize xx-large, appended to term.element,
// NO auto-hide) that then waits for a manual Enter. Return that overlay node iff a
// reconnect-wait prompt is CURRENTLY showing, else null. The discriminator is the
// TEXT: "Reconnecting..." is ttyd self-recovering (leave it); ttyd's resize overlay
// is xx-large too but its text is grid dimensions, so we key on the reconnect text.
function ttydReconnectOverlay(win) {
  try {
    const t = win && win.term;
    if (!t || !t.element) return null;
    const nodes = t.element.querySelectorAll('div');
    for (const n of nodes) {
      if (n.style && n.style.fontSize === 'xx-large' && n.parentNode && n.style.opacity !== '0') {
        const txt = n.textContent || '';
        if (/Reconnect/.test(txt) && !/Reconnecting/.test(txt)) return n;   // "Press ⏎ to Reconnect"
        if (/Connection Closed/.test(txt)) return n;                        // transient stuck close
      }
    }
  } catch (e) { /* cross-origin (impossible under the gateway) -> treat as connected */ }
  return null;
}
// #673: if the activated tab is stuck on ttyd's reconnect prompt, press Enter FOR
// the owner -- a synthetic keydown on xterm's helper textarea is EXACTLY what
// ttyd's own onKey reconnect trigger listens for, so ttyd reconnects IN PLACE
// (tmux restores the full scrollback server-side) with no click, no Enter, and no
// iframe reload (so the whole-script single-src-assignment invariant + the
// #661/#671 beforeunload behaviour are untouched). A cooldown stops a tight loop
// if a backend is genuinely offline (a re-switch just tries again). Verified live
// against real ttyd 1.7.4: the synthetic Enter cleared the overlay and the fresh
// backend banner appeared in the buffer, reconnected with ZERO user input.
function reviveTerminal(f, idx) {
  try {
    const win = f && f.contentWindow;
    if (!win) return;
    if (!ttydReconnectOverlay(win)) return;         // healthy/self-recovering -> instant switch
    const now = Date.now();
    if (f.__wtReviveAt && now - f.__wtReviveAt < 3000) return;   // cooldown vs a dead-backend loop
    f.__wtReviveAt = now;
    const ta = win.document.querySelector('.xterm-helper-textarea, textarea');
    if (ta) {
      try { ta.focus(); } catch (e) {}
      const ev = new win.KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true});
      ta.dispatchEvent(ev);                          // ttyd's onKey Enter trigger -> reconnect
    }
  } catch (e) { /* iframe realm gone / cross-origin -> never break switching */ }
}
// #613 REOPEN-2: force each ttyd xterm to the owner's FIXED client grid
// (CFG.term_cols x CFG.term_rows = the fixed 176x50 tmux window + 1 status row)
// and scale the font so that grid FILLS the iframe viewport, centred. The tmux
// window is a FIXED size (window-size manual) and every webterm attach is
// -f ignore-size (#613 REOPEN-3: a direct attach now, no clone), so the
// browser NEVER resizes tmux; this only makes the browser SHOW the fixed
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
    // #672 REWORK: one canonical grid for every tab, so the clamp re-pins to the
    // fixed (cols, rows) captured at fitFixedGrid entry (the single owner grid) --
    // no per-current-tab getter, no per-tab race to close.
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
  // #678: clear any prior FILL so the natural-cell measurement below (and the
  // font min-fit) is honest -- a re-fit must recompute from the natural grid,
  // not last run's stretched one. The fill is now NATIVE (lineHeight/letterSpacing
  // grow the REAL cell, keeping xterm's mouse hit-test correct); the CSS transform
  // clear stays only defensively (a pre-#678 deploy may have left one on .xterm).
  const fillTarget = () => doc.querySelector('.xterm') || el;
  fillTarget().style.transform = 'none';
  term.options.lineHeight = 1;
  term.options.letterSpacing = 0;
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
  // #655/#678: the FILL (stretch the fixed grid to the viewport, killing the "okno
  // v strede" letterbox) is a SEPARATE deferred pass -- fillFixedGrid(win) below.
  // It must run AFTER this font change has settled. fitFixedGrid does the CRISP
  // bulk scaling (fontSize min-fit) and RESETS the native fill (lineHeight 1 /
  // letterSpacing 0, above) so its measurement is honest; fillFixedGrid measures
  // the settled natural grid and applies the residual fill via lineHeight/
  // letterSpacing (native cell growth -> correct mouse hit-test, #678), never a
  // CSS transform.
  return true;
}
// #678 FILL (deferred pass): the crisp fontSize min-fit in fitFixedGrid fills the
// TIGHT viewport dimension and letterboxes the LOOSE one (the owner's "okno v
// strede"). This pass fills that residual via NATIVE xterm cell geometry --
// lineHeight (taller cells, vertical) + letterSpacing (wider cells, horizontal) --
// NEVER a CSS transform. WHY (the #678 regression): a CSS `transform: scale()`
// scales getBoundingClientRect but NOT xterm's cssCellHeight, so xterm's mouse
// hit-test (row = ceil((clientY - rect.top) / cssCellHeight)) reports a cell BELOW
// the one the user points at, worse with depth (owner: "selectujem kde je kurzor
// ale vybera sa mi ovela nizsie"). Growing the REAL cell keeps render and hit-test
// consistent -- verified live (native fill: a click at every row's visual centre
// hit-tests to that row). TRADE-OFF: xterm quantizes BOTH the cell WIDTH
// (letterSpacing) and the cell HEIGHT (lineHeight) to INTEGER px/cell, so BOTH axes
// fill COARSELY -- each floors to the largest integer cell that fits, leaving a
// small residual letterbox (up to ~one cell per axis, <~5-9%) rather than
// overflowing/clipping; per #678 a WORKING MOUSE outranks a pixel-exact fill (owner:
// "funkčný select má prednosť"). #655 chose a CSS transform for exact fill precisely
// because letterSpacing/lineHeight quantize -- #678 reverses that trade for mouse
// correctness. Bounded (WT_FILL_MAX_*) so an extreme viewport letterboxes the
// remainder rather than distorting text. fitFixedGrid resets lineHeight/
// letterSpacing before its own measurement, so this pass measures the natural grid;
// xterm reflects an option change synchronously (the same path the fontSize min-fit
// relies on) and the scheduleFill re-runs re-converge after any late layout settle.
function fillFixedGrid(win) {
  const cols = CFG.term_cols, rows = CFG.term_rows;
  if (!win || !win.term || !cols || !rows) return false;
  const term = win.term;
  const el = win.document.querySelector('.xterm-screen') || win.document.querySelector('.xterm');
  if (!el) return false;
  // reset the native fill so the measurement is the NATURAL grid (self-contained +
  // idempotent -- a re-run recomputes from natural, never last run's stretched grid)
  term.options.lineHeight = 1;
  term.options.letterSpacing = 0;
  const g = el.getBoundingClientRect();            // NATURAL grid (fill just reset)
  const availW = win.innerWidth, availH = win.innerHeight;
  if (!g.width || !g.height || !availW || !availH) return false;
  // vertical: taller cells via lineHeight. Target an INTEGER cell height that never
  // overflows -- xterm rounds cell HEIGHT to integer px (same as letterSpacing), so a
  // raw lineHeight = availH/g.height would make rows*round(cellH) EXCEED availH (up to
  // ~rows/2 px) and CLIP the bottom row / status bar under the container's
  // overflow:hidden. floor(availH/rows) is the largest integer cell height that fits,
  // so rows*cellH <= availH always (a small residual letterbox, exactly like the
  // horizontal axis). Bounded to WT_FILL_MAX_LINE_STRETCH of the natural cell; never
  // < 1 = a shrink, not a fill (fitFixedGrid already fit the grid within availH, so
  // floor(availH/rows) >= the natural integer cell height).
  const nCellH = g.height / rows;
  const tCellH = Math.min(Math.floor(availH / rows),
                          Math.floor(nCellH * WT_FILL_MAX_LINE_STRETCH));
  let lh = Math.max(1, tCellH / nCellH);
  // horizontal: wider cells via letterSpacing (INTEGER px/cell -> coarse). FLOOR,
  // never round: round can push cols*cellW PAST availW and CLIP the grid (worse
  // than a letterbox); floor is the largest integer px/cell that never overflows,
  // leaving a small residual letterbox. Bounded to WT_FILL_MAX_CELL_STRETCH of the
  // natural cell width, never below 0.
  const naturalCellW = g.width / cols;
  const ls = Math.floor(Math.max(0, Math.min(naturalCellW * (WT_FILL_MAX_CELL_STRETCH - 1),
                                             (availW - g.width) / cols)));
  term.options.lineHeight = +lh.toFixed(4);
  term.options.letterSpacing = ls;
  // CORRECTIVE (vertical): the rendered cell height is round(charHeight*lineHeight),
  // and a FRACTIONAL charHeight can round the floor target UP by 1px -> a 1-cell
  // overflow that CLIPS the bottom row. getBoundingClientRect reflects the option
  // SYNCHRONOUSLY (the same reflow the fontSize min-fit relies on), so step lineHeight
  // down one integer cell until the grid fits -- a bounded safety net mirroring
  // fitFixedGrid's font-shrink loop; letterSpacing needs none (its floor is exact).
  for (let i = 0; i < 4 && lh > 1; i++) {
    if (el.getBoundingClientRect().height <= availH + 1) break;
    lh = Math.max(1, lh - 1 / nCellH);
    term.options.lineHeight = +lh.toFixed(4);
  }
  return true;
}
// #655/#678: the FILL must re-run whenever the NATURAL grid size settles/changes.
// xterm's grid layout can settle noticeably AFTER first paint (font metrics, the
// multi-tab layout). The AUTHORITATIVE driver is a ResizeObserver on .xterm-screen,
// which fires on the REAL layout size both on observe AND on every late settle, so
// the fill always tracks the true natural grid and converges. NO ping-pong: each
// fillFixedGrid run RESETS to the natural cell, measures, and re-applies the SAME
// deterministic integer-cell target, so a settled layout produces no net size change
// and the RO stops (a mid-settle change re-fires it, and it converges to that new
// natural size). The immediate call + the timed passes are only a best-effort first
// paint and a fallback for a browser without ResizeObserver: they may run on a
// still-settling (stale-small) grid, but fillFixedGrid is self-contained + idempotent
// (resets the native fill, re-measures, re-applies), and the floor targets never
// overflow, so the RO corrects any transient the instant the layout settles -- no
// persistent clip.
function scheduleFill(win) {
  try { fillFixedGrid(win); } catch (e) {}
  try {
    const doc = win.document;
    const el = doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
    if (el && win.ResizeObserver && !win.__wtFillRO) {
      win.__wtFillRO = new win.ResizeObserver(() => { try { fillFixedGrid(win); } catch (e) {} });
      win.__wtFillRO.observe(el);
    }
  } catch (e) {}
  [200, 800, 2000].forEach((ms) => {
    setTimeout(() => { try { fillFixedGrid(win); } catch (e) {} }, ms);   // guard the CALL
  });
}
// #671: mouse select/copy -> browser clipboard. Empirically verified against a
// real ttyd 1.7.4 replica (Playwright + tmux capture-pane): ttyd's bundled xterm
// frontend registers NO OSC 52 handler, so a tmux copy-mode mouse drag (which,
// under `set-clipboard external`, emits OSC 52) never reaches the browser
// clipboard -- the owner's reported symptom. We register an OSC 52 handler on
// xterm's OWN parser API and mirror the decoded payload to navigator.clipboard,
// PLUS a copy-on-select mirror for native xterm selections (Shift+drag). Both are
// wired through the same same-origin `window.term` bridge as #613/#643/#661, use
// the IFRAME's own (focused) navigator.clipboard, and are fully guarded so a
// missing/denied clipboard never throws. Paste needs no code: Ctrl+Shift+V pastes
// natively (browser paste event -> xterm), plain Ctrl+V is ^V and is NOT rebound
// (a legit readline key). (#671 rework removed the on-screen copy/paste hint strip.)
function attachClipboard(win) {                  // idempotent: attach once per terminal
  const term = win && win.term;
  if (!term || term.__wtClip) return;
  term.__wtClip = true;
  const clip = (win.navigator && win.navigator.clipboard) || null;   // iframe realm = focused doc
  const write = (text) => {
    try { if (clip && clip.writeText && text) clip.writeText(text).catch(function () {}); }
    catch (e) { /* clipboard unavailable/denied -> silently skip */ }
  };
  try {
    if (term.parser && term.parser.registerOscHandler) {
      term.parser.registerOscHandler(52, (data) => {
        try {
          const parts = String(data).split(';');           // "<targets>;<base64>" (targets may be empty)
          const b64 = parts.length > 1 ? parts[parts.length - 1] : parts[0];
          if (b64 && b64 !== '?') write(decodeURIComponent(escape(win.atob(b64))));  // '?' = read-request
        } catch (e) { /* invalid base64 / read-request -> ignore */ }
        return true;                                        // we own OSC 52
      });
    }
  } catch (e) { /* older xterm without registerOscHandler -> no OSC 52 bridge */ }
  try {
    if (typeof term.onSelectionChange === 'function') {
      term.onSelectionChange(() => {
        try { const s = term.getSelection(); if (s) write(s); } catch (e) {}
      });
    }
  } catch (e) {}
}
function applyFixedGrid(f) {                     // poll for window.term, fit, then watch resize
  if (!f) return;
  const win = f.contentWindow;
  if (!win) return;
  let tries = 0;
  const poll = () => {
    themeTerminal(win.term);                     // #643: Campbell palette + font, once term exists
    attachClipboard(win);                        // #671: OSC 52 + copy-on-select -> browser clipboard
    if (fitFixedGrid(win)) {
      scheduleFill(win);                           // deferred FILL passes (below)
      if (!win.__wtResize) {                       // re-fit + re-fill on window resize
        win.__wtResize = true;
        try {
          win.addEventListener('resize', () => { fitFixedGrid(win); scheduleFill(win); });
        } catch (e) {}
      }
      return;
    }
    if (++tries < 100) setTimeout(poll, 100);    // ttyd connects async after iframe load
  };
  poll();
}
document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => activate(+t.dataset.idx)));
// #674: the prev/next cycle arrows and the ? help toggle are removed (owner: keep
// only fullscreen); tab switching stays via tab clicks + Ctrl+Alt+1..9 (onHotkey).
// #671 REWORK (owner ruling 2026-08-25): the copy/paste footer hint strip + its
// isSecureContext honesty-rewrite are removed entirely (owner: "potrebujem hlavne
// pracovnu plochu nie tvoje blbe vysvetlivky"). The copy bridge (attachClipboard)
// stays; only the visible hint is gone.
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
// #677: the per-tab U dot. Poll the gateway's /u-status (the aggregated per-box U
// map) and toggle a tab's .has-u iff its target box currently has U > 0 -- a
// navigation hint that a question/approval is waiting on the owner there. Read
// LIVE (never a build-time value); a fetch failure leaves the current dots
// untouched (graceful). The gateway fire-and-forgets a fresh collect on a stale
// read, so a short burst after load catches it, then a steady minutes cadence.
function applyUStatus(map) {
  document.querySelectorAll('.tab').forEach((t) => {
    const s = CFG.sessions[+t.dataset.idx];
    const u = s && map ? map[s.id] : undefined;
    t.classList.toggle('has-u', typeof u === 'number' && u > 0);
  });
}
function pollUStatus() {
  fetch('/u-status', { credentials: 'same-origin', cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && d.u) applyUStatus(d.u); })
    .catch(() => {});                     // absent/failed -> leave the dots as-is
}
if (CFG.u_status) {                       // #677 review: OWNER dashboard only
  pollUStatus();
  [4000, 12000, 30000].forEach((ms) => setTimeout(pollUStatus, ms));   // burst after a fresh collect
  setInterval(pollUStatus, 120000);                                     // then minutes-fresh
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


# #584: in PASSWORD mode (this `_LAUNCH_TEMPLATE`; the #663 Access variant is
# `_LAUNCH_TEMPLATE_SOCKET` below, which binds a UNIX socket) ttyd binds LOOPBACK
# only (127.0.0.1) behind a `-b /t` base path. The
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


# #663: Access-mode ttyd binds a mode-0700 UNIX-domain socket in the account's XDG
# runtime dir instead of a TCP loopback port. On a shared box only this account can
# traverse /run/user/<uid> (0700), so a peer account cannot reach the auth-less ttyd
# directly. `rm -f` clears a stale socket left by a restart (tmpfs clears on reboot);
# `umask 077` keeps ttyd's socket owner-only. The gateway reaches it via
# --ttyd-socket over the SAME path.
_LAUNCH_TEMPLATE_SOCKET = """#!/usr/bin/env bash
# airuleset-managed (#555/#579/#584/#663) — do NOT edit; regenerate via `python3 airuleset.py install`.
# Execs ttyd bound on a mode-0700 UNIX-domain socket in the account's XDG runtime
# dir behind a `-b /t` base path; the same-origin gateway reaches it over that socket.
set -euo pipefail
%(inventory_export)sSOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/%(ttyd_sock_basename)s"
rm -f "$SOCK"
umask 077
exec ttyd -i "$SOCK" -b %(base_path)s -a -W \\
  python3 %(repo_dir)s/cli_webterm.py webterm-connect
"""


# #681: a webterm ttyd/gateway TCP bind is loopback (127.0.0.1) or a validated
# tailscale IP — NEVER a wildcard / interface-any bind, which would expose an
# unauthenticated writable terminal on EVERY interface incl. the tailnet. The #661
# harness doc wrongly told a review agent to `ttyd -i 0.0.0.0` and it was executed
# (#671); the production render path has always bound loopback / a #663 UNIX socket,
# so this is a FAIL-CLOSED regression guard on the single TCP-bind chokepoint (the
# UNIX-socket path is not a TCP interface and needs no guard). `_bind_is_wildcard`
# resolves EVERY interface-any spelling (0.0.0.0, ::, ::0, 0:0:0:0:0:0:0:0, and the
# legacy IPv4 shorthands 0 / 0.0 / 0.0.0 / leading-zeros that inet_aton maps to
# 0.0.0.0) via the stdlib, so a future edit cannot slip a wildcard past it (#681
# review). The non-parseable sentinels "" / "*" are covered explicitly.
_WILDCARD_SENTINELS = frozenset({"", "*"})


def _inet_all_zero(b):
    """True iff `b` parses as an IPv4 literal whose 32-bit value is 0 — catches the
    legacy shorthands 0 / 0.0 / 0.0.0 / 0.0.0.0 (and leading-zero forms) that
    inet_aton maps to 0.0.0.0. A non-IPv4 string (an IPv6 literal or a hostname) is a
    handled, expected non-match, not an error to log."""
    try:
        return socket.inet_aton(b) == b"\x00\x00\x00\x00"
    except OSError:
        return False


def _bind_is_wildcard(bind):
    """True iff `bind` is an interface-any / unspecified / empty bind. Parses via
    ipaddress (catches 0.0.0.0, ::, ::0, 0:0:0:0:0:0:0:0) and inet_aton (the legacy
    IPv4 shorthands). A real interface (127.0.0.1, a 100.64/10 tailscale IP, ::1) is
    False."""
    if bind is None:
        return True
    b = str(bind).strip()
    if b in _WILDCARD_SENTINELS:
        return True
    try:
        return bool(ipaddress.ip_address(b).is_unspecified) or _inet_all_zero(b)
    except ValueError:
        # Not an ipaddress literal — an expected, handled case (a shorthand like
        # "0", or a hostname); defer to the inet_aton shorthand check.
        return _inet_all_zero(b)


def _reject_wildcard_bind(bind, where):
    """Return `bind` unchanged if it is a real loopback/tailscale interface; raise
    ValueError (fail closed) on any wildcard / interface-any / empty bind. `where`
    names the call site for the error message. #681 security invariant."""
    if _bind_is_wildcard(bind):
        raise ValueError(
            "webterm %s: refusing interface-any bind %r — ttyd/gateway must bind "
            "loopback (127.0.0.1) or a validated tailscale IP, never 0.0.0.0 "
            "(#681 security invariant)" % (where, bind))
    return bind


def render_webterm_launch_script(inventory_path=None, ttyd_port=None,
                                 ttyd_socket_basename=None):
    """The ttyd launcher: `-b /t` base path, no basic-auth, no `-O` (#584 — the
    gateway is the sole entry + authenticator). #612: when `inventory_path` is given
    (the david/marek profiles), it is EXPORTED as the `WEBTERM_INVENTORY` env var the
    ttyd child (`webterm-connect`) reads — NOT a client-injectable argv flag (ttyd's
    `-a` appends client `?arg=` values as argv, so an argv `--inventory` would be
    injectable — #612 review).

    #663: when `ttyd_socket_basename` is given, ttyd binds a UNIX-domain socket in
    the account's XDG runtime dir (mutually exclusive with `ttyd_port` — the account
    boundary is filesystem permissions, closing the direct-ttyd vector on a shared
    box). Otherwise it binds the TCP loopback port (`ttyd_port` overrides the owner
    default); with no `inventory_path`/`ttyd_port`/`ttyd_socket_basename` (owner
    password mode), the emitted script is BYTE-IDENTICAL to pre-#663."""
    inventory_export = ""
    if inventory_path:
        inventory_export = ("export WEBTERM_INVENTORY=%s\n"
                            % shlex.quote(str(inventory_path)))
    if ttyd_socket_basename:
        return _LAUNCH_TEMPLATE_SOCKET % {
            "ttyd_sock_basename": ttyd_socket_basename,
            "base_path": shlex.quote(WEBTERM_TTYD_BASE),
            "repo_dir": shlex.quote(str(REPO_DIR)),
            "inventory_export": inventory_export,
        }
    return _LAUNCH_TEMPLATE % {
        "ttyd_port": ttyd_port if ttyd_port is not None else WEBTERM_TTYD_PORT,
        "ttyd_bind": shlex.quote(_reject_wildcard_bind(WEBTERM_TTYD_BIND,
                                                       "ttyd launch")),
        "base_path": shlex.quote(WEBTERM_TTYD_BASE),
        "repo_dir": shlex.quote(str(REPO_DIR)),
        "inventory_export": inventory_export,
    }


def _render_webterm_unit():
    tmpl = WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_LAUNCH_PATH))


# #635/#663: prepended to the OWNER gateway unit ONLY in Cloudflare-Access mode, so
# a human reading the installed file is not misled by the shared template's
# tailnet/password wording — the SAME honesty-bar correction the subdev lanes'
# note makes (cli_webterm_lane.render_lane_unit_note, #665). Every claim it
# corrects is FALSE for the #663 UNIX-socket + cloudflared + Access owner gateway.
_OWNER_ACCESS_UNIT_NOTE = (
    "# NOTE (#635/#663, owner ROZHODNUTÉ 2026-08-22): this is the OWNER gateway in\n"
    "# CLOUDFLARE-ACCESS mode — #663 it binds a mode-0700 UNIX-domain socket in the\n"
    "# account's /run/user/<uid> runtime dir (%t/webterm-gateway.sock, NOT TCP\n"
    "# 127.0.0.1:<port>) and is fronted by a cloudflared tunnel (service: unix:<sock>)\n"
    "# for https://zbynek.newlevel.media/. The shared template's 'bound to dev1's\n"
    "# tailscale IP', 'the ONE tailnet-only entry point' and 'security boundary is\n"
    "# tailnet-only exposure' wording below is FALSE here: this gateway binds a UNIX\n"
    "# socket (not a tailscale IP) and is PUBLIC behind Cloudflare Access (the edge\n"
    "# email-OTP check is the boundary), with filesystem permissions on the 0700\n"
    "# runtime dir as the LOCAL account boundary.\n"
    "#\n"
    "# AUTH: NO password / credential / login form / constant-time compare.\n"
    "# Cloudflare Access does email one-time-PIN verification at the EDGE before any\n"
    "# request reaches the tunnel; the gateway runs in --trust-access-header mode and\n"
    "# just trusts the Cf-Access-Authenticated-User-Email header. The template's\n"
    "# 'credential (…) validated constant-time' + 'Bitwarden login form' wording is\n"
    "# the OWNER (password) deployment being RETIRED here — it does NOT apply.\n"
    "#\n"
    "# 'failed logins rate-limited per source IP' does NOT hold at the origin: behind\n"
    "# cloudflared the gateway sees only one peer, so per-real-IP brute-force\n"
    "# protection lives on the Cloudflare EDGE. And 'install REFUSES to provision\n"
    "# rather than bind a public interface' does not apply — Access mode binds a UNIX\n"
    "# socket and needs no tailscale IP at all.\n#\n")


def access_execstart_transform(tmpl, gateway_sock_basename, ttyd_sock_basename):
    """#663 (review): the SINGLE Access-mode ExecStart transform shared by ALL three
    lanes (owner / david / marek) so they cannot drift — swap the shared gateway-unit
    template's password/TCP flags for Cloudflare-Access + UNIX-socket flags:
      --cred {{CRED_PATH}}                            -> --trust-access-header <hdr>
      --bind {{BIND_IP}} --port {{GATEWAY_PORT}}      -> --socket %t/<gw basename>
      --ttyd-host 127.0.0.1 --ttyd-port {{TTYD_PORT}} -> --ttyd-socket %t/<ttyd basename>
    Run this BEFORE the per-lane `{{TOKEN}}` substitutions (the flag substrings still
    carry their literal tokens at this point; `%t` == the account's $XDG_RUNTIME_DIR).
    The three per-lane no-TCP-surface locks CATCH drift; this helper PREVENTS it."""
    import cli_webterm_access as access
    return (tmpl
            .replace("--cred {{CRED_PATH}}",
                     "--trust-access-header " + access.WEBTERM_ACCESS_TRUST_HEADER)
            .replace("--bind {{BIND_IP}} --port {{GATEWAY_PORT}}",
                     "--socket %t/" + gateway_sock_basename)
            .replace("--ttyd-host 127.0.0.1 --ttyd-port {{TTYD_PORT}}",
                     "--ttyd-socket %t/" + ttyd_sock_basename))


def _render_webterm_gateway_unit(bind_ip, access_mode=False):
    """The same-origin gateway systemd --user unit: runs
    `cli_webterm_gateway.py` bound to `bind_ip` on the single gateway port,
    proxying /t/* to the loopback ttyd.

    Default (password mode): `bind_ip` MUST be a validated tailscale IP (see
    `_tailscale_ip`) — never 0.0.0.0/public — and the login form validates the
    credential (`--cred`).

    #635 `access_mode=True` (Cloudflare Access): the `--cred {{CRED_PATH}}` in the
    shared template's ExecStart is swapped for `--trust-access-header <header>`
    (the SAME transform the subdev lanes use via cli_webterm_lane.render_gateway_
    unit, #665) — NO password/credential is validated; Cloudflare email-OTP at the
    edge is the whole gate, and `bind_ip` is loopback (a cloudflared tunnel fronts
    it). The password-model `{{CRED_PATH}}` in the template's COMMENT is neutralised
    to n/a, AND `_OWNER_ACCESS_UNIT_NOTE` is prepended (mirroring the subdev lanes'
    shared note, cli_webterm_lane.render_lane_unit_note) to correct every OTHER now-false
    tailnet/password claim in the shared template header, so a human reading the
    installed Access-mode unit is never misled. #677: the owner unit ALSO always
    carries `--u-collect` (the U-dot data channel is owner-only; david/marek units
    omit it), so `access_mode=False` is BYTE-IDENTICAL to the pre-#635 render EXCEPT
    for that single injected flag."""
    tmpl = WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    # #677: the OWNER gateway (this renderer only) enables the U-dot data channel.
    # david/marek render the SAME shared template in their OWN functions and do NOT
    # inject this, so their gateway runs WITHOUT --u-collect -> /u-status serves an
    # empty map and never spawns an owner-fleet collector as the sub-dev account.
    tmpl = tmpl.replace("--base-path {{TTYD_BASE}}",
                        "--base-path {{TTYD_BASE}} --u-collect")
    if access_mode:
        # #663: retire the TCP loopback origin entirely — bind + reach ttyd over
        # mode-0700 UNIX sockets in the account runtime dir, so no peer unix account
        # on a shared box can forge the trust header at the gateway port OR reach
        # ttyd directly. The shared transform runs BEFORE the {{TOKEN}} substitutions
        # so the ExecStart carries the socket flags while the header COMMENT's tokens
        # (now dead) are still corrected by the note.
        tmpl = access_execstart_transform(
            tmpl, WEBTERM_GATEWAY_SOCK_BASENAME, WEBTERM_TTYD_SOCK_BASENAME)
        cred_sub = "n/a (Cloudflare Access — no credential)"
        # In Access mode {{BIND_IP}} survives only in the header COMMENT now; keep
        # it loopback there so no human reads a tailnet bind into a passwordless unit.
        bind_ip = WEBTERM_TTYD_BIND
        note = _OWNER_ACCESS_UNIT_NOTE
    else:
        cred_sub = str(WEBTERM_CRED_PATH)
        note = ""
    # #681: fail closed — the gateway TCP bind is loopback or a validated tailscale
    # IP, never a wildcard (in Access mode bind_ip is already reset to loopback and
    # survives only in the header comment, so this is a no-op there).
    bind_ip = _reject_wildcard_bind(bind_ip, "gateway unit")
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

    # #635/#663: Cloudflare-Access mode (owner go-live) binds a UNIX socket in the
    # account runtime dir — a cloudflared tunnel is the public front, so NO tailscale
    # IP is needed and there is no direct tailnet exposure (and no peer unix account
    # can reach the socket). Default (password mode): resolve dev1's tailscale IP
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
    # #661: the connect allowlist stays the FULL fleet inventory (tab VISIBILITY
    # is filtered, not reachability — the owner keeps his existing access).
    WEBTERM_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # #584: the ttyd base is now RELATIVE (`/t`) — same-origin under the gateway,
    # so the iframes are same-origin (Ctrl+Alt+N works while typing).
    # #661: the owner DASHBOARD renders only WEBTERM_LOGIN_USER's owner-defined
    # tab list (zbynek.newlevel.media), in his order — NOT a tab per fleet target.
    WEBTERM_DASH_INDEX.write_text(
        render_dashboard_html(inv, ttyd_base=WEBTERM_TTYD_BASE,
                              human=WEBTERM_LOGIN_USER), encoding="utf-8")
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
    # #663: Access mode binds ttyd on a UNIX socket in the account runtime dir (no
    # TCP loopback origin); password mode keeps the TCP loopback port (byte-identical).
    if access_mode:
        launch = render_webterm_launch_script(
            ttyd_socket_basename=WEBTERM_TTYD_SOCK_BASENAME)
    else:
        launch = render_webterm_launch_script()
    WEBTERM_LAUNCH_PATH.write_text(launch, encoding="utf-8")
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
            print("  webterm: gateway live (Cloudflare Access mode, #635/#663) — "
                  "bound on UNIX socket %s (account-scoped 0700 runtime dir), fronted "
                  "by cloudflared (service: unix:) for https://zbynek.newlevel.media/ "
                  "(email-OTP gate; password retired). ttyd UNIX socket %s behind /t."
                  % (webterm_runtime_socket_abs(WEBTERM_GATEWAY_SOCK_BASENAME),
                     webterm_runtime_socket_abs(WEBTERM_TTYD_SOCK_BASENAME)))
        else:
            print("  webterm: gateway live — http://%s:%d/ (tailnet-only, form "
                  "login user %r; credential in %s — read it once to save in "
                  "Bitwarden). ttyd loopback 127.0.0.1:%d behind /t."
                  % (bind_ip, WEBTERM_GATEWAY_PORT, WEBTERM_LOGIN_USER,
                     WEBTERM_CRED_PATH, WEBTERM_TTYD_PORT))
    return ok_all


def maybe_setup_webterm():
    """Install-time entry point (cmd_install). Dispatches by box profile AND the
    install account: dev1 -> owner gateway (unchanged); subdev + the `marek`
    account -> the marek developer gateway; subdev (its own account david1, or the
    default) -> the david developer gateway. subdev is a MULTI-developer box (#612
    marek scope-add) — david and marek each run their OWN gateway as their OWN
    account, so the install-as-account selects which one this run provisions. Each
    provisioner is ALSO prerequisite-gated on its own account (a safe no-op
    otherwise), so a non-matching account never touches systemd. Any other box ->
    no-op. The developer modules are lazily imported to avoid a module-level
    cycle."""
    from cli_filedrop_watchdog import _whoami
    prof = profiles.profile_for_host(os.uname().nodename, account=_whoami())
    if prof == profiles.OWNER:
        return setup_webterm_service()
    if prof == profiles.DAVID:
        import cli_webterm_david
        return cli_webterm_david.setup_webterm_david_service()
    if prof == profiles.MAREK:
        import cli_webterm_marek
        return cli_webterm_marek.setup_webterm_marek_service()
    return False


def main(argv):
    if argv and argv[0] == "webterm-connect":
        return connect_main(argv[1:])
    if argv and argv[0] == "webterm-u-collect":     # #677: spawned by the gateway
        return cmd_webterm_u_collect(argv[1:])
    sys.stderr.write("usage: cli_webterm.py webterm-connect <session-id>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
