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
import cli_webterm_tunnel as tun  # noqa: E402
import cli_filedrop_watchdog as fw  # noqa: E402
import cli_binary_installers as binstall  # noqa: E402  (#614 ttyd auto-install)


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
        self.assertIn(str(d.WEBTERM_DAVID_DASH_INDEX), unit)
        # NOT the owner credential/dashboard realm.
        self.assertNotIn(str(w.WEBTERM_CRED_PATH), unit)

    def test_gateway_unit_is_cloudflare_access_mode_no_credential(self):
        # #612 owner directive 2026-08-22: NO password. The ExecStart runs the
        # gateway in --trust-access-header mode (Cloudflare Access email OTP at
        # the edge), and NEVER validates a --cred credential.
        unit = d.render_david_gateway_unit()
        self.assertIn("--trust-access-header Cf-Access-Authenticated-User-Email",
                      unit)
        self.assertNotIn("--cred ", unit)                 # no credential flag
        # The dead credential PATH must not appear as a live ExecStart argument.
        self.assertNotIn("--cred %s" % w.WEBTERM_DAVID_CRED_PATH, unit)
        # The prepended NOTE states the Access auth model explicitly.
        self.assertIn("Cloudflare", unit)
        self.assertIn("email one-time-PIN", unit)

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
        # #635: redirect the david TUNNEL path constants into tmp too — on the subdev
        # box the REAL creds JSON (1564fe31-….json) exists, so setup_webterm_david_
        # service now transitively calls setup_webterm_david_tunnel, which would write
        # the real ~/.cloudflared/config.yml + unit (systemctl is mocked, so only the
        # file writes would leak). No creds JSON is created in tmp, so the tunnel stays
        # a safe no-op in these tests (mirrors the owner isolation in test_webterm.py).
        stack.enter_context(m.patch.object(tun, "WEBTERM_CLOUDFLARED_DIR",
                                           base / ".cloudflared"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_TUNNEL_CREDS",
            base / ".cloudflared" / (d.WEBTERM_DAVID_TUNNEL_UUID + ".json")))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_TUNNEL_CONFIG", base / ".cloudflared" / "config.yml"))
        stack.enter_context(m.patch.object(
            d, "WEBTERM_DAVID_TUNNEL_SERVICE_DEST",
            base / "systemd" / "webterm-david-tunnel.service"))
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
            # #612 owner directive: NO credential is provisioned any more
            # (Cloudflare Access replaces the password) — the file is absent.
            self.assertFalse((secrets / "webterm_david_credential").exists())

    def test_write_artifacts_retires_a_pre_existing_credential(self):
        # A subdev box that carried the OLD password credential must have it
        # DELETED on the next provision (retire the dead `secret show` channel).
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            _claude, secrets = self._isolate(st, tmp)
            secrets.mkdir(parents=True, exist_ok=True)
            cred = secrets / "webterm_david_credential"
            cred.write_text("david:oldpassword\n", encoding="utf-8")
            self.assertTrue(cred.exists())
            d._write_david_artifacts()
            self.assertFalse(cred.exists())          # retired

    def test_retire_credential_is_a_safe_noop_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            _claude, secrets = self._isolate(st, tmp)
            # No credential file exists — retirement must return False, not raise.
            self.assertFalse(d._retire_david_credential())

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


