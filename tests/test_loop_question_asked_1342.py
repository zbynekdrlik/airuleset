"""#1342 -- the #1329 loop-question gate counts "workable tickets OTHER than
the one asked about".

The asked ticket stays in the footer `I` until its `needs-answer` label
reaches the (120 s) footer cache, so a session asking a genuine blocker on its
ONLY workable ticket read `I 1` and was pushed to a `⏳ WORKING` with nothing
else to do. The footer now records the I numbers (`open_numbers`), and the gate
subtracts the asked ticket (at most one) when it is one of the owed rows
(`open_numbers` + `done_numbers`).
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

import cli_ticket_route  # noqa: E402
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

    def _cache(self, open_n, open_numbers=None, done=0, done_numbers=None, **extra):
        d = Path(self.home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"open": open_n, "done": done, "ts": time.time() - 30, **extra}
        if open_numbers is not None:
            entry["open_numbers"] = open_numbers
        if done_numbers is not None:
            entry["done_numbers"] = done_numbers
        key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]
        (d / (key + ".json")).write_text(json.dumps(entry))

    def _decide(self, msg):
        return loopquestion.decide(json.dumps({"last_assistant_message": msg,
                                                "cwd": self.cwd}),
                                   capture=ARMED, home=self.home)

    def test_only_workable_ticket_is_the_asked_one_allows(self):
        self._cache(1, open_numbers=[7])
        self.assertFalse(self._decide(_block("#7"))[0])

    def test_other_workable_ticket_still_blocks(self):
        self._cache(2, open_numbers=[7, 9])
        block, reason = self._decide(_block("#7"))
        self.assertTrue(block)
        self.assertIn("I 1", reason)

    def test_asked_ticket_not_in_i_is_not_subtracted(self):
        # #12 is parked (W / U / skip / another role's): the one I row is #9.
        self._cache(1, open_numbers=[9], question_cover_rows=[9, 12])
        self.assertTrue(self._decide(_block("#12"))[0])

    def test_owed_close_row_asked_is_subtracted(self):
        self._cache(0, open_numbers=[], done=1, done_numbers=[7])
        self.assertFalse(self._decide(_block("#7"))[0])

    def test_legacy_cache_without_open_numbers_blocks_as_before(self):
        self._cache(1, question_cover_rows=[7], question_cover_numbers=[])
        self.assertTrue(self._decide(_block("#7"))[0])

    def test_cross_repo_ref_is_not_subtracted(self):
        self._cache(1, open_numbers=[7])
        self.assertTrue(self._decide(_block("zbynekdrlik/odoo-erp#7"))[0])

    def test_at_most_one_ticket_is_subtracted(self):
        self._cache(2, open_numbers=[7, 8])
        msg = _block("#7").replace("má mať #7?", "majú mať #7 a #8?")
        self.assertTrue(self._decide(msg)[0])

    def test_heading_names_the_ticket_when_the_decision_line_does_not(self):
        self._cache(1, open_numbers=[7])
        msg = _block("#7").replace("má mať #7?", "má mať výstup?")
        self.assertFalse(self._decide(msg)[0])

    def test_other_work_named_in_the_briefing_is_not_the_asked_one(self):
        # the question is about parked #12; the briefing mentions workable #13.
        self._cache(1, open_numbers=[13])
        msg = _block("#12").replace("potrebuje tvoje", "(na to čaká aj #13) potrebuje tvoje")
        msg = msg.replace("**Otázka — projekt airuleset (správa Claude pravidiel):** Ticket #12 ",
                          "**Otázka — projekt airuleset (správa Claude pravidiel):** Ticket #12\n")
        self.assertTrue(self._decide(msg)[0])

    def test_old_cache_with_done_numbers_but_no_open_numbers_blocks(self):
        self._cache(0, done=1, done_numbers=[7])
        self.assertTrue(self._decide(_block("#7"))[0])

    def test_ref_outside_the_question_block_is_not_the_asked_one(self):
        self._cache(1, open_numbers=[5])
        msg = "Hotové #5 čiastočne.\n\n" + _block("#7")
        self.assertTrue(self._decide(msg)[0])

    def test_trailing_blank_lines_keep_the_asked_ticket(self):
        self._cache(1, open_numbers=[12])
        self.assertFalse(self._decide("Robím.\n\n❓ NEEDS YOU: schváliš #12?\n\n")[0])

    def test_odd_cache_shapes_fail_toward_the_full_count(self):
        for numbers in ([[1]], ["7"], [None], [True]):
            self._cache(1, open_numbers=numbers)
            self.assertTrue(self._decide(_block("#7"))[0], numbers)
            self.assertTrue(self._decide(_block("#1"))[0], numbers)   # True == 1


class FooterRecordsOpenNumbers(unittest.TestCase):
    def test_record_writes_the_i_numbers(self):
        entry = {}
        buckets = {k: {} for k in ("I", "M", "P", "C", "U", "W", "gk")}
        buckets["I"] = {"7": {}, 3: {}}
        cli_ticket_route.record(entry, buckets)
        self.assertEqual(entry["open_numbers"], [3, 7])


if __name__ == "__main__":
    unittest.main()
