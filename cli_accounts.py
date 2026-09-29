"""Per-project account policy: onboarding gate + legacy inventory (#1184).

Owner ROZHODNUTÉ 2026-09-29: new projects are NEVER created under the shared
``newlevel`` account (weak shared password, NOPASSWD sudo, every fleet key in
its home). Every project gets its OWN declared account
(``cli_account_bootstrap.SERVICE_ACCOUNTS`` + ``account-bootstrap --render``).
Projects that live anywhere else are FROZEN as legacy and migrated when
touched, so the fleet converges one project at a time.

This leaf owns the three mechanical halves of that policy:

  * ``account_for_target`` + ``onboard_account_gate`` — the ``onboard-project``
    gate. It is an ALLOW-list: only a DECLARED project account's OWN project
    directory on its declared host passes (one account per project). Anything
    else (``newlevel``, the control accounts, a stream account, an undeclared
    account) is REFUSED, unless ``--legacy-ok <ticket>`` names a ticket AND the
    exact path already has a registry row on that host (migration bookkeeping
    only — never a new project). The directory and its owner are read on the
    target (``realpath -e`` + ``stat``), never from the typed path, and
    onboarding writes only as that owner;
  * ``legacy_inventory`` + ``LEGACY_CEILING`` — every registry row not in a
    declared project account, locked DOWN-ONLY by
    tests/test_project_accounts_1184.py;
  * ``cmd_accounts`` — ``airuleset.py accounts status [--json]``;
  * ``transfer_session`` — ``accounts transfer-session <acct> --from-dir <old
    checkout> [--render | --apply]`` (#1190): the migration step that carries
    the Claude conversation into the account (``cli_account_session``).

Stdlib only; ``cli_account_bootstrap`` / ``cli_account_policy`` /
``cli_onboard_exec`` / ``cli_onboard`` are imported lazily inside functions (no
import cycle with cli_onboard).
"""
import json
import os
import pwd
import re
import subprocess
import sys

# The freeze day (owner ROZHODNUTÉ 2026-09-29). No legacy row may be onboarded
# after it (test-locked).
LEGACY_FREEZE_DATE = "2026-09-29"

# DOWN-ONLY ratchet: the legacy project count (every registry row outside a
# declared project account: the 24 `newlevel` rows + odoo-erp in `gatekeeper`).
# It must EQUAL the live count (test-locked), so a migration lowers it in the
# same change; raising it is a visible, reviewable edit that the freeze forbids.
LEGACY_CEILING = 24

# `--legacy-ok` must name the ticket that carries the migration bookkeeping:
# `#N` or `owner/repo#N` (a bare number is ambiguous — refused).
_TICKET_RE = re.compile(r"(#\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)")
# projects-registry.json spells the gatekeeper box `gk` (its fleet name is
# `gatekeeper`); compare the two in the fleet namespace.
_REGISTRY_HOST_ALIASES = {"gk": "gatekeeper"}


def _declared_accounts():
    import cli_account_bootstrap as bootstrap
    return bootstrap.SERVICE_ACCOUNTS


def is_legacy_account(account):
    """True for every account that is NOT a declared project account."""
    return account not in _declared_accounts()


def resolve_target(target_path, host=None, run=None):
    """(resolved_path, owner) of the project directory, read ON the target
    (``realpath -e`` then ``stat -c %U`` of the resolved path, over ssh for a
    remote host) — never inferred from the typed path, so ``..``/``.``/symlink
    tricks resolve to the real directory and its real owner. (None, None) when
    it cannot be determined (the gate then refuses)."""
    from cli_onboard_exec import _exec
    try:
        r = _exec(["realpath", "-e", str(target_path)], host=host, run=run)
        resolved = (r.stdout or "").strip() if r.returncode == 0 else ""
        if not resolved.startswith("/"):
            return None, None
        r = _exec(["stat", "-c", "%U", resolved], host=host, run=run)
        owner = (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception as e:  # never raise from the gate's probe
        print("accounts: owner probe of %s failed (%s)" % (target_path, e),
              file=sys.stderr)
        return None, None
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner):
        return resolved, None
    return resolved, owner


def account_for_target(target_path, host=None, run=None):
    """The unix account that OWNS the project directory (``resolve_target``)."""
    return resolve_target(target_path, host, run)[1]


def login_user_for_host(host):
    """The unix user onboarding runs AS: the REMOTE_HOSTS entry user for a
    remote host, else this process's own user (pwd-based, env-spoof-proof)."""
    from cli_onboard_exec import resolve_remote
    remote = resolve_remote(host)
    if remote is not None:
        return remote.get("user")
    return pwd.getpwuid(os.getuid()).pw_name


def _host_box(host):
    """The fleet box a `--host` value names (`dev1`, `claudy@controller` ->
    `controller`); a local/None host is this box's own name."""
    from cli_onboard_exec import is_local_host, _local_hostname
    if is_local_host(host):
        name = _local_hostname() or ""
        return "controller" if name == "airuleset" else name
    return str(host).split("@")[-1]


