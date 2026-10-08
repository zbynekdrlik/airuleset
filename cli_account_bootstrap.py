"""Project-account declaration + root bootstrap renderer (#960, #1184).

#1184 (owner ROZHODNUTÉ 2026-09-29): every project and its Claude session run in
a DEDICATED unix account named after the project, never under the shared
``newlevel`` account (weak shared password, NOPASSWD sudo, every fleet key in
its home). ``SERVICE_ACCOUNTS`` below is THE declaration of those project
accounts: for each one, its host, whether it has sudo (default NO), exactly
where it may reach (default nowhere), which named credentials it receives
(default none) and which humans get a webterm tab to it. ``validate_account``
refuses anything the declaration does not state explicitly. sudo and reach
are ENFORCED by the bootstrap (``cli_account_hardening``); ``secrets`` is a
declarative inventory (nothing here provisions a credential, so none arrives
undeclared through this path).

#1186 widens the declaration for a project with LAN hardware (camera-box)
without widening what it may do implicitly. A ``reach`` entry is a fleet box
name OR ``{"cidr": "<ipv4>/32", "ports": [..], "reason": ".."}``: one private
(RFC1918 / tailscale 100.64.0.0/10) host that is not a fleet box, with its
reason. ``sudo`` may also be ``"commands"``: ``sudo_commands`` then lists
exact ``/usr/local/sbin/<account>-<name>`` scripts, and the bootstrap proves
each one root-owned and account-unwritable before the rule lands.

``render_root_bootstrap`` renders an IDEMPOTENT bash script that root runs ONCE
on the declared host. It creates the unix user, sets permissions, and installs
authorized_keys: the fleet push keys, the owner keys, and one forced-command
line per declared webterm human (``restrict,pty,command=`` attaching the
project's tmux session, nothing else). It enables loginctl linger. It installs
the privilege boundary (``cli_account_hardening``): the declared sudo policy
(none, failing LOUD on any route, or ONE scoped visudo-checked rule), the
declared reach as a uid-keyed nftables ssh egress rule, and a su/polkit
lockout — so the uid cannot become ``newlevel`` via its shared password.
Only then does it clone the project repo and create the project tmux session.

Module-level imports are stdlib plus the pure-render sibling
``cli_account_hardening`` (the L-E leaf convention); fleet/webterm constants
are imported LAZILY inside functions so the module loads with no side effects.
"""

import copy
import re
import shlex
import sys
import textwrap

import cli_account_hardening as hardening
import cli_account_policy as policy
import cli_project_toolchain as toolchain


# Debian package name grammar (Policy §5.6.1): [a-z0-9][a-z0-9.+\-]+
# with minimum length 2.  We validate at render time so a stray
# space/quote/metachar fails LOUD instead of silently breaking the script.
_PACKAGE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9.+\-]+")

# #1184 declaration grammar — every value the renderer interpolates into a root
# script is validated against one of these first, ALWAYS with `fullmatch` (a
# `$` anchor would accept a trailing newline and split an authorized_keys or
# sudoers line).
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{1,31}")
_TOKEN_RE = policy.TOKEN_RE
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")   # tmux rewrites '.'
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REL_DIR_RE = re.compile(r"(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_./-]+")
# (a scoped sudo command is checked by cli_account_hardening.sudo_command_problem)
_ONE_LINE_RE = policy.ONE_LINE_RE
# (sudo + reach entries — incl. the #1186 LAN hosts — are cli_account_policy's)
_is_ip = policy.is_ip

# Accounts that are NEVER a project account: the shared legacy account, root,
# and the control/maintainer accounts.
_NEVER_PROJECT_ACCOUNTS = frozenset({"newlevel", "root", "airuleset",
                                     "gatekeeper"})

_ALLOWED_KEYS = frozenset({
    "host", "sudo", "sudo_reason", "sudo_commands", "reach", "reach_enforced",
    "reach_reason", "secrets", "webterm_sessions", "system_packages", "repo",
    "project_dir", "tmux_session", "github_app", "repo_secrets", "tools",
})

# The defaults every declaration inherits. They are the SAFE direction: no
# sudo, no reach, no secrets. `host` defaults to the controller, where the
# pre-#1184 renderer ran.
_DEFAULTS = {
    "host": "controller",
    "sudo": False,
    "reach": (),
    "reach_enforced": True,
    "secrets": (),
    "webterm_sessions": {},
}


