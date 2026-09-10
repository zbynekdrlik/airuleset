"""Tests for #974 reopened — gateway code-hash reconcile.

The reconcile must detect when a RUNNING gateway's code hash (recorded in
Environment=AIRULESET_GATEWAY_CODE_HASH) differs from the freshly computed
hash, and restart the unit. Tests are hermetic: fake /proc, fake systemctl,
never touch live systemd.
"""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestComputeGatewayCodeHash(unittest.TestCase):
    """compute_gateway_code_hash: SHA256 of the gateway code set."""

    def test_hash_of_single_file(self):
        """When the gateway imports only stdlib, the hash is of the single file."""
        import cli_webterm_reconcile as rec

        gateway = Path(__file__).resolve().parent.parent / "cli_webterm_gateway.py"
        if not gateway.exists():
            self.fail("cli_webterm_gateway.py not found — cannot verify hash")
        h = rec.compute_gateway_code_hash(gateway)
        # Must be a valid hex SHA256
        self.assertEqual(len(h), 64)
        int(h, 16)  # must not raise
        # Must match the actual file's SHA256
        expected = hashlib.sha256(gateway.read_bytes()).hexdigest()
        self.assertEqual(h, expected)

    def test_hash_includes_local_imports(self):
        """When the gateway imports a local module, that module's content is
        included in the hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import cli_webterm_reconcile as rec
            # Create a fake gateway that imports a local module
            helper = Path(tmpdir) / "my_helper.py"
            helper.write_text("X = 1\n", encoding="utf-8")
            gateway = Path(tmpdir) / "gateway.py"
            gateway.write_text(
                "import os\nimport my_helper\ndef main(): pass\n",
                encoding="utf-8")
            h1 = rec.compute_gateway_code_hash(gateway)
            # Change the helper — hash must change
            helper.write_text("X = 2\n", encoding="utf-8")
            h2 = rec.compute_gateway_code_hash(gateway)
            self.assertNotEqual(h1, h2)

    def test_hash_stable_on_unchanged_code(self):
        """Two calls on the same code produce the same hash."""
        import cli_webterm_reconcile as rec

        gateway = Path(__file__).resolve().parent.parent / "cli_webterm_gateway.py"
        if not gateway.exists():
            self.fail("cli_webterm_gateway.py not found — cannot verify hash")
        h1 = rec.compute_gateway_code_hash(gateway)
        h2 = rec.compute_gateway_code_hash(gateway)
        self.assertEqual(h1, h2)


class TestReconcileCodeHash(unittest.TestCase):
    """reconcile_live_argv must restart a gateway when its code hash differs."""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"HOME": self._home})
        self._patcher.start()
        self.systemctl_calls = []

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)

    def _run_systemctl(self, args):
        self.systemctl_calls.append(args)
        if args[0] == "show" and "MainPID" in args:
            unit = args[-1]
            pid = self._pid_map.get(unit, "0")
            return (0, pid, "")
        if args[0] == "restart":
            return (0, "", "")
        if args[0] == "is-active":
            return (0, "active", "")
        return (0, "", "")

    def _make_proc_cmdline(self, pid, argv_parts):
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        cmdline = b"\x00".join(p.encode() for p in argv_parts) + b"\x00"
        (proc_dir / "cmdline").write_bytes(cmdline)

    def _make_proc_environ(self, pid, env_dict):
        """Create a fake /proc/<pid>/environ with the given env vars."""
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        entries = [("%s=%s" % (k, v)).encode() for k, v in env_dict.items()]
        (proc_dir / "environ").write_bytes(b"\x00".join(entries) + b"\x00")

    def test_mismatched_code_hash_triggers_restart(self):
        """A gateway whose recorded hash differs from the rendered hash must be
        restarted. This is the core bug this ticket fixes — today the predicate
        ignores code hashes entirely."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        self._pid_map = {"webterm-gateway.service": "54321"}
        # Argv matches — the path is correct
        self._make_proc_cmdline(54321, [
            "/usr/bin/env", "python3", correct_path,
            "--bind", "100.104.8.125", "--port", "8080",
        ])
        # Process was started with an OLD code hash
        self._make_proc_environ(54321, {
            "AIRULESET_GATEWAY_CODE_HASH": "a" * 64,
            "HOME": "/home/user",
        })

        rendered_paths = {"webterm-gateway.service": correct_path}
        code_hashes = {"webterm-gateway.service": "b" * 64}  # DIFFERENT hash

        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-gateway.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
            code_hashes=code_hashes,
        )
        self.assertEqual(restarted, ["webterm-gateway.service"])
        restart_calls = [c for c in self.systemctl_calls if c[0] == "restart"]
        self.assertEqual(len(restart_calls), 1)

    def test_matching_hash_and_argv_no_restart(self):
        """A gateway with matching argv AND matching code hash must NOT be restarted."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        the_hash = "c" * 64
        self._pid_map = {"webterm-gateway.service": "54321"}
        self._make_proc_cmdline(54321, [
            "/usr/bin/env", "python3", correct_path,
            "--bind", "100.104.8.125",
        ])
        self._make_proc_environ(54321, {
            "AIRULESET_GATEWAY_CODE_HASH": the_hash,
        })

        rendered_paths = {"webterm-gateway.service": correct_path}
        code_hashes = {"webterm-gateway.service": the_hash}

        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-gateway.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
            code_hashes=code_hashes,
        )
        self.assertEqual(restarted, [])
        restart_calls = [c for c in self.systemctl_calls if c[0] == "restart"]
        self.assertEqual(len(restart_calls), 0)

    def test_absent_hash_treated_as_stale(self):
        """A pre-existing unit with no AIRULESET_GATEWAY_CODE_HASH env var
        (first upgrade) must be treated as stale and restarted."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        self._pid_map = {"webterm-gateway.service": "54321"}
        self._make_proc_cmdline(54321, [
            "/usr/bin/env", "python3", correct_path,
            "--bind", "100.104.8.125",
        ])
        # NO environ file at all — simulates pre-existing unit with no hash
        # (or environ without the var)
        self._make_proc_environ(54321, {"HOME": "/home/user"})

        rendered_paths = {"webterm-gateway.service": correct_path}
        code_hashes = {"webterm-gateway.service": "d" * 64}

        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-gateway.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
            code_hashes=code_hashes,
        )
        self.assertEqual(restarted, ["webterm-gateway.service"])

    def test_ttyd_unit_not_hash_checked(self):
        """ttyd units are OUT of scope for code-hash checking — even when
        code_hashes is provided, a ttyd unit with matching argv must NOT
        be restarted (its connect command is spawned per connection)."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        self._pid_map = {"webterm-ttyd.service": "11111"}
        self._make_proc_cmdline(11111, ["/usr/bin/env", "bash", correct_path])
        # No environ needed — ttyd should not be hash-checked

        rendered_paths = {"webterm-ttyd.service": correct_path}
        # code_hashes only has gateway entries — ttyd is not in it
        code_hashes = {"webterm-gateway.service": "e" * 64}

        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
            code_hashes=code_hashes,
        )
        self.assertEqual(restarted, [])

    def test_restart_log_line_shape(self):
        """The log line for a code-hash restart must match the prescribed shape:
        'webterm: restarting <unit> — gateway code changed (<old8>→<new8>)'."""
        import cli_webterm_reconcile as rec
        import io

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        old_hash = "a1b2c3d4" + "0" * 56
        new_hash = "e5f6g7h8" + "0" * 56
        self._pid_map = {"webterm-gateway.service": "54321"}
        self._make_proc_cmdline(54321, [
            "/usr/bin/env", "python3", correct_path,
        ])
        self._make_proc_environ(54321, {
            "AIRULESET_GATEWAY_CODE_HASH": old_hash,
        })

        rendered_paths = {"webterm-gateway.service": correct_path}
        code_hashes = {"webterm-gateway.service": new_hash}

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rec.reconcile_live_argv(
                self._run_systemctl,
                ["webterm-gateway.service"],
                rendered_paths,
                log_prefix="webterm",
                proc_root=Path(self._home) / "fake_proc",
                code_hashes=code_hashes,
            )
        output = buf.getvalue()
        self.assertIn("gateway code changed", output)
        self.assertIn(old_hash[:8], output)
        self.assertIn(new_hash[:8], output)


class TestStatusCodeHashVerdict(unittest.TestCase):
    """check_webterm_argv_health must include code-hash verdict in status rows."""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"HOME": self._home})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)

    def _run_systemctl(self, args):
        if args[0] == "show" and "MainPID" in args:
            unit = args[-1]
            pid = self._pid_map.get(unit, "0")
            return (0, pid, "")
        return (0, "", "")

    def _make_proc_cmdline(self, pid, argv_parts):
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        cmdline = b"\x00".join(p.encode() for p in argv_parts) + b"\x00"
        (proc_dir / "cmdline").write_bytes(cmdline)

    def _make_proc_environ(self, pid, env_dict):
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        entries = [("%s=%s" % (k, v)).encode() for k, v in env_dict.items()]
        (proc_dir / "environ").write_bytes(b"\x00".join(entries) + b"\x00")

    def test_ok_with_hash_match(self):
        """Status shows 'OK (live argv + code hash match)' when both match."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        the_hash = "f" * 64
        self._pid_map = {"webterm-gateway.service": "100"}
        self._make_proc_cmdline(100, [
            "/usr/bin/env", "python3", correct_path,
        ])
        self._make_proc_environ(100, {
            "AIRULESET_GATEWAY_CODE_HASH": the_hash,
        })

        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-gateway.service": correct_path},
            proc_root=Path(self._home) / "fake_proc",
            code_hashes={"webterm-gateway.service": the_hash},
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("OK", lines[0])
        self.assertIn("code hash match", lines[0])

    def test_stale_hash_status(self):
        """Status shows 'STALE (code hash ...)' when hash differs."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        self._pid_map = {"webterm-gateway.service": "100"}
        self._make_proc_cmdline(100, [
            "/usr/bin/env", "python3", correct_path,
        ])
        self._make_proc_environ(100, {
            "AIRULESET_GATEWAY_CODE_HASH": "a" * 64,
        })

        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-gateway.service": correct_path},
            proc_root=Path(self._home) / "fake_proc",
            code_hashes={"webterm-gateway.service": "b" * 64},
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("STALE", lines[0])
        self.assertIn("code hash", lines[0])

    def test_no_hash_unit_shows_argv_only_ok(self):
        """A ttyd unit (not in code_hashes) shows 'OK (live argv matches rendered)'."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        self._pid_map = {"webterm-ttyd.service": "100"}
        self._make_proc_cmdline(100, ["/usr/bin/env", "bash", correct_path])

        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-ttyd.service": correct_path},
            proc_root=Path(self._home) / "fake_proc",
            code_hashes={},
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("OK", lines[0])
        self.assertIn("live argv matches rendered", lines[0])


