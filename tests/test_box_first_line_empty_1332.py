"""#1332 — a box whose FIRST line is empty: job 1 read it busy (txt='') forever.

obs@dev1, 15.11.2026: the 01:45 OAuth-rotation resume read the input box as BARE
while it held a line break (`_input_box_rows_raw` drops blank rows), typed the
pointer onto the box's second line, failed its read-back, and from then on the
box (`❯` head row + an indented text row) was "no box" to every reader:
`_classify_boundary` said ("busy", None), job 1 logged `skip busy-pane
(api-error) … [busy txt='']` for six hours, the janitor refused the unreadable box,
and the only signal was a ping #1137 suppresses.

The renders below are CC 2.1.317 captures measured on a private tmux server
(scratch HOME, `claude --bare`, a local fake Messages API; see the ticket).
"""
import datetime as _dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog  # noqa: E402

SEP = "─" * 120
FOOT = "  ⏵⏵ auto mode on (shift+tab to cycle)"
PTR_TAIL = "— celý text: ~/.claude/nudges/resume-261111183944-c447.md"
ABOVE = "● ok\n\n✻ Brewed for 0s · done 8:53 AM\n\n"

# Measured: `\` + Enter in a bare box (the box holds only a line break).
LONE_NL = (ABOVE + " " * 97 + "ctrl+g to edit in Vim\n" + SEP + "\n❯\xa0\n\n"
           + SEP + "\n" + FOOT + "\n")
# Measured: the resume pointer typed after that line break (the obs shape).
PTR = "nudge: [resume] prebehla rotacia " + PTR_TAIL
PTR_BOX = (ABOVE + " " * 97 + "ctrl+g to edit in Vim\n" + SEP + "\n❯\xa0\n  "
           + PTR + "\n" + SEP + "\n" + FOOT + "\n")
# Controls, measured on the same session.
BARE = ABOVE + SEP + "\n❯\xa0\n" + SEP + "\n" + FOOT + "\n"
QUEUED = ("❯ nudge: x\n  ctrl+x ctrl+s to send now\n\n✶ Flowing…\n\n" + SEP
          + "\n❯\xa0Press up to edit queued messages\n" + SEP + "\n" + FOOT + "\n")


class ReaderSeesTheBox(TestCase):
    """The reader must call neither shape bare nor unreadable."""

    def test_a_box_holding_only_a_line_break_is_not_bare(self):
        self.assertFalse(watchdog.pane_at_idle_prompt(LONE_NL))
        self.assertEqual(watchdog._classify_boundary(LONE_NL), ("input", "\n"))
        self.assertEqual(watchdog._input_line_text(LONE_NL), "\n")

    def test_text_under_an_empty_first_line_is_a_readable_draft(self):
        self.assertEqual(watchdog._classify_boundary(PTR_BOX), ("input", PTR))
        self.assertFalse(watchdog.pane_at_idle_prompt(PTR_BOX))
        self.assertTrue(watchdog._has_free_prompt(PTR_BOX, bare_only=False))
        self.assertEqual(watchdog._box_norm_from_capture(PTR_BOX), PTR)

    def test_bare_and_queued_boxes_are_unchanged(self):
        for cap in (BARE, QUEUED):
            self.assertEqual(watchdog._classify_boundary(cap), ("input", ""), cap)
            self.assertTrue(watchdog.pane_at_idle_prompt(cap), cap)
        draft = ABOVE + SEP + "\n❯\xa0hello there\n" + SEP + "\n" + FOOT + "\n"
        self.assertEqual(watchdog._classify_boundary(draft), ("input", "hello there"))


def _iso(epoch):
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


