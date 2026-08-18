"""Tests for the web terminal gateway (#555, cli_webterm.py).

Covers: inventory generation from the fleet table (never a hand list), the
per-host ssh identity rule matching the deploy loop, the connect-argv shapes
(local sh -c / identity ssh / sshpass ssh), the connect allowlist (an unknown
id is REFUSED, never execed — the security-critical path), dashboard/launcher/
unit rendering, and the dev1-only provisioning gate.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402


# A small, controlled fleet: one owner box (identity), one owner box (no
# identity/sshpass), one stream (identity), one stream (no identity), and one
# PENDING host that must be filtered out.
_FAKE_HOSTS = [
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "gatekeeper", "host": "10.0.0.9", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "david@subdev", "host": "10.0.0.5", "user": "david",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "montalu@subdev", "host": "10.0.0.5", "user": "montalu"},
    {"name": "ghost@subdev", "host": "10.0.0.5", "user": "ghost", "pending": True},
]
_FAKE_AUTHORITY = {"david": "fork-no-merge", "montalu": "branch-merge"}


def _fake_inventory():
    import airuleset
    with m.patch.object(airuleset, "REMOTE_HOSTS", _FAKE_HOSTS), \
            m.patch.object(airuleset, "AUTHORITY_BY_USER", _FAKE_AUTHORITY):
        return w.webterm_inventory()


class TestInventory(unittest.TestCase):
    def test_generated_from_fleet_plus_dev1_never_hand_list(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        # dev1 (localhost) is always first; the pending ghost host is filtered.
        self.assertEqual(ids[0], "dev1")
        self.assertIn("dev2", ids)
        self.assertIn("gatekeeper", ids)
        self.assertIn("david-subdev", ids)     # `@` sanitized to `-`
        self.assertIn("montalu-subdev", ids)
        self.assertNotIn("ghost-subdev", ids)   # pending -> excluded
        self.assertEqual(len(inv), 5)           # dev1 + 4 live hosts

    def test_ids_unique_and_url_safe(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9-]+$")  # no @, no ., URL-safe

    def test_dev1_is_local(self):
        dev1 = next(e for e in _fake_inventory() if e["id"] == "dev1")
        self.assertTrue(dev1["local"])
        self.assertEqual(dev1["preferred"], "zbynek")
        self.assertEqual(dev1["kind"], "owner")

    def test_preferred_group_stream_is_user_owner_is_zbynek(self):
        inv = {e["id"]: e for e in _fake_inventory()}
        # stream accounts -> their unix user (the #264 whoami convention)
        self.assertEqual(inv["david-subdev"]["preferred"], "david")
        self.assertEqual(inv["david-subdev"]["kind"], "stream")
        self.assertEqual(inv["montalu-subdev"]["preferred"], "montalu")
        # owner boxes -> the owner group
        self.assertEqual(inv["dev2"]["preferred"], "zbynek")
        self.assertEqual(inv["dev2"]["kind"], "owner")
        self.assertEqual(inv["gatekeeper"]["preferred"], "zbynek")

    def test_identity_decision_matches_deploy_loop(self):
        # The web terminal's identity-vs-sshpass DECISION must not drift from the
        # deploy loop's: an entry has an identity iff its fleet row does.
        inv = {e["id"]: e for e in _fake_inventory()}
        self.assertEqual(inv["david-subdev"]["identity"],
                         "~/.secrets/gatekeeper_access_ed25519")
        self.assertIsNone(inv["montalu-subdev"]["identity"])
        self.assertIsNone(inv["dev2"]["identity"])


class TestConnectArgv(unittest.TestCase):
    def test_local_is_sh_c_with_attach_snippet(self):
        e = {"id": "dev1", "local": True, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sh")
        self.assertEqual(argv[1], "-c")
        self.assertIn("P=zbynek", argv[2])
        self.assertIn("new-session -A -s", argv[2])
        # No ssh for a local target.
        self.assertNotIn("ssh", argv)

    def test_identity_host_uses_ssh_i_with_pty(self):
        e = {"local": False, "user": "david", "host": "10.0.0.5",
             "identity": "~/.secrets/gatekeeper_access_ed25519", "preferred": "david"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser("~/.secrets/gatekeeper_access_ed25519"), argv)
        self.assertIn("-t", argv)                       # force a PTY
        self.assertIn("david@10.0.0.5", argv)
        self.assertNotIn("sshpass", argv)

    def test_no_identity_host_uses_sshpass(self):
        e = {"local": False, "user": "newlevel", "host": "10.0.0.2",
             "identity": None, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sshpass")
        self.assertIn("ssh", argv)
        self.assertIn("-t", argv)
        self.assertIn("newlevel@10.0.0.2", argv)

    def test_preferred_is_shell_quoted(self):
        # A pathological preferred value must be shell-quoted, never injected.
        e = {"id": "x", "local": True, "preferred": "a; rm -rf /"}
        argv = w.build_connect_argv(e)
        self.assertIn("'a; rm -rf /'", argv[2])


class TestConnectAllowlist(unittest.TestCase):
    """The security-critical path: connect_main validates the id against the
    generated inventory, never interpolates an unknown value into a shell."""

    def _inv_file(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "inv.json"
        p.write_text(json.dumps([
            {"id": "dev1", "local": True, "preferred": "zbynek"},
        ]), encoding="utf-8")
        return p

    def test_unknown_id_is_refused_and_never_execs(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")) as ex:
            rc = w.connect_main(["totally-unknown"], inventory_path=p)
        self.assertEqual(rc, 2)
        ex.assert_not_called()

    def test_no_id_is_refused(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main([], inventory_path=p)
        self.assertEqual(rc, 2)

    def test_known_id_execs_the_built_argv(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["dev1"], inventory_path=p)
        ex.assert_called_once()
        cmd = ex.call_args[0][1]
        self.assertEqual(cmd[0], "sh")
        self.assertIn("P=zbynek", cmd[2])

    def test_unreadable_inventory_is_refused(self):
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main(["dev1"], inventory_path="/no/such/inventory.json")
        self.assertEqual(rc, 2)


class TestRendering(unittest.TestCase):
    def test_dashboard_has_cards_and_ttyd_links(self):
        inv = [
            {"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
             "local": True, "host": None, "user": None},
            {"id": "david-subdev", "label": "david@subdev", "kind": "stream",
             "local": False, "host": "10.0.0.5", "user": "david"},
        ]
        html = w.render_dashboard_html(inv, ttyd_base="http://box:7682")
        self.assertIn("http://box:7682/?arg=dev1", html)
        self.assertIn("http://box:7682/?arg=david-subdev", html)
        self.assertIn("Moje boxy", html)
        self.assertIn("Subdev streamy", html)
        self.assertIn('target="_blank"', html)   # one tab per session
        self.assertIn("david@subdev", html)

    def test_dashboard_escapes_labels(self):
        inv = [{"id": "x", "label": "<script>", "kind": "owner",
                "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_launch_script_execs_ttyd_with_connect(self):
        s = w.render_webterm_launch_script()
        self.assertIn("exec ttyd", s)
        self.assertIn("-p %d" % w.WEBTERM_TTYD_PORT, s)
        self.assertIn("-i 127.0.0.1", s)
        self.assertIn("-a", s)      # url-arg
        self.assertIn("-W", s)      # writable
        self.assertIn("cli_webterm.py webterm-connect", s)
        self.assertIn(str(w.WEBTERM_CRED_PATH), s)

    def test_unit_substitutes_launch_script_placeholder(self):
        unit = w._render_webterm_unit()
        self.assertNotIn("{{LAUNCH_SCRIPT}}", unit)
        self.assertIn(str(w.WEBTERM_LAUNCH_PATH), unit)
        self.assertIn("WantedBy=default.target", unit)


class TestProvisioningGate(unittest.TestCase):
    def test_setup_is_dev1_only(self):
        fake = os.uname_result(("Linux", "dev2", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertFalse(w.is_webterm_gateway())
            self.assertFalse(w.setup_webterm_service())

    def test_gateway_true_on_dev1(self):
        fake = os.uname_result(("Linux", "dev1", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertTrue(w.is_webterm_gateway())


if __name__ == "__main__":
    unittest.main()
