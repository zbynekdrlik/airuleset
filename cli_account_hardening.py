"""Privilege-boundary steps of the project-account root bootstrap (#1184).

A separate uid is only a boundary when nothing lets it BECOME another uid or
reach another box with a credential it was never declared. On a host that
still runs the legacy shared ``newlevel`` account (weak shared password,
NOPASSWD sudo, every fleet key), a shell in a project account could otherwise
``su - newlevel`` + ``sudo -i``, ``pkexec`` with newlevel's password, or
``ssh newlevel@<any box>`` with the shared password (#1184 review 1 🔴). These
renderers close THOSE paths for the declared account ONLY — nothing they write
changes what any OTHER account may do — and they all run BEFORE the account's
authorized_keys land, so a failed step never leaves a loginable, unbounded
account (#1184 review 2):

  * ``render_account_marker_step`` — root-only ownership marker, so a
    re-render can never rewrite a FOREIGN pre-existing account;
  * ``render_sudo_step`` — the declared sudo policy: none (fail LOUD on any
    route, incl. a root-equivalent group) or ONE scoped, ``NOEXEC``,
    ``visudo``-checked ``/etc/sudoers.d/<acct>`` rule over root-owned binaries;
  * ``render_reach_step`` — the declared ``reach`` as an nftables egress rule
    keyed on the account's uid: new outbound ssh (tcp/22, loopback included)
    is rejected except to the declared boxes; applied now, enabled, and
    re-applied whenever nftables.service restarts or reloads;
  * ``render_lockout_step`` — the account joins ``airuleset-project``, which
    ``pam_wheel`` (deny) and a polkit rule bar from ``su`` and polkit admin;
  * ``render_isolation_check_step`` — other accounts' homes must not be
    world-readable/traversable (fail LOUD); a ``/proc`` without ``hidepid``
    is reported.

What this does NOT close (honest residual, a follow-up in the lane return):
non-ssh traffic from the uid (https for the repo and the Claude API) and any
password-authenticated NON-22 service on loopback or the tailnet.

Pure string renderers (stdlib only). ``cli_account_bootstrap`` validates every
interpolated value before calling them.
"""
import re
import textwrap

# The group every project account joins; PAM (su) and polkit deny its members.
PROJECT_GROUP = "airuleset-project"
MARKER_DIR = "/etc/airuleset/project-accounts"
BACKUP_DIR = "/etc/airuleset/backup"
# Groups that are root-equivalent or expose other accounts' secrets.
PRIVILEGED_GROUPS = ("sudo", "admin", "wheel", "root", "docker", "lxd", "disk",
                     "shadow", "adm")
PAM_SU_LINE = "auth required pam_wheel.so deny use_uid group=%s" % PROJECT_GROUP

# A scoped sudo command runs a binary from the system bin dirs ONLY (never an
# account-writable path, never `..`), with explicit plain arguments.
SUDO_CMD_RE = re.compile(r"(/usr)?/s?bin/[A-Za-z0-9_.+-]+( [A-Za-z0-9_./+-]+)+")

# Basenames a "scoped" sudo rule may never name: shells, interpreters, editors,
# pagers and generic exec/copy tools are root-equivalent whatever the args.
SUDO_DENY_BASENAMES = frozenset({
    "sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish", "busybox",
    "su", "sudo", "doas", "pkexec", "env", "nice", "nohup", "setsid", "stdbuf",
    "timeout", "xargs", "script", "systemd-run", "python", "python2",
    "python3", "perl", "ruby", "node", "nodejs", "php", "lua", "tclsh",
    "expect", "awk", "gawk", "mawk", "nawk", "sed", "find", "vi", "vim",
    "nvim", "view", "nano", "pico", "emacs", "ed", "ex", "less", "more",
    "most", "man", "tee", "cp", "mv", "dd", "install", "chmod", "chown",
    "chgrp", "ln", "tar", "zip", "unzip", "rsync", "scp", "ssh", "git",
    "crontab", "at", "socat", "nc", "ncat", "docker", "podman", "mount",
    "journalctl",
})


