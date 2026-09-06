"""Tests for the 6 F4a-live wirings (#870).

Wiring 1: setup_service skips tunnel_fn when shared_tunnel=True
Wiring 2: render_gateway_unit reads collector_mode
Wiring 3: Controller dispatch in maybe_setup_webterm
Wiring 4: Owner LaneSpec in cli_webterm_zbynek.py
Wiring 5: Y5 dev1-side profile_for_host
Wiring 6: cli_privileges.PRIVILEGES entries
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


# Pin box class in EVERY test touching box-class-dependent branches.
def _pin_box(m, cls="workstation"):
    return m.patch("watchdog.reaper.default_box_class", return_value=cls)


class TestWiring1SharedTunnelSkip(unittest.TestCase):
    """setup_service skips tunnel_fn when shared_tunnel=True."""

    def test_shared_tunnel_true_skips_tunnel_fn(self):
        """A LaneSpec with shared_tunnel=True must NOT call tunnel_fn."""
        import cli_webterm_lane as lane

        tunnel_fn = mock.MagicMock()
        prereq_fn = mock.MagicMock(return_value=(True, "ready"))
        write_fn = mock.MagicMock(return_value=(False, False))

        spec = _make_minimal_spec(shared_tunnel=True)
        with mock.patch.object(lane, "binstall") as binstall_mock, \
             mock.patch("cli_filedrop_watchdog._run_systemctl",
                        return_value=(0, "", "")), \
             mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="airuleset"), \
             _pin_box(mock):
            binstall_mock.ensure_ttyd_static_binary = mock.MagicMock()
            lane.setup_service(
                spec, run=mock.MagicMock(),
                prereq_fn=prereq_fn,
                write_artifacts_fn=write_fn,
                tunnel_fn=tunnel_fn)

        tunnel_fn.assert_not_called()

    def test_shared_tunnel_false_calls_tunnel_fn(self):
        """A LaneSpec with shared_tunnel=False MUST call tunnel_fn."""
        import cli_webterm_lane as lane

        tunnel_fn = mock.MagicMock()
        prereq_fn = mock.MagicMock(return_value=(True, "ready"))
        write_fn = mock.MagicMock(return_value=(False, False))

        spec = _make_minimal_spec(shared_tunnel=False)
        with mock.patch.object(lane, "binstall") as binstall_mock, \
             mock.patch("cli_filedrop_watchdog._run_systemctl",
                        return_value=(0, "", "")), \
             mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="airuleset"), \
             _pin_box(mock):
            binstall_mock.ensure_ttyd_static_binary = mock.MagicMock()
            lane.setup_service(
                spec, run=mock.MagicMock(),
                prereq_fn=prereq_fn,
                write_artifacts_fn=write_fn,
                tunnel_fn=tunnel_fn)

        tunnel_fn.assert_called_once()


class TestWiring2CollectorMode(unittest.TestCase):
    """render_gateway_unit reads collector_mode."""

    def test_collector_mode_none_uses_u_lane(self):
        """collector_mode=None keeps the existing --u-lane injection."""
        import cli_webterm_lane as lane

        spec = _make_render_spec(collector_mode=None)
        with _pin_box(mock):
            unit = lane.render_gateway_unit(spec)
        self.assertIn("--u-lane", unit)
        self.assertNotIn("--u-collect", unit)

    def test_collector_mode_u_collect_replaces_u_lane(self):
        """collector_mode='--u-collect' replaces --u-lane."""
        import cli_webterm_lane as lane

        spec = _make_render_spec(collector_mode="--u-collect")
        with _pin_box(mock):
            unit = lane.render_gateway_unit(spec)
        self.assertIn("--u-collect", unit)
        self.assertNotIn("--u-lane", unit)


class TestWiring3ControllerDispatch(unittest.TestCase):
    """Controller dispatch in maybe_setup_webterm."""

    def test_controller_provisions_shared_tunnel(self):
        """On a controller box as airuleset, maybe_setup_webterm provisions
        the shared tunnel via tun._provision_managed_tunnel."""
        import cli_webterm as w
        import cli_webterm_tunnel as tun
        import pwd as _pwd

        with mock.patch("watchdog.reaper.default_box_class",
                        return_value="controller"), \
             mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="airuleset"), \
             mock.patch.object(_pwd, "getpwuid",
                               return_value=SimpleNamespace(
                                   pw_name="airuleset")), \
             mock.patch.object(tun, "_provision_managed_tunnel",
                               return_value=True) as prov_mock, \
             mock.patch("cli_webterm.setup_webterm_service"), \
             mock.patch("cli_webterm.profiles") as prof_mock:
            prof_mock.LANE_HOST = {
                "zbynek": "dev1", "david": "subdev",
                "marek": "subdev", "dominika": "subdev",
            }
            prof_mock.OWNER = "owner"
            prof_mock.DAVID = "david"
            prof_mock.MAREK = "marek"
            prof_mock.DOMINIKA = "dominika"
            w.maybe_setup_webterm()

        # The shared tunnel must have been provisioned
        prov_mock.assert_called_once()
        call_kwargs = prov_mock.call_args
        # Verify the config text contains multi-ingress catch-all
        config_text = call_kwargs[0][3] if len(call_kwargs[0]) > 3 else ""
        self.assertIn("http_status:404", config_text)

    def test_controller_with_hosted_lane_provisions_lane_and_tunnel(self):
        """When LANE_HOST has a human on 'controller', the controller dispatch
        provisions that lane AND the shared tunnel with an ingress rule for it."""
        import cli_webterm as w
        import cli_webterm_tunnel as tun
        import cli_webterm_zbynek as zbynek
        import pwd as _pwd

        zbynek_setup = mock.MagicMock()
        with mock.patch("watchdog.reaper.default_box_class",
                        return_value="controller"), \
             mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="airuleset"), \
             mock.patch.object(_pwd, "getpwuid",
                               return_value=SimpleNamespace(
                                   pw_name="airuleset")), \
             mock.patch.object(tun, "_provision_managed_tunnel",
                               return_value=True) as prov_mock, \
             mock.patch("cli_webterm.setup_webterm_service"), \
             mock.patch.object(zbynek, "setup_webterm_zbynek_service",
                               zbynek_setup), \
             mock.patch("cli_webterm.profiles") as prof_mock:
            prof_mock.LANE_HOST = {
                "zbynek": "controller", "david": "subdev",
                "marek": "subdev", "dominika": "subdev",
            }
            prof_mock.OWNER = "owner"
            prof_mock.DAVID = "david"
            prof_mock.MAREK = "marek"
            prof_mock.DOMINIKA = "dominika"
            w.maybe_setup_webterm()

        # zbynek's lane setup must have been called
        zbynek_setup.assert_called_once()
        # The shared tunnel must carry zbynek's ingress rule
        prov_mock.assert_called_once()
        config_text = prov_mock.call_args[0][3]
        self.assertIn("zbynek.newlevel.media", config_text)
        self.assertIn("webterm-zbynek-gateway.sock", config_text)
        # credentials-file must be controller-webterm.json (drift-lock)
        self.assertIn("controller-webterm.json", config_text)
        # The service_name positional arg
        self.assertEqual(prov_mock.call_args[0][5],
                         "webterm-controller-tunnel.service")

    def test_non_controller_dev1_is_noop_after_zbynek_flip(self):
        """#870 F4c-zbynek: after the flip, dev1 hosts NO lane. A dev1 install
        call to maybe_setup_webterm does nothing (profile_for_host returns None,
        setup_webterm_service is NOT called)."""
        import cli_webterm as w
        import pwd as _pwd

        with mock.patch("watchdog.reaper.default_box_class",
                        return_value="workstation"), \
             mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="newlevel"), \
             mock.patch.object(_pwd, "getpwuid",
                               return_value=SimpleNamespace(
                                   pw_name="newlevel")), \
             mock.patch("os.uname") as uname_mock, \
             mock.patch("cli_webterm.setup_webterm_service",
                        return_value=True) as setup_mock:
            uname_mock.return_value = SimpleNamespace(nodename="dev1")
            w.maybe_setup_webterm()
        setup_mock.assert_not_called()

    def test_creds_path_matches_privileges(self):
        """Drift-lock: the controller tunnel creds path in the dispatch code
        must match the PRIVILEGES registry entry."""
        import cli_webterm as w
        import cli_privileges as p
        priv = next(priv for priv in p.PRIVILEGES
                    if priv.name == "controller_tunnel_creds")
        # The PRIVILEGES local_path is ~-relative; the code uses Path.home()
        expected_name = priv.local_path.split("/")[-1]
        self.assertEqual(w.CONTROLLER_TUNNEL_CREDS_NAME, expected_name)


class TestWiring4ZbynekSpec(unittest.TestCase):
    """Owner LaneSpec in cli_webterm_zbynek.py."""

    def test_spec_shared_tunnel_true(self):
        import cli_webterm_zbynek as zbynek
        spec = zbynek._spec()
        self.assertTrue(spec.shared_tunnel)

    def test_spec_collector_mode(self):
        import cli_webterm_zbynek as zbynek
        spec = zbynek._spec()
        self.assertEqual(spec.collector_mode, "--u-collect")

    def test_spec_tunnel_hostname(self):
        import cli_webterm_zbynek as zbynek
        spec = zbynek._spec()
        self.assertEqual(spec.tunnel_hostname, "zbynek.newlevel.media")

    def test_spec_ports(self):
        import cli_webterm_zbynek as zbynek
        spec = zbynek._spec()
        self.assertEqual(spec.ttyd_port, 7686)
        self.assertEqual(spec.gateway_port, 8084)

    def test_spec_profile_is_owner(self):
        import cli_webterm_zbynek as zbynek
        import cli_webterm_profiles as profiles
        spec = zbynek._spec()
        self.assertEqual(spec.profile, profiles.OWNER)


class TestWiring4RuleOfN(unittest.TestCase):
    """Rule-of-N=4 lock: LANE_HOST has exactly 4 humans."""

    def test_lane_host_count(self):
        import cli_webterm_profiles as profiles
        self.assertEqual(len(profiles.LANE_HOST), 4)

    def test_all_lane_host_keys_have_spec_factories(self):
        """Every human in LANE_HOST must have a corresponding thin module
        with a _spec() factory."""
        import cli_webterm_profiles as profiles
        _HUMAN_TO_MODULE = {
            "zbynek": "cli_webterm_zbynek",
            "david": "cli_webterm_david",
            "marek": "cli_webterm_marek",
            "dominika": "cli_webterm_dominika",
        }
        for human in profiles.LANE_HOST:
            mod_name = _HUMAN_TO_MODULE.get(human)
            self.assertIsNotNone(mod_name,
                                 "no module mapping for human %r" % human)
            import importlib
            mod = importlib.import_module(mod_name)
            self.assertTrue(hasattr(mod, "_spec"),
                            "%s has no _spec()" % mod_name)


class TestWiring5ProfileForHost(unittest.TestCase):
    """Y5: dev1-side profile_for_host returns None when zbynek moves off dev1."""

    def test_dev1_returns_none_after_zbynek_flip(self):
        """#870 F4c-zbynek: with LANE_HOST['zbynek']=='controller' (the flip),
        dev1 returns None -- no lane is hosted there any more."""
        import cli_webterm_profiles as profiles
        self.assertEqual(profiles.LANE_HOST["zbynek"], "controller")
        result = profiles.profile_for_host("dev1")
        self.assertIsNone(result)

    def test_dev1_returns_owner_when_zbynek_patched_back_to_dev1(self):
        """Regression lock: if LANE_HOST['zbynek'] were patched back to 'dev1',
        dev1 would return OWNER again."""
        import cli_webterm_profiles as profiles
        with mock.patch.dict(profiles.LANE_HOST,
                             {"zbynek": "dev1"}):
            result = profiles.profile_for_host("dev1")
        self.assertEqual(result, profiles.OWNER)


class TestWiring6Privileges(unittest.TestCase):
    """cli_privileges.PRIVILEGES entries for webterm keys + controller tunnel."""

    def test_webterm_zbynek_key_entry(self):
        import cli_privileges as p
        names = {priv.name for priv in p.PRIVILEGES}
        self.assertIn("webterm_zbynek_ed25519", names)

    def test_webterm_marek_key_entry(self):
        import cli_privileges as p
        names = {priv.name for priv in p.PRIVILEGES}
        self.assertIn("webterm_marek_ed25519", names)

    def test_webterm_dominika_key_entry(self):
        import cli_privileges as p
        names = {priv.name for priv in p.PRIVILEGES}
        self.assertIn("webterm_dominika_ed25519", names)

    def test_controller_tunnel_creds_entry(self):
        import cli_privileges as p
        entry = next(
            (priv for priv in p.PRIVILEGES
             if priv.name == "controller_tunnel_creds"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.local_path,
                         "~/.cloudflared/controller-webterm.json")


# --- helpers ----------------------------------------------------------------

def _make_minimal_spec(**overrides):
    """A minimal LaneSpec-like namespace for setup_service tests."""
    defaults = {
        "name": "test",
        "gateway_user": "testuser",
        "profile": "test",
        "bind": "127.0.0.1",
        "ttyd_port": 9999,
        "gateway_port": 9998,
        "gateway_sock_basename": "test-gw.sock",
        "ttyd_sock_basename": "test-ttyd.sock",
        "ttyd_service_name": "test-ttyd.service",
        "gateway_service_name": "test-gw.service",
        "tunnel_service_name": "test-tunnel.service",
        "log_prefix": "test",
        "go_live": "",
        "shared_tunnel": False,
        "label": "(test)",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_render_spec(**overrides):
    """A minimal LaneSpec-like namespace for render_gateway_unit tests."""
    defaults = {
        "profile": "owner",
        "bind": "127.0.0.1",
        "gateway_port": 8080,
        "ttyd_port": 7682,
        "gateway_sock_basename": "test-gw.sock",
        "ttyd_sock_basename": "test-ttyd.sock",
        "dash_index": Path("/tmp/test-dash/index.html"),
        "ttyd_service_name": "test-ttyd.service",
        "unit_note": "",
        "label": "(test)",
        "collector_mode": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


if __name__ == "__main__":
    unittest.main()
