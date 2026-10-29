"""Peer channel — the MANAGED side of #1266 (config, per-account install, root leg).

The runtime is ``peer_channel.py`` (one stdlib MCP channel server per Claude
session, self-contained). This leaf owns everything push/install does around it:

* ``peer-links.json`` (repo root): per host, undirected pairs of
  ``projects-registry.json`` project names allowed to message each other.
  ``validate_links`` locks it against the registry.
* ``install_step``: as each account, iff it owns a linked project on this host —
  register the local directory marketplace ``plugins/airuleset`` and install
  ``peer@airuleset`` (user scope), and write ``~/.claude/airuleset-peer-channel.on``,
  the marker the managed launcher reads to add ``--channels plugin:peer@airuleset``.
  An account with no link gets neither (marker removed, plugin uninstalled).
  New sessions only: a live session is never touched.
* ``render_root_script`` / ``provision_peer_channel_root``: as root on every host
  named in ``peer-links.json`` (reached like the #1223 disk-guard root leg,
  ``sudo -n`` as the workstation account), write
  ``/etc/tmpfiles.d/50-airuleset-peer-channel.conf`` — ``/run/claude-peers`` root-owned,
  sticky, ``1700`` plus ONE POSIX ACL entry per linked account (an ACL, not a
  group: a supplementary group never reaches the owner's long-lived tmux server)
  — and ``/etc/claude-code/managed-settings.d/50-airuleset-peer-channel.json``
  (``channelsEnabled`` + ``allowedChannelPlugins``: Anthropic's official channel
  plugins plus ``peer@airuleset``; Claude Code accepts a plugin channel through plain
  ``--channels`` only when that managed allowlist names it, a plain MCP server
  only behind a confirm dialog on every launch). Then a read-back.

``python3 cli_peer_channel.py status|render-root [--host H]|root-apply [--host H]``.
"""
import json
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import peer_channel

