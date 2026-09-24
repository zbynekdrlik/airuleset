"""#950 / #1140 — per-user ext4 disk quota for shared-stream boxes: the
quota slice of the #775 shared-stream guardrails, split out of
``cli_resource_guards`` (it had grown past the ~1000-line budget). Pure
renderers + constants; ``cli_resource_guards`` re-exports every name, installs
the files through its ``guard_files()`` + atomic ``_install``, and embeds
``_render_quota_apply_block()`` in its root apply script.

* #950: ext4 usrquota via legacy journaled quota (vfsv1), the boot unit and the
  usage-aware per-user limits (``_render_quota_limits_block`` — the ONE copy of
  the ceiling math).
* #1140: a vfsv1 file does not track writes made while quota is off, so usage
  drifted upward (montalu1 charged 25.6 GB for 17 GB real, EDQUOT 24.9.2026).
  The boot unit and a daily root refresh re-count with ``quotacheck -u -m /``
  (NEVER the create flag — it recreates the file WITHOUT limits) and the refresh
  re-applies the SAME limits block, so ceilings follow usage between pushes.
"""

# #950: per-user disk quota on shared-stream boxes.  ext4 usrquota via legacy
# journaled quota (NOT `tune2fs -O quota` which is REFUSED on a mounted root
# by e2fsprogs 1.47 — evidenced by the Fable design consult).  Soft = warning
# threshold; hard = ENOSPC wall; grace = time before soft becomes hard.
QUOTA_SOFT_KIB = 8 * 1024 * 1024   # 8G in KiB
QUOTA_HARD_KIB = 10 * 1024 * 1024  # 10G in KiB
QUOTA_GRACE_S = 86400               # 24 hours
# The systemd oneshot unit that re-enables quota at boot (persistence without
# editing the root fstab line — a mangled fstab = unbootable remote VPS).
QUOTA_UNIT_PATH = "/etc/systemd/system/airuleset-quota.service"
# #1140: daily root refresh of the quota accounting + usage-aware ceilings.
# vfsv1 journaled quota does NOT track writes made while quota is off, so usage
# drifts upward (montalu1 was charged 25.6 GB for 17 GB real, 24.9.2026). The
# refresh re-counts with `quotacheck -u -m /` — NEVER `-c` (that recreates the
# file WITHOUT limits) — and re-applies the ONE _render_quota_limits_block().
QUOTA_REFRESH_SCRIPT_PATH = "/usr/local/lib/airuleset/quota-refresh.sh"
QUOTA_REFRESH_SERVICE_PATH = "/etc/systemd/system/airuleset-quota-refresh.service"
QUOTA_REFRESH_TIMER_PATH = "/etc/systemd/system/airuleset-quota-refresh.timer"
QUOTACHECK_BOOT_TIMEOUT_S = 300    # boot unit is Before=ssh.service — bounded
QUOTACHECK_REFRESH_TIMEOUT_S = 1800
# ONE quota writer at a time (#1140 review): the daily refresh and the push
# apply both take this lock, so a push can never `quotaon` under a running
# recount (quotacheck must never rewrite a file the kernel is using). The boot
# unit needs none: it runs Before=ssh (no push yet) and the refresh service is
# ordered After= it. The apply waits less than the push's 180 s ssh timeout.
QUOTA_LOCK_PATH = "/run/airuleset-quota.lock"
QUOTA_LOCK_WAIT_S = 600
QUOTA_APPLY_LOCK_WAIT_S = 120