class _BoxPane:
    """A tmux fake that EMULATES the CC input box measured above: literal keys
    append, BSpace deletes the last char, Enter submits (a `user` turn lands in
    the transcript), Ctrl+S ignores a whitespace-only box (measured 2.1.317)."""

    def __init__(self, panes, box, tpath):
        self.panes, self.box, self.tpath = panes, box, tpath
        self.sent, self.submitted = [], []

    def render(self):
        lines = self.box.split("\n")
        rows = ["❯\xa0" + lines[0]] + [("  " + ln) if ln else "" for ln in lines[1:]]
        return ABOVE + SEP + "\n" + "\n".join(rows) + "\n" + SEP + "\n" + FOOT + "\n"

    def __call__(self, argv, timeout=8):
        if argv[:2] == ["tmux", "list-panes"]:
            return self.panes
        if argv[:2] == ["tmux", "display-message"]:
            if argv[-1] == "#{pane_width}":
                return "120"
            return "0" if argv[-1] == "#{pane_in_mode}" else ""
        if argv[:2] == ["tmux", "capture-pane"]:
            return self.render()
        if argv[:2] != ["tmux", "send-keys"]:
            return ""
        self.sent.append(argv)
        keys = argv[4:]
        if keys[:2] == ["-l", "--"]:
            self.box += keys[2]
            return ""
        for k in keys:
            if k == "BSpace":
                self.box = self.box[:-1]
            elif k == "Enter" and self.box.strip():
                self.submitted.append(self.box.strip())
                with open(self.tpath, "a") as f:
                    f.write(json.dumps({"type": "user", "timestamp": _iso(time.time()),
                                        "message": {"role": "user",
                                                    "content": self.box.strip()}}) + "\n")
                self.box = ""
        return ""

    def backspaces(self):
        return sum(a.count("BSpace") for a in self.sent)


class _ObsHarness:
    """Job 1 through the real `run_once` + `send_verified`: only tmux, Discord and
    the clock seams are faked."""

    _SID = "86c10329-ebe4-4242-ac43-5633f1a177f7"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp)
        (self.home / ".claude").mkdir()
        self.projects = self.home / "projects"
        self.state = str(self.home / ".claude" / "api-watchdog-state.json")
        self.nudges = self.home / ".claude" / "nudges"
        self.nudges.mkdir()
        p = m.patch.dict(os.environ, {"AIRULESET_NUDGE_FILE_DIR": str(self.nudges)})
        p.start()
        self.addCleanup(p.stop)
        orig = watchdog.usage._USAGE_CACHE_PATH
        watchdog.usage._USAGE_CACHE_PATH = str(self.home / "usage-cache.json")
        self.addCleanup(setattr, watchdog.usage, "_USAGE_CACHE_PATH", orig)
        self.pings = []

    def _send(self, body, owner=None, dedup_key=None, dry_run=False):
        self.pings.append((body, dedup_key))
        return "sent"

    def _pointer_file(self):
        f = self.nudges / "resume-261111183944-c447.md"
        f.write_text("oauth-resume: …", encoding="utf-8")
        os.chmod(f, 0o600)
        return "nudge: [resume] prebehla rotacia — celý text: " + str(f)

    def _pane(self, cwd, err_text, box, now):
        d = self.projects / watchdog.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        tp = d / (self._SID + ".jsonl")
        err = {"type": "assistant", "isApiErrorMessage": True,
               "timestamp": _iso(now - 600),
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": err_text}]}}
        tp.write_text(json.dumps({"type": "user", "timestamp": _iso(now - 700),
                                  "message": {"role": "user", "content": "go"}}) + "\n"
                      + json.dumps(err) + "\n")
        os.utime(tp, (now - 600, now - 600))
        return _BoxPane("%0\tclaude\t" + cwd + "\n", box, str(tp))

    def _sweep(self, fake, now):
        return watchdog.run_once(
            now=now, run=fake, send_fn=self._send, projects_dir=self.projects,
            state_path=self.state, grace=300, interval=300, max_nudges=3,
            sleep_fn=lambda s: None, cred_mtime_fn=lambda: now - 60,
            proc_start_fn=lambda pid, run: now - 86400)

    def _two_sweeps(self, err_text, box):
        now = time.time()
        fake = self._pane("/devel/obs", err_text, box, now)
        logs = self._sweep(fake, now) + self._sweep(fake, now + 70)
        return fake, logs



class JobOneOnTheObsShape(_ObsHarness, TestCase):
    def test_oauth_rotation_reclaims_our_pointer_then_resumes(self):
        fake, logs = self._two_sweeps("OAuth token revoked · Please run /login",
                                      "\n" + self._pointer_file())
        self.assertFalse(any("busy txt=''" in ln for ln in logs), logs)
        self.assertEqual(len(fake.submitted), 1, logs)
        self.assertNotIn("\n", fake.box)

    def test_a_529_stall_reclaims_our_pointer_then_resumes(self):
        fake, logs = self._two_sweeps("API Error: 529 Overloaded",
                                      "\n" + self._pointer_file())
        self.assertFalse(any("skip busy-pane" in ln for ln in logs), logs)
        self.assertEqual(fake.submitted, ["continue"], logs)

    def test_a_box_holding_only_a_line_break_is_cleared_then_resumed(self):
        fake, logs = self._two_sweeps("API Error: 529 Overloaded", "\n")
        self.assertEqual(fake.submitted, ["continue"], logs)

    def test_the_owners_words_after_an_empty_first_line_are_never_eaten(self):
        fake, logs = self._two_sweeps("OAuth token revoked · Please run /login",
                                      "\nnechaj to tak, ráno to dorobím")
        self.assertEqual(fake.box, "\nnechaj to tak, ráno to dorobím", logs)
        self.assertEqual(fake.backspaces(), 0, logs)
        self.assertEqual(fake.submitted, [], logs)


class CredentialDeadSendThreadsState(TestCase):
    """The FRESH one-continue must hand `state` to `send_verified`, or a verify
    failure records nothing for the janitor (the 01:45:29 journal line)."""

    def test_state_reaches_send_verified(self):
        seen = {}

        def _sv(pid, text, run=None, tpath=None, sleep_fn=None, logs=None,
                user_authored=False, nudge=None, state=None, now=None, **kw):
            seen.update(state=state, now=now, nudge=nudge)
            return False
        st = {}
        with m.patch.object(watchdog, "send_verified", _sv), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: BARE):
            watchdog._handle_authdead_episode(
                st, "sid", lambda: 2000.0, 3000.0, 600, "obs", "%0", "/devel/obs",
                "/x.jsonl", BARE, False, lambda *a, **k: "", lambda *a, **k: None,
                None, lambda s: None, False, [], "/nonexistent/state.json", 1000.0,
                1000.0)
        self.assertIs(seen.get("state"), st)


