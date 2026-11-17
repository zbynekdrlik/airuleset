"""#1334 — two more peer links on subdev, mirroring #1320 (devbridge <-> david3).

* codex-bridge (dev2, shared ``newlevel``, the #1293 presenter precedent) reaches
  david3 over a bridge whose far end is ``codex-bridge-peer@subdev``;
* david4 joins ``hosts.subdev.streams`` and is linked to devbridge.

The bridge key is the home account's public key. It must be a real ed25519 key:
``FAR_KEY_RE`` alone accepts any base64-looking word, so a placeholder or a
truncated paste would reach ``authorized_keys``. ``far_key_ok`` decodes the blob.
"""
import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_peer_channel as cpc  # noqa: E402
import peer_channel as pc  # noqa: E402

SUBDEV_IP = "100.118.174.27"
REAL_KEY = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGkRnAroNZk+tD6jrYVbJUalRDoBk/pxTiLwB/rUZvfW "
            "presenter-peer@dev2")


def _blob(type_name=b"ssh-ed25519", key=b"\x01" * 32):
    raw = (len(type_name).to_bytes(4, "big") + type_name
           + len(key).to_bytes(4, "big") + key)
    return base64.b64encode(raw).decode()


class TestFarKey(unittest.TestCase):
    def test_a_real_ed25519_key_passes(self):
        self.assertTrue(cpc.far_key_ok(REAL_KEY))
        self.assertTrue(cpc.far_key_ok("ssh-ed25519 %s c@h" % _blob()))

    def test_a_placeholder_or_a_broken_paste_is_refused(self):
        for bad in ("ssh-ed25519 AAAA c@h",
                    "ssh-ed25519 PLACEHOLDER-generate-on-dev2 c@h",
                    "ssh-ed25519 PLACEHOLDERgenerateondev2 codex-bridge-peer@dev2",
                    REAL_KEY.replace("AAAAIGkR", "AAAAIGk"),          # truncated blob
                    "ssh-ed25519 %s c@h" % _blob(type_name=b"ssh-rsa"),
                    "ssh-ed25519 %s c@h" % _blob(key=b"\x01" * 31),
                    "ssh-ed25519 %s c@h" % _blob(key=b"\x01" * 33),
                    "ssh-ed25519 %s! c@h" % _blob(),
                    "", None, 7):
            self.assertFalse(cpc.far_key_ok(bad), bad)

    def test_validate_links_refuses_a_bridge_with_a_non_key(self):
        doc = {"hosts": {"h": {"links": []}},
               "bridges": {"x": {"account": "a", "home": "dev2", "path": "~/x",
                                 "hosts": {"h": {"account": "far", "ssh": "far@10.0.0.1",
                                                 "identity": "~/k", "path": cpc.SNAPSHOT_DIR,
                                                 "key": "ssh-ed25519 PLACEHOLDERx c@h"}}}}}
        errs = cpc.validate_links(doc, [], {"h", "dev2"})
        self.assertTrue([e for e in errs if "bridge x/h" in e], errs)
        doc["bridges"]["x"]["hosts"]["h"]["key"] = REAL_KEY
        self.assertEqual(cpc.validate_links(doc, [], {"h", "dev2"}), [])


