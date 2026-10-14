"""#1186: camera-box project account — LAN reach entries + command-scoped sudo.

The #1184 declaration could only express reach to FLEET boxes and a boolean
sudo. camera-box (the first project with LAN hardware) needs ssh to non-fleet
LAN hosts and two root-owned project scripts. Main's design
(issuecomment-5894645187, Approach 1) extends the ONE declaration:

  (a) a reach entry may be a fleet box name OR
      ``{"cidr": "<ip>/32", "ports": [..], "reason": "<why>"}`` — private
      (RFC1918 / tailscale 100.64/10) single hosts only, each with a reason;
      the nft render accepts exactly those destinations + ports BEFORE the uid
      reject;
  (b) ``sudo: "commands"`` + ``sudo_commands`` renders NOPASSWD for exactly
      the listed ``/usr/local/sbin/<account>-*`` paths, and the bootstrap
      refuses a path that is missing, not root-owned, or account-writable;
      ``sudo: True`` is unchanged;
  (c) the camera-box declaration validates and renders nothing broader;
  (d) ``accounts status`` shows the reach entries and the sudo commands.

The existing fohmixer/claudy renders stay byte-identical in their sudo+reach
section (golden fixtures captured from main before this change).
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_webterm_only as wo  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "account_render_1186"
_FAKE_TIMO_PUBKEY = "ssh-ed25519 " + "AAAA" + "X" * 64 + " webterm-timo-controller"


def _valid_spec(**over):
    spec = {"host": "dev1", "sudo": False, "secrets": [],
            "webterm_sessions": {}}
    spec.update(over)
    return spec


def _render(spec, account="projx"):
    with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {account: spec}):
        return bootstrap.render_root_bootstrap(account)


def _sudoers_body(script):
    m = re.search(r"<< 'SUDOERS_EOF'\n(.*?)SUDOERS_EOF\n", script, re.S)
    assert m, "no sudoers heredoc in the render"
    return m.group(1)


def _section(script):
    return script[script.index("# 3a."):script.index("# 3c.")]


# --------------------------------------------------------------------------- #
# (a) LAN reach entries — validator
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# (a) LAN reach entries — nft render
# --------------------------------------------------------------------------- #
class TestGoldenRenders(unittest.TestCase):
    """The fohmixer/claudy sudo + 3b sections stay byte-identical to the
    captured goldens (#1186 lock; #1232 re-captured them with the teardown)."""

    def test_existing_fohmixer_and_claudy_renders_are_byte_identical(self):
        with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS,
                             {"timo": _FAKE_TIMO_PUBKEY}):
            foh = bootstrap.render_root_bootstrap("fohmixer")
        self.assertEqual(_section(foh),
                         (FIXTURES / "fohmixer_3a_3b.txt").read_text())
        self.assertEqual(_section(bootstrap.render_root_bootstrap("claudy")),
                         (FIXTURES / "claudy_3a_3b.txt").read_text())


