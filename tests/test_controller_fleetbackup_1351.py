"""#1351 — the controller's `fleetbackup` login (dev1 fleet-backup manager) is
push-managed, not hand-made.

The owner granted it directly (ROZHODNUTÉ 17.11.2026, option 2): a key-only
login on the controller with passwordless sudo, restricted to dev1 with
`from="100.104.8.125"`. It was created by hand; nothing in `push` declared it,
so a rebuild loses it. The fix is a controller-only provisioner in the shape of
#982/#985/#1355, called from `cmd_install`.
"""

import inspect
import types

import airuleset
import cli_controller_fleetbackup as fb
import cli_owner_vps

BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAIMzhgslnCx56OLBPtqRStknHDCCDlpWvLoNQkXVB5B3l"
DESIRED = fb.FLEETBACKUP["authorized_key"]
FOREIGN = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIForeignForeignForeignForeignForeignFore other@box"


# --------------------------------------------------------------------------
# the declaration
# --------------------------------------------------------------------------

def test_declaration_carries_key_from_restriction_and_sudo_grant():
    spec = fb.FLEETBACKUP
    assert spec["user"] == "fleetbackup"
    assert DESIRED.startswith('from="100.104.8.125" ssh-ed25519 ' + BLOB + " ")
    assert spec["sudoers_path"] == "/etc/sudoers.d/60-airuleset-fleetbackup"
    assert "fleetbackup ALL=(ALL) NOPASSWD:ALL" in spec["sudoers"].splitlines()
    # the sudoers file name has no '.', so sudo's #includedir reads it
    assert "." not in spec["sudoers_path"].rsplit("/", 1)[1]


def test_declared_sudoers_bytes_equal_the_live_hand_made_file():
    """The live 17.11. file is 104 bytes; the declaration must read as it."""
    assert fb.FLEETBACKUP["sudoers"] == (
        "# airuleset#1351 (owner 17.11.2026): fleet-backup manager, permanent\n"
        "fleetbackup ALL=(ALL) NOPASSWD:ALL\n")


# --------------------------------------------------------------------------
# authorized_keys reconciliation (pure)
# --------------------------------------------------------------------------

def test_parse_key_line_splits_quoted_options():
    opts, ktype, blob = fb.parse_key_line(
        'from="10.0.0.1,10.0.0.2",command="echo a b" ssh-ed25519 %s c d' % BLOB)
    assert opts == 'from="10.0.0.1,10.0.0.2",command="echo a b"'
    assert (ktype, blob) == ("ssh-ed25519", BLOB)
    assert fb.parse_key_line("ssh-ed25519 %s x" % BLOB) == ("", "ssh-ed25519", BLOB)
    assert fb.parse_key_line("# comment") is None
    assert fb.parse_key_line("") is None


def test_live_line_is_present_even_with_a_different_comment():
    live = 'from="100.104.8.125" ssh-ed25519 %s some other comment\n' % BLOB
    new, action = fb.reconcile_authorized_keys(live, DESIRED)
    assert action == "present"
    assert new == live


def test_missing_key_is_appended_after_foreign_lines_kept_verbatim():
    existing = "# keep me\n" + FOREIGN + "\n\n"
    new, action = fb.reconcile_authorized_keys(existing, DESIRED)
    assert action == "added"
    assert new == existing + DESIRED + "\n"


def test_missing_trailing_newline_never_glues_onto_a_foreign_line():
    new, action = fb.reconcile_authorized_keys(FOREIGN, DESIRED)
    assert action == "added"
    assert new.splitlines() == [FOREIGN, DESIRED]


def test_empty_file_gets_the_line():
    assert fb.reconcile_authorized_keys("", DESIRED) == (DESIRED + "\n", "added")


def test_unrestricted_copy_is_replaced_in_place_foreign_untouched():
    existing = FOREIGN + "\nssh-ed25519 %s fleet-backup-manager@dev1\n# tail\n" % BLOB
    new, action = fb.reconcile_authorized_keys(existing, DESIRED)
    assert action == "replaced"
    assert new.splitlines() == [FOREIGN, DESIRED, "# tail"]


def test_duplicate_unrestricted_copy_is_dropped_so_from_is_not_bypassed():
    existing = DESIRED + "\n" + FOREIGN + "\nssh-ed25519 %s dup\n" % BLOB
    new, action = fb.reconcile_authorized_keys(existing, DESIRED)
    assert action == "replaced"
    assert new.splitlines() == [DESIRED, FOREIGN]


