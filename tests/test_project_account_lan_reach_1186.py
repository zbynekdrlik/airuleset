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
    spec = {"host": "dev1", "sudo": False, "reach": [], "secrets": [],
            "webterm_sessions": {}}
    spec.update(over)
    return spec


def _lan(cidr="10.77.9.61/32", ports=(22,), reason="cam1"):
    return {"cidr": cidr, "ports": list(ports), "reason": reason}


def _entry_err(errs, field):
    """A structured LAN reach-entry error naming ``field`` (never the generic
    fleet-name error, which merely echoes the entry's repr)."""
    return any(e.startswith("reach entry") and field in e for e in errs)


def _render(spec, account="projx"):
    with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {account: spec}):
        return bootstrap.render_root_bootstrap(account)


def _ruleset(script):
    """The nft ruleset heredoc of a rendered bootstrap."""
    m = re.search(r"<< 'REACH_EOF'\n(.*?)REACH_EOF\n", script, re.S)
    assert m, "no nft ruleset heredoc in the render"
    return m.group(1)


def _sudoers_body(script):
    m = re.search(r"<< 'SUDOERS_EOF'\n(.*?)SUDOERS_EOF\n", script, re.S)
    assert m, "no sudoers heredoc in the render"
    return m.group(1)


def _section(script):
    return script[script.index("# 3a."):script.index("# 3c.")]


# --------------------------------------------------------------------------- #
# (a) LAN reach entries — validator
# --------------------------------------------------------------------------- #
class TestLanReachValidator(unittest.TestCase):

    def _errs(self, *entries, **over):
        return bootstrap.validate_account(
            "proj", _valid_spec(reach=list(entries), **over))

    def test_private_host_with_a_reason_passes(self):
        for entry in (_lan(), _lan("192.168.1.10/32"), _lan("172.31.255.1/32"),
                      _lan("100.64.1.2/32", reason="tailscale non-fleet node"),
                      _lan("10.77.9.204/32", (22, 4455, 8898), "stream OBS")):
            self.assertEqual(self._errs(entry), [], entry)

    def test_fleet_name_and_lan_entries_mix(self):
        self.assertEqual(self._errs("gatekeeper", _lan()), [])

    def test_public_or_special_addresses_are_refused(self):
        for cidr in ("8.8.8.8/32", "0.0.0.0/32", "127.0.0.1/32",
                     "169.254.1.1/32", "224.0.0.1/32", "255.255.255.255/32",
                     "172.32.0.1/32", "100.128.0.1/32", "100.63.255.255/32",
                     "192.169.0.1/32", "11.0.0.1/32"):
            errs = self._errs(_lan(cidr))
            self.assertTrue(_entry_err(errs, "private"), (cidr, errs))

    def test_only_single_canonical_ipv4_hosts(self):
        # a range could silently include a password-shared box's LAN address
        for cidr in ("10.77.9.0/24", "10.0.0.0/8", "0.0.0.0/0", "10.77.9.60/30",
                     "10.77.9.60/31", "10.77.9.61/31",
                     "10.77.9.61", "10.77.9.61/32 ", " 10.77.9.61/32",
                     "10.77.9.61/032", "fd7a:115c:a1e0::1/128", "::/0",
                     "10.77.9.61/32\n", "cam1.lan/32", "", None, 42):
            errs = self._errs(_lan(cidr))
            self.assertTrue(_entry_err(errs, "cidr"), (cidr, errs))

    def test_a_reason_is_mandatory(self):
        for reason in ("", "   ", None, 7, "cam1\nsecond line", "cam\x001"):
            errs = self._errs(_lan(reason=reason))
            self.assertTrue(_entry_err(errs, "reason"), (reason, errs))
        entry = {"cidr": "10.77.9.61/32", "ports": [22]}
        self.assertTrue(_entry_err(self._errs(entry), "reason"))

    def test_ports_are_explicit_distinct_ints(self):
        for ports in ([], ["22"], [0], [65536], [-1], [True], [22, 22], "22",
                      ["*"], [None], [22.0], None):
            entry = {"cidr": "10.77.9.61/32", "ports": ports, "reason": "cam1"}
            errs = self._errs(entry)
            self.assertTrue(_entry_err(errs, "ports"), (ports, errs))

    def test_entry_keys_are_exact(self):
        for entry in ({"cidr": "10.77.9.61/32", "ports": [22], "reason": "x",
                       "user": "root"},
                      {"cidr": "10.77.9.61/32", "reason": "x"},
                      {"ports": [22], "reason": "x"}):
            self.assertTrue(_entry_err(self._errs(entry), "keys"), entry)

    def test_a_fleet_box_address_is_declared_by_name_only(self):
        # a cidr would bypass the password-shared / shared-account checks
        for addr in set(bootstrap.fleet_boxes().values()):
            if bootstrap._is_ip(addr):
                errs = self._errs(_lan(addr + "/32", reason="x"))
                self.assertTrue(_entry_err(errs, "fleet"), (addr, errs))

    def test_duplicate_destinations_are_refused(self):
        errs = self._errs(_lan(), _lan(ports=(8898,)))
        self.assertTrue(_entry_err(errs, "duplicate"), errs)

    def test_an_unenforced_account_still_validates_its_lan_entries(self):
        errs = self._errs(_lan("8.8.8.8/32"), reach_enforced=False,
                          reach_reason="x")
        self.assertTrue(_entry_err(errs, "private"), errs)


