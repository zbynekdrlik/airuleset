"""Peer channel — the MANAGED side of #1266 (config, per-account install, root leg).

The runtime is ``peer_channel.py`` (one stdlib MCP channel server per Claude
session, self-contained). This leaf owns everything push/install does around it:

* ``peer-links.json`` (repo root): per host, undirected pairs of
  ``projects-registry.json`` project names allowed to message each other. A host
  key must be both a registry host and a ``DISK_GUARD_ROOT_HOSTS`` name (the box's
  own hostname), so a link can never silently miss its runtime or its root leg.
* ``install_step``: as each account (identity from ``pwd``, never ``$USER``), iff
  it owns a linked project on this host — register the local directory marketplace
  ``plugins/airuleset``, install ``peer@airuleset`` (user scope) and write
  ``~/.claude/airuleset-peer-channel.on``, the marker the managed launcher reads to
  add ``--channels plugin:peer@airuleset``. An unlinked account gets the marker,
  plugin and marketplace removed. New sessions only: a live session is untouched.
* ``render_root_script`` / ``provision_peer_channel_root``: as root (the #1223
  ``sudo -n`` workstation path of ``DISK_GUARD_ROOT_HOSTS``) on every host named in
  ``peer-links.json``: a dedicated 1 MiB tmpfs ``/run/claude-peers`` (a systemd
  mount unit, so no account can fill the system ``/run``), ``root:root 0750`` with
  one ``r-x`` ACL per linked account, one ``<account>/`` subdir (0755) owned by
  each account, and the managed policy drop-in
  ``/etc/claude-code/managed-settings.d/50-airuleset-peer-channel.json``
  (``channelsEnabled`` + ``allowedChannelPlugins``: Anthropic's official channel
  plugins plus ``peer@airuleset``; Claude Code accepts a plugin channel through
  plain ``--channels`` only when that managed allowlist names it, a plain MCP
  server only behind a confirm dialog on every launch). Files are rewritten only
  when they changed; then a strict read-back. A root host that is no longer named
  gets a teardown (only where our files exist).

``python3 cli_peer_channel.py status|render-root [--host H]|root-apply [--host H]``.
"""
import json
import os
import pwd
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
MCP_JSON_PATH = MARKETPLACE_DIR / "peer" / ".mcp.json"
MARKETPLACE = "airuleset"
PLUGIN_KEY = "peer@" + MARKETPLACE
CHANNEL_ARG = peer_channel.CHANNEL_ARG
MARKER_NAME = "airuleset-peer-channel.on"
SOCK_DIR = peer_channel.SOCK_DIR
MOUNT_UNIT = "run-claude\\x2dpeers.mount"          # systemd-escape -p --suffix=mount
MOUNT_UNIT_PATH = "/etc/systemd/system/" + MOUNT_UNIT
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


def root_host_names():
    import airuleset  # L-E: DISK_GUARD_ROOT_HOSTS lives in cli_fleet, via the facade
    return {h.get("name") for h in airuleset.DISK_GUARD_ROOT_HOSTS}


def validate_links(links_doc, registry, root_hosts=None):
    """Every problem in ``links_doc`` as a string (empty = valid): an unknown host
    (not a ``DISK_GUARD_ROOT_HOSTS`` name), a host entry without a ``links`` list, a
    malformed pair, a self link, or a project that is not a registry project ON
    THAT HOST."""
    root_hosts = root_host_names() if root_hosts is None else root_hosts
    errors = []
    hosts = links_doc.get("hosts") if isinstance(links_doc, dict) else None
    if not isinstance(hosts, dict):
        return ["peer-links.json: missing 'hosts' object"]
    for host, entry in sorted(hosts.items()):
        if host not in root_hosts:
            errors.append("%s: not a root-leg host (DISK_GUARD_ROOT_HOSTS)" % host)
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
    """Sorted unix accounts that own a linked project on ``host``. A declared
    project account still ``pending`` (no root bootstrap yet) is left out, so
    the root leg never names a missing account (its exit 3 would fail the
    whole host, #1267); it joins on the first push after it goes live."""
    import cli_account_bootstrap as bootstrap
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    projects = peer_channel.host_projects(registry, host)
    accts = set()
    for pair in peer_channel.host_links(links_doc, host):
        for name in pair:
            if name in projects:
                accts.add(projects[name]["account"])
    return sorted(a for a in accts if not bootstrap.is_pending(a))