# ---------------------------------------------------------------------------
# THE project-account declaration (#960 claudy, #1184 policy)
# ---------------------------------------------------------------------------

# Each entry declares ONE project account. `webterm_sessions` maps each human
# whose webterm key gets a forced-command line in authorized_keys to the tmux
# session (`preferred`) and start dir chain that forced command targets.
SERVICE_ACCOUNTS = {
    "claudy": {
        "host": "controller",
        "sudo": False,
        # claudy is the fleet Claude-credential manager (#960): its service
        # ssh-probes / refreshes / switches EVERY fleet account (claudy repo
        # ssh_serialize.py, resync.py, vault.py). An egress rule would cut the
        # fleet token refresh, so its reach is DECLARED but not enforced —
        # narrowing it is a follow-up, never a silent cut.
        "reach": ["controller", "dev1", "dev2", "gatekeeper", "subdev",
                  "spinbike-vps", "forestshop-dev"],
        "reach_enforced": False,
        "reach_reason": "fleet credential manager: ssh to every fleet account "
                        "is its job (#960)",
        "secrets": ["claudy-vault"],   # the credential vault it manages (vault.py)
        # #1207: the repo venv's playwright 1.62.0 (chromium 1234), in /opt
        "tools": ["playwright:1.62.0/chromium"],
        "project_dir": "devel/claudy",
        "webterm_sessions": {
            # human → {preferred, start_dir_chain} — the forced command
            # opens in the project dir, not the default STREAM_DEV_CWD_CHAIN.
            "zbynek": {"preferred": "zbynek", "start_dir_chain": ["devel/claudy"]},
            "marek": {"preferred": "marek", "start_dir_chain": ["devel/claudy"]},
        },
        # Playwright chromium headless shell deps for the claudy-controller
        # runner (#973).  Without these the E2E test job cannot launch
        # chromium and claudy#239 is blocked.
        "system_packages": [
            "libnss3", "libnspr4", "libatk1.0-0t64", "libatk-bridge2.0-0t64",
            "libatspi2.0-0t64", "libgbm1", "libasound2t64", "libxcomposite1",
            "libxdamage1", "libxext6", "libxfixes3", "libxrandr2",
            # Playwright E2E glyph metrics — without these, DejaVu Sans
            # replaces Liberation Sans and wider glyphs overflow tables
            # (#973 reopen).
            "fonts-liberation", "fontconfig",
        ],
    },
    # #1183: the first project account built on the #1184 policy. The owner,
    # Marek and Timo each get a tab to the SAME account and share ONE project
    # tmux session (owner ROZHODNUTÉ 2026-09-29: the account is per PROJECT and
    # people share the project's session — never a per-person account).
    "fohmixer": {
        "host": "dev1",
        "sudo": False,
        "reach": [],
        "secrets": ["fohmixer-engineer-pin", "cloudflare-fohmixer-analytics",   # #1206
                    "cloudflare-fohmixer-access-audit", "cloudflared-fohmixer-token",
                    "cloudflare-fohmixer-acme-dns", "cloudflare-fohmixer-setup"],
        "repo": "zbynekdrlik/fohmixer",
        # #1190: a 1-hour token scoped to this repo, minted on the controller
        # by the newlevel-project-accounts App (cli_project_gh_token).
        "github_app": True,
        "repo_secrets": ["DENYLIST"],   # CI secrets it may sync (#1199)
        # #1201: its repo's pins (rust-toolchain.toml, CI ruff, the e2e lockfile)
        "tools": ["rust-toolchain:1.98.1", "rust-component:llvm-tools-preview",
                  "rust-target:wasm32-unknown-unknown", "pipx:ruff==0.16.2",
                  "playwright:1.58.2/chromium", "playwright:1.58.2/webkit"],
        "project_dir": "devel/fohmixer",
        "tmux_session": "fohmixer",
        "webterm_sessions": {
            human: {"preferred": "fohmixer", "start_dir_chain": ["devel/fohmixer"]}
            for human in ("zbynek", "marek", "timo")
        },
    },
    # #1186: the first project with LAN hardware (the stream rig). Its reach is
    # the rig inventory read from the camera-box repo itself (obs-fleet.sh,
    # dantesync-fleet.sh, the netcfg facet) — every host ONE private /32 with
    # its reason; ssh (22) is what the reject enforces, OBS WebSocket (4455),
    # dantesync (8898) and the bundle-state JSON (8899) are declared with it.
    # resolume is NOT declared: its only address is a drifting DHCP lease
    # (resolume.lan, today 10.77.9.201, which obs-fleet.sh records colliding
    # with `bridge`) — an allowlist entry needs a stable address (a DHCP
    # reservation) first. Its sudo is exactly two root-owned dev1 installer
    # scripts the live migration installs (main's design, #1186
    # issuecomment-5894645187: a narrow rule, never an admin account); webterm
    # is the owner.
    "camera-box": {
        "host": "dev1",
        "sudo": "commands",
        "sudo_reason": "dev1-side root installs only: the dev1 dantesync "
                       "self-upgrade and the dev1 remote-log receiver install "
                       "(#1186)",
        "sudo_commands": [
            "/usr/local/sbin/camera-box-dev1-dantesync-upgrade",
            "/usr/local/sbin/camera-box-dev1-remote-log-install",
        ],
        "reach": [
            *({"cidr": "10.77.9.%d/32" % (60 + n), "ports": [22, 8898],
               "reason": "cam%d (camera box)" % n} for n in range(1, 8)),
            {"cidr": "10.77.9.202/32", "ports": [22, 4455, 8898, 8899],
             "reason": "strih-lx, production strih + fleet NTP master (obs-fleet.sh)"},
            {"cidr": "10.77.9.204/32", "ports": [22, 4455, 8898, 8899],
             "reason": "stream OBS box, Windows (obs-fleet.sh)"},
            {"cidr": "10.77.7.232/32", "ports": [22, 8898],
             "reason": "mbc, Master Broadcast Console, audio VLAN (dantesync-fleet.sh)"},
            {"cidr": "10.77.7.30/32", "ports": [22, 8898],
             "reason": "fohabl, FOH Ableton PC, audio VLAN (dantesync-fleet.sh)"},
            {"cidr": "10.77.8.1/32", "ports": [22],
             "reason": "MikroTik RB4011 router_snv, read-only admin@ netcfg audit"},
            *({"cidr": "10.77.9.%d/32" % n, "ports": [22],
               "reason": "MikroTik CRS310 switch, read-only admin@ netcfg audit"}
              for n in range(2, 6)),
        ],
        "secrets": ["av-soak.env", "dantesync-fleet.env", "netcfg-drift.env",
                    "obs-ws-pass", "obs-burn-reconcile-watchdog.conf",
                    "fleet-ssh-password", "gh-token"],
        "repo": "zbynekdrlik/camera-box",
        "project_dir": "devel/camera-box",
        "tmux_session": "camera-box",
        "webterm_sessions": {
            "zbynek": {"preferred": "camera-box",
                       "start_dir_chain": ["devel/camera-box"]},
        },
    },
    # #1211 (migrate-on-touch): the MikroTik/VPN tooling. Its runtime timer runs on
    # companion-snv, outside the fleet. github_app comes with the live migration.
    "nlvpn": {
        "host": "dev1",
        "sudo": False,
        "reach": [
            {"cidr": "10.77.8.1/32", "ports": [22],
             "reason": "MikroTik router_snv (tailscale subnet route)"},
            {"cidr": "100.122.204.47/32", "ports": [22],
             "reason": "companion-snv, the nlvpn runtime host (git pull deploy)"},
        ],
        "secrets": ["nlvpn-state"],   # gitignored state/: HCLOUD_TOKEN, MikroTik, WG, VPS key
        "repo": "zbynekdrlik/nlvpn",
        "project_dir": "devel/nlvpn",
        "tmux_session": "nlvpn",
        "webterm_sessions": {
            "zbynek": {"preferred": "nlvpn", "start_dir_chain": ["devel/nlvpn"]},
        },
    },
    # #1212 (migrate-on-touch): NLMEDIA Resolume Arena box tooling on dev2. HTTP to
    # remoteos-mcp :8092 only (tcp/22 is what the uid reject enforces).
    "resolume": {
        "host": "dev2",
        "sudo": False,
        "reach": [
            {"cidr": "10.77.9.201/32", "ports": [8092],
             "reason": "resolume-snv remoteos-mcp (via companion-snv)"},
            {"cidr": "10.77.9.212/32", "ports": [8092],
             "reason": "songs-snv remoteos-mcp (via companion-snv)"},
            {"cidr": "10.76.8.201/32", "ports": [8092],
             "reason": "resolume-pp remoteos-mcp (via companion-pp)"},
        ],
        "secrets": ["resolume-mcp-json", "winremote-songs-snv", "WIN_RESOLUME_PP_KEY"],
        "repo": "zbynekdrlik/resolume",
        "project_dir": "devel/newlevelmedia/resolume",
        "tmux_session": "resolume",
        "webterm_sessions": {
            "zbynek": {"preferred": "resolume",
                       "start_dir_chain": ["devel/newlevelmedia/resolume"]},
        },
    },
    # #1220 (owner 8.10.: its own account, `z1`): Varos East accounting. Bank
    # data never in `newlevel`; outbound HTTPS only (Fio API, mail). The repo is
    # PRIVATE and CODE ONLY; documents are gitignored. github_app comes after
    # the owner adds the repo to the App installation.
    "varos": {
        "host": "dev2",
        "sudo": False,
        "reach": [],
        "secrets": ["fio-token-varos"],   # Fio API token, read-only account mode
        "repo": "zbynekdrlik/varos",
        "project_dir": "devel/varos/uctovnictvo",
        "tmux_session": "varos",
        "webterm_sessions": {
            "zbynek": {"preferred": "varos",
                       "start_dir_chain": ["devel/varos/uctovnictvo"]},
        },
    },
}


