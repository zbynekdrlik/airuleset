"""#1301 -- the `restart <N>M` footer segment for a session whose own
transcript crossed the weight at which long-lived sessions pinned a CPU core
(david3: 68 MB on 10.11., 97 MB on 13.11.2026). A signal for the owner, never a
keystroke or a kill: only the owner restarts a session."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import airuleset
import transcript_weight as tw

MIB = 1024 * 1024


def _sparse(dirpath, size):
    p = Path(dirpath) / "sess.jsonl"
    with open(p, "wb") as f:
        f.truncate(size)       # sparse: no real disk use
    return str(p)


class RestartSegment(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def test_heavy_transcript_shows_restart_with_size(self):
        p = _sparse(self.dir, 97 * MIB + 5)
        segs = tw.restart_segments({"transcript_path": p})
        self.assertEqual(len(segs), 1)
        self.assertIn("restart 97M", segs[0])
        self.assertIn("\033[38;5;196m", segs[0])     # red: owner action

    def test_exact_threshold_shows(self):
        p = _sparse(self.dir, tw.RESTART_TRANSCRIPT_BYTES)
        self.assertEqual(len(tw.restart_segments({"transcript_path": p})), 1)

    def test_below_threshold_is_hidden(self):
        p = _sparse(self.dir, tw.RESTART_TRANSCRIPT_BYTES - 1)
        self.assertEqual(tw.restart_segments({"transcript_path": p}), [])

    def test_threshold_sits_under_the_first_lockup(self):
        self.assertLess(tw.RESTART_TRANSCRIPT_BYTES, 68 * 1000 * 1000)
        self.assertGreaterEqual(tw.RESTART_TRANSCRIPT_BYTES, 32 * MIB)

    def test_degraded_payloads_render_nothing(self):
        for payload in (None, "x", {}, {"transcript_path": ""},
                        {"transcript_path": 5},
                        {"transcript_path": os.path.join(self.dir, "missing")},
                        {"transcript_path": "bad\0path"}):
            self.assertEqual(tw.restart_segments(payload), [], payload)

    def test_never_reads_the_content(self):
        p = _sparse(self.dir, 70 * MIB)
        real_open = open

        def no_open(*a, **k):
            raise AssertionError("transcript content was opened")
        import builtins
        builtins.open = no_open
        try:
            segs = tw.restart_segments({"transcript_path": p})
        finally:
            builtins.open = real_open
        self.assertEqual(len(segs), 1)


class ShimRendersIt(unittest.TestCase):
    """End to end through the REAL rendered statusline shim."""

    def _render(self, payload):
        with tempfile.TemporaryDirectory() as home:
            shim = os.path.join(home, "shim.sh")
            with open(shim, "w") as fh:
                fh.write(airuleset.CAVEMAN_SHIM_CONTENT)
            env = dict(os.environ)
            env["HOME"] = home
            env.pop("TMUX_PANE", None)
            return subprocess.run(["bash", shim], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env)

    def test_heavy_session_footer_carries_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p = _sparse(d, 98 * MIB)
            r = self._render({"workspace": {"current_dir": d},
                              "transcript_path": p})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restart 98M", r.stdout)

    def test_light_session_footer_has_no_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p = _sparse(d, 3 * MIB)
            r = self._render({"workspace": {"current_dir": d},
                              "transcript_path": p})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("restart", r.stdout)


if __name__ == "__main__":
    unittest.main()
