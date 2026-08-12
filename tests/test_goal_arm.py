"""The collapsed `/goal` arming callback model (#403).

Mirrors `tests/test_compact.py`'s own contract-locking shape for the
sibling #402 collapse — `watchdog/goal.py`'s own module docstring is the
single source of truth for the design this file locks:

  - Exactly ONE origin ever creates a request (`goal-arm --self`, called by
    the `/autopilot` skill's own Step 2 right after printing the `/goal`
    line) — nothing guessed from a viewport scan, no "virgin candidate"
    heuristic.
  - `deliver_goal()`'s conditions (owner kill-switch, hard non-refreshable
    age cap with a deduped ping on expiry, pane resolve, copy-mode,
    dialog-open, the #170 clear-suppression guard, a tri-state
    already-armed check, boundary classify) are evaluated in order and
    every SKIP leaves the request pending for the next sweep.
  - `goal_sweep` (job 9's new body) re-evaluates every still-pending
    request through that SAME function, clearing only on a TERMINAL word.
  - `goal_dark_watch` (job 20, half 1) NEVER types a keystroke — a
    2-sweep-debounced Discord ping only, and only for the transcript-says-
    armed/footer-says-dark mismatch; a `cleared` marker or no marker at
    all stays silent by construction.
  - `goal_lane_occupancy_nudge` (job 20, half 2) is the ONE remaining
    watchdog-INITIATED keystroke action, and is the only one of the two
    that DOES apply the recent-human-activity gate (arm delivery
    deliberately does not — see the module's own header docstring).
  - The kill-switch (`~/.claude/watchdog-disable-goal`) is honoured by
    every entry point.
"""

import json
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

class GoalRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(goal.record_goal_request(
            "sess-1", "/home/x/proj", "/goal STOP...", "full", now=1000,
            path=p, origin="self-callback"))
        d = goal.load_goal_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["text"], "/goal STOP...")
        self.assertEqual(d["sess-1"]["authority"], "full")
        self.assertEqual(d["sess-1"]["ts"], 1000)
        self.assertEqual(d["sess-1"]["origin"], "self-callback")

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(goal.record_goal_request("", "/x", "/goal x", "full", path=p))
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_ts_is_never_refreshed_across_a_re_record(self):
        # #400's own invariant, mirrored for goal-arm requests: the age-cap
        # anchor is set ONCE, never refreshed by a later re-record for the
        # same still-pending session.
        p = self._p()
        goal.record_goal_request("sess-1", "/x", "/goal a", "full", now=1000,
                                 path=p, origin="self-callback")
        goal.record_goal_request("sess-1", "/y", "/goal b", "branch-merge",
                                 now=5000, path=p, origin="self-callback")
        d = goal.load_goal_requests(p)
        self.assertEqual(d["sess-1"]["ts"], 1000)          # frozen at first-seen
        self.assertEqual(d["sess-1"]["cwd"], "/y")          # latest cwd wins
        self.assertEqual(d["sess-1"]["text"], "/goal b")    # latest text wins
        self.assertEqual(d["sess-1"]["authority"], "branch-merge")

    def test_clear_removes_one_request_only(self):
        p = self._p()
        goal.record_goal_request("sess-1", "/x", "/goal a", "full", path=p)
        goal.record_goal_request("sess-2", "/y", "/goal b", "full", path=p)
        self.assertTrue(goal.clear_goal_request("sess-1", path=p))
        d = goal.load_goal_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(goal.clear_goal_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(goal.load_goal_requests(p), {})

    def test_goal_requests_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-goal"}):
            self.assertEqual(
                goal.goal_requests_path(),
                Path("/tmp/fake-home-goal") / ".claude" / "goal-requests.json")


# --------------------------------------------------------------------------- #
# 2. Template resolution — the #403-review greedy-DOTALL regex fix, the
#    4000-char cap refusal, missing-authority handling.
# --------------------------------------------------------------------------- #

def _skill_md(*blocks):
    """Build a minimal SKILL.md carrying one or more
    `**AUTHORITY: X**` + fenced `/goal ...` blocks, in the exact shape
    `_GOAL_AUTHORITY_BLOCK_RX` anchors on."""
    parts = ["## Step 2 — Start the engine\n"]
    for authority, line in blocks:
        parts.append("**AUTHORITY: %s**\n\n```\n%s\n```\n\n" % (authority, line))
    return "\n".join(parts)


class GoalTemplateResolution(unittest.TestCase):
    def _p(self, content):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_resolves_the_matching_authority_block(self):
        p = self._p(_skill_md(
            ("full", "/goal STOP CONDITIONS full text"),
            ("branch-merge", "/goal STOP CONDITIONS branch text"),
            ("fork-no-merge", "/goal STOP CONDITIONS fork text")))
        self.assertEqual(goal.goal_template_for_authority("full", path=p),
                         "/goal STOP CONDITIONS full text")
        self.assertEqual(goal.goal_template_for_authority("branch-merge", path=p),
                         "/goal STOP CONDITIONS branch text")
        self.assertEqual(goal.goal_template_for_authority("fork-no-merge", path=p),
                         "/goal STOP CONDITIONS fork text")

    def test_three_templates_never_bleed_into_one_giant_capture(self):
        # #403-review CRITICAL: a greedy DOTALL `.+` in the goal-line group
        # backtracks from the END of the file, so the FIRST authority
        # block ("full") used to swallow all three templates as one
        # oversized capture -- every authority silently resolved to None
        # (correctly refused by the cap, but for the WRONG reason: the
        # feature was permanently non-functional). Each resolved line here
        # must be reasonably short and must NOT contain either sibling
        # template's own distinguishing word.
        p = self._p(_skill_md(
            ("full", "/goal STOP CONDITIONS full text mentions main only"),
            ("branch-merge", "/goal STOP CONDITIONS branch text mentions integration only"),
            ("fork-no-merge", "/goal STOP CONDITIONS fork text mentions handoff only")))
        full = goal.goal_template_for_authority("full", path=p)
        self.assertNotIn("integration", full)
        self.assertNotIn("handoff", full)
        self.assertLess(len(full), 200)

    def test_missing_authority_returns_none(self):
        p = self._p(_skill_md(("full", "/goal x")))
        self.assertIsNone(goal.goal_template_for_authority("branch-merge", path=p))

    def test_blank_authority_returns_none(self):
        self.assertIsNone(goal.goal_template_for_authority(""))
        self.assertIsNone(goal.goal_template_for_authority(None))

    def test_unreadable_file_returns_none(self):
        self.assertIsNone(goal.goal_template_for_authority(
            "full", path="/nonexistent/SKILL.md"))

    def test_over_cap_line_is_refused_never_typed(self):
        # #169 -- a template Claude Code itself would reject must never be
        # resolved as armable at all.
        long_line = "/goal " + ("x" * (goal.GOAL_ARM_CHAR_CAP + 50))
        p = self._p(_skill_md(("full", long_line)))
        self.assertIsNone(goal.goal_template_for_authority("full", path=p))

    def test_the_real_shipped_skill_md_resolves_all_three_authorities(self):
        # Live-shipped-artifact proof, not just a synthetic fixture: the
        # ACTUAL installed skills/autopilot/SKILL.md this repo ships must
        # resolve for real, under the cap, for all three profiles.
        real = str(Path(__file__).resolve().parent.parent
                   / "skills" / "autopilot" / "SKILL.md")
        for authority in ("full", "branch-merge", "fork-no-merge"):
            line = goal.goal_template_for_authority(authority, path=real)
            self.assertIsNotNone(line, "authority %r failed to resolve "
                                 "from the real shipped SKILL.md" % authority)
            self.assertTrue(line.startswith("/goal "))
            self.assertLessEqual(len(line), goal.GOAL_ARM_CHAR_CAP)


# --------------------------------------------------------------------------- #
# 3. deliver_goal — the state machine.
# --------------------------------------------------------------------------- #

class TestDeliverGoal(unittest.TestCase):
    SID = "sess-goal-deliver-1"
    CWD = "/home/newlevel/devel/goaltest"
    TEXT = "/goal STOP CONDITIONS test payload"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, now=None, request_ts=None, in_mode=False,
           cap_seq=(), proj=None, state=None, send_fn=None, dry_run=False,
           model_type=True):
        if proj is None:
            proj = self._dir()
            _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, in_mode=in_mode,
            cap_seq=cap_seq, model_type=model_type)
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=now,
                                 state=state, request_ts=request_ts,
                                 send_fn=send_fn, dry_run=dry_run,
                                 sleep_fn=lambda s: None)
        return word, tmux, proj

    def test_idle_bare_pane_sends(self):
        word, tmux, _ = self._go(GOAL_IDLE_CAP)
        self.assertEqual(word, "sent")
        self.assertIn(self.TEXT, tmux.typed_texts())

    def test_no_pane_skips(self):
        proj = self._dir()      # no transcript, no matching pane
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        word = goal.deliver_goal("no-such-sid", self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj)
        self.assertEqual(word, "skip:no-pane")
        self.assertEqual(tmux.sent, [])

    def test_pane_in_copy_mode_skips(self):
        word, tmux, _ = self._go(GOAL_IDLE_CAP, in_mode=True)
        self.assertEqual(word, "skip:in-mode")
        self.assertEqual(tmux.sent, [])

    def test_dialog_open_skips(self):
        word, tmux, _ = self._go(GOAL_DIALOG_CAP)
        self.assertEqual(word, "skip:dialog-open")
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_skips(self):
        # `pane_goal_armed` runs BEFORE the boundary classify inside
        # `deliver_goal` -- a genuinely busy capture (no `❯` line anywhere)
        # is UNDETERMINABLE for it (no box to read), so the real code path
        # reaches "skip:undeterminable" here, never "skip:busy" (that kind
        # IS a real, separately-reachable branch -- exercised below by
        # forcing `pane_goal_armed` to a determinable False so the busy-
        # kind check itself is what's under test, isolated from that
        # ordering interaction).
        word, tmux, _ = self._go(GOAL_BUSY_CAP)
        self.assertEqual(word, "skip:undeterminable")
        self.assertEqual(tmux.sent, [])

    def test_busy_kind_itself_skips_once_armed_is_determinable(self):
        with m.patch.object(wd, "pane_goal_armed", return_value=False):
            word, tmux, _ = self._go(GOAL_BUSY_CAP)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_already_armed_drops(self):
        word, tmux, _ = self._go(GOAL_ARMED_CAP)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(tmux.sent, [])

    def test_draft_pane_delivers_via_stash(self):
        # A draft in the box must be delivered through deliver_with_stash,
        # never a raw overwrite -- verified via the real primitive (a
        # non-empty box, no "› stashed" marker present -> park succeeds).
        word, tmux, _ = self._go(GOAL_DRAFT_CAP,
                                 cap_seq=[GOAL_DRAFT_CAP, "", "", ""])
        self.assertIn(word, ("sent", "skip:stash-abort"))
        # Either outcome is a legitimate stash-path result depending on the
        # exact scripted capture sequence -- the invariant under test is
        # that the draft text is NEVER silently overwritten with a raw
        # (non-stash) type, which a "sent" outcome via the wrong path
        # would risk. Assert the stash primitive's own signature keystroke
        # (Ctrl-S) appears whenever anything was typed at all.
        if tmux.sent:
            self.assertIn("C-s", tmux.keys())

    def test_hard_age_cap_expires_and_pings_once(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        sent = []
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 1000 + goal.GOAL_REQUEST_MAX_AGE_S + 1
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=now,
                                 request_ts=1000,
                                 send_fn=lambda msg, **kw: sent.append(msg))
        self.assertEqual(word, "expired")
        self.assertEqual(tmux.sent, [])          # never typed
        self.assertEqual(len(sent), 1)           # pinged exactly once

    def test_clear_suppression_drops_when_cleared_postdates_the_request(self):
        # #170's own invariant, reused here for arm delivery: a `cleared`
        # marker NEWER than the request means the user deliberately turned
        # the loop off after this request was made -- never re-arm.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "Goal cleared: some earlier text", ts_epoch=2000)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        word = goal.deliver_goal(self.SID, self.CWD, self.TEXT, "full",
                                 run=tmux, projects_dir=proj, now=2100,
                                 request_ts=1000)
        self.assertEqual(word, "drop:cleared-after-request")
        self.assertEqual(tmux.sent, [])

    def test_clear_predating_the_request_does_not_suppress(self):
        # The mirror-image case: a clear that happened BEFORE this request
        # was made is not a reason to refuse it -- the user asked again.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "Goal cleared: some earlier text", ts_epoch=500)
        word, tmux, _ = self._go(GOAL_IDLE_CAP, now=2100, request_ts=1000,
                                 proj=proj)
        self.assertEqual(word, "sent")

    def test_kill_switch_disables_delivery(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            word, tmux, _ = self._go(GOAL_IDLE_CAP)
        self.assertEqual(word, "skip:disabled")
        self.assertEqual(tmux.sent, [])

    def test_send_is_logged(self):
        self._go(GOAL_IDLE_CAP)
        log = self.syncp.read_text(encoding="utf-8")
        self.assertIn("SEND", log)
        self.assertIn(self.SID, log)

    def test_skip_is_logged(self):
        self._go(GOAL_BUSY_CAP)
        log = self.syncp.read_text(encoding="utf-8")
        self.assertIn("SKIP undeterminable", log)


# --------------------------------------------------------------------------- #
# 4. goal_sweep — job 9's new body: the periodic re-evaluation loop.
# --------------------------------------------------------------------------- #

class TestGoalSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/goalsweep"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _reqp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    def test_sent_request_is_cleared(self):
        proj = self._dir()
        sid = "sess-sweep-1"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_skip_leaves_the_request_pending(self):
        proj = self._dir()
        sid = "sess-sweep-2"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_BUSY_CAP)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("SKIP (goal-sweep)" in ln for ln in logs), logs)
        self.assertIn(sid, goal.load_goal_requests(reqp))

    def test_expired_request_is_dropped(self):
        proj = self._dir()
        sid = "sess-sweep-3"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        far_future = 1000 + goal.GOAL_REQUEST_MAX_AGE_S + 100
        logs = goal.goal_sweep(far_future, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("LAPSE" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_malformed_entry_with_no_text_is_dropped_not_retried_forever(self):
        reqp = self._reqp()
        Path(reqp).write_text(json.dumps({"sess-x": {"cwd": "/x"}}))
        logs = goal.goal_sweep(1000, requests_path=reqp, run=lambda *a, **k: "")
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_already_handled_this_sweep_is_skipped(self):
        proj = self._dir()
        sid = "sess-sweep-4"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        handled = {sid}
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, handled=handled,
                               sleep_fn=lambda s: None)
        self.assertTrue(any("handled this sweep already" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])
        self.assertIn(sid, goal.load_goal_requests(reqp))   # left pending

    def test_dry_run_never_types(self):
        proj = self._dir()
        sid = "sess-sweep-5"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_sweep(2000, run=tmux, dry_run=True, projects_dir=proj,
                               requests_path=reqp)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("DRY-RUN" in ln for ln in logs), logs)

    def test_kill_switch_disables_the_whole_sweep(self):
        reqp = self._reqp()
        goal.record_goal_request("sess-x", "/x", "/goal x", "full", path=reqp)
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_sweep(1000, requests_path=reqp,
                                   run=lambda *a, **k: "")
        self.assertTrue(any("DISABLED" in ln for ln in logs), logs)
        self.assertIn("sess-x", goal.load_goal_requests(reqp))  # untouched