if __name__ == "__main__":
    unittest.main()


class TestRenderedUnitCarriesHash(unittest.TestCase):
    """MEDIUM-1 fix: the PRIMARY path — rendered gateway units must carry
    Environment=AIRULESET_GATEWAY_CODE_HASH in [Service], before [Install]."""

    def test_owner_unit_carries_hash(self):
        """_render_webterm_gateway_unit embeds the code hash."""
        from cli_webterm import _render_webterm_gateway_unit, WEBTERM_GATEWAY_MODULE
        import cli_webterm_reconcile as rec
        with patch("cli_webterm._tailscale_ip", return_value="100.104.8.125"):
            unit_text = _render_webterm_gateway_unit("100.104.8.125")
        expected_hash = rec.compute_gateway_code_hash(WEBTERM_GATEWAY_MODULE)
        self.assertIn("Environment=AIRULESET_GATEWAY_CODE_HASH=" + expected_hash,
                       unit_text)
        # Must be between [Service] and [Install]
        service_idx = unit_text.index("[Service]")
        install_idx = unit_text.index("[Install]")
        hash_idx = unit_text.index("AIRULESET_GATEWAY_CODE_HASH=")
        self.assertGreater(hash_idx, service_idx)
        self.assertLess(hash_idx, install_idx)

    def test_lane_unit_carries_hash(self):
        """render_gateway_unit (lane path) embeds the code hash."""
        from cli_webterm_lane import render_gateway_unit
        from cli_webterm import WEBTERM_GATEWAY_MODULE
        import cli_webterm_reconcile as rec

        class FakeSpec:
            unit_note = ""
            bind = "127.0.0.1"
            gateway_port = 8081
            dash_index = "/tmp/dash.html"
            ttyd_port = 7683
            profile = "david"
            ttyd_service_name = "webterm-david-ttyd.service"
            gateway_sock_basename = "webterm-david-gateway.sock"
            ttyd_sock_basename = "webterm-david-ttyd.sock"
            label = "(david lane)"
            collector_mode = None

        unit_text = render_gateway_unit(FakeSpec())
        expected_hash = rec.compute_gateway_code_hash(WEBTERM_GATEWAY_MODULE)
        self.assertIn("Environment=AIRULESET_GATEWAY_CODE_HASH=" + expected_hash,
                       unit_text)
