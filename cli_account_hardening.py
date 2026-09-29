"""Privilege-boundary steps of the project-account root bootstrap (#1184).

A separate uid is only a boundary when nothing lets it BECOME another uid or
reach another box with a credential it was never declared. On a host that
still runs the legacy shared ``newlevel`` account (weak shared password,
NOPASSWD sudo, every fleet key), a shell in a project account could otherwise
``su - newlevel`` + ``sudo -i``, ``pkexec`` with newlevel's password, or
``ssh newlevel@<any box>`` with the shared password (#1184 review 1 🔴). These
renderers close each path for the declared account ONLY — nothing they write
changes what any OTHER account may do:

  * ``render_account_marker_step`` — root-only ownership marker, so a
    re-render can never rewrite a FOREIGN pre-existing account;
  * ``render_sudo_step`` — the declared sudo policy: none (fail LOUD on any
    route, incl. a root-equivalent group) or ONE scoped, ``NOEXEC``,
    ``visudo``-checked ``/etc/sudoers.d/<acct>`` rule;
  * ``render_reach_step`` — the declared ``reach`` as an nftables egress rule
    keyed on the account's uid: new outbound ssh (tcp/22, loopback included)
    is rejected except to the declared boxes; persisted by a oneshot unit;
  * ``render_lockout_step`` — the account joins ``airuleset-project``, which
    ``pam_wheel`` (deny) and a polkit rule bar from ``su`` and polkit admin.

Pure string renderers (stdlib only). ``cli_account_bootstrap`` validates every
interpolated value before calling them.
"""
import re
import textwrap

# The group every project account joins; PAM (su) and polkit deny its members.
PROJECT_GROUP = "airuleset-project"
MARKER_DIR = "/etc/airuleset/project-accounts"
# Groups that are root-equivalent or expose other accounts' secrets.
PRIVILEGED_GROUPS = ("sudo", "admin", "wheel", "root", "docker", "lxd", "disk",
                     "shadow", "adm")
PAM_SU_LINE = "auth required pam_wheel.so deny use_uid group=%s" % PROJECT_GROUP

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
    parts = cmd.split(" ")
    base = parts[0].rsplit("/", 1)[-1]
    if re.fullmatch(r"python[0-9.]*|perl[0-9.]*|ruby[0-9.]*|php[0-9.]*", base) \
            or base in SUDO_DENY_BASENAMES:
        return "%r is a shell/interpreter/editor/exec tool (root-equivalent)" % base
    if len(parts) < 2:
        return "%r has no explicit arguments (sudoers would allow ANY)" % cmd
    return None


def render_account_marker_step():
    """Refuse to touch a PRE-EXISTING account that airuleset never created (a
    stream / webterm-only / owner account whose authorized_keys the next step
    would replace); mark a freshly created one."""
    return textwrap.dedent("""\

        # 1b. Ownership marker (#1184) — never rewrite a foreign account
        MARKER="{marker_dir}/$ACCOUNT"
        install -d -m 0755 {marker_dir}
        if [ "$CREATED" = "1" ]; then
            printf '%s\\n' "created by airuleset account-bootstrap (#1184)" > "$MARKER"
        elif [ ! -f "$MARKER" ]; then
            echo "ERROR: $ACCOUNT existed before and has no $MARKER — refusing to rewrite a foreign account." >&2
            echo "       If it IS this declared project account: touch $MARKER as root, then re-run." >&2
            exit 1
        fi
    """).format(marker_dir=MARKER_DIR)


