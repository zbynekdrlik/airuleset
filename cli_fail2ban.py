"""fail2ban hardening drop-ins for the #841 disk-guard ROOT apply.

A zero-import leaf: `cli_disk_guard_root.build_apply_script` writes both files
(only where fail2ban is installed) and reloads fail2ban.

  * ``jail.d/50-airuleset-hardening.conf`` (#841): a longer ban + the recidive
    jail + a ``[DEFAULT]`` ignoreip with loopback and the tailnet.
  * ``jail.d/50-airuleset-hardening.local`` (#1306): the per-jail ignoreip
    APPEND. fail2ban reads jail.conf -> jail.d/*.conf -> jail.local ->
    jail.d/*.local, so the ``[DEFAULT]`` of the .conf LOSES to any hand-made
    jail.local. subdev's listed dev1 + gk but not the controller, which was
    banned three times on 16.11.2026. The .local loads after jail.local and
    wins, while ``%(known/ignoreip)s`` keeps the hand-made entries.
"""

FAIL2BAN_JAIL_PATH = "/etc/fail2ban/jail.d/50-airuleset-hardening.conf"
FAIL2BAN_JAIL_LOCAL_PATH = "/etc/fail2ban/jail.d/50-airuleset-hardening.local"
# The jails whose ignoreip the .local appends to: sshd (distro-enabled) and the
# recidive jail the hardening enables.
FAIL2BAN_IGNOREIP_JAILS = ("sshd", "recidive")

FAIL2BAN_BANTIME = "1d"            # was 600 s — reduces auth.log growth at source
# loopback + tailscale (100.64.0.0/10) so a recidive misfire never locks out an
# admin session on the fleet's own tailnet (Fable-flagged).
FAIL2BAN_IGNOREIP = "127.0.0.1/8 ::1 100.64.0.0/10"


def render_fail2ban_hardening():
    """fail2ban ``jail.d`` hardening — a longer ban + a ``recidive`` jail cut
    the brute-force churn that GROWS auth.log/btmp (~600M per rotation cycle on
    gk). ``ignoreip`` carries the fleet tailnet so a recidive misfire never
    locks out an admin session."""
    return (
        "# Managed by airuleset (#841) — reduce auth.log/btmp GROWTH at the\n"
        "# source: a longer ban + a recidive jail against the public-box\n"
        "# brute-force stream (11 010 failed logins / 1 672 bans on gk).\n"
        "# ignoreip carries loopback + the tailscale range so a recidive\n"
        "# misfire never locks out an admin session on the fleet's own tailnet.\n"
        "[DEFAULT]\n"
        "bantime = %s\n"
        "ignoreip = %s\n"
        "\n"
        "[recidive]\n"
        "enabled = true\n"
        % (FAIL2BAN_BANTIME, FAIL2BAN_IGNOREIP)
    )


def render_fail2ban_ignoreip_local():
    """fail2ban ``jail.d/*.local`` (#1306) — APPENDS the fleet ignoreip
    (loopback + the tailnet, so the controller too) to each jail in
    ``FAIL2BAN_IGNOREIP_JAILS``. ``%(known/ignoreip)s`` keeps whatever an
    earlier file set (a hand-made jail.local's entries survive). Never in
    ``[DEFAULT]``: there ``known/`` recurses and fail2ban 1.0.2 refuses the
    whole config (measured). Sets no ``enabled`` — it never turns a jail on."""
    body = "".join(
        "\n[%s]\nignoreip = %%(known/ignoreip)s %s\n" % (jail, FAIL2BAN_IGNOREIP)
        for jail in FAIL2BAN_IGNOREIP_JAILS)
    return (
        "# Managed by airuleset (#841, #1306) — loads AFTER jail.local, so a\n"
        "# hand-made jail.local can never drop the fleet tailnet (the\n"
        "# controller) from the ignore list. Appends per jail; keeps the rest.\n"
        + body
    )