# --------------------------------------------------------------------------- #
# (b) command-scoped sudo
# --------------------------------------------------------------------------- #
class TestCommandScopedSudo(unittest.TestCase):

    CMDS = ["/usr/local/sbin/projx-upgrade", "/usr/local/sbin/projx-log-install"]

    def _spec(self, **over):
        base = dict(sudo="commands", sudo_reason="dev1-side root installs",
                    sudo_commands=list(self.CMDS))
        base.update(over)
        return _valid_spec(**base)

    def test_valid_command_scoped_declaration(self):
        self.assertEqual(bootstrap.validate_account("projx", self._spec()), [])

    def test_needs_a_reason_and_a_list(self):
        errs = bootstrap.validate_account("projx", self._spec(sudo_reason=None))
        self.assertTrue(any("sudo_reason" in e for e in errs), errs)
        for cmds in (None, [], "/usr/local/sbin/projx-upgrade"):
            errs = bootstrap.validate_account("projx", self._spec(sudo_commands=cmds))
            self.assertTrue(any("sudo_commands" in e for e in errs), (cmds, errs))

    def test_only_exact_project_script_paths(self):
        for bad in ("ALL", "/usr/local/sbin/projx-*", "/usr/local/sbin/projx-up grade",
                    "/usr/local/sbin/projx-upgrade --apply", "projx-upgrade",
                    "/usr/bin/systemctl", "/usr/local/sbin/other-upgrade",
                    "/usr/local/sbin/projx", "/usr/local/sbin/projx-",
                    "/usr/local/sbin/../sbin/projx-a", "/usr/local/sbin/projx-a/b",
                    "/usr/local/bin/projx-a", "/home/projx/projx-a",
                    "/tmp/projx-a", "/usr/local/sbin/projx-a\n",
                    "/usr/local/sbin/projx-a,ALL", "/usr/local/sbin/projx-a:x",
                    "/usr/local/sbin/projx-a=b", "/usr/local/sbin/projx-a\\",
                    "/usr/local/sbin/projx-a!", "/usr/local/sbin/projx-A", 7):
            errs = bootstrap.validate_account("projx", self._spec(sudo_commands=[bad]))
            self.assertTrue(any("sudo_commands" in e for e in errs), (bad, errs))

    def test_a_path_of_a_longer_named_account_is_refused(self):
        # review 2: `projx` must never grant a declared `projx-box`'s script
        spec = self._spec(sudo_commands=["/usr/local/sbin/projx-box-upgrade"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx-box": {}}):
            errs = bootstrap.validate_account("projx", spec)
        self.assertTrue(any("projx-box" in e and "sudo_commands" in e
                            for e in errs), errs)
        self.assertEqual(bootstrap.validate_account("projx", spec), [])

    def test_duplicate_paths_are_refused(self):
        errs = bootstrap.validate_account("projx", self._spec(
            sudo_commands=[self.CMDS[0], self.CMDS[0]]))
        self.assertTrue(any("duplicate" in e for e in errs), errs)

    def test_unknown_sudo_modes_are_refused(self):
        for mode in ("yes", "all", "ALL", "command", 1, None):
            errs = bootstrap.validate_account("projx", _valid_spec(sudo=mode))
            self.assertTrue(any("sudo must be" in e for e in errs), (mode, errs))

    def test_renders_exactly_the_listed_paths(self):
        script = _render(self._spec())
        body = _sudoers_body(script).splitlines()
        self.assertEqual(len(body), 2, body)
        self.assertTrue(body[0].startswith("# airuleset:managed"), body)
        # review 1: `""` = no arguments, so argument parsing is never root
        # attack surface
        self.assertEqual(body[1], 'projx ALL=(root) NOPASSWD: '
                                  '/usr/local/sbin/projx-upgrade "", '
                                  '/usr/local/sbin/projx-log-install ""')
        self.assertIn("dev1-side root installs", body[0])
        self.assertIn('visudo -cf "$SUDOERS_TMP"', script)
        self.assertIn('*" sudo "*', script)   # root-equivalent group check stays
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_path_checks_precede_the_sudoers_write(self):
        script = _render(self._spec())
        checks = hardening.render_sudo_path_checks(self.CMDS)
        self.assertIn(checks, script)
        self.assertLess(script.index(checks), script.index("SUDOERS_TMP=$(mktemp"))

    def _run_checks(self, paths):
        snippet = hardening.render_sudo_path_checks(paths)
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\nACCOUNT=nobody\n" + snippet],
            capture_output=True, text=True)

    def test_refuses_a_missing_path(self):
        r = self._run_checks(["/usr/local/sbin/projx-does-not-exist-1186"])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("missing", r.stderr)

    def test_refuses_a_non_root_owned_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "projx-a")
            Path(p).write_text("#!/bin/sh\ntrue\n")
            os.chmod(p, 0o755)
            if os.geteuid() == 0:   # make the fixture non-root-owned under root
                os.chown(p, 65534, -1)
            r = self._run_checks([p])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not root-owned", r.stderr)

    def test_refuses_a_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "projx-a")
            os.symlink("/bin/true", p)
            r = self._run_checks([p])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("symlink", r.stderr)

    def _run_stubbed(self, stat_map, writable=()):
        """Run the checks on /usr/bin/true with `stat` answering from
        ``stat_map`` ({path: "owner mode"}, default a root-owned 0755) and
        `runuser … test -w` answering yes for ``writable`` paths."""
        cases = "".join('        %s) echo "%s" ;;\n' % (k, v)
                        for k, v in stat_map.items())
        stubs = ("stat() {\n    case \"${@: -1}\" in\n%s"
                 "        /*) echo \"root drwxr-xr-x\" ;;\n    esac\n}\n"
                 "runuser() {\n    case \"${@: -1}\" in\n%s"
                 "        *) echo no ;;\n    esac\n}\n") % (
            cases, "".join("        %s) echo yes ;;\n" % w for w in writable))
        snippet = hardening.render_sudo_path_checks(["/usr/bin/true"])
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\nACCOUNT=projx\n" + stubs + snippet],
            capture_output=True, text=True)

    def test_a_clean_root_owned_path_passes(self):
        r = self._run_stubbed({})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_ancestor_dir_is_checked(self):
        for anc in ("/usr/bin", "/usr", "/"):
            r = self._run_stubbed({anc: "root drwxrwxrwx"})
            self.assertEqual(r.returncode, 1, (anc, r.stdout + r.stderr))
            self.assertIn("ERROR: %s (sudo command /usr/bin/true) is not "
                          "root-owned" % anc, r.stderr)
            r = self._run_stubbed({anc: "projx drwxr-xr-x"})
            self.assertEqual(r.returncode, 1, (anc, r.stdout + r.stderr))

    def test_a_group_or_other_writable_file_is_refused(self):
        for mode in ("root -rwxrwxr-x", "root -rwxr-xrwx"):
            r = self._run_stubbed({"/usr/bin/true": mode})
            self.assertEqual(r.returncode, 1, (mode, r.stdout + r.stderr))

    def test_a_path_the_account_can_write_is_refused(self):
        for w in ("/usr/bin/true", "/usr/bin", "/usr"):
            r = self._run_stubbed({}, writable=(w,))
            self.assertEqual(r.returncode, 1, (w, r.stdout + r.stderr))
            self.assertIn("%s (sudo command /usr/bin/true) is writable by projx"
                          % w, r.stderr)

    def test_commands_reason_has_no_control_characters(self):
        errs = bootstrap.validate_account("projx", self._spec(
            sudo_reason="installs\x1b[2Jx"))
        self.assertTrue(any("sudo_reason" in e for e in errs), errs)

    def test_account_writability_is_checked_fail_closed(self):
        checks = hardening.render_sudo_path_checks(self.CMDS)
        self.assertIn('runuser -u "$ACCOUNT" --', checks)
        # a runuser failure is never read as "not writable"
        r = self._run_checks(["/usr/bin/true"])
        if os.geteuid() == 0:   # runuser works: root-owned 0755 is not writable
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:                   # runuser refuses a non-root caller -> fail closed
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("cannot verify", r.stderr)

    def test_sudo_true_semantics_are_unchanged(self):
        script = _render(_valid_spec(
            sudo=True, sudo_reason="restart its own unit",
            sudo_commands=["/usr/bin/systemctl restart proj.service"]))
        self.assertIn("projx ALL=(root) NOPASSWD:NOEXEC: "
                      "/usr/bin/systemctl restart proj.service", script)
        self.assertIn("for BIN in /usr/bin/systemctl; do", script)
        self.assertNotIn("runuser -u", _section(script))


