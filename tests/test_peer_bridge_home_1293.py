"""#1293 — a bridge home on a root host keeps the managed channel policy.

presenter (dev2, `newlevel`) joins the dev1 peer channel through a #1266 bridge.
Its home box needs the policy drop-in, or Claude Code refuses the
`plugin:peer@airuleset` channel. The root leg used to send every host that is not
a links host the teardown script, which deletes that file.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_peer_channel as cpc  # noqa: E402

FAR = {"account": "p-peer", "ssh": "p-peer@1.2.3.4", "identity": "~/.ssh/k",
       "path": cpc.SNAPSHOT_DIR,
       "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGkRnAroNZk+tD6jrYVbJUalRDoBk/pxTiLwB/rUZvfW p"}
REG = [{"name": "a", "host": "h1", "account": "ua", "path": "/a"}]


def _doc(home="h2"):
    spec = {"account": "nl", "path": "~/p", "hosts": {"h1": dict(FAR)}}
    if home is not None:
        spec["home"] = home
    return {"hosts": {"h1": {"links": [["a", "p"]], "topics": {"a|p": "t"}}},
            "bridges": {"p": spec}}


class R:
    returncode, stdout, stderr = 0, "ok", ""


class TestRootLeg(unittest.TestCase):
    HOSTS = [{"name": "h1", "host": "1.1.1.1", "identity": "~/k"},
             {"name": "h2", "host": "2.2.2.2", "identity": "~/k"},
             {"name": "h3", "host": "3.3.3.3", "identity": "~/k"}]

    def provision(self, doc):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return R()
        failed = cpc.provision_peer_channel_root(hosts=self.HOSTS, run=run, links_doc=doc,
                                                 registry=REG)
        self.assertEqual(failed, [])
        return {c[-2].split("@")[1]: c[-1] for c in calls if c[0] == "ssh"}

    def test_home_host_gets_the_policy_not_the_teardown(self):
        by_host = self.provision(_doc())
        self.assertIn(cpc.POLICY_PATH, by_host["2.2.2.2"])
        self.assertIn("peer-channel-home: ok", by_host["2.2.2.2"])
        self.assertNotIn("torn down", by_host["2.2.2.2"])
        self.assertIn('"plugin": "peer"', by_host["2.2.2.2"])
        self.assertIn('"plugin": "telegram"', by_host["2.2.2.2"])   # shared box keeps official

    def test_other_hosts_unchanged(self):
        by_host = self.provision(_doc())
        self.assertIn("nothing to tear down", by_host["3.3.3.3"])
        self.assertIn("peer-channel-root: ok", by_host["1.1.1.1"])

    def test_without_a_home_the_host_is_torn_down(self):
        by_host = self.provision(_doc(home=None))
        self.assertIn("nothing to tear down", by_host["2.2.2.2"])


class TestValidation(unittest.TestCase):
    def test_home_must_be_a_root_host(self):
        errs = cpc.validate_links(_doc(home="nope"), REG, root_hosts={"h1", "h2"})
        self.assertTrue(any("home 'nope'" in e for e in errs), errs)
        self.assertEqual([e for e in cpc.validate_links(_doc(), REG, root_hosts={"h1", "h2"})
                          if "home" in e], [])

    def test_committed_presenter_bridge_is_valid_and_homed_on_dev2(self):
        doc = cpc.load_links()
        self.assertEqual(cpc.validate_links(doc, cpc.load_registry()), [])
        self.assertEqual(doc["bridges"]["presenter"]["home"], "dev2")
        self.assertIn(["presenter", "songplayer"], doc["hosts"]["dev1"]["links"])


class TestBridgeHomeMatch(unittest.TestCase):
    def test_declared_home_matches_only_there(self):
        self.assertTrue(cpc.is_bridge_home("nl", _doc(), "h2"))
        self.assertFalse(cpc.is_bridge_home("nl", _doc(), "h1"))
        self.assertFalse(cpc.is_bridge_home("other", _doc(), "h2"))

    def test_undeclared_home_matches_any_host(self):
        self.assertTrue(cpc.is_bridge_home("nl", _doc(home=None), "h1"))
        self.assertTrue(cpc.is_bridge_home("nl", _doc(home=None)))

    def test_apply_home_policy_skips_a_root_homed_bridge(self):
        import pwd
        import os
        me = pwd.getpwuid(os.getuid()).pw_name
        doc = {"bridges": {"p": {"account": me, "home": "h2", "path": "~/p", "hosts": {}}}}
        self.assertEqual(cpc.apply_home_policy(lambda *a, **k: self.fail("ran"), doc), [])


if __name__ == "__main__":
    unittest.main()
