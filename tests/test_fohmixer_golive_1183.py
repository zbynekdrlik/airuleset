"""#1183 go-live: fohmixer lives in its own project account on dev1.

After the root bootstrap ran on dev1 (2026-09-29), the registry row, the
legacy ceiling and the fleet entry follow the account: the project is no
longer a legacy `newlevel` row, push manages the `fohmixer` account, and that
account is classified (full authority over its own repo, like `claudy`).
"""

import json
import unittest
from pathlib import Path

import cli_accounts
import cli_fleet

REPO = Path(__file__).resolve().parent.parent


def _registry_row(name):
    data = json.loads((REPO / "projects-registry.json").read_text())
    rows = data if isinstance(data, list) else data.get("projects", data)
    rows = rows if isinstance(rows, list) else list(rows.values())
    return next(r for r in rows if r.get("name") == name)


class FohmixerGoLive(unittest.TestCase):
    def test_registry_row_is_the_project_account(self):
        row = _registry_row("fohmixer")
        self.assertEqual(row["account"], "fohmixer")
        self.assertEqual(row["path"], "/home/fohmixer/devel/fohmixer")

    def test_legacy_ceiling_went_down(self):
        self.assertEqual(cli_accounts.LEGACY_CEILING, 24)

    def test_fleet_entry_exists_and_is_classified(self):
        entry = next(h for h in cli_fleet.REMOTE_HOSTS
                     if h.get("name") == "fohmixer@dev1")
        self.assertEqual(entry["user"], "fohmixer")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertEqual(entry["identity"], "~/.secrets/airuleset_push_ed25519")
        self.assertIn("fohmixer", cli_fleet.FULL_AUTHORITY_USERS)


class FohmixerHasNoDropLaneYet(unittest.TestCase):
    """A project account gets no file-drop lane until one is declared, and adding
    it to dev1 must not turn dev1 into a SHARED drop box: the owner's
    `drop-dev1.newlevel.media` host stays byte-identical."""

    def test_newlevel_dev1_keeps_its_host_and_fohmixer_has_no_lane(self):
        import cli_drop_gateway as g
        self.assertEqual(g.DROP_LANES[("dev1", "newlevel")].host,
                         "drop-dev1.newlevel.media")
        self.assertNotIn(("dev1", "fohmixer"), g.DROP_LANES)


if __name__ == "__main__":
    unittest.main()