def test_commented_out_copy_of_our_key_is_a_foreign_line_kept_verbatim():
    existing = "# ssh-ed25519 %s old copy\n" % BLOB
    new, action = fb.reconcile_authorized_keys(existing, DESIRED)
    assert action == "added"
    assert new == existing + DESIRED + "\n"


def test_escaped_quote_inside_an_option_does_not_split_it():
    line = 'command="echo \\"a b\\"",no-pty ssh-ed25519 %s c' % BLOB
    assert fb.parse_key_line(line) == (
        'command="echo \\"a b\\"",no-pty', "ssh-ed25519", BLOB)


def test_reconcile_is_idempotent():
    once, _ = fb.reconcile_authorized_keys(FOREIGN + "\n", DESIRED)
    twice, action = fb.reconcile_authorized_keys(once, DESIRED)
    assert (twice, action) == (once, "present")


# --------------------------------------------------------------------------
# the provisioner (fake runner)
# --------------------------------------------------------------------------

HOME = "/home/fleetbackup"
AK = HOME + "/.ssh/authorized_keys"


class _Box:
    """A fake controller. Answers the provisioner's commands from a small
    state model and records every argv. `writes` lists the mutating ones."""

    MUTATING = ("useradd", "passwd -l", "write-ak", "chmod", "sudoers")

    def __init__(self, user=True, locked=True, ak=None, modes="750 700 600",
                 sudo_ok=True, sudoers_unchanged=True):
        self.user = user
        self.locked = locked
        self.ak = (DESIRED + "\n") if ak is None else ak
        if ak is False:
            self.ak = None         # the file does not exist
        self.modes = modes
        self.sudo_ok = sudo_ok
        self.sudoers_unchanged = sudoers_unchanged
        self.calls = []
        self.writes = []

    @staticmethod
    def _ok(stdout="", rc=0):
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw.get("input")))
        a = list(argv)
        if a == ["sudo", "-n", "true"]:
            return self._ok(rc=0 if self.sudo_ok else 1)
        if a[:2] == ["getent", "passwd"]:
            if not self.user:
                return self._ok(rc=2)
            return self._ok("fleetbackup:x:1003:1003:g:%s:/bin/bash\n" % HOME)
        if a[:3] == ["sudo", "-n", "useradd"]:
            self.writes.append("useradd")
            self.user = True
            self.locked = True     # a real `useradd -m` leaves `!` in shadow
            self.ak = None         # and no ~/.ssh/authorized_keys
            return self._ok()
        if a[:4] == ["sudo", "-n", "passwd", "-S"]:
            return self._ok("fleetbackup %s 2026-11-17 0 99999 7 -1\n"
                            % ("L" if self.locked else "P"))
        if a[:4] == ["sudo", "-n", "passwd", "-l"]:
            self.writes.append("passwd -l")
            self.locked = True
            return self._ok()
        if a[:5] == ["sudo", "-n", "-u", "fleetbackup", "test"]:
            return self._ok(rc=0 if self.ak is not None else 1)
        if a[:5] == ["sudo", "-n", "-u", "fleetbackup", "cat"]:
            if self.ak is None:
                return types.SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="cat: %s: Datei oder Verzeichnis nicht gefunden\n" % AK)
            return self._ok(self.ak)
        if a[:5] == ["sudo", "-n", "-u", "fleetbackup", "stat"]:
            return self._ok(self.modes.replace(" ", "\n") + "\n")
        if a[:5] == ["sudo", "-n", "-u", "fleetbackup", "chmod"]:
            self.writes.append("chmod")
            self.modes = "700 600"
            return self._ok()
        if a[:6] == ["sudo", "-n", "-u", "fleetbackup", "sh", "-c"]:
            self.writes.append("write-ak")
            self.ak = kw.get("input") or ""
            return self._ok()
        if a[:4] == ["sudo", "-n", "sh", "-c"]:
            if self.sudoers_unchanged:
                return self._ok("AIRULESET_SUDOERS_UNCHANGED\n")
            self.writes.append("sudoers")
            return self._ok("AIRULESET_SUDOERS_INSTALLED\n")
        raise AssertionError("unexpected command %r" % (a,))


def test_non_controller_runs_nothing():
    box = _Box()
    out = fb.provision_fleetbackup_login(run=box, box_class="workstation")
    assert out.startswith("skipped")
    assert box.calls == []


