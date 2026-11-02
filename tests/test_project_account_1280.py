"""#1280: dsync is its own project account, and only dstack and airuleset reach it
(owner 1.11.: it is a critical part of dstack, so nothing changes it unchecked)."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop

ROOT = Path(__file__).resolve().parent.parent


class TestDsyncAccount(unittest.TestCase):
    def test_the_account_is_valid_and_owns_the_repo_alone(self):
        spec = bootstrap.account_spec("dsync")
        self.assertEqual(bootstrap.validate_account("dsync", spec), [])
        self.assertEqual(spec["repo"], "zbynekdrlik/dsync")
        self.assertIs(spec["sudo"], False)
        self.assertNotIn("extra_repos", bootstrap.account_spec("dstack"))

    def test_the_drop_port_is_its_own(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "dsync")], 8901)

    def test_only_dstack_and_airuleset_reach_it(self):
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = [sorted(p) for p in hosts["dev1"]["links"] if "dsync" in p]
        self.assertEqual(sorted(pairs), [["airuleset", "dsync"], ["dstack", "dsync"]])
        self.assertIn("through dstack", hosts["dev1"]["topics"]["dstack|dsync"])

    def test_intercom_reaches_camera_box_dstack_and_airuleset(self):
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "intercom" in p)
        self.assertEqual(pairs, [["airuleset", "intercom"], ["camera-box", "intercom"],
                                 ["dstack", "intercom"]])
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "intercom")], 8902)
        self.assertEqual(bootstrap.validate_account("intercom", bootstrap.account_spec("intercom")), [])

    def test_obs_reaches_only_camera_box_dstack_and_airuleset(self):
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "obs" in p)
        self.assertEqual(pairs, [["airuleset", "obs"], ["camera-box", "obs"], ["dstack", "obs"]])
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "obs")], 8903)
        self.assertEqual(bootstrap.validate_account("obs", bootstrap.account_spec("obs")), [])

    def test_shading_reaches_only_camera_box_and_airuleset(self):
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "shading" in p)
        self.assertEqual(pairs, [["airuleset", "shading"], ["camera-box", "shading"]])
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "shading")], 8904)
        self.assertEqual(bootstrap.validate_account("shading", bootstrap.account_spec("shading")), [])

    def test_the_old_name_left_the_registry(self):
        rows = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        rows = rows if isinstance(rows, list) else rows["projects"]
        names = {r["name"]: r for r in rows}
        self.assertNotIn("dantesync", names)
        self.assertEqual(names["dsync"]["account"], "dsync")


if __name__ == "__main__":
    unittest.main()