# --------------------------------------------------------------------------- #
# (c) the camera-box declaration
# --------------------------------------------------------------------------- #
CAMERA_BOX_SUDO = ["/usr/local/sbin/camera-box-dev1-dantesync-upgrade",
                   "/usr/local/sbin/camera-box-dev1-remote-log-install"]


class TestCameraBoxDeclaration(unittest.TestCase):

    def test_declaration_is_clean_and_explicit(self):
        self.assertIn("camera-box", bootstrap.SERVICE_ACCOUNTS)
        self.assertEqual(bootstrap.validate_account(
            "camera-box", bootstrap.SERVICE_ACCOUNTS["camera-box"]), [])
        spec = bootstrap.account_spec("camera-box")
        self.assertEqual(spec["host"], "dev1")
        self.assertEqual(spec["sudo"], "commands")
        self.assertEqual(list(spec["sudo_commands"]), CAMERA_BOX_SUDO)
        self.assertTrue(spec["sudo_reason"].strip())
        self.assertEqual(set(spec["secrets"]), {
            "av-soak.env", "dantesync-fleet.env", "netcfg-drift.env",
            "obs-ws-pass", "obs-burn-reconcile-watchdog.conf",
            "fleet-ssh-password", "gh-token"})
        self.assertEqual(set(spec["webterm_sessions"]), {"zbynek"})
        self.assertEqual(spec["webterm_sessions"]["zbynek"]["preferred"],
                         "camera-box")
        self.assertEqual(spec["repo"], "zbynekdrlik/camera-box")
        self.assertEqual(spec["project_dir"], "devel/camera-box")
        self.assertEqual(spec["tmux_session"], "camera-box")

    def test_the_registry_row_is_not_flipped_before_the_live_migration(self):
        rows = json.loads((Path(__file__).resolve().parent.parent
                           / "projects-registry.json").read_text())
        row = next(r for r in rows if r["name"] == "camera-box")
        self.assertEqual(row["account"], "newlevel")


# --------------------------------------------------------------------------- #
# (d) accounts status shows reach entries and sudo commands
# --------------------------------------------------------------------------- #
class TestAccountsStatusShowsReachAndSudo(unittest.TestCase):

    def _run(self, as_json):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = accounts.cmd_accounts(mock.Mock(action="status", json=as_json,
                                                 registry=None))
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_text(self):
        out = self._run(False)
        self.assertRegex(out, r"camera-box\s+host=dev1\s+sudo=commands")
        for path in CAMERA_BOX_SUDO:
            self.assertIn("sudo " + path, out)
        # the fohmixer/claudy lines keep their shape
        self.assertRegex(out, r"fohmixer\s+host=dev1\s+sudo=no\s+secrets=")

    def test_json(self):
        data = json.loads(self._run(True))
        cb = data["project_accounts"]["camera-box"]
        self.assertEqual(cb["sudo"], "commands")
        self.assertEqual(cb["sudo_commands"], CAMERA_BOX_SUDO)
        self.assertNotIn("reach", cb)
        self.assertIs(data["project_accounts"]["fohmixer"]["sudo"], False)
        self.assertEqual(data["project_accounts"]["fohmixer"]["sudo_commands"], [])


if __name__ == "__main__":
    unittest.main()
