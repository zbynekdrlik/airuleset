"""Shared-stream resource guardrails (#775) — a self-contained CLI leaf.

Layer 3 of the subdev-OOM-collapse fix (2026-08-31). #776 (ugrep reaper) and
#778 (heavy-build ban) each kill ONE known runaway signature; this leaf makes
the CATEGORY "anything else that eats the box" hit a mechanical ceiling: a
per-stream-user systemd cgroup cap so the kernel cgroup-OOM killer takes out the
GREEDIEST PROCESS INSIDE the offending user's slice — never the whole box, and
never sshd/tailscaled (which are OOM-protected so the box stays reachable).

Root cause it closes (from #774 primary evidence): subdev collapsed WITH a full
8 GB swap and 15.6 GB RAM — swap was never the missing safety-net. The real gap
was that `user-<uid>.slice` had `MemoryMax=infinity`, so no per-user cgroup
ceiling existed and the kernel OOM killer only engaged at GLOBAL thrash, where
it did not protect sshd/tailscaled → kex-reset, box dead.

MECHANISM (settled DESIGN phase, #775 comment 5489114579):
  * A TEMPLATE systemd drop-in on `user-.slice` (one file covers EVERY present
    AND future stream user — no per-uid list to drift when a stream is added or
    renamed): MemoryHigh=12%, MemoryMax=18%, TasksMax=512, CPUWeight=100. The
    limits are PERCENTAGES (systemd computes them from physical RAM), so they
    survive a box resize — the exact change (8→16 GB) that already staled the
    ticket body.
  * A root-exempt drop-in on `user-0.slice` (the template also matches uid 0;
    a throttled root recovery session could never recover a thrashing box).
  * OOM-protection drop-ins on ssh.service + tailscaled.service
    (OOMScoreAdjust=-900 + MemoryMin=128M) so the box stays reachable under
    pressure — the #774 acceptance criterion.
  * A sysctl `vm.swappiness=10` — swap as an emergency reserve, not active
    paging. Swap itself is NEVER created here (it already exists); the apply
    script only VERIFIES its presence and reports LOUD if it is missing.

Applied by a NEW `airuleset.py push` step (`provision_shared_stream_guards`)
over `ssh root@subdev` (stream accounts are sudo-less, so this cannot run in the
per-user `install`) — the SAME idempotent-atomic write + fail-loud discipline as
`cli_owner_vps._sudoers_install_script` (#659). The apply script writes each
drop-in atomically (mktemp+mv), `daemon-reload`s, `sysctl --system`s, live-
applies the caps to already-running slices via `systemctl set-property
--runtime` (SKIPPING any slice whose current usage already exceeds the new max,
so a live legit session is never insta-killed at deploy), and READS BACK the
result (`systemctl show` vs a MemTotal-derived expectation) — a mismatch/infinity
exits non-zero, turning the push step LOUD.

The dev1→root@subdev operator key is NOT evidenced today (root ownership is with
gatekeeper); until a GATEKEEPER-ACTION authorizes it, this step is a fail-LOUD
no-op. The `watchdog.resource_guard` verify-only job is the standing backstop
that surfaces "subdev still runs without guardrails".

ZERO couplings by design — a pure leaf: `provision_shared_stream_guards` reads
`airuleset.SHARED_STREAM_GUARD_HOSTS` (the facade re-export) LAZILY inside the
function body, the same L-E convention `cli_remote.cmd_push` uses.
"""
import os
import shlex
import subprocess
import sys


# ---------------------------------------------------------------------------
# Managed drop-in / sysctl file paths (systemd + sysctl standard locations).
# The `50-airuleset-*` prefix keeps them ordered after distro defaults and
# unmistakably airuleset-owned.
# ---------------------------------------------------------------------------
GUARD_DROPIN_PATH = "/etc/systemd/system/user-.slice.d/50-airuleset-resource-guard.conf"
ROOT_EXEMPT_PATH = "/etc/systemd/system/user-0.slice.d/50-airuleset-root-exempt.conf"
SSH_OOM_PATH = "/etc/systemd/system/ssh.service.d/50-airuleset-oom-protect.conf"
TAILSCALED_OOM_PATH = "/etc/systemd/system/tailscaled.service.d/50-airuleset-oom-protect.conf"
SYSCTL_VM_PATH = "/etc/sysctl.d/50-airuleset-vm.conf"

