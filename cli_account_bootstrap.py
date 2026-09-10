"""Root bootstrap renderer for service accounts on the controller (#960).

Renders an IDEMPOTENT bash script that root runs ONCE to create a service
account (e.g. ``claudy``) on the controller box.  The script creates the unix
user, sets permissions, installs authorized_keys (fleet push keys + per-human
webterm forced-command keys), and enables loginctl linger — everything a
sudo-less controller box cannot do itself.

ZERO outbound imports by design (the L-E leaf convention); constants are
imported LAZILY inside render functions so the module loads with no side effects.
"""

import re
import sys
import textwrap


# Debian package name grammar (Policy §5.6.1): [a-z0-9][a-z0-9.+\-]+
# with minimum length 2.  We validate at render time so a stray
# space/quote/metachar fails LOUD instead of silently breaking the script.
_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+\-]+$")


# ---------------------------------------------------------------------------
# Service account registry — which accounts this renderer knows about
# ---------------------------------------------------------------------------

# Each entry defines the per-human tmux session names that webterm connects to.
# The keys of `webterm_sessions` are the humans whose webterm key gets a
# forced-command line in authorized_keys; the values are the tmux session
# `preferred` names those forced commands target.
SERVICE_ACCOUNTS = {
    "claudy": {
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
}


def _forced_command_key_line(preferred, pubkey, start_dir_chain=None):
    """Build a ``restrict,pty,command="..."`` authorized_keys line that
    attaches the named tmux session.  Delegates to the SINGLE source
    ``_controller_lane_key_line`` in ``cli_webterm_only.py`` — Y1 Fable
    review: two copies of authorized_keys escaping = drift hazard.

    #960+#961: ``start_dir_chain`` threads to the forced command so the
    tab opens in the correct project dir (e.g. ``devel/claudy``)."""
    from cli_webterm_only import _controller_lane_key_line
    return _controller_lane_key_line(preferred, pubkey,
                                     start_dir_chain=start_dir_chain)


def desired_keys_for_service_account(account):
    """Return the list of authorized_keys lines for a service account.
    Raises ValueError for an unknown account."""
    if account not in SERVICE_ACCOUNTS:
        raise ValueError("unknown service account: %r" % account)

    from cli_webterm_only import FLEET_PUSH_PUBKEYS, WEBTERM_CONTROLLER_LANE_PUBKEYS
    from cli_owner_keys import OWNER_PUBKEYS

    keys = list(FLEET_PUSH_PUBKEYS)
    keys.extend(OWNER_PUBKEYS)

    # Per-human webterm forced-command keys
    spec = SERVICE_ACCOUNTS[account]
    for human in sorted(spec["webterm_sessions"]):
        sess = spec["webterm_sessions"][human]
        preferred = sess["preferred"]
        chain = sess.get("start_dir_chain")
        pubkey = WEBTERM_CONTROLLER_LANE_PUBKEYS.get(human)
        if pubkey:
            keys.append(_forced_command_key_line(preferred, pubkey,
                                                start_dir_chain=chain))

    return keys


def render_authorized_keys(account):
    """Render the full authorized_keys content for a service account."""
    header = (
        "# airuleset:managed — service account %s (#960); "
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
        if not _PACKAGE_NAME_RE.match(name):
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


def render_root_bootstrap(account):
    """Render an idempotent bash script for root that creates the service
    account on the controller.  Returns the script as a string."""
    if account not in SERVICE_ACCOUNTS:
        raise ValueError("unknown service account: %r" % account)

    ak_content = render_authorized_keys(account)
    spec = SERVICE_ACCOUNTS[account]
    packages = spec.get("system_packages", [])

    # Shell-escape the authorized_keys content for a heredoc
    # Using a quoted heredoc delimiter so no shell expansion happens
    script = textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail

        # airuleset root bootstrap for service account '{account}' (#960)
        # Generated by: python3 airuleset.py account-bootstrap --render {account}
        # Run as root on the controller box. Idempotent.

        ACCOUNT='{account}'

        echo "=== airuleset root bootstrap: $ACCOUNT ==="

        # 1. Create user (idempotent)
        if id "$ACCOUNT" &>/dev/null; then
            echo "  user $ACCOUNT already exists — skipping useradd"
        else
            useradd -m -s /bin/bash -U "$ACCOUNT"
            echo "  created user $ACCOUNT"
        fi

        # 2. Home permissions
        chmod 0750 "/home/$ACCOUNT"
        echo "  home permissions: $(stat -c '%a' /home/$ACCOUNT)"

        # 3. Enable loginctl linger (reboot-durable user services)
        loginctl enable-linger "$ACCOUNT"
        echo "  linger: $(loginctl show-user "$ACCOUNT" -p Linger --value 2>/dev/null || echo 'enabled')"

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
    """).format(account=account, ak_content=ak_content)

    # 8. System packages — only when the account declares them (#973)
    script += _render_system_packages_step(packages)

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
        echo "  3. Verify: ssh -i ~/.secrets/airuleset_push_ed25519 $ACCOUNT@100.101.214.103 true"
    """)
    script += readback
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
