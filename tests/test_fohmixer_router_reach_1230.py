"""#1230 — fohmixer may ssh to the MikroTik router 10.77.7.1, and nowhere else.

The fohmixer session could not reach the router with a key (owner request,
fohmixer issue 90): its #1186 reach list was empty, so the per-uid nft rule
rejected every outgoing tcp/22. One single-host entry opens exactly that path.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_account_bootstrap as bootstrap  # noqa: E402


def _ruleset(script):
    m = re.search(r"<< 'REACH_EOF'\n(.*?)REACH_EOF\n", script, re.S)
    assert m, "no nft ruleset heredoc in the render"
    return m.group(1)


class TestFohmixerRouterReach(unittest.TestCase):
    def setUp(self):
        self.rules = _ruleset(bootstrap.render_root_bootstrap("fohmixer"))

    def test_router_ssh_is_accepted_before_the_reject(self):
        accept = 'meta skuid "fohmixer" ip daddr 10.77.7.1 tcp dport { 22 } accept'
        reject = 'meta skuid "fohmixer" tcp dport 22 ct state new reject with tcp reset'
        self.assertIn(accept, self.rules)
        self.assertLess(self.rules.index(accept), self.rules.index(reject))

    def test_the_router_is_the_only_lan_host(self):
        self.assertEqual(set(re.findall(r"ip daddr (\S+)", self.rules)),
                         {"10.77.7.1"})

    def test_the_reason_is_carried(self):
        self.assertIn("MikroTik", self.rules)

    def test_the_declaration_validates(self):
        spec = bootstrap.SERVICE_ACCOUNTS["fohmixer"]
        self.assertEqual(bootstrap.validate_account("fohmixer", spec), [])


if __name__ == "__main__":
    unittest.main()