def render_sudo_step(account, spec):
    """Step 9: the DECLARED sudo policy. A root-equivalent group always fails
    the bootstrap. ``sudo: False`` renders NO sudoers entry and fails LOUD on
    any route (a stray file, a `%group` rule) — never a silent grant, never a
    silent delete. ``sudo: True`` writes ONE scoped ``NOPASSWD:NOEXEC`` rule
    (NOEXEC blocks a pager/editor shell escape), ``visudo -cf``-checked in a
    dot-prefixed temp file (sudo's includedir ignores it) then ``mv``'d."""
    groups = "|".join('*" %s "*' % g for g in PRIVILEGED_GROUPS)
    out = textwrap.dedent("""\

        # 9. Sudo policy (#1184) — no root-equivalent group, ever
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
    rule = "%s ALL=(root) NOPASSWD:NOEXEC: %s" % (
        account, ", ".join(spec["sudo_commands"]))
    header = "# airuleset:managed — project account %s (#1184): %s" % (
        account, spec["sudo_reason"].strip())
    return out + (
        "# declared SCOPED sudo — one visudo-checked rule\n"
        'SUDOERS_TMP=$(mktemp /etc/sudoers.d/.airuleset-"$ACCOUNT".XXXXXX)\n'
        "cat > \"$SUDOERS_TMP\" << 'SUDOERS_EOF'\n"
        "%s\n%s\nSUDOERS_EOF\n"
        'chmod 0440 "$SUDOERS_TMP"\n'
        'visudo -cf "$SUDOERS_TMP"\n'
        'mv "$SUDOERS_TMP" "/etc/sudoers.d/$ACCOUNT"\n'
        'echo "  sudo: scoped rule installed in /etc/sudoers.d/$ACCOUNT"\n'
        % (header, rule))


def nft_table_name(account):
    return "airuleset_reach_" + account.replace("-", "_")


def render_reach_step(account, reach_ips):
    """Step 10: the DECLARED reach, enforced for the account's uid by nftables:
    a NEW outbound tcp/22 connection (loopback included) is rejected unless it
    goes to a declared box (``reach_ips``). The network layer enforces BOXES —
    a ``user@box`` reach entry cannot be narrowed to the user here. Idempotent
    (declare, delete, recreate the table) and persisted by a oneshot unit that
    runs after nftables.service (whose `flush ruleset` would drop it)."""
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
        "\n# 10. Declared reach (#1184) — ssh egress for this uid only to the "
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
        '"$NFT" -f "$REACH_NFT"\n'
        'cat > "/etc/systemd/system/airuleset-reach-$ACCOUNT.service" << UNIT_EOF\n'
        "[Unit]\n"
        "Description=airuleset #1184 declared ssh reach for $ACCOUNT\n"
        "After=nftables.service network-pre.target\n"
        "PartOf=nftables.service\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        "ExecStart=$NFT -f $REACH_NFT\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
        "UNIT_EOF\n"
        "systemctl daemon-reload\n"
        'systemctl enable "airuleset-reach-$ACCOUNT.service"\n'
        'echo "  reach: ssh egress limited to %s"\n'
        % (ruleset, ", ".join(reach_ips) or "none"))


def render_lockout_step():
    """Step 11: the account can never become another uid through su or polkit
    (the legacy shared password would otherwise make any tab a newlevel/root
    shell). Scoped to ``airuleset-project`` members — no other account's su or
    polkit behaviour changes. The PAM file is backed up, prepended atomically,
    and only when the line is not already there."""
    return textwrap.dedent("""\

        # 11. su / polkit lockout for project accounts (#1184)
        getent group {group} >/dev/null || groupadd --system {group}
        usermod -aG {group} "$ACCOUNT"
        PAM_LINE='{pam}'
        for f in /etc/pam.d/su /etc/pam.d/su-l; do
            [ -f "$f" ] || continue
            if grep -qxF "$PAM_LINE" "$f"; then
                echo "  $f: su lockout already present"
                continue
            fi
            cp -p "$f" "$f.airuleset-prev-$(date +%s)"
            {{ printf '%s\\n' "# airuleset:managed (#1184) — project accounts can never su" "$PAM_LINE"; cat "$f"; }} > "$f.airuleset-new"
            chmod --reference="$f" "$f.airuleset-new"
            mv "$f.airuleset-new" "$f"
            echo "  $f: su lockout added"
        done
        if [ -d /etc/polkit-1/rules.d ]; then
            cat > /etc/polkit-1/rules.d/10-airuleset-project-accounts.rules << 'POLKIT_EOF'
        // airuleset:managed (#1184) — project accounts never get polkit admin
        polkit.addRule(function(action, subject) {{
            if (subject.isInGroup("{group}")) {{ return polkit.Result.NO; }}
        }});
        POLKIT_EOF
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
        fi
    """).format(group=PROJECT_GROUP, pam=PAM_SU_LINE)