def this_host():
    return socket.gethostname().split(".")[0]


# --------------------------------------------------------------------------- #
# Per-account install step (cmd_install)
# --------------------------------------------------------------------------- #
def _plugin_installed(claude_dir):
    from cli_caveman_plugins import _plugin_registry_keys
    return PLUGIN_KEY in _plugin_registry_keys(claude_dir / "plugins" / "installed_plugins.json")


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


def _disable(run, claude_dir, marker, user, host):
    """Unlinked account: marker, plugin and marketplace go. False on a failed removal."""
    ok = True
    if marker.exists():
        marker.unlink()
        print("  peer channel: %s has no link on %s — launcher flag removed" % (user, host))
    if _plugin_installed(claude_dir):
        rc, out = _claude(run, ["uninstall", PLUGIN_KEY])
        print("  peer channel: uninstall %s rc=%s %s" % (PLUGIN_KEY, rc, out))
        ok = ok and rc == 0
    if _registered_marketplace_path(claude_dir) is not None:
        rc, out = _claude(run, ["marketplace", "remove", MARKETPLACE])
        print("  peer channel: marketplace remove %s rc=%s %s" % (MARKETPLACE, rc, out))
        ok = ok and rc == 0
    return ok


def install_step(user=None, host=None, claude_dir=None, run=None, links_doc=None,
                 registry=None):
    """Enable or disable the peer channel for THIS account (idempotent). Returns
    False only when a required step failed (the caller latches install_failed)."""
    if run is None and "PYTEST_CURRENT_TEST" in os.environ:
        print("  peer channel: skipped under pytest (no real `claude plugin` calls)")
        return True
    run = run or subprocess.run
    user = user or pwd.getpwuid(os.getuid()).pw_name
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
        return _disable(run, claude_dir, marker, user, host)

    def fail(what, rc, out):
        marker.unlink(missing_ok=True)   # never a launcher flag for a missing plugin
        print("  ⚠ peer channel: %s failed rc=%s: %s" % (what, rc, out), file=sys.stderr)
        return False

    registered = _registered_marketplace_path(claude_dir)
    if registered != str(MARKETPLACE_DIR):
        if registered is not None:
            rc, out = _claude(run, ["marketplace", "remove", MARKETPLACE])
            if rc != 0:
                return fail("marketplace remove", rc, out)
        rc, out = _claude(run, ["marketplace", "add", str(MARKETPLACE_DIR)])
        if rc != 0:
            return fail("marketplace add", rc, out)
    if not _plugin_installed(claude_dir):
        rc, out = _claude(run, ["install", PLUGIN_KEY, "--scope", "user"])
        if rc != 0:
            return fail("install " + PLUGIN_KEY, rc, out)
    marker.write_text("%s\n" % CHANNEL_ARG)
    print("  peer channel: enabled for %s on %s (%s)" % (user, host, CHANNEL_ARG))
    return True


# --------------------------------------------------------------------------- #
# Root leg (push)
# --------------------------------------------------------------------------- #
def render_mount_unit():
    return ("# airuleset-managed (#1266) — peer channel socket tmpfs. Do not edit.\n"
            "[Unit]\nDescription=airuleset peer channel sockets (#1266)\n\n"
            "[Mount]\nWhat=tmpfs\nWhere=%s\nType=tmpfs\n"
            "Options=mode=0750,size=1m,nr_inodes=1024,nosuid,nodev,noexec\n\n"
            "[Install]\nWantedBy=local-fs.target\n" % SOCK_DIR)


def render_tmpfiles(accounts):
    lines = ["# airuleset-managed (#1266) — peer channel dirs. Do not edit.",
             "d %s 0750 root root -" % SOCK_DIR]
    acl = ["u::rwx", "g::---", "o::---"] + ["u:%s:r-x" % a for a in accounts] + ["m::r-x"]
    lines.append("a %s - - - - %s" % (SOCK_DIR, ",".join(acl)))
    lines += ["d %s/%s 0755 %s %s -" % (SOCK_DIR, a, a, a) for a in accounts]
    return "\n".join(lines) + "\n"