# #925-C: rsyslog quiet rule — suppress api-watchdog per-poll output from
# syslog. The journal retains all entries; this only prevents rsyslog from
# forwarding ~5M lines/day to /var/log/syslog on shared-stream boxes
# (12 accounts x 60s polls x ~7 lines each).
RSYSLOG_QUIET_PATH = "/etc/rsyslog.d/30-airuleset-watchdog-quiet.conf"

# #925-C: journald SystemMaxUse cap for shared-stream boxes. Uses a `60-`
# prefix because cli_disk_guard_root.py already ships
# `50-airuleset-journal-cap.conf` with 200M for gk — on shared-stream boxes
# where BOTH provisioning systems run, the `60-` sorts AFTER the `50-` and its
# 300M value wins (systemd conf.d precedence = lexical last wins for duplicate
# keys). On gk (not shared-stream), only the 50-/200M applies.
JOURNALD_CAP_PATH = "/etc/systemd/journald.conf.d/60-airuleset-resource-guard-journal.conf"
JOURNALD_MAX_USE = "300M"

# #925-C: swap policy for shared-stream boxes (owner ruling, comment
# 5570068675). Exactly ONE /swapfile of 10G — 16G was wasteful, 4G caused OOM.
SWAP_SIZE_GB = 10

# The numeric policy (percentages of physical RAM + absolutes). Kept as named
# constants so the render functions AND the read-back verify in the apply
# script derive from ONE source.
MEMORY_HIGH_PCT = 12
MEMORY_MAX_PCT = 18
TASKS_MAX = 512
# #922: CPUWeight lowered from 100 (default, no advantage) to 30 — root
# (weight 100 via the root-exempt drop-in) gets scheduling priority over
# any stream user under contention. Under no contention, streams use any
# idle CPU. The root-exempt drop-in restores CPUWeight=100 for uid 0.
CPU_WEIGHT = 30
# #922: CPUQuota hard-caps each stream at 300% (3 of 8 vCPU cores). Was
# DELIBERATELY absent in #775 (the collapse was memory thrash, not CPU),
# but the owner's live incident (david3 test at 98% CPU, owner can't
# scroll) showed the CPU gap is real.
CPU_QUOTA_PCT = 300
OOM_SCORE_ADJUST = -900
MEMORY_MIN = "128M"
VM_SWAPPINESS = 10


def render_guard_dropin() -> str:
    """The TEMPLATE `user-.slice` guardrail (applies to every stream user)."""
    return (
        "# Managed by airuleset (#775/#922) — mechanical per-stream-user resource\n"
        "# guardrails on shared-stream boxes (subdev). TEMPLATE drop-in: applies\n"
        "# to EVERY user-<uid>.slice (present AND future streams), so a new or\n"
        "# renamed stream is covered automatically — no per-uid list to drift.\n"
        "# user-0.slice is exempted by 50-airuleset-root-exempt.conf.\n"
        "[Slice]\n"
        "MemoryHigh=%d%%\n"
        "MemoryMax=%d%%\n"
        "TasksMax=%d\n"
        "CPUWeight=%d\n"
        "CPUQuota=%d%%\n"
        % (MEMORY_HIGH_PCT, MEMORY_MAX_PCT, TASKS_MAX, CPU_WEIGHT,
           CPU_QUOTA_PCT)
    )


def render_root_exempt_dropin() -> str:
    """Restore an UNLIMITED root recovery session (uid 0 also matches the
    `user-.slice` template)."""
    return (
        "# Managed by airuleset (#775/#922) — the user-.slice TEMPLATE guardrail\n"
        "# also matches uid 0, so this drop-in restores an UNLIMITED root\n"
        "# recovery session. A root session throttled by its own guardrail\n"
        "# could not recover a thrashing box.\n"
        "[Slice]\n"
        "MemoryHigh=infinity\n"
        "MemoryMax=infinity\n"
        "TasksMax=infinity\n"
        "CPUWeight=100\n"
        "CPUQuota=\n"
    )