class StuckRecoveryIsVisible(TestCase):
    """A job-1 recovery that cannot proceed shows as `stuck!` in the footer."""

    _SID = "aaaabbbb-0000-4000-8000-000000001332"

    def test_busy_pane_wedge_paints_the_footer_and_it_expires(self):
        import statusbar
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".claude").mkdir()
        projects = tmp / "projects"
        cwd = "/devel/wedged"
        d = projects / watchdog.encode_project_dir(cwd)
        d.mkdir(parents=True)
        now = 1_000_000
        tp = d / (self._SID + ".jsonl")
        tp.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True,
                                  "message": {"role": "assistant", "content": [
                                      {"type": "text",
                                       "text": "API Error: 529 Overloaded"}]}}) + "\n")
        os.utime(tp, (now - 1000, now - 1000))
        busy = "● Validate\n  ⎿ running…\n✻ Working… (3m · esc to interrupt)\n"

        def run(argv, timeout=8):
            if argv[:2] == ["tmux", "list-panes"]:
                return "%5\tclaude\t" + cwd + "\n"
            if argv[:2] == ["tmux", "capture-pane"]:
                return busy
            if argv[:2] == ["tmux", "display-message"]:
                return "0"
            return ""
        orig = watchdog.usage._USAGE_CACHE_PATH
        watchdog.usage._USAGE_CACHE_PATH = str(tmp / "usage-cache.json")
        self.addCleanup(setattr, watchdog.usage, "_USAGE_CACHE_PATH", orig)
        self.assertEqual(statusbar.recovery_stuck_segment(home=str(tmp), now=now), "")
        watchdog.run_once(now=now, run=run, send_fn=lambda *a, **k: "sent",
                          projects_dir=projects,
                          state_path=str(tmp / ".claude" / "api-watchdog-state.json"),
                          grace=300, interval=300, max_nudges=3)
        self.assertIn("stuck!", statusbar.recovery_stuck_segment(home=str(tmp), now=now))
        self.assertEqual(statusbar.recovery_stuck_segment(home=str(tmp), now=now + 3600),
                         "", "a wedge the watchdog stopped seeing must not stay painted")


