"""Sudo + reach policy of a project-account declaration (#1184, #1186).

The two privilege axes of ``cli_account_bootstrap.SERVICE_ACCOUNTS`` are
validated here, fail-closed, before anything is rendered into a root script:

  * ``validate_sudo`` — ``sudo: False`` (no route), ``sudo: True`` (ONE scoped
    ``NOEXEC`` rule over system binaries with explicit arguments) or (#1186)
    ``sudo: "commands"`` (exact ``/usr/local/sbin/<account>-<name>`` project
    scripts, proven root-owned and account-unwritable by the bootstrap);
  * ``validate_reach`` — each ``reach`` entry is a fleet box name (``[user@]box``,
    never a shared/control account, never a password-shared box when enforced)
    or (#1186) a LAN host ``{"cidr": "<ipv4>/32", "ports": [..], "reason": ".."}``:
    ONE canonical private (RFC1918 / tailscale 100.64.0.0/10) host that is not
    a fleet box address, on explicit distinct ports, with a one-line reason.
    Only single hosts: the venue LAN also carries the password-shared dev
    boxes, which the fleet table knows only by tailscale IP, so a range could
    silently include one. What a declared host's own login opens (a possible
    hop to such a box) is the migration's check, stated in
    ``cli_account_hardening``'s residual.

Plus the small read helpers the renderer and ``accounts status`` share
(``reach_parts`` / ``reach_summary`` / ``lan_label`` / ``lan_rules`` /
``sudo_label``). Split out of ``cli_account_bootstrap`` (#1186) so the
declaration module stays a declaration + renderer. Stdlib only, plus the
pure-render sibling ``cli_account_hardening``; ``cli_fleet`` is imported lazily.
"""
import ipaddress
import re

import cli_account_hardening as hardening

# Shared with cli_account_bootstrap (it aliases these; one definition each).
ONE_LINE_RE = re.compile(r"[^\n\r]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")    # names, `user@` of a box
_LAN_KEYS = frozenset({"cidr", "ports", "reason"})
_LAN_CIDR_RE = re.compile(r"\d{1,3}(\.\d{1,3}){3}/32")
_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC1918
    "100.64.0.0/10"))                                   # tailscale CGNAT
_REASON_RE = re.compile(r"[^\x00-\x1f\x7f]+")           # one line, no controls
# #1199 follow-up: a GitHub Actions secret name (uppercase here; GitHub itself
# is case-insensitive and refuses a GITHUB_ prefix)
SECRET_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


# --------------------------------------------------------------------------- #
# sudo
# --------------------------------------------------------------------------- #
def _validate_sudo_commands(account, reason, cmds):
    """#1186 ``sudo: "commands"``: a one-line reason plus a non-empty list of
    distinct exact ``/usr/local/sbin/<account>-<name>`` script paths."""
    errs = []
    if (not isinstance(reason, str) or not reason.strip()
            or not _REASON_RE.fullmatch(reason)):
        errs.append('sudo: "commands" needs a one-line sudo_reason (no '
                    'control characters — it is a sudoers comment line)')
    if not isinstance(cmds, (list, tuple)) or not cmds:
        return errs + ['sudo: "commands" needs a non-empty sudo_commands list '
                       'of exact script paths']
    for c in cmds:
        problem = (hardening.sudo_script_problem(account, c)
                   if isinstance(c, str) else "not a string")
        if problem:
            errs.append("sudo_commands entry %r is not an exact project script "
                        "path: %s" % (c, problem))
    if len(set(map(repr, cmds))) != len(cmds):
        errs.append("sudo_commands has a duplicate path")
    return errs


def _foreign_script_paths(account, cmds, declared):
    """#1186: a ``/usr/local/sbin/<acct>-<name>`` script belongs to the LONGEST
    declared account name it matches — ``camera`` never grants
    ``camera-box``'s ``/usr/local/sbin/camera-box-*`` script."""
    errs = []
    for path in cmds if isinstance(cmds, (list, tuple)) else ():
        owners = [a for a in declared if isinstance(path, str) and path.startswith(
            "%s/%s-" % (hardening.SUDO_SCRIPT_DIR, a))]
        owner = max(owners, key=len, default=account)
        if owner != account:
            errs.append("sudo_commands entry %r belongs to the declared account "
                        "%r, not %r" % (path, owner, account))
    return errs


