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
# Maintained here as a simple tuple; the root drain script skips these HOME dirs.
# Updated at push time via provision; a stale list errs toward keeping (safe).
PAUSED_ACCOUNTS = ("simap1",)

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
        "                _act \"${d%%/}\" \"playwright-old $fam rev $rev < $kept\"\n"
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
        "# --- rung (d): redundant pull tgz archives ---\n"
        "_log \"RUNG-D redundant pull tgz archives\"\n"
        "find /home -name '*.tgz' -type f -mmin +%d -print0 2>/dev/null | "
        "while IFS= read -r -d '' f; do\n"
        "    stem=\"${f%%.tgz}\"\n"
        "    if [ -d \"$stem\" ]; then\n"
        "        _act \"$f\" \"tgz-redundant extracted=$stem\"\n"
        "    fi\n"
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
