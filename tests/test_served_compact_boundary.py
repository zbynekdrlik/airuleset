"""Served (non-autopilot-worker) session compact boundary — REMOVED (#1084).

#228 taught a served, interactive session to call `airuleset.py compact-request
--self` at its own `## ✅ Work Complete` boundary. #1084 (owner ROZHODNUTÉ
2026-09-19) removes machine-triggered compacts for good, so that teaching is
GONE: no session ever records or types a `/compact` — Claude Code's native
threshold autocompact is the only compaction left.

This is the L1 inverted lock — the completion-report doctrine no longer instructs
any compact-request call, and `cmd_compact_request` is a removed stub. L2 (a
later lane) deletes this file with the rest of the compact-specific tests.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import airuleset  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestServedCompactTeachingRemoved(TestCase):
    MOD = "modules/core/completion-report.md"
    DEEP = "skills/completion-report-deep/DEEP.md"

    def _combined(self):
        return read(self.MOD) + "\n" + read(self.DEEP)

    def test_no_compact_request_self_instruction(self):
        t = self._combined()
        self.assertNotIn("compact-request --self", t)
        self.assertNotIn("compact-request --record", t)

    def test_names_the_removed_state(self):
        t = self._combined()
        # the doctrine states plainly that machine compacts are removed and
        # native autocompact is the only compaction left.
        low = t.lower()
        self.assertIn("removed", low)
        self.assertIn("autocompact", low)

    def test_cmd_compact_request_is_a_removed_stub(self):
        # any flags -> the removed line, exit 0 (no --self/--record/--status
        # branch remains). Canonical lock: tests/test_compact_removed_1084.py.
        import types
        import unittest.mock as m
        args = types.SimpleNamespace(self=True, record=False, status=False,
                                     session="", cwd="", origin="")
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            rc = airuleset.cmd_compact_request(args)
        self.assertIn("machine compacts removed", "".join(buf))
        self.assertIn(rc, (None, 0))


if __name__ == "__main__":
    main()