class ReviewFindings(TestCase):
    """Both adversarial reviews (round 1): the new shape must never read as an
    EMPTY box to a reader that keys on the head row, a dry run must not touch
    state, and the line-break clear runs once per episode."""

    def _cap(self, box_rows, stashed=False):
        return "\n".join(["some transcript", "", SEP] + box_rows + [SEP]
                         + (["  › stashed · ctrl+s to restore"] if stashed else [])
                         + [FOOT]) + "\n"

    def test_the_head_text_of_an_empty_first_line_is_never_empty(self):
        self.assertEqual(watchdog._input_box_head_text(PTR_BOX), PTR)
        self.assertEqual(watchdog._input_box_head_text(LONE_NL), "\n")
        self.assertEqual(watchdog._input_box_head_text(BARE), "")

    def test_the_janitor_never_presses_ctrl_s_into_the_owners_words(self):
        from watchdog import janitor
        cap = self._cap(["❯\xa0", "  my owner words on line two"], stashed=True)
        sent = []
        now = time.time()
        state = {"stash_parks": {"%1": now}}
        with m.patch.object(watchdog, "keys", lambda pid, *k, **kw: sent.append(k) or True), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: cap), \
                m.patch.object(watchdog, "_draft_rescue_persist", lambda *a, **k: None):
            janitor._janitor_recover(lambda *a, **k: "", {}, "%1", "/x", cap, "s:0.0",
                                     None, False, lambda s: None, state=state, now=now)
        self.assertEqual(sent, [])

    def test_a_dry_run_reclaim_leaves_state_untouched(self):
        from watchdog import apierr_box
        cap = self._cap(["❯\xa0", "  my owner words on line two"])
        now = time.time()
        state = {"stranded_own": {"%1": {"ts": now - 60, "typed": "nudge: x — celý text: /n"}},
                 "janitor_watch": {"%1": now - 60}}
        before = json.dumps(state, sort_keys=True)
        with m.patch.object(watchdog, "capture_pane", lambda *a, **k: cap):
            apierr_box.reclaim("%1", lambda *a, **k: "", lambda s: None, state, now,
                               True, [], "p")
        self.assertEqual(json.dumps(state, sort_keys=True), before)

    def test_the_line_break_clear_runs_once_per_episode(self):
        from watchdog import apierr_box
        sent = []
        now = time.time()
        state = {}
        with m.patch.object(watchdog, "keys", lambda pid, *k, **kw: sent.append(k) or True), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL):
            for i in range(3):
                apierr_box.reclaim("%1", lambda *a, **k: "", lambda s: None, state,
                                   now + 70 * i, False, [], "p")
        self.assertLessEqual(len(sent), watchdog.JANITOR_CLEAR_MAX_ITER,
                             "a box that never converges is tried in ONE episode pass")


class ReviewFindingsJobOne(_ObsHarness, TestCase):
    """Job-1 ordering: no key before grace or into an owner-stopped pane."""

    def test_no_key_reaches_the_pane_inside_the_grace_window(self):
        now = time.time()
        fake = self._pane("/devel/obs", "API Error: 529 Overloaded",
                          "\n" + self._pointer_file(), now)
        os.utime(fake.tpath, (now - 30, now - 30))       # the stall is 30 s old
        self._sweep(fake, now)
        self.assertEqual(fake.sent, [], "a stall younger than grace gets no key")

    def test_no_key_reaches_an_owner_stopped_pane(self):
        now = time.time()
        fake = self._pane("/devel/obs", "API Error: 529 Overloaded",
                          "\n" + self._pointer_file(), now)
        from watchdog import owner_stop
        with m.patch.object(owner_stop, "pane_latch", lambda pid: {"ts": now - 5}):
            self._sweep(fake, now)
        self.assertEqual(fake.sent, [], "a stopped session gets no key (B9)")
