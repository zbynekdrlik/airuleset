"""Disk-guard ROOT/system-level legs (#841) — a self-contained CLI leaf.

Follow-up to the shipped #834 per-USER disk-pressure guard (watchdog Job 40,
`watchdog/disk_guard.py`), which drains the CALLING user's own ``$HOME`` and
REFUSES the moment ``euid==0`` — so it structurally cannot reach the root-owned
or cross-user classes that actually fill the public boxes (gk 91 %, subdev
90 %): ``/var/log`` btmp/wtmp/auth.log (brute-force noise, 11 010 failed logins
/ 1 672 bans on gk; btmp/wtmp rotate MONTHLY rotate-1 → ~900M on disk at once),
the system journal, the apt cache, docker CI images, a gh-runner ``_work``, and
other stream users' ``/tmp``. Stream users on subdev are sudo-less, so a
per-user watchdog can neither rotate nor even READ these.

DOCTRINE (owner HARD RULE 2026-09-02, a SECURITY BOUNDARY): the root legs
SURFACE and ROTATE — they NEVER delete bytes outside the caller's own home,
EXCEPT standard log ROTATION (config-driven). So this module installs, at
push/install time over ``ssh root@<host>`` (mirroring the shipped
`cli_resource_guards.py` #775 EXACTLY — never a live root action from the
implementing lane):

  * ROTATE: btmp/wtmp logrotate config OVERWRITING the distro
    ``/etc/logrotate.d/btmp|wtmp`` in place (weekly + compress + rotate 4, the
    ``create`` mode preserved EXACTLY — btmp ``0660 root utmp``, since btmp
    holds usernames/mistyped passwords), a journald ``SystemMaxUse`` cap
    drop-in (journald's OWN rotation), and a fail2ban ``jail.d`` hardening
    drop-in (bantime 1d + a ``recidive`` jail + a fleet ``ignoreip`` — reduces
    auth.log GROWTH at its source).
  * SURFACE: a root-owned ``airuleset-disk-guard-root.timer`` + oneshot service
    running a REPORT-ONLY reporter daily that sizes the root-owned reclaimable
    candidates and writes them to a world-readable ``/run/airuleset/
    disk-guard-root.json`` — NEVER deleting/rotating/pruning anything. The
    per-user watchdog reads that report (no root needed) and, at CRITICAL
    pressure, records a ``disk-guard: root-level candidates`` finding a SESSION
    raises the owner-daily ``❓`` from (`watchdog/disk_guard_root.py`).

The apply script is idempotent-atomic (mktemp+chmod+mv), ``daemon-reload``s,
``systemctl enable --now``s the timer, runs the reporter ONCE (bounded
``timeout``) so the JSON exists on day 0, and READS BACK the result (journald
cap present, logrotate config free of the duplicate-entry fail-open, timer
enabled, report JSON parseable) → a mismatch exits non-zero, turning the push
step LOUD. ZERO couplings by design — a pure leaf: `provision_disk_guard_root`
reads `airuleset.DISK_GUARD_ROOT_HOSTS` (the facade re-export) LAZILY inside the
function body, the same L-E convention `cli_resource_guards.provision_shared_
stream_guards` uses.
"""
import os
import shlex
import subprocess
import sys


# ---------------------------------------------------------------------------
# Managed drop-in / reporter / unit paths. The btmp/wtmp logrotate files
# OVERWRITE the distro paths in place (NOT a second `50-airuleset-*` file — two
# logrotate stanzas for the same log error "duplicate log entry" and silently
# rotate NOTHING; the Fable-flagged fail-open). Everything else is
# unmistakably airuleset-owned with a `50-airuleset-*` prefix.
# ---------------------------------------------------------------------------
LOGROTATE_BTMP_PATH = "/etc/logrotate.d/btmp"
LOGROTATE_WTMP_PATH = "/etc/logrotate.d/wtmp"
JOURNALD_CAP_PATH = "/etc/systemd/journald.conf.d/50-airuleset-journal-cap.conf"
FAIL2BAN_JAIL_PATH = "/etc/fail2ban/jail.d/50-airuleset-hardening.conf"
# #982: owner break-glass ignoreip — controller-only, separate from the
# fleet-wide hardening. The `60-` prefix loads AFTER the `50-` fleet default.
# NOTE: fail2ban [DEFAULT] ignoreip is LAST-WRITER-WINS — the 60- file
# REPLACES (not extends) any 50- ignoreip on the same box. This is safe
# because the controller is NOT in DISK_GUARD_ROOT_HOSTS, so 50-airuleset-
# hardening.conf is never deployed there. If the controller is ever added
# to DISK_GUARD_ROOT_HOSTS, this file must fold in the 50-'s ignoreip too.
OWNER_IGNOREIP_PATH = "/etc/fail2ban/jail.d/60-airuleset-owner-ignoreip.conf"
# #985: sshd password login for the airuleset user — controller-only, break-glass
# from any device. The `60-` prefix loads AFTER the `50-` fleet default (which
# sets the global PasswordAuthentication no); the Match User scopes password auth
# to the single airuleset account.
SSHD_PASSWORD_PATH = "/etc/ssh/sshd_config.d/60-airuleset-password.conf"
# #982: DNS names that must resolve to the controller.
# ar.newlevel.media -> the PUBLIC IP (reachable from anywhere, #985 re-scope).
# airuleset (MagicDNS) + claudy.newlevel.media -> the tailscale IP.
CONTROLLER_TAILSCALE_IP = "100.101.214.103"
CONTROLLER_PUBLIC_IP = "159.69.209.249"
# Per-name expected IP: ar uses the public IP, airuleset uses tailscale.
# claudy.newlevel.media is a PROXIED CNAME (resolves to Cloudflare edge IPs,
# not any controller IP) — excluded from the IP check, presence-only.
CONTROLLER_DNS_EXPECTED = {
    "ar.newlevel.media": CONTROLLER_PUBLIC_IP,
    "airuleset": CONTROLLER_TAILSCALE_IP,
}
# All names to check: expected-IP entries + proxied entries (resolves-at-all).
CONTROLLER_DNS_PROXIED = ("claudy.newlevel.media",)
CONTROLLER_DNS_NAMES = tuple(CONTROLLER_DNS_EXPECTED.keys()) + CONTROLLER_DNS_PROXIED
TMPFILES_PATH = "/etc/tmpfiles.d/50-airuleset-disk-guard.conf"
REPORTER_SCRIPT_PATH = "/usr/local/lib/airuleset/disk-guard-root-report.sh"
ROOT_SERVICE_PATH = "/etc/systemd/system/airuleset-disk-guard-root.service"
ROOT_TIMER_PATH = "/etc/systemd/system/airuleset-disk-guard-root.timer"

RUN_DIR = "/run/airuleset"
ROOT_REPORT_PATH = "/run/airuleset/disk-guard-root.json"
DRAIN_SCRIPT_PATH = "/usr/local/lib/airuleset/disk-guard-root-drain.sh"
DRAIN_SERVICE_PATH = "/etc/systemd/system/airuleset-disk-guard-root-drain.service"
DRAIN_TIMER_PATH = "/etc/systemd/system/airuleset-disk-guard-root-drain.timer"
DRAIN_LOG_PATH = "/run/airuleset/disk-guard-root-drain.log"

# #895: accounts to SKIP when cleaning cross-user caches (paused = #851).
# DERIVED from cli_fleet.REMOTE_HOSTS at render time (F3 review fix) so adding
# a paused account to the fleet config automatically skips it in the drain.
def _paused_accounts():
    """The set of paused account usernames, derived from the fleet config."""
    try:
        import cli_fleet
        return tuple(sorted({h["user"] for h in cli_fleet.REMOTE_HOSTS
                             if h.get("paused") and h.get("user")}))
    except Exception:
        return ("simap1",)     # fail-safe fallback — errs toward keeping (safe)


PAUSED_ACCOUNTS = _paused_accounts()

# Playwright: keep ONLY the newest revision per family per account.
# tmp: regular files in /tmp older than 2 days.
TMP_MAX_AGE_DAYS = 2
# tgz archives: older than 24h with extracted target present.
TGZ_MAX_AGE_HOURS = 24
# npm/pip cache: older than 7 days.
CACHE_MAX_AGE_DAYS = 7

# The numeric/text policy. One source, shared by the renderers + the read-back.
JOURNAL_MAX_USE = "200M"           # journald's own SystemMaxUse rotation cap
LOGROTATE_ROTATE = 4               # weekly + rotate 4 (was monthly rotate 1)
FAIL2BAN_BANTIME = "1d"            # was 600 s — reduces auth.log growth at source
# loopback + tailscale (100.64.0.0/10) so a recidive misfire never locks out an
# admin session on the fleet's own tailnet (Fable-flagged).
FAIL2BAN_IGNOREIP = "127.0.0.1/8 ::1 100.64.0.0/10"
REPORTER_TIMEOUT_S = 120           # the reporter is `du`-heavy; never hang the push


def render_logrotate_btmp():
    """btmp logrotate config OVERWRITING the distro ``/etc/logrotate.d/btmp``
    (weekly + compress + rotate 4). ``create 0660 root utmp`` preserved EXACTLY
    — btmp records failed-login usernames + mistyped passwords; a wrong mode is
    an information leak, not a cosmetic bug."""
    return (
        "# Managed by airuleset (#841) — btmp weekly + compress + rotate 4\n"
        "# (was monthly rotate 1 → up to ~450M of one file on disk at once).\n"
        "# OVERWRITES the distro file in place: a second logrotate stanza for\n"
        "# the same log errors 'duplicate log entry' and silently rotates\n"
        "# NOTHING. create mode 0660 root utmp preserved EXACTLY (btmp holds\n"
        "# failed-login usernames/mistyped passwords — a wrong mode leaks them).\n"
        "/var/log/btmp {\n"
        "    missingok\n"
        "    weekly\n"
        "    compress\n"
        "    delaycompress\n"
        "    notifempty\n"
        "    rotate %d\n"
        "    create 0660 root utmp\n"
        "}\n" % LOGROTATE_ROTATE
    )