def _fleet_account_conflict(account, host):
    """Why ``account`` may not be declared as a NEW project account, or None:
    a stream / webterm-only / reduced-authority account, or a REMOTE_HOSTS
    account on another box, already belongs to someone — rendering a bootstrap
    for it would replace that account's authorized_keys."""
    import cli_fleet
    if account in cli_fleet.AUTHORITY_BY_USER or account in cli_fleet.WEBTERM_ONLY_USERS:
        return "%r is an existing stream/webterm account" % account
    for h in cli_fleet.REMOTE_HOSTS:
        if h.get("user") == account and h["name"].split("@")[-1] != host:
            return "%r is an existing fleet account on %r" % (
                account, h["name"].split("@")[-1])
    return None


def account_spec(account):
    """The declaration of ``account`` with every default filled in. Raises
    ValueError for an unknown account."""
    if account not in SERVICE_ACCOUNTS:
        raise ValueError("unknown project account: %r" % account)
    spec = copy.deepcopy(_DEFAULTS)
    spec.update(copy.deepcopy(SERVICE_ACCOUNTS[account]))
    return spec


def fleet_boxes():
    """{box: address} for every box the fleet knows — the namespace a
    declaration's ``host`` and ``reach`` must live in. Derived from the ONE
    fleet source (``cli_fleet.REMOTE_HOSTS``): a ``<user>@<box>`` entry names
    ``<box>``, a bare entry name IS the box (dev1, dev2, gatekeeper, ...)."""
    import cli_fleet
    boxes = {}
    for h in cli_fleet.REMOTE_HOSTS:
        box = h["name"].split("@")[-1]
        boxes.setdefault(box, h["host"])
    return boxes


