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

    def test_a_controller_homed_bridge_tears_no_root_host_down_to_a_policy(self):
        by_host = self.provision(_doc(home=cpc.CONTROLLER_HOME))
        self.assertIn("nothing to tear down", by_host["2.2.2.2"])

    def test_the_far_end_of_the_presenter_bridge_renders_on_dev1(self):
        doc, reg = cpc.load_links(), cpc.load_registry()
        script = cpc.render_root_script(cpc.accounts_for_host("dev1", doc, reg), doc, reg)
        self.assertIn("useradd --system --create-home --home-dir /var/lib/presenter-peer", script)
        self.assertIn("bridge --project presenter", script)


class TestLinksHostAlsoHome(TestRootLeg):
    def test_a_links_host_keeps_the_full_leg(self):
        by_host = self.provision(_doc(home="h1"))
        self.assertIn("peer-channel-root: ok", by_host["1.1.1.1"])
        self.assertIn("nothing to tear down", by_host["2.2.2.2"])


class TestValidation(unittest.TestCase):
    def test_a_non_string_home_is_a_validation_error_not_a_crash(self):
        for bad in (["h2"], {"h": 1}, 7, "", None):
            errs = cpc.validate_links(_doc(home=bad), REG, root_hosts={"h1", "h2"})
            self.assertTrue(any("home" in e for e in errs), (bad, errs))
        self.assertEqual(cpc.bridge_home_hosts(_doc(home=["h2"])), set())

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

    def test_an_undeclared_home_matches_nowhere(self):
        # review 2: install and runtime must agree; home is required, so a bridge
        # without one is invalid and enables no host
        doc = _doc(home=None)
        for host in ("h1", "h2", cpc.CONTROLLER_HOME, None):
            self.assertFalse(cpc.is_bridge_home("nl", doc, host), host)

    def test_the_controller_bridge_is_homed_on_the_controller(self):
        doc = cpc.load_links()
        self.assertEqual(doc["bridges"]["airuleset"]["home"], cpc.CONTROLLER_HOME)
        self.assertTrue(cpc.is_bridge_home("airuleset", doc, cpc.CONTROLLER_HOME))
        self.assertFalse(cpc.is_bridge_home("airuleset", doc, "dev1"))

    def test_install_step_enables_the_home_account_only_on_its_host(self):
        import tempfile
        d = tempfile.mkdtemp()
        calls = []

        class Ok:
            returncode, stdout, stderr = 0, "", ""

        def run(argv, **kw):
            calls.append(argv)
            return Ok()
        self.assertTrue(cpc.install_step(user="nl", host="h2", claude_dir=d, run=run,
                                         links_doc=_doc(), registry=REG))
        self.assertTrue((Path(d) / cpc.MARKER_NAME).exists())
        self.assertTrue(cpc.install_step(user="nl", host="h3", claude_dir=d, run=run,
                                         links_doc=_doc(), registry=REG))
        self.assertFalse((Path(d) / cpc.MARKER_NAME).exists())

    def test_runtime_bridge_home_follows_the_declared_home(self):
        import peer_channel as pc
        doc = _doc()
        self.assertEqual(pc.bridge_home(doc, "nl", "/h/p", "/h", host="h2")[0], "p")
        self.assertIsNone(pc.bridge_home(doc, "nl", "/h/p", "/h", host="h1"))
        self.assertIsNone(pc.bridge_home(_doc(home=None), "nl", "/h/p", "/h", host="h1"))

    def _apply(self, home, host):
        from unittest import mock
        doc = {"bridges": {"p": {"account": "nl", "home": home, "path": "~/p", "hosts": {}}}}
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(cpc.pwd, "getpwuid", return_value=mock.Mock(pw_name="nl")), \
                mock.patch.object(cpc, "this_host", return_value=host):
            self.assertEqual(cpc.apply_home_policy(run, doc), [])
        return calls

    def test_apply_home_policy_runs_only_for_the_controller_home_on_it(self):
        self.assertEqual(self._apply("h2", "h2"), [])        # the root leg owns a root home
        self.assertEqual(self._apply(cpc.CONTROLLER_HOME, "dev1"), [])
        calls = self._apply(cpc.CONTROLLER_HOME, cpc.CONTROLLER_HOME)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('"plugin": "telegram"', calls[0][-1])   # controller: peer only (#1266)


if __name__ == "__main__":
    unittest.main()
