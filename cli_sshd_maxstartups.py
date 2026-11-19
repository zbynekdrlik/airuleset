"""sshd ``MaxStartups`` drop-in for the #841 disk-guard ROOT apply (#1349).

A zero-import leaf: `cli_disk_guard_root.build_apply_script` embeds the step
and the read-back on every ``DISK_GUARD_ROOT_HOSTS`` box.

``airuleset.py push`` fans out ~20 parallel ssh sessions (one per account) to a
box within ~2 s. OpenSSH's default ``MaxStartups 10:30:100`` drops 30 % of new
unauthenticated connections above 10, so a project's own deploy that lands in
that window fails with ``kex_exchange_identification: Connection reset``.

  * ``sshd_config.d/50-airuleset-maxstartups.conf`` raises the pool to
    ``60:30:200``. sshd takes the FIRST value it reads and Ubuntu includes
    ``sshd_config.d/*.conf`` at the top of ``sshd_config``, so the drop-in wins
    over the main file; a foreign drop-in that sorts earlier would still win,
    which is why the read-back checks the value ``sshd -T`` resolves.
  * ``sshd -t`` gates the reload: a refused config this run wrote is removed
    again and the leg fails, so a broken file never waits for the next sshd
    restart; a config broken by a foreign file fails the leg untouched.
  * ``systemctl reload ssh`` (SIGHUP, live sessions survive) runs ONLY when
    the file changed or the last reload did not land (a sha256 stamp in
    ``/run/airuleset``); otherwise nothing reloads.
  * The #985 ``60-airuleset-password.conf`` and every foreign file are never
    touched.
"""
import shlex

SSHD_MAXSTARTUPS_PATH = "/etc/ssh/sshd_config.d/50-airuleset-maxstartups.conf"
SSHD_MAXSTARTUPS = "60:30:200"
# One source may hold at most 30 pre-auth slots: the controller's push burst
# (~20-25) fits, and one source on a public box (gk, subdev) cannot take all 200.
SSHD_PER_SOURCE_MAXSTARTUPS = "30"
# sha256 of the drop-in the running sshd last reloaded (wiped at boot).
SSHD_MAXSTARTUPS_STAMP = "/run/airuleset/sshd-maxstartups.reloaded"


def render_sshd_maxstartups_conf():
    """The ``50-airuleset-maxstartups.conf`` body: the managed marker on line
    1, then ``MaxStartups`` and the ``PerSourceMaxStartups`` cap."""
    return (
        "# Managed by airuleset (#1349) — the push fans out ~20 parallel ssh\n"
        "# sessions per box; the default MaxStartups 10:30:100 dropped them.\n"
        "MaxStartups %s\n"
        "PerSourceMaxStartups %s\n"
        % (SSHD_MAXSTARTUPS, SSHD_PER_SOURCE_MAXSTARTUPS)
    )


def build_sshd_maxstartups_step(heredoc_mark, path=SSHD_MAXSTARTUPS_PATH,
                                stamp=SSHD_MAXSTARTUPS_STAMP):
    """The apply-script step: write the drop-in through the script's
    ``_install`` (which sets ``DGROOT_WROTE`` only when it replaced the file),
    gate it with ``sshd -t``, then reload when the file changed OR the reload
    stamp does not hold the file's sha256 (a reload that failed on an earlier
    run is retried; ``/run`` is wiped at boot, so at worst one extra reload).
    A refused config this run wrote is removed again; a refused config this
    run did NOT change (a foreign drop-in broke it) leaves our file alone.
    Both, and a failed reload, set ``SSHD_MS_FAIL``. ``path``/``stamp`` exist
    for the execution tests (a scratch dir)."""
    qpath, qstamp = shlex.quote(path), shlex.quote(stamp)
    return (
        'SSHD_MS_FAIL=""\n'
        'DGROOT_WROTE=""\n'
        "_install %s 0644 <<'%s'\n%s%s\n"
        'sshd_ms_sum=$( { sha256sum < %s | cut -d" " -f1; } 2>/dev/null || true)\n'
        'if sshd_t_out=$(sshd -t 2>&1); then\n'
        '    if [ -n "$DGROOT_WROTE" ] || [ "$(cat %s 2>/dev/null || true)" != '
        '"$sshd_ms_sum" ]; then\n'
        '        if systemctl reload ssh >/dev/null 2>&1; then\n'
        '            mkdir -p "$(dirname %s)" && printf "%%s\\n" "$sshd_ms_sum" > %s '
        '|| echo "  ⚠ disk-guard-root: sshd reload stamp not written (non-fatal)"\n'
        '            echo "  disk-guard-root: sshd reloaded (MaxStartups %s)"\n'
        '        else\n'
        '            echo "  ⚠ DISK-GUARD-ROOT: sshd reload failed" >&2\n'
        '            SSHD_MS_FAIL=1\n'
        '        fi\n'
        '    fi\n'
        'elif [ -n "$DGROOT_WROTE" ]; then\n'
        '    rm -f %s\n'
        '    echo "  ⚠ DISK-GUARD-ROOT: sshd config test failed — %s removed, '
        'reload skipped: $(printf "%%s" "$sshd_t_out" | head -n 3)" >&2\n'
        '    SSHD_MS_FAIL=1\n'
        'else\n'
        '    echo "  ⚠ DISK-GUARD-ROOT: sshd config test failed on a config this '
        'run did not change (a foreign drop-in?) — reload skipped: '
        '$(printf "%%s" "$sshd_t_out" | head -n 3)" >&2\n'
        '    SSHD_MS_FAIL=1\n'
        'fi'
        % (qpath, heredoc_mark, render_sshd_maxstartups_conf(), heredoc_mark,
           qpath, qstamp, qstamp, qstamp, SSHD_MAXSTARTUPS, qpath, path)
    )


def build_sshd_maxstartups_readback():
    """Read-back: a failed step fails the apply, and the value sshd resolves
    from the files on disk (``sshd -T``) must be the managed one — a foreign
    drop-in that sorts before ``50-`` would shadow ours. ``sshd -T`` parses the
    files, not the running daemon; the reload stamp above covers the daemon."""
    return (
        'if [ -n "$SSHD_MS_FAIL" ]; then fail=1; fi\n'
        'sshd_ms=$(sshd -T 2>/dev/null | grep -i "^maxstartups " || true)\n'
        'if [ "$sshd_ms" != "maxstartups %s" ]; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: configured sshd MaxStartups '
        '(sshd -T) is [${sshd_ms#maxstartups }], expected %s" >&2\n'
        '    fail=1\n'
        'fi\n'
        'sshd_ps=$(sshd -T 2>/dev/null | grep -i "^persourcemaxstartups " '
        '|| true)\n'
        'if [ "$sshd_ps" != "persourcemaxstartups %s" ]; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: configured sshd '
        'PerSourceMaxStartups (sshd -T) is [${sshd_ps#persourcemaxstartups }], '
        'expected %s" >&2\n'
        '    fail=1\n'
        'fi' % (SSHD_MAXSTARTUPS, SSHD_MAXSTARTUPS,
                SSHD_PER_SOURCE_MAXSTARTUPS, SSHD_PER_SOURCE_MAXSTARTUPS)
    )
