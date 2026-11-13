"""#1319 -- tmux treats a `;` at the END of a `send-keys` argument as a command
separator and drops it. `_type_literal` types long text in 120-char chunks, so a
chunk that happens to end in `;` lost that byte. The gk review `/goal` variant has
such a chunk (`... do 1 h;`), the head/scroll verify then could not prove the box
was ours, no Enter was sent, and the owner's /autopilot never armed on gk.

Two layers: the argv `watchdog.keys` builds (hermetic), and what a REAL tmux 3.x
server types from that argv (private `-L` socket, box-bound like #1124: a missing
tmux binary FAILS, never skips).
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import watchdog as wd
from watchdog import stash

PID = "%9"


class _Rec:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, *a, **k):
        self.calls.append(list(argv))
        return ""


def _typed_from_argv(calls):
    """Decode what tmux would type from recorded send-keys argvs: a `-l --`
    literal argument (minus a trailing `;`, which tmux eats) and `-H` hex."""
    out = []
    for argv in calls:
        keys = argv[argv.index(PID) + 1:]
        if keys[:2] == ["-l", "--"]:
            arg = keys[2]
            out.append(arg[:-1] if arg.endswith(";") and not arg.endswith("\\;") else
                       arg[:-2] + ";" if arg.endswith("\\;") else arg)
        elif keys[:1] == ["-H"]:
            out.append(bytes(int(h, 16) for h in keys[1:]).decode())
    return "".join(out)


class TestKeysArgv(unittest.TestCase):
    def _send(self, text):
        rec = _Rec()
        ok = wd.keys(PID, "-l", "--", text, kind="goal", nudge="goal-arm",
                     user_authored=True, run=rec)
        self.assertTrue(ok)
        return rec.calls

    def test_no_literal_argument_ends_with_semicolon(self):
        for text in ("do 1 h;", "a;;", ";", "x\\;"):
            for argv in self._send(text):
                keys = argv[argv.index(PID) + 1:]
                if keys[:2] == ["-l", "--"]:
                    self.assertFalse(keys[2].endswith(";"), (text, argv))

    def test_typed_bytes_equal_the_text(self):
        for text in ("do 1 h;", "a;;", ";", "x\\;", "plain", "mid;dle"):
            self.assertEqual(_typed_from_argv(self._send(text)), text, text)

    def test_chunked_type_keeps_every_semicolon(self):
        text = ("x" * (stash.GOAL_TYPE_CHUNK_SIZE - 1) + ";") * 3
        rec = _Rec()
        self.assertTrue(stash._type_literal(PID, rec, text, sleep_fn=lambda s: None,
                                            kind="goal", nudge="goal-arm",
                                            user_authored=True))
        self.assertEqual(_typed_from_argv(rec.calls), text)


class TestRealTmux(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("tmux"), "tmux binary required (box-bound test)")
        self.sock = "ar1319-%s" % uuid.uuid4().hex[:12]
        self.env = dict(os.environ, HOME=str(Path(tempfile.mkdtemp())))
        self.env.pop("TMUX", None)
        self._tmux("new-session", "-d", "-s", "t", "-x", "200", "-y", "5", "cat")

    def tearDown(self):
        self._tmux("kill-server")

    def _tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True,
                              text=True, timeout=10, env=self.env)

    def _runner(self, argv, *a, **k):
        self.assertEqual(argv[0], "tmux")
        argv = [x if x != PID else "t" for x in argv]
        return self._tmux(*argv[1:]).stdout

    def test_chunk_ending_in_semicolon_lands_literally(self):
        text = "abc h;" + "|x\\;" + "|;;"
        for part in ("abc h;", "|x\\;", "|;;"):
            self.assertTrue(wd.keys(PID, "-l", "--", part, kind="goal", nudge="goal-arm",
                                    user_authored=True, run=self._runner))
        time.sleep(0.3)
        shown = self._tmux("capture-pane", "-p", "-t", "t").stdout.splitlines()[0]
        self.assertEqual(shown.rstrip(), text)


if __name__ == "__main__":
    unittest.main()
