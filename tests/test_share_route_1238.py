"""#1238 — a new controller drop lane's share (`/s/`) is live after ONE push, and
`share` never prints a URL whose `/s/` route it cannot prove.

Two defects compounded (#1237, fohmixer@dev1):
  * the controller rendered its multi-ingress during `install`, BEFORE the push
    deploy loop harvested the account's persistent filedrop port, so a new lane
    had no `^/s/` rule until the NEXT push;
  * `share` saw Access's edge 302 on `/s/`, probed `/healthz` — served by the
    no-path rule's EPHEMERAL endpoint port, nothing listening, 502 — and took
    the 502 as "tunnel verified": a dead URL with no note.

These tests pin the fix:
  * `persist_measured_filedrop_ports` reports the lane keys whose port changed,
    and the deploy loop hands them to `cli_controller_tunnel.reapply_after_
    harvest`, which re-renders the SAME controller config (one render shared
    with install) in the same push;
  * next to every `^/s/` rule the controller (and a local lane's `--apply`)
    renders `^/healthz/s$` to the same filedrop origin; filedrop answers it 204
    + its own port. The share verdict tests live in test_public_only_review_1192.
Every network call, ssh, tunnel write and Cloudflare call is faked.
"""
import contextlib
import importlib
import io
import os
import re
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import cli_drop_gateway as dg
import cli_drop_golive as gl
import cli_drop_lanes as dl
import cli_filedrop_watchdog as fw
import cli_webterm as w
import cli_webterm_tunnel as tun
from filedrop import server as fserver

FOH_KEY = "dev1/fohmixer"
FOH_HOST = "drop-dev1-fohmixer.newlevel.media"
FOH_ORIGIN = "http://100.104.8.125:8791"
PROBE_RULE = "^/healthz/s$"


def _ct():
    """The new leaf, imported per test so this file collects before it exists."""
    return importlib.import_module("cli_controller_tunnel")


def _quiet():
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


