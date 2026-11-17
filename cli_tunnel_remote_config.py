"""#1347 — the controller tunnel's ingress as REMOTELY managed config, applied live.

Incident (owner 17.11.2026): every webterm tab dropped five times on 16.11.
Each route change (a drop lane, a service route, an account) re-rendered the
locally managed `controller-webterm.yml`. cloudflared reads a local config only
at start (`cmd/cloudflared/tunnel/signal.go` handles only SIGTERM/SIGINT, so
there is no reload). The #1189 apply therefore restarted the connector through
the overlap. The overlap keeps the HOSTNAME registered, but a websocket is bound
to the process that accepted it. The old process drains its 30 s grace and
exits, and every open tab dies (scratch measurement: SIGTERM 09:19:39Z, socket
dead 09:20:10Z).

A REMOTELY managed tunnel takes its ingress from
`PUT /accounts/<acc>/cfd_tunnel/<id>/configurations`. cloudflared's orchestrator
swaps the proxy live (`orchestration/orchestrator.go` `UpdateConfig`: start the
new origins, store the new proxy, close the old one's shutdown channel, which no
http/unix origin uses), so a request already in flight keeps its connection.
Measured on a scratch tunnel with cloudflared 2026.8.3: each change was applied
in about 2 s, and one websocket survived two changes (72/72 echoes).

Two facts shape the flow:

- A connector learns at REGISTRATION whether its tunnel is remotely managed
  (`connection/control.go`). A process that registered while the tunnel was
  local never receives the pushes, so the FIRST conversion needs ONE restart.
  `apply_remote_tunnel` detects this and does that restart itself: the running
  main PID has not logged the version within `APPLY_WAIT_S`. It goes through the
  #1189 overlap, so there is no 1033 window; open tabs drop once.
- Remote config wins over a local YAML's ingress (local is version -1, remote
  >= 0), so the controller runs a static bootstrap YAML (tunnel +
  credentials-file only). A route change then touches no file the connector
  runs, and the #1189 change gate sees nothing to restart.

The RUNNING process's applied version is read from its own journal lines
(`journalctl --user _PID=<MainPID>`: `INF Updated to new configuration …
version=N`). This is proof that THIS process took the config, which the API
cannot give. A confirmed apply is stamped `<pid> <version>` in
`<config>.remote-applied`. When a connector has run for weeks and its old
journal lines are rotated away, an unchanged install trusts that stamp for the
same pid and does not restart it.

Prerequisite-gated on `TOKEN_FILE` (an account token with Cloudflare Tunnel:
Edit): without it the controller keeps the local YAML path byte-for-byte. After
the cutover, removing the token is NOT a rollback. The tunnel stays remotely
managed in Cloudflare, and a local YAML ingress would be ignored. Flip it back in
the dashboard first.

Stdlib leaf. The HTTP layer is `cli_webterm_access.AccessClient` (same `_http`
and injectable `transport` seam). `read_journal` and the overlap restart are
resolved at call time, so tests patch them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import cli_tunnel_apply
import cli_webterm_access as wa

TOKEN_FILE = "~/.secrets/cloudflare-tunnel-config"
ACCOUNT_ID = wa.WEBTERM_ACCESS_ACCOUNT_ID
# A pushed config reaches a remote-registered connector in ~2 s (measured), and a
# restarted one applies it right after its first registration (~1-3 s). Thirty
# seconds means "this process will not": it registered as locally managed.
APPLY_WAIT_S = 30
POLL_S = 1.0
_UPDATED = "Updated to new configuration"
# The LAST `version=` on the line: zerolog sorts fields, so it follows the
# config JSON. journald splits a line past LineMax (48K; ~60 rules today are
# ~7K), which would hide it; that reads as "not applied" and never restarts a
# connector that already ran remote config (`_ran_remote`).
_VERSION_RE = re.compile(r"\bversion=(\d+)")


class TunnelConfigClient(wa.AccessClient):
    """The Cloudflare Tunnel API calls this leaf needs, on AccessClient's HTTP
    layer and test seam."""

    def _tunnel(self, tunnel_id, suffix=""):
        return "/accounts/%s/cfd_tunnel/%s%s" % (self.account_id, tunnel_id, suffix)

    def get_tunnel(self, tunnel_id):
        return self._call("GET", self._tunnel(tunnel_id))

    def get_config(self, tunnel_id):
        return self._call("GET", self._tunnel(tunnel_id, "/configurations"))

    def put_config(self, tunnel_id, ingress):
        return self._call("PUT", self._tunnel(tunnel_id, "/configurations"),
                          {"config": {"ingress": ingress}})


def load_client(token_file=TOKEN_FILE):
    """A client for the controller's account, or None when the token file is
    absent (the local-YAML path stays in force). The value is never printed."""
    path = Path(os.path.expanduser(token_file))
    if not path.exists():
        return None
    token = wa._load_token(str(path))
    return TunnelConfigClient(ACCOUNT_ID, token=token) if token else None


def remote_ingress(rules):
    """The API ingress list for the SAME `(hostname, service)` /
    `(hostname, path, service)` tuples the local render takes, same order,
    plus the catch-all 404."""
    out = []
    for rule in rules:
        if len(rule) == 3:
            hostname, path, service = rule
            out.append({"hostname": hostname, "path": path, "service": service})
        elif len(rule) == 2:
            hostname, service = rule
            out.append({"hostname": hostname, "service": service})
        else:
            raise ValueError("ingress rule must be (hostname, service) or "
                             "(hostname, path, service): %r" % (rule,))
    out.append({"service": "http_status:404"})
    return out


def _ingress_key(ingress):
    """Order-sensitive comparison key. Cloudflare echoes `originRequest: {}`
    (and may echo an empty `path`) on a rule; an empty value is the same as an
    absent one, else every install would PUT a new version."""
    empty = ({}, "", None, [])
    key = []
    for r in ingress or []:
        extra = {k: v for k, v in r.items()
                 if k not in ("hostname", "path", "service") and v not in empty}
        key.append((r.get("hostname") or None, r.get("path") or None,
                    r.get("service"), json.dumps(extra, sort_keys=True)))
    return key


def sync_ingress(client, tunnel_id, ingress):
    """PUT `ingress` unless the tunnel is ALREADY remotely managed with exactly
    it. Returns `(ok, version, action, err)`: action is `unchanged`, `updated` or
    `converted` (the first PUT on a locally managed tunnel). Never raises."""
    try:
        st, t = client.get_tunnel(tunnel_id)
        if not wa._ok(st, t):
            return False, None, "", "read tunnel (HTTP %s): %s" % (st, wa._first_err(t))
        remote = bool((t.get("result") or {}).get("remote_config"))
        st, c = client.get_config(tunnel_id)
        if not wa._ok(st, c):
            return False, None, "", "read config (HTTP %s): %s" % (st, wa._first_err(c))
        cur = c.get("result") or {}
        if remote and _ingress_key((cur.get("config") or {}).get("ingress")) \
                == _ingress_key(ingress):
            version, action = cur.get("version"), "unchanged"
        else:
            st, p = client.put_config(tunnel_id, ingress)
            if not wa._ok(st, p):
                return False, None, "", "PUT config (HTTP %s): %s" % (st, wa._first_err(p))
            version = (p.get("result") or {}).get("version")
            action = "updated" if remote else "converted"
        if not isinstance(version, int) or isinstance(version, bool):
            # Unconfirmable: it must never reach the restart branch (#1347 review).
            return False, None, "", "%s reply carries no version: %r" % (action, version)
        return True, version, action, ""
    except Exception as e:  # noqa: BLE001 — returned LOUD by the caller
        return False, None, "", repr(e)


def read_journal(pid):
    """The running connector's own journal lines (empty on any error)."""
    try:
        r = subprocess.run(["journalctl", "--user", "_PID=%d" % pid, "-o", "cat",
                            "--no-pager"], capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def applied_version(journal_text):
    """The newest `Updated to new configuration … version=N`, or None."""
    found = None
    for line in (journal_text or "").splitlines():
        versions = _VERSION_RE.findall(line) if _UPDATED in line else []
        if versions:
            found = int(versions[-1])
    return found


def applied_stamp_path(config_path):
    """`<config>.remote-applied`: "<pid> <version>" of the last CONFIRMED apply."""
    return Path(config_path).with_suffix(".remote-applied")


def applied_stamp(config_path):
    """(pid, version) from the stamp, or None (absent / unreadable / garbage)."""
    try:
        pid, version = Path(applied_stamp_path(config_path)).read_text().split()
        return int(pid), int(version)
    except (OSError, ValueError):
        return None


def _write_stamp(config_path, pid, version, lane):
    try:
        applied_stamp_path(config_path).write_text("%d %d" % (pid, version))
    except OSError as e:
        print("  webterm%s: LOUD — could not write the remote-applied stamp (%r); "
              "the next install re-reads the journal (#1347)." % (lane, e),
              file=sys.stderr)


def _wait_applied(systemctl, service_name, version, config_path, sleep, clock):
    """The main PID once it has applied `version`, else 0 after APPLY_WAIT_S.
    Proof is its own journal line, or (a long-running process whose old lines
    were rotated away) the stamp of THIS pid at >= that version."""
    deadline = clock() + APPLY_WAIT_S
    stamp = applied_stamp(config_path)
    while True:
        pid = cli_tunnel_apply._main_pid(systemctl, service_name)
        if pid > 0 and version is not None:
            if stamp is not None and stamp[0] == pid and stamp[1] >= version:
                return pid
            got = applied_version(read_journal(pid))
            if got is not None and got >= version:
                return pid
        if clock() >= deadline:
            return 0
        sleep(POLL_S)


def _ran_remote(systemctl, service_name, config_path):
    """True when the running main process has EVER applied remote config: a
    stamp for its pid, or any `Updated to new configuration` line of its own.
    Only a process with neither registered as locally managed (the cutover)."""
    pid = cli_tunnel_apply._main_pid(systemctl, service_name)
    if pid <= 0:
        return False
    stamp = applied_stamp(config_path)
    return (stamp is not None and stamp[0] == pid) \
        or applied_version(read_journal(pid)) is not None


def apply_remote_tunnel(client, tunnel_id, ingress, *, provision, service_name,
                        config_path, systemctl, sleep=time.sleep,
                        clock=time.monotonic, lane="(controller)"):
    """PUT the ingress, write the static bootstrap YAML + unit (`provision`, the
    #1189 change-gated apply), then prove the RUNNING connector applied that
    version. A process that registered while the tunnel was local never will:
    exactly ONE overlap restart (the one-time cutover), then prove it again.
    Returns True only when the current main process serves `ingress`. Never
    raises; every failure is LOUD and the next install retries."""
    ok, version, action, err = sync_ingress(client, tunnel_id, ingress)
    if not ok:
        print("  webterm%s: LOUD — remote ingress sync FAILED (%s); the connector "
              "and its files are left as they are (#1347)." % (lane, err),
              file=sys.stderr)
        return False
    print("  webterm%s: remote ingress %s (version %s, #1347)."
          % (lane, action, version), file=sys.stderr)
    if not provision():
        print("  webterm%s: LOUD — bootstrap config/unit apply failed (#1347)." % lane,
              file=sys.stderr)
        return False
    pid = _wait_applied(systemctl, service_name, version, config_path, sleep, clock)
    if pid:
        _write_stamp(config_path, pid, version, lane)
        return True
    if _ran_remote(systemctl, service_name, config_path):
        # This process already took remote config: the push is lagging, it is
        # not the cutover. A restart would drop every tab for nothing.
        print("  webterm%s: LOUD — %s has not confirmed remote version %s within "
              "%ds; it already runs remote config, so NO restart — the next "
              "install re-checks (#1347)." % (lane, service_name, version, APPLY_WAIT_S),
              file=sys.stderr)
        return False
    print("  webterm%s: LOUD — %s has not applied remote version %s (it registered "
          "while the tunnel was locally managed): ONE overlap restart, open "
          "tabs reconnect once (#1347 cutover)." % (lane, service_name, version),
          file=sys.stderr)
    ok, shape = cli_tunnel_apply.overlap_restart(
        systemctl, service_name, config_path, sleep=sleep, clock=clock, lane=lane)
    pid = ok and _wait_applied(systemctl, service_name, version, config_path,
                               sleep, clock)
    if pid:
        _write_stamp(config_path, pid, version, lane)
        print("  webterm%s: %s now applies remote config (%s, #1347)."
              % (lane, service_name, shape), file=sys.stderr)
        return True
    print("  webterm%s: LOUD — %s still does not apply remote version %s after the "
          "restart (%s); the next install retries (#1347)."
          % (lane, service_name, version, shape), file=sys.stderr)
    return False


def render_bootstrap_config(tunnel_uuid, credentials_file):
    """The static local YAML of a remotely managed tunnel: identity only. The
    ingress lives in Cloudflare, so a route change never touches this file."""
    return (
        "# airuleset-managed cloudflared tunnel config (webterm, #635/#870/#1347).\n"
        "# The routes are REMOTELY managed: PUT to the Cloudflare API on\n"
        "# every `airuleset.py install` and applied live, so this file holds only\n"
        "# the tunnel identity. Do NOT add ingress here; remote config wins.\n"
        "tunnel: %s\n"
        "credentials-file: %s\n"
        % (tunnel_uuid, credentials_file))
