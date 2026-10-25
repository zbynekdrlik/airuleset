"""#998 — window ROLE resolution + the fleet `windows` declaration.

`cli_fleet.managed_windows`/`box_windows`/`validate_windows` expose the
DECLARED managed tmux windows; `cli_concurrency.resolve_role` is the single
role resolver every consumer (footer, quals, goal renderer, status) reads: a
declared window of the box's own fleet entry, matched by cwd (containment,
longest wins) else by name. #1137 (owner 25.10.2026) removed the
sequential/parallel MODE half — these tests keep the role half.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_fleet  # noqa: E402
import cli_concurrency as cc  # noqa: E402

GK_WINDOWS = [
    {"name": "gk", "cwd": "~/devel/odoo/odoo-erp", "role": "review"},
    {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra", "role": "infra"},
    # #1074 — the third declared window (gk-quality); the full #1074 role/
    # resolver behaviour is locked in test_gk_quality_role_1074.py.
    {"name": "gk-quality", "cwd": "~/devel/odoo/odoo-erp-quality",
     "role": "quality"},
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
            [{"name": "a b", "cwd": "/abs", "role": "boss"}])
        self.assertEqual(len(errs), 3)
        errs2 = cli_fleet.validate_windows(
            [{"name": "ok", "cwd": "../esc", "role": None}])
        self.assertTrue(any(".." in e for e in errs2))

    def test_validate_rejects_duplicate_names(self):
        errs = cli_fleet.validate_windows(
            [{"name": "x", "cwd": "a", "role": None},
             {"name": "x", "cwd": "b", "role": None}])
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_box_windows_scopes_to_the_user(self):
        self.assertEqual(
            [w["name"] for w in cli_fleet.box_windows("gatekeeper")],
            ["gk", "gk-infra", "gk-quality"])
        # a non-declaring account gets nothing — never mis-inherits gk's.
        self.assertEqual(cli_fleet.box_windows("newlevel"), [])
        self.assertEqual(cli_fleet.box_windows(""), [])

    def test_stream_windows_keep_their_tmux_names(self):
        # The stream boxes keep a role-less declared window that names tmux.
        for u, alias, cwd in (("david3", "d3", "~/devel/odoo/odoo-erp"),
                              ("montalu4", "m4", "~/devel/odoo/odoo-erp"),
                              ("montalu1", "m1", "~/devel/odoo/odoo-slovnormal")):
            w = cli_fleet.box_windows(u)
            self.assertEqual([x["name"] for x in w], [alias])
            self.assertEqual(w[0]["cwd"], cwd)
            self.assertIsNone(w[0]["role"])
            self.assertEqual(cli_fleet.validate_windows(w), [])


class TestResolveRole(TestCase):
    HOME = "/home/gatekeeper"

    def test_declared_review_window_by_cwd(self):
        self.assertEqual(cc.resolve_role(
            self.HOME + "/devel/odoo/odoo-erp", windows=GK_WINDOWS,
            home=self.HOME), "review")

    def test_declared_infra_window_by_cwd(self):
        self.assertEqual(cc.resolve_role(
            self.HOME + "/devel/odoo/odoo-erp-infra", windows=GK_WINDOWS,
            home=self.HOME), "infra")

    def test_declared_window_by_name(self):
        self.assertEqual(cc.resolve_role(
            "/anything", window_name="gk-infra", windows=GK_WINDOWS,
            home=self.HOME), "infra")

    def test_cwd_wins_over_a_conflicting_window_name(self):
        # #998 addendum (owner 2026-09-12, "prečo mám dva gk"): a window
        # mis-named `gk` sitting in the infra cwd must resolve to role infra
        # (CWD wins), never review by name.
        self.assertEqual(cc.resolve_role(
            self.HOME + "/devel/odoo/odoo-erp-infra", window_name="gk",
            windows=GK_WINDOWS, home=self.HOME), "infra")

    def test_subdir_of_a_declared_cwd_resolves_to_that_window(self):
        # Containment: a pane cd'd into a subdirectory of a declared window's
        # cwd still resolves to that window's role.
        self.assertEqual(cc.resolve_role(
            self.HOME + "/devel/odoo/odoo-erp-infra/addons/x",
            windows=GK_WINDOWS, home=self.HOME), "infra")

    def test_no_declaration_no_role(self):
        self.assertIsNone(cc.resolve_role("/srv/elsewhere", windows=GK_WINDOWS,
                                          home=self.HOME))
        self.assertIsNone(cc.resolve_role("/srv/elsewhere", windows=[],
                                          home=self.HOME))

    def test_status_row_names_the_role_only_when_declared(self):
        self.assertEqual(cc.role_status_row(
            self.HOME + "/devel/odoo/odoo-erp-infra", windows=GK_WINDOWS,
            home=self.HOME), "role: infra")
        self.assertIsNone(cc.role_status_row("/srv/x", windows=[],
                                             home=self.HOME))

    def test_real_gk_review_window_resolves_review(self):
        gk = cli_fleet.box_windows("gatekeeper")
        self.assertEqual(cc.resolve_role("/home/gatekeeper/devel/odoo/odoo-erp",
                                         windows=gk, home="/home/gatekeeper"),
                         "review")


if __name__ == "__main__":
    main()
