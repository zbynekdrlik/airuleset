"""Footer `· bounce K` segment (#1056 L1 (b) / #1057 item 1).

RED-first: a distinct red `· bounce K` segment for open `prio:bounce` tickets in
the sub-dev slice — hidden at 0, positioned immediately after `I N`, on the
sub-dev (scope=mine) cache only. Plus `cli_quals._count_bounce` (the count the
footer writes, from the SAME workable partition rows).
"""
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals
import statusbar


def _seed(home, cwd, **fields):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    entry = {"root": str(cwd), "ts": int(time.time())}
    entry.update(fields)
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))


def _labels(*names):
    return [{"name": n} for n in names]


class CountBounce(unittest.TestCase):
    def test_counts_only_prio_bounce_rows(self):
        rows = {
            1: {"number": 1, "labels": _labels("stream:montalu", "prio:bounce")},
            2: {"number": 2, "labels": _labels("stream:montalu")},
            3: {"number": 3, "labels": _labels("prio:bounce", "ready-for-review")},
        }
        self.assertEqual(cli_quals._count_bounce(rows), 2)

    def test_empty_and_malformed(self):
        self.assertEqual(cli_quals._count_bounce({}), 0)
        self.assertEqual(cli_quals._count_bounce(None), 0)
        self.assertEqual(cli_quals._count_bounce({9: {"labels": None}}), 0)


class BounceSegmentRender(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.cwd = "/home/x/devel/demo"

    def _seg(self):
        return statusbar.tickets_segment(self.cwd, home=self.home, spawn=False)

    def test_bounce_segment_renders_after_i_and_is_red(self):
        _seed(self.home, self.cwd, name="demo", scope="mine",
              open=4, gk=2, bounce=3)
        seg = self._seg()
        self.assertIn("bounce 3", seg)
        self.assertIn("38;5;196", seg)                 # red
        # positioned after I N, before gk
        self.assertLess(seg.index("bounce"), seg.index("gk"))
        self.assertLess(seg.index("I "), seg.index("bounce"))

    def test_bounce_hidden_at_zero(self):
        _seed(self.home, self.cwd, name="demo", scope="mine", open=4,
              gk=1, bounce=0)
        self.assertNotIn("bounce", self._seg())

    def test_bounce_absent_on_full_authority_cache(self):
        # A full (core) cache carries no bounce field → never rendered.
        _seed(self.home, self.cwd, name="demo", scope="core", open=5)
        self.assertNotIn("bounce", self._seg())

    def test_bounce_in_carry_forward_keys(self):
        self.assertIn("bounce", statusbar._CARRY_FORWARD_KEYS)


if __name__ == "__main__":
    unittest.main()
