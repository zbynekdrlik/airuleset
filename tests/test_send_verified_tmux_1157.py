"""#1157 — the long wrapped partition-audit nudge verifies and submits on a
REAL private tmux server.

The pane runs `tests/_cc_box_pane.py`, which draws a Claude-Code-shaped input
box (the render measured live on CC 2.1.281, see its docstring). The watchdog
primitive `send_verified` drives it with REAL `send-keys` and reads it with
REAL `capture-pane`; only the socket differs. Every tmux call carries
`-L <uuid> -f /dev/null` (tests/test_tmux_test_isolation_lock.py, and the
#1124 lesson: without `-f` the owner's real ~/.tmux.conf hooks would load), and
the server is killed in tearDown. TMUX/TMUX_PANE are stripped so nothing can
fall back to the live server.

Box-bound: the hermetic CI container has no tmux, so this file is listed in
.github/box-bound-tests.txt. A missing tmux binary FAILS here and never skips.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from test_send_verified_undo_1157 import partition_batch_text  # noqa: E402

PANE_PROG = Path(__file__).resolve().parent / "_cc_box_pane.py"


class RealPrivateTmuxBox(unittest.TestCase):
    WIDTH = 176

    def setUp(self):
        self.assertIsNotNone(shutil.which("tmux"),
                             "tmux binary required (box-bound test)")
        self.sock = "i1157-%s" % uuid.uuid4().hex[:12]
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tpath = Path(tmp.name) / "sess.jsonl"
        self.tpath.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
        self.addCleanup(self._tmux, "kill-server")

    def _tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, "-f", "/dev/null", *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def _run(self, argv, timeout=8):
        # The production argv, routed to the private socket.
        self.assertEqual(argv[0], "tmux")
        r = self._tmux(*argv[1:])
        return r.stdout if r.returncode == 0 else ""

    def _start(self, max_rows, width=None):
        cmd = "%s %s --max-rows %d --transcript %s" % (
            sys.executable, PANE_PROG, max_rows, self.tpath)
        r = self._tmux("new-session", "-d", "-s", "t", "-x",
                       str(width or self.WIDTH), "-y", "30", cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if wd._input_line_text(wd.capture_pane("t:0.0", self._run)) == "":
                return "t:0.0"
            time.sleep(0.1)
        self.fail("box never rendered: %r" % wd.capture_pane("t:0.0", self._run))

    def _submitted(self):
        turns = [json.loads(ln) for ln in self.tpath.read_text().splitlines()]
        return [t["message"]["content"] for t in turns if t["type"] == "user"]

    def test_scrolled_box_renders_the_live_shape(self):
        # The fixture is only worth something if it reproduces the incident's
        # render: typed text scrolled so the glyph row is mid-payload.
        pid = self._start(max_rows=3)
        text = partition_batch_text()
        self._run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
        deadline = time.monotonic() + 5
        head = None
        while time.monotonic() < deadline:
            head = wd._input_box_head_text(wd.capture_pane(pid, self._run))
            if head and not head.startswith("nudge:"):
                break
            time.sleep(0.1)
        self.assertTrue(head)
        self.assertFalse(head.startswith("nudge:"), head)
        self.assertIn(" ".join(head.split()), " ".join(text.split()))
        self.assertTrue(text.endswith(
            wd._input_line_text(wd.capture_pane(pid, self._run))))

    def test_long_wrapped_nudge_submits_on_a_scrolled_box(self):
        pid = self._start(max_rows=3)
        text = partition_batch_text()
        logs = []
        res = wd.send_verified(pid, text, self._run, self.tpath,
                               sleep_fn=time.sleep, logs=logs,
                               nudge="partition-audit")
        self.assertTrue(res, logs)
        self.assertEqual(self._submitted(), [text], logs)
        self.assertEqual(wd._input_line_text(wd.capture_pane(pid, self._run)), "")

    def test_nudge_with_a_hard_broken_long_token_submits(self):
        # A token longer than a row (a URL) is hard-broken mid-token, so the
        # row-joined reconstruction carries a spurious space: the scrolled
        # acceptance must compare whitespace-insensitively (provenance=True)
        # and the per-chunk verify must accept a row break with no space.
        pid = self._start(max_rows=3, width=60)
        url = ("https://github.com/zbynekdrlik/odoo-erp/issues/7887"
               "#issuecomment-5839297264-and-a-long-anchor-tail")
        text = ("nudge: [gk-request] gk-request backstop: odoo-erp caka na "
                "supervizora, pozri tiket a vybav ziadost " + url
                + " a potom pokracuj v slucke, nic nerob naslepo.")
        self.assertGreater(len(url), 60 - 4)
        logs = []
        res = wd.send_verified(pid, text, self._run, self.tpath,
                               sleep_fn=time.sleep, logs=logs,
                               nudge="gk-request")
        self.assertTrue(res, logs)
        self.assertEqual(self._submitted(), [text], logs)

    def test_long_wrapped_nudge_submits_on_an_unscrolled_box(self):
        # CONTROL: the 4-row render that fits (a tall pane) — verified before
        # and after #1157.
        pid = self._start(max_rows=0)
        text = partition_batch_text()
        logs = []
        res = wd.send_verified(pid, text, self._run, self.tpath,
                               sleep_fn=time.sleep, logs=logs,
                               nudge="partition-audit")
        self.assertTrue(res, logs)
        self.assertEqual(self._submitted(), [text], logs)


if __name__ == "__main__":
    unittest.main()