def validate_sudo(spec, account, declared=()):
    """Every problem with the declaration's sudo policy ([] = valid).
    ``declared`` are all declared account names (a script path belongs to the
    longest one it matches)."""
    errs = []
    sudo = spec.get("sudo")
    reason = spec.get("sudo_reason")
    cmds = spec.get("sudo_commands")
    if isinstance(sudo, str) and sudo == "commands":
        return (_validate_sudo_commands(account, reason, cmds)
                + _foreign_script_paths(account, cmds, declared))
    if not isinstance(sudo, bool):
        return ['sudo must be True, False or "commands", got %r' % (sudo,)]
    if not sudo:
        if reason is not None or cmds is not None:
            errs.append("sudo_reason/sudo_commands declared on a sudo: False "
                        "account — a grant never hides behind sudo: False")
        return errs
    if not isinstance(reason, str) or not ONE_LINE_RE.fullmatch(reason.strip() or ""):
        errs.append("sudo: True needs a one-line sudo_reason")
    if not isinstance(cmds, (list, tuple)) or not cmds:
        errs.append("sudo: True needs a non-empty scoped sudo_commands list")
    else:
        for c in cmds:
            problem = (hardening.sudo_command_problem(c) if isinstance(c, str)
                       else "not a string")
            if problem:
                errs.append("sudo_commands entry %r is not a scoped command "
                            "(never ALL / a wildcard / a list): %s" % (c, problem))
    return errs


def sudo_label(sudo):
    """``no`` / ``yes`` (scoped binaries) / ``commands`` (#1186 scripts)."""
    return sudo if sudo == "commands" else ("yes" if sudo else "no")


# --------------------------------------------------------------------------- #
# reach
# --------------------------------------------------------------------------- #
def password_shared_boxes():
    """Boxes still running a PASSWORD-authenticated shared account: the legacy
    `newlevel` (fleet-shared password) boxes and the controller (`airuleset`
    break-glass password, #985). An ENFORCED reach to one of them would reopen
    the very escape the account exists to close (#1184 review 2)."""
    import cli_fleet
    boxes = {h["name"].split("@")[-1] for h in cli_fleet.REMOTE_HOSTS
             if h.get("user") == "newlevel"}
    return boxes | {"controller"}


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _lan_network(cidr):
    """The IPv4Network of a canonical ``a.b.c.d/32``, else None."""
    if not isinstance(cidr, str) or not _LAN_CIDR_RE.fullmatch(cidr):
        return None
    try:
        net = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        return None
    return net if str(net) == cidr else None


def _validate_lan_entry(n, entry, boxes):
    """Every problem with the ``n``-th (1-based) reach entry when it is a
    #1186 LAN host ``{"cidr", "ports", "reason"}``. Fail-closed on each field;
    every message starts ``reach entry #n``."""
    errs = []
    head = "reach entry #%d" % n
    if set(entry) != _LAN_KEYS:
        errs.append("%s: keys must be exactly cidr, ports, reason (got %s)"
                    % (head, ", ".join(sorted(map(str, entry)))))
    cidr = entry.get("cidr")
    net = _lan_network(cidr)
    if net is None:
        errs.append("%s: cidr %r is not ONE canonical IPv4 host 'a.b.c.d/32' "
                    "(a range could include a password-shared box's LAN "
                    "address)" % (head, cidr))
    else:
        for box, addr in sorted(boxes.items()):
            if is_ip(addr) and ipaddress.ip_address(addr).version == 4 \
                    and ipaddress.ip_address(addr) in net:
                errs.append("%s: cidr %s is the fleet box %r — declare a fleet "
                            "box by its name" % (head, cidr, box))
        if not any(net.subnet_of(p) for p in _PRIVATE_NETS):
            errs.append("%s: cidr %s is not a private address (RFC1918 or "
                        "tailscale 100.64.0.0/10)" % (head, cidr))
    ports = entry.get("ports")
    if (not isinstance(ports, (list, tuple)) or not ports
            or any(type(p) is not int or not 1 <= p <= 65535 for p in ports)
            or len(set(ports)) != len(ports)):
        errs.append("%s: ports must be a non-empty list of distinct TCP ports "
                    "1-65535, got %r" % (head, ports))
    why = entry.get("reason")
    if not isinstance(why, str) or not why.strip() or not _REASON_RE.fullmatch(why):
        errs.append("%s: reason must be a non-empty one-line text, got %r"
                    % (head, why))
    return errs