def _webterm_humans():
    from cli_webterm_profiles import LANE_HOST
    return frozenset(LANE_HOST)


def _validate_webterm(sessions):
    errs = []
    if not isinstance(sessions, dict):
        return ["webterm_sessions must be a dict"]
    humans = _webterm_humans()
    for human, sess in sessions.items():
        if human not in humans:
            errs.append("webterm human %r is not a webterm lane (known: %s)"
                        % (human, ", ".join(sorted(humans))))
            continue
        if not isinstance(sess, dict) or not _SESSION_RE.fullmatch(
                str(sess.get("preferred") or "")):
            errs.append("webterm_sessions[%r] needs a plain `preferred` "
                        "session name" % human)
            continue
        for rel in sess.get("start_dir_chain") or []:
            if not isinstance(rel, str) or not _REL_DIR_RE.fullmatch(rel):
                errs.append("webterm_sessions[%r] start dir %r is not a "
                            "HOME-relative path" % (human, rel))
    return errs


def validate_account(account, raw):
    """Every problem with one declaration, as a list of strings ([] = valid).
    Fail-closed on every axis: an unknown host, sudo without a reason or
    without scoped commands, a reach target outside the fleet, a malformed
    secret name, an unknown webterm human or an unknown key is an ERROR —
    nothing the declaration does not state explicitly is ever rendered."""
    errs = []
    if not _ACCOUNT_RE.fullmatch(account or "") or account in _NEVER_PROJECT_ACCOUNTS:
        errs.append("%r is not a valid project account name (shared/control "
                    "accounts are never a project account)" % (account,))
    if not isinstance(raw, dict):
        return errs + ["declaration must be a dict"]
    for key in sorted(set(raw) - _ALLOWED_KEYS):
        errs.append("unknown declaration key %r" % key)
    spec = copy.deepcopy(_DEFAULTS)
    spec.update(raw)
    conflict = _fleet_account_conflict(account, spec["host"])
    if conflict:
        errs.append(conflict)
    boxes = fleet_boxes()
    if spec["host"] not in boxes:
        errs.append("host %r is not a fleet box (known: %s)"
                    % (spec["host"], ", ".join(sorted(boxes))))
    errs += policy.validate_sudo(spec, account, declared=SERVICE_ACCOUNTS)
    enforced = spec.get("reach_enforced")
    if not isinstance(enforced, bool):
        errs.append("reach_enforced must be True or False")
        enforced = True
    elif not enforced and not _ONE_LINE_RE.fullmatch(str(spec.get("reach_reason") or "")):
        errs.append("reach_enforced: False needs a one-line reach_reason")
    elif enforced and "reach_reason" in spec:
        errs.append("reach_reason declared on an enforced reach")
    errs += policy.validate_reach(spec["reach"], boxes, enforced,
                                  _NEVER_PROJECT_ACCOUNTS)
    secrets = spec["secrets"]
    if not isinstance(secrets, (list, tuple)):
        errs.append("secrets must be a list")
    else:
        errs += ["secrets entry %r is not a plain credential name" % (s,)
                 for s in secrets
                 if not isinstance(s, str) or not _TOKEN_RE.fullmatch(s)]
    errs += _validate_webterm(spec["webterm_sessions"])
    if "repo" in spec and not _REPO_RE.fullmatch(str(spec["repo"])):
        errs.append("repo %r is not owner/name" % (spec["repo"],))
    if "project_dir" in spec and not _REL_DIR_RE.fullmatch(str(spec["project_dir"])):
        errs.append("project_dir %r is not a HOME-relative path"
                    % (spec["project_dir"],))
    if "repo" in spec and "project_dir" not in spec:
        errs.append("repo declared without project_dir")
    errs += policy.validate_github_app(spec, account, SERVICE_ACCOUNTS)
    errs += toolchain.validate_tools(spec, account, SERVICE_ACCOUNTS)   # #1201
    if "tmux_session" in spec and not _SESSION_RE.fullmatch(str(spec["tmux_session"])):
        errs.append("tmux_session %r is not a plain name" % (spec["tmux_session"],))
    elif "tmux_session" in spec and isinstance(spec["webterm_sessions"], dict):
        # a declared project session is THE session every tab attaches
        for human, sess in spec["webterm_sessions"].items():
            if isinstance(sess, dict) and sess.get("preferred") != spec["tmux_session"]:
                errs.append("webterm_sessions[%r] preferred %r is not the declared "
                            "tmux_session %r" % (human, sess.get("preferred"),
                                                 spec["tmux_session"]))
    return errs


