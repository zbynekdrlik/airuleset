"""#870 F4c step 1 — controller-hosted lane prerequisite gate tests.

On the controller, ALL hosted lanes run under the ONE ``airuleset`` account
(accepted B1 residue). The prerequisite gate must accept this: when the box-class
is ``"controller"`` AND the pwd user is ``"airuleset"`` AND the lane's human maps
to ``"controller"`` in ``LANE_HOST`` -> prereqs OK (given ttyd available).

TDD RED tests — the positive tests FAIL on the pre-fix code (prerequisites_ready
refuses because _whoami()=="airuleset" != spec.gateway_user=="dominika").
"""
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm_lane as lane  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402


def _make_spec(name="dominika", gateway_user="dominika"):
    """Minimal LaneSpec-like object for the prerequisite gate."""
    return SimpleNamespace(
        name=name,
        gateway_user=gateway_user,
        identity_key=None,
        log_prefix="test",
    )


class _ControllerPrereqBase(unittest.TestCase):
    """Base with hermetic HOME + ttyd mocking."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp
        # Ensure ttyd is "available" — put a fake ttyd in the temp home
        local_bin = Path(self._tmp) / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        ttyd = local_bin / "ttyd"
        ttyd.write_text("#!/bin/sh\n")
        ttyd.chmod(0o755)
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        else:
            os.environ.pop("HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestControllerPrereqAccepts(_ControllerPrereqBase):
    """POSITIVE: on the controller, airuleset user, LANE_HOST[human]==controller
    -> prerequisites_ready must return (True, "ready")."""

    def test_controller_airuleset_accepts_dominika(self):
        with (mock.patch("cli_filedrop_watchdog._whoami",
                         return_value="airuleset"),
              mock.patch("watchdog.reaper.default_box_class",
                         return_value="controller"),
              mock.patch("pwd.getpwuid",
                         return_value=SimpleNamespace(pw_name="airuleset")),
              mock.patch.dict(profiles.LANE_HOST,
                              {"dominika": "controller"})):
            ok, reason = lane.prerequisites_ready(
                _make_spec("dominika", "dominika"))
        self.assertTrue(ok,
                        "controller + airuleset + LANE_HOST==controller "
                        "should accept: %s" % reason)
        self.assertEqual(reason, "ready")

    def test_controller_airuleset_accepts_zbynek(self):
        with (mock.patch("cli_filedrop_watchdog._whoami",
                         return_value="airuleset"),
              mock.patch("watchdog.reaper.default_box_class",
                         return_value="controller"),
              mock.patch("pwd.getpwuid",
                         return_value=SimpleNamespace(pw_name="airuleset")),
              mock.patch.dict(profiles.LANE_HOST,
                              {"zbynek": "controller"})):
            ok, reason = lane.prerequisites_ready(
                _make_spec("zbynek", "zbynek"))
        self.assertTrue(ok,
                        "controller + airuleset + zbynek -> should accept: %s"
                        % reason)


class TestSubdevPrereqStillRefuses(_ControllerPrereqBase):
    """NEGATIVE LOCK: on a non-controller box with mismatched account, the gate
    still refuses — the fix must not weaken subdev behavior."""

    def test_non_controller_mismatched_account_refuses(self):
        """box-class is 'workstation' (non-controller), user is airuleset but
        gateway_user is dominika -> refused."""
        with mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="airuleset"):
            ok, reason = lane.prerequisites_ready(
                _make_spec("dominika", "dominika"))
        self.assertFalse(ok)
        self.assertIn("not the gateway account", reason)

    def test_subdev_mismatched_account_refuses(self):
        """On subdev (non-controller), david1 trying dominika lane -> refused."""
        with mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="david1"):
            ok, reason = lane.prerequisites_ready(
                _make_spec("dominika", "dominika"))
        self.assertFalse(ok)
        self.assertIn("not the gateway account", reason)


class TestControllerLaneHostSubdevRefuses(_ControllerPrereqBase):
    """NEGATIVE LOCK: on the controller as airuleset, but the lane's human maps
    to 'subdev' in LANE_HOST -> refused (the lane is NOT hosted here)."""

    def test_controller_but_lane_host_subdev_refuses(self):
        with (mock.patch("cli_filedrop_watchdog._whoami",
                         return_value="airuleset"),
              mock.patch("watchdog.reaper.default_box_class",
                         return_value="controller"),
              mock.patch("pwd.getpwuid",
                         return_value=SimpleNamespace(pw_name="airuleset")),
              mock.patch.dict(profiles.LANE_HOST, {"david": "subdev"})):
            ok, reason = lane.prerequisites_ready(
                _make_spec("david", "david1"))
        self.assertFalse(ok,
                         "LANE_HOST[david]==subdev on controller -> should "
                         "refuse, but got ok=True")

    def test_controller_wrong_pwd_user_refuses(self):
        """pwd user is NOT airuleset -> controller acceptance branch should
        not fire."""
        with (mock.patch("cli_filedrop_watchdog._whoami",
                         return_value="someoneelse"),
              mock.patch("watchdog.reaper.default_box_class",
                         return_value="controller"),
              mock.patch("pwd.getpwuid",
                         return_value=SimpleNamespace(pw_name="someoneelse")),
              mock.patch.dict(profiles.LANE_HOST,
                              {"dominika": "controller"})):
            ok, reason = lane.prerequisites_ready(
                _make_spec("dominika", "dominika"))
        self.assertFalse(ok,
                         "pwd user != airuleset on controller -> should refuse")


class TestSubdevMatchingAccountAccepts(_ControllerPrereqBase):
    """EXISTING BEHAVIOR: on subdev, matching account + ttyd -> accepts."""

    def test_subdev_matching_account_accepts(self):
        with mock.patch("cli_filedrop_watchdog._whoami",
                        return_value="dominika"):
            ok, reason = lane.prerequisites_ready(
                _make_spec("dominika", "dominika"))
        self.assertTrue(ok,
                        "matching account on subdev should accept: %s" % reason)


if __name__ == "__main__":
    unittest.main()