def render_service_oom_dropin() -> str:
    """OOM protection for a critical reachability service (ssh + tailscaled)."""
    return (
        "# Managed by airuleset (#775) — protect the box's reachability under\n"
        "# memory pressure: bias the OOM killer away from this critical service\n"
        "# and reserve a memory floor, so it survives a stream-user OOM and the\n"
        "# box stays reachable (the #774 acceptance criterion).\n"
        "[Service]\n"
        "OOMScoreAdjust=%d\n"
        "MemoryMin=%s\n"
        % (OOM_SCORE_ADJUST, MEMORY_MIN)
    )


def render_sysctl_vm() -> str:
    """`vm.swappiness` tuning — swap as an emergency reserve, not active paging.
    Swap itself is NOT created (verify-only; see the apply script)."""
    return (
        "# Managed by airuleset (#775) — swap is an emergency reserve, not active\n"
        "# paging (thrash mitigation). Swap itself is NOT created here (it already\n"
        "# exists on subdev); the apply script only VERIFIES its presence and\n"
        "# reports LOUD if it is missing (creating swap would be a separate,\n"
        "# destructive-ish op).\n"
        "vm.swappiness=%d\n"
        % (VM_SWAPPINESS,)
    )


def render_rsyslog_quiet():
    """rsyslog rule to suppress api-watchdog per-poll output from syslog.

    The api-watchdog systemd --user service runs every 60s on each stream
    account. On a shared-stream box with ~12 accounts, this produces ~5M
    syslog lines/day (the journal keeps them all — this rule only stops
    rsyslog from forwarding them to /var/log/syslog). Uses the structured
    journal field ``_SYSTEMD_USER_UNIT`` so it matches exactly the
    api-watchdog.service unit regardless of syslog identifier."""
    return (
        "# Managed by airuleset (#925-C) — suppress api-watchdog per-poll\n"
        "# output from syslog. The journal retains all entries; this only\n"
        "# prevents rsyslog from forwarding ~5M lines/day to /var/log/syslog\n"
        "# on shared-stream boxes (12 accounts x 60s polls).\n"
        'if $!_SYSTEMD_USER_UNIT == "api-watchdog.service" then stop\n'
    )


def render_journald_cap():
    """journald ``SystemMaxUse`` cap for shared-stream boxes — 300M (owner
    ruling, #925 comment 5570042178). Overrides the 200M from
    ``cli_disk_guard_root.py``'s ``50-airuleset-journal-cap.conf`` via a
    ``60-`` prefix (lexical-last wins in systemd conf.d). journald re-reads
    this ONLY at (re)start, so the apply script restarts systemd-journald."""
    return (
        "# Managed by airuleset (#925-C) — shared-stream journal cap. Overrides\n"
        "# the 200M cap from the disk-guard-root provisioning with a higher\n"
        "# 300M limit suited to the ~12 accounts on this box. journald re-reads\n"
        "# conf.d ONLY at (re)start — the apply script restarts it.\n"
        "[Journal]\n"
        "SystemMaxUse=%s\n" % JOURNALD_MAX_USE
    )


def guard_files():
    """The (path, content) pairs the apply script installs, in write order.
    ssh + tailscaled share the identical OOM-protect body by design."""
    return [
        (GUARD_DROPIN_PATH, render_guard_dropin()),
        (ROOT_EXEMPT_PATH, render_root_exempt_dropin()),
        (SSH_OOM_PATH, render_service_oom_dropin()),
        (TAILSCALED_OOM_PATH, render_service_oom_dropin()),
        (SYSCTL_VM_PATH, render_sysctl_vm()),
        # #925-C: rsyslog quiet + journald cap
        (RSYSLOG_QUIET_PATH, render_rsyslog_quiet()),
        (JOURNALD_CAP_PATH, render_journald_cap()),
    ]


# The heredoc terminator for embedding a drop-in body inside the apply script.
# QUOTED (`<<'...'`) so the body is written verbatim (no shell expansion), and
# distinctive so a drop-in body can never contain it.
_HEREDOC_MARK = "AIRULESET_GUARD_EOF"


