"""#1342 -- the #1329 loop-question gate counts "workable tickets OTHER than
the one asked about".

The asked ticket stays in the footer `I` until its `needs-answer` label
reaches the (120 s) footer cache, so a session asking a genuine blocker on its
ONLY workable ticket read `I 1` and was pushed to a `⏳ WORKING` with nothing
else to do. The gate now subtracts the asked ticket (at most one) when the
cache shows it as a counted row not yet in U.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import loopquestion  # noqa: E402

ARMED = "  ctx ███░  caveman:lite  ◎ /goal active (12m)\n"


def _block(ref="#7"):
    return ("Robím na tom.\n\n"
            "**Otázka — projekt airuleset (správa Claude pravidiel):** Ticket %s "
            "potrebuje tvoje rozhodnutie o formáte výstupu.\n"
            "- A (odporúčam) — krátky formát\n- B — dlhý formát\n\n"
            "❓ NEEDS YOU: ktorý formát výstupu má mať %s?" % (ref, ref))


class AskedTicketIsNotOtherWork(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lq-1342-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = "/repo/x"

    def _cache(self, open_n, rows=None, label_u=None, waiting=None):
        d = Path(self.home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"open": open_n, "ts": time.time() - 30}
        if rows is not None:
            entry["question_cover_rows"] = rows
        if label_u is not None:
            entry["question_cover_numbers"] = label_u
        if waiting is not None:
            entry["user_waiting_numbers"] = waiting
        key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]
        (d / (key + ".json")).write_text(json.dumps(entry))

    def _decide(self, msg):
        return loopquestion.decide(json.dumps({"last_assistant_message": msg,
                                                "cwd": self.cwd}),
                                   capture=ARMED, home=self.home)

    def test_only_workable_ticket_is_the_asked_one_allows(self):
        self._cache(1, rows=[7], label_u=[], waiting=[])
        self.assertFalse(self._decide(_block("#7"))[0])

    def test_other_workable_ticket_still_blocks(self):
        self._cache(2, rows=[7, 9], label_u=[], waiting=[])
        block, reason = self._decide(_block("#7"))
        self.assertTrue(block)
        self.assertIn("I 1", reason)

    def test_asked_ticket_already_in_u_is_not_subtracted(self):
        # #7 is already parked in U, so the one I row is ANOTHER ticket.
        self._cache(1, rows=[7, 9], label_u=[7], waiting=[7])
        self.assertTrue(self._decide(_block("#7"))[0])

    def test_legacy_cache_without_cover_fields_blocks_as_before(self):
        self._cache(1)
        self.assertTrue(self._decide(_block("#7"))[0])

    def test_uncounted_ref_is_not_subtracted(self):
        self._cache(1, rows=[9], label_u=[], waiting=[])
        self.assertTrue(self._decide(_block("#7"))[0])

    def test_cross_repo_ref_is_not_subtracted(self):
        self._cache(1, rows=[7], label_u=[], waiting=[])
        self.assertTrue(self._decide(_block("zbynekdrlik/odoo-erp#7"))[0])

    def test_at_most_one_ticket_is_subtracted(self):
        self._cache(2, rows=[7, 8], label_u=[], waiting=[])
        msg = _block("#7").replace("formáte výstupu", "formáte výstupu (aj #8)")
        self.assertTrue(self._decide(msg)[0])

    def test_ref_outside_the_question_block_is_not_the_asked_one(self):
        # a ticket mentioned in the progress text above the question block is
        # not the asked ticket.
        self._cache(1, rows=[5], label_u=[], waiting=[])
        msg = "Hotové #5 čiastočne.\n\n" + _block("#7")
        self.assertTrue(self._decide(msg)[0])


if __name__ == "__main__":
    unittest.main()