def onboard_account_gate(account, legacy_ok, *, host=None, login_user=None,
                         existing=None, resolved=None):
    """None when onboarding into ``account`` is allowed, else the refusal text.

    Allowed: a DECLARED project account on its declared host, and only ITS OWN
    project directory (``resolved`` == ``/home/<acct>/<project_dir>``) — one
    account per project, never a second project sharing its uid. Everything
    else is refused, unless ``legacy_ok`` names a ticket AND ``existing`` is
    the registry row matched by PATH on this host — migration bookkeeping,
    never a new project. Onboarding also writes (git, CLAUDE.md), so it must
    run AS the owner: a ``login_user`` that differs from ``account`` refuses."""
    if not account:
        return ("cannot determine which unix account owns the target "
                "directory (realpath/stat failed) — refusing (#1184)")
    if login_user and login_user != account:
        return ("the target is owned by %r but onboarding would run as %r — "
                "onboard as the owner (`--host %s@<box>`, its REMOTE_HOSTS entry) "
                "(#1184)" % (account, login_user, account))
    declared = _declared_accounts()
    if account in declared:
        want = declared[account].get("host", "controller")
        if _host_box(host) != want:
            return ("project account %r is declared on %r, not on %r (#1184)"
                    % (account, want, _host_box(host)))
        project_dir = declared[account].get("project_dir")
        if not project_dir or resolved != "/home/%s/%s" % (account, project_dir):
            return ("%r is not the declared project directory of %r "
                    "(/home/%s/%s) — one account per project (#1184)"
                    % (resolved, account, account, project_dir))
        return None
    row_box = _REGISTRY_HOST_ALIASES.get((existing or {}).get("host"),
                                         (existing or {}).get("host"))
    if (legacy_ok and _TICKET_RE.fullmatch(str(legacy_ok).strip()) and existing
            and row_box == _host_box(host)):
        return None
    why = ("--legacy-ok needs a ticket (#N or owner/repo#N) AND the registry "
           "row of this exact path on this host" if legacy_ok
           else "no --legacy-ok <ticket> given")
    return ("target lives in %r, which is not a declared project account — new "
            "projects get their OWN account (#1184): declare it in "
            "cli_account_bootstrap.SERVICE_ACCOUNTS, run `airuleset.py "
            "account-bootstrap --render <acct>` as root on the host, then onboard "
            "the project account path. (%s; --legacy-ok is for migration "
            "bookkeeping of an already-registered project only.)" % (account, why))


def legacy_inventory(entries):
    """The registry rows NOT in a declared project account, as
    [{name, host, account, path, onboarded}] sorted by host then name."""
    rows = [{"name": e.get("name"), "host": e.get("host"),
             "account": e.get("account"), "path": e.get("path"),
             "onboarded": e.get("onboarded")}
            for e in entries if is_legacy_account(e.get("account"))]
    rows.sort(key=lambda r: (r["host"] or "", r["name"] or ""))
    return rows