def build_apply_script() -> str:
    """A `bash -eo pipefail` script (run as `bash -c <script>` over ssh
    root@subdev, so its stdin stays free for the heredocs below) that
    idempotently installs every guard drop-in atomically, reloads systemd,
    live-applies the caps to running slices (skipping any already over the new
    max — never insta-kill a live session), verifies swap presence (never
    creates it), and READS BACK the applied limits (mismatch/infinity → exit 4,
    turning the push step LOUD). Pure string builder — no side effects."""
    parts = []
    parts.append("set -euo pipefail")
    parts.append("")
    # Atomic idempotent installer: content on stdin (a quoted heredoc), written
    # to a dotted mktemp in the destination dir and mv'd into place only once
    # fully written — the SAME never-a-truncating-write discipline as #659.
    parts.append(
        '_install() {\n'
        '    dest="$1"; dir=$(dirname "$dest")\n'
        '    mkdir -p "$dir"\n'
        '    tmp=$(mktemp "$dir/.airuleset-guard-XXXXXX")\n'
        "    trap 'rm -f \"$tmp\"' EXIT\n"
        '    cat > "$tmp"\n'
        '    chmod 0644 "$tmp"\n'
        '    mv -f "$tmp" "$dest"\n'
        '    trap - EXIT\n'
        '    echo "  resource-guards: wrote $dest"\n'
        '}'
    )
    parts.append("")
    for path, content in guard_files():
        parts.append(
            "_install %s <<'%s'\n%s%s"
            % (shlex.quote(path), _HEREDOC_MARK, content.rstrip("\n") + "\n",
               _HEREDOC_MARK)
        )
    parts.append("")
    # daemon-reload MUST precede any set-property (systemd must re-read the
    # template drop-in first). sysctl is best-effort LOUD.
    parts.append("systemctl daemon-reload")
    parts.append(
        'sysctl --system >/dev/null 2>&1 '
        '|| echo "  ⚠ resource-guards: sysctl --system failed (non-fatal)"'
    )
    parts.append("")
    # #925-C: restart rsyslog so it picks up the new quiet rule from rsyslog.d/.
    # rsyslog re-reads .d/ files only at restart. Best-effort LOUD.
    parts.append(
        'systemctl restart rsyslog.service >/dev/null 2>&1 '
        '|| echo "  ⚠ resource-guards: rsyslog restart failed (non-fatal)"'
    )
    # #925-C: journald cap — re-read ONLY at (re)start (the #841 gotcha:
    # daemon-reload does NOT make journald re-read its own conf.d, and
    # `journalctl --rotate` alone vacuums to the OLD limit). So restart
    # journald FIRST, then rotate so it vacuums to the new SystemMaxUse.
    parts.append(
        'systemctl try-restart systemd-journald.service >/dev/null 2>&1 '
        '|| echo "  ⚠ resource-guards: systemd-journald restart failed (non-fatal)"'
    )
    parts.append(
        'journalctl --rotate >/dev/null 2>&1 '
        '|| echo "  ⚠ resource-guards: journalctl --rotate failed (non-fatal)"'
    )
    parts.append("")
    # #925-C: swap policy — idempotent swap management on shared-stream boxes.
    # Exactly ONE /swapfile of SWAP_SIZE_GB. Guard: only acts when a
    # shared-stream box-class marker exists under ANY home.
    # The sequence is: create new → mkswap → swapon new → swapoff old → rm old
    # → rename new → fstab entry. NEVER leaves two active swapfiles.
    parts.append(
        '# Swap policy: exactly ONE /swapfile of %dG (#925-C, owner ruling).\n'
        '# Guard: only acts when a shared-stream box-class marker exists.\n'
        'swap_is_shared_stream=false\n'
        'for bcf in /home/*/.claude/airuleset-box-class; do\n'
        '    if [ -f "$bcf" ] && grep -q "shared-stream" "$bcf" 2>/dev/null; then\n'
        '        swap_is_shared_stream=true; break\n'
        '    fi\n'
        'done\n'
        'want_swap_bytes=$((%d * 1024 * 1024 * 1024))\n'
        'if "$swap_is_shared_stream"; then\n'
        '    cur_swap_bytes=0\n'
        '    if [ -f /swapfile ]; then\n'
        '        cur_swap_bytes=$(stat -c %%s /swapfile 2>/dev/null || echo 0)\n'
        '    fi\n'
        '    # Tolerance: within 1%% of the target size → no-op\n'
        '    lo_swap=$((want_swap_bytes * 99 / 100))\n'
        '    hi_swap=$((want_swap_bytes * 101 / 100))\n'
        '    if [ "$cur_swap_bytes" -ge "$lo_swap" ] && [ "$cur_swap_bytes" -le "$hi_swap" ]; then\n'
        '        echo "  resource-guards: /swapfile already %dG — no-op"\n'
        '    else\n'
        '        echo "  resource-guards: provisioning /swapfile (%dG)"\n'
        '        # Create the new swapfile\n'
        '        fallocate -l %dG /swapfile.new 2>/dev/null \\\n'
        '            || dd if=/dev/zero of=/swapfile.new bs=1G count=%d status=none\n'
        '        chmod 0600 /swapfile.new\n'
        '        mkswap /swapfile.new >/dev/null\n'
        '        swapon /swapfile.new\n'
        '        # Deactivate + remove old swapfiles (swapfile, swapfile2, swapfile3)\n'
        '        for old in /swapfile /swapfile2 /swapfile3; do\n'
        '            if [ -f "$old" ] && [ "$old" != "/swapfile.new" ]; then\n'
        '                swapoff "$old" 2>/dev/null || true\n'
        '                rm -f "$old"\n'
        '                echo "  resource-guards: removed old $old"\n'
        '            fi\n'
        '        done\n'
        '        mv /swapfile.new /swapfile\n'
        '        # Ensure fstab entry\n'
        '        if ! grep -q "^/swapfile " /etc/fstab 2>/dev/null; then\n'
        '            echo "/swapfile none swap sw 0 0" >> /etc/fstab\n'
        '            echo "  resource-guards: added /swapfile to fstab"\n'
        '        fi\n'
        '        # Remove stale fstab entries for old swapfiles\n'
        '        for old_entry in /swapfile2 /swapfile3; do\n'
        '            if grep -q "^${old_entry} " /etc/fstab 2>/dev/null; then\n'
        '                sed -i "\\\\|^${old_entry} |d" /etc/fstab\n'
        '                echo "  resource-guards: removed $old_entry from fstab"\n'
        '            fi\n'
        '        done\n'
        '        echo "  resource-guards: /swapfile %dG active"\n'
        '    fi\n'
        'else\n'
        '    # Non-shared-stream: verify-only (the original #775 behaviour)\n'
        '    swap_total=$(awk \'/^SwapTotal:/{print $2}\' /proc/meminfo)\n'
        '    if [ -z "$swap_total" ] || [ "$swap_total" -eq 0 ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS: NO SWAP present — provision swap manually (#775)." >&2\n'
        '    fi\n'
        'fi'
        % (SWAP_SIZE_GB, SWAP_SIZE_GB,
           SWAP_SIZE_GB, SWAP_SIZE_GB,
           SWAP_SIZE_GB, SWAP_SIZE_GB,
           SWAP_SIZE_GB)
    )
    parts.append("")
    # Expected bytes from MemTotal (the read-back baseline). systemd resolves a
    # `%` against PHYSICAL RAM, which is >= MemTotal by the reserved amount, so
    # the read-back uses a TOLERANCE BAND around this MemTotal-derived value —
    # an exact equality would false-fail on the reserved-memory gap.
    parts.append(
        'memtotal_kb=$(awk \'/^MemTotal:/{print $2}\' /proc/meminfo)\n'
        'memtotal_b=$((memtotal_kb * 1024))\n'
        'exp_max=$((memtotal_b * %d / 100))\n'
        'exp_high=$((memtotal_b * %d / 100))'
        % (MEMORY_MAX_PCT, MEMORY_HIGH_PCT)
    )
    parts.append("")
    # Live-apply to already-running slices (linger keeps stream slices alive),
    # fail-safe: SKIP any slice whose current usage already exceeds the new max
    # (the drop-in still applies from that slice's next restart).
    parts.append(
        'skipped=""\n'
        'for slice in $(systemctl list-units --plain --no-legend \'user-*.slice\' '
        '2>/dev/null | awk \'{print $1}\'); do\n'
        '    [ "$slice" = "user-0.slice" ] && continue\n'
        '    cur=$(systemctl show -p MemoryCurrent --value "$slice" 2>/dev/null '
        '|| echo "")\n'
        '    case "$cur" in \'\'|\'[not set]\'|infinity) cur=0 ;; esac\n'
        '    if [ "$cur" -gt "$exp_max" ] 2>/dev/null; then\n'
        '        echo "  ⚠ resource-guards: $slice MemoryCurrent=$cur > new '
        'MemoryMax=$exp_max — SKIP live set-property (drop-in applies at next '
        'slice restart)"\n'
        '        skipped="$skipped $slice"\n'
        '        continue\n'
        '    fi\n'
        '    systemctl set-property --runtime "$slice" MemoryHigh=%d%% '
        'MemoryMax=%d%% TasksMax=%d CPUWeight=%d CPUQuota=%d%% '
        '|| echo "  ⚠ resource-guards: set-property failed for $slice"\n'
        'done'
        % (MEMORY_HIGH_PCT, MEMORY_MAX_PCT, TASKS_MAX, CPU_WEIGHT,
           CPU_QUOTA_PCT)
    )
    parts.append("")
    # Read-back verify (fail-loud). A skipped slice is exempt (infinity expected
    # there); every applied slice must show finite MemoryMax/MemoryHigh within a
    # tolerance band, TasksMax exactly the policy value, CPUWeight exactly 30,
    # and CPUQuotaPerSecUSec exactly 3s (= 300%).
    parts.append(
        'lo_max=$((exp_max * 3 / 4)); hi_max=$((exp_max * 3 / 2))\n'
        'lo_high=$((exp_high * 3 / 4)); hi_high=$((exp_high * 3 / 2))\n'
        'fail=0\n'
        'for slice in $(systemctl list-units --plain --no-legend \'user-*.slice\' '
        '2>/dev/null | awk \'{print $1}\'); do\n'
        '    [ "$slice" = "user-0.slice" ] && continue\n'
        '    case " $skipped " in *" $slice "*) continue ;; esac\n'
        '    mmax=$(systemctl show -p MemoryMax --value "$slice" 2>/dev/null '
        '|| echo "")\n'
        '    mhigh=$(systemctl show -p MemoryHigh --value "$slice" 2>/dev/null '
        '|| echo "")\n'
        '    tmax=$(systemctl show -p TasksMax --value "$slice" 2>/dev/null '
        '|| echo "")\n'
        '    cpuw=$(systemctl show -p CPUWeight --value "$slice" 2>/dev/null '
        '|| echo "")\n'
        '    cpuq=$(systemctl show -p CPUQuotaPerSecUSec --value "$slice" '
        '2>/dev/null || echo "")\n'
        '    if [ "$mmax" = infinity ] || [ "$mhigh" = infinity ] '
        '|| [ -z "$mmax" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice MemoryMax=$mmax '
        'MemoryHigh=$mhigh (expected finite ~$exp_max / ~$exp_high)" >&2; '
        'fail=1; continue\n'
        '    fi\n'
        '    if [ "$mmax" -lt "$lo_max" ] || [ "$mmax" -gt "$hi_max" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice MemoryMax=$mmax '
        'out of band [$lo_max,$hi_max]" >&2; fail=1\n'
        '    fi\n'
        '    if [ "$mhigh" -lt "$lo_high" ] || [ "$mhigh" -gt "$hi_high" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice MemoryHigh=$mhigh '
        'out of band [$lo_high,$hi_high]" >&2; fail=1\n'
        '    fi\n'
        '    if [ "$tmax" != "%d" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice TasksMax=$tmax '
        '(expected %d)" >&2; fail=1\n'
        '    fi\n'
        '    if [ "$cpuw" != "%d" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice CPUWeight=$cpuw '
        '(expected %d)" >&2; fail=1\n'
        '    fi\n'
        '    if [ "$cpuq" != "%ds" ]; then\n'
        '        echo "  ⚠ RESOURCE-GUARDS VERIFY FAIL: $slice '
        'CPUQuotaPerSecUSec=$cpuq (expected %ds)" >&2; fail=1\n'
        '    fi\n'
        'done\n'
        'if [ "$fail" -ne 0 ]; then\n'
        '    echo "  ⚠ RESOURCE-GUARDS FAILED read-back verify" >&2\n'
        '    exit 4\n'
        'fi\n'
        'echo "  resource-guards: applied + verified (MemTotal=${memtotal_b}B '
        'MemoryMax~${exp_max}B MemoryHigh~${exp_high}B CPUWeight=%d '
        'CPUQuota=%d%%)"'
        % (TASKS_MAX, TASKS_MAX, CPU_WEIGHT, CPU_WEIGHT,
           CPU_QUOTA_PCT // 100, CPU_QUOTA_PCT // 100,
           CPU_WEIGHT, CPU_QUOTA_PCT)
    )
    return "\n".join(parts) + "\n"


