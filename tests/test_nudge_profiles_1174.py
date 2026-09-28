"""#1174 — declared machine-nudge PROFILES per box type (owner decision 28.9.2026).

Each box type runs a DECLARED nudge profile (versioned fleet data,
`cli_fleet.NUDGE_PROFILES`) instead of hand-drifted toggles; the footer shows the
profile name (`nudges stream`), never the old `N/M` count, and a runtime override
(`nudges on|off --kind`) is KEPT and shown as a deviation (`+1` / `-2`), never
silently reset.

Locks:
- the profile table equals the LIVE per-box sets the owner read on 28.9.2026
  (so the rollout changes no behaviour);
- every `REMOTE_HOSTS` entry (and the controller) resolves a declared profile;
- `apply` writes the profile into the SAME store `nudges on|off` writes, keeps a
  runtime deviation and reports it; a later profile CHANGE applies only its delta;
- the footer renders `nudges <profile>` + deviation; `nudges status` prints the
  `profile:` line.
"""
import io
import json
import os
import re
import sys
import unittest
import unittest.mock as m
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_fleet  # noqa: E402
import cli_nudge_profiles as np  # noqa: E402
import statusbar  # noqa: E402
import watchdog as wd  # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The LIVE sets read on 28.9.2026 (ticket #1174 body, verbatim lists).
LIVE_GK = {"goal-guard", "infra-priority", "lane-occupancy", "lane-reconcile",
           "partition-audit", "queue-arrival", "release-gap", "task-hygiene",
           "u-freshness", "watch-trigger"}
LIVE_STREAM = {"bounce", "goal-guard", "lane-occupancy", "lane-reconcile",
               "partition-audit", "queue-arrival", "release-gap", "task-hygiene",
               "u-freshness"}
LIVE_CONTROLLER = {"queue-arrival"}
LIVE_WORKSTATION = set()


def _plain(seg):
    return _ANSI.sub("", seg)