def sudo_command_problem(cmd):
    """None when ``cmd`` is an acceptable scoped sudo command, else why not."""
    if not SUDO_CMD_RE.fullmatch(cmd) or "/../" in cmd or "/./" in cmd:
        return ("%r is not a system-bin binary (/usr/bin, /usr/sbin, /bin, "
                "/sbin) with explicit plain arguments" % cmd)
    base = cmd.split(" ")[0].rsplit("/", 1)[-1]
    if re.fullmatch(r"python[0-9.]*|perl[0-9.]*|ruby[0-9.]*|php[0-9.]*", base) \
            or base in SUDO_DENY_BASENAMES:
        return "%r is a shell/interpreter/editor/exec tool (root-equivalent)" % base
    return None


def render_account_marker_step():
    """Refuse to touch a PRE-EXISTING account that airuleset never created (a
    stream / webterm-only / owner account whose authorized_keys a later step
    would replace); mark a freshly created one. A pre-#1184 project account
    (claudy) is ADOPTED by root touching the marker after reviewing its
    declaration — the refusal text says exactly that."""
    return textwrap.dedent("""\

        # 1b. Ownership marker (#1184) — never rewrite a foreign account
        MARKER="{marker_dir}/$ACCOUNT"
        install -d -m 0755 {marker_dir}
        if [ "$CREATED" = "1" ]; then
            printf '%s\\n' "created by airuleset account-bootstrap (#1184)" > "$MARKER"
        elif [ ! -f "$MARKER" ]; then
            echo "ERROR: $ACCOUNT existed before and has no $MARKER — refusing to rewrite a foreign account." >&2
            echo "       Adopt it ONLY if it IS this declared project account and its declared reach/sudo match" >&2
            echo "       what it really needs (a wrong declaration cuts its access): touch $MARKER as root, re-run." >&2
            exit 1
        fi
    """).format(marker_dir=MARKER_DIR)


def render_sudo_step(account, spec):
    """Step 3a: the DECLARED sudo policy. A root-equivalent group always fails
    the bootstrap. ``sudo: False`` renders NO sudoers entry and fails LOUD on
    any route (a stray file, a `%group` rule) — never a silent grant, never a
    silent delete. ``sudo: True`` writes ONE scoped ``NOPASSWD:NOEXEC`` rule
    (NOEXEC blocks a pager/editor shell escape) over root-owned, not
    group/other-writable binaries, ``visudo -cf``-checked in a dot-prefixed
    temp file (sudo's includedir ignores it; removed on a failed check)."""
    groups = "|".join('*" %s "*' % g for g in PRIVILEGED_GROUPS)
    out = textwrap.dedent("""\

        # 3a. Sudo policy (#1184) — no root-equivalent group, ever
        case " $(id -nG "$ACCOUNT") " in
            {groups})
                echo "ERROR: $ACCOUNT is in a root-equivalent group ({names})" >&2
                exit 1 ;;
        esac
    """).format(groups=groups, names=" ".join(PRIVILEGED_GROUPS))
    if not spec["sudo"]:
        return out + textwrap.dedent("""\
            # declared sudo: NO — never grant; fail LOUD if any route exists
            if [ -e "/etc/sudoers.d/$ACCOUNT" ]; then
                echo "ERROR: /etc/sudoers.d/$ACCOUNT exists but the declaration says declared sudo: NO" >&2
                exit 1
            fi
            SUDO_L=$(LC_ALL=C sudo -n -l -U "$ACCOUNT" 2>&1 || true)
            case "$SUDO_L" in
                *"may run the following"*)
                    echo "ERROR: $ACCOUNT has a sudo route but declared sudo: NO" >&2
                    exit 1 ;;
            esac
            echo "  sudo: none (declared sudo: NO)"
        """)
    binaries = sorted({c.split(" ")[0] for c in spec["sudo_commands"]})
    rule = "%s ALL=(root) NOPASSWD:NOEXEC: %s" % (
        account, ", ".join(spec["sudo_commands"]))
    header = "# airuleset:managed — project account %s (#1184): %s" % (
        account, spec["sudo_reason"].strip())
    return out + (
        "# declared SCOPED sudo — root-owned binaries, one visudo-checked rule\n"
        "for BIN in %s; do\n"
        '    case "$(stat -L -c \'%%U %%A\' "$BIN")" in\n'
        '        "root "?????-??-?) ;;\n'
        '        *) echo "ERROR: $BIN is not root-owned + non-group/other-writable" >&2; exit 1 ;;\n'
        "    esac\n"
        "done\n"
        'SUDOERS_TMP=$(mktemp /etc/sudoers.d/.airuleset-"$ACCOUNT".XXXXXX)\n'
        "cat > \"$SUDOERS_TMP\" << 'SUDOERS_EOF'\n"
        "%s\n%s\nSUDOERS_EOF\n"
        'chmod 0440 "$SUDOERS_TMP"\n'
        'if ! visudo -cf "$SUDOERS_TMP"; then rm -f "$SUDOERS_TMP"; exit 1; fi\n'
        'mv "$SUDOERS_TMP" "/etc/sudoers.d/$ACCOUNT"\n'
        'echo "  sudo: scoped rule installed in /etc/sudoers.d/$ACCOUNT"\n'
        % (" ".join(binaries), header, rule))


