"""#610 -> #1084 -- the SubagentStop compact-request channel is now DELETED.

#610 RETIRED the record channel in `hooks/notify-compact-subagent-boundary.sh`
(the hook stayed wired, only DECLINING). #1084 (owner ROZHODNUTÉ 2026-09-19)
removes machine-triggered compacts for good, so the hook is DELETED outright,
its SubagentStop registration is gone, and `compact_sweep` delivers nothing but
the removed journal line.

This is the L1 inverted lock -- the #610 concern is now resolved by deletion.
L2 (a later lane) deletes this file with the rest of the compact-specific tests.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
from watchdog import compact

REPO = Path(airuleset.__file__).resolve().parent
REMOVED_LINE = ("compact: machine compacts removed (owner 2026-09-19, #1084) "
                "— native autocompact only")


class TestSubagentCompactChannelDeleted(unittest.TestCase):
    def test_hook_file_is_deleted(self):
        self.assertFalse(
            (REPO / "hooks" / "notify-compact-subagent-boundary.sh").exists(),
            "the SubagentStop compact hook must be deleted (#1084)")

    def test_hook_is_unwired_from_subagent_stop(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        self.assertNotIn(
            "notify-compact-subagent-boundary",
            json.dumps(cfg["hooks"].get("SubagentStop", [])),
            "the deleted hook must be unwired from SubagentStop (#1084)")

    def test_compact_sweep_delivers_nothing_but_the_removed_line(self):
        with TemporaryDirectory() as d:
            rp = str(Path(d) / "compact-requests.json")
            logs = compact.compact_sweep(now=1.0, requests_path=rp)
        self.assertEqual(logs, [REMOVED_LINE])


if __name__ == "__main__":
    unittest.main()
