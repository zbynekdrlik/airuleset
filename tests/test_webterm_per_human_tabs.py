"""#661: per-domain OWNER-DEFINED webterm dashboard tab lists.

The owner (`zbynek.newlevel.media`) never asked for a tab per fleet target; the
#579/#612 multi-target inventory surfaced OTHER people's personal accounts
(marek@subdev, david1-4@subdev, stepan@forestshop-dev, admin@forestshop-dev) on
his own dashboard. The fix is an EXCLUSIVE, owner-defined, per-domain tab list:
a domain renders EXACTLY the inventory ids listed for it, in the owner's order,
and NOTHING renders merely because it exists in the fleet inventory.

Plus two owner amendments in the same PR: keyboard focus jumps into the shown
terminal on every tab switch, and unselected tab text is lightened for
readability.

This is tab VISIBILITY on the dashboard, NOT an auth boundary: the connect
allowlist stays the full fleet (the owner retains reachability via his own
dev1 SSH keys; Cloudflare Access remains the auth layer).
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_aliases  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402

# The owner-defined zbynek.newlevel.media tab list, EXACT order (owner ROZHODNUTÉ
# 2026-08-24: "dev1, dev2, gk, m1..m6, d1, d2, miva, sb"; david3 (d3) added after
# d2 per owner request 2026-08-26, #719).
ZBYNEK_ORDER = [
    "dev1", "dev2", "gatekeeper",
    "montalu1-subdev", "montalu2-subdev", "montalu3-subdev",
    "montalu4-subdev", "montalu5-subdev", "montalu6-subdev",
    "david1-subdev", "david2-subdev", "david3-subdev",
    "miva1-subdev", "spinbike-vps",
]
# Fleet targets the owner EXCLUDED from his domain (david4 stays excluded, #719).
ZBYNEK_EXCLUDED = [
    "montalu7-subdev", "montalu8-subdev", "david4-subdev",
    "simap1-subdev", "marek-subdev", "stepan-forestshop-dev",
    "admin-forestshop-dev",
]
# The owner's expected tab ALIASES, in his order (spinbike -> "sb").
ZBYNEK_ALIAS_ORDER = [
    "dev1", "dev2", "gk", "m1", "m2", "m3", "m4", "m5", "m6", "d1", "d2",
    "d3", "miva", "sb",
]


def _owner_inv():
    return w.webterm_inventory(profile=profiles.OWNER)


def _render_owner(human="zbynek"):
    return w.render_dashboard_html(
        _owner_inv(), ttyd_base="/t", human=human, term_grid=(176, 51))


class TestExclusiveTabListMechanism(unittest.TestCase):
    def test_config_keyed_by_human_with_owner_exact_list(self):
        # The declarative, owner-editable per-domain list constant exists and
        # carries the owner's EXACT zbynek order.
        self.assertEqual(w.WEBTERM_DASHBOARD_TABS["zbynek"], ZBYNEK_ORDER)

    def test_owner_login_user_has_a_defined_list(self):
        # Wiring proof: the owner gateway renders for WEBTERM_LOGIN_USER, so it
        # must be a configured key (else the owner dashboard would not filter).
        self.assertIn(w.WEBTERM_LOGIN_USER, w.WEBTERM_DASHBOARD_TABS)

    def test_entries_for_tab_list_is_exclusive_and_preserves_order(self):
        inv = _owner_inv()
        got = [e["id"] for e in w.entries_for_tab_list(inv, ["gatekeeper", "dev1", "dev2"])]
        # LIST order preserved (NOT the #579 _tab_order_key WT sort).
        self.assertEqual(got, ["gatekeeper", "dev1", "dev2"])
        # An id not named is never returned.
        self.assertNotIn("montalu1-subdev", got)

    def test_entries_for_tab_list_drops_unknown_ids(self):
        inv = _owner_inv()
        got = [e["id"] for e in w.entries_for_tab_list(inv, ["dev1", "does-not-exist", "dev2"])]
        self.assertEqual(got, ["dev1", "dev2"])

    def test_zbynek_list_resolves_to_exact_order(self):
        inv = _owner_inv()
        got = [e["id"] for e in w.entries_for_tab_list(inv, w.WEBTERM_DASHBOARD_TABS["zbynek"])]
        self.assertEqual(got, ZBYNEK_ORDER)

    def test_zbynek_excludes_unlisted_fleet_targets(self):
        inv = _owner_inv()
        got = {e["id"] for e in w.entries_for_tab_list(inv, w.WEBTERM_DASHBOARD_TABS["zbynek"])}
        for excluded in ZBYNEK_EXCLUDED:
            self.assertNotIn(excluded, got)

    def test_marek_domain_resolves_his_lane_inventory_to_exact_order(self):
        # #661 rework (owner ruling 2026-08-25): marek's set grew from the
        # rejected single member to marek + montalu4 + his dev1/dev2 sessions +
        # his forestshop VPS. #787 (2026-08-31) added montalu2, mirroring
        # montalu4. The policy ids are the MAREK LANE inventory ids
        # (cli_webterm_profiles.marek_inventory — the inventory his gateway
        # actually renders with human="marek"), NOT the fleet ids: his `dev1`/
        # `dev2` entries attach HIS `marek` tmux group, never the owner's.
        got = [e["id"] for e in w.entries_for_tab_list(
            profiles.marek_inventory(), w.WEBTERM_DASHBOARD_TABS["marek"])]
        self.assertEqual(got, ["marek-subdev", "montalu2-subdev",
                               "montalu4-subdev", "dev1", "dev2", "forestshop"])
        self.assertEqual(w.WEBTERM_DASHBOARD_TABS["marek"], got)

    def test_marek_lane_render_alias_order_and_exclusions(self):
        # The prod marek-lane render path: his scoped inventory + human="marek".
        html = w.render_dashboard_html(
            profiles.marek_inventory(), ttyd_base="/t", human="marek",
            term_grid=(176, 51))
        aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
        self.assertEqual(aliases, ["marek", "m2", "m4", "dev1", "dev2", "fs"])
        # No third person's personal account on Marek's dashboard (the original
        # #661 sin): stepan@forestshop-dev must never render here.
        self.assertNotIn("stepan", html)
        # A lane render never enables the owner-only U-status poll (#677).
        self.assertIn('"u_status": false', html)

    def test_david_domain_shows_only_david_accounts(self):
        inv = _owner_inv()
        got = {e["id"] for e in w.entries_for_tab_list(inv, w.WEBTERM_DASHBOARD_TABS["david"])}
        self.assertEqual(got, {"david1-subdev", "david2-subdev", "david3-subdev", "david4-subdev"})


class TestOwnerDashboardRender(unittest.TestCase):
    def test_owner_dashboard_html_excludes_other_humans(self):
        html = _render_owner()
        for other in ('title="marek@subdev"', 'title="stepan@forestshop-dev"',
                      'title="david4@subdev"',
                      'title="admin@forestshop-dev"', 'title="montalu7@subdev"',
                      'title="simap1@subdev"'):
            self.assertNotIn(other, html)

    def test_owner_dashboard_html_includes_his_boxes(self):
        html = _render_owner()
        for present in ('title="dev1 (localhost)"', 'title="dev2"',
                        'title="gatekeeper"', 'title="spinbike-vps"',
                        'title="david3@subdev"'):  # #719: d3 now on the owner dashboard
            self.assertIn(present, html)

    def test_owner_dashboard_tab_alias_order_is_owner_defined(self):
        html = _render_owner()
        aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
        self.assertEqual(aliases, ZBYNEK_ALIAS_ORDER)

    def test_render_without_human_is_unfiltered(self):
        # The david gateway path renders its scoped inventory with no human
        # filter — backward compatible (full given inventory, WT-sorted).
        inv = _owner_inv()
        html = w.render_dashboard_html(inv, ttyd_base="/t", human=None, term_grid=(176, 51))
        aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
        self.assertEqual(len(aliases), len(inv))  # every fleet entry still a tab

    def test_truthy_unconfigured_human_fails_closed_to_empty(self):
        # #661 review 🔵: a truthy human with NO configured list must NOT leak the
        # full fleet onto a personal domain — it renders an EMPTY tab set (loud
        # fail-closed), never everyone. Only human=None is unfiltered.
        inv = _owner_inv()
        html = w.render_dashboard_html(inv, ttyd_base="/t", human="nobody", term_grid=(176, 51))
        aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
        self.assertEqual(aliases, [])
        # no foreign account leaked
        self.assertNotIn('title="marek@subdev"', html)


class TestConnectAllowlistUnchanged(unittest.TestCase):
    def test_connect_allowlist_stays_full_fleet(self):
        # #661 is VISIBILITY, not an auth boundary: the inventory that feeds the
        # connect allowlist is NOT filtered — the owner keeps reachability.
        inv = _owner_inv()
        ids = {e["id"] for e in inv}
        for foreign in ("marek-subdev", "stepan-forestshop-dev", "david3-subdev"):
            self.assertIn(foreign, ids)


class TestDavidProfileOwnDomain(unittest.TestCase):
    def test_david_profile_renders_david_accounts(self):
        dinv = w.webterm_inventory(profile=profiles.DAVID)
        ids = {e["id"] for e in dinv}
        self.assertLessEqual({"david1", "david2", "david3", "david4"}, ids)


class TestSpinbikeAlias(unittest.TestCase):
    def test_spinbike_alias_is_sb(self):
        # Owner writes the spinbike tab as "sb"; the single alias source
        # (cli_aliases, #592) supplies it so the tab and tmux window agree.
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "spinbike-vps"), "sb")


class TestFocusOnTabSwitch(unittest.TestCase):
    def test_activate_focuses_shown_terminal(self):
        html = _render_owner()
        # A focus helper is defined and invoked from the central switch fn so
        # typing works immediately after a tab switch (mouse AND Ctrl+Alt+N),
        # with no extra click into the terminal (#661 amendment 2).
        self.assertIn("function focusTerminal(", html)
        m = re.search(r"function activate\([^)]*\)\s*\{.*?\n\}", html, re.DOTALL)
        self.assertIsNotNone(m, "activate() not found")
        self.assertIn("focusTerminal(", m.group(0))

    def test_focus_has_generation_guard(self):
        # #661 review 🟡: a superseded retry chain must bail so a late-connecting
        # hidden terminal never steals focus back. activate passes idx; the retry
        # bails when `current` has moved on.
        html = _render_owner()
        self.assertIn("focusTerminal(made[idx], idx)", html)
        fm = re.search(r"function focusTerminal\([^)]*\)\s*\{.*?\n\}", html, re.DOTALL)
        self.assertIsNotNone(fm, "focusTerminal() not found")
        self.assertIn("idx !== current", fm.group(0))


class TestUnselectedTabContrast(unittest.TestCase):
    def test_unselected_tab_text_is_lighter(self):
        html = _render_owner()
        m = re.search(r"\.tab \{[^}]*\}", html)
        self.assertIsNotNone(m, ".tab CSS rule not found")
        rule = m.group(0)
        # Unselected tab text lightened to a readable Campbell grey; the old
        # low-contrast #9a9a9a is gone from the .tab rule (#661 amendment 3).
        self.assertIn("#CCCCCC", rule)
        self.assertNotIn("#9a9a9a", rule)

    def test_active_tab_stays_lightest(self):
        html = _render_owner()
        m = re.search(r"\.tab\.active \{[^}]*\}", html)
        self.assertIsNotNone(m, ".tab.active CSS rule not found")
        self.assertIn("#F2F2F2", m.group(0))


class TestTabLabelLeftPadding(unittest.TestCase):
    """#661 owner-acceptance amendment (2026-08-24): the tab NAMES sit a bit
    too close to the tab's left edge ("odsadit nazvy tabov z lavej strany, apon
    trosicku"). The fix indents all tab content from the left by bumping the
    `.tab` LEFT padding to 16px (a restrained +4px over the 12px right),
    expressed via the 4-value shorthand. This test locks that #661 INTENT — a
    left padding of 16px, distinct from and larger than the 12px right, never
    the pre-#661 tight `12px`-all-round form. The VERTICAL (top/bottom) values
    are owned by #691 (vertical-breathing rework, 6px -> 9px), so they are NOT
    pinned here — only the left/right asymmetry #661 introduced."""

    def test_tab_has_extra_left_padding(self):
        html = _render_owner()
        m = re.search(r"\.tab \{[^}]*\}", html)
        self.assertIsNotNone(m, ".tab CSS rule not found")
        rule = m.group(0)
        pm = re.search(r"padding: (\d+)px (\d+)px (\d+)px (\d+)px", rule)
        self.assertIsNotNone(pm, "4-value .tab padding shorthand not found: %s" % rule)
        _top, right, _bottom, left = (int(pm.group(i)) for i in (1, 2, 3, 4))
        # #661 intent: left indented to 16px, larger than the 12px right.
        self.assertEqual(left, 16, "tab left padding must stay the #661 16px")
        self.assertEqual(right, 12)
        self.assertGreater(left, right, "left must stay indented past the right (#661)")
        # The pre-#661 tight two-value all-round form must be gone.
        self.assertNotIn("padding: 6px 12px;", rule)


if __name__ == "__main__":
    unittest.main()
