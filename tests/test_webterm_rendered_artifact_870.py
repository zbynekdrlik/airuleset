"""#870 URGENT — lock the RENDERED inventory artifact for all 4 lanes.

The symmetry tests (test_webterm_f4d_symmetry.py) locked the FUNCTIONS
(profile_inventory, zbynek_inventory) but not the rendered JSON artifact
that ``write_artifacts`` actually writes.  The incident: ``write_artifacts``
calls ``webterm_inventory(profile=OWNER)`` which returned the LEGACY
fleet-derived inventory instead of ``zbynek_inventory()``.

These tests invoke the REAL ``_write_*_artifacts()`` with box-class patched
to ``"controller"`` and assert the written inventory JSON — the artefact the
gateway reads at connect time — matches the declarative ``*_inventory()``
leaf, with no identity=None on non-local entries.
"""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock as m

import cli_webterm as w
import cli_webterm_profiles as p


class TestZbynekRenderedInventory(unittest.TestCase):
    """The zbynek lane artifact must be zbynek_inventory(), never the fleet."""

    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        import cli_webterm_zbynek as zb
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_INVENTORY_PATH",
            claude / "webterm-zbynek-inventory.json"))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_DASH_DIR",
            claude / "webterm-zbynek-dash"))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_DASH_INDEX",
            claude / "webterm-zbynek-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_LAUNCH_PATH",
            claude / "airuleset-webterm-zbynek-ttyd.sh"))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_SERVICE_DEST",
            base / "systemd" / "webterm-zbynek-ttyd.service"))
        stack.enter_context(m.patch.object(
            zb, "WEBTERM_ZBYNEK_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-zbynek-gateway.service"))
        # Patch box class to controller so the sshpass guard does not fire
        # during fleet-derived inventory building (the bug path).
        stack.enter_context(m.patch(
            "watchdog.reaper.default_box_class", return_value="controller"))
        return claude

    def test_rendered_inventory_matches_zbynek_inventory(self):
        """The artifact write_artifacts produces must be exactly
        zbynek_inventory(), not the fleet-derived inventory."""
        import cli_webterm_zbynek as zb
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            zb._write_zbynek_artifacts()
            inv = json.loads(
                (claude / "webterm-zbynek-inventory.json")
                .read_text(encoding="utf-8"))
            expected = p.zbynek_inventory()
            self.assertEqual(inv, expected,
                             "rendered artifact != zbynek_inventory()")

    def test_no_identity_none_on_non_local(self):
        """Every non-local entry in the rendered artifact must carry an
        explicit identity — identity=None triggers the sshpass branch."""
        import cli_webterm_zbynek as zb
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            zb._write_zbynek_artifacts()
            inv = json.loads(
                (claude / "webterm-zbynek-inventory.json")
                .read_text(encoding="utf-8"))
            for entry in inv:
                if entry.get("local"):
                    continue
                self.assertIsNotNone(
                    entry.get("identity"),
                    "non-local entry %r has identity=None — would trigger "
                    "sshpass on controller" % entry["id"])

    def test_no_local_true_dev1(self):
        """The controller's local entry is 'ar', not 'dev1'."""
        import cli_webterm_zbynek as zb
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            zb._write_zbynek_artifacts()
            inv = json.loads(
                (claude / "webterm-zbynek-inventory.json")
                .read_text(encoding="utf-8"))
            for entry in inv:
                if entry["id"] == "dev1" and entry.get("local"):
                    self.fail("rendered artifact has local=True dev1 — "
                              "controller's local is 'ar'")

    def test_no_legacy_fleet_members(self):
        """No montalu7/8/stepan-forestshop (legacy fleet members not in
        zbynek_inventory)."""
        import cli_webterm_zbynek as zb
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            zb._write_zbynek_artifacts()
            inv = json.loads(
                (claude / "webterm-zbynek-inventory.json")
                .read_text(encoding="utf-8"))
            ids = {e["id"] for e in inv}
            for legacy_id in ("montalu7-subdev", "montalu8-subdev",
                              "stepan-forestshop", "forestshop"):
                self.assertNotIn(legacy_id, ids,
                                 "%s is a legacy fleet member, not in "
                                 "zbynek_inventory" % legacy_id)


class TestDavidRenderedInventory(unittest.TestCase):
    """The david lane artifact must be david_inventory()."""

    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        import cli_webterm_david as dv
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_INVENTORY_PATH",
            claude / "webterm-david-inventory.json"))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_DASH_DIR",
            claude / "webterm-david-dash"))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_DASH_INDEX",
            claude / "webterm-david-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_LAUNCH_PATH",
            claude / "airuleset-webterm-david-ttyd.sh"))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_SERVICE_DEST",
            base / "systemd" / "webterm-david-ttyd.service"))
        stack.enter_context(m.patch.object(
            dv, "WEBTERM_DAVID_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-david-gateway.service"))
        return claude

    def test_rendered_inventory_matches_david_inventory(self):
        import cli_webterm_david as dv
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            dv._write_david_artifacts()
            inv = json.loads(
                (claude / "webterm-david-inventory.json")
                .read_text(encoding="utf-8"))
            expected = p.david_inventory()
            self.assertEqual(inv, expected)


class TestMarekRenderedInventory(unittest.TestCase):
    """The marek lane artifact must be marek_inventory()."""

    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        import cli_webterm_marek as mk
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_INVENTORY_PATH",
            claude / "webterm-marek-inventory.json"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_DASH_DIR",
            claude / "webterm-marek-dash"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_DASH_INDEX",
            claude / "webterm-marek-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_LAUNCH_PATH",
            claude / "airuleset-webterm-marek-ttyd.sh"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_SERVICE_DEST",
            base / "systemd" / "webterm-marek-ttyd.service"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-marek-gateway.service"))
        return claude

    def test_rendered_inventory_matches_marek_inventory(self):
        import cli_webterm_marek as mk
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            mk._write_marek_artifacts()
            inv = json.loads(
                (claude / "webterm-marek-inventory.json")
                .read_text(encoding="utf-8"))
            expected = p.marek_inventory()
            self.assertEqual(inv, expected)


class TestDominikaRenderedInventory(unittest.TestCase):
    """The dominika lane artifact must be dominika_inventory()."""

    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        import cli_webterm_dominika as dn
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_INVENTORY_PATH",
            claude / "webterm-dominika-inventory.json"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_DASH_DIR",
            claude / "webterm-dominika-dash"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_DASH_INDEX",
            claude / "webterm-dominika-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_LAUNCH_PATH",
            claude / "airuleset-webterm-dominika-ttyd.sh"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_SERVICE_DEST",
            base / "systemd" / "webterm-dominika-ttyd.service"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-dominika-gateway.service"))
        return claude

    def test_rendered_inventory_matches_dominika_inventory(self):
        import cli_webterm_dominika as dn
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            dn._write_dominika_artifacts()
            inv = json.loads(
                (claude / "webterm-dominika-inventory.json")
                .read_text(encoding="utf-8"))
            expected = p.dominika_inventory()
            self.assertEqual(inv, expected)


if __name__ == "__main__":
    unittest.main()