def render_logrotate_wtmp():
    """wtmp logrotate config OVERWRITING the distro ``/etc/logrotate.d/wtmp``
    (weekly + compress + rotate 4). ``create 0664 root utmp`` preserved."""
    return (
        "# Managed by airuleset (#841) — wtmp weekly + compress + rotate 4.\n"
        "# OVERWRITES the distro file in place (duplicate-stanza fail-open).\n"
        "/var/log/wtmp {\n"
        "    missingok\n"
        "    weekly\n"
        "    compress\n"
        "    delaycompress\n"
        "    notifempty\n"
        "    rotate %d\n"
        "    create 0664 root utmp\n"
        "}\n" % LOGROTATE_ROTATE
    )


def render_journald_cap():
    """journald ``SystemMaxUse`` cap drop-in — journald's OWN rotation (config
    cap, not a runtime delete). May drop older journal history on FIRST apply
    (intentional, ROTATION by owner rule — disclosed in the PR)."""
    return (
        "# Managed by airuleset (#841) — cap the system journal. journald's OWN\n"
        "# rotation (config-driven); journald re-reads this ONLY at (re)start, so\n"
        "# the apply script restarts systemd-journald before rotating (a bare\n"
        "# daemon-reload would leave the cap inert until a reboot). NOTE: first\n"
        "# application may drop older journal history (intentional, ROTATION by\n"
        "# owner rule).\n"
        "[Journal]\n"
        "SystemMaxUse=%s\n" % JOURNAL_MAX_USE
    )


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


def render_owner_ignoreip(ips=None):
    """Owner break-glass ``ignoreip`` (#982) — controller-only.

    Renders the ``60-airuleset-owner-ignoreip.conf`` drop-in with ONLY the
    owner's specific device IPs (never the whole CGNAT range). The ``60-``
    prefix ensures it loads AFTER the fleet-wide ``50-`` hardening. ``ips``
    defaults to ``cli_fleet.OWNER_BREAK_GLASS_IPS`` (late-bound to avoid a
    circular import at module level)."""
    if ips is None:
        import cli_fleet
        ips = cli_fleet.OWNER_BREAK_GLASS_IPS
    ip_str = " ".join(["127.0.0.1/8", "::1"] + list(ips))
    return (
        "# Managed by airuleset (#982) — owner break-glass ignoreip.\n"
        "# Controller-only. Specific owner device IPs; NEVER the whole\n"
        "# tailscale CGNAT range (the fleet-wide 50-* already covers that\n"
        "# for the recidive jail; this is the narrow break-glass path).\n"
        "[DEFAULT]\n"
        "ignoreip = %s\n" % ip_str
    )


def render_sshd_password_conf(user="airuleset"):
    """Render the ``60-airuleset-password.conf`` sshd drop-in (#985).

    Enables ``PasswordAuthentication yes`` and ``KbdInteractiveAuthentication yes``
    ONLY for the specified user via a ``Match User`` block.  The global
    ``PasswordAuthentication no`` in ``50-airuleset-hardening.conf`` is untouched.

    MUST be byte-identical to the live file the supervisor already applied."""
    return (
        "# airuleset owner directive 2026-09-10: password login for the "
        "%s account only (break-glass from any device); "
        "fail2ban sshd jail guards it\n"
        "Match User %s\n"
        "    PasswordAuthentication yes\n"
        "    KbdInteractiveAuthentication yes\n"
        % (user, user)
    )


def render_tmpfiles():
    """tmpfiles.d drop-in that (re)creates the world-readable ``/run/airuleset``
    report dir on boot — decoupled from the oneshot service (a Type=oneshot's
    ``RuntimeDirectory=`` is removed when the unit exits)."""
    return (
        "# Managed by airuleset (#841) — the world-readable disk-guard root\n"
        "# report dir (tmpfs; cleared on reboot by design — a tmpfs report can\n"
        "# never itself add to disk pressure). Decoupled from the oneshot\n"
        "# service, whose RuntimeDirectory= would be removed on unit exit.\n"
        "d %s 0755 root root -\n" % RUN_DIR
    )


def render_reporter_script():
    """The REPORT-ONLY root reporter (bash). Sizes the root-owned reclaimable
    candidates and writes them to ``/run/airuleset/disk-guard-root.json``
    atomically. Contains NO destructive verb (``rm``/``prune``/``clean``/
    ``--vacuum``/``delete``) — asserted at test time; it only READS with ``du``
    and ``journalctl --disk-usage`` is deliberately NOT used (a plain ``du`` of
    ``/var/log/journal`` is the byte source). ``set -uo pipefail`` (NOT ``-e``:
    a ``du`` on a missing path must not abort the whole report)."""
    return (
        "#!/bin/bash\n"
        "# Managed by airuleset (#841) — REPORT-ONLY root disk-candidate\n"
        "# reporter. SURFACES root-owned reclaimable candidate sizes; NEVER\n"
        "# deletes/rotates/prunes anything. Written to a world-readable tmpfs\n"
        "# JSON the per-user watchdog reads (it has no root, cannot du these).\n"
        "set -uo pipefail\n"
        "run_dir=%s\n"
        "out=%s\n"
        "# scan-root PREFIX — empty in production (real /var, /tmp, /home, /opt);\n"
        "# a test/dev set it to a seeded fixture tree so the du set is tiny +\n"
        "# hermetic (never du's the developer's live disk). Report-only either way.\n"
        "r=\"${AIRULESET_DGROOT_SCAN_ROOT:-}\"\n"
        "mkdir -p \"$run_dir\" 2>/dev/null || true\n"
        "chmod 0755 \"$run_dir\" 2>/dev/null || true\n"
        "now=$(date -u +%%s)\n"
        "gen=$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)\n"
        "# bytes, one filesystem, 0 on any error (report-only, never fails hard)\n"
        "_du() { local b; b=$(du -sxb \"$1\" 2>/dev/null | awk 'NR==1{print $1+0}'); "
        "echo \"${b:-0}\"; }\n"
        "apt=$(_du \"$r/var/cache/apt\")\n"
        "jrn=$(_du \"$r/var/log/journal\")\n"
        "vlog=$(_du \"$r/var/log\")\n"
        "# gate on the DIRECTORY, not the docker binary: a CI runner / seeded test\n"
        "# tree has the path without the binary (main run 33635218286 failed on\n"
        "# exactly that), and _du of a missing path is already a safe 0.\n"
        "dkr=0; [ -d \"$r/var/lib/docker\" ] && dkr=$(_du \"$r/var/lib/docker\")\n"
        "ghr=0\n"
        "for d in \"$r\"/home/*/actions-runner*/_work \"$r\"/opt/actions-runner*/_work "
        "\"$r\"/home/*/_work; do\n"
        "    [ -d \"$d\" ] && ghr=$((ghr + $(_du \"$d\")))\n"
        "done\n"
        "otmp=$(_du \"$r/tmp\")\n"
        "# estimate = the CLEARLY-reclaimable set ONLY (apt cache re-fills on\n"
        "# next apt; gh-runner _work is CI scratch). docker is DELIBERATELY\n"
        "# EXCLUDED from the ask estimate — /var/lib/docker holds named volumes +\n"
        "# container writable layers (PERSISTENT data), so summing it would\n"
        "# overstate the safe reclaim; it is SURFACED as a candidate (labelled as\n"
        "# incl. volumes) for the session to judge, never a `prune --volumes`\n"
        "# invitation. journal/var-log are rotation-managed by the caps we\n"
        "# install; /tmp ages out on its own — surfaced, not summed.\n"
        "est=$((apt + ghr))\n"
        "tmp=$(mktemp \"$run_dir/.report-XXXXXX\") || exit 0\n"
        "cat > \"$tmp\" <<JSON\n"
        "{\"generated_at\":\"$gen\",\"generated_ts\":$now,\"estimate_bytes\":$est,"
        "\"candidates\":[\n"
        "{\"cls\":\"apt-cache\",\"path\":\"/var/cache/apt\",\"bytes\":$apt},\n"
        "{\"cls\":\"docker-incl-volumes\",\"path\":\"/var/lib/docker\",\"bytes\":$dkr},\n"
        "{\"cls\":\"gh-runner\",\"path\":\"actions-runner/_work\",\"bytes\":$ghr},\n"
        "{\"cls\":\"journal\",\"path\":\"/var/log/journal\",\"bytes\":$jrn},\n"
        "{\"cls\":\"var-log\",\"path\":\"/var/log\",\"bytes\":$vlog},\n"
        "{\"cls\":\"tmp\",\"path\":\"/tmp\",\"bytes\":$otmp}]}\n"
        "JSON\n"
        "chmod 0644 \"$tmp\" 2>/dev/null || true\n"
        "mv -f \"$tmp\" \"$out\"\n"
        % (shlex.quote(RUN_DIR), shlex.quote(ROOT_REPORT_PATH))
    )


def render_root_service():
    """The oneshot service the daily timer triggers — runs the reporter."""
    return (
        "# Managed by airuleset (#841) — REPORT-ONLY root disk-candidate\n"
        "# reporter (surfaces root-owned reclaimable candidates; never deletes).\n"
        "[Unit]\n"
        "Description=airuleset disk-guard root report (report-only, #841)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/bin/bash %s\n" % REPORTER_SCRIPT_PATH
    )