class TestDavidDropInInvariant638(unittest.TestCase):
    """#638: the #614 invariant — airuleset renders the MAIN unit and NEVER
    writes or deletes `.d/` drop-ins — STANDS. The redundant hand-placed
    `10-path.conf` is removed once by hand (owner-action), never by code.
    These lock the decision mechanically so a future worker cannot QUIETLY
    add drop-in write/deletion (or a `.d/` scanner) next to an invariant that
    says the opposite — the exact "not defensible" outcome the ticket names."""

    _SRC = Path(__file__).resolve().parent.parent / "cli_webterm_david.py"

    def test_module_never_touches_a_dropin_in_code(self):
        # The invariant, mechanically: any reference to a `.service.d` drop-in
        # path in this module's SOURCE must live in a comment. The module
        # renders the MAIN unit only — it never writes, deletes, or otherwise
        # references a `.service.d/` path in code. A future change that adds any
        # drop-in handling would put `service.d` on an executable line and trip
        # this, forcing the invariant change to be NAMED, not made quietly
        # (#614/#638). Accepted residual: a path built via string indirection
        # that never contains the literal `service.d` would evade it — the
        # realistic/naive footgun form is caught; see the #638 design comment.
        offenders = [
            (i, line.strip())
            for i, line in enumerate(self._SRC.read_text(
                encoding="utf-8").splitlines(), 1)
            if "service.d" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(
            offenders, [],
            "cli_webterm_david.py references a .service.d drop-in path OUTSIDE "
            "a comment — the #614/#638 invariant is that airuleset renders the "
            "MAIN unit and NEVER writes or deletes .d/ drop-ins. If you are "
            "deliberately changing that invariant, say so on the ticket and "
            "update this test with justification. Offending: %r" % offenders)

    def test_638_decision_recorded_at_the_path_risk_site(self):
        # The risk-site comment near _DAVID_TTYD_PATH_ENV must warn a future
        # PATH editor that a stale hand-placed drop-in would silently override
        # a changed PATH and airuleset will not clean it (the ticket's fear:
        # "nobody will know why"). Content-lock so the warning is never dropped.
        src = self._SRC.read_text(encoding="utf-8")
        flat = src.replace("\n", " ")
        # assertTrue(needle in text, msg) — never assertIn, so a miss does not
        # dump the whole module source as the mismatch haystack (#419 lesson).
        for needle, hay in (("#638", src), ("10-path.conf", src),
                            ("DropInPaths", src),
                            ("never writes or deletes", flat)):
            self.assertTrue(
                needle in hay,
                "cli_webterm_david.py risk-site comment is missing %r — the "
                "#638 decision (invariant stands; stale drop-in silently "
                "overrides a changed PATH; airuleset never cleans .d/) must "
                "stay recorded where a future PATH editor will read it." % needle)

    def test_main_unit_still_carries_the_path_so_no_dropin_is_needed(self):
        # The machinery the decision KEEPS: the PATH lives in the MAIN unit,
        # so a clean start needs no drop-in at all (#614). If this regresses,
        # the "invariant stands" decision is no longer safe. And the render
        # itself must never emit a drop-in path.
        unit = d.render_david_ttyd_unit()
        self.assertIn("Environment=PATH=%h/.local/bin:", unit)
        self.assertNotIn(".service.d", unit)


class TestDavidTtydAutoInstall(unittest.TestCase):
    """#614 (owner decision 2026-08-23, Approach 2): auto-install the ttyd
    BINARY into ~/.local/bin so a fresh subdev re-provision no longer depends on
    the #612 hand install. It runs BEFORE the prerequisite gate (which REQUIRES
    ttyd) and is best-effort/non-fatal, exactly how cmd_install calls the
    ffmpeg/claude installers."""

    def test_installer_runs_before_the_prerequisite_gate(self):
        # Order is load-bearing: prerequisites_ready() gates on ttyd being
        # PRESENT, so a fresh (ttyd-absent) box would no-op the gate forever
        # unless the binary is installed FIRST.
        order = []

        def fake_gate():
            order.append("gate")
            return False, "not the gateway account"

        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            lambda *a, **k: order.append("install")), \
                m.patch.object(d, "prerequisites_ready", side_effect=fake_gate):
            self.assertFalse(d.setup_webterm_david_service())
        self.assertEqual(order, ["install", "gate"])

    def test_installer_failure_never_breaks_setup(self):
        # Best-effort/non-fatal: a raise inside the installer must not crash the
        # never-raises setup — it just no-ops on the gate as usual, touching no
        # systemd.
        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            side_effect=RuntimeError("network down")), \
                m.patch.object(fw, "_whoami", lambda: "marek"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(d.setup_webterm_david_service())   # must not raise


if __name__ == "__main__":
    unittest.main()