class _TempCache(unittest.TestCase):
    """The controller-side filedrop-port cache in a temp dir (the leaf's own
    binding is the one every reader uses, #1115 review MINOR-2)."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.cache = Path(td.name) / "drop-lanes.json"
        patcher = mock.patch.object(dl, "DROP_LANES_CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestPersistReportsTheChangedPorts(_TempCache):

    def persist(self, measured):
        with _quiet():
            return dl.persist_measured_filedrop_ports(measured, dg.DROP_LANES)

    def test_a_new_lanes_first_port_is_a_change(self):
        dl.write_drop_lanes_cache({"subdev/david1": 8790}, path=self.cache)
        self.assertEqual(self.persist({"subdev/david1": 8790, FOH_KEY: 8791}),
                         [FOH_KEY])

    def test_a_drifted_port_is_a_change(self):
        dl.write_drop_lanes_cache({"subdev/david1": 8790}, path=self.cache)
        self.assertEqual(self.persist({"subdev/david1": 8795}), ["subdev/david1"])

    def test_an_unchanged_harvest_changes_nothing(self):
        dl.write_drop_lanes_cache({FOH_KEY: 8791}, path=self.cache)
        self.assertEqual(self.persist({FOH_KEY: 8791}), [])

    def test_nothing_measured_changes_nothing(self):
        self.assertEqual(self.persist({}), [])

    def test_an_account_without_a_lane_is_never_a_change(self):
        self.assertEqual(self.persist({"nobox/nobody": 9000}), [])


class TestDeployLoopReappliesAfterTheHarvest(_TempCache):
    """The wiring: the harvest's changed keys reach the controller re-render
    in the SAME push, right after the persist."""

    def deploy(self, stdout):
        import airuleset
        import cli_remote
        ct = _ct()
        handed = []

        def fake_run(argv, *a, **k):
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        hosts = [{"name": "fohmixer@dev1", "host": "100.104.8.125",
                  "user": "fohmixer", "repo_path": "~/devel/airuleset",
                  "identity": "~/.ssh/test_identity_1238"}]
        with mock.patch("subprocess.run", side_effect=fake_run), \
                mock.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                mock.patch.object(gl, "read_golive_live_hosts", return_value={}), \
                mock.patch.object(cli_remote, "provision_subdev_soniox_key",
                                  return_value=[]), \
                mock.patch.object(cli_remote, "provision_model_backend_markers",
                                  return_value=[]), \
                mock.patch.object(ct, "reapply_after_harvest",
                                  side_effect=lambda ch, **k: handed.append(list(ch))), \
                _quiet():
            cli_remote._deploy_to_all_remotes([], set())
        return handed

    def test_a_new_lanes_harvested_port_is_reapplied_in_the_same_push(self):
        handed = self.deploy("installed\nAIRULESET-FILEDROP-PORT 8791\n")
        self.assertEqual(handed, [[FOH_KEY]])
        self.assertEqual(dl.read_drop_lanes_cache(path=self.cache), {FOH_KEY: 8791})

    def test_an_unchanged_port_hands_over_nothing(self):
        dl.write_drop_lanes_cache({FOH_KEY: 8791}, path=self.cache)
        self.assertEqual(self.deploy("installed\nAIRULESET-FILEDROP-PORT 8791\n"),
                         [[]])


class TestReapplyAfterHarvest(unittest.TestCase):

    def test_nothing_changed_is_a_no_op(self):
        apply = mock.Mock(return_value=True)
        self.assertIsNone(_ct().reapply_after_harvest(
            [], apply_fn=apply, is_controller_fn=lambda: True))
        apply.assert_not_called()

    def test_off_the_controller_account_is_a_no_op(self):
        apply = mock.Mock(return_value=True)
        self.assertIsNone(_ct().reapply_after_harvest(
            [FOH_KEY], apply_fn=apply, is_controller_fn=lambda: False))
        apply.assert_not_called()

    def test_a_changed_port_on_the_controller_reapplies(self):
        apply = mock.Mock(return_value=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(_ct().reapply_after_harvest(
                [FOH_KEY], apply_fn=apply, is_controller_fn=lambda: True))
        apply.assert_called_once_with()
        self.assertIn(FOH_KEY, out.getvalue())
        self.assertIn("#1238", out.getvalue())

    def test_a_test_run_never_touches_the_real_controller_tunnel(self):
        ct = _ct()
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "t"}), \
                mock.patch.object(ct, "apply_tunnel") as real_apply, \
                mock.patch.object(ct, "is_controller_account", return_value=True):
            self.assertIsNone(ct.reapply_after_harvest([FOH_KEY]))
        real_apply.assert_not_called()

    def test_a_failing_apply_is_loud_and_never_raises(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(_ct().reapply_after_harvest(
                [FOH_KEY], apply_fn=mock.Mock(side_effect=RuntimeError("boom")),
                is_controller_fn=lambda: True))
        self.assertIn("boom", err.getvalue())

    def test_an_apply_that_did_not_go_live_is_loud(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(_ct().reapply_after_harvest(
                [FOH_KEY], apply_fn=mock.Mock(return_value=False),
                is_controller_fn=lambda: True))
        self.assertIn(FOH_KEY, err.getvalue())


class TestOneRenderForInstallAndPush(_TempCache):
    """install and the post-harvest re-render are ONE render of the controller
    config; only the cache between them differs."""

    def capture(self, fn):
        configs = []

        def fake_provision(creds, bin_, cfg_path, config_text, *a, **k):
            configs.append(config_text)
            return True

        with mock.patch.object(w.profiles, "LANE_HOST", {}), \
                mock.patch.object(dl, "harvest_local_controller_filedrop_port",
                                  return_value=None), \
                mock.patch.object(tun, "_provision_managed_tunnel",
                                  side_effect=fake_provision), \
                mock.patch.object(gl, "reconcile_and_report", return_value=True), \
                _quiet():
            fn()
        self.assertEqual(len(configs), 1)
        return configs[0]

    def reapply(self, changed):
        return lambda: _ct().reapply_after_harvest(changed,
                                                   is_controller_fn=lambda: True)

    def test_the_first_push_install_has_no_share_route_for_a_new_lane(self):
        self.assertNotIn(FOH_ORIGIN, self.capture(w._setup_controller_webterm))

    def test_the_reapply_after_the_harvest_adds_it_in_the_same_push(self):
        install_cfg = self.capture(w._setup_controller_webterm)
        with _quiet():
            changed = dl.persist_measured_filedrop_ports({FOH_KEY: 8791}, dg.DROP_LANES)
        push_cfg = self.capture(self.reapply(changed))
        self.assertNotIn(FOH_ORIGIN, install_cfg)
        for path in ("^/s/", PROBE_RULE):
            self.assertIn("  - hostname: %s\n    path: %s\n    service: %s\n"
                          % (FOH_HOST, path, FOH_ORIGIN), push_cfg)

    def test_install_and_the_reapply_render_the_same_config(self):
        dl.write_drop_lanes_cache({FOH_KEY: 8791}, path=self.cache)
        self.assertEqual(self.capture(w._setup_controller_webterm),
                         self.capture(self.reapply([FOH_KEY])))


class TestShareProbeRule(unittest.TestCase):

    def rules(self):
        return dl.drop_ingress_rules_for_controller(dg.DROP_LANES, cache={FOH_KEY: 8791})

    def test_every_controller_share_route_has_its_probe_rule(self):
        rules = self.rules()
        shares = [r for r in rules if len(r) == 3 and r[1] == "^/s/"]
        self.assertGreaterEqual(len(shares), 5)
        for host, _path, origin in shares:
            probe = (host, PROBE_RULE, origin)
            self.assertIn(probe, rules, host)
            no_path = [r for r in rules if len(r) == 2 and r[0] == host]
            self.assertEqual(len(no_path), 1, host)
            self.assertLess(rules.index(probe), rules.index(no_path[0]), host)

    def test_a_lane_without_a_share_route_gets_no_probe_rule(self):
        rules = self.rules()
        share_hosts = {r[0] for r in rules if len(r) == 3 and r[1] == "^/s/"}
        probe_hosts = {r[0] for r in rules if len(r) == 3 and r[1] == PROBE_RULE}
        self.assertEqual(probe_hosts, share_hosts)

    def test_the_probe_rule_is_the_filedrop_servers_own_route_and_only_it(self):
        self.assertEqual(PROBE_RULE, "^%s$" % fserver.SHARE_ROUTE_PROBE_PATH)
        self.assertTrue(re.search(PROBE_RULE, "/healthz/s"))
        for other in ("/healthz", "/healthz/s/x", "/s/tok/f.txt", "/x/healthz/s"):
            self.assertIsNone(re.search(PROBE_RULE, other), other)


class TestLocalLaneProbeRule(unittest.TestCase):
    """A LOCAL lane's `--apply` renders the probe with its `/s/` pair; the
    install heal path (no filedrop port) still never rewrites a share config."""
    HOST = "drop-subdev-dominika.newlevel.media"
    BASE = ("tunnel: u\ningress:\n"
            "  - hostname: other.example\n    service: http://127.0.0.1:1\n"
            "  - service: http_status:404\n")
    PRE_1238 = ("tunnel: u\ningress:\n"
                "  - hostname: other.example\n    service: http://127.0.0.1:1\n"
                "  - hostname: %s\n    path: ^/s/\n    service: http://127.0.0.1:8804\n"
                "  - hostname: %s\n    service: http://127.0.0.1:8875\n"
                "  - service: http_status:404\n" % (HOST, HOST))
    PROBE = ("  - hostname: %s\n    path: ^/healthz/s$\n"
             "    service: http://127.0.0.1:8804\n" % HOST)

    def apply(self, text):
        return dg.render_drop_ingress_augmentation(text, self.HOST, 8875,
                                                   filedrop_port=8804)

    def test_apply_renders_the_probe_with_the_share_pair(self):
        out = self.apply(self.BASE)
        self.assertIn(self.PROBE, out)
        self.assertLess(out.index(self.PROBE),
                        out.index("    service: http://127.0.0.1:8875"))
        self.assertEqual(self.apply(out), out)

    def test_a_pre_1238_share_config_gains_the_probe_on_apply(self):
        out = self.apply(self.PRE_1238)
        self.assertEqual(out.count(self.PROBE), 1)
        self.assertEqual(out.count("path: ^/s/"), 1)
        self.assertEqual(out.count("    service: http://127.0.0.1:8875"), 1)
        self.assertIn("other.example", out)

    def test_the_install_heal_path_never_rewrites_a_share_config(self):
        self.assertEqual(dg.render_drop_ingress_augmentation(
            self.PRE_1238, self.HOST, 8875), self.PRE_1238)


class TestFiledropAnswersTheRouteProbe(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.httpd = fserver.make_server(host="127.0.0.1", port=0,
                                         base_dir=Path(td.name))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port

    def test_get_answers_204_with_its_own_port(self):
        with urllib.request.urlopen(self.base + "/healthz/s", timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers.get(fserver.FILEDROP_PORT_HEADER), str(self.port))

    def test_head_answers_the_same(self):
        req = urllib.request.Request(self.base + "/healthz/s", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers.get(fserver.FILEDROP_PORT_HEADER), str(self.port))

    def test_plain_healthz_is_still_not_a_filedrop_route(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/healthz", timeout=5)
        self.assertEqual(cm.exception.code, 404)

    def test_the_share_probe_reads_the_status_and_the_port(self):
        self.assertEqual(fw._public_share_probe(self.base + "/healthz/s"),
                         (204, str(self.port)))


if __name__ == "__main__":
    unittest.main()