def render_quota_unit():
    """#950: a oneshot systemd unit that re-enables ext4 usrquota at boot.

    Persistence without editing the root fstab line (a mangled fstab =
    unbootable remote VPS, per Fable design consult).  The unit remounts /
    with journaled quota options and turns quota on — a failed unit leaves a
    bootable box with quota off, and the verify job reports it LOUD.
    ExecStartPre loads quota_v2 (fix-forward D1: virtual kernels ship it
    in linux-modules-extra, not the base image).

    #1140: re-counts usage with ``quotacheck -u -m /`` AFTER the remount (the
    journaled mount options are how quotacheck finds the file + format) and
    BEFORE ``quotaon`` — a vfsv1 file does not track writes made while quota
    was off, so without it usage only drifts up. ``-`` prefix: a failed or
    timed-out quotacheck NEVER blocks quotaon. NEVER ``-c`` (recreates the file
    WITHOUT limits). Bounded: the unit runs before ssh on a remote VPS."""
    return (
        "# Managed by airuleset (#950, #1140) — ext4 usrquota at boot.\n"
        "# A failed unit = bootable box with quota off (verify job reports).\n"
        "[Unit]\n"
        "Description=airuleset ext4 usrquota enablement\n"
        "After=local-fs.target\n"
        "Before=ssh.service\n"
        "ConditionPathExists=/aquota.user\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        "ExecStartPre=-/sbin/modprobe quota_v2\n"
        "ExecStartPre=/bin/mount -o remount,usrjquota=aquota.user,jqfmt=vfsv1 /\n"
        "ExecStartPre=-/usr/bin/timeout %d /sbin/quotacheck -u -m /\n"
        "ExecStart=/sbin/quotaon -u /\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
        % QUOTACHECK_BOOT_TIMEOUT_S
    )


def render_quota_refresh_script() -> str:
    """#1140 A+B: the daily ROOT refresh (bash) — true accounting, then
    usage-aware ceilings from the ONE ``_render_quota_limits_block()``.

    Takes ``QUOTA_LOCK_PATH`` FIRST (shared with the push apply; busy →
    exit 4 touching nothing). Order: ``quotaoff -u /`` when the ``quotaon -pu`` probe says quota is on
    → ``quotacheck -u -m /`` (skipped only when that quotaoff FAILED —
    quotacheck on an ACTIVE file damages it) → ``quotaon -u /``
    UNCONDITIONALLY → the limits block. An EXIT trap armed BEFORE quotaoff
    re-runs ``quotaon`` on ANY exit (error, SIGTERM, systemd timeout), so quota
    is never left off; ``quotaon`` on an already-on fs is a harmless EBUSY.
    NEVER ``-c``. Exits non-zero (LOUD in ``systemctl status``) when quotaoff,
    quotacheck, quotaon or the limits read-back failed — AFTER still running
    quotaon + the limits. Pure renderer."""
    return (
        "#!/bin/bash\n"
        "# Managed by airuleset (#1140) — daily quota accounting refresh +\n"
        "# usage-aware ceilings. NEVER re-create the quota file (drops every limit).\n"
        "set -euo pipefail\n"
        "if [ ! -f /aquota.user ]; then\n"
        "    echo \"  ⚠ quota-refresh: /aquota.user missing — quota not provisioned\" >&2\n"
        "    exit 3\n"
        "fi\n"
        "# one quota writer at a time — the push apply takes the same lock\n"
        "if ! { exec 9>>%s; } 2>/dev/null || ! flock -w %d 9; then\n"
        "    echo \"  ⚠ quota-refresh: quota lock %s busy/unopenable\" \\\n"
        "        \"(a push apply running?) — refresh SKIPPED, quota untouched\" >&2\n"
        "    exit 4\n"
        "fi\n"
        "rc=0\n"
        "# quota is NEVER left off: re-enable on ANY exit (a no-op EBUSY if on)\n"
        "trap 'quotaon -u / >/dev/null 2>&1 || true' EXIT\n"
        "trap 'exit 143' TERM INT HUP\n"
        "# quotaoff FAILS on an already-off fs (a failed boot unit) — exactly\n"
        "# when the recount is needed most; quotacheck is safe on an off file.\n"
        "qoff_rc=0; qoff_err=\"\"\n"
        "case \"$(quotaon -pu / 2>&1 || true)\" in\n"
        "    *\"is on\"*|*\"are on\"*) qoff_err=$(quotaoff -u / 2>&1) || qoff_rc=$? ;;\n"
        "esac\n"
        "if [ \"$qoff_rc\" -eq 0 ]; then\n"
        "    qc_err=$(nice -n19 ionice -c2 -n7 timeout %d quotacheck -u -m / 2>&1) \\\n"
        "        && qc_rc=0 || qc_rc=$?\n"
        "    if [ \"$qc_rc\" -ne 0 ]; then\n"
        "        echo \"  ⚠ quota-refresh: quotacheck failed rc=$qc_rc — stderr: $qc_err\" >&2\n"
        "        rc=1\n"
        "    else\n"
        "        echo \"  quota-refresh: quotacheck re-counted usage on /\"\n"
        "    fi\n"
        "else\n"
        "    echo \"  ⚠ quota-refresh: quotaoff failed rc=$qoff_rc — quotacheck SKIPPED\" \\\n"
        "        \"(it would damage an active quota file) — stderr: $qoff_err\" >&2\n"
        "    rc=1\n"
        "fi\n"
        "qon_err=$(quotaon -u / 2>&1) || case \"$qon_err\" in\n"
        "    *busy*) ;;\n"
        "    *) echo \"  ⚠ quota-refresh: quotaon failed — stderr: $qon_err\" >&2; rc=1 ;;\n"
        "esac\n"
        "%s\n"
        "if [ \"$qfail\" -ne 0 ]; then rc=1; fi\n"
        "exit \"$rc\"\n"
        % (QUOTA_LOCK_PATH, QUOTA_LOCK_WAIT_S, QUOTA_LOCK_PATH,
           QUOTACHECK_REFRESH_TIMEOUT_S, _render_quota_limits_block())
    )