def render_policy():
    allowed = [{"marketplace": OFFICIAL_MARKETPLACE, "plugin": p}
               for p in OFFICIAL_CHANNEL_PLUGINS]
    allowed.append({"marketplace": MARKETPLACE, "plugin": "peer"})
    return json.dumps({"channelsEnabled": True, "allowedChannelPlugins": allowed},
                      indent=2) + "\n"


def expected_acl(accounts):
    """``LC_ALL=C getfacl -cp`` of the dir, as the read-back compares it."""
    return "\n".join(["user::rwx"] + ["user:%s:r-x" % a for a in accounts]
                     + ["group::---", "mask::r-x", "other::---"])


_INSTALL_FN = (
    # Atomic + idempotent: content on stdin, dotted mktemp in the dest dir, EXIT-trap
    # cleanup, an identical file is left alone (CHANGED stays empty).
    '_install() {\n'
    '    dest="$1"; dir=$(dirname "$dest")\n'
    '    install -d -m 0755 "$dir"\n'
    '    tmp=$(mktemp "$dir/.airuleset-peer-XXXXXX")\n'
    "    trap 'rm -f \"$tmp\"' EXIT\n"
    '    cat > "$tmp"\n'
    '    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then rm -f "$tmp"; trap - EXIT; return 0; fi\n'
    '    chmod 0644 "$tmp"; mv -f "$tmp" "$dest"; trap - EXIT\n'
    '    CHANGED="$CHANGED $dest"; echo "  wrote $dest"\n'
    '}\n')


def _heredoc(dest, content):
    mark = "AIRULESET_PEER_EOF"
    assert mark not in content
    return "_install %s <<'%s'\n%s%s\n" % (shlex.quote(dest), mark, content, mark)


def render_root_script(accounts):
    """Idempotent root script: mount unit + tmpfiles + policy drop-in, applied
    only when changed, then a strict read-back. Exit 3 = a named account does not
    exist, 4 = read-back mismatch."""
    if not accounts:
        raise ValueError("no linked account: use render_teardown_script")
    for a in accounts:
        if not peer_channel.PROJECT_RE.match(a):
            raise ValueError("bad account name %r" % a)
    want = " ".join(shlex.quote(a) for a in accounts)
    unit, d = shlex.quote(MOUNT_UNIT), SOCK_DIR
    return "".join([
        "set -euo pipefail\nexport LC_ALL=C\ncd /\numask 022\nCHANGED=''\n",
        "for a in %s; do id -u \"$a\" >/dev/null 2>&1 || "
        "{ echo \"peer-channel-root: account $a missing\" >&2; exit 3; }; done\n" % want,
        _INSTALL_FN,
        _heredoc(MOUNT_UNIT_PATH, render_mount_unit()),
        _heredoc(TMPFILES_PATH, render_tmpfiles(accounts)),
        _heredoc(POLICY_PATH, render_policy()),
        "case \"$CHANGED\" in *peers.mount*) systemctl daemon-reload ;; esac\n",
        "systemctl enable --now %s\n" % unit,
        # tmpfiles re-applies the ACL; skip it when nothing changed (no transient
        # mask reset under live sessions) unless a dir is missing (fresh boot state)
        "need=''; [ -n \"$CHANGED\" ] && need=1\n",
        "for a in %s; do [ -d %s/\"$a\" ] || need=1; done\n" % (want, d),
        "[ -z \"$need\" ] || systemd-tmpfiles --create %s\n" % TMPFILES_PATH,
        "[ \"$(findmnt -no FSTYPE %s)\" = tmpfs ] || "
        "{ echo 'peer-channel-root: not a tmpfs mount' >&2; exit 4; }\n" % d,
        "[ \"$(stat -c '%%U:%%G %%a' %s)\" = 'root:root 750' ] || "
        "{ echo \"peer-channel-root: dir $(stat -c '%%U:%%G %%a' %s)\" >&2; exit 4; }\n" % (d, d),
        "acl=$(getfacl -cp %s | sed '/^$/d' | sort)\n" % d,
        "[ \"$acl\" = \"$(printf '%%s\\n' %s | sort)\" ] || "
        "{ echo \"peer-channel-root: ACL mismatch: $acl\" >&2; exit 4; }\n"
        % shlex.quote(expected_acl(accounts)),
        "for a in %s; do [ \"$(stat -c '%%U %%a' %s/\"$a\")\" = \"$a 755\" ] || "
        "{ echo \"peer-channel-root: subdir $a wrong\" >&2; exit 4; }; done\n" % (want, d),
        "python3 -I -c 'import json,sys; json.load(open(sys.argv[1]))' %s || "
        "{ echo 'peer-channel-root: policy unreadable' >&2; exit 4; }\n" % POLICY_PATH,
        "echo \"peer-channel-root: ok dir=%s accounts=[%s] changed=[${CHANGED# }]\"\n"
        % (d, " ".join(accounts)),
    ])


