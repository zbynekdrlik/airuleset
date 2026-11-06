"""#1241: the infra gatekeeper rule sits where a target would otherwise mint a key.

The ruling lived only on the ticket, so #1272 asked the owner for one more token.
These locks hold the negations and the routing, not the nouns: an inverted
sentence must fail.
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def flat(rel):
    return re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8"))


class TestRuleReachesTheMoment(TestCase):
    def test_cloudflare_skill_refuses_a_new_token_before_the_taxonomy(self):
        text = flat("skills/cloudflare-api-tokens/SKILL.md")
        rule = text.index("A project never gets a new token for a rare change")
        self.assertLess(rule, text.index("## 0. Which credential"))
        self.assertIn("--label infra-request", text)

    def test_credential_companion_asks_first_whether_it_is_needed(self):
        text = flat("skills/receive-files-credentials/DEEP.md")
        rule = text.index("Never ask the owner to mint or type a high-privilege key")
        self.assertLess(rule, text.index("#### Receiving CREDENTIALS FROM the User"))
        self.assertIn("removed only with the owner's OK per key", text)
        self.assertIn("`newlevel` access stays as it is", text)
        self.assertIn("remoteos confirms with the owner", text)


class TestStreamRouting(TestCase):
    def test_odoo_goes_to_gk_and_other_infra_to_airuleset(self):
        text = flat("skills/autopilot/references/cross-stream-protocol.md")
        start = text.index("Which side takes the request (#1241")
        window = text[start:start + 560]
        self.assertRegex(window, r"client board\) go to \*\*gk\*\*")
        self.assertRegex(window, r"own tooling\) goes to \*\*airuleset\*\*")
        self.assertIn("gk-request --repo zbynekdrlik/airuleset", window)


if __name__ == "__main__":
    main()
