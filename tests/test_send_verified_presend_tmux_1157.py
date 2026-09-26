"""#1157 slice 2 — our own stale machine text is cleared at the pre-send gate,
on a REAL private tmux server.

Live on gk after v0.1.472 (26.9.2026 01:48-01:54): the 18:26 partition-audit
batch nudge (`nudge: [partition-audit] stuck-check: …`, 5 wrapped rows) still
sat in the idle gk-infra box, typed before slice 1 existed, so no
`stranded_own` record and no janitor watch named it. Every sweep logged
`send-verified abort: box not bare pre-send` and delivered nothing. The fix:
at that gate, an IDLE pane whose READABLE box provably holds our own text is
cleared (logged `pre-send: cleared stale own machine text (NN chars)`), the
call types nothing, and the next call delivers. Anything else is untouched.

The pane runs `tests/_cc_box_pane.py` (the render measured live on CC 2.1.281);
the leftover / draft is put in the box with a REAL `send-keys -l`. Every tmux
call carries `-L <uuid> -f /dev/null` and the server is killed in tearDown.
Box-bound (listed in .github/box-bound-tests.txt): a missing tmux FAILS here.
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
from watchdog import nudge_file  # noqa: E402
from watchdog import nudge_gate  # noqa: E402
from watchdog import ops_wait_recheck as owr  # noqa: E402
from _cc_box_pane import wrap_rows  # noqa: E402
from test_send_verified_undo_1157 import partition_batch_text  # noqa: E402

PANE_PROG = Path(__file__).resolve().parent / "_cc_box_pane.py"
WIDTH = 170          # the incident text wraps into exactly 5 rows here
SPINNER = "✻ Pondering… (12s · esc to interrupt)"
OWNER_DRAFT = ("Pozri sa este raz na ten release, prosim, a skontroluj ci "
               "gatekeeper naozaj zavrel vsetky tikety z vcerajska, lebo mne "
               "to v paticke stale ukazuje I 3 a neviem preco. Potom mi napis "
               "kratke zhrnutie, co je hotove a co este caka na klienta.")


def _next_batch_text():
    """A DIFFERENT, later batch (other counts) — what the next sweep sends."""
    w = [{"number": n, "labels": ["ops-wait"]} for n in range(100, 105)]
    t = owr._nudge_text(3, w, deploy_window=[], release_landed=[])
    return nudge_gate.compose_batch([("partition-audit", t)],
                                    max_chars=nudge_gate.BATCH_MAX_CHARS)[0]


class PreSendOwnLeftoverOnRealTmux(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(shutil.which("tmux"),
                             "tmux binary required (box-bound test)")
        self.sock = "i1157s2-%s" % uuid.uuid4().hex[:12]
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tpath = Path(tmp.name) / "sess.jsonl"
        self.tpath.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
        self.keys_sent = []
        self.addCleanup(self._tmux, "kill-server")

    def _tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, "-f", "/dev/null", *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def _run(self, argv, timeout=8):
        # The production argv, routed to the private socket; every keystroke
        # the watchdog fires is recorded.
        self.assertEqual(argv[0], "tmux")
        if "send-keys" in argv:
            self.keys_sent.append(list(argv))
        r = self._tmux(*argv[1:])
        return r.stdout if r.returncode == 0 else ""

    def _cap(self):
        return wd.capture_pane("t:0.0", self._run, lines=40)

    def _start_with_box(self, text, *extra):
        cmd = [sys.executable, str(PANE_PROG), "--transcript", str(self.tpath),
               *extra]
        r = self._tmux("new-session", "-d", "-s", "t", "-x", str(WIDTH), "-y",
                       "30", *cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "❯" in self._tmux("capture-pane", "-p", "-t", "t:0.0").stdout:
                break
            time.sleep(0.1)
        # the leftover / draft arrives with a REAL literal type (not ours to
        # count: it models the earlier sweep / the owner)
        self._tmux("send-keys", "-t", "t:0.0", "-l", "--", text)
        want = text.split()[-1]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if want in self._tmux("capture-pane", "-p", "-t", "t:0.0").stdout:
                return "t:0.0"
            time.sleep(0.1)
        self.fail("box never showed the text: %r" % self._cap())

    def _submitted(self):
        turns = [json.loads(ln) for ln in self.tpath.read_text().splitlines()]
        return [t["message"]["content"] for t in turns if t["type"] == "user"]

    def _assert_delivered(self, text, logs):
        # #1157 s3: the next sweep types ONE pointer row; its file holds `text`
        subs = self._submitted()
        self.assertEqual(len(subs), 1, logs)
        self.assertTrue(nudge_file.is_pointer_line(subs[0], require_file=True),
                        subs)
        self.assertEqual(nudge_file.expand(subs[0]), text, logs)

    def _send(self, pid, text, state):
        logs = []
        res = wd.send_verified(pid, text, self._run, self.tpath,
                               sleep_fn=time.sleep, logs=logs,
                               nudge="partition-audit", state=state,
                               now=time.time())
        return res, logs

    def test_idle_own_leftover_is_cleared_at_the_gate_then_delivered(self):
        leftover = partition_batch_text()
        self.assertTrue(leftover.startswith(
            "nudge: [partition-audit] stuck-check: "))
        self.assertEqual(len(wrap_rows(leftover, WIDTH)), 5)
        pid = self._start_with_box(leftover)
        self.assertEqual(len(wd._input_box_rows_raw(self._cap())), 5)
        state = {}       # no stranded record, no janitor watch: the legacy case
        res, logs = self._send(pid, _next_batch_text(), state)
        self.assertFalse(res, logs)
        self.assertEqual(getattr(res, "kind", None), "not-typed", logs)
        self.assertEqual(wd._input_line_text(self._cap()), "",
                         "the stale own text must be cleared: %r" % logs)
        self.assertTrue(any("pre-send: cleared stale own machine text (%d chars)"
                            % len(" ".join(leftover.split())) in ln
                            for ln in logs), logs)
        self.assertEqual(self._submitted(), [], "never typed in the same call")
        self.assertFalse(any("-l" in a for a in self.keys_sent),
                         "no literal type in the clearing call: %r"
                         % self.keys_sent)
        # the NEXT call (the next sweep) delivers into the now-bare box
        nxt = _next_batch_text()
        res2, logs2 = self._send(pid, nxt, state)
        self.assertTrue(res2, logs2)
        self._assert_delivered(nxt, logs2)

    def test_scrolled_recorded_leftover_is_cleared_then_delivered(self):
        # a short pane: CC shows 3 of the 5 rows. Slice 1 recorded the text.
        leftover = partition_batch_text()
        pid = self._start_with_box(leftover, "--max-rows", "3")
        self.assertFalse(wd._input_box_head_text(self._cap()).startswith(
            "nudge:"), "the fixture must be scrolled")
        state = {"stranded_own": {pid: {"ts": time.time() - 60,
                                        "typed": leftover}}}
        res, logs = self._send(pid, _next_batch_text(), state)
        self.assertEqual(wd._input_line_text(self._cap()), "", logs)
        self.assertNotIn(pid, state.get("box_not_own", {}), logs)
        nxt = _next_batch_text()
        res2, logs2 = self._send(pid, nxt, state)
        self.assertTrue(res2, logs2)
        self._assert_delivered(nxt, logs2)

    def test_owner_draft_is_untouched_with_the_truthful_verb(self):
        pid = self._start_with_box(OWNER_DRAFT)
        before = wd._box_norm_from_capture(self._cap())
        res, logs = self._send(pid, _next_batch_text(), {})
        self.assertFalse(res, logs)
        self.assertEqual(self.keys_sent, [], "no keystroke into an owner draft")
        self.assertEqual(wd._box_norm_from_capture(self._cap()), before)
        self.assertTrue(any("box holds a non-machine draft — held" in ln
                            for ln in logs), logs)
        self.assertFalse(any("cleared" in ln for ln in logs), logs)

    def test_stray_human_char_before_our_nudge_is_untouched(self):
        # mixed text: the #852 shape (an owner's `s` raced in front of our
        # nudge) is not a box that provably holds only our text
        pid = self._start_with_box("s" + partition_batch_text())
        before = wd._box_norm_from_capture(self._cap())
        res, logs = self._send(pid, _next_batch_text(), {})
        self.assertFalse(res, logs)
        self.assertEqual(self.keys_sent, [])
        self.assertEqual(wd._box_norm_from_capture(self._cap()), before)
        self.assertTrue(any("box holds a non-machine draft — held" in ln
                            for ln in logs), logs)

    def test_busy_pane_is_untouched(self):
        pid = self._start_with_box(partition_batch_text(), "--above", SPINNER)
        before = wd._box_norm_from_capture(self._cap())
        res, logs = self._send(pid, _next_batch_text(), {})
        self.assertFalse(res, logs)
        self.assertEqual(self.keys_sent, [], "never an Escape into a running turn")
        self.assertEqual(wd._box_norm_from_capture(self._cap()), before)
        self.assertTrue(any("turn running under the box — held" in ln
                            for ln in logs), logs)

    def test_unreadable_box_is_untouched(self):
        # the live h=10 render: the box is taller than the pane, its bottom
        # rule is off-screen, so the box cannot be read at all
        pid = self._start_with_box(partition_batch_text(), "--cut-bottom")
        self.assertIsNone(wd._input_line_text(self._cap()))
        res, logs = self._send(pid, _next_batch_text(), {})
        self.assertFalse(res, logs)
        self.assertEqual(self.keys_sent, [], "no keystroke into an unreadable box")
        self.assertIn("nudge: [partition-audit]",
                      self._tmux("capture-pane", "-p", "-t", pid).stdout)
        self.assertTrue(any("box unreadable — held" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