# --------------------------------------------------------------------------- #
# (a) LAN reach entries — nft render
# --------------------------------------------------------------------------- #
class TestLanReachRender(unittest.TestCase):

    def test_accepts_exactly_the_declared_destination_and_ports_before_reject(self):
        script = _render(_valid_spec(reach=[
            _lan("10.77.9.61/32", (22, 8898), "cam1"),
            _lan("10.77.7.30/32", (22,), "fohabl")]))
        rules = _ruleset(script)
        cam = 'meta skuid "projx" ip daddr 10.77.9.61 tcp dport { 22, 8898 } accept'
        foh = 'meta skuid "projx" ip daddr 10.77.7.30 tcp dport { 22 } accept'
        rej = 'meta skuid "projx" tcp dport 22 ct state new reject with tcp reset'
        # review 1: ssh to ANY address of this host is refused in the kernel on
        # every re-apply, even after a LAN address drifts onto a declared /32
        loc = ('meta skuid "projx" fib daddr type local tcp dport 22 reject '
               'with tcp reset')
        for line in (loc, cam, foh, rej):
            self.assertIn(line, rules)
        self.assertLess(rules.index(cam), rules.index(rej))
        self.assertLess(rules.index(foh), rules.index(rej))
        # the reject is the LAST rule; nothing but the declared hosts accepted
        body = [ln.strip() for ln in rules.splitlines()
                if ln.strip().startswith("meta skuid")]
        self.assertEqual(body, [loc, cam, foh, rej])
        self.assertEqual(set(re.findall(r"ip daddr (\S+)", rules)),
                         {"10.77.9.61", "10.77.7.30"})
        # every reason is carried as a comment, never inside a rule
        self.assertIn("# cam1", rules)
        self.assertIn("# fohabl", rules)
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fleet_names_and_lan_entries_render_together(self):
        rules = _ruleset(_render(_valid_spec(reach=["gatekeeper", _lan()])))
        self.assertIn('meta skuid "projx" tcp dport 22 ip daddr { 100.90.94.41 } '
                      'accept', rules)
        self.assertIn('meta skuid "projx" ip daddr 10.77.9.61 tcp dport { 22 } '
                      'accept', rules)

    def test_a_declared_host_that_is_this_hosts_own_address_is_refused(self):
        script = _render(_valid_spec(reach=[_lan()]))
        self.assertIn("ip -4 -o addr show", script)
        self.assertIn("is an address of this host", script)
        self.assertLess(script.index("is an address of this host"),
                        script.index("<< 'REACH_EOF'"))

    def test_the_own_address_check_fires(self):
        snippet = hardening.render_reach_self_address_check(["10.77.9.61"])
        fake_ip = ("ip() { printf '%s\\n' "
                   "'1: lo    inet 127.0.0.1/8 scope host lo' "
                   "'2: eth0    inet 10.77.9.61/24 brd 10.77.9.255 scope global eth0'; }\n")
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + fake_ip + snippet],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("10.77.9.61 is an address of this host", r.stderr)
        fake_ip = fake_ip.replace("10.77.9.61/24", "10.77.9.21/24")
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + fake_ip + snippet],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unenforced_account_renders_no_lan_rule(self):
        script = _render(_valid_spec(reach=[_lan()], reach_enforced=False,
                                     reach_reason="x"))
        self.assertNotIn("meta skuid", script)
        self.assertIn("reach: NOT enforced", script)

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
CAMERA_BOX_HOSTS = {
    # cam1-7 (ticket), dantesync on every camera
    **{"10.77.9.%d" % (60 + n): {22, 8898} for n in range(1, 8)},
    "10.77.9.202": {22, 4455, 8898, 8899},   # strih-lx (obs-fleet.sh)
    "10.77.9.204": {22, 4455, 8898, 8899},   # stream OBS (obs-fleet.sh)
    "10.77.7.232": {22, 8898},         # mbc (dantesync-fleet.sh)
    "10.77.7.30": {22, 8898},          # fohabl (dantesync-fleet.sh)
    "10.77.8.1": {22},                 # MikroTik RB4011 (netcfg facet)
    **{"10.77.9.%d" % n: {22} for n in range(2, 6)},   # 4x CRS310
}
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
        self.assertIs(spec["reach_enforced"], True)
        got = {e["cidr"].split("/")[0]: set(e["ports"]) for e in spec["reach"]}
        self.assertEqual(got, CAMERA_BOX_HOSTS)
        self.assertTrue(all(isinstance(e, dict) and e["reason"].strip()
                            for e in spec["reach"]))
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

    def test_render_contains_its_rules_and_nothing_broader(self):
        script = bootstrap.render_root_bootstrap("camera-box")
        rules = _ruleset(script)
        self.assertIn("table inet airuleset_reach_camera_box", rules)
        for ip, ports in CAMERA_BOX_HOSTS.items():
            want = 'meta skuid "camera-box" ip daddr %s tcp dport { %s } accept' % (
                ip, ", ".join(str(p) for p in sorted(ports)))
            self.assertIn(want, rules)
        self.assertEqual(set(re.findall(r"ip daddr (\S+)", rules)),
                         set(CAMERA_BOX_HOSTS))
        self.assertNotIn("/", "".join(re.findall(r"ip daddr (\S+)", rules)))
        last = [ln.strip() for ln in rules.splitlines()
                if ln.strip().startswith("meta skuid")][-1]
        self.assertEqual(last, 'meta skuid "camera-box" tcp dport 22 ct state '
                               'new reject with tcp reset')
        body = _sudoers_body(script).splitlines()
        self.assertEqual(body[1], "camera-box ALL=(root) NOPASSWD: "
                         + ", ".join('%s ""' % p for p in CAMERA_BOX_SUDO))
        self.assertEqual(len(body), 2)
        for broad in ("NOPASSWD: ALL", "(ALL)", "ALL, ", "*"):
            self.assertNotIn(broad, "\n".join(body))
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

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
        self.assertIn("10.77.9.61/32 tcp 22,8898", out)
        self.assertIn("10.77.9.202/32 tcp 22,4455,8898,8899 (only 22 enforced)",
                      out)
        self.assertIn("10.77.8.1/32 tcp 22 — ", out)   # nothing to qualify
        for path in CAMERA_BOX_SUDO:
            self.assertIn("sudo " + path, out)
        # the fohmixer/claudy lines keep their shape
        self.assertRegex(out, r"fohmixer\s+host=dev1\s+sudo=no\s+reach=none")

    def test_json(self):
        data = json.loads(self._run(True))
        cb = data["project_accounts"]["camera-box"]
        self.assertEqual(cb["sudo"], "commands")
        self.assertEqual(cb["sudo_commands"], CAMERA_BOX_SUDO)
        self.assertIn({"cidr": "10.77.9.61/32", "ports": [22, 8898],
                       "reason": "cam1 (camera box)"}, cb["reach"])
        self.assertIs(data["project_accounts"]["fohmixer"]["sudo"], False)
        self.assertEqual(data["project_accounts"]["fohmixer"]["sudo_commands"], [])


if __name__ == "__main__":
    unittest.main()
