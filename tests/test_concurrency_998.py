"""#998 — concurrency mode/role resolution + the fleet `windows` declaration.

`cli_fleet.managed_windows`/`box_windows`/`validate_windows` expose the
DECLARED managed tmux windows; `cli_concurrency.resolve_concurrency` is the
single (mode, role, source) resolver every consumer (footer, quals, goal
renderer, dispatch hook, lane caps) reads. Three-source chain: declared window
(name or cwd) -> project lane-resources.json `mode` -> default parallel.
"""
import json
import os
import sys
import tempfile
import unittest.mock as m
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
    # #1074 — the third declared window (gk-quality); the full #1074 role/
    # resolver behaviour is locked in test_gk_quality_role_1074.py.
    {"name": "gk-quality", "cwd": "~/devel/odoo/odoo-erp-quality",
     "role": "quality", "mode": "sequential"},
]


class TestFleetWindows(TestCase):
    def _gk(self):
        return [h for h in cli_fleet.REMOTE_HOSTS
                if h["name"] == "gatekeeper"][0]

    def _dev2(self):
        return [h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "dev2"][0]

    def test_gk_declares_review_and_infra_windows(self):
        w = cli_fleet.managed_windows(self._gk())
        self.assertEqual([x["name"] for x in w], ["gk", "gk-infra", "gk-quality"])
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
            ["gk", "gk-infra", "gk-quality"])
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


class TestDavid3Sequential1031(TestCase):
    """#1031 — david3@subdev (window d3) declared SEQUENTIAL in the fleet, the
    SAME #998 declared-window mechanism as gk-infra. A stream window is neither
    the gk `review` nor `infra` kind, so its role is None (which
    `validate_windows` accepts). Every consumer reads the ONE cwd-first
    resolver, so the declaration alone flips d3 to sequential — no consumer
    code change. These lock the REAL fleet declaration (via
    `cli_fleet.box_windows`), not a hand-copied literal."""

    HOME = "/home/david3"
    D3_CWD = "/home/david3/devel/odoo/odoo-erp"

    def _windows(self):
        return cli_fleet.box_windows("david3")

    # (1) d3 resolves ("sequential", None, "role") via the declared window.
    def test_d3_resolves_sequential_via_declared_window(self):
        w = self._windows()
        self.assertNotEqual(w, [], "david3@subdev must declare a window (#1031)")
        self.assertEqual(
            cc.resolve_concurrency(self.D3_CWD, windows=w, home=self.HOME),
            ("sequential", None, "role"))

    def test_d3_window_shape(self):
        w = self._windows()
        self.assertEqual([x["name"] for x in w], ["d3"])
        self.assertEqual(w[0]["cwd"], "~/devel/odoo/odoo-erp")
        self.assertIsNone(w[0]["role"])
        self.assertEqual(w[0]["mode"], "sequential")

    def test_d3_subdir_worktree_lane_resolves_sequential(self):
        # a worktree lane under d3's cwd (the autopilot-worker isolation dir)
        # still resolves to sequential by containment.
        w = self._windows()
        mode, role, _ = cc.resolve_concurrency(
            self.D3_CWD + "/.claude/worktrees/agent-x", windows=w,
            home=self.HOME)
        self.assertEqual((mode, role), ("sequential", None))

    # (4) the new declaration is shape-valid (role=None accepted).
    def test_d3_declaration_validates(self):
        self.assertEqual(cli_fleet.validate_windows(self._windows()), [])

    # (2) regression — the change is scoped to the david3 account ONLY.
    def test_gk_review_window_still_parallel(self):
        gk = cli_fleet.box_windows("gatekeeper")
        self.assertEqual(
            cc.resolve_concurrency("/home/gatekeeper/devel/odoo/odoo-erp",
                                   windows=gk, home="/home/gatekeeper"),
            ("parallel", "review", "role"))

    def test_sibling_streams_declared_sequential_20260921(self):
        # Owner directive 2026-09-21 („prepni david1 až david4 aby nemali multi
        # subagent mod ale aby išli sekvenčne … to isté aj miva1") reverses the
        # #1031 "david3 ONLY" scoping: david1/2/4 and miva1 now declare the SAME
        # single sequential window shape as d3 (window name = the box alias).
        for u, alias in (("david1", "d1"), ("david2", "d2"), ("david4", "d4"),
                         ("miva1", "miva")):
            w = cli_fleet.box_windows(u)
            self.assertEqual([x["name"] for x in w], [alias],
                             "%s must declare exactly its own window" % u)
            self.assertEqual(w[0]["cwd"], "~/devel/odoo/odoo-erp")
            self.assertIsNone(w[0]["role"])
            self.assertEqual(w[0]["mode"], "sequential")
            self.assertEqual(cli_fleet.validate_windows(w), [])
            self.assertEqual(
                cc.resolve_concurrency("/home/%s/devel/odoo/odoo-erp" % u,
                                       windows=w, home="/home/%s" % u),
                ("sequential", None, "role"))

    def test_montalu_streams_still_default_parallel(self):
        # the flip is scoped to the david family + miva1; montalu* keep the
        # parallel default (owner 2026-09-21: gk + montalu1 run full throttle).
        for u in ("montalu1", "montalu2", "montalu5"):
            self.assertEqual(cli_fleet.box_windows(u), [],
                             "%s must NOT declare a window" % u)
            self.assertEqual(
                cc.resolve_concurrency("/home/%s/devel/odoo/odoo-slovnormal" % u,
                                       windows=cli_fleet.box_windows(u),
                                       home="/home/%s" % u),
                ("parallel", None, "default"))

    # (3) dispatch gate: d3's 2nd concurrent autopilot-worker is refused. The
    # real `dispatch_gate_line` resolves the mode itself via `_current_user`, so
    # patch it to david3 and pass d3's cwd expanded against THIS runner's $HOME
    # (the resolver expands the declared `~/...` cwd the same way).
    def test_dispatch_gate_blocks_d3_second_lane(self):
        cwd = os.path.expanduser("~/devel/odoo/odoo-erp")
        with m.patch.object(cc, "_current_user", return_value="david3"):
            self.assertEqual(cc.dispatch_gate_line(cwd, live_count=1),
                             "block|sequential|1")
            self.assertEqual(cc.dispatch_gate_line(cwd, live_count=0),
                             "allow|sequential|0")

    # (5) the /autopilot goal renderer has a fork-no-merge x sequential variant
    # (the phrase the registry already uses for sequential; refill phrase gone).
    def test_goal_renderer_forknomerge_sequential_variant(self):
        import goal_registry as gr
        line = gr.render_goal_line("fork-no-merge", "sequential", None)
        self.assertIn(
            "SEQUENTIAL — ONE unit at a time: dispatch → main review → "
            "integrate → verify → next; no refill;", line)
        self.assertNotIn("CONTINUOUS REFILL", line)


if __name__ == "__main__":
    main()