def render_root_timer():
    """The daily timer. ``Persistent=true`` catches a missed run after a reboot
    (closing the ``/run`` tmpfs reset gap); ``OnBootSec`` seeds the report soon
    after boot so a session never reads an absent report post-reboot."""
    return (
        "# Managed by airuleset (#841) — daily root disk-candidate report so\n"
        "# the owner-daily ❓ always has a FRESH root estimate. Persistent=true\n"
        "# + OnBootSec close the /run tmpfs reboot-reset gap.\n"
        "[Unit]\n"
        "Description=airuleset disk-guard root report timer (#841)\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=10min\n"
        "OnCalendar=daily\n"
        "Persistent=true\n"
        "Unit=airuleset-disk-guard-root.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def render_drain_script():
    """The root-side DRAIN script (#895). Acts ONLY on clearly-reclaimable
    root-scope candidates the per-user guard cannot reach. Each rung is
    bounded, provenance-proven, and logged to ``DRAIN_LOG_PATH``. Rungs:

    (a) ``/tmp`` regular files with mtime > TMP_MAX_AGE_DAYS (safe: tmpfiles.d
        semantics — transient scratch, no user data).
    (b) Old playwright browser revisions across ALL accounts (non-paused): keep
        the NEWEST revision per family (e.g. chromium, chromium_headless_shell)
        per account's ``.cache/ms-playwright/``, delete the rest. Paused accounts
        (#851) are SKIPped entirely.
    (c) npm ``_cacache`` + pip cache of OTHER accounts (never the calling user's
        own — they manage it themselves): files older than CACHE_MAX_AGE_DAYS.
    (d) Redundant pull ``.tgz`` archives: a ``.tgz`` file older than
        TGZ_MAX_AGE_HOURS with a same-stem directory present alongside → the
        archive is redundant (the extraction succeeded). Scans /home/*/
        directories only.

    The script uses ``set -uo pipefail`` (NOT ``-e``: a ``find`` on a missing
    path must not abort). Every action is logged. ``AIRULESET_DGROOT_DRAIN_DRY``
    env var makes it log-only (for testing)."""
    # Build the list of paused account names for the skip check
    paused_list = " ".join(shlex.quote(a) for a in PAUSED_ACCOUNTS)
    return (
        "#!/bin/bash\n"
        "# Managed by airuleset (#895) — root-side drain ladder.\n"
        "# Acts ONLY on clearly-reclaimable cross-user/root candidates.\n"
        "# NEVER deletes user data, docker volumes, or the journal.\n"
        "set -uo pipefail\n"
        "log=%s\n"
        "dry=${AIRULESET_DGROOT_DRAIN_DRY:-}\n"
        "paused=(%s)\n"
        "_is_paused() { local u=\"$1\"; local p; for p in \"${paused[@]}\"; do "
        "[ \"$p\" = \"$u\" ] && return 0; done; return 1; }\n"
        "_log() { echo \"$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ) $*\" >> \"$log\"; }\n"
        "_act() {\n"
        "    # F1 review fix: TOCTOU symlink-swap guard — refuse to delete a\n"
        "    # symlink (a user-writable dir can swap a real entry for a symlink\n"
        "    # to /usr/lib or another account between plan and act).\n"
        "    if [ -L \"$1\" ]; then\n"
        "        _log \"SKIP-SYMLINK $1 ($2) — refusing to delete a symlink\"\n"
        "        return 0\n"
        "    fi\n"
        "    if [ -n \"$dry\" ]; then\n"
        "        _log \"WOULD-DELETE $1 ($2)\"\n"
        "    else\n"
        "        rm -rf --one-file-system -- \"$1\" 2>/dev/null && "
        "_log \"DELETED $1 ($2)\" || _log \"FAIL $1 ($2)\"\n"
        "    fi\n"
        "}\n"
        "mkdir -p %s 2>/dev/null || true\n"
        "touch \"$log\" 2>/dev/null || true\n"
        "\n"
        "# --- rung (a): /tmp regular files mtime +%dd ---\n"
        "_log \"RUNG-A /tmp regular files mtime +%d days\"\n"
        "find /tmp -maxdepth 1 -type f -mtime +%d -print0 2>/dev/null | "
        "while IFS= read -r -d '' f; do _act \"$f\" \"tmp-stale\"; done\n"
        "\n"
        "# --- rung (b): old playwright versions per-family per-account ---\n"
        "_log \"RUNG-B playwright browser revisions\"\n"
        "for home in /home/*; do\n"
        "    [ -d \"$home\" ] || continue\n"
        "    u=$(basename \"$home\")\n"
        "    _is_paused \"$u\" && { _log \"SKIP $home (paused #851)\"; continue; }\n"
        "    pwdir=\"$home/.cache/ms-playwright\"\n"
        "    [ -d \"$pwdir\" ] || continue\n"
        "    # Group by family (everything before the last -<digits> segment)\n"
        "    declare -A newest_rev\n"
        "    declare -A newest_dir\n"
        "    for d in \"$pwdir\"/*/; do\n"
        "        [ -d \"$d\" ] || continue\n"
        "        bn=$(basename \"$d\")\n"
        "        # Extract family and revision: family-revision\n"
        "        if [[ \"$bn\" =~ ^(.+)-([0-9]+)$ ]]; then\n"
        "            fam=\"${BASH_REMATCH[1]}\"\n"
        "            rev=\"${BASH_REMATCH[2]}\"\n"
        "            cur=${newest_rev[$fam]:-0}\n"
        "            if [ \"$rev\" -gt \"$cur\" ]; then\n"
        "                newest_rev[$fam]=$rev\n"
        "                newest_dir[$fam]=\"$d\"\n"
        "            fi\n"
        "        fi\n"
        "    done\n"
        "    for d in \"$pwdir\"/*/; do\n"
        "        [ -d \"$d\" ] || continue\n"
        "        bn=$(basename \"$d\")\n"
        "        if [[ \"$bn\" =~ ^(.+)-([0-9]+)$ ]]; then\n"
        "            fam=\"${BASH_REMATCH[1]}\"\n"
        "            rev=\"${BASH_REMATCH[2]}\"\n"
        "            kept=${newest_rev[$fam]:-0}\n"
        "            if [ \"$rev\" -lt \"$kept\" ]; then\n"
        "                # F4 review fix: keep revisions younger than 30d (may\n"
        "                # be in use by a pinned playwright version)\n"
        "                age_d=$(( ( $(date -u +%%s) - $(stat -c %%Y \"${d%%/}\" 2>/dev/null || echo 0) ) / 86400 ))\n"
        "                if [ \"$age_d\" -lt 30 ]; then\n"
        "                    _log \"SKIP ${d%%/} playwright $fam rev $rev — younger than 30d\"\n"
        "                    continue\n"
        "                fi\n"
        "                _act \"${d%%/}\" \"playwright-old $fam rev $rev < $kept age=${age_d}d\"\n"
        "            fi\n"
        "        fi\n"
        "    done\n"
        "    unset newest_rev newest_dir\n"
        "done\n"
        "\n"
        "# --- rung (c): npm/pip cache of other accounts ---\n"
        "_log \"RUNG-C npm/pip cache stale files\"\n"
        "for home in /home/*; do\n"
        "    [ -d \"$home\" ] || continue\n"
        "    u=$(basename \"$home\")\n"
        "    _is_paused \"$u\" && continue\n"
        "    for cdir in \"$home/.npm/_cacache\" \"$home/.cache/pip\"; do\n"
        "        [ -d \"$cdir\" ] || continue\n"
        "        find \"$cdir\" -type f -mtime +%d -print0 2>/dev/null | "
        "while IFS= read -r -d '' f; do _act \"$f\" \"cache-stale\"; done\n"
        "    done\n"
        "done\n"
        "\n"
        "# --- rung (d): redundant pull tgz archives (F2 review: skip paused) ---\n"
        "_log \"RUNG-D redundant pull tgz archives\"\n"
        "for home in /home/*; do\n"
        "    [ -d \"$home\" ] || continue\n"
        "    u=$(basename \"$home\")\n"
        "    _is_paused \"$u\" && { _log \"SKIP-D $home (paused #851)\"; continue; }\n"
        "    find \"$home\" -name '*.tgz' -type f -not -type l -mmin +%d "
        "-print0 2>/dev/null | "
        "while IFS= read -r -d '' f; do\n"
        "        stem=\"${f%%.tgz}\"\n"
        "        if [ -d \"$stem\" ]; then\n"
        "            _act \"$f\" \"tgz-redundant extracted=$stem\"\n"
        "        fi\n"
        "    done\n"
        "done\n"
        "\n"
        "_log \"DRAIN COMPLETE\"\n"
        % (shlex.quote(DRAIN_LOG_PATH),
           paused_list,
           shlex.quote(RUN_DIR),
           TMP_MAX_AGE_DAYS, TMP_MAX_AGE_DAYS, TMP_MAX_AGE_DAYS,
           CACHE_MAX_AGE_DAYS,
           TGZ_MAX_AGE_HOURS * 60)
    )


def render_drain_service():
    """The oneshot service the drain timer triggers (#895)."""
    return (
        "# Managed by airuleset (#895) — root-side drain ladder.\n"
        "# Removes clearly-reclaimable cross-user/root candidates.\n"
        "[Unit]\n"
        "Description=airuleset disk-guard root drain (#895)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/bin/bash %s\n" % DRAIN_SCRIPT_PATH
    )


def render_drain_timer():
    """The 4-hourly drain timer (#895). ``Persistent=true`` catches missed runs
    after a reboot."""
    return (
        "# Managed by airuleset (#895) — root-side drain ladder timer.\n"
        "[Unit]\n"
        "Description=airuleset disk-guard root drain timer (#895)\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=30min\n"
        "OnUnitActiveSec=4h\n"
        "Persistent=true\n"
        "Unit=airuleset-disk-guard-root-drain.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def guard_files():
    """The (path, content, mode) triples the apply script installs, in write
    order. The reporter script is 0755 (executable); everything else 0644.
    fail2ban is installed CONDITIONALLY by the apply script (only where
    ``fail2ban-client`` exists) — a workstation box with no fail2ban is left
    clean — so it is NOT in this always-install list."""
    return [
        (LOGROTATE_BTMP_PATH, render_logrotate_btmp(), "0644"),
        (LOGROTATE_WTMP_PATH, render_logrotate_wtmp(), "0644"),
        (JOURNALD_CAP_PATH, render_journald_cap(), "0644"),
        (TMPFILES_PATH, render_tmpfiles(), "0644"),
        (REPORTER_SCRIPT_PATH, render_reporter_script(), "0755"),
        (ROOT_SERVICE_PATH, render_root_service(), "0644"),
        (ROOT_TIMER_PATH, render_root_timer(), "0644"),
        # #895 — root-side drain ladder
        (DRAIN_SCRIPT_PATH, render_drain_script(), "0755"),
        (DRAIN_SERVICE_PATH, render_drain_service(), "0644"),
        (DRAIN_TIMER_PATH, render_drain_timer(), "0644"),
    ]


# The heredoc terminator for embedding a drop-in body inside the apply script.
# QUOTED (`<<'...'`) so the body is written verbatim (no shell expansion — the
# reporter body itself carries `$` and heredocs), and distinctive so no body
# can contain it.
_HEREDOC_MARK = "AIRULESET_DGROOT_EOF"


def build_apply_script():
    """A ``bash`` script (run as ``bash -c <script>`` over ``ssh root@<host>``,
    so its stdin stays free for the heredocs) that idempotently installs every
    drop-in atomically, installs the fail2ban hardening ONLY where fail2ban
    exists, reloads systemd, enables + starts the daily report timer, runs the
    reporter ONCE (bounded ``timeout`` so a slow ``du`` can never stall the
    push), and READS BACK the result (journald cap present, logrotate free of
    the duplicate-entry fail-open, timer enabled, report JSON parseable) →
    mismatch exits 4, turning the push step LOUD. Pure string builder."""
    parts = []
    parts.append("set -uo pipefail")
    parts.append("")
    # Atomic idempotent installer: content on stdin (a quoted heredoc), written
    # to a dotted mktemp in the destination dir and mv'd into place only once
    # fully written + chmod'd — the same never-a-truncating-write discipline as
    # #659/#775. Takes an explicit mode (the reporter is 0755).
    parts.append(
        '_install() {\n'
        '    dest="$1"; mode="$2"; dir=$(dirname "$dest")\n'
        '    mkdir -p "$dir"\n'
        '    tmp=$(mktemp "$dir/.airuleset-dgroot-XXXXXX")\n'
        "    trap 'rm -f \"$tmp\"' EXIT\n"
        '    cat > "$tmp"\n'
        '    chmod "$mode" "$tmp"\n'
        '    mv -f "$tmp" "$dest"\n'
        '    trap - EXIT\n'
        '    echo "  disk-guard-root: wrote $dest"\n'
        '}'
    )
    parts.append("")
    for path, content, mode in guard_files():
        parts.append(
            "_install %s %s <<'%s'\n%s%s"
            % (shlex.quote(path), shlex.quote(mode), _HEREDOC_MARK,
               content.rstrip("\n") + "\n", _HEREDOC_MARK)
        )
    parts.append("")
    # fail2ban hardening ONLY where fail2ban is installed (a box without it is
    # left clean, and the read-back below never fails for its absence).
    parts.append(
        'if command -v fail2ban-client >/dev/null 2>&1; then\n'
        "    _install %s 0644 <<'%s'\n%s%s\n"
        '    fail2ban-client reload >/dev/null 2>&1 '
        '|| echo "  ⚠ disk-guard-root: fail2ban reload failed (non-fatal)"\n'
        'else\n'
        '    echo "  disk-guard-root: fail2ban not present — hardening skipped"\n'
        'fi'
        % (shlex.quote(FAIL2BAN_JAIL_PATH), _HEREDOC_MARK,
           render_fail2ban_hardening().rstrip("\n") + "\n", _HEREDOC_MARK)
    )
    parts.append("")
    # Create the world-readable report dir now (tmpfiles.d covers subsequent
    # boots) and reload systemd so it re-reads the journald cap + the new units.
    parts.append(
        'systemd-tmpfiles --create %s >/dev/null 2>&1 || mkdir -p %s'
        % (shlex.quote(TMPFILES_PATH), shlex.quote(RUN_DIR))
    )
    parts.append("chmod 0755 %s 2>/dev/null || true" % shlex.quote(RUN_DIR))
    parts.append("systemctl daemon-reload")
    # The journald cap only takes effect once journald RE-READS journald.conf.d,
    # which it does ONLY at (re)start — `daemon-reload` reloads the systemd
    # MANAGER, not journald's own config, and `journalctl --rotate` alone would
    # vacuum to the OLD (running) limit until an unrelated reboot (the #618/#623
    # "deployed != effective" trap). So restart journald FIRST (it re-reads the
    # cap), THEN rotate (journald now vacuums to the new SystemMaxUse). Both are
    # rotation, not deletion of user data; both non-fatal.
    parts.append(
        'systemctl try-restart systemd-journald.service >/dev/null 2>&1 '
        '|| echo "  ⚠ disk-guard-root: systemd-journald restart failed (non-fatal)"'
    )
    parts.append(
        'journalctl --rotate >/dev/null 2>&1 '
        '|| echo "  ⚠ disk-guard-root: journalctl --rotate failed (non-fatal)"'
    )
    # Enable + start the daily report timer.
    parts.append(
        'systemctl enable --now airuleset-disk-guard-root.timer '
        '|| echo "  ⚠ disk-guard-root: report timer enable failed (non-fatal)"'
    )
    # #895: Enable + start the 4-hourly drain timer.
    parts.append(
        'systemctl enable --now airuleset-disk-guard-root-drain.timer '
        '|| echo "  ⚠ disk-guard-root: drain timer enable failed (non-fatal)"'
    )
    # Seed the report on day 0 — bounded so a slow `du` can NEVER stall the push.
    parts.append(
        'timeout %d /bin/bash %s '
        '|| echo "  ⚠ disk-guard-root: initial report run failed/timed out '
        '(non-fatal)"' % (REPORTER_TIMEOUT_S, shlex.quote(REPORTER_SCRIPT_PATH))
    )
    parts.append("")
    # --- READ-BACK VERIFY (fail-loud) -------------------------------------- #
    parts.append("fail=0")
    # 1. journald cap file present + carries the cap.
    parts.append(
        'if ! grep -q "SystemMaxUse=%s" %s 2>/dev/null; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: journald cap not written" >&2; '
        'fail=1\n'
        'fi' % (JOURNAL_MAX_USE, shlex.quote(JOURNALD_CAP_PATH))
    )
    # 2. logrotate config parses WITHOUT the duplicate-entry fail-open (the
    #    Fable-flagged silent no-rotation). `logrotate --debug` never rotates.
    #    The grep is unscoped (logrotate reports the offending path in the SAME
    #    line but not always parseably), so the message says "a logrotate
    #    duplicate log entry" rather than over-attributing to btmp/wtmp.
    parts.append(
        'if command -v logrotate >/dev/null 2>&1; then\n'
        '    if logrotate --debug /etc/logrotate.conf 2>&1 | grep -qi '
        '"duplicate log entry"; then\n'
        '        echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: a logrotate duplicate log '
        'entry — a log (likely btmp/wtmp) would silently NOT rotate; check '
        '/etc/logrotate.d for a second stanza" >&2; fail=1\n'
        '    fi\n'
        'fi'
    )
    # 3. the daily report timer is enabled.
    parts.append(
        'if ! systemctl is-enabled airuleset-disk-guard-root.timer '
        '>/dev/null 2>&1; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: report timer not enabled" '
        '>&2; fail=1\n'
        'fi'
    )
    # 4. (#895) the drain timer is enabled.
    parts.append(
        'if ! systemctl is-enabled airuleset-disk-guard-root-drain.timer '
        '>/dev/null 2>&1; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: drain timer not enabled" '
        '>&2; fail=1\n'
        'fi'
    )
    # NON-FATAL: the report JSON existing on day 0 depends on the bounded seed
    # run finishing; on a huge box (gk: tens of GB of du) that seed can time out
    # legitimately, and the DAILY timer produces the report within a day anyway.
    # So a missing/unparseable report is a WARNING, never exit 4 — the ROTATION
    # config + the enabled timer are the load-bearing install, the report is a
    # SURFACE the timer refreshes. (The owner-daily ❓ reads it once it exists;
    # the per-user guard's ≥90 % escalation is the standing backstop meanwhile.)
    parts.append(
        'if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" %s '
        '>/dev/null 2>&1; then\n'
        '    echo "  ⚠ disk-guard-root: report JSON not yet present/parseable at '
        '%s (seed may have timed out on a large box — the daily timer will '
        'produce it; non-fatal)"\n'
        'fi' % (shlex.quote(ROOT_REPORT_PATH), ROOT_REPORT_PATH)
    )
    parts.append(
        'if [ "$fail" -ne 0 ]; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT FAILED read-back verify" >&2\n'
        '    exit 4\n'
        'fi\n'
        'echo "  disk-guard-root: applied + verified (journald cap %s, btmp/wtmp '
        'rotation, report %s, drain %s)"'
        % (JOURNAL_MAX_USE, ROOT_REPORT_PATH, DRAIN_SCRIPT_PATH)
    )
    return "\n".join(parts) + "\n"


def provision_disk_guard_root(hosts=None, run=None, control_opts=None):
    """Apply the root/system-level disk-guard legs on every
    ``DISK_GUARD_ROOT_HOSTS`` target over ``ssh root@<host>``. Called by
    ``cmd_push`` AFTER the shared-stream resource-guard step.

    NON-FATAL + LOUD, exactly like `provision_shared_stream_guards` (#775): any
    failure (unreachable root, a not-yet-authorized operator key, a read-back
    mismatch) prints ``⚠ DISK-GUARD-ROOT FAILED (<name>)`` and is returned in
    the failure list — it never raises, so the rest of the push is unaffected.
    Until the dev1→root@<host> operator key is authorized this is a fail-loud
    no-op (the same shape #775 shipped with).

    ``hosts`` defaults (lazily, via the airuleset facade — the L-E convention)
    to ``airuleset.DISK_GUARD_ROOT_HOSTS``; ``run`` defaults to
    ``subprocess.run`` (injectable for tests). Returns a list of
    ``(name, reason)`` failures."""
    run = run or subprocess.run
    if hosts is None:
        import airuleset  # L-E: DISK_GUARD_ROOT_HOSTS lives in cli_fleet, via facade
        hosts = airuleset.DISK_GUARD_ROOT_HOSTS
    script = build_apply_script()
    remote_cmd = "bash -c " + shlex.quote(script)
    failed = []
    for h in hosts:
        name = h.get("name", h.get("host", "?"))
        host = h.get("host")
        identity = h.get("identity")
        if not host:
            print("  ⚠ DISK-GUARD-ROOT FAILED (%s): guard entry has no host."
                  % name, file=sys.stderr)
            failed.append((name, "no-host"))
            continue
        if not identity:
            print("  ⚠ DISK-GUARD-ROOT FAILED (%s): no pinned ssh identity — a "
                  "root apply must never ride a shared password." % name,
                  file=sys.stderr)
            failed.append((name, "no-identity"))
            continue
        ssh_prefix = [
            "ssh", "-i", os.path.expanduser(identity),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
        ]
        argv = ssh_prefix + list(control_opts or []) + [
            "%s@%s" % (h.get("admin_user", "root"), host), remote_cmd]
        try:
            r = run(argv, capture_output=True, text=True, timeout=180)
        except Exception as e:  # noqa: BLE001 — best-effort, never break push
            print("  ⚠ DISK-GUARD-ROOT FAILED (%s): ssh error (non-fatal): %r"
                  % (name, e), file=sys.stderr)
            failed.append((name, repr(e)))
            continue
        if getattr(r, "returncode", 1) != 0:
            print("  ⚠ DISK-GUARD-ROOT FAILED (%s) rc=%s: %s"
                  % (name, r.returncode, (r.stderr or "").strip()[:400]),
                  file=sys.stderr)
            failed.append((name, "rc=%s" % r.returncode))
        else:
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            print("  disk-guard-root: applied + verified on %s%s"
                  % (name, ("\n    " + out.replace("\n", "\n    ")) if out else ""))
    return failed


# ---------------------------------------------------------------------------
# #992/#993 item 9 — managed `swap` install step. The controller (4 GB, zero
# swap) OOM-killed the watchdog + a push's Pass B; a managed box with no swap
# gets a /swapfile sized = RAM (clamped [2 GB, 8 GB]). Idempotent (a box that
# already has swap is a no-op), needs `sudo -n` (clear skip line otherwise),
# LOCAL-only, non-fatal — the same shape as the other provisioning leaves.
# ---------------------------------------------------------------------------
SWAP_MIN_GB = 2
SWAP_MAX_GB = 8
SWAPFILE_PATH = "/swapfile"


def swap_size_gb(mem_total_kb):
    """RAM in GB, rounded, clamped to [SWAP_MIN_GB, SWAP_MAX_GB]."""
    try:
        ram_gb = int(round(int(mem_total_kb) / (1024 * 1024)))
    except (TypeError, ValueError):
        ram_gb = SWAP_MIN_GB
    return max(SWAP_MIN_GB, min(SWAP_MAX_GB, ram_gb or SWAP_MIN_GB))


def render_swap_setup_script(size_gb):
    """Idempotent bash creating a size_gb /swapfile: re-checks swap + the fstab
    line so a second run is a no-op. Uses fallocate, falling back to dd.

    #993-review hardening: (a) a FREE-SPACE check (need size + 2 GB headroom)
    before allocating — these boxes are drained at >=80% (#834); (b) a
    trailing-newline guard before the fstab append (an fstab whose last line
    lacks \\n would otherwise get the entry glued onto that mount line); (c) a
    blkid guard so mkswap never runs over a pre-existing NON-swap file at the
    path."""
    p = SWAPFILE_PATH
    need_kb = (size_gb + 2) * 1024 * 1024   # size + 2 GB headroom, in KB
    return (
        "set -euo pipefail\n"
        "if [ \"$(swapon --show=NAME --noheadings 2>/dev/null | wc -l)\" -gt 0 ]; then\n"
        "  echo 'swap already active — skip'; exit 0\n"
        "fi\n"
        "avail=$(df --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')\n"
        "if [ -n \"$avail\" ] && [ \"$avail\" -lt %(need)d ]; then\n"
        "  echo 'swap: skipped — insufficient free space on / (need %(needg)dG incl. headroom)'; exit 0\n"
        "fi\n"
        "if [ ! -f %(p)s ]; then\n"
        "  fallocate -l %(n)dG %(p)s || dd if=/dev/zero of=%(p)s bs=1M count=%(mb)d\n"
        "fi\n"
        "chmod 600 %(p)s\n"
        "ftype=$(blkid -o value -s TYPE %(p)s 2>/dev/null || true)\n"
        "if [ -n \"$ftype\" ] && [ \"$ftype\" != swap ]; then\n"
        "  echo \"swap: refusing — %(p)s already holds a $ftype filesystem\"; exit 1\n"
        "fi\n"
        "if ! swapon --show=NAME --noheadings 2>/dev/null | grep -qx %(p)s; then\n"
        "  mkswap %(p)s\n"
        "  swapon %(p)s\n"
        "fi\n"
        "if ! grep -qE '^%(p)s[[:space:]]' /etc/fstab; then\n"
        "  [ -z \"$(tail -c1 /etc/fstab 2>/dev/null)\" ] || printf '\\n' >> /etc/fstab\n"
        "  echo '%(p)s none swap sw 0 0' >> /etc/fstab\n"
        "fi\n"
        "echo 'swap: created %(n)dG at %(p)s'\n"
        % {"p": p, "n": size_gb, "mb": size_gb * 1024,
           "need": need_kb, "needg": size_gb + 2}
    )


def _read_meminfo(meminfo_text=None):
    """Return (mem_total_kb, swap_total_kb, swap_free_kb) from meminfo text
    (or /proc/meminfo). Missing/unreadable → all zero."""
    text = meminfo_text
    if text is None:
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            print("  swap: cannot read /proc/meminfo (%s)" % e, file=sys.stderr)
            return (0, 0, 0)
    vals = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0][:-1]
            try:
                vals[key] = int(parts[1])
            except ValueError:
                continue
    return (vals.get("MemTotal", 0), vals.get("SwapTotal", 0),
            vals.get("SwapFree", 0))


def _fmt_mb(kb):
    return "%dMB" % (kb // 1024)


def swap_status(meminfo_text=None):
    """`swap: <total>/<used>` line for `airuleset.py status` (0 when none)."""
    _mem, swap_total, swap_free = _read_meminfo(meminfo_text)
    if swap_total <= 0:
        return "swap: 0 (none)"
    used = max(0, swap_total - swap_free)
    return "swap: %s/%s" % (_fmt_mb(swap_total), _fmt_mb(used))


def provision_swap(run=None, meminfo_text=None):
    """Create a managed /swapfile on a box with no swap. Idempotent (present
    swap → no-op), sudo-`-n`-gated (clear skip line otherwise), non-fatal.
    Returns a status string for logging."""
    run = run or subprocess.run
    mem_total, swap_total, _free = _read_meminfo(meminfo_text)
    if swap_total > 0:
        return "swap: present (%s) — skip" % _fmt_mb(swap_total)
    # sudo gate — never prompt.
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001
        return "swap: skipped — sudo probe error (%r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return ("swap: skipped — sudo -n unavailable (create %s manually: "
                "fallocate/mkswap/swapon + /etc/fstab)" % SWAPFILE_PATH)
    size = swap_size_gb(mem_total)
    script = render_swap_setup_script(size)
    try:
        r = run(["sudo", "-n", "bash", "-c", script],
                capture_output=True, text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return "swap: FAILED (error: %r)" % e
    if getattr(r, "returncode", 1) != 0:
        return "swap: FAILED (rc=%s: %s)" % (
            r.returncode, (getattr(r, "stderr", "") or "").strip()[:200])
    return "swap: applied (%dG %s)" % (size, SWAPFILE_PATH)


# --------------------------------------------------------------------------- #
# #999 -- managed `volume` provisioning step. The gk box (38 GB disk) runs 7
# self-hosted runners, docker, and ~/.cache on a permanently-near-full root
# disk, so the #993 swap step keeps hitting its free-space guard. Owner
# (2026-09-12): use the attached-but-UNMOUNTED 20 GB Hetzner volume gk-vol1
# instead of paying for a bigger VPS -- mount it and RELOCATE those dirs onto
# it to free the root disk. Same layer/shape as `provision_swap`: a per-box
# `volume` declaration in cli_fleet.REMOTE_HOSTS drives an idempotent render ->
# `sudo -n` apply -> status row; a box with no `volume` key is a no-op line.
#
# Safety (owner: "NIKDY NESMIE MAINTENANCE POSKODIT beziacu robotu"): NEVER
# swapoff; NEVER delete an original (moved aside as `<orig>.relocated-<date>`,
# removed later by the OWNER, never by the script); runners ONE AT A TIME (at
# most one down at any instant, abort the whole loop on a failed is-active);
# docker only when 0 containers run; fstab `nofail` so a missing volume never
# blocks boot; idempotent (a second apply is all no-op lines). This module
# only RENDERS + applies via injected `run`; the gk box itself is provisioned
# by the gk-infra window (odoo-erp#6989, owner present), never by this code
# path in a worktree lane.
# ---------------------------------------------------------------------------


def render_volume_setup_script(decl, home=None, root=""):
    """Idempotent bash (`set -euo pipefail`) that (1) adds an fstab entry for
    the by-id device with `defaults,nofail,discard` if absent, (2) mounts the
    volume if it is not already a mountpoint (aborting relocation if it still
    is not mounted -- never relocate onto the root disk), and (3) relocates
    each `relocate` entry onto the volume ONLY IF not already relocated.

    Relocation is dispatched by kind:
      * `/var/lib/docker` -> rewrite docker `data-root` in daemon.json + restart
        docker, but ONLY when 0 containers run (never restart under load);
      * `/home/gh-runner` -> per `actions-runner*` subdir, ONE AT A TIME:
        stop its unit, rsync, mv aside, symlink, start, verify is-active
        (abort the whole loop on failure);
      * any other dir -> rsync to `<mount>/<basename>`, mv aside, symlink.

    `home` expands a leading `~/` (the invoking user's home; injectable for
    tests). `root` prefixes the system paths -- "" in production, a temp dir
    in tests so the rendered script runs hermetically with stub commands. Pure
    renderer: no side effects."""
    by_id = decl["by_id"]
    mount = decl["mount"]
    relocate = decl.get("relocate", [])
    if home is None:
        home = os.path.expanduser("~")
    dev = "/dev/disk/by-id/" + by_id
    r = root
    fstab = r + "/etc/fstab"
    mnt = r + mount
    lines = []
    a = lines.append
    a("set -euo pipefail")
    a("_vol_date=$(date +%Y%m%d-%H%M%S)")
    # 1. fstab entry (nofail so a missing volume never blocks boot).
    a("if ! grep -qE '^%s[[:space:]]' %s 2>/dev/null; then" % (dev, fstab))
    a("  [ -z \"$(tail -c1 %s 2>/dev/null)\" ] || printf '\\n' >> %s" % (fstab, fstab))
    a("  echo '%s %s ext4 defaults,nofail,discard 0 2' >> %s" % (dev, mnt, fstab))
    a("  echo 'volume: added fstab entry for %s'" % mount)
    a("fi")
    # 2. mkdir + mount (only if not already a mountpoint); abort if unmounted.
    a("mkdir -p %s" % mnt)
    a("if ! findmnt %s >/dev/null 2>&1; then" % mnt)
    a("  mount %s || true" % mnt)
    a("fi")
    a("if ! findmnt %s >/dev/null 2>&1; then" % mnt)
    a("  echo 'volume: %s not mounted — skipping relocation'; exit 0" % mount)
    a("fi")
    a("echo 'volume: %s mounted'" % mount)
    # 3. relocations, one entry at a time, idempotent.
    for entry in relocate:
        if entry.startswith("~/"):
            expanded = home + entry[1:]
        elif entry == "~":
            expanded = home
        else:
            expanded = entry
        orig = r + expanded
        base = os.path.basename(expanded.rstrip("/"))
        target = mnt + "/" + base
        if expanded == "/var/lib/docker":
            a("# relocate docker data-root (only with 0 running containers)")
            a("if [ -d '%s' ] && [ ! -e '%s' ]; then" % (target, orig))
            a("  echo 'volume: docker already relocated -> %s — skip'" % target)
            a("elif ! command -v docker >/dev/null 2>&1; then")
            a("  echo 'volume: docker absent — skip'")
            a("else")
            a("  _droot=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)")
            a("  _running=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ' || true)")
            a("  if [ \"$_droot\" = '%s' ]; then" % target)
            a("    echo 'volume: docker already at %s — skip'" % target)
            a("  elif [ \"${_running:-0}\" != '0' ]; then")
            a("    echo \"volume: docker relocation SKIPPED — ${_running} container(s) running\"")
            a("  else")
            # recovery trap: if the migration dies after stopping docker but
            # before the restart, bring docker back so running work is never
            # left down (cleared once the restart has completed).
            a("    trap 'echo \"volume: docker relocation FAILED mid-flight — restarting docker to protect running work\"; systemctl start docker.socket docker.service 2>/dev/null || true' EXIT")
            # stop docker.socket BEFORE docker.service — a listening socket
            # socket-activates (restarts) docker mid-rsync if any client
            # touches it, corrupting the copy.
            a("    systemctl stop docker.socket || true")
            a("    systemctl stop docker.service || true")
            a("    mkdir -p '%s'" % target)
            a("    rsync -aHAX '%s/' '%s/'" % (orig, target))
            a("    mkdir -p '%s/etc/docker'" % r)
            a("    python3 - '%s/etc/docker/daemon.json' '%s' <<'PYEOF'" % (r, target))
            a("import json, os, sys")
            a("p, dataroot = sys.argv[1], sys.argv[2]")
            a("try:")
            a("    with open(p) as f: d = json.load(f)")
            a("    if not isinstance(d, dict): d = {}")
            a("except Exception: d = {}")
            a("d['data-root'] = dataroot")
            a("os.makedirs(os.path.dirname(p), exist_ok=True)")
            a("with open(p, 'w') as f: json.dump(d, f, indent=2)")
            a("PYEOF")
            a("    mv '%s' '%s.relocated-'\"$_vol_date\"" % (orig, orig))
            a("    systemctl restart docker.socket docker.service")
            a("    trap - EXIT")
            a("    echo 'volume: docker data-root -> %s'" % target)
            a("  fi")
            a("fi")
        elif expanded == "/home/gh-runner":
            a("# relocate self-hosted runners — ONE AT A TIME (never all down)")
            a("mkdir -p '%s'" % target)
            # recovery trap: if a runner is stopped and the copy/start then
            # fails, restart that runner so it is never left down.
            a("_stopped_unit=''")
            a("trap 'if [ -n \"${_stopped_unit:-}\" ]; then echo \"volume: runner relocation FAILED mid-flight — restarting $_stopped_unit to protect running work\"; systemctl start \"$_stopped_unit\" 2>/dev/null || true; fi' EXIT")
            a("for _rd in '%s'/actions-runner*/; do" % orig)
            a("  [ -e \"$_rd\" ] || continue")
            a("  _rd=\"${_rd%/}\"")
            a("  _rbase=$(basename \"$_rd\")")
            a("  case \"$_rbase\" in *.relocated-*) continue;; esac")
            a("  _rtarget='%s'/\"$_rbase\"" % target)
            a("  if [ -L \"$_rd\" ] && [ -d \"$_rtarget\" ]; then")
            a("    echo \"volume: runner $_rbase already relocated — skip\"; continue")
            a("  fi")
            a("  _unit=''")
            a("  [ -f \"$_rd/.service\" ] && _unit=$(cat \"$_rd/.service\")")
            a("  if [ -n \"$_unit\" ]; then systemctl stop \"$_unit\" || true; _stopped_unit=\"$_unit\"; fi")
            a("  rsync -aHAX \"$_rd/\" \"$_rtarget/\"")
            a("  mv \"$_rd\" \"$_rd.relocated-$_vol_date\"")
            a("  ln -s \"$_rtarget\" \"$_rd\"")
            a("  if [ -n \"$_unit\" ]; then")
            a("    systemctl start \"$_unit\"")
            a("    if ! systemctl is-active --quiet \"$_unit\"; then")
            a("      echo \"volume: runner $_rbase FAILED is-active after start — aborting\"; exit 1")
            a("    fi")
            a("  fi")
            a("  _stopped_unit=''")   # this runner is safely back up
            a("  echo \"volume: runner $_rbase relocated -> $_rtarget\"")
            a("done")
            a("trap - EXIT")
        else:
            a("# relocate %s" % base)
            a("if [ -L '%s' ] && [ -d '%s' ]; then" % (orig, target))
            a("  echo 'volume: %s already relocated — skip'" % base)
            a("elif [ -e '%s' ]; then" % orig)
            a("  mkdir -p '%s'" % target)
            a("  rsync -aHAX '%s/' '%s/'" % (orig, target))
            a("  mv '%s' '%s.relocated-'\"$_vol_date\"" % (orig, orig))
            a("  ln -s '%s' '%s'" % (target, orig))
            a("  echo 'volume: %s relocated -> %s'" % (base, target))
            a("else")
            a("  echo 'volume: %s absent — skip'" % base)
            a("fi")
    return "\n".join(lines) + "\n"


def _local_volume_decl(hosts=None, user=None):
    """This box's `volume` declaration from cli_fleet.REMOTE_HOSTS, matched by
    the invoking UNIX account (`user` == entry `user`; pw_name from the uid,
    unspoofable — the #839 identity source), or None. Local `import cli_fleet`
    keeps this module a pure leaf (no module-level coupling)."""
    if hosts is None:
        try:
            import cli_fleet
            hosts = cli_fleet.REMOTE_HOSTS
        except Exception as e:  # noqa: BLE001
            print("  volume: cli_fleet import failed (%r)" % e, file=sys.stderr)
            return None
    if user is None:
        try:
            import pwd
            user = pwd.getpwuid(os.getuid()).pw_name
        except Exception as e:  # noqa: BLE001
            print("  volume: cannot resolve pw_name (%r)" % e, file=sys.stderr)
            return None
    for h in hosts:
        if h.get("user") == user and isinstance(h.get("volume"), dict):
            return h["volume"]
    return None


def _count_relocated(decl, islink=None, isdir=None, home=None):
    """How many of `decl`'s relocate entries are already on the volume — a
    symlinked generic dir (orig is a symlink AND its target dir exists), or a
    gh-runner/docker whose target dir exists. Best-effort, for the status
    row; `islink`/`isdir`/`home` are injectable for tests."""
    islink = islink or os.path.islink
    isdir = isdir or os.path.isdir
    if home is None:
        home = os.path.expanduser("~")
    mount = decl.get("mount", "")
    n = 0
    for entry in decl.get("relocate", []):
        if entry.startswith("~/"):
            expanded = home + entry[1:]
        elif entry == "~":
            expanded = home
        else:
            expanded = entry
        base = os.path.basename(expanded.rstrip("/"))
        target = os.path.join(mount, base)
        if expanded in ("/var/lib/docker", "/home/gh-runner"):
            if isdir(target):
                n += 1
        elif islink(expanded) and isdir(target):
            n += 1
    return n


def volume_status(decl=None, run=None):
    """`volume: <mount> <used>/<size> (<n> relocated)` line for
    `airuleset.py status` (or `volume: none (no declaration)` off a
    volume box). Reads used/size via `df -h` on the mount."""
    run = run or subprocess.run
    if decl is None:
        decl = _local_volume_decl()
    if not decl:
        return "volume: none (no declaration)"
    mount = decl.get("mount", "?")
    used = size = "?"
    try:
        r = run(["df", "-h", "--output=used,size", mount],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) == 0:
            body = (getattr(r, "stdout", "") or "").strip().splitlines()
            if len(body) >= 2:
                parts = body[1].split()
                if len(parts) >= 2:
                    used, size = parts[0], parts[1]
    except Exception as e:  # noqa: BLE001
        print("  volume: df read failed (%r)" % e, file=sys.stderr)
    n = _count_relocated(decl)
    return "volume: %s %s/%s (%d relocated)" % (mount, used, size, n)


def volume_install_status(decl=None, run=None, islink=None, isdir=None,
                          home=None):
    """The install-time volume line (#999 MAIN REVIEW blocker). `install` is
    idempotent CONFIGURATION and NEVER moves data or stops services — this
    PRINTS state/plan, it NEVER runs the relocation. Cases:

      * no declaration           -> `volume: none (no declaration)`
      * declared, not yet applied -> `volume: declared, not applied — run:
                                      python3 airuleset.py volume --apply`
      * fully applied            -> the normal `volume_status` row.

    PURE: only READS state (a `df` via `run` for the applied row, path stat via
    islink/isdir for the relocated count) — never `sudo`/`bash`/`mount`/`rsync`/
    `systemctl stop`. The actual mount + relocation is the EXPLICIT operator
    command `airuleset.py volume --apply` (see cmd_volume / provision_volume),
    run by the gk-infra window with the owner present (odoo-erp#6989)."""
    if decl is None:
        decl = _local_volume_decl()
    if not decl:
        return "volume: none (no declaration)"
    relocate = decl.get("relocate", [])
    done = _count_relocated(decl, islink=islink, isdir=isdir, home=home)
    # "Fully applied" is a BEST-EFFORT signal, not a guarantee (review F2):
    # _count_relocated proves a generic dir relocated via a real symlink
    # (robust), but for docker/gh-runner it only checks the target dir exists,
    # which the render script `mkdir -p`s before moving data — so a partial,
    # in-progress apply could over-count. That window is transient and happens
    # in the observed gk-infra window; the only effect here is WHICH status
    # line install prints (never relocation), and `airuleset.py volume --plan`
    # renders the authoritative state (findmnt + the full script). An empty
    # `relocate` (mount-only decl) is `0 >= 0` → status row by design.
    if done >= len(relocate):
        return volume_status(decl=decl, run=run)
    return ("volume: declared, not applied — run: "
            "python3 airuleset.py volume --apply")


def provision_volume(run=None, decl=None):
    """Mount + relocate onto this box's declared volume. Idempotent (a
    fully-relocated box → all no-op lines), sudo-`-n`-gated (a clear skip
    line otherwise), non-fatal. No declaration → no-op skip. Returns a
    status string for logging."""
    run = run or subprocess.run
    if decl is None:
        decl = _local_volume_decl()
    if not decl:
        return "volume: no declaration — skip"
    # sudo gate — never prompt.
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001
        return "volume: skipped — sudo probe error (%r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return ("volume: skipped — sudo -n unavailable (mount %s + relocate "
                "manually)" % decl.get("mount", "?"))
    script = render_volume_setup_script(decl)
    try:
        r = run(["sudo", "-n", "bash", "-c", script],
                capture_output=True, text=True, timeout=1800)
    except Exception as e:  # noqa: BLE001
        return "volume: FAILED (error: %r)" % e
    if getattr(r, "returncode", 1) != 0:
        return "volume: FAILED (rc=%s: %s)" % (
            r.returncode, (getattr(r, "stderr", "") or "").strip()[:200])
    return "volume: applied (%s)" % decl.get("mount", "?")


def provision_owner_ignoreip(run=None, dest=None):
    """Install the owner break-glass ``ignoreip`` drop-in on the controller
    (#982). LOCAL-only via ``sudo`` — follows the ``_provision_shared_fleet_dir``
    pattern (controller-only, passwordless-sudo-gated, non-fatal).

    Called from ``cmd_install`` — runs on every box, but is a no-op unless:
      * box-class is ``controller``
      * fail2ban is installed (``fail2ban-client`` exists)
      * passwordless sudo is available (``sudo -n true``)

    *dest* overrides the drop-in path — used by tests to avoid reading
    the live ``/etc`` state (#982 fix-forward hermeticity seam).

    IDEMPOTENT: writes the drop-in atomically (mktemp+mv via sudo), then
    reloads fail2ban. Returns a status string for logging."""
    import shutil as _shutil
    run = run or subprocess.run

    # Controller-only gate (same pattern as _provision_shared_fleet_dir).
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return "skipped (box-class=%s, not controller)" % bc

    # fail2ban gate — a controller without fail2ban is left clean.
    if not _shutil.which("fail2ban-client"):
        return "skipped (fail2ban not installed)"

    content = render_owner_ignoreip()
    dest = dest or OWNER_IGNOREIP_PATH

    # Idempotent short-circuit: if the drop-in already exists and is
    # byte-identical, skip the write + reload (the _provision_shared_fleet_dir
    # pattern). The file is 0644, readable without sudo — checked BEFORE
    # the sudo probe so an unchanged file never shells out at all.
    if os.path.isfile(dest):
        try:
            with open(dest, "r", encoding="utf-8", errors="replace") as fh:
                if fh.read() == content:
                    return "unchanged (%s)" % dest
        except OSError as e:
            print("  break-glass: cannot read %s (%s), rewriting"
                  % (dest, e), file=sys.stderr)

    # sudo gate — never prompt.
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001
        return "skipped (sudo probe error: %r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return "skipped (no passwordless sudo)"

    # Atomic write via sudo: tee to tmp, chmod, mv into place.
    # Every rc is checked — a silent failure would leave the owner IP
    # un-whitelisted while reporting "applied".
    tmp = dest + ".airuleset-tmp"
    try:
        run(["sudo", "-n", "mkdir", "-p", os.path.dirname(dest)],
            capture_output=True, text=True, timeout=10)
        r = run(["sudo", "-n", "tee", tmp], input=content,
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (tee rc=%d)" % r.returncode
        r = run(["sudo", "-n", "chmod", "0644", tmp],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            run(["sudo", "-n", "rm", "-f", tmp],
                capture_output=True, text=True, timeout=5)
            return "FAILED (chmod rc=%d)" % r.returncode
        r = run(["sudo", "-n", "mv", "-f", tmp, dest],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            run(["sudo", "-n", "rm", "-f", tmp],
                capture_output=True, text=True, timeout=5)
            return "FAILED (mv rc=%d)" % r.returncode
        r = run(["sudo", "-n", "fail2ban-client", "reload"],
                capture_output=True, text=True, timeout=15)
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (fail2ban reload rc=%d — file written but NOT live)" \
                   % r.returncode
    except Exception as e:  # noqa: BLE001
        run(["sudo", "-n", "rm", "-f", tmp],
            capture_output=True, text=True, timeout=5)
        return "FAILED (error: %r)" % e
    return "applied (%s)" % dest


def check_owner_ignoreip_status():
    """Status check for the owner break-glass ``ignoreip`` drop-in on the
    controller (#982). Returns ``(ok, message)`` — ``ok=True`` when the drop-in
    exists and carries ALL managed IPs; ``ok=False`` (RED row) when it is missing
    or incomplete. A non-controller box always returns ``(True, "n/a")``.

    Called from ``cmd_status`` — never modifies anything."""
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return (True, "n/a (not controller)")
    if not os.path.isfile(OWNER_IGNOREIP_PATH):
        return (False, "MISSING — %s does not exist" % OWNER_IGNOREIP_PATH)
    try:
        with open(OWNER_IGNOREIP_PATH, "r", encoding="utf-8",
                  errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        return (False, "UNREADABLE — %s" % e)
    import cli_fleet
    missing = [ip for ip in cli_fleet.OWNER_BREAK_GLASS_IPS
               if ip not in content]
    if missing:
        return (False, "INCOMPLETE — missing IPs: %s" % ", ".join(missing))
    return (True, "OK — %s" % OWNER_IGNOREIP_PATH)


def provision_sshd_password(run=None, dest=None):
    """Install the sshd password-login drop-in for the airuleset user on the
    controller (#985). Follows the same pattern as ``provision_owner_ignoreip``:
    controller-only, passwordless-sudo-gated, idempotent, ``sshd -t`` before
    reload.

    *dest* overrides the drop-in path — used by tests to avoid reading live
    ``/etc``. Returns a status string for logging."""
    run = run or subprocess.run

    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return "skipped (box-class=%s, not controller)" % bc

    content = render_sshd_password_conf()
    dest = dest or SSHD_PASSWORD_PATH

    # Idempotent short-circuit.
    if os.path.isfile(dest):
        try:
            with open(dest, "r", encoding="utf-8", errors="replace") as fh:
                if fh.read() == content:
                    return "unchanged (%s)" % dest
        except OSError as e:
            print("  sshd-password: cannot read %s (%s), rewriting"
                  % (dest, e), file=sys.stderr)

    # sudo gate.
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001
        return "skipped (sudo probe error: %r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return "skipped (no passwordless sudo)"

    # Write via sudo: tee to tmp, chmod, mv into place, THEN sshd -t on the
    # LIVE config (the Include glob is *.conf — a *.airuleset-tmp is invisible
    # to sshd, so pre-mv validation is a no-op; the correct pattern is
    # mv → sshd -t → restore on failure, per cli_webterm_only.py:747-762).
    tmp = dest + ".airuleset-tmp"
    try:
        run(["sudo", "-n", "mkdir", "-p", os.path.dirname(dest)],
            capture_output=True, text=True, timeout=10)
        # Save previous content for rollback (best-effort).
        prev_content = None
        if os.path.isfile(dest):
            try:
                with open(dest, "r", encoding="utf-8", errors="replace") as fh:
                    prev_content = fh.read()
            except OSError as e:
                print("  sshd-password: cannot read %s for rollback (%s)"
                      % (dest, e), file=sys.stderr)
        r = run(["sudo", "-n", "tee", tmp], input=content,
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (tee rc=%d)" % r.returncode
        r = run(["sudo", "-n", "chmod", "0644", tmp],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            run(["sudo", "-n", "rm", "-f", tmp],
                capture_output=True, text=True, timeout=5)
            return "FAILED (chmod rc=%d)" % r.returncode
        r = run(["sudo", "-n", "mv", "-f", tmp, dest],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            run(["sudo", "-n", "rm", "-f", tmp],
                capture_output=True, text=True, timeout=5)
            return "FAILED (mv rc=%d)" % r.returncode
        # sshd -t validates the LIVE config (the new file is now in place).
        r = run(["sudo", "-n", "sshd", "-t"],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            # ROLLBACK: restore previous content or remove the bad file.
            if prev_content is not None:
                run(["sudo", "-n", "tee", dest], input=prev_content,
                    capture_output=True, text=True, timeout=10)
            else:
                run(["sudo", "-n", "rm", "-f", dest],
                    capture_output=True, text=True, timeout=5)
            return "FAILED (sshd -t rc=%d: %s — ROLLED BACK)" % (
                r.returncode, (r.stderr or "").strip()[:200])
        r = run(["sudo", "-n", "systemctl", "reload", "ssh"],
                capture_output=True, text=True, timeout=15)
        if getattr(r, "returncode", 1) != 0:
            return ("FAILED (ssh reload rc=%d — file written but NOT live)"
                    % r.returncode)
    except Exception as e:  # noqa: BLE001
        run(["sudo", "-n", "rm", "-f", tmp],
            capture_output=True, text=True, timeout=5)
        return "FAILED (error: %r)" % e
    return "applied (%s)" % dest


def check_sshd_password_status(dest=None):
    """Status check for the sshd password-login drop-in (#985).

    Returns ``(ok, message)`` — ``ok=True`` when the drop-in exists and is
    byte-identical to the rendered content; ``ok=False`` when missing or
    different. Non-controller returns ``(True, "n/a")``.

    *dest* is injectable for tests."""
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return (True, "n/a (not controller)")
    dest = dest or SSHD_PASSWORD_PATH
    if not os.path.isfile(dest):
        return (False, "MISSING — %s does not exist" % dest)
    try:
        with open(dest, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        return (False, "UNREADABLE — %s" % e)
    expected = render_sshd_password_conf()
    if content != expected:
        return (False, "DRIFT — content differs from expected render")
    return (True, "OK — sshd password login (airuleset)")


def provision_ufw_ssh(run=None):
    """Ensure ufw allows rate-limited SSH from anywhere on the controller (#985).

    Idempotent: parses ``ufw status`` output, adds the rule only when absent.
    Controller-only, sudo-gated, non-fatal. Returns a status string."""
    run = run or subprocess.run

    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return "skipped (box-class=%s, not controller)" % bc

    # sudo gate.
    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001
        return "skipped (sudo probe error: %r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return "skipped (no passwordless sudo)"

    # Check if the rule already exists.
    try:
        r = run(["sudo", "-n", "ufw", "status"],
                capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 1) != 0:
            return "skipped (ufw status failed rc=%d)" % r.returncode
        status_out = r.stdout or ""
        # Look for a 22/tcp LIMIT rule from Anywhere.
        for line in status_out.splitlines():
            if "22/tcp" in line and "LIMIT" in line.upper() and "Anywhere" in line:
                return "unchanged (22/tcp LIMIT Anywhere already present)"
    except Exception as e:  # noqa: BLE001
        return "skipped (ufw check error: %r)" % e

    # Add the rule.
    try:
        r = run(["sudo", "-n", "ufw", "limit", "22/tcp",
                 "comment", "airuleset break-glass ssh from anywhere (#985)"],
                capture_output=True, text=True, timeout=15)
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (ufw limit rc=%d: %s)" % (
                r.returncode, (r.stderr or "").strip()[:200])
    except Exception as e:  # noqa: BLE001
        return "FAILED (ufw error: %r)" % e
    return "applied (ufw limit 22/tcp)"


def check_ufw_ssh_status(ufw_output=None):
    """Status check for the ufw SSH rate-limit rule (#985).

    Returns ``(ok, message)``. Non-controller returns ``(True, "n/a")``.
    ``ufw_output`` is injectable for tests (the raw ``ufw status`` stdout)."""
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return (True, "n/a (not controller)")
    if ufw_output is None:
        try:
            r = subprocess.run(
                ["sudo", "-n", "ufw", "status"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return (False, "ufw status failed rc=%d" % r.returncode)
            ufw_output = r.stdout or ""
        except Exception as e:
            return (False, "ufw check failed: %s" % e)
    for line in ufw_output.splitlines():
        if "22/tcp" in line and "LIMIT" in line.upper() and "Anywhere" in line:
            return (True, "OK — ufw 22/tcp LIMIT Anywhere")
    return (False, "MISSING — no 22/tcp LIMIT Anywhere rule in ufw")


def check_owner_key_status(authorized_keys_path=None):
    """Status check for the owner break-glass SSH key on the controller (#982).
    Returns ``(ok, message)`` — ``ok=True`` when ALL managed break-glass keys
    are present in the ``airuleset`` account's ``authorized_keys``;
    ``ok=False`` (RED row) when any key is missing. A non-controller box always
    returns ``(True, "n/a")``.

    ``authorized_keys_path`` defaults to ``~/.ssh/authorized_keys`` (injectable
    for tests). Called from ``cmd_status`` — never modifies anything."""
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return (True, "n/a (not controller)")
    if authorized_keys_path is None:
        authorized_keys_path = os.path.expanduser("~/.ssh/authorized_keys")
    if not os.path.isfile(authorized_keys_path):
        return (False, "MISSING — %s does not exist" % authorized_keys_path)
    try:
        with open(authorized_keys_path, "r", encoding="utf-8",
                  errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        return (False, "UNREADABLE — %s" % e)
    import cli_fleet
    missing = []
    for key_line in cli_fleet.OWNER_BREAK_GLASS_KEYS:
        parts = key_line.split()
        blob = parts[1] if len(parts) >= 2 else None
        if blob and blob not in content:
            comment = parts[2] if len(parts) >= 3 else "(no comment)"
            missing.append(comment)
    if missing:
        return (False, "MISSING key(s): %s" % ", ".join(missing))
    return (True, "OK")


def check_controller_dns(resolve_fn=None):
    """Status check for DNS names resolving to their expected IPs (#982/#985).

    Each name in ``CONTROLLER_DNS_EXPECTED`` has its OWN expected IP:
    ``ar.newlevel.media`` → the PUBLIC IP (reachable from anywhere),
    others → the tailscale IP. Returns a list of ``(name, ok, message)``
    rows. A non-controller box always returns an empty list.

    ``resolve_fn(name)`` defaults to ``socket.getaddrinfo`` lookup returning
    the set of A-record addresses (injectable for tests)."""
    try:
        from watchdog.reaper import default_box_class
        bc = default_box_class()
    except Exception:  # noqa: BLE001
        bc = "unknown"
    if bc != "controller":
        return []
    if resolve_fn is None:
        import socket

        def resolve_fn(name):
            """Return the SET of IPv4 addresses for *name*, or None on
            gaierror. A multi-homed name (public + tailscale) returns both."""
            try:
                results = socket.getaddrinfo(name, None, socket.AF_INET)
                return set(r[4][0] for r in results) if results else None
            except socket.gaierror:
                return None
    rows = []
    for name in CONTROLLER_DNS_NAMES:
        addrs = resolve_fn(name)
        if addrs is None:
            rows.append((name, False, "UNRESOLVABLE"))
            continue
        # Proxied names (Cloudflare edge): only check resolvability.
        if name in CONTROLLER_DNS_PROXIED:
            rows.append((name, True,
                         "OK (proxied) → %s" % (sorted(addrs)
                                                 if isinstance(addrs, set)
                                                 else addrs)))
            continue
        # Expected-IP names: check the specific IP is in the set.
        expected_ip = CONTROLLER_DNS_EXPECTED.get(name, CONTROLLER_TAILSCALE_IP)
        if isinstance(addrs, set):
            if expected_ip in addrs:
                rows.append((name, True,
                             "OK → %s (in %s)" % (expected_ip,
                                                   sorted(addrs))))
            else:
                rows.append((name, False,
                             "DRIFT — resolves to %s, expected %s in set"
                             % (sorted(addrs), expected_ip)))
        elif addrs == expected_ip:
            # Legacy single-address resolve_fn (injected by tests).
            rows.append((name, True, "OK → %s" % addrs))
        else:
            rows.append((name, False,
                         "DRIFT — resolves to %s, expected %s"
                         % (addrs, expected_ip)))
    return rows


def cmd_disk_guard_root(args):
    """Session-facing reader for the owner-daily root-level finding (#841 leg C).

    ``airuleset.py disk-guard-root`` prints the current fresh finding (the
    root-level reclaimable candidates the per-user guard cannot reach) so a
    SESSION can raise ONE owner-daily ``❓`` from it — or ``none`` when there is
    no fresh finding above threshold. ``--mark-asked`` records that the ``❓``
    was raised (the once-per-EPISODE dedup, #795 no re-ask); ``--json`` output.

    The watchdog (Job 40) is the only WRITER of the finding; this command only
    READS it (and, with ``--mark-asked``, stamps the dedup date). NO ping is
    ever fired from here (`notify` is not touched)."""
    import json as _json
    from watchdog import disk_guard_root as _r
    finding = _r.read_finding()
    if getattr(args, "mark_asked", False):
        if finding is None:
            print("disk-guard-root: no fresh finding to mark asked")
            return 0
        day = _r.mark_asked()
        print("disk-guard-root: marked asked_on=%s" % day)
        return 0
    if getattr(args, "json", False):
        print(_json.dumps(finding))
        return 0
    if finding is None:
        print("disk-guard-root: none (no fresh root-level finding above threshold)")
        return 0
    est = finding.get("estimate_bytes", 0)
    asked = finding.get("asked_on")
    cands = finding.get("candidates") or []
    summary = ", ".join("%s=%s" % (c.get("cls"), _r._human(c.get("bytes", 0)))
                        for c in cands[:6]) or "(none)"
    print("disk-guard-root: FINDING reclaimable~=%s (report %s, asked_on=%s)"
          % (_r._human(est), finding.get("report_generated_at"), asked))
    print("  candidates: %s" % summary)
    if not asked:
        print("  → a SESSION should raise ONE owner-daily ❓ (Slovak, self-"
              "contained) then run `airuleset.py disk-guard-root --mark-asked`")
    return 0