def _validate_box_target(target, boxes, enforced, shared_accounts):
    """The problem with ONE ``[user@]box`` reach target, or None."""
    user, _, box = target.rpartition("@")
    if box not in boxes or (user and not TOKEN_RE.fullmatch(user)):
        return ("reach target %r is not a fleet box (known: %s)"
                % (target, ", ".join(sorted(boxes))))
    if user in shared_accounts:
        return ("reach target %r names a shared/control account — a "
                "project account never reaches into it" % (target,))
    if enforced and not is_ip(boxes[box]):
        return ("reach target %r: box address %r is not an IP, so the "
                "egress rule cannot enforce it" % (target, boxes[box]))
    if enforced and box in password_shared_boxes():
        return ("reach target %r: %r still has a password-authenticated "
                "shared account — an ssh route there reopens the escape"
                % (target, box))
    return None


def validate_reach(reach, boxes, enforced, shared_accounts):
    """Every problem with a declaration's ``reach`` ([] = valid). ``boxes`` is
    ``{box: address}`` of the fleet; ``shared_accounts`` are the users a
    ``user@box`` target may never name."""
    errs = []
    if not isinstance(reach, (list, tuple)):
        return ["reach must be a list, got %r" % (reach,)]
    seen = set()
    for n, target in enumerate(reach, 1):
        if isinstance(target, dict):
            errs += _validate_lan_entry(n, target, boxes)
            cidr = target.get("cidr")
            if isinstance(cidr, str) and cidr in seen:
                errs.append("reach entry #%d: duplicate destination %s" % (n, cidr))
            seen.add(cidr if isinstance(cidr, str) else None)
        elif not isinstance(target, str):
            errs.append("reach target %r is neither a fleet box name nor a LAN "
                        "entry {cidr, ports, reason}" % (target,))
        else:
            problem = _validate_box_target(target, boxes, enforced, shared_accounts)
            if problem:
                errs.append(problem)
    return errs


def reach_parts(spec):
    """(fleet box names, LAN entries) of a declaration's ``reach``, in
    declared order — the ONE split the renderer and ``accounts status`` use."""
    names = [t for t in spec["reach"] if isinstance(t, str)]
    lan = [t for t in spec["reach"] if isinstance(t, dict)]
    return names, lan


def reach_summary(spec):
    """``gatekeeper,subdev,17 LAN hosts`` / ``none`` — one-line reach."""
    names, lan = reach_parts(spec)
    return ",".join(names + (["%d LAN hosts" % len(lan)] if lan else [])) or "none"


def lan_label(entry):
    """``10.77.9.61/32 tcp 22,8898 (only 22 enforced)`` — one LAN reach entry,
    human-readable; the suffix is honest that the uid reject covers tcp/22
    only, so any other declared port is declared, not restricted."""
    ports = sorted(entry["ports"])
    note = " (only 22 enforced)" if ports != [22] else ""
    return "%s tcp %s%s" % (entry["cidr"], ",".join(str(p) for p in ports), note)


def lan_rules(lan):
    """(ip, sorted ports, reason) per VALIDATED LAN entry, for the nft render."""
    return [(str(ipaddress.ip_network(e["cidr"]).network_address),
             tuple(sorted(e["ports"])), e["reason"].strip()) for e in lan]


def validate_github_app(spec):
    """#1190: ``github_app`` is a bool, and True needs the ``repo`` its
    controller-minted token is scoped to. #1199: ``repo_secrets`` too."""
    if "github_app" in spec and not isinstance(spec["github_app"], bool):
        return ["github_app must be True or False"]
    if spec.get("github_app") and "repo" not in spec:
        return ["github_app: True needs the repo the token is scoped to"]
    return validate_repo_secrets(spec)


def is_secret_name(name):
    """True iff ``name`` is a settable GitHub Actions secret name."""
    return (isinstance(name, str) and bool(SECRET_NAME_RE.fullmatch(name))
            and not name.startswith("GITHUB_"))


def validate_repo_secrets(spec):
    """#1199 follow-up: ``repo_secrets`` is the allow-list of CI secret names
    the controller may set on the account's declared repo from a
    ``secret-sync:<NAME>`` request (``cli_project_ci_sync``). It needs
    ``github_app: True`` (the account's repo is the request queue)."""
    if "repo_secrets" not in spec:
        return []
    names = spec["repo_secrets"]
    if not isinstance(names, (list, tuple)):
        return ["repo_secrets must be a list of secret names"]
    errs = []
    if spec.get("github_app") is not True:
        errs.append("repo_secrets needs github_app: True (the declared repo is "
                    "where the secret-sync requests are filed)")
    errs += ["repo_secrets entry %r is not a GitHub secret name "
             "([A-Z_][A-Z0-9_]*, never GITHUB_*)" % (n,)
             for n in names if not is_secret_name(n)]
    named = [n for n in names if isinstance(n, str)]
    if len(set(named)) != len(named):
        errs.append("repo_secrets has a duplicate name")
    return errs
