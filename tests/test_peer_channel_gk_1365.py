"""#1365 — gk <-> gk-infra over the peer channel on the gk VPS (owner 18.11.2026).

Both windows run in the one ``gatekeeper`` account, in two odoo-erp checkouts. Two
gaps kept them apart: the runtime keyed links by the socket hostname
(``odoo-gatekeeper``) while validation and the root leg use the fleet name
(``gatekeeper``), and neither checkout had a peer name on that host (the registry
row ``odoo-erp`` says host ``gk`` and a dozen readers depend on that). Now
``peer_channel.FLEET_HOST`` maps the hostname for the runtime, and
``hosts.<host>.locals`` names the checkouts without a registry row.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_peer_channel as cpc  # noqa: E402
import peer_channel as pc  # noqa: E402
from test_peer_channel_1266 import HOST, ME, _Env, _Recorder, _scratch  # noqa: E402

GK_HOME = "/home/gatekeeper"
GK = "gatekeeper"


def _with_stdin(argv, kw):
    return list(argv) + [kw.get("input") or ""]


class TestCommittedGkLink(unittest.TestCase):
    def setUp(self):
        self.doc, self.reg = cpc.load_links(), cpc.load_registry()

    def test_the_committed_config_is_valid(self):
        self.assertEqual(cpc.validate_links(self.doc, self.reg), [])

    def test_gk_and_gk_infra_are_linked_with_a_topic(self):
        entry = self.doc["hosts"][GK]
        self.assertEqual(entry["links"], [[GK, "gk-infra"]])
        topic = entry["topics"]["gatekeeper|gk-infra"]
        self.assertIn("gk-infra", topic)
        self.assertIn("reports the fix back", topic)

    def test_gk_quality_stays_unlinked(self):
        entry = self.doc["hosts"][GK]
        self.assertEqual(sorted(entry["locals"]), [GK, "gk-infra"])
        self.assertNotIn("gk-quality", pc.host_projects(self.reg, GK, self.doc))

    def test_the_registry_row_keeps_host_gk_and_no_row_was_added(self):
        rows = [r for r in self.reg if r.get("account") == GK]
        self.assertEqual([(r["name"], r["host"]) for r in rows], [("odoo-erp", "gk")])

    def test_each_checkout_resolves_to_its_own_name(self):
        projects = pc.host_projects(self.reg, GK, self.doc)
        for cwd, want in (("devel/odoo/odoo-erp", GK),
                          ("devel/odoo/odoo-erp/addons/x", GK),
                          ("devel/odoo/odoo-erp-infra", "gk-infra"),
                          ("devel/odoo/odoo-erp-quality", None),
                          ("devel/odoo/worktrees/a", None)):
            self.assertEqual(pc.resolve_project(projects, GK, os.path.join(GK_HOME, cwd),
                                                GK_HOME), want, cwd)

    def test_the_gk_end_keeps_its_bridge_name_so_home_peer_keeps_the_local_leg(self):
        # home_peer keeps the local Peer only when its project IS the bridge name
        self.assertIn(GK, self.doc["bridges"])
        self.assertEqual(self.doc["bridges"][GK]["path"],
                         self.doc["hosts"][GK]["locals"][GK]["path"])

    def test_a_local_is_trusted(self):
        self.assertEqual(pc.inbound_meta(self.doc, GK, "gk-infra"), {"from": "gk-infra"})
        self.assertEqual(pc.inbound_meta(self.doc, GK, GK), {"from": GK})

    def test_the_root_leg_creates_the_shared_dir_for_gatekeeper(self):
        self.assertEqual(cpc.accounts_for_host(GK, self.doc, self.reg), [GK])
        script = cpc.render_root_script([GK], self.doc, self.reg, GK)
        lines = script.splitlines()
        self.assertIn("d /run/claude-peers 0750 root root -", lines)
        self.assertIn("a /run/claude-peers - - - - u::rwx,g::---,o::---,"
                      "u:gatekeeper:r-x,m::r-x", lines)
        self.assertIn("d /run/claude-peers/gatekeeper 0755 gatekeeper gatekeeper -", lines)
        self.assertIn("systemctl enable --now 'run-claude\\x2dpeers.mount'", lines)
        self.assertIn("_install /etc/airuleset/peer/peer-links.json <<'AIRULESET_PEER_EOF'",
                      lines)
        self.assertNotIn("useradd", script)          # no far end lives on gk
        self.assertIn('case "  " in', script)         # the sweep keeps no far end there
        self.assertEqual(json.loads(script.split(
            "_install /etc/claude-code/managed-settings.d/50-airuleset-peer-channel.json "
            "<<'AIRULESET_PEER_EOF'\n")[1].split("AIRULESET_PEER_EOF")[0]),
            json.loads(cpc.render_policy(cpc.OFFICIAL_CHANNEL_PLUGINS)))

    def test_push_gives_gk_the_full_leg_as_root(self):
        calls = []

        class R:
            returncode, stdout, stderr = 0, "ok", ""

        def run(argv, **kw):
            calls.append(_with_stdin(argv, kw))
            return R()
        import airuleset
        with mock.patch.object(cpc, "apply_home_policy", return_value=[]):
            self.assertEqual(cpc.provision_peer_channel_root(
                hosts=airuleset.DISK_GUARD_ROOT_HOSTS, run=run, only=GK), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-3:-1], ["root@100.90.94.41", "bash -s"])
        self.assertIn("d /run/claude-peers/gatekeeper 0755", calls[0][-1])
        self.assertNotIn("peer-channel-home: ok", calls[0][-1])

    def test_install_enables_gatekeeper_by_its_hostname_without_the_bridge(self):
        doc = dict(self.doc, bridges={})
        d = _scratch(self, "pgk")

        class Ok:
            returncode, stdout, stderr = 0, "", ""
        run = mock.Mock(return_value=Ok())
        self.assertTrue(cpc.install_step(user=GK, host="odoo-gatekeeper", claude_dir=d,
                                         run=run, links_doc=doc, registry=self.reg))
        self.assertTrue(Path(d, cpc.MARKER_NAME).exists())


class TestFleetHost(unittest.TestCase):
    def test_one_table_for_runtime_and_root_leg(self):
        self.assertEqual(pc.FLEET_HOST, {"odoo-gatekeeper": GK})
        for hostname, fleet in pc.FLEET_HOST.items():
            self.assertEqual(cpc.HOME_FLEET[hostname], fleet)

    def _main(self, hostname, home_end):
        seen = {}

        class Stop(Exception):
            pass

        def fake_peer(host, *a, **kw):
            seen["peer_host"] = host
            raise Stop

        def fake_bridge_home(links, user, cwd, home, host):
            seen["bridge_host"] = host
            return home_end
        with mock.patch.object(pc.socket, "gethostname", return_value=hostname), \
                mock.patch.object(pc, "Peer", fake_peer), \
                mock.patch.object(pc, "bridge_home", fake_bridge_home), \
                mock.patch.object(pc.signal, "signal"):
            with self.assertRaises(Stop):
                pc.main(["serve"])
        return seen

    def test_a_gk_session_peer_uses_the_fleet_name(self):
        self.assertEqual(self._main("odoo-gatekeeper", None),
                         {"bridge_host": "odoo-gatekeeper", "peer_host": GK})

    def test_the_gk_bridge_home_keeps_the_hostname_and_its_local_peer_the_fleet_name(self):
        self.assertEqual(self._main("odoo-gatekeeper.example", (GK, {})),
                         {"bridge_host": "odoo-gatekeeper", "peer_host": GK})

    def test_a_box_named_as_its_fleet_is_unchanged(self):
        self.assertEqual(self._main("dev1", None), {"bridge_host": "dev1", "peer_host": "dev1"})


REG = [{"name": "a", "host": "h1", "account": "ua", "path": "/a"}]


def _doc(locals_):
    return {"hosts": {"h1": {"links": [["a", "l1"]], "topics": {"a|l1": "t"},
                             "locals": locals_}}}


class TestLocalValidation(unittest.TestCase):
    def errors(self, locals_, users=lambda h: {"ua", "full", "s1"}, streams=lambda h: {"s1"}):
        return cpc.validate_links(_doc(locals_), REG, root_hosts={"h1"},
                                  stream_users=streams, host_users=users)

    def test_a_valid_local_passes(self):
        self.assertEqual(self.errors({"l1": {"account": "full", "path": "~/w"}}), [])

    def test_the_account_must_be_a_fleet_user_of_the_host(self):
        errs = self.errors({"l1": {"account": "nobody", "path": "~/w"}})
        self.assertTrue([e for e in errs if "not a full fleet user" in e], errs)

    def test_a_stream_account_is_refused(self):
        errs = self.errors({"l1": {"account": "s1", "path": "~/w"}})
        self.assertTrue([e for e in errs if "not a full fleet user" in e], errs)

    def test_the_path_stays_in_the_home(self):
        for path in ("/etc", "~/", "~/../x", 7):
            errs = self.errors({"l1": {"account": "full", "path": path}})
            self.assertTrue([e for e in errs if "must be under ~/" in e], (path, errs))

    def test_a_local_never_shadows_a_registry_project(self):
        doc = _doc({"a": {"account": "full", "path": "~/w"}})
        doc["hosts"]["h1"]["links"] = [["a", "l1"]]
        errs = cpc.validate_links(doc, REG, root_hosts={"h1"}, stream_users=lambda h: set(),
                                  host_users=lambda h: {"full"})
        self.assertTrue([e for e in errs if "local a collides" in e], errs)
        self.assertEqual(pc.host_projects(REG, "h1", doc)["a"]["account"], "ua")

    def test_locals_must_be_an_object(self):
        errs = self.errors(["l1"])
        self.assertTrue([e for e in errs if "'locals' must be an object" in e], errs)

    def test_a_malformed_local_is_refused_and_never_a_project(self):
        for bad in ({"l1": "x"}, {"l1": {"path": "~/w"}}, {"l 1": {"account": "full",
                                                                  "path": "~/w"}}):
            self.assertTrue(self.errors(bad), bad)
            self.assertNotIn("l1", pc.host_projects(REG, "h1", _doc(bad)), bad)

    def test_host_users_are_the_fleet_users_of_that_host(self):
        self.assertIn(GK, cpc.host_users(GK))
        self.assertNotIn(GK, cpc.stream_users(GK))
        self.assertEqual(cpc.host_users("no-such-host"), set())


class TestLocalRuntime(_Env):
    """Two checkouts of ONE account talk over the shared dir, both ways."""
    LINKS = [["gkx", "infra"]]

    def setUp(self):
        super().setUp()
        doc = json.loads(Path(self.cfg, pc.LINKS_FILE).read_text())
        doc["hosts"][HOST]["topics"] = {"gkx|infra": "infra findings"}
        doc["hosts"][HOST]["locals"] = {"gkx": {"account": ME, "path": "~/devel/odoo/erp"},
                                        "infra": {"account": ME,
                                                  "path": "~/devel/odoo/erp-infra"}}
        Path(self.cfg, pc.LINKS_FILE).write_text(json.dumps(doc))
        for d in ("erp", "erp-infra"):
            os.makedirs(os.path.join(self.home, "devel", "odoo", d))

    def session(self, cwd, got):
        p = pc.Peer(HOST, ME, os.getuid(), os.path.join(self.home, "devel", "odoo", cwd),
                    self.home, sock_dir=self.sock, config_dir=self.cfg, log=_Recorder(),
                    notify=lambda text, meta: got.append((text, meta)), armed=True)
        self.addCleanup(p.close)
        return p

    def test_both_directions_deliver_and_peers_shows_live(self):
        got_gk, got_infra = [], []
        gk, infra = self.session("erp", got_gk), self.session("erp-infra", got_infra)
        self.assertEqual((gk.project, infra.project), ("gkx", "infra"))
        self.assertTrue(gk.start_listener())
        self.assertTrue(infra.start_listener())
        ok, text = infra.list_peers()
        self.assertTrue(ok, text)
        self.assertIn("gkx: live (1) — infra findings", text)
        self.assertTrue(gk.send("infra", "CI gate broke")[0])
        self.assertTrue(infra.send("gkx", "fixed")[0])
        self.assertEqual(got_infra, [("CI gate broke", {"from": "gkx"})])
        self.assertEqual(got_gk, [("fixed", {"from": "infra"})])
        self.assertIn("infra: infra findings", gk.instructions())


if __name__ == "__main__":
    unittest.main()
