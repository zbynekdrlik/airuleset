"""#592: cli_aliases.short_target_alias -- the SINGLE shared target-alias
derivation, drawn on by BOTH the webterm dashboard tabs (cli_webterm._short_alias)
and the tmux window naming (cli_tmux_provisioning). One source, never a parallel
map."""
import unittest

import cli_aliases


class TestShortTargetAlias(unittest.TestCase):
    def test_owner_boxes_by_box_name(self):
        # dev1/dev2 share the `newlevel` unix user -> the BOX name disambiguates.
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "dev1"), "dev1")
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "dev2"), "dev2")

    def test_dev1_box_name_wins_regardless_of_user(self):
        # box_name == "dev1" resolves to dev1 first (mirrors the old
        # _short_alias `local or id=="dev1"` short-circuit).
        self.assertEqual(cli_aliases.short_target_alias("gatekeeper", "dev1"), "dev1")

    def test_gatekeeper(self):
        self.assertEqual(cli_aliases.short_target_alias("gatekeeper", "gatekeeper-cx23"), "gk")

    def test_montalu_family(self):
        self.assertEqual(cli_aliases.short_target_alias("montalu2", "subdev"), "m2")
        self.assertEqual(cli_aliases.short_target_alias("montalu8", "subdev"), "m8")

    def test_david_family_base_is_d1(self):
        self.assertEqual(cli_aliases.short_target_alias("david", "subdev"), "d1")
        self.assertEqual(cli_aliases.short_target_alias("david4", "subdev"), "d4")

    def test_miva_family(self):
        self.assertEqual(cli_aliases.short_target_alias("miva1", "subdev"), "miva")
        self.assertEqual(cli_aliases.short_target_alias("miva2", "subdev"), "mv2")

    def test_simap_family(self):
        self.assertEqual(cli_aliases.short_target_alias("simap1", "subdev"), "si1")

    def test_unknown_user_fallback_short(self):
        a = cli_aliases.short_target_alias("admin", "admin-forestshop-dev")
        self.assertTrue(0 < len(a) <= 8)
        self.assertNotIn("@", a)
        self.assertEqual(a, "admin")

    def test_empty_user_falls_back_to_box_name(self):
        # An UNRECOGNIZED owner box (spinbike is now recognized, see below) still
        # falls back to its box name's first segment.
        self.assertEqual(cli_aliases.short_target_alias("", "webbox-vps"), "webbox")
        self.assertEqual(cli_aliases.short_target_alias(None, "webbox-vps"), "webbox")

    def test_spinbike_box_alias_is_sb(self):
        # #661: spinbike-vps -> "sb", the owner's canonical short alias, regardless
        # of the box's unix user (it shares `newlevel` with dev2). Single alias
        # source (#592), so the webterm tab and the tmux window name agree.
        self.assertEqual(cli_aliases.short_target_alias("newlevel", "spinbike-vps"), "sb")
        self.assertEqual(cli_aliases.short_target_alias("", "spinbike-vps"), "sb")

    def test_marek_owner_account(self):
        # marek is an owner account (not in any alias family) -> its own name.
        self.assertEqual(cli_aliases.short_target_alias("marek", "subdev"), "marek")

    def test_forestshop_box_alias_is_fs(self):
        # #661 rework (owner DOPLNENIE 2026-08-25): Marek's forestshop VPS tab,
        # handled like the owner's spinbike `sb` -> box-keyed "fs" at the single
        # alias source (#592), regardless of the unix account, so the webterm
        # tab (lane id "forestshop") and the box's tmux WINDOW name (hostname
        # "forestshop-dev") agree for BOTH accounts.
        self.assertEqual(cli_aliases.short_target_alias("admin", "forestshop"), "fs")
        self.assertEqual(cli_aliases.short_target_alias("admin", "forestshop-dev"), "fs")
        self.assertEqual(cli_aliases.short_target_alias("stepan", "forestshop-dev"), "fs")
        # The FLEET inventory ids (admin-forestshop-dev / stepan-forestshop-dev)
        # keep their user-derived aliases — box_name's first segment is not
        # "forestshop" there, so nothing else in the fleet shifts.
        self.assertEqual(
            cli_aliases.short_target_alias("admin", "admin-forestshop-dev"), "admin")


if __name__ == "__main__":
    unittest.main()