def validate_all():
    """{account: [errors]} for every INVALID declaration ({} = all clean)."""
    out = {}
    for account, raw in SERVICE_ACCOUNTS.items():
        errs = validate_account(account, raw)
        if errs:
            out[account] = errs
    return out


def _checked_spec(account):
    """The filled-in declaration, or ValueError naming every problem."""
    spec = account_spec(account)
    errs = validate_account(account, SERVICE_ACCOUNTS[account])
    if errs:
        raise ValueError("invalid declaration for %r: %s"
                         % (account, "; ".join(errs)))
    return spec


def _forced_command_key_line(preferred, pubkey, start_dir_chain=None, account=None):
    """Build a ``restrict,pty,command="..."`` authorized_keys line that
    attaches the named tmux session.  Delegates to the SINGLE source
    ``_controller_lane_key_line`` in ``cli_webterm_only.py`` — Y1 Fable
    review: two copies of authorized_keys escaping = drift hazard.

    #960+#961: ``start_dir_chain`` threads to the forced command so the
    tab opens in the project dir (``devel/claudy``); #1202: ``account`` keys windows."""
    from cli_webterm_only import _controller_lane_key_line
    return _controller_lane_key_line(preferred, pubkey,
                                     start_dir_chain=start_dir_chain, account=account)