def render_quota_refresh_service() -> str:
    """#1140: the oneshot the daily timer triggers — runs the refresh script.
    ``TimeoutStartSec`` exceeds the script's own quotacheck bound so systemd
    never kills it mid-count. ``After=airuleset-quota.service``: a
    ``Persistent=true`` catch-up at boot waits for the boot unit's recount.
    ``ExecStopPost=-quotaon`` runs even after a SIGKILL/OOM kill the script's
    EXIT trap cannot see — quota is never left off (EBUSY when on: ignored)."""
    return (
        "# Managed by airuleset (#1140) — quota accounting + ceiling refresh.\n"
        "[Unit]\n"
        "Description=airuleset quota accounting + usage-aware ceiling refresh (#1140)\n"
        "ConditionPathExists=/aquota.user\n"
        "After=airuleset-quota.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "TimeoutStartSec=%d\n"
        "ExecStart=/bin/bash %s\n"
        "ExecStopPost=-/sbin/quotaon -u /\n"
        % (QUOTACHECK_REFRESH_TIMEOUT_S + 600, QUOTA_REFRESH_SCRIPT_PATH)
    )


def render_quota_refresh_timer() -> str:
    """#1140: daily; ``Persistent=true`` catches a run missed while the box
    was down."""
    return (
        "# Managed by airuleset (#1140) — daily quota refresh so accounting\n"
        "# stays true and ceilings follow usage between pushes.\n"
        "[Unit]\n"
        "Description=airuleset quota refresh timer (#1140)\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=daily\n"
        "Persistent=true\n"
        "Unit=airuleset-quota-refresh.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _render_quota_kmod_block() -> str:
    """#950 fix-forward D1+D4: ensure quota_v2 kernel module is loaded.

    On virtual kernels (linux-image-virtual) the module lives in
    linux-modules-extra, which is NOT installed by default.  When modprobe
    fails, install the package and retry.  Persist via modules-load.d.
    All stderr is captured and printed on failure (D4)."""
    return (
        '# --- #950: quota_v2 kernel module ---\n'
        'kmod_ok=0\n'
        'kmod_err=$(modprobe quota_v2 2>&1) && kmod_ok=1\n'
        'if [ "$kmod_ok" -eq 0 ]; then\n'
        '    echo "  quota: modprobe quota_v2 failed — installing linux-modules-extra"\n'
        '    apt_err=$(DEBIAN_FRONTEND=noninteractive apt-get install -y \\\n'
        '        -o DPkg::Lock::Timeout=60 \\\n'
        '        "linux-modules-extra-$(uname -r)" linux-image-extra-virtual 2>&1) \\\n'
        '        || echo "  ⚠ quota: apt-get install failed — stderr: $apt_err" >&2\n'
        '    kmod_err=$(modprobe quota_v2 2>&1) && kmod_ok=1\n'
        '    if [ "$kmod_ok" -eq 0 ]; then\n'
        '        echo "  ⚠ quota: modprobe quota_v2 still fails after install — stderr: $kmod_err" >&2\n'
        '        echo "  ⚠ quota: kernel module unavailable — skipping quota setup" >&2\n'
        '    fi\n'
        'fi\n'
        '# Persist so the module loads at next boot\n'
        'if [ "$kmod_ok" -eq 1 ]; then\n'
        '    echo "quota_v2" > /etc/modules-load.d/airuleset-quota.conf\n'
        'fi'
    )


def _render_quota_limits_block() -> str:
    """#950 fix-forward D3+D4: usage-aware per-user quota application.

    For each shared-stream user, reads current usage from repquota and
    computes hard=max(QUOTA_HARD_KIB, ceil(used*1.2)),
    soft=max(QUOTA_SOFT_KIB, ceil(used*1.1)).  When above target, prints a
    LOUD warning about the temporary ceiling.  Read-back verify compares
    against the per-user APPLIED value, not the constant.

    Y6: a failed repquota call or a missing per-user row is NEVER treated
    as zero usage -- both cases print a LOUD warning and skip setquota for
    that user, so a transient repquota failure can never silently apply a
    blind (possibly too-low) limit."""
    return (
        '# --- #950: per-user usage-aware quota limits ---\n'
        'qfail=0; expected=""\n'
        'setquota -t -u %d %d / 2>&1 || true\n'
        '# Y5: hoist repquota above the loop (one call for all users)\n'
        '# Y6: capture rc without letting the assignment itself trip -e\n'
        'rq_out=$(repquota -u / 2>&1) && rq_rc=0 || rq_rc=$?\n'
        'for home in /home/*; do\n'
        '    [ -d "$home" ] || continue\n'
        '    u=$(basename "$home")\n'
        '    bcf="$home/.claude/airuleset-box-class"\n'
        '    grep -q "shared-stream" "$bcf" 2>/dev/null || continue\n'
        '    # Y6: repquota failure -> no usage for ANY user, never guess 0\n'
        '    if [ "$rq_rc" -ne 0 ]; then\n'
        '        echo "  ⚠ quota: repquota gave no usage for $u'
        ' — skipping setquota for $u" >&2\n'
        '        continue\n'
        '    fi\n'
        '    # Read current usage in KiB from the hoisted repquota output\n'
        '    used_kib=$(echo "$rq_out" | awk -v u="$u" \'$1==u{print $3}\')\n'
        '    # Y6: no row for this user -> never treat missing usage as 0\n'
        '    if [ -z "$used_kib" ]; then\n'
        '        echo "  ⚠ quota: repquota gave no usage for $u'
        ' — skipping setquota for $u" >&2\n'
        '        continue\n'
        '    fi\n'
        '    # Target limits (KiB)\n'
        '    target_soft=%d\n'
        '    target_hard=%d\n'
        '    # Usage-aware: never set a limit below current usage\n'
        '    # hard = max(target_hard, ceil(used * 1.2))\n'
        '    # soft = max(target_soft, ceil(used * 1.1))\n'
        '    used_hard=$(( (used_kib * 120 + 99) / 100 ))\n'
        '    used_soft=$(( (used_kib * 110 + 99) / 100 ))\n'
        '    if [ "$used_hard" -gt "$target_hard" ]; then\n'
        '        hard_kib=$used_hard\n'
        '    else\n'
        '        hard_kib=$target_hard\n'
        '    fi\n'
        '    if [ "$used_soft" -gt "$target_soft" ]; then\n'
        '        soft_kib=$used_soft\n'
        '    else\n'
        '        soft_kib=$target_soft\n'
        '    fi\n'
        '    expected="$expected $u=$hard_kib"\n'
        '    sq_err=$(setquota -u "$u" "$soft_kib" "$hard_kib" 0 0 / 2>&1) \\\n'
        '        || { echo "  ⚠ quota: setquota failed for $u — stderr: $sq_err" >&2; qfail=1; }\n'
        '    # Y4: warn whenever applied ceiling exceeds fleet target\n'
        '    if [ "$hard_kib" -gt "$target_hard" ]; then\n'
        '        used_g=$(( used_kib / 1048576 ))\n'
        '        target_g=$(( target_hard / 1048576 ))\n'
        '        ceil_g=$(( hard_kib / 1048576 ))\n'
        '        echo "  ⚠ quota: $u used=${used_g}G > target ${target_g}G'
        ' — temporary ceiling ${ceil_g}G, drain required" >&2\n'
        '    fi\n'
        'done\n'
        'systemctl enable airuleset-quota.service >/dev/null 2>&1 || true\n'
        '# Read-back verify (per-user applied values = the ones computed above)\n'
        'rq_verify=$(repquota -u / 2>&1 || true)\n'
        'for home in /home/*; do\n'
        '    [ -d "$home" ] || continue\n'
        '    u=$(basename "$home")\n'
        '    bcf="$home/.claude/airuleset-box-class"\n'
        '    grep -q "shared-stream" "$bcf" 2>/dev/null || continue\n'
        '    # Re-read the applied hard limit for THIS user\n'
        '    applied_hard=$(echo "$rq_verify" | awk -v u="$u" \'$1==u{print $5}\')\n'
        '    applied_soft=$(echo "$rq_verify" | awk -v u="$u" \'$1==u{print $4}\')\n'
        '    exp_hard=""\n'
        '    for kv in $expected; do [ "${kv%%%%=*}" = "$u" ] && exp_hard="${kv#*=}"; done\n'
        '    if [ -z "$applied_hard" ] || [ "$applied_hard" = "0" ]; then\n'
        '        echo "  ⚠ QUOTA VERIFY FAIL: $u has no hard limit set" >&2\n'
        '        qfail=1\n'
        '    elif [ -n "$exp_hard" ] && [ "$applied_hard" != "$exp_hard" ]; then\n'
        '        echo "  ⚠ QUOTA VERIFY FAIL: $u hard=$applied_hard, expected $exp_hard" >&2\n'
        '        qfail=1\n'
        '    else\n'
        '        ah_g=$(( applied_hard / 1048576 ))\n'
        '        as_g=$(( applied_soft / 1048576 ))\n'
        '        echo "  quota: $u verified (soft=${as_g}G hard=${ah_g}G)"\n'
        '    fi\n'
        'done\n'
        'if [ "$qfail" -ne 0 ]; then\n'
        '    echo "  ⚠ QUOTA read-back verify had failures" >&2\n'
        'else\n'
        '    echo "  quota: all users applied + verified (grace=%ds)"\n'
        'fi'
        % (QUOTA_GRACE_S, QUOTA_GRACE_S,
           QUOTA_SOFT_KIB, QUOTA_HARD_KIB,
           QUOTA_GRACE_S)
    )


def _render_quota_apply_lock_block() -> str:
    """#1140 review: ONE quota writer at a time. The push apply takes the SAME
    lock as the daily refresh, so it never ``quotaon``s under a running
    recount (busy → the quota part of this push is SKIPPED, LOUD). An
    unopenable lock (non-root, never in production) proceeds unserialized,
    LOUD. The fd is closed at the end of the apply block."""
    return (
        '    if [ "$quota_fail" -eq 0 ]; then\n'
        '        if { exec 9>>%s; } 2>/dev/null; then\n'
        '            if ! flock -w %d 9; then\n'
        '                echo "  ⚠ quota: quota lock busy (a refresh recount running?)" \\\n'
        '                    "— quota apply SKIPPED this push" >&2\n'
        '                quota_fail=1\n'
        '            fi\n'
        '        else\n'
        '            echo "  ⚠ quota: cannot open the quota lock — applying unserialized" >&2\n'
        '        fi\n'
        '    fi'
        % (QUOTA_LOCK_PATH, QUOTA_APPLY_LOCK_WAIT_S)
    )


def _render_quota_apply_block() -> str:
    """#950: per-user disk quota bash snippet — ext4 usrquota via legacy
    journaled quota (NOT tune2fs -O quota, which is REFUSED on a mounted root
    by e2fsprogs 1.47). Idempotent: each step is a no-op when already applied.
    Uncertain -> skip. Pure renderer returning the bash snippet.

    Fix-forward (D1-D4): kmod packaging, pipefail-safe already-on check,
    usage-aware limits, loud errors.  Orchestrates the extracted helpers
    _render_quota_kmod_block and _render_quota_limits_block."""
    return (
        '# --- #950: per-user ext4 disk quota ---\n'
        'quota_fail=0\n'
        'dev=$(findmnt -no SOURCE /); fstype=$(findmnt -no FSTYPE /)\n'
        'if [ "$fstype" != "ext4" ]; then\n'
        '    echo "  ⚠ quota: / is $fstype, not ext4 — skipped"\n'
        '    quota_fail=1\n'
        'elif ! command -v quotaon >/dev/null 2>&1; then\n'
        '    inst_err=$(DEBIAN_FRONTEND=noninteractive apt-get install -y quota 2>&1) \\\n'
        '        || { echo "  ⚠ quota: quota package install failed — stderr: $inst_err" >&2; quota_fail=1; }\n'
        'fi\n'
        'if [ "$quota_fail" -eq 0 ] && [ "$fstype" = "ext4" ] && command -v quotaon >/dev/null 2>&1; then\n'
        + _render_quota_kmod_block() + '\n'
        '    if [ "$kmod_ok" -eq 0 ]; then quota_fail=1; fi\n'
        + _render_quota_apply_lock_block() + '\n'
        '    # Check if quota is already on (D2: pipefail-safe, no | grep -q)\n'
        '    if [ "$quota_fail" -eq 0 ]; then\n'
        '        state=$(quotaon -pu / 2>&1 || true)\n'
        '        case "$state" in\n'
        '            *"is on"*|*"are on"*)\n'
        '                echo "  quota: already enabled on /"\n'
        '                ;;\n'
        '            *)\n'
        '                # Try remount with journaled quota options\n'
        '                rmnt_err=$(mount -o remount,usrjquota=aquota.user,jqfmt=vfsv1 / 2>&1) || {\n'
        '                    echo "  ⚠ quota: remount with usrjquota refused — stderr: $rmnt_err" >&2\n'
        '                    quota_fail=1\n'
        '                }\n'
        '                if [ "$quota_fail" -eq 0 ] && [ ! -f /aquota.user ]; then\n'
        '                    qc_err=$(nice -n19 ionice -c3 timeout 1800 quotacheck -cum / 2>&1) || {\n'
        '                        echo "  ⚠ quota: quotacheck failed — stderr: $qc_err" >&2\n'
        '                        quota_fail=1\n'
        '                    }\n'
        # #1140: found OFF with an existing file → re-count before quotaon
        # (never the create flag on an existing file: it drops every limit)
        '                elif [ "$quota_fail" -eq 0 ]; then\n'
        '                    qc_err=$(nice -n19 ionice -c2 -n7 timeout 1800 quotacheck -u -m / 2>&1) \\\n'
        '                        || echo "  ⚠ quota: recount failed (non-fatal) — stderr: $qc_err" >&2\n'
        '                fi\n'
        '                if [ "$quota_fail" -eq 0 ]; then\n'
        '                    qon_err=$(quotaon -u / 2>&1) || {\n'
        '                        # D2: treat EBUSY / "busy" as already-on\n'
        '                        case "$qon_err" in\n'
        '                            *"Device or resource busy"*|*"busy"*)\n'
        '                                echo "  quota: already enabled (quotaon returned busy)"\n'
        '                                ;;\n'
        '                            *)\n'
        '                                echo "  ⚠ quota: quotaon failed — stderr: $qon_err" >&2\n'
        '                                quota_fail=1\n'
        '                                ;;\n'
        '                        esac\n'
        '                    }\n'
        '                fi\n'
        '                ;;\n'
        '        esac\n'
        '    fi\n'
        '    if [ "$quota_fail" -eq 0 ]; then\n'
        + _render_quota_limits_block() + '\n'
        # #1140: the daily refresh timer + a read-back (LOUD on mismatch)
        '        systemctl enable --now airuleset-quota-refresh.timer >/dev/null 2>&1 \\\n'
        '            || echo "  ⚠ quota: refresh timer enable failed" >&2\n'
        '        tmr_state=$(systemctl is-enabled airuleset-quota-refresh.timer 2>&1 || true)\n'
        '        if [ "$tmr_state" = "enabled" ]; then\n'
        '            echo "  quota: refresh timer enabled + verified"\n'
        '        else\n'
        '            echo "  ⚠ QUOTA VERIFY FAIL: refresh timer is-enabled=$tmr_state" >&2\n'
        '        fi\n'
        '    fi\n'
        '    exec 9>&-\n'
        'fi'
    )