class TestCommitted(unittest.TestCase):
    def setUp(self):
        self.doc, self.reg = cpc.load_links(), cpc.load_registry()
        self.sub = self.doc["hosts"]["subdev"]

    def test_the_committed_config_is_valid(self):
        # fails while the codex-bridge home key is still the placeholder
        self.assertEqual(cpc.validate_links(self.doc, self.reg), [])

    def test_every_committed_bridge_key_is_a_real_key(self):
        for name, spec in self.doc["bridges"].items():
            for host, far in spec["hosts"].items():
                self.assertTrue(cpc.far_key_ok(far["key"]),
                                "%s/%s key is not a real ed25519 key: %s" % (name, host, far["key"]))

    def test_the_codex_bridge_bridge_mirrors_presenter(self):
        spec = self.doc["bridges"]["codex-bridge"]
        self.assertEqual((spec["account"], spec["home"], spec["path"]),
                         ("newlevel", "dev2", "~/devel/codex-bridge"))
        row = {p["name"]: p for p in self.reg}["codex-bridge"]
        self.assertEqual((row["host"], row["account"], row["path"]),
                         ("dev2", spec["account"], spec["path"]))
        self.assertEqual(sorted(spec["hosts"]), ["subdev"])
        far = spec["hosts"]["subdev"]
        self.assertEqual(far["account"], "codex-bridge-peer")
        self.assertEqual(far["ssh"], "codex-bridge-peer@" + SUBDEV_IP)
        self.assertEqual(far["identity"], "~/.ssh/codex-bridge_peer_ed25519")
        self.assertEqual(far["path"], cpc.SNAPSHOT_DIR)
        self.assertTrue(far["key"].endswith(" codex-bridge-peer@dev2"), far["key"])

    def test_subdev_links_and_streams(self):
        self.assertEqual(sorted(sorted(p) for p in self.sub["links"]),
                         [["codex-bridge", "david3"], ["david1", "devbridge"],
                          ["david3", "devbridge"], ["david3", "gatekeeper"],
                          ["david4", "devbridge"]])   # + #1330, #1350
        self.assertEqual(self.sub["streams"],
                         {"david1": {"account": "david1", "path": "~/devel/odoo/odoo-erp"},   # #1350
                          "david3": {"account": "david3", "path": "~/devel/odoo/odoo-erp"},
                          "david4": {"account": "david4", "path": "~/devel/odoo/odoo-erp"}})
        topic = self.sub["topics"]["codex-bridge|david3"]
        for word in ("Codex", "read-only", "Granč", "SNV"):
            self.assertIn(word, topic)
        topic = self.sub["topics"]["david4|devbridge"]
        for word in ("devbridge#92", "spisska-pc", "8860"):
            self.assertIn(word, topic)

    def test_no_other_stream_is_linked(self):
        linked = {n for p in self.sub["links"] for n in p}
        self.assertEqual(linked, {"codex-bridge", "david1", "david3", "david4", "devbridge",
                                  "gatekeeper"})                       # + #1330, #1350
        for other in ("simap1", "david2", "dominika", "montalu1", "miva1"):
            self.assertNotIn(other, self.sub["streams"])

    def test_subdev_root_leg_names_the_linked_accounts(self):
        accounts = cpc.accounts_for_host("subdev", self.doc, self.reg)
        self.assertEqual(accounts, ["codex-bridge-peer", "david1", "david3", "david4",
                                    "devbridge-peer", "gatekeeper-peer"])   # + #1330, #1350
        script = cpc.render_root_script(accounts, self.doc, self.reg, "subdev")
        acl = [ln for ln in script.splitlines() if ln.startswith("a /run/claude-peers")]
        self.assertEqual(acl, ["a /run/claude-peers - - - - u::rwx,g::---,o::---,"
                               "u:codex-bridge-peer:r-x,u:david1:r-x,u:david3:r-x,u:david4:r-x,"
                               "u:devbridge-peer:r-x,u:gatekeeper-peer:r-x,m::r-x"])
        key = self.doc["bridges"]["codex-bridge"]["hosts"]["subdev"]["key"]
        self.assertIn('restrict,command="exec python3 -I /etc/airuleset/peer/peer_channel.py '
                      'bridge --project codex-bridge" ' + key, script)
        self.assertIn("--home-dir /var/lib/codex-bridge-peer", script)
        self.assertIn('case " codex-bridge-peer devbridge-peer gatekeeper-peer " in', script)
        self.assertNotIn("u:simap1", script)

    def test_install_enables_david4_and_the_dev2_home(self):
        class Ok:
            returncode, stdout, stderr = 0, "", ""
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        run = mock.Mock(return_value=Ok())
        self.assertTrue(cpc.install_step(user="david4", host="subdev", claude_dir=d, run=run))
        self.assertTrue(Path(d, cpc.MARKER_NAME).exists())
        self.assertTrue(cpc.is_bridge_home("newlevel", self.doc, "dev2"))

    def test_the_dev2_runtime_picks_the_bridge_by_checkout(self):
        home = "/home/newlevel"
        got = pc.bridge_home(self.doc, "newlevel", home + "/devel/codex-bridge/odoo_import",
                             home, host="dev2")
        self.assertEqual(got[0], "codex-bridge")
        got = pc.bridge_home(self.doc, "newlevel", home + "/devel/presenter/presenter-dev2",
                             home, host="dev2")
        self.assertEqual(got[0], "presenter")
        self.assertIsNone(pc.bridge_home(self.doc, "newlevel", home + "/devel/other", home,
                                         host="dev2"))

    def test_david3_sees_both_partners_with_their_topics(self):
        projects = pc.host_projects(self.reg, "subdev", self.doc)
        self.assertEqual(projects["codex-bridge"]["account"], "codex-bridge-peer")
        self.assertEqual(projects["david4"]["account"], "david4")
        pairs = pc.host_links(self.doc, "subdev")
        self.assertEqual(pc.linked_to(pairs, "david3"),
                         ["codex-bridge", "devbridge", "gatekeeper"])           # + #1330
        self.assertEqual(pc.linked_to(pairs, "david4"), ["devbridge"])
        self.assertEqual(pc.linked_to(pairs, "devbridge"), ["david1", "david3", "david4"])


if __name__ == "__main__":
    unittest.main()