def desired_keys_for_service_account(account):
    """Return the list of authorized_keys lines for a project account.
    Raises ValueError for an unknown/invalid account, or when a declared
    webterm human has no controller lane key yet (#1184: a declared tab is
    never dropped silently — mint the key first)."""
    spec = _checked_spec(account)

    from cli_webterm_only import FLEET_PUSH_PUBKEYS, WEBTERM_CONTROLLER_LANE_PUBKEYS
    from cli_owner_keys import OWNER_PUBKEYS

    keys = list(FLEET_PUSH_PUBKEYS)
    keys.extend(OWNER_PUBKEYS)

    # Per-human webterm forced-command keys
    for human in sorted(spec["webterm_sessions"]):
        sess = spec["webterm_sessions"][human]
        pubkey = WEBTERM_CONTROLLER_LANE_PUBKEYS.get(human)
        if not pubkey:
            raise ValueError(
                "declared webterm human %r of account %r has no controller "
                "lane pubkey — mint ~/.secrets/webterm_%s_ed25519 on the "
                "controller and add it to WEBTERM_CONTROLLER_LANE_PUBKEYS "
                "before rendering" % (human, account, human))
        keys.append(_forced_command_key_line(
            sess["preferred"], pubkey, start_dir_chain=sess.get("start_dir_chain"), account=account))

    return keys


def render_authorized_keys(account):
    """Render the full authorized_keys content for a project account."""
    header = (
        "# airuleset:managed — project account %s (#960/#1184); "
        "re-run bootstrap to refresh\n" % account
    )
    lines = desired_keys_for_service_account(account)
    return header + "".join(line.rstrip("\n") + "\n" for line in lines)


def _validate_package_names(packages):
    """Validate that every name in *packages* matches Debian policy §5.6.1.

    Raises ValueError on the first invalid name.  Called at render time so
    a typo / metachar fails LOUD instead of silently producing a broken
    script (#973 Fable review BLUE)."""
    for name in packages:
        if not _PACKAGE_NAME_RE.fullmatch(name):
            raise ValueError(
                "invalid Debian package name in system_packages: %r" % name)


def _render_system_packages_step(packages):
    """Render the idempotent apt-get step for system_packages.

    Returns a bash snippet that checks each package with dpkg-query and
    installs only missing ones.  Returns '' when *packages* is empty/None
    so the caller can unconditionally concatenate it (#973)."""
    if not packages:
        return ""
    _validate_package_names(packages)
    pkg_list = " ".join(packages)
    # The loop collects names of packages not yet installed, then runs ONE
    # apt-get call (cheaper and cleaner than N individual installs).
    return textwrap.dedent("""\

        # 8. System packages (idempotent) — #973
        NEED_INSTALL=()
        for pkg in {pkg_list}; do
            if dpkg-query -W -f='${{Status}}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
                :  # already present
            else
                NEED_INSTALL+=("$pkg")
            fi
        done
        if [ ${{#NEED_INSTALL[@]}} -gt 0 ]; then
            echo "  installing ${{#NEED_INSTALL[@]}} missing packages: ${{NEED_INSTALL[*]}}"
            DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o DPkg::Lock::Timeout=60 "${{NEED_INSTALL[@]}}"
        else
            echo "  all {n_pkgs} system packages already installed — nothing to do"
        fi
    """).format(pkg_list=pkg_list, n_pkgs=len(packages))


def _render_system_packages_readback(packages):
    """Render the read-back lines for system packages.

    Returns '' when *packages* is empty/None (#973)."""
    if not packages:
        return ""
    pkg_list = " ".join(packages)
    return textwrap.dedent("""\
        echo "  system packages:"
        for pkg in {pkg_list}; do
            if dpkg-query -W -f='${{Status}}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
                echo "    $pkg: installed"
            else
                echo "    $pkg: MISSING"
            fi
        done
    """).format(pkg_list=pkg_list)


