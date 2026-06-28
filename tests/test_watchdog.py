"""Tests for the api-watchdog text-emitted tool-call stall detector (job 4a).

A tool call the model emits as TEXT (`<invoke name="...">…</invoke>` inside an
assistant text block) never runs → the turn ends → the session sits idle while
still LOOKING like it was about to act. Job 4a detects this from the transcript
shape and nudges immediately. These tests lock the detector's precision (it must
NOT fire on a meta-conversation that merely discusses `<invoke>` markup — like this
very repo) and the run_once wiring.
"""

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import watchdog as wd


def _write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _assistant(text, **extra):
    e = {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": text}]}}
    e.update(extra)
    return e


def _assistant_tooluse(name="Read"):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def _system():
    return {"type": "system", "content": ""}


# the exact shape that stalled camera-box (PR #305): garbled lead-in + a Read call
# rendered as literal text, then trailing hook/system entries.
CAMERA_BOX_TEXT = ('court <invoke name="Read"><parameter name="file_path">'
                   '/tmp/x/tasks/b0kqzh3do.output</parameter></invoke>')


class TextToolcallStallDetector(unittest.TestCase):
    def _stall(self, entries):
        with TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            _write_jsonl(p, entries)
            return wd.transcript_text_toolcall_stall(p)

    def test_camera_box_incident_is_a_stall(self):
        self.assertTrue(self._stall([
            _assistant("Earlier normal turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(), _system(),          # hook noise after the broken turn
        ]))

    def test_antml_prefixed_invoke_is_a_stall(self):
        txt = ('Pozriem výstup. <invoke name="Bash">'
               '<parameter name="command">ls</parameter></invoke>')
        self.assertTrue(self._stall([_assistant(txt), _system()]))

    def test_unclosed_invoke_tail_is_a_stall(self):
        # turn died mid-emit — opening tag with no close, still the trailing content
        self.assertTrue(self._stall([_assistant('Reading it now <invoke name="Read">')]))

    def test_meta_discussion_does_not_match(self):
        # this repo literally documents <invoke> — markup buried mid-prose, normal
        # marker at the tail → MUST NOT fire (the key false-positive guard).
        txt = ("The failure mode: the model emits `<invoke name=\"Read\">` as text. "
               + ("Explanation continues. " * 30)
               + "\n\n✅ DONE: vysvetlené, žiadna akcia.")
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_real_tool_use_is_not_a_stall(self):
        # a parsed tool_use block means the harness DID call the tool
        self.assertFalse(self._stall([_assistant("Earlier"), _assistant_tooluse("Read")]))

    def test_api_error_is_not_a_textcall_stall(self):
        # job 1 owns api errors, even if the text happens to contain <invoke
        self.assertFalse(self._stall([
            _assistant("API Error: overloaded <invoke name=\"Read\">",
                       isApiErrorMessage=True)]))

    def test_progressed_user_entry_is_not_a_stall(self):
        # a user / tool_result entry AFTER the text-invoke → conversation moved on
        self.assertFalse(self._stall([
            _assistant(CAMERA_BOX_TEXT),
            {"type": "user", "message": {"role": "user", "content": "ok"}},
        ]))

    def test_normal_marker_turn_is_not_a_stall(self):
        self.assertFalse(self._stall([_assistant("Done.\n\n⏳ WORKING: CI beží.")]))

    def test_synthetic_sentinel_is_skipped(self):
        # trailing "No response requested." must not mask the real broken turn before it
        self.assertTrue(self._stall([
            _assistant(CAMERA_BOX_TEXT),
            _assistant("No response requested."),
        ]))

    def test_missing_transcript_is_not_a_stall(self):
        self.assertFalse(wd.transcript_text_toolcall_stall("/no/such/file.jsonl"))


class EntryHasToolUse(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(wd._entry_has_tool_use(_assistant_tooluse()))

    def test_negative_text_only(self):
        self.assertFalse(wd._entry_has_tool_use(_assistant("just text")))

    def test_negative_garbage(self):
        self.assertFalse(wd._entry_has_tool_use({"type": "assistant"}))


class RunOnceTextcallWiring(unittest.TestCase):
    """run_once must emit a textcall-nudge for a stalled pane, and NOT for a pane
    whose last turn merely discusses <invoke>."""

    CWD = "/home/newlevel/devel/camera-box"
    PANE = "%9"

    def _run_with_transcript(self, entries, idle_seconds=600):
        """Build a temp projects dir + state, a fake tmux `run`, call run_once, return logs."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / "90bc51f3.jsonl"
        _write_jsonl(tpath, entries)
        now = time.time()
        os.utime(tpath, (now - idle_seconds, now - idle_seconds))
        state_path = Path(tmp.name) / "state.json"
        sent = []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in argv[0:2] or "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return ""                      # not waiting on the user
            if "send-keys" in j:
                sent.append(argv)
                return ""
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))
        return logs, sent

    def test_stalled_pane_gets_textcall_nudge(self):
        logs, sent = self._run_with_transcript([
            _assistant("Earlier turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(),
        ])
        self.assertTrue(any(ln.startswith("textcall-nudge#1") for ln in logs),
                        "expected a textcall-nudge log line, got: %r" % logs)
        # the nudge keystroke must actually have been sent (not dry_run)
        self.assertTrue(any("send-keys" in " ".join(a) and wd.TEXTCALL_NUDGE_TEXT in a
                            for a in sent),
                        "expected TEXTCALL_NUDGE_TEXT typed into the pane")

    def test_meta_discussion_pane_is_not_nudged(self):
        txt = ("The bug: model emits `<invoke name=\"Read\">` as text. "
               + ("More prose. " * 30) + "\n\n✅ DONE: vysvetlené.")
        logs, sent = self._run_with_transcript([_assistant(txt)])
        self.assertFalse(any("textcall" in ln for ln in logs),
                         "meta-discussion must not trigger a textcall stall: %r" % logs)
        self.assertEqual(sent, [], "no keystroke should be sent for a healthy pane")

    def test_fresh_stall_within_grace_is_not_nudged_yet(self):
        # idle below STALL_TEXTCALL_SECONDS → hold (guard against a mid-write turn)
        logs, sent = self._run_with_transcript([_assistant(CAMERA_BOX_TEXT)],
                                               idle_seconds=30)
        self.assertFalse(any("textcall-nudge" in ln for ln in logs),
                         "a sub-grace stall must not nudge yet: %r" % logs)


if __name__ == "__main__":
    unittest.main()
