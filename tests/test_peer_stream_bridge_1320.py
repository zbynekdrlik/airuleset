"""#1320 — devbridge (dev1) <-> david3 (a stream account on subdev) over a #1266 bridge.

Three new pieces: a ``streams`` block names a non-registry member of a host
(david3), subdev runs the full root leg for exactly the linked accounts, and a
bridge home that is ALSO a local project (devbridge on dev1, linked to
airuleset) keeps its local link next to the bridge.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_peer_channel as cpc  # noqa: E402
import peer_channel as pc  # noqa: E402
from test_host_grants_peer_bridge_1282 import _Child  # noqa: E402
from test_peer_channel_1266 import HOST, ME, _Env, _Recorder, _scratch  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGkRnAroNZk+tD6jrYVbJUalRDoBk/pxTiLwB/rUZvfW p"
REG = [{"name": "a", "host": "h1", "account": "ua", "path": "/a"}]


def _doc(streams=None, links=None):
    return {"hosts": {"h1": {"links": links or [["a", "s1"]], "topics": {"a|s1": "t"},
                             "streams": {"s1": {"account": "s1", "path": "~/w"}}
                             if streams is None else streams}}}


def _users(host):
    return {"s1", "s2"} if host == "h1" else set()


class TestStreamRows(unittest.TestCase):
    def test_a_stream_is_a_project_of_its_host_only(self):
        rows = pc.host_projects(REG, "h1", _doc())
        self.assertEqual(rows["s1"]["account"], "s1")
        self.assertEqual(rows["s1"]["path"], "~/w")
        self.assertIn("no-git", rows["s1"]["overrides"])
        self.assertNotIn("s1", pc.host_projects(REG, "h2", _doc()))
        self.assertNotIn("s1", pc.host_projects(REG, "h1"))     # no links doc, no stream

    def test_a_malformed_stream_is_never_a_project(self):
        for bad in ({"s1": {"account": None, "path": "~/w"}}, {"s1": "x"}, ["s1"],
                    {"s 1": {"account": "s 1", "path": "~/w"}},
                    {"s1": {"account": "s1", "path": 7}}):
            self.assertNotIn("s1", pc.host_projects(REG, "h1", _doc(streams=bad)), bad)

    def test_a_registry_row_wins_over_a_stream_of_the_same_name(self):
        reg = REG + [{"name": "s1", "host": "h1", "account": "ua", "path": "/s"}]
        self.assertEqual(pc.host_projects(reg, "h1", _doc())["s1"]["account"], "ua")


class TestStreamValidation(unittest.TestCase):
    def errors(self, streams):
        return cpc.validate_links(_doc(streams=streams), REG, root_hosts={"h1"},
                                  stream_users=_users)

    def test_a_valid_stream_passes(self):
        self.assertEqual(self.errors(None), [])

    def test_the_name_must_be_the_account(self):
        self.assertTrue([e for e in self.errors({"s1": {"account": "s2", "path": "~/w"}})
                         if "stream" in e])

    def test_only_an_active_fleet_user_of_that_host(self):
        errs = cpc.validate_links(_doc(streams={"s3": {"account": "s3", "path": "~/w"}},
                                       links=[["a", "s3"]]),
                                  REG, root_hosts={"h1"}, stream_users=_users)
        self.assertTrue([e for e in errs if "fleet user" in e], errs)

    def test_the_path_stays_in_the_home(self):
        for path in ("/srv/x", "~/../david1", "~", "", None, "~/a/../../b"):
            errs = self.errors({"s1": {"account": "s1", "path": path}})
            self.assertTrue([e for e in errs if "stream" in e], (path, errs))

    def test_a_stream_never_shadows_a_registry_project(self):
        errs = cpc.validate_links(_doc(streams={"a": {"account": "a", "path": "~/w"}}),
                                  REG, root_hosts={"h1"}, stream_users=lambda h: {"a"})
        self.assertTrue([e for e in errs if "collides" in e], errs)

    def test_streams_must_be_an_object(self):
        self.assertTrue([e for e in self.errors(["s1"]) if "streams" in e])

    def test_fleet_users_of_subdev(self):
        users = cpc.stream_users("subdev")
        self.assertIn("david3", users)
        self.assertNotIn("simap1", users)               # #851 paused: never named
        self.assertEqual(cpc.stream_users("nope"), set())


class TestCommittedLink(unittest.TestCase):
    def setUp(self):
        self.doc, self.reg = cpc.load_links(), cpc.load_registry()

    def test_the_committed_config_is_valid(self):
        self.assertEqual(cpc.validate_links(self.doc, self.reg), [])

    def test_subdev_carries_exactly_one_link(self):
        sub = self.doc["hosts"]["subdev"]
        self.assertEqual(sub["links"], [["david3", "devbridge"]])
        self.assertEqual(sorted(sub["streams"]), ["david3"])
        self.assertIn("Worldline", sub["topics"]["david3|devbridge"])
        self.assertEqual(cpc.accounts_for_host("subdev", self.doc, self.reg),
                         ["david3", "devbridge-peer"])

    def test_the_bridge_goes_from_dev1_out_to_subdev(self):
        spec = self.doc["bridges"]["devbridge"]
        self.assertEqual((spec["account"], spec["home"]), ("devbridge", "dev1"))
        self.assertEqual(spec["path"], "/home/devbridge/devel/devbridge")
        self.assertEqual(sorted(spec["hosts"]), ["subdev"])
        far = spec["hosts"]["subdev"]
        self.assertEqual(far["ssh"], "devbridge-peer@100.118.174.27")
        self.assertEqual(far["identity"], "~/.ssh/devbridge_peer_ed25519")

    def test_the_subdev_root_leg_names_only_the_two_accounts(self):
        script = cpc.render_root_script(cpc.accounts_for_host("subdev", self.doc, self.reg),
                                        self.doc, self.reg)
        acl = [ln for ln in script.splitlines() if ln.startswith("a /run/claude-peers")]
        self.assertEqual(acl, ["a /run/claude-peers - - - - u::rwx,g::---,o::---,"
                               "u:david3:r-x,u:devbridge-peer:r-x,m::r-x"])
        far = self.doc["bridges"]["devbridge"]["hosts"]["subdev"]["key"]
        self.assertIn('restrict,command="exec python3 -I /etc/airuleset/peer/peer_channel.py '
                      'bridge --project devbridge" ' + far, script)
        self.assertIn("useradd --system --create-home --home-dir /var/lib/devbridge-peer", script)
        for other in ("simap1", "montalu", "miva1", "david1", "david2", "david4", "dominika"):
            self.assertNotIn("u:%s" % other, script)

    def test_push_gives_subdev_the_full_leg(self):
        calls = []

        class R:
            returncode, stdout, stderr = 0, "ok", ""

        def run(argv, **kw):
            calls.append(argv)
            return R()
        import airuleset
        hosts = [h for h in airuleset.DISK_GUARD_ROOT_HOSTS if h["name"] == "subdev"]
        with mock.patch.object(cpc, "apply_home_policy", return_value=[]):
            self.assertEqual(cpc.provision_peer_channel_root(hosts=hosts, run=run), [])
        self.assertEqual(calls[0][-2], "root@100.118.174.27")
        self.assertIn("peer-channel-root: ok", calls[0][-1])
        self.assertNotIn("torn down", calls[0][-1])

    def test_install_enables_david3_only(self):
        d = _scratch(self, "pci")

        class Ok:
            returncode, stdout, stderr = 0, "", ""
        run = mock.Mock(return_value=Ok())
        self.assertTrue(cpc.install_step(user="david3", host="subdev", claude_dir=d, run=run))
        self.assertTrue(Path(d, cpc.MARKER_NAME).exists())
        for other in ("david1", "david2", "simap1", "montalu1", "miva1"):
            self.assertTrue(cpc.install_step(user=other, host="subdev", claude_dir=d, run=run))
            self.assertFalse(Path(d, cpc.MARKER_NAME).exists(), other)
            Path(d, cpc.MARKER_NAME).write_text("x")
        Path(d, cpc.MARKER_NAME).unlink()

    def test_snapshot_carries_the_stream_for_old_code(self):
        rows = [r for r in cpc.snapshot_registry(self.reg, self.doc) if r.get("name") == "david3"]
        self.assertEqual([(r["host"], r["account"]) for r in rows], [("subdev", "david3")])

    def test_no_issue_token_for_a_stream(self):
        self.assertNotIn("zbynekdrlik/david3", cpc.peer_repos("devbridge", self.doc, self.reg))


class TestStreamRuntime(_Env):
    """A stream session resolves its project from the streams block and is reached."""

    def setUp(self):
        super().setUp()
        doc = json.loads(Path(self.cfg, pc.LINKS_FILE).read_text())
        doc["hosts"][HOST]["links"].append(["alpha", ME])
        doc["hosts"][HOST]["streams"] = {ME: {"account": ME, "path": "~/devel/odoo"}}
        Path(self.cfg, pc.LINKS_FILE).write_text(json.dumps(doc))

    def test_a_stream_session_listens_and_receives(self):
        os.makedirs(os.path.join(self.home, "devel", "odoo", "sub"))
        got = []
        s = pc.Peer(HOST, ME, os.getuid(), os.path.join(self.home, "devel", "odoo", "sub"),
                    self.home, sock_dir=self.sock, config_dir=self.cfg, log=_Recorder(),
                    notify=lambda text, meta: got.append((text, meta)), armed=True)
        self.addCleanup(s.close)
        self.assertEqual(s.project, ME)
        self.assertTrue(s.start_listener())
        ok, text = self.peer("alpha").send(ME, "hi")
        self.assertTrue(ok, text)
        self.assertEqual(got, [("hi", {"from": "alpha"})])


class TestCompositeHome(_Env):
    """devbridge: a bridge home that is also a local project with a local link."""
    LINKS = [["alpha", "home"]]
    LOCAL = "localhost1"
    SPEC = {"account": ME, "home": "dev1", "path": "~/devel/home",
            "hosts": {HOST: {"account": ME, "ssh": "u@h", "identity": "~/k", "path": "~/x"}}}

    def setUp(self):
        super().setUp()
        doc = json.loads(Path(self.cfg, pc.LINKS_FILE).read_text())
        doc["bridges"] = {"home": self.SPEC}
        doc["hosts"][self.LOCAL] = {"links": [["home", "loc"]], "topics": {"home|loc": "lt"}}
        doc["hosts"][HOST]["topics"] = {"alpha|home": "bt"}
        Path(self.cfg, pc.LINKS_FILE).write_text(json.dumps(doc))
        reg = json.loads(Path(self.cfg, pc.REGISTRY_FILE).read_text())
        reg += [{"name": "home", "host": self.LOCAL, "account": ME, "path": "~/devel/home"},
                {"name": "loc", "host": self.LOCAL, "account": ME, "path": "~/devel/loc"}]
        Path(self.cfg, pc.REGISTRY_FILE).write_text(json.dumps(reg))
        p = mock.patch.object(pc, "active_config_dir", return_value=self.cfg)
        p.start()
        self.addCleanup(p.stop)
        self.far = pc.Peer(HOST, ME, os.getuid(), "/", self.home, project_arg="home",
                           sock_dir=self.sock, config_dir=self.cfg, log=_Recorder(), armed=True)
        self.addCleanup(self.far.close)
        self.lsock = _scratch(self, "pcl")
        os.makedirs(os.path.join(self.lsock, ME))

    def local_peer(self, project, got=None):
        p = pc.Peer(self.LOCAL, ME, os.getuid(), "/", self.home, project_arg=project,
                    sock_dir=self.lsock, config_dir=self.cfg, log=_Recorder(), armed=True,
                    notify=(lambda text, meta: got.append((text, meta))) if got is not None
                    else None)
        self.addCleanup(p.close)
        return p

    def start(self):
        seen = []
        home = pc.home_peer(("home", self.SPEC), self.local_peer("home"), True, self.home,
                            spawn=lambda *a, **k: _Child(self.far), retry_s=60, log=_Recorder())
        home.notify = lambda text, meta: seen.append((text, meta))
        home.start_listener()
        self.addCleanup(home.close)
        for _ in range(200):
            if self.far.listener is not None and home.children:
                break
            time.sleep(0.01)
        return home, seen

    def wait(self, box):
        for _ in range(200):
            if box:
                break
            time.sleep(0.01)

    def test_the_local_link_still_works_both_ways(self):
        got = []
        loc = self.local_peer("loc", got)
        loc.start_listener()
        home, seen = self.start()
        ok, text = home.send("loc", "to loc")
        self.assertTrue(ok, text)
        self.assertEqual(got, [("to loc", {"from": "home"})])
        ok, text = loc.send("home", "to home")
        self.assertTrue(ok, text)
        self.wait(seen)
        self.assertEqual(seen, [("to home", {"from": "loc"})])

    def test_the_bridged_link_works_both_ways(self):
        alpha = self.peer("alpha")
        alpha.start_listener()
        home, seen = self.start()
        ok, text = home.send("alpha", "over the bridge")
        self.assertTrue(ok, text)
        self.assertEqual(alpha.delivered, [("over the bridge", {"from": "home"})])
        ok, text = self.peer("alpha").send("home", "back")
        self.assertTrue(ok, text)
        self.wait(seen)
        self.assertEqual(seen, [("back", {"from": "alpha"})])

    def test_peers_and_instructions_cover_both(self):
        home, _ = self.start()
        ok, text = home.list_peers()
        self.assertTrue(ok, text)
        self.assertIn("loc", text)
        self.assertIn("alpha", text)
        intro = home.instructions()
        self.assertIn("loc (%s): lt" % self.LOCAL, intro)
        self.assertIn("alpha (%s): bt" % HOST, intro)

    def test_a_home_without_local_links_stays_a_plain_bridge(self):
        other = self.local_peer("loc")
        home = pc.home_peer(("home", self.SPEC), other, True, self.home, log=_Recorder())
        self.assertIsNone(home.local)                    # a different project
        bare = pc.Peer("nolinks", ME, os.getuid(), "/", self.home, project_arg=None,
                       sock_dir=self.lsock, config_dir=self.cfg, log=_Recorder())
        self.assertIsNone(pc.home_peer(("home", self.SPEC), bare, True, self.home,
                                       log=_Recorder()).local)

    def test_close_closes_the_local_listener(self):
        home, _ = self.start()
        path = home.local.sock_path
        self.assertTrue(path and os.path.exists(path))
        home.close()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
