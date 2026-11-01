"""#1282 host grants of a project account + #1266 the bridge to a session on another box."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_account_policy as policy  # noqa: E402
import cli_account_verify as verify  # noqa: E402
import cli_peer_channel as cpc  # noqa: E402
import peer_channel as pc  # noqa: E402
from test_peer_channel_1266 import HOST, ME, _Env, _Recorder  # noqa: E402


class TestDeclaration(unittest.TestCase):
    def test_only_allow_listed_devices(self):
        self.assertEqual(policy.validate_host_grants({"devices": ["cpu_dma_latency"]}), [])
        for bad in (["sda"], ["mem"], "cpu_dma_latency", ["../sda"]):
            self.assertTrue(policy.validate_host_grants({"devices": bad}), repr(bad))

    def test_connection_name_takes_no_shell(self):
        self.assertEqual(policy.validate_host_grants({"nic_noqueue": "dante-net"}), [])
        for bad in ("a; id", "$(id)", "", 7):
            self.assertTrue(policy.validate_host_grants({"nic_noqueue": bad}), repr(bad))

    def test_a_bad_declaration_fails_validation(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["dstack"], devices=["sda"])
        self.assertTrue([e for e in bootstrap.validate_account("dstack", raw) if "devices" in e])

    def test_dstack_declares_the_requested_grants(self):
        spec = bootstrap.account_spec("dstack")
        self.assertEqual(spec["devices"], ["cpu_dma_latency"])
        self.assertEqual(spec["nic_noqueue"], "dante-net")
        self.assertIs(spec["sudo"], False)


class TestRender(unittest.TestCase):
    def _run(self, step, tmp, stubs=("setfacl", "udevadm", "tc", "getfacl", "nmcli")):
        bin_dir = Path(tmp, "bin")
        bin_dir.mkdir(exist_ok=True)
        for name in stubs:
            out = "dev0" if name == "nmcli" else "user:dstack:rw-" if name == "getfacl" else ""
            f = bin_dir / name
            f.write_text('#!/bin/sh\necho "%s $*" >> %s/calls\necho %s\n' % (name, tmp, out))
            f.chmod(0o755)
        script = step.replace(hardening.UDEV_RULES_DIR, tmp).replace(
            hardening.NM_DISPATCHER_DIR, tmp)
        return subprocess.run(["bash", "-c", "set -euo pipefail\nACCOUNT=dstack\n" + script],
                              capture_output=True, text=True,
                              env={"PATH": "%s:/usr/bin:/bin" % bin_dir})

    def test_a_declared_grant_is_applied(self):
        step = hardening.render_host_grants_step(
            "dstack", {"devices": ["cpu_dma_latency"], "nic_noqueue": "dante-net"})
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(step, tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            rule = Path(tmp, "90-dstack-devices.rules").read_text()
            self.assertIn("setfacl -m u:dstack:rw /dev/%k", rule)
            dispatcher = Path(tmp, "90-dstack-noqueue")
            self.assertTrue(os.access(dispatcher, os.X_OK))
            self.assertIn("root noqueue", dispatcher.read_text())
            self.assertIn("tc qdisc replace dev dev0 root noqueue",
                          Path(tmp, "calls").read_text())

    def test_a_dropped_key_removes_grant_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "90-dstack-devices.rules").write_text(
                'KERNEL=="cpu_dma_latency", RUN+="x"\n')
            Path(tmp, "90-dstack-noqueue").write_text("x")
            r = self._run(hardening.render_host_grants_step("dstack", {}), tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("90-*")), [])
            self.assertIn("setfacl -x u:dstack /dev/cpu_dma_latency",
                          Path(tmp, "calls").read_text())

    def test_no_declaration_needs_no_udev_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(hardening.render_host_grants_step("x", {}), tmp, stubs=())
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_connection_that_is_down_does_not_abort_the_bootstrap(self):
        step = hardening.render_host_grants_step("dstack", {"nic_noqueue": "dante-net"})
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp, "bin")
            self._run("", tmp)
            (bin_dir / "nmcli").write_text("#!/bin/sh\n")
            r = self._run(step.replace("nmcli -g", "%s/nmcli -g" % bin_dir), tmp, stubs=())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("dispatcher applies it", r.stdout)


class TestVerify(unittest.TestCase):
    SPEC = {"devices": ["cpu_dma_latency"], "nic_noqueue": "dante-net"}

    def test_declared_and_usable(self):
        s = {"grants": {"writable": ["cpu_dma_latency"], "qdisc": ["noqueue"], "dev": ["e0"]}}
        self.assertTrue(verify._check_host_grants(self.SPEC, s)[0])

    def test_missing_grant_fails(self):
        s = {"grants": {"writable": [], "qdisc": ["fq_codel"], "dev": ["e0"]}}
        ok, why = verify._check_host_grants(self.SPEC, s)
        self.assertFalse(ok)
        self.assertIn("fq_codel", why)

    def test_an_undeclared_writable_device_fails(self):
        self.assertFalse(verify._check_host_grants({}, {"grants": {"writable": ["cpu_dma_latency"]}})[0])


class _Child:
    """ssh stand-in: run_bridge on the far side of two real pipes."""

    def __init__(self, peer):
        to_far, self._far_in = os.pipe()
        far_out, to_home = os.pipe()
        self.stdin = os.fdopen(self._far_in, "w", buffering=1)
        self.stdout = os.fdopen(far_out)
        far_in = os.fdopen(to_far)
        out = os.fdopen(to_home, "w", buffering=1)
        self.returncode = None

        def run():
            pc.run_bridge(peer, inp=far_in, out=out)
            peer.close()
            out.close()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def wait(self):
        self.thread.join(5)
        self.returncode = 0

    def terminate(self):
        self.stdin.close()


class TestBridge(_Env):
    LINKS = [["alpha", "beta"], ["alpha", "home"]]
    SPEC = {"account": ME, "path": "~/devel/home",
            "hosts": {HOST: {"account": ME, "ssh": "u@h", "identity": "~/k", "path": "~/x"}}}

    def setUp(self):
        super().setUp()
        doc = json.loads(Path(self.cfg, pc.LINKS_FILE).read_text())
        doc["bridges"] = {"home": self.SPEC}
        Path(self.cfg, pc.LINKS_FILE).write_text(json.dumps(doc))
        self.far = pc.Peer(HOST, ME, os.getuid(), "/", self.home, project_arg="home",
                           sock_dir=self.sock, config_dir=self.cfg, log=_Recorder(), armed=True)
        self.addCleanup(self.far.close)

    def start_home(self, armed=True):
        rec, seen = _Recorder(), []
        home = pc.BridgeHome("home", self.SPEC, self.home, armed, log=rec,
                             spawn=lambda *a, **k: _Child(self.far), retry_s=60)
        home.notify = lambda text, meta: seen.append((text, meta))
        home.start_listener()
        self.addCleanup(home.close)
        if armed:
            for _ in range(200):
                if self.far.listener is not None and home.children:
                    break
                time.sleep(0.01)
        return home, seen

    def test_a_project_reaches_the_home_session(self):
        _, seen = self.start_home()
        ok, text = self.peer("alpha").send("home", "request")
        self.assertTrue(ok, text)
        for _ in range(200):
            if seen:
                break
            time.sleep(0.01)
        self.assertEqual(seen, [("request", {"from": "alpha"})])

    def test_the_home_session_answers_a_project(self):
        alpha = self.peer("alpha")
        alpha.start_listener()
        home, _ = self.start_home()
        ok, text = home.send("alpha", "answer")
        self.assertTrue(ok, text)
        self.assertEqual(alpha.delivered, [("answer", {"from": "home"})])

    def test_an_unlinked_project_is_refused_on_the_box(self):
        self.start_home()
        ok, text = self.peer("beta").send("home", "x")
        self.assertFalse(ok)
        self.assertIn("not linked", text)

    def test_no_session_means_no_socket_and_no_false_delivery(self):
        ok, text = self.peer("alpha").send("home", "x")
        self.assertFalse(ok)
        self.assertIn("no live session", text)

    def test_an_unarmed_session_opens_no_bridge(self):
        with mock.patch.object(pc.subprocess, "Popen") as popen:
            home = pc.BridgeHome("home", self.SPEC, self.home, False, log=_Recorder())
            home.start_listener()
            ok, text = home.send("alpha", "x")
        popen.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("--channels", text)

    def test_the_session_end_removes_the_socket(self):
        home, _ = self.start_home()
        path = self.far.sock_path
        self.assertTrue(os.path.exists(path))
        home.close()
        for _ in range(200):
            if not os.path.exists(path):
                break
            time.sleep(0.01)
        self.assertFalse(os.path.exists(path))

    def test_a_dead_bridge_is_reported(self):
        home, _ = self.start_home()
        home.children.clear()
        ok, text = home.send("alpha", "x")
        self.assertFalse(ok)
        self.assertIn("down", text)

    def test_only_the_home_directory_is_the_home_end(self):
        doc = {"bridges": {"home": {"account": ME, "path": "/srv/home"}}}
        with mock.patch("os.path.realpath", side_effect=lambda p: p):
            self.assertEqual(pc.bridge_home(doc, ME, "/srv/home/sub", "/h")[0], "home")
            self.assertIsNone(pc.bridge_home(doc, ME, "/srv/other", "/h"))
            self.assertIsNone(pc.bridge_home(doc, "someone", "/srv/home", "/h"))


class TestManagedSide(unittest.TestCase):
    def test_the_real_config_links_dstack_to_airuleset(self):
        links, registry = cpc.load_links(), cpc.load_registry()
        self.assertEqual(cpc.validate_links(links, registry, {"dev1", "dev2", "subdev", "gatekeeper"}), [])
        self.assertIn(frozenset(("airuleset", "dstack")), pc.host_links(links, "dev1"))
        self.assertIn("newlevel", cpc.accounts_for_host("dev1", links, registry))

    def test_the_bridge_adds_no_repo_token(self):
        self.assertNotIn("zbynekdrlik/airuleset", cpc.peer_repos("dstack"))

    def test_home_account_gets_the_plugin(self):
        doc = {"bridges": {"airuleset": {"account": "airuleset"}}}
        self.assertTrue(cpc.is_bridge_home("airuleset", doc))
        self.assertFalse(cpc.is_bridge_home("dstack", doc))

    def test_home_policy_failure_is_loud(self):
        doc = {"bridges": {"x": {"account": ME}}}
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="no sudo"))
        self.assertEqual(cpc.apply_home_policy(run, doc), [("home", "rc=1")])
        self.assertIn(cpc.POLICY_PATH, run.call_args[0][0][-1])

    def test_a_foreign_account_gets_no_home_policy(self):
        run = mock.Mock()
        self.assertEqual(cpc.apply_home_policy(run, {"bridges": {}}), [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
