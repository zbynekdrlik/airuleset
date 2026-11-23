"""#1301 / #1395 -- the `shrink <N>M` footer segment (was `restart <N>M`) for a
session whose own transcript crossed the weight at which long-lived sessions
pinned a CPU core (david3: 68 MB on 10.11., 97 MB on 13.11.2026). A restart with
`-c` reloads the same file, so #1395 points the segment at `session-shrink`. A
signal for the owner, never a keystroke or a kill."""

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


def _sparse_segs(dirpath, size):
    return tw.shrink_segments({"transcript_path": _sparse(dirpath, size)})


class RestartSegment(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def test_heavy_transcript_shows_shrink_with_size(self):
        p = _sparse(self.dir, 137 * MIB + 5)
        segs = tw.shrink_segments({"transcript_path": p})
        self.assertEqual(len(segs), 1)
        self.assertIn("shrink 137M", segs[0])
        self.assertNotIn("restart", segs[0])
        self.assertIn("\033[38;5;196m", segs[0])     # red: owner action

    def test_exact_threshold_shows(self):
        p = _sparse(self.dir, tw.RESTART_TRANSCRIPT_BYTES)
        self.assertEqual(len(tw.shrink_segments({"transcript_path": p})), 1)

    def test_below_threshold_is_hidden(self):
        p = _sparse(self.dir, tw.RESTART_TRANSCRIPT_BYTES - 1)
        self.assertEqual(tw.shrink_segments({"transcript_path": p}), [])

    def test_threshold_is_the_owners_100_mib(self):
        # owner 23.11.2026 (#1395): shrink only from 100 MiB, the footer agrees
        self.assertEqual(tw.RESTART_TRANSCRIPT_BYTES, 100 * MIB)
        self.assertEqual(_sparse_segs(self.dir, 99 * MIB), [])

    def test_degraded_payloads_render_nothing(self):
        for payload in (None, "x", {}, {"transcript_path": ""},
                        {"transcript_path": 5},
                        {"transcript_path": os.path.join(self.dir, "missing")},
                        {"transcript_path": "bad\0path"}):
            self.assertEqual(tw.shrink_segments(payload), [], payload)

    def test_never_reads_the_content(self):
        p = _sparse(self.dir, 120 * MIB)
        real_open = open

        def no_open(*a, **k):
            raise AssertionError("transcript content was opened")
        import builtins
        builtins.open = no_open
        try:
            segs = tw.shrink_segments({"transcript_path": p})
        finally:
            builtins.open = real_open
        self.assertEqual(len(segs), 1)

    def test_deployed_shims_calling_the_old_name_still_render(self):
        # an installed shim keeps calling restart_segments until the next install
        p = _sparse(self.dir, 120 * MIB)
        self.assertIn("shrink 120M", tw.restart_segments({"transcript_path": p})[0])

    def test_the_shim_calls_the_new_name(self):
        self.assertIn("transcript_weight.shrink_segments(d)", airuleset.CAVEMAN_SHIM_CONTENT)


class ShimRendersIt(unittest.TestCase):
    """End to end through the REAL rendered statusline shim."""

    def _render(self, payload, content=None):
        with tempfile.TemporaryDirectory() as home:
            shim = os.path.join(home, "shim.sh")
            with open(shim, "w") as fh:
                fh.write(content or airuleset.CAVEMAN_SHIM_CONTENT)
            env = dict(os.environ)
            env["HOME"] = home
            env.pop("TMUX_PANE", None)
            return subprocess.run(["bash", shim], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env)

    def test_heavy_session_footer_carries_shrink(self):
        with tempfile.TemporaryDirectory() as d:
            p = _sparse(d, 128 * MIB)
            r = self._render({"workspace": {"current_dir": d},
                              "transcript_path": p})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shrink 128M", r.stdout)
        self.assertNotIn("restart", r.stdout)

    def test_light_session_footer_has_no_shrink(self):
        with tempfile.TemporaryDirectory() as d:
            p = _sparse(d, 99 * MIB)
            r = self._render({"workspace": {"current_dir": d},
                              "transcript_path": p})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("shrink", r.stdout)

    def test_missing_module_never_blanks_the_rest_of_the_footer(self):
        # review 2: the optional segment's import must not share the statusbar
        # import guard, or one missing file drops every other segment.
        content = airuleset.CAVEMAN_SHIM_CONTENT
        self.assertEqual(content.count("import transcript_weight"), 1)
        broken = content.replace("import transcript_weight",
                                 "import transcript_weight_gone_1301")
        payload = {"workspace": {"current_dir": "/tmp/nowhere-1301"},
                   "model": {"id": "claude-opus-5-5"},
                   "context_window": {"current_usage": {
                       "cache_read_input_tokens": 570000,
                       "cache_creation_input_tokens": 0}}}
        ok = self._render(payload)
        self.assertIn("ctx 570K", ok.stdout)          # the control renders it
        r = self._render(payload, content=broken)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ctx 570K", r.stdout)


if __name__ == "__main__":
    unittest.main()