REPO_DIR = Path(__file__).resolve().parent
LINKS_PATH = REPO_DIR / peer_channel.LINKS_FILE
REGISTRY_PATH = REPO_DIR / peer_channel.REGISTRY_FILE
MARKETPLACE_DIR = REPO_DIR / "plugins" / "airuleset"
MARKETPLACE = "airuleset"
PLUGIN_KEY = "peer@" + MARKETPLACE
CHANNEL_ARG = "plugin:" + PLUGIN_KEY
MARKER_NAME = "airuleset-peer-channel.on"
TMPFILES_PATH = "/etc/tmpfiles.d/50-airuleset-peer-channel.conf"
POLICY_PATH = "/etc/claude-code/managed-settings.d/50-airuleset-peer-channel.json"
# Anthropic's own channel plugins (claude-plugins-official external_plugins). A
# managed allowedChannelPlugins REPLACES the default allowlist, so they are kept.
OFFICIAL_CHANNEL_PLUGINS = ("discord", "fakechat", "imessage", "telegram")
OFFICIAL_MARKETPLACE = "claude-plugins-official"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_links(path=None):
    with open(path or LINKS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_registry(path=None):
    with open(path or REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_links(links_doc, registry):
    """Every problem in ``links_doc`` as a string (empty = valid): a host entry
    without a ``links`` list, a malformed pair, a self link, or a project that is
    not a registry project ON THAT HOST."""
    errors = []
    hosts = links_doc.get("hosts") if isinstance(links_doc, dict) else None
    if not isinstance(hosts, dict):
        return ["peer-links.json: missing 'hosts' object"]
    for host, entry in sorted(hosts.items()):
        raw = entry.get("links") if isinstance(entry, dict) else None
        if not isinstance(raw, list):
            errors.append("%s: 'links' must be a list" % host)
            continue
        projects = peer_channel.host_projects(registry, host)
        for pair in raw:
            if not (isinstance(pair, list) and len(pair) == 2
                    and all(isinstance(x, str) for x in pair)):
                errors.append("%s: malformed link %r" % (host, pair))
                continue
            if pair[0] == pair[1]:
                errors.append("%s: self link %r" % (host, pair[0]))
            for name in pair:
                if name not in projects:
                    errors.append("%s: %r is not a registry project on %s" % (host, name, host))
    return errors


def accounts_for_host(host, links_doc=None, registry=None):
    """Sorted unix accounts that own a linked project on ``host``."""
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    projects = peer_channel.host_projects(registry, host)
    accts = set()
    for pair in peer_channel.host_links(links_doc, host):
        for name in pair:
            if name in projects:
                accts.add(projects[name]["account"])
    return sorted(accts)


def this_host():
    return socket.gethostname().split(".")[0]


# --------------------------------------------------------------------------- #
# Per-account install step (cmd_install)
# --------------------------------------------------------------------------- #
def _plugin_installed(claude_dir):
    try:
        data = json.loads((claude_dir / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError):
        return False
    return PLUGIN_KEY in (data.get("plugins") or {}) if isinstance(data, dict) else False


def _registered_marketplace_path(claude_dir):
    try:
        data = json.loads((claude_dir / "plugins" / "known_marketplaces.json").read_text())
    except (OSError, ValueError):
        return None
    src = ((data.get(MARKETPLACE) or {}).get("source") or {}) if isinstance(data, dict) else {}
    return src.get("path")


def _claude(run, args):
    from cli_binary_installers import _claude_cli_env
    r = run(["claude", "plugin"] + args, capture_output=True, text=True, timeout=180,
            env=_claude_cli_env())
    return r.returncode, ((r.stderr or "") + (r.stdout or "")).strip()[:300]


def install_step(user=None, host=None, claude_dir=None, run=None, links_doc=None,
                 registry=None):
    """Enable or disable the peer channel for THIS account (idempotent). Returns
    False only when a required step failed (the caller latches install_failed)."""
    if run is None and "PYTEST_CURRENT_TEST" in os.environ:
        print("  peer channel: skipped under pytest (no real `claude plugin` calls)")
        return True
    run = run or subprocess.run
    user = user or os.environ.get("USER") or ""
    host = host or this_host()
    if claude_dir is None:
        import airuleset  # the install's own CLAUDE_DIR (tests patch it)
        claude_dir = airuleset.CLAUDE_DIR
    claude_dir = Path(claude_dir)
    marker = claude_dir / MARKER_NAME
    try:
        linked = user in accounts_for_host(host, links_doc, registry)
    except (OSError, ValueError) as e:
        print("  ⚠ peer channel: config unreadable (%s)" % e, file=sys.stderr)
        return False
    if not linked:
        if marker.exists():
            marker.unlink()
            print("  peer channel: %s has no link on %s — launcher flag removed" % (user, host))
        if _plugin_installed(claude_dir):
            rc, out = _claude(run, ["uninstall", PLUGIN_KEY])
            print("  peer channel: uninstalled %s (rc=%s) %s" % (PLUGIN_KEY, rc, out))
        return True
    ok = True
    if _registered_marketplace_path(claude_dir) != str(MARKETPLACE_DIR):
        if _registered_marketplace_path(claude_dir) is not None:
            _claude(run, ["marketplace", "remove", MARKETPLACE])
        rc, out = _claude(run, ["marketplace", "add", str(MARKETPLACE_DIR)])
        if rc != 0:
            print("  ⚠ peer channel: marketplace add failed rc=%s: %s" % (rc, out),
                  file=sys.stderr)
            return False
    if not _plugin_installed(claude_dir):
        rc, out = _claude(run, ["install", PLUGIN_KEY, "--scope", "user"])
        if rc != 0:
            print("  ⚠ peer channel: install %s failed rc=%s: %s" % (PLUGIN_KEY, rc, out),
                  file=sys.stderr)
            ok = False
    if ok:
        marker.write_text("%s\n" % CHANNEL_ARG)
        print("  peer channel: enabled for %s on %s (%s)" % (user, host, CHANNEL_ARG))
    return ok


# --------------------------------------------------------------------------- #
# Root leg (push)
# --------------------------------------------------------------------------- #
def render_tmpfiles(accounts):
    acl = ",".join(["u:%s:rwx" % a for a in accounts] + ["m::rwx"]) if accounts \
        else "u::rwx,g::---,o::---"
    return ("# airuleset-managed (#1266) — peer channel shared dir. Do not edit.\n"
            "d %s 1700 root root -\n"
            "a %s - - - - %s\n" % (peer_channel.SOCK_DIR, peer_channel.SOCK_DIR, acl))


def render_policy():
    allowed = [{"marketplace": OFFICIAL_MARKETPLACE, "plugin": p}
               for p in OFFICIAL_CHANNEL_PLUGINS]
    allowed.append({"marketplace": MARKETPLACE, "plugin": "peer"})
    return json.dumps({"channelsEnabled": True, "allowedChannelPlugins": allowed},
                      indent=2) + "\n"


def _atomic_write(path, content):
    d = os.path.dirname(path)
    return ("install -d -m 0755 %s\n"
            "tmp=$(mktemp %s/.airuleset-peer.XXXXXX)\n"
            "printf '%%s' %s > \"$tmp\"\n"
            "chmod 0644 \"$tmp\"\n"
            "mv -f \"$tmp\" %s\n" % (shlex.quote(d), shlex.quote(d), shlex.quote(content),
                                     shlex.quote(path)))


def render_root_script(accounts):
    """Idempotent root script: tmpfiles + policy drop-in + apply + read-back.
    Exit 3 = a named account does not exist, 4 = read-back mismatch."""
    for a in accounts:
        if not peer_channel.PROJECT_RE.match(a):
            raise ValueError("bad account name %r" % a)
    want = " ".join(shlex.quote(a) for a in accounts)
    d = peer_channel.SOCK_DIR
    return "".join([
        "set -euo pipefail\ncd /\numask 022\n",
        "for a in %s; do id -u \"$a\" >/dev/null 2>&1 || "
        "{ echo \"peer-channel-root: account $a missing\" >&2; exit 3; }; done\n" % want,
        _atomic_write(TMPFILES_PATH, render_tmpfiles(accounts)),
        _atomic_write(POLICY_PATH, render_policy()),
        "systemd-tmpfiles --create %s\n" % TMPFILES_PATH,
        # read-back: owner/sticky, the exact set of named ACL users, a parseable policy
        "[ \"$(stat -c '%%U:%%G' %s)\" = root:root ] || "
        "{ echo 'peer-channel-root: dir owner wrong' >&2; exit 4; }\n" % d,
        "[ -k %s ] || { echo 'peer-channel-root: sticky bit missing' >&2; exit 4; }\n" % d,
        "got=$(getfacl -cp %s | sed -n 's/^user:\\([^:]\\+\\):.*/\\1/p' | sort | xargs)\n" % d,
        "[ \"$got\" = %s ] || { echo \"peer-channel-root: ACL users '$got' != %s\" >&2; "
        "exit 4; }\n" % (shlex.quote(" ".join(accounts)), shlex.quote(" ".join(accounts))),
        "python3 -I -c 'import json,sys; json.load(open(sys.argv[1]))' %s || "
        "{ echo 'peer-channel-root: policy unreadable' >&2; exit 4; }\n" % POLICY_PATH,
        "echo \"peer-channel-root: ok dir=%s acl=[$got]\"\n" % d,
    ])


def _root_hosts(links_doc):
    import airuleset  # L-E: DISK_GUARD_ROOT_HOSTS lives in cli_fleet, via the facade
    named = set((links_doc.get("hosts") or {}).keys())
    return [h for h in airuleset.DISK_GUARD_ROOT_HOSTS if h.get("name") in named]


def provision_peer_channel_root(hosts=None, run=None, links_doc=None, registry=None,
                                only=None):
    """Apply the root leg on every host named in peer-links.json. NON-FATAL + LOUD
    like the disk-guard root leg: returns ``[(name, reason)]`` failures."""
    if run is None and "PYTEST_CURRENT_TEST" in os.environ:
        print("  peer-channel-root: skipped under pytest (no real ssh)")
        return []
    run = run or subprocess.run
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    errors = validate_links(links_doc, registry)
    if errors:
        print("  ⚠ PEER-CHANNEL-ROOT FAILED: peer-links.json invalid: %s" % "; ".join(errors),
              file=sys.stderr)
        return [("config", "invalid")]
    hosts = _root_hosts(links_doc) if hosts is None else hosts
    failed = []
    for h in hosts:
        name = h.get("name", "?")
        if only and name != only:
            continue
        accounts = accounts_for_host(name, links_doc, registry)
        remote = (("sudo -n " if h.get("sudo") else "") + "bash -c "
                  + shlex.quote(render_root_script(accounts)))
        argv = ["ssh", "-i", os.path.expanduser(h.get("identity", "")),
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=15", "%s@%s" % (h.get("admin_user", "root"), h["host"]),
                remote]
        try:
            r = run(argv, capture_output=True, text=True, timeout=120)
        except Exception as e:  # noqa: BLE001 — best-effort, never break push
            print("  ⚠ PEER-CHANNEL-ROOT FAILED (%s): %r" % (name, e), file=sys.stderr)
            failed.append((name, repr(e)))
            continue
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            print("  ⚠ PEER-CHANNEL-ROOT FAILED (%s) rc=%s: %s" % (name, r.returncode, out[:400]),
                  file=sys.stderr)
            failed.append((name, "rc=%s" % r.returncode))
        else:
            print("  peer-channel-root: %s accounts=%s %s" % (name, ",".join(accounts) or "-", out))
    return failed


def status_lines(host=None, claude_dir=None, links_doc=None, registry=None):
    host = host or this_host()
    claude_dir = Path(claude_dir or Path.home() / ".claude")
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    lines = ["host %s: links %s" % (host, sorted(
        "<->".join(sorted(p)) for p in peer_channel.host_links(links_doc, host)) or "none"),
             "accounts: %s" % (", ".join(accounts_for_host(host, links_doc, registry)) or "none"),
             "this account: marker %s, plugin %s" % (
                 "on" if (claude_dir / MARKER_NAME).exists() else "off",
                 "installed" if _plugin_installed(claude_dir) else "absent"),
             "shared dir %s: %s" % (peer_channel.SOCK_DIR,
                                     "present" if os.path.isdir(peer_channel.SOCK_DIR)
                                     else "missing")]
    lines += ["config error: %s" % e for e in validate_links(links_doc, registry)]
    return lines


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="peer channel managed side (#1266)")
    ap.add_argument("action", choices=["status", "render-root", "root-apply"])
    ap.add_argument("--host")
    a = ap.parse_args(argv)
    if a.action == "status":
        print("\n".join(status_lines(a.host)))
        return 0
    if a.action == "render-root":
        sys.stdout.write(render_root_script(accounts_for_host(a.host or this_host())))
        return 0
    return 1 if provision_peer_channel_root(only=a.host) else 0


if __name__ == "__main__":
    sys.exit(main())
