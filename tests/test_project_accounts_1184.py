"""#1184: one system account per PROJECT — declaration, provisioning, gate, freeze.

Owner ROZHODNUTÉ 2026-09-29: every project and its Claude session run in a
dedicated unix account with an EXPLICIT declaration (host, sudo, reach, secrets,
webterm humans); no new project is created under the shared `newlevel` account;
the legacy newlevel inventory is frozen with a count that only goes down.

Covers the design acceptance (a)-(d):
  (a) the declaration validator (unknown host / sudo without a reason / reach
      outside the fleet all FAIL; the shipped table is clean);
  (b) the rendered fohmixer bootstrap: user creation, NO sudoers, the repo
      clone, the tmux session, forced-command lines for zbynek/marek/timo, and
      nothing but the fleet push + owner keys unrestricted;
  (c) onboard-project REFUSES a newlevel-owned target unless --legacy-ok;
  (d) every registry row carries `account`; `accounts status` lists the legacy
      inventory; the down-only lock holds.
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

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)
import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_onboard as ob  # noqa: E402
import cli_webterm_only as wo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "projects-registry.json"

# A syntactically valid, obviously-fake ed25519 pubkey used ONLY to exercise the
# timo forced-command line before the real key is minted at go-live.
_FAKE_TIMO_PUBKEY = "ssh-ed25519 " + "AAAA" + "X" * 64 + " webterm-timo-controller"


def _with_timo_key():
    return mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS,
                           {"timo": _FAKE_TIMO_PUBKEY})


def _valid_spec(**over):
    spec = {
        "host": "dev1", "sudo": False, "reach": [], "secrets": [],
        "webterm_sessions": {},
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------- #
# (a) the declaration + its validator
# --------------------------------------------------------------------------- #
class TestDeclarationValidator(unittest.TestCase):

    def test_shipped_table_is_clean(self):
        self.assertEqual(bootstrap.validate_all(), {})

    def test_valid_minimal_spec_has_no_errors(self):
        self.assertEqual(bootstrap.validate_account("proj", _valid_spec()), [])

    def test_unknown_host_fails(self):
        errs = bootstrap.validate_account("proj", _valid_spec(host="dve1"))
        self.assertTrue(any("host" in e for e in errs), errs)

    def test_sudo_without_reason_fails(self):
        errs = bootstrap.validate_account(
            "proj", _valid_spec(sudo=True, sudo_commands=["/usr/bin/systemctl"]))
        self.assertTrue(any("sudo_reason" in e for e in errs), errs)

    def test_sudo_without_scoped_commands_fails(self):
        errs = bootstrap.validate_account(
            "proj", _valid_spec(sudo=True, sudo_reason="restart its own unit"))
        self.assertTrue(any("sudo_commands" in e for e in errs), errs)

    def test_sudo_all_is_never_scoped(self):
        for bad in (["ALL"], ["/usr/bin/*"], ["systemctl"], ["/bin/sh, ALL"]):
            errs = bootstrap.validate_account(
                "proj", _valid_spec(sudo=True, sudo_reason="x",
                                    sudo_commands=bad))
            self.assertTrue(any("sudo_commands" in e for e in errs),
                            "%r accepted: %r" % (bad, errs))

    def test_sudo_fields_without_sudo_true_fail(self):
        # a stray grant never hides behind sudo=False
        errs = bootstrap.validate_account(
            "proj", _valid_spec(sudo_commands=["/usr/bin/systemctl"]))
        self.assertTrue(errs)

    def test_sudo_must_be_a_real_bool(self):
        errs = bootstrap.validate_account("proj", _valid_spec(sudo="no"))
        self.assertTrue(any("sudo" in e for e in errs), errs)

    def test_reach_outside_the_fleet_fails(self):
        errs = bootstrap.validate_account(
            "proj", _valid_spec(reach=["root@evil.example.com"]))
        self.assertTrue(any("reach" in e for e in errs), errs)

    def test_reach_inside_the_fleet_passes(self):
        self.assertEqual(bootstrap.validate_account(
            "proj", _valid_spec(reach=["dev2", "david1@subdev"])), [])

    def test_reach_into_a_shared_account_fails(self):
        # review 1: `reach` never points INTO newlevel/root/control accounts
        for bad in ("newlevel@dev2", "root@dev1", "airuleset@controller"):
            errs = bootstrap.validate_account("proj", _valid_spec(reach=[bad]))
            self.assertTrue(any("shared/control" in e for e in errs), (bad, errs))

    def test_reach_to_a_dns_named_box_cannot_be_enforced(self):
        errs = bootstrap.validate_account(
            "proj", _valid_spec(reach=["forestshop-dev"]))
        self.assertTrue(any("not an IP" in e for e in errs), errs)

    def test_a_trailing_newline_is_never_accepted(self):
        # review 1: `$` matches before a final \n — every value uses fullmatch
        for over in ({"tmux_session": "proj\n", "project_dir": "devel/proj"},
                     {"repo": "o/r\n", "project_dir": "devel/r"},
                     {"project_dir": "devel/proj\n"},
                     {"secrets": ["tok\n"]},
                     {"webterm_sessions": {"zbynek": {"preferred": "p\n"}}}):
            self.assertTrue(bootstrap.validate_account("proj", _valid_spec(**over)),
                            over)
        self.assertTrue(bootstrap.validate_account("proj\n", _valid_spec()))
        errs = bootstrap.validate_account("proj", _valid_spec(
            sudo=True, sudo_reason="x", sudo_commands=["/usr/bin/systemctl restart a\n"]))
        self.assertTrue(any("sudo_commands" in e for e in errs), errs)

    def test_root_equivalent_sudo_commands_are_refused(self):
        for bad in ("/bin/bash -c true", "/usr/bin/env ls", "/usr/bin/vim /etc/x",
                    "/usr/bin/python3.12 x.py", "/usr/bin/find / -name x",
                    "/usr/bin/less /var/log/syslog", "/usr/bin/systemctl"):
            errs = bootstrap.validate_account("proj", _valid_spec(
                sudo=True, sudo_reason="x", sudo_commands=[bad]))
            self.assertTrue(any("sudo_commands" in e for e in errs), (bad, errs))

    def test_an_existing_fleet_account_can_never_be_declared(self):
        # review 1: declaring a stream/webterm account would REPLACE its keys
        for name, host in (("dominika", "subdev"), ("david1", "subdev"),
                           ("montalu3", "subdev"), ("marek", "dev1"),
                           ("admin", "dev1")):
            errs = bootstrap.validate_account(name, _valid_spec(host=host))
            self.assertTrue(any("existing" in e for e in errs), (name, errs))

    def test_declared_session_is_the_session_every_tab_attaches(self):
        errs = bootstrap.validate_account("proj", _valid_spec(
            tmux_session="proj", project_dir="devel/proj",
            webterm_sessions={"zbynek": {"preferred": "zbynek"}}))
        self.assertTrue(any("tmux_session" in e for e in errs), errs)

    def test_account_spec_never_shares_mutable_defaults(self):
        spec = bootstrap.account_spec("fohmixer")
        spec["webterm_sessions"]["mallory"] = {}
        spec["reach"].append("dev2")
        self.assertNotIn("mallory", bootstrap.account_spec("fohmixer")["webterm_sessions"])
        self.assertEqual(list(bootstrap.account_spec("fohmixer")["reach"]), [])

    def test_bad_secret_name_fails(self):
        errs = bootstrap.validate_account(
            "proj", _valid_spec(secrets=["../../etc/shadow"]))
        self.assertTrue(any("secrets" in e for e in errs), errs)

    def test_unknown_key_fails(self):
        # a typo (`sudoo`) must never be silently ignored
        errs = bootstrap.validate_account("proj", _valid_spec(sudoo=True))
        self.assertTrue(any("sudoo" in e for e in errs), errs)

    def test_unknown_webterm_human_fails(self):
        errs = bootstrap.validate_account("proj", _valid_spec(
            webterm_sessions={"mallory": {"preferred": "proj"}}))
        self.assertTrue(any("mallory" in e for e in errs), errs)

    def test_shared_accounts_can_never_be_declared(self):
        for name in ("newlevel", "root", "airuleset", "gatekeeper"):
            self.assertTrue(bootstrap.validate_account(name, _valid_spec()),
                            name)

    def test_render_refuses_an_invalid_declaration(self):
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS,
                             {"_bad": _valid_spec(host="nowhere")}):
            with self.assertRaises(ValueError):
                bootstrap.render_root_bootstrap("_bad")

    def test_claudy_declaration_is_explicit(self):
        spec = bootstrap.account_spec("claudy")
        self.assertEqual(spec["host"], "controller")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(list(spec["reach"]), [])
        self.assertEqual(list(spec["secrets"]), [])

    def test_fohmixer_declaration(self):
        spec = bootstrap.account_spec("fohmixer")
        self.assertEqual(spec["host"], "dev1")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(list(spec["reach"]), [])
        self.assertEqual(list(spec["secrets"]), [])
        self.assertEqual(set(spec["webterm_sessions"]),
                         {"zbynek", "marek", "timo"})
        for human, sess in spec["webterm_sessions"].items():
            # ONE shared project session (owner: people share the project's
            # session, never a per-person account/session)
            self.assertEqual(sess["preferred"], "fohmixer", human)
            self.assertEqual(sess["start_dir_chain"], ["devel/fohmixer"], human)
        self.assertEqual(spec["repo"], "zbynekdrlik/fohmixer")
        self.assertEqual(spec["project_dir"], "devel/fohmixer")
        self.assertEqual(spec["tmux_session"], "fohmixer")


# --------------------------------------------------------------------------- #
# (b) provisioning render
# --------------------------------------------------------------------------- #
class TestFohmixerRender(unittest.TestCase):

    def setUp(self):
        with _with_timo_key():
            self.script = bootstrap.render_root_bootstrap("fohmixer")
            self.keys = bootstrap.desired_keys_for_service_account("fohmixer")

    def test_valid_bash(self):
        r = subprocess.run(["bash", "-n"], input=self.script,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("set -euo pipefail", self.script)

    def test_creates_the_user_idempotently(self):
        self.assertIn("useradd -m -s /bin/bash -U \"$ACCOUNT\"", self.script)
        self.assertIn("ACCOUNT='fohmixer'", self.script)
        self.assertIn("if id \"$ACCOUNT\"", self.script)

    def test_is_host_generic_not_controller_bound(self):
        self.assertIn("Run as root on dev1", self.script)
        self.assertNotIn("100.101.214.103", self.script)
        self.assertIn("$ACCOUNT@100.104.8.125", self.script)

    def test_no_sudoers_for_a_sudo_less_account(self):
        self.assertNotIn("NOPASSWD", self.script)
        self.assertNotIn("visudo", self.script)
        # ...and an existing sudo route FAILS the bootstrap loudly, in a fixed
        # locale (a translated `sudo -l` would otherwise pass silently)
        self.assertIn("LC_ALL=C sudo -n -l -U \"$ACCOUNT\"", self.script)
        self.assertIn("declared sudo: NO", self.script)
        for group in ("sudo", "docker", "lxd", "disk"):
            self.assertIn('*" %s "*' % group, self.script)

    def test_foreign_preexisting_account_is_never_rewritten(self):
        self.assertIn("/etc/airuleset/project-accounts/$ACCOUNT", self.script)
        self.assertIn("refusing to rewrite a foreign account", self.script)
        self.assertLess(self.script.index("# 1b. Ownership marker"),
                        self.script.index("# 5. authorized_keys"))

    def test_reach_none_rejects_every_new_outbound_ssh_for_the_uid(self):
        self.assertIn('meta skuid "fohmixer" tcp dport 22 ct state new reject',
                      self.script)
        self.assertNotIn("accept\n", self.script.split("REACH_EOF")[1])
        self.assertIn('"$NFT" -c -f "$REACH_NFT"', self.script)
        self.assertIn("airuleset-reach-$ACCOUNT.service", self.script)

    def test_su_and_polkit_are_locked_for_the_account(self):
        self.assertIn("pam_wheel.so deny use_uid group=airuleset-project",
                      self.script)
        self.assertIn('usermod -aG airuleset-project "$ACCOUNT"', self.script)
        self.assertIn('subject.isInGroup("airuleset-project")', self.script)

    def test_boundary_lands_before_the_account_gets_a_shell(self):
        for step in ("# 9. Sudo policy", "# 10. Declared reach",
                     "# 11. su / polkit lockout"):
            self.assertLess(self.script.index(step),
                            self.script.index("# 12. Project checkout"), step)
            self.assertLess(self.script.index(step),
                            self.script.index("# 13. Project tmux session"), step)

    def test_clones_the_project_repo(self):
        self.assertIn("https://github.com/zbynekdrlik/fohmixer.git", self.script)
        self.assertIn("GIT_TERMINAL_PROMPT=0", self.script)
        self.assertIn("devel/fohmixer", self.script)

    def test_creates_the_project_tmux_session(self):
        self.assertIn("tmux new-session -d -s fohmixer", self.script)
        self.assertIn("tmux has-session -t =fohmixer", self.script)

    def test_forced_command_lines_for_every_declared_human(self):
        lanes = [k for k in self.keys if k.startswith("restrict,pty,command=")]
        comments = {k.split()[-1] for k in lanes}
        self.assertEqual(comments, {"webterm-zbynek-controller",
                                    "webterm-marek-controller",
                                    "webterm-timo-controller"})

    def test_forced_command_only_attaches_the_project_tmux_session(self):
        from cli_webterm import _remote_command
        want = _remote_command("fohmixer", start_dir_chain=["devel/fohmixer"])
        want_escaped = want.replace("\\", "\\\\").replace('"', '\\"')
        for k in self.keys:
            if not k.startswith("restrict"):
                continue
            m = re.match(r'^restrict,pty,command="(.*)" ssh-ed25519 ', k)
            self.assertIsNotNone(m, k)
            self.assertEqual(m.group(1), want_escaped)

    def test_nothing_else_is_unrestricted(self):
        from cli_owner_keys import OWNER_PUBKEYS
        allowed_plain = set(wo.FLEET_PUSH_PUBKEYS) | set(OWNER_PUBKEYS)
        for k in self.keys:
            self.assertTrue(k.startswith("restrict,pty,command=")
                            or k in allowed_plain, k)

    def test_render_refuses_a_declared_human_without_a_key(self):
        # timo's controller key is minted at go-live; until then the render
        # FAILS LOUD instead of silently dropping his tab
        self.assertNotIn("timo", wo.WEBTERM_CONTROLLER_LANE_PUBKEYS)
        with self.assertRaises(ValueError) as cm:
            bootstrap.render_root_bootstrap("fohmixer")
        self.assertIn("timo", str(cm.exception))


class TestSudoRender(unittest.TestCase):

    def test_sudo_true_writes_a_scoped_checked_sudoers_file(self):
        spec = _valid_spec(sudo=True, sudo_reason="restart its own unit",
                           sudo_commands=["/usr/bin/systemctl restart proj.service"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx": spec}):
            script = bootstrap.render_root_bootstrap("projx")
        self.assertIn("/etc/sudoers.d/$ACCOUNT", script)
        self.assertIn("visudo -cf", script)
        self.assertIn("projx ALL=(root) NOPASSWD:NOEXEC: "
                      "/usr/bin/systemctl restart proj.service", script)
        self.assertIn('*" sudo "*', script)   # group check in BOTH branches
        self.assertIn("restart its own unit", script)
        self.assertNotIn("declared sudo: NO", script)
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_claudy_render_still_has_no_sudo_grant(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertNotIn("NOPASSWD", script)
        self.assertIn("declared sudo: NO", script)

    def test_declared_reach_is_the_only_ssh_egress(self):
        spec = _valid_spec(reach=["dev2", "david1@subdev"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx": spec}):
            script = bootstrap.render_root_bootstrap("projx")
        self.assertIn('meta skuid "projx" tcp dport 22 ip daddr '
                      '{ 100.118.174.27, 100.82.64.27 } accept', script)
        self.assertIn('meta skuid "projx" tcp dport 22 ct state new reject', script)


# --------------------------------------------------------------------------- #
# (c) the onboarding gate
# --------------------------------------------------------------------------- #
def _fs_runner(resolved, owner):
    """A runner answering `realpath -e` + `stat -c %U` like the target box."""
    def run(argv, **kw):
        cmd = argv[-1] if argv[0] == "ssh" else " ".join(argv)   # ssh-wrapped or local
        if cmd.startswith("realpath "):
            return subprocess.CompletedProcess(argv, 0, resolved + "\n", "")
        if cmd.startswith("stat "):
            return subprocess.CompletedProcess(argv, 0, owner + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")
    return run


class TestAccountForTarget(unittest.TestCase):

    def test_owner_of_the_resolved_dir_not_the_typed_path(self):
        # review 1 🔴: `/home/fohmixer/../newlevel/x` must read as newlevel
        run = _fs_runner("/home/newlevel/devel/new", "newlevel")
        self.assertEqual(accounts.account_for_target(
            "/home/fohmixer/../newlevel/devel/new", run=run), "newlevel")

    def test_real_directory_owner(self):
        import pwd
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(accounts.account_for_target(d),
                             pwd.getpwuid(os.stat(d).st_uid).pw_name)

    def test_undeterminable_owner_is_none(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "")  # noqa: E731
        self.assertIsNone(accounts.account_for_target("/nope", run=run))


class TestOnboardGate(unittest.TestCase):

    ROW = {"name": "camera-box", "account": "newlevel"}

    def test_newlevel_target_is_refused(self):
        err = accounts.onboard_account_gate("newlevel", None, host="dev1")
        self.assertIsNotNone(err)
        self.assertIn("account-bootstrap", err)
        self.assertIn("1184", err)

    def test_every_undeclared_account_is_refused(self):
        # review 1 🔴: an ALLOW-list — the control/stream accounts too
        for acct, host in (("airuleset", None), ("root", "dev1"),
                           ("gatekeeper", "gatekeeper"), ("david1", "david1@subdev"),
                           ("montalu3", "montalu3@subdev")):
            self.assertIsNotNone(
                accounts.onboard_account_gate(acct, None, host=host), acct)

    def test_legacy_ok_is_bookkeeping_for_a_registered_project_only(self):
        self.assertIsNone(accounts.onboard_account_gate(
            "newlevel", "#1184", host="dev1", existing=self.ROW))
        self.assertIsNone(accounts.onboard_account_gate(
            "newlevel", "zbynekdrlik/fohmixer#12", host="dev1", existing=self.ROW))
        # a brand-new project never passes, whatever the ticket
        self.assertIsNotNone(accounts.onboard_account_gate(
            "newlevel", "#1184", host="dev1", existing=None))

    def test_legacy_ok_must_name_a_ticket(self):
        for bad in ("yes", "#12x", "12 13"):
            self.assertIsNotNone(accounts.onboard_account_gate(
                "newlevel", bad, host="dev1", existing=self.ROW), bad)

    def test_declared_project_account_on_its_host_is_allowed(self):
        self.assertIsNone(accounts.onboard_account_gate(
            "fohmixer", None, host="fohmixer@dev1", login_user="fohmixer"))
        self.assertIsNotNone(accounts.onboard_account_gate(
            "fohmixer", None, host="dev2", login_user="fohmixer"))

    def test_onboarding_must_run_as_the_owner(self):
        err = accounts.onboard_account_gate("fohmixer", None, host="dev1",
                                            login_user="newlevel")
        self.assertIn("run as 'newlevel'", err)

    def test_unknown_owner_is_refused(self):
        self.assertIsNotNone(accounts.onboard_account_gate(None, "#1184",
                                                           existing=self.ROW))

    def test_onboard_project_refuses_a_newlevel_path(self):
        with mock.patch.object(ob, "_remote_preflight",
                               return_value=("/home/newlevel/devel/x", None)):
            r = ob.onboard_project(
                "~/devel/x", host="dev1", registry_path="/nonexistent/r.json",
                dry_run=True, run=_fs_runner("/home/newlevel/devel/x", "newlevel"))
        self.assertIsNotNone(r["error"])
        self.assertIn("newlevel", r["error"])
        self.assertEqual(r["steps"], [])

    def test_onboard_project_refuses_a_dotdot_escape(self):
        typed = "/home/fohmixer/../newlevel/devel/new"
        with mock.patch.object(ob, "_remote_preflight", return_value=(typed, None)):
            r = ob.onboard_project(
                typed, host="dev1", registry_path="/nonexistent/r.json",
                dry_run=True, run=_fs_runner("/home/newlevel/devel/new", "newlevel"))
        self.assertIn("'newlevel'", r["error"])

    def test_onboard_project_cli_has_legacy_ok(self):
        import airuleset
        seen = {}

        def fake(args):
            seen["legacy_ok"] = args.legacy_ok
            return 0
        argv = ["airuleset.py", "onboard-project", "x", "--legacy-ok", "#1184"]
        with mock.patch.dict(airuleset.SUBCOMMANDS, {"onboard-project": fake}), \
                mock.patch.object(sys, "argv", argv):
            airuleset.main()
        self.assertEqual(seen["legacy_ok"], "#1184")

    def test_cmd_onboard_project_threads_legacy_ok(self):
        captured = {}

        def fake_onboard(path, **kw):
            captured.update(kw)
            return {"name": "x", "stack": None, "steps": [], "entry": None,
                    "error": "stop"}
        with mock.patch.object(ob, "onboard_project", fake_onboard):
            ob.cmd_onboard_project(mock.Mock(
                path="x", host=None, name=None, override=[], dry_run=True,
                audit=False, registry="/nonexistent/r.json",
                legacy_ok="#1184"))
        self.assertEqual(captured.get("legacy_ok"), "#1184")

    def test_registry_entry_records_the_account(self):
        entry = ob.build_registry_entry(
            "/home/fohmixer/devel/fohmixer", "/home/fohmixer/devel/fohmixer",
            None, "fohmixer", [], None, onboarded_date="2026-09-29",
            run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""),
            account="fohmixer")
        self.assertEqual(entry["account"], "fohmixer")


# --------------------------------------------------------------------------- #
# (d) freeze + inventory + down-only lock
# --------------------------------------------------------------------------- #
class TestRegistryAccounts(unittest.TestCase):

    def setUp(self):
        self.entries = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_every_row_has_an_account(self):
        for e in self.entries:
            self.assertIn("account", e, e["name"])
            self.assertTrue(e["account"], e["name"])

    def test_dev_box_rows_are_legacy_newlevel(self):
        for e in self.entries:
            if e["host"] in ("dev1", "dev2"):
                self.assertEqual(e["account"], "newlevel", e["name"])

    def test_known_project_accounts(self):
        by = {e["name"]: e for e in self.entries}
        self.assertEqual(by["claudy"]["account"], "claudy")
        self.assertEqual(by["odoo-erp"]["account"], "gatekeeper")

    def test_down_only_lock(self):
        # The legacy count may only go DOWN: an exact match forces whoever
        # migrates a project to lower the ceiling in the same change, and a
        # raise is a visible, reviewable edit of this constant.
        n = len(accounts.legacy_inventory(self.entries))
        self.assertEqual(n, accounts.LEGACY_CEILING,
                         "legacy newlevel count %d != ceiling %d — lower "
                         "LEGACY_CEILING after a migration, never raise it"
                         % (n, accounts.LEGACY_CEILING))

    def test_ceiling_never_above_the_freeze_snapshot(self):
        # 24 newlevel rows + odoo-erp in `gatekeeper` (no declared account)
        self.assertLessEqual(accounts.LEGACY_CEILING, 25)

    def test_legacy_means_not_a_declared_project_account(self):
        rows = accounts.legacy_inventory(self.entries)
        self.assertIn("odoo-erp", {r["name"] for r in rows})
        self.assertNotIn("claudy", {r["name"] for r in rows})

    def test_no_legacy_row_newer_than_the_freeze(self):
        for row in accounts.legacy_inventory(self.entries):
            self.assertLessEqual(row["onboarded"] or "",
                                 accounts.LEGACY_FREEZE_DATE, row["name"])


class TestAccountsStatusCli(unittest.TestCase):

    def test_subcommand_is_registered(self):
        import airuleset
        self.assertIs(airuleset.SUBCOMMANDS["accounts"], accounts.cmd_accounts)

    def test_status_lists_the_legacy_inventory_and_count(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = accounts.cmd_accounts(mock.Mock(action="status", json=False,
                                                 registry=None))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("fohmixer", out)
        self.assertIn("camera-box", out)
        self.assertIn("legacy projects (no declared project account): %d"
                      % accounts.LEGACY_CEILING, out)
        # project accounts are listed with their declaration
        self.assertIn("claudy", out)

    def test_status_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            accounts.cmd_accounts(mock.Mock(action="status", json=True,
                                            registry=None))
        data = json.loads(buf.getvalue())
        self.assertEqual(data["legacy_count"], accounts.LEGACY_CEILING)
        self.assertIn("fohmixer", data["project_accounts"])
        self.assertIs(data["project_accounts"]["fohmixer"]["sudo"], False)


class TestLegacyNotice(unittest.TestCase):
    """(d) the migrate-on-touch notice is DATA in the existing trigger table."""

    def _rows(self):
        conf = (ROOT / "hooks" / "situational-triggers.conf").read_text(
            encoding="utf-8")
        return [ln.split("\t") for ln in conf.splitlines()
                if ln and not ln.startswith("#")]

    def test_row_exists_and_body_exists(self):
        rows = [r for r in self._rows() if r[0] == "legacy-newlevel-project"]
        self.assertEqual(len(rows), 1)
        topic, tool, pattern, body = rows[0][:4]
        self.assertTrue((ROOT / body).exists(), body)
        self.assertIn("1184", (ROOT / body).read_text(encoding="utf-8"))

    def _inject(self, tool, tool_input):
        payload = json.dumps({"hook_event_name": "PreToolUse",
                              "tool_name": tool, "tool_input": tool_input,
                              "session_id": "t1184-%s" % abs(hash(
                                  json.dumps(tool_input)))})
        r = subprocess.run(["bash", str(ROOT / "hooks" /
                                        "inject-situational-rule.sh")],
                           input=payload, capture_output=True, text=True,
                           env=hermetic_hook_env(self, TMPDIR=self._tmp.name))
        return r.stdout

    def test_editing_a_newlevel_project_injects_the_notice(self):
        out = self._inject("Edit", {"file_path":
                                    "/home/newlevel/devel/camera-box/src/x.rs",
                                    "old_string": "a", "new_string": "b"})
        self.assertIn("legacy newlevel project", out)

    def test_editing_a_project_account_file_injects_nothing(self):
        out = self._inject("Edit", {"file_path":
                                    "/home/fohmixer/devel/fohmixer/x.py",
                                    "old_string": "a", "new_string": "b"})
        self.assertNotIn("legacy newlevel project", out)

    def test_exclude_is_anchored_to_the_edited_file(self):
        # review 1: an airuleset path merely MENTIONED in the edit text must
        # not suppress the notice for a legacy project file
        out = self._inject("Edit", {"file_path":
                                    "/home/newlevel/devel/camera-box/README.md",
                                    "old_string": "a",
                                    "new_string": "/home/newlevel/devel/airuleset/x"})
        self.assertIn("legacy newlevel project", out)

    def test_the_airuleset_deploy_clone_is_excluded(self):
        out = self._inject("Edit", {"file_path":
                                    "/home/newlevel/devel/airuleset/x.py",
                                    "old_string": "a", "new_string": "b"})
        self.assertNotIn("legacy newlevel project", out)

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
