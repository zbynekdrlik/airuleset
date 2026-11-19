"""#1362: keep file-drop tests hermetic when the suite runs ON the controller.

On the controller machine `filedrop.bind_ips()` returns only loopback and
`--private` refuses (`filedrop.is_controller_box`). Tests that exercise the
interface enumeration or the `--private` path pin "not the controller" with
`off_controller()`: it sets the `FILEDROP_NODENAME` seam in os.environ, so the
CLI subprocesses those tests start inherit it too. The controller branch has
its own tests (test_controller_loopback_drop_1362.py).
"""
import os
from unittest import mock

OFF_CONTROLLER_NODENAME = "test-not-the-controller"


def off_controller():
    """A patcher (start/stop or `with`) that makes this box NOT the controller."""
    return mock.patch.dict(os.environ, {"FILEDROP_NODENAME": OFF_CONTROLLER_NODENAME})


_MODULE_PATCH = off_controller()


def setUpModule():
    """Import into a test module to pin "not the controller" for the module."""
    _MODULE_PATCH.start()


def tearDownModule():
    _MODULE_PATCH.stop()
