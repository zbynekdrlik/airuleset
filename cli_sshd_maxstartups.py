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
    which is why the read-back checks the EFFECTIVE ``sshd -T`` value.
  * ``sshd -t`` gates the reload: a refused config is removed again and the
    leg fails, so a broken file never waits for the next sshd restart.
  * ``systemctl reload ssh`` (SIGHUP, live sessions survive) runs ONLY when
    the file changed; an unchanged file reloads nothing.
  * The #985 ``60-airuleset-password.conf`` and every foreign file are never
    touched.
"""
import shlex

SSHD_MAXSTARTUPS_PATH = "/etc/ssh/sshd_config.d/50-airuleset-maxstartups.conf"
SSHD_MAXSTARTUPS = "60:30:200"


def render_sshd_maxstartups_conf():
    """The ``50-airuleset-maxstartups.conf`` body: the managed marker on line
    1, then the one ``MaxStartups`` directive."""
    return (
        "# Managed by airuleset (#1349) — the push fans out ~20 parallel ssh\n"
        "# sessions per box; the default MaxStartups 10:30:100 dropped them.\n"
        "MaxStartups %s\n" % SSHD_MAXSTARTUPS
    )


def build_sshd_maxstartups_step(heredoc_mark, path=SSHD_MAXSTARTUPS_PATH):
    """The apply-script step: write the drop-in through the script's
    ``_install`` (which sets ``DGROOT_WROTE`` only when it changed the file),
    gate it with ``sshd -t``, then reload only on a change. A refused config
    removes the drop-in and sets ``SSHD_MS_FAIL``; so does a failed reload.
    ``path`` exists for the execution tests (a scratch dir)."""
    qpath = shlex.quote(path)
    return (
        'SSHD_MS_FAIL=""\n'
        'DGROOT_WROTE=""\n'
        "_install %s 0644 <<'%s'\n%s%s\n"
        'if sshd_t_out=$(sshd -t 2>&1); then\n'
        '    if [ -n "$DGROOT_WROTE" ]; then\n'
        '        if systemctl reload ssh >/dev/null 2>&1; then\n'
        '            echo "  disk-guard-root: sshd reloaded (MaxStartups %s)"\n'
        '        else\n'
        '            echo "  ⚠ DISK-GUARD-ROOT: sshd reload failed" >&2\n'
        '            SSHD_MS_FAIL=1\n'
        '        fi\n'
        '    fi\n'
        'else\n'
        '    rm -f %s\n'
        '    echo "  ⚠ DISK-GUARD-ROOT: sshd config test failed — %s removed, '
        'reload skipped: $(printf "%%s" "$sshd_t_out" | head -n 3)" >&2\n'
        '    SSHD_MS_FAIL=1\n'
        'fi'
        % (qpath, heredoc_mark, render_sshd_maxstartups_conf(), heredoc_mark,
           SSHD_MAXSTARTUPS, qpath, path)
    )


def build_sshd_maxstartups_readback():
    """Read-back: a failed step fails the apply, and the value sshd itself
    resolves (``sshd -T``) must be the managed one — a foreign drop-in that
    sorts before ``50-`` would shadow the file while it sits on disk."""
    return (
        'if [ -n "$SSHD_MS_FAIL" ]; then fail=1; fi\n'
        'sshd_ms=$(sshd -T 2>/dev/null | grep -i "^maxstartups " || true)\n'
        'if [ "$sshd_ms" != "maxstartups %s" ]; then\n'
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: effective sshd MaxStartups '
        'is [${sshd_ms#maxstartups }], expected %s" >&2\n'
        '    fail=1\n'
        'fi' % (SSHD_MAXSTARTUPS, SSHD_MAXSTARTUPS)
    )
