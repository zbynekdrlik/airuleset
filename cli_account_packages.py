"""Project-account ``system_packages`` render steps (#973), split out of
``cli_account_bootstrap`` at its size cap (#1401). Stdlib only;
``cli_account_bootstrap`` re-exports these names unchanged."""
import re
import textwrap

# Debian policy §5.6.1: lowercase alnum start, then [a-z0-9.+-], length >= 2.
_PACKAGE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9.+\-]+")


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
