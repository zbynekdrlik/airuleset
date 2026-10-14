"""Privilege-boundary steps of the project-account root bootstrap (#1184).

A separate uid is only a boundary when nothing lets it BECOME another uid. On a host that
still runs the legacy shared ``newlevel`` account (weak shared password,
NOPASSWD sudo, every fleet key), a shell in a project account could otherwise
``su - newlevel`` + ``sudo -i`` or ``pkexec`` with newlevel's password
(#1184 review 1 🔴). These
renderers close THOSE paths for the declared account ONLY — nothing they write
changes what any OTHER account may do — and they all run BEFORE the account's
authorized_keys land, so a failed step never leaves a loginable, unbounded
account (#1184 review 2):

  * ``render_account_marker_step`` — root-only ownership marker, so a
    re-render can never rewrite a FOREIGN pre-existing account;
  * ``render_sudo_step`` — the declared sudo policy: none (fail LOUD on any
    route, incl. a root-equivalent group), ONE scoped, ``NOEXEC``,
    ``visudo``-checked ``/etc/sudoers.d/<acct>`` rule over root-owned binaries
    (``sudo: True``), or (#1186, ``sudo: "commands"``) ONE rule naming exactly
    the declared ``/usr/local/sbin/<acct>-*`` project scripts with no
    arguments, each proven root-owned and not writable by the account
    (``render_sudo_path_checks``);
  * ``render_reach_teardown_step`` — (#1232) removes a pre-#1232 per-uid ssh
    egress table, its unit and nft file; no account gets one any more;
  * ``render_lockout_step`` — the account joins ``airuleset-project``, which
    ``pam_wheel`` (deny) and a polkit rule bar from ``su`` and polkit admin;
  * ``render_isolation_check_step`` — other accounts' homes must not be
    world-readable/traversable (fail LOUD); a ``/proc`` without ``hidepid``
    is reported.

What this does NOT close (honest residual): network egress. #1232 (owner
ruling 14.10.2026) removed the per-uid ssh egress rule: it was routed around
through any reachable host and pushed sessions to riskier detours. A project
account can therefore open ssh to any host; reaching another account still
needs that account's credential. The remaining exposure is a host whose
shared ``newlevel`` still accepts PASSWORD ssh: refusing password auth there
closes it without a network rule.

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


# #1186: a command-scoped sudo rule names ONLY a project script the migration
# installs root-owned under this dir, prefixed with the account name, so a
# generic system tool (systemctl, apt, docker) can never be granted; the rule
# then allows each script with NO arguments (`""`).
SUDO_SCRIPT_DIR = "/usr/local/sbin"


def sudo_script_problem(account, path):
    """None when ``path`` is an acceptable ``sudo: "commands"`` entry for
    ``account`` (exactly ``/usr/local/sbin/<account>-<name>``: one lowercase
    ``[a-z0-9._-]`` component, no argument, wildcard or sudoers metachar),
    else why not."""
    want = (re.escape(SUDO_SCRIPT_DIR + "/" + account + "-")
            + r"[a-z0-9][a-z0-9._-]*")
    if not re.fullmatch(want, path) or ".." in path:
        return ("%r is not exactly %s/%s-<name> (one lowercase component, no "
                "arguments or wildcards)" % (path, SUDO_SCRIPT_DIR, account))
    return None


def render_sudo_path_checks(paths):
    """#1186: refuse a declared sudo command path that is missing, a symlink,
    not an executable file, or on a path (the file and EVERY ancestor dir up
    to ``/``) that is not root-owned, is group/other-writable, or is writable
    by the account itself (ACLs included: asked AS the account via
    ``runuser``). Fail-CLOSED: a ``runuser`` that cannot answer is a refusal,
    never read as "not writable"."""
    return (
        "for P in %s; do\n"
        '    if [ -L "$P" ]; then\n'
        '        echo "ERROR: sudo command $P is a symlink — declare the real '
        'root-owned path" >&2\n'
        "        exit 1\n"
        "    fi\n"
        '    if [ ! -f "$P" ] || [ ! -x "$P" ]; then\n'
        '        echo "ERROR: sudo command $P is missing or not an executable '
        'file — install it root-owned first" >&2\n'
        "        exit 1\n"
        "    fi\n"
        '    D="$P"\n'
        "    while :; do\n"
        '        case "$(stat -c \'%%U %%A\' "$D")" in\n'
        '            "root "?????-??-?) ;;\n'
        '            *) echo "ERROR: $D (sudo command $P) is not root-owned + '
        'non-group/other-writable" >&2; exit 1 ;;\n'
        "        esac\n"
        '        W=$(runuser -u "$ACCOUNT" -- sh -c \'if [ -w "$1" ]; then '
        'echo yes; else echo no; fi\' _ "$D" 2>/dev/null || true)\n'
        '        case "$W" in\n'
        "            no) ;;\n"
        '            yes) echo "ERROR: $D (sudo command $P) is writable by '
        '$ACCOUNT" >&2; exit 1 ;;\n'
        '            *) echo "ERROR: cannot verify that $ACCOUNT cannot write '
        '$D (sudo command $P) — refusing" >&2; exit 1 ;;\n'
        "        esac\n"
        '        [ "$D" = "/" ] && break\n'
        '        D=$(dirname "$D")\n'
        "    done\n"
        "done\n" % " ".join(paths))


# One visudo-checked /etc/sudoers.d/<acct> rule, written through a dot-prefixed
# temp file (sudo's includedir ignores it; removed on a failed check).
_SUDOERS_INSTALL = (
    'SUDOERS_TMP=$(mktemp /etc/sudoers.d/.airuleset-"$ACCOUNT".XXXXXX)\n'
    "cat > \"$SUDOERS_TMP\" << 'SUDOERS_EOF'\n"
    "%s\n%s\nSUDOERS_EOF\n"
    'chmod 0440 "$SUDOERS_TMP"\n'
    'if ! visudo -cf "$SUDOERS_TMP"; then rm -f "$SUDOERS_TMP"; exit 1; fi\n'
    'mv "$SUDOERS_TMP" "/etc/sudoers.d/$ACCOUNT"\n'
    'echo "  sudo: scoped rule installed in /etc/sudoers.d/$ACCOUNT"\n')


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
            echo "       Adopt it ONLY if it IS this declared project account and its declared sudo matches" >&2
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
    temp file (sudo's includedir ignores it; removed on a failed check).
    ``sudo: "commands"`` (#1186) writes ONE ``NOPASSWD:`` rule naming exactly
    the declared project script paths, each with NO arguments (``""``), after
    ``render_sudo_path_checks``."""
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
    if spec["sudo"] == "commands":
        # #1186: exactly the declared project scripts. NOT NOEXEC (an installer
        # script must run its child commands), so each path is proven
        # root-owned and account-unwritable before the rule lands.
        paths = list(spec["sudo_commands"])
        # `""` = the command may run with NO arguments (sudoers(5)), so a
        # script's argument parsing is never root attack surface.
        rule = "%s ALL=(root) NOPASSWD: %s" % (
            account, ", ".join('%s ""' % p for p in paths))
        header = "# airuleset:managed — project account %s (#1184/#1186): %s" % (
            account, spec["sudo_reason"].strip())
        return out + (
            "# declared COMMAND-SCOPED sudo (#1186) — exactly these root-owned "
            "project scripts, one visudo-checked rule\n"
            + render_sudo_path_checks(paths) + _SUDOERS_INSTALL % (header, rule))
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
        "done\n" % " ".join(binaries)) + _SUDOERS_INSTALL % (header, rule)