def _render_project_step(spec):
    """#1184 steps 10-11: clone the project repo (idempotent — an existing
    checkout is left alone) and create the project tmux session, both AS the
    account. Returns '' for a declaration with neither."""
    out = ""
    if spec.get("repo"):
        url = "https://github.com/%s.git" % spec["repo"]
        dest = "$HOME/" + spec["project_dir"]
        # #1220: a private repo cannot be cloned before the controller delivered
        # the App token, so a failed clone DEFERS (dir created, loud) instead
        # of aborting the whole render; the re-run after the mint clones.
        clone = ("if [ -d %s/.git ]; then echo \"  checkout exists — skipping "
                 "clone\"; elif GIT_TERMINAL_PROMPT=0 git clone %s %s; then :; "
                 "else mkdir -p %s; echo \"  clone deferred: %s needs the "
                 "account's App token; re-run this render after: airuleset.py "
                 "project-gh-token mint $(id -un)\"; fi"
                 % (dest, shlex.quote(url), dest, dest, spec["repo"]))
        out += ("\n# 9. Project checkout (idempotent, as the account) — #1184\n"
                "runuser -l \"$ACCOUNT\" -c %s\n" % shlex.quote(clone))
    if spec.get("tmux_session"):
        sess = spec["tmux_session"]
        start = "$HOME/" + spec["project_dir"] if spec.get("project_dir") else "$HOME"
        tmux = ("tmux has-session -t =%s 2>/dev/null || tmux new-session -d -s %s -c "
                "%s %s" % (sess, sess, start, toolchain.TMUX_LOGIN_SHELL))  # #1201
        out += ("\n# 10. Project tmux session (idempotent, as the account) — #1184\n"
                "runuser -l \"$ACCOUNT\" -c %s\n" % shlex.quote(tmux))
    return out


# Steps 4-7 (rendered AFTER the 3a-3d boundary): ssh dir, authorized_keys (an
# atomic write), and the .claude / devel dirs.
_KEYS_TEMPLATE = """\
        # 4. SSH directory
        install -d -m 0700 -o "$ACCOUNT" -g "$ACCOUNT" "/home/$ACCOUNT/.ssh"

        # 5. authorized_keys (atomic write)
        AK_TEMP=$(mktemp "/home/$ACCOUNT/.ssh/authorized_keys.XXXXXX")
        cat > "$AK_TEMP" << 'AUTHORIZED_KEYS_EOF'
        {ak_content}AUTHORIZED_KEYS_EOF
        chmod 0600 "$AK_TEMP"
        chown "$ACCOUNT":"$ACCOUNT" "$AK_TEMP"
        mv "$AK_TEMP" "/home/$ACCOUNT/.ssh/authorized_keys"
        echo "  authorized_keys: $(wc -l < /home/$ACCOUNT/.ssh/authorized_keys) lines"

        # 6. Create .claude directory for airuleset markers
        install -d -m 0755 -o "$ACCOUNT" -g "$ACCOUNT" "/home/$ACCOUNT/.claude"

        # 7. Create devel directory for the repo clone
        install -d -m 0755 -o "$ACCOUNT" -g "$ACCOUNT" "/home/$ACCOUNT/devel"
"""


