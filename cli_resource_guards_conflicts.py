"""#1321 — drop-ins that fight the managed `user-.slice` template.

subdev carried a hand-made gatekeeper `50-fairness.conf` (8.8.2026, 4-core era:
CPUQuota=300%, CPUWeight=100, TasksMax=8192). It sorts after
`50-airuleset-resource-guard.conf`, so every daemon-reload let it override the
managed CPU and task limits. The apply removes it before its daemon-reload, only
when the gatekeeper marker is on its first line (a foreign file without it is
left alone), and keeps a backup.
"""
import shlex

STALE_FAIRNESS_PATH = "/etc/systemd/system/user-.slice.d/50-fairness.conf"
STALE_FAIRNESS_MARKER = "# Managed by gatekeeper (odoo-erp subdev VPS fairness"
BACKUP_DIR = "/root"


def render_stale_fairness_removal(path=STALE_FAIRNESS_PATH, backup_dir=BACKUP_DIR):
    p, b = shlex.quote(path), shlex.quote(backup_dir)
    return (
        # never `head | grep -q` under pipefail: grep's early exit SIGPIPEs head (#1223)
        'if [ -f %s ] && case "$(head -n 1 %s)" in %s*) true ;; *) false ;; esac; then\n'
        '    cp -p %s %s/airuleset-1321-50-fairness.conf.$(date +%%Y%%m%%d%%H%%M%%S).bak\n'
        '    rm -f %s\n'
        '    echo "  resource-guards: removed stale %s (#1321, backup in %s)"\n'
        'fi'
        % (p, p, shlex.quote(STALE_FAIRNESS_MARKER), p, b, p, path, backup_dir)
    )
