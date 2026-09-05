"""Tests for the consolidated per-developer webterm lane provisioner (#665).

The rule-of-three (owner has david + marek + a future 4th developer) reached, so
the ~90%-shared render + setup skeleton is extracted into ONE parameterized engine
(`cli_webterm_lane`) driven by a per-lane `LaneSpec`. These lock the consolidation:

  * the shared engine module exposes the single provisioner implementation;
  * david AND marek build a `LaneSpec` and render THROUGH the engine (a patched
    engine function is observed by BOTH wrappers -> no duplicate render/setup body);
  * both lanes' honesty-bar unit-note comes from the ONE shared note renderer;
  * the two rendered systemd units are byte-identical AFTER the per-user params
    (socket basenames, ports, dash/service paths, hostname, lane name, label) are
    normalised away — i.e. "identical structure, only per-user params differ", the
    ticket's exact goal.

Behaviour (the rendered unit bytes, launcher, inventory, tunnel config) is preserved
for the two LIVE subdev services — the existing per-lane test files
(test_webterm_david / test_webterm_marek / test_webterm_tunnel /
test_webterm_loopback_hardening_663 / test_webterm_u_status) keep passing against
the thin wrappers, so this file only adds the consolidation-shape locks.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm_lane as lane      # noqa: E402
import cli_webterm_david as d        # noqa: E402
# #882: marek webterm module deleted
import cli_webterm_dominika as dn    # noqa: E402  (#867 — the FOURTH lane)


class TestLaneEngineExposesTheSingleProvisioner(unittest.TestCase):
    def test_engine_symbols_present(self):
        for name in ("LaneSpec", "TTYD_PATH_ENV", "render_lane_unit_note",
                     "render_ttyd_unit", "render_gateway_unit", "write_artifacts",
                     "prerequisites_ready", "setup_tunnel", "setup_service",
                     "retire_credential"):
            self.assertTrue(hasattr(lane, name),
                            "cli_webterm_lane must expose %r" % name)


class TestBothLanesGoThroughTheEngine(unittest.TestCase):
    # #867: the rule-of-three is now a rule-of-FOUR (owner + david + marek +
    # dominika) — every NON-owner lane (david/marek/dominika) is a thin façade over
    # the ONE engine, so a new human lane stays a config entry, never a copy.
    def test_all_lane_specs_are_lanespecs(self):
        self.assertIsInstance(d._spec(), lane.LaneSpec)
        # #882: marek webterm module deleted
        self.assertIsInstance(dn._spec(), lane.LaneSpec)

    def test_gateway_render_delegates_to_the_one_engine(self):
        # If any lane kept its OWN render body, patching the engine would not be
        # observed by it — the whole point of the consolidation.
        sentinel = "SENTINEL-GATEWAY-UNIT\n"
        with m.patch.object(lane, "render_gateway_unit", return_value=sentinel):
            self.assertEqual(d.render_david_gateway_unit(), sentinel)
            # #882: marek webterm module deleted
            self.assertEqual(dn.render_dominika_gateway_unit(), sentinel)

    def test_ttyd_render_delegates_to_the_one_engine(self):
        sentinel = "SENTINEL-TTYD-UNIT\n"
        with m.patch.object(lane, "render_ttyd_unit", return_value=sentinel):
            self.assertEqual(d.render_david_ttyd_unit(), sentinel)
            # #882: marek webterm module deleted
            self.assertEqual(dn.render_dominika_ttyd_unit(), sentinel)

    def test_unit_note_comes_from_the_one_shared_renderer(self):
        # The _*_UNIT_NOTE near-copies (the ticket's named drift site) collapse to
        # ONE renderer every lane calls at spec-build time.
        sentinel = "# SENTINEL SHARED NOTE\n"
        with m.patch.object(lane, "render_lane_unit_note", return_value=sentinel):
            self.assertEqual(d._spec().unit_note, sentinel)
            # #882: marek webterm module deleted
            self.assertEqual(dn._spec().unit_note, sentinel)


class TestLaneSkeletonIsIdenticalModuloParams(unittest.TestCase):
    """The rule-of-three goal, mechanically: strip the human-facing NOTE + template
    header comment, normalise every per-user param, and the two rendered units are
    byte-identical — so a 4th developer is a config entry, not a copy."""

    def _body(self, unit):
        lines = unit.splitlines()
        i = next(idx for idx, ln in enumerate(lines) if ln.startswith("["))
        return "\n".join(lines[i:])

    def _norm(self, text, spec):
        subs = [
            (spec.gateway_sock_basename, "@GW_SOCK@"),
            (spec.ttyd_sock_basename, "@TTYD_SOCK@"),
            (str(spec.dash_index), "@DASH_INDEX@"),
            (str(spec.launch_path), "@LAUNCH@"),
            (spec.ttyd_service_name, "@TTYD_SVC@"),
            (spec.gateway_service_name, "@GW_SVC@"),
            (spec.tunnel_hostname, "@HOST@"),
            (str(spec.gateway_port), "@GWPORT@"),
            (str(spec.ttyd_port), "@TTYDPORT@"),
            (spec.label, "@LABEL@"),
            (spec.name, "@LANE@"),
        ]
        # longest token first, so a short token (the lane name) never eats a
        # substring of a longer one (a socket basename / service name).
        for tok, ph in sorted(subs, key=lambda kv: len(kv[0]), reverse=True):
            text = text.replace(tok, ph)
        return text

    def test_gateway_unit_skeleton_identical(self):
        # #882: marek webterm module deleted — compare david vs dominika only.
        ds, ns = d._spec(), dn._spec()
        db = self._norm(self._body(d.render_david_gateway_unit()), ds)
        nb = self._norm(self._body(dn.render_dominika_gateway_unit()), ns)
        self.assertEqual(db, nb)

    def test_ttyd_unit_skeleton_identical(self):
        # #882: marek webterm module deleted — compare david vs dominika only.
        ds, ns = d._spec(), dn._spec()
        db = self._norm(self._body(d.render_david_ttyd_unit()), ds)
        nb = self._norm(self._body(dn.render_dominika_ttyd_unit()), ns)
        self.assertEqual(db, nb)


if __name__ == "__main__":
    unittest.main()