# --------------------------------------------------------------------------- #
# 5. goal_dark_watch — job 20 half 1: NEVER types, 2-sweep debounce, silent
#    on cleared/no-marker, and the shared janitor recovery runs first.
# --------------------------------------------------------------------------- #

class TestGoalDarkWatch(unittest.TestCase):
    CWD = "/home/newlevel/devel/darkwatch"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_kill_switch_disables_dark_watch(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_dark_watch(1000, run=lambda *a, **k: "")
        self.assertTrue(any("DISABLED" in ln for ln in logs), logs)

    def test_never_sends_a_keystroke_regardless_of_outcome(self):
        proj = self._dir()
        sid = "sess-dark-1"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        # First observation.
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        # Second, still-dark observation of the SAME marker -- the ping
        # fires here, but STILL zero keystrokes ever.
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])

    def test_debounced_across_two_sweeps_before_pinging(self):
        proj = self._dir()
        sid = "sess-dark-2"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        send_fn = lambda m, **k: sent.append(m)
        state = {}
        goal.goal_dark_watch(1000, run=tmux, send_fn=send_fn, projects_dir=proj,
                             state=state, sleep_fn=lambda s: None)
        self.assertEqual(sent, [], "must NOT ping on the first observation")
        goal.goal_dark_watch(2000, run=tmux, send_fn=send_fn, projects_dir=proj,
                             state=state, sleep_fn=lambda s: None)
        self.assertEqual(len(sent), 1, "must ping once the SAME episode "
                        "survives a second sweep")

    def test_cleared_marker_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-3"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal cleared: /goal x",
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_no_marker_at_all_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-4"
        _write_marker_transcript(proj, self.CWD, sid)   # no goal marker ever
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_armed_footer_matching_marker_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-5"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_sweep_deadline_defers_remaining_panes(self):
        # #403 STEP 0's own requirement: this per-pane loop must respect
        # the #172/#255 wall-clock self-bound, since it walks EVERY live
        # candidate pane, unbounded by anything but the box's own pane
        # count.
        proj = self._dir()
        panes = []
        for i in range(3):
            sid = "sess-dark-budget-%d" % i
            cwd = "%s-%d" % (self.CWD, i)
            _write_marker_transcript(proj, cwd, sid)
            panes.append(("%%%d" % i, "claude", cwd, str(100 + i)))
        tmux = DeliverGoalFakeTmux(panes, GOAL_IDLE_CAP)
        clock = {"t": 0.0}

        def time_fn():
            clock["t"] += 1.0
            return clock["t"]

        logs = goal.goal_dark_watch(1000, run=tmux, projects_dir=proj,
                                    sleep_fn=lambda s: None, time_fn=time_fn,
                                    sweep_deadline=1.5)
        self.assertTrue(any("budget-exceeded" in ln for ln in logs), logs)

    def test_unbounded_when_no_deadline_given(self):
        proj = self._dir()
        sid = "sess-dark-nolimit"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(1000, run=tmux, projects_dir=proj,
                                    sleep_fn=lambda s: None)
        self.assertFalse(any("budget-exceeded" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 6. goal_lane_sweep / goal_lane_occupancy_nudge — job 20 half 2: the ONE
#    remaining watchdog-INITIATED keystroke. Recent-human-activity DOES
#    apply here (unlike arm delivery).
# --------------------------------------------------------------------------- #

class TestGoalLaneSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanesweep"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_backlog_fetch_is_a_no_op(self):
        logs = goal.goal_lane_sweep(1000, run=lambda *a, **k: "")
        self.assertEqual(logs, [])

    def test_kill_switch_disables_lane_sweep(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_lane_sweep(1000, run=lambda *a, **k: "",
                                        backlog_fetch=lambda cwd: 5)
        self.assertEqual(logs, [])

    def test_not_armed_is_skipped_entirely(self):
        proj = self._dir()
        sid = "sess-lane-1"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5)
        self.assertEqual(logs, [])
        self.assertEqual(tmux.sent, [])

    def test_sweep_deadline_defers_remaining_panes(self):
        proj = self._dir()
        panes = []
        for i in range(3):
            sid = "sess-lane-budget-%d" % i
            cwd = "%s-%d" % (self.CWD, i)
            _write_marker_transcript(proj, cwd, sid)
            panes.append(("%%%d" % i, "claude", cwd, str(200 + i)))
        tmux = DeliverGoalFakeTmux(panes, GOAL_ARMED_CAP)
        clock = {"t": 0.0}

        def time_fn():
            clock["t"] += 1.0
            return clock["t"]

        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5,
                                    time_fn=time_fn, sweep_deadline=1.5)
        self.assertTrue(any("budget-exceeded" in ln for ln in logs), logs)


class TestGoalLaneOccupancyNudge(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanenudge"
    SID = "sess-lane-nudge-1"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _call(self, captured, backlog_fetch, now, tmtime, rec=None, state=None,
             authority="full", handled=None):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], captured)
        with m.patch("airuleset.resolve_authority", return_value=authority):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec if rec is not None else {}, self.SID, self.CWD,
                "111", captured, tpath, tmtime, "loc", None, False, handled,
                proj, backlog_fetch=backlog_fetch,
                state=state if state is not None else {},
                sleep_fn=lambda s: None)
        return logs, owns, tmux

    def test_idle_armed_pane_with_backlog_and_no_workers_nudges(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertIn("C-s" not in tmux.keys() and True, [True])  # no stash needed
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_recent_human_activity_refuses_the_nudge(self):
        # Unlike arm delivery, the lane-occupancy nudge IS a genuinely
        # watchdog-INITIATED action, so it keeps the recent-human-activity
        # gate.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "human just typed")):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_question_marker_refuses_the_nudge(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "❓ NEEDS YOU: rozhodni sa", ts_epoch=tmtime)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "transcript_last_marker", return_value="❓"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])

    def test_no_open_backlog_refuses(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 0, now, tmtime)
        self.assertTrue(any("no measurable open backlog" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_live_worker_present_refuses(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(any("occupied" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_max_nudges_gives_up_and_pings_once(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# 7. scan_goal_markers simplification — no `arm_after` key any more, and no
#    crash on a line that would have needed `_entry_asks_to_arm`/
#    `_GOAL_ASK_PROBE` (both deleted).
# --------------------------------------------------------------------------- #

class TestScanGoalMarkersSimplified(unittest.TestCase):
    CWD = "/home/newlevel/devel/scanmarkers"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_marker_dict_has_no_arm_after_key(self):
        proj = self._dir()
        sid = "sess-scan-1"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        _off, mark = wd.scan_goal_markers(tpath, off=0)
        self.assertIsNotNone(mark)
        self.assertNotIn("arm_after", mark)

    def test_does_not_crash_on_an_arm_question_shaped_line(self):
        # The OLD design's `_entry_asks_to_arm`/`_GOAL_ASK_PROBE` used to
        # classify a line CONTAINING the literal bytes "/goal" as a
        # possible arm-question -- confirm the simplified scan reads
        # straight past such a line with no error, finding only the real
        # marker.
        proj = self._dir()
        sid = "sess-scan-2"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {
                "content": "Should I paste the /goal line now?"}}) + "\n")
        _off, mark = wd.scan_goal_markers(tpath, off=0)
        self.assertIsNotNone(mark)
        self.assertEqual(mark.get("state"), "set")


if __name__ == "__main__":
    unittest.main()