def _project_accounts():
    """{account: {host, sudo, sudo_commands, reach, secrets, webterm}} from THE
    declaration. ``reach`` keeps each entry as declared: a fleet box name, or
    a #1186 LAN host ``{cidr, ports, reason}``."""
    import cli_account_bootstrap as bootstrap
    out = {}
    for name in sorted(bootstrap.SERVICE_ACCOUNTS):
        spec = bootstrap.account_spec(name)
        out[name] = {"host": spec["host"], "sudo": spec["sudo"],
                     "sudo_commands": list(spec.get("sudo_commands") or []),
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


def transfer_session(account, from_dir, *, from_home=None, render=False,
                     apply=False, target_home=None, target_cwd=None,
                     proc_root="/proc"):
    """#1190: copy the Claude conversation of the old checkout ``from_dir`` into
    the declared project account. Default = dry run (the listing); ``render``
    prints the root script; ``apply`` runs it and needs root. The target is
    the declaration's ``/home/<acct>/<project_dir>`` (tests pass fake roots)."""
    import cli_account_session as session
    declared = _declared_accounts()
    if account not in declared:
        print("transfer-session: %r is not a declared project account (#1184 "
              "SERVICE_ACCOUNTS)" % (account,), file=sys.stderr)
        return 1
    import cli_account_bootstrap as bootstrap
    project_dir = bootstrap.account_spec(account).get("project_dir")
    if not project_dir:
        print("transfer-session: %r declares no project_dir" % account,
              file=sys.stderr)
        return 1
    target_home = target_home or "/home/%s" % account
    target_cwd = target_cwd or "%s/%s" % (target_home, project_dir)
    from_home = from_home or session.default_from_home(from_dir or "")
    if not from_home:
        print("transfer-session: cannot tell whose home %r is — pass --from-home"
              % (from_dir,), file=sys.stderr)
        return 1
    try:
        plan = session.build_plan(account, from_dir, from_home=from_home,
                                  target_home=target_home, target_cwd=target_cwd,
                                  proc_root=proc_root)
    except ValueError as e:
        print("transfer-session: %s" % e, file=sys.stderr)
        return 1
    if plan["refusals"]:
        # stderr ONLY: `--render | sudo bash` must never feed a listing (raw,
        # owner-chosen file names) to root bash
        print(session.format_plan(plan), file=sys.stderr)
        for r in plan["refusals"]:
            print("REFUSED: %s" % r, file=sys.stderr)
        return 1
    if render:
        print(session.render_script(plan))
        return 0
    if apply:
        if os.geteuid() != 0:
            print("transfer-session --apply runs the root script: run it as root "
                  "(sudo), or pipe --render into `sudo bash`", file=sys.stderr)
            return 1
        return subprocess.run(["bash", "-s"], input=session.render_script(plan),
                              text=True, check=False).returncode
    print(session.format_plan(plan))
    return 0


def cmd_accounts(args):
    """``airuleset.py accounts status [--json]`` — the project accounts (their
    declaration) and the frozen legacy inventory with its count;
    ``accounts transfer-session <acct> --from-dir <dir>`` (#1190)."""
    action = getattr(args, "action", None) or "status"
    if action == "transfer-session":
        if not getattr(args, "account", None) or not getattr(args, "from_dir", None):
            print("usage: airuleset.py accounts transfer-session <account> "
                  "--from-dir <old checkout> [--from-home DIR] [--render | --apply]",
                  file=sys.stderr)
            return 2
        return transfer_session(args.account, args.from_dir,
                                from_home=getattr(args, "from_home", None),
                                render=bool(getattr(args, "render", False)),
                                apply=bool(getattr(args, "apply", False)))
    stray = (isinstance(getattr(args, "account", None), str)
             or isinstance(getattr(args, "from_dir", None), str)
             or getattr(args, "render", None) is True
             or getattr(args, "apply", None) is True)
    if action != "status" or stray:
        print("usage: airuleset.py accounts status [--json] | transfer-session "
              "<account> --from-dir <old checkout>", file=sys.stderr)
        return 2
    data = _status(getattr(args, "registry", None))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    import cli_account_policy as policy
    print("project accounts (#1184 declaration):")
    for name, a in data["project_accounts"].items():
        lan = policy.reach_parts(a)[1]
        print("  %-12s host=%-10s sudo=%-3s reach=%s secrets=%s webterm=%s"
              % (name, a["host"], policy.sudo_label(a["sudo"]),
                 policy.reach_summary(a),
                 ",".join(a["secrets"]) or "none",
                 ",".join(a["webterm"]) or "none"))
        for entry in lan:   # #1186: every LAN host + its ports + its reason
            print("      reach %s — %s" % (policy.lan_label(entry),
                                          entry["reason"]))
        for path in a["sudo_commands"] if a["sudo"] == "commands" else ():
            print("      sudo %s" % path)
    print("legacy projects (no declared project account): %d (ceiling %d, "
          "down-only — migrate on touch)" % (data["legacy_count"],
                                             data["legacy_ceiling"]))
    for r in data["legacy"]:
        print("  %-24s %-5s %-10s %s" % (r["name"], r["host"], r["account"],
                                         r["path"]))
    return 0


def register_parser(sub):
    """The `accounts` argparse subparser, plus the #1184 `--legacy-ok` flag on
    the already-registered `onboard-project` subparser (this leaf owns the
    gate, so it owns the flag; main() grows by one call only)."""
    onboard = sub.choices.get("onboard-project")
    if onboard is not None:
        onboard.add_argument(
            "--legacy-ok", dest="legacy_ok", metavar="TICKET",
            help="#1184: bookkeeping for an ALREADY-registered project outside "
                 "a declared project account (never a new project)")
    p = sub.add_parser(
        "accounts",
        help="#1184: per-project accounts — `status` lists the declared project "
             "accounts and the frozen legacy inventory + count; "
             "`transfer-session` carries a Claude conversation into one (#1190)")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "transfer-session"])
    p.add_argument("account", nargs="?", default=None,
                   help="transfer-session: the declared project account")
    p.add_argument("--from-dir", dest="from_dir", default=None,
                   help="transfer-session: the OLD checkout the session ran in")
    p.add_argument("--from-home", dest="from_home", default=None,
                   help="transfer-session: the old account's home (default: "
                        "/home/<user> of --from-dir)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--render", action="store_true",
                      help="transfer-session: print the root script")
    mode.add_argument("--apply", action="store_true",
                      help="transfer-session: run the root script (as root); "
                           "default is a dry run")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--registry", default=None,
                   help="registry path (default: the repo's projects-registry.json)")
    return p
