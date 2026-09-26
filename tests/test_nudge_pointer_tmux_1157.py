"""#1157 slice 3 — the incident nudge is delivered as ONE row on a REAL private
tmux server at the incident geometry (176x12), and a narrow 80-column pane
still gets one row.

Live on gk (25.9.2026 18:26, window gk-infra): the ~720-char partition-audit
batch nudge wrapped to 5 rows, the 12-row pane showed only 3 of them (CC
scrolls the box), the read-back failed and the text sat in the owner's box for
4 h. Slice 3 types only `nudge: [<kind>] <headline> — celý text: <file>` and
writes the full text to that file first.

The pane runs `tests/_cc_box_pane.py` (the render measured live on CC 2.1.281,
the box capped at 3 visible rows like the live 12-row pane). Every tmux call
carries `-L <uuid> -f /dev/null`; the server is killed in tearDown. Box-bound
(listed in .github/box-bound-tests.txt): a missing tmux FAILS here.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock as m
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from _cc_box_pane import wrap_rows  # noqa: E402
from test_send_verified_undo_1157 import partition_batch_text  # noqa: E402

PANE_PROG = Path(__file__).resolve().parent / "_cc_box_pane.py"


class PointerRowOnRealTmux(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(shutil.which("tmux"),
                             "tmux binary required (box-bound test)")
        self.sock = "i1157s3-%s" % uuid.uuid4().hex[:12]
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = m.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AIRULESET_NUDGE_FILE_DIR", None)   # the real default dir
        self.tpath = self.home / "sess.jsonl"
        self.tpath.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
        self.literals, self.max_rows_seen = [], 0
        self.addCleanup(self._unlink_socket)       # after kill-server (LIFO)
        self.addCleanup(self._tmux, "kill-server")

    def _unlink_socket(self):
        # kill-server leaves the socket file behind (#548: never litter /tmp)
        sock = Path(os.environ.get("TMUX_TMPDIR", "/tmp"),
                    "tmux-%d" % os.getuid(), self.sock)
        if sock.is_socket():
            sock.unlink()

    def _tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, "-f", "/dev/null", *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def _run(self, argv, timeout=8):
        # the production argv routed to the private socket; every literal type
        # and the tallest box any capture showed are recorded
        self.assertEqual(argv[0], "tmux")
        if "send-keys" in argv and "-l" in argv:
            self.literals.append(argv[-1])
        r = self._tmux(*argv[1:])
        out = r.stdout if r.returncode == 0 else ""
        if "capture-pane" in argv:
            self.max_rows_seen = max(self.max_rows_seen,
                                     len(wd._input_box_rows_raw(out)))
        return out

    def _start(self, width, height, max_rows):
        cmd = [sys.executable, str(PANE_PROG), "--transcript", str(self.tpath),
               "--max-rows", str(max_rows)]
        r = self._tmux("new-session", "-d", "-s", "t", "-x", str(width), "-y",
                       str(height), *cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "❯" in self._tmux("capture-pane", "-p", "-t", "t:0.0").stdout:
                return "t:0.0"
            time.sleep(0.1)
        self.fail("the box never rendered")

    def _submitted(self):
        rows = [json.loads(ln) for ln in self.tpath.read_text().splitlines()]
        return [r["message"]["content"] for r in rows if r["type"] == "user"]

    def _deliver(self, width, height, max_rows):
        text = partition_batch_text()
        pid = self._start(width, height, max_rows)
        logs = []
        res = wd.send_verified(pid, text, self._run, self.tpath,
                               sleep_fn=time.sleep, logs=logs,
                               nudge="partition-audit", state={},
                               now=time.time())
        return text, res, logs

    def _assert_one_row_delivery(self, text, res, logs, width):
        from watchdog import nudge_file as nf
        self.assertTrue(res, logs)
        subs = self._submitted()
        self.assertEqual(len(subs), 1, subs)
        line = subs[0]
        self.assertEqual(self.literals, [line], "the pane got only the pointer")
        self.assertEqual(len(wrap_rows(line, width)), 1, line)
        self.assertLessEqual(nf.cells(line), min(100, width - 6), line)
        self.assertEqual(self.max_rows_seen, 1, "never more than one box row")
        files = list((self.home / ".claude" / "nudges").glob("*.md"))
        self.assertEqual(len(files), 1, files)
        self.assertTrue(line.endswith("~/.claude/nudges/" + files[0].name), line)
        self.assertEqual(files[0].read_bytes(), text.encode("utf-8"))

    def test_incident_geometry_176x12_delivers_one_row_that_submits(self):
        text, res, logs = self._deliver(176, 12, 3)
        self.assertGreater(len(text), 700)
        self.assertGreater(len(wrap_rows(text, 176)), 3,
                           "the paragraph itself would have scrolled")
        self._assert_one_row_delivery(text, res, logs, 176)
        self.assertTrue(any("pointer" in ln and "pane width 176" in ln
                            for ln in logs), logs)

    def test_narrow_80_column_pane_still_gets_one_row(self):
        text, res, logs = self._deliver(80, 12, 3)
        self._assert_one_row_delivery(text, res, logs, 80)


if __name__ == "__main__":
    unittest.main()
