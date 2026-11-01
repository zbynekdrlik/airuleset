"""Claude Code 2.1.3xx writes large api-request-* records after a prompt. A byte or
entry tail of the raw file loses the owner's prompt, so a latched session stayed
latched and the 401 continue was withheld (1.11.2026, 06:43 prompt, 06:53 401)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import owner_stop as osm  # noqa: E402


def _prompt(ts="2026-11-01T05:43:14.134Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": "oprav to prosim"}}


def _noise(n, size):
    return [{"type": "api-request-blob", "blob": "x" * size} for _ in range(n)]


class TestApiRecords(unittest.TestCase):
    def _latch_after(self, noise):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "s.jsonl")
            rows = [_prompt()] + noise
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            rec = {"fact": "exit", "ts": 1}
            return osm.scan(osm._tail(path), rec)[0]

    def test_a_prompt_survives_their_bytes(self):
        self.assertIsNone(self._latch_after(_noise(4, 450_000)))

    def test_a_prompt_survives_their_count(self):
        self.assertIsNone(self._latch_after(_noise(osm.TAIL_ENTRIES + 50, 100)))


if __name__ == "__main__":
    unittest.main()