def provision_shared_stream_guards(hosts=None, run=None, control_opts=None):
    """Apply the shared-stream resource guardrails on every `SHARED_STREAM_GUARD_HOSTS`
    target over `ssh root@<host>`. Called by `cmd_push` AFTER the deploy loop.

    #851 NOTE (deliberate, not an oversight): this leg is HOST-scoped, not
    per-REMOTE_HOSTS-account — `SHARED_STREAM_GUARD_HOSTS` has exactly ONE
    entry (`subdev`, the whole shared VPS), covering every present-and-future
    stream user on that box via a single `user-.slice` systemd template. A
    `"paused": ...` marker on ONE REMOTE_HOSTS account sharing that host
    (e.g. simap1@subdev) must NOT skip this leg — doing so would strip the
    cgroup OOM protection from every OTHER, still-active stream on the same
    box (marek/david1/montalu*/miva1/...). The #851 pause therefore protects
    simap1 from `_deployable_hosts()`-routed ssh (deploy/soniox/burn/webterm)
    but never reaches this genuinely host-level root connection.

    NON-FATAL + LOUD, exactly like `provision_owner_sudo` (#659): any failure
    (unreachable root, a not-yet-authorized operator key, a read-back mismatch)
    prints `⚠ RESOURCE-GUARDS FAILED (<name>)` and is returned in the failure
    list — it never raises, so the rest of the push is unaffected. Until the
    GATEKEEPER-ACTION authorizes the dev1→root@subdev operator key this is a
    fail-loud no-op, and the `watchdog.resource_guard` verify job is the backstop.

    `hosts` defaults (lazily, via the airuleset facade — the L-E convention) to
    `airuleset.SHARED_STREAM_GUARD_HOSTS`; `run` defaults to `subprocess.run`
    (injectable for tests). Returns a list of `(name, reason)` failures."""
    run = run or subprocess.run
    if hosts is None:
        import airuleset  # L-E: SHARED_STREAM_GUARD_HOSTS lives in cli_fleet, via facade
        hosts = airuleset.SHARED_STREAM_GUARD_HOSTS
    script = build_apply_script()
    # Passed as a single `bash -c <quoted script>` remote command (NOT via
    # stdin) so the heredocs inside the script keep an unconsumed stdin.
    remote_cmd = "bash -c " + shlex.quote(script)
    failed = []
    for h in hosts:
        name = h.get("name", h.get("host", "?"))
        host = h.get("host")
        identity = h.get("identity")
        # #775 review F5: a malformed guard entry must not raise out of a
        # function documented to never raise — refuse it like the identity gate.
        if not host:
            print("  ⚠ RESOURCE-GUARDS FAILED (%s): guard entry has no host."
                  % name, file=sys.stderr)
            failed.append((name, "no-host"))
            continue
        if not identity:
            print("  ⚠ RESOURCE-GUARDS FAILED (%s): no pinned ssh identity — a "
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
            print("  ⚠ RESOURCE-GUARDS FAILED (%s): ssh error (non-fatal): %r"
                  % (name, e), file=sys.stderr)
            failed.append((name, repr(e)))
            continue
        if getattr(r, "returncode", 1) != 0:
            print("  ⚠ RESOURCE-GUARDS FAILED (%s) rc=%s: %s"
                  % (name, r.returncode, (r.stderr or "").strip()[:400]),
                  file=sys.stderr)
            failed.append((name, "rc=%s" % r.returncode))
        else:
            # #775 review F1: surface stderr too on a SUCCESSFUL apply — the
            # script writes the design-mandated LOUD "NO SWAP present" warning
            # (and any non-fatal sysctl warning) to >&2 WITHOUT failing, so a
            # stdout-only success log would swallow it on a swapless box.
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            print("  resource-guards: applied + verified on %s%s"
                  % (name, ("\n    " + out.replace("\n", "\n    ")) if out else ""))
    return failed
