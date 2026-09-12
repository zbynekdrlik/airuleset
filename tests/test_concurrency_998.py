"""#998 — concurrency mode/role resolution + the fleet `windows` declaration.

`cli_fleet.managed_windows`/`box_windows`/`validate_windows` expose the
DECLARED managed tmux windows; `cli_concurrency.resolve_concurrency` is the
single (mode, role, source) resolver every consumer (footer, quals, goal
renderer, dispatch hook, lane caps) reads. Three-source chain: declared window
(name or cwd) -> project lane-resources.json `mode` -> default parallel.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_fleet  # noqa: E402
import cli_concurrency as cc  # noqa: E402

GK_WINDOWS = [
    {"name": "gk", "cwd": "~/devel/odoo/odoo-erp", "role": "review",
     "mode": "parallel"},
    {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra", "role": "infra",
     "mode": "sequential"},
]


class TestFleetWindows(TestCase):
    def _gk(self):
        return [h for h in cli_fleet.REMOTE_HOSTS
                if h["name"] == "gatekeeper"][0]

    def _dev2(self):
        return [h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "dev2"][0]

    def test_gk_declares_review_and_infra_windows(self):
        w = cli_fleet.managed_windows(self._gk())
        self.assertEqual([x["name"] for x in w], ["gk", "gk-infra"])
        infra = [x for x in w if x["name"] == "gk-infra"][0]
        self.assertEqual(infra["role"], "infra")
        self.assertEqual(infra["mode"], "sequential")

    def test_default_target_declares_no_windows(self):
        # dev2 (and every non-gk target) is byte-identical to today.
        self.assertEqual(cli_fleet.managed_windows(self._dev2()), [])

    def test_gk_declaration_validates(self):
        self.assertEqual(cli_fleet.validate_windows(GK_WINDOWS), [])
        self.assertEqual(
            cli_fleet.validate_windows(cli_fleet.managed_windows(self._gk())),
            [])

    def test_validate_rejects_bad_shapes(self):
        errs = cli_fleet.validate_windows(
            [{"name": "a b", "cwd": "/abs", "role": "boss", "mode": "q"}])
        self.assertEqual(len(errs), 4)
        errs2 = cli_fleet.validate_windows(
            [{"name": "ok", "cwd": "../esc", "role": None, "mode": None}])
        self.assertTrue(any(".." in e for e in errs2))

    def test_validate_rejects_duplicate_names(self):
        errs = cli_fleet.validate_windows(
            [{"name": "x", "cwd": "a", "role": None, "mode": None},
             {"name": "x", "cwd": "b", "role": None, "mode": None}])
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_box_windows_scopes_to_the_user(self):
        self.assertEqual(
            [w["name"] for w in cli_fleet.box_windows("gatekeeper")],
            ["gk", "gk-infra"])
        # a non-declaring account gets nothing — never mis-inherits gk's.
        self.assertEqual(cli_fleet.box_windows("newlevel"), [])
        self.assertEqual(cli_fleet.box_windows(""), [])


class TestResolveConcurrency(TestCase):
    HOME = "/home/gatekeeper"

    def test_declared_review_window_by_cwd(self):
        mode, role, src = cc.resolve_concurrency(
            self.HOME + "/devel/odoo/odoo-erp", windows=GK_WINDOWS,
            home=self.HOME)
        self.assertEqual((mode, role, src), ("parallel", "review", "role"))

    def test_declared_infra_window_by_cwd_is_sequential(self):
        mode, role, src = cc.resolve_concurrency(
            self.HOME + "/devel/odoo/odoo-erp-infra", windows=GK_WINDOWS,
            home=self.HOME)
        self.assertEqual((mode, role, src), ("sequential", "infra", "role"))

    def test_declared_window_by_name_wins(self):
        mode, role, src = cc.resolve_concurrency(
            "/anything", window_name="gk-infra", windows=GK_WINDOWS,
            home=self.HOME)
        self.assertEqual((mode, role, src), ("sequential", "infra", "role"))

    def test_cwd_wins_over_a_conflicting_window_name(self):
        # #998 addendum (owner 2026-09-12, "prečo mám dva gk"): a window
        # mis-named `gk` sitting in the infra cwd must resolve to role infra
        # (CWD wins), never review by name — else the install's rename-all
        # (which clobbered the infra window to `gk`) would make it dispatch as
        # the parallel review lane.
        mode, role, src = cc.resolve_concurrency(
            self.HOME + "/devel/odoo/odoo-erp-infra", window_name="gk",
            windows=GK_WINDOWS, home=self.HOME)
        self.assertEqual((mode, role, src), ("sequential", "infra", "role"))

    def test_subdir_of_a_declared_cwd_resolves_to_that_window(self):
        # Containment: a pane cd'd into a subdirectory of a declared window's
        # cwd (routine odoo-erp-infra work) still resolves to that window's
        # role — the exact-match-only resolver missed this, so a push while the
        # owner was deep in a subdir would re-clobber the name.
        mode, role, src = cc.resolve_concurrency(
            self.HOME + "/devel/odoo/odoo-erp-infra/addons/x",
            windows=GK_WINDOWS, home=self.HOME)
        self.assertEqual((mode, role, src), ("sequential", "infra", "role"))

    def test_project_mode_when_no_window_matches(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "lane-resources.json").write_text(
                json.dumps({"mode": "sequential"}))
            mode, role, src = cc.resolve_concurrency(d, windows=[],
                                                     home=self.HOME)
            self.assertEqual((mode, role, src), ("sequential", None, "project"))

    def test_project_mode_merges_with_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "lane-resources.json").write_text(
                json.dumps({"max_lanes": 5, "mode": "sequential"}))
            self.assertEqual(cc.read_project_mode(d), "sequential")

    def test_default_parallel_when_nothing_declared(self):
        with tempfile.TemporaryDirectory() as d:
            mode, role, src = cc.resolve_concurrency(d, windows=[],
                                                     home=self.HOME)
            self.assertEqual((mode, role, src), ("parallel", None, "default"))

    def test_malformed_project_file_falls_to_default(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "lane-resources.json").write_text("{not json")
            self.assertIsNone(cc.read_project_mode(d))
            self.assertEqual(cc.resolve_mode(d, windows=[]), "parallel")

    def test_unknown_project_mode_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "lane-resources.json").write_text(
                json.dumps({"mode": "turbo"}))
            self.assertIsNone(cc.read_project_mode(d))

    def test_status_row_names_source_and_role(self):
        row = cc.concurrency_status_row(
            self.HOME + "/devel/odoo/odoo-erp-infra", windows=GK_WINDOWS,
            home=self.HOME)
        self.assertIn("concurrency: sequential", row)
        self.assertIn("source: role", row)
        self.assertIn("role=infra", row)

    def test_status_row_default(self):
        with tempfile.TemporaryDirectory() as d:
            row = cc.concurrency_status_row(d, windows=[], home=self.HOME)
            self.assertIn("concurrency: parallel (source: default)", row)


class TestSequentialCaps(TestCase):
    """#998 item 2 — sequential mode forces the lane total to 1; parallel is
    byte-identical to the pre-#998 file-only cap."""

    def _write(self, d, obj):
        claude = Path(d) / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "lane-resources.json").write_text(json.dumps(obj))

    def test_sequential_project_forces_total_1(self):
        from watchdog import lane_resources as lr
        with tempfile.TemporaryDirectory() as d:
            self._write(d, {"mode": "sequential"})
            caps, reason = lr.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 1})
            self.assertEqual(reason, "sequential-mode")

    def test_sequential_overrides_max_lanes(self):
        from watchdog import lane_resources as lr
        with tempfile.TemporaryDirectory() as d:
            self._write(d, {"max_lanes": 5, "mode": "sequential"})
            caps, _ = lr.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 1})

    def test_parallel_is_byte_identical_to_file_caps(self):
        from watchdog import lane_resources as lr
        with tempfile.TemporaryDirectory() as d:
            self._write(d, {"max_lanes": 3})
            self.assertEqual(lr.lane_resource_caps(d), lr._file_caps(d))
            self.assertEqual(lr.lane_resource_caps(d)[0], {"total": 3})

    def test_no_file_default_parallel_unchanged(self):
        from watchdog import lane_resources as lr
        with tempfile.TemporaryDirectory() as d:
            caps, reason = lr.lane_resource_caps(d)
            self.assertEqual(caps, {"total": lr.GOAL_LANE_SATURATION_WORKERS})
            self.assertIsNone(reason)


class TestDispatchGateLine(TestCase):
    """#998 item 2 — the block-dispatch-over-wdrain.sh sequential verdict."""

    def _seq_dir(self):
        d = tempfile.mkdtemp()
        claude = Path(d) / ".claude"
        claude.mkdir()
        (claude / "lane-resources.json").write_text(json.dumps({"mode": "sequential"}))
        return d

    def test_parallel_always_allows(self):
        with tempfile.TemporaryDirectory() as d:
            line = cc.dispatch_gate_line(d, live_count=99)
            self.assertTrue(line.startswith("allow|"))

    def test_sequential_with_zero_live_allows_first(self):
        d = self._seq_dir()
        self.assertEqual(cc.dispatch_gate_line(d, live_count=0),
                         "allow|sequential|0")

    def test_sequential_with_one_live_blocks_second(self):
        d = self._seq_dir()
        self.assertEqual(cc.dispatch_gate_line(d, live_count=1),
                         "block|sequential|1")

    def test_sequential_with_many_live_blocks(self):
        d = self._seq_dir()
        self.assertTrue(cc.dispatch_gate_line(d, live_count=3).startswith("block|"))


if __name__ == "__main__":
    main()
