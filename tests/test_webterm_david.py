"""Tests for the DAVID developer gateway provisioning (#612, cli_webterm_david).

The subdev live systemd path is unverifiable from dev1, so these pin the
RENDER + GATE + ARTIFACT correctness (systemctl mocked, exactly like the owner
provisioning test): the gate is a SAFE no-op unless prerequisites hold, the
gateway unit binds LOOPBACK with the david realm's cred/dash and an `After=`
pointing at the DAVID ttyd unit, and the written launcher EXPORTS the scoped
`WEBTERM_INVENTORY` env var (not a client-injectable argv flag) so the connect
allowlist is david's set.
"""
import contextlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_david as d  # noqa: E402
import cli_webterm_profiles as p  # noqa: E402
import cli_filedrop_watchdog as fw  # noqa: E402


class TestDavidUnitRender(unittest.TestCase):
    def test_gateway_unit_binds_loopback_with_david_realm(self):
        unit = d.render_david_gateway_unit()
        self.assertIn("--bind %s" % d.WEBTERM_DAVID_BIND, unit)
        self.assertEqual(d.WEBTERM_DAVID_BIND, "127.0.0.1")
        # The actual bind is loopback — never an `--bind` to a tailscale/public
        # IP (the "100.64.0.0/10" text elsewhere is only a template comment).
        self.assertNotIn("--bind 100.", unit)
        self.assertNotIn("--bind 0.0.0.0", unit)
        self.assertIn("--port %d" % d.WEBTERM_DAVID_GATEWAY_PORT, unit)
        self.assertIn("--ttyd-port %d" % d.WEBTERM_DAVID_TTYD_PORT, unit)
        self.assertIn(str(w.WEBTERM_DAVID_CRED_PATH), unit)
        self.assertIn(str(d.WEBTERM_DAVID_DASH_INDEX), unit)
        # NOT the owner credential/dashboard realm.
        self.assertNotIn(str(w.WEBTERM_CRED_PATH), unit)

    def test_gateway_after_points_at_david_ttyd_unit(self):
        unit = d.render_david_gateway_unit()
        self.assertIn("webterm-david-ttyd.service", unit)
        # The owner ttyd unit name must NOT survive as a stray dependency.
        self.assertNotIn("network-online.target webterm-ttyd.service", unit)
        self.assertNotIn("(dev1-only)", unit)

    def test_ttyd_unit_execs_the_david_launcher(self):
        unit = d.render_david_ttyd_unit()
        self.assertIn(str(d.WEBTERM_DAVID_LAUNCH_PATH), unit)
        self.assertNotIn("(dev1-only)", unit)

    def test_ttyd_unit_carries_self_contained_path(self):
        # #614: the DAVID ttyd unit must be PATH self-contained so bare
        # `exec ttyd` in the launcher resolves the no-sudo ~/.local/bin
        # user-space static binary on a clean systemd --user manager start
        # (reboot / fresh re-provision), WITHOUT a hand-placed .d/ drop-in.
        unit = d.render_david_ttyd_unit()
        self.assertIn(
            "Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:"
            "/usr/sbin:/usr/bin:/sbin:/bin", unit)
        # The PATH directive sits INSIDE the [Service] block — after its header
        # AND before the [Install] section, so a mis-injection past [Install]
        # would not pass either.
        self.assertIn("[Service]", unit)
        self.assertLess(unit.index("[Service]"), unit.index("Environment=PATH="))
        self.assertLess(unit.index("Environment=PATH="), unit.index("[Install]"))

    def test_owner_ttyd_unit_has_no_path_env(self):
        # The PATH env is scoped to the DAVID render ONLY — the owner (dev1)
        # unit, where ttyd is a system /usr/bin binary already on the manager
        # PATH, must NOT gain the line (#614).
        self.assertNotIn("Environment=PATH=", w._render_webterm_unit())


