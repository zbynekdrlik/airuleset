"""Per-project account policy: onboarding gate + legacy inventory (#1184).

Owner ROZHODNUTÉ 2026-09-29: new projects are NEVER created under the shared
``newlevel`` account (weak shared password, NOPASSWD sudo, every fleet key in
its home). Every project gets its own declared account
(``cli_account_bootstrap.SERVICE_ACCOUNTS`` + ``account-bootstrap --render``).
The current newlevel projects are FROZEN as legacy and migrated when touched,
so the fleet converges one project at a time.

This leaf owns the three mechanical halves of that policy:

  * ``account_for_target`` + ``onboard_account_gate`` — the ``onboard-project``
    gate: a target owned by a legacy shared account is REFUSED unless
    ``--legacy-ok <ticket>`` is given (migration bookkeeping only);
  * ``legacy_inventory`` + ``LEGACY_CEILING`` — the frozen inventory from
    ``projects-registry.json`` rows whose ``account`` is legacy, locked
    DOWN-ONLY by tests/test_project_accounts_1184.py;
  * ``cmd_accounts`` — ``airuleset.py accounts status [--json]``.

Stdlib only; ``cli_account_bootstrap`` / ``cli_onboard_exec`` are imported
lazily inside functions (no import cycle with cli_onboard).
"""
import json
import os
import pwd
import re
import sys

# The shared accounts no NEW project may live in. `newlevel` is the owner's
# dev1/dev2 maintainer account; any future legacy-shared account joins here.
LEGACY_SHARED_ACCOUNTS = frozenset({"newlevel"})

# The freeze day (owner ROZHODNUTÉ 2026-09-29). No legacy row may be onboarded
# after it (test-locked).
LEGACY_FREEZE_DATE = "2026-09-29"

# DOWN-ONLY ratchet: the legacy newlevel project count. It must EQUAL the live
# count (test-locked), so a migration lowers it in the same change; raising it
# is a visible, reviewable edit that the freeze forbids.
LEGACY_CEILING = 24

# `--legacy-ok` must name the ticket that carries the migration bookkeeping:
# `#N`, `N`, or `owner/repo#N`.
_TICKET_RE = re.compile(r"^(#?\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)$")
_HOME_RE = re.compile(r"^/home/([^/]+)(/|$)")


def account_for_target(target_path, host=None, run=None):
    """The unix account a project directory lives in. A path under
    ``/home/<user>/`` names ``<user>`` directly (``/root/...`` -> ``root``);
    anything else is the OWNER of the directory (``stat -c %U``, run on the
    target host). Falls back to the host's REMOTE_HOSTS user (remote) or this
    process's user (local) when stat gives nothing."""
    path = str(target_path)
    m = _HOME_RE.match(path)
    if m:
        return m.group(1)
    if path == "/root" or path.startswith("/root/"):
        return "root"
    from cli_onboard_exec import _exec, resolve_remote
    try:
        r = _exec(["stat", "-c", "%U", path], host=host, run=run)
        owner = (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception as e:  # never raise from the gate's probe
        print("accounts: stat of %s failed (%s)" % (path, e), file=sys.stderr)
        owner = ""
    if owner:
        return owner
    remote = resolve_remote(host)
    if remote is not None:
        return remote.get("user")
    return pwd.getpwuid(os.getuid()).pw_name


def is_legacy_account(account):
    return account in LEGACY_SHARED_ACCOUNTS


def onboard_account_gate(account, legacy_ok):
    """None when onboarding into ``account`` is allowed, else the refusal text.
    A legacy shared account is refused unless ``legacy_ok`` names a ticket."""
    if not is_legacy_account(account):
        return None
    if legacy_ok and _TICKET_RE.match(str(legacy_ok).strip()):
        return None
    why = ("--legacy-ok %r does not name a ticket (#N or owner/repo#N)"
           % legacy_ok) if legacy_ok else "no --legacy-ok <ticket> given"
    return ("target lives in the shared legacy account %r — new projects get "
            "their OWN account (#1184): declare it in "
            "cli_account_bootstrap.SERVICE_ACCOUNTS, run `airuleset.py "
            "account-bootstrap --render <acct>` as root on the host, then "
            "onboard the project account path. (%s; --legacy-ok is for "
            "migration bookkeeping only.)" % (account, why))


def legacy_inventory(entries):
    """The registry rows that still live in a legacy shared account, as
    [{name, host, account, path, onboarded}] sorted by host then name."""
    rows = [{"name": e.get("name"), "host": e.get("host"),
             "account": e.get("account"), "path": e.get("path"),
             "onboarded": e.get("onboarded")}
            for e in entries if is_legacy_account(e.get("account"))]
    rows.sort(key=lambda r: (r["host"] or "", r["name"] or ""))
    return rows


def _project_accounts():
    """{account: {host, sudo, reach, secrets, webterm}} from THE declaration."""
    import cli_account_bootstrap as bootstrap
    out = {}
    for name in sorted(bootstrap.SERVICE_ACCOUNTS):
        spec = bootstrap.account_spec(name)
        out[name] = {"host": spec["host"], "sudo": spec["sudo"],
                     "reach": list(spec["reach"]),
                     "secrets": list(spec["secrets"]),
                     "webterm": sorted(spec["webterm_sessions"])}
    return out


def _status(registry_path=None):
    from cli_onboard import default_registry_path, load_registry
    entries = load_registry(registry_path or default_registry_path())
    legacy = legacy_inventory(entries)
    return {"legacy": legacy, "legacy_count": len(legacy),
            "legacy_ceiling": LEGACY_CEILING,
            "project_accounts": _project_accounts()}


def cmd_accounts(args):
    """``airuleset.py accounts status [--json]`` — the project accounts (their
    declaration) and the frozen legacy newlevel inventory with its count."""
    action = getattr(args, "action", None) or "status"
    if action != "status":
        print("usage: airuleset.py accounts status [--json]", file=sys.stderr)
        return 2
    data = _status(getattr(args, "registry", None))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print("project accounts (#1184 declaration):")
    for name, a in data["project_accounts"].items():
        print("  %-12s host=%-10s sudo=%-3s reach=%s secrets=%s webterm=%s"
              % (name, a["host"], "yes" if a["sudo"] else "no",
                 ",".join(a["reach"]) or "none",
                 ",".join(a["secrets"]) or "none",
                 ",".join(a["webterm"]) or "none"))
    print("legacy newlevel projects: %d (ceiling %d, down-only — migrate "
          "on touch)" % (data["legacy_count"], data["legacy_ceiling"]))
    for r in data["legacy"]:
        print("  %-24s %-5s %s" % (r["name"], r["host"], r["path"]))
    return 0


def register_parser(sub):
    """The `accounts` argparse subparser, plus the #1184 `--legacy-ok` flag on
    the already-registered `onboard-project` subparser (this leaf owns the
    gate, so it owns the flag; main() grows by one call only)."""
    onboard = sub.choices.get("onboard-project")
    if onboard is not None:
        onboard.add_argument(
            "--legacy-ok", dest="legacy_ok", metavar="TICKET",
            help="#1184: allow a target in the shared legacy newlevel account "
                 "(migration bookkeeping only)")
    p = sub.add_parser(
        "accounts",
        help="#1184: per-project accounts — `status` lists the declared project "
             "accounts and the frozen legacy newlevel inventory + count")
    p.add_argument("action", nargs="?", default="status", choices=["status"])
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--registry", default=None,
                   help="registry path (default: the repo's projects-registry.json)")
    return p