def _state(home):
    with open(os.path.join(home, ".claude", "nudges-kinds.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def _write_state(home, payload):
    d = os.path.join(home, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "nudges-kinds.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class TestProfileTableLock(unittest.TestCase):
    """The declared table is TODAY's live sets — the rollout changes nothing."""

    def test_profiles_equal_the_live_sets(self):
        self.assertEqual(set(cli_fleet.NUDGE_PROFILES["gk"]), LIVE_GK)
        self.assertEqual(set(cli_fleet.NUDGE_PROFILES["stream"]), LIVE_STREAM)
        self.assertEqual(set(cli_fleet.NUDGE_PROFILES["controller"]),
                         LIVE_CONTROLLER)
        self.assertEqual(set(cli_fleet.NUDGE_PROFILES["workstation"]),
                         LIVE_WORKSTATION)

    def test_exactly_the_declared_profiles(self):
        self.assertEqual(set(cli_fleet.NUDGE_PROFILES),
                         {"gk", "stream", "controller", "workstation"})

    def test_every_profile_kind_is_a_machine_kind(self):
        for name, kinds in cli_fleet.NUDGE_PROFILES.items():
            self.assertTrue(set(kinds) <= wd.MACHINE_NUDGE_KINDS,
                            "%s: unknown kinds %s"
                            % (name, set(kinds) - wd.MACHINE_NUDGE_KINDS))


class TestResolution(unittest.TestCase):
    def test_every_remote_host_resolves_a_declared_profile(self):
        for entry in cli_fleet.REMOTE_HOSTS:
            name = np.resolve_profile(entry.get("user"), entry)
            self.assertIn(name, cli_fleet.NUDGE_PROFILES,
                          "%s resolved to undeclared profile %r"
                          % (entry.get("name"), name))

    def test_box_types(self):
        self.assertEqual(np.resolve_profile("gatekeeper"), "gk")
        self.assertEqual(np.resolve_profile("airuleset"), "controller")
        for stream in ("montalu1", "montalu4", "montalu6", "david1", "miva1"):
            self.assertEqual(np.resolve_profile(stream), "stream", stream)
        self.assertEqual(np.resolve_profile("newlevel"), "workstation")
        # webterm observers run no Claude stream of their own
        for obs in cli_fleet.WEBTERM_OBSERVER_USERS:
            self.assertEqual(np.resolve_profile(obs), "workstation", obs)

    def test_explicit_entry_declaration_wins(self):
        self.assertEqual(
            np.resolve_profile("montalu1", {"nudge_profile": "workstation"}),
            "workstation")

    def test_box_profile_uses_self_entry(self):
        self.assertEqual(np.box_profile(user="gatekeeper"), "gk")
        self.assertEqual(np.box_profile(user="newlevel", hostname="dev2"),
                         "workstation")


class TestApply(unittest.TestCase):
    def test_first_adoption_matching_live_set_records_profile_no_change(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_STREAM), "since": "S",
                                "by": "montalu1"})
            r = np.apply_profile(home=home, user="montalu1")
            st = _state(home)
            self.assertEqual(st["profile"], "stream")
            self.assertEqual(set(st["on"]), LIVE_STREAM)
            self.assertEqual(st["since"], "S")      # live set untouched
            self.assertEqual((r["plus"], r["minus"]), ([], []))

    def test_first_adoption_never_changes_a_differing_live_set(self):
        # A box whose live set differs from its profile is KEPT and REPORTED,
        # never silently reset to the profile (the no-behaviour-change rollout).
        with TemporaryDirectory() as home:
            live = (LIVE_STREAM - {"bounce"}) | {"card"}
            _write_state(home, {"on": sorted(live)})
            r = np.apply_profile(home=home, user="montalu1")
            self.assertEqual(set(_state(home)["on"]), live)
            self.assertEqual(r["plus"], ["card"])
            self.assertEqual(r["minus"], ["bounce"])

    def test_first_adoption_absent_file_turns_nothing_on(self):
        with TemporaryDirectory() as home:
            r = np.apply_profile(home=home, user="montalu1")
            self.assertEqual(wd.nudges_on_kinds(home), set())
            self.assertEqual(_state(home)["profile"], "stream")
            self.assertEqual(len(r["minus"]), len(LIVE_STREAM))

    def test_profile_change_applies_only_its_delta_and_keeps_overrides(self):
        with TemporaryDirectory() as home:
            old_profile = sorted(LIVE_STREAM - {"release-gap"})
            # runtime overrides vs the OLD profile: +card, -bounce
            live = (set(old_profile) - {"bounce"}) | {"card"}
            _write_state(home, {"on": sorted(live), "profile": "stream",
                                "profile_kinds": old_profile})
            r = np.apply_profile(home=home, user="montalu1")
            on = set(_state(home)["on"])
            self.assertIn("release-gap", on)      # added by the profile change
            self.assertIn("card", on)             # runtime +override kept
            self.assertNotIn("bounce", on)        # runtime -override kept
            self.assertEqual(r["added"], ["release-gap"])
            self.assertEqual((r["plus"], r["minus"]), (["card"], ["bounce"]))
            self.assertEqual(set(_state(home)["profile_kinds"]), LIVE_STREAM)

    def test_profile_change_turns_off_a_dropped_kind(self):
        with TemporaryDirectory() as home:
            old_profile = sorted(LIVE_STREAM | {"card"})
            _write_state(home, {"on": old_profile, "profile": "stream",
                                "profile_kinds": old_profile})
            r = np.apply_profile(home=home, user="montalu1")
            self.assertNotIn("card", set(_state(home)["on"]))
            self.assertEqual(r["removed"], ["card"])

    def test_apply_is_idempotent(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_GK)})
            np.apply_profile(home=home, user="gatekeeper")
            r = np.apply_profile(home=home, user="gatekeeper")
            self.assertFalse(r["written"])

    def test_runtime_toggle_preserves_profile_keys(self):
        # `nudges on|off --kind` writes the SAME store; it must not drop the
        # recorded profile (else the next apply would re-adopt from scratch).
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_STREAM)})
            np.apply_profile(home=home, user="montalu1")
            wd.set_nudge_kind("card", True, home=home, by="owner")
            st = _state(home)
            self.assertEqual(st["profile"], "stream")
            self.assertEqual(set(st["profile_kinds"]), LIVE_STREAM)
            self.assertIn("card", st["on"])

    def test_reset_realigns_to_the_profile(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": ["card"]})
            np.apply_profile(home=home, user="montalu1")
            r = np.reset_to_profile(home=home, user="montalu1", by="owner")
            self.assertEqual(wd.nudges_on_kinds(home), LIVE_STREAM)
            self.assertEqual((r["plus"], r["minus"]), ([], []))

    def test_install_step_prints_and_never_raises(self):
        with TemporaryDirectory() as home:
            buf = io.StringIO()
            with redirect_stdout(buf), \
                    m.patch.object(np, "apply_profile",
                                   side_effect=RuntimeError("boom")):
                self.assertIsNone(np.install_step(home=home))
            buf = io.StringIO()
            with redirect_stdout(buf), \
                    m.patch.object(np, "_current_user", return_value="miva1"):
                np.install_step(home=home)
            self.assertIn("Nudge profile: stream", buf.getvalue())

    def test_cmd_install_runs_the_apply_step(self):
        import ast
        import inspect
        src = inspect.getsource(airuleset.cmd_install)
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else
                 getattr(n.func, "id", None)
                 for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
        self.assertIn("_nudge_profile_install_step", calls)


class TestFooter(unittest.TestCase):
    def _seg(self, home):
        return _plain(statusbar.nudges_off_segment(home=home))

    def test_profile_without_deviation(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_STREAM)})
            np.apply_profile(home=home, user="montalu1")
            self.assertEqual(self._seg(home), "nudges stream · recovery on")

    def test_profile_plus_deviation(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_STREAM | {"card"})})
            np.apply_profile(home=home, user="montalu1")
            self.assertEqual(self._seg(home), "nudges stream +1 · recovery on")

    def test_profile_minus_deviation(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_GK - {"bounce", "goal-guard",
                                                        "watch-trigger"})})
            np.apply_profile(home=home, user="gatekeeper")
            self.assertEqual(self._seg(home), "nudges gk -2 · recovery on")

    def test_both_deviations(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": ["card"]})
            np.apply_profile(home=home, user="airuleset")
            self.assertEqual(self._seg(home),
                             "nudges controller +1 -1 · recovery on")

    def test_no_count_fraction_once_a_profile_is_recorded(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_GK)})
            np.apply_profile(home=home, user="gatekeeper")
            self.assertNotRegex(self._seg(home), r"\d+/\d+")