class TestDavidPrerequisiteGate(unittest.TestCase):
    def test_no_op_when_not_the_gateway_account(self):
        with m.patch.object(fw, "_whoami", lambda: "marek"):
            ok, reason = d.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("gateway account", reason)

    def test_no_op_when_key_or_ttyd_missing(self):
        with m.patch.object(fw, "_whoami", lambda: p.DAVID_GATEWAY_USER), \
                m.patch.object(d.shutil, "which", return_value=None):
            ok, reason = d.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("prerequisites missing", reason)

    def test_ready_when_ttyd_only_in_local_bin(self):
        # On subdev ttyd is a no-sudo ~/.local/bin static binary that the
        # push-driven ssh install PATH does NOT include, so `shutil.which`
        # returns None even though ttyd is genuinely present — the gate must
        # still be READY via the explicit ~/.local/bin/ttyd check (#614),
        # else the box would never re-provision.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".local" / "bin").mkdir(parents=True)
            ttyd = home / ".local" / "bin" / "ttyd"
            ttyd.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(ttyd, 0o755)
            key = home / "webterm_david_ed25519"
            key.write_text("dummy", encoding="utf-8")
            with m.patch.dict(os.environ, {"HOME": str(home)}), \
                    m.patch.object(fw, "_whoami", lambda: p.DAVID_GATEWAY_USER), \
                    m.patch.object(d.shutil, "which", return_value=None), \
                    m.patch.object(p, "WEBTERM_DAVID_IDENTITY", str(key)):
                ok, reason = d.prerequisites_ready()
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ready")

    def test_still_no_op_when_ttyd_absent_everywhere(self):
        # Belt-and-suspenders: with `which` None AND no ~/.local/bin/ttyd, the
        # gate stays a SAFE no-op (the new local-bin check must not make the
        # gate pass on a genuinely ttyd-less box) (#614).
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            key = home / "webterm_david_ed25519"
            key.write_text("dummy", encoding="utf-8")
            with m.patch.dict(os.environ, {"HOME": str(home)}), \
                    m.patch.object(fw, "_whoami", lambda: p.DAVID_GATEWAY_USER), \
                    m.patch.object(d.shutil, "which", return_value=None), \
                    m.patch.object(p, "WEBTERM_DAVID_IDENTITY", str(key)):
                ok, reason = d.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("prerequisites missing", reason)

    def test_setup_is_a_safe_noop_when_not_ready(self):
        # The gate must return False WITHOUT ever calling systemctl.
        with m.patch.object(fw, "_whoami", lambda: "montalu"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(d.setup_webterm_david_service())


class TestDavidArtifactsWrite(unittest.TestCase):
    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        secrets = base / ".secrets"
        keyfile = base / "webterm_david_ed25519"
        keyfile.write_text("dummy-key", encoding="utf-8")
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(w, "SECRETS_DIR", secrets))
        stack.enter_context(m.patch.object(
            w, "WEBTERM_DAVID_CRED_PATH", secrets / "webterm_david_credential"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_INVENTORY_PATH", claude / "webterm-david-inventory.json"))
        stack.enter_context(m.patch.object(d, "WEBTERM_DAVID_DASH_DIR",
                                           claude / "webterm-david-dash"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_DASH_INDEX",
            claude / "webterm-david-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_LAUNCH_PATH", claude / "airuleset-webterm-david-ttyd.sh"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_SERVICE_DEST",
            base / "systemd" / "webterm-david-ttyd.service"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-david-gateway.service"))
        return claude, secrets

    def test_write_artifacts_scoped_inventory_and_launcher(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude, secrets = self._isolate(st, tmp)
            d._write_david_artifacts()
            inv = json.loads((claude / "webterm-david-inventory.json")
                             .read_text(encoding="utf-8"))
            ids = [e["id"] for e in inv]
            self.assertEqual(ids, ["david1", "david2", "david3", "david4",
                                   "codex-bridge"])
            # The scoped inventory carries NO owner-fleet id.
            self.assertNotIn("dev1", ids)
            launcher = (claude / "airuleset-webterm-david-ttyd.sh").read_text(
                encoding="utf-8")
            # Scoped inventory handed via the env var, never a client-injectable
            # argv flag (#612 review).
            self.assertIn("export WEBTERM_INVENTORY=", launcher)
            self.assertIn("webterm-david-inventory.json", launcher)
            self.assertNotIn("--inventory", launcher)
            self.assertIn("-p %d" % d.WEBTERM_DAVID_TTYD_PORT, launcher)
            # The david credential realm was created with the david login.
            cred = (secrets / "webterm_david_credential").read_text(
                encoding="utf-8").strip()
            self.assertTrue(cred.startswith("david:"))

    def test_full_setup_when_ready_provisions_and_enables(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            self._isolate(st, tmp)
            st.enter_context(m.patch.object(fw, "_whoami",
                                            lambda: p.DAVID_GATEWAY_USER))
            st.enter_context(m.patch.object(d.shutil, "which",
                                            return_value="/usr/bin/ttyd"))
            st.enter_context(m.patch.object(
                p, "WEBTERM_DAVID_IDENTITY", str(Path(tmp) / "webterm_david_ed25519")))
            st.enter_context(m.patch.object(
                fw, "_run_systemctl", lambda args: (calls.append(args), (0, "", ""))[1]))
            st.enter_context(m.patch.object(d.subprocess, "run",
                                            return_value=None))
            ok = d.setup_webterm_david_service()
        self.assertTrue(ok)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("enable --now webterm-david-gateway.service" in f
                            for f in flat))


if __name__ == "__main__":
    unittest.main()
