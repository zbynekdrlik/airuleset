"""#1273: dcontroller is a declared project account on dev1 (owner 31.10.: a web
Dante controller in its own project), peer of dante-stack, which owns the
protocol."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_drop_lanes as drop  # noqa: E402
import cli_peer_channel as cpc  # noqa: E402


class TestDcontroller(unittest.TestCase):

    def test_declaration(self):
        spec = bootstrap.account_spec("dcontroller")
        self.assertEqual(bootstrap.validate_account("dcontroller", spec), [])
        self.assertEqual(spec["host"], "dev1")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(spec["repo"], "zbynekdrlik/dcontroller")
        self.assertIs(spec["github_app"], True)
        self.assertEqual(set(spec["webterm_sessions"]), {"zbynek", "marek"})
        self.assertEqual(spec["handover"], "docs/HANDOVER.md")
        self.assertIn("rust-target:wasm32-unknown-unknown", spec["tools"])

    def test_drop_lane_port_is_unique_and_not_reserved(self):
        port = drop._GENERATED_DROP_PORTS[("dev1", "dcontroller")]
        self.assertNotIn(port, drop._RESERVED_PORTS)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(port), 1)

    def test_only_peer_is_dante_stack_with_a_topic(self):
        doc = cpc.load_links()
        self.assertEqual(cpc.validate_links(doc, cpc.load_registry()), [])
        pairs = [p for p in doc["hosts"]["dev1"]["links"] if "dcontroller" in p]
        self.assertEqual(pairs, [["dante-stack", "dcontroller"]])
        self.assertIn("dante-stack",
                      doc["hosts"]["dev1"]["topics"]["dante-stack|dcontroller"])


if __name__ == "__main__":
    unittest.main()