def test_no_passwordless_sudo_is_skipped_loud():
    box = _Box(sudo_ok=False)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("skipped (no passwordless sudo)")
    assert box.writes == []


def test_live_hand_made_state_reads_satisfied_with_zero_writes():
    box = _Box(ak='from="100.104.8.125" ssh-ed25519 %s fleet-backup-manager@dev1 '
                  '(airuleset#1351)\n' % BLOB)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("unchanged"), out
    assert box.writes == []


def test_fresh_box_creates_locked_user_key_and_sudo():
    """A real useradd leaves the password locked and no authorized_keys file;
    the missing file is detected without parsing (localised) error text."""
    box = _Box(user=False, sudoers_unchanged=False)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    assert box.writes == ["useradd", "write-ak", "sudoers"]
    assert box.ak == DESIRED + "\n"
    useradd = next(a for a, _ in box.calls if a[:3] == ["sudo", "-n", "useradd"])
    assert "-m" in useradd and useradd[-1] == "fleetbackup"


def test_foreign_lines_survive_and_unrestricted_copy_gets_from():
    box = _Box(ak=FOREIGN + "\nssh-ed25519 %s x\n" % BLOB)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    assert box.ak.splitlines() == [FOREIGN, DESIRED]


def test_user_with_a_password_gets_it_locked():
    box = _Box(locked=False)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    assert box.writes == ["passwd -l"]


def test_missing_authorized_keys_on_existing_user_is_written():
    box = _Box(ak=False)
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    assert box.writes == ["write-ak"]
    assert box.ak == DESIRED + "\n"


def test_group_writable_home_is_fixed_too_strictmodes():
    box = _Box(modes="770 700 600")
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    chmods = [a for a, _ in box.calls if a[:5] == ["sudo", "-n", "-u", "fleetbackup", "chmod"]]
    assert chmods and HOME in chmods[0]


def test_real_runner_under_pytest_is_inert():
    """#1196: a test that drives the real cmd_install must never reach real sudo."""
    out = fb.provision_fleetbackup_login(box_class="controller")
    assert out == "skipped (pytest)"


def test_install_step_never_raises_and_prints_one_line(capsys, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("x")
    monkeypatch.setattr(fb, "provision_fleetbackup_login", boom)
    fb.install_step()
    err = capsys.readouterr().err
    assert "fleetbackup login error (non-fatal)" in err


def test_wrong_modes_are_fixed_without_rewriting_the_file():
    box = _Box(modes="750 755 644")
    out = fb.provision_fleetbackup_login(run=box, box_class="controller")
    assert out.startswith("applied"), out
    assert box.writes == ["chmod"]


def test_the_key_is_written_as_the_user_never_by_root_into_its_home():
    """Root never writes into a user-owned dir (no symlink games): the
    authorized_keys write runs under `sudo -u fleetbackup`."""
    box = _Box(ak="")
    fb.provision_fleetbackup_login(run=box, box_class="controller")
    write = [a for a, _ in box.calls if a[:6] == ["sudo", "-n", "-u", "fleetbackup", "sh", "-c"]]
    assert len(write) == 1
    assert "mktemp" in write[0][6] and "mv -f" in write[0][6]


def test_sudoers_goes_through_the_659_visudo_installer():
    box = _Box()
    fb.provision_fleetbackup_login(run=box, box_class="controller")
    sh = [(a, i) for a, i in box.calls if a[:4] == ["sudo", "-n", "sh", "-c"]]
    assert len(sh) == 1
    argv, stdin = sh[0]
    assert argv[4] == cli_owner_vps._sudoers_install_script(fb.FLEETBACKUP["sudoers_path"])
    assert stdin == fb.FLEETBACKUP["sudoers"]


def test_a_failing_step_reports_failed_not_applied():
    class _Fail(_Box):
        def __call__(self, argv, **kw):
            if list(argv[:3]) == ["sudo", "-n", "useradd"]:
                self.calls.append((list(argv), None))
                return self._ok(rc=9)
            return super().__call__(argv, **kw)
    out = fb.provision_fleetbackup_login(run=_Fail(user=False), box_class="controller")
    assert out.startswith("FAILED"), out


def test_cmd_install_calls_the_non_fatal_install_step():
    """The call site is the leaf's install_step (it owns the try/except, so
    cmd_install grows by as few lines as possible; #1116 slice 2 shape)."""
    src = inspect.getsource(airuleset.cmd_install)
    assert "cli_controller_fleetbackup import install_step" in src