def nft_table_name(account):
    return "airuleset_reach_" + account.replace("-", "_")


def render_reach_step(account, reach_ips, enforced=True, reason=""):
    """Step 3b: the DECLARED reach, enforced for the account's uid by nftables:
    a NEW outbound tcp/22 connection (loopback included) is rejected unless it
    goes to a declared box (``reach_ips``); INBOUND sessions (the webterm tabs)
    are untouched — their reply packets never carry dport 22. The network layer
    enforces BOXES — a ``user@box`` entry cannot be narrowed to the user here.
    Idempotent (declare, delete, recreate the table), applied NOW, and
    persisted by a oneshot unit ordered like nftables.service and re-applied
    whenever nftables.service restarts (PartOf) or reloads
    (ReloadPropagatedFrom) — both flush the ruleset. An account whose reach is
    declared ``reach_enforced: False`` (a pre-#1184 service account whose
    fleet-wide ssh is its job) gets NO rule and the stated reason instead."""
    if not enforced:
        return ("\n# 3b. Declared reach (#1184) — NOT enforced for this account: "
                "%s\necho \"  reach: NOT enforced (declared reach_enforced: False)\"\n"
                % reason)
    table = nft_table_name(account)
    allow = ""
    v4 = [ip for ip in reach_ips if ":" not in ip]
    v6 = [ip for ip in reach_ips if ":" in ip]
    if v4:
        allow += ('\t\tmeta skuid "%s" tcp dport 22 ip daddr { %s } accept\n'
                  % (account, ", ".join(v4)))
    if v6:
        allow += ('\t\tmeta skuid "%s" tcp dport 22 ip6 daddr { %s } accept\n'
                  % (account, ", ".join(v6)))
    ruleset = (
        "table inet {t}\n"
        "delete table inet {t}\n"
        "table inet {t} {{\n"
        "\tchain output {{\n"
        "\t\ttype filter hook output priority 0; policy accept;\n"
        "{allow}"
        '\t\tmeta skuid "{a}" tcp dport 22 ct state new reject with tcp reset\n'
        "\t}}\n"
        "}}\n").format(t=table, a=account, allow=allow)
    return (
        "\n# 3b. Declared reach (#1184) — ssh egress for this uid only to the "
        "declared boxes\n"
        'NFT=$(command -v nft || true)\n'
        'if [ -z "$NFT" ]; then\n'
        '    echo "ERROR: nft not found — cannot enforce the declared reach of '
        '$ACCOUNT (install the nftables package)" >&2\n'
        '    exit 1\n'
        'fi\n'
        'REACH_NFT="/etc/airuleset/reach-$ACCOUNT.nft"\n'
        "cat > \"$REACH_NFT\" << 'REACH_EOF'\n"
        "%sREACH_EOF\n"
        '"$NFT" -c -f "$REACH_NFT"\n'
        'cat > "/etc/systemd/system/airuleset-reach-$ACCOUNT.service" << UNIT_EOF\n'
        "[Unit]\n"
        "Description=airuleset #1184 declared ssh reach for $ACCOUNT\n"
        "DefaultDependencies=no\n"
        "After=nftables.service\n"
        "Before=network-pre.target shutdown.target\n"
        "Wants=network-pre.target\n"
        "Conflicts=shutdown.target\n"
        "PartOf=nftables.service\n"
        "ReloadPropagatedFrom=nftables.service\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        "ExecStart=$NFT -f $REACH_NFT\n"
        "ExecReload=$NFT -f $REACH_NFT\n"
        "[Install]\n"
        "WantedBy=sysinit.target\n"
        "UNIT_EOF\n"
        "systemctl daemon-reload\n"
        'systemctl enable "airuleset-reach-$ACCOUNT.service"\n'
        'systemctl restart "airuleset-reach-$ACCOUNT.service"\n'
        '"$NFT" list table inet %s >/dev/null\n'
        'echo "  reach: ssh egress limited to %s"\n'
        % (ruleset, table, ", ".join(reach_ips) or "none"))


