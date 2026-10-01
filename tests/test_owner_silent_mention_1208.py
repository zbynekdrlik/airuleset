"""#1208: the owner is a SILENT delivery-control recipient of client Discuss
messages — in `partner_ids` on every message, but never a mention anchor and
never the first name (owner, 30.9.2026: "Preco by mala sprava spetovi zacinat
mojim menom!!!"). The #702 bullet said "anchor for EVERY addressee", which read
literally includes the owner it also puts in `partner_ids`.

The sentence lives inside the #702 bullet of the injected compose companion;
each negation is locked as a whole phrase (a noun-only lock survives an
inverted rule, #799).
"""
import re
import unittest
from pathlib import Path

COMPOSE = (Path(__file__).resolve().parents[1] / "skills" / "odoo-client-messaging"
           / "handover-compose.md")


def norm(s):
    return re.sub(r"\s+", " ", s)


def mention_bullet():
    text = COMPOSE.read_text(encoding="utf-8")
    idx = text.find("REÁLNE označený")
    nxt = text.find("\n- **", idx)
    return norm(text[idx:nxt if nxt != -1 else len(text)])


class TestOwnerSilent(unittest.TestCase):

    def test_the_owner_is_a_silent_recipient(self):
        b = mention_bullet()
        self.assertIn("Owner je TICHÝ delivery-control príjemca (#1208)", b)
        self.assertIn("je v `partner_ids`", b)

    def test_each_negation_is_stated(self):
        b = mention_bullet()
        self.assertIn("NIKDY nedostane mention anchor", b)
        self.assertIn("nikdy nie je prvé meno", b)
        self.assertIn("anchor patrí len klientskym adresátom", b)

    def test_the_script_half_is_pointed_at(self):
        self.assertIn("odoo-erp#8840", mention_bullet())


if __name__ == "__main__":
    unittest.main()