def render_teardown_script():
    """A root host no longer named in peer-links.json: remove our files (only if
    present) and stop the mount. Idempotent no-op on a host that never had it."""
    unit = shlex.quote(MOUNT_UNIT)
    return ("set -euo pipefail\ncd /\n"
            "[ -e %s ] || [ -e %s ] || { echo 'peer-channel-root: nothing to tear down'; exit 0; }\n"
            "systemctl disable --now %s >/dev/null 2>&1 || true\n"
            "rm -f %s %s %s\nsystemctl daemon-reload\n"
            "echo 'peer-channel-root: torn down'\n"
            % (shlex.quote(TMPFILES_PATH), shlex.quote(POLICY_PATH), unit,
               shlex.quote(MOUNT_UNIT_PATH), shlex.quote(TMPFILES_PATH),
               shlex.quote(POLICY_PATH)))


def provision_peer_channel_root(hosts=None, run=None, links_doc=None, registry=None,
                                only=None):
    """Apply the root leg on every ``DISK_GUARD_ROOT_HOSTS`` host: the full leg where
    peer-links.json names the host, a teardown elsewhere. NON-FATAL + LOUD like the
    disk-guard root leg: returns ``[(name, reason)]`` failures."""
    if run is None and "PYTEST_CURRENT_TEST" in os.environ:
        print("  peer-channel-root: skipped under pytest (no real ssh)")
        return []
    run = run or subprocess.run
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    if hosts is None:
        import airuleset  # L-E: via the facade
        hosts = airuleset.DISK_GUARD_ROOT_HOSTS
    errors = validate_links(links_doc, registry, {h.get("name") for h in hosts})
    if errors:
        print("  ⚠ PEER-CHANNEL-ROOT FAILED: peer-links.json invalid: %s" % "; ".join(errors),
              file=sys.stderr)
        return [("config", "invalid")]
    named = set(links_doc.get("hosts", {}))
    failed = []
    for h in hosts:
        name = h.get("name", "?")
        if only and name != only:
            continue
        if not h.get("identity") or not h.get("host"):
            print("  ⚠ PEER-CHANNEL-ROOT FAILED (%s): no host / pinned ssh identity" % name,
                  file=sys.stderr)
            failed.append((name, "no-identity"))
            continue
        accounts = accounts_for_host(name, links_doc, registry) if name in named else []
        script = render_root_script(accounts) if accounts else render_teardown_script()
        remote = ("sudo -n " if h.get("sudo") else "") + "bash -c " + shlex.quote(script)
        argv = ["ssh", "-i", os.path.expanduser(h["identity"]), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
                "%s@%s" % (h.get("admin_user", "root"), h["host"]), remote]
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
            print("  peer-channel-root: %s %s" % (name, out.replace("\n", " | ")))
    return failed


def status_lines(host=None, claude_dir=None, links_doc=None, registry=None):
    host = host or this_host()
    claude_dir = Path(claude_dir or Path.home() / ".claude")
    links_doc = load_links() if links_doc is None else links_doc
    registry = load_registry() if registry is None else registry
    pairs = sorted("<->".join(sorted(p)) for p in peer_channel.host_links(links_doc, host))
    lines = ["host %s: links %s" % (host, pairs or "none"),
             "accounts: %s" % (", ".join(accounts_for_host(host, links_doc, registry)) or "none"),
             "this account: marker %s, plugin %s" % (
                 "on" if (claude_dir / MARKER_NAME).exists() else "off",
                 "installed" if _plugin_installed(claude_dir) else "absent"),
             "shared dir %s: %s" % (SOCK_DIR, "present" if os.path.isdir(SOCK_DIR) else "missing")]
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
