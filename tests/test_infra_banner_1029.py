"""#1029 item 2 — `core-quals --role infra` NEW-since banner.

The INFRA session sees arrivals even without the proactive nudge: on the human
`core-quals --role infra` display, an additive `NEW since <last run>` banner names
the infra-queue items (tickets + tagged STOP:/GATEKEEPER-ACTION (INFRA) comments)
that appeared since the previous `--role infra` run, from a per-box per-role
durable marker (`~/.claude/quals-last-run/infra.json`). Additive — NEVER changes
the counts.

RED against the pre-implementation tree: `_infra_new_since_banner` /
`_read_quals_last_run` don't exist, and cmd_core_quals never prints a banner.
"""
import inspect
import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals_cmd as q


def _rec(id_, kind="ticket", num=None):
    num = num if num is not None else id_
    return {"id": id_, "kind": kind, "num": num,
            "permalink": "https://github.com/zbynekdrlik/odoo-erp/issues/%d" % num,
            "tag": "infra"}


class TestInfraBannerMarker(unittest.TestCase):
    def setUp(self):
        self._home = TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        p = m.patch.dict(os.environ, {"HOME": self._home.name})
        p.start()
        self.addCleanup(p.stop)

    def _fetch(self, records):
        return m.patch("airuleset._watchdog_infra_queue_fetch",
                       return_value=records)

    def test_first_run_seeds_no_banner(self):
        with self._fetch([_rec(1), _rec(6883)]):
            banner, new, cur = q._infra_new_since_banner("/r")
        self.assertEqual(banner, "")
        self.assertEqual(cur, {1, 6883})
        # marker seeded so a LATER run can diff
        last, ts = q._read_quals_last_run("infra")
        self.assertEqual(last, {1, 6883})
        self.assertIsNotNone(ts)

    def test_second_run_names_new_ticket(self):
        with self._fetch([_rec(1), _rec(6883)]):
            q._infra_new_since_banner("/r")           # seed
        with self._fetch([_rec(1), _rec(6883), _rec(7001)]):
            banner, new, cur = q._infra_new_since_banner("/r")
        self.assertIn("#7001", banner)
        self.assertNotIn("#1", banner)               # not new
        self.assertEqual(new, {7001})
        # marker advanced
        last, _ = q._read_quals_last_run("infra")
        self.assertEqual(last, {1, 6883, 7001})

    def test_second_run_names_new_comment_permalink(self):
        cid = 999_000_001
        with self._fetch([_rec(6883)]):
            q._infra_new_since_banner("/r")           # seed
        comment = _rec(cid, kind="comment", num=6883)
        comment["permalink"] = ("https://github.com/zbynekdrlik/odoo-erp/"
                                "issues/6883#issuecomment-%d" % cid)
        comment["tag"] = "GATEKEEPER-ACTION (INFRA)"
        with self._fetch([_rec(6883), comment]):
            banner, new, cur = q._infra_new_since_banner("/r")
        self.assertIn("issuecomment-%d" % cid, banner)   # permalink named
        self.assertEqual(new, {cid})

    def test_unchanged_no_banner(self):
        with self._fetch([_rec(1), _rec(6883)]):
            q._infra_new_since_banner("/r")           # seed
        with self._fetch([_rec(1), _rec(6883)]):
            banner, new, cur = q._infra_new_since_banner("/r")
        self.assertEqual(banner, "")
        self.assertEqual(new, set())

    def test_unmeasurable_no_banner_marker_untouched(self):
        with self._fetch([_rec(1)]):
            q._infra_new_since_banner("/r")           # seed {1}
        with self._fetch(None):                       # a gh error → None
            banner, new, cur = q._infra_new_since_banner("/r")
        self.assertEqual(banner, "")
        self.assertIsNone(new)
        # marker NOT advanced past the seed (so a real arrival is not lost)
        last, _ = q._read_quals_last_run("infra")
        self.assertEqual(last, {1})

    def test_marker_path_is_per_role_under_claude(self):
        p = q._quals_last_run_path("infra")
        self.assertTrue(p.endswith(os.path.join("quals-last-run", "infra.json")))
        self.assertIn(".claude", p)


class TestCoreQualsWiresBanner(unittest.TestCase):
    def test_core_quals_default_infra_path_prints_banner(self):
        # source lock: the default (human list) path of cmd_core_quals prints the
        # infra banner for role==infra (additive, before the workable rows).
        src = inspect.getsource(q.cmd_core_quals)
        self.assertIn("_infra_new_since_banner", src)


if __name__ == "__main__":
    unittest.main()
