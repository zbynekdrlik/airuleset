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
import time
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

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
# #442 — an ARMED pane whose input box holds an at-rest draft: the lane
# nudge must deliver into it via deliver_with_stash, never "skip draft".
GOAL_ARMED_DRAFT_CAP = ("● Hotovo.\n❯ rozpisany draft\n"
                        "  ctx ███░  caveman:lite  ◎ /goal active\n")
# #442 re-fix 2 — an ARMED, idle pane whose agent strip shows LIVE background
# workers (`◯ ...` rows), exactly like the reopen-2 live box (2 workers visible).
# `_pane_has_bg_agent` returns True here, while the box stays at a free idle `❯`
# prompt (armed, deliverable) — so the count-based fill-the-cap nudge must still
# fire, proving the removal of the old `_pane_has_bg_agent` early-skip.
GOAL_ARMED_STRIP_CAP = ("● Predošlá práca hotová.\n❯ \n"
                        "  ctx ███░  caveman:lite  ◎ /goal active\n"
                        "  ● main\n"
                        "  ◯ autopilot-worker  Working on #500\n"
                        "  ◯ autopilot-worker  Awaiting CI on ci-complete gate\n")


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
                model_type=False, enters_swallowed=0, transcript_path=None,
                initial_box="", wrap_width=None, arm_on_submit=True):
        self.panes = panes
        # #720 — arm the modelled pane after a `/goal <cond>` submit (default).
        # `False` models the #720 incident: the /goal was submitted but CC read
        # it as a plain prompt, so the pane NEVER shows the `◎ /goal` footer.
        self.arm_on_submit = arm_on_submit
        self.captured = captured
        self.in_mode = in_mode
        self.cap_seq = list(cap_seq)
        self._cap_calls = 0
        self.sent = []
        # #720 — model CC arming/clearing a goal on a `/goal` submit so a
        # POST-submit `pane_goal_armed` read is truthful (what `_send_goal_
        # verified`'s #720 arm-confirm polls): a `/goal <cond>` submit sets it
        # True, a `/goal clear` submit False, a plain nudge leaves it untouched.
        self._armed = False
        self.model_type = model_type
        # #490 — model the two facts `send_verified` verifies against: an
        # ACCEPTED submit appends a real `user` turn to `transcript_path` and
        # clears the box; a SWALLOWED submit (`enters_swallowed` > 0) keeps the
        # box text and writes NOTHING (the live lane-nudge regression). A
        # `BSpace` run trims the box so the restore/undo path reaches a
        # genuinely-bare box.
        self.enters_swallowed = enters_swallowed
        self.transcript_path = transcript_path
        # #501 — pre-seed the input box with an at-rest draft so a test can
        # drive the SUBMIT-IN-PLACE path (`submit_own_draft_verified`) end to
        # end: the box already holds a swallowed OWN nudge before any watchdog
        # keystroke, and `capture-pane` renders it via the SAME `❯ ` bare-line
        # model. Default "" keeps every pre-#501 test byte-identical.
        self.box = initial_box
        # #501 -- when set, `capture-pane` renders the box the way a REAL pane
        # does: greedy word-wrap at `wrap_width` cols into a bordered,
        # multi-row box (`❯`+NBSP on the head row, continuation rows indented),
        # so `_find_input_box` reports `wrapped=True` and head/tail DIFFER. This
        # is the production shape a 289-720-char own nudge always takes -- the
        # unwrapped single-line render (default None) is a state that CANNOT
        # occur for a real nudge, so the wrapped path is what actually proves
        # `_input_box_head_text`-based recognition. Mirrors
        # `tests/test_wrapped_draft.py::render_box`.
        self.wrap_width = wrap_width
        self._bare_line = None
        if model_type:
            for ln in captured.splitlines():
                if ln.strip() == "❯":
                    self._bare_line = ln
                    break

    def _append_user_turn(self, text):
        """Model CC accepting a submit: append the real `user` turn it writes
        the instant it ACCEPTS a submit (before any response exists) — the
        structured signal `send_verified` polls for (`transcript_last_error`'s
        own documented invariant)."""
        from datetime import datetime, timezone
        iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entry = {"type": "user", "message": {"content": text},
                 "timestamp": iso}
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _render(self):
        if not self.model_type or self._bare_line is None:
            return self.captured
        if not self.box:
            return self._with_arm(self.captured)   # #720 post-submit armed footer
        if self.wrap_width:
            return self._render_wrapped()
        new_line = self._bare_line.replace("❯", "❯ " + self.box, 1)
        return self.captured.replace(self._bare_line, new_line, 1)

    def _with_arm(self, base):
        """#720 — append CC's `◎ /goal active` footer glyph to the ctx line when
        `self._armed` (a `/goal <cond>` was submitted), so a post-submit
        `pane_goal_armed` read is truthful. `arm_on_submit`-off / a `/goal clear`
        submit leave the flag False — the 'submitted but never armed' shape the
        #720 arm-confirm guards against."""
        if not self._armed or "◎ /goal" in base:
            return base
        lines = base.split("\n")
        for i, ln in enumerate(lines):
            if "ctx" in ln:
                lines[i] = ln + "  ◎ /goal active"
                break
        return "\n".join(lines)

    def _render_wrapped(self):
        """#501 — render `self.box` the way a REAL wrapped input box captures:
        greedy word-wrap at `self.wrap_width`, `❯`+NBSP on the head row,
        continuation rows indented by two cols, bordered above and below (the
        `tests/test_wrapped_draft.py::render_box` shape). Keeps the armed
        `◎ /goal active` marker on the ctx line below the box so
        `pane_goal_armed` still reads True. `_find_input_box` reports
        `wrapped=True` and head/tail DIFFER — the shape the head-vs-tail
        recognition (#501) is actually about."""
        w = self.wrap_width
        rows, cur, prefix = [], "", "❯\xa0"
        for word in self.box.split(" "):
            cand = (cur + " " + word) if cur else word
            if len(prefix) + len(cand) > w:
                rows.append(prefix + cur)
                cur, prefix = word, "  "
            else:
                cur = cand
        rows.append(prefix + cur)
        ctx = "  ctx ███░  caveman:lite  ◎ /goal active"
        return "\n".join(
            ["● Hotovo.", "", "─" * 60] + rows + ["─" * 60, ctx]) + "\n"

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
                keys = argv[4:]  # after "-t", pid
                if "-l" in argv:
                    self.box += argv[-1]
                elif argv[-1] == "Enter":
                    if self.enters_swallowed > 0:
                        # a swallowed submit: the box KEEPS its text and the
                        # transcript gains NOTHING (the #490 live incident).
                        self.enters_swallowed -= 1
                    else:
                        submitted = self.box            # #720 — before clearing
                        if self.box and self.transcript_path is not None:
                            self._append_user_turn(self.box)
                        self.box = ""
                        # #720 — model CC arming (`/goal <cond>`) / clearing
                        # (`/goal clear`) a goal on this submit, unless
                        # `arm_on_submit` is off (the 'read as a plain prompt,
                        # never armed' shape); a non-`/goal` submit is untouched.
                        s = submitted.strip()
                        if s == "/goal clear":
                            self._armed = False
                        elif s.startswith("/goal ") and self.arm_on_submit:
                            self._armed = True
                elif keys and all(k == "BSpace" for k in keys):
                    n = len(keys)
                    self.box = self.box[:-n] if n < len(self.box) else ""
                # Escape / C-s: no-op for the box in this model.
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


class _SwallowFirstCharFake(DeliverGoalFakeTmux):
    """A DeliverGoalFakeTmux whose FIRST-byte of a FRESH type is SWALLOWED,
    `swallow_budget` times. A `send-keys -l` burst landing while the box is
    empty (a fresh type's first chunk) has its opening character dropped — the
    live first-byte race (#670) — so the box renders "ane-check…" not
    "lane-check…". Later chunks and re-types land intact once the budget is
    spent (modelling an INTERMITTENT race that a retry escapes). Shared home
    (#720): the goal-arm bare-box send now routes through the SAME head-inclusive
    verified typing primitive `send_verified` uses, so both test files drive this
    one fake."""

    def __init__(self, *a, swallow_budget=1, **kw):
        super().__init__(*a, **kw)
        self.swallow_budget = swallow_budget

    def __call__(self, argv, timeout=8):
        if self.model_type and "send-keys" in " ".join(argv) and "-l" in argv:
            self.sent.append(argv)
            chunk = argv[-1]
            if self.box == "" and self.swallow_budget > 0 and chunk:
                self.swallow_budget -= 1
                self.box += chunk[1:]        # <-- first char dropped
            else:
                self.box += chunk
            return ""
        return super().__call__(argv, timeout)


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

