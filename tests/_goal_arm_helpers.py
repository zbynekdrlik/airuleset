"""Shared fixtures for the #403 goal-callback test files.

`tests/test_goal_arm.py` (request state, template resolution, deliver_goal,
CLI) and `tests/test_goal_sweep.py` (job 9 sweep, job 20 dark-watch + lane
halves) both exercise `watchdog/goal.py` through the same fake tmux and
state-isolation fixtures — this module is their single shared home, in the
repo's own `tests/_hook_state_cleanup.py` underscore-helper shape (#404:
the size ratchet's day-one cap forced the original single 1140-line file
into this split).
"""

import json
import os
import time
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import airuleset
import watchdog as wd
from watchdog import goal

def _encode(cwd):
    return wd.encode_project_dir(cwd)


def _write_marker_transcript(base, cwd, sid, marker_text=None):
    """A minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl —
    required for pane resolution (`_find_pane_for_session` matches by
    transcript STEM, never by cwd alone)."""
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    entry = {"type": "assistant", "message": {
        "id": "msg_1", "content": marker_text or ""}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_goal_marker(base, cwd, sid, mark_text, ts_epoch=None):
    """Append a real `<local-command-stdout>Goal set: ...</local-command-
    stdout>` marker entry to the SAME transcript `scan_goal_markers` reads —
    `mark_text` is the bare `"Goal set: ..."`/`"Goal cleared: ..."` text;
    this helper wraps it in the tag `_parse_goal_marker` actually requires
    the content to START WITH (a top-level `system` entry whose `.content`
    is a plain string)."""
    from datetime import datetime, timezone
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    ts = ts_epoch if ts_epoch is not None else time.time()
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    wrapped = "<local-command-stdout>%s</local-command-stdout>" % mark_text
    entry = {"type": "system", "subtype": "local_command",
             "timestamp": iso, "content": wrapped}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return p


GOAL_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
GOAL_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
                "  ctx ███░  caveman:lite\n")
GOAL_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                   "  Tab/Arrow keys to navigate · Enter to select\n")
GOAL_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
GOAL_ARMED_CAP = ("● Predošlá práca hotová.\n❯ \n"
                  "  ctx ███░  caveman:lite  ◎ /goal active\n")


class DeliverGoalFakeTmux:
    """Fake `run` for the goal module — mirrors `test_compact.py`'s own
    `DeliverCompactFakeTmux` shape (same panes/list-panes/display-message/
    capture-pane/send-keys), plus an opt-in STATEFUL typing model
    (`model_type=True`, mirroring this repo's own established
    `model_stash=True` pattern) so `_send_goal_verified`'s real type-verify-
    submit protocol can be driven end to end instead of racing a frozen
    static capture (the exact class of bug #189 documents — a static
    capture makes every keystroke look like a no-op).

    The model keys off the ONE bare `❯ ` line in the seed `captured`
    template: `send-keys -l -- TEXT` appends to an internal box buffer,
    `Enter` submits (clears it back to bare), `Escape` is a no-op for the
    box. Every later `capture-pane` re-renders the template with that one
    line reflecting the current box state, so `_input_line_text`/
    `_typed_landed` see the SAME thing a real pane would."""

    def __init__(self, panes, captured, in_mode=False, cap_seq=(),
                model_type=False):
        self.panes = panes
        self.captured = captured
        self.in_mode = in_mode
        self.cap_seq = list(cap_seq)
        self._cap_calls = 0
        self.sent = []
        self.model_type = model_type
        self.box = ""
        self._bare_line = None
        if model_type:
            for ln in captured.splitlines():
                if ln.strip() == "❯":
                    self._bare_line = ln
                    break

    def _render(self):
        if not self.model_type or self._bare_line is None:
            return self.captured
        if not self.box:
            return self.captured
        new_line = self._bare_line.replace("❯", "❯ " + self.box, 1)
        return self.captured.replace(self._bare_line, new_line, 1)

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            # `_reconcile_candidate_panes` (goal_dark_watch/goal_lane_sweep)
            # queries WITHOUT `#{pane_pid}` (3 fields) -- `list_claude_panes`/
            # `_find_pane_for_session` query WITH it (4 fields). Reply with
            # the field count the REAL query actually asked for, or the
            # 3-field consumer's `len(parts) != 3` guard silently drops
            # every line and the caller sees zero candidate panes.
            if "#{pane_pid}" in argv[-1]:
                return "\n".join("%s\t%s\t%s\t%s" % t for t in self.panes)
            return "\n".join("%s\t%s\t%s" % (t[0], t[1], t[2]) for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            if self.model_type:
                if "-l" in argv:
                    text = argv[-1]
                    self.box += text
                elif argv[-1] == "Enter":
                    self.box = ""
                # Escape: no-op for the box in this model.
            return ""
        if "capture-pane" in j:
            if not self.cap_seq:
                return self._render()
            idx = min(self._cap_calls, len(self.cap_seq) - 1)
            self._cap_calls += 1
            return self.cap_seq[idx]
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]


def _isolate_goal_state(testcase):
    """This test's OWN isolated goal-requests/-sync-log files — the live
    systemd watchdog executes this repo's WORKING TREE every 60s, so a
    test process touching the REAL `~/.claude/goal-requests.json` would
    race a live production job (the exact discipline `test_compact.py`'s
    `_isolate_compact_state` already established for the sibling #402
    module)."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    reqp = Path(d.name) / "goal-requests-test.json"
    syncp = Path(d.name) / "goal-sync-test.log"
    for name, path in (("goal_requests_path", reqp),
                       ("goal_sync_log_path", syncp)):
        patcher = m.patch.object(goal, name, return_value=path)
        patcher.start()
        testcase.addCleanup(patcher.stop)
    return reqp, syncp


# --------------------------------------------------------------------------- #
# 1. Request store — record / load / clear, ts-set-once invariant (#400's own
#    non-refreshable age-cap anchor, mirrored here verbatim).
# --------------------------------------------------------------------------- #

