"""#1355 — the controller's journald cap, applied LOCALLY from `cmd_install`.

The controller is not in `cli_fleet.DISK_GUARD_ROOT_HOSTS` (a root-ssh fan-out
whose fail2ban drop-in would collide with the controller-only 60- ignoreip), so
the #841 journald cap never reached it: 3.7G of journal on 19.11.2026 and no
`/etc/systemd/journald.conf.d/`. The fix follows the controller-only
provisioners (#982/#985): box-class gated, `sudo -n` gated, idempotent.
"""

import inspect
import types

import airuleset
import cli_controller_journal as cj
import cli_disk_guard_root as dgr


class _Run:
    """Records every argv; answers rc 0 unless an argv[2] is in `fail`."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        rc = 1 if len(argv) > 2 and argv[2] in self.fail else 0
        return types.SimpleNamespace(returncode=rc, stdout="Archived and active "
                                     "journals take up 1.0G in the file system.\n",
                                     stderr="")


def test_cap_is_rendered_by_the_one_journald_renderer():
    assert cj.JOURNAL_CAP_CONTROLLER == "1G"
    assert cj.render_controller_journal_cap() == dgr.render_journald_cap("1G")
    assert "SystemMaxUse=1G" in cj.render_controller_journal_cap()
    assert cj.CONTROLLER_JOURNAL_CAP_PATH == dgr.JOURNALD_CAP_PATH


def test_non_controller_is_skipped_without_any_command(tmp_path):
    run = _Run()
    out = cj.provision_controller_journal_cap(
        run=run, dest=str(tmp_path / "cap.conf"), box_class="workstation")
    assert out.startswith("skipped")
    assert run.calls == []


def test_identical_file_is_left_alone(tmp_path):
    dest = tmp_path / "cap.conf"
    dest.write_text(cj.render_controller_journal_cap())
    run = _Run()
    out = cj.provision_controller_journal_cap(run=run, dest=str(dest),
                                              box_class="controller")
    assert out.startswith("unchanged")
    assert run.calls == []


def test_no_passwordless_sudo_is_skipped(tmp_path):
    run = _Run(fail={"true"})
    out = cj.provision_controller_journal_cap(
        run=run, dest=str(tmp_path / "cap.conf"), box_class="controller")
    assert out.startswith("skipped")
    assert run.calls == [["sudo", "-n", "true"]]


def test_apply_writes_atomically_then_restarts_then_rotates(tmp_path):
    dest = str(tmp_path / "d" / "cap.conf")
    run = _Run()
    out = cj.provision_controller_journal_cap(run=run, dest=dest,
                                              box_class="controller")
    assert out.startswith("applied"), out
    verbs = [c[2] for c in run.calls]
    assert verbs[:5] == ["true", "mkdir", "tee", "chmod", "mv"]
    tee = run.calls[2]
    assert tee[3] == dest + ".airuleset-tmp"
    assert run.calls[4][-1] == dest
    # journald reads the cap only at start: restart BEFORE rotate (#841)
    restart = verbs.index("systemctl")
    rotate = verbs.index("journalctl")
    assert restart < rotate
    assert run.calls[restart][3:] == ["try-restart", "systemd-journald.service"]
    assert "--rotate" in run.calls[rotate]
    assert "1.0G" in out      # the read-back of --disk-usage is reported


def test_a_failed_write_is_loud_and_never_restarts(tmp_path):
    run = _Run(fail={"tee"})
    out = cj.provision_controller_journal_cap(
        run=run, dest=str(tmp_path / "cap.conf"), box_class="controller")
    assert out.startswith("FAILED")
    assert "systemctl" not in [c[2] for c in run.calls]


def test_cmd_install_wires_the_cap():
    src = inspect.getsource(airuleset.cmd_install)
    assert "provision_controller_journal_cap" in src