def render_lockout_step():
    """Step 3c: the account can never become another uid through su or polkit
    (the legacy shared password would otherwise make any tab a newlevel/root
    shell). Scoped to ``airuleset-project`` members — no other account's su or
    polkit behaviour changes. The PAM file is backed up OUTSIDE /etc/pam.d
    (every file there is a PAM service), prepended atomically, and only when
    the line is not already there. A host with ``pkexec`` but no polkit rules
    dir we know fails LOUD instead of silently skipping the lockout."""
    return textwrap.dedent("""\

        # 3c. su / polkit lockout for project accounts (#1184)
        getent group {group} >/dev/null || groupadd --system {group}
        usermod -aG {group} "$ACCOUNT"
        PAM_LINE='{pam}'
        install -d -m 0700 {backup}
        for f in /etc/pam.d/su /etc/pam.d/su-l; do
            [ -f "$f" ] || continue
            if grep -qxF "$PAM_LINE" "$f"; then
                echo "  $f: su lockout already present"
                continue
            fi
            cp -p "$f" "{backup}/$(basename "$f").airuleset-prev-$(date +%s)"
            {{ printf '%s\\n' "# airuleset:managed (#1184) — project accounts can never su" "$PAM_LINE"; cat "$f"; }} > "$f.airuleset-new"
            chmod --reference="$f" "$f.airuleset-new"
            mv "$f.airuleset-new" "$f"
            echo "  $f: su lockout added"
        done
        POLKIT_DONE=0
        if [ -d /etc/polkit-1/rules.d ]; then
            cat > /etc/polkit-1/rules.d/10-airuleset-project-accounts.rules << 'POLKIT_EOF'
        // airuleset:managed (#1184) — project accounts never get polkit admin
        polkit.addRule(function(action, subject) {{
            if (subject.isInGroup("{group}")) {{ return polkit.Result.NO; }}
        }});
        POLKIT_EOF
            POLKIT_DONE=1
        fi
        if [ -d /etc/polkit-1/localauthority/50-local.d ]; then
            cat > /etc/polkit-1/localauthority/50-local.d/10-airuleset-project-accounts.pkla << 'PKLA_EOF'
        [airuleset project accounts (#1184)]
        Identity=unix-group:{group}
        Action=*
        ResultAny=no
        ResultInactive=no
        ResultActive=no
        PKLA_EOF
            POLKIT_DONE=1
        fi
        if [ "$POLKIT_DONE" = "0" ] && command -v pkexec >/dev/null; then
            echo "ERROR: pkexec present but no polkit rules dir — cannot lock polkit for $ACCOUNT" >&2
            exit 1
        fi
    """).format(group=PROJECT_GROUP, pam=PAM_SU_LINE, backup=BACKUP_DIR)


def render_isolation_check_step():
    """Step 3d: another account's home that is world-readable/traversable would
    let the project uid read its files — fail LOUD (the fix, `chmod o-rx`, is
    the owner's call, never applied silently here). A ``/proc`` without
    ``hidepid`` exposes other users' command lines (e.g. an ``sshpass -p``) —
    reported, not changed (it is a host-wide mount option)."""
    return textwrap.dedent("""\

        # 3d. Isolation checks (#1184) — other homes closed, /proc visibility reported
        for H in /home/*; do
            [ -d "$H" ] || continue
            [ "$H" = "/home/$ACCOUNT" ] && continue
            case "$(stat -c '%A' "$H")" in
                *r??|*??[xt])
                    echo "ERROR: $H is world-readable/traversable — chmod o-rx it first" >&2
                    exit 1 ;;
            esac
        done
        case "$(findmnt -n -o OPTIONS /proc 2>/dev/null || true)" in
            *hidepid=*) echo "  /proc: hidepid set" ;;
            *) echo "  WARNING: /proc has no hidepid — other users' command lines are visible to $ACCOUNT" ;;
        esac
    """)
