"""#1404: an owner dependency written as "waiting for the next FOH slot" /
"with someone at the PC" is a wait on a person or an on-site slot.

The real dasio corpus (zbynekdrlik/dasio, read 28.11.2026): #127's 26.11
07:03Z comment "Waiting for the next FOH slot: dasio 0.1.176 …", #26's 25.11
comment "done only with someone at the PC or the owner's word", #127's 28.11
"then FOH with someone on site". None matched `gates.ownerdep`, so the
step sat in I until the owner asked. The same detector feeds the Stop gate
and the watchdog triage notice, so the hook prefilter must stay a superset.
"""
import re
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gates import ownerdep  # noqa: E402

HOOK = REPO_ROOT / "hooks" / "stop-check-question-quality.sh"

DASIO = (
    "26.11 08:03 CET: FOH band freed for the sound engineer's work (owner). The planned "
    "09:00 CET slot is off. Waiting for the next FOH slot: dasio 0.1.176 (main 4b8142c) "
    "on band-asio-dante.",
    "Both need a NIC key write and an adapter restart on a PC with one NIC: a network "
    "change, done only with someone at the PC or the owner's word.",
    "Plan: on mbc, a 60 s stack-trace ring before and after Ndu Start 4 + reboot, then "
    "FOH with someone on site.",
    "Blocked on the next on-site window at FOH.",
    "Waiting for someone at the FOH PC for ~45 min, outside a service.",
    "Čaká na ďalší FOH slot (reštart karty).",
)
NOT_A_WAIT = (
    "The next FOH slot went fine: all four steps done 27.11.",
    "It was waiting for the next FOH slot; done in that slot on 27.11.",
    "The scheduler hands each job the next free slot in the queue.",
    "Paired with someone at the PC earlier; nothing is left for them.",
    "Waiting for the next CI run to finish.",
)


class Corpus(unittest.TestCase):
    def test_dasio_waits_are_owner_dependencies(self):
        for text in DASIO:
            self.assertTrue(ownerdep.owner_dependency(text), text)

    def test_settled_or_machine_slots_are_not(self):
        for text in NOT_A_WAIT:
            self.assertEqual(ownerdep.owner_dependency(text), "", text)

    def test_hook_prefilter_stays_a_superset(self):
        words = re.search(r"OD_WORDS='([^']+)'", HOOK.read_text(encoding="utf-8")).group(1)
        rx = re.compile(words, re.IGNORECASE)
        for text in DASIO:
            self.assertTrue(rx.search(text), text)

    def test_no_quadratic_scan(self):
        long_line = "waiting for the next " + "x-" * 40000 + " slot"
        start = time.monotonic()
        ownerdep.owner_dependency(long_line + "\n" + "with someone at the " + "a " * 40000)
        self.assertLess(time.monotonic() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