def nft_table_name(account):
    return "airuleset_reach_" + account.replace("-", "_")


def render_reach_teardown_step(account):
    """Step 3b (#1232): remove the ssh reach rule an earlier render installed.

    The owner ruled on 14.10.2026 that a network egress rule is routed around
    through any reachable host and pushes a session to riskier detours, so no
    account gets one. A box bootstrapped before #1232 still carries the per-uid
    ``inet airuleset_reach_<acct>`` table, its ``airuleset-reach-<acct>.service``
    unit and ``/etc/airuleset/reach-<acct>.nft``; this idempotent step removes
    each one that exists and is a quiet no-op otherwise. Fails loud (``set -e``)
    when a present unit or table cannot be removed."""
    return (
        "\n# 3b. ssh reach rule removed (#1232) — tear down what an earlier render "
        "installed\n"
        "REACH_TABLE='%s'\n"
        'REACH_UNIT="airuleset-reach-$ACCOUNT.service"\n'
        'REACH_NFT="/etc/airuleset/reach-$ACCOUNT.nft"\n'
        'if [ -f "/etc/systemd/system/$REACH_UNIT" ]; then\n'
        '    systemctl disable --now "$REACH_UNIT"\n'
        '    rm -f "/etc/systemd/system/$REACH_UNIT"\n'
        "    systemctl daemon-reload\n"
        "fi\n"
        "NFT=$(command -v nft || true)\n"
        'if [ -n "$NFT" ] && "$NFT" list table inet "$REACH_TABLE" >/dev/null 2>&1; then\n'
        '    "$NFT" delete table inet "$REACH_TABLE"\n'
        "fi\n"
        'rm -f "$REACH_NFT"\n'
        'echo "  reach: no ssh egress rule (#1232)"\n'
        % nft_table_name(account))


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