class TestStatus(unittest.TestCase):
    def _status(self, home, user):
        args = m.Mock(nudges_action="status", kind=None, all=False, fleet=False)
        buf = io.StringIO()
        with m.patch("os.path.expanduser",
                     side_effect=lambda p: p.replace("~", home, 1)), \
                m.patch.object(np, "_current_user", return_value=user), \
                redirect_stdout(buf):
            airuleset.cmd_nudges(args)
        return buf.getvalue()

    def test_status_prints_profile_line(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted(LIVE_STREAM)})
            np.apply_profile(home=home, user="montalu1")
            out = self._status(home, "montalu1")
            self.assertIn("profile: stream (9 kinds)", out)
            self.assertIn("deviation: none", out)
            self.assertIn("nudges: ON", out)     # fleet parser shape kept

    def test_status_lists_deviations(self):
        with TemporaryDirectory() as home:
            _write_state(home, {"on": sorted((LIVE_STREAM - {"bounce"})
                                             | {"card"})})
            np.apply_profile(home=home, user="montalu1")
            out = self._status(home, "montalu1")
            self.assertIn("+card", out)
            self.assertIn("-bounce", out)

    def test_status_before_first_apply_names_the_declared_profile(self):
        with TemporaryDirectory() as home:
            out = self._status(home, "gatekeeper")
            self.assertIn("profile: gk (10 kinds)", out)


class TestVocabulary(unittest.TestCase):
    def test_footer_bullet_documents_the_profile(self):
        vocab = (REPO / "modules" / "core" / "statusline-vocabulary.md"
                 ).read_text(encoding="utf-8").splitlines()
        bullets = [ln for ln in vocab if ln.startswith("- `nudges <profile>")]
        self.assertEqual(len(bullets), 1)
        self.assertIn("recovery on", bullets[0])
        self.assertNotIn("N/M", "\n".join(vocab))


if __name__ == "__main__":
    unittest.main()