def render_root_bootstrap(account):
    """Render an idempotent bash script for root that creates the project
    account on its DECLARED host.  Returns the script as a string."""
    spec = _checked_spec(account)
    host = spec["host"]
    address = fleet_boxes()[host]
    names, lan = policy.reach_parts(spec)

    ak_content = render_authorized_keys(account)
    packages = spec.get("system_packages", [])

    # Shell-escape the authorized_keys content for a heredoc
    # Using a quoted heredoc delimiter so no shell expansion happens
    script = textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail

        # airuleset root bootstrap for project account '{account}' (#960/#1184)
        # Generated by: python3 airuleset.py account-bootstrap --render {account}
        # Run as root on {host}. Idempotent.
        # Declared: sudo={sudo} reach={reach} secrets={secrets}

        ACCOUNT='{account}'

        echo "=== airuleset root bootstrap: $ACCOUNT on {host} ==="

        # 1. Create user (idempotent)
        CREATED=0
        if id "$ACCOUNT" &>/dev/null; then
            echo "  user $ACCOUNT already exists — skipping useradd"
        else
            if getent group "$ACCOUNT" >/dev/null; then
                useradd -m -s /bin/bash -g "$ACCOUNT" "$ACCOUNT"
            else
                useradd -m -s /bin/bash -U "$ACCOUNT"
            fi
            CREATED=1
            echo "  created user $ACCOUNT"
        fi

        {marker}

        # 2. Home permissions
        chmod 0750 "/home/$ACCOUNT"
        echo "  home permissions: $(stat -c '%a' /home/$ACCOUNT)"

        # 3. Enable loginctl linger (reboot-durable user services)
        loginctl enable-linger "$ACCOUNT"
        echo "  linger: $(loginctl show-user "$ACCOUNT" -p Linger --value 2>/dev/null || echo 'enabled')"

    """).format(account=account, host=host,
                marker=hardening.render_account_marker_step().strip("\n"),
                sudo=policy.sudo_label(spec["sudo"]),
                reach=policy.reach_summary(spec),
                secrets=",".join(spec["secrets"]) or "none")

    # 3a-3d: the privilege boundary (#1184, cli_account_hardening) lands
    # BEFORE step 5 installs any key, so a failed step never leaves a
    # loginable, unbounded account.
    script += hardening.render_sudo_step(account, spec)
    boxes = fleet_boxes()
    reach_ips = sorted({boxes[t.rpartition("@")[2]] for t in names})
    script += hardening.render_reach_step(account, reach_ips,
                                          enforced=spec["reach_enforced"],
                                          reason=spec.get("reach_reason", ""),
                                          lan_rules=policy.lan_rules(lan))
    script += hardening.render_lockout_step()
    script += hardening.render_isolation_check_step()
    # 4-7: keys + dirs (4 space-indented template → dedent)
    script += "\n" + textwrap.dedent(_KEYS_TEMPLATE).format(ak_content=ak_content)
    # 8. System packages — only when the account declares them (#973)
    script += _render_system_packages_step(packages)
    script += toolchain.render_account_env_step(spec)   # 8b: #1201
    # 11 BEFORE 9-10 (#1220): the gh token shim is the git credential helper
    # the checkout of a private repo needs (#1190)
    import cli_project_gh_token
    script += cli_project_gh_token.render_bootstrap_step(spec)
    # 9-10: the project checkout + its tmux session (#1184)
    script += _render_project_step(spec)
    script += toolchain.render_system_step(spec)   # 12 (LAST): #1201

    # Read-back section
    readback = textwrap.dedent("""\

        echo ""
        echo "=== Read-back ==="
        echo "  user: $(id $ACCOUNT)"
        echo "  home: $(ls -ld /home/$ACCOUNT)"
        echo "  linger: $(loginctl show-user $ACCOUNT -p Linger --value 2>/dev/null || echo 'check manually')"
        echo "  ssh dir: $(ls -ld /home/$ACCOUNT/.ssh)"
        echo "  authorized_keys: $(wc -l < /home/$ACCOUNT/.ssh/authorized_keys) lines"
        echo "  key fingerprints:"
        ssh-keygen -lf "/home/$ACCOUNT/.ssh/authorized_keys" 2>/dev/null || echo "    (ssh-keygen failed)"
    """)
    readback += _render_system_packages_readback(packages)
    readback += textwrap.dedent("""\
        echo ""
        echo "=== Next steps (as $ACCOUNT) ==="
        echo "  1. git clone https://github.com/zbynekdrlik/airuleset.git ~/devel/airuleset"
        echo "  2. python3 ~/devel/airuleset/airuleset.py install"
        echo "  3. Verify: ssh -i ~/.secrets/airuleset_push_ed25519 $ACCOUNT@{address} true"
    """).format(address=address) + cli_project_gh_token.render_next_steps(spec)
    script += readback + toolchain.render_next_steps(spec)   # #1201: verify is LAST
    return script


def cmd_account_bootstrap(args):
    """CLI entry point: ``airuleset.py account-bootstrap --render <account>``."""
    # Support both argparse Namespace (from main() argparse) and raw list
    # (from legacy callers).
    if hasattr(args, "render"):
        account = args.render
    elif isinstance(args, (list, tuple)):
        if len(args) < 2 or args[0] != "--render":
            print("Usage: airuleset.py account-bootstrap --render <account>",
                  file=sys.stderr)
            print("Known accounts: %s" % ", ".join(sorted(SERVICE_ACCOUNTS)),
                  file=sys.stderr)
            return 1
        account = args[1]
    else:
        print("Usage: airuleset.py account-bootstrap --render <account>",
              file=sys.stderr)
        return 1
    if not account:
        print("Usage: airuleset.py account-bootstrap --render <account>",
              file=sys.stderr)
        print("Known accounts: %s" % ", ".join(sorted(SERVICE_ACCOUNTS)),
              file=sys.stderr)
        return 1
    try:
        script = render_root_bootstrap(account)
    except ValueError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    print(script)
    return 0
