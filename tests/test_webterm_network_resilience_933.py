"""Tests for #933: webterm network resilience — WS drop tolerance, input
buffer, and connection-state indicator.

The dashboard template (``cli_webterm_dash_template.py``) must contain:
1. A connection monitor with grace period + exponential backoff.
2. A local input buffer that captures printable keys while disconnected
   and replays them on reconnect.
3. A subtle per-tab offline indicator (CSS class toggle, no text banner).

All three live in the JS/CSS of ``DASHBOARD_TEMPLATE``; the sentinel/subst
contract and the fit-layer invariants are untouched.
"""

from __future__ import annotations

import re
import unittest

from cli_webterm_dash_template import DASHBOARD_TEMPLATE


class TestConnectionMonitor933(unittest.TestCase):
    """The template must have a connection-monitor function with grace
    period and exponential backoff constants."""

    def test_monitor_connection_function_exists(self) -> None:
        self.assertIn("monitorConnection", DASHBOARD_TEMPLATE)

    def test_grace_period_constant(self) -> None:
        """A grace-period constant (ms) controls how long a WS drop is
        tolerated before declaring disconnected."""
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"WT_CONN_GRACE_MS\s*=\s*\d+",
        )

    def test_backoff_cap_constant(self) -> None:
        """An exponential-backoff cap (ms) limits the reconnect interval."""
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"WT_RECONNECT_MAX_MS\s*=\s*\d+",
        )

    def test_initial_reconnect_interval(self) -> None:
        """A starting reconnect interval (ms) for the exponential backoff."""
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"WT_RECONNECT_BASE_MS\s*=\s*\d+",
        )


class TestInputBuffer933(unittest.TestCase):
    """The template must capture printable keystrokes while disconnected
    and replay them on reconnect."""

    def test_input_buffer_property(self) -> None:
        """Each frame tracks its own input buffer."""
        self.assertIn("_wtInputBuf", DASHBOARD_TEMPLATE)

    def test_replay_on_reconnect(self) -> None:
        """The buffer is replayed via term.paste on reconnect."""
        # The replay function should contain term.paste with the buffer
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"term\.paste\(",
        )

    def test_printable_key_gate(self) -> None:
        """Only printable keys (key.length === 1) are buffered, not
        control sequences — the design comment's chosen semantics."""
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"\.key\.length\s*===?\s*1",
        )

    def test_enter_key_buffered(self) -> None:
        r"""Enter is buffered as \r (the PTY newline)."""
        # The buffer handler must check for the Enter key and append \r.
        # In the triple-quoted DASHBOARD_TEMPLATE, '\r' is a literal CR char.
        buf_section = DASHBOARD_TEMPLATE[DASHBOARD_TEMPLATE.index("_wtInputBuf"):]
        self.assertIn("'Enter'", buf_section)
        # The += '\r' line exists (literal CR in the Python string)
        self.assertIn("_wtInputBuf += '", buf_section)


class TestOfflineIndicator933(unittest.TestCase):
    """A subtle CSS indicator on the tab when the connection is down —
    no text banner, no overlay, per #671 'ziadne vysvetlivky'."""

    def test_offline_css_class(self) -> None:
        """A .tab.wt-offline CSS rule dims the tab's arrow."""
        self.assertIn(".tab.wt-offline", DASHBOARD_TEMPLATE)

    def test_no_text_banner(self) -> None:
        """No visible text element for the offline state — the indicator
        is purely a colour change on the existing .ico arrow, per #671."""
        # The offline CSS must NOT add any ::after/::before content
        # or a new visible span/div for the state text
        offline_section = ""
        for m in re.finditer(r"\.tab\.wt-offline[^}]*}", DASHBOARD_TEMPLATE):
            offline_section += m.group()
        self.assertNotIn("content:", offline_section)

    def test_toggle_uses_class_list(self) -> None:
        """The JS toggles 'wt-offline' via classList (same pattern as
        the existing .has-u toggle)."""
        self.assertIn("wt-offline", DASHBOARD_TEMPLATE)
        self.assertRegex(
            DASHBOARD_TEMPLATE,
            r"classList\.toggle\(\s*['\"]wt-offline['\"]",
        )


class TestFitLayerUntouched933(unittest.TestCase):
    """The network-resilience additions must NOT break any of the
    existing fit-layer functions or caps."""

    def test_fit_fixed_grid_present(self) -> None:
        self.assertIn("function fitFixedGrid", DASHBOARD_TEMPLATE)

    def test_fill_fixed_grid_present(self) -> None:
        self.assertIn("function fillFixedGrid", DASHBOARD_TEMPLATE)

    def test_stretch_frame_to_fill_present(self) -> None:
        self.assertIn("function stretchFrameToFill", DASHBOARD_TEMPLATE)

    def test_reconcile_frame_fit_present(self) -> None:
        self.assertIn("function reconcileFrameFit", DASHBOARD_TEMPLATE)

    def test_caps_unchanged(self) -> None:
        """The existing fill caps must be present with their documented
        values (unchanged by this ticket)."""
        self.assertIn("WT_FILL_MAX_CELL_STRETCH = 1.5", DASHBOARD_TEMPLATE)
        self.assertIn("WT_FILL_MAX_LINE_STRETCH = 1.8", DASHBOARD_TEMPLATE)
        self.assertIn("WT_FRAME_FILL_MAX_STRETCH = 1.25", DASHBOARD_TEMPLATE)
        self.assertIn("WT_FRAME_FILL_MIN_SHRINK = 0.5", DASHBOARD_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
